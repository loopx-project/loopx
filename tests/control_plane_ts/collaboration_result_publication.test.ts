import assert from "node:assert/strict";
import test from "node:test";
import { createHash } from "node:crypto";
import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";
import { planCollaborationResult, collaborationResultDeliveryReady, collaborationResultText, resultAttachmentRefs } from "../../loopx/control_plane/collaboration/result_publication.ts";

const request = { request_id: "a".repeat(64), goal_id: "delivery", agent_id: "worker", source_id: "chat:original" };
const initial = { ...request, phase: "conclusion", text: "Waiting for review.", decision: "adopt" };
function observation(key: string, receipt: JsonObject): JsonObject {
  const { text, ...value } = receipt;
  return { key, value, text_chars: Array.from(String(text)).length, text_sha256: createHash("sha256").update(String(text)).digest("hex") };
}
const input = { request, phase: "conclusion", text: "Review finished.", decision: "adopt", update_id: "review-finished", results: [observation("conclusion", initial)] };

test("new results use reader paragraphs while immutable legacy returns keep their original format", () => {
  const text = "本地测试更快，正式结果却更差。费用未知，不能据此宣称改进。\n\n" + "完整证据与反例。\n".repeat(700);
  const remaining_work = {summary: "仍需核验实际采用的版本。", resume_when: "取得原始验收证据后继续；不重复付费评测。"};
  const source = {...input, text, remaining_work, update_id: null, results: []};
  const plan = planCollaborationResult(source);
  const value = plan.value as JsonObject;
  assert.equal(value.return_format_version, 2);
  assert.equal(value.text, text.trim());
  assert.deepEqual(value.remaining_work, remaining_work);
  assert.equal(collaborationResultText(value), `${text.trim()}\n\n${remaining_work.summary}\n\n${remaining_work.resume_when}`);
  const replay = planCollaborationResult({...source, results: [observation("conclusion", value)]});
  assert.deepEqual(replay.value, value);
  const legacy = {...value};
  delete legacy.return_format_version;
  const original = collaborationResultText(legacy);
  assert.equal(original, `${text.trim()}\n\n仍需完成 / Remaining work: ${remaining_work.summary}\n继续条件 / Continue when: ${remaining_work.resume_when}`);
  assert.deepEqual(planCollaborationResult({...source, results: [observation("conclusion", legacy)]}).value, legacy);
  assert.equal(collaborationResultText(legacy), original);
  assert.throws(() => planCollaborationResult({...source,
    results: [observation("conclusion", {...value, return_format_version: 999})]}), /return format/);
});

for (const symbol of ["证", "😀"]) {
  test(`the selected return format owns the 20000-character budget (${symbol})`, () => {
    const remaining_work = {summary: "实际采用尚未核验。", resume_when: "取得原始证据后继续。"};
    const readerSuffix = `\n\n${remaining_work.summary}\n\n${remaining_work.resume_when}`;
    const text = symbol.repeat(20000 - [...readerSuffix].length);
    const source = {...input, text, remaining_work, update_id: null, results: []};
    const plan = planCollaborationResult(source);
    const value = plan.value as JsonObject;
    assert.equal(collaborationResultText(value), text + readerSuffix);
    assert.equal([...collaborationResultText(value)].length, 20000);
    assert.deepEqual(planCollaborationResult({...source, results: [observation("conclusion", value)]}).value, value);
    assert.throws(() => planCollaborationResult({...source, text: text + symbol}), /result budget/);

    // The immutable old format still pays for its actual labels. Replaying a
    // legacy result must not silently adopt the shorter renderer's allowance.
    const legacySuffix = `\n\n仍需完成 / Remaining work: ${remaining_work.summary}\n继续条件 / Continue when: ${remaining_work.resume_when}`;
    const legacyText = symbol.repeat(20000 - [...legacySuffix].length);
    const legacy = {...initial, text: legacyText, remaining_work};
    const replay = planCollaborationResult({...source, text: legacyText, results: [observation("conclusion", legacy)]});
    assert.equal(collaborationResultText(replay.value as JsonObject), legacyText + legacySuffix);
    assert.equal([...collaborationResultText(replay.value as JsonObject)].length, 20000);
    assert.throws(() => planCollaborationResult({...source,
      results: [observation("conclusion", {...legacy, text})]}), /result budget/);
  });
}

