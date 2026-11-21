import { z } from "zod";

const scalarSchema = z.union([z.string(), z.number(), z.boolean(), z.null()]);
const scalarRecordSchema = z.record(z.string(), scalarSchema);

export const goalChannelTodoSchema = z.object({
  todo_id: z.string().optional(),
  priority: z.string().optional(),
  status: z.string(),
  title: z.string(),
  claimed_by: z.string().optional(),
  task_class: z.string().optional(),
  action_kind: z.string().optional(),
});

export const goalChannelGateSchema = z.object({
  gate_id: z.string(),
  kind: z.string(),
  status: z.string(),
  blocks: z.array(z.string()).optional(),
});

export const goalChannelLeaseSchema = z.object({
  todo_id: z.string().optional(),
  owner_agent: z.string().optional(),
  status: z.string().optional(),
  lease_until: z.string().optional(),
  write_scope: z.array(z.string()).optional(),
});

export const goalChannelEventSchema = z.object({
  generated_at: z.string().optional(),
  classification: z.string().optional(),
  summary: z.string().optional(),
});

export const goalChannelSourceWarningSchema = z.object({
  kind: z.string().optional().default("warning"),
  message: z.union([z.string(), z.array(z.string())]).optional().default("compact source warning"),
}).passthrough();

export const goalChannelProjectionSchema = z.object({
  schema_version: z.literal("goal_channel_projection_v0"),
  mode: z.literal("read_only"),
  goal_id: z.string(),
  display_name: z.string(),
  generated_at: z.string().optional().nullable(),
  latest_status: z.string(),
  waiting_on: z.string(),
  next_action: z.string(),
  source_refs: z.record(z.string(), scalarSchema),
  decision_frame: z.object({
    user_action_required: z.boolean(),
    agent_action_required: z.boolean(),
    quiet_noop_allowed: z.boolean(),
  }),
  quota: scalarRecordSchema,
  user_todos: z.array(goalChannelTodoSchema).default([]),
  agent_todos: z.array(goalChannelTodoSchema).default([]),
  open_gates: z.array(goalChannelGateSchema).default([]),
  active_leases: z.array(goalChannelLeaseSchema).default([]),
  artifacts: z.array(scalarRecordSchema).default([]),
  recent_events: z.array(goalChannelEventSchema).default([]),
  source_warnings: z.array(goalChannelSourceWarningSchema).default([]),
  truth_contract: z.object({
    event_ledger_is_source_of_truth: z.boolean(),
    projection_is_writable: z.boolean(),
    recompute_rule: z.string(),
    write_authority: z.string(),
  }),
});

export type GoalChannelTodo = {
  todo_id?: string;
  priority?: string;
  status: string;
  title: string;
  claimed_by?: string;
  task_class?: string;
  action_kind?: string;
};

export type GoalChannelGate = {
  gate_id: string;
  kind: string;
  status: string;
  blocks?: string[];
};

export type GoalChannelLease = {
  todo_id?: string;
  owner_agent?: string;
  status?: string;
  lease_until?: string;
  write_scope?: string[];
};

export type GoalChannelEvent = {
  generated_at?: string;
  classification?: string;
  summary?: string;
};

export type GoalChannelProjection = {
  schema_version: "goal_channel_projection_v0";
  mode: "read_only";
  goal_id: string;
  display_name: string;
  generated_at?: string | null;
  latest_status: string;
  waiting_on: string;
  next_action: string;
  source_refs: Record<string, string | number | boolean | null>;
  decision_frame: {
    user_action_required: boolean;
    agent_action_required: boolean;
    quiet_noop_allowed: boolean;
  };
  quota: Record<string, string | number | boolean | null>;
  user_todos: GoalChannelTodo[];
  agent_todos: GoalChannelTodo[];
  open_gates: GoalChannelGate[];
  active_leases: GoalChannelLease[];
  artifacts: Array<Record<string, string | number | boolean | null>>;
  recent_events: GoalChannelEvent[];
  source_warnings: Array<Record<string, unknown> & { kind?: string; message?: string | string[] }>;
  truth_contract: {
    event_ledger_is_source_of_truth: boolean;
    projection_is_writable: boolean;
    recompute_rule: string;
    write_authority: string;
  };
};

