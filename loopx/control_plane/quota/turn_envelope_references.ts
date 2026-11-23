import { isDeepStrictEqual } from "node:util";

import { AGENT_SCOPE_FRONTIER_ACTIONS } from "../agents/agent_scope_frontier.generated.ts";
import { omitUnboundExploreResultWriteback, referenceExploreResultWriteback } from "../capabilities/explore_context_delivery.ts";
import { BARE_SHA256_PATTERN } from "../content_digest.ts";
import { assertSupportedQuotaPacket, SETTLEMENT_PLAN_SCHEMA_VERSION } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { jsonObject, requireJsonObject, requireStringLiteral, type JsonObject } from "../runtime_decode.ts";
import { assertInteractionRequiredReads, decodeInteractionContract, selectedWorkTodoId } from "../work_items/interaction_contract.ts";
import { REPLAN_SETTLEMENT_CONTRACT_SCHEMA } from "../work_items/replan_settlement.ts";
import {
  contextProjectionChanges, contextProjectionIdentity, contextProjectionRetainFields,
  contextSourceIdentity, resolveQuotaSummaryLanes, type ProjectionSourceValues,
} from "./diagnostic_projection.ts";
import { EFFECTIVE_ACTIONS, EffectiveAction } from "./effective_action.generated.ts";
import { scopedReplanYardstickReversal } from "./progressive_context.ts";
import { resolveSettlementWorkspaceRequirement } from "./settlement_workspace_causality.ts";
import {
  COMPLETE_TURN_DECISION_VERSION, COMPLETE_TURN_HASH_CODEC, canonicalHash, canonicalValue,
  compactFields, completeSourceHash, decisionSourceHash, integer, isNonEmptyFilePath,
  object, pickNonNull, pickOwn, scalarString, shellQuote, text, textList,
} from "./turn_envelope_codec.ts";

export const WORK_CONTEXT_GOAL_ACCEPTANCE_REF_PATTERN =
  /^\$\.interaction_contract\.agent_channel\.work_context\.sources\[(0|[1-9]\d*)\]\.content\.goal_acceptance_contract$/;

export type ReplanValueIdentity = (value: unknown, path: (string | number)[]) => string;
export type ReplanReadPrecision = {
  identity: ReplanValueIdentity;
  retain: ReturnType<typeof contextProjectionRetainFields>;
  equalProjection: (left: JsonObject, right: JsonObject) => boolean;
  values: ProjectionSourceValues;
};

export type WritebackResolver = (
  payload: JsonObject, precision: ReplanReadPrecision | undefined, verifiedSourceHash: string,
) => JsonObject;

export function replanReadPrecision(source: JsonObject, supplied: unknown): ReplanReadPrecision | undefined {
  if (supplied === undefined) return undefined;
  const identity = contextSourceIdentity(source, supplied);
  const values: ProjectionSourceValues = new WeakMap();
  const projectedIdentity = (view: JsonObject) => contextProjectionIdentity(source, view, supplied, values);
  return {identity, values, retain: contextProjectionRetainFields(source, values),
    equalProjection: (left, right) => projectedIdentity(left) === projectedIdentity(right)};
}

export function replanAuthoringContract(payload: JsonObject, precision?: ReplanReadPrecision): JsonObject {
  const contract = jsonObject(jsonObject(payload.replan_action_packet)?.writeback_contract) ?? {};
  if (!Object.hasOwn(contract, "vision_authoring_ref")) return contract;
  const reference = contract.vision_authoring_ref;
  const match = typeof reference === "string"
    ? /^\$\.interaction_contract\.cli_channel\.settlement_plan\.ordered_steps\[(0|[1-9]\d*)\]\.vision_authoring$/.exec(reference) : null;
  const steps = object(object(object(payload.interaction_contract).cli_channel).settlement_plan).ordered_steps;
  const step = object(match && Array.isArray(steps) ? steps[Number(match[1])] : null);
  if (Object.hasOwn(contract, "vision_authoring") || step.kind !== "durable_writeback" ||
      Object.hasOwn(step, "vision_authoring_ref") || !Object.keys(object(step.vision_authoring)).length) {
    throw new EffectRuntimeRequestError("Replan authoring reference must resolve to one complete inline durable-writeback owner");
  }
  const {vision_authoring_ref: _reference, ...fields} = contract;
  const resolved = {...fields, vision_authoring: step.vision_authoring};
  return precision?.retain(resolved, contract) ?? resolved;
}

export function referenceReplanAuthoring(payload: JsonObject,
    identity = contextSourceIdentity(payload)): JsonObject {
  const packet = object(payload.replan_action_packet);
  const contract = object(packet.writeback_contract);
  if (Object.hasOwn(contract, "vision_authoring_ref")) {
    replanAuthoringContract(payload);
    return payload;
  }
  const authoring = object(contract.vision_authoring);
  const steps = object(object(object(payload.interaction_contract).cli_channel).settlement_plan).ordered_steps;
  if (!Object.keys(authoring).length || !Array.isArray(steps)) return payload;
  const encoded = JSON.stringify(canonicalValue(authoring));
  const source = identity(authoring, ["replan_action_packet", "writeback_contract", "vision_authoring"]);
  const index = steps.findIndex((value, index) => {
    const step = object(value);
    return step.kind === "durable_writeback" && !Object.hasOwn(step, "vision_authoring_ref") &&
      jsonObject(step.vision_authoring) !== null &&
      identity(step.vision_authoring, ["interaction_contract", "cli_channel", "settlement_plan",
        "ordered_steps", index, "vision_authoring"]) === source;
  });
  if (index < 0) return payload;
  const reference = `$.interaction_contract.cli_channel.settlement_plan.ordered_steps[${index}].vision_authoring`;
  if (Buffer.byteLength(encoded, "utf8") <= Buffer.byteLength(reference, "utf8") + 8) return payload;
  delete contract.vision_authoring;
  return {...payload, replan_action_packet: {...packet, writeback_contract: {
    ...contract, vision_authoring_ref: reference}}};
}

export function resolveExploreGuidance(payload: JsonObject, context: JsonObject): JsonObject {
  if (!Array.isArray(context.sources)) return context;
  const steps = object(object(object(payload.interaction_contract).cli_channel).settlement_plan).ordered_steps;
  return {...context, sources: context.sources.map(value => {
    const source = object(value), content = object(source.content), graph = object(content.graph);
    if (source.kind !== "explore_turn_context" || !Object.hasOwn(graph, "result_writeback_guidance_ref")) return value;
    const reference = graph.result_writeback_guidance_ref;
    const match = typeof reference === "string"
      ? /^\$\.interaction_contract\.cli_channel\.settlement_plan\.ordered_steps\[(0|[1-9]\d*)\]\.optional_attachments\[(0|[1-9]\d*)\]\.guidance$/.exec(reference) : null;
    const step = object(match && Array.isArray(steps) ? steps[Number(match[1])] : null);
    const attachment = object(match && Array.isArray(step.optional_attachments)
      ? step.optional_attachments[Number(match[2])] : null);
    if (Object.hasOwn(graph, "result_writeback_guidance") || step.kind !== "durable_writeback" ||
        attachment.capability_id !== "explore" || Object.hasOwn(attachment, "guidance_ref") ||
        typeof attachment.guidance !== "string" || !attachment.guidance) {
      throw new EffectRuntimeRequestError("Explore guidance reference must resolve to one inline durable-writeback Explore attachment");
    }
    delete graph.result_writeback_guidance_ref;
    return {...source, content: {...content, graph: {...graph, result_writeback_guidance: attachment.guidance}}};
  })};
}

/** Existing acceptance carriers only: this is not a general JSON-path codec.
 * The loaded work context is preferred, then the complete planning/replan facts.
 */
