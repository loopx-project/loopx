/** Local experiment formats; LoopX's existing issue-fix contract is unchanged. */
import {createHash} from "node:crypto";
import {execFileSync} from "node:child_process";
import {lstatSync, readFileSync, readdirSync, realpathSync, rmSync} from "node:fs";
import {dirname, isAbsolute, join, relative, sep} from "node:path";

export interface Query {
  id: string;
  issue: string;
  objective: string;
  terms: string[];
  relevant_files: string[];
}

export interface Citation {file: string; line: number; snippet: string}
export interface Evidence {file: string; line: number; sha256: string}

export const digest = (text: string | Buffer): string =>
  createHash("sha256").update(text).digest("hex");

export function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("invalid_object");
  return value as Record<string, unknown>;
}

export function loadQueries(value: unknown): Query[] {
  if (!Array.isArray(value) || value.length < 1 || value.length > 12) throw new Error("invalid_queries");
  const ids = new Set<string>();
  return value.map(raw => {
    const row = object(raw);
    if (typeof row.id !== "string" || !/^[a-z0-9-]{1,64}$/u.test(row.id) || ids.has(row.id))
      throw new Error("invalid_query_id");
    ids.add(row.id);
    if (typeof row.issue !== "string" || !/^https:\/\/github\.com\/[^/]+\/[^/]+\/issues\/\d+$/u.test(row.issue) ||
        typeof row.objective !== "string" || !row.objective.trim() || row.objective.length > 500)
      throw new Error("invalid_query");
    for (const key of ["terms", "relevant_files"]) {
      if (!Array.isArray(row[key]) || row[key].length > 8 || row[key].some(x => typeof x !== "string" || !x || x.length > 260))
        throw new Error("invalid_query_list");
    }
    if (!(row.terms as string[]).length) throw new Error("missing_search_terms");
    return row as unknown as Query;
  });
}

export function exportSnapshot(repository: string, revision: string, destination: string): {
  archive_bytes: number; source_hashes: Map<string, string>;
} {
  if (!/^[a-f0-9]{40}$/u.test(revision)) throw new Error("immutable_revision_required");
  const resolved = execFileSync("git", ["-C", repository, "rev-parse", "--verify", `${revision}^{commit}`],
    {encoding: "utf8", timeout: 10_000}).trim();
  if (resolved !== revision) throw new Error("revision_mismatch");
  const archive = join(dirname(destination), "source.tar");
  let archiveBytes: number;
  try {
    execFileSync("git", ["-C", repository, "archive", "--format=tar", `--output=${archive}`, revision],
      {timeout: 30_000, stdio: "pipe"});
    archiveBytes = lstatSync(archive).size;
    execFileSync("tar", ["-xf", archive, "-C", destination], {timeout: 30_000, stdio: "pipe"});
  } finally { rmSync(archive, {force: true}); }
  // Never give the provider an existing index, a live worktree or an escape link.
  const sourceHashes = new Map<string, string>();
  function prune(directory: string): void {
    for (const entry of readdirSync(directory, {withFileTypes: true})) {
      const path = join(directory, entry.name);
      if (entry.isSymbolicLink() || entry.name === ".rune") rmSync(path, {recursive: true, force: true});
      else if (entry.isDirectory()) prune(path);
      else if (entry.isFile()) sourceHashes.set(relative(destination, path).split(sep).join("/"), digest(readFileSync(path)));
    }
  }
  prune(destination);
  return {archive_bytes: archiveBytes, source_hashes: sourceHashes};
}

export function verifyCitations(root: string, raw: unknown[], limit: number, sourceHashes?: Map<string, string>): {
  evidence: Evidence[]; rejected: number; omitted: number;
} {
  const evidence: Evidence[] = [], seen = new Set<string>();
  let rejected = 0, omitted = 0;
  for (const candidate of raw) {
    try {
      const row = object(candidate);
      if (typeof row.file !== "string" || !row.file || row.file.includes("\\") ||
          row.file.split("/").some(part => !part || part === "." || part === "..") || isAbsolute(row.file) ||
          !Number.isSafeInteger(row.line) || Number(row.line) < 1 || typeof row.snippet !== "string" ||
          !row.snippet.trim() || row.snippet.length > 2048) throw new Error("invalid_citation");
      const path = realpathSync(join(root, row.file));
      const contained = relative(realpathSync(root), path);
      if (!contained || contained.startsWith(`..${sep}`) || contained === ".." || isAbsolute(contained) ||
          !lstatSync(path).isFile() || lstatSync(path).size > 2 * 1024 * 1024) throw new Error("outside_source");
      const bytes = readFileSync(path);
      if (sourceHashes && sourceHashes.get(row.file) !== digest(bytes)) throw new Error("source_changed");
      const text = bytes.toString("utf8").replace(/\r\n/gu, "\n");
      const line = Number(row.line), snippet = row.snippet.trim().replace(/\r\n/gu, "\n");
      const window = text.split("\n").slice(line - 1, line - 1 + snippet.split("\n").length).join("\n");
      if (!window.includes(snippet)) throw new Error("stale_citation");
      if (seen.has(row.file)) continue;
      seen.add(row.file);
      if (evidence.length === limit) { omitted += 1; continue; }
      evidence.push({file: row.file, line, sha256: digest(bytes)});
    } catch { rejected += 1; }
  }
  return {evidence, rejected, omitted};
}

export function assess(evidence: Evidence[], expected: string[]) {
  const relevant = new Set(expected), returned = new Set(evidence.map(x => x.file));
  const found = [...returned].filter(file => relevant.has(file));
  return {
    relevant_file_count: relevant.size, relevant_files_found: found,
    missing_relevant_files: [...relevant].filter(file => !returned.has(file)),
    unexpected_files: [...returned].filter(file => !relevant.has(file)),
    recall: relevant.size ? found.length / relevant.size : null,
    precision: returned.size ? found.length / returned.size : null,
    unanswerable_abstained: relevant.size ? null : returned.size === 0,
  };
}

export function contextInput(revision: string, evidence: Evidence[]) {
  return {
    schema_version: "issue_fix_repository_context_input_v0", repository_revision: revision,
    sources: evidence.map(row => ({
      source_id: `context-${digest(row.file).slice(0, 16)}`, source_kind: "knowledge_bundle",
      reference: row.file, trust: "advisory", freshness: "current", supports: ["change_scope"],
      summary: `Source-checked citation at line ${row.line}; file SHA-256 ${row.sha256}. Verify relevance before acting.`,
    })),
  };
}