export const sampleGoalChannelProjection: GoalChannelProjection = {
  schema_version: "goal_channel_projection_v0",
  mode: "read_only",
  goal_id: "demo-goal-channel",
  display_name: "Demo Goal Channel",
  generated_at: "2026-06-20T08:04:00Z",
  latest_status: "safe_side_path_running",
  waiting_on: "capability",
  next_action:
    "Keep the leased delivery claim visible, wait on the missing capability, repair workspace drift, then write a durable outcome.",
  source_refs: {
    status_generated_at: "2026-06-20T08:01:00Z",
    active_state_updated_at: "2026-06-20T08:00:00Z",
    latest_run_generated_at: "2026-06-20T08:02:00Z",
    review_packet_generated_at: "2026-06-20T08:03:00Z",
    event_ledger_source: "run_history",
    latest_delivery_outcome: "outcome_progress",
  },
  decision_frame: {
    user_action_required: true,
    agent_action_required: true,
    quiet_noop_allowed: false,
  },
  quota: {
    allowed_slots: "10",
    cadence_class: "active_work",
    latest_evidence_ref: "run:validated_progress_fixture",
    no_spend_for_cadence_change: true,
    override_policy: "fresh quota guard required",
    pause_policy: "control-plane policy only",
    reason: "synthetic fixture has quota",
    scheduler_reset_token: "fixture-reset-token",
    scheduler_rrule: "FREQ=MINUTELY;INTERVAL=3",
    spend_policy: "spend after validated writeback",
    spent_slots: "2",
    state: "eligible",
  },
  user_todos: [
    {
      todo_id: "todo_user_decision",
      priority: "P0",
      status: "open",
      task_class: "user_gate",
      action_kind: "approve_route",
      title: "Decide whether the gated delivery route may continue.",
    },
  ],
  agent_todos: [
    {
      todo_id: "todo_primary_route",
      priority: "P0",
      status: "open",
      claimed_by: "codex-main-control",
      task_class: "advancement_task",
      action_kind: "delivery",
      title: "Keep the primary delivery route visible while it waits.",
    },
    {
      todo_id: "todo_capability_wait",
      priority: "P0",
      status: "waiting",
      claimed_by: "codex-capability",
      task_class: "advancement_task",
      action_kind: "capability_wait",
      title: "Wait for the missing worker_bridge capability before resuming delivery.",
    },
    {
      todo_id: "todo_workspace_repair",
      priority: "P1",
      status: "open",
      claimed_by: "codex-workspace",
      task_class: "advancement_task",
      action_kind: "workspace_repair",
      title: "Repair the stale writable worktree before the next bounded write.",
    },
    {
      todo_id: "todo_side_fixture",
      priority: "P2",
      status: "open",
      claimed_by: "codex-side-bypass",
      task_class: "advancement_task",
      action_kind: "frontstage_render",
      title: "Render the productization frontstage fixture.",
    },
  ],
  open_gates: [
    {
      gate_id: "interaction_contract_user_channel",
      kind: "user_channel",
      status: "action_required",
      blocks: ["todo_user_decision"],
    },
    {
      gate_id: "capability_gate_worker_bridge",
      kind: "capability_wait",
      status: "waiting",
      blocks: ["todo_capability_wait", "todo_primary_route"],
    },
  ],
  active_leases: [
    {
      owner_agent: "codex-main-control",
      status: "hard_lease",
      todo_id: "todo_primary_route",
      lease_until: "2026-06-20T09:00:00Z",
      write_scope: ["apps/presentation/dashboard/src/views/frontstage-page.tsx"],
    },
    {
      owner_agent: "codex-capability",
      status: "soft_claim",
      todo_id: "todo_capability_wait",
      lease_until: "2026-06-20T08:30:00Z",
      write_scope: ["docs/product/roadmaps/frontstage-channel-lease-roadmap.md"],
    },
    {
      owner_agent: "codex-workspace",
      status: "soft_claim",
      todo_id: "todo_workspace_repair",
      lease_until: "2026-06-20T08:20:00Z",
      write_scope: ["worktree"],
    },
    {
      owner_agent: "codex-side-bypass",
      status: "soft_claim",
      todo_id: "todo_side_fixture",
    },
  ],
  artifacts: [
    {
      kind: "doc",
      label: "frontstage roadmap",
      path: "docs/product/roadmaps/frontstage-channel-lease-roadmap.md",
    },
    {
      kind: "local_state",
      label: "omitted private control-plane source",
    },
  ],
  recent_events: [
    {
      generated_at: "2026-06-20T08:03:00Z",
      classification: "delivery_outcome",
      summary: "latest delivery_outcome=outcome_progress stays visible without browser write authority",
    },
    {
      generated_at: "2026-06-20T08:02:00Z",
      classification: "validated_progress",
      summary: "frontstage fixture rendered from compact projection",
    },
    {
      generated_at: "2026-06-20T08:01:00Z",
      classification: "capability_wait",
      summary: "worker_bridge missing; capability-wait blocks primary delivery",
    },
    {
      generated_at: "2026-06-20T07:55:00Z",
      classification: "workspace_repair",
      summary: "writable worktree marked stale; repair before the next scoped write",
    },
    {
      generated_at: "2026-06-20T07:50:00Z",
      classification: "operator_gate_recorded",
      summary: "human decision stayed explicit",
    },
  ],
  source_warnings: [
    {
      kind: "raw_or_private_material_omitted",
      message:
        "raw/private-looking fields were omitted; inspect compact source references instead of copying raw material into the frontstage channel projection",
    },
  ],
  truth_contract: {
    event_ledger_is_source_of_truth: true,
    projection_is_writable: false,
    recompute_rule:
      "refresh from LoopX status/quota/run history; do not edit the channel projection as project truth",
    write_authority: "none",
  },
};

