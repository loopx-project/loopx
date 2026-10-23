import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  requireBoolean,
  requireJsonObject,
  requireNonEmptyString,
  requireStringLiteral,
} from "../runtime_decode.ts";
import { BARE_SHA256_PATTERN, ENVELOPED_SHA256_PATTERN } from "../content_digest.ts";
import { createHash, randomUUID } from "node:crypto";
import { constants } from "node:fs";
import { open, readdir } from "node:fs/promises";
import { isAbsolute, join, relative, resolve, toNamespacedPath } from "node:path";
import { isDeepStrictEqual } from "node:util";
import { atomicWriteJson, withFileMutationLock } from "../effect_runtime_io.ts";
import { parseTodoTimestampMicros } from "../runtime_timestamp.ts";
import { decideCollaborationLifecycle } from "./goal_instance_lifecycle.ts";
import { resolveConversationScope } from "./conversation_scope.ts";
import { planCollaborationResult, collaborationResultDeliveryReady } from "./result_publication.ts";

export const MANAGER_RETURN_DELIVERY_ATTEMPT_SCHEMA =
  "manager_return_delivery_attempt_v0";

const PROVIDER = /^[a-z][a-z0-9_-]{0,31}$/;
const OPAQUE_REF = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$/;
const DIGEST = ENVELOPED_SHA256_PATTERN;
const ATTEMPT_KEYS = [
  "schema_version",
  "provider",
  "message_ref",
  "intent_digest",
  "provider_receipt",
] as const;

function matchingString(value: unknown, label: string, pattern: RegExp): string {
  const result = requireNonEmptyString(value, label);
  if (!pattern.test(result)) {
    throw new EffectRuntimeRequestError(`${label} is invalid`);
  }
  return result;
}

/**
 * The provider locator for one recorded attempt, when the provider gave one.
 *
 * `null` is the typed state for "the provider accepted the write and reported
 * no message id". Such an attempt is still the durable record that a write
 * happened; what it cannot do is name a readback target. Keeping the key
 * required and the absence explicit is what stops a later retry from treating
 * an unlocatable write as a write that never happened.
 */
function optionalOpaqueRef(value: unknown, label: string): string | null {
  if (value === null) return null;
  return matchingString(value, label, OPAQUE_REF);
}

export function normalizeManagerReturnDeliveryAttempt(value: unknown): JsonObject {
  const attempt = requireJsonObject(value, "attempt");
  const keys = Object.keys(attempt).sort();
  if (keys.length !== ATTEMPT_KEYS.length
    || ATTEMPT_KEYS.some((key) => !keys.includes(key))) {
    throw new EffectRuntimeRequestError(
      "manager return delivery attempt has unsupported or missing fields",
    );
  }
  if (attempt.schema_version !== MANAGER_RETURN_DELIVERY_ATTEMPT_SCHEMA) {
    throw new EffectRuntimeRequestError(
      "manager return delivery attempt schema is invalid",
    );
  }
  return {
    schema_version: MANAGER_RETURN_DELIVERY_ATTEMPT_SCHEMA,
    provider: matchingString(attempt.provider, "attempt.provider", PROVIDER),
    message_ref: optionalOpaqueRef(attempt.message_ref, "attempt.message_ref"),
    intent_digest: matchingString(
      attempt.intent_digest,
      "attempt.intent_digest",
      DIGEST,
    ),
    provider_receipt: matchingString(
      attempt.provider_receipt,
      "attempt.provider_receipt",
      DIGEST,
    ),
  };
}

