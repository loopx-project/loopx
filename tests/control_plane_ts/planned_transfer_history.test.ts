import assert from "node:assert/strict";
import {copyFile, mkdtemp, mkdir, readFile, readdir, realpath, rename, rm, symlink, writeFile} from "node:fs/promises";
import {join} from "node:path";
import {tmpdir} from "node:os";
import test, {type TestContext} from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {evaluateQuotaSpendCommit, QUOTA_SPEND_COMMIT_REQUEST_SCHEMA} from "../../loopx/control_plane/quota/spend_commit.ts";
import {evaluateQuotaVoidCommit, QUOTA_VOID_COMMIT_REQUEST_SCHEMA} from "../../loopx/control_plane/quota/void_commit.ts";
import {readGoalQuotaHistoryForTransfer} from "../../loopx/control_plane/quota/accounting_artifact_transaction.ts";
import {readGoalTurnHistoryForTransfer} from "../../loopx/control_plane/turn_driver/turn_journal_query.ts";
import {decodeGoalTransferHistory, goalTransferHistory, goalTransferHistoryInputs,
  inspectGoalTransferHistory, retainGoalTransferHistory} from "../../loopx/control_plane/goals/planned_transfer_history.ts";

async function fixture(t: TestContext) {
  const directory = await realpath(await mkdtemp(join(tmpdir(), "loopx-transfer-history-")));
  t.after(() => rm(directory, {recursive: true, force: true}));
  const root = join(directory, "source"), destination = join(directory, "destination"), incoming = join(directory, "incoming");
  await mkdir(incoming); await mkdir(destination);
  const before = {should_run: true, normal_delivery_allowed: true, recovery_delivery_allowed: false,
    effective_action: "advance", self_repair_allowed: false, capability_repair_allowed: false,
    workspace_repair_allowed: false, state: "eligible", safe_bypass_allowed: false, safe_bypass_kind: null,
    blocked_action_scope: null, compute: 1, window_hours: 24, slot_minutes: 1, spent_slots: 0, allowed_slots: 1440};
  const after = {...before, spent_slots: 1};
  const spend = await evaluateQuotaSpendCommit({schema_version: QUOTA_SPEND_COMMIT_REQUEST_SCHEMA,
    effect_id: "original-effect#quota_spend", runtime_root: root, goal_id: "report", source: "heartbeat",
    generated_at: "2026-08-25T12:00:00+08:00", execute: true, expected_index_digest: null,
    resolved_agent_id: "agent-a", before, after,
    preview: {ok: true, mode: "spend-slot", dry_run: true, goal_id: "report", slots: 1, agent_id: "agent-a",
      before, after, appended: false, registry_mutated: false, delivery_completion_spend: false,
      safe_bypass_spend: false, delivery_workspace_validated: false}});
  assert.equal(spend.status, "written");
  const index = String(spend.payload.index_path);
  const voided = await evaluateQuotaVoidCommit({schema_version: QUOTA_VOID_COMMIT_REQUEST_SCHEMA,
    operation: "commit", effect_id: "original-void", runtime_root: root, goal_id: "report",
    voided_run_generated_at: "2026-08-25T12:00:00+08:00", source: "heartbeat", reason_summary: "Original correction",
    generated_at: "2026-08-25T12:01:00+08:00", execute: true,
    expected_index_digest: spend.index_digest,
    before: {...after, quota: {spent_slots: 1, allowed_slots: 1440, window_hours: 24, compute: 1, slot_minutes: 1}}});
  assert.equal(voided.status, "written");
  const quota = await readGoalQuotaHistoryForTransfer(root, "report");
  assert.equal(quota.closed, true); assert.equal((quota.receipts as JsonObject[]).length, 2);
  const manifest = goalTransferHistory(root, "report", await readGoalTurnHistoryForTransfer(root, "report"), quota);
  const originalFiles = await goalTransferHistoryInputs(manifest);
  const files = [];
  for (const [n, file] of originalFiles.entries()) {
    const path = join(incoming, String(n)); await copyFile(file.path, path); files.push({...file, path});
  }
  const original = await readFile(index);
  await rename(root, `${root}-unavailable`);
  return {directory, root, destination, input: {manifest, files}, quota, original};
}

