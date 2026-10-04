import { createHash } from "node:crypto";
import { open, readFile } from "node:fs/promises";
import { dirname, isAbsolute, join, resolve } from "node:path";

import {
  POST_WRITEBACK_HOOK_INPUT_SCHEMA_VERSION,
  POST_WRITEBACK_HOOK_RECEIPT_SCHEMA_VERSION,
  POST_WRITEBACK_HOOK_REGISTRATION_SCHEMA_VERSION,
  validatePostWritebackHookInput,
  validatePostWritebackHookInvocation,
  validatePostWritebackHookReceipt,
  validatePostWritebackHookRegistration,
} from "./capability_hooks.ts";
import type { JsonObject } from "./effect_program.ts";
import { EffectRuntimeRequestError } from "./effect_runtime_errors.ts";
import {
  atomicWriteJson,
  withFileMutationLock,
} from "./effect_runtime_io.ts";
import {
  jsonObject,
  optionalNonEmptyString,
  requireBoolean,
  requireInteger,
  requireJsonObject,
  requireNonEmptyString,
  requireStringLiteral,
} from "./runtime_decode.ts";

export const POST_WRITEBACK_HOOK_SOURCE_SCHEMA_VERSION =
  "loopx_post_writeback_hook_source_v0";
// Source decoding rejections are one bounded input-construction category,
// distinguishable from every other runtime/transport fault so the Python
// adapter can project them as composition failures without re-owning the
// source field rules, which stay owned by this decoder alone.
export const POST_WRITEBACK_SOURCE_REJECTION_CODE =
  "post_writeback_source_invalid";
export const POST_WRITEBACK_HOOK_TRANSACTION_REQUEST_SCHEMA_VERSION =
  "loopx_post_writeback_hook_transaction_request_v0";
export const POST_WRITEBACK_HOOK_TRANSACTION_RESULT_SCHEMA_VERSION =
  "loopx_post_writeback_hook_transaction_result_v0";
export const POST_WRITEBACK_HOOK_DISPATCH_SCHEMA_VERSION =
  "loopx_post_writeback_capability_hook_dispatch_v0";

const PHASES = ["preflight", "finalize"] as const;
const OUTCOME_STATUSES = [
  "returned",
  "producer_failed",
  "contract_rejected",
  "lock_unavailable",
  "lock_failed",
  "receipt_changed",
] as const;
const SAFE_PATH_SEGMENT = /^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$/;
const DISPATCH_ID = /^pwh_[0-9a-f]{64}$/;
const MAX_TRANSACTION_RESULT_BYTES = 1_750_000;
// Covers array commas and count digit growth; result and hook bytes are added exactly.
const FINAL_RESULT_PLAN_OVERHEAD_BYTES = 16;
// The managed RPC has a five-second response budget. Share one shorter wait
// budget across the batch so contended receipts cannot multiply that timeout.
const FINALIZE_MUTATION_LOCK_BUDGET_MS = 1_000;
const PYTHON_STRIP_SINGLE_CODE_POINTS = new Set([
  0x85,
  0xA0,
  0x1680,
  0x2028,
  0x2029,
  0x202F,
  0x205F,
  0x3000,
]);

type TransactionPhase = (typeof PHASES)[number];
type ProviderOutcomeStatus = (typeof OUTCOME_STATUSES)[number];

interface PostWritebackSource extends JsonObject {
  schema_version: typeof POST_WRITEBACK_HOOK_SOURCE_SCHEMA_VERSION;
  event_kind: string;
  status: string;
  durable: boolean;
  identity: JsonObject & { goal_id: string; todo_id: string | null };
  state_version: string;
  receipt_id?: string | null;
  committed_at: string;
  projection: JsonObject;
}

interface ProviderOutcome {
  dispatch_id: string;
  hook_id: string;
  capability_id: string;
  attempt_count: number;
  status: ProviderOutcomeStatus;
  result: JsonObject | null;
}

interface TransactionRequest {
  phase: TransactionPhase;
  runtime_root: string | null;
  source: PostWritebackSource | null;
  hook_input: JsonObject | null;
  registrations: unknown[];
  transaction_id: string | null;
  provider_outcomes: ProviderOutcome[];
}

interface ProviderPlan extends JsonObject {
  dispatch_id: string;
  hook_id: string;
  capability_id: string;
  attempt_count: number;
  retry: boolean;
  receipt_snapshot: string;
  hook_input: JsonObject;
}

interface AdmittedHook {
  contract: JsonObject;
  registration: JsonObject & {
    hook_id: string;
    capability_id: string;
    policy_version: string;
    requested_read_scope: string[];
    max_result_bytes: number;
  };
  hook_input: JsonObject;
  dispatch_id: string;
}

type InspectionResultSlot =
  | { kind: "failure"; failure: JsonObject }
  | { kind: "hook"; dispatch_id: string };

interface Inspection {
  registered_count: number;
  plans: ProviderPlan[];
  result_slots: InspectionResultSlot[];
  admitted: Map<string, AdmittedHook>;
  known_dispatch_ids: Set<string>;
  blocked_dispatch_ids: Set<string>;
  intents: JsonObject[];
  failures: JsonObject[];
  replayed_hooks: string[];
  retried_hooks: string[];
  seen_intent_keys: Set<string>;
  terminal_receipts: Map<string, JsonObject>;
}

interface ReceiptRead {
  state: "missing" | "present" | "malformed";
  receipt: JsonObject | null;
  snapshot: string;
}

type ReceiptStoreResult =
  | { status: "stored"; receipt: JsonObject }
  | { status: "replayed"; receipt: JsonObject }
  | { status: "conflict"; receipt: JsonObject | null }
  | { status: "failed"; receipt: null };

type CanonicalJsonTask =
  | { kind: "text"; value: string }
  | { kind: "value"; value: unknown };

