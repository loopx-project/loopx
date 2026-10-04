import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";

import {
  evaluatePostWritebackHookTransaction,
  POST_WRITEBACK_HOOK_TRANSACTION_REQUEST_SCHEMA_VERSION,
} from "../../loopx/control_plane/post_writeback_hook_transaction.ts";
import {
  acquireFileMutationLock,
  releaseFileMutationLock,
} from "../../loopx/control_plane/effect_runtime_io.ts";

function registration(
  hookId: string,
  readScope: string[] = ["stage_completion"],
  maxResultBytes = 16 * 1024,
): Record<string, unknown> {
  return {
    schema_version: "loopx_post_writeback_capability_hook_registration_v0",
    hook_id: hookId,
    capability_id: "periodic-report",
    policy_version: "v0",
    phase: "post_writeback",
    event_kinds: ["refresh_state"],
    intent_kinds: ["periodic_report.trigger_evaluation"],
    budget: {
      max_invocations_per_dispatch: 1,
      max_input_bytes: 64 * 1024,
      max_result_bytes: maxResultBytes,
    },
    failure_policy: "isolate",
    requested_read_scope: readScope,
    requested_write_scope: [],
  };
}

function source(): Record<string, unknown> {
  return {
    schema_version: "loopx_post_writeback_hook_source_v0",
    event_kind: "refresh_state",
    status: "committed",
    durable: true,
    identity: {
      goal_id: "goal-1",
      agent_id: "agent-1",
      todo_id: "todo-1",
      turn_instance_id: "turn-1",
      effect_id: "effect-1",
    },
    state_version: "state-v1",
    committed_at: "2026-09-02T12:00:00Z",
    projection: {
      stage_completion: { transition: "goal_terminal" },
      project_progress: { items: [] },
    },
  };
}

function request(
  runtimeRoot: string | null,
  extra: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    schema_version: POST_WRITEBACK_HOOK_TRANSACTION_REQUEST_SCHEMA_VERSION,
    phase: "preflight",
    runtime_root: runtimeRoot,
    source: source(),
    hook_input: null,
    registrations: [registration("periodic_report.stage_completion")],
    transaction_id: null,
    provider_outcomes: [],
    ...extra,
  };
}

function legacyHookInput(): Record<string, unknown> {
  return {
    schema_version: "loopx_post_writeback_capability_hook_input_v0",
    receipt: {
      schema_version: "loopx_rollout_event_v0",
      event_id: "evt-stage-1",
      event_kind: "refresh_state",
      status: "appended",
      recorded_at: "2026-08-30T01:00:00+08:00",
      durable: true,
    },
    identity: {
      goal_id: "goal-1",
      agent_id: "agent-1",
      todo_id: "todo-1",
      turn_instance_id: "turn-1",
      effect_id: "goal-1:agent-1:todo-1:turn-1",
    },
    state_version: "vision-revision-2",
    projection: {
      stage_completion: { transition: "goal_terminal" },
    },
  };
}

function intentResult(
  plan: Record<string, unknown>,
  idempotencyKey = "periodic-report:stage-1",
): Record<string, unknown> {
  const hookInput = plan.hook_input as Record<string, unknown>;
  const receipt = hookInput.receipt as Record<string, unknown>;
  return {
    schema_version: "loopx_post_writeback_capability_hook_result_v0",
    hook_id: plan.hook_id,
    capability_id: plan.capability_id,
    phase: "post_writeback",
    status: "intent",
    intent: {
      schema_version: "loopx_capability_intent_v0",
      intent_kind: "periodic_report.trigger_evaluation",
      idempotency_key: idempotencyKey,
      source_receipt_id: receipt.event_id,
      payload: { generation_authorized: false },
      requested_write_scope: [],
    },
  };
}

function returnedOutcome(
  plan: Record<string, unknown>,
  result: Record<string, unknown> = intentResult(plan),
): Record<string, unknown> {
  return {
    dispatch_id: plan.dispatch_id,
    hook_id: plan.hook_id,
    capability_id: plan.capability_id,
    attempt_count: plan.attempt_count,
    status: "returned",
    result,
  };
}

function notApplicableResult(
  plan: Record<string, unknown>,
): Record<string, unknown> {
  return {
    schema_version: "loopx_post_writeback_capability_hook_result_v0",
    hook_id: plan.hook_id,
    capability_id: plan.capability_id,
    phase: "post_writeback",
    status: "not_applicable",
    intent: null,
  };
}

function receiptPathFor(
  runtimeRoot: string,
  plan: Record<string, unknown>,
): string {
  return join(
    runtimeRoot,
    "goals",
    "goal-1",
    "post_writeback_hooks",
    `${plan.dispatch_id}.json`,
  );
}

function retryableReceipt(
  plan: Record<string, unknown>,
  attemptCount: number,
): Record<string, unknown> {
  const receipt = (plan.hook_input as Record<string, unknown>)
    .receipt as Record<string, unknown>;
  return {
    schema_version: "loopx_post_writeback_capability_hook_receipt_v0",
    dispatch_id: plan.dispatch_id,
    hook_id: plan.hook_id,
    capability_id: plan.capability_id,
    source_receipt_id: receipt.event_id,
    status: "retryable_failure",
    intent: null,
    error_code: "producer_failed",
    attempt_count: attemptCount,
    recorded_at: receipt.recorded_at,
  };
}

async function waitForFile(path: string): Promise<void> {
  for (let attempt = 0; attempt < 500; attempt += 1) {
    try {
      await readFile(path, "utf8");
      return;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error(`timed out waiting for ${path}`);
}

async function tempRuntime(t: test.TestContext): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), "loopx-post-writeback-transaction-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  return root;
}