export function classifyManagerReturnVerification(value: unknown): JsonObject {
  const outcome = requireJsonObject(value, "outcome");
  const performed = requireBoolean(
    outcome.verification_performed,
    "outcome.verification_performed",
  );
  const verified = requireBoolean(
    outcome.reply_verified,
    "outcome.reply_verified",
  );
  if (verified && !performed) {
    throw new EffectRuntimeRequestError(
      "a verified return requires a completed verification read",
    );
  }
  if (verified) {
    return {
      status: "delivered",
      error: null,
      verification: "reconciled_after_restart",
    };
  }
  if (!performed) {
    // A transport outage is not proof that the original route or grant was
    // revoked. Only exact, adapter-declared resolution reasons stop readback.
    const blocker = outcome.blocker;
    if (blocker === "return_authorization_unavailable"
      || blocker === "original_route_unavailable"
      || blocker === "initial_delivery_receipt_unavailable") {
      return { status: "explicit_unverified", error: blocker, verification: null };
    }
    return {
      status: "verification_required",
      error: "provider_verification_unavailable",
      verification: null,
    };
  }
  const blocker = typeof outcome.blocker === "string" ? outcome.blocker : "";
  const error = [
    "provider_delivery_intent_conflict",
    "provider_message_missing",
  ].includes(blocker)
    ? blocker
    : "provider_delivery_mismatch";
  return { status: "explicit_unverified", error, verification: null };
}

export const RETURN_DELIVERY_TRANSACTION = "manager_return_delivery_transaction_v0";
const RESULT_KEY = /^(decision|conclusion|conclusion-[0-9]{8}|decision-[0-9]{8})$/;
const TERMINAL = new Set(["delivered", "superseded", "explicit_unverified"]);

interface ReturnCommand {
  request: JsonObject;
  folder: string;
  key: string;
  path: string;
  now: string;
  exact: boolean;
  facts: JsonObject;
}

function returnAddress(value: unknown): {request_id: string; result_key: string} {
  const row = requireJsonObject(value, "return candidate");
  return {request_id: matchingString(row.request_id, "request_id", BARE_SHA256_PATTERN),
    result_key: matchingString(row.result_key, "result_key", RESULT_KEY)};
}

function returnCommand(value: unknown): ReturnCommand {
  const request = requireJsonObject(value, "return delivery transaction");
  if (request.schema_version !== RETURN_DELIVERY_TRANSACTION) {
    throw new EffectRuntimeRequestError("return delivery transaction schema mismatch");
  }
  const root = requireNonEmptyString(request.runtime_root, "runtime_root");
  if (!isAbsolute(root)) throw new EffectRuntimeRequestError("return runtime root must be absolute");
  const {request_id: id, result_key: key} = returnAddress(request);
  const now = requireNonEmptyString(request.now, "now");
  if (now.length > 64 || parseTodoTimestampMicros(now) === null) throw new EffectRuntimeRequestError("invalid return clock");
  requireStringLiteral(request.phase, ["prepare", "attempt", "settle"], "return delivery phase");
  const folder = join(root, ".local", "manager-context");
  return { request, folder, key, now,
    path: join(folder, "replies", id, `${key}.delivery.json`),
    exact: requireBoolean(request.exact, "exact"),
    facts: requireJsonObject(request.facts, "return source facts") };
}

async function readReturnRecord(path: string, absent: JsonObject | null = null): Promise<JsonObject | null> {
  try {
    const file = await open(path, constants.O_RDONLY | (constants.O_NONBLOCK ?? 0));
    try {
      const metadata = await file.stat();
      if (!metadata.isFile() || metadata.size > 128000) {
        throw new EffectRuntimeRequestError("manager context record too large or not regular");
      }
      const bytes = Buffer.alloc(Math.min(metadata.size + 1, 128001));
      let length = 0;
      while (length < bytes.length) {
        const read = await file.read(bytes, length, bytes.length - length, null);
        if (!read.bytesRead) break;
        length += read.bytesRead;
      }
      if (length > 128000) throw new EffectRuntimeRequestError("manager context record too large");
      const text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes.subarray(0, length));
      return requireJsonObject(JSON.parse(text), "manager context record");
    } finally { await file.close(); }
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return absent;
    throw error;
  }
}

function returnResponse(fields: JsonObject = {}): JsonObject {
  return { schema_version: RETURN_DELIVERY_TRANSACTION, action: "skip", processed: 0, ...fields };
}

