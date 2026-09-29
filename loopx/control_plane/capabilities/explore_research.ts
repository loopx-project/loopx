/** Explore-owned research evidence. Pure read model; no settlement authority. */
import {createHash} from "node:crypto";
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireJsonObject, requireStringLiteral} from "../runtime_decode.ts";

const OBSERVATION = "typed_research_observation_v0";
const CLOSURE = "research_closure_basis_v0";
const CONSTRAINTS = ["stage", "decision", "invariant", "dependency", "resource", "policy"] as const;
const INTERACTIONS = ["shared_constraint", "producer_consumer", "state_interference", "order_dependency", "resource_coupling", "unknown_interaction"] as const;
export const MAX_RESEARCH_CANDIDATES = 3;
export const MAX_RESEARCH_GAPS = 3;
const TERMINAL = new Set(["exploration_exhausted", "no_followup"]);
const compare = (a: string, b: string) => a < b ? -1 : a > b ? 1 : 0;

function id(value: unknown, field: string): string {
  if (typeof value !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value)) {
    throw new EffectRuntimeRequestError(`${field} must be an opaque public-safe identifier`);
  }
  return value;
}
function object(value: unknown, field: string, allowed: string[]): JsonObject {
  const row = requireJsonObject(value, field);
  if (Object.keys(row).some(key => !allowed.includes(key))) {
    throw new EffectRuntimeRequestError(`${field} contains unknown fields`);
  }
  return row;
}
function list(value: unknown, field: string, maximum: number): unknown[] {
  if (!Array.isArray(value) || value.length > maximum) {
    throw new EffectRuntimeRequestError(`${field} must be an array of at most ${maximum} items`);
  }
  return value;
}
function ids(value: unknown, field: string): string[] {
  return [...new Set(list(value, field, 12).map(item => id(item, field)))].sort();
}
function rows(value: unknown, field: string): JsonObject[] {
  if (!Array.isArray(value)) throw new EffectRuntimeRequestError(`${field} must be an array`);
  return value.map(row => requireJsonObject(row, field));
}
function digest(value: unknown): string {
  const stable = (item: unknown): unknown => Array.isArray(item) ? item.map(stable)
    : item !== null && typeof item === "object" ? Object.fromEntries(Object.entries(item)
      .sort(([a], [b]) => compare(a, b)).map(([key, child]) => [key, stable(child)])) : item;
  return createHash("sha256").update(JSON.stringify(stable(value))).digest("hex").slice(0, 16);
}

