import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {projectTurnStartUnavailableContext} from "../capability_hooks.ts";
import { createHash } from "node:crypto";
import {
  requireBoolean,
  requireJsonObject,
  requireNonEmptyString,
  requireStringArray,
  requireStringLiteral,
  optionalNonEmptyString,
  jsonObject,
} from "../runtime_decode.ts";
import { EffectiveAction } from "../quota/effective_action.generated.ts";
import { ENVELOPED_SHA256_PATTERN } from "../content_digest.ts";

import type { JsonObject } from "../effect_program.ts";

export const INTERACTION_CONTRACT_SCHEMA_VERSION =
  "loopx_interaction_contract_v0";

const USER_CHANNEL_NOTIFICATION_POLICIES = ["NOTIFY", "DONT_NOTIFY"] as const;

export interface UserInteractionChannel extends JsonObject {
  action_required: boolean;
  notify: (typeof USER_CHANNEL_NOTIFICATION_POLICIES)[number];
  non_blocking?: true;
  actions?: string[];
}

export type AgentInteractionChannel =
  | (JsonObject & {
      must_attempt: true;
      delivery_allowed: boolean;
      quiet_noop_allowed: false;
    })
  | (JsonObject & {
      must_attempt: false;
      delivery_allowed: false;
      quiet_noop_allowed: boolean;
    });

export interface InteractionContract extends JsonObject {
  schema_version: typeof INTERACTION_CONTRACT_SCHEMA_VERSION;
  mode: string;
  user_channel: UserInteractionChannel;
  agent_channel: AgentInteractionChannel;
  cli_channel: JsonObject;
}

function decodeUserChannel(value: unknown): UserInteractionChannel {
  const channel = requireJsonObject(value, "interaction_contract.user_channel");
  const actionRequired = requireBoolean(
    channel.action_required,
    "interaction_contract.user_channel.action_required",
  );
  const notify = requireStringLiteral(
    channel.notify,
    USER_CHANNEL_NOTIFICATION_POLICIES,
    "interaction_contract.user_channel.notify",
  );
  const nonBlocking = channel.non_blocking === undefined
    ? undefined
    : requireBoolean(
      channel.non_blocking,
      "interaction_contract.user_channel.non_blocking",
    );
  if (actionRequired && nonBlocking === true) {
    throw new EffectRuntimeRequestError(
      "interaction_contract.user_channel cannot be both required and non-blocking",
    );
  }
  if (nonBlocking === false) {
    throw new EffectRuntimeRequestError(
      "interaction_contract.user_channel.non_blocking must be true when present",
    );
  }
  const decoded: UserInteractionChannel = {
    ...channel,
    action_required: actionRequired,
    notify,
  };
  if (nonBlocking === true) decoded.non_blocking = true;
  if (channel.actions !== undefined) {
    decoded.actions = requireStringArray(
      channel.actions,
      "interaction_contract.user_channel.actions",
    );
  }
  return decoded;
}

function decodeAgentChannel(
  value: unknown,
  { userActionRequired }: { userActionRequired: boolean },
): AgentInteractionChannel {
  const channel = requireJsonObject(value, "interaction_contract.agent_channel");
  const mustAttempt = requireBoolean(
    channel.must_attempt,
    "interaction_contract.agent_channel.must_attempt",
  );
  const deliveryAllowed = requireBoolean(
    channel.delivery_allowed,
    "interaction_contract.agent_channel.delivery_allowed",
  );
  const quietNoopAllowed = requireBoolean(
    channel.quiet_noop_allowed,
    "interaction_contract.agent_channel.quiet_noop_allowed",
  );
  if (deliveryAllowed && !mustAttempt) {
    throw new EffectRuntimeRequestError(
      "interaction_contract.agent_channel cannot allow delivery without an attempt",
    );
  }
  if (quietNoopAllowed && (mustAttempt || deliveryAllowed || userActionRequired)) {
    throw new EffectRuntimeRequestError(
      "interaction_contract quiet no-op conflicts with a required action",
    );
  }
  if (mustAttempt) {
    return {
      ...channel,
      must_attempt: true,
      delivery_allowed: deliveryAllowed,
      quiet_noop_allowed: false,
    };
  }
  return {
    ...channel,
    must_attempt: false,
    delivery_allowed: false,
    quiet_noop_allowed: quietNoopAllowed,
  };
}