function acceptanceCarriers(payload: JsonObject) {
  // Keep read-only carrier parents attached to their source. The general
  // object() helper intentionally returns copies for projection callers.
  const carrierObject = (value: unknown): JsonObject =>
    typeof value === "object" && value !== null && !Array.isArray(value)
      ? value as JsonObject : {};
  const carriers: {parent: JsonObject; field: string; path: string; parentPath: (string | number)[]}[] = [];
  const sources = carrierObject(carrierObject(carrierObject(payload.interaction_contract).agent_channel).work_context).sources;
  if (Array.isArray(sources)) {
    const index = sources.findIndex(value => {
      const source = carrierObject(value), content = carrierObject(source.content);
      return source.source === "goal_acceptance" &&
        (content.status === undefined || content.status === "loaded") &&
        Object.keys(carrierObject(content.goal_acceptance_contract)).length > 0;
    });
    if (index >= 0) carriers.push({parent: carrierObject(carrierObject(sources[index]).content),
      field: "goal_acceptance_contract",
      parentPath: ["interaction_contract", "agent_channel", "work_context", "sources", index, "content"],
      path: `$.interaction_contract.agent_channel.work_context.sources[${index}].content.goal_acceptance_contract`});
  }
  carriers.push(
    {parent: carrierObject(carrierObject(payload.planning_horizon).core_goal), field: "acceptance_contract",
      parentPath: ["planning_horizon", "core_goal"],
      path: "$.planning_horizon.core_goal.acceptance_contract"},
    {parent: carrierObject(carrierObject(carrierObject(payload.autonomous_replan_obligation).replan_context).core_goal),
      field: "acceptance_contract", path: "$.autonomous_replan_obligation.replan_context.core_goal.acceptance_contract",
      parentPath: ["autonomous_replan_obligation", "replan_context", "core_goal"]},
    {parent: carrierObject(payload.agent_todo_summary), field: "goal_acceptance_contract",
      parentPath: ["agent_todo_summary"],
      path: "$.agent_todo_summary.goal_acceptance_contract"},
  );
  return carriers;
}

function inlineAcceptance(payload: JsonObject, reference: unknown): JsonObject {
  // Older captures may name any loaded work-context carrier, not only the
  // preferred first source chosen for new sharing.
  const match = typeof reference === "string" ? WORK_CONTEXT_GOAL_ACCEPTANCE_REF_PATTERN.exec(reference) : null;
  if (match) {
    const sources = object(object(object(payload.interaction_contract).agent_channel).work_context).sources;
    const source = object(Array.isArray(sources) ? sources[Number(match[1])] : null);
    const content = object(source.content);
    if (source.source === "goal_acceptance" &&
        (content.status == null || content.status === "loaded") &&
        !Object.hasOwn(content, "goal_acceptance_contract_ref") &&
        Object.keys(object(content.goal_acceptance_contract)).length) {
      return object(content.goal_acceptance_contract);
    }
  }
  const carrier = acceptanceCarriers(payload).find(item => item.path === reference);
  const value = object(carrier?.parent[carrier.field]);
  if (!carrier || Object.hasOwn(carrier.parent, `${carrier.field}_ref`) || !Object.keys(value).length) {
    throw new EffectRuntimeRequestError("Goal acceptance reference must resolve to complete inline work context or planning facts");
  }
  return value;
}

export function referenceGoalAcceptance(payload: JsonObject,
    identity = contextSourceIdentity(payload)): JsonObject {
  let result = payload;
  // Copy only a changed registered carrier's spine. Retained source objects
  // keep their identity for the display-edit transport and original numbers.
  const writableParent = (path: (string | number)[]): JsonObject => {
    result = {...result};
    let target: Record<string | number, unknown> = result;
    for (const key of path) {
      const value = target[key];
      target[key] = Array.isArray(value) ? [...value] : {...requireJsonObject(value, "acceptance carrier")};
      target = target[key] as Record<string | number, unknown>;
    }
    return target;
  };
  const owners = new Map<string, string>();
  const relocated = new Map<string, string>();
  const carriers = acceptanceCarriers(payload);
  for (const {parent, field, path, parentPath} of carriers) {
    const value = object(parent[field]);
    if (!Object.keys(value).length || Object.hasOwn(parent, `${field}_ref`)) continue;
    // Complete canonical JSON distinguishes booleans/numbers, revisions,
    // provenance and negative evidence. Never deduplicate a prose summary.
    const encoded = identity(value, [...parentPath, field]);
    const owner = owners.get(encoded);
    if (!owner) owners.set(encoded, path);
    else if (Buffer.byteLength(JSON.stringify(canonicalValue(value)), "utf8") > Buffer.byteLength(owner, "utf8") + 8) {
      const target = writableParent(parentPath);
      delete target[field];
      target[`${field}_ref`] = owner;
      relocated.set(path, owner);
    }
  }
  // A historical source can already reference a carrier that was just shared.
  // Rebind that exact reference to the retained inline owner, never a chain.
  for (const {parent, field, parentPath} of carriers) {
    const reference = parent[`${field}_ref`];
    if (typeof reference === "string" && relocated.has(reference)) {
      writableParent(parentPath)[`${field}_ref`] = relocated.get(reference);
    }
  }
  return result;
}

/** One registry for ordinary captured reads and staged replan. Only registered
 * acceptance carriers participate; unrelated objects named `outcomes` are not
 * sources. Equal content shares a reference, while differing revisions of the
 * same outcome retain their complete bodies, including unknown fields.
 */
function indexCapturedAcceptanceOutcomes(payload: JsonObject, indexOutcome: (value: unknown) => string): void {
  const carriers = acceptanceCarriers(payload);
  // Sharing prefers the first source, but a diagnostic index must cover every
  // loaded registered carrier, including different same-id revisions.
  const sources = object(object(object(payload.interaction_contract).agent_channel).work_context).sources;
  if (Array.isArray(sources)) for (const [index, value] of sources.entries()) {
    const source = object(value), content = object(source.content);
    if (source.source === "goal_acceptance" && (content.status == null || content.status === "loaded")) {
      carriers.push({parent: content, field: "goal_acceptance_contract", path: "",
        parentPath: ["interaction_contract", "agent_channel", "work_context", "sources", index, "content"]});
    }
  }
  for (const {parent, field} of carriers) {
    const acceptance = Object.hasOwn(parent, `${field}_ref`)
      ? inlineAcceptance(payload, parent[`${field}_ref`]) : object(parent[field]);
    if (!Object.keys(acceptance).length) continue;
    const requirements = object(acceptance.outcome_requirements);
    if (!Object.hasOwn(acceptance, "outcome_requirements")) continue;
    if (!Array.isArray(requirements.outcomes)) throw new EffectRuntimeRequestError("captured outcome source is unsupported");
    for (const value of requirements.outcomes) indexOutcome(value);
  }
}

/** Expose existing facts, not another acceptance verdict. Missing values stay
 * absent; an Agent's `met` review is not a current independent check.
 */
function capturedOutcomeIndex(outcomes: ReadonlyMap<string, JsonObject>, precision?: ReplanReadPrecision): JsonObject[] {
  return [...outcomes].map(([ref, outcome]) => {
    const states = new Map<string, JsonObject>();
    if (Array.isArray(outcome.requirements)) for (const value of outcome.requirements) {
      const requirement = requireJsonObject(value, "captured outcome requirement");
      const review = jsonObject(requirement.agent_review);
      const state: JsonObject = {
        ...pickOwn(requirement, ["status", "planning_satisfied"]),
        ...(Object.hasOwn(requirement, "agent_review") ? {agent_review: review === null
          ? requirement.agent_review : pickOwn(review, ["conclusion", "basis_current", "independently_verified"])} : {}),
      };
      const key = JSON.stringify(canonicalValue(state));
      const group = states.get(key);
      if (group) (group.requirement_ids as unknown[]).push(requirement.id);
      else states.set(key, {requirement_ids: [requirement.id], ...state});
    }
    const row: JsonObject = {outcome_ref: ref, id: outcome.id, objective: outcome.objective, revision: outcome.revision,
      ...pickOwn(outcome, ["owner_agent_id", "work", ...(Object.hasOwn(outcome, "work") ? [] : ["todo_ids"])]),
      requirement_count: Array.isArray(outcome.requirements) ? outcome.requirements.length : null,
      requirement_states: Array.isArray(outcome.requirements) ? [...states.values()] : null};
    return precision?.retain(row, outcome, ["id", "objective", "revision", "owner_agent_id"]) ?? row;
  });
}

