import {EffectiveAction} from "./effective_action.generated.ts";
/** Model presentation: requirements stay inline, supporting material is
 * addressed in the immutable source. This plan changes no canonical state. */
import {isDeepStrictEqual} from "node:util";
import {visionAuthoringContract} from "../goals/vision_checkpoint.ts";
import {completeGoalNarrative} from "../work_items/goal_context_source.ts";
import type {JsonObject} from "../effect_program.ts";
import {jsonObject, requireBoolean, requireJsonObject} from "../runtime_decode.ts";
import {GOAL_OUTCOMES_SCHEMA, OUTCOME_CONTEXT_NAVIGATION_SCHEMA} from "../goals/outcome_requirements.ts";
import {FULL_OUTCOME_READ_GUIDANCE, PROGRESSIVE_OUTCOME_READ_GUIDANCE}
  from "../work_items/interaction_contract.ts";
import {projectReplanSemantics} from "../work_items/replan_semantics.ts";
import {quotaSummaryReferences, contextSourceIdentity, contextProjectionChanges,
  type ProjectionSourceValues} from "./diagnostic_projection.ts";
import {encodeContextDelivery, fileNavigationPaths} from "./context_delivery.ts";
import {capturedContextSelection} from "./context_selection.ts";
import {createHash} from "node:crypto";



type Path = (string | number)[];
type ReadSelection = {pointer: string; path: Path};
type ReusableReadField = {path: Path; freshKeys: string[]; minimumBytes: number; sourceIdentity?: string | string[]};
export type ReusableExactBody = {path: Path; sourceIdentity: string};
const MIN_REUSABLE_BODY_BYTES = 1024;
const SETTLEMENT_PLAN_PATH = ["interaction_contract", "cli_channel", "settlement_plan"];
const pointer = (path: Path): string => path.length ? "/" + path.map(key =>
  String(key).replaceAll("~", "~0").replaceAll("/", "~1")).join("/") : "";
const at = (root: unknown, path: Path): unknown => path.reduce<unknown>((row, key) =>
  Array.isArray(row) ? row[Number(key)] : jsonObject(row)?.[key], root);
const known = (row: JsonObject, fields: string[]): boolean => Object.keys(row).every(key => fields.includes(key));

function orderedSettlementSteps(plan: JsonObject | null | undefined): unknown[] | undefined {
  return (plan?.schema_version === "quota_settlement_plan_v1" || plan?.schema_version === "quota_settlement_plan_v2")
    && Array.isArray(plan.ordered_steps) ? plan.ordered_steps : undefined;
}

function readySettlementIdentity(context: JsonObject, interaction: JsonObject,
    plan: JsonObject | null | undefined): JsonObject | undefined {
  const cli = jsonObject(interaction.cli_channel), prepared = jsonObject(cli?.execution_preparation);
  const identity = jsonObject(plan?.identity);
  return context.effective_action === EffectiveAction.NORMAL_RUN && context.normal_delivery_allowed === true
    && jsonObject(interaction.agent_channel)?.delivery_allowed === true && cli?.selection_required !== true
    && prepared?.state === "ready" && typeof identity?.effect_id === "string" && identity.effect_id.length > 0
    && typeof identity.todo_id === "string" && prepared.todo_id === identity.todo_id
    && jsonObject(context.selected_todo)?.todo_id === identity.todo_id
    && isDeepStrictEqual(identity, jsonObject(jsonObject(context.heartbeat_receipt)?.settlement_identity))
    ? identity : undefined;
}

function reusableWritebackPrecondition(context: JsonObject): ReusableReadField | undefined {
  const interaction = jsonObject(context.interaction_contract);
  if (interaction?.schema_version !== "loopx_interaction_contract_v0") return undefined;
  const plan = jsonObject(at(context, SETTLEMENT_PLAN_PATH)), steps = orderedSettlementSteps(plan);
  const identity = readySettlementIdentity(context, interaction, plan);
  const identityAgent = jsonObject(context.agent_identity)?.agent_id;
  const agent = context.agent_id ?? identityAgent;
  if (!plan || !steps || !identity || typeof agent !== "string" || agent !== identity.agent_id
      || (identityAgent !== undefined && identityAgent !== agent)
      || typeof context.goal_id !== "string" || context.goal_id !== identity.goal_id
      || typeof context.registry !== "string" || !context.registry
      || typeof context.runtime_root !== "string" || !context.runtime_root) return undefined;
  const writebacks = steps.map((value, index) => ({step: jsonObject(value), index}))
    .filter(({step}) => step?.kind === "durable_writeback");
  if (writebacks.length !== 1) return undefined;
  const {step, index} = writebacks[0]!;
  if (step?.owner !== "agent" || typeof step.precondition !== "string") return undefined;
  return {path: [...SETTLEMENT_PLAN_PATH, "ordered_steps", index, "precondition"],
    freshKeys: [], minimumBytes: MIN_REUSABLE_BODY_BYTES,
    sourceIdentity: JSON.stringify([context.registry, context.runtime_root, context.goal_ref ?? null,
      plan.schema_version, identity.goal_id, agent, identity.todo_id, step.kind, step.owner])};
}

/** One registry for bodies eligible for observed reuse, regardless of whether
 * the reader delivered the body itself or packaged it in a complete parent. */
function reusableFirstReadFields(context: JsonObject, exactBody?: ReusableExactBody): ReusableReadField[] {
  const exactFields = exactBody && at(context, exactBody.path) !== undefined
    ? [{...exactBody, freshKeys: [], minimumBytes: MIN_REUSABLE_BODY_BYTES}] : [];
  const writeback = reusableWritebackPrecondition(context);
  const fields = [...exactFields, ...(writeback ? [writeback] : [])];
  const workPath: Path = ["interaction_contract", "agent_channel", "work_context"];
  const work = jsonObject(at(context, workPath));
  const base = [...workPath, "sources"];
  const sources = work?.sources;
  if (!Array.isArray(sources)) return fields;
  const selectedId = jsonObject(context.selected_todo)?.todo_id;
  const selectedSources = sources.filter(value => jsonObject(value)?.source === "selected_todo").length;
  return [...fields, ...sources.flatMap<ReusableReadField>((value, i) => {
    const source = jsonObject(value), content = jsonObject(source?.content);
    if (!content) return [];
    const path = [...base, i, "content"];
    // The current source and full-file revision still reach the first read.
    // Only the literal narrative may carry forward across generated Todo edits.
    if (source?.source === "goal_state" && content.ok !== false &&
        (content.status === undefined || content.status === "loaded") &&
        typeof content.state_file === "string" && completeGoalNarrative(content))
      return [{path: [...path, "text"], freshKeys: [], minimumBytes: MIN_REUSABLE_BODY_BYTES,
        sourceIdentity: content.state_file}];
    if (source?.source === "selected_todo") {
      const todo = jsonObject(content.todo), authority = jsonObject(content.authority_read);
      const route = [context.goal_id, context.registry, context.runtime_root];
      if (selectedSources !== 1 || work?.selected_todo_text_verified !== true ||
          typeof selectedId !== "string" || !selectedId ||
          typeof todo?.note !== "string" || authority?.decision_read_from_provider !== true ||
          authority.legacy_fallback_used !== false || typeof authority.source_authority !== "string" ||
          !authority.source_authority || !route.every((part): part is string => typeof part === "string" && !!part)) return [];
      const full = content.ok === true && content.matched === true && content.ambiguous === false &&
        content.read_complete !== false && content.source === authority.source_authority && todo.todo_id === selectedId;
      // The interaction owner can elide the identical Todo id and title from
      // its checked source. In that shape the explicit selected reference owns the id.
      const compact = work.selected_todo_ref === "selected_todo" &&
        known(content, ["todo", "authority_read"]) && (todo.todo_id === undefined || todo.todo_id === selectedId);
      if (!full && !compact) return [];
      return [{path: [...path, "todo", "note"], freshKeys: [], minimumBytes: MIN_REUSABLE_BODY_BYTES,
        sourceIdentity: [...route, authority.source_authority, selectedId]}];
    }
    if (source?.kind === "agent_preferences")
      return [{path, freshKeys: ["ok", "revision", "instruction"], minimumBytes: MIN_REUSABLE_BODY_BYTES}];
    if (source?.source !== "goal_acceptance") return [];
    const outcomesPath = [...path, "goal_acceptance_contract", "outcome_requirements", "outcomes"];
    const outcomes = at(context, outcomesPath);
    return Array.isArray(outcomes) ? outcomes.flatMap((value, index) => {
      const outcome = jsonObject(value), path = [...outcomesPath, index];
      if (typeof outcome?.id !== "string") return [];
      const fields = [{path, freshKeys: ["id", "revision"], minimumBytes: MIN_REUSABLE_BODY_BYTES}];
      // An appended criterion, revised review or supporting-material projection
      // changes the outcome container, not every unchanged requirement body.
      // Keep all other fields current, including unknown controls and order.
      if (Array.isArray(outcome.requirements)) {
        fields.push({path: [...path, "requirements"], freshKeys: [], minimumBytes: MIN_REUSABLE_BODY_BYTES});
        outcome.requirements.forEach((value, i) => {
          const requirement = jsonObject(value), body = [...path, "requirements", i, "description"];
          if (typeof requirement?.id === "string" && typeof requirement.description === "string") {
            const record = body.slice(0, -1);
            // Prefer an exact complete criterion before splitting its metadata.
            // Tiny bodies do not justify a digest and long-address journal row.
            // Changed status, review or unknown controls invalidate the record;
            // a large unchanged description can still be considered alone.
            fields.push({path: record, freshKeys: ["id"], minimumBytes: MIN_REUSABLE_BODY_BYTES});
            fields.push({path: body, freshKeys: [], minimumBytes: MIN_REUSABLE_BODY_BYTES});
          }
        });
      }
      return fields;
    }) : [];
  })];
}

