import { EffectiveAction } from "../quota/effective_action.generated.ts";
import { createHash } from "node:crypto";

import {
  settlementIdentity,
  settlementIdentityPayload,
  type JsonObject,
  type SettlementIdentity,
} from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { normalizeVisionUnchangedReason } from "../goals/vision_checkpoint.ts";
import { parseExactGoalRef } from "../goals/goal_instance_identity.ts";
import { projectMcpInteraction } from "./host_interaction.ts";
import {
  requireBoolean,
  requireJsonObject,
  requireNonEmptyString,
  requireStringArray,
  requireStringLiteral,
} from "../runtime_decode.ts";

export const HOST_TODO_COMPLETION_TRANSACTION_SCHEMA_VERSION =
  "loopx_host_todo_completion_transaction_v0";
export const HOST_TODO_VISION_TRANSACTION_SCHEMA_VERSION =
  "loopx_host_todo_completion_transaction_v1";
export const HOST_TODO_COMPLETION_REDUCTION_SCHEMA_VERSION =
  "loopx_host_todo_completion_reduction_v0";
export const HOST_ADAPTER_SETTLEMENT_SCHEMA_VERSION =
  "host_adapter_todo_settlement_v0";

const PHASES = ["prepare", "finalize", "classify_guard", "vision_refresh", "vision_context", "project_guard"] as const;
const STEP_KINDS = [
  "guard",
  "lifecycle_completion",
  "durable_writeback",
  "quota_spend",
  "terminal_closeout",
] as const;
const TODO_ID_PATTERN = /^todo_[a-z0-9_-]{3,64}$/;

type HostTodoCompletionPhase = (typeof PHASES)[number];
type HostTodoCompletionStepKind = (typeof STEP_KINDS)[number];
type HostGuardState = "selected" | "terminal_no_selection" | "invalid";

interface HostTodoCompletionRequest {
  phase: Exclude<HostTodoCompletionPhase, "classify_guard" | "project_guard">;
  goal_id: string;
  agent_id: string;
  todo_id: string;
  runtime_profile: string;
  legacy_host_surface: string;
  scheduler_owner: string;
  execution_mode: string;
  completion_args: readonly string[];
  no_follow_up: boolean;
  vision_path: string | null;
  vision_unchanged_reason: string | null;
  checkpoint_read_context_id: string | null;
  goal_instance_id: string | null;
  provider_outcomes: readonly ProviderOutcome[];
}

interface ProviderOutcome {
  step_kind: HostTodoCompletionStepKind;
  output: string;
}

interface ProviderStep extends JsonObject {
  step_kind: HostTodoCompletionStepKind;
  args: readonly string[];
  legacy_args: readonly string[] | null;
  continue_when: JsonObject | null;
}

interface GuardSelection extends JsonObject {
  state: HostGuardState;
  todo_id: string | null;
  reason: string | null;
  settlement_identity: JsonObject | null;
}

function decodePhase(value: JsonObject): HostTodoCompletionPhase {
  requireStringLiteral(
    value.schema_version,
    [HOST_TODO_COMPLETION_TRANSACTION_SCHEMA_VERSION, HOST_TODO_VISION_TRANSACTION_SCHEMA_VERSION] as const,
    "schema_version",
    "schema_version is unsupported",
  );
  return requireStringLiteral(
    value.phase,
    PHASES,
    "phase",
    "host Todo completion phase is unsupported",
  );
}

function typedTodoId(value: unknown, label: string): string {
  const raw = requireNonEmptyString(value, label);
  const candidate = raw.trim().toLowerCase();
  if (candidate !== raw || !TODO_ID_PATTERN.test(candidate)) {
    throw new EffectRuntimeRequestError(`${label} must be a typed Todo id`);
  }
  return candidate;
}

