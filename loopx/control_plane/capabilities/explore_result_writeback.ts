/** Explore's explicit result attachment. Pure validation/intent; never writes. */
import {createHash} from "node:crypto";
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {jsonObject, requireJsonObject, requireNonEmptyString, requireStringArray, requireStringLiteral} from "../runtime_decode.ts";
import {APPLICABILITY_LIMIT, EXPLORE_EXECUTION_FROM_FINDING_SCHEMA, EXPLORE_PATH_DELTA_ATTACHMENT_SCHEMA,
  EXPLORE_RESULT_ATTACHMENT_SCHEMA, FINDING_EVIDENCE_SELECTION, QUESTION_LIMIT} from "./explore_context_delivery.ts";
import {normalizeGoalPathDelta} from "../goals/vision_checkpoint.ts";
import {EXPLORE_EXECUTION_OBSERVATION_SCHEMA, normalizeExploreExecutionObservation} from "./explore_execution_observation.ts";
import {isExploreEvidenceRef} from "./explore_evidence_ref.ts";
import {normalizeWorkYardstickDecision} from "../work_items/yardstick_decision.ts";
import {normalizeExploreMeasurements} from "./explore_measurements.ts";

const FIELDS = ["schema_version", "node_id", "question", "applicability", "input_revision",
  "observation", "interpretation", "status", "evidence_refs", "question_update", "relations", "execution_observation", "measurements", "measurement_context", "work_yardstick_decision"];