function knownVisionAuthoring(step: JsonObject): boolean {
  return step.kind === "durable_writeback" &&
    [false, true].some(replan => isDeepStrictEqual(step.vision_authoring, visionAuthoringContract(replan)));
}

/** Only the stable writing instructions can carry forward. The ready decision,
 * current precondition, command and settlement identity must still be delivered. */
function reusableVisionAuthoring(context: JsonObject): ReusableReadField | undefined {
  const path: Path = ["interaction_contract", "cli_channel", "settlement_plan"];
  const plan = jsonObject(at(context, path)), identity = jsonObject(plan?.identity);
  const receipt = jsonObject(context.heartbeat_receipt);
  // Explicit Todo selection can finish binding the original receipt, and a
  // retry can read it back. Both still require its exact settlement identity.
  if (context.effective_action !== EffectiveAction.NORMAL_RUN || context.normal_delivery_allowed !== true ||
      at(context, ["interaction_contract", "user_channel", "action_required"]) !== false ||
      receipt?.closeout_required !== true || receipt.settlement_binding_owed === true ||
      (receipt.status !== "committed" && receipt.status !== "upgraded" && receipt.status !== "replayed") ||
      !identity || typeof identity.effect_id !== "string" || !identity.effect_id ||
      typeof identity.todo_id !== "string" || identity.todo_id !== jsonObject(context.selected_todo)?.todo_id ||
      identity.goal_id !== context.goal_id || typeof identity.agent_id !== "string" ||
      typeof identity.turn_instance_id !== "string" || identity.turn_instance_id !== receipt.turn_instance_id ||
      typeof context.registry !== "string" || !context.registry ||
      typeof context.runtime_root !== "string" || !context.runtime_root ||
      !isDeepStrictEqual(identity, receipt.settlement_identity) ||
      (plan?.schema_version !== "quota_settlement_plan_v1" && plan?.schema_version !== "quota_settlement_plan_v2") ||
      !Array.isArray(plan.ordered_steps)) return undefined;
  const steps = plan.ordered_steps.map(jsonObject);
  const index = steps.findIndex(step => step?.kind === "durable_writeback");
  const step = steps[index];
  if (!step || step.owner !== "agent" || !isDeepStrictEqual(step.vision_authoring, visionAuthoringContract(false)) ||
      steps.slice(index + 1).some(other => other?.kind === "durable_writeback") ||
      typeof step.semantic_closeout_command_template !== "string" || !step.semantic_closeout_command_template) return undefined;
  return {path: [...path, "ordered_steps", index, "vision_authoring"],
    freshKeys: [], minimumBytes: MIN_REUSABLE_BODY_BYTES,
    sourceIdentity: JSON.stringify([context.registry, context.runtime_root, context.goal_ref ?? null,
      plan.schema_version, identity.goal_id, identity.agent_id, identity.todo_id, step.kind, step.owner])};
}

function observedSectionDigest(body: string, field?: ReusableReadField): string {
  // A repeated body at the same position can belong to a different source.
  const value = field?.sourceIdentity === undefined ? body : JSON.stringify([field.sourceIdentity, body]);
  return createHash("sha256").update(value).digest("hex");
}

/** Digests stay in the private observed-read journal, never repeated to the model.
 * The existing exact-source identity preserves non-JS numeric spellings. */
export function contextReadSectionDigests(request: JsonObject, paths: Path[], exactBody?: ReusableExactBody): JsonObject {
  const context = requireJsonObject(request.context, "read section source");
  const identity = contextSourceIdentity(request, request.source_number_tokens);
  const selected = new Set(paths.map(pointer));
  const fields = new Map(reusableFirstReadFields(context, exactBody).map(field => [pointer(field.path), field]));
  const authoring = reusableVisionAuthoring(context);
  if (authoring) fields.set(pointer(authoring.path), authoring);
  const covered = new Map(paths.map(path => [pointer(path), {path, minimumBytes: MIN_REUSABLE_BODY_BYTES}]));
  for (const [address, field] of fields)
    if (paths.some(parent => pathStartsWith(field.path, parent))) covered.set(address, field);
  const inherited = jsonObject(request.consumed_pointers), coverage = new Set(paths.map(pointer));
  // The IO owner authenticates the reader's native observation. Recheck each
  // inherited value against this exact source before combining it with the
  // current literal selections. This is section coverage, never a full-input
  // baseline. Without it, a partial read strands the next Turn on small fields
  // even after every part of the revised outcome has actually been delivered.
  if (inherited && Object.keys(inherited).length) {
    for (const [address, expected] of Object.entries(inherited)) {
      const read = capturedContextSelection({context, select: [address], recover_selection_errors: true});
      if (read.rejected_selection) continue;
      const path = (read.selections as {path: Path}[])[0].path;
      if (observedSectionDigest(identity(at(context, path), ["context", ...path]), fields.get(address)) === expected)
        coverage.add(address);
    }
    const complete = (path: Path): boolean => {
      if (path.map((_, i) => pointer(path.slice(0, i + 1))).concat([""]).some(parent => coverage.has(parent))) return true;
      const value = at(context, path);
      return value !== null && typeof value === "object" && Object.keys(value).length > 0
        && Object.keys(value).every(key => complete([...path, Array.isArray(value) ? Number(key) : key]));
    };
    for (const [address, field] of fields)
      if (complete(field.path)) covered.set(address, field);
  }
  return Object.fromEntries([...covered].flatMap(([address, {path, minimumBytes}]) => {
    const content = identity(at(context, path), ["context", ...path]);
    // Parent delivery must not multiply tiny bodies into a larger journal.
    return selected.has(address) || Buffer.byteLength(content) >= minimumBytes
      ? [[address, observedSectionDigest(content, fields.get(address))]] : [];
  }));
}

/** Only registered, previously delivered bodies may use a reference. Current
 * decisions, stops, selection, settlement and unknown source shapes stay inline. */
