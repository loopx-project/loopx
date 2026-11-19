import assert from "node:assert/strict";
import test from "node:test";

import {
  validateGovernedCapabilityAdmission,
  validateGovernedCapabilityResult,
  validateGovernedCapabilitySettlementCallback,
} from "../../loopx/control_plane/governed_capability.ts";

const authority = {
  invocation_id: "capability-synthetic",
  effect_id: "goal:agent:todo:turn",
  result_schema: "synthetic_material_result_v0",
  effect_class: "external_write",
} as const;

const settlementIdentity = {
  schema_version: "quota_settlement_identity_v0",
  goal_id: "fixture-goal",
  agent_id: "fixture-agent",
  todo_id: "todo_fixture",
  turn_instance_id: "fixture-turn",
  effect_id: authority.effect_id,
};

const providerRequest = {
  invocation_id: authority.invocation_id,
  authority: settlementIdentity,
  lifecycle: {
    phase: "start",
    idempotency_key: authority.effect_id,
  },
};

const providerRequestDigest =
  "sha256:76b78b8c551187420d89b4525a86d06f92d0eb1b4a7aadc65eadc38c8ec03770";
const effectReceiptDigest =
  "sha256:8bb4405904cc29c96d4124430ec162409db148b856a57371801848b9bd4e8050";

const transitionContract = {
  proposal_kinds: [
    "continuous_monitor_upsert",
    "continuous_monitor_complete",
  ],
  monitor_key_prefixes: ["fixture:"],
  monitor_action_kinds: ["poll_delivery"],
  monitor_target_key_prefixes: ["delivery-run:"],
  monitor_required_capabilities: ["network"],
};

const monitorUpsert = {
  schema_version: "loopx_continuous_monitor_proposal_v0",
  proposal_id: "monitor_upsert_1",
  kind: "continuous_monitor_upsert",
  monitor_key: "fixture:delivery-run",
  text: "Poll the synthetic delivery run.",
  action_kind: "poll_delivery",
  target_key: "delivery-run:synthetic-1",
  cadence: "5m",
  next_due_at: "2099-01-01T00:05:00+00:00",
  expires_at: "2099-01-02T00:00:00+00:00",
  required_capabilities: ["network"],
};

const monitorComplete = {
  schema_version: "loopx_continuous_monitor_proposal_v0",
  proposal_id: "monitor_complete_1",
  kind: "continuous_monitor_complete",
  monitor_key: "fixture:delivery-run",
  evidence: "Synthetic delivery reached a terminal state.",
};

function result(status: "running" | "succeeded" | "no_change") {
  return {
    schema_version: authority.result_schema,
    invocation_id: authority.invocation_id,
    status,
    observations: [],
    domain_state_mutations: [],
    domain_transition_receipts: [],
    transition_proposals: [],
    effect_receipt:
      status === "running"
        ? null
        : {
            schema_version: "loopx_external_effect_receipt_v0",
            invocation_id: authority.invocation_id,
            idempotency_key: authority.effect_id,
            status: status === "no_change" ? "no_change" : "committed",
            external_ref: "synthetic-run-1",
            evidence_digest: "sha256:synthetic-evidence",
          },
    follow_up: { kind: status === "running" ? "poll" : "none" },
  };
}

function lifecycleJournal(
  status: "starting" | "running" | "ready_to_settle" | "settlement_failed" | "committed",
  providerResult: ReturnType<typeof result> | null,
) {
  return {
    schema_version: "loopx_governed_capability_run_v0",
    status,
    invocation_id: authority.invocation_id,
    request_digest: providerRequestDigest,
    request: providerRequest,
    operation_profile: {
      effect_class: authority.effect_class,
      result_schema: authority.result_schema,
      todo_contract: {
        action_kinds: ["publish_requirement"],
        target_key_prefixes: ["requirement:"],
      },
      transition_contract: transitionContract,
    },
    transaction_plan: {
      settlement_plan: { identity: settlementIdentity },
    },
    goal_id: settlementIdentity.goal_id,
    agent_id: settlementIdentity.agent_id,
    todo_id: settlementIdentity.todo_id,
    turn_instance_id: settlementIdentity.turn_instance_id,
    effect_id: settlementIdentity.effect_id,
    completed_phases: [],
    provider_result: providerResult,
    transition_receipts: [],
    writeback: null,
    quota_spend: null,
    settlement_result: null,
  };
}

function reduceLifecycle(
  journal: ReturnType<typeof lifecycleJournal>,
  phase: "inspect" | "observe_result" = "inspect",
) {
  return validateGovernedCapabilityResult({
    ...authority,
    transition_contract: transitionContract,
    value: {
      schema_version: "loopx_governed_capability_lifecycle_packet_v0",
      phase,
      dry_run: false,
      canonical_request_digest: providerRequestDigest,
      admission: null,
      journal,
    },
  });
}

