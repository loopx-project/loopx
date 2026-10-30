/** Native model presentation: requirements stay inline, supporting material is
 * addressed in the immutable source. This plan changes no canonical state. */
import type {JsonObject} from "../effect_program.ts";
import {jsonObject, requireJsonObject} from "../runtime_decode.ts";
import {GOAL_OUTCOMES_SCHEMA} from "../goals/outcome_requirements.ts";
import {FULL_OUTCOME_READ_GUIDANCE, PROGRESSIVE_OUTCOME_READ_GUIDANCE} from "../work_items/interaction_contract.ts";
import {quotaSummaryReferences, contextSourceIdentity, contextProjectionChanges,
  type ProjectionSourceValues} from "./diagnostic_projection.ts";
import {encodeContextDelivery} from "./context_delivery.ts";

type Path = (string | number)[];
const at = (root: unknown, path: Path): unknown => path.reduce<unknown>((row, key) =>
  Array.isArray(row) ? row[Number(key)] : jsonObject(row)?.[key], root);
const known = (row: JsonObject, fields: string[]): boolean => Object.keys(row).every(key => fields.includes(key));

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

function requiredNativeContext(request: JsonObject, sourceValues: ProjectionSourceValues):
    {context: JsonObject; tokens: [Path, string][]; projected: boolean} {
  const context = requireJsonObject(request.context, "current projection");
  const details = requireJsonObject(request.native_projection, "supporting material reference");
  const identity = contextSourceIdentity(request, request.source_number_tokens);
  const plan = progressiveContextPlan({payload: context, surface: request.surface});
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
      parent([...owner, String(edit.index_key)])[String(edit.index_key)] = "/" + owner.map(key =>
        String(key).replaceAll("~", "~0").replaceAll("/", "~1")).join("/");
      removed.push(["context", ...path]);
    }
    for (const edit of instructions) {
      const path = edit.path as Path;
      parent(path)[path.at(-1)!] = edit.value;
    }
    if (deferred.length) required.context_details = {...details,
      instruction: "This view retains complete requirements and current review/check states. "
        + "Source history, review reasoning and evidence references are supporting material. "
        + "Read them from this same immutable capture when a decision, verification or review depends on them. "
        + "For source history use supporting_material_pointer plus /sources. For review detail append "
        + "/requirements/<index>/agent_review/note or /requirements/<index>/agent_review/evidence_refs instead. "
        + "Repeat --select to batch needed fields without replaying the outcome's requirements. "
        + "Detail reads never establish a full baseline. "
        + "Uncertain or missing necessary facts must be recovered before dependent action; Agent review is not independent acceptance."};
  }
  const remaining = tokens.filter(([path]) => !removed.some(start => pathStartsWith(path, start)));
  // Unchanged source spellings cancel. Removed registered records contain only
  // safe integral numbers; sparse tokens retain their original float spelling.
  const adjustment = (entries: [Path, string][]) => entries.reduce((size, [path, token]) =>
    path[0] === "context" ? size + token.length - JSON.stringify(at(request, path)).length : size, 0);
  const projected = required !== context && Buffer.byteLength(JSON.stringify(required))
    - Buffer.byteLength(JSON.stringify(context)) + adjustment(remaining) - adjustment(tokens) < 0;
  return {context: projected ? required : context, tokens: projected ? remaining : tokens, projected};
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
export function prepareProgressiveContextDelivery(request: JsonObject, legacyFields: readonly string[]): JsonObject {
  const sourceValues: ProjectionSourceValues = new WeakMap();
  const required = requiredNativeContext(request, sourceValues);
  const codec = nativeContextContinuity({...request, context: required.context,
    ...(request.source_number_tokens === undefined ? {} : {source_number_tokens: required.tokens})});
  const delivery = encodeContextDelivery(codec, legacyFields, sourceValues);
  const {operation: _operation, source_edits: _edits, source_number_tokens: _numbers, ...source} = request;
  return contextProjectionChanges(source, {context: required.context, delivery, projected: required.projected}, sourceValues);
}