// Reuse the persisted Explore node/edge vocabulary, distinct from finding status.
const QUESTION_STATUSES = ["open", "exploring", "blocked", "resolved", "dead_end"] as const;
const RELATION_TYPES = ["subtopic_of", "depends_on", "answers", "supports", "refutes", "leads_to"] as const;
function evolutionFields(row: JsonObject, node: string): JsonObject {
  const result: JsonObject = {};
  if (Object.hasOwn(row, "question_update")) {
    const update = requireJsonObject(row.question_update, "question_update");
    if (Object.keys(update).some(key => !["status", "blocked_reason"].includes(key))) {
      throw new EffectRuntimeRequestError("question_update contains unknown fields");
    }
    const status = requireStringLiteral(update.status, QUESTION_STATUSES, "question status");
    if (status === "blocked") {
      result.question_update = {status, blocked_reason: text(update.blocked_reason, "blocked_reason", 320)};
    } else {
      if (Object.hasOwn(update, "blocked_reason")) {
        throw new EffectRuntimeRequestError("blocked_reason requires blocked question status");
      }
      result.question_update = {status};
    }
  }
  if (Object.hasOwn(row, "relations")) {
    if (!Array.isArray(row.relations) || !row.relations.length || row.relations.length > 4) {
      throw new EffectRuntimeRequestError("relations requires one to four explicit relations");
    }
    const seen = new Set<string>();
    result.relations = row.relations.map(value => {
      const relation = requireJsonObject(value, "question relation");
      if (Object.keys(relation).some(key => !["to_node", "edge_type", "summary"].includes(key))) {
        throw new EffectRuntimeRequestError("question relation contains unknown fields");
      }
      const to = explicitNodeId(relation.to_node);
      const type = requireStringLiteral(relation.edge_type, RELATION_TYPES, "relation type");
      const key = JSON.stringify([to, type]);
      if (to === node || seen.has(key)) {
        throw new EffectRuntimeRequestError("question relations cannot be self-links or duplicates");
      }
      seen.add(key);
      return {to_node: to, edge_type: type, summary: text(relation.summary, "relation summary", 320)};
    }).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
  }
  return result;
}
function text(value: unknown, field: string, limit: number): string {
  if (typeof value !== "string" || !value.trim() || value.length > limit) {
    throw new EffectRuntimeRequestError(`${field} requires nonempty text of at most ${limit} characters`);
  }
  return value.trim();
}
function explicitNodeId(value: unknown): string {
  if (typeof value !== "string" || value.length > 96 || !/^[A-Za-z][A-Za-z0-9_.:-]{0,95}$/.test(value.trim())) {
    throw new EffectRuntimeRequestError("Invalid Explore node_id: use a returned question id, or omit node_id with explicit question and applicability for a new question; do not precreate an area node");
  }
  return value.trim();
}
/** Expand an explicit same-finding binding before the canonical codec. */
function normalizeAttachedExecution(value: unknown, inputRevision: unknown, refs: string[]): JsonObject {
  const authored = jsonObject(value);
  if (authored?.schema_version !== EXPLORE_EXECUTION_FROM_FINDING_SCHEMA) {
    return normalizeExploreExecutionObservation({observation: value});
  }
  if (Object.hasOwn(authored, "input_revision")) {
    throw new EffectRuntimeRequestError("execution observation cannot override its explicit finding input revision");
  }
  return normalizeExploreExecutionObservation({observation: {...authored,
    schema_version: EXPLORE_EXECUTION_OBSERVATION_SCHEMA, input_revision: inputRevision,
    evidence_refs: authored.evidence_refs === FINDING_EVIDENCE_SELECTION ? refs : authored.evidence_refs,
  }});
}
function normalizeAttachment(value: unknown, visionPacket?: unknown, linkedScope?: unknown): JsonObject {
  const row = requireJsonObject(value, "Explore result attachment");
  if (row.schema_version === EXPLORE_PATH_DELTA_ATTACHMENT_SCHEMA) {
    return attachmentFromPathDelta(row, visionPacket, linkedScope);
  }
  if (Object.keys(row).some(key => !FIELDS.includes(key))) {
    throw new EffectRuntimeRequestError("Explore result attachment contains unknown fields");
  }
  requireStringLiteral(row.schema_version, [EXPLORE_RESULT_ATTACHMENT_SCHEMA], "attachment schema");
  const resolved = {...row, ...questionScope(row, linkedScope)};
  const question = text(resolved.question, "question", QUESTION_LIMIT);
  const applicability = text(resolved.applicability, "applicability", APPLICABILITY_LIMIT);
  // Identity is mechanical, never inferred evidence. Exact scoped text, not
  // revision/status/observation, binds successive results to the same question.
  const node = Object.hasOwn(resolved, "node_id")
    ? explicitNodeId(resolved.node_id)
    : "question_" + createHash("sha256").update(JSON.stringify([question, applicability])).digest("hex");
  if (!Array.isArray(row.evidence_refs) || !row.evidence_refs.length || row.evidence_refs.length > 8) {
    throw new EffectRuntimeRequestError("evidence_refs requires one to eight opaque evidence identifiers");
  }
  const refs = row.evidence_refs.map(value => {
    const ref = text(value, "evidence_ref", 128);
    if (!isExploreEvidenceRef(ref)) {
      throw new EffectRuntimeRequestError("evidence_ref must be an opaque identifier, not file content");
    }
    return ref;
  });
  const execution = Object.hasOwn(row, "execution_observation")
    ? normalizeAttachedExecution(row.execution_observation, row.input_revision, refs) : null;
  if (Object.hasOwn(row, "measurement_context") && !Object.hasOwn(row, "measurements")) {
    throw new EffectRuntimeRequestError("measurement_context requires measurements");
  }
  const measurements = Object.hasOwn(row, "measurements")
    // Transport also reconstructs already committed inputs before replay is
    // identified. New writes bind measurements in their precommit event plan;
    // a new ingestion intent repeats that check below, never during replay.
    ? normalizeExploreMeasurements(row.measurements, row.measurement_context) : null;
  if (measurements?.some(item => item.input_revision !== text(row.input_revision, "input_revision", 160)
    || !refs.includes(item.evidence_ref))) {
    throw new EffectRuntimeRequestError("measurements must retain this finding's input revision and evidence references");
  }
  if (execution && (execution.input_revision !== text(row.input_revision, "input_revision", 160)
    || (execution.evidence_refs as string[]).some(ref => !refs.includes(ref)))) {
    throw new EffectRuntimeRequestError("execution observation must retain this finding's input revision and evidence references");
  }
  // Status reuses the existing finding vocabulary. The caller supplies the
  // interpretation; a failed prerequisite belongs in a tentative observation.
  return {schema_version: EXPLORE_RESULT_ATTACHMENT_SCHEMA, node_id: node,
    question, applicability,
    input_revision: text(row.input_revision, "input_revision", 160),
    observation: text(row.observation, "observation", 320),
    interpretation: text(row.interpretation, "interpretation", 1200),
    status: requireStringLiteral(row.status, ["tentative", "confirmed", "refuted"], "finding status"),
    evidence_refs: [...new Set(refs)], ...evolutionFields(row, node),
    ...(execution ? {execution_observation: execution} : {}),
    ...(measurements ? {measurements} : {}),
    ...(row.work_yardstick_decision !== undefined ? {work_yardstick_decision: normalizeWorkYardstickDecision(row.work_yardstick_decision, refs)} : {})};
}
/** Explicit reference, not inferred evidence. Preserve every route item verbatim. */
function attachmentFromPathDelta(row: JsonObject, visionPacket: unknown, linkedScope: unknown): JsonObject {
  const fields = ["schema_version", "node_id", "question", "applicability", "input_revision", "status", "evidence_refs", "question_update", "relations", "execution_observation", "measurements", "measurement_context", "work_yardstick_decision"];
  if (Object.keys(row).some(key => !fields.includes(key))) {
    throw new EffectRuntimeRequestError("Explore path_delta attachment contains unknown fields");
  }
  const vision = requireJsonObject(visionPacket, "Explore path_delta capture requires the same vision packet");
  const [delta] = normalizeGoalPathDelta(vision.path_delta);
  if (delta === null) {
    throw new EffectRuntimeRequestError("Explore path_delta capture requires top-level path_delta in this vision packet");
  }
  // Plain list text preserves each normalized item without JSON escaping
  // inflating the Goal owner's nine-by-120-character route budget.
  const decision = [
    `Outcome: ${delta.outcome}`,
    ...["retained", "changed", "stopped"].filter(key => delta[key] !== undefined)
      .map(key => `${key}:\n- ${(delta[key] as string[]).join("\n- ")}`),
  ].join("\n");
  const refs = Object.hasOwn(row, "evidence_refs") ? row.evidence_refs : delta.evidence_refs;
  if (!Array.isArray(refs) || refs.some(ref => !(delta.evidence_refs as unknown[] | undefined)?.includes(ref))) {
    throw new EffectRuntimeRequestError("Explore selected evidence_refs must occur in this same path_delta");
  }
  return normalizeAttachment({...row, ...questionScope(row, linkedScope), schema_version: EXPLORE_RESULT_ATTACHMENT_SCHEMA,
    observation: delta.observed_reality, interpretation: decision, evidence_refs: refs});
}
/** Reuse only an unambiguous question linked by the claimed Todo. */
function questionScope(row: JsonObject, value: unknown): JsonObject {
  const question = Object.hasOwn(row, "question"), applicability = Object.hasOwn(row, "applicability");
  if (question && applicability) return {};
  if (question || applicability) {
    throw new EffectRuntimeRequestError("Supply both question and applicability, or omit both for a linked question");
  }
  const scope = requireJsonObject(value, "Explore capture requires a canonical linked question scope");
  const nodeId = Object.hasOwn(row, "node_id") ? explicitNodeId(row.node_id) : null;
  const refs = requireStringArray(scope.requested_node_refs, "linked question refs");
  const nodes = Array.isArray(scope.nodes) ? scope.nodes.map(node => requireJsonObject(node, "linked question")) : [];
  const matches = nodes.filter(node => typeof node.node_id === "string" && refs.includes(node.node_id)
    && node.node_kind === "question" && (nodeId === null || node.node_id === nodeId));
  if (matches.length !== 1) {
    throw new EffectRuntimeRequestError("Explore capture requires one existing question linked to this Todo; use its node_id when ambiguous");
  }
  return {node_id: explicitNodeId(matches[0].node_id), question: text(matches[0].title, "question", QUESTION_LIMIT),
    applicability: text(matches[0].summary, "applicability", APPLICABILITY_LIMIT)};
}
export function normalizeExploreResultAttachment(params: JsonObject): JsonObject {
  const result = normalizeAttachment(params.attachment, params.vision_packet, params.linked_scope);
  if (Object.hasOwn(params, "other_attachment")) {
    const other = normalizeAttachment(params.other_attachment, params.vision_packet, params.linked_scope);
    // Evidence identifiers form a set; source order must not create conflict.
    const comparable = (row: JsonObject) => JSON.stringify({...row,
      evidence_refs: [...row.evidence_refs as string[]].sort()});
    if (comparable(result) !== comparable(other)) {
      throw new EffectRuntimeRequestError("Explore result sources conflict; supply one result or matching contents");
    }
  }
  return result;
}
/** State-dependent preflight for the existing result writer; no graph effects. */
export function planExploreQuestionEvolution(params: JsonObject): JsonObject {
  const attachment = normalizeExploreResultAttachment({attachment: params.attachment});
  if (!Object.hasOwn(attachment, "question_update") && !Object.hasOwn(attachment, "relations")) {
    return {existing_node: null};
  }
  const agent = requireNonEmptyString(params.agent_id, "question owner");
  if (!Array.isArray(params.nodes) || !Array.isArray(params.edges)) {
    throw new EffectRuntimeRequestError("Question evolution requires the current Goal graph");
  }
  const nodes = params.nodes.map(row => requireJsonObject(row, "Explore node"));
  const edges = params.edges.map(row => requireJsonObject(row, "Explore edge"));
  const existing = nodes.find(node => node.node_id === attachment.node_id);
  if (existing && (existing.agent_id !== agent || existing.node_kind !== "question")) {
    throw new EffectRuntimeRequestError("Only the recorded question owner may evolve its state or relations");
  }
  if (existing && (existing.title !== attachment.question || existing.summary !== attachment.applicability)) {
    throw new EffectRuntimeRequestError("Explore question evolution must preserve its canonical scope");
  }
  const proposed = (attachment.relations ?? []) as JsonObject[];
  for (const relation of proposed) {
    if (!nodes.some(node => node.node_id === relation.to_node)) {
      throw new EffectRuntimeRequestError("Explore question relation target must already exist in this Goal");
    }
  }
  for (const relation of proposed) {
    if (!["depends_on", "subtopic_of"].includes(String(relation.edge_type))) continue;
    const all: JsonObject[] = [...edges, ...proposed.map(row => ({...row, from_node: attachment.node_id}))];
    const pending = [relation.to_node];
    const seen = new Set<unknown>();
    while (pending.length) {
      const next = pending.pop();
      if (next === attachment.node_id) throw new EffectRuntimeRequestError("Explore question relation creates a cycle");
      if (seen.has(next)) continue;
      seen.add(next);
      pending.push(...all.filter(edge => edge.edge_type === relation.edge_type && edge.from_node === next)
        .map(edge => edge.to_node));
    }
  }
  return {existing_node: existing ?? null};
}
export function produceExploreResultIntent(params: JsonObject): JsonObject {
  const projection = requireJsonObject(params.projection, "writeback projection");
  const attachment = normalizeExploreResultAttachment({attachment: projection.explore_result});
  if (attachment.measurements !== undefined) {
    normalizeExploreMeasurements(attachment.measurements, undefined, attachment.execution_observation);
  }
  const receipt = requireJsonObject(params.receipt, "writeback receipt");
  const source = text(receipt.event_id, "source receipt", 200);
  const key = createHash("sha256").update(source).digest("hex");
  return {schema_version: "loopx_post_writeback_capability_hook_result_v0",
    hook_id: "explore.result_writeback", capability_id: "explore", phase: "post_writeback", status: "intent",
    intent: {schema_version: "loopx_capability_intent_v0", intent_kind: "explore.result_ingestion",
      idempotency_key: `explore-result:${key}`, source_receipt_id: source,
      payload: {attachment}, requested_write_scope: []}};
}
