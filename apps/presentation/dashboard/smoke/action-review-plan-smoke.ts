import {
  compileActionReviewPlan,
  isStaleActionFailure,
} from "../../../../loopx/control_plane/presentation/action_review_plan.js";
import { typedActionProposalSchema, type TypedActionProposal } from "../src/data/chat.js";

const proposal: TypedActionProposal = {
  schema_version: "loopx_chat_action_proposal_v1", proposal_id: "preview-1",
  action_kind: "goal.lifecycle", summary: "Stop sample Goal",
  normalized_parameters: { goal_id: "sample-goal", operation: "stop" },
  context: { kind: "goal_directory", goal_id: "sample-goal" },
  expected_state_fingerprint: "revision-1", permission_classification: "durable_write",
  validation_evidence: ["Canonical bounded shape validated"], available_transitions: ["apply", "cancel"],
  status: "preview_ready", receipt: null, stale: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
};
function check(condition: boolean, description: string) { if (!condition) throw new Error(description); }
const compile = (patch: Partial<TypedActionProposal> = {}) => compileActionReviewPlan({ ...proposal, ...patch });
check(compile().interaction === "direct", "A validated ready stop preserves the direct path");
for (const validation_evidence of [[null], [""], [" \t"], [{}], ["valid", null], ["valid", {}], ["valid", ""]]) {
  const raw = { ...proposal, validation_evidence };
  check(!typedActionProposalSchema.safeParse(raw).success, "Transport must reject every malformed evidence element");
  check(compileActionReviewPlan(raw as unknown as TypedActionProposal).interaction !== "direct", "Unparsed malformed evidence cannot become direct");
}
check(typedActionProposalSchema.parse({ ...proposal, validation_evidence: ["  Canonical validation  "] }).validation_evidence[0] === "  Canonical validation  ", "Validation preserves evidence text");
for (const operation of ["resume", "delete"]) {
  const result = compile({ normalized_parameters: { goal_id: "sample-goal", operation } });
  check(result.interaction === "review" && result.canApply, `${operation} requires review`);
}
// Mutate each fact independently. None may preserve direct presentation.
const unsafe: Array<Partial<TypedActionProposal>> = [
  { permission_classification: "protected" }, { permission_classification: "unknown" },
  { action_kind: "goal.update" }, { normalized_parameters: { goal_id: "sample-goal", operation: "unknown" } },
  { normalized_parameters: { operation: "stop" } }, { expected_state_fingerprint: "" },
  { proposal_id: "" }, { validation_evidence: [] }, { available_transitions: ["cancel"] },
  { gate: { kind: "authority_required" } }, { stale: { actual: "revision-2" } },
  { error: { message: "failed validation" } },
  { context: { goal_id: "another-goal" } },
];
for (const patch of unsafe) check(compile(patch).interaction !== "direct", `Unsafe fact must remove direct path: ${JSON.stringify(patch)}`);
for (const status of ["gated", "stale", "applying", "applied", "failed", "rejected", "deferred", "cancelled"] as const) {
  check(compile({ status }).interaction !== "direct", `${status} is never direct`);
}
check(compile({ status: "applied", receipt: { projection_verified: true } }).interaction === "completed", "Only verified applied state completes");
for (const receipt of [null, {}, { projection_verified: false }, { projection_verified: "true" }]) {
  const result = compile({ status: "applied", receipt });
  check(result.interaction === "repair" && !result.canApply, "Unverified receipt cannot complete or repeat apply");
}
check(compile({ status: "preview_ready", receipt: { projection_verified: true } }).interaction !== "completed", "Receipt alone cannot complete");
check(compile({ status: "applied", receipt: { projection_verified: true }, stale: {} }).interaction === "refresh", "Stale wins over nominal success");
check(compile({ status: "applied", receipt: { projection_verified: true }, gate: {} }).interaction === "gated", "Gate wins over nominal success");
for (const action_kind of ["goal.create", "goal.update", "todo.create", "todo.update", "agent.bind", "heartbeat.bind", "monitor.create", "monitor.update", "gate.resolve", "run.correct"] as const) {
  for (const status of ["preview_ready", "deferred"] as const) {
    const result = compile({ action_kind, status, validation_evidence: [] });
    check(result.interaction === "review" && result.canApply, `${action_kind} keeps existing reviewed behavior`);
    if (status === "deferred") check(compile({ action_kind, status, gate: { kind: "previous_gate" } }).canApply, "Generic deferred retries retain historical gate without losing the apply path");
  }
}
check(compile().sourceFingerprint === "revision-1" && compile().proposalId === "preview-1", "Preserve exact identity and fingerprint");
const frozen = JSON.stringify(proposal);
check(JSON.stringify(compile()) === JSON.stringify(compile()), "Deterministic compilation");
check(JSON.stringify(proposal) === frozen, "No input mutation");
console.log("PASS: action review parity, negative fact mutations, state precedence and verified readback");

check(isStaleActionFailure({ error_code: "action_stale" }), "Typed stale errors offer refresh");
check(isStaleActionFailure({ error_code: "action_conflict" }), "Typed conflicts offer refresh");
check(isStaleActionFailure({ proposal: { status: "stale" } }), "Typed stale proposal survives error wrapping");
check(!isStaleActionFailure({ error_code: "canonical_action_failed", error: "conflict with unrelated external service" }), "Error wording cannot classify source state");