function comparePythonText(left: string, right: string): number {
  const leftCodePoints = Array.from(left, (value) => value.codePointAt(0) ?? 0);
  const rightCodePoints = Array.from(right, (value) => value.codePointAt(0) ?? 0);
  const length = Math.min(leftCodePoints.length, rightCodePoints.length);
  for (let index = 0; index < length; index += 1) {
    const difference = leftCodePoints[index] - rightCodePoints[index];
    if (difference !== 0) return difference;
  }
  return leftCodePoints.length - rightCodePoints.length;
}

function isPythonStripCodePoint(codePoint: number): boolean {
  return (codePoint >= 0x09 && codePoint <= 0x0D) ||
    (codePoint >= 0x1C && codePoint <= 0x20) ||
    (codePoint >= 0x2000 && codePoint <= 0x200A) ||
    PYTHON_STRIP_SINGLE_CODE_POINTS.has(codePoint);
}

function stripPythonWhitespace(value: string): string {
  let start = 0;
  let end = value.length;
  while (
    start < end &&
    isPythonStripCodePoint(value.codePointAt(start) ?? -1)
  ) {
    start += 1;
  }
  while (
    end > start &&
    isPythonStripCodePoint(value.codePointAt(end - 1) ?? -1)
  ) {
    end -= 1;
  }
  return value.slice(start, end);
}

/**
 * Match Python json.dumps(sort_keys=True, separators=(",", ":")) identity
 * bytes for the string, boolean, and null shapes this contract carries.
 * Floats and integers beyond Number.MAX_SAFE_INTEGER differ from CPython's
 * repr (exponent zero-padding and format thresholds), so cross-language
 * digest equality must not rely on those shapes.
 */
function pythonCanonicalJson(value: unknown): string {
  const chunks: string[] = [];
  const tasks: CanonicalJsonTask[] = [{ kind: "value", value }];
  while (tasks.length > 0) {
    const task = tasks.pop();
    if (task === undefined) break;
    if (task.kind === "text") {
      chunks.push(task.value);
      continue;
    }
    const nested = task.value;
    if (nested === null || typeof nested !== "object") {
      const encoded = JSON.stringify(nested);
      if (encoded === undefined) {
        throw new EffectRuntimeRequestError(
          "canonical JSON value is not serializable",
        );
      }
      chunks.push(encoded);
      continue;
    }
    if (Array.isArray(nested)) {
      chunks.push("[");
      tasks.push({ kind: "text", value: "]" });
      for (let index = nested.length - 1; index >= 0; index -= 1) {
        if (index < nested.length - 1) {
          tasks.push({ kind: "text", value: "," });
        }
        tasks.push({ kind: "value", value: nested[index] });
      }
      continue;
    }
    const source = nested as Record<string, unknown>;
    const keys = Object.keys(source).sort(comparePythonText);
    chunks.push("{");
    tasks.push({ kind: "text", value: "}" });
    for (let index = keys.length - 1; index >= 0; index -= 1) {
      if (index < keys.length - 1) {
        tasks.push({ kind: "text", value: "," });
      }
      const key = keys[index];
      tasks.push({ kind: "value", value: source[key] });
      tasks.push({ kind: "text", value: ":" });
      tasks.push({ kind: "text", value: JSON.stringify(key) });
    }
  }
  return chunks.join("").replace(
    /[\u007F-\uFFFF]/g,
    (character) => `\\u${(character.codePointAt(0) ?? 0).toString(16).padStart(4, "0")}`,
  );
}

