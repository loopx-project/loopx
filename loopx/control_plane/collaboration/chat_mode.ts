/** Conversation execution admission. Native lifecycle is a host observation;
 * this contract never settles canonical work or grants another Agent's lease. */
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {resolveConversationScope} from "./conversation_scope.ts";
import {isTerminalTurnStatus} from "../turn_driver/chat_turn_acceptance.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";

function requireThat(ok: unknown, message: string): asserts ok {
  if (!ok) throw new EffectRuntimeRequestError(message);
}

export function planChatMode(input: JsonObject): JsonObject {
  const session = requireJsonObject(input.session, "conversation session");
  const operation = input.operation;
  requireThat(["configure", "start", "resume", "pause", "exit", "message", "wake"].includes(String(operation)), "unsupported conversation operation");
  const localOwner = resolveConversationScope(session).kind === "owner_goal"
    && session.session_mode !== "attached_host" && session.agent_id === "codex";
  // External inbox returns have their own exact audience owner. A recorded
  // conversation is provenance, not authority to resume a native Goal. Settle
  // this host intent instead of throwing and retrying it on every pump tick.
  if (operation === "wake" && input.origin === "host" && !localOwner) {
    return {operation, state: "refused", reason: "no_wake_owner"};
  }
  requireThat(localOwner && input.origin === (operation === "wake" ? "host" : "web"),
    "LoopX mode requires a local managed Codex Goal conversation");
  const settings = requireJsonObject(input.settings, "conversation settings");
  const native = requireJsonObject(input.native ?? {}, "native Goal observation");
  if (operation === "wake") return planDelegationWake(input, session, settings, native);
  if (operation === "message") {
    const mode = requireJsonObject(session.loopx_mode ?? {}, "mode");
    const turn = requireJsonObject(input.turn ?? {}, "active execution turn");
    requireThat(mode.enabled === true && mode.paused !== true && !!session.active_turn_id
      && turn.loopx_execution === true && turn.turn_id === session.active_turn_id,
      "LoopX message delivery requires active conversation execution");
    requireThat(["queue", "inbox", "steer"].includes(String(input.delivery_mode)), "unsupported delivery mode");
    return {operation, delivery_mode: input.delivery_mode};
  }
  if (operation === "pause" || operation === "exit") {
    if (operation === "pause") requireThat(requireJsonObject(session.loopx_mode ?? {}, "mode").enabled === true,
      "pause requires enabled conversation execution");
    return {operation, enabled: operation !== "exit"};
  }
  requireThat(input.goal_active === true, "Goal is stopped or unavailable");
  requireThat(!session.active_turn_id, "wait for the current conversation turn before changing execution");
  requireThat(Number.isSafeInteger(settings.token_budget) && Number(settings.token_budget) > 0
    && Number(settings.token_budget) <= 2147483647, "set a positive coordinator token allowance");
  requireThat(typeof settings.agent_id === "string" && Array.isArray(input.registered_agents)
    && input.registered_agents.includes(settings.agent_id), "select a registered coordinator identity");
  requireThat(input.execution_binding_valid === true, "configure the coordinator's authorized execution bindings first");
  if (operation === "start") requireThat(!native.status || ["absent", "complete"].includes(String(native.status)),
    "resume the unfinished native Goal instead of replacing it");
  if (operation === "resume") {
    requireThat(["paused", "blocked", "usageLimited", "budgetLimited"].includes(String(native.status)),
      "resume requires a paused, blocked or limited native Goal");
    requireThat(Number(settings.token_budget) > Number(native.tokensUsed ?? 0), "total allowance must exceed consumed tokens");
  }
  return {operation, enabled: operation !== "configure", settings};
}

const RESUMABLE_NATIVE = ["paused", "blocked", "usageLimited", "budgetLimited"];

