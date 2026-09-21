import {evaluateTodoPriority} from "./todos/priority.ts";
import {evaluateUserCompletion} from "./todos/user_completion.ts";
import {projectTodoSuccession, projectTodoClosure} from "./todos/succession.ts";
import {projectTodoSummaryLanes, projectLegacyTodoWorkCounts} from "./todos/summary_lanes.ts";
import {recordDelegationAdoption, delegationInventoryItem, delegationInventoryQuery, delegationPreflight, delegationTurnPlanDecision, selectDelegationBinding, transitionDelegationObservation} from "./collaboration/delegation.ts";
import {planChatMode} from "./collaboration/chat_mode.ts";
import {resolveConversationScope} from "./collaboration/conversation_scope.ts";
import {previewTeamPlan, planTeamTransaction, teamTransactionIdentity} from "./work_items/team_plan.ts";
import {commitLocalTeamPlan} from "./work_items/team_plan_authority.ts";
import {inspectLocalGoalAcceptance, commitLocalGoalAcceptance,
  commitLocalGoalAcceptanceVerification} from "./goals/acceptance_authority.ts";
import {planHandoffMode} from "./coordination/handoff_mode_policy.ts";
import {setLocalHandoffMode} from "./coordination/handoff_mode_runtime.ts";
import {projectOwnershipObservation} from "./coordination/ownership_observation.ts";
import {observeLocalCoordinationOwnership} from "./coordination/local_authority_runtime.ts";
import {evaluateTaskLeaseOwnerEligibility} from "./work_items/task_lease_eligibility.ts";
import { evaluateSubagentContext, describeSubagentContext } from "./subagent_context.ts";
import {
  effectIdsMatch,
  effectProgramFromOrderedSteps,
  interpretQuotaShouldRunPacket,
  interpretTurnResultPacket,
  settlementBindGate,
  settlementBindReduce,
  settlementReceipt,
  settlementIdentity,
  settlementIdentityPayload,
  settlementPlanPayload,
  settlementResultPayload,
  SETTLEMENT_BINDING_KINDS,
  SETTLEMENT_FAILURE_KINDS,
  SETTLEMENT_STEP_KINDS,
  type JsonObject,
  type SettlementFailure,
  type SettlementFailureKind,
  type SettlementIdentityInput,
  type SettlementPlan,
  type SettlementReceipt,
  type SettlementResult,
  type SettlementStep,
  type SettlementStepKind,
} from "./effect_program.ts";
import {
  receiptBoundMonitorPhase,
  receiptBoundReplayPhase,
  receiptBoundTerminalPhase,
} from "./quota/settlement_phase.ts";
import { EffectRuntimeRequestError } from "./effect_runtime_errors.ts";
import {
  optionalNonEmptyString as optionalString,
  requireJsonObject as requiredObject,
  requireNonEmptyString as requiredString,
  requireStringArray as stringArray,
  requireStringLiteral,
} from "./runtime_decode.ts";
import {
  governedCapabilitySettlementStatus,
  validateGovernedCapabilityAdmission,
  validateGovernedCapabilityResult,
  validateGovernedCapabilitySettlementCallback,
} from "./governed_capability.ts";
import { evaluateDeliveryWorkspaceCausality } from "./quota/settlement_workspace_causality.ts";
import { evaluateQuotaSpendCommit } from "./quota/spend_commit.ts";
import { evaluateQuotaVoidCommit } from "./quota/void_commit.ts";
import { readQuotaSettlement } from "./quota/settlement_readback.ts";
import {
  preflightPriorHostTurnCloseout,
  reduceUnsettledHostTurnRecovery,
} from "./quota/unsettled_host_turn_recovery.ts";
import { evaluateTurnEnvelope } from "./quota/turn_envelope.ts";
import { evaluateQuotaMonitorPollCommit } from "./quota/monitor_poll_commit.ts";
import { planMonitorSuccessor, selectMonitorTodoRequest } from "./scheduler/monitor_successor.ts";
import { evaluateDeliveryWorkspace } from "./agents/delivery_workspace.ts";
import {
  interpretTurnJournal,
  type TurnJournalInspectionRequest,
} from "./turn_driver/turn_journal.ts";
import { commitTurnJournal } from "./turn_driver/turn_journal_effects.ts";
import {
  evaluateTodoCompletionFence,
} from "./todos/completion_fence.ts";
import {
  normalizeTodoCompletionValue,
  requireTodoCompletionMetadataValue,
  selectTodoCompletionContinuation,
} from "./todos/completion_state.ts";
import { reduceTodoCompletionTransaction } from "./todos/completion_transaction.ts";
import { transitionTodoNextAction } from "./todos/next_action.ts";
import { planTodoFieldUpdate } from "./todos/field_update.ts";
import { planPublicTodoUpdate } from "./todos/public_update.ts";
import { planMonitorMetadata } from "./todos/monitor_metadata.ts";
import { planTodoAuthoringScope } from "./todos/authoring_scope.ts";
import {
  evaluateTodoResumeConditions,
  normalizeTodoResumeWhen,
  planTodoExternalWaitTransition,
} from "./todos/resume_condition.ts";
import { evaluateSchedulerStateTransition } from "./scheduler/state_transition_rules.ts";
import { projectTodoResumePlanning } from "./todos/resume_planning.ts";
import { projectTodoQuotaPlanning } from "./todos/quota_selection.ts";
import {
  evaluateSchedulerStateOperation,
  loadSchedulerState,
  writeSchedulerState,
} from "./scheduler/state_store.ts";
import { buildVisionCheckpoint } from "./goals/vision_checkpoint.ts";
import { projectVisionWaitCoverage } from "./goals/vision_wait_coverage.ts";
import { admitGoalAmendmentProposal } from "./goals/goal_amendment_proposal.ts";
import { projectSharedGoalAlignment } from "./goals/shared_goal_alignment.ts";
import { projectGoalOperatorActions } from "./goals/operator_actions.ts";
import {
  evaluateDeliveryRoute,
} from "./turn_driver/delivery_continuity.ts";
import { reduceTurnSettlementTransaction } from "./turn_driver/settlement.ts";
import { evaluateHostTodoCompletion } from "./turn_driver/host_todo_completion.ts";
import { projectReplanSemantics } from "./work_items/replan_semantics.ts";
import {
  projectReplanSettlementContract,
  projectTodoLifecycleSettlementReentry,
} from "./work_items/replan_settlement.ts";
import {
  projectQuotaActionPortfolio,
  qualifyActionSelection,
  reconcileRetainedActionSelection,
} from "./work_items/action_portfolio.ts";
import { projectQuotaPlanningHorizon } from "./work_items/planning_horizon.ts";
import { projectTaskGraphTopology } from "./work_items/task_graph.ts";
import { projectDeliveryHistory, projectDeliveryResponse } from "./work_items/delivery_history.ts";
import { validateDeliveryClaim } from "./work_items/delivery_outcome.ts";
import {
  evaluateTaskLeaseAcquireDecision,
  evaluateTaskLeaseWriteScopesOverlap,
} from "./work_items/task_lease_acquire_decision.ts";
import {executeTaskLeaseAcquire} from "./work_items/task_lease_acquire.ts";
import { executeTaskLeaseLifecycle } from "./work_items/task_lease_lifecycle.ts";
import {
  commitLocalAuthorityShadowEntry,
  readLocalAuthorityShadow,
  recordLocalAuthorityShadow,
} from "./coordination/local_authority_shadow.ts";
import { evaluateTaskLeaseLifecycleDecision } from "./work_items/task_lease_lifecycle_decision.ts";
import {
  bootstrapCoordinationRuntimeShadow,
  commitCoordinationRuntimeShadow,
  inspectCoordinationRuntimeShadow,
  qualifyCoordinationRuntimeShadow,
  readCoordinationRuntimeShadowTodoCandidate,
  rollbackCoordinationRuntimeShadow,
} from "./coordination/runtime_shadow.ts";
import {
  acknowledgeLocalCoordinationTodoArchive,
  archiveLocalCoordinationTodos,
  claimLocalCoordinationTodo,
  continueLocalTodo,
  createLocalCoordinationTodo,
  updateLocalCoordinationTodo,
  pollLocalCoordinationMonitor,
  listLocalCoordinationTodos,
  promoteLocalCoordinationAuthority,
  reviewLocalCoordinationAuthorityPromotion,
  readLocalCoordinationTodo,
  terminalLifecycleLocalCoordinationTodo,
} from "./coordination/local_authority_runtime.ts";
import { evaluateCoordinationTodoClaimDecision } from "./coordination/todo_claim.ts";
import {
  evaluateCoordinationTodoTerminalDecision,
  evaluateCoordinationTodoMutationDecision,
  evaluateTodoOwnershipGate,
} from "./coordination/todo_lifecycle_decision.ts";
import { evaluateCoordinationTodoArchiveSelection } from "./coordination/todo_archive_selection.ts";
import {evaluateStandingDecisionProjection} from "./todos/standing_decision.ts";
import {evaluateDecisionScope} from "./todos/decision_scope.ts";
import {agentCapabilityMemory} from "./agents/capability_memory.ts";
import {evaluateCapabilityGate} from "./agents/capability_gate.ts";
import {captureArchivedTodoDependencies} from "./todos/archive_capture.ts";
import {projectAdvancementFrontier, evaluateLongTodoChain} from "./todos/frontier_revision.ts";
import { evaluateCoordinationTodoSuccessorDerivation } from "./coordination/todo_successor_derivation.ts";
import {
  checkLegacyCoordinationWriteAllowed,
  engageLegacyCoordinationWriterFence,
} from "./coordination/legacy_writer_fence.ts";
import {
  projectTodoPlanningInventory,
  projectTodoPlanningInventoryDetail,
} from "./work_items/planning_inventory.ts";
import { resolveRefreshRecommendation } from "./work_items/refresh_recommendation.ts";
import {
  validateInteractionProjectionHookInvocation,
  validateInteractionProjectionHookRegistration,
  validateTurnStartHookInvocation,
  validateTurnStartHookRegistration,
} from "./capability_hooks.ts";
import { evaluatePostWritebackHookTransaction } from "./post_writeback_hook_transaction.ts";
import { compileActionReviewPlan } from "./presentation/action_review_plan.ts";
import {
  classifyManagerReturnVerification,
  normalizeManagerReturnDeliveryAttempt,
} from "./collaboration/return_delivery.ts";