function decodeProviderOutcomes(value: unknown): ProviderOutcome[] {
  if (!Array.isArray(value)) {
    throw new EffectRuntimeRequestError("provider_outcomes must be an array");
  }
  return value.map((item, index) => {
    const outcome = requireJsonObject(item, `provider_outcomes[${index}]`);
    const stepKind = requireStringLiteral(
      outcome.step_kind,
      STEP_KINDS,
      `provider_outcomes[${index}].step_kind`,
      `provider_outcomes[${index}].step_kind is unsupported`,
    );
    if (typeof outcome.output !== "string") {
      throw new EffectRuntimeRequestError(
        `provider_outcomes[${index}].output must be a string`,
      );
    }
    return { step_kind: stepKind, output: outcome.output };
  });
}

function decodeRequest(
  value: JsonObject,
  phase: Exclude<HostTodoCompletionPhase, "classify_guard" | "project_guard">,
): HostTodoCompletionRequest {
  const todoId = typedTodoId(value.todo_id, "todo_id");
  const optionalText = (field: string): string | null => value[field] == null
    ? null : requireNonEmptyString(value[field], field);
  const visionPath = optionalText("vision_path");
  const unchanged = normalizeVisionUnchangedReason(optionalText("vision_unchanged_reason"));
  const readContextId = optionalText("checkpoint_read_context_id");
  if (visionPath && unchanged) {
    throw new EffectRuntimeRequestError("choose a vision patch or an unchanged reason, not both");
  }
  if ((visionPath || unchanged || readContextId || phase === "vision_refresh" || phase === "vision_context") &&
      value.schema_version !== HOST_TODO_VISION_TRANSACTION_SCHEMA_VERSION) {
    throw new EffectRuntimeRequestError("host vision authoring requires v1");
  }
  if (phase === "vision_refresh" && !visionPath && !unchanged) {
    throw new EffectRuntimeRequestError("vision refresh requires an authored decision");
  }
  if (phase === "vision_context" && (visionPath || unchanged || readContextId)) {
    throw new EffectRuntimeRequestError("vision context reads cannot submit a decision or receipt");
  }
  if (readContextId && phase !== "vision_refresh") {
    throw new EffectRuntimeRequestError("checkpoint read context belongs only to vision recovery");
  }
  const goalId = requireNonEmptyString(value.goal_id, "goal_id");
  const parsedGoalRef = value.goal_ref === undefined
    ? null
    : parseExactGoalRef(value.goal_ref);
  if (parsedGoalRef?.kind === "invalid") {
    throw new EffectRuntimeRequestError("host Todo completion GoalRef is malformed");
  }
  if (
    parsedGoalRef?.kind === "parsed"
    && parsedGoalRef.value.goalId.value !== goalId
  ) {
    throw new EffectRuntimeRequestError(
      "host Todo completion GoalRef does not match goal_id",
    );
  }
  const request: HostTodoCompletionRequest = {
    phase,
    goal_id: goalId,
    agent_id: requireNonEmptyString(value.agent_id, "agent_id"),
    todo_id: todoId,
    runtime_profile: requireNonEmptyString(
      value.runtime_profile,
      "runtime_profile",
    ),
    legacy_host_surface: requireNonEmptyString(
      value.legacy_host_surface,
      "legacy_host_surface",
    ),
    scheduler_owner: requireNonEmptyString(
      value.scheduler_owner,
      "scheduler_owner",
    ),
    execution_mode: requireNonEmptyString(
      value.execution_mode,
      "execution_mode",
    ),
    completion_args: requireStringArray(
      value.completion_args,
      "completion_args",
    ),
    no_follow_up: requireBoolean(value.no_follow_up, "no_follow_up"),
    vision_path: visionPath,
    vision_unchanged_reason: unchanged,
    checkpoint_read_context_id: readContextId,
    goal_instance_id: parsedGoalRef?.kind === "parsed"
      ? parsedGoalRef.value.goalInstanceId.value
      : null,
    provider_outcomes: [],
  };
  if (phase === "finalize") {
    request.provider_outcomes = decodeProviderOutcomes(value.provider_outcomes);
  }
  return request;
}