test("source and dispatch identities replay the retired Python canonical bytes", async () => {
  const value = source();
  const identity = value.identity as Record<string, unknown>;
  identity.agent_id = "agent-😀";
  const preflight = await evaluatePostWritebackHookTransaction(
    request(null, { source: value }),
  );
  const plan = (preflight.provider_plan as Record<string, unknown>[])[0];
  const hookInput = plan.hook_input as Record<string, unknown>;
  const receipt = hookInput.receipt as Record<string, unknown>;

  assert.equal(receipt.event_id, "pwr_1e21e6cc20ec36025f486859");
  assert.equal(
    plan.dispatch_id,
    "pwh_bd119d54eba68a96ecd93c21ff3565283decd8c1e3746d2d9ab1bfc621098933",
  );

  const pythonWhitespace = source();
  const pythonWhitespaceChars =
    "\u0009\u000A\u000B\u000C\u000D\u001C\u001D\u001E\u001F\u0020" +
    "\u0085\u00A0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006" +
    "\u2007\u2008\u2009\u200A\u2028\u2029\u202F\u205F\u3000";
  pythonWhitespace.event_kind = `${pythonWhitespaceChars}refresh_state${pythonWhitespaceChars}`;
  pythonWhitespace.state_version = `${pythonWhitespaceChars}state-v1${pythonWhitespaceChars}`;
  pythonWhitespace.committed_at =
    `${pythonWhitespaceChars}2026-09-02T12:00:00Z${pythonWhitespaceChars}`;
  const whitespaceIdentity = pythonWhitespace.identity as Record<string, unknown>;
  whitespaceIdentity.goal_id = `${pythonWhitespaceChars}goal-1${pythonWhitespaceChars}`;
  whitespaceIdentity.agent_id =
    `${pythonWhitespaceChars}agent-\uFEFF${pythonWhitespaceChars}`;
  whitespaceIdentity.todo_id = `${pythonWhitespaceChars}todo-1${pythonWhitespaceChars}`;
  whitespaceIdentity.turn_instance_id =
    `${pythonWhitespaceChars}turn-1${pythonWhitespaceChars}`;
  whitespaceIdentity.effect_id =
    `${pythonWhitespaceChars}effect-1${pythonWhitespaceChars}`;
  const whitespacePreflight = await evaluatePostWritebackHookTransaction(
    request(null, { source: pythonWhitespace }),
  );
  const whitespacePlan = (
    whitespacePreflight.provider_plan as Record<string, unknown>[]
  )[0];
  const whitespaceReceipt = (
    whitespacePlan.hook_input as Record<string, unknown>
  ).receipt as Record<string, unknown>;
  assert.equal(whitespaceReceipt.event_id, "pwr_d67ba8a0f7e295de520d0989");
  assert.equal(
    whitespacePlan.dispatch_id,
    "pwh_143821700486f93b3af19a6c473caee038819832f2c4a70819ac7d7ff81690ac",
  );

  const byteOrderMarkSource = source();
  (byteOrderMarkSource.identity as Record<string, unknown>).agent_id = "\uFEFF";
  byteOrderMarkSource.state_version = "\uFEFF";
  const byteOrderMarkPreflight = await evaluatePostWritebackHookTransaction(
    request(null, { source: byteOrderMarkSource }),
  );
  assert.deepEqual(byteOrderMarkPreflight.provider_plan, []);
  assert.equal(
    (((byteOrderMarkPreflight.dispatch as Record<string, unknown>)
      .failures as Record<string, unknown>[])[0]).error_code,
    "registration_or_input_rejected",
  );
});

test("todoless autonomous replan source preserves an explicit null Todo identity", async () => {
  const value = source();
  const identity = value.identity as Record<string, unknown>;
  identity.todo_id = null;

  const preflight = await evaluatePostWritebackHookTransaction(
    request(null, { source: value }),
  );
  const plan = (preflight.provider_plan as Record<string, unknown>[])[0];
  const hookInput = plan.hook_input as Record<string, unknown>;
  const admittedIdentity = hookInput.identity as Record<string, unknown>;

  assert.equal(admittedIdentity.todo_id, null);
  assert.equal(admittedIdentity.goal_id, "goal-1");
  assert.equal(admittedIdentity.agent_id, "agent-1");
  assert.equal(admittedIdentity.turn_instance_id, "turn-1");
  assert.equal(admittedIdentity.effect_id, "effect-1");
});

test("legacy hook input is exact-validated and preserves its durable event identity", async () => {
  const preflight = await evaluatePostWritebackHookTransaction(
    request(null, {
      source: null,
      hook_input: legacyHookInput(),
    }),
  );
  const plan = (preflight.provider_plan as Record<string, unknown>[])[0];
  const receipt = (plan.hook_input as Record<string, unknown>)
    .receipt as Record<string, unknown>;
  assert.equal(receipt.event_id, "evt-stage-1");

  const invalid = legacyHookInput();
  invalid.unexpected = true;
  const rejected = await evaluatePostWritebackHookTransaction(
    request(null, { source: null, hook_input: invalid }),
  );
  assert.deepEqual(rejected.provider_plan, []);
  assert.equal(
    ((rejected.dispatch as Record<string, unknown>)
      .failures as Record<string, unknown>[])[0].error_code,
    "registration_or_input_rejected",
  );

  const deleteEvent = legacyHookInput();
  (deleteEvent.receipt as Record<string, unknown>).event_id = "evt-\u007f";
  const deletePreflight = await evaluatePostWritebackHookTransaction(
    request(null, { source: null, hook_input: deleteEvent }),
  );
  const deletePlan = (
    deletePreflight.provider_plan as Record<string, unknown>[]
  )[0];
  assert.equal(
    deletePlan.dispatch_id,
    "pwh_814eeff656b73bb32cdfb8cb56adfdc3df1a51b299fbbfac5b339a56daeee9ce",
  );

  const pythonWhitespaceEvent = legacyHookInput();
  (pythonWhitespaceEvent.receipt as Record<string, unknown>).event_id =
    "\u0085evt-\uFEFF\u0085";
  const whitespaceEventPreflight = await evaluatePostWritebackHookTransaction(
    request(null, { source: null, hook_input: pythonWhitespaceEvent }),
  );
  const whitespaceEventPlan = (
    whitespaceEventPreflight.provider_plan as Record<string, unknown>[]
  )[0];
  assert.equal(
    whitespaceEventPlan.dispatch_id,
    "pwh_e1e565a53173e5e5a6d932096699fd022494355a32913f1fb15acd8e0128fd33",
  );
});

test("batch preflight orders hooks and grants each provider only its read scope", async () => {
  const preflight = await evaluatePostWritebackHookTransaction(
    request(null, {
      registrations: [
        registration("periodic_report.z", ["project_progress"]),
        registration("periodic_report.a", ["stage_completion"]),
      ],
    }),
  );

  assert.equal(preflight.phase, "preflight");
  assert.equal(preflight.dispatch, null);
  const plans = preflight.provider_plan as Record<string, unknown>[];
  assert.deepEqual(plans.map((item) => item.hook_id), [
    "periodic_report.a",
    "periodic_report.z",
  ]);
  assert.deepEqual(
    (plans[0].hook_input as Record<string, unknown>).projection,
    { stage_completion: { transition: "goal_terminal" } },
  );
  assert.deepEqual(
    (plans[1].hook_input as Record<string, unknown>).projection,
    { project_progress: { items: [] } },
  );

  const finalized = await evaluatePostWritebackHookTransaction(
    request(null, {
      phase: "finalize",
      registrations: [
        registration("periodic_report.z", ["project_progress"]),
        registration("periodic_report.a", ["stage_completion"]),
      ],
      transaction_id: preflight.transaction_id,
      provider_outcomes: plans.map((plan, index) =>
        returnedOutcome(plan, intentResult(plan, `intent-${index}`))
      ),
    }),
  );
  const dispatch = finalized.dispatch as Record<string, unknown>;
  assert.equal(dispatch.registered_count, 2);
  assert.equal(dispatch.invoked_count, 2);
  assert.equal(dispatch.intent_count, 2);
  assert.deepEqual(dispatch.failures, []);
});