import { normalizeCollaborationRequest } from "./collaboration/semantic_request.ts";

type EffectRuntimeHandler = (params: JsonObject) => unknown | Promise<unknown>;

export interface EffectRuntimeHandlerContext {
  fingerprint: string;
  requestShutdown: () => void;
}

function asObject(value: unknown): JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as JsonObject)
    : {};
}

function settlementStepKind(value: unknown, label: string): SettlementStepKind {
  return requireStringLiteral(
    value,
    SETTLEMENT_STEP_KINDS,
    label,
    `${label} has an unsupported settlement step kind`,
  );
}

function settlementFailureKind(
  value: unknown,
  label: string,
): SettlementFailureKind {
  return requireStringLiteral(
    value,
    SETTLEMENT_FAILURE_KINDS,
    label,
    `${label} has an unsupported settlement failure kind`,
  );
}

function settlementIdentityInput(
  value: unknown,
  label = "identity",
): SettlementIdentityInput {
  const input = requiredObject(value, label);
  return {
    goal_id: requiredString(input.goal_id, `${label}.goal_id`),
    agent_id: requiredString(input.agent_id, `${label}.agent_id`),
    todo_id: optionalString(input.todo_id, `${label}.todo_id`),
    turn_instance_id: requiredString(
      input.turn_instance_id,
      `${label}.turn_instance_id`,
    ),
    replan_obligation_id: optionalString(
      input.replan_obligation_id,
      `${label}.replan_obligation_id`,
    ),
  };
}

