import assert from "node:assert/strict";
import test from "node:test";

import {
  compileActionReviewPlan,
  compileOperationReviewFrame,
  compileReviewCardFrame,
  isStaleActionFailure,
} from "../../loopx/control_plane/presentation/action_review_plan.ts";

test("interrupted Goal bootstrap retries its original identity before downstream effects", () => {
  const proposal = {proposal_id: "creation", expected_state_fingerprint: "original-registry",
    action_kind: "goal.create", permission_classification: "durable_write", status: "failed",
    normalized_parameters: {goal_id: "new-goal"},
    checkpoint: {steps: {workspace_validated: {outcome: "workspace_validated"}}}};
  const plan = compileActionReviewPlan(proposal);
  assert.equal(plan.reason, "goal_creation_retry");
  assert.equal(plan.retryOriginal, true);
  assert.equal(plan.canApply, true);
  assert.equal(plan.proposalId, "creation");
  for (const change of [{checkpoint: null}, {status: "applying"}, {status: "gated"}, {status: "stale"},
    {status: "applied", receipt: {projection_verified: true}}, {action_kind: "goal.update"},
    {permission_classification: "protected"}, {proposal_id: ""}, {normalized_parameters: {}},
    {checkpoint: {steps: {...proposal.checkpoint.steps, goal_bootstrapped: {goal_id: "new-goal"}}}}]) {
    assert.equal(compileActionReviewPlan({...proposal, ...change}).retryOriginal, undefined);
  }
});

test("canonical update recovery includes User completion without changing generic action authority", () => {
  const proposal = {proposal_id: "reviewed", expected_state_fingerprint: "review-basis",
    action_kind: "todo.update", status: "failed", normalized_parameters: {operation: "edit"},
    canonical_update_basis: {schema_version: "loopx_chat_canonical_update_basis_v0",
      provider_revision: "revision", registry_sha256: "a".repeat(64)},
    failure: {error_code: "canonical_update_projection_pending"}};
  const plan = compileActionReviewPlan(proposal);
  assert.equal(plan.retryOriginal, true);
  assert.equal(plan.canApply, true);
  assert.equal(plan.reason, "canonical_update_projection_pending");
  assert.equal(compileActionReviewPlan({...proposal, status: "applying"}).retryOriginal, true);
  const completedUpdate = compileActionReviewPlan({...proposal, normalized_parameters: {operation: "complete"}});
  assert.equal(completedUpdate.retryOriginal, true);
  assert.equal(completedUpdate.canApply, true);
  assert.equal(completedUpdate.interaction, "review");
  for (const change of [{canonical_update_basis: undefined}, {action_kind: "operation.execute"},
    {normalized_parameters: {operation: "complete"}, canonical_update_basis: undefined}, {status: "stale"}, {status: "gated"},
    {status: "applied", receipt: {projection_verified: true}}]) {
    assert.equal(compileActionReviewPlan({...proposal, ...change}).retryOriginal, undefined);
  }
});

function operationProposal(
  lifecycleState: "awaiting_confirmation" | "claimed" | "outcome_observed",
) {
  return {
    schema_version: "loopx_chat_action_proposal_v1",
    proposal_id: "operation-1",
    action_kind: "operation.execute",
    permission_classification: "protected",
    status: lifecycleState === "outcome_observed" ? "applied" : "gated",
    expected_state_fingerprint: "state-1",
    normalized_parameters: {
      projection: {
        schema_version: "loopx_operation_projection_v0",
        title: "Review simulated order",
        subtitle: "Bound Goal Channel request",
        focus: "BUY 1 SYNTH @ 10 TEST",
        fields: [{ label: "Order", value: "Limit · GTC" }],
        warning: "Simulation only.",
        simulated: true,
      },
    },
    operation: {
      schema_version: "loopx_operation_envelope_v0",
      lifecycle_state: lifecycleState,
      operation_id: "operation-1",
      confirmation_digest: "confirmation-1",
      expires_at: "2026-01-02T00:00:00Z",
      outcome: lifecycleState === "outcome_observed"
        ? {
            outcome: "executed",
            simulation: true,
            summary: "Simulation completed.",
          }
        : null,
      result_delivery: lifecycleState === "outcome_observed"
        ? { receipt_id: "delivery-1" }
        : null,
    },
  };
}