test("retained spend/void content passes original owners after source loss, with byte-identical receipts", async t => {
  const f = await fixture(t);
  const result = await retainGoalTransferHistory(f.destination, f.input, true, false, true);
  assert.equal(result?.historical_only, true);
  assert.equal(result?.runtime_accounting_adopted, false);
  assert.equal(result?.execution_authority_granted, false);
  const retained = join(f.destination, "runtime-history");
  assert.deepEqual(await readFile(join(retained, "goals/report/runs/index.jsonl")), f.original);
  assert.deepEqual(await readGoalQuotaHistoryForTransfer(f.root, "report", retained), f.quota);
  const replay = await evaluateQuotaSpendCommit({schema_version: QUOTA_SPEND_COMMIT_REQUEST_SCHEMA,
    operation: "replay", runtime_root: retained, goal_id: "report", effect_id: "original-effect#quota_spend",
    resolved_agent_id: "agent-a", read_only: true});
  assert.equal(replay.status, "replayed"); assert.equal(replay.payload.appended, false);
  assert.deepEqual(await retainGoalTransferHistory(f.destination, f.input, true, true, true), result);
  assert.deepEqual(await inspectGoalTransferHistory(retained, f.input.manifest), result);
  // Historical bodies remain original, including their source addresses.
  const transaction = f.input.manifest.files.find(row => row.relative_path.includes("quota-spend/"))!;
  const bytes = await readFile(join(retained, "goals/report", transaction.relative_path));
  assert.match(bytes.toString(), new RegExp(f.root.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  assert.deepEqual(bytes, await readFile(join(`${f.root}-unavailable`, "goals/report", transaction.relative_path)));
});

test("missing, changed, linked and later history is never repaired from inbound bytes", async t => {
  const f = await fixture(t);
  await retainGoalTransferHistory(f.destination, f.input, true, false, true);
  const root = join(f.destination, "runtime-history"), file = f.input.manifest.files.find(row => row.relative_path.endsWith(".json"))!;
  const path = join(root, "goals/report", file.relative_path), original = await readFile(path);
  await writeFile(path, Buffer.concat([original, Buffer.from("\n")]));
  await assert.rejects(retainGoalTransferHistory(f.destination, f.input, true, true, true), /history changed/);
  assert.deepEqual(await readFile(path), Buffer.concat([original, Buffer.from("\n")]));
  await rm(path);
  await assert.rejects(retainGoalTransferHistory(f.destination, f.input, true, true, true), /history is missing/);
  await symlink(f.input.files[0].path, path);
  await assert.rejects(inspectGoalTransferHistory(root, f.input.manifest), /linked/);
  await rm(path); await writeFile(path, original);
  await writeFile(join(root, "later-result.txt"), "Later result must survive");
  await assert.rejects(retainGoalTransferHistory(f.destination, f.input, true, true, true), /later content/);
  assert.equal(await readFile(join(root, "later-result.txt"), "utf8"), "Later result must survive");
  await rm(join(root, "later-result.txt"));
  await rm(root, {recursive: true});
  await assert.rejects(retainGoalTransferHistory(f.destination, f.input, true, true, true), /Completed runtime history is missing/);
});

test("an original empty publication reservation recovers; an unowned occupied directory does not", async t => {
  const f = await fixture(t), root = join(f.destination, "runtime-history");
  await mkdir(root);
  await assert.rejects(retainGoalTransferHistory(f.destination, f.input, true, false, true), /occupied/);
  await writeFile(join(f.destination, "runtime-history-intent.json"), JSON.stringify({history_sha256: canonicalAuthoritySha256(f.input.manifest)}));
  await retainGoalTransferHistory(f.destination, f.input, true, false, true);
  assert.deepEqual((await readdir(root)).sort(), ["goals", "manifest.json"]);
  await mkdir(join(root, "unknown-empty-directory"));
  await assert.rejects(inspectGoalTransferHistory(root, f.input.manifest), /unsupported|later content/);
});

test("carrier rejects false closure and addresses outside the original owner inventories", async t => {
  const f = await fixture(t), manifest = f.input.manifest;
  for (const relative_path of ["../registry.json", "/etc/passwd", "turns/attempt-1.json", "turns/" + "a".repeat(64) + ".hosts/attempt-1.json"]) {
    assert.throws(() => decodeGoalTransferHistory({...manifest, files: [{relative_path, sha256: "a".repeat(64), bytes: 1}]}), /address/);
  }
  assert.throws(() => decodeGoalTransferHistory({...manifest, quota_history: {...manifest.quota_history, closed: false}}), /closed/);
  assert.throws(() => decodeGoalTransferHistory({...manifest, files: []}), /inventories/);
  assert.throws(() => decodeGoalTransferHistory({...manifest, files: [manifest.files[0], manifest.files[0]]}), /address/);
});
