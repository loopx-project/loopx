import type {DecisionOutcome, ResumeState} from "../todos/user_completion_types.js";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {goalCreationOutcome, type GoalCreationOutcome} from "../goals/creation_outcome.ts";

export type ActionReviewIdentity = {
  schemaVersion: "action_review_plan_v0";
  proposalId: string;
  sourceFingerprint: string;
};

export type ActionReviewReason =
  | "ready_stop" | "resume_review" | "delete_review" | "action_review"
  | "protected_action" | "unknown_permission" | "unknown_action"
  | "incomplete_proposal" | "authority_gate" | "stale_proposal"
  | "apply_pending" | "readback_verified" | "readback_unverified"
  | "apply_failed" | "inactive_proposal"
  | "operation_authorization_pending" | "operation_outcome_pending"
  | "operation_confirmation_expired" | "operation_expiry_unknown" | "team_plan_retry"
  | "canonical_update_retry" | "canonical_update_projection_pending" | "goal_creation_retry" | "task_start_retry" | "todo_creation_retry";

export type OperationReviewContent = {
  title: string;
  subtitle: string;
  focus: string;
  fields: Array<{ label: string; value: string }>;
  warning: string;
};

type OperationReviewFrameBase = {
  schemaVersion: "operation_review_frame_v0";
  operationId: string;
  confirmationDigest: string;
  lifecycleState: "awaiting_confirmation" | "claimed" | "outcome_observed";
  simulated: boolean;
  expiresAt: string;
  content: OperationReviewContent;
};

export type OperationReviewFrame = OperationReviewFrameBase & (
  | {
      kind: "inactive";
      attentionKind: "progress";
      interactionMode: "inform";
      reason: "operation_confirmation_expired" | "operation_expiry_unknown";
    }
  | {
      kind: "confirmation";
      attentionKind: "authority";
      interactionMode: "confirm_reject";
      decisions: readonly ["confirm", "reject"];
      /** Canonical transport receipt, not proof of confirmation or execution. */
      confirmationDeliveryVerified: boolean;
    }
  | {
      kind: "pending";
      attentionKind: "progress";
      interactionMode: "inform";
      executionState?: "host_authentication_required" | "managed_turn_pending" | "managed_turn_started" | "consumed_outcome_pending";
    }
  | {
      kind: "result";
      attentionKind: "progress";
      interactionMode: "inform";
      resultKind: "rejected" | "cancelled" | "simulation_completed" | "completed" | "unknown" | "not_executed";
      resultDeliveryVerified: boolean;
      summary: string;
    }
);

type ActionReviewState =
  | { interaction: "direct"; reason: "ready_stop"; canApply: true }
  | { interaction: "review"; reason: ActionReviewReason; canApply: boolean }
  | {
      interaction: "gated" | "refresh" | "repair" | "pending" | "completed" | "inactive";
      reason: ActionReviewReason;
      canApply: false;
    };

/** An owner decision on one User request, recorded by the canonical User completion owner. */
export type DecisionReviewFrame = {
  decision: DecisionOutcome;
  /**
   * What the canonical receipt says happened to the work waiting on it. Only
   * present after a verified apply; an unrecognized or mismatched receipt is
   * `unknown`, never a guessed success.
   */
  dependentEffect?: "resumed" | "still_waiting" | "paused" | "no_waiting_work" | "unknown";
};

export type ActionReviewPlan = ActionReviewIdentity & ActionReviewState & {
  operationFrame?: OperationReviewFrame;
  reviewCardFrame?: ReviewCardFrame;
  decisionFrame?: DecisionReviewFrame;
  /** Recover this exact canonical command; generating a new preview loses its receipt identity. */
  retryOriginal?: true;
  /** A saved create attempt is never replaced by implicit regeneration. */
  prohibitRegeneration?: true;
  /** Recorded creation effects, not evidence of a completed Goal or model Turn. */
  creationProgress?: GoalCreationProgress;
  creationOutcome?: GoalCreationOutcome;
  /** Accepted dispatch bound to its original Turn. It does not establish a result. */
  taskExecution?: { goalId: string; todoId: string; sessionId: string; turnId: string };
};

export type GoalCreationProgress = {
  workspaceDigest: string;
  goalId?: string;
  agentId?: string;
  todoIds?: string[];
  completionBasis?: {operationId: string; providerRevision: string};
  completionRecorded?: true;
  sessionId?: string;
  firstTurn?: {sessionId: string; turnId: string};
};