function sha256(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function compareText(left: string, right: string): number {
  if (left < right) return -1;
  if (left > right) return 1;
  return 0;
}

function pythonStrippedString(value: unknown, label: string): string {
  if (typeof value !== "string") {
    throw new EffectRuntimeRequestError(`${label} must be a non-empty string`);
  }
  const stripped = stripPythonWhitespace(value);
  if (!stripped) {
    throw new EffectRuntimeRequestError(`${label} must be a non-empty string`);
  }
  return stripped;
}

function optionalPythonStrippedString(
  value: unknown,
  label: string,
): string | null {
  if (value === null || value === undefined || value === "") return null;
  return pythonStrippedString(value, label);
}

function boundedTransactionResult(result: JsonObject): JsonObject {
  if (
    new TextEncoder().encode(JSON.stringify(result)).byteLength >
      MAX_TRANSACTION_RESULT_BYTES
  ) {
    throw new EffectRuntimeRequestError(
      "post-writeback hook transaction result exceeds the transport envelope",
    );
  }
  return result;
}

function assertFinalResultFitsEnvelope(
  inspection: Inspection,
  transactionId: string,
  invokedCount: number,
): void {
  const encoder = new TextEncoder();
  const inspectionFailures = inspection.result_slots.flatMap((slot) =>
    slot.kind === "failure" ? [slot.failure] : []
  );
  const baseline = encoder.encode(JSON.stringify({
    schema_version: POST_WRITEBACK_HOOK_TRANSACTION_RESULT_SCHEMA_VERSION,
    phase: "finalize",
    transaction_id: transactionId,
    provider_plan: [],
    dispatch: {
      ...dispatchResult(inspection, invokedCount),
      failures: inspectionFailures,
    },
  })).byteLength;
  const pendingHookBytes = inspection.result_slots.reduce((total, slot) => {
    if (slot.kind === "failure") return total;
    const admitted = inspection.admitted.get(slot.dispatch_id);
    if (!admitted) {
      throw new EffectRuntimeRequestError("result slot lost its admitted hook");
    }
    const terminal = inspection.terminal_receipts.get(slot.dispatch_id);
    const intentBytes = terminal === undefined
      ? admitted.registration.max_result_bytes
      : terminal.intent === null
      ? 0
      : encoder.encode(JSON.stringify(terminal.intent)).byteLength;
    const replayBytes = encoder.encode(
      JSON.stringify(admitted.registration.hook_id),
    ).byteLength;
    const failureBytes = encoder.encode(JSON.stringify(hookFailure(
      admitted.registration,
      "journal_write_failed",
      `post-writeback-hook:${slot.dispatch_id}`,
    ))).byteLength;
    // A hook produces at most one intent or failure and may be reported as a
    // replay after a compare-and-swap race. Counting all three keeps admission
    // conservative before any provider runs or durable receipt is written.
    return total + intentBytes + replayBytes + failureBytes +
      FINAL_RESULT_PLAN_OVERHEAD_BYTES;
  }, 0);
  if (baseline + pendingHookBytes > MAX_TRANSACTION_RESULT_BYTES) {
    throw new EffectRuntimeRequestError(
      "post-writeback hook transaction result may exceed the transport envelope",
    );
  }
}

function exactObjectFields(
  value: JsonObject,
  expected: readonly string[],
  label: string,
): void {
  const actual = Object.keys(value).sort(compareText);
  const fields = [...expected].sort(compareText);
  if (
    actual.length !== fields.length ||
    actual.some((field, index) => field !== fields[index])
  ) {
    throw new EffectRuntimeRequestError(`${label} fields are invalid`);
  }
}

function decodeSourceFields(value: unknown): PostWritebackSource {
  const source = requireJsonObject(value, "source");
  exactObjectFields(
    source,
    [
      "schema_version",
      "event_kind",
      "status",
      "durable",
      "identity",
      "state_version",
      ...(Object.hasOwn(source, "receipt_id") ? ["receipt_id"] : []),
      "committed_at",
      "projection",
    ],
    "source",
  );
  if (source.schema_version !== POST_WRITEBACK_HOOK_SOURCE_SCHEMA_VERSION) {
    throw new EffectRuntimeRequestError("post-writeback source schema is invalid");
  }
  const identity = requireJsonObject(source.identity, "source.identity");
  exactObjectFields(
    identity,
    ["goal_id", "agent_id", "todo_id", "turn_instance_id", "effect_id"],
    "source.identity",
  );
  const normalizedIdentity: JsonObject & {
    goal_id: string;
    todo_id: string | null;
  } = {
    goal_id: pythonStrippedString(identity.goal_id, "source.identity.goal_id"),
    agent_id: pythonStrippedString(identity.agent_id, "source.identity.agent_id"),
    todo_id: optionalPythonStrippedString(
      identity.todo_id,
      "source.identity.todo_id",
    ),
    turn_instance_id: pythonStrippedString(
      identity.turn_instance_id,
      "source.identity.turn_instance_id",
    ),
    effect_id: pythonStrippedString(
      identity.effect_id,
      "source.identity.effect_id",
    ),
  };
  if (!SAFE_PATH_SEGMENT.test(normalizedIdentity.goal_id)) {
    throw new EffectRuntimeRequestError("source.identity.goal_id is not a safe path segment");
  }
  return {
    schema_version: POST_WRITEBACK_HOOK_SOURCE_SCHEMA_VERSION,
    event_kind: pythonStrippedString(source.event_kind, "source.event_kind"),
    status: pythonStrippedString(source.status, "source.status"),
    durable: requireBoolean(source.durable, "source.durable"),
    identity: normalizedIdentity,
    state_version: pythonStrippedString(
      source.state_version,
      "source.state_version",
    ),
    ...(Object.hasOwn(source, "receipt_id")
      ? {receipt_id: optionalPythonStrippedString(source.receipt_id, "source.receipt_id")}
      : {}),
    committed_at: pythonStrippedString(
      source.committed_at,
      "source.committed_at",
    ),
    projection: requireJsonObject(source.projection, "source.projection"),
  };
}

function decodeSource(value: unknown): PostWritebackSource {
  try {
    return decodeSourceFields(value);
  } catch (error) {
    if (error instanceof EffectRuntimeRequestError) {
      // Every field/type/emptiness rejection from the source decoder carries
      // one dedicated code; message text stays free to evolve.
      throw new EffectRuntimeRequestError(
        error.message,
        POST_WRITEBACK_SOURCE_REJECTION_CODE,
      );
    }
    throw error;
  }
}

function decodeRuntimeRoot(value: unknown): string | null {
  const candidate = optionalNonEmptyString(value, "runtime_root");
  if (candidate === null) return null;
  if (!isAbsolute(candidate)) {
    throw new EffectRuntimeRequestError("runtime_root must be absolute");
  }
  return resolve(candidate);
}

function decodeProviderOutcome(value: unknown, index: number): ProviderOutcome {
  const outcome = requireJsonObject(value, `provider_outcomes[${index}]`);
  exactObjectFields(
    outcome,
    [
      "dispatch_id",
      "hook_id",
      "capability_id",
      "attempt_count",
      "status",
      "result",
    ],
    `provider_outcomes[${index}]`,
  );
  const dispatchId = requireNonEmptyString(
    outcome.dispatch_id,
    `provider_outcomes[${index}].dispatch_id`,
  );
  if (!DISPATCH_ID.test(dispatchId)) {
    throw new EffectRuntimeRequestError(
      `provider_outcomes[${index}].dispatch_id is invalid`,
    );
  }
  const status = requireStringLiteral(
    outcome.status,
    OUTCOME_STATUSES,
    `provider_outcomes[${index}].status`,
  );
  const result = outcome.result === null
    ? null
    : requireJsonObject(outcome.result, `provider_outcomes[${index}].result`);
  if ((status === "returned") !== (result !== null)) {
    throw new EffectRuntimeRequestError(
      `provider_outcomes[${index}] result does not match status`,
    );
  }
  const attemptCount = requireInteger(
    outcome.attempt_count,
    `provider_outcomes[${index}].attempt_count`,
  );
  if (attemptCount < 1 || attemptCount > 10_000) {
    throw new EffectRuntimeRequestError(
      `provider_outcomes[${index}].attempt_count is invalid`,
    );
  }
  return {
    dispatch_id: dispatchId,
    hook_id: requireNonEmptyString(
      outcome.hook_id,
      `provider_outcomes[${index}].hook_id`,
    ),
    capability_id: requireNonEmptyString(
      outcome.capability_id,
      `provider_outcomes[${index}].capability_id`,
    ),
    attempt_count: attemptCount,
    status,
    result,
  };
}

function decodeRequest(value: JsonObject): TransactionRequest {
  exactObjectFields(
    value,
    [
      "schema_version",
      "phase",
      "runtime_root",
      "source",
      "hook_input",
      "registrations",
      "transaction_id",
      "provider_outcomes",
    ],
    "post-writeback hook transaction request",
  );
  if (
    value.schema_version !== POST_WRITEBACK_HOOK_TRANSACTION_REQUEST_SCHEMA_VERSION
  ) {
    throw new EffectRuntimeRequestError(
      "post-writeback hook transaction schema is invalid",
    );
  }
  const phase = requireStringLiteral(
    value.phase,
    PHASES,
    "phase",
    "post-writeback hook transaction phase is unsupported",
  );
  if (!Array.isArray(value.registrations)) {
    throw new EffectRuntimeRequestError("registrations must be an array");
  }
  if (value.registrations.length > 128) {
    throw new EffectRuntimeRequestError("registrations exceed the batch limit");
  }
  if (!Array.isArray(value.provider_outcomes)) {
    throw new EffectRuntimeRequestError("provider_outcomes must be an array");
  }
  const transactionId = optionalNonEmptyString(
    value.transaction_id,
    "transaction_id",
  );
  if (phase === "preflight" && transactionId !== null) {
    throw new EffectRuntimeRequestError("preflight transaction_id must be null");
  }
  if (phase === "preflight" && value.provider_outcomes.length > 0) {
    throw new EffectRuntimeRequestError("preflight provider_outcomes must be empty");
  }
  if (phase === "finalize" && transactionId === null) {
    throw new EffectRuntimeRequestError("finalize transaction_id is required");
  }
  const outcomes = value.provider_outcomes.map((outcome, index) =>
    decodeProviderOutcome(outcome, index)
  );
  if (new Set(outcomes.map((outcome) => outcome.dispatch_id)).size !== outcomes.length) {
    throw new EffectRuntimeRequestError("provider_outcomes contain duplicate dispatch_id");
  }
  const source = value.source === null ? null : decodeSource(value.source);
  const hookInput = value.hook_input === null
    ? null
    : requireJsonObject(value.hook_input, "hook_input");
  if ((source === null) === (hookInput === null)) {
    throw new EffectRuntimeRequestError(
      "exactly one of source or hook_input is required",
    );
  }
  return {
    phase,
    runtime_root: decodeRuntimeRoot(value.runtime_root),
    source,
    hook_input: hookInput,
    registrations: [...value.registrations],
    transaction_id: transactionId,
    provider_outcomes: outcomes,
  };
}

function sourceHookInput(source: PostWritebackSource, readScope: string[]): JsonObject {
  const receiptFacts = {
    event_kind: source.event_kind,
    identity: source.identity,
    state_version: source.state_version,
    // Old sources retain their byte-for-byte identity. New lifecycle receipts
    // carry an immutable committed id; their clock is diagnostic, not a version.
    ...(source.receipt_id == null
      ? {committed_at: source.committed_at}
      : {receipt_id: source.receipt_id}),
  };
  const eventId = `pwr_${sha256(pythonCanonicalJson(receiptFacts)).slice(0, 24)}`;
  const projection = Object.fromEntries(
    readScope
      .filter((field) => Object.hasOwn(source.projection, field))
      .map((field) => [field, source.projection[field]]),
  );
  return {
    schema_version: POST_WRITEBACK_HOOK_INPUT_SCHEMA_VERSION,
    receipt: {
      schema_version: "loopx_primary_writeback_receipt_v0",
      event_id: eventId,
      event_kind: source.event_kind,
      status: source.status,
      recorded_at: source.committed_at,
      durable: source.durable,
    },
    identity: source.identity,
    state_version: source.state_version,
    projection,
  };
}

function dispatchIdentity(registration: JsonObject, hookInput: JsonObject): string {
  const receipt = requireJsonObject(hookInput.receipt, "hook_input.receipt");
  return `pwh_${sha256(pythonCanonicalJson({
    source_receipt_id: pythonStrippedString(
      receipt.event_id,
      "receipt.event_id",
    ),
    event_kind: pythonStrippedString(
      receipt.event_kind,
      "receipt.event_kind",
    ),
    hook_id: registration.hook_id,
    capability_id: registration.capability_id,
    registration_schema: POST_WRITEBACK_HOOK_REGISTRATION_SCHEMA_VERSION,
    policy_version: registration.policy_version,
  }))}`;
}

function hookInputGoalId(hookInput: JsonObject): string {
  const identity = requireJsonObject(hookInput.identity, "hook_input.identity");
  const goalId = pythonStrippedString(
    identity.goal_id,
    "hook_input.identity.goal_id",
  );
  if (!SAFE_PATH_SEGMENT.test(goalId)) {
    throw new EffectRuntimeRequestError(
      "hook_input.identity.goal_id is not a safe path segment",
    );
  }
  return goalId;
}

function transactionIdentity(request: TransactionRequest): string {
  const registrations = request.registrations
    .map((registration, index) => ({ registration, index }))
    .sort((left, right) => {
      const leftObject = jsonObject(left.registration) ?? {};
      const rightObject = jsonObject(right.registration) ?? {};
      const byHook = compareText(
        String(leftObject.hook_id ?? ""),
        String(rightObject.hook_id ?? ""),
      );
      if (byHook !== 0) return byHook;
      return left.index - right.index;
    })
    .map(({ registration }) => registration);
  return `pwtx_${sha256(pythonCanonicalJson({
    runtime_root: request.runtime_root,
    source: request.source,
    hook_input: request.hook_input,
    registrations,
  }))}`;
}

function hookFailure(
  registration: unknown,
  errorCode: string,
  durableReceiptRef: string | null = null,
): JsonObject {
  const value = jsonObject(registration) ?? {};
  const failure: JsonObject = {
    hook_id: typeof value.hook_id === "string" && value.hook_id
      ? value.hook_id
      : "unknown",
    capability_id: typeof value.capability_id === "string" && value.capability_id
      ? value.capability_id
      : "unknown",
    error_code: errorCode,
  };
  if (durableReceiptRef !== null) {
    failure.durable_receipt_ref = durableReceiptRef;
  }
  return failure;
}

function receiptPath(
  runtimeRoot: string,
  goalId: string,
  dispatchId: string,
): string {
  return join(
    runtimeRoot,
    "goals",
    goalId,
    "post_writeback_hooks",
    `${dispatchId}.json`,
  );
}

async function readReceipt(path: string, dispatchId: string): Promise<ReceiptRead> {
  try {
    const encoded = await readFile(path);
    const snapshot = `sha256:${createHash("sha256").update(encoded).digest("hex")}`;
    const value: unknown = JSON.parse(encoded.toString("utf8"));
    const receipt = jsonObject(value);
    if (
      !receipt ||
      receipt.schema_version !== POST_WRITEBACK_HOOK_RECEIPT_SCHEMA_VERSION ||
      receipt.dispatch_id !== dispatchId
    ) {
      return { state: "malformed", receipt: null, snapshot };
    }
    return { state: "present", receipt, snapshot };
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return { state: "missing", receipt: null, snapshot: "missing" };
    }
    return { state: "malformed", receipt: null, snapshot: "unreadable" };
  }
}