export function normalizeResearchObservation(params: JsonObject): JsonObject {
  const raw = object(params.observation, "research observation",
    ["schema_version", "explore_node_id", "progress", "closure_basis", "composition_candidates", "input_observations", "fingerprint"]);
  requireStringLiteral(raw.schema_version, [OBSERVATION], "research observation schema");
  const node = id(raw.explore_node_id, "explore_node_id");
  // The Python transport composes its existing generic progress codec. This
  // owner validates research-only semantics; it does not redefine progress.
  const progress = requireJsonObject(raw.progress, "progress");
  requireStringLiteral(progress.schema_version, ["typed_progress_observation_v0"], "progress schema");
  id(progress.work_item_id, "progress.work_item_id");
  const result = requireStringLiteral(progress.result_class,
    ["advanced", "unchanged", "blocked", "exploration_exhausted", "no_followup"], "result_class");
  const evidence = ids(progress.evidence_ids ?? [], "progress.evidence_ids");
  let closure: JsonObject | null = null;
  if (raw.closure_basis !== undefined && raw.closure_basis !== null) {
    const basis = object(raw.closure_basis, "closure_basis", ["schema_version", "disposition", "constraints", "evidence_ids"]);
    requireStringLiteral(basis.schema_version, [CLOSURE], "closure schema");
    const constraints = list(basis.constraints, "closure constraints", 12).map(value => {
      const constraint = object(value, "constraint", ["kind", "id", "role"]);
      return {kind: requireStringLiteral(constraint.kind, CONSTRAINTS, "constraint kind"),
        id: id(constraint.id, "constraint id"),
        role: requireStringLiteral(constraint.role, ["decisive", "supporting"], "constraint role")};
    });
    if (new Set(constraints.map(item => `${item.kind}:${item.id}`)).size !== constraints.length) {
      throw new EffectRuntimeRequestError("closure constraints contain duplicate identities");
    }
    const basisEvidence = ids(basis.evidence_ids, "closure evidence_ids");
    if (!basisEvidence.length || basisEvidence.some(item => !evidence.includes(item))) {
      throw new EffectRuntimeRequestError("closure evidence must be attributable to progress evidence_ids");
    }
    closure = {schema_version: CLOSURE,
      disposition: requireStringLiteral(basis.disposition, ["bounded", "exhausted", "no_followup", "blocked"], "closure disposition"),
      constraints, evidence_ids: basisEvidence};
  }
  if (TERMINAL.has(result)) {
    id(progress.coverage_scope_id, "terminal coverage_scope_id");
    if (progress.coverage_complete !== true || !evidence.length || closure === null
      || !(closure.constraints as {role: string}[]).some(item => item.role === "decisive")) {
      throw new EffectRuntimeRequestError("terminal research requires complete coverage, evidence and a decisive closure basis");
    }
    if (closure.disposition === "blocked") {
      throw new EffectRuntimeRequestError("a blocked closure cannot assert terminal research coverage");
    }
  }
  const candidates = list(raw.composition_candidates ?? [], "composition_candidates", MAX_RESEARCH_CANDIDATES).map(value => {
    const candidate = object(value, "composition candidate", ["target_node_id", "basis", "interaction_kind", "evidence_ids"]);
    const target = id(candidate.target_node_id, "target_node_id");
    if (target === node) throw new EffectRuntimeRequestError("composition requires distinct input nodes");
    const refs = ids(candidate.evidence_ids, "candidate evidence_ids");
    if (!refs.length) throw new EffectRuntimeRequestError("composition candidate requires evidence_ids");
    return {target_node_id: target, basis: requireStringLiteral(candidate.basis, ["explicit"], "candidate basis"),
      interaction_kind: requireStringLiteral(candidate.interaction_kind, INTERACTIONS, "interaction_kind"), evidence_ids: refs};
  }).sort((a, b) => compare(a.target_node_id, b.target_node_id));
  if (new Set(candidates.map(item => item.target_node_id)).size !== candidates.length) {
    throw new EffectRuntimeRequestError("composition candidate target is duplicated");
  }
  const lineage = list(raw.input_observations ?? [], "input_observations", 2).map(value => {
    const input = object(value, "input observation", ["node_id", "fingerprint"]);
    return {node_id: id(input.node_id, "input node_id"), fingerprint: id(input.fingerprint, "input fingerprint")};
  }).sort((a, b) => compare(a.node_id, b.node_id));
  if (new Set(lineage.map(input => input.node_id)).size !== lineage.length || lineage.some(input => input.node_id === node)) {
    throw new EffectRuntimeRequestError("input observations must have distinct non-self node identities");
  }
  const canonical: JsonObject = {schema_version: OBSERVATION, explore_node_id: node, progress,
    ...(lineage.length ? {input_observations: lineage} : {}),
    ...(closure ? {closure_basis: closure} : {}), composition_candidates: candidates};
  return {...canonical, fingerprint: digest(canonical)};
}

function observationFor(node: JsonObject): JsonObject | null {
  if (!node.research_observation) return null;
  const observation = normalizeResearchObservation({observation: node.research_observation});
  if (observation.explore_node_id !== node.node_id) throw new EffectRuntimeRequestError("research observation belongs to a different node");
  return observation;
}
function eligible(node: JsonObject | undefined): boolean {
  if (!node || !["resolved", "dead_end"].includes(String(node.status))) return false;
  const observation = observationFor(node);
  return !!observation && TERMINAL.has(String((observation.progress as JsonObject).result_class));
}
function evidenceFor(node: JsonObject): Set<string> {
  const observation = observationFor(node);
  return new Set([...(Array.isArray(node.evidence_refs) ? node.evidence_refs as string[] : []),
    ...(observation ? (observation.progress as JsonObject).evidence_ids as string[] ?? [] : [])]);
}

