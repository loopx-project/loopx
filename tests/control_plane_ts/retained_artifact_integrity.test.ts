import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {dirname, join, resolve} from "node:path";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {settlementIdentity, settlementIdentityPayload} from "../../loopx/control_plane/effect_program.ts";
import {inspectCheckpointReplay} from "../../loopx/control_plane/goals/checkpoint_commit.ts";

const sha = (value: string) => createHash("sha256").update(value).digest("hex");
function fixture(witness: boolean) {
  const root = mkdtempSync(join(tmpdir(), "retained-integrity-")), goal = "retained-fixture";
  assert.equal(dirname(resolve(root)), resolve(tmpdir())); // Bound every recursive cleanup to this temp root.
  const runs = join(root, "goals", goal, "runs"), receipts = join(root, "goals", goal, "checkpoint-contexts");
  mkdirSync(runs, {recursive: true}); mkdirSync(receipts, {recursive: true});
  const identity = settlementIdentityPayload(settlementIdentity({goal_id: goal,
    agent_id: "fixture-agent", todo_id: "todo_result", turn_instance_id: "original-turn"}));
  const row: JsonObject = {settlement_identity: identity, refresh_recovery: {mutation_digest: "original"},
    result_work_context: {read_context_id: "original-work-read"},
    vision_checkpoint: {required: true, satisfied: false, decision: "missing_required"},
    json_path: join(runs, "result.json"), markdown_path: join(runs, "result.md")};
  const json = JSON.stringify({...row, classification: "original-result"}), markdown = "Original result.\n";
  writeFileSync(String(row.json_path), json); writeFileSync(String(row.markdown_path), markdown);
  const receipt = join(receipts, sha(String(identity.effect_id) + ":delivery_result") + ".json");
  if (witness) {
    row.result_artifact_digests = {json_sha256: sha(json), markdown_sha256: sha(markdown)};
    writeFileSync(receipt, JSON.stringify({commit_attempt: {index_record: row}}));
  }
  const index = join(runs, "index.jsonl");
  const saveIndex = () => writeFileSync(index, JSON.stringify(row) + "\n");
  saveIndex();
  return {root, row, index, receipt, saveIndex,
    inspect: () => inspectCheckpointReplay({runtime_root: root, goal_id: goal, prior: row})};
}

test("original witnessed bytes and legacy unmarked records replay without writes", () => {
  for (const witness of [false, true]) {
    const f = fixture(witness);
    try {
      const before = readFileSync(f.index);
      assert.equal(f.inspect().ok, true);
      assert.deepEqual(readFileSync(f.index), before);
    } finally { rmSync(f.root, {recursive: true, force: true}); }
  }
});

test("artifact replacement, witness loss and index downgrade stay unknown", () => {
  const corruptions = [
    (f: ReturnType<typeof fixture>) => writeFileSync(String(f.row.json_path), "{}"),
    (f: ReturnType<typeof fixture>) => writeFileSync(String(f.row.markdown_path), "Changed result.\n"),
    (f: ReturnType<typeof fixture>) => rmSync(f.receipt),
    (f: ReturnType<typeof fixture>) => { delete f.row.result_artifact_digests; f.saveIndex(); },
    (f: ReturnType<typeof fixture>) => { f.row.result_artifact_digests = {}; f.saveIndex(); },
    (f: ReturnType<typeof fixture>) => writeFileSync(f.index, readFileSync(f.index, "utf8") +
      JSON.stringify({...f.row, result_artifact_digests: {json_sha256: "0".repeat(64),
        markdown_sha256: "0".repeat(64)}}) + "\n"),
  ];
  for (const corrupt of corruptions) {
    const f = fixture(true);
    try {
      corrupt(f);
      const before = readFileSync(f.index);
      assert.throws(f.inspect, (error: unknown) =>
        (error as {code?: string}).code === "checkpoint_commit_unknown");
      assert.deepEqual(readFileSync(f.index), before);
    } finally { rmSync(f.root, {recursive: true, force: true}); }
  }
});