function settlementReceiptInput(
  value: unknown,
  label: string,
): SettlementReceipt {
  const receipt = requiredObject(value, label);
  return {
    step_kind: settlementStepKind(receipt.step_kind, `${label}.step_kind`),
    status: requiredString(receipt.status, `${label}.status`),
    effect_id: requiredString(receipt.effect_id, `${label}.effect_id`),
    ...(optionalString(receipt.source_ref, `${label}.source_ref`)
      ? { source_ref: optionalString(receipt.source_ref, `${label}.source_ref`)! }
      : {}),
  };
}

function settlementFailureInput(value: unknown, label: string): SettlementFailure {
  const failure = requiredObject(value, label);
  const details = failure.details === undefined
    ? undefined
    : requiredObject(failure.details, `${label}.details`);
  return {
    kind: settlementFailureKind(failure.kind, `${label}.kind`),
    step_kind: settlementStepKind(failure.step_kind, `${label}.step_kind`),
    reason: requiredString(failure.reason, `${label}.reason`),
    ...(details ? { details } : {}),
  };
}

function settlementResultInput(value: unknown, label: string): SettlementResult {
  const result = requiredObject(value, label);
  if (!Array.isArray(result.receipts)) {
    throw new EffectRuntimeRequestError(`${label}.receipts must be an array`);
  }
  const receipts = result.receipts.map((receipt, index) =>
    settlementReceiptInput(receipt, `${label}.receipts[${index}]`)
  );
  if (result.failure === null) {
    return { value: result.value, receipts, failure: null };
  }
  if (result.value !== null) {
    throw new EffectRuntimeRequestError(`${label} cannot carry both a value and a failure`);
  }
  return {
    value: null,
    receipts,
    failure: settlementFailureInput(result.failure, `${label}.failure`),
  };
}