/** Reuse the existing settlement authoring owner, never a summary or a chain.
 * Same-capture references have one typed settlement owner. Historical readers
 * expand before moving work context, so a full-packet path never dangles in a
 * staged envelope. Explore's independent read and domain guidance stay intact. */
export function referenceExploreGuidance(payload: JsonObject): JsonObject {
  const interaction = object(payload.interaction_contract), agent = object(interaction.agent_channel);
  const context = object(agent.work_context);
  resolveExploreGuidance(payload, context); // Validate existing references, including idempotent reads.
  if (!Array.isArray(context.sources)) return payload;
  const steps = object(object(interaction.cli_channel).settlement_plan).ordered_steps;
  if (!Array.isArray(steps)) return payload;
  const owners = new Map<string, string>();
  steps.forEach((value, stepIndex) => {
    const step = object(value);
    if (step.kind !== "durable_writeback" || !Array.isArray(step.optional_attachments)) return;
    step.optional_attachments.forEach((value, attachmentIndex) => {
      const attachment = object(value), guidance = attachment.guidance;
      if (attachment.capability_id !== "explore" || Object.hasOwn(attachment, "guidance_ref") ||
          typeof guidance !== "string" || !guidance || owners.has(guidance)) return;
      owners.set(guidance, `$.interaction_contract.cli_channel.settlement_plan.ordered_steps[${stepIndex}].optional_attachments[${attachmentIndex}].guidance`);
    });
  });
  const sources = context.sources.map(value => {
    const source = object(value), content = object(source.content), graph = object(content.graph);
    const guidance = graph.result_writeback_guidance;
    const reference = typeof guidance === "string" ? owners.get(guidance) : undefined;
    if (source.kind !== "explore_turn_context" || typeof guidance !== "string" || !reference ||
        Buffer.byteLength(JSON.stringify(guidance), "utf8") <= Buffer.byteLength(reference, "utf8") + 8) return value;
    delete graph.result_writeback_guidance;
    return {...source, content: {...content, graph: {...graph, result_writeback_guidance_ref: reference}}};
  });
  return {...payload, interaction_contract: {...interaction, agent_channel: {...agent,
    work_context: {...context, sources}}}};
}

const VISION_AUDIT_PATH = "$.vision_continuation_audit";
const CLI_VISION_AUDIT_REFERENCE = {
  projection_ref: VISION_AUDIT_PATH, projection_view: "cli_channel", required: true,
};

/** The writer checks the entire view against the root fields before sharing
 * them. The capture reader uses the existing interaction owner to present it. */
function cliVisionAuditView(owner: JsonObject): JsonObject | undefined {
  if (owner.schema_version !== "vision_continuation_audit_v0" || owner.required !== true) return undefined;
  const view = pickOwn(owner, ["required", "required_before_closeout", "recommended_action"]);
  const judge = object(owner.vision_gap_judge);
  if (Object.keys(judge).length) view.vision_gap_judge = pickOwn(judge, [
    "done", "decision", "reason", "agent_judge_instruction", "evidence_read_instruction",
    "registry_read_instruction", "done_only_when", "continue_when", "otherwise",
  ]);
  return view;
}

/** Exact copies use the complete root as their projection. */
function visionAuditReference(audit: JsonObject): JsonObject {
  const reference: JsonObject = {
    projection_ref: VISION_AUDIT_PATH,
    ...pickOwn(audit, ["schema_version", "required", "decision"]),
  };
  if (typeof audit.vision_gap_judge === "object" && audit.vision_gap_judge !== null &&
      !Array.isArray(audit.vision_gap_judge)) {
    reference.vision_gap_judge = {
      projection_ref: "$.vision_continuation_audit.vision_gap_judge",
      ...pickOwn(object(audit.vision_gap_judge), ["done", "decision"]),
    };
  }
  return reference;
}

function mapNestedVisionAudits(payload: JsonObject,
    visit: (audit: JsonObject, path: (string | number)[]) => JsonObject): JsonObject {
  const map = (value: unknown, path: (string | number)[]) => {
    const parent = object(value), audit = parent.vision_continuation_audit;
    if (typeof audit !== "object" || audit === null || Array.isArray(audit)) return value;
    return {...parent, vision_continuation_audit: visit(audit as JsonObject, [...path, "vision_continuation_audit"])};
  };
  const rawInteraction = payload.interaction_contract, interaction = object(rawInteraction);
  const hasInteraction = typeof rawInteraction === "object" && rawInteraction !== null && !Array.isArray(rawInteraction);
  return {...payload,
    ...(Object.hasOwn(payload, "goal_frontier_projection") ? {
      goal_frontier_projection: map(payload.goal_frontier_projection, ["goal_frontier_projection"])} : {}),
    ...(hasInteraction ? {interaction_contract: {...interaction,
      ...Object.fromEntries(["agent_channel", "cli_channel"].filter(key => Object.hasOwn(interaction, key))
        .map(key => [key, map(interaction[key], ["interaction_contract", key])]))}} : {})};
}

export function requireVisionAuditReferences(payload: JsonObject): void {
  const owner = object(payload.vision_continuation_audit);
  const hasReference = (audit: JsonObject) => Object.hasOwn(audit, "projection_ref") ||
    Object.hasOwn(audit, "projection_view") || Object.hasOwn(object(audit.vision_gap_judge), "projection_ref");
  if (hasReference(owner)) throw new EffectRuntimeRequestError("Vision audit reference requires a complete inline root owner");
  mapNestedVisionAudits(payload, (audit, path) => {
    if (hasReference(audit)) {
      const expected = audit.projection_view === "cli_channel" && path[1] === "cli_channel" && cliVisionAuditView(owner)
        ? CLI_VISION_AUDIT_REFERENCE : visionAuditReference(owner);
      if (!Object.keys(owner).length ||
          JSON.stringify(canonicalValue(audit)) !== JSON.stringify(canonicalValue(expected))) {
        throw new EffectRuntimeRequestError("Vision audit reference must match its complete inline root owner without conflicting facts");
      }
    }
    return audit;
  });
}

export function referenceVisionContinuation(payload: JsonObject,
    identity = contextSourceIdentity(payload), shareCliView = false): JsonObject {
  requireVisionAuditReferences(payload);
  const owner = object(payload.vision_continuation_audit);
  if (!Object.keys(owner).length) return payload;
  const encoded = JSON.stringify(canonicalValue(owner)), reference = visionAuditReference(owner);
  const smallerThanRoot = Buffer.byteLength(JSON.stringify(reference)) < Buffer.byteLength(encoded);
  const cliView = shareCliView ? cliVisionAuditView(owner) : undefined;
  // A short audit is cheaper inline.
  if (!smallerThanRoot && !cliView) return payload;
  const source = identity(owner, ["vision_continuation_audit"]);
  const cliIdentity = cliView && identity(cliView, ["vision_continuation_audit"]);
  const cliReferenceBytes = Buffer.byteLength(JSON.stringify(CLI_VISION_AUDIT_REFERENCE));
  return mapNestedVisionAudits(payload, (audit, path) => {
    const candidate = identity(audit, path);
    if (smallerThanRoot && candidate === source) return structuredClone(reference);
    if (path[1] === "cli_channel" && candidate === cliIdentity &&
        cliReferenceBytes < Buffer.byteLength(JSON.stringify(audit))) {
      return {...CLI_VISION_AUDIT_REFERENCE};
    }
    return audit;
  });
}

