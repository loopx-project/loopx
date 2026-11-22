import { strict as assert } from "node:assert";
import test from "node:test";

import {
  negotiateVerifierHooks, recommendVerifiedClose, type VerifiedCloseRequest,
} from "../../loopx/control_plane/turn_driver/verifier_hook_contract.ts";

const l1 = { session_lineage: true, boundary_context: true };
const l2 = { ...l1, milestone_observation: true, isolated_subscope: true, verified_return: true };

test("enabled negotiation uses observed capabilities and never infers pre-tool opt-in", () => {
  const policy = { enabled: true };
  assert.equal(negotiateVerifierHooks({ enabled: false, pre_tool_use: true }, l2), null);
  assert.deepEqual(negotiateVerifierHooks(policy), { tier: "L0", pre_tool_use: false });
  assert.deepEqual(negotiateVerifierHooks(policy, { ...l2, session_lineage: false }),
    { tier: "L0", pre_tool_use: false });
  assert.deepEqual(negotiateVerifierHooks(policy, l1), { tier: "L1", pre_tool_use: false });
  assert.deepEqual(negotiateVerifierHooks(policy, { ...l2, verified_return: false }),
    { tier: "L1", pre_tool_use: false });
  assert.deepEqual(negotiateVerifierHooks(policy, { ...l2, pre_tool_use: true }),
    { tier: "L2", pre_tool_use: false });
  assert.deepEqual(negotiateVerifierHooks({ ...policy, pre_tool_use: true }, l2),
    { tier: "L2", pre_tool_use: false });
  assert.deepEqual(negotiateVerifierHooks({ ...policy, pre_tool_use: true }, { ...l2, pre_tool_use: true }),
    { tier: "L2", pre_tool_use: true });
});

const identity = {
  goal_id: "goal", todo_id: "todo", parent_epoch_ref: "epoch:9", child_scope_ref: "scope:3",
  candidate_sha: "candidate:26", verifier_version: "verifier:4",
};
const request: VerifiedCloseRequest = {
  identity, receipt_ref: "receipt:21", overlap: "low", open_obligations: false,
};
const source = {
  identity, oracle_evidence_ref: "oracle:result", yardstick_evidence_ref: "probe:result",
  source_ref: "capture:raw-child", falsification_refs: ["finding:invalid-shortcut"],
};

test("verified return resolves the named oracle receipt and stages the exact source reference", () => {
  const result = recommendVerifiedClose(request, ref => {
    assert.equal(ref, "receipt:21");
    return { ...source, verdict: "passed", summary_ref: "capture:child-receipt" };
  });
  assert.deepEqual(result, {
    action: "FoldToReceipt", receipt_ref: "receipt:21",
    source_ref: "capture:raw-child", summary_ref: "capture:child-receipt",
  });
});

test("open, overlapping, unknown and mismatched work stays raw", () => {
  const passed = { ...source, verdict: "passed", summary_ref: "capture:child-receipt" } as const;
  for (const variant of [
    { ...request, overlap: "high" as const },
    { ...request, overlap: "unknown" as const },
    { ...request, open_obligations: true },
  ]) {
    assert.deepEqual(recommendVerifiedClose(variant, () => passed), { action: "KeepRaw" });
  }
  const missingObligations = { ...request, open_obligations: undefined } as unknown as VerifiedCloseRequest;
  assert.deepEqual(recommendVerifiedClose(missingObligations, () => passed), { action: "KeepRaw" });
  for (const key of Object.keys(identity) as (keyof typeof identity)[]) {
    const mismatch = { ...identity, [key]: "different" };
    assert.deepEqual(recommendVerifiedClose(request, () => ({ ...passed, identity: mismatch })),
      { action: "KeepRaw" });
  }
  assert.deepEqual(recommendVerifiedClose(request, () => undefined), { action: "KeepRaw" });
  assert.deepEqual(recommendVerifiedClose(request, () => ({ ...source, verdict: "unknown" })),
    { action: "KeepRaw" });
  assert.deepEqual(recommendVerifiedClose(request, () => ({
    ...source, verdict: "failed", counterexample_ref: "oracle:counterexample",
  })), { action: "KeepRaw", counterexample_ref: "oracle:counterexample" });
});
