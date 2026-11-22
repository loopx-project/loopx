import { strict as assert } from "node:assert";
import test from "node:test";
import {createHash} from "node:crypto";

import {
  negotiateVerifierHooks, recommendVerifiedClose, type VerifiedCloseReader,
  type VerifiedCloseRequest, type VerifierHookEvent, type VerifierScopeReceipt,
} from "../../loopx/control_plane/turn_driver/verifier_hook_contract.ts";
import {capturedContextSelection} from "../../loopx/control_plane/quota/context_selection.ts";

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

const passed = { ...source, verdict: "passed", summary_ref: "capture:child-receipt" } as const;
const readable = new Set([source.oracle_evidence_ref, source.source_ref, passed.summary_ref, "oracle:counterexample"]);
function authority(receipt?: VerifierScopeReceipt): VerifiedCloseReader {
  return {
    readReceipt(ref) {
      assert.equal(ref, request.receipt_ref);
      return receipt;
    },
    canReadEvidence: ref => readable.has(ref),
  };
}

test("verified return resolves the named oracle receipt and stages the exact source reference", () => {
  const result = recommendVerifiedClose(request, authority(passed));
  assert.deepEqual(result, {
    action: "FoldToReceipt", receipt_ref: "receipt:21",
    source_ref: "capture:raw-child", summary_ref: "capture:child-receipt",
  });
});

test("open, overlapping, unknown and mismatched work stays raw", () => {
  for (const variant of [
    { ...request, overlap: "high" as const },
    { ...request, overlap: "unknown" as const },
    { ...request, open_obligations: true },
  ]) {
    assert.deepEqual(recommendVerifiedClose(variant, authority(passed)), { action: "KeepRaw" });
  }
  const missingObligations = { ...request, open_obligations: undefined } as unknown as VerifiedCloseRequest;
  assert.deepEqual(recommendVerifiedClose(missingObligations, authority(passed)), { action: "KeepRaw" });
  for (const key of Object.keys(identity) as (keyof typeof identity)[]) {
    const mismatch = { ...identity, [key]: "different" };
    assert.deepEqual(recommendVerifiedClose(request, authority({ ...passed, identity: mismatch })),
      { action: "KeepRaw" });
  }
  assert.deepEqual(recommendVerifiedClose(request, authority()), { action: "KeepRaw" });
  assert.deepEqual(recommendVerifiedClose(request, authority({ ...source, verdict: "unknown" })),
    { action: "KeepRaw" });
  assert.deepEqual(recommendVerifiedClose(request, authority({
    ...source, verdict: "failed", counterexample_ref: "oracle:counterexample",
  })), { action: "KeepRaw", counterexample_ref: "oracle:counterexample" });
});

test("the verifier, raw source and compact result must each remain readable", () => {
  for (const field of ["oracle_evidence_ref", "source_ref", "summary_ref"] as const) {
    for (const ref of ["", "  ", "capture:unavailable"]) {
      const result = recommendVerifiedClose(request, authority({ ...passed, [field]: ref }));
      assert.deepEqual(result, { action: "KeepRaw" }, field);
    }
  }
  assert.deepEqual(recommendVerifiedClose({ ...request, receipt_ref: "" }, authority(passed)),
    { action: "KeepRaw" });
  assert.deepEqual(recommendVerifiedClose(request, authority({
    ...source, verdict: "failed", counterexample_ref: "capture:unavailable",
  })), { action: "KeepRaw" });
});

test("startup and recovery use the same captured C1 Goal boundary", () => {
  const context = { goal_boundary: {
    rule: "stay_in_scope_or_stop", write_scope: ["candidate"], stop_condition: "Stop before publishing.",
  } };
  const captureSha256 = createHash("sha256").update(JSON.stringify(context)).digest("hex");
  const boundary = { capture_sha256: captureSha256, pointer: "/goal_boundary" } as const;
  const start: Extract<VerifierHookEvent, {boundary: "start"}> = {
    boundary: "start", goal_id: "goal", todo_id: "todo", session_ref: "session",
    epoch_ref: "before-compact",
    view: {
      objective: "Verify the selected delivery.", acceptance_refs: ["acceptance:1"],
      goal_boundary_ref: boundary, verifier: { oracle_ref: "oracle:1", verifier_version: "v1" },
      falsification_refs: [],
    },
  };
  const recovery: typeof start = { ...start, epoch_ref: "after-compact" };
  for (const event of [start, recovery]) {
    const ref = event.view.goal_boundary_ref;
    assert.equal(ref.capture_sha256, captureSha256);
    const read = capturedContextSelection({context, select: [ref.pointer]});
    assert.deepEqual(read.selections, [{pointer: "/goal_boundary", path: ["goal_boundary"]}]);
  }
  assert.throws(() => capturedContextSelection({context: {}, select: [boundary.pointer]}),
    /pointer does not exist/);
});