function unavailableRecord(error: unknown): boolean {
  return error instanceof SyntaxError || error instanceof EffectRuntimeRequestError
    || typeof (error as NodeJS.ErrnoException)?.code === "string";
}

function pendingDelivery(command: ReturnCommand, state: JsonObject): boolean {
  if (TERMINAL.has(String(state.status))) return false;
  if (!command.exact || state.status !== "admitted") {
    if (state.retry_at && command.now < String(state.retry_at)) return false;
  }
  const admission = state.admission;
  return !(command.exact && admission && typeof admission === "object"
    && (admission as JsonObject).expires_at
    && command.now < String((admission as JsonObject).expires_at));
}

function deliveryState(value: unknown): JsonObject {
  const state = requireJsonObject(value, "delivery state");
  if (Object.keys(state).length) requireStringLiteral(state.status,
    [...TERMINAL, "queued", "retry_pending", "verification_required", "admitted"], "delivery status");
  return state;
}

async function scanReturns(request: JsonObject): Promise<JsonObject> {
  if (request.schema_version !== RETURN_DELIVERY_TRANSACTION) {
    throw new EffectRuntimeRequestError("return delivery transaction schema mismatch");
  }
  if (!Array.isArray(request.candidates) || request.candidates.length > 128) {
    throw new EffectRuntimeRequestError("return scan requires at most 128 candidates");
  }
  const candidates = [];
  let unavailable = 0;
  for (const candidate of request.candidates) {
    let address: ReturnType<typeof returnAddress>;
    try { address = returnAddress(candidate); }
    catch (error) {
      if (!(error instanceof EffectRuntimeRequestError)) throw error;
      unavailable += 1;
      continue;
    }
    // A damaged address is local to that item; an invalid transaction envelope
    // still rejects the scan. Candidate fields cannot replace its root or scope.
    const command = returnCommand({...request, ...address, phase: "prepare", facts: {}});
    try {
      const state = deliveryState(await readReturnRecord(command.path, {}));
      if (pendingDelivery(command, state)) candidates.push({ request_id: command.request.request_id, result_key: command.key });
    } catch (error) { if (!unavailableRecord(error)) throw error; unavailable += 1; }
  }
  return returnResponse({ candidates, unavailable_count: unavailable });
}

function shiftedClock(now: string, seconds: number): string {
  // Preserve Python's offset/fraction representation, used by existing lexical retry fences.
  const match = /^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?(Z|[+-]\d\d:\d\d(?::\d\d(?:\.\d+)?)?)?$/.exec(now);
  if (!match) throw new EffectRuntimeRequestError("invalid return clock representation");
  return new Date(Date.parse(`${match[1]}Z`) + seconds * 1000).toISOString().slice(0, 19)
    + (match[2] ?? "") + (match[3] ?? "");
}

function retryState(state: JsonObject, command: ReturnCommand, error: unknown): JsonObject {
  const attempts = Number(state.attempts ?? 0) + 1;
  if (!Number.isSafeInteger(attempts) || attempts < 1) {
    throw new EffectRuntimeRequestError("invalid return retry count");
  }
  const retained = Object.fromEntries(Object.entries(state).filter(([key]) =>
    !["admission", "delivered_at", "message_id", "verification"].includes(key)));
  return { ...retained, status: "retry_pending", attempts, error,
    retry_at: shiftedClock(command.now, Math.min(300, 5 * 2 ** Math.min(attempts, 6))) };
}

function deliveryRevision(state: JsonObject): string {
  return createHash("sha256").update(JSON.stringify(state)).digest("hex");
}

async function writeDeliveryState(command: ReturnCommand, state: JsonObject): Promise<void> {
  await atomicWriteJson(command.path, state);
}