function observedFirstRead(request: JsonObject, pointers: string[], exactBody?: ReusableExactBody): JsonObject | undefined {
  const context = requireJsonObject(request.context, "current first read");
  const seen = requireJsonObject(request.consumed_pointers, "observed sections");
  if (typeof jsonObject(context.selected_todo)?.todo_id !== "string") return undefined;
  const fields = new Map(reusableFirstReadFields(context, exactBody).map(field => [pointer(field.path), field]));
  const parents = new Set([...fields.values()].flatMap(({path}) => path.map((_, i) => pointer(path.slice(0, i)))));
  const identity = contextSourceIdentity(request, request.source_number_tokens);
  const reused: JsonObject = {};
  const authoring = reusableVisionAuthoring(context);
  const authoringAddress = authoring && pointer(authoring.path);
  const retainedAuthoring: JsonObject = {};
  if (authoring && authoringAddress && typeof seen[authoringAddress] === "string" &&
      pointers.includes(pointer([...authoring.path, "rule"]))) {
    const body = identity(at(context, authoring.path), ["context", ...authoring.path]);
    if (Buffer.byteLength(body) >= authoring.minimumBytes &&
        observedSectionDigest(body, authoring) === seen[authoringAddress])
      retainedAuthoring[authoringAddress] = seen[authoringAddress];
  }
  const authoringOnly = () => Object.keys(retainedAuthoring).length === 0 ? undefined :
    request.include_initial_read_plan === true
      ? {selection_splice: {start: 0, end: 0, selections: []}, reused: retainedAuthoring}
      : {selections: capturedContextSelection({context, initial_read: true}, pointers).selections,
        reused: retainedAuthoring};
  // Match semantic fields before the reader groups complete parents. Otherwise
  // selecting every outcome hides the individual unchanged bodies from reuse.
  const selections = capturedContextSelection({context, select: pointers,
    preserve_selection_order: true}).selections as ReadSelection[];
  const visit = (row: {pointer: string; path: Path}): typeof selections => {
    const {path, pointer: address} = row;
    const field = fields.get(address);
    const body = at(context, path);
    if (typeof seen[address] === "string" && field) {
      const content = identity(body, ["context", ...path]);
      if (Buffer.byteLength(content) >= field.minimumBytes && observedSectionDigest(content, field) === seen[address]) {
        reused[address] = seen[address];
        return field.freshKeys.filter(key => body !== null && typeof body === "object" &&
          Object.hasOwn(body, key)).map(key => ({pointer: pointer([...path, key]), path: [...path, key]}));
      }
    }
    if (!parents.has(address) || body === null || typeof body !== "object") return [row];
    const before = Object.keys(reused).length;
    const children = Object.keys(body).flatMap(key => {
      const child = [...path, Array.isArray(body) ? Number(key) : key];
      return visit({pointer: pointer(child), path: child});
    });
    return Object.keys(reused).length > before ? children : [row];
  };
  const current = selections.flatMap(visit);
  if (!Object.keys(reused).length) return authoringOnly();
  const grouped = capturedContextSelection({context, initial_read: true}, current.map(row => row.pointer)).selections as typeof selections;
  const original = capturedContextSelection({context, initial_read: true}, pointers).selections as typeof selections;
  const bytes = (rows: typeof selections) => rows.reduce((total, row) => total + Buffer.byteLength(
    JSON.stringify({pointer: row.pointer}).slice(0, -1) + ',"value":' + identity(at(context, row.path), ["context", ...row.path]) + '}'), 0);
  // Splitting a container repeats addresses. Count those and the reuse index;
  // retain a margin for the existing recovery guidance, or deliver it whole.
  if (bytes(grouped) + Buffer.byteLength(JSON.stringify(Object.keys({...reused, ...retainedAuthoring})))
      + MIN_REUSABLE_BODY_BYTES >= bytes(original)) return authoringOnly();
  Object.assign(reused, retainedAuthoring);
  return {...(request.include_initial_read_plan === true
    ? compactObservedSelection(original, grouped) : {selections: grouped}), reused};
}

function compactObservedSelection(ordinary: ReadSelection[], selected: ReadSelection[]): JsonObject {
  // Reuse main's ordered edit algorithm for the opening and its linked remainder.
  const positions = new Map(selected.map((row, index) => [row.pointer, index]));
  const splices: {start: number; end: number; selections: ReadSelection[]}[] = [];
  let start = 0, next = 0;
  for (let end = 0; end < ordinary.length; end++) {
    const position = positions.get(ordinary[end].pointer);
    if (position === undefined || position < next) continue;
    if (start < end || next < position)
      splices.push({start, end, selections: selected.slice(next, position)});
    start = end + 1;
    next = position + 1;
  }
  if (start < ordinary.length || next < selected.length)
    splices.push({start, end: ordinary.length, selections: selected.slice(next)});
  const edits = splices.length === 1 ? {selection_splice: splices[0]} : {selection_splices: splices};
  return splices.length && Buffer.byteLength(JSON.stringify(edits)) < Buffer.byteLength(JSON.stringify({selections: selected}))
    ? edits : {selections: selected};
}

/** Compose the ready opening with the same observation, without comparing
 * bodies again. A coalesced parent can span both parts; keep only the coverage
 * shared by the verified reduced selection and the ordinary remainder. */
function withLinkedObservedRemainder(observed: JsonObject | undefined,
    initial: ReturnType<typeof nativeContextReadPlan> | undefined): JsonObject | undefined {
  if (!observed || !initial?.linked_opening_detail || !initial.remaining
      || Object.hasOwn(observed.reused as JsonObject, initial.linked_opening_detail)) return observed;
  const ordinary = initial.plan.selections as ReadSelection[];
  const selected = observedSelection(ordinary, observed);
  if (selected === undefined) return observed;
  const addresses = new Set(selected.map(row => row.pointer));
  const remaining = initial.remaining.selections as ReadSelection[];
  const scoped = remaining.flatMap(row => addresses.has(row.pointer) || selected.some(item => pathStartsWith(row.path, item.path))
    ? [row] : selected.filter(item => pathStartsWith(item.path, row.path)));
  return {...observed, linked_remaining: compactObservedSelection(remaining, scoped)};
}

function observedSelection(ordinary: ReadSelection[], observed: JsonObject): ReadSelection[] | undefined {
  const candidate = observed.selection_splices ?? (observed.selection_splice === undefined
    ? undefined : [observed.selection_splice]);
  if (candidate === undefined)
    return Array.isArray(observed.selections) ? observed.selections as ReadSelection[] : undefined;
  if (!Array.isArray(candidate) || !candidate.length) return undefined;
  const selected: ReadSelection[] = [];
  let cursor = 0;
  for (const value of candidate) {
    const splice = jsonObject(value);
    const start = splice?.start, end = splice?.end, replacement = splice?.selections;
    if (typeof start !== "number" || !Number.isSafeInteger(start) || typeof end !== "number"
        || !Number.isSafeInteger(end) || start < cursor || end < start || end > ordinary.length
        || !Array.isArray(replacement)) return undefined;
    selected.push(...ordinary.slice(cursor, start), ...replacement as ReadSelection[]);
    cursor = end;
  }
  selected.push(...ordinary.slice(cursor));
  return selected;
}

function inlineReadSections(request: JsonObject, required: JsonObject, exactBody?: ReusableExactBody): JsonObject {
  const original = requireJsonObject(request.context, "inline source"), paths: Path[] = [];
  const visit = (source: unknown, shown: unknown, path: Path) => {
    if (source === shown) { paths.push(path); return; }
    if (!source || !shown || typeof source !== "object" || typeof shown !== "object") return;
    for (const key of Object.keys(source)) if (Object.hasOwn(shown, key)) visit(
      (source as JsonObject)[key], (shown as JsonObject)[key], [...path, Array.isArray(source) ? Number(key) : key]);
  };
  visit(original, required, []);
  return contextReadSectionDigests(request, paths, exactBody);
}

/** Return paths instead of round-tripping the caller's JSON numeric values
 * through JS. The IO adapter performs only these typed-owner-selected edits. */
