/** Composable 5-stage methodology operator kernel.
 * Centralizes activation isolation, canonical replay-safe evidence windows,
 * scope-tuple partitioning, bounded progressive cues, and non-escalation
 * invariants so methodology operators (Yardstick, Stride, Fidelity, Budgeted
 * Continuation, Counterfactual Continuation, and future search operators)
 * share one verified engineering foundation instead of bespoke wiring.
 * Pure and browser-safe: zero node:* imports and zero store/authority mutation. */
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {jsonObject, requireJsonObject, type JsonObject} from "../runtime_decode.ts";
import {parseTodoTimestampMicros} from "../runtime_timestamp.ts";
import {isExploreEvidenceRef} from "./explore_evidence_ref.ts";

export const STAGE_METHODOLOGY_CATALOG_SCHEMA = "stage_methodology_operator_catalog_v0";
export const STAGE_METHODOLOGY_VERIFICATION_SCHEMA = "stage_methodology_operator_verification_v0";

export const LOOPX_CONTROL_STAGES = [
  "admission",
  "compile_context",
  "execute_verify",
  "writeback_settle",
  "feedback_evolve",
] as const;
export type LoopxControlStage = (typeof LOOPX_CONTROL_STAGES)[number];

export const QUIET_TURN_POLICIES = [
  "silent_when_untriggered",
  "due_obligation_only",
  "explicit_invocation_only",
] as const;
export type QuietTurnPolicy = (typeof QUIET_TURN_POLICIES)[number];

export interface StageMethodologyOperatorDescriptor {
  operator_id: string;
  stage: LoopxControlStage;
  companion_stages: readonly LoopxControlStage[];
  enabled_gate: string;
  quiet_turn_policy: QuietTurnPolicy;
  scope_keys: readonly string[];
  typed_transition_schema: string;
  lightweight_guidance: string;
  rollback_contract: "stateless_disable_preserves_history";
  advisory_only: true;
  grants_execution_authority: false;
}

export interface CanonicalSourceSnapshot {
  event_count: number;
  event_ids_sha256: string;
}

export interface WorkIdentityTuple {
  goal_id: string;
  agent_id: string;
  todo_id: string;
}

const FORBIDDEN_AUTHORITY_KEYS = new Set([
  "should_run",
  "normal_delivery_allowed",
  "lease_granted",
  "gate_override",
  "admin_bypass",
  "execution_authorized",
]);

