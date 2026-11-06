import {EffectiveAction} from "./effective_action.generated.ts";
/** Model presentation: requirements stay inline, supporting material is
 * addressed in the immutable source. This plan changes no canonical state. */
import {isDeepStrictEqual} from "node:util";
import {visionAuthoringContract} from "../goals/vision_checkpoint.ts";
import type {JsonObject} from "../effect_program.ts";
import {jsonObject, requireBoolean, requireJsonObject} from "../runtime_decode.ts";
import {GOAL_OUTCOMES_SCHEMA, OUTCOME_CONTEXT_NAVIGATION_SCHEMA} from "../goals/outcome_requirements.ts";
import {FULL_OUTCOME_READ_GUIDANCE, PROGRESSIVE_OUTCOME_READ_GUIDANCE} from "../work_items/interaction_contract.ts";
import {quotaSummaryReferences, contextSourceIdentity, contextProjectionChanges,
  type ProjectionSourceValues} from "./diagnostic_projection.ts";
import {encodeContextDelivery, fileNavigationPaths} from "./context_delivery.ts";
import {createHash} from "node:crypto";
import {capturedContextSelection} from "./context_selection.ts";

type Path = (string | number)[];
const MIN_REUSABLE_BODY_BYTES = 1024;
const pointer = (path: Path): string => path.length ? "/" + path.map(key =>
  String(key).replaceAll("~", "~0").replaceAll("/", "~1")).join("/") : "";
const at = (root: unknown, path: Path): unknown => path.reduce<unknown>((row, key) =>
  Array.isArray(row) ? row[Number(key)] : jsonObject(row)?.[key], root);
const known = (row: JsonObject, fields: string[]): boolean => Object.keys(row).every(key => fields.includes(key));

/** One registry for bodies eligible for observed reuse, regardless of whether
 * the reader delivered the body itself or packaged it in a complete parent. */
function reusableFirstReadFields(context: JsonObject): {path: Path; freshKeys: string[]}[] {
  const base: Path = ["interaction_contract", "agent_channel", "work_context", "sources"];
  const sources = at(context, base);
  if (!Array.isArray(sources)) return [];
  return sources.flatMap((value, i) => {
    const source = jsonObject(value), content = jsonObject(source?.content);
    if (!content) return [];
    const path = [...base, i, "content"];
    if (source?.kind === "agent_preferences")
      return [{path, freshKeys: ["ok", "revision", "instruction"]}];
    if (source?.source !== "goal_acceptance") return [];
    const outcomesPath = [...path, "goal_acceptance_contract", "outcome_requirements", "outcomes"];
    const outcomes = at(context, outcomesPath);
    return Array.isArray(outcomes) ? outcomes.flatMap((outcome, index) =>
      typeof jsonObject(outcome)?.id === "string"
        ? [{path: [...outcomesPath, index], freshKeys: ["id", "revision"]}] : []) : [];
  });
}

/** Digests stay in the private observed-read journal, never repeated to the model.
 * The existing exact-source identity preserves non-JS numeric spellings. */
export function contextReadSectionDigests(request: JsonObject, paths: Path[]): JsonObject {
  const context = requireJsonObject(request.context, "read section source");
  const identity = contextSourceIdentity(request, request.source_number_tokens);
  const selected = new Set(paths.map(pointer));
  const covered = new Map(paths.map(path => [pointer(path), path]));
  for (const {path} of reusableFirstReadFields(context))
    if (paths.some(parent => pathStartsWith(path, parent))) covered.set(pointer(path), path);
  return Object.fromEntries([...covered].flatMap(([address, path]) => {
    const content = identity(at(context, path), ["context", ...path]);
    // Parent delivery must not multiply tiny bodies into a larger journal.
    return selected.has(address) || Buffer.byteLength(content) >= MIN_REUSABLE_BODY_BYTES
      ? [[address, createHash("sha256").update(content).digest("hex")]] : [];
  }));
}