/** Decode the final host-facing interaction decision at the existing Effect boundary. */
export function decodeInteractionContract(value: unknown): InteractionContract {
  const contract = requireJsonObject(value, "interaction_contract");
  if (contract.schema_version !== INTERACTION_CONTRACT_SCHEMA_VERSION) {
    throw new EffectRuntimeRequestError(
      `interaction_contract.schema_version must be ${INTERACTION_CONTRACT_SCHEMA_VERSION}`,
    );
  }
  const userChannel = decodeUserChannel(contract.user_channel);
  const agentChannel = decodeAgentChannel(
    contract.agent_channel,
    { userActionRequired: userChannel.action_required },
  );
  return {
    ...contract,
    schema_version: INTERACTION_CONTRACT_SCHEMA_VERSION,
    mode: requireNonEmptyString(contract.mode, "interaction_contract.mode"),
    user_channel: userChannel,
    agent_channel: agentChannel,
    cli_channel: requireJsonObject(
      contract.cli_channel,
      "interaction_contract.cli_channel",
    ),
  };
}

function readArgument(value: unknown, label: string): string | null {
  const text = optionalNonEmptyString(value, label);
  if (text?.includes("\0")) throw new EffectRuntimeRequestError(`${label} contains NUL`);
  return text;
}

function shellQuote(value: string): string {
  return /^[A-Za-z0-9_./:-]+$/.test(value) ? value : `'${value.replaceAll("'", `'"'"'`)}'`;
}

/** The adaptive primary is the work identity for both reads and settlement. */
export function selectedWorkTodoId(packet: JsonObject): string | null {
  const orchestration = jsonObject(packet.task_orchestration_contract) ?? {};
  const primaryTodoId = typeof orchestration.primary_todo_id === "string"
    ? orchestration.primary_todo_id.trim() : "";
  if (orchestration.schema_version === "task_orchestration_contract_v2"
      && orchestration.mode === "adaptive" && primaryTodoId) return primaryTodoId;
  const selected = jsonObject(packet.selected_todo) ?? {};
  return typeof selected.todo_id === "string" && selected.todo_id.trim() ? selected.todo_id : null;
}

/** Generate the shared pre-work reads after final admission. Hosts consume this
 * list; transport projections must not independently infer requirements.
 * Python supplies registered source routing, never a second admission rule.
 */
