import type { JsonObject } from "../effect_program.ts";
import {
  requireBoolean,
  requireNonEmptyString as requiredString,
  requireJsonObject as requiredObject,
} from "../runtime_decode.ts";
import { ENVELOPED_SHA256_PATTERN } from "../content_digest.ts";

const SCHEMA_VERSION = "pending_capability_intent_projection_v0";
const TOKEN_RE = /^[a-z][a-z0-9_.:-]{2,127}$/;
const IDEMPOTENCY_RE = /^[A-Za-z0-9][A-Za-z0-9_.:-]{2,255}$/;
const DIGEST_RE = ENVELOPED_SHA256_PATTERN;
const FIELDS = new Set([
  "schema_version",
  "capability_id",
  "intent_kind",
  "idempotency_key",
  "intent_digest",
  "goal_id",
  "agent_id",
  "state",
  "action_kind",
  "action_summary",
  "command",
  "generation_authorized",
  "external_delivery_authorized",
  "agent_read_required",
]);

function requireExactFields(value: JsonObject): void {
  const fields = Object.keys(value);
  if (fields.length !== FIELDS.size || fields.some((field) => !FIELDS.has(field))) {
    throw new Error("pending capability intent fields are invalid");
  }
}

function boundedToken(value: unknown, label: string): string {
  const token = requiredString(value, label);
  if (!TOKEN_RE.test(token)) {
    throw new Error(`${label} is invalid`);
  }
  return token;
}

export function projectPendingCapabilityIntent(value: unknown): JsonObject {
  const projection = requiredObject(value, "pending capability intent");
  requireExactFields(projection);
  if (
    projection.schema_version !== SCHEMA_VERSION ||
    projection.state !== "pending" ||
    requireBoolean(
      projection.generation_authorized,
      "pending capability intent generation_authorized",
    ) !== true ||
    requireBoolean(
      projection.external_delivery_authorized,
      "pending capability intent external_delivery_authorized",
    ) !== true ||
    requireBoolean(
      projection.agent_read_required,
      "pending capability intent agent_read_required",
    ) !== true
  ) {
    throw new Error("pending capability intent authority is invalid");
  }
  const idempotencyKey = requiredString(
    projection.idempotency_key,
    "pending capability intent idempotency_key",
  );
  const intentDigest = requiredString(
    projection.intent_digest,
    "pending capability intent intent_digest",
  );
  const actionSummary = requiredString(
    projection.action_summary,
    "pending capability intent action_summary",
  );
  const command = requiredString(
    projection.command,
    "pending capability intent command",
  );
  if (
    !IDEMPOTENCY_RE.test(idempotencyKey) ||
    !DIGEST_RE.test(intentDigest) ||
    actionSummary.length > 320 ||
    command.length > 1200 ||
    /[\r\n\0]/u.test(command)
  ) {
    throw new Error("pending capability intent action contract is invalid");
  }
  const capabilityId = boundedToken(
    projection.capability_id,
    "pending capability intent capability_id",
  );
  const intentKind = boundedToken(
    projection.intent_kind,
    "pending capability intent intent_kind",
  );
  const goalId = boundedToken(
    projection.goal_id,
    "pending capability intent goal_id",
  );
  const agentId = boundedToken(
    projection.agent_id,
    "pending capability intent agent_id",
  );
  const actionKind = boundedToken(
    projection.action_kind,
    "pending capability intent action_kind",
  );
  const expectedCommand =
    `loopx periodic-report consume-pending --goal-id ${goalId} ` +
    `--agent-id ${agentId} --execute`;
  if (
    capabilityId !== "periodic-report" ||
    intentKind !== "periodic_report.trigger_evaluation" ||
    actionKind !== "consume_periodic_report_intent" ||
    command !== expectedCommand
  ) {
    throw new Error("pending capability intent action is unsupported");
  }
  return {
    schema_version: SCHEMA_VERSION,
    capability_id: capabilityId,
    intent_kind: intentKind,
    idempotency_key: idempotencyKey,
    intent_digest: intentDigest,
    goal_id: goalId,
    agent_id: agentId,
    state: "pending",
    action_kind: actionKind,
    action_summary: actionSummary,
    command,
    generation_authorized: true,
    external_delivery_authorized: true,
    agent_read_required: true,
  };
}
