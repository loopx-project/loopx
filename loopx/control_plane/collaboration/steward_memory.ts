import {createHash} from "node:crypto";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {normalizeStewardContext} from "./conversation_scope.ts";

const USER_SPACE_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$/;
const TOPIC_FILE_RE = /^[^/\\~%?#\x00-\x1f\x7f]+\.md$/;
const SPEAKER_DIGITS = 12;
const SPEAKER_SEGMENT_CHARS = 32;
const TOPIC_MAX_CHARS = 60;
const CONTENT_MAX_CHARS = 1200;
const MAX_PREFERENCE_OPS = 8;

const FORBIDDEN_TARGET_KEYS = new Set([
  "speaker",
  "owner",
  "uri",
  "scope_ref",
  "user_space",
  "peer_id",
  "operator_ref",
  "binding_id",
  "person",
  "target",
  "sender_id",
  "sender_ref",
  "directory",
]);

const ALLOWED_PREFERENCE_KEYS = new Set(["operation", "topic", "content", "topic_file"]);

export interface StewardMemorySettings {
  schema_version: "steward_memory_machine_defaults_v0";
  enabled: boolean;
  user_space: string;
  auto_recall: boolean;
  recall_timeout_seconds: number;
}

export interface StewardMemoryPreferenceOp {
  operation: "upsert" | "remove";
  topic: string;
  topic_file: string;
  content?: string;
}

export function normalizeStewardMemorySettings(raw: unknown): StewardMemorySettings {
  if (raw == null) {
    return {
      schema_version: "steward_memory_machine_defaults_v0",
      enabled: false,
      user_space: "steward",
      auto_recall: true,
      recall_timeout_seconds: 3,
    };
  }
  if (typeof raw !== "object" || Array.isArray(raw)) {
    throw new EffectRuntimeRequestError("steward_memory configuration must be an object");
  }
  const row = raw as Record<string, unknown>;
  const unknown = Object.keys(row).filter(
    (k) => !["schema_version", "enabled", "user_space", "auto_recall", "recall_timeout_seconds"].includes(k),
  );
  if (unknown.length > 0) {
    throw new EffectRuntimeRequestError(`steward_memory contains unsupported fields: ${unknown.sort().join(", ")}`);
  }
  if (row.schema_version !== "steward_memory_machine_defaults_v0") {
    throw new EffectRuntimeRequestError("steward_memory must use steward_memory_machine_defaults_v0");
  }
  if (typeof row.enabled !== "boolean") {
    throw new EffectRuntimeRequestError("steward_memory.enabled must be a boolean");
  }
  const userSpace = typeof row.user_space === "string" ? row.user_space.trim() : "";
  if (!USER_SPACE_RE.test(userSpace)) {
    throw new EffectRuntimeRequestError("steward_memory.user_space must use a path-safe identifier");
  }
  if (typeof row.auto_recall !== "boolean") {
    throw new EffectRuntimeRequestError("steward_memory.auto_recall must be a boolean");
  }
  if (
    typeof row.recall_timeout_seconds !== "number"
    || !Number.isInteger(row.recall_timeout_seconds)
    || row.recall_timeout_seconds < 1
    || row.recall_timeout_seconds > 15
  ) {
    throw new EffectRuntimeRequestError("steward_memory.recall_timeout_seconds must be an integer between 1 and 15");
  }
  return {
    schema_version: "steward_memory_machine_defaults_v0",
    enabled: row.enabled,
    user_space: userSpace,
    auto_recall: row.auto_recall,
    recall_timeout_seconds: row.recall_timeout_seconds,
  };
}

export function sanitizeSpeakerSegment(label: unknown): string {
  const normalized = String(label ?? "").normalize("NFC");
  const cleaned = normalized
    .replace(/[\u0000-\u001f\u007f/\\~%?#]+/g, "")
    .replace(/\s+/g, "_")
    .replace(/^[._]+/, "")
    .slice(0, SPEAKER_SEGMENT_CHARS);
  return cleaned || "speaker";
}

export function slugifyMemoryTopic(topic: unknown): {topic: string; topic_file: string} {
  if (typeof topic !== "string") {
    throw new EffectRuntimeRequestError("memory preference topic must be a string");
  }
  const trimmed = topic.normalize("NFC").trim();
  if (/[/\\~%?#\x00-\x1f\x7f]/.test(trimmed) || trimmed.includes("..")) {
    throw new EffectRuntimeRequestError("invalid memory preference topic");
  }
  const slug = trimmed
    .toLowerCase()
    .replace(/[\s_]+/g, "-")
    .replace(/^[.-]+|[.-]+$/g, "")
    .slice(0, TOPIC_MAX_CHARS)
    .replace(/\.+$/g, "");
  const file = `${slug}.md`;
  if (!slug || slug === "." || slug === ".." || !TOPIC_FILE_RE.test(file)) {
    throw new EffectRuntimeRequestError("invalid memory preference topic");
  }
  return {topic: slug, topic_file: file};
}

export function normalizeStewardMemoryPreferences(value: unknown): StewardMemoryPreferenceOp[] {
  if (value == null) return [];
  if (!Array.isArray(value)) {
    throw new EffectRuntimeRequestError("memory_preferences must be a list");
  }
  if (value.length > MAX_PREFERENCE_OPS) {
    throw new EffectRuntimeRequestError(`memory_preferences accepts at most ${MAX_PREFERENCE_OPS} operations`);
  }
  const operations: StewardMemoryPreferenceOp[] = [];
  for (const item of value) {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      throw new EffectRuntimeRequestError("each memory preference item must be an object");
    }
    const row = item as Record<string, unknown>;
    for (const key of Object.keys(row)) {
      if (FORBIDDEN_TARGET_KEYS.has(key) || !ALLOWED_PREFERENCE_KEYS.has(key)) {
        throw new EffectRuntimeRequestError(`unsupported memory preference field: ${key}`);
      }
    }
    const op = typeof row.operation === "string" ? row.operation.trim().toLowerCase() : "";
    if (op !== "upsert" && op !== "remove") {
      throw new EffectRuntimeRequestError("memory preference operation must be upsert or remove");
    }
    const {topic, topic_file} = slugifyMemoryTopic(row.topic);
    if (typeof row.topic_file === "string" && row.topic_file !== topic_file) {
      throw new EffectRuntimeRequestError("memory preference topic_file mismatch");
    }
    if (op === "upsert") {
      if (typeof row.content !== "string") {
        throw new EffectRuntimeRequestError("upsert memory preference requires string content");
      }
      const content = row.content.trim();
      if (!content || content.length > CONTENT_MAX_CHARS) {
        throw new EffectRuntimeRequestError(`memory preference content must contain 1 to ${CONTENT_MAX_CHARS} characters`);
      }
      operations.push({operation: "upsert", topic, topic_file, content});
    } else {
      operations.push({operation: "remove", topic, topic_file});
    }
  }
  return operations;
}

function rejectTurn(reason: string): Record<string, unknown> {
  return {
    schema_version: "steward_memory_turn_plan_v0",
    eligible: false,
    reason,
  };
}

export function planStewardMemoryTurn(input: Record<string, unknown>): Record<string, unknown> {
  const setting = normalizeStewardMemorySettings(input.setting);
  if (!setting.enabled) {
    return rejectTurn("disabled");
  }
  const session = input.session;
  const scope = input.scope;
  if (!session || typeof session !== "object" || Array.isArray(session)) {
    return rejectTurn("session_unavailable");
  }
  const sessionRow = session as Record<string, unknown>;
  if (sessionRow.project_context != null || sessionRow.steward_context == null) {
    return rejectTurn("non_steward_session");
  }
  if (sessionRow.answer_review === true) {
    return rejectTurn("answer_review_turn");
  }
  let stewardContext: Record<string, unknown>;
  try {
    stewardContext = normalizeStewardContext(sessionRow.steward_context);
  } catch {
    return rejectTurn("invalid_steward_context");
  }
  if (!scope || typeof scope !== "object" || Array.isArray(scope)) {
    return rejectTurn("scope_unavailable");
  }
  const scopeRow = scope as Record<string, unknown>;
  if (
    scopeRow.kind !== "external_audience"
    || scopeRow.bound_steward !== true
    || scopeRow.private_conversation !== true
  ) {
    return rejectTurn("audience_isolated");
  }
  const turn = input.turn;
  if (turn && typeof turn === "object" && !Array.isArray(turn)) {
    const turnRow = turn as Record<string, unknown>;
    if (
      turnRow.loopx_execution === true
      || turnRow.loopx_request != null
      || turnRow.origin === "native_loopx"
      || turnRow.origin === "peer"
      || "collaboration_request" in turnRow
      || turnRow.answer_review != null
      || turnRow.metadata_review != null
    ) {
      return rejectTurn("internal_or_review_turn");
    }
  }
  const senderType = typeof input.sender_type === "string" ? input.sender_type.trim().toLowerCase() : "";
  if (senderType === "bot" || senderType === "app") {
    return rejectTurn("bot_sender_excluded");
  }
  const operatorRef = String(stewardContext.operator_ref ?? "").trim();
  if (!operatorRef) {
    return rejectTurn("operator_ref_missing");
  }
  const explicitSender = typeof input.sender_ref === "string" ? input.sender_ref.trim() : "";
  const senderRef = explicitSender || operatorRef;
  if (!senderRef || senderRef === "unknown") {
    return rejectTurn("sender_unverified");
  }
  const isOwner = senderRef === operatorRef;
  const channelId = String(sessionRow.channel_id ?? "");
  const speakerDirectory = isOwner
    ? "owner"
    : `${sanitizeSpeakerSegment(input.sender_label)}~${createHash("sha256")
      .update(`${channelId}\n${senderRef}`)
      .digest("hex")
      .slice(0, SPEAKER_DIGITS)}`;

  const bindingId = String(stewardContext.binding_id);
  const actorPeerId = `steward-${bindingId}`;
  const memoryRootUri = `viking://user/${setting.user_space}/peers/${actorPeerId}/memories`;
  return {
    schema_version: "steward_memory_turn_plan_v0",
    eligible: true,
    reason: "eligible",
    actor_peer_id: actorPeerId,
    binding_id: bindingId,
    user_space: setting.user_space,
    auto_recall: setting.auto_recall,
    recall_timeout_seconds: setting.recall_timeout_seconds,
    is_owner: isOwner,
    speaker_directory: speakerDirectory,
    owner_preferences_uri: `${memoryRootUri}/preferences/owner`,
    speaker_preferences_uri: `${memoryRootUri}/preferences/${speakerDirectory}`,
    events_uri: `${memoryRootUri}/events`,
    memory_root_uri: memoryRootUri,
  };
}