test("duplicate hook order is bound into the transaction identity", async () => {
  const stage = registration(
    "periodic_report.duplicate",
    ["stage_completion"],
  );
  const progress = registration(
    "periodic_report.duplicate",
    ["project_progress"],
  );
  const stageFirst = await evaluatePostWritebackHookTransaction(
    request(null, { registrations: [stage, progress] }),
  );
  const progressFirst = await evaluatePostWritebackHookTransaction(
    request(null, { registrations: [progress, stage] }),
  );

  assert.notEqual(stageFirst.transaction_id, progressFirst.transaction_id);
  const stagePlan = (stageFirst.provider_plan as Record<string, unknown>[])[0];
  const progressPlan = (progressFirst.provider_plan as Record<string, unknown>[])[0];
  assert.deepEqual(
    (stagePlan.hook_input as Record<string, unknown>).projection,
    { stage_completion: { transition: "goal_terminal" } },
  );
  assert.deepEqual(
    (progressPlan.hook_input as Record<string, unknown>).projection,
    { project_progress: { items: [] } },
  );
});

test("durable finalize writes terminal sidecars and one preflight replays the batch", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const preflight = await evaluatePostWritebackHookTransaction(request(runtimeRoot));
  const plan = (preflight.provider_plan as Record<string, unknown>[])[0];

  const finalized = await evaluatePostWritebackHookTransaction(
    request(runtimeRoot, {
      phase: "finalize",
      transaction_id: preflight.transaction_id,
      provider_outcomes: [returnedOutcome(plan)],
    }),
  );
  const first = finalized.dispatch as Record<string, unknown>;
  assert.equal(first.intent_count, 1);
  assert.equal(first.invoked_count, 1);

  const receiptDirectory = join(
    runtimeRoot,
    "goals",
    "goal-1",
    "post_writeback_hooks",
  );
  const receiptNames = await readdir(receiptDirectory);
  assert.equal(receiptNames.length, 1);
  const receipt = JSON.parse(
    await readFile(join(receiptDirectory, receiptNames[0]), "utf8"),
  ) as Record<string, unknown>;
  assert.equal(receipt.status, "intent_recorded");
  assert.equal(receipt.attempt_count, 1);

  const replay = await evaluatePostWritebackHookTransaction(request(runtimeRoot));
  assert.deepEqual(replay.provider_plan, []);
  const replayDispatch = replay.dispatch as Record<string, unknown>;
  assert.equal(replayDispatch.invoked_count, 0);
  assert.deepEqual(replayDispatch.replayed_hooks, [
    "periodic_report.stage_completion",
  ]);
  assert.deepEqual(replayDispatch.intents, first.intents);
});

test("concurrent finalize uses terminal CAS for exact replay and divergent conflict", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const firstPreflight = await evaluatePostWritebackHookTransaction(
    request(runtimeRoot),
  );
  const secondPreflight = await evaluatePostWritebackHookTransaction(
    request(runtimeRoot),
  );
  const firstPlan = (firstPreflight.provider_plan as Record<string, unknown>[])[0];
  const secondPlan = (secondPreflight.provider_plan as Record<string, unknown>[])[0];
  const firstRequest = request(runtimeRoot, {
    phase: "finalize",
    transaction_id: firstPreflight.transaction_id,
    provider_outcomes: [
      returnedOutcome(firstPlan, intentResult(firstPlan, "intent-a")),
    ],
  });
  const secondRequest = request(runtimeRoot, {
    phase: "finalize",
    transaction_id: secondPreflight.transaction_id,
    provider_outcomes: [
      returnedOutcome(secondPlan, intentResult(secondPlan, "intent-b")),
    ],
  });

  const finalized = await Promise.all([
    evaluatePostWritebackHookTransaction(firstRequest),
    evaluatePostWritebackHookTransaction(secondRequest),
  ]);
  const dispatches = finalized.map(
    (item) => item.dispatch as Record<string, unknown>,
  );
  const winner = dispatches.find((item) => item.intent_count === 1);
  const loser = dispatches.find((item) => item.intent_count === 0);
  assert.ok(winner);
  assert.ok(loser);
  assert.equal(
    (loser.failures as Record<string, unknown>[])[0].error_code,
    "receipt_conflict",
  );
  assert.deepEqual(loser.replayed_hooks, []);

  const winningKey = ((winner.intents as Record<string, unknown>[])[0]
    .idempotency_key) as string;
  const exactRequest = winningKey === "intent-a" ? firstRequest : secondRequest;
  const exactReplay = await evaluatePostWritebackHookTransaction(exactRequest);
  const replayDispatch = exactReplay.dispatch as Record<string, unknown>;
  assert.equal(replayDispatch.intent_count, 1);
  assert.deepEqual(replayDispatch.intents, winner.intents);
  assert.deepEqual(replayDispatch.replayed_hooks, [
    "periodic_report.stage_completion",
  ]);
});

test("exact finalize replay admits deeply nested results without collapsing siblings", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const registrations = [
    registration("periodic_report.a_deep", ["stage_completion"], 65_536),
    registration("periodic_report.b_sibling"),
  ];
  const preflight = await evaluatePostWritebackHookTransaction(
    request(runtimeRoot, { registrations }),
  );
  const plans = preflight.provider_plan as Record<string, unknown>[];

  let payload: Record<string, unknown> = { leaf: true };
  for (let depth = 0; depth < 2_000; depth += 1) {
    payload = { next: payload };
  }
  const deepResult = intentResult(plans[0], "deep-key");
  (deepResult.intent as Record<string, unknown>).payload = payload;
  const finalizeRequest = request(runtimeRoot, {
    phase: "finalize",
    registrations,
    transaction_id: preflight.transaction_id,
    provider_outcomes: [
      returnedOutcome(plans[0], deepResult),
      returnedOutcome(plans[1], intentResult(plans[1], "sibling-key")),
    ],
  });

  const first = await evaluatePostWritebackHookTransaction(finalizeRequest);
  const receiptPaths = plans.map((plan) => receiptPathFor(runtimeRoot, plan));
  const firstReceiptBytes = await Promise.all(
    receiptPaths.map((path) => readFile(path, "utf8")),
  );
  const replay = await evaluatePostWritebackHookTransaction(finalizeRequest);
  const replayReceiptBytes = await Promise.all(
    receiptPaths.map((path) => readFile(path, "utf8")),
  );
  const firstDispatch = first.dispatch as Record<string, unknown>;
  const replayDispatch = replay.dispatch as Record<string, unknown>;

  assert.equal(firstDispatch.intent_count, 2);
  assert.equal(replayDispatch.intent_count, 2);
  assert.deepEqual(firstDispatch.failures, []);
  assert.deepEqual(replayDispatch.failures, []);
  assert.equal(
    JSON.stringify(replayDispatch.intents),
    JSON.stringify(firstDispatch.intents),
  );
  assert.deepEqual(replayDispatch.replayed_hooks, [
    "periodic_report.a_deep",
    "periodic_report.b_sibling",
  ]);
  assert.deepEqual(replayReceiptBytes, firstReceiptBytes);
});

