import type {JsonObject} from "../effect_program.ts";
import {ENVELOPED_SHA256_PATTERN} from "../content_digest.ts";
import {canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {requireBoolean, requireJsonObject, requireNonEmptyString, requireStringArray} from "../runtime_decode.ts";
import {
  COMPLETE_TURN_DECISION_VERSION, decodeTurnSource, evaluateTurnEnvelope, quotaActionSignatureDocument,
  requireCompleteTurnDecision, turnEnvelopeActionSignatureDocument,
} from "../quota/turn_envelope.ts";

// This is a local host-IO contract, not a new capability or authority vocabulary.
// Capability providers own enablement. These rules only bound their contributions.
const RESERVED = new Set([
  "schema_version", "turn_key", "turn_envelope", "work_contract", "source_decision_hash",
  "contract_binding", "turn_context_contributions",
  "session", "goal_ref", "route",
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

function contextSource(value: unknown, bindReaderLocation = false): JsonObject {
  const source = requireJsonObject(value, "context source");
  if (source.schema_version === "loopx_turn_host_request_v1") {
    // These identities come from the complete-source validator. The basis
    // does not include the reader location or regenerate a legacy signature.
    const turnKey = requireNonEmptyString(source.turn_key, "context Turn key");
    const sourceHash = requireNonEmptyString(source.source_decision_hash, "context source hash");
    if (!ENVELOPED_SHA256_PATTERN.test(turnKey) || !ENVELOPED_SHA256_PATTERN.test(sourceHash)) {
      throw new Error("invalid complete context binding");
    }
    return {turn_key: turnKey, source_decision_hash: sourceHash};
  }
  // The replan reader generates commands that carry the capture's own hash.
  // Bind the governing action and settlement rather than these read addresses.
  const {replan_read_path: _readerPath, ...beforeReaderBinding} = source;
  return {goal_id: source.goal_id, agent_id: source.agent_id,
    governing: "heartbeat_receipt" in source
      ? quotaActionSignatureDocument(bindReaderLocation ? source : beforeReaderBinding)
      : turnEnvelopeActionSignatureDocument(source)};
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

function normalizeTurnContextContribution(input: JsonObject) {
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
  return {hook_id: registration.hook_id, capability_id: registration.capability_id,
    readback_slots: slots, context_fields: context, instructions, result_fields: resultFields, readback};
}

export function validateTurnContextContribution(input: JsonObject): JsonObject {
  const normalized = normalizeTurnContextContribution(input);
  const embed = input.embed_in_complete_source !== undefined &&
    requireBoolean(input.embed_in_complete_source, "embed_in_complete_source");
  const hasSource = Object.hasOwn(input, "source") || Object.hasOwn(input, "source_transport");
  if (input.readback_only !== undefined && requireBoolean(input.readback_only, "readback_only")) {
    // Status has no advice to bind and remains available independently.
    if (embed || hasSource || Object.keys(normalized.context_fields).length ||
        normalized.instructions.length || Object.keys(normalized.result_fields).length) {
      throw new Error("readback-only context validation cannot carry a source or a contribution");
    }
    return normalized;
  }
  if (embed) {
    if (hasSource) throw new Error("embedded context cannot carry a separate source");
    // A draft for the quota producer. Only the verified complete capture can
    // deliver it. Standalone Host/journal contributions still need a basis.
    return normalized;
  }
  const source = Object.hasOwn(input, "source_transport") ? decodeTurnSource(input, "source") : input.source;
  return {...normalized, basis_digest: canonicalAuthoritySha256({source: contextSource(source), ...normalized})};
}

function storedContributionInput(contribution: JsonObject, readback: JsonObject): JsonObject {
  return {registration: {hook_id: contribution.hook_id, capability_id: contribution.capability_id,
      readback_slots: contribution.readback_slots, max_result_bytes: 65536},
    result: {context_fields: contribution.context_fields, instructions: contribution.instructions,
      result_fields: contribution.result_fields, readback}};
}

export function projectTurnContextContribution(input: JsonObject): JsonObject {
  const contribution = requireJsonObject(input.contribution, "context contribution");
  const readback = requireJsonObject(input.readback, "current context readback");
  // Revalidate serialized contributions on journal recovery and before host input.
  const normalized = validateTurnContextContribution({source: input.source,
    ...storedContributionInput(contribution, readback)});
  if (contribution.basis_digest !== normalized.basis_digest) {
    const source = requireJsonObject(input.source, "context source");
    const {basis_digest: _currentBasis, ...value} = normalized;
    if (source.schema_version === "loopx_turn_host_request_v1" && Object.hasOwn(source, "recorded_projection")) {
      const legacyBasis = canonicalAuthoritySha256({source: contextSource(source.recorded_projection), ...value});
      if (contribution.basis_digest === legacyBasis) {
        // An already validated result can recover its original bounded slots.
        // The historical basis is insufficient to deliver advice to a Host.
        return {...normalized, context_fields: {}, instructions: [], basis_digest: legacyBasis};
      }
    }
    // An existing unversioned quota observation may already have been bound
    // to its exact reader. Preserve that basis only on an exact legacy match.
    if (source.schema_version !== "loopx_turn_host_request_v1" &&
        "heartbeat_receipt" in source && !Object.hasOwn(source, "contract_binding") &&
        typeof source.replan_read_path === "string") {
      const legacyBasis = canonicalAuthoritySha256({source: contextSource(source, true), ...value});
      if (contribution.basis_digest === legacyBasis) return {...normalized, basis_digest: legacyBasis};
    }
    throw new Error("context source or contribution changed after registration");
  }
  return normalized;
}

/** Project one Host's optional contributions against its one governing source. */
export function projectTurnContext(input: JsonObject): JsonObject {
  const source = requireJsonObject(input.source, "context source");
  const complete = source.schema_version === COMPLETE_TURN_DECISION_VERSION ||
    Object.hasOwn(source, "contract_binding");
  if (complete) {
    if (Object.hasOwn(input, "contributions") || Object.hasOwn(input, "readback")) {
      throw new Error("complete context must read contributions from its original source");
    }
    requireCompleteTurnDecision(source, input.source_number_tokens);
  }
  const contributions = complete ? source.turn_context_contributions : input.contributions;
  if (!Array.isArray(contributions)) throw new Error("context contributions must be an array");
  const readback = complete ? source : requireJsonObject(input.readback, "current context readback");
  const context: JsonObject = {}, resultFields: JsonObject = {};
  const instructions: string[] = [];
  let recordedSource: JsonObject | undefined;
  for (const value of contributions) {
    try {
      const contribution = requireJsonObject(value, "context contribution");
      const slots = requireStringArray(contribution.readback_slots, "context readback slots");
      const currentReadback = Object.fromEntries(slots.map(key => [key, readback[key] ?? null]));
      const embedded = complete && !Object.hasOwn(contribution, "basis_digest");
      let projected: JsonObject;
      if (embedded) {
        if (slots.some(key => !Object.hasOwn(readback, key))) continue;
        projected = normalizeTurnContextContribution(storedContributionInput(contribution, currentReadback));
      } else {
        // Older complete captures still carry the registered quota basis.
        // Recover it with its original codec, after checking the capture.
        if (complete && recordedSource === undefined) {
          const identity = evaluateTurnEnvelope({operation: "capability_automation_source", payload: source,
            source_number_tokens: input.source_number_tokens});
          recordedSource = {...source, agent_id: identity.agent_id};
        }
        projected = projectTurnContextContribution({contribution, source: recordedSource ?? source,
          readback: currentReadback});
      }
      const fields = requireJsonObject(projected.context_fields, "context fields");
      const results = requireJsonObject(projected.result_fields, "capability result fields");
      const advice = requireStringArray(projected.instructions, "context instructions");
      if (Object.keys(fields).some(key => Object.hasOwn(context, key)) ||
          Object.keys(results).some(key => Object.hasOwn(resultFields, key))) continue;
      Object.assign(context, fields);
      instructions.push(...advice);
      Object.assign(resultFields, results);
    } catch {
      // A stale, malformed or conflicting contribution cannot suppress another
      // provider's valid advice, or be repaired from cached/private context.
    }
  }
  if (!Object.keys(context).length && !instructions.length && !Object.keys(resultFields).length) return {};
  return {...context, capability_context_fields: Object.keys(context),
    capability_instructions: instructions, capability_result_fields: resultFields};
}