test("operation frame preserves exact confirmation identity and bounded content", () => {
  const frame = compileOperationReviewFrame(operationProposal("awaiting_confirmation"));

  assert.deepEqual(frame, {
    schemaVersion: "operation_review_frame_v0",
    operationId: "operation-1",
    confirmationDigest: "confirmation-1",
    lifecycleState: "awaiting_confirmation",
    simulated: true,
    expiresAt: "2026-01-02T00:00:00Z",
    content: {
      title: "Review simulated order",
      subtitle: "Bound Goal Channel request",
      focus: "BUY 1 SYNTH @ 10 TEST",
      fields: [{ label: "Order", value: "Limit · GTC" }],
      warning: "Simulation only.",
    },
    kind: "confirmation",
    attentionKind: "authority",
    interactionMode: "confirm_reject",
    decisions: ["confirm", "reject"],
    confirmationDeliveryVerified: false,
  });
});

test("preparation is not card delivery, and cancellation never becomes execution completion", () => {
  const proposal: Record<string, any> = operationProposal("awaiting_confirmation");
  proposal.operation.delivery = {};
  let frame = compileOperationReviewFrame(proposal);
  assert.equal(frame?.kind === "confirmation" && frame.confirmationDeliveryVerified, false);
  proposal.operation.delivery = {provider: "lark", message_id: "message", chat_id: "chat", app_id: "app",
    binding_digest: "a".repeat(64), card_digest: "b".repeat(64), delivered_at: "2026-01-01T00:00:00Z"};
  frame = compileOperationReviewFrame(proposal);
  assert.equal(frame?.kind === "confirmation" && frame.confirmationDeliveryVerified, true);
  const original = structuredClone(proposal.operation);
  proposal.status = "cancelled";
  const plan = compileActionReviewPlan(proposal);
  assert.equal(plan.interaction, "inactive");
  assert.equal(plan.canApply, false);
  assert.equal(plan.operationFrame?.kind === "result" && plan.operationFrame.resultKind, "cancelled");
  assert.equal(plan.operationFrame?.kind === "result" && plan.operationFrame.resultDeliveryVerified, false);
  assert.deepEqual(proposal.operation, original, "Projection must not synthesize an outcome or claim");
  proposal.operation.lifecycle_state = "outcome_observed";
  proposal.operation.outcome = {outcome: "cancelled_before_confirmation", projection_verified: true};
  assert.equal(compileOperationReviewFrame(proposal)?.kind, "result");
  assert.equal(compileActionReviewPlan(proposal).interaction, "inactive");
});

test("expiry hides stale confirmations without changing claimed operations or canonical outcomes", () => {
  const proposal: Record<string, any> = operationProposal("awaiting_confirmation");
  proposal.operation.expires_at = "2026-01-02T00:00:00Z";
  proposal.operation.delivery = {provider: "lark", message_id: "message", chat_id: "chat", app_id: "app",
    binding_digest: "a".repeat(64), card_digest: "b".repeat(64), delivered_at: "2026-01-01T00:00:00Z"};
  const expiry = Date.parse(proposal.operation.expires_at);
  const original = structuredClone(proposal);
  assert.equal(compileOperationReviewFrame(proposal)?.kind, "confirmation",
    "Timeless Lark reconstruction must leave delayed callbacks to canonical confirmed_at validation");
  assert.equal(compileOperationReviewFrame(proposal, expiry)?.kind, "confirmation",
    "The canonical callback boundary allows the exact expiry instant");
  const expired = compileActionReviewPlan(proposal, expiry + 1);
  assert.equal(expired.interaction, "inactive");
  assert.equal(expired.canApply, false);
  assert.equal(expired.reason, "operation_confirmation_expired");
  assert.equal(expired.operationFrame?.kind, "inactive");
  assert.deepEqual(proposal, original, "A display guard must not cancel or execute the operation");
  for (const expiresAt of ["not-a-time", "2026-01-02T00:00:00"]) {
    assert.equal(compileActionReviewPlan({...proposal, operation: {...proposal.operation, expires_at: expiresAt}}, expiry).reason,
      "operation_expiry_unknown");
  }
  assert.equal(compileOperationReviewFrame({...proposal, status: "applying",
    operation: {...proposal.operation, lifecycle_state: "claimed"}}, expiry + 1)?.kind, "pending");
  assert.equal(compileOperationReviewFrame({...proposal, status: "cancelled"}, expiry + 1)?.kind, "result");
});