function intentKey(intent: unknown): string | null {
  const value = jsonObject(intent);
  return value && typeof value.idempotency_key === "string"
    ? value.idempotency_key
    : null;
}

function validateStoredReceipt(
  request: TransactionRequest,
  admitted: AdmittedHook,
  receipt: JsonObject,
): JsonObject {
  // An explicit primary receipt id binds the dispatch independently of a
  // later projection timestamp. Validate and return the stored clock rather
  // than rewriting history; legacy timestamp identities remain exact.
  const hookInput = request.source?.receipt_id != null
    ? {...admitted.hook_input, receipt: {
      ...requireJsonObject(admitted.hook_input.receipt, "hook_input.receipt"),
      recorded_at: receipt.recorded_at,
    }}
    : admitted.hook_input;
  return validatePostWritebackHookReceipt({
    registration: admitted.contract, hook_input: hookInput, receipt,
  });
}

function recordReceiptConflict(
  inspection: Inspection,
  admitted: AdmittedHook,
  receipt: JsonObject | null,
): void {
  const key = receipt === null ? null : intentKey(receipt.intent);
  if (key !== null) inspection.seen_intent_keys.add(key);
  inspection.failures.push(hookFailure(admitted.registration, "receipt_conflict"));
}

function replayTerminalReceipt(
  inspection: Inspection,
  admitted: AdmittedHook,
  receipt: JsonObject,
): void {
  const key = intentKey(receipt.intent);
  if (key !== null) {
    if (inspection.seen_intent_keys.has(key)) {
      inspection.failures.push(
        hookFailure(admitted.registration, "intent_key_conflict"),
      );
      return;
    }
    inspection.seen_intent_keys.add(key);
    inspection.intents.push(requireJsonObject(receipt.intent, "receipt.intent"));
  }
  inspection.replayed_hooks.push(admitted.registration.hook_id);
}