/** Validate attribution at append time against the current same-goal graph. */
export function validateResearchAttribution(params: JsonObject): JsonObject {
  const observation = normalizeResearchObservation(params);
  const nodes = new Map(rows(params.nodes, "nodes").map(node => [String(node.node_id), node]));
  const source = nodes.get(String(observation.explore_node_id));
  if (!source) throw new EffectRuntimeRequestError("explore_node_id must reference an existing same-goal node");
  const lineage = observation.input_observations as JsonObject[] ?? [];
  const dependencies = [...new Set(rows(params.edges ?? [], "edges").filter(edge => edge.edge_type === "depends_on"
    && edge.from_node === source.node_id).map(edge => String(edge.to_node)))].sort();
  if (source.node_kind === "experiment" && dependencies.length >= 2) {
    if (dependencies.length !== 2 || JSON.stringify(lineage.map(input => input.node_id)) !== JSON.stringify(dependencies)
      || lineage.some(input => observationFor(nodes.get(String(input.node_id)) ?? {})?.fingerprint !== input.fingerprint)) {
      throw new EffectRuntimeRequestError("composition experiment requires the exact current input observation fingerprints");
    }
  } else if (lineage.length) {
    throw new EffectRuntimeRequestError("input observations require a binary composition experiment");
  }
  for (const candidate of observation.composition_candidates as JsonObject[]) {
    const target = nodes.get(String(candidate.target_node_id));
    if (!target) throw new EffectRuntimeRequestError("candidate target must reference an existing same-goal node");
    const sourceEvidence = new Set([...(Array.isArray(source.evidence_refs) ? source.evidence_refs as string[] : []),
      ...((observation.progress as JsonObject).evidence_ids as string[] ?? [])]);
    const targetEvidence = evidenceFor(target);
    const refs = candidate.evidence_ids as string[];
    if (!refs.some(ref => sourceEvidence.has(ref)) || !refs.some(ref => targetEvidence.has(ref))
      || refs.some(ref => !sourceEvidence.has(ref) && !targetEvidence.has(ref))) {
      throw new EffectRuntimeRequestError("candidate evidence must be attributable to both input nodes");
    }
  }
  return observation;
}

