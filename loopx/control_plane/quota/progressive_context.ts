/** Native model presentation: requirements stay inline, supporting material is
 * addressed in the immutable source. This plan changes no canonical state. */
import type {JsonObject} from "../effect_program.ts";
import {jsonObject, requireJsonObject} from "../runtime_decode.ts";
import {GOAL_OUTCOMES_SCHEMA} from "../goals/outcome_requirements.ts";
import {FULL_OUTCOME_READ_GUIDANCE, PROGRESSIVE_OUTCOME_READ_GUIDANCE,
  SCOPED_OUTCOME_READ_GUIDANCE, PROGRESSIVE_SCOPED_OUTCOME_READ_GUIDANCE} from "../work_items/interaction_contract.ts";
import {quotaSummaryReferences} from "./diagnostic_projection.ts";

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
  const originalPaths = new WeakMap<object, Path>();
  const indexOriginal = (value: unknown, path: Path): void => {
    if (!value || typeof value !== "object" || originalPaths.has(value)) return;
    originalPaths.set(value, path);
    if (Array.isArray(value)) value.forEach((row, i) => indexOriginal(row, [...path, i]));
    else Object.entries(value).forEach(([key, row]) => indexOriginal(row, [...path, key]));
  };
  if (request.source_payload !== undefined) indexOriginal(request.source_payload, []);
  const guidance = (value: unknown): [string, string] | undefined => typeof value === "string"
    ? [[FULL_OUTCOME_READ_GUIDANCE, PROGRESSIVE_OUTCOME_READ_GUIDANCE],
      [SCOPED_OUTCOME_READ_GUIDANCE, PROGRESSIVE_SCOPED_OUTCOME_READ_GUIDANCE]]
      .find(([original]) => value.includes(original)) as [string, string] | undefined : undefined;
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
        && (source.reason === undefined || guidance(source.reason)))
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
      const original = originalPaths.get(row);
      // A scoped array can reorder its retained rows. Its detail pointer still
      // addresses the original immutable source, not the filtered array index.
      if (request.source_payload !== undefined && !original) return;
      const pointer = original ? {index_path: original} : {};
      if (row.sources.length) deferred.push({path: [...parent, "sources"], parent, index_key, ...pointer});
      for (const j of reviews) for (const field of ["note", "evidence_refs"])
        deferred.push({path: [...parent, "requirements", j, "agent_review", field], parent, index_key, ...pointer});
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
      const clause = guidance(row?.reason);
      if (row?.source === "goal_acceptance" && clause)
        instructions.push({path: [...listPath, i, "reason"], value:
          (row.reason as string).replace(clause[0], clause[1])});
    });
  }
  if (references.length) instructions.push({path: ["context_reference_instruction"], value:
    "A Todo row with context_ref is exactly the complete row at that JSON pointer in THIS required view. "
    + "Use that row for every referenced lane; all constraints, evidence, ownership and order still apply. "
    + "No detail read or older baseline is needed. Missing target content requires recovery of this same capture before dependent work."});
  return {deferred, instructions, ...(references.length ? {references} : {})};
}

/** Apply only this owner's edits to a presentation. Source objects retain their
 * identity until their spine changes, allowing the IO adapter to copy exact
 * original JSON values instead of round-tripping numbers through JavaScript. */
export function applyProgressiveContextPlan(payload: JsonObject, plan: JsonObject): JsonObject {
  let result = payload;
  const edit = (path: Path, value: unknown, remove = false): void => {
    result = {...result};
    let row: Record<string | number, unknown> = result;
    for (const key of path.slice(0, -1)) {
      const child = row[key];
      row[key] = Array.isArray(child) ? [...child] : {...requireJsonObject(child, "presentation edit parent")};
      row = row[key] as Record<string | number, unknown>;
    }
    if (remove) delete row[path.at(-1)!];
    else row[path.at(-1)!] = value;
  };
  for (const value of plan.references as JsonObject[] ?? []) {
    edit(value.path as Path, value.value);
  }
  for (const value of plan.deferred as JsonObject[]) {
    edit(value.path as Path, undefined, true);
    const pointer = "/" + (value.index_path as Path ?? value.parent as Path)
      .map(key => String(key).replaceAll("~", "~0").replaceAll("/", "~1")).join("/");
    edit([...value.parent as Path, value.index_key as string], pointer);
  }
  for (const value of plan.instructions as JsonObject[]) edit(value.path as Path, value.value);
  return result;
}
