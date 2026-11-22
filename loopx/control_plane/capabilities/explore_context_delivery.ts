/** Explore guidance and exact references shared by captured and live context. */
import {isDeepStrictEqual} from "node:util";
import type {JsonObject} from "../effect_program.ts";
import {jsonObject, requireJsonObject, requireNonEmptyString, requireStringArray} from "../runtime_decode.ts";
import {contextSourceIdentity} from "../quota/diagnostic_projection.ts";

export function exploreTurnContextBoundary(): JsonObject {
  return {read_only: true, changes_configuration: false, writes_evidence: false,
    claims_todos: false, starts_agents: false, changes_quota: false};
}

/** A disabled opening from the live reader has no graph, plan or page to project.
 * Other requests retain the full reader's validation and replay rules. */
export function projectEmptyExploreTurnContext(params: JsonObject): JsonObject | undefined {
  if (params.graph_enabled !== false || jsonObject(params.harness_gate)?.enabled !== false ||
      (params.result_limit ?? 3) !== 3 || (params.result_offset ?? 0) !== 0 ||
      params.result_node != null || params.result_revision != null || params.review_pages !== undefined ||
      params.todo_id != null ||
      (params.include_finding_digests != null && typeof params.include_finding_digests !== "boolean")) {
    return undefined;
  }
  const projection = jsonObject(params.projection), plan = jsonObject(params.plan);
  if (!projection || !plan || Object.keys(projection).length || Object.keys(plan).length) return undefined;
  const goal = requireNonEmptyString(params.goal_id, "goal_id");
  const agent = requireNonEmptyString(params.agent_id, "agent_id");
  requireStringArray(params.route, "route");
  return {ok: true, goal_id: goal, agent_id: agent, graph_enabled: false, harness_enabled: false,
    graph: null, harness: null, boundary: exploreTurnContextBoundary()};
}

