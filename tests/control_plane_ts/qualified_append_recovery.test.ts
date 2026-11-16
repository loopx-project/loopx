import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {dirname, join, resolve} from "node:path";
import test from "node:test";
import {settlementIdentityPayload} from "../../loopx/control_plane/effect_program.ts";
import {inspectCheckpointAttempt} from "../../loopx/control_plane/goals/checkpoint_commit.ts";
import {evaluateCheckpointReadContext} from "../../loopx/control_plane/goals/checkpoint_read_context.ts";

function fixture() {
  const root = mkdtempSync(join(tmpdir(), "qualified-attempt-"));
  assert.equal(dirname(resolve(root)), resolve(tmpdir()));
  const identity = settlementIdentityPayload({goal_id: "qualified-goal", agent_id: "agent",
    todo_id: "todo_original", turn_instance_id: "original-turn"});
  const contexts = join(root, "goals", identity.goal_id as string, "checkpoint-contexts");
  const runs = join(root, "goals", identity.goal_id as string, "runs");
  mkdirSync(contexts, {recursive: true}); mkdirSync(runs, {recursive: true});
  const json = join(runs, "qualified.json"), markdown = join(runs, "qualified.md");
  writeFileSync(json, ""); writeFileSync(markdown, "");
  const receipt = {schema_version: "checkpoint_read_context_v3", purpose: "supplement_checkpoint",
    identity, read_context_id: "original-read", commit_attempt: {json_path: json, markdown_path: markdown,
      vision_request_digest: "original-request", index_record: {settlement_identity: identity}}};
  const path = join(contexts, createHash("sha256").update(String(identity.effect_id)).digest("hex") + ".json");
  writeFileSync(path, JSON.stringify(receipt));
  return {root, identity, receipt, path, json, inspect: (otherTurn = false) => inspectCheckpointAttempt({
    runtime_root: root, identity: otherTurn ? settlementIdentityPayload({
      goal_id: "qualified-goal", agent_id: "agent", todo_id: "todo_original", turn_instance_id: "replacement-turn"}) : identity,
    purpose: "supplement_checkpoint", check_other_attempts: otherTurn})};
}

test("empty Q reservations prove absence without changing the original read or receipt", () => {
  const f = fixture();
  try {
    const before = readFileSync(f.path);
    const result = f.inspect();
    assert.equal(result.status, "absent");
    assert.equal(result.read_context_id, "original-read");
    assert.equal((result.receipt as Record<string, unknown>).commit_attempt, undefined);
    assert.deepEqual(readFileSync(f.path), before); // Inspector does not write authority.
  } finally { rmSync(f.root, {recursive: true, force: true}); }
});

test("partial Q bytes and another Turn cannot erase the original uncertain attempt", () => {
  const f = fixture();
  try {
    writeFileSync(f.json, "partial qualified result");
    const before = readFileSync(f.path);
    for (const other of [false, true]) assert.throws(() => f.inspect(other),
      (error: unknown) => (error as {code?: string}).code === "checkpoint_commit_unknown");
    assert.deepEqual(readFileSync(f.path), before);
    const read = evaluateCheckpointReadContext({phase: "read", purpose: "supplement_checkpoint",
      checkpoint_continuation: true, identity: f.identity, receipt: f.receipt, facts: {}, read_context_id: "replacement"});
    assert.equal(read.error_code, "checkpoint_commit_unknown");
    assert.equal(read.reread_required, false);
  } finally { rmSync(f.root, {recursive: true, force: true}); }
});
