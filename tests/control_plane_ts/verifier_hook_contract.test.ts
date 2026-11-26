import { strict as assert } from "node:assert";
import test from "node:test";
import {createHash} from "node:crypto";
import {chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";

import type {
  VerifiedCloseReader, VerifiedCloseRequest, VerifiedScopeEvidence, VerifierHookEvent, VerifierScopeReceipt,
} from "../../loopx/control_plane/turn_driver/verifier_hook_contract.ts";
import {negotiateVerifierHooks, recommendVerifiedClose} from "./fixtures/verifier_hook_contract.ts";
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

test("files missing, unreadable or wrong-kind at receipt or evidence readers preserve raw scope", async (t) => {
  const directory = mkdtempSync(join(tmpdir(), "loopx-verifier-hook-"));
  const protectedPaths: string[] = [];
  t.after(() => {
    for (const target of protectedPaths) {
      try {
        chmodSync(target, 0o600);
      } catch {
        // Best-effort restore before directory cleanup.
      }
    }
    rmSync(directory, { recursive: true, force: true });
  });
  const readJsonFile = <T>(filePath: string): T => JSON.parse(readFileSync(filePath, "utf8")) as T;
  const readMissingFile = (): never => readJsonFile(join(directory, "unavailable.json"));
  const subDir = join(directory, "record-dir");
  mkdirSync(subDir);
  const regularFile = join(directory, "regular-file.txt");
  writeFileSync(regularFile, "not-a-directory", "utf8");
  const wrongKindParentPath = join(regularFile, "entry.json");
  const lockedReceiptFile = join(directory, "locked-receipt.json");
  const lockedEvidenceFile = join(directory, "locked-evidence.json");
  writeFileSync(lockedReceiptFile, JSON.stringify(passed), "utf8");
  writeFileSync(lockedEvidenceFile, JSON.stringify(evidence.get(passed.source_ref)), "utf8");

  const runReadBackCounterfactual = (label: string, path: string): void => {
    assert.deepEqual(recommendVerifiedClose(request, {
      readReceipt: () => readJsonFile<VerifierScopeReceipt>(path),
      readEvidence: () => assert.fail(`evidence read after receipt became unavailable (${label})`),
    }), { action: "KeepRaw" }, `receipt ${label}`);
    for (const ref of [passed.oracle_evidence_ref, passed.source_ref, passed.summary_ref]) {
      assert.deepEqual(recommendVerifiedClose(request, authority(passed, key =>
        key === ref ? readJsonFile<VerifiedScopeEvidence>(path) : evidence.get(key),
      )), { action: "KeepRaw" }, `evidence ${ref} (${label})`);
    }
    const failed = { ...source, verdict: "failed", counterexample_ref: "oracle:counterexample" } as const;
    assert.deepEqual(recommendVerifiedClose(request, authority(failed, key =>
      key === "oracle:counterexample" ? readJsonFile<VerifiedScopeEvidence>(path) : evidence.get(key),
    )), { action: "KeepRaw" }, `counterexample ${label}`);
  };

  await t.test("a missing receipt short-circuits all evidence reads", () => {
    assert.deepEqual(recommendVerifiedClose(request, {
      readReceipt: readMissingFile,
      readEvidence: () => assert.fail("evidence read after receipt disappeared"),
    }), { action: "KeepRaw" });
  });
  await t.test("a missing required evidence file or counterexample retains the raw scope", () => {
    for (const ref of [passed.oracle_evidence_ref, passed.source_ref, passed.summary_ref]) {
      assert.deepEqual(recommendVerifiedClose(request, authority(passed, key =>
        key === ref ? readMissingFile() : evidence.get(key),
      )), { action: "KeepRaw" }, ref);
    }
    const failed = { ...source, verdict: "failed", counterexample_ref: "oracle:counterexample" } as const;
    assert.deepEqual(recommendVerifiedClose(request, authority(failed, readMissingFile)), { action: "KeepRaw" });
  });
  await t.test("wrong-kind directory and non-directory paths (EISDIR / ENOTDIR) keep raw scope", () => {
    runReadBackCounterfactual("EISDIR", subDir);
    runReadBackCounterfactual("ENOTDIR", wrongKindParentPath);
  });
  await t.test("permission-denied receipt and evidence files (EACCES) keep raw scope while readable files fold", () => {
    const readableOracle = join(directory, "ok-oracle.json");
    const readableSource = join(directory, "ok-source.json");
    const readableSummary = join(directory, "ok-summary.json");
    writeFileSync(readableOracle, JSON.stringify(evidence.get(passed.oracle_evidence_ref)), "utf8");
    writeFileSync(readableSource, JSON.stringify(evidence.get(passed.source_ref)), "utf8");
    writeFileSync(readableSummary, JSON.stringify(evidence.get(passed.summary_ref)), "utf8");
    const fileBackedMap = new Map([
      [passed.oracle_evidence_ref, readableOracle],
      [passed.source_ref, readableSource],
      [passed.summary_ref, readableSummary],
    ]);
    assert.deepEqual(recommendVerifiedClose(request, {
      readReceipt: () => readJsonFile<VerifierScopeReceipt>(lockedReceiptFile),
      readEvidence: ref => {
        const target = fileBackedMap.get(ref);
        return target ? readJsonFile<VerifiedScopeEvidence>(target) : undefined;
      },
    }), {
      action: "FoldToReceipt",
      receipt_ref: "receipt:21",
      source_ref: passed.source_ref,
      summary_ref: passed.summary_ref,
    });

    if (typeof process.getuid === "function" && process.getuid() === 0) {
      t.skip("root bypasses POSIX chmod 0o000 EACCES restrictions");
      return;
    }
    chmodSync(lockedReceiptFile, 0o000);
    chmodSync(lockedEvidenceFile, 0o000);
    protectedPaths.push(lockedReceiptFile, lockedEvidenceFile);
    assert.throws(
      () => readFileSync(lockedReceiptFile, "utf8"),
      error => error instanceof Error && "code" in error && error.code === "EACCES",
    );
    runReadBackCounterfactual("EACCES-receipt", lockedReceiptFile);
    runReadBackCounterfactual("EACCES-evidence", lockedEvidenceFile);
  });
});

test("unexpected authority reader and parse errors remain observable", () => {
  const directory = mkdtempSync(join(tmpdir(), "loopx-verifier-parse-"));
  try {
    const corruptFile = join(directory, "corrupt.json");
    writeFileSync(corruptFile, "{invalid-json", "utf8");
    const readCorrupt = (): never => JSON.parse(readFileSync(corruptFile, "utf8")) as never;
    for (const reader of [
      { readReceipt: readCorrupt, readEvidence: () => assert.fail("evidence read after parse failure") },
      authority(passed, readCorrupt),
    ]) {
      assert.throws(() => recommendVerifiedClose(request, reader), error => error instanceof SyntaxError);
    }

    const failure = new TypeError("invalid authority record");
    const fail = (): never => { throw failure; };
    for (const reader of [
      { readReceipt: fail, readEvidence: () => assert.fail("evidence read after unexpected failure") },
      authority(passed, fail),
    ]) {
      assert.throws(() => recommendVerifiedClose(request, reader), error => error === failure);
    }
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
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