export const EXPLORE_RESULT_ATTACHMENT_SCHEMA = "explore_result_attachment_v0";
export const EXPLORE_PATH_DELTA_ATTACHMENT_SCHEMA = "explore_result_from_path_delta_v0";
export const EXPLORE_EXECUTION_FROM_FINDING_SCHEMA = "explore_execution_from_finding_v0";
export const FINDING_EVIDENCE_SELECTION = "finding";
export const QUESTION_LIMIT = 180;
export const APPLICABILITY_LIMIT = 200;
// The complete scoped finding fits the result-log owner: 160 revision + 200
// applicability + 320 observation + 1200 interpretation + four labels < 2000.
export const EXPLORE_WRITEBACK_SUMMARY_LIMIT = 2000;
// These clauses constrain evidence collection, route selection or lease lifetime
// before authoring. Keep them in the entry even when attachment syntax is read
// later. The full standalone guidance retains its original order and bytes.
const EXPLORE_BEFORE_WORK = {
  openLease: "When execution fencing applies to an open Todo, keep its claimed execution through refresh-state and Explore graph/Todo-link delivery; end that execution only after explore_result_delivery.ok=true. The writeback resolves and checks execution proof internally.",
  leaseRecovery: "If execution has ended or expired, re-enter the normal guard and todo claim path before retrying; a stale task reference cannot resume execution.",
  completedLease: "When execution fencing applies to a completed Todo, only its existing owner-scoped additive evidence-link exception is permitted. Writeback checks retained execution history internally; this grants no new execution authority.",
  deliveryReplay: "An exact replay of already-successful delivery is readback-only and needs no new claim; unfinished delivery still requires current ownership and execution checks.",
  optionalFinding: "Routine work needs no attachment; do not invent findings.",
  optionalCapture: "Capture is optional, not a settlement obligation.",
  negativeEvidence: "A stopped route does not imply refuted status, and a score alone does not prove refutation.",
  rawEvidence: "Keep raw logs local.",
  measurementConditions: "Attach measurements when an evaluation returns quantitative evidence: include its metric, unit, explicit direction, candidate and result reference. Use the same comparison_scope only for the same task, evaluator, model/resource budget and evaluation conditions; keep distinct workloads or evaluation conditions in their own profiles.",
  measurementProvenance: "input_revision identifies the evaluated task/data revision; evaluation_ref identifies the evaluator/scoring protocol version. Keep both stable across candidates only when those conditions are verified unchanged. Put candidate code/artifact revisions in candidate_ref and individual evaluation/submission receipts in evidence_ref, not in the comparison keys. The measurement input revision and evidence reference must match this finding. If comparability is unknown, keep results separate and explain the uncertainty; never invent a shared version to obtain a delta.",
  feedbackBeforeWork: "Use null for an unmeasured value. Read measurement_feedback before repeating a measured route: compare the latest recorded candidate with its same-scope best, investigate regressions and retain useful alternatives; a score does not itself close or authorize work.",
  inTurnFeedback: "For an open claimed Todo, a result can inform the next action in this Turn: record this same attachment with `loopx explore finding --result-json FILE` and its current Goal, Agent, Todo and Turn. The receipt returns scoped feedback without settling or spending.",
  executionEvidence: "When an experiment has execution evidence, attach execution_observation: preserve its exact candidate and input version, planned versus executed method, change reason and measured resources. Missing measurements are null, not zero. A finding source digest is not an executable digest. Result evidence must name the candidate it actually evaluated, including delayed or regressing results; it does not certify current workspace quality or Goal completion.",
};
export function exploreBeforeWorkGuidance(): string {
  return Object.values(EXPLORE_BEFORE_WORK).join(" ");
}
export function exploreInTurnFeedbackGuidance(): string {
  return EXPLORE_BEFORE_WORK.inTurnFeedback;
}
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
      question: "", applicability: "", input_revision: "", status: "",
    },
    linked_question_attachment_template: {
      schema_version: EXPLORE_PATH_DELTA_ATTACHMENT_SCHEMA,
      node_id: "<linked-question-id>", input_revision: "", status: "",
    },
    optional_question_evolution_example: {
      question_update: {status: "resolved"},
      relations: [{to_node: "<existing-node-id>", edge_type: "depends_on",
        summary: "<observed prerequisite; omit relations when none changed>"}],
    },
    optional_measurement_example: {measurements: [{metric: "accuracy", value: 0.82,
      unit: "ratio", higher_is_better: true, comparison_scope: "validation:fixed-budget-v1",
      input_revision: "dataset-v1", evaluation_ref: "evaluator:validation-v1",
      candidate_ref: "candidate:revision-2", evidence_ref: "result:validation-2"}]},
    optional_execution_observation_example: {execution_observation: {
      schema_version: EXPLORE_EXECUTION_FROM_FINDING_SCHEMA,
      candidate_sha256: "a".repeat(64), planned_method_id: "bounded-probe", executed_method_id: "bounded-probe",
      change_reason: null, elapsed_seconds: 0.125, limit_seconds: 10, search_iterations: 2,
      stop_reason: "iteration_limit", evidence_refs: FINDING_EVIDENCE_SELECTION,
    }},
    guidance: [
      "After validating reusable evidence, put explore_result in the vision JSON already submitted with --agent-vision-json.",
      EXPLORE_BEFORE_WORK.openLease,
      EXPLORE_BEFORE_WORK.leaseRecovery,
      EXPLORE_BEFORE_WORK.completedLease,
      EXPLORE_BEFORE_WORK.deliveryReplay,
      "Prefer path_delta_attachment_template when this same packet contains an evidence-linked path_delta: supply the question, applicability, tested input revision and explicit finding status.",
      "For a new question, omit node_id: the hook derives its identity from the exact question and applicability, creates it and links this Todo; no explore node call is needed.",
      "To add to a known question, reuse its returned node_id and canonical scope.",
      "At a meaningful experimental conclusion or route change, reuse that question and optionally add question_update and relations in this same result; no separate cleanup turn is needed.",
      "Use question_update.status=resolved only when the scoped question is answered (including a negative answer), dead_end only for an explicitly stopped route, or exploring/open for remaining work; blocked requires blocked_reason.",
      "Keep the answer, scope limits and next discriminating check in the existing interpretation or path_delta; finding status never determines question status.",
      "Relations point from this question to an existing same-Goal node and require an evidence-backed summary; temporal order alone establishes neither dependency nor benefit.",
      "Only the question's recorded owner may evolve it. Omission preserves the existing question state; preserve its exact title and applicability, and reopen explicitly only when new evidence warrants it.",
      "The hook reuses the complete observation and route decision.",
      "For an existing question linked to this Todo, use linked_question_attachment_template: omit both question and applicability to reuse its canonical scope.",
      "New or unlinked questions still require explicit scope; input_revision and status are never inferred.",
      "Choose status explicitly: tentative for unresolved interpretation, insufficient evidence or failed prerequisites; confirmed when evidence supports the stated conclusion within its applicability; refuted when evidence contradicts that conclusion within its applicability. Evidence references, a successful check, a score or a route decision alone do not choose the status.",
      "Separate established observations from unresolved broader claims in interpretation; state what remains unknown and the next discriminating check when useful. Revisit the linked question at a meaningful conclusion using question_update; do not keep it open solely because the overall Goal is unfinished, or close it merely because a finding is confirmed.",
      "If path_delta also references local files, explicitly select its opaque identifiers with optional evidence_refs; selected refs must occur in this same path_delta.",
      "Without a selection, all refs must be opaque identifiers.",
      "Otherwise fill attachment_template, inline or via --explore-result-json.",
      "Matching sources coalesce; conflicts reject.",
      EXPLORE_BEFORE_WORK.optionalFinding,
      EXPLORE_BEFORE_WORK.optionalCapture,
      EXPLORE_BEFORE_WORK.negativeEvidence,
      EXPLORE_BEFORE_WORK.rawEvidence,
      EXPLORE_BEFORE_WORK.measurementConditions,
      EXPLORE_BEFORE_WORK.measurementProvenance,
      "To reuse explicitly verified conditions, optional measurement_context supplies comparison_scope, input_revision and evaluation_ref for observations that omit those fields; conflicting fields reject. CLI finding accepts the same context via --measurement-context-json. This copies declared conditions, not proof of comparability.",
      EXPLORE_BEFORE_WORK.feedbackBeforeWork,
      EXPLORE_BEFORE_WORK.inTurnFeedback,
      EXPLORE_BEFORE_WORK.executionEvidence,
      `Use optional_execution_observation_example when the execution used this finding's tested input. Omit the nested input_revision and set its evidence_refs to "${FINDING_EVIDENCE_SELECTION}" only if all finding refs describe this execution, or list the exact opaque subset. Supply the actual candidate and execution facts. A released result still needs its result_ref and matching result_candidate_sha256.`,
      `Questions allow ${QUESTION_LIMIT} characters, applicability ${APPLICABILITY_LIMIT}, observations 320, and interpretations 1200. The complete scoped summary is preserved within 2000 characters.`,
    ].join(" "),
    // Blank evidence fields deliberately fail validation until the caller
    // supplies observed facts. Goal/Agent/Todo/Turn bind in ordinary writeback;
    // a source-code revision here would not establish the tested input revision.
    attachment_template: {
      schema_version: EXPLORE_RESULT_ATTACHMENT_SCHEMA,
      question: "", applicability: "", input_revision: "",
      observation: "", interpretation: "", status: "", evidence_refs: [],
    },
  };
}