function turnInstanceId(request: HostTodoCompletionRequest): string {
  const identityParts = request.goal_instance_id === null
    ? [request.goal_id, request.agent_id, request.todo_id]
    : [
      "loopx_host_todo_completion_exact_v1",
      request.goal_id,
      request.goal_instance_id,
      request.agent_id,
      request.todo_id,
    ];
  const digest = createHash("sha256")
    .update(identityParts.join("\0"), "utf8")
    .digest("hex")
    .slice(0, 32);
  return `mcp-${digest}`;
}

function expectedIdentity(request: HostTodoCompletionRequest): {
  identity: SettlementIdentity;
  payload: JsonObject;
} {
  const identity = settlementIdentity({
    goal_id: request.goal_id,
    agent_id: request.agent_id,
    todo_id: request.todo_id,
    turn_instance_id: turnInstanceId(request),
  });
  return { identity, payload: settlementIdentityPayload(identity) };
}

function equals(path: readonly string[], value: unknown): JsonObject {
  return { kind: "equals", path: [...path], value };
}

function nullish(path: readonly string[]): JsonObject {
  return { kind: "nullish", path: [...path] };
}

function objectAt(path: readonly string[]): JsonObject {
  return { kind: "object", path: [...path] };
}

function notObjectAt(path: readonly string[]): JsonObject {
  return { kind: "not_object", path: [...path] };
}

function normalizedStringEquals(
  path: readonly string[],
  value: string,
  normalization: "trim" | "trim_lowercase",
): JsonObject {
  return {
    kind: "normalized_string_equals",
    path: [...path],
    value,
    normalization,
  };
}

function all(...conditions: readonly JsonObject[]): JsonObject {
  return { kind: "all", conditions: [...conditions] };
}

function any(...conditions: readonly JsonObject[]): JsonObject {
  return { kind: "any", conditions: [...conditions] };
}

function exactIdentityConditions(
  prefix: readonly string[],
  identity: JsonObject,
): JsonObject[] {
  return [
    objectAt(prefix),
    equals([...prefix, "goal_id"], identity.goal_id),
    equals([...prefix, "agent_id"], identity.agent_id),
    equals([...prefix, "todo_id"], identity.todo_id),
    equals([...prefix, "turn_instance_id"], identity.turn_instance_id),
    equals([...prefix, "effect_id"], identity.effect_id),
  ];
}

function guardContinueWhen(identity: JsonObject): JsonObject {
  const path = ["heartbeat_receipt", "settlement_identity"];
  return all(
    equals(["ok"], true),
    any(
      all(
        objectAt(path),
        normalizedStringEquals(
          [...path, "goal_id"],
          String(identity.goal_id),
          "trim",
        ),
        normalizedStringEquals(
          [...path, "agent_id"],
          String(identity.agent_id),
          "trim",
        ),
        normalizedStringEquals(
          [...path, "todo_id"],
          String(identity.todo_id),
          "trim_lowercase",
        ),
        normalizedStringEquals(
          [...path, "turn_instance_id"],
          String(identity.turn_instance_id),
          "trim",
        ),
        equals([...path, "effect_id"], identity.effect_id),
      ),
      all(
        notObjectAt(path),
        normalizedStringEquals(
          ["selected_todo", "todo_id"],
          String(identity.todo_id),
          "trim_lowercase",
        ),
      ),
    ),
  );
}

function completionContinueWhen(identity: JsonObject): JsonObject {
  return all(
    equals(["ok"], true),
    equals(["completed"], true),
    equals(["status"], "done"),
    ...exactIdentityConditions(["settlement_identity"], identity),
    objectAt(["settlement_result"]),
    nullish(["settlement_result", "failure"]),
  );
}

function writebackContinueWhen(identity: JsonObject): JsonObject {
  return all(
    equals(["ok"], true),
    ...exactIdentityConditions(["settlement_identity"], identity),
    objectAt(["settlement_result"]),
    nullish(["settlement_result", "failure"]),
  );
}