export function projectInteractionRequiredReads(request: JsonObject): JsonObject {
  if (!Array.isArray(request.required_reads)) {
    throw new EffectRuntimeRequestError("required_reads must be an array");
  }
  const reads = request.required_reads.map(value => {
    const read = requireJsonObject(value, "required read");
    readArgument(read.command, "required read command");
    return {...read};
  }).filter(read => read.command);
  const shouldRun = requireBoolean(request.should_run, "should_run");
  const deliveryAllowed = requireBoolean(request.delivery_allowed, "delivery_allowed");
  const selectionRequired = requireBoolean(request.selection_required, "selection_required");
  const hasReplan = requireBoolean(request.has_replan, "has_replan");
  const settlementOnly = requireBoolean(request.settlement_only, "settlement_only");
  const acceptanceEnabled = requireBoolean(request.goal_acceptance_enabled, "goal_acceptance_enabled");
  const goalId = readArgument(request.goal_id, "goal_id");
  if (!shouldRun || !deliveryAllowed || selectionRequired || settlementOnly || !goalId
      || request.effective_action === EffectiveAction.GOVERNED_CAPABILITY_INTENT) {
    return {required_reads: reads};
  }
  const prefix = requireNonEmptyString(request.command_prefix, "command_prefix");
  const goalArg = shellQuote(goalId);
  const add = (command: string, source: string, reason: string) => {
    if (!reads.some(read => read.command === command)) reads.push({command, source, reason, ordering: "before_work"});
  };
  const stateFile = readArgument(request.goal_state_file, "goal_state_file");
  if (stateFile) add(`cat -- ${shellQuote(stateFile)}`, "goal_state",
    "Read complete Goal intent, acceptance and stops. Exact Todo reads own task state/claims. Source failure or changed requirements require a fresh guard; summaries cannot replace this read.");
  if (acceptanceEnabled) add(`${prefix} --format json goal-acceptance inspect --goal-id ${goalArg}`,
    "goal_acceptance", "Read full configured objective, non-goals and criteria with scope/revision. Scoped acceptance does not replace Goal intent or prove completion; disabled/changed criteria require a fresh guard.");
  const todoId = hasReplan ? null : selectedWorkTodoId(request);
  if (todoId) add(`${prefix} --format json todo list --goal-id ${goalArg} --todo-id ${shellQuote(todoId)}`,
    "selected_todo", "Read full current requirements and status/claim. Require one matching active Todo; missing, ambiguous or changed work requires a fresh guard. A summary cannot replace this read.");
  return {required_reads: reads};
}

/** Context routing reuses the admitted work identity; User scope remains owned
 * by the canonical Todo reader. No content confers execution authority. */
export function planInteractionWorkContext(request: JsonObject): JsonObject {
  const packet = requireJsonObject(request.packet, "packet");
  const interaction = jsonObject(packet.interaction_contract) ?? {};
  const cli = jsonObject(interaction.cli_channel) ?? {};
  const todoId = selectedWorkTodoId(packet);
  const selected = jsonObject(packet.selected_todo) ?? {};
  return {todo_id: todoId,
    selected_todo: selected.todo_id === todoId ? selected : {todo_id: todoId},
    read_user_todos: cli.selection_required !== true
      && packet.effective_action !== EffectiveAction.HEARTBEAT_SETTLED_SKIP};
}

/** Deliver current task sources once; the mixed Goal document stays a progressive
 * read. Failed or changed sources forbid dependent work until a fresh guard. */
