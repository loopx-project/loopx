import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

/**
 * Rebuildable CJK 1/2-gram + ASCII 3-gram + cross-script 2-gram file/document exclusion index.
 *
 * Correctness contract:
 * 1. The index ONLY excludes documents that provably cannot contain the query substring.
 * 2. A document is eligible for exclusion ONLY when its live content version (contentSha256, plus
 *    size & mtimeNs when file-backed) matches the indexed manifest entry. Same-size / same-mtime
 *    content rewrites produce a different contentSha256 and therefore NEVER get falsely excluded.
 * 3. Wide (CJK/Kana/Hangul/Emoji) codepoints emit both 1-grams (`w1:<cp>`) and adjacent 2-grams
 *    (`w2:<a+b>`), so single-character CJK queries (including the second character of a two-char
 *    word like "猫" in "黑猫") are exact posting lookups rather than prefix guesses.
 * 4. Whenever a query cannot form at least one indexable gram (e.g. a 1- or 2-character pure ASCII
 *    query) or `.search/` is missing, stale, or corrupt, exclusion returns an empty set so the
 *    caller falls back to direct raw text scanning with 100% recall parity.
 */

export interface IndexedDocInput {
  docId: string;
  text: string;
  size?: number;
  mtimeNs?: string | null;
  contentSha256?: string;
}

export interface ManifestDocEntry {
  contentSha256: string;
  size: number;
  mtimeNs: string | null;
}

interface SearchManifest {
  schemaVersion: 'workbench_gram_exclusion_v1';
  builtAt: string;
  docs: Record<string, ManifestDocEntry>;
}

interface SearchSegment {
  schemaVersion: 'workbench_gram_segment_v1';
  postings: Record<string, string[]>;
}

export function sha256Text(text: string): string {
  return createHash('sha256').update(text, 'utf8').digest('hex');
}

function normalizeSearchStream(input: string): string {
  return input.normalize('NFC').toLowerCase();
}

export function isWideCodePoint(cp: number): boolean {
  return (
    (cp >= 0x1100 && cp <= 0x11ff) || // Hangul Jamo
    (cp >= 0x2e80 && cp <= 0xa4cf) || // CJK Radicals, Kana, Bopomofo, CJK Unified, Yi
    (cp >= 0xac00 && cp <= 0xd7af) || // Hangul Syllables
    (cp >= 0xf900 && cp <= 0xfaff) || // CJK Compatibility Ideographs
    (cp >= 0xfe30 && cp <= 0xfe4f) || // CJK Compatibility Forms
    (cp >= 0x1f000 && cp <= 0x1faff) || // Emoji & Symbols
    (cp >= 0x20000 && cp <= 0x323af) // CJK Unified Extensions B–H
  );
}

function isWhitespace(ch: string): boolean {
  return /\s/.test(ch);
}

/**
 * Extract deterministic grams from normalized text:
 * - Wide chars: `w1:<c>` and adjacent wide `w2:<c1><c2>`
 * - Narrow runs (non-whitespace, non-wide): `n3:<c1><c2><c3>`
 * - Wide <-> Narrow adjacent boundary (no whitespace between): `x2:<c1><c2>`
 */
export function extractGrams(rawText: string): Set<string> {
  const norm = normalizeSearchStream(rawText);
  const chars = Array.from(norm);
  const grams = new Set<string>();
  let narrowRun: string[] = [];

  const flushNarrow = () => {
    if (narrowRun.length >= 3) {
      for (let i = 0; i <= narrowRun.length - 3; i++) {
        grams.add(`n3:${narrowRun[i]}${narrowRun[i + 1]}${narrowRun[i + 2]}`);
      }
    }
    narrowRun = [];
  };

  for (let i = 0; i < chars.length; i++) {
    const ch = chars[i]!;
    if (isWhitespace(ch)) {
      flushNarrow();
      continue;
    }
    const cp = ch.codePointAt(0)!;
    const wide = isWideCodePoint(cp);
    const prev = i > 0 ? chars[i - 1]! : null;
    const prevNonSpace = prev !== null && !isWhitespace(prev);
    const prevWide = prevNonSpace ? isWideCodePoint(prev!.codePointAt(0)!) : false;

    if (wide) {
      flushNarrow();
      grams.add(`w1:${ch}`);
      if (prevNonSpace && prevWide) {
        grams.add(`w2:${prev}${ch}`);
      } else if (prevNonSpace && !prevWide) {
        grams.add(`x2:${prev}${ch}`);
      }
    } else {
      if (prevNonSpace && prevWide) {
        grams.add(`x2:${prev}${ch}`);
      }
      narrowRun.push(ch);
    }
  }
  flushNarrow();
  return grams;
}