/** Reusable evidence and instructions in this owner's read model. Current
 * selection, eligibility, source revisions and pagination stay outside this
 * split. Exact array edits retain every change, including its status,
 * provenance and applicability; no timestamp or revision is normalized away. */
export function exploreReusableContextPaths(content: JsonObject = {}): (string | number)[][] {
  return [
    ...["recent_nodes", "recent_findings", "writeback_results", "measurement_feedback", "work_measurements", "work_yardsticks", "work_yardstick_capture",
      "path_delta_attachment_template", "linked_question_attachment_template",
      "optional_question_evolution_example", "optional_execution_observation_example", "result_writeback_guidance",
      "result_attachment_template", "optional_measurement_example", "measurement_guidance",
      "record_node_template", "record_finding_template", "guidance",
      "result_writeback_option", "result_writeback_inline_option", "result_writeback_inline_field",
      "result_attachment_schema", "path_delta_attachment_schema"].map(key => ["graph", key]),
    ["harness", "guidance"], ["harness", "link_evidence_template"],
    // The branch's identity, selection, status and resume facts stay fresh.
    // Its evidence audit is a read model, not an eligibility or execution grant.
    ...["selected_branches", "comparison_candidates", "rejected_candidates", "frontier"].flatMap(lane => {
      const branches = (content.harness as JsonObject | undefined)?.[lane];
      return Array.isArray(branches) ? branches.flatMap((branch, index) =>
        branch !== null && typeof branch === "object" && !Array.isArray(branch)
          && typeof branch.todo_id === "string" && branch.todo_id
          ? [["harness", lane, index, "typed_evidence_audit"]] : []) : [];
    }),
  ];
}