// ---------------------------------------------------------------------------
// Operator state semantics.
//
// Classification is exact and typed: only tokens from the shipped control-plane
// vocabularies (loopx/control_plane/work_items/delivery_outcome.py, todo
// action kinds, gate kinds, lease statuses) map to a tone. Unknown values stay
// neutral — absence of evidence is not success — and todo titles are display
// copy only, never classification input.
// ---------------------------------------------------------------------------

export type BadgeTone = "neutral" | "success" | "warning" | "info" | "danger";

export type OperatorStateSignal = {
  helper: string;
  label: string;
  tone: BadgeTone;
  value: string;
};

// DeliveryOutcome enum plus the shipped failure dispositions.
const DELIVERY_OUTCOME_TONES: Record<string, BadgeTone> = {
  outcome_progress: "success",
  primary_goal_outcome: "success",
  outcome_gap: "danger",
  delivery_failed: "danger",
  delivery_blocked: "danger",
  surface_only: "neutral",
  unknown: "neutral",
  not_configured: "neutral",
};

// Outcome tokens that carry valence when seen as event classifications.
const VALENCED_OUTCOME_TOKENS = new Set([
  "outcome_progress",
  "primary_goal_outcome",
  "outcome_gap",
  "delivery_failed",
  "delivery_blocked",
]);

const ACTION_KIND_TONES: Record<string, BadgeTone> = {
  workspace_repair: "warning",
  capability_wait: "warning",
  capability_repair: "warning",
};

const EVENT_CLASSIFICATION_TONES: Record<string, BadgeTone> = {
  validated_progress: "success",
  delivery_outcome: "info",
  operator_gate_recorded: "info",
  capability_wait: "warning",
  workspace_repair: "warning",
  outcome_progress: "success",
  primary_goal_outcome: "success",
  outcome_gap: "danger",
  delivery_failed: "danger",
  delivery_blocked: "danger",
};

const LEASE_STATUS_TONES: Record<string, BadgeTone> = {
  hard_lease: "success",
  active: "success",
  soft_claim: "info",
  claimed: "info",
  expired: "neutral",
  released: "neutral",
};

function normalizeToken(value: string | null | undefined): string {
  return (value ?? "").trim().toLowerCase();
}

