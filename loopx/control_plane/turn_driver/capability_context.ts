import type {JsonObject} from "../effect_program.ts";
import {canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {requireJsonObject, requireNonEmptyString, requireStringArray} from "../runtime_decode.ts";
import {turnEnvelopeActionSignatureDocument} from "../quota/turn_envelope.ts";

// This is a local host-IO contract, not a new capability or authority vocabulary.
// Capability providers own enablement. These rules only bound their contributions.
const RESERVED = new Set([
  "schema_version", "turn_key", "turn_envelope", "session", "goal_ref", "route",
  "result_contract", "capability_context_fields", "capability_instructions",
  "capability_result_fields", "result_kind", "completed_phases", "classification",
  "next_action", "recommended_action", "summary", "agent_vision_json",
  "path_delta_mode", "delivery_batch_scale", "delivery_outcome", "vision_unchanged_reason",
  "work_evidence", "validation_evidence", "child_execution_receipts",
  "primary_action", "required_reads", "write_scope", "workspace_guard", "boundary",
  "work_context", "selected_todo", "unavailable_context", "agent_context",
  "replan_action_packet", "detail_ref", "action_signature", "execution_profile",
  "vision_continuation_audit", "goal_id", "agent_id",
]);
const FIELD = /^[a-z][a-z0-9_]{2,95}$/;

function contextSource(value: unknown): JsonObject {
  const source = requireJsonObject(value, "context source");
  // Reuse the governing owner. Binding the original observation adds detail
  // locations/measurements without changing the authorized decision.
  return {goal_id: source.goal_id, agent_id: source.agent_id,
    governing: turnEnvelopeActionSignatureDocument(source)};
}

function fields(value: unknown, label: string): JsonObject {
  const result = requireJsonObject(value, label);
  if (Object.keys(result).length > 8 || Object.keys(result).some(k => !FIELD.test(k) || RESERVED.has(k))) {
    throw new Error(`${label} may not replace host authority or core result fields`);
  }
  return result;
}

export function validateTurnContextRegistration(input: unknown): JsonObject {
  const registration = requireJsonObject(input, "context registration");
  const hookId = requireNonEmptyString(registration.hook_id, "hook_id");
  const capabilityId = requireNonEmptyString(registration.capability_id, "capability_id");
  if (![hookId, capabilityId].every(s => /^[a-z][a-z0-9_.:-]{2,95}$/.test(s))) throw new Error("invalid context identity");
  const slots = requireStringArray(registration.readback_slots, "context readback slots");
  fields(Object.fromEntries(slots.map(s => [s, null])), "context readback slots");
  if (new Set(slots).size !== slots.length) throw new Error("duplicate context readback slots");
  const maximum = registration.max_result_bytes;
  if (!Number.isInteger(maximum) || Number(maximum) < 1024 || Number(maximum) > 65536) throw new Error("context budget exceeded");
  return {hook_id: hookId, capability_id: capabilityId, readback_slots: slots, max_result_bytes: maximum};
}

export function validateTurnContextContribution(input: JsonObject): JsonObject {
  const registration = validateTurnContextRegistration(input.registration);
  const result = requireJsonObject(input.result, "context result");
  if (Buffer.byteLength(JSON.stringify(result)) > Number(registration.max_result_bytes)) throw new Error("context budget exceeded");
  const context = fields(result.context_fields, "context fields");
  const readback = fields(result.readback, "context readback");
  const slots = requireStringArray(registration.readback_slots, "context readback slots");
  if (Object.keys(readback).length !== slots.length || Object.keys(readback).some(k => !slots.includes(k))) throw new Error("undeclared context readback");
  const instructions = requireStringArray(result.instructions, "context instructions");
  if (instructions.length > 8 || instructions.some(s => s.length > 4096)) throw new Error("context instructions exceeded");
  const resultFields = fields(result.result_fields, "capability result fields");
  for (const descriptor of Object.values(resultFields)) {
    const schema = requireJsonObject(descriptor, "capability result schema");
    if (Object.keys(schema).length !== 2 || schema.type !== "string" ||
        !Number.isInteger(schema.maxLength) || Number(schema.maxLength) < 1 || Number(schema.maxLength) > 16384) {
      throw new Error("capability result must be bounded optional text");
    }
  }
  const normalized = {hook_id: registration.hook_id, capability_id: registration.capability_id,
    readback_slots: slots, context_fields: context, instructions, result_fields: resultFields, readback};
  return {...normalized, basis_digest: canonicalAuthoritySha256({source: contextSource(input.source), ...normalized})};
}

export function projectTurnContextContribution(input: JsonObject): JsonObject {
  const contribution = requireJsonObject(input.contribution, "context contribution");
  const readback = requireJsonObject(input.readback, "current context readback");
  // Revalidate serialized contributions on journal recovery and before host input.
  const normalized = validateTurnContextContribution({source: input.source,
    registration: {hook_id: contribution.hook_id, capability_id: contribution.capability_id,
      readback_slots: contribution.readback_slots, max_result_bytes: 65536},
    result: {context_fields: contribution.context_fields, instructions: contribution.instructions,
      result_fields: contribution.result_fields, readback}});
  if (contribution.basis_digest !== normalized.basis_digest) {
    throw new Error("context source or contribution changed after registration");
  }
  return normalized;
}