test("legacy original revision is an optional result selector, not request control", () => {
  const legacy = {...request, schema_version: "loopx_manager_context_entry_v1"};
  for (const phase of ["decision", "conclusion"]) {
    const source = {...input, request: legacy, phase, update_id: null, results: []};
    const expected = planCollaborationResult(source);
    const explicit = planCollaborationResult({...source, request_revision: 1});
    assert.deepEqual(explicit, expected);
    const value = explicit.value as JsonObject;
    assert.equal(Object.hasOwn(value, "request_revision"), false);
    assert.equal(Object.hasOwn(value, "request_revision_digest"), false);
    assert.equal(planCollaborationResult({...source, request_revision: 1,
      results: [observation(phase, value)]}).replay, true);
    assert.equal(planCollaborationResult({...source,
      results: [observation(phase, value)]}).replay, true);
  }
});

test("legacy revision selection cannot create corrections or bypass immutable results", () => {
  const source = {...input, request: {...request, schema_version: "loopx_manager_context_entry_v1"},
    request_revision: 1};
  for (const request_revision of [0, 2, -1, 1.5, "1", true, {}]) {
    assert.throws(() => planCollaborationResult({...source, request_revision}), /original result revision 1/);
  }
  assert.equal(planCollaborationResult(source).result_key, "conclusion-00000001");
  assert.throws(() => planCollaborationResult({...source, update_id: null}), /conflicting/);
  assert.throws(() => planCollaborationResult({...source, results: []}), /initial conclusion/);
  assert.throws(() => planCollaborationResult({...source,
    request: {...source.request, request_control: {}}}), /exact-instance/);
});

test("a later result appends without replacing the initial conclusion", () => {
  const plan = planCollaborationResult(input);
  assert.equal(plan.result_key, "conclusion-00000001");
  assert.equal(plan.replay, false);
  const value = plan.value as JsonObject;
  assert.equal(value.previous_result_key, "conclusion");
  assert.equal(value.update_id, "review-finished");
  assert.deepEqual(input.results[0], observation("conclusion", initial));
  const replay = planCollaborationResult({ ...input, results: [...input.results, observation(String(plan.result_key), value)] });
  assert.equal(replay.result_key, plan.result_key);
  assert.equal(replay.replay, true);
  assert.throws(() => planCollaborationResult({ ...input, text: "Different fact", results: [...input.results, observation(String(plan.result_key), value)] }), /conflicting/);
});

test("ordinary publication retains immutable phases and rejects invalid update ancestry", () => {
  assert.throws(() => planCollaborationResult({ ...input, update_id: null }), /conflicting/);
  assert.throws(() => planCollaborationResult({ ...input, results: [] }), /initial conclusion/);
  assert.throws(() => planCollaborationResult({ ...input, phase: "decision" }), /conclusion/);
  assert.throws(() => planCollaborationResult({ ...input, update_id: "../escape" }), /update id/);
  assert.throws(() => planCollaborationResult({ ...input, results: [...input.results, ...input.results] }), /duplicate/);
  const plan = planCollaborationResult(input);
  for (const patch of [{ source_id: "another" }, { previous_result_key: "decision" }]) {
    assert.throws(() => planCollaborationResult({ ...input, results: [...input.results, observation(String(plan.result_key), { ...(plan.value as JsonObject), ...patch })] }), /identity|chain/);
  }
});

test("stored result digests retain the bare lowercase SHA-256 boundary", () => {
  for (const digest of [null, 42, "", "a".repeat(63), "a".repeat(65), "A".repeat(64), "g".repeat(64), `sha256:${"a".repeat(64)}`]) {
    assert.throws(() => planCollaborationResult({ ...input,
      results: [{ ...input.results[0], text_sha256: digest }] }), /identity or content conflict/);
  }
  assert.equal(planCollaborationResult(input).result_key, "conclusion-00000001");
});