function spendContinueWhen(identity: JsonObject): JsonObject {
  return all(
    equals(["ok"], true),
    any(
      equals(["appended"], true),
      equals(["idempotent_replay"], true),
      equals(["receipt_repaired"], true),
    ),
    ...exactIdentityConditions(["settlement_identity"], identity),
    objectAt(["settlement_result"]),
    nullish(["settlement_result", "failure"]),
  );
}

// One command owner for first delivery and checkpoint-only recovery. The CLI's
// typed refresh recovery validates the original intent, baseline and revision;
// this projection neither invents a vision nor changes Todo/Goal terminal state.
function writebackArgs(request: HostTodoCompletionRequest, identity: JsonObject): string[] {
  return [
    "refresh-state", "--goal-id", request.goal_id, "--agent-id", request.agent_id,
    "--classification", "mcp_completed_turn_writeback",
    "--delivery-batch-scale", "single_surface", "--delivery-outcome", "outcome_progress",
    "--todo-id", request.todo_id, "--turn-instance-id", String(identity.turn_instance_id),
    ...(request.goal_instance_id
      ? ["--goal-instance-id", request.goal_instance_id]
      : []),
    "--completion-todo-id", request.todo_id, "--completion-turn-key", String(identity.effect_id),
    "--no-global-sync", "--suppress-external-sinks",
    ...(request.vision_path ? ["--agent-vision-json", request.vision_path] : []),
    ...(request.vision_unchanged_reason ? ["--vision-unchanged-reason", request.vision_unchanged_reason] : []),
    ...(request.checkpoint_read_context_id ? ["--checkpoint-read-context", request.checkpoint_read_context_id] : []),
  ];
}

function providerSteps(
  request: HostTodoCompletionRequest,
  identity: JsonObject,
): ProviderStep[] {
  const turnId = String(identity.turn_instance_id);
  const guardCommon = [
    "quota",
    "should-run",
    "--goal-id",
    request.goal_id,
    "--agent-id",
    request.agent_id,
    "--todo-id",
    request.todo_id,
    "--turn-instance-id",
    turnId,
    ...(request.goal_instance_id
      ? ["--goal-instance-id", request.goal_instance_id]
      : []),
  ];
  const lifecycleArgs = request.completion_args.filter(
    (arg) => arg !== "--no-follow-up",
  );
  if (request.goal_instance_id) {
    lifecycleArgs.push("--goal-instance-id", request.goal_instance_id);
  }
  const steps: ProviderStep[] = [
    {
      step_kind: "guard",
      args: [...guardCommon, "--runtime-profile", request.runtime_profile],
      legacy_args: [
        ...guardCommon,
        "--host-surface",
        request.legacy_host_surface,
        "--scheduler-owner",
        request.scheduler_owner,
        "--execution-mode",
        request.execution_mode,
      ],
      continue_when: guardContinueWhen(identity),
    },
    {
      step_kind: "lifecycle_completion",
      args: [...lifecycleArgs, "--turn-instance-id", turnId],
      legacy_args: null,
      continue_when: completionContinueWhen(identity),
    },
    {
      step_kind: "durable_writeback",
      args: writebackArgs(request, identity),
      legacy_args: null,
      continue_when: writebackContinueWhen(identity),
    },
    {
      step_kind: "quota_spend",
      args: [
        "quota",
        "spend-slot",
        "--goal-id",
        request.goal_id,
        "--slots",
        "1",
        "--source",
        "heartbeat",
        "--execute",
        "--agent-id",
        request.agent_id,
        "--todo-id",
        request.todo_id,
        "--turn-instance-id",
        turnId,
        ...(request.goal_instance_id
          ? ["--goal-instance-id", request.goal_instance_id]
          : []),
      ],
      legacy_args: null,
      continue_when: request.no_follow_up
        ? spendContinueWhen(identity)
        : null,
    },
  ];
  if (request.no_follow_up) {
    steps.push({
      step_kind: "terminal_closeout",
      args: [
        ...request.completion_args,
        "--turn-instance-id",
        turnId,
        ...(request.goal_instance_id
          ? ["--goal-instance-id", request.goal_instance_id]
          : []),
      ],
      legacy_args: null,
      continue_when: null,
    });
  }
  return steps;
}