function orderedRegistrations(registrations: unknown[]): unknown[] {
  return [...registrations].sort((left, right) => {
    const leftHook = String((jsonObject(left) ?? {}).hook_id ?? "");
    const rightHook = String((jsonObject(right) ?? {}).hook_id ?? "");
    return compareText(leftHook, rightHook);
  });
}

async function inspectTransaction(request: TransactionRequest): Promise<Inspection> {
  const inspection: Inspection = {
    registered_count: request.registrations.length,
    plans: [],
    result_slots: [],
    admitted: new Map(),
    known_dispatch_ids: new Set(),
    blocked_dispatch_ids: new Set(),
    intents: [],
    failures: [],
    replayed_hooks: [],
    retried_hooks: [],
    seen_intent_keys: new Set(),
    terminal_receipts: new Map(),
  };
  const seenHookIds = new Set<string>();
  const suppliedOutcomes = new Map(
    request.provider_outcomes.map((outcome) => [outcome.dispatch_id, outcome]),
  );

  for (const rawRegistration of orderedRegistrations(request.registrations)) {
    const raw = jsonObject(rawRegistration) ?? {};
    const rawHookId = typeof raw.hook_id === "string" ? raw.hook_id : "";
    if (rawHookId && seenHookIds.has(rawHookId)) {
      inspection.result_slots.push({
        kind: "failure",
        failure: hookFailure(rawRegistration, "duplicate_hook_id"),
      });
      continue;
    }
    if (rawHookId) seenHookIds.add(rawHookId);

    let registration: ReturnType<typeof validatePostWritebackHookRegistration>;
    let hookInput: JsonObject;
    try {
      registration = validatePostWritebackHookRegistration(rawRegistration);
      const candidateInput = request.source === null
        ? request.hook_input
        : sourceHookInput(
          request.source,
          registration.requested_read_scope,
        );
      hookInput = validatePostWritebackHookInput({
        registration: rawRegistration,
        hook_input: candidateInput,
      });
    } catch {
      inspection.result_slots.push({
        kind: "failure",
        failure: hookFailure(rawRegistration, "registration_or_input_rejected"),
      });
      continue;
    }
    const dispatchId = dispatchIdentity(registration, hookInput);
    inspection.known_dispatch_ids.add(dispatchId);
    const admitted: AdmittedHook = {
      contract: raw,
      registration,
      hook_input: hookInput,
      dispatch_id: dispatchId,
    };
    inspection.admitted.set(dispatchId, admitted);

    let previousAttemptCount = 0;
    let receiptSnapshot = "missing";
    if (request.runtime_root !== null) {
      const path = receiptPath(
        request.runtime_root,
        hookInputGoalId(hookInput),
        dispatchId,
      );
      const read = await readReceipt(path, dispatchId);
      receiptSnapshot = read.snapshot;
      if (read.state === "malformed") {
        inspection.blocked_dispatch_ids.add(dispatchId);
        inspection.result_slots.push({
          kind: "failure",
          failure: hookFailure(registration, "journal_read_failed"),
        });
        continue;
      }
      if (read.receipt !== null) {
        let receipt: JsonObject;
        try {
          receipt = validateStoredReceipt(request, admitted, read.receipt);
        } catch {
          inspection.blocked_dispatch_ids.add(dispatchId);
          inspection.result_slots.push({
            kind: "failure",
            failure: hookFailure(registration, "receipt_conflict"),
          });
          continue;
        }
        if (receipt.status === "retryable_failure") {
          previousAttemptCount = Number(receipt.attempt_count);
          inspection.retried_hooks.push(registration.hook_id);
          if (
            previousAttemptCount >= 10_000 &&
            suppliedOutcomes.get(dispatchId)?.attempt_count !== previousAttemptCount
          ) {
            inspection.blocked_dispatch_ids.add(dispatchId);
            inspection.result_slots.push({
              kind: "failure",
              failure: hookFailure(registration, "journal_write_failed"),
            });
            continue;
          }
        } else {
          inspection.terminal_receipts.set(dispatchId, receipt);
          inspection.result_slots.push({ kind: "hook", dispatch_id: dispatchId });
          continue;
        }
      }
    }
    inspection.plans.push({
      dispatch_id: dispatchId,
      hook_id: registration.hook_id,
      capability_id: registration.capability_id,
      attempt_count:
        suppliedOutcomes.get(dispatchId)?.attempt_count === previousAttemptCount
          ? previousAttemptCount
          : previousAttemptCount + 1,
      retry: previousAttemptCount > 0,
      receipt_snapshot: receiptSnapshot,
      hook_input: hookInput,
    });
    inspection.result_slots.push({ kind: "hook", dispatch_id: dispatchId });
  }
  return inspection;
}