/** A delegated result was accepted; decide only whether the lead may continue now.
 *
 * The intent is pinned to the conversation whose Turn started the operation;
 * no other conversation of the same Goal and coordinator can consume it, and
 * a closed or exited origin is refused rather than handed over implicitly.
 *
 * ``wake_turn`` is the Turn already accepted under this intent's client id in
 * that conversation, if any.  Only a dispatched Turn is dispatch evidence: it
 * is recorded as ``woken`` without another dispatch.  A still-queued Turn is
 * not; it is replayed through the native acceptance owner under the same
 * admission as a new wake, so pause and revocation still hold.  Dispatch is
 * proven by ``upstream_turn_id``, which the runtime writes when the provider
 * reports ``turn.started`` — not by ``started_at``, which the worker stamps
 * before it builds the context and reaches the provider, so a Turn that is
 * merely activating stays pending.  A Turn that ended without dispatch is
 * refused, since its client id cannot admit another Turn.
 *
 * The same facts that admit an owner resume admit a host wake, plus mode
 * enabled and not paused.  A refusal is terminal for that intent; pending
 * keeps it for a later tick.  A running Turn is checked before the native
 * status: while a start is still activating, "absent" or a previous run's
 * "complete" is not yet a stable fact.  A wake never unpauses the lead, never
 * starts a native Goal and never raises the conversation allowance. */
function planDelegationWake(input: JsonObject, session: JsonObject, settings: JsonObject, native: JsonObject): JsonObject {
  const mode = requireJsonObject(session.loopx_mode ?? {}, "mode");
  const intent = requireJsonObject(input.intent, "wake intent");
  const requester = requireJsonObject(intent.requester, "wake requester");
  const conversation = requireJsonObject(intent.conversation, "wake conversation");
  requireThat(typeof intent.intent_id === "string" && BARE_SHA256_PATTERN.test(intent.intent_id), "invalid wake intent");
  const outcome = (state: "pending" | "refused", reason: string) => ({operation: "wake", state, reason});
  if (conversation.session_id !== session.session_id || requester.goal_id !== session.goal_id) {
    return outcome("refused", "wake_identity_conflict");
  }
  const turn = input.wake_turn == null ? null : requireJsonObject(input.wake_turn, "wake Turn");
  if (turn !== null) {
    // Another request owns this client id; claiming it would be a false receipt.
    if (turn.loopx_execution !== true || turn.operation !== "wake" || turn.intent_id !== intent.intent_id) {
      return outcome("refused", "wake_identity_conflict");
    }
    // `upstream_turn_id` is written when the provider reports `turn.started`,
    // so it is evidence that a Turn was actually dispatched. `started_at` is
    // earlier than that: the worker stamps it before it builds the turn
    // context, prepares LoopX mode and hands the message to the adapter, so
    // recording `woken` on it would claim a dispatch that may never happen and
    // lose the intent, since a terminal receipt is never rescanned.
    if (typeof turn.upstream_turn_id === "string" && turn.upstream_turn_id) {
      return {operation: "wake", state: "woken", reason: null, dispatch: "recorded"};
    }
    // Ended without ever being dispatched: its client id cannot admit another
    // Turn, but the owner must be told rather than left waiting.
    if (isTerminalTurnStatus(turn.status)) return outcome("refused", "wake_turn_ended_unstarted");
    // Accepted but not yet dispatched, including a start still activating.
    if (turn.status !== "queued") return outcome("pending", "wake_dispatch_pending");
  }
  if (input.goal_active !== true) return outcome("refused", "goal_stopped");
  if (session.status === "closed" || mode.enabled !== true) return outcome("refused", "no_wake_owner");
  if (settings.agent_id !== requester.agent_id) return outcome("refused", "wake_identity_conflict");
  if (!(typeof settings.agent_id === "string" && Array.isArray(input.registered_agents)
    && input.registered_agents.includes(settings.agent_id))) return outcome("refused", "lead_unbound");
  if (input.execution_binding_valid !== true) return outcome("refused", "binding_revoked");
  if (session.active_turn_id && session.active_turn_id !== turn?.turn_id) return outcome("pending", "lead_turn_active");
  // The wake's own queued Turn has not run, so the native status is still the lead's.
  const status = String(native.status ?? "absent");
  if (status === "complete") return outcome("refused", "native_goal_complete");
  if (status === "absent") return outcome("refused", "native_goal_absent");
  if (mode.paused === true) return outcome("pending", "lead_paused");
  if (!RESUMABLE_NATIVE.includes(status)) return outcome("pending", "lead_turn_active");
  if (!(Number.isSafeInteger(settings.token_budget) && Number(settings.token_budget) > 0
    && Number(settings.token_budget) <= 2147483647
    && Number(settings.token_budget) > Number(native.tokensUsed ?? 0))) return outcome("pending", "allowance_exhausted");
  return {operation: "wake", state: "admitted", reason: null, dispatch: turn === null ? "create" : "replay", settings};
}