test("operation frame projects pending and verified result states", () => {
  const pending = compileOperationReviewFrame(operationProposal("claimed"));
  assert.equal(pending?.kind, "pending");
  assert.equal(pending?.interactionMode, "inform");

  const completed = compileActionReviewPlan(operationProposal("outcome_observed"));
  assert.equal(completed.interaction, "repair");
  assert.equal(completed.operationFrame?.kind, "result");
  if (completed.operationFrame?.kind !== "result") assert.fail("expected result frame");
  assert.equal(completed.operationFrame.resultKind, "simulation_completed");
  assert.equal(completed.operationFrame.resultDeliveryVerified, true);
  assert.equal(completed.operationFrame.summary, "Simulation completed.");
});

test("operation frame rejects identity drift and malformed projection fields", () => {
  const mismatched = operationProposal("awaiting_confirmation");
  mismatched.operation.operation_id = "another-operation";
  assert.equal(compileOperationReviewFrame(mismatched), undefined);

  const malformed = operationProposal("awaiting_confirmation");
  malformed.normalized_parameters.projection.fields = [{ label: "Order", value: "" }];
  assert.equal(compileOperationReviewFrame(malformed), undefined);
});

test("original-Agent pending, unknown and reconciled results share truthful surface semantics", () => {
  const proposal: Record<string, any> = operationProposal("claimed");
  proposal.status = "applying";
  proposal.normalized_parameters.executor = {kind: "agent_session"};
  proposal.normalized_parameters.projection.simulated = false;
  let frame = compileOperationReviewFrame(proposal);
  assert.equal(frame?.kind === "pending" && frame.executionState, "host_authentication_required");
  proposal.operation.agent_handoff = {consumption_id: "attempt-1"};
  frame = compileOperationReviewFrame(proposal);
  assert.equal(frame?.kind === "pending" && frame.executionState, "consumed_outcome_pending");
  proposal.status = "applied";
  proposal.receipt = {projection_verified: true};
  proposal.operation.lifecycle_state = "outcome_observed";
  proposal.operation.outcome = {outcome: "submission_unknown", simulation: false, summary: "Reconciliation required."};
  proposal.operation.result_delivery = {receipt_id: "delivery-1", outcome_stage: "initial"};
  const unknown = compileActionReviewPlan(proposal);
  assert.equal(unknown.interaction, "repair");
  assert.equal(unknown.canApply, false);
  assert.equal(unknown.operationFrame?.kind === "result" && unknown.operationFrame.resultKind, "unknown");
  proposal.operation.reconciliation = {outcome: "not_executed", simulation: false, summary: "No external effect verified."};
  assert.equal(compileActionReviewPlan(proposal).interaction, "repair", "Old unknown-result card is not final delivery");
  proposal.operation.result_delivery.outcome_stage = "reconciled";
  const reconciled = compileActionReviewPlan(proposal);
  assert.equal(reconciled.interaction, "completed");
  assert.equal(reconciled.canApply, false);
  assert.equal(reconciled.operationFrame?.kind === "result" && reconciled.operationFrame.resultKind, "not_executed");
  assert.equal(proposal.operation.outcome.outcome, "submission_unknown");
});

test("managed executor and source context use the same frame without turning approval into execution", () => {
  const proposal: Record<string, any> = operationProposal("claimed");
  proposal.status = "applying";
  proposal.normalized_parameters.agent_id = "worker";
  proposal.normalized_parameters.executor = {kind: "managed_turn", todo_id: "todo-worker", model: "test-model", reasoning_effort: "xhigh"};
  proposal.normalized_parameters.source_route = {host_surface: "codex-app", agent_id: "source-agent", thread_id: "private-source-thread"};
  let frame = compileOperationReviewFrame(proposal);
  assert.equal(frame?.kind === "pending" && frame.executionState, "managed_turn_pending");
  assert.equal(frame?.content.fields.at(-3)?.value, "Managed Turn / 受管回合 · test-model@xhigh");
  assert.equal(frame?.content.fields.at(-2)?.value, "worker · todo-worker");
  assert.equal(frame?.content.fields.at(-1)?.value, "codex-app · source-agent");
  assert.equal(JSON.stringify(frame).includes("private-source-thread"), false);
  assert.equal(compileActionReviewPlan(proposal).canApply, false);
  assert.equal(compileActionReviewPlan(proposal).reason, "operation_authorization_pending");
  proposal.operation.host_start = {schema_version: "loopx_operation_host_start_v0", host_turn_id: "native-turn"};
  frame = compileOperationReviewFrame(proposal);
  assert.equal(frame?.kind === "pending" && frame.executionState, "managed_turn_started");
  assert.equal(compileActionReviewPlan(proposal).canApply, false);
  assert.equal(compileActionReviewPlan(proposal).reason, "operation_authorization_pending");
  proposal.operation.agent_handoff = {consumption_id: "managed-attempt"};
  frame = compileOperationReviewFrame(proposal);
  assert.equal(frame?.kind === "pending" && frame.executionState, "consumed_outcome_pending");
  assert.equal(compileActionReviewPlan(proposal).reason, "operation_outcome_pending");
});