export function progressiveContextPlan(request: JsonObject,
    scope?: {path: Path; marker_path: Path; instruction: string}): JsonObject {
  const payload = requireJsonObject(request.payload, "captured context");
  const deferred: JsonObject[] = [], instructions: JsonObject[] = [];
  const unchanged = {deferred, instructions};
  // Signed envelopes need their own presentation contract. Unknown shapes and
  // already projected packets retain their entire original content.
  if (request.surface !== "quota_packet" || payload.ok !== true || payload.schema_version !== undefined
      || payload.contract_binding !== undefined || payload.context_details !== undefined
      || payload.context_reference_instruction !== undefined) return unchanged;
  const references = quotaSummaryReferences(payload);
  const carriers: Path[] = [
    ["planning_horizon", "core_goal", "acceptance_contract"],
    ["autonomous_replan_obligation", "replan_context", "core_goal", "acceptance_contract"],
    ["agent_todo_summary", "goal_acceptance_contract"],
  ];
  const agent = ["interaction_contract", "agent_channel"];
  const sourcePath = [...agent, "work_context", "sources"];
  const sources = at(payload, sourcePath);
  if (Array.isArray(sources)) sources.forEach((value, i) => {
    const source = jsonObject(value), content = jsonObject(source?.content);
    if (source?.source === "goal_acceptance" && content?.ok !== false && content?.read_complete !== false
        && (content?.status === undefined || content.status === "loaded")
        && (source.reason === undefined || typeof source.reason === "string" && source.reason.includes(FULL_OUTCOME_READ_GUIDANCE)))
      carriers.push([...sourcePath, i, "content", "goal_acceptance_contract"]);
  });
  for (const carrier of carriers) {
    const path = [...carrier, "outcome_requirements"], declaration = jsonObject(at(payload, path));
    if (declaration?.schema_version !== GOAL_OUTCOMES_SCHEMA || !Array.isArray(declaration.outcomes)) continue;
    declaration.outcomes.forEach((value, i) => {
      const row = jsonObject(value);
      if (!row || !known(row, ["id", "owner_agent_id", "objective", "revision", "requirements", "todo_ids", "sources", "basis_digest", "work"])
          || typeof row.id !== "string" || typeof row.objective !== "string" || !Array.isArray(row.requirements)
          || !Array.isArray(row.sources) || row.sources.some(value => {
            const source = jsonObject(value);
            return !source || !known(source, ["ref", "summary"]) || typeof source.ref !== "string" || typeof source.summary !== "string";
          })) return;
      const reviews: number[] = [];
      for (const [j, value] of row.requirements.entries()) {
        const requirement = jsonObject(value);
        if (!requirement || !known(requirement, ["id", "description", "agent_review", "planning_satisfied", "status"])
            || typeof requirement.id !== "string" || typeof requirement.description !== "string") return;
        if (requirement.agent_review === undefined) continue;
        const review = jsonObject(requirement.agent_review);
        if (!review || !known(review, ["id", "conclusion", "note", "evidence_refs", "basis_current", "independently_verified"])
            || typeof review.note !== "string" || !Array.isArray(review.evidence_refs)
            || review.evidence_refs.some(ref => typeof ref !== "string")
            || !["met", "unmet", "uncertain"].includes(String(review.conclusion))
            || typeof review.basis_current !== "boolean" || review.independently_verified !== false) return;
        reviews.push(j);
      }
      const parent = [...path, "outcomes", i];
      const index_key = "supporting_material_pointer";
      if (row.sources.length) deferred.push({path: [...parent, "sources"], parent, index_key});
      for (const j of reviews) for (const field of ["note", "evidence_refs"])
        deferred.push({path: [...parent, "requirements", j, "agent_review", field], parent, index_key});
    });
  }
  // Replace only the exact source-owned obligation. Other merged obligations,
  // objective adoption, unknown provider prose and pending reads remain intact.
  for (const listPath of deferred.length ? [sourcePath, [...agent, "required_reads"]] : []) {
    const rows = at(payload, listPath);
    if (!Array.isArray(rows)) continue;
    rows.forEach((value, i) => {
      const row = jsonObject(value);
      if (row?.source === "goal_acceptance" && typeof row.reason === "string"
          && row.reason.includes(FULL_OUTCOME_READ_GUIDANCE))
        instructions.push({path: [...listPath, i, "reason"], value:
          row.reason.replace(FULL_OUTCOME_READ_GUIDANCE, PROGRESSIVE_OUTCOME_READ_GUIDANCE)});
    });
  }
  if (scope) {
    deferred.push({path: scope.path, read_instruction: scope.instruction});
    instructions.push({path: scope.marker_path, value: pointer(scope.path)});
  }
  if (!deferred.length && !references.length) return unchanged;
  if (references.length) instructions.push({path: ["context_reference_instruction"], value:
    "A Todo row with context_ref is exactly the complete row at that JSON pointer in THIS required view. "
    + "Use that row for every referenced lane; all constraints, evidence, ownership and order still apply. "
    + "No detail read or older baseline is needed. Missing target content requires recovery of this same capture before dependent work."});
  return {deferred, instructions, ...(references.length ? {references} : {})};
}

const pathStartsWith = (path: Path, start: Path) => start.every((key, i) => path[i] === key);

function requiredNativeContext(request: JsonObject, sourceValues: ProjectionSourceValues,
    plan = progressiveContextPlan({payload: request.context, surface: request.surface})):
    {context: JsonObject; tokens: [Path, string][]; projected: boolean} {
  const original = requireJsonObject(request.context, "current projection");
  const staged = jsonObject(plan.staged_context);
  const context = staged ?? original;
  const details = requireJsonObject(request.native_projection, "supporting material reference");
  const identity = contextSourceIdentity(request, request.source_number_tokens);
  const edits = details.progressive_enabled === false ? {deferred: [], instructions: []} : plan;
  const deferred = edits.deferred as JsonObject[], references = (edits.references ?? []) as JsonObject[];
  const instructions = edits.instructions as JsonObject[];
  const at = (root: unknown, path: Path): unknown => path.reduce<unknown>((row, key) =>
    row !== null && typeof row === "object" && Object.hasOwn(row, key)
      ? (row as Record<string | number, unknown>)[key] : undefined, root);
  const tokens = (request.source_number_tokens ?? []) as [Path, string][];
  const removed: Path[] = [];
  let required = context;
  const copies = new Map<string, JsonObject | unknown[]>();
  const parent = (path: Path): Record<string | number, unknown> => {
    for (let depth = 0; depth < path.length; depth++) {
      const spine = path.slice(0, depth), key = JSON.stringify(spine);
      if (copies.has(key)) continue;
      const original = at(context, spine) as JsonObject | unknown[];
      const copy = Array.isArray(original) ? [...original] : {...original};
      const numbers = new Map<string, Path>();
      for (const [field, value] of Object.entries(original))
        if (typeof value === "number") numbers.set(field,
          ["context", ...spine, Array.isArray(original) ? Number(field) : field]);
      if (numbers.size) sourceValues.set(copy, numbers);
      copies.set(key, copy);
      if (!depth) required = copy as JsonObject;
      else (at(required, spine.slice(0, -1)) as Record<string | number, unknown>)[spine.at(-1)!] = copy;
    }
    return at(required, path.slice(0, -1)) as Record<string | number, unknown>;
  };
  const exactReferences = references.every(edit => identity(at(context, edit.path as Path), ["context", ...edit.path as Path])
    === identity(at(context, edit.from as Path), ["context", ...edit.from as Path]));
  if (exactReferences && (deferred.length || references.length)) {
    for (const edit of references) {
      const path = edit.path as Path;
      parent(path)[path.at(-1)!] = edit.value;
      removed.push(["context", ...path]);
    }
    for (const edit of deferred) {
      const path = edit.path as Path;
      delete parent(path)[path.at(-1)!];
      if (typeof edit.index_key === "string") {
        const owner = edit.parent as Path;
        parent([...owner, edit.index_key])[edit.index_key] = pointer(owner);
      }
      removed.push(["context", ...path]);
    }
    for (const edit of instructions) {
      const path = edit.path as Path;
      parent(path)[path.at(-1)!] = edit.value;
    }
    if (deferred.length) {
      const consumed = jsonObject(request.consumed_pointers) ?? {};
      const digests = new Map<string, string>();
      const currentDigest = (path: Path) => {
        const address = pointer(path);
        if (!digests.has(address)) digests.set(address,
          createHash("sha256").update(identity(at(context, path), ["context", ...path])).digest("hex"));
        return digests.get(address);
      };
      const hasRead = (path: Path) => path.map((_, i) => path.slice(0, i + 1)).concat([[]]).some(parent => {
        const address = pointer(parent), seen = consumed[address];
        return typeof seen === "string" && currentDigest(parent) === seen;
      });
      const sourcePath: Path = ["interaction_contract", "agent_channel", "work_context", "sources"];
      const sources = at(context, sourcePath);
      required.context_details = {path: details.path, sha256: details.sha256,
        ...(typeof details.command_template === "string" ? {command_template: details.command_template} : {}),
        source_path: details.path,
        outcome_support_from_id: true,
      unread_context_index: {
        source: {path: details.path, sha256: details.sha256},
        coverage_confirmed: false,
        read_before_dependent_work: true,
        pointers: [...new Set(deferred.filter(edit => !hasRead(edit.path as Path)).map(edit => pointer(edit.path as Path)))],
        source_blocks: Array.isArray(sources) ? sources.flatMap((source, i) => {
          const path = [...sourcePath, i, "content"];
          return jsonObject(source)?.content !== undefined && !hasRead(path) ? [pointer(path)] : [];
        }) : [],
      },
      instruction: deferred.map(edit => typeof edit.read_instruction === "string" ? edit.read_instruction : "").join("")
        + "This view retains complete requirements and current review/check states. "
        + "Source history, review reasoning and evidence references are supporting material. "
        + "Read them from this same immutable capture when a decision, verification or review depends on them. "
        + "Use the outcome's supporting_material_pointer when shown, or remove /id from its selected JSON pointer. "
        + "Append /sources for source history, or /requirements/<index>/agent_review/note or "
        + "/requirements/<index>/agent_review/evidence_refs for review detail. "
        + "Repeat --select to batch needed fields without replaying the outcome's requirements. "
        + "unread_context_index lists supporting fields not observed read in this epoch, or changed since that read. "
        + "source_blocks conservatively includes partially read current sources; this index is not a full-read gate. "
        + "Use its source path and sha256 with quota context-read --select for each exact pointer. "
        + "A prior selected read, EOF or saved file does not confirm model-visible coverage or retention. "
        + "Detail reads never establish a full baseline. "
        + "Uncertain or missing necessary facts must be recovered before dependent action; Agent review is not independent acceptance."};
    }
  }
  // A signed source and its presentation have different content hashes even
  // when the only edit is a same-view Todo reference. Keep an address for the
  // original and include its cost when deciding whether the projection wins.
  const signedSource = request.surface === "complete_turn";
  const sourceNavigation = typeof details.command_template === "string"
    ? {command_template: details.command_template} : {path: details.path, sha256: details.sha256};
  if (signedSource && required !== context && required.context_details === undefined)
    required.context_details = sourceNavigation;
  const remaining = tokens.filter(([path]) => !removed.some(start => pathStartsWith(path, start)));
  // Unchanged source spellings cancel. Removed registered records contain only
  // safe integral numbers; sparse tokens retain their original float spelling.
  const adjustment = (entries: [Path, string][]) => entries.reduce((size, [path, token]) =>
    path[0] === "context" ? size + token.length - JSON.stringify(at(request, path)).length : size, 0);
  const projected = required !== context && Buffer.byteLength(JSON.stringify(required))
    - Buffer.byteLength(JSON.stringify(context)) + adjustment(remaining) - adjustment(tokens) < 0;
  if (!projected) { required = context; copies.clear(); }
  // The capture can predate the current native invocation. Only the view gets
  // its current selection recipe. Replan commands still use the original hash.
  const selectionPath = ["interaction_contract", "cli_channel", "selection_command", "command_args_template"];
  const transport = details.selection_command_args_template;
  const bindTransport = typeof transport === "string" && typeof at(context, selectionPath) === "string"
    && transport !== at(context, selectionPath);
  if (bindTransport) {
    parent(selectionPath)[selectionPath.at(-1)!] = transport;
    if (signedSource && required.context_details === undefined)
      required.context_details = sourceNavigation;
  }
  if (staged) {
    if (required.context_details === undefined) {
      required = {...required, context_details: sourceNavigation};
    }
    // Staging can omit whole subtrees, but the retained numeric facts still
    // refer to the same source fields, including in the encoded full/delta.
    const retained = tokens.filter(([path]) => {
      if (path[0] !== "context") return true;
      const target = path.slice(1), value = at(required, target);
      if (typeof value !== "number" || !Object.is(value, at(original, target))) return false;
      const owner = at(required, target.slice(0, -1)) as object;
      const fields = sourceValues.get(owner) ?? new Map<string, Path>();
      fields.set(String(target.at(-1)), path);
      sourceValues.set(owner, fields);
      return true;
    });
    return {context: required, tokens: retained, projected: true};
  }
  return {context: required, tokens: projected ? remaining : tokens, projected: projected || bindTransport};
}