test("material admission binds the exact Todo action to the operation", () => {
  const admission = {
    selected_todo: {
      todo_id: "todo_material1",
      role: "agent",
      status: "open",
      action_kind: "publish_requirement",
      target_key: "requirement:REQ-1",
    },
  };
  assert.equal(
    validateGovernedCapabilityAdmission({
      admission,
      todo_id: "todo_material1",
      todo_contract: {
        action_kinds: ["publish_requirement"],
        target_key_prefixes: ["requirement:"],
      },
    }).todo_id,
    "todo_material1",
  );
  assert.throws(
    () => validateGovernedCapabilityAdmission({
      admission,
      todo_id: "todo_material1",
      todo_contract: {
        action_kinds: ["deploy_release"],
        target_key_prefixes: ["release:"],
      },
    }),
    /not authorized by selected_todo action_kind/,
  );

  assert.throws(
    () => validateGovernedCapabilityAdmission({
      admission,
      todo_id: "todo_material1",
      todo_contract: {
        action_kinds: ["publish_requirement"],
        target_key_prefixes: ["release:"],
      },
    }),
    /not authorized by selected_todo target_key/,
  );
});

test("material journal inspection revalidates authority before recovery writes", () => {
  const journal = lifecycleJournal("ready_to_settle", result("succeeded"));
  const admission = {
    selected_todo: {
      todo_id: settlementIdentity.todo_id,
      role: "agent",
      status: "open",
      action_kind: "publish_requirement",
      target_key: "requirement:REQ-1",
    },
  };
  const reduce = (selectedAdmission: unknown) =>
    validateGovernedCapabilityResult({
      ...authority,
      transition_contract: transitionContract,
      value: {
        schema_version: "loopx_governed_capability_lifecycle_packet_v0",
        phase: "inspect",
        dry_run: false,
        canonical_request_digest: providerRequestDigest,
        admission: selectedAdmission,
        journal,
      },
    });

  assert.equal(reduce(admission).provider_result.status, "succeeded");
  assert.throws(
    () => reduce({
      selected_todo: {
        ...admission.selected_todo,
        action_kind: "deploy_release",
      },
    }),
    /not authorized by selected_todo action_kind/,
  );
});

test("material provider state reduces to a typed journal status", () => {
  assert.equal(
    validateGovernedCapabilityResult({
      ...authority,
      value: result("running"),
    }).journal_status,
    "running",
  );
  assert.equal(
    validateGovernedCapabilityResult({
      ...authority,
      value: result("succeeded"),
    }).journal_status,
    "ready_to_settle",
  );
});

test("running providers cannot claim partial effects", () => {
  const invalid = result("running");
  invalid.domain_state_mutations.push({ kind: "partial-write" });

  assert.throws(
    () => validateGovernedCapabilityResult({ ...authority, value: invalid }),
    /must leave domain_state_mutations empty/,
  );
});

test("running providers may propose one admitted continuous monitor", () => {
  const running = result("running");
  running.transition_proposals = [{ ...monitorUpsert }];

  const validated = validateGovernedCapabilityResult({
    ...authority,
    transition_contract: transitionContract,
    value: running,
  });

  assert.equal(validated.journal_status, "running");
  assert.deepEqual(validated.result.transition_proposals, [monitorUpsert]);
});

test("transition proposals fail closed outside profile authority", () => {
  const running = result("running");
  running.transition_proposals = [{ ...monitorUpsert }];
  assert.throws(
    () => validateGovernedCapabilityResult({ ...authority, value: running }),
    /transition_contract must be an object/,
  );

  const wrongAction = result("running");
  wrongAction.transition_proposals = [{
    ...monitorUpsert,
    action_kind: "deploy_release",
  }];
  assert.throws(
    () => validateGovernedCapabilityResult({
      ...authority,
      transition_contract: transitionContract,
      value: wrongAction,
    }),
    /action_kind is not admitted/,
  );

  const wrongMonitor = result("running");
  wrongMonitor.transition_proposals = [{
    ...monitorUpsert,
    monitor_key: "unrelated:delivery-run",
  }];
  assert.throws(
    () => validateGovernedCapabilityResult({
      ...authority,
      transition_contract: transitionContract,
      value: wrongMonitor,
    }),
    /monitor_key is not admitted/,
  );

  const wrongTarget = result("running");
  wrongTarget.transition_proposals = [{
    ...monitorUpsert,
    target_key: "unrelated:synthetic-1",
  }];
  assert.throws(
    () => validateGovernedCapabilityResult({
      ...authority,
      transition_contract: transitionContract,
      value: wrongTarget,
    }),
    /target_key is not admitted/,
  );

  const wrongCapability = result("running");
  wrongCapability.transition_proposals = [{
    ...monitorUpsert,
    required_capabilities: ["production_access"],
  }];
  assert.throws(
    () => validateGovernedCapabilityResult({
      ...authority,
      transition_contract: transitionContract,
      value: wrongCapability,
    }),
    /required_capabilities are not admitted/,
  );
});

test("running providers cannot complete a monitor", () => {
  const running = result("running");
  running.transition_proposals = [{ ...monitorComplete }];

  assert.throws(
    () => validateGovernedCapabilityResult({
      ...authority,
      transition_contract: transitionContract,
      value: running,
    }),
    /running result may only upsert a monitor/,
  );
});

