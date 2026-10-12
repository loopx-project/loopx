/** Optional stride strategy: proposes model decisions, never execution authority.
 * Configuration tokens have one shared schema. Add a strategy implementation
 * and qualify its behavior before making another version selectable.
 */
import schema from "../stride_strategy_schema.json" with {type: "json"};
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";

export function normalizeStrideStrategy(value: unknown): string {
  if (value === undefined) return schema.default;
  if (typeof value !== "string" || !schema.enum.includes(value)) {
    throw new EffectRuntimeRequestError("stride_strategy must be off or evidence_conditioned_v1");
  }
  return value;
}

export function stridePlanningGuidance(value: unknown): string[] {
  const version = normalizeStrideStrategy(value);
  if (version === "off") return [];
  if (version === "evidence_conditioned_v1") return [
    "Review the previous path decision against subsequent work and authoritative outcomes. " +
      "Adjust work-slice size, mechanism distance and validation breadth independently: a bold new " +
      "route can start with a small probe; a supported route can justify a larger coherent slice. " +
      "If local tuning leaves the goal gap unexplained, compare an alternative with continuation or replication; " +
      "do not force a pivot from elapsed time or one noisy result. Preserve the best qualified artifact. " +
      "State the next probe's distinguishing prediction, adoption criterion and bounded cost; review its " +
      "actual execution and outcome next. Use the live stride_control contract to declare a bounded " +
      "agent_vision.stride_decision; its strategy derives the three axes from the evidence basis. " +
      "Use existing Todo/path/successor boundaries without changing authority or acceptance.",
  ];
  throw new EffectRuntimeRequestError("stride strategy version has no qualified implementation");
}

// This is a model-assessed evidence basis, not a classifier of prose or scores.
// The versioned TS policy owns the three independently adjustable coordinates.
import decisionSchema from "../stride_decision_schema.json" with {type: "json"};
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";

const V1_VECTORS: Record<string, JsonObject> = {
  supported_progress: {work_slice: "grow", route_distance: "hold", validation_scope: "hold"},
  unexplained_gap: {work_slice: "shrink", route_distance: "grow", validation_scope: "shrink"},
  uncertain_evidence: {work_slice: "shrink", route_distance: "hold", validation_scope: "grow"},
  useful_new_route: {work_slice: "grow", route_distance: "hold", validation_scope: "grow"},
  decision_failure: {work_slice: "shrink", route_distance: "hold", validation_scope: "shrink"},
};

export const STRIDE_DECISION_SCHEMA = decisionSchema.schema_version;
export const STRIDE_PROBE_LIMITS = decisionSchema.probe_field_limits;

type BoundedText = (field: string, value: unknown, limit: number) => string;
function text(field: string, value: unknown, limit: number): string {
  const result = requireNonEmptyString(value, field).trim();
  if (result.length > limit) throw new EffectRuntimeRequestError(`${field} exceeds ${limit} characters`);
  return result;
}
function exactFields(value: JsonObject, fields: string[], label: string): void {
  if (Object.keys(value).some(key => !fields.includes(key))) {
    throw new EffectRuntimeRequestError(`${label} contains unsupported fields`);
  }
}

/** Normalize a declared control decision; derive the vector in the strategy
 * owner rather than trusting an arbitrary model-supplied vector. The basis and
 * predicted utility remain Agent claims, not certified evidence or authority.
 */