function nativeContextContinuity(codec: JsonObject): JsonObject {
  const currentTokens = (codec.source_number_tokens ?? []) as [Path, string][];
  const unsafeInteger = (start: Path) => currentTokens.some(([path, token]) => pathStartsWith(path, start)
    && /^-?\d+$/.test(token) && !Number.isSafeInteger(JSON.parse(token)));
  const floatSources = (start: Path) => JSON.stringify(currentTokens
    .filter(([path, token]) => pathStartsWith(path, start) && /[.eE]/.test(token))
    .map(([path, token]) => JSON.stringify([path.slice(start.length), token])).sort());
  if (unsafeInteger(["context"]) || unsafeInteger(["baseline", "context"])
      || jsonObject(codec.baseline)?.context !== undefined
        && floatSources(["context"]) !== floatSources(["baseline", "context"])) {
    return {...codec, baseline: null, retained: null, continuity: null,
      source_number_tokens: currentTokens.filter(([path]) => path[0] !== "baseline")};
  }
  return codec;
}

/** Plan and encode one native capture without sending its intermediate view
 * back through Python. Capture publication and native locking remain Host IO. */
export function prepareProgressiveContextDelivery(request: JsonObject, legacyFields: readonly string[],
    decisionFields: readonly string[], plan?: JsonObject, exactBody?: ReusableExactBody): JsonObject {
  if (request.include_initial_read_plan !== undefined)
    requireBoolean(request.include_initial_read_plan, "include_initial_read_plan");
  const sourceValues: ProjectionSourceValues = new WeakMap();
  const required = requiredNativeContext(request, sourceValues, plan);
  const codec = nativeContextContinuity({...request, context: required.context,
    ...(request.source_number_tokens === undefined ? {} : {source_number_tokens: required.tokens})});
  const delivery = encodeContextDelivery(codec, legacyFields, sourceValues, fileNavigationPaths(required.context));
  // The Python edit adapter already reconstructs this exact required view.
  // Full native delivery can use that copy; delta still carries its own edits.
  if (request.source_edits === true && delivery.mode === "full") delete delivery.context;
  const observe = request.observe_first_read === true && !!jsonObject(required.context.selected_todo)?.todo_id;
  const groups = observe || request.include_initial_read_plan === true
    ? initialContextReadGroups(required.context, decisionFields) : undefined;
  const readyScope = request.include_initial_read_plan === true && groups
    ? prepareReadyOpening(required.context, groups, exactBody?.path) : undefined;
  const pointers = groups ? [...groups.opening, ...groups.remaining] : [];
  const initial = request.include_initial_read_plan === true && groups
    ? nativeContextReadPlan(required.context, pointers, groups, readyScope) : undefined;
  const observed = observe && pointers.length
    ? withLinkedObservedRemainder(observedFirstRead(codec, pointers, exactBody), initial) : undefined;
  const {operation: _operation, source_edits: _edits, source_number_tokens: _numbers, ...source} = request;
  return contextProjectionChanges(source, {context: required.context, delivery, projected: required.projected,
    ...(observed ? {observed_first_read: observed} : {}),
    read_sections: inlineReadSections(request, required.context, exactBody),
    ...(initial ? {initial_read_plan: initial} : {})}, sourceValues, undefined, true);
}

/** Stateless presentation uses the same plan without inventing a native session,
 * retained baseline or admission identity. Hosts may add delta independently. */
export function prepareProgressiveContext(request: JsonObject, plan?: JsonObject): JsonObject {
  const values: ProjectionSourceValues = new WeakMap();
  const required = requiredNativeContext(request, values, plan);
  const {operation: _operation, source_edits: _edits, source_number_tokens: _numbers, ...source} = request;
  return contextProjectionChanges(source, {context: required.context, projected: required.projected}, values, undefined, true);
}

function prepareReadyOpening(context: JsonObject, groups: ReturnType<typeof initialContextReadGroups>,
    scope?: Path): {boundary: string; detail: string; controls: string[]} | undefined {
  if (!scope) return undefined;
  const parent = scope.slice(0, -1), detail = scope.at(-1)!;
  const body = jsonObject(at(context, parent)), boundary = pointer(parent);
  if (!groups.opening.includes(boundary) || !body
      || !Array.isArray(body[detail]) || !body[detail].length) return undefined;
  // Current readiness belongs with the decision. Reorder only the new pinned
  // reader, so an explicit historical initial-read cursor still replays.
  const preparation = groups.remaining.indexOf("/interaction_contract/cli_channel/execution_preparation");
  if (preparation >= 0) groups.opening.push(...groups.remaining.splice(preparation, 1));
  return {boundary, detail: pointer(scope),
    controls: Object.keys(body).filter(key => key !== detail).map(key => pointer([...parent, key]))};
}