function providerEffect(steps: readonly ProviderStep[]): JsonObject {
  return {
    provider_id: "loopx_cli",
    kind: "ordered_cli_sequence",
    steps: steps.map((step) => ({
      ...step,
      args: [...step.args],
      legacy_args: step.legacy_args ? [...step.legacy_args] : null,
    })),
  };
}

function reduction(
  phase: "prepare" | "finalize",
  decision: "execute" | "complete" | "blocked" | "provider_result",
  identity: JsonObject,
  effect: JsonObject | null,
  result: JsonObject | null,
): JsonObject {
  return {
    schema_version: HOST_TODO_COMPLETION_REDUCTION_SCHEMA_VERSION,
    phase,
    decision,
    identity,
    provider_effect: effect,
    result,
  };
}

function parseObject(value: string): JsonObject | null {
  try {
    return requireJsonObject(JSON.parse(value), "provider output");
  } catch {
    return null;
  }
}

function reasonFrom(value: JsonObject | null): string {
  if (!value) return "";
  const candidate = value.error || value.reason;
  return candidate === null || candidate === undefined
    ? ""
    : String(candidate).trim();
}

function nullableFailure(value: JsonObject): boolean {
  const result = value.settlement_result;
  if (typeof result !== "object" || result === null || Array.isArray(result)) {
    return false;
  }
  const failure = (result as JsonObject).failure;
  return failure === null || failure === undefined;
}

function identityMatches(value: JsonObject, identity: JsonObject): boolean {
  const candidate = value.settlement_identity;
  if (
    typeof candidate !== "object" ||
    candidate === null ||
    Array.isArray(candidate)
  ) {
    return false;
  }
  const record = candidate as JsonObject;
  return ["goal_id", "agent_id", "todo_id", "turn_instance_id", "effect_id"].every(
    (field) => record[field] === identity[field],
  );
}

function normalizedTodoId(value: unknown): string | null {
  const candidate = String(value || "").trim().toLowerCase();
  return TODO_ID_PATTERN.test(candidate) ? candidate : null;
}

function guardSelection(value: string): GuardSelection {
  let payload: unknown;
  try {
    payload = JSON.parse(value);
  } catch {
    return {
      state: "invalid",
      todo_id: null,
      reason: "quota guard returned malformed JSON",
      settlement_identity: null,
    };
  }
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    return {
      state: "invalid",
      todo_id: null,
      reason: "quota guard did not return an ok object",
      settlement_identity: null,
    };
  }
  const guard = payload as JsonObject;
  if (guard.ok !== true) {
    return {
      state: "invalid",
      todo_id: null,
      reason: reasonFrom(guard) || "quota guard did not return an ok object",
      settlement_identity: null,
    };
  }
  const heartbeat = guard.heartbeat_receipt;
  if (typeof heartbeat === "object" && heartbeat !== null && !Array.isArray(heartbeat)) {
    const rawIdentity = (heartbeat as JsonObject).settlement_identity;
    if (
      typeof rawIdentity === "object" &&
      rawIdentity !== null &&
      !Array.isArray(rawIdentity)
    ) {
      const raw = rawIdentity as JsonObject;
      const goalId = String(raw.goal_id || "").trim();
      const agentId = String(raw.agent_id || "").trim();
      const todoId = normalizedTodoId(raw.todo_id);
      const turnId = String(raw.turn_instance_id || "").trim();
      if (!(goalId && agentId && todoId && turnId)) {
        return {
          state: "invalid",
          todo_id: null,
          reason: "quota guard returned an incomplete settlement identity",
          settlement_identity: null,
        };
      }
      const identity = settlementIdentity({
        goal_id: goalId,
        agent_id: agentId,
        todo_id: todoId,
        turn_instance_id: turnId,
      });
      if (raw.effect_id !== identity.effect_id) {
        return {
          state: "invalid",
          todo_id: null,
          reason: "quota guard returned a mismatched settlement effect id",
          settlement_identity: null,
        };
      }
      return {
        state: "selected",
        todo_id: todoId,
        reason: null,
        settlement_identity: settlementIdentityPayload(identity),
      };
    }
  }
  const selected = guard.selected_todo;
  if (typeof selected === "object" && selected !== null && !Array.isArray(selected)) {
    const todoId = normalizedTodoId((selected as JsonObject).todo_id);
    if (todoId) {
      return {
        state: "selected",
        todo_id: todoId,
        reason: null,
        settlement_identity: null,
      };
    }
    return {
      state: "invalid",
      todo_id: null,
      reason: "quota guard selected_todo has no typed todo_id",
      settlement_identity: null,
    };
  }
  if (
    guard.should_run === false &&
    guard.effective_action === EffectiveAction.TERMINAL_NO_FOLLOWUP
  ) {
    return {
      state: "terminal_no_selection",
      todo_id: null,
      reason: null,
      settlement_identity: null,
    };
  }
  return {
    state: "invalid",
    todo_id: null,
    reason: "quota guard has no authoritative Todo selection",
    settlement_identity: null,
  };
}