test("an earlier failed receipt write still reserves its duplicate intent key", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const registrations = [
    registration("periodic_report.a_locked"),
    registration("periodic_report.b_duplicate"),
  ];
  const preflight = await evaluatePostWritebackHookTransaction(
    request(runtimeRoot, { registrations }),
  );
  const plans = preflight.provider_plan as Record<string, unknown>[];
  const [lockedPlan, duplicatePlan] = plans;
  const lock = await acquireFileMutationLock(
    receiptPathFor(runtimeRoot, lockedPlan),
  );
  let lockReleased = false;
  t.after(async () => {
    if (!lockReleased) {
      await releaseFileMutationLock(lock.targetPath, lock.token);
    }
  });

  const started = Date.now();
  const finalized = await evaluatePostWritebackHookTransaction(
    request(runtimeRoot, {
      phase: "finalize",
      registrations,
      transaction_id: preflight.transaction_id,
      provider_outcomes: [
        returnedOutcome(lockedPlan, intentResult(lockedPlan, "shared-key")),
        returnedOutcome(
          duplicatePlan,
          intentResult(duplicatePlan, "shared-key"),
        ),
      ],
    }),
  );
  assert.ok(Date.now() - started < 4_000);
  lockReleased = await releaseFileMutationLock(lock.targetPath, lock.token);
  assert.equal(lockReleased, true);

  const dispatch = finalized.dispatch as Record<string, unknown>;
  assert.equal(dispatch.invoked_count, 2);
  assert.equal(dispatch.intent_count, 0);
  assert.deepEqual(dispatch.intents, []);
  assert.deepEqual(
    (dispatch.failures as Record<string, unknown>[]).map((failure) => [
      failure.hook_id,
      failure.error_code,
    ]),
    [
      ["periodic_report.a_locked", "journal_write_failed"],
      ["periodic_report.b_duplicate", "intent_key_conflict"],
    ],
  );
  await assert.rejects(
    readFile(receiptPathFor(runtimeRoot, lockedPlan), "utf8"),
    { code: "ENOENT" },
  );
  const duplicateReceipt = JSON.parse(
    await readFile(receiptPathFor(runtimeRoot, duplicatePlan), "utf8"),
  ) as Record<string, unknown>;
  assert.equal(duplicateReceipt.status, "retryable_failure");
  assert.equal(duplicateReceipt.error_code, "intent_key_conflict");
});

test("a divergent CAS winner reserves its intent key for later hooks", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const registrations = [
    registration("periodic_report.a_marker"),
    registration("periodic_report.b_blocker"),
    registration("periodic_report.c_race"),
    registration("periodic_report.d_duplicate"),
  ];
  const preflight = await evaluatePostWritebackHookTransaction(
    request(runtimeRoot, { registrations }),
  );
  const plans = preflight.provider_plan as Record<string, unknown>[];
  const [markerPlan, blockerPlan, racePlan, duplicatePlan] = plans;
  const blockerLock = await acquireFileMutationLock(
    receiptPathFor(runtimeRoot, blockerPlan),
  );
  let lockReleased = false;
  t.after(async () => {
    if (!lockReleased) {
      await releaseFileMutationLock(
        blockerLock.targetPath,
        blockerLock.token,
      );
    }
  });

  const losingFinalize = evaluatePostWritebackHookTransaction(
    request(runtimeRoot, {
      phase: "finalize",
      registrations,
      transaction_id: preflight.transaction_id,
      provider_outcomes: [
        returnedOutcome(markerPlan, notApplicableResult(markerPlan)),
        returnedOutcome(blockerPlan, notApplicableResult(blockerPlan)),
        returnedOutcome(racePlan, intentResult(racePlan, "divergent-key")),
        returnedOutcome(
          duplicatePlan,
          intentResult(duplicatePlan, "shared-winner-key"),
        ),
      ],
    }),
  );
  await waitForFile(receiptPathFor(runtimeRoot, markerPlan));

  const winnerPreflight = await evaluatePostWritebackHookTransaction(
    request(runtimeRoot, { registrations: [registrations[2]] }),
  );
  const winnerPlan = (
    winnerPreflight.provider_plan as Record<string, unknown>[]
  )[0];
  await evaluatePostWritebackHookTransaction(
    request(runtimeRoot, {
      phase: "finalize",
      registrations: [registrations[2]],
      transaction_id: winnerPreflight.transaction_id,
      provider_outcomes: [
        returnedOutcome(
          winnerPlan,
          intentResult(winnerPlan, "shared-winner-key"),
        ),
      ],
    }),
  );

  lockReleased = await releaseFileMutationLock(
    blockerLock.targetPath,
    blockerLock.token,
  );
  assert.equal(lockReleased, true);
  const finalized = await losingFinalize;
  const dispatch = finalized.dispatch as Record<string, unknown>;
  assert.equal(dispatch.intent_count, 0);
  assert.deepEqual(dispatch.replayed_hooks, []);
  assert.deepEqual(
    (dispatch.failures as Record<string, unknown>[]).map((failure) => [
      failure.hook_id,
      failure.error_code,
    ]),
    [
      ["periodic_report.c_race", "receipt_conflict"],
      ["periodic_report.d_duplicate", "intent_key_conflict"],
    ],
  );

  const raceReceipt = JSON.parse(
    await readFile(receiptPathFor(runtimeRoot, racePlan), "utf8"),
  ) as Record<string, unknown>;
  const duplicateReceipt = JSON.parse(
    await readFile(receiptPathFor(runtimeRoot, duplicatePlan), "utf8"),
  ) as Record<string, unknown>;
  assert.equal(
    (raceReceipt.intent as Record<string, unknown>).idempotency_key,
    "shared-winner-key",
  );
  assert.equal(duplicateReceipt.status, "retryable_failure");
  assert.equal(duplicateReceipt.error_code, "intent_key_conflict");
});

test("finalize requires one bound outcome for every current provider plan", async () => {
  const preflight = await evaluatePostWritebackHookTransaction(request(null));
  await assert.rejects(
    evaluatePostWritebackHookTransaction(
      request(null, {
        phase: "finalize",
        transaction_id: preflight.transaction_id,
        provider_outcomes: [],
      }),
    ),
    /do not cover the current provider plan/,
  );
});