/** Invocation-local navigation, not captured input or a read/retention receipt.
 * Decision vocabulary comes from the same owner as standalone initial reads. */
function nativeContextReadPlan(context: JsonObject, pointers: string[],
    groups: ReturnType<typeof initialContextReadGroups>, readyScope?: ReturnType<typeof prepareReadyOpening>): {
      initial: boolean; plan: JsonObject; opening?: JsonObject; remaining?: JsonObject; linked_opening_detail?: string;
      replan_review?: JsonObject;
      ordinary_opening_rows?: number;
    } {
  if (!pointers.length) return {initial: false,
    plan: capturedContextSelection({context, select: [""], list_fields: true})};
  const select = (addresses: string[]) => capturedContextSelection({context, initial_read: true}, addresses);
  const ordinary = select(pointers);
  const review = successorReplanReadSelection(context, pointers);
  // Both plans address this same capture. Reuse the ordinary addresses already
  // in the response; only send typed paths that the review actually adds.
  const relativeReview = review && (() => {
    const positions = new Map((ordinary.selections as {pointer: string}[]).map((row, i) => [row.pointer, i]));
    return {selections: (review.selections as {pointer: string; path: Path}[])
      .map(row => positions.get(row.pointer) ?? row)};
  })();
  let {opening, remaining} = groups;
  if (readyScope) {
    // The reader chooses the split using the actual serialized opening.
    opening = opening.flatMap(field => field === readyScope.boundary ? readyScope.controls : [field]);
    remaining = [readyScope.detail, ...remaining];
  }
  const ordinaryRows = readyScope ? (ordinary.selections as ReadSelection[]).reduce((end, row, index) =>
    groups.opening.some(field => field === row.pointer || field.startsWith(row.pointer + "/"))
      ? index + 1 : end, 0) : undefined;
  // Keep the ordinary fallback in the same order as the observed selection.
  return {initial: true, plan: ordinary, ...(opening.length && remaining.length
    ? {opening: select(opening), remaining: select(remaining)} : {}),
    ...(readyScope ? {linked_opening_detail: readyScope.detail, ordinary_opening_rows: ordinaryRows} : {}),
    ...(relativeReview ? {replan_review: relativeReview} : {})};
}

/** Address a review before choosing a successor. The ordinary reader and
 * complete source retain authoring for every other outcome. */
export function successorReplanReadSelection(context: JsonObject, pointers: string[]): JsonObject | undefined {
  const obligation = jsonObject(context.autonomous_replan_obligation);
  const replan = jsonObject(context.replan_action_packet);
  const review = jsonObject(obligation?.replan_context);
  const writer = jsonObject(replan?.writeback_contract);
  const receipt = jsonObject(context.heartbeat_receipt);
  const identity = jsonObject(receipt?.settlement_identity);
  const interaction = jsonObject(context.interaction_contract), cli = jsonObject(interaction?.cli_channel);
  const plan = jsonObject(cli?.settlement_plan);
  const binding = jsonObject(jsonObject(cli?.replan_settlement_contract)?.settlement_binding);
  const choices = jsonObject(replan?.uncovered_frontier)?.required_any_of;
  if (!pointers.length || context.schema_version !== "loopx_turn_envelope_v1" ||
      context.ok !== true || context.should_run !== true || context.normal_delivery_allowed !== false ||
      context.effective_action !== EffectiveAction.AUTONOMOUS_REPLAN_REQUIRED || context.selected_todo != null ||
      jsonObject(interaction?.user_channel)?.action_required !== false || cli?.turn_binding_required === true ||
      replan?.schema_version !== "replan_action_packet_v0" || replan.decision !== "replan_required" ||
      replan.settlement_only === true || !obligation || review?.schema_version !== "replan_context_v0" ||
      receipt?.status !== "committed" ||
      !identity || identity.binding_kind !== "autonomous_replan" || !identity.replan_obligation_id ||
      identity.replan_obligation_id !== obligation.obligation_id || identity.replan_obligation_id !== replan.obligation_id ||
      identity.replan_obligation_id !== review.obligation_id || identity.goal_id !== context.goal_id ||
      identity.goal_id !== review.goal_id || identity.agent_id !== review.agent_id ||
      typeof identity.turn_instance_id !== "string" || identity.turn_instance_id !== receipt?.turn_instance_id ||
      plan?.schema_version !== "quota_settlement_plan_v1" || !isDeepStrictEqual(plan.identity, identity) ||
      binding?.kind !== "autonomous_replan" || binding.id !== identity.replan_obligation_id ||
      binding.cli_argument !== "--replan-obligation-id" ||
      typeof writer?.successor_binding_args !== "string" || !writer.successor_binding_args ||
      typeof writer.successor_settlement !== "string" || !writer.successor_settlement ||
      !Array.isArray(choices) || !choices.includes("new_runnable_successor")) return undefined;
  if (!Array.isArray(plan.ordered_steps) || plan.ordered_steps.some(value => {
    const step = jsonObject(value);
    return !step || (step.kind !== "validation" && step.kind !== "durable_writeback" && step.kind !== "quota_spend") ||
      (Object.hasOwn(step, "optional_attachments") && (!Array.isArray(step.optional_attachments) ||
        step.optional_attachments.some(item => jsonObject(item)?.required !== false)));
  })) return undefined;
  // Reuse the typed owner for plain reviews. Cadence, observed stalls, capability
  // gates, lifecycle recovery and mixed triggers need their ordinary authoring.
  if (!projectReplanSemantics({operation: "receipt_review_source", obligation,
    agent_id: identity.agent_id}).review_source) return undefined;

  const ob = "/autonomous_replan_obligation", policy = "/replan_action_packet";
  const contract = policy + "/writeback_contract", cliPath = "/interaction_contract/cli_channel";
  const omit = [ob, cliPath + "/next_cli_actions", cliPath + "/settlement_plan"];
  const scoped = pointers.filter(path => !omit.some(root => path === root || path.startsWith(root + "/")));
  const atReview = scoped.indexOf(policy + "/planning_guidance");
  if (atReview < 0) return undefined;
  scoped.splice(atReview + 1, 0, ob, policy + "/uncovered_frontier",
    cliPath + "/replan_settlement_contract", contract + "/successor_binding_args",
    contract + "/successor_settlement", ...(typeof writer.successor_command === "string" ? [contract + "/successor_command"] : []));
  return capturedContextSelection({context, initial_read: true}, scoped);
}

export function initialContextReadPointers(context: JsonObject, decisionFields: readonly string[]): string[] {
  const {opening, remaining} = initialContextReadGroups(context, decisionFields);
  return [...opening, ...remaining];
}

/** A pending Manager Inbox read already delivers these scoped request rows. */
function canDeferManagerAgenda(value: unknown, requiredReads: unknown[]): boolean {
  const {content, ...read} = jsonObject(value) ?? {};
  const source = jsonObject(content), agenda = jsonObject(source?.unfinished_delegations);
  if (read.source !== "turn_start_capability_hook" || read.kind !== "operator_inbox"
      || read.capability_id !== "manager-context" || read.hook_id !== "manager.context_inbox"
      || read.ordering !== "before_work" || typeof read.command !== "string" || !read.command
      || source?.ok !== true || source.read_complete !== false
      || !agenda || agenda.read_command !== read.command || agenda.count_unit !== "request"
      || agenda.scan_complete !== true || agenda.omitted_count !== 0 || agenda.unavailable_count !== 0
      || !Array.isArray(agenda.items)
      || !known(agenda, ["count_unit", "items", "incoming_count", "outgoing_count", "omitted_count",
        "scan_complete", "unavailable_count", "observed_at", "read_command", "instruction"])
      || !requiredReads.some(required => isDeepStrictEqual(required, read))) return false;
  const {incoming_count: incoming, outgoing_count: outgoing, items} = agenda;
  if (typeof incoming !== "number" || !Number.isSafeInteger(incoming) || incoming < 0
      || typeof outgoing !== "number" || !Number.isSafeInteger(outgoing) || outgoing < 0
      || incoming + outgoing !== items.length) return false;
  // Keep small agendas inline. Three pointers retain the starting counts and
  // scan status without repeating the required read's details or guidance.
  return Buffer.byteLength(JSON.stringify(items), "utf8") >= 2048;
}