function sidecarReceipt(
  admitted: AdmittedHook,
  plan: ProviderPlan,
  status: "retryable_failure" | "not_applicable" | "intent_recorded",
  intent: JsonObject | null,
  errorCode: string | null,
): JsonObject {
  const sourceReceipt = requireJsonObject(
    admitted.hook_input.receipt,
    "hook_input.receipt",
  );
  return validatePostWritebackHookReceipt({
    registration: admitted.contract,
    hook_input: admitted.hook_input,
    receipt: {
      schema_version: POST_WRITEBACK_HOOK_RECEIPT_SCHEMA_VERSION,
      dispatch_id: admitted.dispatch_id,
      hook_id: admitted.registration.hook_id,
      capability_id: admitted.registration.capability_id,
      source_receipt_id: sourceReceipt.event_id,
      status,
      intent,
      error_code: errorCode,
      attempt_count: plan.attempt_count,
      recorded_at: sourceReceipt.recorded_at,
    },
  });
}

async function syncParentDirectory(path: string): Promise<void> {
  if (process.platform === "win32") return;
  const handle = await open(dirname(path), "r");
  try {
    await handle.sync();
  } finally {
    await handle.close();
  }
}

async function storeReceipt(
  request: TransactionRequest,
  admitted: AdmittedHook,
  plan: ProviderPlan,
  receipt: JsonObject,
  mutationLockDeadline: number,
): Promise<ReceiptStoreResult> {
  if (request.runtime_root === null) {
    return { status: "stored", receipt };
  }
  const path = receiptPath(
    request.runtime_root,
    hookInputGoalId(admitted.hook_input),
    admitted.dispatch_id,
  );
  try {
    return await withFileMutationLock(
      path,
      async () => {
        const current = await readReceipt(path, admitted.dispatch_id);
        if (current.state === "malformed") {
          return { status: "conflict", receipt: null };
        }
        if (current.receipt !== null) {
          let validated: JsonObject;
          try {
            validated = validateStoredReceipt(request, admitted, current.receipt);
          } catch {
            return { status: "conflict", receipt: null };
          }
          const candidate = request.source?.receipt_id != null
            ? {...receipt, recorded_at: validated.recorded_at} : receipt;
          if (pythonCanonicalJson(validated) === pythonCanonicalJson(candidate)) {
            return validated.status === "retryable_failure"
              ? { status: "stored", receipt: validated }
              : { status: "replayed", receipt: validated };
          }
          if (validated.status !== "retryable_failure") {
            return { status: "conflict", receipt: validated };
          }
          if (Number(validated.attempt_count) + 1 !== plan.attempt_count) {
            return { status: "conflict", receipt: null };
          }
        } else if (plan.attempt_count !== 1) {
          return { status: "conflict", receipt: null };
        }
        await atomicWriteJson(path, receipt);
        await syncParentDirectory(path);
        return { status: "stored", receipt };
      },
      Math.max(0, mutationLockDeadline - Date.now()),
    );
  } catch {
    return { status: "failed", receipt: null };
  }
}

function dispatchResult(
  inspection: Inspection,
  invokedCount: number,
): JsonObject {
  return {
    schema_version: POST_WRITEBACK_HOOK_DISPATCH_SCHEMA_VERSION,
    phase: "post_writeback",
    registered_count: inspection.registered_count,
    invoked_count: invokedCount,
    replayed_hooks: inspection.replayed_hooks,
    retried_hooks: inspection.retried_hooks,
    intent_count: inspection.intents.length,
    intents: inspection.intents,
    failures: inspection.failures,
    primary_writeback_preserved: true,
    external_writes_performed: false,
  };
}

async function recordFailure(
  request: TransactionRequest,
  inspection: Inspection,
  admitted: AdmittedHook,
  plan: ProviderPlan,
  errorCode: string,
  mutationLockDeadline: number,
): Promise<void> {
  const receipt = sidecarReceipt(
    admitted,
    plan,
    "retryable_failure",
    null,
    errorCode,
  );
  const stored = await storeReceipt(
    request,
    admitted,
    plan,
    receipt,
    mutationLockDeadline,
  );
  if (stored.status === "failed") {
    inspection.failures.push(hookFailure(admitted.registration, "journal_write_failed"));
    return;
  }
  if (stored.status === "conflict") {
    recordReceiptConflict(inspection, admitted, stored.receipt);
    return;
  }
  if (stored.status === "replayed") {
    replayTerminalReceipt(inspection, admitted, stored.receipt);
    return;
  }
  inspection.failures.push(
    hookFailure(
      admitted.registration,
      errorCode,
      request.runtime_root === null
        ? null
        : `post-writeback-hook:${admitted.dispatch_id}`,
    ),
  );
}

