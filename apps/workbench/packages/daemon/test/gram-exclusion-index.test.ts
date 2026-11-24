import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import {
  GramExclusionIndex,
  extractGrams,
  extractQueryGrams,
  sha256Text,
  type IndexedDocInput,
} from '../src/gram-exclusion-index.js';

function makeDocs(): IndexedDocInput[] {
  const docs: Array<{ id: string; text: string; size?: number; mtimeNs?: string }> = [
    { id: 'doc:cat-single', text: '猫' },
    { id: 'doc:black-cat', text: '今天排查一只黑猫引发的告警' },
    { id: 'doc:white-dog', text: '白狗在院子里睡觉' },
    { id: 'doc:build-fail', text: 'npm run build failed in packages/web' },
    { id: 'doc:cross-script', text: '帮忙修bug并跑通回归测试' },
    { id: 'doc:ledger', text: '管家在办账本索引设计' },
  ];
  return docs.map((d, idx) => ({
    docId: d.id,
    text: d.text,
    size: d.size ?? Buffer.byteLength(d.text, 'utf8'),
    mtimeNs: d.mtimeNs ?? String(1_700_000_000_000 + idx),
    contentSha256: sha256Text(d.text),
  }));
}

function exactSubstringMatch(docs: IndexedDocInput[], query: string): string[] {
  const terms = query.normalize('NFC').toLowerCase().trim().split(/\s+/).filter(Boolean);
  return docs
    .filter((doc) => {
      const hay = doc.text.normalize('NFC').toLowerCase();
      return terms.every((term) => hay.includes(term));
    })
    .map((doc) => doc.docId);
}

function searchWithExclusion(index: GramExclusionIndex, docs: IndexedDocInput[], query: string): string[] {
  const { excluded } = index.excludeNonMatching(
    query,
    docs.map((d) => ({ docId: d.docId, contentSha256: d.contentSha256 ?? null, size: d.size, mtimeNs: d.mtimeNs })),
  );
  const candidates = docs.filter((doc) => !excluded.has(doc.docId));
  return exactSubstringMatch(candidates, query);
}

test('extracts CJK 1/2-grams, ASCII 3-grams, and cross-script 2-grams without false exclusions', () => {
  const grams = extractGrams('修bug 猫');
  assert.ok(grams.has('w1:修'), 'includes 1-gram for 修');
  assert.ok(grams.has('x2:修b'), 'includes CJK->ASCII boundary 2-gram');
  assert.ok(grams.has('n3:bug'), 'includes ASCII 3-gram');
  assert.ok(grams.has('w1:猫'), 'includes 1-gram for 猫');

  // Short 1-2 char ASCII query produces empty query grams so nothing is falsely excluded.
  assert.equal(extractQueryGrams('ab').size, 0);
});

