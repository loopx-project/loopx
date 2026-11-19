import assert from "node:assert/strict";
import {execFileSync, spawnSync} from "node:child_process";
import {existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {dirname, join} from "node:path";
import {fileURLToPath} from "node:url";
import {test} from "node:test";
import {assess, contextInput, exportSnapshot, loadQueries, verifyCitations} from "./evidence.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "../..");
const PYTHON = join(ROOT, process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");

function fixture(t: {after: (cleanup: () => void) => void}) {
  const temporary = mkdtempSync(join(tmpdir(), "context-pilot-test-"));
  t.after(() => rmSync(temporary, {recursive: true, force: true}));
  const repository = join(temporary, "repo"); mkdirSync(repository);
  const git = (...args: string[]) => execFileSync("git", ["-C", repository, ...args], {encoding: "utf8", stdio: "pipe"}).trim();
  git("init", "--quiet"); git("config", "user.name", "Synthetic Fixture"); git("config", "user.email", "fixture@example.invalid");
  writeFileSync(join(repository, "source.ts"), "export const visible = 1;\n");
  git("add", "source.ts"); git("commit", "--quiet", "-m", "synthetic source");
  return {temporary, repository, revision: git("rev-parse", "HEAD"), git};
}

test("an immutable export excludes dirty/untracked work and provider-owned indexes", t => {
  const f = fixture(t), snapshot = join(f.temporary, "snapshot"); mkdirSync(snapshot);
  mkdirSync(join(f.repository, ".rune")); writeFileSync(join(f.repository, ".rune", "graph.json"), "{}");
  f.git("add", ".rune/graph.json"); f.git("commit", "--quiet", "-m", "synthetic old index");
  const revision = f.git("rev-parse", "HEAD");
  writeFileSync(join(f.repository, "source.ts"), "changed working copy\n");
  writeFileSync(join(f.repository, "private-untracked.txt"), "never export this\n");
  exportSnapshot(f.repository, revision, snapshot);
  assert.equal(readFileSync(join(snapshot, "source.ts"), "utf8"), "export const visible = 1;\n");
  assert.equal(existsSync(join(snapshot, "private-untracked.txt")), false);
  assert.equal(existsSync(join(snapshot, ".rune")), false);
  assert.equal(readFileSync(join(f.repository, "source.ts"), "utf8"), "changed working copy\n");
});

test("citations require exact current source at the claimed line and cannot escape", t => {
  const f = fixture(t);
  writeFileSync(join(f.temporary, "outside.ts"), "external data\n");
  const good = {file: "source.ts", line: 1, snippet: "export const visible = 1;"};
  const checked = verifyCitations(f.repository, [good,
    {...good, line: 2}, {...good, snippet: "export const visible = 2;"},
    {file: "../outside.ts", line: 1, snippet: "external data"}, {...good, line: 1.5}], 8);
  assert.deepEqual(checked.evidence.map(x => [x.file, x.line]), [["source.ts", 1]]);
  assert.equal(checked.rejected, 4);
  const context = contextInput(f.revision, checked.evidence);
  assert.equal(context.sources[0].trust, "advisory");
  assert.deepEqual(context.sources[0].supports, ["change_scope"]);
  assert.equal(JSON.stringify(context).includes("export const"), false);
});

test("a symlink cannot turn an outside file into repository evidence", {skip: process.platform === "win32"}, t => {
  const f = fixture(t);
  writeFileSync(join(f.temporary, "outside.ts"), "outside\n");
  symlinkSync(join(f.temporary, "outside.ts"), join(f.repository, "alias.ts"));
  assert.equal(verifyCitations(f.repository, [{file: "alias.ts", line: 1, snippet: "outside"}], 8).rejected, 1);
});

test("coverage counts unique reference files and preserves an incorrect non-answer", () => {
  const evidence = [{file: "other.ts", line: 1, sha256: "a"}];
  assert.deepEqual(assess(evidence, ["owner.ts"]).missing_relevant_files, ["owner.ts"]);
  assert.equal(assess(evidence, []).unanswerable_abstained, false);
  assert.equal(assess([], []).unanswerable_abstained, true);
});

test("a provider cannot make changed source current by citing its own replacement text", t => {
  const f = fixture(t), snapshot = join(f.temporary, "snapshot"); mkdirSync(snapshot);
  const source = exportSnapshot(f.repository, f.revision, snapshot);
  writeFileSync(join(snapshot, "source.ts"), "export const visible = 999;\n");
  const checked = verifyCitations(snapshot, [{file: "source.ts", line: 1, snippet: "export const visible = 999;"}], 8, source.source_hashes);
  assert.equal(checked.evidence.length, 0);
  assert.equal(checked.rejected, 1);
});

test("duplicate or empty query identities cannot silently overwrite experiment rows", () => {
  const query = {id: "one", issue: "https://github.com/example/repo/issues/1", objective: "visible", terms: ["visible"], relevant_files: []};
  assert.throws(() => loadQueries([query, query]), /invalid_query_id/u);
  assert.throws(() => loadQueries([{...query, terms: []}]), /missing_search_terms/u);
});

function runPilot(f: ReturnType<typeof fixture>, name: string, extra: string[]) {
  const queryFile = join(f.temporary, "queries.json"), output = join(f.temporary, name);
  writeFileSync(queryFile, JSON.stringify([{id: "visible", issue: "https://github.com/loopx-project/loopx/issues/4926",
    objective: "find visible", terms: ["visible"], relevant_files: ["source.ts"]}]));
  const result = spawnSync(process.execPath, ["--experimental-strip-types", join(HERE, "run.ts"),
    "--repository", f.repository, "--revision", f.revision, "--queries", queryFile, "--out", output, ...extra],
  {cwd: ROOT, encoding: "utf8", timeout: 40_000});
  return {result, report: JSON.parse(readFileSync(join(output, "report.json"), "utf8")), output};
}

test("the default direct-search path consumes context through the real CLI without Rune", {skip: !existsSync(PYTHON)}, t => {
  const f = fixture(t), {result, report} = runPilot(f, "direct", ["--loopx-python", PYTHON]);
  assert.equal(result.status, 0, result.stderr);
  const direct = report.rows.find((row: {provider: string}) => row.provider === "direct-search");
  assert.deepEqual(direct.relevant_files_found, ["source.ts"]);
  assert.equal(direct.native_plan, "accepted_read_only");
  assert.equal(direct.native_context_revision, f.revision);
  assert.equal(report.rows.find((row: {provider: string}) => row.provider === "rune").status, "disabled");
});

test("unavailable optional providers retain the successful direct-search observation", t => {
  const f = fixture(t), {result, report} = runPilot(f, "missing-provider", ["--rune-source", join(f.temporary, "missing")]);
  assert.equal(result.status, 1);
  assert.equal(report.failed, true);
  assert.deepEqual(report.rows[0].relevant_files_found, ["source.ts"]);
  assert.equal(report.rows[1].status, "unavailable");
});

test("failed native readback remains distinct from successful retrieval", t => {
  const f = fixture(t), {result, report} = runPilot(f, "missing-native", ["--loopx-python", join(f.temporary, "missing-python")]);
  assert.equal(result.status, 1);
  assert.equal(report.rows[0].status, "completed");
  assert.deepEqual(report.rows[0].relevant_files_found, ["source.ts"]);
  assert.equal(report.rows[0].native_plan, "unavailable");
});

test("a later attempt cannot overwrite the first observation", t => {
  const f = fixture(t), first = runPilot(f, "retained", []);
  const before = readFileSync(join(first.output, "report.json"), "utf8");
  const second = runPilot(f, "retained", []);
  assert.equal(second.result.status, 1);
  assert.equal(readFileSync(join(first.output, "report.json"), "utf8"), before);
});