/** The verified current Task is the only owner of a shared lane title. Keep
 * the original declaration inline and require exact identity, never a label or
 * prefix comparison. The legacy display and complete writer use this owner. */
function currentTaskTitleOwner(payload: JsonObject): {index: number; todo: JsonObject} | null {
  const lane = object(payload.agent_lane_next_action), selected = object(payload.selected_todo);
  const context = object(object(object(payload.interaction_contract).agent_channel).work_context);
  if (context.complete !== true || context.selected_todo_text_verified !== true ||
      typeof lane.todo_id !== "string" || !lane.todo_id || selected.todo_id !== lane.todo_id ||
      !Array.isArray(context.sources)) return null;
  const matches = context.sources.map((value, index) => ({source: object(value), index}))
    .filter(({source}) => source.source === "selected_todo");
  if (matches.length !== 1) return null;
  const {source, index} = matches[0], content = object(source.content), todo = object(content.todo);
  if (content.ok !== true || content.matched !== true || content.ambiguous === true ||
      content.read_complete === false || typeof todo.text !== "string" ||
      todo.archive_state === "archive" || todo.status === "done" || todo.todo_id !== lane.todo_id) return null;
  return {index, todo};
}

export function requireCurrentTaskTitleReference(payload: JsonObject): void {
  const lane = object(payload.agent_lane_next_action);
  if (!Object.hasOwn(lane, "title_ref")) return;
  const reference = lane.title_ref;
  const match = typeof reference === "string"
    ? /^\$\.interaction_contract\.agent_channel\.work_context\.sources\[(0|[1-9]\d*)\]\.content\.todo\.(text|title)$/.exec(reference) : null;
  const owner = currentTaskTitleOwner(payload);
  if (Object.hasOwn(lane, "title") || !match || !owner || owner.index !== Number(match[1]) ||
      typeof owner.todo[match[2]] !== "string" || !owner.todo[match[2]] ||
      Object.hasOwn(owner.todo, `${match[2]}_ref`)) {
    throw new EffectRuntimeRequestError("Current Task title reference must resolve to the complete verified selected Task");
  }
}

export function referenceCurrentTaskTitle(payload: JsonObject): JsonObject {
  const lane = object(payload.agent_lane_next_action), title = lane.title;
  // Preserve historical caller-owned references; complete readers validate them
  // separately. Display-only omission of a title already in a label stays local.
  if (typeof title !== "string" || Object.hasOwn(lane, "title_ref")) return payload;
  const owner = currentTaskTitleOwner(payload);
  if (!owner) return payload;
  for (const field of ["text", "title"]) {
    if (owner.todo[field] !== title || Object.hasOwn(owner.todo, `${field}_ref`)) continue;
    const reference = `$.interaction_contract.agent_channel.work_context.sources[${owner.index}].content.todo.${field}`;
    if (Array.from(JSON.stringify(title)).length <= reference.length + 8) return payload;
    delete lane.title;
    return {...payload, agent_lane_next_action: {...lane, title_ref: reference}};
  }
  return payload;
}

export function selectedTodo(payload: JsonObject, recommendedAction: string | null): JsonObject | null {
  const source = object(payload.selected_todo);
  if (Object.keys(source).length === 0) return null;
  const compact = pickNonNull(source, [
    "todo_id", "priority", "status", "task_class", "action_kind",
    "task_repository", "continuation_policy", "claimed_by", "bound_agent",
    "goal_bound", "blocks_agent", "unblocks_todo_id", "next_due_at",
    "expires_at", "selected_by", "confidence",
    "resume_when", "resume_ready", "resume_condition",
  ]);
  const context = object(object(object(payload.interaction_contract).agent_channel).work_context);
  if (context.selected_todo_text_verified === true) compact.text_source_verified = true;
  // Work declarations can end in acceptance/stop conditions. Keep their exact
  // text; a display prefix or case-folded recommendation cannot stand in for it.
  const rendered = scalarString(source.text, "selected_todo.text");
  if (rendered && rendered === recommendedAction) compact.text_ref = "action.recommended_action";
  else if (rendered) compact.text = rendered;
  return Object.keys(compact).length > 0 ? compact : null;
}

/** These reads stay on one captured observation; they never re-enter the guard. */
export function replanReadCommands(payload: JsonObject, path = payload.replan_read_path,
  argv: unknown = payload.replan_read_argv ?? ["loopx"], verifiedSourceHash?: string,
  captureSha256?: unknown): JsonObject | null {
  const packet = object(payload.replan_action_packet);
  if (!isNonEmptyFilePath(path) || payload.ok !== true ||
      payload.effective_action === EffectiveAction.GOVERNED_CAPABILITY_INTENT ||
      object(object(payload.interaction_contract).cli_channel).turn_binding_required === true ||
      !packet.obligation_id || packet.settlement_only === true) return null;
  // Retain the renderer's executable across reads. A different ambient LoopX
  // may not understand this captured contract. Legacy captures keep their route.
  if (!Array.isArray(argv) || !argv.length || argv.some(arg => typeof arg !== "string" || !arg || /[\x00-\x1f]/.test(arg))) {
    throw new EffectRuntimeRequestError("captured replan CLI route is malformed");
  }
  if (captureSha256 !== undefined &&
      (typeof captureSha256 !== "string" || !BARE_SHA256_PATTERN.test(captureSha256))) {
    throw new EffectRuntimeRequestError("captured replan byte digest is malformed");
  }
  const command = `${argv.map(arg => shellQuote(arg as string)).join(" ")} --format json quota replan-view --decision-file ${shellQuote(path)}` +
    ` --source-hash ${verifiedSourceHash ?? decisionSourceHash(payload)}` +
    (captureSha256 === undefined ? "" : ` --capture-sha256 ${captureSha256}`);
  return {review: `${command} --replan-stage review`, writeback: `${command} --replan-stage writeback`,
    acceptance: `${command} --replan-stage acceptance`, explore: `${command} --replan-stage explore`};
}

/** One entry lens for CLI and Turn. The executor still receives the signed
 * identity; authoring and executable settlement commands have one later read.
 */
export function deferredReplanWriteback(writeback: JsonObject): JsonObject {
  const {settlement_plan: plan, next_cli_actions: actions, ...facts} = writeback;
  if (!Object.keys(object(plan)).length && !(Array.isArray(actions) && actions.length)) return writeback;
  const identity = object(object(plan).identity);
  return {...facts, settlement_read_ref: "$.replan_action_packet.writeback",
    ...(Object.keys(identity).length ? {settlement_identity: identity} : {})};
}

/** Preserve the captured Goal and work horizon for review and the signed
 * executor envelope, indexing cumulative outcome details by exact content.
 */