function guardIdentityMatches(
  selection: GuardSelection,
  identity: JsonObject,
): boolean {
  const candidate = selection.settlement_identity;
  if (!candidate) return true;
  return ["goal_id", "agent_id", "todo_id", "turn_instance_id", "effect_id"].every(
    (field) => candidate[field] === identity[field],
  );
}

function blockedResult(
  request: HostTodoCompletionRequest,
  stage: string,
  reason: string,
  options: {
    guard_state?: HostGuardState;
    completion?: JsonObject;
  } = {},
): JsonObject {
  const settlement: JsonObject = {
    ok: false,
    failed_stage: stage,
    reason,
  };
  if (options.guard_state) settlement.guard_state = options.guard_state;
  const result: JsonObject = {
    schema_version: HOST_ADAPTER_SETTLEMENT_SCHEMA_VERSION,
    ok: false,
    completed: options.completion?.completed === true,
    goal_id: request.goal_id,
    todo_id: request.todo_id,
    settlement_blocked_completion: true,
    settlement,
  };
  if (options.completion) result.completion = options.completion;
  if (options.completion?.completed === true &&
      (request.vision_path !== null || request.vision_unchanged_reason !== null)) {
    result.recovery = {
      tool: "complete_task", todo_id: request.todo_id,
      settlement_identity: expectedIdentity(request).payload,
      instruction: "After correcting the reported error, retry complete_task with the same Todo, evidence and successor/no-follow-up intent. Correct only an uncommitted vision decision; do not repeat work, create another successor, or manually spend. review_task_vision alone does not finish a pending settlement. Committed conflicts and authority failures must not be bypassed.",
    };
  }
  return result;
}

function outcomeAt(
  outcomes: readonly ProviderOutcome[],
  index: number,
): JsonObject | null {
  const outcome = outcomes[index];
  return outcome ? parseObject(outcome.output) : null;
}

function validateOutcomeOrder(
  outcomes: readonly ProviderOutcome[],
  steps: readonly ProviderStep[],
): void {
  if (outcomes.length > steps.length) {
    throw new EffectRuntimeRequestError(
      "provider_outcomes contains more entries than the prepared plan",
    );
  }
  outcomes.forEach((outcome, index) => {
    if (outcome.step_kind !== steps[index].step_kind) {
      throw new EffectRuntimeRequestError(
        `provider_outcomes[${index}].step_kind does not match the prepared plan`,
      );
    }
  });
}