function settlementStepInput(value: unknown, label: string): SettlementStep {
  const step = requiredObject(value, label);
  if (step.conditional !== undefined && step.conditional !== true) {
    throw new EffectRuntimeRequestError(`${label}.conditional must be true when present`);
  }
  return {
    kind: settlementStepKind(step.kind, `${label}.kind`),
    owner: requiredString(step.owner, `${label}.owner`),
    precondition: requiredString(step.precondition, `${label}.precondition`),
    idempotency_key_ref: requiredString(
      step.idempotency_key_ref,
      `${label}.idempotency_key_ref`,
    ),
    expected_receipt: requiredString(
      step.expected_receipt,
      `${label}.expected_receipt`,
    ),
    ...(optionalString(step.command_template, `${label}.command_template`)
      ? {
          command_template: optionalString(
            step.command_template,
            `${label}.command_template`,
          )!,
        }
      : {}),
    ...(step.conditional === true ? { conditional: true } : {}),
  };
}

function settlementPlanInput(value: unknown, label: string): SettlementPlan {
  const plan = requiredObject(value, label);
  if (!Array.isArray(plan.steps)) {
    throw new EffectRuntimeRequestError(`${label}.steps must be an array`);
  }
  return {
    identity: settlementIdentity(settlementIdentityInput(plan.identity, `${label}.identity`)),
    steps: plan.steps.map((step, index) =>
      settlementStepInput(step, `${label}.steps[${index}]`)
    ),
  };
}

function turnJournalInspectionRequest(
  value: unknown,
): TurnJournalInspectionRequest {
  const request = requiredObject(value, "turn_journal.inspect params");
  if (
    request.schema_version !==
      "loopx_turn_journal_interpretation_request_v0"
  ) {
    throw new EffectRuntimeRequestError("Turn-journal interpretation request schema mismatch");
  }
  let sessionRecoveryCheck: TurnJournalInspectionRequest["session_recovery_check"] = null;
  if (request.session_recovery_check !== null && request.session_recovery_check !== undefined) {
    const check = requiredObject(
      request.session_recovery_check,
      "turn_journal.inspect session_recovery_check",
    );
    const kind = requiredString(check.kind, "turn_journal.inspect session_recovery_check.kind");
    const outcome = requiredString(
      check.outcome,
      "turn_journal.inspect session_recovery_check.outcome",
    );
    if (kind !== "host_session_binding" || !["passed", "failed"].includes(outcome)) {
      throw new EffectRuntimeRequestError(
        "Turn-journal session recovery check is unsupported",
      );
    }
    sessionRecoveryCheck = {
      kind,
      outcome: outcome as "passed" | "failed",
      ...(typeof check.reason === "string" ? { reason: check.reason } : {}),
    };
  }
  return {
    schema_version: request.schema_version,
    journal: requiredObject(request.journal, "turn_journal.inspect journal"),
    goal_id: requiredString(request.goal_id, "turn_journal.inspect goal_id"),
    agent_id: requiredString(request.agent_id, "turn_journal.inspect agent_id"),
    turn_key: requiredString(request.turn_key, "turn_journal.inspect turn_key"),
    retry_failed: request.retry_failed === true,
    session_recovery_check: sessionRecoveryCheck,
  };
}