export const BUILTIN_STAGE_METHODOLOGY_OPERATORS: readonly StageMethodologyOperatorDescriptor[] = [
  {
    operator_id: "work_yardstick",
    stage: "execute_verify",
    companion_stages: ["compile_context", "writeback_settle", "feedback_evolve"],
    enabled_gate: "explore.yardstick_enabled",
    quiet_turn_policy: "silent_when_untriggered",
    scope_keys: ["goal_id", "agent_id", "todo_id", "node_id", "comparison_scope", "input_revision", "evaluation_ref"],
    typed_transition_schema: "work_yardstick_decision_v0",
    lightweight_guidance:
      "Match proxy screening to the accepted outcome under identical scope and input_revision before discarding a viable route.",
    rollback_contract: "stateless_disable_preserves_history",
    advisory_only: true,
    grants_execution_authority: false,
  },
  {
    operator_id: "stride_review",
    stage: "feedback_evolve",
    companion_stages: ["compile_context", "writeback_settle"],
    enabled_gate: "goal.stride_strategy",
    quiet_turn_policy: "due_obligation_only",
    scope_keys: ["goal_id", "reference_context_id", "reference_obligation_id"],
    typed_transition_schema: "stride_decision_v2",
    lightweight_guidance:
      "At an admitted replan boundary, compare continuing vs. switching routes by complete marginal cost and decisive evidence.",
    rollback_contract: "stateless_disable_preserves_history",
    advisory_only: true,
    grants_execution_authority: false,
  },
  {
    operator_id: "scoped_yardstick_fidelity",
    stage: "execute_verify",
    companion_stages: ["compile_context", "feedback_evolve"],
    enabled_gate: "explore.yardstick_enabled",
    quiet_turn_policy: "silent_when_untriggered",
    scope_keys: ["node_id", "metric", "unit", "higher_is_better", "comparison_scope", "input_revision", "evaluation_ref"],
    typed_transition_schema: "scoped_yardstick_fidelity_v0",
    lightweight_guidance:
      "Quantify multi-point rank concordance and frontier bias cliffs in this scope; audit unconfirmed proxy winners without global bans.",
    rollback_contract: "stateless_disable_preserves_history",
    advisory_only: true,
    grants_execution_authority: false,
  },
  {
    operator_id: "budgeted_continuation",
    stage: "feedback_evolve",
    companion_stages: ["admission", "compile_context"],
    enabled_gate: "explore.budgeted_continuation",
    quiet_turn_policy: "explicit_invocation_only",
    scope_keys: ["remaining_budget.wall_minutes", "confirmation.eval_duration_minutes", "incumbent.metric"],
    typed_transition_schema: "budgeted_continuation_plan_v0",
    lightweight_guidance:
      "Back-schedule authoritative confirmation from remaining wall/token/slot budgets; keep unknown resource bounds null rather than zero.",
    rollback_contract: "stateless_disable_preserves_history",
    advisory_only: true,
    grants_execution_authority: false,
  },
  {
    operator_id: "counterfactual_continuation",
    stage: "feedback_evolve",
    companion_stages: ["execute_verify"],
    enabled_gate: "explore.counterfactual_continuation",
    quiet_turn_policy: "explicit_invocation_only",
    scope_keys: ["checkpoint_id", "source_state_sha256", "input_revision", "evaluation_ref", "workspace_isolation_id"],
    typed_transition_schema: "counterfactual_continuation_eval_v0",
    lightweight_guidance:
      "Compare treatment and control from the exact frozen checkpoint digest in isolated workspaces; separate action divergence from outcome delta.",
    rollback_contract: "stateless_disable_preserves_history",
    advisory_only: true,
    grants_execution_authority: false,
  },
  {
    operator_id: "evolutionary_population",
    stage: "feedback_evolve",
    companion_stages: ["compile_context", "execute_verify"],
    enabled_gate: "explore.evolutionary_population",
    quiet_turn_policy: "explicit_invocation_only",
    scope_keys: ["node_id", "metric", "unit", "higher_is_better", "comparison_scope", "input_revision", "evaluation_ref"],
    typed_transition_schema: "evolutionary_population_plan_v0",
    lightweight_guidance:
      "Select mechanism-diverse elites, orthogonal crossover pairs, and bottleneck mutations on confirmed outcomes while quarantining Goodharted proxy-only candidates.",
    rollback_contract: "stateless_disable_preserves_history",
    advisory_only: true,
    grants_execution_authority: false,
  },
];

/** Canonical lowercase 64-hex SHA-256 validator shared across methodology operators. */
export function isValidContentSha256(value: unknown): value is string {
  return typeof value === "string" && BARE_SHA256_PATTERN.test(value);
}

/** Shared strict object-shape validator across methodology codecs. */
export function requireExactFields(
  value: unknown,
  allowedFields: readonly string[],
  requiredFields: readonly string[],
  label: string,
  errorMessage?: string,
): JsonObject {
  const row = requireJsonObject(value, label);
  if (
    Object.keys(row).some(key => !allowedFields.includes(key)) ||
    requiredFields.some(key => !Object.hasOwn(row, key))
  ) {
    throw new EffectRuntimeRequestError(errorMessage ?? `${label} fields are missing or unsupported`);
  }
  return row;
}

/** Shared bounded single-line text validator (rejects control characters and empty strings). */
export function requireBoundedSingleLine(value: unknown, label: string, maxChars = 160): string {
  if (
    typeof value !== "string" ||
    !value.trim() ||
    value.length > maxChars ||
    /[\u0000-\u001f]/.test(value)
  ) {
    throw new EffectRuntimeRequestError(`${label} requires bounded single-line text`);
  }
  return value.trim();
}