function admitReturnLifetime(command: ReturnCommand, operation: string): void {
  const facts = command.facts;
  const decision = decideCollaborationLifecycle({
    ...requireJsonObject(facts.scope, "return Goal scope"), operation,
    record_goal_ref: requireJsonObject(facts.entry, "request").goal_ref ?? null,
    route_goal_ref: requireJsonObject(facts.route, "return route").goal_ref ?? null,
    initial_delivery: facts.initial_delivery,
  });
  if (decision.kind === "reject" || decision.kind === "omit") {
    throw new EffectRuntimeRequestError("return Goal lifetime admission rejected");
  }
}

async function inspectReturnPublication(command: ReturnCommand): Promise<{ reply: JsonObject; inspected: JsonObject }> {
  const directory = join(command.folder, "replies", String(command.request.request_id));
  const reply = requireJsonObject(await readReturnRecord(join(directory, `${command.key}.json`)), "return reply");
  const entry = requireJsonObject(command.facts.entry, "request");
  if (entry.request_id !== command.request.request_id) {
    throw new EffectRuntimeRequestError("return request identity mismatch");
  }
  const entryPath = requireNonEmptyString(command.facts.entry_path, "entry_path");
  if (!isAbsolute(entryPath)) throw new EffectRuntimeRequestError("return entry path must be absolute");
  const address = relative(toNamespacedPath(join(command.folder, "entries")), toNamespacedPath(resolve(entryPath))).split(/[\\/]/);
  if (address.length !== 2 || !BARE_SHA256_PATTERN.test(address[0])
      || address[1] !== `${command.request.request_id}.json`
      || !isDeepStrictEqual(await readReturnRecord(entryPath), entry)) {
    throw new EffectRuntimeRequestError("return request source changed");
  }
  const route = requireJsonObject(command.facts.route, "return route");
  if (!isDeepStrictEqual(await readReturnRecord(join(command.folder, "roundtrips", `${entry.request_id}.json`)), route)
      || ["request_id", "goal_id", "agent_id", "source_id", "goal_ref"].some(key =>
        entry[key] !== undefined && !isDeepStrictEqual(entry[key], route[key]))) {
    throw new EffectRuntimeRequestError("return route identity changed");
  }
  const keys = reply.update_id !== undefined || reply.phase === "decision"
    ? (await readdir(directory)).filter(name => name.endsWith(".json") && RESULT_KEY.test(name.slice(0, -5)))
    : [`${command.key}.json`];
  const results = [];
  for (const name of keys.sort()) {
    const observed = requireJsonObject(await readReturnRecord(join(directory, name)), "return result");
    const text = requireNonEmptyString(observed.text, "return result text");
    if (Buffer.from(text, "utf8").toString("utf8") !== text) {
      throw new EffectRuntimeRequestError("return result text is not valid UTF-8");
    }
    results.push({ key: name.slice(0, -5), value: observed,
      text_chars: Array.from(text).length,
      text_sha256: createHash("sha256").update(text).digest("hex") });
  }
  let legacy: JsonObject | null = null;
  try { legacy = await readReturnRecord(join(command.folder, "decisions", `${entry.request_id}.json`)); }
  catch { /* The shared historical-revision decoder rejects unavailable assessment evidence. */ }
  const inspected = planCollaborationResult({ request: entry, phase: reply.phase, text: reply.text,
    decision: reply.decision, update_id: reply.update_id, attachments: reply.attachments,
    request_revision: reply.request_revision, legacy_assessment: legacy, results });
  if ((reply.result_key ?? reply.phase) !== command.key || entry.request_id !== command.request.request_id) {
    throw new EffectRuntimeRequestError("return reply identity mismatch");
  }
  return { reply, inspected };
}

function savedAttempt(attempt: unknown): JsonObject | null {
  if (attempt == null) return null;
  try { return normalizeManagerReturnDeliveryAttempt(attempt); }
  catch (error) { if (error instanceof EffectRuntimeRequestError) return null; throw error; }
}

function attemptLocator(attempt: unknown): string | null {
  return savedAttempt(attempt)?.message_ref as string | null ?? null;
}