test("generic action review keeps state precedence and stale classification", () => {
  const proposal = {
    proposal_id: "preview-1",
    action_kind: "goal.lifecycle",
    normalized_parameters: { goal_id: "goal-1", operation: "stop" },
    context: { goal_id: "goal-1" },
    expected_state_fingerprint: "revision-1",
    permission_classification: "durable_write",
    validation_evidence: ["Validated"],
    available_transitions: ["apply", "cancel"],
    status: "preview_ready",
  };
  assert.equal(compileActionReviewPlan(proposal).interaction, "direct");
  assert.equal(
    compileActionReviewPlan({ ...proposal, stale: { actual: "revision-2" } }).interaction,
    "refresh",
  );
  assert.equal(isStaleActionFailure({ error_code: "action_conflict" }), true);
  assert.equal(isStaleActionFailure({ error: "unrelated conflict text" }), false);
});

function teamPlanProposal() {
  return {
    schema_version: "loopx_chat_action_proposal_v1",
    proposal_id: "proposal-team-plan-1",
    action_kind: "team.plan",
    context: { kind: "manager", goal_id: "goal-1" },
    expected_state_fingerprint: "registry-revision-1",
    permission_classification: "durable_write",
    validation_evidence: ["every ready lane names an Agent this Goal registers"],
    available_transitions: ["apply", "cancel"],
    status: "preview_ready",
    normalized_parameters: {
      goal_id: "goal-1",
      plan: {
        schema_version: "steward_team_plan_preview_v0",
        kind: "steward_team_plan_preview",
        goal_id: "goal-1",
        objective: "Ship the bounded intake",
        lanes: [
          {
            lane_id: "lane_intake",
            agent_id: "agent-backend",
            acceptance: "the Todo exists through the canonical owner",
            staffing: "ready",
            first_todo: {
              text: "Implement the bounded intake",
              priority: "P1",
              task_class: "advancement_task",
              action_kind: "implement",
            },
          },
          {
            lane_id: "lane_review",
            agent_id: "agent-reviewer",
            acceptance: "the review receipt is recorded",
            staffing: "gap",
            gap_reason_code: "agent_not_registered",
            declined_first_todo: {
              text: "Independently review the intake",
              priority: "P1",
              task_class: "advancement_task",
              action_kind: "validate",
            },
          },
        ],
        gaps: [{ lane_id: "lane_review", reason_code: "agent_not_registered" }],
        quota_envelope: { slots: 4, window: "1d" },
        stop_condition: "every lane reports a typed outcome or a stated gap",
        applies: false,
      },
    },
  };
}

test("a validated plan compiles into a confirmation card frame", () => {
  const plan = compileActionReviewPlan(teamPlanProposal());
  assert.equal(plan.interaction, "review");
  assert.equal(plan.canApply, true);
  const frame = compileReviewCardFrame(teamPlanProposal());
  if (!frame) assert.fail("expected review card frame");
  // The confirmation identity is the proposal and the state the apply
  // re-validates against, not an operation envelope this proposal does not have.
  assert.equal(frame.schemaVersion, "review_card_frame_v0");
  assert.equal(frame.kind, "confirmation");
  assert.equal(frame.interactionMode, "confirm_reject");
  assert.deepEqual([...frame.decisions], ["confirm", "reject"]);
  assert.equal(frame.proposalId, "proposal-team-plan-1");
  assert.equal(frame.stateFingerprint, "registry-revision-1");
  const fields = new Map(frame.fields.map((field) => [field.key, field.value]));
  assert.equal(fields.get("goal"), "goal-1");
  assert.equal(fields.get("objective"), "Ship the bounded intake");
  assert.equal(
    fields.get("lane_1"),
    "agent-backend · ready · P1 · implement · Implement the bounded intake"
    + " · acceptance: the Todo exists through the canonical owner",
  );
  // A gap lane keeps the work it did not staff, so a card can show what the
  // owner asked for next to the reason it cannot run.
  assert.equal(
    fields.get("lane_2"),
    "agent-reviewer · gap · agent_not_registered · Independently review the intake",
  );
  assert.equal(fields.get("lane_gaps"), "lane_review: agent_not_registered");
  assert.equal(fields.get("quota_envelope"), "slots: 4 · window: 1d");
  assert.equal(
    fields.get("stop_condition"),
    "every lane reports a typed outcome or a stated gap",
  );
  assert.equal(frame.focus, "goal-1 · 2 lanes");
  // Labels are keys, never sentences: this boundary stays language-neutral and
  // the surface owns the words it renders.
  for (const key of [
    frame.titleKey,
    frame.subtitleKey,
    frame.confirmLabelKey,
    frame.rejectLabelKey,
    frame.warningKey,
    ...frame.fields.map((field) => field.key),
  ]) {
    assert.match(key, /^[a-z][a-z0-9_]*$/);
  }
});