/** Only previously delivered supplementary bodies may use a reference. Current
 * decisions, stops, selection, settlement and unknown source shapes stay inline. */
function observedFirstRead(request: JsonObject, pointers: string[]): JsonObject | undefined {
  const context = requireJsonObject(request.context, "current first read");
  const seen = requireJsonObject(request.consumed_pointers, "observed sections");
  if (typeof jsonObject(context.selected_todo)?.todo_id !== "string") return undefined;
  const fields = new Map(reusableFirstReadFields(context).map(field => [pointer(field.path), field]));
  const identity = contextSourceIdentity(request, request.source_number_tokens);
  const reused: JsonObject = {};
  // Match semantic fields before the reader groups complete parents. Otherwise
  // selecting every outcome hides the individual unchanged bodies from reuse.
  const selections = capturedContextSelection({context, select: pointers,
    preserve_selection_order: true}).selections as {pointer: string; path: Path}[];
  const current = selections.flatMap(row => {
    const {path, pointer: address} = row;
    const field = fields.get(address);
    if (typeof seen[address] !== "string" || !field) return [row];
    const body = jsonObject(at(context, path));
    if (!body) return [row];
    const content = identity(body, ["context", ...path]);
    // A reference and its recovery instructions should cost less than the field.
    if (Buffer.byteLength(content) < MIN_REUSABLE_BODY_BYTES || createHash("sha256").update(content).digest("hex") !== seen[address])
      return [row];
    reused[address] = seen[address];
    return field.freshKeys.filter(key =>
      Object.hasOwn(body, key)).map(key => ({pointer: address + "/" + key, path: [...path, key]}));
  });
  return Object.keys(reused).length ? {selections: capturedContextSelection({context, initial_read: true},
    current.map(row => row.pointer)).selections, reused} : undefined;
}

function inlineReadSections(request: JsonObject, required: JsonObject): JsonObject {
  const original = requireJsonObject(request.context, "inline source"), paths: Path[] = [];
  const visit = (source: unknown, shown: unknown, path: Path) => {
    if (source === shown) { paths.push(path); return; }
    if (!source || !shown || typeof source !== "object" || typeof shown !== "object") return;
    for (const key of Object.keys(source)) if (Object.hasOwn(shown, key)) visit(
      (source as JsonObject)[key], (shown as JsonObject)[key], [...path, Array.isArray(source) ? Number(key) : key]);
  };
  visit(original, required, []);
  return contextReadSectionDigests(request, paths);
}

/** Return paths instead of round-tripping the caller's JSON numeric values
 * through JS. The IO adapter performs only these typed-owner-selected edits. */
