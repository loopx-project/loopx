import { strict as assert } from "node:assert";
import test from "node:test";
import {createHash} from "node:crypto";

import {
  negotiateVerifierHooks, recommendVerifiedClose, type VerifiedCloseReader,
  type VerifiedCloseRequest, type VerifiedScopeEvidence, type VerifierHookEvent, type VerifierScopeReceipt,
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
  candidate_sha: "sha256:" + "a".repeat(64), verifier_version: "verifier:4",
};
const request: VerifiedCloseRequest = {
  identity, receipt_ref: "receipt:21", overlap: "low", open_obligations: false,
};
const source = {
  identity, oracle_evidence_ref: "oracle:result", yardstick_evidence_ref: "probe:result",
  source_ref: "capture:raw-child", falsification_refs: ["finding:invalid-shortcut"],
};

const passed = { ...source, verdict: "passed", summary_ref: "capture:child-receipt" } as const;
const evidence = new Map<string, VerifiedScopeEvidence>([
  [source.oracle_evidence_ref, { ref: source.oracle_evidence_ref, role: "oracle_evidence", identity }],
  [source.source_ref, { ref: source.source_ref, role: "source", identity }],
  [passed.summary_ref, { ref: passed.summary_ref, role: "summary", identity }],
  ["oracle:counterexample", { ref: "oracle:counterexample", role: "counterexample", identity }],
]);
function authority(
  receipt?: VerifierScopeReceipt,
  readEvidence: VerifiedCloseReader["readEvidence"] = ref => evidence.get(ref),
): VerifiedCloseReader {
  return {
    readReceipt(ref) {
      assert.equal(ref, request.receipt_ref);
      return receipt;
    },
    readEvidence,
  };
}

test("verified return resolves the named oracle receipt and stages the exact source reference", () => {
  const result = recommendVerifiedClose(request, authority(passed));
  assert.deepEqual(result, {
    action: "FoldToReceipt", receipt_ref: "receipt:21",
    source_ref: "capture:raw-child", summary_ref: "capture:child-receipt",
  });
});

test("matching candidate identifiers must still identify immutable content", () => {
  for (const candidate_sha of [
    "main", "candidate:26", "a".repeat(64),
    "sha256:" + "a".repeat(63), "sha256:" + "g".repeat(64), "sha256:" + "A".repeat(64),
  ]) {
    const invalid = { ...identity, candidate_sha };
    const reads: string[] = [];
    const reader = {
      readReceipt(ref: string): VerifierScopeReceipt {
        reads.push(ref);
        return { ...passed, identity: invalid };
      },
      readEvidence(ref: string): VerifiedScopeEvidence | undefined {
        reads.push(ref);
        const record = evidence.get(ref);
        return record && { ...record, identity: invalid };
      },
    };
    assert.deepEqual(recommendVerifiedClose({ ...request, identity: invalid }, reader),
      { action: "KeepRaw" }, candidate_sha);
    assert.deepEqual(reads, [], candidate_sha);
  }
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
    const different = key === "candidate_sha" ? "sha256:" + "b".repeat(64) : "different";
    const mismatch = { ...identity, [key]: different };
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

test("readable evidence must match the recorded role, reference and exact work scope", () => {
  for (const receipt of [
    { ...passed, source_ref: passed.summary_ref, summary_ref: passed.source_ref },
    { ...passed, oracle_evidence_ref: passed.summary_ref },
    { ...passed, source_ref: passed.oracle_evidence_ref },
  ]) {
    assert.deepEqual(recommendVerifiedClose(request, authority(receipt)), { action: "KeepRaw" });
  }
  for (const ref of [passed.oracle_evidence_ref, passed.source_ref, passed.summary_ref]) {
    for (const field of Object.keys(identity) as (keyof typeof identity)[]) {
      const scopedReader = (key: string) => {
        const record = evidence.get(key);
        return record && key === ref
          ? { ...record, identity: { ...identity, [field]: "another-scope" } }
          : record;
      };
      assert.deepEqual(recommendVerifiedClose(request, authority(passed, scopedReader)), { action: "KeepRaw" });
    }
  }
  assert.deepEqual(recommendVerifiedClose(request, authority(passed, ref => {
    const record = evidence.get(ref);
    return record && ref === passed.source_ref ? { ...record, ref: "another-source" } : record;
  })), { action: "KeepRaw" });
  assert.deepEqual(recommendVerifiedClose(request, authority({
    ...source, verdict: "failed", counterexample_ref: passed.summary_ref,
  })), { action: "KeepRaw" });
});

test("matching blank identity fields do not authorize a receipt read", () => {
  const reader = {
    readReceipt: () => assert.fail("invalid identity reached receipt authority"),
    readEvidence: () => assert.fail("invalid identity reached evidence authority"),
  };
  for (const field of Object.keys(identity) as (keyof typeof identity)[]) {
    const invalid = { ...identity, [field]: "  " };
    assert.deepEqual(recommendVerifiedClose({ ...request, identity: invalid }, reader), { action: "KeepRaw" });
  }
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