test("retryable provider failure advances the durable attempt on restart", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const firstPreflight = await evaluatePostWritebackHookTransaction(request(runtimeRoot));
  const firstPlan = (firstPreflight.provider_plan as Record<string, unknown>[])[0];
  const failedRequest = request(runtimeRoot, {
    phase: "finalize",
    transaction_id: firstPreflight.transaction_id,
    provider_outcomes: [{
      dispatch_id: firstPlan.dispatch_id,
      hook_id: firstPlan.hook_id,
      capability_id: firstPlan.capability_id,
      attempt_count: firstPlan.attempt_count,
      status: "producer_failed",
      result: null,
    }],
  });
  const failed = await evaluatePostWritebackHookTransaction(
    failedRequest,
  );
  const failedDispatch = failed.dispatch as Record<string, unknown>;
  assert.equal(failedDispatch.intent_count, 0);
  assert.equal(
    (failedDispatch.failures as Record<string, unknown>[])[0].error_code,
    "producer_failed",
  );
  const failedReplay = await evaluatePostWritebackHookTransaction(failedRequest);
  const replayedFailure = failedReplay.dispatch as Record<string, unknown>;
  assert.equal(
    (replayedFailure.failures as Record<string, unknown>[])[0].error_code,
    "producer_failed",
  );

  const retryPreflight = await evaluatePostWritebackHookTransaction(request(runtimeRoot));
  const retryPlan = (retryPreflight.provider_plan as Record<string, unknown>[])[0];
  assert.equal(retryPlan.attempt_count, 2);
  assert.equal(retryPlan.retry, true);
  const recovered = await evaluatePostWritebackHookTransaction(
    request(runtimeRoot, {
      phase: "finalize",
      transaction_id: retryPreflight.transaction_id,
      provider_outcomes: [returnedOutcome(retryPlan)],
    }),
  );
  const recoveredDispatch = recovered.dispatch as Record<string, unknown>;
  assert.deepEqual(recoveredDispatch.retried_hooks, [
    "periodic_report.stage_completion",
  ]);
  assert.equal(recoveredDispatch.intent_count, 1);
});

test("an exhausted retry receipt isolates its hook before provider execution", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const registrations = [
    registration("periodic_report.a_exhausted"),
    registration("periodic_report.b_fresh"),
  ];
  const initial = await evaluatePostWritebackHookTransaction(
    request(runtimeRoot, { registrations }),
  );
  const [exhaustedPlan, initialFreshPlan] = (
    initial.provider_plan as Record<string, unknown>[]
  );
  const exhaustedPath = receiptPathFor(runtimeRoot, exhaustedPlan);
  await mkdir(dirname(exhaustedPath), { recursive: true });
  const exhaustedReceipt = retryableReceipt(exhaustedPlan, 10_000);
  await writeFile(
    exhaustedPath,
    `${JSON.stringify(exhaustedReceipt)}\n`,
    "utf8",
  );

  const preflight = await evaluatePostWritebackHookTransaction(
    request(runtimeRoot, { registrations }),
  );
  const plans = preflight.provider_plan as Record<string, unknown>[];
  assert.deepEqual(plans.map((plan) => plan.hook_id), [
    "periodic_report.b_fresh",
  ]);
  assert.equal(preflight.dispatch, null);
  const freshPlan = plans[0];
  assert.equal(freshPlan.dispatch_id, initialFreshPlan.dispatch_id);
  const finalized = await evaluatePostWritebackHookTransaction(
    request(runtimeRoot, {
      phase: "finalize",
      registrations,
      transaction_id: preflight.transaction_id,
      provider_outcomes: [
        returnedOutcome(freshPlan, intentResult(freshPlan, "fresh-key")),
      ],
    }),
  );
  const dispatch = finalized.dispatch as Record<string, unknown>;
  assert.equal(dispatch.invoked_count, 1);
  assert.deepEqual(dispatch.retried_hooks, ["periodic_report.a_exhausted"]);
  assert.deepEqual(
    (dispatch.failures as Record<string, unknown>[]).map((failure) => [
      failure.hook_id,
      failure.error_code,
    ]),
    [["periodic_report.a_exhausted", "journal_write_failed"]],
  );
  assert.deepEqual(
    JSON.parse(await readFile(exhaustedPath, "utf8")),
    exhaustedReceipt,
  );
  assert.equal(
    (JSON.parse(
      await readFile(receiptPathFor(runtimeRoot, freshPlan), "utf8"),
    ) as Record<string, unknown>).status,
    "intent_recorded",
  );
});

test("the final admitted retry remains idempotent after response loss", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const initial = await evaluatePostWritebackHookTransaction(request(runtimeRoot));
  const initialPlan = (
    initial.provider_plan as Record<string, unknown>[]
  )[0];
  const receiptPath = receiptPathFor(runtimeRoot, initialPlan);
  await mkdir(dirname(receiptPath), { recursive: true });
  await writeFile(
    receiptPath,
    `${JSON.stringify(retryableReceipt(initialPlan, 9_999))}\n`,
    "utf8",
  );

  const preflight = await evaluatePostWritebackHookTransaction(request(runtimeRoot));
  const plan = (preflight.provider_plan as Record<string, unknown>[])[0];
  assert.equal(plan.attempt_count, 10_000);
  const finalizeRequest = request(runtimeRoot, {
    phase: "finalize",
    transaction_id: preflight.transaction_id,
    provider_outcomes: [{
      dispatch_id: plan.dispatch_id,
      hook_id: plan.hook_id,
      capability_id: plan.capability_id,
      attempt_count: plan.attempt_count,
      status: "producer_failed",
      result: null,
    }],
  });
  const first = await evaluatePostWritebackHookTransaction(finalizeRequest);
  const replay = await evaluatePostWritebackHookTransaction(finalizeRequest);

  assert.deepEqual(replay.dispatch, first.dispatch);
  assert.equal(
    (((replay.dispatch as Record<string, unknown>)
      .failures as Record<string, unknown>[])[0]).error_code,
    "producer_failed",
  );
  assert.equal(
    (JSON.parse(await readFile(receiptPath, "utf8")) as Record<string, unknown>)
      .attempt_count,
    10_000,
  );
});