test("successful providers may complete an admitted monitor", () => {
  const succeeded = result("succeeded");
  succeeded.transition_proposals = [{ ...monitorComplete }];

  const validated = validateGovernedCapabilityResult({
    ...authority,
    transition_contract: transitionContract,
    value: succeeded,
  });

  assert.equal(validated.journal_status, "ready_to_settle");
  assert.deepEqual(validated.result.transition_proposals, [monitorComplete]);
});

test("terminal effect receipts bind the exact settlement effect", () => {
  const invalid = result("succeeded");
  assert.ok(invalid.effect_receipt);
  invalid.effect_receipt.idempotency_key = "different-effect";

  assert.throws(
    () => validateGovernedCapabilityResult({ ...authority, value: invalid }),
    /idempotency_key is invalid/,
  );
});

test("terminal result and effect receipt cannot contradict each other", () => {
  const falseCommit = result("no_change");
  assert.ok(falseCommit.effect_receipt);
  falseCommit.effect_receipt.status = "committed";
  assert.throws(
    () => validateGovernedCapabilityResult({ ...authority, value: falseCommit }),
    /requires a no-change effect receipt/,
  );

  const falseNoChange = result("succeeded");
  assert.ok(falseNoChange.effect_receipt);
  falseNoChange.effect_receipt.status = "no_change";
  assert.throws(
    () => validateGovernedCapabilityResult({ ...authority, value: falseNoChange }),
    /requires a committed effect receipt/,
  );

  const mutatedNoChange = result("no_change");
  mutatedNoChange.domain_state_mutations.push({ kind: "contradictory-write" });
  assert.throws(
    () =>
      validateGovernedCapabilityResult({ ...authority, value: mutatedNoChange }),
    /must leave domain_state_mutations empty/,
  );
});

test("writeback proves the effect receipt before settlement can spend", () => {
  const context = {
    effect_id: authority.effect_id,
    effect_receipt_digest: "sha256:receipt",
    require_receipt_digest: true,
  };
  const rejected = validateGovernedCapabilitySettlementCallback({
    ...context,
    payload: {
      ok: true,
      appended: true,
      settlement_identity: { effect_id: authority.effect_id },
    },
  });
  assert.deepEqual(rejected, {
    ok: false,
    appended: false,
    reason: "effect receipt was not written back",
  });

  const committed = validateGovernedCapabilitySettlementCallback({
    ...context,
    payload: {
      ok: true,
      appended: true,
      settlement_identity: { effect_id: authority.effect_id },
      effect_receipt_digest: context.effect_receipt_digest,
    },
  });
  assert.equal(committed.ok, true);
});

test("one lifecycle packet validates an observed provider result and projects its receipt", () => {
  const journal = lifecycleJournal("starting", result("succeeded"));
  const reduction = reduceLifecycle(journal, "observe_result");

  assert.equal(
    reduction.schema_version,
    "loopx_governed_capability_lifecycle_reduction_v0",
  );
  assert.equal(reduction.journal_status, "ready_to_settle");
  assert.equal(reduction.provider_result.status, "succeeded");
  assert.deepEqual(reduction.public_receipt.effects, {
    provider_invoked: true,
    external_write_observed: true,
    loopx_transitions_written: false,
    loopx_state_written: false,
    quota_spent: false,
  });
});

test("committed lifecycle replay validates the whole journal in one reduction", () => {
  const journal = lifecycleJournal("committed", result("succeeded"));
  journal.completed_phases = [
    "host_execute",
    "typed_result",
    "validation",
    "durable_writeback",
    "quota_spend",
  ];
  journal.writeback = {
    ok: true,
    appended: true,
    settlement_identity: settlementIdentity,
    effect_receipt_digest: effectReceiptDigest,
  };
  journal.quota_spend = {
    ok: true,
    appended: true,
    settlement_identity: settlementIdentity,
  };
  journal.settlement_result = { failure: null };

  const reduction = reduceLifecycle(journal);

  assert.equal(reduction.journal_status, "committed");
  assert.equal(reduction.public_receipt.status, "committed");
  assert.equal(reduction.public_receipt.effects.loopx_state_written, true);
  assert.equal(reduction.public_receipt.effects.quota_spent, true);
});

test("lifecycle packets reject tampered request and callback identity", () => {
  const tamperedRequest = lifecycleJournal("running", result("running"));
  tamperedRequest.request = {
    ...providerRequest,
    lifecycle: { ...providerRequest.lifecycle, idempotency_key: "different" },
  };
  assert.throws(
    () => reduceLifecycle(tamperedRequest),
    /request (start lifecycle|digest) is invalid/,
  );

  const tamperedCallback = lifecycleJournal("committed", result("succeeded"));
  tamperedCallback.writeback = {
    ok: true,
    appended: true,
    settlement_identity: { effect_id: "different" },
    effect_receipt_digest: effectReceiptDigest,
  };
  tamperedCallback.quota_spend = {
    ok: true,
    appended: true,
    settlement_identity: settlementIdentity,
  };
  tamperedCallback.settlement_result = { failure: null };
  assert.throws(
    () => reduceLifecycle(tamperedCallback),
    /journal writeback is invalid/,
  );
});
