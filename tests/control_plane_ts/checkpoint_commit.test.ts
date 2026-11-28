import assert from "node:assert/strict";
import {linkSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join, resolve} from "node:path";
import test from "node:test";

import {inspectCheckpointReplay} from "../../loopx/control_plane/goals/checkpoint_commit.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {EffectRuntimeRequestError} from "../../loopx/control_plane/effect_runtime_errors.ts";

const goalId = "goal-checkpoint-relocate";
const jsonName = "2026-08-25T12-00-00Z-checkpoint.json";
const markdownName = "2026-08-25T12-00-00Z-checkpoint.md";
const invalidPath = /checkpoint artifact path is invalid/;

function runtime(t: test.TestContext): string {
  const root = mkdtempSync(join(tmpdir(), "loopx-checkpoint-replay-"));
  t.after(() => rmSync(root, {recursive: true, force: true}));
  return root;
}

function checkpointFields(): JsonObject {
  return {
    settlement_identity: {effect_id: "effect-checkpoint-1"},
    refresh_recovery: {decision: "replay", vision_request_digest: "digest-1"},
    vision_checkpoint: {
      satisfied: true,
      read_context: {schema_version: "checkpoint_read_context_v0", id: "ctx-1"},
    },
  };
}

function writeJson(path: string, marker: string): void {
  writeFileSync(path, `${JSON.stringify({...checkpointFields(), artifact_bytes: marker}, null, 2)}\n`);
}

function indexPrior(jsonPath: string, markdownPath: string): JsonObject {
  return {
    goal_id: goalId,
    ...checkpointFields(),
    json_path: jsonPath,
    markdown_path: markdownPath,
  };
}

function writeIndex(runsDir: string, prior: JsonObject): void {
  writeFileSync(join(runsDir, "index.jsonl"), `${JSON.stringify(prior)}\n`);
}

function inspect(root: string, prior: JsonObject): JsonObject {
  return inspectCheckpointReplay({runtime_root: root, goal_id: goalId, prior});
}

function relocatedFixture(t: test.TestContext): {
  originalRoot: string;
  relocatedRoot: string;
  originalJson: string;
  originalMarkdown: string;
  relocatedRuns: string;
  relocatedJson: string;
  relocatedMarkdown: string;
  prior: JsonObject;
} {
  const originalRoot = runtime(t);
  const relocatedRoot = runtime(t);
  const originalRuns = join(originalRoot, "goals", goalId, "runs");
  const relocatedRuns = join(relocatedRoot, "goals", goalId, "runs");
  mkdirSync(originalRuns, {recursive: true});
  mkdirSync(relocatedRuns, {recursive: true});
  const originalJson = join(originalRuns, jsonName);
  const originalMarkdown = join(originalRuns, markdownName);
  const relocatedJson = join(relocatedRuns, jsonName);
  const relocatedMarkdown = join(relocatedRuns, markdownName);
  writeJson(originalJson, "stale-original");
  writeFileSync(originalMarkdown, "stale markdown\n");
  writeJson(relocatedJson, "relocated-runtime");
  writeFileSync(relocatedMarkdown, "relocated markdown\n");
  const prior = indexPrior(originalJson, originalMarkdown);
  writeIndex(relocatedRuns, prior);
  return {
    originalRoot, relocatedRoot, originalJson, originalMarkdown,
    relocatedRuns, relocatedJson, relocatedMarkdown, prior,
  };
}

function rejectPrior(runsDir: string, root: string, jsonPath: string, markdownPath: string): void {
  const prior = indexPrior(jsonPath, markdownPath);
  writeIndex(runsDir, prior);
  assert.throws(() => inspect(root, prior), invalidPath);
}

function linkOrSkip(t: test.TestContext, existing: string, path: string): void {
  try {
    linkSync(existing, path);
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code === "EPERM" || code === "ENOTSUP" || code === "EOPNOTSUPP" || code === "EXDEV" || code === "ENOSYS") {
      t.skip(`hard links are unavailable on this platform (${code})`);
    }
    throw error;
  }
}

function assertExclusiveHardLink(path: string, outside: string): void {
  const linked = lstatSync(path);
  const target = lstatSync(outside);
  assert.equal(linked.isSymbolicLink(), false);
  assert.equal(linked.isFile(), true);
  assert.equal(linked.nlink, 2);
  assert.equal(linked.ino, target.ino);
}

function assertHardLinkRejected(root: string, prior: JsonObject, message: RegExp): void {
  assert.throws(
    () => inspect(root, prior),
    (error: unknown) => {
      assert.ok(error instanceof EffectRuntimeRequestError);
      assert.equal(error.code, "checkpoint_commit_unknown");
      assert.match(error.message, message);
      return true;
    },
  );
}