/** The same authoring projection is used inline and by the captured writer. */
export function resultWritebackFields(writeback: JsonObject): JsonObject {
  return {
      result_writeback_option: writeback.option,
      result_writeback_inline_option: writeback.inline_option,
      result_writeback_inline_field: writeback.inline_field,
      result_attachment_schema: writeback.attachment_schema,
      path_delta_attachment_schema: writeback.path_delta_attachment_schema,
      path_delta_attachment_template: writeback.path_delta_attachment_template,
      linked_question_attachment_template: writeback.linked_question_attachment_template,
      optional_question_evolution_example: writeback.optional_question_evolution_example,
      optional_execution_observation_example: writeback.optional_execution_observation_example,
      result_writeback_guidance: writeback.guidance,
      result_attachment_template: writeback.attachment_template,
      optional_measurement_example: writeback.optional_measurement_example,
  };
}

/** Reference only exact, recognized authoring already delivered by the captured
 * settlement plan. Unknown/custom guidance stays inline: no prose classification
 * or migration of old obligations to a later phase. Evidence and paging stay put.
 */
function withoutCurrentResultWriteback(content: JsonObject, attachment: JsonObject): JsonObject | null {
  const rawGraph = content.graph;
  if (content.ok !== true || content.graph_enabled !== true || !rawGraph || typeof rawGraph !== "object" || Array.isArray(rawGraph) ||
      attachment.capability_id !== "explore" || attachment.guidance !== exploreResultWritebackAffordance().guidance) return null;
  const graph = requireJsonObject(rawGraph, "Explore graph");
  const fields = resultWritebackFields(attachment);
  if (Object.entries(fields).some(([key, value]) => value === undefined || !isDeepStrictEqual(graph[key], value))) return null;
  const retained = {...graph};
  for (const key of Object.keys(fields)) delete retained[key];
  return retained;
}

export function referenceExploreResultWriteback(content: JsonObject, attachment: JsonObject, read: string): JsonObject {
  const retained = withoutCurrentResultWriteback(content, attachment);
  return retained === null ? content : {...content, graph: {...retained,
    result_writeback_guidance: exploreBeforeWorkGuidance(), result_writeback_read: read}};
}

/** A pure replan has no admitted Todo for refresh-state's result attachment.
 * Preserve evidence collection obligations and the independent graph commands;
 * do not advertise unusable authoring or invent a later attachment authority.
 * The caller must establish the autonomous settlement binding first.
 */
export function omitUnboundExploreResultWriteback(content: JsonObject): JsonObject {
  const retained = withoutCurrentResultWriteback(content, exploreResultWritebackAffordance());
  return retained === null ? content : {...content, graph: {...retained,
    result_writeback_guidance: exploreBeforeWorkGuidance() +
      " The refresh-state Explore attachment requires an admitted Todo and is unavailable for this pure replan. " +
      "The independent graph record commands remain available for reusable evidence."}};
}

/** Only the final display shares the successful hook's identical input. The
 * complete attachment stays inline; durable intents and retry inputs never change. */
export function referenceExploreResultDelivery(source: JsonObject, numberTokens?: unknown): JsonObject {
  const attachment = jsonObject(source.explore_result), delivery = jsonObject(source.explore_result_delivery);
  const hook = jsonObject(delivery?.hook);
  if (attachment?.schema_version !== EXPLORE_RESULT_ATTACHMENT_SCHEMA ||
      Object.hasOwn(attachment, "attachment_ref") || delivery?.ok !== true ||
      delivery.retryable !== false || delivery.primary_committed !== true ||
      hook?.schema_version !== "loopx_post_writeback_capability_hook_dispatch_v0" ||
      hook.phase !== "post_writeback" || !Array.isArray(hook.failures) || hook.failures.length ||
      !Array.isArray(hook.intents)) return source;
  const identity = contextSourceIdentity(source, numberTokens);
  const original = identity(attachment, ["explore_result"]);
  const reference = {attachment_ref: "$.explore_result"};
  if (Buffer.byteLength(JSON.stringify({attachment})) <= Buffer.byteLength(JSON.stringify(reference))) return source;
  let changed = false;
  const intents = hook.intents.map((value, index) => {
    const intent = jsonObject(value), payload = jsonObject(intent?.payload);
    if (intent?.schema_version !== "loopx_capability_intent_v0" ||
        intent.intent_kind !== "explore.result_ingestion" || !payload ||
        Object.hasOwn(payload, "attachment_ref") || !jsonObject(payload.attachment) ||
        identity(payload.attachment, ["explore_result_delivery", "hook", "intents", index, "payload", "attachment"]) !== original) return value;
    const {attachment: _duplicate, ...other} = payload;
    changed = true;
    return {...intent, payload: {...other, ...reference}};
  });
  return changed ? {...source, explore_result_delivery: {...delivery, hook: {...hook, intents}}} : source;
}