/**
 * Extract required grams for a search query. Whitespace splits independent terms; EVERY term
 * must yield at least one gram, otherwise we return an empty set so the caller performs a direct
 * scan rather than risking a false exclusion on short ASCII terms (e.g. "ab" or "go").
 */
export function extractQueryGrams(rawQuery: string): Set<string> {
  const terms = rawQuery
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (!terms.length) return new Set();
  const all = new Set<string>();
  for (const term of terms) {
    const termGrams = extractGrams(term);
    if (termGrams.size === 0) {
      // At least one term cannot be represented in the gram index (e.g. 1-2 char ASCII):
      // return empty so no document is excluded without a raw scan.
      return new Set();
    }
    for (const g of termGrams) all.add(g);
  }
  return all;
}

export class GramExclusionIndex {
  readonly searchDir: string;
  private cachedManifest: SearchManifest | null = null;
  private cachedPostings: Map<string, Set<string>> | null = null;

  constructor(dataDir: string) {
    this.searchDir = dataDir.endsWith('.search') ? dataDir : join(dataDir, '.search');
  }

  private manifestPath(): string {
    return join(this.searchDir, 'manifest.json');
  }

  private segmentPath(): string {
    return join(this.searchDir, 'seg-0.json');
  }

  invalidateMemoryCache(): void {
    this.cachedManifest = null;
    this.cachedPostings = null;
  }

  /** Synchronize the on-disk `.search/` index with the current document corpus. */
  sync(docs: readonly IndexedDocInput[]): { rebuilt: boolean } {
    const loaded = this.loadFromDisk();
    if (loaded) {
      const existingIds = Object.keys(loaded.manifest.docs);
      if (
        existingIds.length === docs.length &&
        docs.every((doc) => {
          const entry = loaded.manifest.docs[doc.docId];
          if (!entry) return false;
          const sha = doc.contentSha256 ?? sha256Text(doc.text);
          const size = doc.size ?? Buffer.byteLength(doc.text, 'utf8');
          const mtimeNs = doc.mtimeNs ?? null;
          return entry.contentSha256 === sha && entry.size === size && entry.mtimeNs === mtimeNs;
        })
      ) {
        return { rebuilt: false };
      }
    }
    this.rebuild(docs);
    return { rebuilt: true };
  }

  /** Rebuild `.search/` atomically from plain text documents. Safe to call after `rm -rf .search`. */
  rebuild(docs: readonly IndexedDocInput[]): void {
    mkdirSync(this.searchDir, { recursive: true });
    const manifestDocs: Record<string, ManifestDocEntry> = {};
    const postingsMap = new Map<string, string[]>();

    for (const doc of docs) {
      const sha = doc.contentSha256 ?? sha256Text(doc.text);
      const size = doc.size ?? Buffer.byteLength(doc.text, 'utf8');
      manifestDocs[doc.docId] = {
        contentSha256: sha,
        size,
        mtimeNs: doc.mtimeNs ?? null,
      };
      for (const gram of extractGrams(doc.text)) {
        let list = postingsMap.get(gram);
        if (!list) {
          list = [];
          postingsMap.set(gram, list);
        }
        list.push(doc.docId);
      }
    }

    const sortedPostings: Record<string, string[]> = {};
    for (const key of [...postingsMap.keys()].sort()) {
      sortedPostings[key] = postingsMap.get(key)!;
    }

    const manifest: SearchManifest = {
      schemaVersion: 'workbench_gram_exclusion_v1',
      builtAt: new Date().toISOString(),
      docs: manifestDocs,
    };
    const segment: SearchSegment = {
      schemaVersion: 'workbench_gram_segment_v1',
      postings: sortedPostings,
    };

    const segTmp = `${this.segmentPath()}.tmp-${process.pid}`;
    const manTmp = `${this.manifestPath()}.tmp-${process.pid}`;
    writeFileSync(segTmp, JSON.stringify(segment));
    renameSync(segTmp, this.segmentPath());
    writeFileSync(manTmp, JSON.stringify(manifest));
    renameSync(manTmp, this.manifestPath());

    this.cachedManifest = manifest;
    this.cachedPostings = new Map(
      Object.entries(sortedPostings).map(([gram, ids]) => [gram, new Set(ids)]),
    );
  }