function finalize(request: HostTodoCompletionRequest): JsonObject {
  const { payload: identity } = expectedIdentity(request);
  const steps = providerSteps(request, identity);
  const outcomes = request.provider_outcomes;
  validateOutcomeOrder(outcomes, steps);

  if (outcomes.length === 0) {
    return reduction(
      "finalize",
      "blocked",
      identity,
      null,
      blockedResult(request, "guard", "host CLI provider returned no guard outcome"),
    );
  }

  const guard = guardSelection(outcomes[0].output);
  if (guard.state !== "selected") {
    return reduction(
      "finalize",
      "blocked",
      identity,
      null,
      blockedResult(
        request,
        "guard",
        guard.reason || "quota guard has no selected Todo",
        { guard_state: guard.state },
      ),
    );
  }
  if (guard.todo_id !== request.todo_id) {
    return reduction(
      "finalize",
      "blocked",
      identity,
      null,
      blockedResult(
        request,
        "guard",
        "quota guard selected a different Todo: expected " +
          `${request.todo_id}, selected ${guard.todo_id}`,
        { guard_state: guard.state },
      ),
    );
  }
  if (!guardIdentityMatches(guard, identity)) {
    return reduction(
      "finalize",
      "blocked",
      identity,
      null,
      blockedResult(
        request,
        "guard",
        "quota guard settlement identity does not match the request",
        { guard_state: "invalid" },
      ),
    );
  }

  if (outcomes.length < 2) {
    return reduction(
      "finalize",
      "blocked",
      identity,
      null,
      blockedResult(
        request,
        "durable_writeback",
        "host CLI provider stopped before Todo completion",
      ),
    );
  }
  const completion = outcomeAt(outcomes, 1);
  if (!completion) {
    return reduction(
      "finalize",
      "blocked",
      identity,
      null,
      blockedResult(
        request,
        "durable_writeback",
        "todo completion returned malformed JSON",
      ),
    );
  }
  if (
    completion.ok !== true ||
    completion.completed !== true ||
    completion.status !== "done"
  ) {
    return reduction("finalize", "provider_result", identity, null, completion);
  }
  if (!identityMatches(completion, identity) || !nullableFailure(completion)) {
    return reduction(
      "finalize",
      "blocked",
      identity,
      null,
      blockedResult(
        request,
        "durable_writeback",
        "todo completion did not prove the expected settlement identity",
        { completion },
      ),
    );
  }

  if (outcomes.length < 3) {
    return reduction(
      "finalize",
      "blocked",
      identity,
      null,
      blockedResult(
        request,
        "durable_writeback",
        "host CLI provider stopped before refresh-state writeback",
        { completion },
      ),
    );
  }
  const writeback = outcomeAt(outcomes, 2);
  if (
    !writeback ||
    writeback.ok !== true ||
    !identityMatches(writeback, identity) ||
    !nullableFailure(writeback)
  ) {
    return reduction(
      "finalize",
      "blocked",
      identity,
      null,
      blockedResult(
        request,
        "durable_writeback",
        reasonFrom(writeback) ||
          "refresh-state did not prove the expected settlement identity",
        { completion },
      ),
    );
  }

  if (outcomes.length < 4) {
    return reduction(
      "finalize",
      "blocked",
      identity,
      null,
      blockedResult(
        request,
        "quota_spend",
        "host CLI provider stopped before quota spend",
        { completion },
      ),
    );
  }
  const spend = outcomeAt(outcomes, 3);
  const spendCommitted = Boolean(
    spend &&
      (spend.appended === true ||
        spend.idempotent_replay === true ||
        spend.receipt_repaired === true),
  );
  if (
    !spend ||
    spend.ok !== true ||
    !spendCommitted ||
    !identityMatches(spend, identity) ||
    !nullableFailure(spend)
  ) {
    return reduction(
      "finalize",
      "blocked",
      identity,
      null,
      blockedResult(
        request,
        "quota_spend",
        reasonFrom(spend) || "quota spend did not append a receipt",
        { completion },
      ),
    );
  }

  let terminal: JsonObject | null = null;
  let finalCompletion = completion;
  if (request.no_follow_up) {
    if (outcomes.length < 5) {
      return reduction(
        "finalize",
        "blocked",
        identity,
        null,
        blockedResult(
          request,
          "terminal_closeout",
          "host CLI provider stopped before Todo terminal closeout",
          { completion },
        ),
      );
    }
    terminal = outcomeAt(outcomes, 4);
    if (
      !terminal ||
      terminal.ok !== true ||
      terminal.completed !== true ||
      terminal.status !== "done" ||
      terminal.completion_continuation !== "no_followup" ||
      !identityMatches(terminal, identity) ||
      !nullableFailure(terminal)
    ) {
      return reduction(
        "finalize",
        "blocked",
        identity,
        null,
        blockedResult(
          request,
          "terminal_closeout",
          reasonFrom(terminal) ||
            "Todo terminal closeout did not prove the expected identity",
          { completion },
        ),
      );
    }
    finalCompletion = terminal;
  }

  const settlement: JsonObject = {
    ok: true,
    guard_state: guard.state,
    durable_writeback: writeback,
    lifecycle_completion: completion,
    quota_spend: spend,
  };
  if (terminal) settlement.terminal_closeout = terminal;
  return reduction("finalize", "complete", identity, null, {
    schema_version: HOST_ADAPTER_SETTLEMENT_SCHEMA_VERSION,
    ok: true,
    completed: true,
    status: "done",
    goal_id: request.goal_id,
    todo_id: request.todo_id,
    settlement_identity: identity,
    completion: finalCompletion,
    settlement,
    ...(request.vision_path !== null || request.vision_unchanged_reason !== null ? {
      completion_scope: "todo",
      goal_terminal: {
        assessed: false,
        authority: "should_run.interaction_contract",
        next_tool: "should_run",
        instruction: "Todo terminal_closeout/no_followup is not Goal completion. Obey the fresh Goal contract, including vision replan. A vision evidence timestamp predating completion does not make that live decision stale. If all scoped acceptance is verified, use the admitted replan path to author no_followup; do not invent filler work or silently drop the obligation.",
      },
    } : {}),
  });
}

