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
  | "canonical_update_retry" | "canonical_update_projection_pending";

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
      kind: "confirmation";
      attentionKind: "authority";
      interactionMode: "confirm_reject";
      decisions: readonly ["confirm", "reject"];
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
      resultKind: "rejected" | "simulation_completed" | "completed";
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

export type ActionReviewPlan = ActionReviewIdentity & ActionReviewState & {
  operationFrame?: OperationReviewFrame;
  reviewCardFrame?: ReviewCardFrame;
  /** Recover this exact canonical command; generating a new preview loses its receipt identity. */
  retryOriginal?: true;
};

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
  return { title, subtitle, focus, fields, warning };
}

export function compileOperationReviewFrame(proposalValue: unknown): OperationReviewFrame | undefined {
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
  if (lifecycleState === "awaiting_confirmation") {
    return {
      ...base,
      kind: "confirmation",
      attentionKind: "authority",
      interactionMode: "confirm_reject",
      decisions: ["confirm", "reject"],
    };
  }
  if (lifecycleState === "claimed") {
    return {
      ...base,
      kind: "pending",
      attentionKind: "progress",
      interactionMode: "inform",
    };
  }
  const outcome = objectValue(operation.outcome);
  if (!outcome) return undefined;
  const rejected = outcome.outcome === "rejected_by_operator";
  const simulated = outcome.simulation === true || base.simulated;
  return {
    ...base,
    kind: "result",
    attentionKind: "progress",
    interactionMode: "inform",
    resultKind: rejected ? "rejected" : simulated ? "simulation_completed" : "completed",
    resultDeliveryVerified: objectValue(operation.result_delivery) !== null,
    summary: textValue(outcome.summary) ?? "",
  };
}

/**
 * Compile provider-neutral presentation semantics from a typed action proposal.
 * This reducer owns no action authority and performs no external effects.
 */
export function compileActionReviewPlan(proposalValue: unknown): ActionReviewPlan {
  const proposal = objectValue(proposalValue) ?? {};
  const identity: ActionReviewIdentity = {
    schemaVersion: "action_review_plan_v0",
    proposalId: typeof proposal.proposal_id === "string" ? proposal.proposal_id : "",
    sourceFingerprint: typeof proposal.expected_state_fingerprint === "string"
      ? proposal.expected_state_fingerprint
      : "",
  };
  const operationFrame = compileOperationReviewFrame(proposal);
  const reviewCardFrame = compileReviewCardFrame(proposal);
  const finish = (state: ActionReviewState): ActionReviewPlan => ({
    ...identity,
    ...state,
    ...(operationFrame ? { operationFrame } : {}),
    ...(reviewCardFrame ? { reviewCardFrame } : {}),
  });
  const held = (
    interaction: "gated" | "refresh" | "repair" | "pending" | "completed" | "inactive",
    reason: ActionReviewReason,
  ): ActionReviewPlan => finish({ interaction, reason, canApply: false });
  const lifecycle = proposal.action_kind === "goal.lifecycle";
  // Lifecycle uses conservative fact precedence. Generic deferred proposals may
  // retain a historical gate; their existing status-based retry path is preserved.
  if ((lifecycle && proposal.gate != null) || proposal.status === "gated") return held("gated", "authority_gate");
  if ((lifecycle && proposal.stale != null) || proposal.status === "stale") return held("refresh", "stale_proposal");
  if (proposal.status === "applied") {
    const receipt = objectValue(proposal.receipt);
    return receipt?.projection_verified === true
      && (proposal.action_kind !== "operation.execute" || objectValue(objectValue(proposal.operation)?.result_delivery) !== null)
      ? held("completed", "readback_verified")
      : held("repair", "readback_unverified");
  }
  const basis = objectValue(proposal.canonical_update_basis);
  const parameters = objectValue(proposal.normalized_parameters);
  const isCanonicalUpdate = basis?.schema_version === "loopx_chat_canonical_update_basis_v0"
    && textValue(basis.provider_revision) !== null && textValue(basis.registry_sha256) !== null
    && ((proposal.action_kind === "todo.update")
      || (proposal.action_kind === "monitor.update" && ["pause", "resume", "edit"].includes(String(parameters?.operation))));
  if (isCanonicalUpdate && (proposal.status === "applying" || proposal.status === "failed")) {
    const failure = objectValue(proposal.failure);
    return {...finish({interaction: "review", canApply: true,
      reason: failure?.error_code === "canonical_update_projection_pending"
        ? "canonical_update_projection_pending" : "canonical_update_retry"}), retryOriginal: true};
  }
  if (proposal.status === "applying") return held("pending", "apply_pending");
  if (proposal.status === "failed" || proposal.error != null) return held("repair", "apply_failed");
  if (proposal.status !== "preview_ready" && proposal.status !== "deferred") return held("inactive", "inactive_proposal");
  const reviewed = (reason: ActionReviewReason, canApply = true): ActionReviewPlan =>
    finish({ interaction: "review", reason, canApply });
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