function receiptForOutcome(
  admitted: AdmittedHook,
  outcome: ProviderOutcome,
): JsonObject {
  if (
    outcome.hook_id !== admitted.registration.hook_id ||
    outcome.capability_id !== admitted.registration.capability_id
  ) {
    throw new EffectRuntimeRequestError("provider outcome does not bind its hook");
  }
  const plan: ProviderPlan = {
    dispatch_id: admitted.dispatch_id,
    hook_id: admitted.registration.hook_id,
    capability_id: admitted.registration.capability_id,
    attempt_count: outcome.attempt_count,
    retry: outcome.attempt_count > 1,
    receipt_snapshot: "outcome",
    hook_input: admitted.hook_input,
  };
  if (
    outcome.status === "lock_unavailable" ||
    outcome.status === "lock_failed" ||
    outcome.status === "receipt_changed"
  ) {
    throw new EffectRuntimeRequestError(
      "lock outcome cannot construct a durable receipt",
    );
  }
  if (outcome.status !== "returned") {
    return sidecarReceipt(
      admitted,
      plan,
      "retryable_failure",
      null,
      outcome.status,
    );
  }
  if (outcome.result === null) {
    return sidecarReceipt(
      admitted,
      plan,
      "retryable_failure",
      null,
      "runtime_result_invalid",
    );
  }
  let result: JsonObject;
  try {
    result = validatePostWritebackHookInvocation({
      registration: admitted.contract,
      hook_input: admitted.hook_input,
      result: outcome.result,
    });
  } catch {
    return sidecarReceipt(
      admitted,
      plan,
      "retryable_failure",
      null,
      "contract_rejected",
    );
  }
  if (result.status !== "intent") {
    return sidecarReceipt(admitted, plan, "not_applicable", null, null);
  }
  return sidecarReceipt(
    admitted,
    plan,
    "intent_recorded",
    requireJsonObject(result.intent, "post-writeback result.intent"),
    null,
  );
}

async function finalizePlan(
  request: TransactionRequest,
  inspection: Inspection,
  plan: ProviderPlan,
  outcome: ProviderOutcome | undefined,
  mutationLockDeadline: number,
): Promise<void> {
  const admitted = inspection.admitted.get(plan.dispatch_id);
  if (!admitted) {
    throw new EffectRuntimeRequestError("provider plan lost its admitted hook");
  }
  const deferred = outcome !== undefined &&
    (
      outcome.status === "lock_unavailable" ||
      outcome.status === "lock_failed" ||
      outcome.status === "receipt_changed"
    );
  if (
    outcome &&
    (
      outcome.hook_id !== plan.hook_id ||
      outcome.capability_id !== plan.capability_id ||
      (!deferred && outcome.attempt_count !== plan.attempt_count)
    )
  ) {
    throw new EffectRuntimeRequestError("provider outcome does not bind its plan");
  }
  if (outcome?.status === "lock_unavailable") {
    inspection.failures.push(
      hookFailure(admitted.registration, "lock_acquire_timeout"),
    );
    return;
  }
  if (outcome?.status === "lock_failed") {
    inspection.failures.push(
      hookFailure(admitted.registration, "journal_read_failed"),
    );
    return;
  }
  if (outcome?.status === "receipt_changed") {
    inspection.failures.push(
      hookFailure(admitted.registration, "receipt_conflict"),
    );
    return;
  }
  if (!outcome || outcome.status !== "returned") {
    await recordFailure(
      request,
      inspection,
      admitted,
      plan,
      outcome?.status ?? "producer_failed",
      mutationLockDeadline,
    );
    return;
  }
  if (outcome.result === null) {
    await recordFailure(
      request,
      inspection,
      admitted,
      plan,
      "runtime_result_invalid",
      mutationLockDeadline,
    );
    return;
  }

  let result: JsonObject;
  try {
    result = validatePostWritebackHookInvocation({
      registration: admitted.contract,
      hook_input: admitted.hook_input,
      result: outcome.result,
    });
  } catch {
    await recordFailure(
      request,
      inspection,
      admitted,
      plan,
      "contract_rejected",
      mutationLockDeadline,
    );
    return;
  }

  if (result.status !== "intent") {
    const receipt = sidecarReceipt(
      admitted,
      plan,
      "not_applicable",
      null,
      null,
    );
    const stored = await storeReceipt(
      request,
      admitted,
      plan,
      receipt,
      mutationLockDeadline,
    );
    if (stored.status === "failed") {
      inspection.failures.push(hookFailure(admitted.registration, "journal_write_failed"));
    } else if (stored.status === "conflict") {
      recordReceiptConflict(inspection, admitted, stored.receipt);
    } else if (stored.status === "replayed") {
      replayTerminalReceipt(inspection, admitted, stored.receipt);
    }
    return;
  }

  const intent = requireJsonObject(result.intent, "post-writeback result.intent");
  const key = requireNonEmptyString(intent.idempotency_key, "intent.idempotency_key");
  if (inspection.seen_intent_keys.has(key)) {
    await recordFailure(
      request,
      inspection,
      admitted,
      plan,
      "intent_key_conflict",
      mutationLockDeadline,
    );
    return;
  }
  // Canonical ordering owns duplicate arbitration even when this hook's
  // receipt write fails after provider execution (including after rename).
  inspection.seen_intent_keys.add(key);
  const receipt = sidecarReceipt(
    admitted,
    plan,
    "intent_recorded",
    intent,
    null,
  );
  const stored = await storeReceipt(
    request,
    admitted,
    plan,
    receipt,
    mutationLockDeadline,
  );
  if (stored.status === "failed") {
    inspection.failures.push(hookFailure(admitted.registration, "journal_write_failed"));
    return;
  }
  if (stored.status === "conflict") {
    recordReceiptConflict(inspection, admitted, stored.receipt);
    return;
  }
  if (stored.status === "replayed") {
    // The key above belongs to this exact receipt, not to an earlier hook.
    inspection.seen_intent_keys.delete(key);
    replayTerminalReceipt(inspection, admitted, stored.receipt);
    return;
  }
  inspection.intents.push(intent);
}