test("a failed retry-safe team plan restores only the original confirmed identity", () => {
  const original = teamPlanProposal();
  const failed = {
    ...original,
    status: "failed",
    failure: {error_code: "team_plan_commit_failed", retry_safe: true},
  };
  const plan = compileActionReviewPlan(failed);
  assert.equal(plan.interaction, "review");
  assert.equal(plan.reason, "team_plan_retry");
  assert.equal(plan.canApply, true);
  assert.equal(plan.retryOriginal, true);
  assert.equal(plan.proposalId, original.proposal_id);
  assert.equal(plan.sourceFingerprint, original.expected_state_fingerprint);

  for (const change of [
    {failure: {error_code: "team_plan_commit_failed", retry_safe: false}},
    {failure: {error_code: "team_plan_commit_failed"}},
    {failure: null},
    {status: "applying"},
    {status: "gated"},
    {status: "stale"},
    {status: "applied", receipt: {projection_verified: true}},
    {status: "cancelled"},
    {status: "rejected"},
    {proposal_id: ""},
    {expected_state_fingerprint: " "},
    {permission_classification: "protected"},
    {available_transitions: ["cancel"]},
    {normalized_parameters: {...original.normalized_parameters, goal_id: "another-goal"}},
    {normalized_parameters: {...original.normalized_parameters,
      plan: {...original.normalized_parameters.plan, goal_id: "another-goal"}}},
    {normalized_parameters: {...original.normalized_parameters,
      plan: {...original.normalized_parameters.plan, applies: true}}},
  ]) {
    const candidate = {...failed, ...change};
    const recovered = compileActionReviewPlan(candidate);
    assert.notEqual(recovered.retryOriginal, true, JSON.stringify(change));
    assert.notEqual(recovered.canApply, true, JSON.stringify(change));
  }
});

test("a plan card keeps the same identity through pending and result states", () => {
  const pending = compileReviewCardFrame({ ...teamPlanProposal(), status: "applying" });
  if (!pending) assert.fail("expected pending review card frame");
  assert.equal(pending.kind, "pending");
  assert.equal(pending.proposalId, "proposal-team-plan-1");
  assert.equal(pending.stateFingerprint, "registry-revision-1");

  const applied = compileReviewCardFrame({
    ...teamPlanProposal(),
    status: "applied",
    receipt: { outcome: "team_plan_applied", projection_verified: true },
  });
  if (!applied) assert.fail("expected applied review card frame");
  assert.equal(applied.kind, "result");
  if (applied.kind !== "result") assert.fail("expected result frame");
  assert.equal(applied.resultKind, "applied");
  assert.equal(applied.resultSummary, "team_plan_applied");
  assert.equal(applied.proposalId, pending.proposalId);
});

test("a plan card frame is refused for anything that was never an admitted preview", () => {
  const applied = teamPlanProposal();
  applied.normalized_parameters.plan.applies = true;
  assert.equal(compileReviewCardFrame(applied), undefined);

  const moved = teamPlanProposal();
  moved.status = "applied";
  assert.equal(compileReviewCardFrame(moved)?.kind, "result");

  const otherKind = { ...teamPlanProposal(), action_kind: "todo.create" };
  assert.equal(compileReviewCardFrame(otherKind), undefined);

  const withoutFingerprint = { ...teamPlanProposal() };
  delete (withoutFingerprint as { expected_state_fingerprint?: string }).expected_state_fingerprint;
  assert.equal(compileReviewCardFrame(withoutFingerprint), undefined);

  // An operation proposal keeps the operation frame path and gains no plan card.
  const operation = compileActionReviewPlan(operationProposal("awaiting_confirmation"));
  assert.equal(operation.operationFrame?.kind, "confirmation");
  assert.equal(operation.reviewCardFrame, undefined);
});