export function replanGoalContext(payload: JsonObject, reads: JsonObject | null, coreRef = "$.core_goal",
    precision?: ReplanReadPrecision) {
  const context = object(object(payload.autonomous_replan_obligation).replan_context);
  const retain = precision?.retain ?? ((view: JsonObject) => view);
  function resolvedGoal(value: unknown): JsonObject {
    const original = jsonObject(value) ?? {};
    const core = retain({...original}, original);
    if (!("acceptance_contract" in core) && "acceptance_contract_ref" in core) {
      const acceptance = inlineAcceptance(payload, core.acceptance_contract_ref);
      delete core.acceptance_contract_ref;
      core.acceptance_contract = acceptance;
    }
    return core;
  }
  function capturedOutcomes(core: JsonObject): JsonObject[] {
    const requirements = object(object(core.acceptance_contract).outcome_requirements);
    return reads && Array.isArray(requirements.outcomes)
      ? requirements.outcomes.map(value => requireJsonObject(value, "captured acceptance outcome")) : [];
  }
  const resolvedCore = resolvedGoal(context.core_goal);
  const originalHorizon = jsonObject(payload.planning_horizon) ?? {};
  const horizon = retain({...originalHorizon}, originalHorizon);
  const resolvedHorizon = horizon.core_goal ? resolvedGoal(horizon.core_goal) : null;
  const coreOutcomes = capturedOutcomes(resolvedCore);
  const horizonOutcomes = resolvedHorizon ? capturedOutcomes(resolvedHorizon) : [];
  const refs = new Map<JsonObject, string>();
  const legacyGroups = new Map<string, JsonObject[]>();
  const captured = [...coreOutcomes, ...horizonOutcomes];
  const complete = payload.schema_version === COMPLETE_TURN_DECISION_VERSION;
  if (reads && complete) indexCapturedAcceptanceOutcomes(payload, value => {
    captured.push(requireJsonObject(value, "captured acceptance outcome"));
    return "";
  });
  for (const outcome of captured) {
    if (refs.has(outcome)) continue;
    const legacy = canonicalHash(outcome);
    refs.set(outcome, legacy);
    if (precision) {
      const group = legacyGroups.get(legacy);
      if (group) group.push(outcome);
      else legacyGroups.set(legacy, [outcome]);
    }
  }
  // Keep historical references where they identify exactly one outcome.
  // When number decoding collapsed different outcomes, index each original
  // body and reject the old ambiguous reference on the acceptance stage.
  const ambiguous = new Set<string>();
  if (precision) for (const [legacy, group] of legacyGroups) {
    if (group.length < 2) continue;
    const exact = group.map(outcome => precision.identity(outcome, []));
    if (new Set(exact).size < 2) continue;
    ambiguous.add(legacy);
    group.forEach((outcome, index) => refs.set(outcome, canonicalHash([legacy, exact[index]])));
  }
  const outcomes = new Map<string, JsonObject>();
  for (const [outcome, ref] of refs) outcomes.set(ref, outcome);
  function indexedGoal(core: JsonObject, captured: JsonObject[]): JsonObject {
    if (!captured.length) return core;
    const originalAcceptance = jsonObject(core.acceptance_contract) ?? {};
    const acceptance = retain({...originalAcceptance}, originalAcceptance);
    const requirements = jsonObject(acceptance.outcome_requirements) ?? {};
    const {outcomes: _outcomes, ...metadata} = requirements;
    delete acceptance.outcome_requirements;
    return retain({...core, acceptance_contract: acceptance,
      outcome_requirements_index: retain({...metadata, outcome_refs: captured.map(outcome => refs.get(outcome)!)},
        requirements)}, core);
  }
  const core = indexedGoal(resolvedCore, coreOutcomes);
  if (resolvedHorizon) {
    const horizonCore = indexedGoal(resolvedHorizon, horizonOutcomes);
    if (canonicalHash(horizonCore) === canonicalHash(core) &&
        (precision?.equalProjection(horizonCore, core) ?? true)) {
      delete horizon.core_goal;
      horizon.core_goal_ref = coreRef;
    } else horizon.core_goal = horizonCore;
  }
  const acceptanceIndex = outcomes.size ? {
    read_action_template: `${reads!.acceptance} --outcome-ref {outcome_ref}`,
    outcomes: complete ? capturedOutcomeIndex(outcomes, precision) : [...outcomes].map(([ref, outcome]) => retain({outcome_ref: ref, id: outcome.id,
      objective: outcome.objective, revision: outcome.revision,
      requirement_count: Array.isArray(outcome.requirements) ? outcome.requirements.length : null},
      outcome, ["id", "objective", "revision"])),
  } : null;
  return {core, horizon, outcomes, acceptanceIndex, ambiguous};
}

/** Applicability of Explore's existing attachment owner, shared by complete
 * composition and the historical staged projection. No authority is granted. */
export function unboundExploreReplan(payload: JsonObject, plan: JsonObject): boolean {
  const steps = Array.isArray(plan.ordered_steps) ? plan.ordered_steps.map(object) : [];
  const attachments = object(steps.find(step => step.kind === "durable_writeback")).optional_attachments;
  const explore = Array.isArray(attachments) ? attachments.map(object).filter(row => row.capability_id === "explore") : [];
  const identity = object(plan.identity);
  return payload.effective_action === EffectiveAction.AUTONOMOUS_REPLAN_REQUIRED &&
    selectedWorkTodoId(payload) === null && identity.binding_kind === "autonomous_replan" &&
    identity.replan_obligation_id === object(payload.replan_action_packet).obligation_id &&
    !identity.todo_id && explore.length === 0;
}

/** Move only an identical acceptance copy behind the existing captured read.
 * Different revisions, other source fields and unavailable context stay intact.
 */
export function replanWorkContext(payload: JsonObject, context: JsonObject, reads = replanReadCommands(payload),
    identity: ReplanValueIdentity = canonicalHash): JsonObject {
  context = resolveExploreGuidance(payload, context);
  const corePath = ["autonomous_replan_obligation", "replan_context", "core_goal"];
  const core = object(object(object(payload.autonomous_replan_obligation).replan_context).core_goal);
  const acceptance = object(core.acceptance_contract);
  if (!reads || !Array.isArray(context.sources)) return context;
  const plan = object(object(object(payload.interaction_contract).cli_channel).settlement_plan);
  const steps = Array.isArray(plan.ordered_steps) ? plan.ordered_steps.map(object) : [];
  const attachments = object(steps.find(step => step.kind === "durable_writeback")).optional_attachments;
  const explore = Array.isArray(attachments) ? attachments.map(object).filter(row => row.capability_id === "explore") : [];
  const unboundReplan = unboundExploreReplan(payload, plan);
  return {...context, sources: context.sources.map((value, index) => {
    const source = object(value), content = object(source.content);
    if (source.source === "turn_start_capability_hook" && source.hook_id === "explore.turn_context" &&
        source.capability_id === "explore" && explore.length === 1 && typeof reads.writeback === "string") {
      return {...source, content: referenceExploreResultWriteback(content, explore[0], reads.writeback)};
    }
    if (unboundReplan && source.source === "turn_start_capability_hook" &&
        source.hook_id === "explore.turn_context" && source.capability_id === "explore") {
      return {...source, content: omitUnboundExploreResultWriteback(content)};
    }
    if (!Object.keys(acceptance).length || source.source !== "goal_acceptance" || content.status != null && content.status !== "loaded" ||
        identity(content.goal_acceptance_contract,
          ["interaction_contract", "agent_channel", "work_context", "sources", index, "content", "goal_acceptance_contract"])
        !== identity(acceptance, [...corePath, "acceptance_contract"])) return value;
    const {goal_acceptance_contract: _contract, ...metadata} = content;
    return {...source, content: {...metadata, context_read: reads.review}};
  })};
}

/** Bind successor authoring to the already-decided Turn. This formatting
 * owner is shared by complete composition and historical staged reads; it does
 * not select work, create a successor or change the settlement plan.
 */