function goalCreationProgress(proposal: Record<string, unknown>): GoalCreationProgress | undefined {
  if (proposal.action_kind !== "goal.create") return undefined;
  const parameters = objectValue(proposal.normalized_parameters);
  const steps = objectValue(objectValue(proposal.checkpoint)?.steps);
  const workspace = objectValue(steps?.workspace_validated);
  const digest = textValue(workspace?.workspace_digest);
  const goalId = textValue(parameters?.goal_id);
  if (!goalId || workspace?.outcome !== "workspace_validated" || !digest || !BARE_SHA256_PATTERN.test(digest)) return undefined;
  const progress: GoalCreationProgress = {workspaceDigest: digest};
  const goal = objectValue(steps?.goal_bootstrapped);
  const agent = objectValue(steps?.agent_bound);
  const todos = objectValue(steps?.todos_created);
  const session = objectValue(steps?.first_session_opened);
  const turn = objectValue(steps?.first_turn_started);
  if (steps?.goal_bootstrapped != null) {
    if (goal?.outcome !== "goal_bootstrapped" || goal.goal_id !== goalId) return undefined;
    progress.goalId = goalId;
  }
  if (steps?.agent_bound != null) {
    const agentId = textValue(parameters?.agent_id);
    if (!progress.goalId || !agentId || agent?.outcome !== "agent_bound"
        || agent.goal_id !== goalId || agent.agent_id !== agentId) return undefined;
    progress.agentId = agentId;
  }
  if (steps?.todos_created != null) {
    const ids = todos?.todo_ids;
    if (!progress.goalId || (textValue(parameters?.agent_id) && !progress.agentId)
        || todos?.outcome !== "todos_created" || !Array.isArray(ids)
        || ids.length !== (Array.isArray(parameters?.initial_todos) ? parameters.initial_todos.length : 0)
        || !ids.every(id => textValue(id) !== null) || new Set(ids).size !== ids.length) return undefined;
    progress.todoIds = ids as string[];
  }
  const completion = objectValue(steps?.goal_completion_prepared);
  const recorded = objectValue(steps?.goal_completion_recorded);
  if (steps?.goal_completion_prepared != null) {
    const declaration = goalCreationOutcome(String(proposal.proposal_id), parameters!, progress.todoIds ?? []);
    const providerRevision = textValue(completion?.provider_revision);
    if (!progress.todoIds || !declaration || completion?.outcome !== "goal_completion_prepared"
        || completion.goal_id !== goalId || completion.operation_id !== declaration.operationId || !providerRevision) return undefined;
    progress.completionBasis = {operationId: declaration.operationId, providerRevision};
  }
  if (steps?.goal_completion_recorded != null) {
    if (!progress.completionBasis || recorded?.outcome !== "goal_completion_recorded"
        || recorded.goal_id !== goalId || recorded.operation_id !== progress.completionBasis.operationId
        || !textValue(recorded.provider_revision)) return undefined;
    progress.completionRecorded = true;
  }
  if ((session || turn) && progress.completionBasis && !progress.completionRecorded) return undefined;
  if (steps?.first_turn_started != null) {
    const sessionId = textValue(turn?.session_id), turnId = textValue(turn?.turn_id);
    if (!progress.agentId || !progress.todoIds || turn?.outcome !== "first_turn_started" || !sessionId || !turnId
        || (steps?.first_session_opened != null && session?.session_id !== sessionId)) return undefined;
    progress.firstTurn = {sessionId, turnId};
  }
  if (steps?.first_session_opened != null) {
    const sessionId = textValue(session?.session_id);
    if (!progress.agentId || !progress.todoIds || session?.outcome !== "first_session_opened" || !sessionId) return undefined;
    progress.sessionId = sessionId;
  }
  return progress;
}

/**
 * Provider-neutral content for a confirmation card on a surface that is not the
 * Dashboard, such as a Lark Card 2.0.
 *
 * The operation frame above can only describe an `operation.execute` proposal,
 * because its identity is the operation envelope. A plan has no envelope: what
 * makes its confirmation exact is the action proposal and the state fingerprint
 * the apply re-validates against, so that pair is the frame's identity. Labels
 * stay keys, not sentences, because this boundary is language-neutral; the
 * surface owns the words and renders the data below.
 */