test("mixed batch replays terminal hooks and retries only unfinished work", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const terminalRegistration = registration("periodic_report.terminal");
  const retryRegistration = registration("periodic_report.retry");
  const freshRegistration = registration("periodic_report.fresh");

  const terminalPreflight = await evaluatePostWritebackHookTransaction(
    request(runtimeRoot, { registrations: [terminalRegistration] }),
  );
  const terminalPlan = (
    terminalPreflight.provider_plan as Record<string, unknown>[]
  )[0];
  await evaluatePostWritebackHookTransaction(
    request(runtimeRoot, {
      phase: "finalize",
      registrations: [terminalRegistration],
      transaction_id: terminalPreflight.transaction_id,
      provider_outcomes: [
        returnedOutcome(
          terminalPlan,
          intentResult(terminalPlan, "terminal-intent"),
        ),
      ],
    }),
  );

  const retryPreflight = await evaluatePostWritebackHookTransaction(
    request(runtimeRoot, { registrations: [retryRegistration] }),
  );
  const failedPlan = (
    retryPreflight.provider_plan as Record<string, unknown>[]
  )[0];
  await evaluatePostWritebackHookTransaction(
    request(runtimeRoot, {
      phase: "finalize",
      registrations: [retryRegistration],
      transaction_id: retryPreflight.transaction_id,
      provider_outcomes: [{
        dispatch_id: failedPlan.dispatch_id,
        hook_id: failedPlan.hook_id,
        capability_id: failedPlan.capability_id,
        attempt_count: failedPlan.attempt_count,
        status: "producer_failed",
        result: null,
      }],
    }),
  );

  const invalidRegistration = {
    ...registration("periodic_report.invalid"),
    requested_write_scope: ["forbidden"],
  };
  const registrations = [
    freshRegistration,
    terminalRegistration,
    retryRegistration,
    invalidRegistration,
  ];
  const mixedPreflight = await evaluatePostWritebackHookTransaction(
    request(runtimeRoot, { registrations }),
  );
  const mixedPlans = mixedPreflight.provider_plan as Record<string, unknown>[];
  assert.deepEqual(
    mixedPlans.map((plan) => [plan.hook_id, plan.attempt_count]),
    [
      ["periodic_report.fresh", 1],
      ["periodic_report.retry", 2],
    ],
  );

  const mixed = await evaluatePostWritebackHookTransaction(
    request(runtimeRoot, {
      phase: "finalize",
      registrations,
      transaction_id: mixedPreflight.transaction_id,
      provider_outcomes: mixedPlans.map((plan) =>
        returnedOutcome(plan, intentResult(plan, `${plan.hook_id}-intent`))
      ),
    }),
  );
  const dispatch = mixed.dispatch as Record<string, unknown>;
  assert.deepEqual(dispatch.replayed_hooks, ["periodic_report.terminal"]);
  assert.deepEqual(dispatch.retried_hooks, ["periodic_report.retry"]);
  assert.equal(dispatch.invoked_count, 2);
  assert.equal(dispatch.intent_count, 3);
  assert.equal(
    (dispatch.failures as Record<string, unknown>[])[0].error_code,
    "registration_or_input_rejected",
  );
});

test("mixed terminal and fresh hooks reduce intents in canonical hook order", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const registrations = [
    registration("periodic_report.c"),
    registration("periodic_report.b"),
    registration("periodic_report.a"),
  ];
  const terminalRegistration = registrations[1];
  const terminalPreflight = await evaluatePostWritebackHookTransaction(
    request(runtimeRoot, { registrations: [terminalRegistration] }),
  );
  const terminalPlan = (
    terminalPreflight.provider_plan as Record<string, unknown>[]
  )[0];
  await evaluatePostWritebackHookTransaction(
    request(runtimeRoot, {
      phase: "finalize",
      registrations: [terminalRegistration],
      transaction_id: terminalPreflight.transaction_id,
      provider_outcomes: [
        returnedOutcome(terminalPlan, intentResult(terminalPlan, "key-b")),
      ],
    }),
  );

  const preflight = await evaluatePostWritebackHookTransaction(
    request(runtimeRoot, { registrations }),
  );
  const plans = preflight.provider_plan as Record<string, unknown>[];
  assert.deepEqual(plans.map((plan) => plan.hook_id), [
    "periodic_report.a",
    "periodic_report.c",
  ]);
  const finalized = await evaluatePostWritebackHookTransaction(
    request(runtimeRoot, {
      phase: "finalize",
      registrations,
      transaction_id: preflight.transaction_id,
      provider_outcomes: plans.map((plan) =>
        returnedOutcome(
          plan,
          intentResult(plan, plan.hook_id === "periodic_report.a" ? "key-a" : "key-c"),
        )
      ),
    }),
  );
  const dispatch = finalized.dispatch as Record<string, unknown>;
  assert.deepEqual(
    (dispatch.intents as Record<string, unknown>[]).map(
      (intent) => intent.idempotency_key,
    ),
    ["key-a", "key-b", "key-c"],
  );
  assert.deepEqual(dispatch.replayed_hooks, ["periodic_report.b"]);
});

test("an earlier fresh hook keeps a duplicate intent key ahead of terminal replay", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const earlyRegistration = registration("periodic_report.a");
  const terminalRegistration = registration("periodic_report.b");
  const terminalPreflight = await evaluatePostWritebackHookTransaction(
    request(runtimeRoot, { registrations: [terminalRegistration] }),
  );
  const terminalPlan = (
    terminalPreflight.provider_plan as Record<string, unknown>[]
  )[0];
  await evaluatePostWritebackHookTransaction(
    request(runtimeRoot, {
      phase: "finalize",
      registrations: [terminalRegistration],
      transaction_id: terminalPreflight.transaction_id,
      provider_outcomes: [
        returnedOutcome(terminalPlan, intentResult(terminalPlan, "shared-key")),
      ],
    }),
  );

  const registrations = [terminalRegistration, earlyRegistration];
  const preflight = await evaluatePostWritebackHookTransaction(
    request(runtimeRoot, { registrations }),
  );
  const earlyPlan = (
    preflight.provider_plan as Record<string, unknown>[]
  )[0];
  const finalized = await evaluatePostWritebackHookTransaction(
    request(runtimeRoot, {
      phase: "finalize",
      registrations,
      transaction_id: preflight.transaction_id,
      provider_outcomes: [
        returnedOutcome(earlyPlan, intentResult(earlyPlan, "shared-key")),
      ],
    }),
  );
  const dispatch = finalized.dispatch as Record<string, unknown>;
  assert.deepEqual(
    (dispatch.intents as Record<string, unknown>[]).map(
      (intent) => intent.source_receipt_id,
    ),
    [
      ((earlyPlan.hook_input as Record<string, unknown>)
        .receipt as Record<string, unknown>).event_id,
    ],
  );
  assert.deepEqual(dispatch.replayed_hooks, []);
  assert.deepEqual(
    (dispatch.failures as Record<string, unknown>[]).map((failure) => [
      failure.hook_id,
      failure.error_code,
    ]),
    [["periodic_report.b", "intent_key_conflict"]],
  );
});

test("registration and provider failures preserve canonical per-hook order", async () => {
  const first = registration("periodic_report.a");
  const duplicate = registration("periodic_report.a");
  const invalid = {
    ...registration("periodic_report.b"),
    requested_write_scope: ["forbidden"],
  };
  const last = registration("periodic_report.c");
  const registrations = [last, duplicate, invalid, first];
  const preflight = await evaluatePostWritebackHookTransaction(
    request(null, { registrations }),
  );
  const plans = preflight.provider_plan as Record<string, unknown>[];
  const finalized = await evaluatePostWritebackHookTransaction(
    request(null, {
      phase: "finalize",
      registrations,
      transaction_id: preflight.transaction_id,
      provider_outcomes: plans.map((plan) => ({
        dispatch_id: plan.dispatch_id,
        hook_id: plan.hook_id,
        capability_id: plan.capability_id,
        attempt_count: plan.attempt_count,
        status: plan.hook_id === "periodic_report.a"
          ? "producer_failed"
          : "contract_rejected",
        result: null,
      })),
    }),
  );
  assert.deepEqual(
    ((finalized.dispatch as Record<string, unknown>)
      .failures as Record<string, unknown>[]).map((failure) => [
        failure.hook_id,
        failure.error_code,
      ]),
    [
      ["periodic_report.a", "producer_failed"],
      ["periodic_report.a", "duplicate_hook_id"],
      ["periodic_report.b", "registration_or_input_rejected"],
      ["periodic_report.c", "contract_rejected"],
    ],
  );
});