export function createEffectRuntimeHandlers(
  context: EffectRuntimeHandlerContext,
): ReadonlyMap<string, EffectRuntimeHandler> {
  return new Map<string, EffectRuntimeHandler>([
    [
      "runtime.ping",
      () => ({ ready: true, pid: process.pid, fingerprint: context.fingerprint }),
    ],
    [
      "runtime.shutdown",
      () => {
        context.requestShutdown();
        return { stopped: true };
      },
    ],
    [
      "turn_journal.inspect",
      (params) => interpretTurnJournal(turnJournalInspectionRequest(params)),
    ],
    ["turn_journal.write", commitTurnJournal],
    ["todo.completion_fence.evaluate", evaluateTodoCompletionFence],
    ["todo.completion_state.normalize", normalizeTodoCompletionValue],
    ["todo.completion_state.require_metadata", requireTodoCompletionMetadataValue],
    ["todo.completion_state.continuation_for_write", selectTodoCompletionContinuation],
    ["todo.field_update.plan", planTodoFieldUpdate],
    ["todo.priority.plan", evaluateTodoPriority],
    ["todo.public_update.plan", planPublicTodoUpdate],
    ["todo.standing_decision.project", evaluateStandingDecisionProjection],
    ["todo.summary_lanes.project", projectTodoSummaryLanes],
    ["todo.succession.project", projectTodoSuccession],
    ["todo.succession.closure", projectTodoClosure],
    ["todo.work_counts.project", projectLegacyTodoWorkCounts],
    ["todo.decision_scope.evaluate", evaluateDecisionScope],
    ["todo.user_completion.plan", evaluateUserCompletion],
    ["agent.capability_gate.evaluate", evaluateCapabilityGate],
    ["agent.capability_memory", agentCapabilityMemory],
    ["todo.archive.capture_dependencies", captureArchivedTodoDependencies],
    ["todo.monitor_metadata.plan", planMonitorMetadata],
    ["todo.authoring_scope.plan", planTodoAuthoringScope],
    [
      "todo.claim.decide",
      (params) => evaluateCoordinationTodoClaimDecision(
        requiredObject(params.todo, "todo"),
        {
          goal_id: requiredString(params.goal_id, "goal_id"),
          todo_id: requiredString(params.todo_id, "todo_id"),
          claimed_by: requiredString(params.claimed_by, "claimed_by"),
          actor_agent_id: params.actor_agent_id === null
            ? null : requiredString(params.actor_agent_id, "actor_agent_id"),
          expected_role: params.expected_role === null
            ? null : requiredString(params.expected_role, "expected_role"),
          registered_agents: stringArray(params.registered_agents, "registered_agents"),
          operation_id: "decision-only",
          dry_run: true,
          now: new Date(0),
        },
      ),
    ],
    ["todo.terminal.decide", evaluateCoordinationTodoTerminalDecision],
    ["todo.mutation.decide", evaluateCoordinationTodoMutationDecision],
    ["todo.ownership_gate.decide", evaluateTodoOwnershipGate],
    ["todo.archive.select", evaluateCoordinationTodoArchiveSelection],
    ["todo.successor.derive", evaluateCoordinationTodoSuccessorDerivation],
    ["todo.completion.reduce", reduceTodoCompletionTransaction],
    ["todo.next_action.transition", transitionTodoNextAction],
    ["todo.resume_condition.normalize", normalizeTodoResumeWhen],
    ["todo.resume_condition.evaluate", evaluateTodoResumeConditions],
    ["todo.resume_planning.project", projectTodoResumePlanning],
    ["todo.quota_planning.project", projectTodoQuotaPlanning],
    ["todo.frontier_revision.project", projectAdvancementFrontier],
    ["goal.long_todo_chain.evaluate", evaluateLongTodoChain],
    ["todo.external_wait.plan", planTodoExternalWaitTransition],
    ["scheduler.state_transition.evaluate", evaluateSchedulerStateTransition],
    ["scheduler.state.evaluate", evaluateSchedulerStateOperation],
    ["scheduler.state.load", loadSchedulerState],
    ["scheduler.state.write", writeSchedulerState],
    ["turn.delivery_route.evaluate", evaluateDeliveryRoute],
    ["work_item.action_portfolio.project", projectQuotaActionPortfolio],
    ["work_item.action_selection.qualify", qualifyActionSelection],
    [
      "work_item.action_selection.reconcile_retained",
      reconcileRetainedActionSelection,
    ],
    ["work_item.planning_horizon.project", projectQuotaPlanningHorizon],
    ["work_item.task_graph.topology", projectTaskGraphTopology],
    ["work_item.planning_inventory.project", projectTodoPlanningInventory],
    ["work_item.planning_inventory.detail", projectTodoPlanningInventoryDetail],
    ["work_item.refresh_recommendation.resolve", resolveRefreshRecommendation],
    ["work_item.delivery_history.project", projectDeliveryHistory],
    ["work_item.delivery_response.project", projectDeliveryResponse],
    ["work_item.delivery_claim.validate", validateDeliveryClaim],
    ["goal.vision_checkpoint.evaluate", buildVisionCheckpoint],
    ["goal.vision_wait.coverage", projectVisionWaitCoverage],
    ["goal.shared_goal_alignment.project", projectSharedGoalAlignment],
    ["goal.operator_actions.project", projectGoalOperatorActions],
    ["goal.amendment_proposal.admit", admitGoalAmendmentProposal],
    ["goal.acceptance.inspect", inspectLocalGoalAcceptance],
    ["goal.acceptance.configure", commitLocalGoalAcceptance],
    ["goal.acceptance.verify.commit", commitLocalGoalAcceptanceVerification],
    ["agent.delivery_workspace.evaluate", evaluateDeliveryWorkspace],
    [
      "quota.delivery_workspace_causality.evaluate",
      evaluateDeliveryWorkspaceCausality,
    ],
    ["quota.spend.commit", evaluateQuotaSpendCommit],
    ["quota.void.commit", evaluateQuotaVoidCommit],
    ["quota.settlement.read", readQuotaSettlement],
    [
      "quota.prior_host_turn_closeout.preflight",
      preflightPriorHostTurnCloseout,
    ],
    [
      "quota.unsettled_host_turn_recovery.reduce",
      reduceUnsettledHostTurnRecovery,
    ],
    ["quota.turn_envelope.evaluate", evaluateTurnEnvelope],
    ["task_lease.owner_eligibility", evaluateTaskLeaseOwnerEligibility],
    ["task_lease.acquire.decide", evaluateTaskLeaseAcquireDecision],
    ["task_lease.acquire.native", executeTaskLeaseAcquire],
    ["task_lease.lifecycle.decide", evaluateTaskLeaseLifecycleDecision],
    ["task_lease.lifecycle.native", executeTaskLeaseLifecycle],
    ["coordination.runtime_shadow.bootstrap", bootstrapCoordinationRuntimeShadow],
    ["coordination.runtime_shadow.commit", commitCoordinationRuntimeShadow],
    ["coordination.runtime_shadow.inspect", inspectCoordinationRuntimeShadow],
    ["coordination.runtime_shadow.qualify", qualifyCoordinationRuntimeShadow],
    [
      "coordination.runtime_shadow.todo_read_candidate",
      readCoordinationRuntimeShadowTodoCandidate,
    ],
    ["coordination.runtime_shadow.rollback", rollbackCoordinationRuntimeShadow],
    ["coordination.local_authority.promote", promoteLocalCoordinationAuthority],
    ["coordination.local_authority.promotion_review", reviewLocalCoordinationAuthorityPromotion],
    ["coordination.local_authority.todo_continuation", continueLocalTodo],
    ["coordination.local_authority.todo_claim", claimLocalCoordinationTodo],
    ["coordination.local_authority.todo_create", createLocalCoordinationTodo],
    ["work_items.team_plan.preview", previewTeamPlan],
    ["work_items.team_plan.plan", planTeamTransaction],
    ["work_items.team_plan.identity", value => teamTransactionIdentity(requiredObject(value, "team plan request"))],
    ["work_items.team_plan.commit", commitLocalTeamPlan],
    ["coordination.local_authority.todo_update", updateLocalCoordinationTodo],
    ["coordination.local_authority.monitor_poll", pollLocalCoordinationMonitor],
    ["coordination.handoff_mode.plan", planHandoffMode],
    ["coordination.local_authority.handoff_mode_set", setLocalHandoffMode],
    ["coordination.local_authority.todo_terminal", terminalLifecycleLocalCoordinationTodo],
    ["coordination.local_authority.todo_archive", archiveLocalCoordinationTodos],
    ["coordination.local_authority.todo_archive_ack", acknowledgeLocalCoordinationTodoArchive],
    ["coordination.local_authority.todo_read", readLocalCoordinationTodo],
    ["coordination.ownership_observation", projectOwnershipObservation],
    ["coordination.local_authority.ownership_observation", observeLocalCoordinationOwnership],
    ["coordination.local_authority.todo_list", listLocalCoordinationTodos],
    [
      "coordination.local_authority.legacy_writer_fence.engage",
      engageLegacyCoordinationWriterFence,
    ],
    [
      "coordination.local_authority.legacy_write_check",
      checkLegacyCoordinationWriteAllowed,
    ],
    ["task_lease.write_scopes.overlap", evaluateTaskLeaseWriteScopesOverlap],
    ["quota.monitor_poll.commit", evaluateQuotaMonitorPollCommit],
    ["presentation.action_review_plan.compile", (params) =>
      compileActionReviewPlan(params.proposal)],
    ["scheduler.monitor_successor.plan", planMonitorSuccessor],
    ["scheduler.monitor_target.select", selectMonitorTodoRequest],
    ["coordination.local_authority_shadow.record", recordLocalAuthorityShadow],
    ["coordination.runtime_shadow.commit_entry", commitLocalAuthorityShadowEntry],
    ["coordination.runtime_shadow.outbox_read", readLocalAuthorityShadow],
    [
      "effect.program_from_ordered_steps",
      (params) => effectProgramFromOrderedSteps(
        Array.isArray(params.ordered_steps) ? params.ordered_steps : [],
        typeof params.execution_mode === "string" ? params.execution_mode : null,
      ),
    ],
    [
      "effect.interpret_quota",
      (params) => interpretQuotaShouldRunPacket(
        asObject(params.packet),
        asObject(params.identity),
      ),
    ],
    [
      "effect.interpret_turn_result",
      (params) => interpretTurnResultPacket(
        asObject(params.packet),
        asObject(params.identity),
      ),
    ],
    [
      "governed_capability.validate_admission",
      (params) => validateGovernedCapabilityAdmission({
        admission: params.admission,
        todo_id: requiredString(params.todo_id, "todo_id"),
        todo_contract: params.todo_contract,
      }),
    ],
    [
      "governed_capability.validate_result",
      (params) => validateGovernedCapabilityResult({
        value: params.value,
        invocation_id: requiredString(params.invocation_id, "invocation_id"),
        effect_id: requiredString(params.effect_id, "effect_id"),
        result_schema: requiredString(params.result_schema, "result_schema"),
        effect_class: requiredString(params.effect_class, "effect_class"),
        transition_contract: params.transition_contract,
      }),
    ],
    [
      "governed_capability.validate_settlement_callback",
      (params) => validateGovernedCapabilitySettlementCallback({
        payload: params.payload,
        effect_id: requiredString(params.effect_id, "effect_id"),
        effect_receipt_digest: requiredString(
          params.effect_receipt_digest,
          "effect_receipt_digest",
        ),
        require_receipt_digest: params.require_receipt_digest === true,
      }),
    ],
    [
      "governed_capability.settlement_status",
      (params) => governedCapabilitySettlementStatus(params.failure),
    ],
    [
      "capability_hook.agent_context.describe",
      () => describeSubagentContext(),
    ],
    [
      "capability_hook.agent_context.project",
      (params) => evaluateSubagentContext(params),
    ],
    [
      "capability_hook.interaction_projection.validate_registration",
      (params) => validateInteractionProjectionHookRegistration(
        params.registration,
      ),
    ],
    [
      "capability_hook.interaction_projection.validate",
      (params) => validateInteractionProjectionHookInvocation({
        registration: params.registration,
        result: params.result,
      }),
    ],
    [
      "capability_hook.turn_start.validate_registration",
      (params) => validateTurnStartHookRegistration(params.registration),
    ],
    [
      "capability_hook.turn_start.validate",
      (params) => validateTurnStartHookInvocation({
        registration: params.registration,
        result: params.result,
      }),
    ],
    [
      "capability_hook.post_writeback.transaction",
      evaluatePostWritebackHookTransaction,
    ],
    ["collaboration.delegation.binding", selectDelegationBinding],
    ["collaboration.delegation.preflight", delegationPreflight],
    ["collaboration.delegation.turn_plan", delegationTurnPlanDecision],
    ["collaboration.delegation.inventory_query", delegationInventoryQuery],
    ["collaboration.delegation.inventory_item", delegationInventoryItem],
    ["collaboration.chat_mode", planChatMode],
    ["collaboration.conversation.scope", resolveConversationScope],
    ["collaboration.delegation.observe", transitionDelegationObservation],
    ["collaboration.delegation.adoption", recordDelegationAdoption],
    [
      "collaboration.request.normalize",
      (params) => normalizeCollaborationRequest(params.request),
    ],
    [
      "manager.return_delivery.normalize_attempt",
      (params) => normalizeManagerReturnDeliveryAttempt(params.attempt),
    ],
    [
      "manager.return_delivery.classify_verification",
      (params) => classifyManagerReturnVerification(params.outcome),
    ],
    [
      "settlement.identity",
      (params) => settlementIdentity(settlementIdentityInput(params)),
    ],
    [
      "settlement.identity_full",
      (params) => {
        const identity = settlementIdentity(
          settlementIdentityInput(params),
        );
        return { identity, payload: settlementIdentityPayload(identity) };
      },
    ],
    [
      "settlement.effect_ids_match",
      (params) => effectIdsMatch(
        typeof params.committed_effect_id === "string"
          ? params.committed_effect_id
          : null,
        requiredString(params.expected_effect_id, "expected_effect_id"),
      ),
    ],
    [
      "settlement.receipt_bound_monitor_phase",
      (params) => receiptBoundMonitorPhase({
        poll_present: params.poll_present === true,
        material_change: params.material_change === true,
        durable_writeback_present: params.durable_writeback_present === true,
        quota_spend_present: params.quota_spend_present === true,
      }),
    ],
    [
      "settlement.receipt_bound_replay_phase",
      (params) => receiptBoundReplayPhase({
        binding_kind: params.binding_kind === undefined
          ? undefined
          : requireStringLiteral(
            params.binding_kind,
            SETTLEMENT_BINDING_KINDS,
            "binding_kind",
            "binding_kind has an unsupported settlement binding kind",
          ),
        writeback_completes_binding:
          params.writeback_completes_binding === true,
        completion_receipt_present: params.completion_receipt_present === true,
        durable_writeback_present: params.durable_writeback_present === true,
        quota_spend_present: params.quota_spend_present === true,
      }),
    ],
    [
      "settlement.receipt_bound_terminal_phase",
      (params) => receiptBoundTerminalPhase({
        terminal_closeout_present: params.terminal_closeout_present === true,
        durable_writeback_present: params.durable_writeback_present === true,
        quota_spend_present: params.quota_spend_present === true,
      }),
    ],
    [
      "settlement.receipt",
      (params) => settlementReceipt(
        settlementIdentityInput(params.identity),
        settlementStepKind(params.step_kind, "step_kind"),
        typeof params.source_ref === "string" ? params.source_ref : undefined,
      ),
    ],
    [
      "settlement.bind_gate",
      (params) => settlementBindGate(settlementResultInput(params.result, "result")),
    ],
    [
      "settlement.bind_reduce",
      (params) => settlementBindReduce(
        settlementResultInput(params.current, "current"),
        settlementResultInput(params.next, "next"),
      ),
    ],
    [
      "settlement.plan_payload",
      (params) => settlementPlanPayload(settlementPlanInput(params.plan, "plan")),
    ],
    [
      "settlement.result_payload",
      (params) => settlementResultPayload(
        settlementResultInput(params.result, "result"),
      ),
    ],
    ["turn.settlement.reduce", reduceTurnSettlementTransaction],
    ["turn.host_todo_completion.evaluate", evaluateHostTodoCompletion],
    ["work_item.replan_settlement.project", projectReplanSettlementContract],
    ["work_item.replan_semantics.project", projectReplanSemantics],
    [
      "work_item.replan_settlement.reentry",
      projectTodoLifecycleSettlementReentry,
    ],
  ]);
}

export async function dispatchEffectRuntimeMethod(
  handlers: ReadonlyMap<string, EffectRuntimeHandler>,
  method: string,
  params: JsonObject,
): Promise<unknown> {
  const handler = handlers.get(method);
  if (!handler) {
    throw new EffectRuntimeRequestError(
      "unsupported Effect runtime method",
      "unsupported_method",
    );
  }
  return await handler(params);
}