export function deliveryOutcomeTone(outcome?: string | null): BadgeTone {
  return DELIVERY_OUTCOME_TONES[normalizeToken(outcome)] ?? "neutral";
}

export function actionKindTone(actionKind?: string | null): BadgeTone {
  return ACTION_KIND_TONES[normalizeToken(actionKind)] ?? "neutral";
}

export function eventClassificationTone(classification?: string | null): BadgeTone {
  return EVENT_CLASSIFICATION_TONES[normalizeToken(classification)] ?? "neutral";
}

export function leaseStatusTone(status?: string | null): BadgeTone {
  return LEASE_STATUS_TONES[normalizeToken(status)] ?? "neutral";
}

export function deriveOperatorStateSignals(
  projection: GoalChannelProjection,
): OperatorStateSignal[] {
  const outcomeRefValue = projection.source_refs.latest_delivery_outcome;
  const hasOutcomeRef = outcomeRefValue !== null && outcomeRefValue !== undefined;
  const outcomeRefToken = normalizeToken(
    hasOutcomeRef ? String(outcomeRefValue) : undefined,
  );
  const outcomeEvent = projection.recent_events.find((event) => {
    const classification = normalizeToken(event.classification);
    return classification === "delivery_outcome" || VALENCED_OUTCOME_TOKENS.has(classification);
  });
  const outcomeToken = hasOutcomeRef
    ? outcomeRefToken
    : normalizeToken(outcomeEvent?.classification) || undefined;
  const outcomeTone = !outcomeToken
    ? "neutral"
    : (DELIVERY_OUTCOME_TONES[outcomeToken] ?? "neutral");

  const lease =
    projection.active_leases.find((entry) => Boolean(entry.lease_until))
    ?? projection.active_leases[0];

  const capabilityTodo = projection.agent_todos.find((todo) => {
    const kind = normalizeToken(todo.action_kind);
    return kind === "capability_wait" || kind === "capability_repair";
  });
  const capabilityGate = projection.open_gates.find(
    (gate) => normalizeToken(gate.kind) === "capability_wait",
  );
  const waitingOnCapability = normalizeToken(projection.waiting_on) === "capability";

  const workspaceTodo = projection.agent_todos.find(
    (todo) => normalizeToken(todo.action_kind) === "workspace_repair",
  );
  const workspaceEvent = projection.recent_events.find(
    (event) => normalizeToken(event.classification) === "workspace_repair",
  );

  return [
    {
      label: "outcome",
      value: hasOutcomeRef
        ? String(outcomeRefValue)
        : (outcomeEvent?.classification ?? "not projected"),
      helper: outcomeEvent?.summary
        ?? "Delivery outcome stays in the compact event ledger, not in browser writes.",
      tone: outcomeTone,
    },
    {
      label: "lease",
      value: lease
        ? `${lease.status ?? "claim"} · ${lease.lease_until ?? "no expiry"}`
        : "no active lease",
      helper: lease?.write_scope?.length
        ? `scope: ${lease.write_scope.join(", ")}`
        : lease?.todo_id
          ? `todo: ${lease.todo_id}`
          : "Claim owners stay visible without granting browser write authority.",
      tone: lease ? leaseStatusTone(lease.status) : "neutral",
    },
    {
      label: "capability-wait",
      value: capabilityGate?.status
        ?? capabilityTodo?.status
        ?? (waitingOnCapability ? projection.waiting_on : "clear"),
      helper: capabilityTodo?.title
        ?? capabilityGate?.gate_id
        ?? "Missing capabilities stay explicit as wait/repair state.",
      tone:
        capabilityGate || capabilityTodo || waitingOnCapability
          ? "warning"
          : "success",
    },
    {
      label: "workspace-repair",
      value: workspaceTodo?.action_kind
        ?? workspaceEvent?.classification
        ?? "clear",
      helper: workspaceTodo?.title
        ?? workspaceEvent?.summary
        ?? "Workspace repair stays a projected agent lane, not a browser mutation.",
      tone: workspaceTodo || workspaceEvent ? "warning" : "success",
    },
  ];
}