async function sourceFailure(command: ReturnCommand, state: JsonObject, blocker: unknown = command.facts.error): Promise<JsonObject> {
  if (command.exact) return returnResponse({ source_unavailable: true });
  const decision = classifyManagerReturnVerification({ verification_performed: false,
    reply_verified: false, blocker });
  if (state.status === "verification_required" && state.attempt != null) {
    if (decision.status === "explicit_unverified" || attemptLocator(state.attempt) === null) {
      await writeDeliveryState(command, { ...state, status: "explicit_unverified",
        error: decision.status === "explicit_unverified" ? decision.error : "provider_locator_unavailable" });
    }
  } else await writeDeliveryState(command, retryState({ attempts: state.attempts ?? 0 }, command,
    decision.status === "explicit_unverified" ? decision.error : "original_route_or_return_delivery_unavailable"));
  return returnResponse({ processed: 1 });
}

function returnAuthorityBlocker(command: ReturnCommand): string | null {
  // Null is the Host's completed lookup of a missing original record. IO errors
  // arrive separately in facts.error and retain their retryable classification.
  if (command.facts.session === null || command.facts.turn === null) return "original_route_unavailable";
  const session = requireJsonObject(command.facts.session, "source session");
  requireJsonObject(command.facts.turn, "source turn");
  const route = requireJsonObject(command.facts.route, "source route");
  const target = requireJsonObject(command.facts.entry, "request");
  const allowed = requireJsonObject(command.facts.grant, "return grant");
  if (session.status === "closed" || session.channel_id !== route.channel_id) return "original_route_unavailable";
  if (allowed.source_id !== target.source_id || !Array.isArray(allowed.targets)
      || !allowed.targets.some(raw => isDeepStrictEqual(raw, { goal_id: target.goal_id, agent_id: target.agent_id }))) {
    return "return_authorization_unavailable";
  }
  return null;
}

async function prepareReturn(command: ReturnCommand, state: JsonObject): Promise<JsonObject> {
  if (!pendingDelivery(command, state)) return returnResponse();
  if (command.facts.error) return sourceFailure(command, state);
  let source: Awaited<ReturnType<typeof inspectReturnPublication>>;
  try {
    source = await inspectReturnPublication(command);
    const blocker = returnAuthorityBlocker(command);
    if (blocker) return sourceFailure(command, state, blocker);
    if (requireJsonObject(command.facts.route, "source route").kind === "peer") return returnResponse();
    admitReturnLifetime(command, "original_return_admit");
  } catch (error) {
    if (error instanceof EffectRuntimeRequestError || error instanceof SyntaxError
        || (error as NodeJS.ErrnoException).code) return sourceFailure(command, state);
    throw error;
  }
  const { reply, inspected } = source;
  // Decode the persisted predecessor before using it as a filesystem address.
  collaborationResultDeliveryReady({ result: reply, previous_delivery: null });
  const previous = reply.previous_result_key == null ? null
    : await readReturnRecord(join(command.folder, "replies", String(reply.request_id), `${reply.previous_result_key}.delivery.json`));
  if (!collaborationResultDeliveryReady({ result: reply, previous_delivery: previous }).ready) return returnResponse();
  const privateConversation = resolveConversationScope(requireJsonObject(command.facts.session, "session")).private_conversation;
  const admission = state.admission == null ? null : requireJsonObject(state.admission, "return admission");
  if (command.exact) {
    if (state.status === "admitted" && state.attempt == null && !privateConversation) {
      await writeDeliveryState(command, { status: "explicit_unverified", error: "provider_delivery_unverified", goal_ref: reply.goal_ref });
      return returnResponse();
    }
  }
  if (inspected.interim_superseded) {
    await writeDeliveryState(command, { status: "superseded", reason: "conclusion_ready",
      ...(command.exact ? { goal_ref: reply.goal_ref } : {}) });
    return returnResponse();
  }
  const priorStatus = (command.exact ? admission?.prior_status : null) ?? state.status ?? "queued";
  if (command.exact) {
    state = { ...state, status: "admitted", goal_ref: reply.goal_ref,
      admission: { token: randomUUID().replaceAll("-", ""), prior_status: priorStatus, admitted_at: command.now,
        expires_at: shiftedClock(command.now, 60) } };
    delete state.retry_at;
    await writeDeliveryState(command, state);
  }
  const verify = priorStatus === "verification_required" || command.exact && state.attempt != null;
  const prefix = command.exact ? (reply.phase === "conclusion" ? "处理结论" : "处理进展")
    : (reply.phase === "conclusion" ? "协作回复" : "协作进展");
  const text = `${prefix} · ${reply.agent_id} · 委托 ${String(reply.request_id).slice(0, 8)}\n\n${reply.text}`;

  return returnResponse({ action: privateConversation ? "private" : verify ? "verify" : "send",
    text, message_id: returnMessageId(command), attempt: verify ? savedAttempt(state.attempt) : null, attachments: reply.attachments ?? [],
    revision: deliveryRevision(state), token: (state.admission as JsonObject | undefined)?.token ?? null });
}