export function bindReplanSuccessorContract(value: JsonObject, plan: JsonObject): JsonObject {
  const contract = {...value};
  const identity = object(plan.identity);
  if (typeof identity.turn_instance_id !== "string") return contract;
  const goalRef = object(plan.goal_ref);
  contract.successor_binding_args = `--turn-instance-id ${shellQuote(identity.turn_instance_id)}` +
    (typeof goalRef.goal_instance_id === "string" ? ` --goal-instance-id ${shellQuote(goalRef.goal_instance_id)}` : "");
  contract.successor_settlement = "If creating a replan successor, include successor_binding_args with its obligation id, then follow the creation receipt's original Turn settlement plan before ending; do not run the successor.";
  delete contract.successor_host_action;
  if (typeof contract.successor_command === "string") {
    contract.successor_command = `${contract.successor_command} ${contract.successor_binding_args}`;
  }
  return contract;
}

export function requireCompleteDecisionSource(source: JsonObject): void {
  assertSupportedQuotaPacket(source);
  // Validate executable semantics with their owner, retaining the original
  // object (including optional facts) rather than its normalized decode.
  const interaction = decodeInteractionContract(source.interaction_contract);
  assertInteractionRequiredReads(interaction.agent_channel.required_reads);
  replanAuthoringContract(source);
  requireVisionAuditReferences(source);
  requireCurrentTaskTitleReference(source);
  resolveQuotaSummaryLanes(source);
  resolveExploreGuidance(source, object(object(object(source.interaction_contract).agent_channel).work_context));
  // This root field selects execution, unlike optional evidence carried intact
  // in the source. Reuse both registered owners; do not inherit the historical
  // adapter's unknown-action fallback at the new version boundary.
  requireStringLiteral(source.effective_action,
    [...EFFECTIVE_ACTIONS, ...AGENT_SCOPE_FRONTIER_ACTIONS], "Turn effective_action");
  for (const {parent, field} of acceptanceCarriers(source)) {
    if (!Object.hasOwn(parent, field) && Object.hasOwn(parent, `${field}_ref`)) {
      inlineAcceptance(source, parent[`${field}_ref`]);
    }
  }
}

export function requireCompleteTurnDecision(value: unknown, numberTokens?: unknown): JsonObject {
  const decision = requireJsonObject(value, "complete Turn decision");
  if (decision.schema_version !== COMPLETE_TURN_DECISION_VERSION) {
    throw new EffectRuntimeRequestError("unsupported complete Turn decision version");
  }
  const binding = requireJsonObject(decision.contract_binding, "Turn contract binding");
  if (binding.hash_codec !== COMPLETE_TURN_HASH_CODEC) {
    throw new EffectRuntimeRequestError("unsupported complete Turn decision hash codec");
  }
  const identity = contextSourceIdentity(decision, numberTokens ?? []);
  if (typeof binding.scheduler_execution_args !== "string") {
    throw new EffectRuntimeRequestError("Turn scheduler execution binding must be a string");
  }
  const unsignedBinding = {...binding};
  delete unsignedBinding.content_hash;
  const content = {...decision, contract_binding: unsignedBinding};
  if (binding.content_hash !== completeSourceHash(content, identity)) {
    throw new EffectRuntimeRequestError("complete Turn decision content integrity mismatch");
  }
  const source = {...decision};
  delete source.schema_version;
  delete source.contract_binding;
  if (binding.source_decision_hash !== completeSourceHash(source, identity)) {
    throw new EffectRuntimeRequestError("complete Turn decision source integrity mismatch");
  }
  requireCompleteDecisionSource(source);
  return decision;
}

export function replanView(request: JsonObject, resolveWriteback: WritebackResolver): JsonObject {
  const payload = requireJsonObject(request.payload, "captured replan decision");
  const sourceHash = decisionSourceHash(payload);
  if (request.source_hash !== sourceHash || payload.ok !== true) {
    throw new EffectRuntimeRequestError("Replan read requires the original successful decision and matching source hash");
  }
  // Validate the complete current source before the historical display lens.
  if (Object.hasOwn(payload, "schema_version") || Object.hasOwn(payload, "contract_binding")) {
    requireCompleteTurnDecision(payload, request.source_number_tokens);
  } else requireVisionAuditReferences(payload);
  const precision = replanReadPrecision(payload, request.source_number_tokens);
  const view = projectReplanView(request, payload, sourceHash, resolveWriteback, precision);
  return precision ? contextProjectionChanges(payload, view, precision.values, {}) : view;
}