test("a stale deferred outcome does not block a later current plan", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const registrations = [
    registration("periodic_report.a_deferred"),
    registration("periodic_report.b_committed"),
  ];
  const preflight = await evaluatePostWritebackHookTransaction(
    request(runtimeRoot, { registrations }),
  );
  const [deferredPlan, committedPlan] = (
    preflight.provider_plan as Record<string, unknown>[]
  );
  const deferredPath = receiptPathFor(runtimeRoot, deferredPlan);
  await mkdir(dirname(deferredPath), { recursive: true });
  const newerRetry = retryableReceipt(deferredPlan, 3);
  await writeFile(deferredPath, `${JSON.stringify(newerRetry)}\n`, "utf8");

  const finalized = await evaluatePostWritebackHookTransaction(
    request(runtimeRoot, {
      phase: "finalize",
      registrations,
      transaction_id: preflight.transaction_id,
      provider_outcomes: [
        {
          dispatch_id: deferredPlan.dispatch_id,
          hook_id: deferredPlan.hook_id,
          capability_id: deferredPlan.capability_id,
          attempt_count: deferredPlan.attempt_count,
          status: "receipt_changed",
          result: null,
        },
        returnedOutcome(
          committedPlan,
          intentResult(committedPlan, "committed-key"),
        ),
      ],
    }),
  );
  const dispatch = finalized.dispatch as Record<string, unknown>;
  assert.equal(dispatch.invoked_count, 1);
  assert.deepEqual(
    (dispatch.intents as Record<string, unknown>[]).map(
      (intent) => intent.idempotency_key,
    ),
    ["committed-key"],
  );
  assert.equal(
    (dispatch.failures as Record<string, unknown>[])[0].error_code,
    "receipt_conflict",
  );
  assert.deepEqual(
    JSON.parse(await readFile(deferredPath, "utf8")),
    newerRetry,
  );
  assert.equal(
    (JSON.parse(
      await readFile(receiptPathFor(runtimeRoot, committedPlan), "utf8"),
    ) as Record<string, unknown>).status,
    "intent_recorded",
  );
});

test("duplicate intent keys isolate the later hook and persist a retryable receipt", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const registrations = [
    registration("periodic_report.a"),
    registration("periodic_report.b"),
  ];
  const preflight = await evaluatePostWritebackHookTransaction(
    request(runtimeRoot, { registrations }),
  );
  const plans = preflight.provider_plan as Record<string, unknown>[];
  const finalized = await evaluatePostWritebackHookTransaction(
    request(runtimeRoot, {
      phase: "finalize",
      registrations,
      transaction_id: preflight.transaction_id,
      provider_outcomes: plans.map((plan) =>
        returnedOutcome(plan, intentResult(plan, "same-intent-key"))
      ),
    }),
  );
  const dispatch = finalized.dispatch as Record<string, unknown>;
  assert.equal(dispatch.intent_count, 1);
  assert.equal(
    (dispatch.failures as Record<string, unknown>[])[0].error_code,
    "intent_key_conflict",
  );

  const receiptDirectory = join(
    runtimeRoot,
    "goals",
    "goal-1",
    "post_writeback_hooks",
  );
  const receipts = await Promise.all(
    (await readdir(receiptDirectory)).map(async (name) =>
      JSON.parse(await readFile(join(receiptDirectory, name), "utf8")) as Record<
        string,
        unknown
      >
    ),
  );
  assert.deepEqual(
    receipts.map((receipt) => receipt.status).sort(),
    ["intent_recorded", "retryable_failure"],
  );
});

test("finalize rejects a transaction id from different source facts", async () => {
  const preflight = await evaluatePostWritebackHookTransaction(request(null));
  const plan = (preflight.provider_plan as Record<string, unknown>[])[0];
  const changedSource = source();
  changedSource.state_version = "state-v2";
  await assert.rejects(
    evaluatePostWritebackHookTransaction(
      request(null, {
        phase: "finalize",
        source: changedSource,
        transaction_id: preflight.transaction_id,
        provider_outcomes: [returnedOutcome(plan)],
      }),
    ),
    /transaction_id/,
  );
});

test("committed receipt ids separate same-clock phases and recover the original intent", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "loopx-hook-receipt-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const ordinarySource = {...source(), receipt_id: "completion-ordinary", state_version: "completion-ordinary"};
  const ordinary = await evaluatePostWritebackHookTransaction(request(root, {source: ordinarySource}));
  const ordinaryPlan = (ordinary.provider_plan as Record<string, unknown>[])[0];
  await evaluatePostWritebackHookTransaction(request(root, {
    phase: "finalize", source: ordinarySource, transaction_id: ordinary.transaction_id,
    provider_outcomes: [returnedOutcome(ordinaryPlan, notApplicableResult(ordinaryPlan))],
  }));
  const closeoutSource = {...ordinarySource, receipt_id: "completion-closeout", state_version: "completion-closeout"};
  const closeout = await evaluatePostWritebackHookTransaction(request(root, {source: closeoutSource}));
  const closeoutPlan = (closeout.provider_plan as Record<string, unknown>[])[0];
  assert.notEqual(closeoutPlan.dispatch_id, ordinaryPlan.dispatch_id);
  const recorded = await evaluatePostWritebackHookTransaction(request(root, {
    phase: "finalize", source: closeoutSource, transaction_id: closeout.transaction_id,
    provider_outcomes: [returnedOutcome(closeoutPlan)],
  }));
  const recordedDispatch = recorded.dispatch as Record<string, unknown>;
  assert.equal(recordedDispatch.intent_count, 1);
  // Later read-model clock changes cannot redefine a committed receipt.
  const replay = await evaluatePostWritebackHookTransaction(request(root, {
    source: {...closeoutSource, committed_at: "2026-09-03T12:00:00Z"},
  }));
  assert.deepEqual(replay.provider_plan, []);
  const replayDispatch = replay.dispatch as Record<string, unknown>;
  assert.deepEqual(replayDispatch.intents, recordedDispatch.intents);
  assert.deepEqual(replayDispatch.replayed_hooks, ["periodic_report.stage_completion"]);
});

test("transaction request rejects unknown top-level fields", async () => {
  await assert.rejects(
    evaluatePostWritebackHookTransaction(
      request(null, { unexpected: true }),
    ),
    /request fields are invalid/,
  );
});