function returnMessageId(command: ReturnCommand): string {
  // Retain Python json.dumps' spaces in the existing ASCII-only message identity.
  const identity = JSON.stringify([command.request.request_id, command.key]).replace(",", ", ");
  return "handoff." + createHash("sha256").update(identity).digest("hex");
}

function deliveryOutcome(command: ReturnCommand, state: JsonObject): JsonObject {
  const outcome = requireJsonObject(command.request.outcome, "return provider outcome");
  requireStringLiteral(outcome.kind, ["private", "sent", "conflict", "verify_unavailable", "locator_unavailable",
    "verified", "error", "verification_error", "sender_unavailable"], "return outcome kind");
  if (outcome.kind === "private" || outcome.kind === "sent" && outcome.reply_verified === true) {
    return { status: "delivered", delivered_at: command.now, message_id: returnMessageId(command),
      ...(outcome.kind === "sent" ? { provider_receipt: outcome.idempotency_key, reply_verified: true } : {}) };
  }
  if (outcome.kind === "conflict") return { status: "explicit_unverified", error: "manager_return_payload_conflict" };
  if (outcome.kind === "verify_unavailable" || outcome.kind === "locator_unavailable") {
    return { status: "explicit_unverified", error: outcome.kind === "verify_unavailable"
      ? "provider_verifier_unavailable" : "provider_locator_unavailable" };
  }
  if (outcome.kind === "verified") {
    let checked: JsonObject;
    try { checked = classifyManagerReturnVerification(outcome.verification); }
    catch (error) {
      if (!(error instanceof EffectRuntimeRequestError)) throw error;
      return { ...retryState(state, command, "provider_verification_unavailable"),
        status: command.exact ? "retry_pending" : "verification_required" };
    }
    return checked.status === "delivered"
      ? { status: "delivered", delivered_at: command.now, message_id: returnMessageId(command),
        provider_receipt: normalizeManagerReturnDeliveryAttempt(state.attempt).provider_receipt,
        reply_verified: true, verification: checked.verification }
      : checked.status === "explicit_unverified" ? { status: checked.status, error: checked.error }
      : { ...retryState(state, command, checked.error), status: command.exact ? "retry_pending" : "verification_required" };
  }
  if (outcome.kind === "sent" && outcome.external_write_performed === true) {
    return { ...(state.attempt == null ? {} : { attempt: state.attempt }),
      status: attemptLocator(state.attempt) === null ? "explicit_unverified" : "verification_required",
      error: attemptLocator(state.attempt) === null ? "provider_locator_unavailable" : "provider_delivery_unverified" };
  }
  const checked = classifyManagerReturnVerification({ verification_performed: false,
    reply_verified: false, blocker: outcome.blocker ?? null });
  if (state.attempt != null && (checked.status === "explicit_unverified" || attemptLocator(state.attempt) === null)) {
    return { status: "explicit_unverified",
      error: checked.status === "explicit_unverified" ? checked.error : "provider_locator_unavailable" };
  }
  if (!command.exact && state.status === "verification_required" && state.attempt != null) {
    if (outcome.kind === "error") return state;
    return { ...retryState(state, command, "provider_verification_unavailable"), status: "verification_required" };
  }
  return retryState(command.exact ? state : { attempts: state.attempts ?? 0 }, command, outcome.kind === "sender_unavailable"
    ? "return_transport_unavailable" : outcome.kind === "verification_error"
      ? "provider_verification_unavailable" : checked.status === "explicit_unverified"
        // A typed failure before any accepted write preserves its cause while
        // remaining retryable. Ordinary exception text never supplies it.
        ? checked.error : "original_route_or_return_delivery_unavailable");
}