test("later delivery waits for verified predecessors, including exact update identity", () => {
  const initialUpdate = { previous_result_key: "conclusion" };
  for (const status of ["queued", "retry_pending", "verification_required", "explicit_unverified", "superseded"]) {
    assert.equal(collaborationResultDeliveryReady({ result: initialUpdate, previous_delivery: { status } }).ready, false);
  }
  assert.equal(collaborationResultDeliveryReady({ result: initialUpdate, previous_delivery: { status: "delivered" } }).ready, true);
  const next = { previous_result_key: "conclusion-00000001" };
  assert.equal(collaborationResultDeliveryReady({ result: next, previous_delivery: { status: "delivered" } }).ready, false);
  assert.equal(collaborationResultDeliveryReady({ result: next, previous_delivery: { status: "delivered", result_key: next.previous_result_key } }).ready, true);
  assert.equal(collaborationResultDeliveryReady({ result: {}, previous_delivery: null }).ready, true);
});

test("explicit result files participate in immutable retry identity", () => {
  const attachment = {ref: "reports/output.pdf", name: "output.pdf", size: 12, sha256: "a".repeat(64)};
  const source = {...input, update_id: null, results: [], attachments: [attachment]};
  const plan = planCollaborationResult(source);
  const replay = {...source, results: [observation("conclusion", plan.value as JsonObject)]};
  assert.equal(planCollaborationResult(replay).replay, true);
  for (const attachments of [[], [{...attachment, sha256: "b".repeat(64)}], [{...attachment, size: 13}]]) {
    assert.throws(() => planCollaborationResult({...replay, attachments}), /conflicting/);
  }
  for (const ref of ["/private/report", "../outside", "link/../outside", "https://example.test/file", "C:\\file"])
    assert.throws(() => resultAttachmentRefs({refs: [ref]}), /relative workspace/);
  assert.throws(() => resultAttachmentRefs({refs: ["report", "report"]}), /duplicate/);
  assert.throws(() => resultAttachmentRefs({refs: ["report"], workspace_current: false}), /instance changed/);
  assert.throws(() => resultAttachmentRefs({refs: ["a", "b", "c", "d", "e"]}), /four/);
  for (const patch of [{size: 0}, {size: 30 * 1024 * 1024 + 1}, {name: "another.pdf"}, {sha256: "A".repeat(64)}]) {
    assert.throws(() => planCollaborationResult({...source, attachments: [{...attachment, ...patch}]}), /attachment/);
  }
});

test("a reported remainder is bounded immutable result scope, not a completion switch", () => {
  const remaining_work = {summary: "Qualify the installed repair", resume_when: "The isolated package is available"};
  const source = {...input, update_id: null, results: [], remaining_work};
  const plan = planCollaborationResult(source);
  const replay = {...source, results: [observation("conclusion", plan.value as JsonObject)]};
  assert.equal(planCollaborationResult(replay).replay, true);
  assert.deepEqual((plan.value as JsonObject).remaining_work, remaining_work);
  for (const change of [null, {...remaining_work, summary: "Everything done"}, {...remaining_work, resume_when: "Now"}]) {
    assert.throws(() => planCollaborationResult({...replay, remaining_work: change}), /conflicting/);
  }
  for (const invalid of [{summary: "Only a summary"}, {...remaining_work, owner: "another-agent"},
    {...remaining_work, summary: " "}, {...remaining_work, resume_when: "😀".repeat(601)},
    {...remaining_work, summary: "\ud800"}]) {
    assert.throws(() => planCollaborationResult({...source, remaining_work: invalid}), /remaining work/);
  }
  assert.throws(() => planCollaborationResult({...source, phase: "decision"}), /conclusion/);
  assert.throws(() => planCollaborationResult({...source, text: "x".repeat(20000)}), /result budget/);
  const final = planCollaborationResult({...input, results: replay.results});
  assert.equal(Object.hasOwn(final.value as JsonObject, "remaining_work"), false);
  assert.deepEqual((replay.results[0].value as JsonObject).remaining_work, remaining_work);
});
