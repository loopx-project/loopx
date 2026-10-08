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

import { EffectRuntimeRequestError } from "./effect_runtime_errors.ts";
import {
  optionalNonEmptyString as optionalString,
  requireJsonObject as requiredObject,
  requireNonEmptyString as requiredString,
  requireStringArray as stringArray,
  requireStringLiteral,
  requireInteger,
} from "./runtime_decode.ts";


import type {TurnJournalInspectionRequest} from "./turn_driver/turn_journal.ts";

type EffectRuntimeHandler = (params: JsonObject) => unknown | Promise<unknown>;

// Resolve only the selected module/handler, never cache its params or decision.
// Node owns module caching; the existing runtime fingerprint owns invalidation.
function lazyHandler<Module>(
  load: () => Promise<Module>,
  select: (module: Module) => EffectRuntimeHandler,
): EffectRuntimeHandler {
  let handler: Promise<EffectRuntimeHandler> | undefined;
  return async (params) => {
    handler ??= load().then(select);
    return (await handler)(params);
  };
}

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
    ...(step.command_condition === undefined ? {} : {
      command_condition: requireStringLiteral(step.command_condition,
        ["todo_deliverable_complete"] as const, `${label}.command_condition`),
    }),
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
      lazyHandler(() => import("./turn_driver/turn_journal.ts"), ({interpretTurnJournal}) => (params) => interpretTurnJournal(turnJournalInspectionRequest(params))),
    ],
    ["turn_journal.write", lazyHandler(() => import("./turn_driver/turn_journal_effects.ts"), ({commitTurnJournal}) => commitTurnJournal)],
    ["turn_journal.find_settlement", lazyHandler(() => import("./turn_driver/turn_journal_query.ts"), ({findTurnJournalBySettlement}) => findTurnJournalBySettlement)],
    ["turn_journal.observed_capabilities", lazyHandler(() => import("./turn_driver/turn_journal_query.ts"), ({readTurnJournalCapabilities}) => readTurnJournalCapabilities)],
    ["todo.completion_fence.evaluate", lazyHandler(() => import("./todos/completion_fence.ts"), ({evaluateTodoCompletionFence}) => evaluateTodoCompletionFence)],
    ["todo.completion_state.normalize", lazyHandler(() => import("./todos/completion_state.ts"), ({normalizeTodoCompletionValue}) => normalizeTodoCompletionValue)],
    ["todo.completion_state.require_metadata", lazyHandler(() => import("./todos/completion_state.ts"), ({requireTodoCompletionMetadataValue}) => requireTodoCompletionMetadataValue)],
    ["todo.field_update.plan", lazyHandler(() => import("./todos/field_update.ts"), ({planTodoFieldUpdate}) => planTodoFieldUpdate)],
    ["todo.priority.plan", lazyHandler(() => import("./todos/priority.ts"), ({evaluateTodoPriority}) => evaluateTodoPriority)],
    ["todo.public_update.plan", lazyHandler(() => import("./todos/public_update.ts"), ({planPublicTodoUpdate}) => planPublicTodoUpdate)],
    ["todo.standing_decision.project", lazyHandler(() => import("./todos/standing_decision.ts"), ({evaluateStandingDecisionProjection}) => evaluateStandingDecisionProjection)],
    ["todo.summary.project", lazyHandler(() => import("./todos/summary_projection.ts"), ({projectTodoSummary}) => projectTodoSummary)],
    ["capabilities.periodic_report.progress.select", lazyHandler(() => import("./capabilities/periodic_report_progress.ts"), ({selectPeriodicReportProgress}) => selectPeriodicReportProgress)],
    ["capabilities.periodic_report.approval_retry.select", lazyHandler(() => import("./capabilities/periodic_report_progress.ts"), ({selectPeriodicReportApprovalRetry}) => selectPeriodicReportApprovalRetry)],
    ["todo.succession.project", lazyHandler(() => import("./todos/succession.ts"), ({projectTodoSuccession}) => projectTodoSuccession)],
    ["todo.work_counts.project", lazyHandler(() => import("./todos/summary_lanes.ts"), ({projectLegacyTodoWorkCounts}) => projectLegacyTodoWorkCounts)],
    ["projection.envelope.seal", lazyHandler(() => import("./projection_envelope.ts"), ({sealProjectionEnvelope}) => sealProjectionEnvelope)],
    ["todo.decision_scope.evaluate", lazyHandler(() => import("./todos/decision_scope.ts"), ({evaluateDecisionScope}) => evaluateDecisionScope)],
    ["todo.user_completion.plan", lazyHandler(() => import("./todos/user_completion.ts"), ({evaluateUserCompletion}) => evaluateUserCompletion)],
    ["agent.capability_gate.evaluate", lazyHandler(() => import("./agents/capability_gate.ts"), ({evaluateCapabilityGate}) => evaluateCapabilityGate)],
    ["agent.capability_memory", lazyHandler(() => import("./agents/capability_memory.ts"), ({agentCapabilityMemory}) => agentCapabilityMemory)],
    ["agent.preferences", lazyHandler(() => import("./capabilities/agent_preferences.ts"), ({agentPreferences}) => agentPreferences)],
    ["todo.archive.capture_dependencies", lazyHandler(() => Promise.all([import("./todos/archive_capture.ts"), import("./coordination/source_transfer.ts")]), ([{captureArchivedTodoDependencies}, {withCoordinationSourceTransfer}]) => withCoordinationSourceTransfer("todo.archive.capture_dependencies", captureArchivedTodoDependencies))],
    ["agent.supervisor.plan_append", lazyHandler(() => import("./agents/supervisor_event_append.ts"), ({planSupervisorEventAppend}) => planSupervisorEventAppend)],
    ["coordination.source.project", lazyHandler(() => Promise.all([import("./coordination/source_projection.ts"), import("./coordination/source_transfer.ts")]), ([{projectCoordinationSource}, {withCoordinationSourceTransfer}]) => withCoordinationSourceTransfer("coordination.source.project", projectCoordinationSource))],
    ["coordination.source.inspect", lazyHandler(() => Promise.all([import("./coordination/cold_source_inspection.ts"), import("./coordination/source_transfer.ts")]), ([{inspectColdCoordinationSource}, {withCoordinationSourceTransfer}]) => withCoordinationSourceTransfer("coordination.source.inspect", inspectColdCoordinationSource))],
    ["coordination.source.inspect_storage", lazyHandler(() => Promise.all([import("./coordination/cold_source_inspection.ts"), import("./coordination/source_transfer.ts")]), ([{inspectColdCoordinationStorage}, {withCoordinationSourceTransfer}]) => withCoordinationSourceTransfer("coordination.source.inspect_storage", inspectColdCoordinationStorage))],
    ["todo.monitor_metadata.plan", lazyHandler(() => import("./todos/monitor_metadata.ts"), ({planMonitorMetadata}) => planMonitorMetadata)],
    ["todo.authoring_scope.plan", lazyHandler(() => import("./todos/authoring_scope.ts"), ({planTodoAuthoringScope}) => planTodoAuthoringScope)],
    ["todo.contract_diagnostics.evaluate", lazyHandler(() => import("./todos/authoring_scope.ts"), ({evaluateTodoContractDiagnostics}) => evaluateTodoContractDiagnostics)],
    [
      "todo.claim.decide",
      lazyHandler(() => import("./coordination/todo_claim.ts"), ({evaluateCoordinationTodoClaimDecision}) => (params) => evaluateCoordinationTodoClaimDecision(
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
      )),
    ],
    ["todo.terminal.decide", lazyHandler(() => import("./coordination/todo_lifecycle_decision.ts"), ({evaluateCoordinationTodoTerminalDecision}) => evaluateCoordinationTodoTerminalDecision)],
    ["todo.mutation.decide", lazyHandler(() => import("./coordination/todo_lifecycle_decision.ts"), ({evaluateCoordinationTodoMutationDecision}) => evaluateCoordinationTodoMutationDecision)],
    ["todo.ownership_gate.decide", lazyHandler(() => import("./coordination/todo_lifecycle_decision.ts"), ({evaluateTodoOwnershipGate}) => evaluateTodoOwnershipGate)],
    ["todo.archive.select", lazyHandler(() => import("./coordination/todo_archive_selection.ts"), ({evaluateCoordinationTodoArchiveSelection}) => evaluateCoordinationTodoArchiveSelection)],
    ["todo.successor.derive", lazyHandler(() => import("./coordination/todo_successor_derivation.ts"), ({evaluateCoordinationTodoSuccessorDerivation}) => evaluateCoordinationTodoSuccessorDerivation)],
    ["todo.completion.reduce", lazyHandler(() => import("./todos/completion_transaction.ts"), ({reduceTodoCompletionTransaction}) => reduceTodoCompletionTransaction)],
    ["todo.next_action.transition", lazyHandler(() => import("./todos/next_action.ts"), ({transitionTodoNextAction}) => transitionTodoNextAction)],
    ["todo.next_action.binding", lazyHandler(() => import("./todos/next_action.ts"), ({projectNextActionBinding}) => projectNextActionBinding)],
    ["todo.resume_condition.normalize", lazyHandler(() => import("./todos/resume_condition.ts"), ({normalizeTodoResumeWhen}) => normalizeTodoResumeWhen)],
    ["todo.resume_condition.evaluate", lazyHandler(() => import("./todos/resume_condition.ts"), ({evaluateTodoResumeConditions}) => evaluateTodoResumeConditions)],
    ["todo.resume_planning.project", lazyHandler(() => import("./todos/resume_planning.ts"), ({projectTodoResumePlanning}) => projectTodoResumePlanning)],
    ["todo.quota_planning.project", lazyHandler(() => import("./todos/quota_selection.ts"), ({projectTodoQuotaPlanning}) => projectTodoQuotaPlanning)],
    ["todo.frontier_revision.project", lazyHandler(() => import("./todos/frontier_revision.ts"), ({projectAdvancementFrontier}) => projectAdvancementFrontier)],
    ["goal.long_todo_chain.evaluate", lazyHandler(() => import("./todos/frontier_revision.ts"), ({evaluateLongTodoChain}) => evaluateLongTodoChain)],
    ["todo.external_wait.plan", lazyHandler(() => import("./todos/resume_condition.ts"), ({planTodoExternalWaitTransition}) => planTodoExternalWaitTransition)],
    ["scheduler.state_transition.evaluate", lazyHandler(() => import("./scheduler/state_transition_rules.ts"), ({evaluateSchedulerStateTransition}) => evaluateSchedulerStateTransition)],
    ["quota.automation_cadence.manage", lazyHandler(() => import("./quota/automation_cadence.ts"), ({manageAutomationCadence}) => manageAutomationCadence)],
    ["quota.automation_cadence.admit", lazyHandler(() => import("./quota/automation_cadence.ts"), ({admitAutomationStart}) => admitAutomationStart)],
    ["quota.automation_cadence.confirm_start", lazyHandler(() => import("./quota/automation_cadence.ts"), ({confirmAutomationStart}) => confirmAutomationStart)],
    ["quota.automation_cadence.schedule", lazyHandler(() => import("./quota/automation_cadence.ts"), ({projectCadenceSchedule}) => projectCadenceSchedule)],
    ["scheduler.state.evaluate", lazyHandler(() => import("./scheduler/state_store.ts"), ({evaluateSchedulerStateOperation}) => evaluateSchedulerStateOperation)],
    ["scheduler.state.load", lazyHandler(() => import("./scheduler/state_store.ts"), ({loadSchedulerState}) => loadSchedulerState)],
    ["scheduler.state.write", lazyHandler(() => import("./scheduler/state_store.ts"), ({writeSchedulerState}) => writeSchedulerState)],
    ["turn.delivery_route.evaluate", lazyHandler(() => import("./turn_driver/delivery_continuity.ts"), ({evaluateDeliveryRoute}) => evaluateDeliveryRoute)],
    ["work_item.action_portfolio.project", lazyHandler(() => import("./work_items/action_portfolio.ts"), ({projectQuotaActionPortfolio}) => projectQuotaActionPortfolio)],
    ["work_item.action_selection.qualify", lazyHandler(() => import("./work_items/action_portfolio.ts"), ({qualifyActionSelection}) => qualifyActionSelection)],
    [
      "work_item.action_selection.reconcile_retained",
      lazyHandler(() => import("./work_items/action_portfolio.ts"), ({reconcileRetainedActionSelection}) => reconcileRetainedActionSelection),
    ],
    ["work_item.planning_horizon.project", lazyHandler(() => import("./work_items/planning_horizon.ts"), ({projectQuotaPlanningHorizon}) => projectQuotaPlanningHorizon)],
    ["todo.context.page", lazyHandler(() => import("./todos/context_projection.ts"), ({projectTodoContextPage}) => projectTodoContextPage)],
    ["work_item.task_graph.topology", lazyHandler(() => import("./work_items/task_graph.ts"), ({projectTaskGraphTopology}) => projectTaskGraphTopology)],
    ["work_item.task_graph.goal_topology", lazyHandler(() => import("./work_items/task_graph.ts"), ({projectGoalTaskGraphTopology}) => projectGoalTaskGraphTopology)],
    ["work_item.planning_inventory.project", lazyHandler(() => import("./work_items/planning_inventory.ts"), ({projectTodoPlanningInventory}) => projectTodoPlanningInventory)],
    ["work_item.planning_inventory.detail", lazyHandler(() => import("./work_items/planning_inventory.ts"), ({projectTodoPlanningInventoryDetail}) => projectTodoPlanningInventoryDetail)],
    ["work_item.refresh_recommendation.resolve", lazyHandler(() => import("./work_items/refresh_recommendation.ts"), ({resolveRefreshRecommendation}) => resolveRefreshRecommendation)],
    ["work_item.refresh_recommendation.lane", lazyHandler(() => import("./work_items/refresh_recommendation.ts"), ({resolveLaneRecommendation}) => resolveLaneRecommendation)],
    ["work_item.delivery_history.project", lazyHandler(() => import("./work_items/delivery_history.ts"), ({projectDeliveryHistory}) => projectDeliveryHistory)],
    ["work_item.delivery_response.project", lazyHandler(() => import("./work_items/delivery_history.ts"), ({projectDeliveryResponse}) => projectDeliveryResponse)],
    ["work_item.delivery_claim.validate", lazyHandler(() => import("./work_items/delivery_outcome.ts"), ({validateDeliveryClaim}) => validateDeliveryClaim)],
    ["goal.vision_checkpoint.evaluate", lazyHandler(() => import("./goals/vision_checkpoint.ts"), ({buildVisionCheckpoint}) => buildVisionCheckpoint)],
    ["goal.checkpoint_read_context.resolve", lazyHandler(() => import("./goals/checkpoint_authority.ts"), ({resolveCheckpointReadContext}) => resolveCheckpointReadContext)],
    ["goal.checkpoint_read_context.commit", lazyHandler(() => import("./goals/checkpoint_commit.ts"), ({commitCheckpoint}) => commitCheckpoint)],
    ["goal.checkpoint_read_context.inspect_replay", lazyHandler(() => import("./goals/checkpoint_commit.ts"), ({inspectCheckpointReplay}) => inspectCheckpointReplay)],
    ["goal.vision_wait.coverage", lazyHandler(() => import("./goals/vision_wait_coverage.ts"), ({projectVisionWaitCoverage}) => projectVisionWaitCoverage)],
    ["goal.shared_goal_alignment.project", lazyHandler(() => import("./goals/shared_goal_alignment.ts"), ({projectSharedGoalAlignment}) => projectSharedGoalAlignment)],
    ["goal.operator_actions.project", lazyHandler(() => import("./goals/operator_actions.ts"), ({projectGoalOperatorActions}) => projectGoalOperatorActions)],
    ["goal.amendment_proposal.admit", lazyHandler(() => import("./goals/goal_amendment_proposal.ts"), ({admitGoalAmendmentProposal}) => admitGoalAmendmentProposal)],
    ["goal.source_session.bind.decide", lazyHandler(() => import("./goals/source_session_lifetime.ts"), ({decideProjectSessionBind}) => decideProjectSessionBind)],
    ["goal.source_session.unbind.decide", lazyHandler(() => import("./goals/source_session_lifetime.ts"), ({decideProjectSessionUnbind}) => decideProjectSessionUnbind)],
    ["goal.source_session.recreate.decide", lazyHandler(() => import("./goals/source_session_lifetime.ts"), ({decideGoalRecreation}) => decideGoalRecreation)],
    ["goal.source_session.turn_effect.admit", lazyHandler(() => import("./goals/source_session_lifetime.ts"), ({decideSourceTurnEffectAdmission}) => decideSourceTurnEffectAdmission)],
    ["goal.source_session.turn_effect.resolve_absent", lazyHandler(() => import("./goals/source_session_lifetime.ts"), ({decideSourceTurnEffectAbsentResolution}) => decideSourceTurnEffectAbsentResolution)],
    ["goal.source_session.turn_effect.release", lazyHandler(() => import("./goals/source_session_lifetime.ts"), ({decideSourceTurnEffectRelease}) => decideSourceTurnEffectRelease)],
    ["goal.source_session.turn_effect.gate", lazyHandler(() => import("./goals/source_session_lifetime.ts"), ({decideSourceTurnEffectGate}) => decideSourceTurnEffectGate)],
    ["goal.first_party_host_runtime.decide", lazyHandler(() => import("./goals/first_party_host_runtime.ts"), ({decideFirstPartyHostRuntime}) => decideFirstPartyHostRuntime)],
    ["goal.chat_session.lifecycle.decide", lazyHandler(() => import("./goals/chat_session_lifecycle.ts"), ({decideChatSessionLifecycle}) => decideChatSessionLifecycle)],
    ["goal.acceptance.inspect", lazyHandler(() => import("./goals/acceptance_authority.ts"), ({inspectLocalGoalAcceptance}) => inspectLocalGoalAcceptance)],
    ["goal.acceptance.configure", lazyHandler(() => import("./goals/acceptance_authority.ts"), ({commitLocalGoalAcceptance}) => commitLocalGoalAcceptance)],
    ["goal.acceptance.verify.commit", lazyHandler(() => import("./goals/acceptance_authority.ts"), ({commitLocalGoalAcceptanceVerification}) => commitLocalGoalAcceptanceVerification)],
    ["agent.delivery_workspace.evaluate", lazyHandler(() => import("./agents/delivery_workspace.ts"), ({evaluateDeliveryWorkspace}) => evaluateDeliveryWorkspace)],
    [
      "quota.delivery_workspace_causality.evaluate",
      lazyHandler(() => import("./quota/settlement_workspace_causality.ts"), ({evaluateDeliveryWorkspaceCausality}) => evaluateDeliveryWorkspaceCausality),
    ],
    ["quota.spend.commit", lazyHandler(() => import("./quota/spend_commit.ts"), ({evaluateQuotaSpendCommit}) => evaluateQuotaSpendCommit)],
    ["quota.void.commit", lazyHandler(() => import("./quota/void_commit.ts"), ({evaluateQuotaVoidCommit}) => evaluateQuotaVoidCommit)],
    ["quota.settlement.read", lazyHandler(() => import("./quota/settlement_readback.ts"), ({readQuotaSettlement}) => readQuotaSettlement)],
    [
      "quota.prior_host_turn_closeout.preflight",
      lazyHandler(() => import("./quota/unsettled_host_turn_recovery.ts"), ({preflightPriorHostTurnCloseout}) => preflightPriorHostTurnCloseout),
    ],
    [
      "quota.unsettled_host_turn_recovery.reduce",
      lazyHandler(() => import("./quota/unsettled_host_turn_recovery.ts"), ({reduceUnsettledHostTurnRecovery}) => reduceUnsettledHostTurnRecovery),
    ],
    ["quota.turn_envelope.evaluate", lazyHandler(() => import("./quota/turn_envelope.ts"), ({evaluateTurnEnvelope}) => evaluateTurnEnvelope)],
    ["quota.scoped_override.project", lazyHandler(() => import("./quota/scoped_override.ts"), ({projectScopedOverride}) => projectScopedOverride)],
    ["task_lease.owner_eligibility", lazyHandler(() => import("./work_items/task_lease_eligibility.ts"), ({evaluateTaskLeaseOwnerEligibility}) => evaluateTaskLeaseOwnerEligibility)],
    ["task_lease.acquire.native", lazyHandler(() => import("./work_items/task_lease_acquire.ts"), ({executeTaskLeaseAcquire}) => executeTaskLeaseAcquire)],
    ["task_lease.inspect.native", lazyHandler(() => import("./work_items/task_lease_inspection.ts"), ({inspectTaskLease}) => inspectTaskLease)],
    ["task_lease.lifecycle.native", lazyHandler(() => import("./work_items/task_lease_lifecycle.ts"), ({executeTaskLeaseLifecycle}) => executeTaskLeaseLifecycle)],
    ["coordination.runtime_shadow.bootstrap", lazyHandler(() => Promise.all([import("./coordination/runtime_shadow.ts"), import("./coordination/source_transfer.ts")]), ([{bootstrapCoordinationRuntimeShadow}, {withCoordinationSourceTransfer}]) => withCoordinationSourceTransfer("coordination.runtime_shadow.bootstrap", bootstrapCoordinationRuntimeShadow))],
    ["coordination.runtime_shadow.commit", lazyHandler(() => Promise.all([import("./coordination/runtime_shadow.ts"), import("./coordination/source_transfer.ts")]), ([{commitCoordinationRuntimeShadow}, {withCoordinationSourceTransfer}]) => withCoordinationSourceTransfer("coordination.runtime_shadow.commit", commitCoordinationRuntimeShadow))],
    ["coordination.runtime_shadow.inspect", lazyHandler(() => Promise.all([import("./coordination/runtime_shadow.ts"), import("./coordination/source_transfer.ts")]), ([{inspectCoordinationRuntimeShadow}, {withCoordinationSourceTransfer}]) => withCoordinationSourceTransfer("coordination.runtime_shadow.inspect", inspectCoordinationRuntimeShadow))],
    ["coordination.runtime_shadow.qualify", lazyHandler(() => Promise.all([import("./coordination/runtime_shadow.ts"), import("./coordination/source_transfer.ts")]), ([{qualifyCoordinationRuntimeShadow}, {withCoordinationSourceTransfer}]) => withCoordinationSourceTransfer("coordination.runtime_shadow.qualify", qualifyCoordinationRuntimeShadow))],
    [
      "coordination.runtime_shadow.todo_read_candidate",
      lazyHandler(() => Promise.all([import("./coordination/runtime_shadow.ts"), import("./coordination/source_transfer.ts")]), ([{readCoordinationRuntimeShadowTodoCandidate}, {withCoordinationSourceTransfer}]) => withCoordinationSourceTransfer("coordination.runtime_shadow.todo_read_candidate", readCoordinationRuntimeShadowTodoCandidate)),
    ],
    ["coordination.runtime_shadow.rollback", lazyHandler(() => Promise.all([import("./coordination/runtime_shadow.ts"), import("./coordination/source_transfer.ts")]), ([{rollbackCoordinationRuntimeShadow}, {withCoordinationSourceTransfer}]) => withCoordinationSourceTransfer("coordination.runtime_shadow.rollback", rollbackCoordinationRuntimeShadow))],
    ["coordination.local_authority.promote", lazyHandler(() => import("./coordination/local_authority_runtime.ts"), ({promoteLocalCoordinationAuthority}) => promoteLocalCoordinationAuthority)],
    ["coordination.authority_archive.manage", lazyHandler(() => import("./coordination/local_authority_archive.ts"), ({manageLocalAuthorityArchive}) => manageLocalAuthorityArchive)],
    ["configuration.backup", lazyHandler(() => import("./configuration_backup.ts"), ({configurationBackupOperation}) => configurationBackupOperation)],
    ["coordination.sqlite_backup.snapshot", lazyHandler(() => import("./coordination/sqlite_backup.ts"), ({snapshotSqliteBackup}) => snapshotSqliteBackup)],
    ["coordination.local_authority.new_goal_storage", lazyHandler(() => import("./coordination/local_authority_defaults.ts"), ({manageNewGoalStorage}) => manageNewGoalStorage)],
    ["coordination.local_authority.promotion_review", lazyHandler(() => Promise.all([import("./coordination/local_authority_runtime.ts"), import("./coordination/source_transfer.ts")]), ([{reviewLocalCoordinationAuthorityPromotion}, {withCoordinationSourceTransfer}]) => withCoordinationSourceTransfer("coordination.local_authority.promotion_review", reviewLocalCoordinationAuthorityPromotion))],
    ["coordination.local_authority.promotion_reviewed", lazyHandler(() => import("./coordination/local_authority_runtime.ts"), ({executeReviewedCoordinationPromotion}) => executeReviewedCoordinationPromotion)],
    ["coordination.local_authority.todo_continuation", lazyHandler(() => import("./coordination/local_authority_runtime.ts"), ({continueLocalTodo}) => continueLocalTodo)],
    ["coordination.local_authority.todo_claim", lazyHandler(() => import("./coordination/local_authority_runtime.ts"), ({claimLocalCoordinationTodo}) => claimLocalCoordinationTodo)],
    ["coordination.local_authority.todo_create", lazyHandler(() => import("./coordination/local_authority_runtime.ts"), ({createLocalCoordinationTodo}) => createLocalCoordinationTodo)],
    ["work_items.team_plan.preview", lazyHandler(() => import("./work_items/team_plan.ts"), ({previewTeamPlan}) => previewTeamPlan)],
    ["work_items.team_plan.plan", lazyHandler(() => import("./work_items/team_plan.ts"), ({planTeamTransaction}) => planTeamTransaction)],
    ["work_items.team_plan.identity", lazyHandler(() => import("./work_items/team_plan.ts"), ({teamTransactionIdentity}) => value => teamTransactionIdentity(requiredObject(value, "team plan request")))],
    ["work_items.team_plan.commit", lazyHandler(() => import("./work_items/team_plan_authority.ts"), ({commitLocalTeamPlan}) => commitLocalTeamPlan)],
    ["coordination.local_authority.todo_update", lazyHandler(() => import("./coordination/local_authority_runtime.ts"), ({updateLocalCoordinationTodo}) => updateLocalCoordinationTodo)],
    ["coordination.local_authority.monitor_poll", lazyHandler(() => import("./coordination/local_authority_runtime.ts"), ({pollLocalCoordinationMonitor}) => pollLocalCoordinationMonitor)],
    ["coordination.handoff_mode.legacy_plan", lazyHandler(() => import("./coordination/handoff_mode_legacy_plan.ts"), ({planLegacyHandoffMode}) => planLegacyHandoffMode)],
    ["coordination.local_authority.handoff_mode_set", lazyHandler(() => import("./coordination/handoff_mode_runtime.ts"), ({setLocalHandoffMode}) => setLocalHandoffMode)],
    ["coordination.local_authority.handoff_mode_migrate", lazyHandler(() => import("./coordination/handoff_mode_runtime.ts"), ({migrateLocalHandoffMode}) => migrateLocalHandoffMode)],
    ["coordination.local_authority.todo_terminal", lazyHandler(() => import("./coordination/local_authority_runtime.ts"), ({terminalLifecycleLocalCoordinationTodo}) => terminalLifecycleLocalCoordinationTodo)],
    ["coordination.local_authority.todo_archive", lazyHandler(() => import("./coordination/local_authority_runtime.ts"), ({archiveLocalCoordinationTodos}) => archiveLocalCoordinationTodos)],
    ["coordination.local_authority.todo_archive_ack", lazyHandler(() => import("./coordination/local_authority_runtime.ts"), ({acknowledgeLocalCoordinationTodoArchive}) => acknowledgeLocalCoordinationTodoArchive)],
    ["coordination.local_authority.todo_read", lazyHandler(() => import("./coordination/local_authority_read.ts"), ({readLocalCoordinationTodo}) => readLocalCoordinationTodo)],
    ["coordination.local_authority.operation_receipt", lazyHandler(() => import("./coordination/local_authority_read.ts"), ({readLocalCoordinationOperationReceipt}) => readLocalCoordinationOperationReceipt)],
    ["coordination.local_authority.todo_source", lazyHandler(() => import("./coordination/local_authority_read.ts"), ({readLocalCoordinationTodoSource}) => readLocalCoordinationTodoSource)],
    ["coordination.ownership_observation", lazyHandler(() => import("./coordination/ownership_observation.ts"), ({projectOwnershipObservation}) => projectOwnershipObservation)],
    ["coordination.local_authority.ownership_observation", lazyHandler(() => import("./coordination/local_authority_runtime.ts"), ({observeLocalCoordinationOwnership}) => observeLocalCoordinationOwnership)],
    ["coordination.local_authority.todo_snapshot_page", lazyHandler(() => import("./coordination/canonical_snapshot_page.ts"), ({readCanonicalSnapshotPage}) => readCanonicalSnapshotPage)],
    ["coordination.local_authority.todo_list", lazyHandler(() => import("./coordination/local_authority_read.ts"), ({listLocalCoordinationTodos}) => listLocalCoordinationTodos)],
    [
      "coordination.local_authority.legacy_writer_fence.engage",
      lazyHandler(() => import("./coordination/legacy_writer_fence.ts"), ({engageLegacyCoordinationWriterFence}) => engageLegacyCoordinationWriterFence),
    ],
    [
      "coordination.local_authority.legacy_write_check",
      lazyHandler(() => import("./coordination/legacy_writer_fence.ts"), ({checkLegacyCoordinationWriteAllowed}) => checkLegacyCoordinationWriteAllowed),
    ],
    ["task_lease.write_scopes.overlap", lazyHandler(() => import("./work_items/task_lease_acquire_decision.ts"), ({evaluateTaskLeaseWriteScopesOverlap}) => evaluateTaskLeaseWriteScopesOverlap)],
    ["quota.monitor_poll.commit", lazyHandler(() => import("./quota/monitor_poll_commit.ts"), ({evaluateQuotaMonitorPollCommit}) => evaluateQuotaMonitorPollCommit)],
    ["presentation.decision_notice.project", lazyHandler(() => import("./presentation/decision_notice.ts"), ({projectDecisionNotice}) => projectDecisionNotice)],
    ["presentation.decision_notice.validate_references", lazyHandler(() => import("./presentation/decision_notice.ts"), ({validateDecisionNoticeReferences}) => validateDecisionNoticeReferences)],
    ["presentation.goal_attention.project", lazyHandler(() => import("./presentation/goal_attention.ts"), ({projectGoalAttention}) => projectGoalAttention)],
    ["presentation.goal_attention.bound", lazyHandler(() => import("./presentation/goal_attention.ts"), ({boundGoalAttention}) => boundGoalAttention)],
    ["presentation.action_review_plan.compile", lazyHandler(() => import("./presentation/action_review_plan.ts"), ({compileActionReviewPlan}) => (params) =>
      compileActionReviewPlan(params.proposal, params.now_ms === undefined
        ? undefined : requireInteger(params.now_ms, "now_ms")))],
    ["operation.agent_executor.normalize", lazyHandler(() => import("./work_items/operation_agent_handoff.ts"), ({normalizeAgentOperationExecutor}) => normalizeAgentOperationExecutor)],
    ["operation.source_route.resolve", lazyHandler(() => import("./work_items/operation_agent_handoff.ts"), ({resolveOperationSourceRoute}) => resolveOperationSourceRoute)],
    ["operation.managed_binding.current", lazyHandler(() => import("./work_items/operation_agent_handoff.ts"), ({managedOperationBindingCurrent}) => managedOperationBindingCurrent)],
    ["operation.managed_transport.project", lazyHandler(() => import("./work_items/operation_agent_handoff.ts"), ({projectManagedOperationTransport}) => projectManagedOperationTransport)],
    ["operation.agent_handoff.actor", lazyHandler(() => import("./work_items/operation_agent_handoff.ts"), ({deriveAgentOperationActor}) => deriveAgentOperationActor)],
    ["operation.agent_handoff.plan", lazyHandler(() => import("./work_items/operation_agent_handoff.ts"), ({planAgentOperationHandoff}) => planAgentOperationHandoff)],
    ["operation.agent_handoff.inbox", lazyHandler(() => import("./work_items/operation_agent_handoff.ts"), ({projectAgentOperationInbox}) => projectAgentOperationInbox)],
    ["scheduler.monitor_successor.plan", lazyHandler(() => import("./scheduler/monitor_successor.ts"), ({planMonitorSuccessor}) => planMonitorSuccessor)],
    ["scheduler.monitor_batch.plan", lazyHandler(() => import("./scheduler/monitor_batch.ts"), ({planLegacyMonitorBatch}) => planLegacyMonitorBatch)],
    ["scheduler.monitor_target.select", lazyHandler(() => import("./scheduler/monitor_successor.ts"), ({selectMonitorTodoRequest}) => selectMonitorTodoRequest)],
    ["capabilities.issue_fix.monitor_reconciliation.plan", lazyHandler(() => import("./capabilities/issue_fix_monitor_reconciliation.ts"), ({planIssueFixMonitorReconciliation}) => planIssueFixMonitorReconciliation)],
    ["capabilities.pr_review.approval_closeout.plan", lazyHandler(() => import("./capabilities/pr_review_approval_closeout.ts"), ({planPrReviewApprovalCloseout}) => planPrReviewApprovalCloseout)],
    ["capabilities.pr_review.configuration", lazyHandler(() => import("./capabilities/pr_review_order.ts"), ({prReviewConfiguration}) => prReviewConfiguration)],
    ["capabilities.pr_review.order", lazyHandler(() => import("./capabilities/pr_review_order.ts"), ({orderPrReviewQueue}) => orderPrReviewQueue)],
    ["coordination.local_authority_shadow.record", lazyHandler(() => import("./coordination/local_authority_shadow.ts"), ({recordLocalAuthorityShadow}) => recordLocalAuthorityShadow)],
    ["coordination.runtime_shadow.commit_entry", lazyHandler(() => import("./coordination/shadow_entry_delivery.ts"), ({deliverShadowEntry}) => deliverShadowEntry)],
    ["coordination.runtime_shadow.outbox_read", lazyHandler(() => import("./coordination/local_authority_shadow.ts"), ({readLocalAuthorityShadow}) => readLocalAuthorityShadow)],
    ["coordination.runtime_shadow.drain", lazyHandler(() => import("./coordination/shadow_drain.ts"), ({drainShadowOutbox}) => drainShadowOutbox)],
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
      lazyHandler(() => import("./governed_capability.ts"), ({validateGovernedCapabilityAdmission}) => (params) => validateGovernedCapabilityAdmission({
        admission: params.admission,
        todo_id: requiredString(params.todo_id, "todo_id"),
        todo_contract: params.todo_contract,
      })),
    ],
    [
      "governed_capability.validate_result",
      lazyHandler(() => import("./governed_capability.ts"), ({validateGovernedCapabilityResult}) => (params) => validateGovernedCapabilityResult({
        value: params.value,
        invocation_id: requiredString(params.invocation_id, "invocation_id"),
        effect_id: requiredString(params.effect_id, "effect_id"),
        result_schema: requiredString(params.result_schema, "result_schema"),
        effect_class: requiredString(params.effect_class, "effect_class"),
        transition_contract: params.transition_contract,
      })),
    ],
    [
      "governed_capability.validate_settlement_callback",
      lazyHandler(() => import("./governed_capability.ts"), ({validateGovernedCapabilitySettlementCallback}) => (params) => validateGovernedCapabilitySettlementCallback({
        payload: params.payload,
        effect_id: requiredString(params.effect_id, "effect_id"),
        effect_receipt_digest: requiredString(
          params.effect_receipt_digest,
          "effect_receipt_digest",
        ),
        require_receipt_digest: params.require_receipt_digest === true,
      })),
    ],
    [
      "governed_capability.settlement_status",
      lazyHandler(() => import("./governed_capability.ts"), ({governedCapabilitySettlementStatus}) => (params) => governedCapabilitySettlementStatus(params.failure)),
    ],
    [
      "quota.peer_orchestration.project",
      lazyHandler(() => import("./quota/peer_orchestration.ts"), ({projectPeerOrchestration}) => (params) => projectPeerOrchestration(params)),
    ],
    [
      "capability_hook.agent_context.describe",
      lazyHandler(() => import("./subagent_context.ts"), ({describeSubagentContext}) => () => describeSubagentContext()),
    ],
    [
      "capability_hook.agent_context.project",
      lazyHandler(() => import("./goal_agent_context.ts"), ({evaluateGoalAgentContext}) => (params) => evaluateGoalAgentContext(params)),
    ],
    [
      "capability.improvement.inspect",
      lazyHandler(() => import("./capabilities/goal_capability_organization.ts"), ({inspectImprovementPolicy}) => (params) => inspectImprovementPolicy(params.policy)),
    ],
    [
      "capability.improvement.configuration",
      lazyHandler(() => import("./capabilities/goal_capability_organization.ts"), ({planImprovementConfiguration}) => (params) => planImprovementConfiguration(params)),
    ],
    [
      "capability_hook.interaction_projection.validate_registration",
      lazyHandler(() => import("./capability_hooks.ts"), ({validateInteractionProjectionHookRegistration}) => (params) => validateInteractionProjectionHookRegistration(
        params.registration,
      )),
    ],
    [
      "capability_hook.interaction_projection.validate",
      lazyHandler(() => import("./capability_hooks.ts"), ({validateInteractionProjectionHookInvocation}) => (params) => validateInteractionProjectionHookInvocation({
        registration: params.registration,
        result: params.result,
      })),
    ],
    [
      "capability_hook.turn_start.validate_registration",
      lazyHandler(() => import("./capability_hooks.ts"), ({validateTurnStartHookRegistration}) => (params) => validateTurnStartHookRegistration(params.registration)),
    ],
    [
      "capability_hook.turn_start.validate",
      lazyHandler(() => import("./capability_hooks.ts"), ({validateTurnStartHookInvocation}) => (params) => validateTurnStartHookInvocation({
        registration: params.registration,
        result: params.result,
      })),
    ],
    [
      "capability_hook.post_writeback.transaction",
      lazyHandler(() => import("./post_writeback_hook_transaction.ts"), ({evaluatePostWritebackHookTransaction}) => evaluatePostWritebackHookTransaction),
    ],
    ["collaboration.delegation.binding", lazyHandler(() => import("./collaboration/delegation.ts"), ({selectDelegationBinding}) => selectDelegationBinding)],
    ["turn.selection.rejection", lazyHandler(() => import("./turn_driver/selection_rejection.ts"), ({projectTurnSelectionRejection}) => projectTurnSelectionRejection)],
    ["collaboration.delegation.preflight", lazyHandler(() => import("./collaboration/delegation.ts"), ({delegationPreflight}) => delegationPreflight)],
    ["collaboration.delegation.validation_plan", lazyHandler(() => import("./collaboration/delegation.ts"), ({delegationValidationPlan}) => delegationValidationPlan)],
    ["collaboration.delegation.checked_artifacts", lazyHandler(() => import("./collaboration/delegation.ts"), ({delegationCheckedArtifacts}) => delegationCheckedArtifacts)],
    ["collaboration.delegation.turn_plan", lazyHandler(() => import("./collaboration/delegation.ts"), ({delegationTurnPlanDecision}) => delegationTurnPlanDecision)],
    ["collaboration.delegation.inventory_query", lazyHandler(() => import("./collaboration/delegation.ts"), ({delegationInventoryQuery}) => delegationInventoryQuery)],
    ["collaboration.delegation.inventory_item", lazyHandler(() => import("./collaboration/delegation.ts"), ({delegationInventoryItem}) => delegationInventoryItem)],
    ["collaboration.chat_mode", lazyHandler(() => import("./collaboration/chat_mode.ts"), ({planChatMode}) => planChatMode)],
    ["collaboration.goal_draft", lazyHandler(() => import("./collaboration/goal_draft.ts"), ({admitGoalDraft}) => (params) => ({draft: admitGoalDraft(params)}))],
    ["collaboration.conversation.trigger", lazyHandler(() => import("./collaboration/conversation_trigger.ts"), ({resolveConversationTrigger}) => resolveConversationTrigger)],
    ["collaboration.conversation.scope", lazyHandler(() => import("./collaboration/conversation_scope.ts"), ({resolveConversationScope}) => resolveConversationScope)],
    ["collaboration.project.context", lazyHandler(() => import("./collaboration/project_conversation.ts"), ({resolveProjectConversation}) => resolveProjectConversation)],
    ["collaboration.project.session_identity", lazyHandler(() => import("./collaboration/conversation_scope.ts"), ({projectConversationIdentity}) => projectConversationIdentity)],
    ["collaboration.steward.session_identity", lazyHandler(() => import("./collaboration/conversation_scope.ts"), ({stewardConversationIdentity}) => stewardConversationIdentity)],
    ["collaboration.steward.command", lazyHandler(() => import("./collaboration/conversation_binding.ts"), ({stewardCommand}) => stewardCommand)],
    ["collaboration.steward.authorize_creation", lazyHandler(() => import("./collaboration/conversation_binding.ts"), ({authorizeStewardCreation}) => authorizeStewardCreation)],
    ["collaboration.conversation.binding", lazyHandler(() => import("./collaboration/conversation_binding.ts"), ({planConversationBinding}) => planConversationBinding)],
    ["collaboration.conversation.bound_context", lazyHandler(() => import("./collaboration/conversation_binding.ts"), ({resolveBoundConversation}) => resolveBoundConversation)],
    ["collaboration.conversation.request", lazyHandler(() => import("./collaboration/conversation_binding.ts"), ({planBoundConversationRequest}) => planBoundConversationRequest)],
    ["collaboration.conversation.agent_target", lazyHandler(() => import("./collaboration/conversation_binding.ts"), ({resolveConversationAgentTarget}) => resolveConversationAgentTarget)],
    ["collaboration.peer.context_access", lazyHandler(() => import("./collaboration/peer_context.ts"), ({requirePeerContextAccess}) => requirePeerContextAccess)],
    ["collaboration.source.recipients", lazyHandler(() => import("./collaboration/source_grants.ts"), ({resolveSourceRecipients}) => resolveSourceRecipients)],
    ["collaboration.source.execution_bindings", lazyHandler(() => import("./collaboration/source_grants.ts"), ({sourceExecutionBindings}) => sourceExecutionBindings)],
    ["collaboration.source.configure_recipient", lazyHandler(() => import("./collaboration/source_grants.ts"), ({configureSourceRecipient}) => configureSourceRecipient)],
    ["collaboration.source.configure_scope", lazyHandler(() => import("./collaboration/source_grants.ts"), ({configureSourceScope}) => configureSourceScope)],
    ["collaboration.conversation.reply_context", lazyHandler(() => import("./collaboration/conversation_reply_context.ts"), ({projectConversationReplyContext}) => projectConversationReplyContext)],
    ["chat.turn.accept", lazyHandler(() => import("./turn_driver/chat_turn_acceptance.ts"), ({planChatTurnAcceptance}) => planChatTurnAcceptance)],
    ["chat.turn.execution_allowed", lazyHandler(() => import("./turn_driver/chat_turn_acceptance.ts"), ({mayContinueChatTurn}) => mayContinueChatTurn)],
    ["collaboration.delegation.observe", lazyHandler(() => import("./collaboration/delegation.ts"), ({transitionDelegationObservation}) => transitionDelegationObservation)],
    ["collaboration.delegation.observe_wake", lazyHandler(() => import("./collaboration/delegation.ts"), ({decideDelegationWakeObservation}) => decideDelegationWakeObservation)],
    ["collaboration.delegation.recover_validated_settlement", lazyHandler(() => import("./collaboration/delegation.ts"), ({recoverValidatedDelegationSettlement}) => recoverValidatedDelegationSettlement)],
    ["collaboration.delegation.stop", lazyHandler(() => import("./collaboration/delegation.ts"), ({decideDelegationStop}) => decideDelegationStop)],
    ["collaboration.delegation.adoption", lazyHandler(() => import("./collaboration/delegation.ts"), ({recordDelegationAdoption}) => recordDelegationAdoption)],
    [
      "collaboration.request.normalize",
      lazyHandler(() => import("./collaboration/semantic_request.ts"), ({normalizeCollaborationRequest}) => (params) => normalizeCollaborationRequest(params.request)),
    ],
    ["collaboration.source_context.normalize", lazyHandler(() => import("./collaboration/semantic_request.ts"), ({normalizeCollaborationSourceContext}) => normalizeCollaborationSourceContext)],
    ["collaboration.inbox.inspect_receipts", lazyHandler(() => import("./collaboration/inbox_receipts.ts"), ({inspectCollaborationInboxReceipts}) => inspectCollaborationInboxReceipts)],
    ["collaboration.inbox.receiver_followthrough", lazyHandler(() => import("./collaboration/inbox_receipts.ts"), ({projectReceiverFollowthrough}) => projectReceiverFollowthrough)],
    ["collaboration.result.plan_publication", lazyHandler(() => import("./collaboration/result_publication.ts"), ({planCollaborationResult}) => planCollaborationResult)],
    ["collaboration.result.attachment_refs", lazyHandler(() => import("./collaboration/result_publication.ts"), ({resultAttachmentRefs}) => resultAttachmentRefs)],
    ["collaboration.result.delivery_ready", lazyHandler(() => import("./collaboration/result_publication.ts"), ({collaborationResultDeliveryReady}) => collaborationResultDeliveryReady)],
    ["collaboration.peer_host_route.select", lazyHandler(() => import("./collaboration/peer_route_selection.ts"), ({selectObservedPeerHostRoute}) => selectObservedPeerHostRoute)],
    ["runtime.execution_identity.codex", lazyHandler(() => import("./runtime/execution_identity.ts"), ({readCodexExecutionIdentity}) => readCodexExecutionIdentity)],
    ["runtime.execution_identity.match", lazyHandler(() => import("./runtime/execution_identity.ts"), ({matchExecutionDeclaration}) => matchExecutionDeclaration)],
    [
      "collaboration.goal_instance.decide",
      lazyHandler(() => import("./collaboration/goal_instance_lifecycle.ts"), ({decideCollaborationLifecycle}) => (params) => decideCollaborationLifecycle(params)),
    ],
    ["external_evidence.discover", lazyHandler(() => import("./capabilities/external_evidence.ts"), ({projectExternalEvidenceDiscovery}) => projectExternalEvidenceDiscovery)],
    ["external_evidence.plan", lazyHandler(() => import("./capabilities/external_evidence.ts"), ({planExternalEvidenceRequest}) => planExternalEvidenceRequest)],
    ["external_evidence.receipt", lazyHandler(() => import("./capabilities/external_evidence.ts"), ({recordExternalEvidenceReceiptObservation}) => recordExternalEvidenceReceiptObservation)],
    ["external_evidence.admit", lazyHandler(() => import("./capabilities/external_evidence.ts"), ({evaluateExternalEvidenceAdmission}) => evaluateExternalEvidenceAdmission)],
    ["external_evidence.retire", lazyHandler(() => import("./capabilities/external_evidence.ts"), ({projectExternalEvidenceRetirement}) => projectExternalEvidenceRetirement)],
    ["performance_diagnosis.plan", lazyHandler(() => import("./capabilities/performance_diagnosis.ts"), ({planPerformanceDiagnosis}) => planPerformanceDiagnosis)],
    ["performance_diagnosis.inspect", lazyHandler(() => import("./capabilities/performance_diagnosis.ts"), ({summarizePerformanceProfile}) => summarizePerformanceProfile)],
    ["content_reference.search", lazyHandler(() => import("./capabilities/content_reference.ts"), ({searchContentReferences}) => searchContentReferences)],
    ["content_reference.capture", lazyHandler(() => import("./capabilities/content_reference.ts"), ({captureContentReference}) => captureContentReference)],
    ["content_reference.draft", lazyHandler(() => import("./capabilities/content_reference.ts"), ({planReferenceDraft}) => planReferenceDraft)],
    ["reward_memory.decision.plan", lazyHandler(() => import("./capabilities/reward_memory_decision.ts"), ({planRewardMemoryDecision}) => planRewardMemoryDecision)],
    ["reward_memory.decision.project", lazyHandler(() => import("./capabilities/reward_memory_decision.ts"), ({projectRewardMemoryDecision}) => projectRewardMemoryDecision)],
    ["reward_memory.read_authority.surface_checkpoints", lazyHandler(() => import("./capabilities/reward_memory_decision.ts"), ({buildRewardMemorySurfaceReadCheckpoints}) => buildRewardMemorySurfaceReadCheckpoints)],
    [
      "manager.return_delivery.normalize_attempt",
      lazyHandler(() => import("./collaboration/return_delivery.ts"), ({normalizeManagerReturnDeliveryAttempt}) => (params) => normalizeManagerReturnDeliveryAttempt(params.attempt)),
    ],
    [
      "manager.return_delivery.classify_verification",
      lazyHandler(() => import("./collaboration/return_delivery.ts"), ({classifyManagerReturnVerification}) => (params) => classifyManagerReturnVerification(params.outcome)),
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
      lazyHandler(() => import("./quota/settlement_phase.ts"), ({receiptBoundMonitorPhase}) => (params) => receiptBoundMonitorPhase({
        poll_present: params.poll_present === true,
        material_change: params.material_change === true,
        durable_writeback_present: params.durable_writeback_present === true,
        quota_spend_present: params.quota_spend_present === true,
      })),
    ],
    [
      "settlement.receipt_bound_replay_phase",
      lazyHandler(() => import("./quota/settlement_phase.ts"), ({receiptBoundReplayPhase}) => (params) => receiptBoundReplayPhase({
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
      })),
    ],
    [
      "settlement.receipt_bound_terminal_phase",
      lazyHandler(() => import("./quota/settlement_phase.ts"), ({receiptBoundTerminalPhase}) => (params) => receiptBoundTerminalPhase({
        terminal_closeout_present: params.terminal_closeout_present === true,
        durable_writeback_present: params.durable_writeback_present === true,
        quota_spend_present: params.quota_spend_present === true,
      })),
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
    ["settlement.turn_scoped_cli_plan", lazyHandler(() => import("./quota/settlement_plan.ts"), ({turnScopedCliSettlementPlan}) => (params) =>
      settlementPlanPayload(turnScopedCliSettlementPlan(params)))],
    [
      "settlement.result_payload",
      (params) => settlementResultPayload(
        settlementResultInput(params.result, "result"),
      ),
    ],

    ["turn.settlement.reduce", lazyHandler(() => import("./turn_driver/settlement.ts"), ({reduceTurnSettlementTransaction}) => reduceTurnSettlementTransaction)],
    ["turn.host_todo_completion.evaluate", lazyHandler(() => import("./turn_driver/host_todo_completion.ts"), ({evaluateHostTodoCompletion}) => evaluateHostTodoCompletion)],
    ["work_item.interaction_reads.project", lazyHandler(() => import("./work_items/interaction_contract.ts"), ({projectInteractionRequiredReads}) => projectInteractionRequiredReads)],
    ["work_item.context.plan", lazyHandler(() => import("./work_items/interaction_contract.ts"), ({planInteractionWorkContext}) => planInteractionWorkContext)],
    ["work_item.context.project", lazyHandler(() => import("./work_items/interaction_contract.ts"), ({projectInteractionWorkContext}) => projectInteractionWorkContext)],
    ["work_item.replan_settlement.project", lazyHandler(() => import("./work_items/replan_settlement.ts"), ({projectReplanSettlementContract}) => projectReplanSettlementContract)],
    ["work_item.replan_semantics.project", lazyHandler(() => import("./work_items/replan_semantics.ts"), ({projectReplanSemantics}) => projectReplanSemantics)],
    ["work_item.replan_context.project", lazyHandler(() => import("./work_items/replan_context.ts"), ({projectReplanContext}) => projectReplanContext)],
    ["work_item.replan_context.project_snapshot", lazyHandler(() => import("./work_items/replan_context.ts"), ({projectReplanContextSnapshot}) => projectReplanContextSnapshot)],
    ["explore.configuration.resolve", lazyHandler(() => import("./capabilities/explore_configuration.ts"), ({resolveExploreConfiguration}) => resolveExploreConfiguration)],
    ["explore.configuration.plan", lazyHandler(() => import("./capabilities/explore_configuration.ts"), ({planExploreConfiguration}) => planExploreConfiguration)],
    ["explore.result.normalize", lazyHandler(() => import("./capabilities/explore_result_writeback.ts"), ({normalizeExploreResultAttachment}) => normalizeExploreResultAttachment)],
    ["explore.result.intent", lazyHandler(() => import("./capabilities/explore_result_writeback.ts"), ({produceExploreResultIntent}) => produceExploreResultIntent)],
    ["explore.turn_context", lazyHandler(() => import("./capabilities/explore_turn_context.ts"), ({projectExploreTurnContext}) => projectExploreTurnContext)],
    ["explore.research.normalize", lazyHandler(() => import("./capabilities/explore_research.ts"), ({normalizeResearchObservation}) => normalizeResearchObservation)],
    ["explore.research.validate_attribution", lazyHandler(() => import("./capabilities/explore_research.ts"), ({validateResearchAttribution}) => validateResearchAttribution)],
    ["explore.research.frontier", lazyHandler(() => import("./capabilities/explore_research.ts"), ({projectResearchFrontier}) => projectResearchFrontier)],
  ["work_item.replan_history.project", lazyHandler(() => import("./work_items/replan_history_settlement.ts"), ({projectSettledReplanHistory}) => projectSettledReplanHistory)],
  ["work_item.replan_history.project_snapshot", lazyHandler(() => import("./work_items/replan_history_snapshot.ts"), ({projectReplanHistorySnapshot}) => projectReplanHistorySnapshot)],

    [
      "work_item.replan_settlement.reentry",
      lazyHandler(() => import("./work_items/replan_settlement.ts"), ({projectTodoLifecycleSettlementReentry}) => projectTodoLifecycleSettlementReentry),
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
