/** Explore's explicit result attachment. Pure validation/intent; never writes. */
import {createHash} from "node:crypto";
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireJsonObject, requireStringArray, requireStringLiteral} from "../runtime_decode.ts";
import {normalizeGoalPathDelta} from "../goals/vision_checkpoint.ts";

export const EXPLORE_RESULT_ATTACHMENT_SCHEMA = "explore_result_attachment_v0";
export const EXPLORE_PATH_DELTA_ATTACHMENT_SCHEMA = "explore_result_from_path_delta_v0";
// The complete scoped finding fits the result-log owner: 160 revision + 200
// applicability + 320 observation + 1200 interpretation + four labels < 2000.
export const EXPLORE_WRITEBACK_SUMMARY_LIMIT = 2000;
/** Shared point-of-use guidance; optional metadata never changes settlement. */
export function exploreResultWritebackAffordance(): JsonObject {
  return {
    capability_id: "explore",
    option: "--explore-result-json <result.json>",
    inline_option: "--agent-vision-json <vision.json>",
    inline_field: "explore_result",
    attachment_schema: EXPLORE_RESULT_ATTACHMENT_SCHEMA,
    required: false,
    path_delta_attachment_schema: EXPLORE_PATH_DELTA_ATTACHMENT_SCHEMA,
    path_delta_attachment_template: {
      schema_version: EXPLORE_PATH_DELTA_ATTACHMENT_SCHEMA,
      question: "", applicability: "", input_revision: "", status: "tentative",
    },
    linked_question_attachment_template: {
      schema_version: EXPLORE_PATH_DELTA_ATTACHMENT_SCHEMA,
      node_id: "<linked-question-id>", input_revision: "", status: "tentative",
    },
    guidance: [
      "After validating reusable evidence, put explore_result in the vision JSON already submitted with --agent-vision-json.",
      "For an open Todo under hard_lease, keep the active task lease through refresh-state and Explore graph/Todo-link delivery; release only after explore_result_delivery.ok=true.",
      "If already released, re-enter the normal guard/claim/lease path before retrying.",
      "A completed Todo under hard_lease permits only its existing additive evidence-link exception with the owner's retained released lease key and current version; this grants no new execution authority.",
      "An exact replay of already-successful delivery is readback-only and needs no new lease; unfinished delivery still requires the applicable claim/lease proof.",
      "Prefer path_delta_attachment_template when this same packet contains an evidence-linked path_delta: supply the question, applicability, tested input revision and explicit finding status.",
      "For a new question, omit node_id: the hook derives its identity from the exact question and applicability, creates it and links this Todo; no explore node call is needed.",
      "To add to a known question, reuse its returned node_id and canonical scope.",
      "The hook reuses the complete observation and route decision.",
      "For an existing question linked to this Todo, use linked_question_attachment_template: omit both question and applicability to reuse its canonical scope.",
      "New or unlinked questions still require explicit scope; input_revision and status are never inferred.",
      "If path_delta also references local files, explicitly select its opaque identifiers with optional evidence_refs; selected refs must occur in this same path_delta.",
      "Without a selection, all refs must be opaque identifiers.",
      "Otherwise fill attachment_template, inline or via --explore-result-json.",
      "Matching sources coalesce; conflicts reject.",
      "Routine work needs no attachment; do not invent findings.",
      "Capture is optional, not a settlement obligation.",
      "A stopped route does not imply refuted status, and a score alone does not prove refutation.",
      "Keep raw logs local.",
      "Observations allow 320 characters, interpretations 1200; the complete scoped summary is preserved within 2000 characters.",
    ].join(" "),
    // Blank evidence fields deliberately fail validation until the caller
    // supplies observed facts. Goal/Agent/Todo/Turn bind in ordinary writeback;
    // a source-code revision here would not establish the tested input revision.
    attachment_template: {
      schema_version: EXPLORE_RESULT_ATTACHMENT_SCHEMA,
      question: "", applicability: "", input_revision: "",
      observation: "", interpretation: "", status: "tentative", evidence_refs: [],
    },
  };
}