export function progressiveContextPlan(request: JsonObject): JsonObject {
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
  if (!deferred.length && !references.length) return unchanged;
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
  const context = requireJsonObject(request.context, "current projection");
  const details = requireJsonObject(request.native_projection, "supporting material reference");
  const identity = contextSourceIdentity(request, request.source_number_tokens);
  const deferred = plan.deferred as JsonObject[], references = (plan.references ?? []) as JsonObject[];
  const instructions = plan.instructions as JsonObject[];
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
      const path = edit.path as Path, owner = edit.parent as Path;
      delete parent(path)[path.at(-1)!];
      parent([...owner, String(edit.index_key)])[String(edit.index_key)] = pointer(owner);
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
      required.context_details = {...details, source_path: details.path,
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
      instruction: "This view retains complete requirements and current review/check states. "
        + "Source history, review reasoning and evidence references are supporting material. "
        + "Read them from this same immutable capture when a decision, verification or review depends on them. "
        + "For source history use supporting_material_pointer plus /sources. For review detail append "
        + "/requirements/<index>/agent_review/note or /requirements/<index>/agent_review/evidence_refs instead. "
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
  // A saved capture may predate the caller's native transport. Keep its signed
  // source immutable; bind only this display recipe after the optional shrink
  // decision, so caller options neither bypass source validation nor defeat its
  // no-growth guard. The next invocation still runs the ordinary CLI guard.
  const selectionPath = ["interaction_contract", "cli_channel", "selection_command", "command_args_template"];
  const transport = details.selection_command_args_template;
  const bindTransport = typeof transport === "string" && typeof at(context, selectionPath) === "string"
    && transport !== at(context, selectionPath);
  if (!projected) { required = context; copies.clear(); }
  if (bindTransport) {
    parent(selectionPath)[selectionPath.at(-1)!] = transport;
    if (signedSource && required.context_details === undefined)
      required.context_details = sourceNavigation;
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
    decisionFields: readonly string[], plan?: JsonObject): JsonObject {
  if (request.include_initial_read_plan !== undefined)
    requireBoolean(request.include_initial_read_plan, "include_initial_read_plan");
  const sourceValues: ProjectionSourceValues = new WeakMap();
  const required = requiredNativeContext(request, sourceValues, plan);
  const codec = nativeContextContinuity({...request, context: required.context,
    ...(request.source_number_tokens === undefined ? {} : {source_number_tokens: required.tokens})});
  const delivery = encodeContextDelivery(codec, legacyFields, sourceValues, fileNavigationPaths(required.context));
  const observe = request.observe_first_read === true && !!jsonObject(required.context.selected_todo)?.todo_id;
  const navigation = request.include_initial_read_plan === true || observe
    ? nativeContextReadPlan(required.context, decisionFields) : undefined;
  const observed = observe && navigation?.initial
    ? observedFirstRead(codec, initialContextReadPointers(required.context, decisionFields)) : undefined;
  const {operation: _operation, source_edits: _edits, source_number_tokens: _numbers, ...source} = request;
  return contextProjectionChanges(source, {context: required.context, delivery, projected: required.projected,
    ...(observed ? {observed_first_read: observed} : {}),
    read_sections: inlineReadSections(request, required.context),
    ...(request.include_initial_read_plan === true
      ? {initial_read_plan: navigation} : {})}, sourceValues);
}

/** Stateless presentation uses the same plan without inventing a native session,
 * retained baseline or admission identity. Hosts may add delta independently. */
export function prepareProgressiveContext(request: JsonObject, plan?: JsonObject): JsonObject {
  const values: ProjectionSourceValues = new WeakMap();
  const required = requiredNativeContext(request, values, plan);
  const {operation: _operation, source_edits: _edits, source_number_tokens: _numbers, ...source} = request;
  return contextProjectionChanges(source, {context: required.context, projected: required.projected}, values);
}

/** Invocation-local navigation, not captured input or a read/retention receipt.
 * Decision vocabulary comes from the same owner as standalone initial reads. */
function nativeContextReadPlan(context: JsonObject, decisionFields: readonly string[]): JsonObject {
  const pointers = initialContextReadPointers(context, decisionFields), initial = pointers.length > 0;
  return {initial, plan: capturedContextSelection(initial ? {context, initial_read: true}
    : {context, select: [""], list_fields: true}, pointers)};
}

export function initialContextReadPointers(context: JsonObject, decisionFields: readonly string[]): string[] {
  const object = (value: unknown): JsonObject | undefined =>
    value !== null && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : undefined;
  const interaction = object(context.interaction_contract);
  // Unknown envelopes retain generic discovery instead of guessing their policy.
  if (interaction?.schema_version !== "loopx_interaction_contract_v0") return [];
  const pointers: string[] = [];
  const include = (...path: (string | number)[]): void => {
    let value: unknown = context;
    for (const key of path) {
      if (value === null || typeof value !== "object" || !Object.hasOwn(value, key)) return;
      value = (value as Record<string | number, unknown>)[key];
    }
    pointers.push("/" + path.map(key => String(key).replaceAll("~", "~0").replaceAll("/", "~1")).join("/"));
  };
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
  for (const field of ["primary_action", "delivery_allowed", "must_attempt", "quiet_noop_allowed"])
    include("interaction_contract", "agent_channel", field);
  include("interaction_contract", "agent_channel", "required_reads");
  const work = ["interaction_contract", "agent_channel", "work_context"];
  include(...work, "instruction");
  include(...work, "user_todos");
  const channel = object(interaction.agent_channel);
  const sources = object(channel?.work_context)?.sources;
  const sourceRows = Array.isArray(sources) ? sources.map((source, index) => ({source, index})) : [];
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
    // The capability already bounds this agenda. Keep pending returns and
    // incomplete observations visible without expanding full request bodies.
    include(...source, "content", "read_complete");
    include(...source, "content", "unfinished_delegations");
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
      const outcomeId = object(_outcome)?.id;
      if (typeof outcomeId === "string" && expanded.has(outcomeId)) { include(...outcome); return; }
      include(...outcome, "id");
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
  for (const field of ["execution_preparation", "next_cli_actions", "spend_allowed_now", "spend_after_validation", "spend_policy",
    "quota_spend_source", "delivery_workspace_causality", "vision_continuation_audit"])
    include("interaction_contract", "cli_channel", field);
  // Explicit selection clears next_cli_actions. Deliver the owner's discovery
  // and same-Turn binding recipe alongside its non-exhaustive suggestions.
  if (object(interaction.cli_channel)?.selection_required === true) {
    for (const field of ["selection_required", "selection_policy_ref", "selection_command"])
      include("interaction_contract", "cli_channel", field);
    include("action_portfolio", "selection_policy");
    include("action_portfolio", "suggested_actions");
  }
  const planPath = ["interaction_contract", "cli_channel", "settlement_plan"];
  const plan = object(object(interaction.cli_channel)?.settlement_plan);
  if ((plan?.schema_version === "quota_settlement_plan_v1" || plan?.schema_version === "quota_settlement_plan_v2") && Array.isArray(plan.ordered_steps)) {
    for (const field of Object.keys(plan)) if (field !== "ordered_steps") include(...planPath, field);
    plan.ordered_steps.forEach((value, stepIndex) => {
      const step = object(value), stepPath = [...planPath, "ordered_steps", stepIndex];
      if (!step) { include(...stepPath); return; }
      for (const field of Object.keys(step)) {
        if (field === "vision_authoring" && step.kind === "durable_writeback"
            && [false, true].some(replan => isDeepStrictEqual(step[field], visionAuthoringContract(replan)))) {
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
              "optional_measurement_example", "optional_question_evolution_example"].includes(key)) continue;
            include(...path, key); // Unknown attachment controls are never hidden.
          }
        });
      }
    });
  } else include(...planPath); // Unknown plan versions keep their complete obligations.
  const details = object(context.context_details), unread = object(details?.unread_context_index);
  const source = object(unread?.source);
  // The current supporting-address inventory can dwarf the required facts.
  // Keep its original source, unknown coverage and read-before-work obligation
  // inline; the complete pointer list remains at this same captured address.
  // Unknown/future layouts stay whole rather than hiding a new obligation.
  if (unread && Object.keys(unread).every(key => ["source", "coverage_confirmed", "read_before_dependent_work", "pointers", "source_blocks"].includes(key))
      && (unread.source_blocks === undefined || Array.isArray(unread.source_blocks)
        && unread.source_blocks.every(pointer => typeof pointer === "string")) && unread.coverage_confirmed === false
      && unread.read_before_dependent_work === true && Array.isArray(unread.pointers)
      && unread.pointers.every(pointer => typeof pointer === "string")
      && typeof source?.path === "string" && typeof source.sha256 === "string"
      && typeof details?.instruction === "string" && details.instruction.length > 0) {
    for (const field of ["source", "coverage_confirmed", "read_before_dependent_work"])
      include("context_details", "unread_context_index", field);
    include("context_details", "instruction");
    include("context_details", "command_template");
  } else if (details && known(details, ["command_template", "path", "sha256"])) {
    include("context_details");
  } else include("context_details", "unread_context_index");
  return pointers;
}