function initialContextReadGroups(context: JsonObject, decisionFields: readonly string[]): {
  opening: string[]; remaining: string[];
} {
  const object = (value: unknown): JsonObject | undefined =>
    value !== null && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : undefined;
  const interaction = object(context.interaction_contract);
  // Unknown envelopes retain generic discovery instead of guessing their policy.
  if (interaction?.schema_version !== "loopx_interaction_contract_v0") return {opening: [], remaining: []};
  const pointers: string[] = [];
  const include = (...path: (string | number)[]): void => {
    let value: unknown = context;
    for (const key of path) {
      if (value === null || typeof value !== "object" || !Object.hasOwn(value, key)) return;
      value = (value as Record<string | number, unknown>)[key];
    }
    pointers.push(pointer(path));
  };
  const details = object(context.context_details), unread = object(details?.unread_context_index);
  const supportingSource = object(unread?.source);
  const inlineSupportingDetails = unread && Object.keys(unread).every(key => ["source", "coverage_confirmed", "read_before_dependent_work", "pointers", "source_blocks"].includes(key))
      && (unread.source_blocks === undefined || Array.isArray(unread.source_blocks)
        && unread.source_blocks.every(pointer => typeof pointer === "string")) && unread.coverage_confirmed === false
      && unread.read_before_dependent_work === true && Array.isArray(unread.pointers)
      && unread.pointers.every(pointer => typeof pointer === "string")
      && typeof supportingSource?.path === "string" && typeof supportingSource.sha256 === "string"
      && typeof details?.instruction === "string" && details.instruction.length > 0;
  for (const field of decisionFields) include(field);
  // A staged replan has already replaced inline acceptance with a pinned
  // review reader. Deliver its first action and ordering without writeback.
  const replan = object(context.replan_action_packet);
  if (context.effective_action === EffectiveAction.AUTONOMOUS_REPLAN_REQUIRED &&
      replan?.schema_version === "replan_action_packet_v0" && replan.decision === "replan_required" &&
      replan.settlement_only !== true && typeof replan.review === "string" && replan.review) {
    include("replan_action_packet", "review");
    include("replan_action_packet", "guidance");
  } else if (context.schema_version === "loopx_turn_envelope_v1" &&
      context.effective_action === EffectiveAction.AUTONOMOUS_REPLAN_REQUIRED && replan?.schema_version === "replan_action_packet_v0" &&
      replan.decision === "replan_required" &&
      replan.settlement_only !== true) {
    // The complete contract already carries the review facts: use its own
    // obligations and the indexed outcome paths below, without another writer.
    for (const field of ["planning_guidance", "required_outcome", "allowed_terminal"])
      include("replan_action_packet", field);
    // An invalidated successor can reopen review under the same receipt. Show
    // the current cause and basis before the agent chooses a writeback route.
    const obligation = object(context.autonomous_replan_obligation);
    const review = object(obligation?.replan_context);
    if (obligation?.schema_version === "autonomous_replan_obligation_v0" &&
        typeof replan.obligation_id === "string" && obligation.obligation_id === replan.obligation_id &&
        review?.schema_version === "replan_context_v0" && review.obligation_id === replan.obligation_id) {
      include("autonomous_replan_obligation", "obligation_id");
      include("autonomous_replan_obligation", "stop_condition");
      include("autonomous_replan_obligation", "replan_context", "context_id");
      if (Array.isArray(obligation.triggers)) obligation.triggers.forEach((_trigger, index) =>
        include("autonomous_replan_obligation", "triggers", index, "kind"));
    }
  }
  include("agent_lane_next_action", "next_step");
  include("agent_lane_next_action", "next_action_basis");
  include("interaction_contract", "user_channel");
  for (const field of ["primary_action", "delivery_allowed", "must_attempt", "quiet_noop_allowed", "next_task_action"])
    include("interaction_contract", "agent_channel", field);
  const turnBindingRequired = object(interaction.cli_channel)?.turn_binding_required === true;
  if (turnBindingRequired)
    for (const field of ["turn_binding_required", "next_cli_actions", "spend_allowed_now", "spend_after_validation", "spend_policy"])
      include("interaction_contract", "cli_channel", field);
  include("interaction_contract", "agent_channel", "required_reads");
  const work = ["interaction_contract", "agent_channel", "work_context"];
  include(...work, "instruction");
  const channel = object(interaction.agent_channel);
  const requiredInboxReads = context.effective_action === EffectiveAction.NORMAL_RUN && context.normal_delivery_allowed === true
    && channel?.delivery_allowed === true && channel.must_attempt === true && Array.isArray(channel.required_reads)
    ? channel.required_reads : [];
  const sources = object(channel?.work_context)?.sources;
  const sourceRows = Array.isArray(sources) ? sources.map((source, index) => ({source, index})) : [];
  const selectedTodo = object(context.selected_todo);
  const identityAgent = object(context.agent_identity)?.agent_id;
  const agentId = context.agent_id ?? identityAgent;
  // Deliver the opted-in decision cue before work, while the candidate or
  // test can still change. The template and evidence stay on their owner read.
  const cueStart = pointers.length;
  if (context.effective_action === EffectiveAction.NORMAL_RUN && context.normal_delivery_allowed === true
      && typeof selectedTodo?.todo_id === "string" && typeof context.goal_id === "string" && typeof agentId === "string"
      && (identityAgent === undefined || identityAgent === agentId)) {
    for (const {source, index} of sourceRows) {
      const row = object(source), view = object(row?.content), graph = object(view?.graph);
      if (row?.kind !== "explore_turn_context" || row.capability_id !== "explore"
          || view?.ok !== true || view.graph_enabled !== true || view.goal_id !== context.goal_id
          || view.agent_id !== agentId || (view.todo_id !== undefined && view.todo_id !== selectedTodo.todo_id)
          || typeof object(graph?.work_yardstick_capture)?.guidance !== "string") continue;
      const graphPath = [...work, "sources", index, "content", "graph"];
      include(...graphPath, "work_yardstick_capture", "guidance");
      const measurements = graph?.work_measurements;
      if (Array.isArray(measurements)) {
        const current = measurements.findIndex(card => object(card)?.todo_id === selectedTodo.todo_id);
        if (current >= 0) include(...graphPath, "work_measurements", current);
      }
      const cues = graph?.work_yardsticks;
      if (Array.isArray(cues)) {
        const current = cues.findIndex(cue => object(cue)?.todo_id === selectedTodo.todo_id);
        if (current >= 0) include(...graphPath, "work_yardsticks", current);
      }
      break;
    }
  }
  // Place the cue after the run decision, before variable-size Goal and work
  // fields. This also works when the full selection falls back to plain paging.
  const decisionEnd = pointers.indexOf("/normal_delivery_allowed");
  if (decisionEnd >= 0 && pointers.length > cueStart) {
    const cuePointers = pointers.splice(cueStart);
    pointers.splice(decisionEnd + 1, 0, ...cuePointers);
  }
  // If the full starting selection needs paging, put the current decision and
  // its read obligations in one independently readable opening. The rest is
  // still required under the original ordering and authority.
  const opening = pointers.splice(0);
  include(...work, "user_todos");
  // A complete scoped preference can be larger than a reader page. Visit
  // current narrative sources first, with their availability and provenance,
  // before selecting other sources' metadata: complete-parent coalescing keeps
  // the first selected child's position. Preserve stable order within each group.
  const hasText = (source: unknown): boolean => typeof object(object(source)?.content)?.text === "string";
  sourceRows.sort((left, right) => Number(hasText(right.source)) - Number(hasText(left.source)));
  sourceRows.forEach(({source: _source, index}) => {
    const source = [...work, "sources", index];
    for (const field of ["source", "kind", "reason", "ordering", "command"])
      include(...source, field);
    // Current Goal/Todo text and source obligations stay whole. Expansive
    // evidence pages, example recipes and historical bodies remain on demand.
    include(...source, "content", "text");
    include(...source, "content", "instruction");
    for (const field of ["ok", "status", "reason", "error", "recovery", "revision", "provider_revision", "source_authority",
      "decision_read_from_provider", "legacy_fallback_used", "graph_enabled", "harness_enabled", "boundary", "authority_read"])
      include(...source, "content", field);
    include(...source, "content", "read_complete");
    const agendaPath = [...source, "content", "unfinished_delegations"];
    if (canDeferManagerAgenda(_source, requiredInboxReads)) {
      for (const field of ["incoming_count", "outgoing_count", "scan_complete"]) include(...agendaPath, field);
    } else include(...agendaPath);
    // The preference owner already supplies the complete current scoped view,
    // including corrections, retirement, provenance and advisory authority.
    // Its revision/instructions alone cannot fulfill that pre-work delivery.
    include(...source, "content", "current");
    include(...source, "content", "outcome_context");
    const sourceRow = object(_source), content = object(sourceRow?.content);
    const requirements = object(object(content?.goal_acceptance_contract)?.outcome_requirements);
    const outcomes = requirements?.outcomes, navigation = object(content?.outcome_context);
    const identityAgent = object(context.agent_identity)?.agent_id;
    const agentId = context.agent_id ?? identityAgent;
    const suggested = navigation?.suggested_outcome_ids;
    // Reuse the acceptance owner's relationship navigation; do not infer scope
    // from ownership or prose. This only expands the starting read: other
    // outcomes and the original required-read obligations remain available.
    const expand = sourceRow?.source === "goal_acceptance" && content?.ok !== false
      && content?.status === "loaded" && content.decision_read_from_provider === true
      && content.legacy_fallback_used === false && content.read_complete !== false
      && requirements?.schema_version === GOAL_OUTCOMES_SCHEMA && requirements.current_lifetime === true
      && navigation?.schema_version === OUTCOME_CONTEXT_NAVIGATION_SCHEMA && navigation.coverage_complete === true
      && typeof context.goal_id === "string" && navigation.goal_id === context.goal_id
      && typeof agentId === "string" && agentId.length > 0 && navigation.agent_id === agentId
      && (context.agent_id === undefined || identityAgent === undefined || context.agent_id === identityAgent)
      && typeof object(context.selected_todo)?.todo_id === "string"
      && navigation.todo_id === object(context.selected_todo)?.todo_id
      && navigation.outcome_revision === requirements.revision
      && typeof content.provider_revision === "string" && navigation.provider_revision === content.provider_revision
      && Array.isArray(outcomes) && Array.isArray(suggested)
      && suggested.every(id => typeof id === "string"
        && outcomes.filter(value => object(value)?.id === id).length === 1);
    const expanded = new Set(expand ? suggested as string[] : []);
    if (Array.isArray(outcomes)) outcomes.forEach((_outcome, outcomeIndex) => {
      const outcome = [...source, "content", "goal_acceptance_contract", "outcome_requirements", "outcomes", outcomeIndex];
      const row = object(_outcome), outcomeId = row?.id;
      if (typeof outcomeId === "string" && expanded.has(outcomeId)) { include(...outcome); return; }
      include(...outcome, "id");
      // The id selection already carries the same absolute outcome address.
      // Keep aliases whose source, value or read guidance cannot be verified.
      if (!(inlineSupportingDetails && details?.outcome_support_from_id === true
          && details.path === supportingSource?.path && details.sha256 === supportingSource?.sha256
          && typeof outcomeId === "string" && row?.supporting_material_pointer === pointer(outcome)))
        include(...outcome, "supporting_material_pointer");
    });
    include(...source, "content", "todo", "text");
    include(...source, "content", "todo", "note");
    include(...source, "content", "graph", "guidance");
    include(...source, "content", "graph", "recent_findings");
    include(...source, "content", "graph", "result_page");
    include(...source, "content", "harness", "guidance");
    include(...source, "content", "harness", "orchestration_gate");
  });
  const cli = object(interaction.cli_channel);
  const planPath = SETTLEMENT_PLAN_PATH;
  const plan = object(cli?.settlement_plan);
  const steps = orderedSettlementSteps(plan);
  const settlementSteps = steps?.map(object).filter(step => step?.kind === "durable_writeback" || step?.kind === "quota_spend");
  const [writeback, spend] = settlementSteps ?? [];
  const manualSettlement = plan?.schema_version === "quota_settlement_plan_v1";
  // Recorded v2 captures retain their original runtime-owned receipt step.
  // Native CLI projection may compose the current plan before binding; history remains unchanged.
  const composedSettlement = plan?.schema_version === "quota_settlement_plan_v2" && writeback?.owner === "agent"
    && spend?.owner === "runtime" && !Object.hasOwn(spend, "command_template");
  const templates = manualSettlement ? [writeback?.command_template, spend?.command_template]
    : composedSettlement ? [writeback?.command_template] : undefined;
  // A ready Turn already shows its ordered writeback and spend with their
  // conditions. Hide only the exact duplicate fallback in this first read;
  // recovery, legacy readers and the original capture keep both forms.
  const repeatsSettlement = readySettlementIdentity(context, interaction, plan) !== undefined
    && settlementSteps?.length === 2 && writeback?.kind === "durable_writeback" && spend?.kind === "quota_spend"
    && templates?.every(command => typeof command === "string" && command.length > 0) === true
    && isDeepStrictEqual(cli?.next_cli_actions, templates);
  if (!turnBindingRequired)
    for (const field of ["execution_preparation", "next_cli_actions", "spend_allowed_now", "spend_after_validation", "spend_policy",
      "quota_spend_source", "delivery_workspace_causality", "vision_continuation_audit"])
      if (field !== "next_cli_actions" || !repeatsSettlement) {
        const before = pointers.length;
        include("interaction_contract", "cli_channel", field);
        // A task-facing operation and its declared continuation belong in the
        // same opening. Keep the existing exact settlement deduplication.
        if (field === "next_cli_actions" && object(channel?.next_task_action) && pointers.length > before)
          opening.push(pointers.pop()!);
      }
  // Explicit selection clears next_cli_actions. Deliver the owner's discovery
  // and same-Turn binding recipe alongside its non-exhaustive suggestions.
  if (cli?.selection_required === true) {
    for (const field of ["selection_required", "selection_policy_ref", "selection_command"])
      include("interaction_contract", "cli_channel", field);
    include("action_portfolio", "selection_policy");
    include("action_portfolio", "suggested_actions");
  }
  if (plan && steps) {
    for (const field of Object.keys(plan)) if (field !== "ordered_steps") include(...planPath, field);
    steps.forEach((value, stepIndex) => {
      const step = object(value), stepPath = [...planPath, "ordered_steps", stepIndex];
      if (!step) { include(...stepPath); return; }
      for (const field of Object.keys(step)) {
        if (field === "vision_authoring" && knownVisionAuthoring(step)) {
          // Keep the decision and continuation policy at admission. Field limits
          // and worked examples are read from this step when writing the Vision.
          for (const key of ["schema_version", "rule", "continuation_review"]) include(...stepPath, field, key);
          continue;
        }
        if (field !== "optional_attachments" || !Array.isArray(step[field])) { include(...stepPath, field); continue; }
        step[field].forEach((attachment, attachmentIndex) => {
          const path = [...stepPath, field, attachmentIndex];
          if (object(attachment)?.required !== false) { include(...path); return; }
          // Explicitly optional guidance is read before using that attachment.
          // Keep its identity and actual read/authoring routes discoverable.
          for (const key of Object.keys(object(attachment)!)) {
            if (["guidance", "attachment_template", "linked_question_attachment_template", "path_delta_attachment_template",
              "optional_measurement_example", "optional_question_evolution_example"].includes(key) ||
              (key === "optional_execution_observation_example" && object(attachment)?.capability_id === "explore")) continue;
            include(...path, key); // Unknown attachment controls are never hidden.
          }
        });
      }
    });
  } else include(...planPath); // Unknown plan versions keep their complete obligations.
  // Keep the pinned supporting-read route and its obligations inline.
  // The complete index is available at the same captured address.
  if (inlineSupportingDetails) {
    for (const field of ["source", "coverage_confirmed", "read_before_dependent_work"])
      include("context_details", "unread_context_index", field);
    include("context_details", "instruction");
    include("context_details", "command_template");
  } else if (details && known(details, ["command_template", "path", "sha256"])) {
    include("context_details");
  } else include("context_details", "unread_context_index");
  return {opening, remaining: pointers};
}