/** Shared opaque reference validator (rejects file: URIs and path traversal). */
export function requireOpaqueMethodologyRef(value: unknown, label: string, maxChars = 128): string {
  const trimmed = requireBoundedSingleLine(value, label, maxChars);
  if (
    !isExploreEvidenceRef(trimmed) ||
    trimmed.toLowerCase().startsWith("file:") ||
    trimmed.split("/").includes("..")
  ) {
    throw new EffectRuntimeRequestError(`${label} requires an opaque reference`);
  }
  return trimmed;
}

/** Shared bounded unique reference array validator. */
export function requireUniqueOpaqueRefs(
  value: unknown,
  label: string,
  maxCount = 8,
  options: {sort?: boolean; requireDistinctInput?: boolean} = {},
): string[] {
  if (!Array.isArray(value) || value.length > maxCount) {
    throw new EffectRuntimeRequestError(`${label} requires at most ${maxCount} references`);
  }
  const normalized = value.map(item => requireOpaqueMethodologyRef(item, label));
  const unique = [...new Set(normalized)];
  if (options.requireDistinctInput && unique.length !== normalized.length) {
    throw new EffectRuntimeRequestError(`${label} requires distinct opaque references`);
  }
  return options.sort === false ? unique : unique.sort();
}

/** Shared nullable finite number validator (never coerces null/undefined to 0). */
export function optionalFiniteNumber(
  value: unknown,
  label: string,
  options: {nonNegative?: boolean} = {},
): number | null {
  if (value === null || value === undefined) return null;
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    (options.nonNegative && value < 0)
  ) {
    throw new EffectRuntimeRequestError(
      `${label} requires ${options.nonNegative ? "a non-negative " : "a "}finite number or null`,
    );
  }
  return value;
}

/** Shared deterministic 6-decimal rounding avoiding -0. */
export function roundMethodologyMetric(value: number): number {
  const rounded = Math.round(value * 1e6) / 1e6;
  return Object.is(rounded, -0) ? 0 : rounded;
}

/** Replay precedence check: returns true only when a settlement replay carries
 * a strictly older committed timestamp than the current committed record. */
export function isHistoricalReplayPrecedingCurrent(
  replayCommittedAt: unknown,
  currentUpdatedAt: unknown,
): boolean {
  const replayTime =
    typeof replayCommittedAt === "string" ? parseTodoTimestampMicros(replayCommittedAt) : null;
  const currentTime =
    typeof currentUpdatedAt === "string" ? parseTodoTimestampMicros(currentUpdatedAt) : null;
  return replayTime !== null && currentTime !== null && replayTime < currentTime;
}

/** Bug Class 1 Guard: strict boolean feature-off & quiet-turn activation check.
 * Rejects truthy strings/objects and suppresses untriggered cues on quiet turns. */
export function evaluateMethodologyActivation(params: {
  enabled: unknown;
  quiet_turn_policy?: QuietTurnPolicy;
  trigger_active?: boolean;
  required_scope?: Record<string, unknown>;
}): {active: boolean; reason: "disabled" | "missing_scope" | "quiet_turn" | "active"} {
  if (params.enabled !== true) {
    return {active: false, reason: "disabled"};
  }
  if (params.required_scope) {
    for (const value of Object.values(params.required_scope)) {
      if (typeof value !== "string" || !value.trim()) {
        return {active: false, reason: "missing_scope"};
      }
    }
  }
  if (params.quiet_turn_policy === "silent_when_untriggered" && params.trigger_active === false) {
    return {active: false, reason: "quiet_turn"};
  }
  return {active: true, reason: "active"};
}

