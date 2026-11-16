import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {mkdtemp, mkdir, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {settlementIdentityPayload} from "../../loopx/control_plane/effect_program.ts";
import {canonicalAuthoritySha256 as sha} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {checkpointReceiptName} from "../../loopx/control_plane/goals/checkpoint_read_context.ts";
import {inspectCheckpointAttemptFromRows, inspectCheckpointReadHistoryFromRows, inspectCheckpointReplayFromRows} from "../../loopx/control_plane/goals/checkpoint_commit.ts";

const digest = (value: string) => createHash("sha256").update(value).digest("hex");

async function fixture(t: test.TestContext, loss: string, fault = "") {
  const root = await mkdtemp(join(tmpdir(), "qualified-history-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const identity = settlementIdentityPayload({goal_id: "history-goal", agent_id: "original-agent",
    todo_id: "original-work", turn_instance_id: "original-turn"});
  const runs = join(root, "goals", "history-goal", "runs");
  const contexts = join(root, "goals", "history-goal", "checkpoint-contexts");
  await mkdir(runs, {recursive: true});
  await mkdir(contexts, {recursive: true});
  const workName = checkpointReceiptName(String(identity.effect_id), true);
  if (loss === "damaged") await writeFile(join(contexts, workName), "{");
  const r: JsonObject = {settlement_identity: identity,
    result_work_context: {ok: true, read_context_id: "original-work-read", result_committed: true},
    refresh_recovery: {decision: "append", mutation_digest: "a".repeat(64)},
    json_path: join(runs, "R.json"), markdown_path: join(runs, "R.md")};
  const rText = JSON.stringify(r), markdown = "Original validated result; preserve the stop.\n";
  await writeFile(String(r.json_path), rText);
  await writeFile(String(r.markdown_path), markdown);
  r.result_artifact_digests = {json_sha256: digest(rText), markdown_sha256: digest(markdown)};
  r.result_work_receipt_sha256 = "b".repeat(64);
  const retained: JsonObject = {settlement_identity: identity, refresh_recovery: r.refresh_recovery, index_record_sha256: sha(r),
    result_artifact_digests: r.result_artifact_digests, result_work_context: r.result_work_context,
    result_work_receipt_sha256: r.result_work_receipt_sha256, work_receipt_sha256: "c".repeat(64)};
  if (fault === "binding") retained.index_record_sha256 = "d".repeat(64);
  if (fault === "work-digest") retained.result_work_receipt_sha256 = "d".repeat(64);
  if (fault === "identity") retained.settlement_identity = {...identity, agent_id: "foreign-agent"};
  const q: JsonObject = {settlement_identity: identity,
    refresh_recovery: {decision: "supplement_checkpoint"},
    vision_checkpoint: {satisfied: true, read_context: {retained_result_binding: retained}},
    json_path: join(runs, "Q.json"), markdown_path: join(runs, "Q.md")};
  const qText = JSON.stringify(q);
  await writeFile(String(q.json_path), qText);
  await writeFile(String(q.markdown_path), "Accepted original direction.\n");
  q.direction_artifact_digests = {json_sha256: digest(qText),
    markdown_sha256: digest("Accepted original direction.\n")};
  if (fault === "Q-witness") delete q.direction_artifact_digests;
  const rows = fault === "no-Q" ? [r] : [r, q];
  if (fault === "duplicate-Q") rows.push(q);
  if (fault === "duplicate-R") rows.push(r);
  if (fault === "R-json") await writeFile(String(r.json_path), rText + " ");
  if (fault === "R-markdown") await writeFile(String(r.markdown_path), markdown + "replacement");
  if (fault === "Q-json") await writeFile(String(q.json_path), qText + " ");
  if (fault === "Q-markdown") await writeFile(String(q.markdown_path), "Replaced direction.\n");
  const observe = () => inspectCheckpointReadHistoryFromRows({runtime_root: root,
    goal_id: "history-goal", receipt_names: [workName]}, rows);
  const peer = () => inspectCheckpointAttemptFromRows({runtime_root: root,
    identity: settlementIdentityPayload({goal_id: "history-goal", agent_id: "independent-agent",
      todo_id: "independent-work", turn_instance_id: "independent-turn"}),
    purpose: "first_delivery", check_other_attempts: true}, rows);
  return {root, identity, r, rows, observe, peer, workName, contexts};
}

for (const loss of ["damaged", "deleted"]) test(`accepted Q recovers ${loss} work-read observation without restoring execution authority`, async t => {
  const f = await fixture(t, loss);
  const files = [String(f.r.json_path), String(f.r.markdown_path)];
  const before = await Promise.all(files.map(path => readFile(path)));
  const result = f.observe();
  const verified = (result.verified as JsonObject[]).filter(row => row.receipt_name === f.workName);
  assert.equal(verified.length, 1);
  assert.equal(verified[0].receipt_name, f.workName);
  assert.deepEqual(verified[0].identity, f.identity);
  assert.equal(verified[0].purpose, "first_delivery");
  assert.equal(f.peer().status, "absent");
  assert.throws(() => inspectCheckpointReplayFromRows({runtime_root: f.root, goal_id: "history-goal",
    prior: f.r, include_retained_binding: true}, f.rows), /witness|work|evidence/);
  assert.deepEqual(await Promise.all(files.map(path => readFile(path))), before);
  if (loss === "damaged") assert.equal(await readFile(join(f.contexts, f.workName), "utf8"), "{");
  else await assert.rejects(readFile(join(f.contexts, f.workName)), {code: "ENOENT"});
});

for (const fault of ["no-Q", "binding", "work-digest", "identity", "R-json", "R-markdown", "Q-json", "Q-markdown", "Q-witness", "duplicate-Q", "duplicate-R"]) {
  test(`qualified history rejects ${fault} when the original work read is unavailable`, async t => {
    const f = await fixture(t, "damaged", fault);
    assert.deepEqual((f.observe().verified as JsonObject[]).filter(row => row.receipt_name === f.workName), []);
    assert.throws(f.peer, /unavailable|preserve|conflicts|disagree|witness|identity|evidence|bind/);
  });
}