function isDeferredOutcome(outcome: ProviderOutcome): boolean {
  return outcome.status === "lock_unavailable" ||
    outcome.status === "lock_failed" ||
    outcome.status === "receipt_changed";
}

async function materializeTransactionResults(
  request: TransactionRequest,
  inspection: Inspection,
  outcomes: Map<string, ProviderOutcome>,
): Promise<void> {
  const mutationLockDeadline = Date.now() + FINALIZE_MUTATION_LOCK_BUDGET_MS;
  const plans = new Map(
    inspection.plans.map((plan) => [plan.dispatch_id, plan]),
  );
  for (const slot of inspection.result_slots) {
    if (slot.kind === "failure") {
      inspection.failures.push(slot.failure);
      continue;
    }
    const admitted = inspection.admitted.get(slot.dispatch_id);
    if (!admitted) {
      throw new EffectRuntimeRequestError("result slot lost its admitted hook");
    }
    const terminal = inspection.terminal_receipts.get(slot.dispatch_id);
    if (terminal !== undefined) {
      const outcome = outcomes.get(slot.dispatch_id);
      if (outcome === undefined || isDeferredOutcome(outcome)) {
        replayTerminalReceipt(inspection, admitted, terminal);
        continue;
      }
      const desired = receiptForOutcome(admitted, outcome);
      if (pythonCanonicalJson(desired) !== pythonCanonicalJson(terminal)) {
        recordReceiptConflict(inspection, admitted, terminal);
        continue;
      }
      replayTerminalReceipt(inspection, admitted, terminal);
      continue;
    }
    const plan = plans.get(slot.dispatch_id);
    if (!plan) {
      throw new EffectRuntimeRequestError("result slot lost its provider plan");
    }
    await finalizePlan(
      request,
      inspection,
      plan,
      outcomes.get(slot.dispatch_id),
      mutationLockDeadline,
    );
  }
}

export async function evaluatePostWritebackHookTransaction(
  value: JsonObject,
): Promise<JsonObject> {
  const request = decodeRequest(value);
  const transactionId = transactionIdentity(request);
  if (
    request.phase === "finalize" &&
    request.transaction_id !== transactionId
  ) {
    throw new EffectRuntimeRequestError(
      "finalize transaction_id does not match source and registrations",
    );
  }
  const inspection = await inspectTransaction(request);
  if (request.phase === "preflight") {
    assertFinalResultFitsEnvelope(
      inspection,
      transactionId,
      inspection.plans.length,
    );
    if (inspection.plans.length === 0) {
      await materializeTransactionResults(request, inspection, new Map());
    }
    return boundedTransactionResult({
      schema_version: POST_WRITEBACK_HOOK_TRANSACTION_RESULT_SCHEMA_VERSION,
      phase: "preflight",
      transaction_id: transactionId,
      provider_plan: inspection.plans,
      dispatch: inspection.plans.length === 0
        ? dispatchResult(inspection, 0)
        : null,
    });
  }

  const outcomes = new Map(
    request.provider_outcomes.map((outcome) => [outcome.dispatch_id, outcome]),
  );
  const plans = new Map(
    inspection.plans.map((plan) => [plan.dispatch_id, plan]),
  );
  for (const plan of inspection.plans) {
    const outcome = outcomes.get(plan.dispatch_id);
    if (!outcome) {
      throw new EffectRuntimeRequestError(
        "provider_outcomes do not cover the current provider plan",
      );
    }
    if (
      outcome.hook_id !== plan.hook_id ||
      outcome.capability_id !== plan.capability_id ||
      (!isDeferredOutcome(outcome) && outcome.attempt_count !== plan.attempt_count)
    ) {
      throw new EffectRuntimeRequestError(
        "provider outcome does not bind its current plan",
      );
    }
  }
  for (const outcome of request.provider_outcomes) {
    if (!inspection.known_dispatch_ids.has(outcome.dispatch_id)) {
      throw new EffectRuntimeRequestError(
        "provider outcome dispatch_id is outside this transaction",
      );
    }
    if (plans.has(outcome.dispatch_id)) continue;
    const admitted = inspection.admitted.get(outcome.dispatch_id);
    if (inspection.blocked_dispatch_ids.has(outcome.dispatch_id)) {
      if (
        !admitted ||
        outcome.hook_id !== admitted.registration.hook_id ||
        outcome.capability_id !== admitted.registration.capability_id
      ) {
        throw new EffectRuntimeRequestError(
          "provider outcome does not bind its blocked hook",
        );
      }
      continue;
    }
    if (!admitted || !inspection.terminal_receipts.has(outcome.dispatch_id)) {
      throw new EffectRuntimeRequestError(
        "provider outcome is outside the current provider plan",
      );
    }
  }
  const invokedCount = request.provider_outcomes.filter(
    (outcome) =>
      outcome.status !== "lock_unavailable" &&
      outcome.status !== "lock_failed" &&
      outcome.status !== "receipt_changed",
  ).length;
  assertFinalResultFitsEnvelope(
    inspection,
    transactionId,
    invokedCount,
  );
  await materializeTransactionResults(request, inspection, outcomes);
  return boundedTransactionResult({
    schema_version: POST_WRITEBACK_HOOK_TRANSACTION_RESULT_SCHEMA_VERSION,
    phase: "finalize",
    transaction_id: transactionId,
    provider_plan: [],
    dispatch: dispatchResult(inspection, invokedCount),
  });
}