export function normalizeCanonicalSourceSnapshot(
  value: unknown,
  label = "source snapshot",
): CanonicalSourceSnapshot {
  const row = requireJsonObject(value, label);
  const keys = Object.keys(row);
  if (
    keys.length !== 2 ||
    !Object.hasOwn(row, "event_count") ||
    !Object.hasOwn(row, "event_ids_sha256") ||
    !Number.isSafeInteger(row.event_count) ||
    Number(row.event_count) < 0 ||
    typeof row.event_ids_sha256 !== "string" ||
    !BARE_SHA256_PATTERN.test(row.event_ids_sha256)
  ) {
    throw new EffectRuntimeRequestError(`${label} requires its original event count and digest`);
  }
  return {event_count: Number(row.event_count), event_ids_sha256: row.event_ids_sha256};
}

/** Bug Class 4 Guard: exact Goal/Agent/Todo work ownership check. */
export function matchesWorkIdentity(
  row: JsonObject,
  binding: JsonObject,
  fallbackBinding?: JsonObject,
): boolean {
  const source = jsonObject(row.work_binding) ?? fallbackBinding;
  return (
    !!source &&
    source.goal_id === binding.goal_id &&
    source.agent_id === binding.agent_id &&
    source.todo_id === binding.todo_id
  );
}

/** Bug Class 2 Guard: prefix order check for explicitly cited earlier records. */
export function recordedBeforePrefix(row: JsonObject, sourceEventCount: unknown): boolean {
  const order = row.recorded_order;
  return (
    typeof sourceEventCount === "number" &&
    Number.isSafeInteger(sourceEventCount) &&
    typeof order === "number" &&
    Number.isSafeInteger(order) &&
    order >= 0 &&
    order < sourceEventCount
  );
}

/** Bug Class 2 Guard: append-order vs. committed-clock late-replay check.
 * Delayed delivery of an older primary cannot masquerade as new evidence, and
 * tied or missing timestamps never prove a newer uncited observation. */
export function committedInWorkWindow(
  row: JsonObject,
  startOrder: number,
  decisionTimeMicros: bigint | null,
): boolean {
  const order = row.recorded_order;
  const committed = parseTodoTimestampMicros(String(row.last_updated_at ?? ""));
  return (
    Number.isSafeInteger(order) &&
    Number(order) >= startOrder &&
    committed !== null &&
    decisionTimeMicros !== null &&
    committed > decisionTimeMicros
  );
}

/** Selects the newest committed record for a scoped work identity; late replays
 * retain their original committed timestamp rather than winning by append order. */
export function latestCommittedScopedRecord(
  records: readonly JsonObject[],
  predicate: (row: JsonObject) => boolean,
): JsonObject | undefined {
  return records
    .filter(predicate)
    .sort((a, b) => {
      const left = parseTodoTimestampMicros(String(a.last_updated_at ?? ""));
      const right = parseTodoTimestampMicros(String(b.last_updated_at ?? ""));
      if (left !== null && right !== null && left !== right) return left > right ? -1 : 1;
      return Number(b.recorded_order ?? 0) - Number(a.recorded_order ?? 0);
    })[0];
}

/** Shared canonical evidence window selector across live work observations and
 * explicitly cited frozen source snapshots. Eliminates hand-rolled replay bugs. */