export function projectResearchFrontier(params: JsonObject): JsonObject {
  const goal = id(params.goal_id, "goal_id");
  const nodeRows = rows(params.nodes, "nodes");
  const nodes = new Map(nodeRows.map(node => [String(node.node_id), node]));
  const candidates = new Map<string, {inputs: string[]; claims: JsonObject[]}>();
  for (const node of params.candidate_sources === undefined ? nodeRows : rows(params.candidate_sources, "candidate_sources")) {
    const observation = observationFor(node);
    for (const candidate of observation?.composition_candidates as JsonObject[] ?? []) {
      const inputs = [String(node.node_id), String(candidate.target_node_id)].sort();
      const identity = digest([goal, inputs]);
      const existing = candidates.get(identity) ?? {inputs, claims: []};
      existing.claims.push({...candidate, source_node_id: node.node_id});
      candidates.set(identity, existing);
    }
  }
  const dependencies = new Map<string, Set<string>>();
  for (const edge of rows(params.edges, "edges")) {
    if (edge.edge_type !== "depends_on") continue;
    const key = String(edge.from_node);
    const inputs = dependencies.get(key) ?? new Set<string>();
    inputs.add(String(edge.to_node)); dependencies.set(key, inputs);
  }
  const experimentsByInputs = new Map<string, JsonObject[]>();
  for (const node of nodeRows) {
    if (node.node_kind !== "experiment") continue;
    const inputs = [...(dependencies.get(String(node.node_id)) ?? [])].sort();
    if (inputs.length !== 2) continue;
    const key = JSON.stringify(inputs);
    const experiments = experimentsByInputs.get(key) ?? [];
    experiments.push(node); experimentsByInputs.set(key, experiments);
  }
  const gaps: JsonObject[] = [];
  for (const [identity, candidate] of [...candidates].sort(([a], [b]) => compare(a, b))) {
    const inputs = candidate.inputs.map(input => nodes.get(input));
    const allEligible = inputs.every(eligible);
    const attributable = candidate.claims.some(claim => {
      if (inputs.some(node => !node)) return false;
      const sets = inputs.map(node => evidenceFor(node!));
      const refs = claim.evidence_ids as string[];
      return sets.every(set => refs.some(ref => set.has(ref))) && refs.every(ref => sets.some(set => set.has(ref)));
    });
    const experiments = experimentsByInputs.get(JSON.stringify(candidate.inputs)) ?? [];
    const observed = allEligible && attributable && experiments.some(experiment => {
      if (!eligible(experiment)) return false;
      const lineage = observationFor(experiment)?.input_observations as JsonObject[] ?? [];
      return JSON.stringify(lineage.map(input => input.node_id)) === JSON.stringify(candidate.inputs)
        && lineage.every(input => observationFor(nodes.get(String(input.node_id))!)?.fingerprint === input.fingerprint);
    });
    const active = experiments.filter(node => ["open", "exploring"].includes(String(node.status)));
    gaps.push({gap_id: `research-composition-${identity}`, input_node_ids: candidate.inputs,
      input_observations: inputs.filter((node): node is JsonObject => !!node).map(node => ({node_id: node.node_id,
        fingerprint: observationFor(node)?.fingerprint ?? null})),
      state: !allEligible || !attributable ? "ineligible" : observed ? "observed" : "pending",
      reason: !allEligible ? "terminal_input_observation_required" : !attributable ? "input_evidence_invalidated"
        : observed ? "typed_experiment_outcome" : "joint_experiment_result_required",
      interaction_kinds: [...new Set(candidate.claims.map(claim => String(claim.interaction_kind)))].sort(),
      experiment_node_ids: experiments.map(node => String(node.node_id)).sort(),
      active_experiment_node_ids: active.map(node => String(node.node_id)).sort()});
  }
  const count = (state: string) => gaps.filter(gap => gap.state === state).length;
  const gapsByInput = new Map<string, JsonObject[]>();
  for (const gap of gaps) for (const input of gap.input_node_ids as string[]) {
    const related = gapsByInput.get(input) ?? [];
    related.push(gap); gapsByInput.set(input, related);
  }
  const nodeSummaries = nodeRows.filter(node => observationFor(node) || gapsByInput.has(String(node.node_id)))
    .sort((a, b) => compare(String(a.node_id), String(b.node_id))).map(node => {
      const observation = observationFor(node);
      const progress = observation?.progress as JsonObject | undefined;
      const basis = observation?.closure_basis as JsonObject | undefined;
      const related = gapsByInput.get(String(node.node_id)) ?? [];
      return {node_id: node.node_id, summary: [
        progress ? `Research: ${String(progress.result_class).replaceAll("_", " ")}` : "Research: observation invalidated",
        ...(progress?.coverage_scope_id ? [`coverage ${progress.coverage_scope_id}`] : []),
        ...(basis ? [`closure ${basis.disposition}`] : []),
        ...(related.length ? [`composition ${related.filter(gap => gap.state === "pending").length} pending, ${related.filter(gap => gap.state === "ineligible").length} ineligible, ${related.filter(gap => gap.state === "observed").length} observed`] : []),
      ].join("; ")};
    });
  return {schema_version: "research_frontier_projection_v0", goal_id: goal, mode: "read_only_shadow",
    candidate_count: gaps.length, pending_count: count("pending"), observed_count: count("observed"),
    ineligible_count: count("ineligible"), projected_count: Math.min(gaps.length, MAX_RESEARCH_GAPS),
    omitted_count: Math.max(0, gaps.length - MAX_RESEARCH_GAPS), gaps: gaps.slice(0, MAX_RESEARCH_GAPS),
    grants_execution_authority: false, node_summaries: nodeSummaries};
}