export function normalizeStrideDecision(value: unknown, bounded: BoundedText = text):
  [JsonObject | null, Record<string, number>] {
  if (value === undefined) return [null, {}];
  const input = requireJsonObject(value, "stride_decision");
  exactFields(input, ["schema_version", "strategy_version", "reference_context_id", "reference_obligation_id", "basis", "axes", "next_probe"], "stride_decision");
  if (input.schema_version !== decisionSchema.schema_version ||
      normalizeStrideStrategy(input.strategy_version) !== "evidence_conditioned_v1") {
    throw new EffectRuntimeRequestError("stride_decision requires stride_decision_v1 and evidence_conditioned_v1");
  }
  const context = requireNonEmptyString(input.reference_context_id, "stride_decision.reference_context_id");
  if (!/^replan-context-[a-f0-9]{24}$/.test(context)) {
    throw new EffectRuntimeRequestError("stride_decision requires the delivered replan context id");
  }
  const obligation = requireNonEmptyString(input.reference_obligation_id, "stride_decision.reference_obligation_id");
  if (!/^replan-[a-f0-9]{16}$/.test(obligation)) {
    throw new EffectRuntimeRequestError("stride_decision requires the admitted replan obligation id");
  }
  if (typeof input.basis !== "string" || !decisionSchema.basis_options.includes(input.basis)) {
    throw new EffectRuntimeRequestError("stride_decision.basis must be a registered model-assessed evidence basis");
  }
  const axes = V1_VECTORS[input.basis];
  if (!axes) throw new EffectRuntimeRequestError("stride basis has no qualified strategy implementation");
  if (input.axes !== undefined) {
    const proposed = requireJsonObject(input.axes, "stride_decision.axes");
    exactFields(proposed, decisionSchema.axis_names, "stride_decision.axes");
    if (decisionSchema.axis_names.some(axis => proposed[axis] !== axes[axis])) {
      throw new EffectRuntimeRequestError("stride_decision axes do not match the selected version and basis");
    }
  }
  const probe = requireJsonObject(input.next_probe, "stride_decision.next_probe");
  exactFields(probe, Object.keys(decisionSchema.probe_field_limits), "stride_decision.next_probe");
  const normalized: JsonObject = {};
  const usage: Record<string, number> = {};
  for (const [field, limit] of Object.entries(decisionSchema.probe_field_limits)) {
    const label = `stride_decision.next_probe.${field}`;
    normalized[field] = bounded(label, requireNonEmptyString(probe[field], label), limit);
    usage[label] = String(normalized[field]).length;
  }
  for (const [field, entry] of Object.entries({strategy_version: input.strategy_version,
    reference_context_id: context, reference_obligation_id: obligation, basis: input.basis, ...axes})) {
    usage[`stride_decision.${decisionSchema.axis_names.includes(field) ? "axes." : ""}${field}`] = String(entry).length;
  }
  return [{schema_version: decisionSchema.schema_version, strategy_version: input.strategy_version,
    reference_context_id: context, reference_obligation_id: obligation, basis: input.basis, axes: {...axes}, next_probe: normalized}, usage];
}

export function strideControlContract(context: JsonObject, obligationId: unknown = null): JsonObject | null {
  if (normalizeStrideStrategy(context.stride_strategy) === "off") return null;
  return {schema_version: decisionSchema.schema_version, strategy_version: context.stride_strategy,
    reference_context_id: context.context_id ?? null, reference_obligation_id: obligationId,
    basis_vectors: V1_VECTORS,
    minimal_example: {schema_version: decisionSchema.schema_version, strategy_version: context.stride_strategy,
      reference_context_id: context.context_id ?? null, reference_obligation_id: obligationId,
      basis: "uncertain_evidence", next_probe: {scope: "One discriminating probe.",
        prediction: "Distinguish the remaining explanations.", adoption_criterion: "Applicable evidence supports the next slice.",
        cost_bound: "One bounded comparison before another review."}},
    probe_field_limits: decisionSchema.probe_field_limits,
    attachment: "agent_vision.stride_decision", requires_structured_fresh_path: true,
    rule: "Enabled fresh Vision path reviews require this structured proposal. Other source-specific exits remain unchanged. " +
      "Assess the evidence basis using the delivered context. The strategy derives the axes; " +
      "declare the next probe's scope, prediction, adoption criterion and cost bound. " +
      "The declaration is a structured control proposal, not execution permission or proof of utility. " +
      "Todo/Next Action governs admitted work; review actual execution and results before expanding.",
  };
}