async function finishReturn(command: ReturnCommand, state: JsonObject): Promise<JsonObject> {
  if (command.request.revision !== deliveryRevision(state)) throw new EffectRuntimeRequestError("return delivery state changed");
  const source = await inspectReturnPublication(command);
  admitReturnLifetime(command, "original_return_settle");
  if (command.exact) {
    if ((state.admission as JsonObject | undefined)?.token !== command.request.token) {
      throw new EffectRuntimeRequestError("exact return delivery admission changed");
    }
  }
  if (command.request.phase === "attempt") {
    const attempt = normalizeManagerReturnDeliveryAttempt(command.request.attempt);
    if (state.attempt != null && !isDeepStrictEqual(normalizeManagerReturnDeliveryAttempt(state.attempt), attempt)) {
      throw new EffectRuntimeRequestError("manager return delivery attempt conflict");
    }
    const checkpoint = command.exact ? { ...state, attempt }
      : { status: "verification_required", error: "provider_delivery_unverified", attempt };
    await writeDeliveryState(command, checkpoint);
    return returnResponse({ revision: deliveryRevision(checkpoint), token: command.request.token });
  }
  if (command.request.phase !== "settle") throw new EffectRuntimeRequestError("unsupported return delivery phase");
  const settled = deliveryOutcome(command, state);
  if (requireJsonObject(command.request.outcome, "return outcome").kind === "private"
      && !resolveConversationScope(requireJsonObject(command.facts.session, "source session")).private_conversation) {
    throw new EffectRuntimeRequestError("external return requires a provider outcome");
  }
  if (settled.status === "explicit_unverified" && state.attempt != null) settled.attempt = state.attempt;
  if (command.exact) settled.goal_ref = requireJsonObject(command.facts.entry, "request").goal_ref;
  if (source.reply.result_key !== undefined) settled.result_key = source.reply.result_key;
  if (!isDeepStrictEqual(settled, state)) await writeDeliveryState(command, settled);
  const outcome = requireJsonObject(command.request.outcome, "return outcome");
  return returnResponse({ processed: command.exact || settled.status === "delivered"
    || ["error", "conflict", "sender_unavailable"].includes(String(outcome.kind)) ? 1 : 0,
    observe_verified: command.exact && settled.status === "delivered" && settled.reply_verified === true });
}

/** One locked owner for admission, provider-attempt checkpoints and delivery receipts. */
export async function transactManagerReturnDelivery(value: unknown): Promise<JsonObject> {
  const request = requireJsonObject(value, "return delivery transaction");
  if (request.phase === "scan") return scanReturns(request);
  const command = returnCommand(value);
  return withFileMutationLock(command.path, async () => {
    let state: JsonObject;
    try { state = deliveryState(await readReturnRecord(command.path, {})); }
    catch (error) {
      if (command.request.phase === "prepare" && unavailableRecord(error)) return returnResponse();
      throw error;
    }
    return command.request.phase === "prepare" ? prepareReturn(command, state) : finishReturn(command, state);
  });
}