const FIELDS = ["schema_version", "node_id", "question", "applicability", "input_revision",
  "observation", "interpretation", "status", "evidence_refs"];
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
function normalizeAttachment(value: unknown, visionPacket?: unknown, linkedScope?: unknown): JsonObject {
  const row = requireJsonObject(value, "Explore result attachment");
  if (row.schema_version === EXPLORE_PATH_DELTA_ATTACHMENT_SCHEMA) {
    return attachmentFromPathDelta(row, visionPacket, linkedScope);
  }
  if (Object.keys(row).some(key => !FIELDS.includes(key))) {
    throw new EffectRuntimeRequestError("Explore result attachment contains unknown fields");
  }
  requireStringLiteral(row.schema_version, [EXPLORE_RESULT_ATTACHMENT_SCHEMA], "attachment schema");
  const question = text(row.question, "question", 180);
  const applicability = text(row.applicability, "applicability", 200);
  // Identity is mechanical, never inferred evidence. Exact scoped text, not
  // revision/status/observation, binds successive results to the same question.
  const node = Object.hasOwn(row, "node_id")
    ? explicitNodeId(row.node_id)
    : "question_" + createHash("sha256").update(JSON.stringify([question, applicability])).digest("hex");
  if (!Array.isArray(row.evidence_refs) || !row.evidence_refs.length || row.evidence_refs.length > 8) {
    throw new EffectRuntimeRequestError("evidence_refs requires one to eight opaque evidence identifiers");
  }
  const refs = row.evidence_refs.map(value => {
    const ref = text(value, "evidence_ref", 128);
    if (!/^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$/.test(ref)) {
      throw new EffectRuntimeRequestError("evidence_ref must be an opaque identifier, not file content");
    }
    return ref;
  });
  // Status reuses the existing finding vocabulary. The caller supplies the
  // interpretation; a failed prerequisite belongs in a tentative observation.
  return {schema_version: EXPLORE_RESULT_ATTACHMENT_SCHEMA, node_id: node,
    question, applicability,
    input_revision: text(row.input_revision, "input_revision", 160),
    observation: text(row.observation, "observation", 320),
    interpretation: text(row.interpretation, "interpretation", 1200),
    status: requireStringLiteral(row.status, ["tentative", "confirmed", "refuted"], "finding status"),
    evidence_refs: [...new Set(refs)]};
}
/** Explicit reference, not inferred evidence. Preserve every route item verbatim. */
function attachmentFromPathDelta(row: JsonObject, visionPacket: unknown, linkedScope: unknown): JsonObject {
  const fields = ["schema_version", "node_id", "question", "applicability", "input_revision", "status", "evidence_refs"];
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
/** Only an explicit reference to the claimed Todo's stored question can reuse scope. */
function questionScope(row: JsonObject, value: unknown): JsonObject {
  const question = Object.hasOwn(row, "question"), applicability = Object.hasOwn(row, "applicability");
  if (question && applicability) return {};
  if (question || applicability) {
    throw new EffectRuntimeRequestError("Supply both question and applicability, or omit both for a linked question");
  }
  const scope = requireJsonObject(value, "Explore capture requires a canonical linked question scope");
  const nodeId = explicitNodeId(row.node_id);
  const refs = requireStringArray(scope.requested_node_refs, "linked question refs");
  const nodes = Array.isArray(scope.nodes) ? scope.nodes.map(node => requireJsonObject(node, "linked question")) : [];
  const matches = nodes.filter(node => node.node_id === nodeId && node.node_kind === "question");
  if (!refs.includes(nodeId) || matches.length !== 1) {
    throw new EffectRuntimeRequestError("Explore capture requires an existing question linked to this Todo");
  }
  return {question: text(matches[0].title, "question", 180),
    applicability: text(matches[0].summary, "applicability", 200)};
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
export function produceExploreResultIntent(params: JsonObject): JsonObject {
  const projection = requireJsonObject(params.projection, "writeback projection");
  const attachment = normalizeExploreResultAttachment({attachment: projection.explore_result});
  const receipt = requireJsonObject(params.receipt, "writeback receipt");
  const source = text(receipt.event_id, "source receipt", 200);
  const key = createHash("sha256").update(source).digest("hex");
  return {schema_version: "loopx_post_writeback_capability_hook_result_v0",
    hook_id: "explore.result_writeback", capability_id: "explore", phase: "post_writeback", status: "intent",
    intent: {schema_version: "loopx_capability_intent_v0", intent_kind: "explore.result_ingestion",
      idempotency_key: `explore-result:${key}`, source_receipt_id: source,
      payload: {attachment}, requested_write_scope: []}};
}