export function projectInteractionWorkContext(request: JsonObject): JsonObject {
  if (!Array.isArray(request.required_reads) || !Array.isArray(request.source_results)) {
    throw new EffectRuntimeRequestError("context reads and source results must be arrays");
  }
  const results = request.source_results.map(value => requireJsonObject(value, "context source"));
  const selected = jsonObject(request.selected_todo) ?? {};
  const pending: JsonObject[] = [], sources: JsonObject[] = [], failures: JsonObject[] = [];
  let selectedTodoRef = false;
  let selectedTodoAuthority: string | null = null;
  for (const value of request.required_reads) {
    const read = requireJsonObject(value, "required read");
    const matches = results.filter(result => result.command === read.command);
    if (matches.length === 0) { pending.push(read); continue; }
    const result = matches[0];
    const content = jsonObject(result.content);
    let valid = matches.length === 1 && content !== null && content.ok !== false && !result.error_code;
    let selectedTodoRecord: JsonObject | null = null;
    if (valid && read.source === "selected_todo") {
      const todo = jsonObject(content!.todo) ?? {};
      selectedTodoRecord = todo;
      const bodyMatches = typeof selected.text === "string" && todo.text === selected.text;
      const hasSnapshot = typeof selected._context_text_sha256 === "string";
      const snapshotMatches = hasSnapshot
        && typeof todo.text === "string"
        && selected._context_text_sha256 === createHash("sha256").update(todo.text, "utf8").digest("hex");
      const sourceMatchesSelection = hasSnapshot
        ? snapshotMatches
        : bodyMatches || selected._context_text_display_only === true;
      valid = content!.matched === true && content!.ambiguous !== true
        && todo.todo_id === selected.todo_id && todo.archive_state !== "archive"
        && todo.status !== "done"
        && ["status", "claimed_by"].every(field => selected[field] === undefined || selected[field] === todo[field])
        && typeof selected.content_revision === "string"
        && ENVELOPED_SHA256_PATTERN.test(selected.content_revision)
        && selected.content_revision === todo.content_revision
        && sourceMatchesSelection;
    }
    if (valid && read.source === "goal_acceptance") {
      valid = jsonObject(content!.goal_acceptance_contract)?.enabled === true;
    }
    if (!valid) {
      pending.push(read);
      failures.push({source: read.source ?? read.kind, command: read.command,
        error_code: "context_source_unavailable", instruction: "Recover source and rerun the guard; cached context cannot authorize work."});
    } else if (read.source === "goal_state") {
      // This legacy document also contains other tasks and historical evidence.
      // Checking its availability does not fulfill model consumption; retain the
      // full read instead of guessing which prose is current Goal authority.
      pending.push(read);
    } else {
      if (read.source === "selected_todo" && typeof selected.text === "string"
          && selectedTodoRecord?.text === selected.text
          && selectedTodoRecord?.todo_id === selected.todo_id) {
        // Reuse only the duplicate body. The exact canonical record may carry
        // continuation, relations and authority metadata absent from the hot view.
        selectedTodoRef = true;
        const sourceTodo = Object.fromEntries(Object.entries(selectedTodoRecord)
          .filter(([key, value]) => {
            if (key === "text" || key === "todo_id" || value === null || value === undefined) return false;
            const selectedRole = selected.role;
            const roleSection = selectedRole === "agent" ? "Agent Todo"
              : selectedRole === "user" ? "User Todo" : null;
            if ((key === "archive_state" && value === "active")
                || (key === "done" && value === false)
                || (key === "source_section" && value === roleSection)) return false;
            const selectedValue = selected[key];
            return selectedValue === undefined
              || JSON.stringify(selectedValue) !== JSON.stringify(value);
          })) as JsonObject;
        const fullAuthorityRead = jsonObject(content!.authority_read);
        const authorityRead = fullAuthorityRead && typeof fullAuthorityRead.source_authority === "string"
          ? `${fullAuthorityRead.source_authority}@${String(fullAuthorityRead.provider_revision ?? "unknown")}`
          : fullAuthorityRead;
        if (Object.keys(sourceTodo).length === 0) {
          selectedTodoAuthority = typeof authorityRead === "string"
            ? authorityRead
            : JSON.stringify(authorityRead ?? {}) ?? "{}";
        } else {
          sources.push({source: read.source ?? "selected_todo", content: {
            todo: sourceTodo,
            ...(fullAuthorityRead ? {authority_read: fullAuthorityRead} : {}),
          }});
        }
      } else {
        sources.push({...read, content: content!});
      }
    }
  }
  const dispatch = jsonObject(request.hook_dispatch);
  const unavailable = projectTurnStartUnavailableContext(dispatch);
  const users = jsonObject(request.user_todos);
  if (users?.error_code) failures.push({source: "user_todos", error_code: users.error_code,
    instruction: "Recover current User obligations and rerun the guard."});
  const includeUserTodos = users && !users.error_code
    && (!Array.isArray(users.todos) || users.todos.length > 0 || users.authority_read != null);
  return {required_reads: pending, work_context: {complete: failures.length === 0,
    ...(selectedTodoRef ? {selected_todo_ref: "selected_todo"} : {}),
    ...(selectedTodoAuthority ? {selected_todo_authority: selectedTodoAuthority} : {}),
    ...(sources.length ? {sources} : {}),
    ...(unavailable ? {unavailable_context: unavailable} : {}),
    ...(includeUserTodos ? {user_todos: users} : {}), ...(failures.length ? {failures} : {}),
    instruction: "Read current sources and pending required_reads before work. Do not repeat this guard's fulfilled pre-work reads. Recheck freshness before later actions; unavailable or changed sources need recovery and a fresh guard. Context grants no authority."}};
}