test("inspectCheckpointReplay reads checkpoint artifacts copied into a relocated runtime", (t) => {
  const fixture = relocatedFixture(t);
  const result = inspect(fixture.relocatedRoot, fixture.prior);
  const runsDir = realpathSync(fixture.relocatedRuns);
  assert.equal(result.ok, true);
  assert.equal(result.replayed, true);
  assert.equal(result.json_path, join(runsDir, jsonName));
  assert.equal(result.markdown_path, join(runsDir, markdownName));
  assert.match(readFileSync(String(result.json_path), "utf8"), /"artifact_bytes": "relocated-runtime"/);
  assert.equal(readFileSync(String(result.markdown_path), "utf8"), "relocated markdown\n");
  assert.match(readFileSync(fixture.originalJson, "utf8"), /"artifact_bytes": "stale-original"/);
  assert.notEqual(resolve(String(result.json_path)), resolve(fixture.originalJson));
});

test("inspectCheckpointReplay keeps reading an artifact already inside the current runs directory", (t) => {
  const root = runtime(t);
  const runsDir = join(root, "goals", goalId, "runs");
  mkdirSync(runsDir, {recursive: true});
  const jsonPath = join(runsDir, jsonName);
  const markdownPath = join(runsDir, markdownName);
  writeJson(jsonPath, "current-runtime");
  writeFileSync(markdownPath, "current markdown\n");
  const prior = indexPrior(jsonPath, markdownPath);
  writeIndex(runsDir, prior);
  const result = inspect(root, prior);
  assert.equal(result.json_path, resolve(jsonPath));
  assert.equal(result.markdown_path, resolve(markdownPath));
  assert.match(readFileSync(String(result.json_path), "utf8"), /"artifact_bytes": "current-runtime"/);
});

test("inspectCheckpointReplay rejects checkpoint paths outside the current goal runs directory", (t) => {
  const fixture = relocatedFixture(t);
  rmSync(fixture.relocatedJson);
  rmSync(fixture.relocatedMarkdown);
  rejectPrior(fixture.relocatedRuns, fixture.relocatedRoot, fixture.originalJson, fixture.originalMarkdown);

  const restored = relocatedFixture(t);
  const otherRuns = join(restored.originalRoot, "goals", "other-goal", "runs");
  mkdirSync(otherRuns, {recursive: true});
  const otherJson = join(otherRuns, jsonName);
  const otherMarkdown = join(otherRuns, markdownName);
  writeJson(otherJson, "other-goal");
  writeFileSync(otherMarkdown, "other goal\n");
  rejectPrior(restored.relocatedRuns, restored.relocatedRoot, otherJson, otherMarkdown);

  const outsideJson = join(restored.originalRoot, "outside.json");
  const outsideMarkdown = join(restored.originalRoot, "outside.md");
  writeJson(outsideJson, "outside");
  writeFileSync(outsideMarkdown, "outside markdown\n");
  writeJson(join(restored.relocatedRuns, "outside.json"), "relocated-runtime");
  writeFileSync(join(restored.relocatedRuns, "outside.md"), "relocated markdown\n");
  rejectPrior(restored.relocatedRuns, restored.relocatedRoot, outsideJson, outsideMarkdown);

  const traversedJson = join(restored.originalRoot, "goals", goalId, "runs", "..", "..", "escaped.json");
  const traversedMarkdown = join(restored.originalRoot, "goals", goalId, "runs", "..", "..", "escaped.md");
  writeJson(resolve(traversedJson), "escaped");
  writeFileSync(resolve(traversedMarkdown), "escaped markdown\n");
  rejectPrior(restored.relocatedRuns, restored.relocatedRoot, traversedJson, traversedMarkdown);
});

test("inspectCheckpointReplay rejects a relocated symlink or non-file artifact", (t) => {
  const linked = relocatedFixture(t);
  const outside = join(linked.relocatedRoot, "linked-outside.json");
  writeJson(outside, "symlink-escape");
  rmSync(linked.relocatedJson);
  symlinkSync(outside, linked.relocatedJson);
  rejectPrior(linked.relocatedRuns, linked.relocatedRoot, linked.originalJson, linked.originalMarkdown);

  const internal = relocatedFixture(t);
  const alias = join(internal.relocatedRuns, "alias.json");
  writeJson(alias, "internal-alias");
  rmSync(internal.relocatedJson);
  symlinkSync(alias, internal.relocatedJson);
  rejectPrior(internal.relocatedRuns, internal.relocatedRoot, internal.originalJson, internal.originalMarkdown);

  const directory = relocatedFixture(t);
  rmSync(directory.relocatedJson);
  mkdirSync(directory.relocatedJson);
  rejectPrior(directory.relocatedRuns, directory.relocatedRoot, directory.originalJson, directory.originalMarkdown);
});