export function selectCanonicalEvidenceWindow(params: {
  findings: readonly JsonObject[];
  cited_sources?: unknown;
  source_finding: JsonObject;
  binding: WorkIdentityTuple;
  source_snapshot?: CanonicalSourceSnapshot;
  cited_refs: ReadonlySet<string>;
  has_decision_attachment: (row: JsonObject) => boolean;
  matches_decision_target: (row: JsonObject) => boolean;
}): {
  scoped_findings: JsonObject[];
  source_order: number | undefined;
  work_observation_start: number;
  decision_time_micros: bigint | null;
} {
  const {findings, source_finding, binding, source_snapshot, cited_refs} = params;
  const sourceOrder = source_snapshot?.event_count;
  const workObservationStart = sourceOrder ?? Number(source_finding.recorded_order) + 1;
  const decisionTime = parseTodoTimestampMicros(String(source_finding.last_updated_at ?? ""));
  const priorSource = Array.isArray(params.cited_sources)
    ? (params.cited_sources as JsonObject[]).find(
        row =>
          row.source_finding_id === source_finding.finding_id &&
          source_snapshot !== undefined &&
          (row.source_snapshot as JsonObject | undefined)?.event_count === source_snapshot.event_count &&
          (row.source_snapshot as JsonObject | undefined)?.event_ids_sha256 === source_snapshot.event_ids_sha256,
      )
    : undefined;
  const plainFindings = Array.isArray(priorSource?.findings) ? (priorSource.findings as JsonObject[]) : [];
  const scopedFindings = [
    ...findings.filter(
      row =>
        params.has_decision_attachment(row) ||
        (row.work_binding !== undefined && committedInWorkWindow(row, workObservationStart, decisionTime)),
    ),
    ...plainFindings.filter(row => !params.has_decision_attachment(row)),
  ].filter(row => {
    if (row.node_id !== source_finding.node_id) return false;
    const bound = jsonObject(row.work_binding) !== null;
    if (bound && (!matchesWorkIdentity(row, binding as unknown as JsonObject) || row.status !== "confirmed")) {
      return false;
    }
    if (!params.has_decision_attachment(row)) {
      if (bound) return true;
      return (
        cited_refs.size > 0 &&
        recordedBeforePrefix(row, sourceOrder) &&
        row.agent_id === binding.agent_id &&
        Array.isArray(row.evidence_refs) &&
        row.evidence_refs.some(ref => typeof ref === "string" && cited_refs.has(ref))
      );
    }
    return params.matches_decision_target(row);
  });

  return {
    scoped_findings: scopedFindings,
    source_order: sourceOrder,
    work_observation_start: workObservationStart,
    decision_time_micros: decisionTime,
  };
}

/** Bug Class 4 Guard: exact scope-tuple comparison (never substring/prose match). */
export function matchesExactScopeTuple<T extends object>(
  candidate: object,
  expected: T,
  keys: readonly (keyof T)[],
): boolean {
  const row = candidate as Record<string, unknown>;
  const target = expected as Record<string, unknown>;
  return keys.every(key => row[key as string] === target[key as string]);
}

/** Bug Class 3 Guard: bounded compact projection with omitted count & cold-path pointer. */
export function projectBoundedMethodologyItems<T>(
  items: readonly T[],
  maxItems: number,
  detailsCommand?: readonly string[],
): {visible: T[]; omitted_count: number; details_command?: string[]} {
  if (!Number.isSafeInteger(maxItems) || maxItems < 1) {
    throw new EffectRuntimeRequestError("maxItems requires a positive integer");
  }
  const visible = items.slice(0, maxItems);
  return {
    visible,
    omitted_count: Math.max(0, items.length - visible.length),
    ...(detailsCommand ? {details_command: [...detailsCommand]} : {}),
  };
}

function utf8ByteLength(text: string): number {
  return new TextEncoder().encode(text).byteLength;
}

function inspectTreeForInvariants(
  value: unknown,
  path: string,
  violations: string[],
  depth = 0,
): void {
  if (depth > 32) {
    violations.push(`${path}: nesting depth exceeds 32`);
    return;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      violations.push(`${path}: non-finite number is forbidden`);
    }
    return;
  }
  if (typeof value === "string") {
    if (/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(value)) {
      violations.push(`${path}: control characters are forbidden`);
    }
    return;
  }
  if (Array.isArray(value)) {
    for (let i = 0; i < value.length; i++) {
      inspectTreeForInvariants(value[i], `${path}[${i}]`, violations, depth + 1);
    }
    return;
  }
  if (value !== null && typeof value === "object") {
    const row = value as Record<string, unknown>;
    for (const [key, child] of Object.entries(row)) {
      if (FORBIDDEN_AUTHORITY_KEYS.has(key)) {
        violations.push(`${path}.${key}: methodology operator cannot emit authority/gate field`);
      }
      inspectTreeForInvariants(child, `${path}.${key}`, violations, depth + 1);
    }
  }
}