for (const [action_kind, operation] of [["todo.update", "complete"], ["monitor.update", "stop"]] as const) {
  for (const status of ["applying", "failed"] as const) {
    const terminal = typedActionProposalSchema.parse({...proposal, action_kind, status,
      normalized_parameters: {goal_id: "sample-goal", todo_id: "todo_work", operation},
      canonical_update_basis: {schema_version: "loopx_chat_canonical_terminal_basis_v0",
        provider_revision: "revision-1", registry_sha256: "a".repeat(64), source_authority: "file_v0"},
      failure: {error_code: "canonical_update_projection_pending", message: "Display pending", retry_safe: true}});
    const plan = compileActionReviewPlan(terminal);
    check(plan.canApply && plan.retryOriginal === true, "Terminal recovery retries the original proposal");
    check(plan.reason === "canonical_update_projection_pending", "Pending display is distinct from failed business mutation");
    check(compileActionReviewPlan({...terminal, status: "stale"}).canApply === false, "A stale terminal preview must be regenerated");
    check(compileActionReviewPlan({...terminal, normalized_parameters: {...terminal.normalized_parameters, operation: "edit"}}).canApply === false,
      "A terminal review basis cannot enable retries of unrelated operations");
    check(compileActionReviewPlan({...terminal, status: "applied", receipt: {projection_verified: true}}).interaction === "completed",
      "Only current display readback completes terminal presentation");
  }
}

const operationProposal = typedActionProposalSchema.parse({
  ...proposal,
  proposal_id: "operation-1",
  action_kind: "operation.execute",
  permission_classification: "protected",
  status: "gated",
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
    lifecycle_state: "awaiting_confirmation",
    operation_id: "operation-1",
    confirmation_digest: "confirmation-1",
    payload_digest: "payload-1",
    projection_digest: "projection-1",
    expires_at: "2026-01-02T00:00:00Z",
    delivery: null,
    confirmation: null,
    claim: null,
    outcome: null,
    result_delivery: null,
  },
});
const operationPlan = compileActionReviewPlan(operationProposal);
check(operationPlan.interaction === "gated", "Operation execution keeps its authenticated human gate");
check(operationPlan.operationFrame?.kind === "confirmation", "Dashboard consumes the shared confirmation frame");
check(operationPlan.operationFrame?.interactionMode === "confirm_reject", "The shared frame preserves confirm/reject interaction");
check(operationPlan.operationFrame?.content.fields[0]?.value === "Limit · GTC", "The shared frame preserves bounded projection fields");

const agentPending = typedActionProposalSchema.parse({...operationProposal, status: "applying",
  normalized_parameters: {...operationProposal.normalized_parameters, executor: {kind: "agent_session"}},
  operation: {...operationProposal.operation, lifecycle_state: "claimed", agent_handoff: {consumption_id: "attempt-1"}}});
const agentPendingFrame = compileActionReviewPlan(agentPending).operationFrame;
check(agentPendingFrame?.kind === "pending" && agentPendingFrame.executionState === "consumed_outcome_pending",
  "Transport retains the original consumption; it does not imply an external result");
const unauthenticatedFrame = compileActionReviewPlan({...agentPending,
  operation: {...agentPending.operation, agent_handoff: null}}).operationFrame;
check(unauthenticatedFrame?.kind === "pending" && unauthenticatedFrame.executionState === "host_authentication_required",
  "Human confirmation alone cannot qualify original-host authentication");
const unknownAgentResult = typedActionProposalSchema.parse({...agentPending, status: "applied",
  receipt: {projection_verified: true}, operation: {...agentPending.operation, lifecycle_state: "outcome_observed",
    outcome: {outcome: "submission_unknown", simulation: false}, result_delivery: {outcome_stage: "initial"}}});
const managedPending = typedActionProposalSchema.parse({...agentPending,
  normalized_parameters: {...agentPending.normalized_parameters, agent_id: "managed-worker", executor: {
    kind: "managed_turn", todo_id: "todo-managed", session_id: "owned-thread", profile_digest: "a".repeat(64),
    model: "test-model", reasoning_effort: "xhigh", revision: "managed-turn-handoff-v0"}},
  operation: {...agentPending.operation, agent_handoff: null}});
const managedFrame = compileActionReviewPlan(managedPending).operationFrame;
check(managedFrame?.kind === "pending" && managedFrame.executionState === "managed_turn_pending",
  "Managed confirmation waits for its exact admitted executor rather than Desktop authentication");
check(managedFrame?.content.fields.some(field => field.value.includes("test-model@xhigh")) === true,
  "The shared managed profile survives the frontend schema transport");
check(compileActionReviewPlan(managedPending).canApply === false, "Managed approval exposes no local execute control");
check(compileActionReviewPlan(unknownAgentResult).interaction === "repair", "Delivered unknown submission is not completion");
const reconciledAgentResult = typedActionProposalSchema.parse({...unknownAgentResult,
  operation: {...unknownAgentResult.operation, reconciliation: {outcome: "not_executed", simulation: false}}});
check(compileActionReviewPlan(reconciledAgentResult).interaction === "repair", "Old card delivery cannot certify a new reconciliation");
check(compileActionReviewPlan({...reconciledAgentResult, operation: {...reconciledAgentResult.operation,
  result_delivery: {outcome_stage: "reconciled"}}}).interaction === "completed", "Only reconciled card readback completes presentation");
console.log("PASS: original-Agent handoff and append-only reconciliation survive the frontend transport");