  private loadFromDisk(): { manifest: SearchManifest; postings: Map<string, Set<string>> } | null {
    if (this.cachedManifest && this.cachedPostings) {
      return { manifest: this.cachedManifest, postings: this.cachedPostings };
    }
    if (!existsSync(this.manifestPath()) || !existsSync(this.segmentPath())) {
      return null;
    }
    try {
      const rawManifest = JSON.parse(readFileSync(this.manifestPath(), 'utf8')) as SearchManifest;
      const rawSegment = JSON.parse(readFileSync(this.segmentPath(), 'utf8')) as SearchSegment;
      if (
        rawManifest?.schemaVersion !== 'workbench_gram_exclusion_v1' ||
        rawSegment?.schemaVersion !== 'workbench_gram_segment_v1' ||
        typeof rawManifest.docs !== 'object' ||
        !rawManifest.docs ||
        typeof rawSegment.postings !== 'object' ||
        !rawSegment.postings
      ) {
        return null;
      }
      const postings = new Map<string, Set<string>>();
      for (const [gram, ids] of Object.entries(rawSegment.postings)) {
        if (!Array.isArray(ids)) return null;
        postings.set(gram, new Set(ids));
      }
      this.cachedManifest = rawManifest;
      this.cachedPostings = postings;
      return { manifest: rawManifest, postings };
    } catch {
      this.invalidateMemoryCache();
      return null;
    }
  }

  /**
   * Return the set of `docId`s that PROVABLY cannot match `query`.
   *
   * Any document missing from `.search/`, having an unverified/missing `contentSha256`, or having
   * any mismatch in `(contentSha256, size, mtimeNs)` is NEVER placed in `excluded`, guaranteeing
   * fallback to direct raw scanning.
   */
  excludeNonMatching(
    query: string,
    candidates: ReadonlyArray<{
      docId: string;
      contentSha256: string | null;
      size?: number | null;
      mtimeNs?: string | null;
    }>,
  ): { excluded: Set<string>; indexUsed: boolean } {
    const requiredGrams = extractQueryGrams(query);
    if (requiredGrams.size === 0) {
      return { excluded: new Set(), indexUsed: false };
    }
    // Always verify on-disk presence so `rm -rf .search` at runtime is observed immediately.
    if (!existsSync(this.manifestPath()) || !existsSync(this.segmentPath())) {
      this.invalidateMemoryCache();
      return { excluded: new Set(), indexUsed: false };
    }
    const loaded = this.loadFromDisk();
    if (!loaded) {
      return { excluded: new Set(), indexUsed: false };
    }

    // Intersect posting lists across all required query grams.
    let intersection: Set<string> | null = null;
    for (const gram of requiredGrams) {
      const list = loaded.postings.get(gram);
      if (!list || list.size === 0) {
        intersection = new Set();
        break;
      }
      if (intersection === null) {
        intersection = new Set(list);
      } else {
        for (const id of [...intersection]) {
          if (!list.has(id)) intersection.delete(id);
        }
        if (intersection.size === 0) break;
      }
    }
    const hitSet = intersection ?? new Set<string>();

    const excluded = new Set<string>();
    for (const candidate of candidates) {
      if (!candidate.contentSha256) continue; // Content version uncertain -> must raw scan!
      const indexed = loaded.manifest.docs[candidate.docId];
      if (!indexed) continue;
      if (indexed.contentSha256 !== candidate.contentSha256) continue;
      if (candidate.size != null && indexed.size !== candidate.size) continue;
      if (candidate.mtimeNs !== undefined && indexed.mtimeNs !== candidate.mtimeNs) continue;
      if (!hitSet.has(candidate.docId)) {
        excluded.add(candidate.docId);
      }
    }
    return { excluded, indexUsed: true };
  }

  clear(): void {
    this.invalidateMemoryCache();
    rmSync(this.searchDir, { recursive: true, force: true });
  }
}