test("malformed durable sidecar is isolated before provider execution", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const preflight = await evaluatePostWritebackHookTransaction(request(runtimeRoot));
  const plan = (preflight.provider_plan as Record<string, unknown>[])[0];
  const receiptDirectory = join(
    runtimeRoot,
    "goals",
    "goal-1",
    "post_writeback_hooks",
  );
  await mkdir(receiptDirectory, { recursive: true });
  await writeFile(join(receiptDirectory, `${plan.dispatch_id}.json`), "{}\n", "utf8");

  const conflicted = await evaluatePostWritebackHookTransaction(request(runtimeRoot));
  assert.deepEqual(conflicted.provider_plan, []);
  const dispatch = conflicted.dispatch as Record<string, unknown>;
  assert.equal(
    (dispatch.failures as Record<string, unknown>[])[0].error_code,
    "journal_read_failed",
  );
});

test("finalize isolates a sidecar that changed after preflight", async (t) => {
  for (const fixture of ["malformed", "contract_conflict"] as const) {
    await t.test(fixture, async (t) => {
      const runtimeRoot = await tempRuntime(t);
      const registrations = [
        registration("periodic_report.a_blocked"),
        registration("periodic_report.b_committed"),
      ];
      const preflight = await evaluatePostWritebackHookTransaction(
        request(runtimeRoot, { registrations }),
      );
      const plans = preflight.provider_plan as Record<string, unknown>[];
      const [blockedPlan, committedPlan] = plans;
      const blockedPath = receiptPathFor(runtimeRoot, blockedPlan);
      await mkdir(dirname(blockedPath), { recursive: true });
      const blockedReceipt = fixture === "malformed"
        ? {}
        : {
          schema_version: "loopx_post_writeback_capability_hook_receipt_v0",
          dispatch_id: blockedPlan.dispatch_id,
          hook_id: blockedPlan.hook_id,
          capability_id: blockedPlan.capability_id,
          source_receipt_id: "wrong-source",
          status: "not_applicable",
          intent: null,
          error_code: null,
          attempt_count: 1,
          recorded_at: "2026-09-02T12:00:00Z",
        };
      await writeFile(
        blockedPath,
        `${JSON.stringify(blockedReceipt)}\n`,
        "utf8",
      );

      const finalized = await evaluatePostWritebackHookTransaction(
        request(runtimeRoot, {
          phase: "finalize",
          registrations,
          transaction_id: preflight.transaction_id,
          provider_outcomes: plans.map((plan) =>
            returnedOutcome(plan, intentResult(plan, `${plan.hook_id}-key`))
          ),
        }),
      );
      const dispatch = finalized.dispatch as Record<string, unknown>;
      assert.equal(dispatch.invoked_count, 2);
      assert.equal(dispatch.intent_count, 1);
      assert.deepEqual(dispatch.intents, [
        intentResult(
          committedPlan,
          `${committedPlan.hook_id}-key`,
        ).intent,
      ]);
      assert.equal(
        (dispatch.failures as Record<string, unknown>[])[0].error_code,
        fixture === "malformed" ? "journal_read_failed" : "receipt_conflict",
      );
      assert.deepEqual(
        JSON.parse(await readFile(blockedPath, "utf8")),
        blockedReceipt,
      );
      const committedReceipt = JSON.parse(
        await readFile(receiptPathFor(runtimeRoot, committedPlan), "utf8"),
      ) as Record<string, unknown>;
      assert.equal(committedReceipt.status, "intent_recorded");
    });
  }
});

test("finalize still rejects an unknown extra outcome", async () => {
  const preflight = await evaluatePostWritebackHookTransaction(request(null));
  const plan = (preflight.provider_plan as Record<string, unknown>[])[0];
  await assert.rejects(
    evaluatePostWritebackHookTransaction(
      request(null, {
        phase: "finalize",
        transaction_id: preflight.transaction_id,
        provider_outcomes: [
          returnedOutcome(plan),
          {
            ...returnedOutcome(plan),
            dispatch_id: `pwh_${"f".repeat(64)}`,
          },
        ],
      }),
    ),
    /outside this transaction/,
  );
});

test("result envelope admission happens before providers and durable writes", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const registrations = Array.from(
    { length: 27 },
    (_, index) => registration(
      `periodic_report.envelope_${String(index).padStart(2, "0")}`,
      ["stage_completion"],
      65_536,
    ),
  );
  const receiptDirectory = join(
    runtimeRoot,
    "goals",
    "goal-1",
    "post_writeback_hooks",
  );

  await assert.rejects(
    evaluatePostWritebackHookTransaction(
      request(runtimeRoot, { registrations }),
    ),
    /may exceed the transport envelope/,
  );
  await assert.rejects(readdir(receiptDirectory), { code: "ENOENT" });

  const allPlans: Record<string, unknown>[] = [];
  for (let offset = 0; offset < registrations.length; offset += 8) {
    const chunk = registrations.slice(offset, offset + 8);
    const preflight = await evaluatePostWritebackHookTransaction(
      request(runtimeRoot, { registrations: chunk }),
    );
    const plans = preflight.provider_plan as Record<string, unknown>[];
    allPlans.push(...plans);
    const finalized = await evaluatePostWritebackHookTransaction(
      request(runtimeRoot, {
        phase: "finalize",
        registrations: chunk,
        transaction_id: preflight.transaction_id,
        provider_outcomes: plans.map((plan, index) => {
          const result = intentResult(plan, `${plan.hook_id}-key`);
          if (offset + index < 24) {
            (result.intent as Record<string, unknown>).payload = {
              evidence: "x".repeat(60_000),
            };
          }
          return returnedOutcome(plan, result);
        }),
      }),
    );
    assert.equal(
      (finalized.dispatch as Record<string, unknown>).intent_count,
      chunk.length,
    );
  }
  assert.equal((await readdir(receiptDirectory)).length, 27);

  const replay = await evaluatePostWritebackHookTransaction(
    request(runtimeRoot, { registrations }),
  );
  assert.deepEqual(replay.provider_plan, []);
  const replayDispatch = replay.dispatch as Record<string, unknown>;
  assert.equal(replayDispatch.intent_count, 27);
  assert.equal(
    (replayDispatch.replayed_hooks as unknown[]).length,
    27,
  );

  const freshRegistrations = Array.from(
    { length: 5 },
    (_, index) => registration(
      `periodic_report.mixed_fresh_${String(index).padStart(2, "0")}`,
      ["stage_completion"],
      65_536,
    ),
  );
  await assert.rejects(
    evaluatePostWritebackHookTransaction(
      request(runtimeRoot, {
        registrations: [...registrations, ...freshRegistrations],
      }),
    ),
    /may exceed the transport envelope/,
  );

  assert.equal(allPlans.length, 27);
  assert.equal((await readdir(receiptDirectory)).length, 27);
});