function projectReplanView(
  request: JsonObject, payload: JsonObject, sourceHash: string,
  resolveWriteback: WritebackResolver, precision?: ReplanReadPrecision,
): JsonObject {
  const retain = precision?.retain ?? ((view: JsonObject) => view);
  requireVisionAuditReferences(payload);
  const originalContract = replanAuthoringContract(payload, precision);
  const packet = jsonObject(payload.replan_action_packet) ?? {};
  const obligation = jsonObject(payload.autonomous_replan_obligation) ?? {};
  const context = jsonObject(obligation.replan_context) ?? {};
  if (!packet.obligation_id || packet.settlement_only === true || packet.obligation_id !== context.obligation_id) {
    throw new EffectRuntimeRequestError("Replan read requires a matching decision context");
  }
  const common = {ok: true, goal_id: payload.goal_id, agent_id: payload.agent_id ?? object(payload.agent_identity).agent_id,
    obligation_id: packet.obligation_id, context_id: context.context_id, source_hash: request.source_hash,
    stage: request.stage};
  // A native journal binds its path after planning. The CLI supplies that same
  // physical source route without modifying the captured observation or hash.
  const path = request.captured_decision_path ?? payload.replan_read_path;
  const reads = replanReadCommands(payload, path, payload.replan_read_argv ?? request.reader_argv ?? ["loopx"],
    sourceHash, request.capture_sha256);
  // The stateless entry already exposes its digest-checked complete context.
  // Its writer should not offer a second, unchecked whole-file route.
  const sourceRead = reads && request.capture_sha256 === undefined
    ? {source_read: `cat -- ${shellQuote(path as string)}`} : {};
  if (request.stage === "acceptance") {
    const {outcomes, ambiguous} = replanGoalContext(payload, reads, undefined, precision);
    const ref = String(request.outcome_ref);
    if (ambiguous.has(ref)) throw new EffectRuntimeRequestError(
      "The old captured outcome reference is ambiguous. Read the review stage for the distinct references.");
    const outcome = outcomes.get(ref);
    if (!outcome) throw new EffectRuntimeRequestError("acceptance read requires an exact captured outcome reference");
    return {...common, outcome_ref: request.outcome_ref, outcome};
  }
  if (request.stage === "explore")
    return retain({...common, explore_basis: context.explore_basis ?? null}, context, ["explore_basis"]);
  if (request.stage === "writeback") {
    // Preserve executable shape and all settlement rules; decision advice and
    // duplicate context are available in the unchanged full captured source.
    const originalStride = jsonObject(packet.stride_control) ?? {};
    const stride = retain({...originalStride}, originalStride);
    // The current nested review syntax belongs to authoring, not duplicate
    // decision evidence. Top-level required_fields cannot describe that shape.
    for (const key of ["decision_followthrough", "explore_basis", "decision_value_review"]) delete stride[key];
    const contract = {...originalContract};
    if (contract.vision_authoring) {
      const originalAuthoring = jsonObject(contract.vision_authoring) ?? {};
      const authoring = retain({...originalAuthoring}, originalAuthoring);
      delete authoring.minimal_example;
      contract.vision_authoring = authoring;
    }
    const writeback = resolveWriteback(payload, precision, sourceHash);
    const plan = object(writeback.settlement_plan);
    const boundContract = payload.schema_version === COMPLETE_TURN_DECISION_VERSION
      ? contract : bindReplanSuccessorContract(contract, plan);
    if (typeof boundContract.successor_command === "string" && typeof object(plan.identity).turn_instance_id === "string") {
      // Creation returns the original settlement plan directly. A guard
      // reentry would repeat admission/context and can select new work.
      writeback.next_cli_actions = [boundContract.successor_command];
    }
    const steps = Array.isArray(plan.ordered_steps) ? plan.ordered_steps.map(object) : [];
    const refresh = steps.find(step => step.kind === "durable_writeback");
    const spend = steps.find(step => step.kind === "quota_spend");
    const receipt = object(payload.heartbeat_receipt), identity = object(plan.identity);
    const cli = object(object(payload.interaction_contract).cli_channel);
    const settlement = object(cli.replan_settlement_contract), binding = object(settlement.settlement_binding);
    const semantic = object(settlement.semantic_obligation), actions = cli.next_cli_actions;
    // Use the captured semantic writer, which already carries the owner's
    // authoring arguments. The generic plan template omits those arguments.
    // Leave the signed plan and separate recovery actions untouched.
    const directWriter = payload.schema_version === COMPLETE_TURN_DECISION_VERSION
      && payload.should_run === true && payload.effective_action === EffectiveAction.AUTONOMOUS_REPLAN_REQUIRED
      && plan.schema_version === SETTLEMENT_PLAN_SCHEMA_VERSION && identity.binding_kind === "autonomous_replan"
      && typeof identity.turn_instance_id === "string" && receipt.turn_instance_id === identity.turn_instance_id
      && identity.replan_obligation_id === packet.obligation_id
      && ["committed", "upgraded", "replayed"].includes(String(receipt.status))
      && receipt.settlement_binding_owed !== true && isDeepStrictEqual(receipt.settlement_identity, identity)
      && settlement.schema_version === REPLAN_SETTLEMENT_CONTRACT_SCHEMA && settlement.single_binding_required === true
      && binding.kind === "autonomous_replan" && binding.id === packet.obligation_id
      && binding.cli_argument === "--replan-obligation-id" && semantic.kind === "autonomous_replan"
      && semantic.settlement_bound === true && semantic.id === packet.obligation_id
      && semantic.discharge === "direct_settlement" && cli.spend_after_validation === true
      && cli.spend_allowed_now === false && cli.selection_required !== true
      && typeof boundContract.successor_command !== "string"
      && refresh?.owner === "agent" && spend?.owner === "agent" && typeof spend.command_template === "string"
      && Array.isArray(actions) && actions.length === 2 && actions[1] === spend.command_template
      && typeof actions[0] === "string" && actions[0].length > 0
      && isDeepStrictEqual(writeback.next_cli_actions, [actions[0]])
      ? actions[0] : undefined;
    const settlementCommand = directWriter !== undefined
      ? directWriter + " --settle-turn --settlement-receipt"
      : typeof refresh?.command_template === "string" ? refresh.command_template + " --settle-turn" : undefined;
    const needsDeliveryWorkspace = directWriter !== undefined && resolveSettlementWorkspaceRequirement(
      jsonObject(receipt.delivery_workspace_causality), "autonomous_replan",
    ).requirement !== "not_required";
    return retain({...common, ...sourceRead, writeback_contract: retain(boundContract, originalContract),
      required_outcome: packet.required_outcome, allowed_terminal: packet.allowed_terminal,
      ...(packet.stride_control ? {stride_control: stride} : {}),
      writeback,
      ...(settlementCommand === undefined ? {} : {
        settlement_command: settlementCommand,
        settlement_usage: "After validation, fill this refresh command from the actual result. " +
          (needsDeliveryWorkspace ? "Run from the workspace that produced the delivery or pass its path with --delivery-workspace-path. " +
            "When peer isolation applies, use the independent Git worktree that produced the delivery. " : "") +
          "It follows only the verified owed quota step on this same binding. " +
          "Do not also execute a quota step after settled readback; retain the original plan for completion, blocked/no-spend closeout and failure recovery.",
      })}, packet, ["required_outcome", "allowed_terminal"]);
  }
  if (request.stage !== "review") throw new EffectRuntimeRequestError("unsupported replan read stage");
  const {core, horizon, acceptanceIndex} = replanGoalContext(payload, reads, undefined, precision);
  const cli = object(object(payload.interaction_contract).cli_channel);
  // Index every captured outcome, including differing context/horizon revisions.
  // Full-content equality alone shares a row; identical IDs do not erase edits.
  const coverage = Array.isArray(context.coverage_ledger) ? context.coverage_ledger.map(value =>
    jsonObject(value) ?? {}) : [];
  const originalFollow = jsonObject(context.decision_followthrough) ?? {};
  const follow = {...originalFollow};
  delete follow.guidance;
  const followFields = Object.keys(follow);
  if (Object.hasOwn(follow, "recorded_progress_count")) {
    follow.observation_count = follow.recorded_progress_count;
    delete follow.recorded_progress_count;
    retain(follow, originalFollow, followFields.filter(field => field !== "observation_count"));
    retain(follow, originalFollow, [["recorded_progress_count", "observation_count"]]);
  } else retain(follow, originalFollow, followFields);
  const explore = jsonObject(context.explore_basis) ?? {};
  const yardstickCount = Array.isArray(explore.work_yardsticks) ? explore.work_yardsticks.length : 0;
  const reversal = reads ? scopedReplanYardstickReversal(payload) : undefined;
  const decisionValueReview = jsonObject(jsonObject(packet.stride_control)?.decision_value_review);
  const review = retain({...common,
    core_goal: core,
    planning_guidance: packet.planning_guidance ?? [],
    // The staged writer only carries authoring rules. Deliver the captured
    // decision advice here so it can inform the choice before writeback.
    ...(decisionValueReview ? {decision_value_review: decisionValueReview} : {}),
    ...(acceptanceIndex ? {acceptance_index: acceptanceIndex} : {}),
    triggers: obligation.triggers,
    decision_followthrough: Object.keys(follow).length ? follow : null,
    planning_horizon: payload.planning_horizon == null ? null : horizon,
    action_portfolio: payload.action_portfolio ?? null,
    ...(context.explore_basis ? {capabilities: {explore: retain({
      availability: explore.availability, revision: explore.revision,
      ...(Array.isArray(explore.measurement_feedback) && explore.measurement_feedback.length
        ? {measurement_count: explore.measurement_feedback.length} : {}),
      ...(yardstickCount ? {yardstick_count: yardstickCount} : {}),
      ...(reversal ? {work_yardstick_reversal: retain(compactFields(reversal.cue, reversal.fields), reversal.cue)} : {}),
      read_action: reads?.explore ?? null,
    }, explore, ["availability", "revision"])}} : {}),
    evidence: (Array.isArray(context.evidence) ? context.evidence : []).map(value => {
      const row = jsonObject(value) ?? {};
      const observation = coverage.find(item => item.fingerprint === row.coverage_ref);
      const item = retain({...compactFields(row, ["evidence_ref", "generated_at", "delivery_outcome", "coverage_ref",
        "occurrences", "first_observed_at", "read_action"]),
        ...(observation ? {result_class: observation.result_class} : {})}, row);
      return observation ? retain(item, observation, ["result_class"]) : item;
    }),
    evidence_count: context.evidence_count,
    coverage_count: context.coverage_count,
    coverage_truncated: context.coverage_truncated,
    omitted_evidence: context.omitted_evidence ?? null,
    evidence_read_action_template: context.evidence_read_action_template ?? null,
    uncovered_frontier: context.uncovered_frontier,
    // Facts and their provenance are the review input. Do not preselect a
    // diagnostic method, candidate route or judgment from observation counts.
    // A suggested Todo can already be present before the guard binds it.
    // Follow the entry's command: its host may already have bound the current
    // presentation options after this immutable decision was captured.
    next: cli.selection_required === true
      ? {selection_required: true, selection_command_ref: "$.interaction_contract.cli_channel.selection_command"}
      : {writeback: reads?.writeback ?? null},
  }, context, ["evidence_count", "coverage_count", "coverage_truncated", "omitted_evidence",
    "evidence_read_action_template", "uncovered_frontier"]);
  retain(review, obligation, ["triggers"]);
  return retain(review, packet, ["planning_guidance"]);
}