test("inspectCheckpointReplay rejects a relocated hard link with matching checkpoint bytes", (t) => {
  const linkedJson = relocatedFixture(t);
  const jsonBytes = readFileSync(linkedJson.originalJson);
  const markdownBytes = readFileSync(linkedJson.relocatedMarkdown);
  const indexBytes = readFileSync(join(linkedJson.relocatedRuns, "index.jsonl"));
  rmSync(linkedJson.relocatedJson);
  linkOrSkip(t, linkedJson.originalJson, linkedJson.relocatedJson);
  assertExclusiveHardLink(linkedJson.relocatedJson, linkedJson.originalJson);
  assertHardLinkRejected(linkedJson.relocatedRoot, linkedJson.prior, invalidPath);
  assert.deepEqual(readFileSync(linkedJson.originalJson), jsonBytes);
  assert.deepEqual(readFileSync(linkedJson.relocatedMarkdown), markdownBytes);
  assert.deepEqual(readFileSync(join(linkedJson.relocatedRuns, "index.jsonl")), indexBytes);
  assertExclusiveHardLink(linkedJson.relocatedJson, linkedJson.originalJson);

  const linkedMarkdown = relocatedFixture(t);
  const sharedMarkdown = readFileSync(linkedMarkdown.originalMarkdown);
  const markdownIndex = readFileSync(join(linkedMarkdown.relocatedRuns, "index.jsonl"));
  rmSync(linkedMarkdown.relocatedMarkdown);
  linkOrSkip(t, linkedMarkdown.originalMarkdown, linkedMarkdown.relocatedMarkdown);
  assertExclusiveHardLink(linkedMarkdown.relocatedMarkdown, linkedMarkdown.originalMarkdown);
  assertHardLinkRejected(linkedMarkdown.relocatedRoot, linkedMarkdown.prior, invalidPath);
  assert.deepEqual(readFileSync(linkedMarkdown.originalMarkdown), sharedMarkdown);
  assert.deepEqual(readFileSync(join(linkedMarkdown.relocatedRuns, "index.jsonl")), markdownIndex);
  assertExclusiveHardLink(linkedMarkdown.relocatedMarkdown, linkedMarkdown.originalMarkdown);
});

test("inspectCheckpointReplay rejects an in-directory hard link to an outside file", (t) => {
  const root = runtime(t);
  const runsDir = join(root, "goals", goalId, "runs");
  mkdirSync(runsDir, {recursive: true});
  const jsonPath = join(runsDir, jsonName);
  const markdownPath = join(runsDir, markdownName);
  writeJson(jsonPath, "current-runtime");
  writeFileSync(markdownPath, "current markdown\n");
  const outsideJson = join(root, "outside-checkpoint.json");
  const outsideMarkdown = join(root, "outside-checkpoint.md");
  writeJson(outsideJson, "current-runtime");
  writeFileSync(outsideMarkdown, "current markdown\n");
  const outsideJsonBytes = readFileSync(outsideJson);
  const outsideMarkdownBytes = readFileSync(outsideMarkdown);

  rmSync(jsonPath);
  linkOrSkip(t, outsideJson, jsonPath);
  const jsonPrior = indexPrior(jsonPath, markdownPath);
  writeIndex(runsDir, jsonPrior);
  const indexBytes = readFileSync(join(runsDir, "index.jsonl"));
  assertExclusiveHardLink(jsonPath, outsideJson);
  assertHardLinkRejected(root, jsonPrior, /checkpoint artifact is not a regular file/);
  assert.deepEqual(readFileSync(outsideJson), outsideJsonBytes);
  assert.deepEqual(readFileSync(markdownPath), Buffer.from("current markdown\n"));
  assert.deepEqual(readFileSync(join(runsDir, "index.jsonl")), indexBytes);
  assertExclusiveHardLink(jsonPath, outsideJson);

  rmSync(jsonPath);
  writeJson(jsonPath, "current-runtime");
  const restoredJson = readFileSync(jsonPath);
  rmSync(markdownPath);
  linkOrSkip(t, outsideMarkdown, markdownPath);
  const markdownPrior = indexPrior(jsonPath, markdownPath);
  writeIndex(runsDir, markdownPrior);
  const markdownIndex = readFileSync(join(runsDir, "index.jsonl"));
  assertExclusiveHardLink(markdownPath, outsideMarkdown);
  assertHardLinkRejected(root, markdownPrior, /checkpoint artifact is not a regular file/);
  assert.deepEqual(readFileSync(outsideMarkdown), outsideMarkdownBytes);
  assert.deepEqual(readFileSync(jsonPath), restoredJson);
  assert.deepEqual(readFileSync(join(runsDir, "index.jsonl")), markdownIndex);
  assertExclusiveHardLink(markdownPath, outsideMarkdown);
});