test('1-char CJK, 2-char CJK, mid-word ASCII, cross-script, and short ASCII match exact scan 100%', () => {
  const dir = mkdtempSync(join(tmpdir(), 'gram-index-'));
  try {
    const docs = makeDocs();
    const index = new GramExclusionIndex(join(dir, '.search'));
    index.sync(docs);

    for (const query of ['猫', '黑猫', '账本', 'uild', '修bug', 'ab', 'packages/web']) {
      const viaIndex = searchWithExclusion(index, docs, query);
      const exact = exactSubstringMatch(docs, query);
      assert.deepEqual(viaIndex, exact, `parity mismatch for query "${query}"`);
    }

    // Verify exclusion actually narrows candidates for selective queries.
    const { excluded: excludedCat } = index.excludeNonMatching(
      '猫',
      docs.map((d) => ({ docId: d.docId, contentSha256: d.contentSha256 ?? null, size: d.size, mtimeNs: d.mtimeNs })),
    );
    assert.equal(excludedCat.has('doc:cat-single'), false);
    assert.equal(excludedCat.has('doc:black-cat'), false);
    assert.equal(excludedCat.has('doc:white-dog'), true);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('deleting .search or corrupting segments rebuilds cleanly with zero false negatives', () => {
  const dir = mkdtempSync(join(tmpdir(), 'gram-rebuild-'));
  try {
    const searchDir = join(dir, '.search');
    const docs = makeDocs();
    const index = new GramExclusionIndex(searchDir);
    index.sync(docs);

    const candidates = docs.map((d) => ({
      docId: d.docId,
      contentSha256: d.contentSha256 ?? null,
      size: d.size,
      mtimeNs: d.mtimeNs,
    }));

    // 1. Delete .search directory completely while index is live.
    rmSync(searchDir, { recursive: true, force: true });
    const freshReader = new GramExclusionIndex(searchDir);
    assert.equal(freshReader.excludeNonMatching('uild', candidates).excluded.size, 0, 'missing .search excludes nothing');
    freshReader.sync(docs);
    assert.deepEqual(
      searchWithExclusion(freshReader, docs, 'uild'),
      ['doc:build-fail'],
      'rebuilt .search restores exact match results',
    );

    // 2. Corrupt manifest.json and segment file.
    writeFileSync(join(searchDir, 'manifest.json'), '{not-valid-json', 'utf8');
    const corruptedReader = new GramExclusionIndex(searchDir);
    assert.equal(corruptedReader.excludeNonMatching('猫', candidates).excluded.size, 0, 'corrupt manifest excludes nothing');
    corruptedReader.sync(docs);
    assert.deepEqual(
      searchWithExclusion(corruptedReader, docs, '猫'),
      ['doc:cat-single', 'doc:black-cat'],
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('same-size and same-mtimeNs content rewrite is never falsely excluded thanks to contentSha256', () => {
  const dir = mkdtempSync(join(tmpdir(), 'gram-sha-'));
  try {
    const searchDir = join(dir, '.search');
    const originalText = 'alpha_1234'; // 10 UTF-8 bytes
    const rewrittenText = 'bravo_5678'; // 10 UTF-8 bytes, identical size & mtimeNs!
    assert.equal(Buffer.byteLength(originalText, 'utf8'), Buffer.byteLength(rewrittenText, 'utf8'));

    const v1: IndexedDocInput[] = [
      {
        docId: 'doc:mutable',
        text: originalText,
        size: 10,
        mtimeNs: '1700000000999',
        contentSha256: sha256Text(originalText),
      },
    ];
    const index = new GramExclusionIndex(searchDir);
    index.sync(v1);
    assert.equal(
      index.excludeNonMatching('bravo', [{ docId: 'doc:mutable', contentSha256: v1[0].contentSha256!, size: 10, mtimeNs: '1700000000999' }])
        .excluded.has('doc:mutable'),
      true,
    );

    // Before sync() runs again, the document is rewritten in-place with identical size and restored mtimeNs.
    const v2: IndexedDocInput[] = [
      {
        docId: 'doc:mutable',
        text: rewrittenText,
        size: 10,
        mtimeNs: '1700000000999',
        contentSha256: sha256Text(rewrittenText),
      },
    ];
    const { excluded: excludedBeforeSync } = index.excludeNonMatching('bravo', [
      { docId: 'doc:mutable', contentSha256: v2[0].contentSha256!, size: 10, mtimeNs: '1700000000999' },
    ]);
    assert.equal(
      excludedBeforeSync.has('doc:mutable'),
      false,
      'stale entry with mismatched contentSha256 must NOT exclude rewritten document',
    );
    assert.deepEqual(searchWithExclusion(index, v2, 'bravo'), ['doc:mutable']);

    // And sync() refreshes the postings despite identical size & mtimeNs.
    const syncState = index.sync(v2);
    assert.equal(syncState.rebuilt, true);
    assert.equal(
      index.excludeNonMatching('alpha', [{ docId: 'doc:mutable', contentSha256: v2[0].contentSha256!, size: 10, mtimeNs: '1700000000999' }])
        .excluded.has('doc:mutable'),
      true,
    );
    assert.deepEqual(searchWithExclusion(index, v2, 'bravo'), ['doc:mutable']);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