export function replanActionPacket(payload: JsonObject, reads = replanReadCommands(payload)): JsonObject | null {
  const source = object(payload.replan_action_packet);
  if (Object.keys(source).length === 0) return null;
  const writeback = replanAuthoringContract(payload);
  if (reads) return {...compactFields(source, ["schema_version", "decision", "obligation_id", "required_outcome", "allowed_terminal"]),
    review: reads.review, writeback: reads.writeback,
    guidance: "Read review state and applicable acceptance/evidence; choose the next work, then follow review.next for Todo selection or writeback. Reads grant no authority and require no acknowledgement."};
  const compact = compactFields(source, [
    "schema_version", "decision", "obligation_id", "uncovered_frontier",
    "required_outcome", "allowed_terminal", "bounded_frontier", "settlement_only", "successor_todo_id", "stride_control",
  ]);
  // The typed replan owner supplies bounded instructions; do not truncate their
  // authority/stop qualifiers through generic diagnostic compaction.
  if (Array.isArray(source.planning_guidance)) compact.planning_guidance = source.planning_guidance;
  if (writeback.vision_authoring) compact.writeback_contract = writeback;
  return Object.keys(compact).length > 0 ? compact : null;
}

export function userChannel(interaction: JsonObject, payload: JsonObject): JsonObject {
  const source = object(interaction.user_channel);
  const channel: JsonObject = {
    action_required: Boolean(source.action_required ?? payload.action_required),
    open_count: integer(payload.open_count, "payload.open_count"),
    notify: scalarString(source.notify, "interaction.user_channel.notify", "DONT_NOTIFY"),
  };
  const actions = textList(source.actions, 3, 360);
  if (actions.length > 0) channel.actions = actions;
  const reason = text(source.reason, 360);
  if (reason) channel.reason = reason;
  return channel;
}

export function responsePlan(interaction: JsonObject): JsonObject | null {
  const source = object(interaction.response_plan);
  if (Object.keys(source).length === 0) return null;
  const plan: JsonObject = {};
  for (const field of ["schema_version", "kind", "decision"]) {
    const rendered = text(source[field], 80);
    if (rendered) plan[field] = rendered;
  }
  const actionSequence = textList(source.action_sequence, 8, 80);
  if (actionSequence.length > 0) plan.action_sequence = actionSequence;
  if (typeof source.silent_wait_allowed === "boolean") plan.silent_wait_allowed = source.silent_wait_allowed;
  return Object.keys(plan).length > 0 ? plan : null;
}

export function requiredReads(interaction: JsonObject, payload: JsonObject): JsonObject[] {
  const raw = object(interaction.agent_channel).required_reads
    ?? interaction.required_reads ?? payload.required_reads;
  const result: JsonObject[] = [];
  for (const value of Array.isArray(raw) ? raw : []) {
    const item = object(value);
    // Required reads are executable obligations, not display summaries. Keep
    // every admitted command byte-for-byte, including quoted path whitespace.
    const command = scalarString(item.command, "required read command");
    if (!command) continue;
    const compact: JsonObject = { command };
    // These are existing obligation coordinates, not provider diagnostics.
    // Keep identity and ordering intact, just like the executable command.
    for (const field of ["kind", "reason", "source", "ordering", "hook_id", "capability_id"]) {
      const value = scalarString(item[field], `required read ${field}`);
      if (value) compact[field] = value;
    }
    if (item.source === "turn_start_capability_hook" && item.prompt_budget_bytes !== undefined) {
      compact.prompt_budget_bytes = item.prompt_budget_bytes;
    }
    result.push(compact);
  }
  return result;
}

export function replanCliView(payload: JsonObject, identity: ReplanValueIdentity = canonicalHash,
    reads = replanReadCommands(payload), precision?: ReplanReadPrecision): JsonObject {
  if (!reads) return payload;
  const obligation = {...object(payload.autonomous_replan_obligation)};
  delete obligation.replan_context;
  const interaction = object(payload.interaction_contract), agent = object(interaction.agent_channel);
  const cli = object(interaction.cli_channel);
  // Defer only authoring/settlement format, not admission or stop facts.
  // This default captured path needs no optional native delta transport.
  const goal = replanGoalContext(payload, reads, undefined, precision);
  const summary = object(payload.agent_todo_summary);
  const originalCore = object(object(object(payload.autonomous_replan_obligation).replan_context).core_goal);
  // Reuse the same captured acceptance index for an exact summary copy.
  // Otherwise this legacy carrier silently restores the full deferred detail.
  // A different revision or an existing summary index remains untouched.
  const indexedSummary = goal.core.outcome_requirements_index &&
    !Object.hasOwn(summary, "outcome_requirements_index") &&
    identity(summary.goal_acceptance_contract ?? null, ["agent_todo_summary", "goal_acceptance_contract"])
      === identity(originalCore.acceptance_contract ?? null,
        ["autonomous_replan_obligation", "replan_context", "core_goal", "acceptance_contract"])
    ? {...summary, goal_acceptance_contract: goal.core.acceptance_contract,
      outcome_requirements_index: goal.core.outcome_requirements_index} : summary;
  // Review owns the captured planning facts. The entry retains admission,
  // current selection and stops; the mandatory reader supplies the complete
  // Goal and horizon before the Agent chooses its next work.
  const {core_goal: _core, planning_horizon: _horizon, ...entry} = payload;
  return {...entry,
    ...(indexedSummary !== summary ? {agent_todo_summary: indexedSummary} : {}),
    interaction_contract: {...interaction, cli_channel: deferredReplanWriteback(cli), agent_channel: {...agent,
      work_context: replanWorkContext(payload, object(agent.work_context), reads, identity)}},
    autonomous_replan_obligation: {...obligation, context_read: reads.review},
    replan_action_packet: replanActionPacket(payload, reads)};
}

export function nativeReplanPresentation(request: JsonObject): JsonObject | undefined {
  const projection = object(request.native_projection);
  const captured = object(projection.captured_decision);
  const stateless = captured.path === undefined && projection.replan_reader_argv !== undefined;
  const path = stateless ? projection.source_path : captured.path;
  if (typeof path !== "string") return undefined;
  const source = requireJsonObject(request.context, "captured replan source");
  if (stateless) {
    if (projection.sha256 === undefined || request.surface !== "complete_turn" &&
        (request.surface !== "quota_packet" || Object.hasOwn(source, "schema_version") ||
          Object.hasOwn(source, "contract_binding"))) return undefined;
  }
  // Preserve a bound reversal in the opening, before candidate choice.
  if (scopedReplanYardstickReversal(source)) return undefined;
  const packet = object(source.replan_action_packet);
  const review = object(object(source.autonomous_replan_obligation).replan_context);
  // Existing staged files are presentation inputs. Do not derive a new source
  // hash from them or replace their already pinned reader commands.
  if ((!stateless && typeof source.replan_read_path !== "string") || packet.schema_version !== "replan_action_packet_v0"
      || typeof packet.obligation_id !== "string"
      || packet.obligation_id !== review.obligation_id || packet.review !== undefined
      || packet.writeback !== undefined) return undefined;
  const precision = replanReadPrecision(request, request.source_number_tokens);
  const identity = precision?.identity ?? contextSourceIdentity(request);
  const view = replanCliView(source, (value, field) => identity(value, ["context", ...field]),
    replanReadCommands(source, path, stateless ? projection.replan_reader_argv : undefined,
      undefined, stateless ? projection.sha256 : captured.sha256), precision);
  return view === source ? undefined : {staged_context: view, deferred: [], instructions: []};
}