/** Bug Class 5 Guard: verifies non-escalation, finite numbers, and byte bounds. */
export function verifyMethodologyOperatorOutput(params: {
  operator_id: string;
  output: JsonObject;
  max_bytes?: number;
  require_advisory_flags?: boolean;
}): {
  schema_version: typeof STAGE_METHODOLOGY_VERIFICATION_SCHEMA;
  operator_id: string;
  valid: boolean;
  byte_length: number;
  violations: string[];
} {
  const violations: string[] = [];
  const {operator_id, output} = params;
  const maxBytes = params.max_bytes ?? 65_536;
  const requireAdvisory = params.require_advisory_flags ?? true;

  if (requireAdvisory) {
    if (output.advisory_only !== true) {
      violations.push("$.advisory_only: must be explicitly true");
    }
    if (output.grants_execution_authority !== false) {
      violations.push("$.grants_execution_authority: must be explicitly false");
    }
  }
  inspectTreeForInvariants(output, "$", violations, 0);
  const encoded = JSON.stringify(output);
  const byteLength = utf8ByteLength(encoded);
  if (byteLength > maxBytes) {
    violations.push(`$: payload ${byteLength} UTF-8 bytes exceeds cap ${maxBytes}`);
  }
  return {
    schema_version: STAGE_METHODOLOGY_VERIFICATION_SCHEMA,
    operator_id,
    valid: violations.length === 0,
    byte_length: byteLength,
    violations,
  };
}

export function assertMethodologyOperatorOutput<T extends JsonObject>(
  operatorId: string,
  output: T,
  maxBytes = 65_536,
): T {
  const report = verifyMethodologyOperatorOutput({
    operator_id: operatorId,
    output,
    max_bytes: maxBytes,
    require_advisory_flags: true,
  });
  if (!report.valid) {
    throw new EffectRuntimeRequestError(
      `methodology operator ${operatorId} violated output invariants: ${report.violations.join("; ")}`,
    );
  }
  return output;
}

export function listStageMethodologyOperators(params: JsonObject = {}): JsonObject {
  const stageFilter = params.stage;
  if (stageFilter !== undefined) {
    if (typeof stageFilter !== "string" || !(LOOPX_CONTROL_STAGES as readonly string[]).includes(stageFilter)) {
      throw new EffectRuntimeRequestError("stage must be one of the 5 LoopX control stages");
    }
  }
  const operators = BUILTIN_STAGE_METHODOLOGY_OPERATORS.filter(
    op =>
      stageFilter === undefined ||
      op.stage === stageFilter ||
      op.companion_stages.includes(stageFilter as LoopxControlStage),
  );
  return {
    schema_version: STAGE_METHODOLOGY_CATALOG_SCHEMA,
    stages: [...LOOPX_CONTROL_STAGES],
    operators: operators.map(op => ({
      ...op,
      companion_stages: [...op.companion_stages],
      scope_keys: [...op.scope_keys],
    })),
    invariants: [
      "strict_feature_off_and_quiet_turn_silence",
      "canonical_prefix_and_late_replay_provenance",
      "exact_scope_tuple_isolation_without_null_to_zero_coercion",
      "bounded_compact_projection_with_cold_path_pointer",
      "advisory_non_escalation_and_stateless_rollback",
    ],
  };
}

export function verifyMethodologyOperatorOutputHandler(params: JsonObject): JsonObject {
  const row = requireJsonObject(params, "methodology verification request");
  if (typeof row.operator_id !== "string" || !row.operator_id.trim()) {
    throw new EffectRuntimeRequestError("operator_id requires a non-empty string");
  }
  const output = requireJsonObject(row.output, "operator output");
  const maxBytes =
    row.max_bytes === undefined
      ? 65_536
      : typeof row.max_bytes === "number" && Number.isSafeInteger(row.max_bytes) && row.max_bytes > 0
        ? row.max_bytes
        : (() => {
            throw new EffectRuntimeRequestError("max_bytes requires a positive integer");
          })();
  return verifyMethodologyOperatorOutput({
    operator_id: row.operator_id.trim(),
    output,
    max_bytes: maxBytes,
    require_advisory_flags: row.require_advisory_flags !== false,
  }) as unknown as JsonObject;
}