type ReviewCardFrameBase = {
  schemaVersion: "review_card_frame_v0";
  actionKind: string;
  proposalId: string;
  stateFingerprint: string;
  titleKey: string;
  subtitleKey: string;
  warningKey: string;
  focus: string;
  fields: Array<{ key: string; value: string }>;
};

export type ReviewCardFrame = ReviewCardFrameBase & (
  | {
      kind: "confirmation";
      attentionKind: "authority";
      interactionMode: "confirm_reject";
      decisions: readonly ["confirm", "reject"];
      confirmLabelKey: string;
      rejectLabelKey: string;
    }
  | {
      kind: "pending";
      attentionKind: "progress";
      interactionMode: "inform";
    }
  | {
      kind: "result";
      attentionKind: "progress";
      interactionMode: "inform";
      resultKind: "applied" | "rejected" | "stale" | "failed" | "inactive";
      resultSummary: string;
    }
);

type JsonRecord = Record<string, unknown>;

const lifecycleReviewReasons = {
  stop: "ready_stop",
  resume: "resume_review",
  delete: "delete_review",
} as const;

function objectValue(value: unknown): JsonRecord | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as JsonRecord
    : null;
}

function textValue(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function compactValue(value: unknown, limit = 240): string {
  const text = typeof value === "string" ? value.replace(/\s+/g, " ").trim() : "";
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

function laneFieldValue(laneValue: unknown): string {
  const lane = objectValue(laneValue) ?? {};
  const agent = compactValue(lane.agent_id, 80) || "unknown-agent";
  const acceptance = compactValue(lane.acceptance, 200);
  if (lane.staffing === "gap") {
    const declined = objectValue(lane.declined_first_todo) ?? {};
    return [
      `${agent} · gap`,
      compactValue(lane.gap_reason_code, 80),
      compactValue(declined.text, 200),
    ].filter(Boolean).join(" · ");
  }
  const todo = objectValue(lane.first_todo) ?? {};
  return [
    `${agent} · ready`,
    compactValue(todo.priority, 8),
    compactValue(todo.action_kind, 40),
    compactValue(todo.text, 240),
    acceptance ? `acceptance: ${acceptance}` : "",
  ].filter(Boolean).join(" · ");
}

function envelopeFieldValue(value: unknown): string {
  const envelope = objectValue(value) ?? {};
  return Object.entries(envelope)
    .map(([key, item]) => `${key}: ${typeof item === "object" && item !== null ? JSON.stringify(item) : String(item)}`)
    .join(" · ");
}

/**
 * Compile the confirmation frame for a validated steward team plan.
 *
 * Returns `undefined` for anything else, so a surface asks for a plan card only
 * when the proposal is one, and gets the same silence for a proposal whose plan
 * is not a preview. The plan below is data the model wrote; the frame copies it
 * as values and never as instructions.
 */
export function compileReviewCardFrame(proposalValue: unknown): ReviewCardFrame | undefined {
  const proposal = objectValue(proposalValue);
  if (proposal?.action_kind !== "team.plan") return undefined;
  const parameters = objectValue(proposal.normalized_parameters);
  const plan = objectValue(parameters?.plan);
  if (!plan || plan.kind !== "steward_team_plan_preview" || plan.applies !== false) return undefined;
  const proposalId = textValue(proposal.proposal_id);
  const stateFingerprint = textValue(proposal.expected_state_fingerprint);
  const goalId = textValue(plan.goal_id);
  if (!proposalId || !stateFingerprint || !goalId) return undefined;
  const lanes = Array.isArray(plan.lanes) ? plan.lanes : [];
  const gaps = Array.isArray(plan.gaps) ? plan.gaps : [];
  const fields = [
    { key: "goal", value: goalId },
    { key: "objective", value: compactValue(plan.objective) },
    ...lanes.map((lane, index) => ({ key: `lane_${index + 1}`, value: laneFieldValue(lane) })),
    ...(gaps.length > 0
      ? [{
        key: "lane_gaps",
        value: gaps
          .map((gapValue) => {
            const gap = objectValue(gapValue) ?? {};
            return [compactValue(gap.lane_id, 80), compactValue(gap.reason_code, 80)].filter(Boolean).join(": ");
          })
          .filter(Boolean)
          .join(" · "),
      }]
      : []),
    { key: "quota_envelope", value: envelopeFieldValue(plan.quota_envelope) },
    { key: "stop_condition", value: compactValue(plan.stop_condition) },
  ].filter((field) => field.value.length > 0);
  const base: ReviewCardFrameBase = {
    schemaVersion: "review_card_frame_v0",
    actionKind: "team.plan",
    proposalId,
    stateFingerprint,
    titleKey: "team_plan_preview",
    subtitleKey: "preview_only_no_lane_exists",
    warningKey: "confirming_creates_each_ready_lane_first_todo",
    focus: `${goalId} · ${lanes.length} lane${lanes.length === 1 ? "" : "s"}`,
    fields,
  };
  if (proposal.status === "preview_ready" || proposal.status === "deferred") {
    return {
      ...base,
      kind: "confirmation",
      attentionKind: "authority",
      interactionMode: "confirm_reject",
      decisions: ["confirm", "reject"],
      confirmLabelKey: "confirm_team_plan",
      rejectLabelKey: "reject_team_plan",
    };
  }
  if (proposal.status === "applying") {
    return {
      ...base,
      kind: "pending",
      attentionKind: "progress",
      interactionMode: "inform",
    };
  }
  const receipt = objectValue(proposal.receipt);
  const failure = objectValue(proposal.failure);
  const resultKind = proposal.status === "applied"
    ? "applied"
    : proposal.status === "rejected"
    ? "rejected"
    : proposal.status === "stale"
    ? "stale"
    : proposal.status === "failed"
    ? "failed"
    : "inactive";
  return {
    ...base,
    kind: "result",
    attentionKind: "progress",
    interactionMode: "inform",
    resultKind,
    resultSummary: compactValue(
      receipt?.outcome ?? failure?.error_code ?? proposal.status,
      160,
    ),
  };
}

function operationContent(parameters: JsonRecord): OperationReviewContent | null {
  const projection = objectValue(parameters.projection);
  if (projection?.schema_version !== "loopx_operation_projection_v0") return null;
  const title = textValue(projection.title);
  const subtitle = textValue(projection.subtitle);
  const focus = textValue(projection.focus);
  const warning = textValue(projection.warning);
  if (!title || !subtitle || !focus || !warning || !Array.isArray(projection.fields)) return null;
  const fields: Array<{ label: string; value: string }> = [];
  for (const raw of projection.fields) {
    const field = objectValue(raw);
    const label = textValue(field?.label);
    const value = textValue(field?.value);
    if (!label || !value) return null;
    fields.push({ label, value });
  }
  const executor = objectValue(parameters.executor);
  if (executor?.kind === "managed_turn") {
    fields.push({label: "Executor / 执行者", value: `Managed Turn / 受管回合 · ${compactValue(executor.model, 80)}@${compactValue(executor.reasoning_effort, 20)}`});
    fields.push({label: "Scope / 范围", value: `${compactValue(parameters.agent_id, 80)} · ${compactValue(executor.todo_id, 80)}`});
    const source = objectValue(parameters.source_route);
    if (source) fields.push({label: "Source context / 来源上下文", value: `${compactValue(source.host_surface, 60)} · ${compactValue(source.agent_id, 80)}`});
  }
  return { title, subtitle, focus, fields, warning };
}

export function compileOperationReviewFrame(proposalValue: unknown, nowMs?: number): OperationReviewFrame | undefined {
  const proposal = objectValue(proposalValue);
  if (proposal?.action_kind !== "operation.execute") return undefined;
  const parameters = objectValue(proposal.normalized_parameters);
  const operation = objectValue(proposal.operation);
  if (!parameters || operation?.schema_version !== "loopx_operation_envelope_v0") return undefined;
  const operationId = textValue(operation.operation_id);
  const confirmationDigest = textValue(operation.confirmation_digest);
  const expiresAt = textValue(operation.expires_at);
  const content = operationContent(parameters);
  if (!operationId || !confirmationDigest || !expiresAt || !content) return undefined;
  if (operationId !== proposal.proposal_id) return undefined;
  const lifecycleState = operation.lifecycle_state;
  if (
    lifecycleState !== "awaiting_confirmation"
    && lifecycleState !== "claimed"
    && lifecycleState !== "outcome_observed"
  ) return undefined;
  const projection = objectValue(parameters.projection)!;
  const base: OperationReviewFrameBase = {
    schemaVersion: "operation_review_frame_v0",
    operationId,
    confirmationDigest,
    lifecycleState,
    simulated: projection.simulated === true,
    expiresAt,
    content,
  };
  // Cancellation is an administrative terminal state, not a domain execution
  // outcome. Preserve the original envelope; never manufacture an execution
  // receipt or offer the old confirmation again.
  if (proposal.status === "cancelled") {
    return {...base, kind: "result", attentionKind: "progress", interactionMode: "inform",
      resultKind: "cancelled", resultDeliveryVerified: false, summary: ""};
  }
  if (lifecycleState === "awaiting_confirmation") {
    // An explicit read-time/fresh-delivery guard. Card reconstruction omits the
    // clock so delayed callbacks still reach canonical confirmed_at
    // validation. Never mutate the envelope or invent an outcome.
    const expiryMs = /(?:Z|[+-]\d{2}:\d{2})$/i.test(expiresAt) ? Date.parse(expiresAt) : NaN;
    if (nowMs !== undefined && (!Number.isFinite(expiryMs) || !Number.isFinite(nowMs) || nowMs > expiryMs)) {
      return {...base, kind: "inactive", attentionKind: "progress", interactionMode: "inform",
        reason: Number.isFinite(expiryMs) && Number.isFinite(nowMs)
          ? "operation_confirmation_expired" : "operation_expiry_unknown"};
    }
    const delivery = objectValue(operation.delivery);
    return {
      ...base,
      kind: "confirmation",
      attentionKind: "authority",
      interactionMode: "confirm_reject",
      decisions: ["confirm", "reject"],
      confirmationDeliveryVerified: delivery?.provider === "lark"
        && ["message_id", "chat_id", "app_id", "binding_digest", "card_digest", "delivered_at"]
          .every(key => textValue(delivery[key]) !== null),
    };
  }
  if (lifecycleState === "claimed") {
    return {
      ...base,
      kind: "pending",
      attentionKind: "progress",
      interactionMode: "inform",
      ...(["agent_session", "managed_turn"].includes(String(objectValue(parameters.executor)?.kind))
        ? {executionState: objectValue(operation.agent_handoff) ? "consumed_outcome_pending" as const
          : objectValue(parameters.executor)?.kind === "managed_turn"
          ? objectValue(operation.host_start)?.schema_version === "loopx_operation_host_start_v0"
            ? "managed_turn_started" as const : "managed_turn_pending" as const
          : "host_authentication_required" as const}
        : {}),
    };
  }
  const outcome = objectValue(operation.reconciliation) ?? objectValue(operation.outcome);
  if (!outcome) return undefined;
  const rejected = outcome.outcome === "rejected_by_operator";
  const simulated = outcome.simulation === true || base.simulated;
  return {
    ...base,
    kind: "result",
    attentionKind: "progress",
    interactionMode: "inform",
    resultKind: outcome.outcome === "submission_unknown" ? "unknown" : outcome.outcome === "not_executed" ? "not_executed"
      : rejected ? "rejected" : simulated ? "simulation_completed" : "completed",
    resultDeliveryVerified: objectValue(operation.result_delivery) !== null
      && (!["agent_session", "managed_turn"].includes(String(objectValue(parameters.executor)?.kind))
        || objectValue(operation.result_delivery)?.outcome_stage === (operation.reconciliation ? "reconciled" : "initial")),
    summary: textValue(outcome.summary) ?? "",
  };
}

// Presentation buckets over `ResumeState` in control_plane/todos/user_completion.ts.
const DEPENDENT_EFFECTS = {
  resumed: "resumed",
  decision_requirements_remaining: "still_waiting",
  other_user_blockers_active: "still_waiting",
  explicit_blocker_repair_required: "still_waiting",
  decision_rejected: "paused",
  decision_cancelled: "paused",
  target_not_blocked: "no_waiting_work",
  target_not_active: "no_waiting_work",
  target_not_found: "no_waiting_work",
  target_or_decision_scope_not_found: "unknown",
} as const satisfies Readonly<Record<ResumeState, NonNullable<DecisionReviewFrame["dependentEffect"]>>>;

function compileDecisionReviewFrame(proposal: Record<string, unknown>): DecisionReviewFrame | undefined {
  if (proposal.action_kind !== "gate.resolve") return undefined;
  const decision = objectValue(proposal.normalized_parameters)?.decision;
  if (decision !== "approve" && decision !== "reject" && decision !== "cancel") return undefined;
  const receipt = objectValue(proposal.receipt);
  if (proposal.status !== "applied" || receipt?.projection_verified !== true) return { decision };
  if (receipt.outcome !== "gate_resolved" || receipt.decision_outcome !== decision
      || !Object.hasOwn(receipt, "unblock_resume_state")) return { decision, dependentEffect: "unknown" };
  const state = receipt.unblock_resume_state;
  return { decision, dependentEffect: typeof state === "string" && Object.hasOwn(DEPENDENT_EFFECTS, state)
    ? DEPENDENT_EFFECTS[state as keyof typeof DEPENDENT_EFFECTS] : "unknown" };
}

function taskExecutionReadback(
  proposal: Record<string, unknown>, receipt: Record<string, unknown>, sourceFingerprint: string,
): ActionReviewPlan["taskExecution"] {
  const parameters = objectValue(proposal.normalized_parameters);
  if (proposal.action_kind !== "todo.create" || parameters?.start_execution !== true
      || receipt.outcome !== "task_execution_started") return undefined;
  const resources = objectValue(receipt.resource_ids);
  const canonical = objectValue(receipt.canonical_receipt);
  const goalId = textValue(parameters.goal_id);
  const todoId = textValue(resources?.todo_id);
  const sessionId = textValue(resources?.session_id);
  const turnId = textValue(resources?.turn_id);
  if (!goalId || !todoId || !sessionId || !turnId
      || resources?.goal_id !== goalId || canonical?.goal_id !== goalId || canonical?.todo_id !== todoId
      || canonical?.status !== "applied" || !sourceFingerprint || canonical?.preview_id !== sourceFingerprint) return undefined;
  return {goalId, todoId, sessionId, turnId};
}

/**
 * Compile provider-neutral presentation semantics from a typed action proposal.
 * This reducer owns no action authority and performs no external effects.
 */
export function compileActionReviewPlan(proposalValue: unknown, nowMs?: number): ActionReviewPlan {
  const proposal = objectValue(proposalValue) ?? {};
  const identity: ActionReviewIdentity = {
    schemaVersion: "action_review_plan_v0",
    proposalId: typeof proposal.proposal_id === "string" ? proposal.proposal_id : "",
    sourceFingerprint: typeof proposal.expected_state_fingerprint === "string"
      ? proposal.expected_state_fingerprint
      : "",
  };
  const operationFrame = compileOperationReviewFrame(proposal, nowMs);
  const reviewCardFrame = compileReviewCardFrame(proposal);
  const decisionFrame = compileDecisionReviewFrame(proposal);
  const steps = objectValue(objectValue(proposal.checkpoint)?.steps);
  const createPlan = objectValue(steps?.todo_create_prepared);
  const todoCreation = proposal.action_kind === "todo.create" || proposal.action_kind === "monitor.create";
  const prohibitRegeneration = todoCreation && Boolean(createPlan || objectValue(steps?.todo_created));
  const creationProgress = goalCreationProgress(proposal);
  // Older already-dispatched creations retain their original semantics. A
  // failed write retries its original CAS even after a response was lost.
  const creationOutcome = creationProgress?.todoIds && (!creationProgress.sessionId && !creationProgress.firstTurn
      || creationProgress.completionBasis)
    ? goalCreationOutcome(identity.proposalId, objectValue(proposal.normalized_parameters) ?? {}, creationProgress.todoIds) : undefined;
  const finish = (state: ActionReviewState): ActionReviewPlan => ({
    ...identity,
    ...state,
    ...(operationFrame ? { operationFrame } : {}),
    ...(reviewCardFrame ? { reviewCardFrame } : {}),
    ...(decisionFrame ? { decisionFrame } : {}),
    ...(creationProgress ? { creationProgress } : {}),
    ...(prohibitRegeneration ? {prohibitRegeneration: true as const} : {}),
    ...(creationOutcome ? { creationOutcome } : {}),
  });
  const held = (
    interaction: "gated" | "refresh" | "repair" | "pending" | "completed" | "inactive",
    reason: ActionReviewReason,
  ): ActionReviewPlan => finish({ interaction, reason, canApply: false });
  const lifecycle = proposal.action_kind === "goal.lifecycle";
  if (operationFrame?.kind === "inactive") return held("inactive", operationFrame.reason);
  // Lifecycle uses conservative fact precedence. Generic deferred proposals may
  // retain a historical gate; their existing status-based retry path is preserved.
  if ((lifecycle && proposal.gate != null) || proposal.status === "gated") return held("gated", "authority_gate");
  if ((lifecycle && proposal.stale != null) || proposal.status === "stale") return held("refresh", "stale_proposal");
  // Host readback may return an immutable terminal replay which never started.
  // Its acceptance id is still original, but cannot certify first-Turn startup.
  const firstTurnReadback = objectValue(proposal.first_turn_readback);
  if (proposal.action_kind === "goal.create" && firstTurnReadback
      && (textValue(firstTurnReadback.completed_at) !== null || textValue(firstTurnReadback.error_code) !== null)
      && textValue(firstTurnReadback.started_at) === null && textValue(firstTurnReadback.upstream_turn_id) === null) {
    return held("repair", "readback_unverified");
  }
  if (proposal.status === "applied") {
    if (operationFrame?.kind === "result" && operationFrame.resultKind === "unknown") {
      return held("repair", "readback_unverified");
    }
    const receipt = objectValue(proposal.receipt);
    if (receipt?.projection_verified !== true
        || (proposal.action_kind === "operation.execute"
          && (operationFrame?.kind !== "result" || !operationFrame.resultDeliveryVerified))) {
      return held("repair", "readback_unverified");
    }
    const taskExecution = taskExecutionReadback(proposal, receipt, identity.sourceFingerprint);
    const completed = held("completed", "readback_verified");
    return taskExecution ? {...completed, taskExecution} : completed;
  }
  const basis = objectValue(proposal.canonical_update_basis);
  const parameters = objectValue(proposal.normalized_parameters);
  // Recovery keeps the frozen workspace and committed effects. The server
  // revalidates their identity and reuses native Todo/Turn idempotency owners.
  if (proposal.action_kind === "goal.create" && ["failed", "applying"].includes(String(proposal.status))
      && proposal.permission_classification === "durable_write"
      && identity.proposalId && identity.sourceFingerprint && textValue(parameters?.goal_id)
      && creationProgress) {
    return {...finish({interaction: "review", canApply: true, reason: "goal_creation_retry"}), retryOriginal: true};
  }
  if (proposal.action_kind === "todo.create" && parameters?.start_execution === true
      && proposal.permission_classification === "durable_write" && identity.proposalId && identity.sourceFingerprint
      && (proposal.status === "applying" || (proposal.status === "failed" && objectValue(proposal.failure)?.retry_safe === true))) {
    const created = objectValue(steps?.todo_created);
    const receipt = objectValue(created?.canonical_receipt);
    if (textValue(created?.todo_id) && created?.todo_id === receipt?.todo_id
        && textValue(parameters.goal_id) && receipt?.goal_id === parameters.goal_id
        && receipt?.preview_id === identity.sourceFingerprint && receipt.status === "applied") {
      return {...finish({interaction: "review", canApply: true, reason: "task_start_retry"}), retryOriginal: true};
    }
  }
  // A confirmation with a saved create plan may already have written. Only a
  // complete plan for this exact proposal can offer native recovery on reload.
  if (todoCreation && ["failed", "applying"].includes(String(proposal.status))
      && proposal.permission_classification === "durable_write"
      && identity.proposalId && identity.sourceFingerprint && textValue(parameters?.goal_id)
      && createPlan?.operation_id === ("chat-create:" + identity.proposalId)
      && createPlan.preview_id === identity.sourceFingerprint
      && textValue(createPlan.source_identity) && textValue(createPlan.source_authority)
      && textValue(createPlan.expected_provider_revision) && textValue(createPlan.expected_registry_sha256)
      && textValue(createPlan.expected_store_identity)) {
    return {...finish({interaction: "review", canApply: true, reason: "todo_creation_retry"}), retryOriginal: true};
  }
  const isCanonicalUpdate = basis?.schema_version === "loopx_chat_canonical_update_basis_v0"
    && textValue(basis.provider_revision) !== null && textValue(basis.registry_sha256) !== null
    && ((proposal.action_kind === "todo.update")
      || (proposal.action_kind === "monitor.update" && ["pause", "resume", "edit"].includes(String(parameters?.operation))));
  const isCanonicalTerminal = basis?.schema_version === "loopx_chat_canonical_terminal_basis_v0"
    && textValue(basis.provider_revision) !== null && textValue(basis.registry_sha256) !== null
    && ((proposal.action_kind === "todo.update" && parameters?.operation === "complete")
      || (proposal.action_kind === "monitor.update" && parameters?.operation === "stop")
      || (proposal.action_kind === "gate.resolve" && ["approve", "reject", "cancel"].includes(String(parameters?.decision))));
  if ((isCanonicalUpdate || isCanonicalTerminal) && (proposal.status === "applying" || proposal.status === "failed")) {
    const failure = objectValue(proposal.failure);
    return {...finish({interaction: "review", canApply: true,
      reason: failure?.error_code === "canonical_update_projection_pending"
        ? "canonical_update_projection_pending" : "canonical_update_retry"}), retryOriginal: true};
  }
  const teamPlanFrame = proposal.action_kind === "team.plan" ? reviewCardFrame : undefined;
  const teamPlanFailure = objectValue(proposal.failure);
  const teamPlanGoalId = textValue(parameters?.goal_id);
  const teamPlanContext = objectValue(proposal.context);
  if (proposal.schema_version === "loopx_chat_action_proposal_v1"
      && proposal.action_kind === "team.plan" && proposal.status === "failed"
      && proposal.permission_classification === "durable_write"
      && teamPlanFailure?.retry_safe === true
      && identity.proposalId && identity.sourceFingerprint
      && Array.isArray(proposal.available_transitions)
      && proposal.available_transitions.includes("apply")
      && teamPlanFrame?.kind === "result" && teamPlanFrame.resultKind === "failed"
      && teamPlanGoalId
      && textValue(objectValue(parameters?.plan)?.goal_id) === teamPlanGoalId
      && (teamPlanContext?.goal_id == null || teamPlanContext.goal_id === teamPlanGoalId)) {
    return {...finish({interaction: "review", canApply: true, reason: "team_plan_retry"}), retryOriginal: true};
  }
  if (proposal.status === "applying") {
    if (operationFrame?.kind === "pending" && operationFrame.executionState) {
      return held("pending", operationFrame.executionState === "consumed_outcome_pending"
        ? "operation_outcome_pending" : "operation_authorization_pending");
    }
    return held("pending", "apply_pending");
  }
  if (proposal.status === "failed" || proposal.error != null) return held("repair", "apply_failed");
  if (proposal.status !== "preview_ready" && proposal.status !== "deferred") return held("inactive", "inactive_proposal");
  const reviewed = (reason: ActionReviewReason, canApply = true): ActionReviewPlan =>
    finish({ interaction: "review", reason, canApply });
  if (proposal.action_kind === "goal.acceptance.check") {
    const context = objectValue(proposal.context);
    const goalId = textValue(parameters?.goal_id);
    if (!goalId || context?.kind !== "goal" || context.goal_id !== goalId
        || !objectValue(parameters?.document) || !identity.proposalId || !identity.sourceFingerprint) {
      return held("refresh", "incomplete_proposal");
    }
    return reviewed(proposal.permission_classification === "local_command_execution"
      ? "action_review" : "unknown_permission", proposal.permission_classification === "local_command_execution");
  }
  if (proposal.action_kind !== "goal.lifecycle") {
    return reviewed(proposal.permission_classification === "protected" ? "protected_action" : "action_review");
  }
  const evidence = proposal.validation_evidence;
  const transitions = proposal.available_transitions;
  const complete = textValue(proposal.proposal_id) !== null
    && textValue(proposal.expected_state_fingerprint) !== null
    && Array.isArray(evidence)
    && evidence.length > 0
    && evidence.every((item) => textValue(item) !== null)
    && Array.isArray(transitions)
    && transitions.includes("apply");
  if (!complete) return held("refresh", "incomplete_proposal");
  const context = objectValue(proposal.context);
  const operation = parameters?.operation;
  const goalId = textValue(parameters?.goal_id);
  if (!goalId || (context?.goal_id != null && context.goal_id !== goalId)) return held("refresh", "incomplete_proposal");
  if (operation !== "stop" && operation !== "resume" && operation !== "delete") return reviewed("unknown_action", false);
  if (proposal.permission_classification === "protected") return reviewed("protected_action");
  if (proposal.permission_classification !== "durable_write") return reviewed("unknown_permission", false);
  const reason = lifecycleReviewReasons[operation];
  if (reason === "ready_stop" && proposal.status === "preview_ready") {
    return finish({ interaction: "direct", reason, canApply: true });
  }
  return reviewed(reason === "ready_stop" ? "action_review" : reason);
}

/** The existing Chat error envelope, not translated prose, identifies stale state. */
export function isStaleActionFailure(payload: Record<string, unknown>): boolean {
  if (payload.error_code === "action_stale" || payload.error_code === "action_conflict") return true;
  const proposal = objectValue(payload.proposal);
  return proposal?.status === "stale";
}