export function evaluateHostTodoCompletion(value: JsonObject): JsonObject {
  const phase = decodePhase(value);
  if (phase === "project_guard") {
    if (value.schema_version !== HOST_TODO_VISION_TRANSACTION_SCHEMA_VERSION) {
      throw new EffectRuntimeRequestError("host interaction projection requires v1");
    }
    return {schema_version: HOST_TODO_COMPLETION_REDUCTION_SCHEMA_VERSION, phase,
      packet: projectMcpInteraction(requireJsonObject(value.packet, "packet"))};
  }
  if (phase === "classify_guard") {
    if (typeof value.guard_output !== "string") {
      throw new EffectRuntimeRequestError("guard_output must be a string");
    }
    return {
      schema_version: HOST_TODO_COMPLETION_REDUCTION_SCHEMA_VERSION,
      phase,
      decision: "complete",
      selection: guardSelection(value.guard_output),
    };
  }
  const request = decodeRequest(value, phase);
  if (phase === "finalize") return finalize(request);
  const { payload: identity } = expectedIdentity(request);
  if (phase === "vision_context") {
    return {
      schema_version: HOST_TODO_COMPLETION_REDUCTION_SCHEMA_VERSION,
      phase, identity, args: ["checkpoint-context", "--goal-id", request.goal_id,
        "--agent-id", request.agent_id, "--todo-id", request.todo_id,
        "--turn-instance-id", String(identity.turn_instance_id),
        ...(request.goal_instance_id
          ? ["--goal-instance-id", request.goal_instance_id]
          : [])],
    };
  }
  if (phase === "vision_refresh") {
    return {
      schema_version: HOST_TODO_COMPLETION_REDUCTION_SCHEMA_VERSION,
      phase, identity, args: writebackArgs(request, identity),
    };
  }
  const steps = providerSteps(request, identity);
  return reduction(
    "prepare",
    "execute",
    identity,
    providerEffect(steps),
    null,
  );
}
