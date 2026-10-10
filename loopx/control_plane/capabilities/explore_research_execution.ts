/** Explore execution attribution. This records evidence, never work authority. */
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {normalizeResearchObservation, researchCompositionGaps, researchIdentifier, researchDigest,
  validateResearchAttribution} from "./explore_research.ts";
import {evaluateTodoResumeConditions, TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION,
  resumeConditionHasKnownPendingTarget} from "../todos/resume_condition.ts";

const object = (value: unknown): JsonObject => value && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : {};
const array = (value: unknown): JsonObject[] => Array.isArray(value) ? value.map(row => requireJsonObject(row, "research row")) : [];
const same = (left: unknown, right: unknown) => JSON.stringify(left) === JSON.stringify(right);
type CompositionFrontierState = "disabled" | "pending" | "scheduled" | "observed" | "dismissed" | "deferred" | "ineligible" | "empty";

export function normalizeResearchCompositionPolicy(params: JsonObject): JsonObject {
  const harness = requireJsonObject(params.harness, "Explore harness");
  const mode = harness.composition_mode ?? "disabled";
  if (!["disabled", "explicit_only"].includes(String(mode))) {
    throw new EffectRuntimeRequestError("composition_mode must be disabled or explicit_only");
  }
  const scope = harness.composition_scope_id == null || harness.composition_scope_id === "" ? null
    : researchIdentifier(harness.composition_scope_id, "composition_scope_id");
  if (mode === "explicit_only" && scope === null) {
    throw new EffectRuntimeRequestError("explicit_only composition requires composition_scope_id");
  }
  return {schema_version: "research_composition_policy_v0", mode, coverage_scope_id: scope,
    enabled: harness.enabled === true && mode === "explicit_only"};
}

function boundTodo(todo: JsonObject, gap: JsonObject, obligationId: unknown, agent: unknown,
  retainedHistory = false): boolean {
  const refs = todo.explore_result_node_refs;
  const completed = retainedHistory && todo.status === "done";
  return (todo.claimed_by === agent || completed && todo.claimed_by == null) && todo.replan_obligation_id === obligationId
    && (!todo.role || todo.role === "agent")
    && todo.task_class === "advancement_task" && todo.action_kind === "joint_probe"
    && (completed || todo.archive_state !== "archive") && Array.isArray(refs) && refs.length === 1
    && (gap.experiment_node_ids as string[]).includes(String(refs[0]))
    && (!todo.target_key || todo.target_key === refs[0])
    && !(Array.isArray(todo.excluded_agents) && todo.excluded_agents.includes(agent));
}

/** Internal unbounded facts for obligation identity transport. Never an extra
 * public list: CLI/status receive only the compact frontier below. */
export function researchCompositionFacts(params: JsonObject): JsonObject {
  const policy = normalizeResearchCompositionPolicy(params);
  if (!policy.enabled) return {policy, gaps: []};
  const gaps = researchCompositionGaps(params).map(gap => ({...gap,
    frontier_revision: `research-composition-v0:${researchDigest([policy, gap.gap_id, gap.input_observations])}`}));
  return {policy, gaps};
}

/** Join current evidence and canonical Todo facts. Only exact lineage can
 * schedule or observe a gap; historical diagnostic observations stay cold. */
export function projectResearchComposition(params: JsonObject): JsonObject {
  const {policy, gaps: raw} = researchCompositionFacts(params);
  const gaps = raw as JsonObject[], todos = array(params.todos), bindings = array(params.bindings);
  const nodes = array(params.nodes);
  const nodeById = new Map(nodes.map(node => [node.node_id, node]));
  const todoById = new Map(todos.map(todo => [todo.todo_id, todo]));
  if (todoById.size !== todos.length || nodeById.size !== nodes.length) {
    throw new EffectRuntimeRequestError("canonical research node and Todo identities must be unique");
  }
  const todosByObligation = new Map<unknown, JsonObject[]>();
  for (const todo of todos) {
    // A terminal record may clear its claim. Its accepted observation retains
    // the actor; this historical join cannot grant runnable work authority.
    if (todo.claimed_by !== params.agent_id && !(todo.status === "done" && todo.claimed_by == null)) continue;
    const owned = todosByObligation.get(todo.replan_obligation_id) ?? [];
    owned.push(todo); todosByObligation.set(todo.replan_obligation_id, owned);
  }
  const bindingByGap = new Map(bindings.map(binding => [binding.gap_id, requireJsonObject(binding.obligation, "composition obligation")]));
  const resumeByTodo = new Map(array((policy as JsonObject).enabled ? evaluateTodoResumeConditions({
    schema_version: TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION,
    items: todos, source_items: todos, kinds: ["todo_done"], rollout_events: [],
  }).conditions : []).map(row => [row.todo_id, object(row.condition)]));
  const projected: JsonObject[] = [];
  for (const gap of gaps) {
    const obligation = bindingByGap.get(gap.gap_id);
    if (!obligation || !/^replan-[a-f0-9]{16}$/.test(String(obligation.obligation_id))) {
      throw new EffectRuntimeRequestError("composition gap requires its common obligation identity");
    }
    const matched = (todosByObligation.get(obligation.obligation_id) ?? [])
      .filter(todo => boundTodo(todo, gap, obligation.obligation_id, params.agent_id, true));
    const results = (gap.experiment_node_ids as string[]).map(id => nodeById.get(id))
      .flatMap(node => {
        if (!node?.research_observation || gap.state === "ineligible") return [];
        const observation = normalizeResearchObservation({observation: node.research_observation});
        const lineage = object(observation.execution_lineage), progress = object(observation.progress);
        const todo = todoById.get(lineage.successor_todo_id);
        const resolution = object(observation.composition_resolution);
        const attributable = !!todo && matched.includes(todo)
          && (["open", "claimed", "done"].includes(String(todo.status))
            || resolution.disposition === "deferred" && ["blocked", "deferred"].includes(String(todo.status)))
          && lineage.goal_id === params.goal_id && lineage.agent_id === params.agent_id && lineage.gap_id === gap.gap_id
          && lineage.replan_obligation_id === obligation.obligation_id
          && progress.work_item_id === todo.todo_id && progress.coverage_scope_id === (policy as JsonObject).coverage_scope_id
          && same(observation.input_observations, gap.input_observations);
        return attributable ? [{node, observation, progress, todo, resolution}] : [];
      });
    const observed = results.find(result => ["resolved", "dead_end"].includes(String(result.node.status))
      && !result.resolution.disposition && ["exploration_exhausted", "no_followup"].includes(String(result.progress.result_class)));
    const dismissed = results.find(result => result.node.status === "dead_end" && result.resolution.disposition === "dismissed");
    const deferred = results.find(result => {
      if (result.node.status !== "blocked" || result.resolution.disposition !== "deferred"
        || !["blocked", "deferred"].includes(String(result.todo.status))) return false;
      const blocker = todoById.get(result.progress.blocker_id), condition = resumeByTodo.get(result.todo.todo_id);
      return !!blocker && blocker.role === "agent" && blocker.task_class === "blocker"
        && blocker.claimed_by === params.agent_id && blocker.archive_state !== "archive"
        && ["open", "deferred"].includes(String(blocker.status)) && blocker.unblocks_todo_id === result.todo.todo_id
        && !!condition && condition.kind === "todo_done" && condition.target_todo_id === blocker.todo_id
        && resumeConditionHasKnownPendingTarget(condition, result.todo);
    });
    const successor = gap.state !== "ineligible" ? matched.find(todo => todo.actionable_open === true
      && boundTodo(todo, gap, obligation.obligation_id, params.agent_id) && ["open", "claimed"].includes(String(todo.status))
      && ["open", "exploring"].includes(String(nodeById.get((todo.explore_result_node_refs as string[])[0])?.status))) : undefined;
    const experiment = successor ? (successor.explore_result_node_refs as string[])[0]
      : (gap.active_experiment_node_ids as string[])[0] ?? null;
    projected.push({...gap, status: gap.state === "ineligible" ? "ineligible" : observed ? "observed" : dismissed ? "dismissed"
      : successor ? "scheduled" : deferred ? "deferred" : "pending",
      obligation_id: obligation.obligation_id, experiment_node_ref: experiment,
      input_node_refs: gap.input_node_ids, required_outcome: "typed_joint_experiment_result",
      successor_todo_id: successor?.todo_id ?? null,
      observed_todo_id: observed?.todo?.todo_id ?? null,
      observed_progress_fingerprint: observed?.progress.fingerprint ?? null,
      dismissed_todo_id: dismissed?.todo.todo_id ?? null,
      deferred_todo_id: deferred?.todo.todo_id ?? null,
      blocker_todo_id: deferred?.progress.blocker_id ?? null,
      resume_when: deferred?.todo.resume_when ?? null,
      execution_results: results.map(result => ({todo_id: result.todo?.todo_id, experiment_node_id: result.node.node_id,
        observation_fingerprint: result.observation.fingerprint, progress_fingerprint: result.progress.fingerprint,
        progress: result.progress,
        result_class: result.progress.result_class, blocker_id: result.progress.blocker_id ?? null,
        disposition: result.resolution.disposition ?? null,
        evidence_ids: result.progress.evidence_ids})),
      successor_summary: experiment ? `Run the bounded joint experiment: ${experiment}` : "Create one binary experiment for this explicit input set, then bind a runnable Todo.",
      successor_binding: experiment ? {action_kind: "joint_probe", task_domain: "research", target_key: experiment,
        explore_result_node_refs: [experiment]} : null});
  }
  projected.sort((a, b) => Number(a.status !== "pending") - Number(b.status !== "pending")
    || String(a.gap_id).localeCompare(String(b.gap_id), "en"));
  const selected = projected.find(gap => gap.status === "pending") ?? null;
  const state: CompositionFrontierState = !(policy as JsonObject).enabled ? "disabled" : selected ? "pending"
    : projected.some(gap => gap.status === "scheduled") ? "scheduled"
    : projected.some(gap => gap.status === "deferred") ? "deferred"
    : projected.some(gap => gap.status === "ineligible") ? "ineligible"
    : projected.some(gap => gap.status === "observed") ? "observed" : projected.length ? "dismissed" : "empty";
  return {schema_version: "research_composition_frontier_v0", goal_id: params.goal_id, agent_id: params.agent_id, policy,
    grants_execution_authority: false,
    enabled: (policy as JsonObject).enabled, state,
    candidate_count: gaps.length, pending_count: projected.filter(gap => gap.status === "pending").length,
    scheduled_count: projected.filter(gap => gap.status === "scheduled").length,
    observed_count: projected.filter(gap => gap.status === "observed").length,
    dismissed_count: projected.filter(gap => gap.status === "dismissed").length,
    deferred_count: projected.filter(gap => gap.status === "deferred").length,
    ineligible_count: projected.filter(gap => gap.status === "ineligible").length,
    omitted_count: Math.max(0, projected.length - 3), gaps: projected.slice(0, 3), selected_gap: selected,
    // This internal lane is removed by the transport before public projection.
    lineage_gaps: projected,
    obligation_tasks: todos.filter(todo => todo.replan_obligation_id)
      .map(todo => ({obligation_id: todo.replan_obligation_id, todo_id: todo.todo_id,
        status: todo.status, archive_state: todo.archive_state ?? "active"}))};
}

function retirementContract(frontier: JsonObject, guard: JsonObject, obligation: unknown): JsonObject | null {
  if (frontier.schema_version !== "research_composition_frontier_v0"
    || guard.schema_version !== "semantic_replan_capability_guard_v0" || guard.capability_id !== "explore") return null;
  const current = array(frontier.lineage_gaps).find(row => row.gap_id === guard.gap_id);
  if (frontier.enabled === true && (!current || current.frontier_revision === guard.frontier_revision
    && current.status !== "ineligible")) return null;
  const blocking = array(frontier.obligation_tasks).filter(todo => todo.obligation_id === obligation
    && todo.archive_state !== "archive" && ["open", "claimed"].includes(String(todo.status)));
  const revision = `capability-evidence-v0:${researchDigest([frontier.policy, current ?? null])}`;
  const blocker = `capability-invalidated-${researchDigest([guard, revision])}`;
  return {schema_version: "capability_obligation_retirement_v0", disposition: "invalidated",
    reason_code: frontier.enabled !== true ? "source_disabled" : current?.status === "ineligible"
      ? "source_ineligible" : "source_revision_changed",
    capability_id: "explore", obligation_id: obligation, original_guard: guard, current_revision: revision,
    blocking_todo_ids: blocking.slice(0, 3).map(todo => todo.todo_id), blocking_todo_count: blocking.length,
    progress_observation: {schema_version: "typed_progress_observation_v0", result_class: "blocked",
      work_item_id: obligation, blocker_id: blocker, evidence_ids: [revision]}};
}

/** Qualify the exact research duty selected at admission. Generic progress or
 * a vision/read/ACK cannot impersonate the canonical experiment observation. */
export function qualifyResearchCompositionWriteback(params: JsonObject): JsonObject {
  const frontier = requireJsonObject(params.frontier, "live research frontier");
  const guard = requireJsonObject(params.capability_guard, "selected capability guard");
  const gap = array(frontier.lineage_gaps).find(row => row.gap_id === guard.gap_id
    && row.frontier_revision === guard.frontier_revision && row.obligation_id === params.obligation_id);
  const observation = object(params.progress_observation);
  const progressFingerprint = observation.fingerprint;
  const repeated = Array.isArray(params.claimed_progress_fingerprints) && params.claimed_progress_fingerprints.includes(progressFingerprint);
  let outcome: string | null = null, capabilityOutcome: string | null = null;
  let result: JsonObject | undefined;
  const retirement = gap && gap.status !== "ineligible" ? null : retirementContract(frontier, guard, params.obligation_id);
  let retired = false;
  if (retirement && retirement.blocking_todo_count === 0 && typeof progressFingerprint === "string") {
    const claimed = {...observation}; delete claimed.fingerprint;
    if (researchDigest(claimed) === researchDigest(retirement.progress_observation)) {
      outcome = "capability_duty_retired"; capabilityOutcome = "composition_duty_invalidated"; retired = true;
    }
  }
  if (guard.capability_id === "explore" && frontier.enabled === true && gap) {
    if (gap.status === "scheduled" && gap.successor_todo_id) {
      outcome = "new_runnable_successor"; capabilityOutcome = "new_runnable_composition_experiment";
    } else if (!repeated && typeof progressFingerprint === "string") {
      result = array(gap.execution_results).find(row => row.progress_fingerprint === progressFingerprint
        && row.todo_id === observation.work_item_id && researchDigest(row.progress) === researchDigest(observation));
      if (result && gap.status === "observed" && result.todo_id === gap.observed_todo_id
        && progressFingerprint === gap.observed_progress_fingerprint) {
        outcome = "capability_evidence_observed"; capabilityOutcome = "composition_experiment_observed";
      } else if (result && gap.status === "dismissed" && result.todo_id === gap.dismissed_todo_id
        && result.disposition === "dismissed") {
        outcome = "capability_evidence_observed"; capabilityOutcome = "composition_candidate_dismissed";
      } else if (result && gap.status === "deferred" && result.todo_id === gap.deferred_todo_id
        && result.disposition === "deferred" && result.blocker_id === gap.blocker_todo_id
        && !(Array.isArray(params.claimed_blocker_ids) && params.claimed_blocker_ids.includes(result.blocker_id))) {
        outcome = "new_concrete_blocker"; capabilityOutcome = "composition_temporarily_deferred";
      }
    }
  }
  return {schema_version: "replan_semantic_delta_v0", obligation_id: params.obligation_id,
    accepted: outcome !== null, outcomes: outcome ? [outcome] : [], satisfying_outcomes: outcome ? [outcome] : [],
    required_any_of: ["new_runnable_successor", "capability_evidence_observed", "new_concrete_blocker", "capability_duty_retired"],
    capability_guard: guard, capability_outcome: capabilityOutcome,
    ...(retirement ? {retirement_contract: retirement} : {}),
    ...(retired ? {retirement: {...retirement, progress_fingerprint: progressFingerprint}} : {}),
    ...(gap?.successor_todo_id ? {successor_todo_id: gap.successor_todo_id} : {}),
    observation_fingerprint: result?.observation_fingerprint ?? null,
    reason_code: outcome ? "research_semantic_delta_accepted" : !gap || frontier.enabled !== true
      ? "research_frontier_invalidated" : repeated ? "research_result_replayed" : "research_result_required",
    reason: outcome ? "the selected evidence duty has an exact canonical transition"
      : "the selected evidence duty requires a current bound experiment result or runnable successor; reads, ACKs, stale inputs and unrelated progress cannot settle it"};
}

export function validateResearchCompositionSuccessor(params: JsonObject): JsonObject {
  const frontier = requireJsonObject(params.frontier, "live research frontier"), todo = requireJsonObject(params.todo, "successor intent");
  const gap = object(frontier.selected_gap);
  const refs = todo.explore_result_node_refs;
  const accepted = frontier.enabled === true && gap.status === "pending"
    && todo.replan_obligation_id === gap.obligation_id && boundTodo(todo, gap, gap.obligation_id, params.agent_id)
    && ["open", "claimed"].includes(String(todo.status)) && !todo.resume_when
    && Array.isArray(refs) && (gap.active_experiment_node_ids as string[]).includes(String(refs[0]));
  if (!accepted) throw new EffectRuntimeRequestError(
    `research successor ${gap.obligation_id ?? params.obligation_id}: bind one current binary experiment with joint_probe and no deferral; read Explore summary before todo add`);
  return {accepted: true, gap_id: gap.gap_id, obligation_id: gap.obligation_id};
}

/** A completion guard is eligibility evidence, never a replacement for actor,
 * lease or CAS admission. Historical terminal replays do not execute new work. */
export function qualifyResearchCompletion(params: JsonObject): JsonObject {
  const frontier = requireJsonObject(params.frontier, "research completion frontier");
  const todo = requireJsonObject(params.todo, "completion Todo");
  const gaps = array(frontier.lineage_gaps);
  const required = frontier.enabled === true && todo.status !== "done" && todo.role !== "user"
    && (todo.action_kind === "joint_probe" || todo.capability_binding_ref === "explore:research-composition-v0"
      || gaps.some(gap => gap.obligation_id === todo.replan_obligation_id));
  if (!required) return {required: false, allowed: true, evidence: null};
  const actor = params.actor_agent_id ?? todo.claimed_by;
  for (const gap of gaps) {
    if (!["observed", "dismissed"].includes(String(gap.status)) || !boundTodo(todo, gap, gap.obligation_id, actor)) continue;
    const result = array(gap.execution_results).find(row => row.todo_id === todo.todo_id
      && ["exploration_exhausted", "no_followup"].includes(String(row.result_class))
      && (todo.explore_result_node_refs as string[]).includes(String(row.experiment_node_id)));
    if (!result) continue;
    return {required: true, allowed: true, evidence: {
      schema_version: "capability_completion_evidence_v0", capability_id: "explore", goal_id: params.goal_id,
      todo_id: todo.todo_id, agent_id: actor, gap_id: gap.gap_id, obligation_id: gap.obligation_id,
      frontier_revision: gap.frontier_revision, experiment_node_id: result.experiment_node_id,
      input_observations: gap.input_observations, observation_fingerprint: result.observation_fingerprint,
      progress_fingerprint: result.progress_fingerprint, evidence_ids: result.evidence_ids,
      disposition: result.disposition === "dismissed" ? "candidate_dismissed" : "experiment_observed",
    }};
  }
  return {required: true, allowed: false, reason_code: "research_experiment_result_required",
    reason: "This Todo requires its current typed experiment observation with exact task/input lineage before closeout. Read Explore summary and record the result through explore observe.",
    evidence: null};
}

/** The transport supplies a fresh canonical Todo snapshot and its existing
 * actionable-open decision. Historical replay bypasses this gate without
 * rewriting evidence; any later settlement must revalidate its own authority.
 */
export function validateResearchExecution(params: JsonObject): JsonObject {
  const observation = validateResearchAttribution(params);
  const lineage = requireJsonObject(observation.execution_lineage, "execution_lineage");
  const todo = requireJsonObject(params.todo, "canonical execution Todo");
  const node = (params.nodes as JsonObject[]).find(row => row.node_id === observation.explore_node_id);
  const reject = (reason: string): never => {
    throw new EffectRuntimeRequestError(`research execution ${lineage.replan_obligation_id}: ${reason}; read the current Todo and Explore summary before explore observe`);
  };
  const frontier = params.frontier == null ? null : requireJsonObject(params.frontier, "execution frontier");
  if (frontier && (frontier.schema_version !== "research_composition_frontier_v0"
    || frontier.goal_id !== params.goal_id || frontier.agent_id !== params.agent_id)) {
    reject("the current execution frontier must belong to this Goal and actor");
  }
  // M2 diagnostics retain their cold meaning. M3 uses the same current
  // canonical lineage join as status and closeout, including retained Todos.
  const live = frontier?.enabled === true;
  const gap = (live ? array(frontier!.lineage_gaps) : researchCompositionGaps(params))
    .find(row => row.gap_id === lineage.gap_id);
  const uncovered = live ? !!gap && ["pending", "scheduled"].includes(String(gap.status))
    && gap.obligation_id === lineage.replan_obligation_id
    && object(frontier!.policy).coverage_scope_id === object(observation.progress).coverage_scope_id
    : gap?.state === "pending";
  if (lineage.goal_id !== params.goal_id || lineage.agent_id !== params.agent_id) {
    reject("Goal or actor differs from execution lineage");
  }
  if (todo.todo_id !== lineage.successor_todo_id || todo.claimed_by !== lineage.agent_id
    || todo.replan_obligation_id !== lineage.replan_obligation_id
    || todo.task_class !== "advancement_task" || todo.action_kind !== "joint_probe"
    || (todo.role && todo.role !== "agent")
    || todo.actionable_open !== true || todo.archive_state === "archive"
    || (Array.isArray(todo.excluded_agents) && todo.excluded_agents.includes(lineage.agent_id))) {
    reject("a current same-agent runnable joint-probe Todo with the exact obligation is required");
  }
  const refs = todo.explore_result_node_refs;
  if (!Array.isArray(refs) || refs.length !== 1 || refs[0] !== observation.explore_node_id
    || (todo.target_key && todo.target_key !== observation.explore_node_id)) {
    reject("Todo must bind exactly this experiment node");
  }
  if (node?.node_kind !== "experiment" || (node.agent_id && node.agent_id !== lineage.agent_id)
    || !gap || !uncovered
    || !(gap.experiment_node_ids as string[]).includes(String(observation.explore_node_id))
    || JSON.stringify(gap.input_observations) !== JSON.stringify(observation.input_observations)) {
    reject("the experiment must cover this pending gap's current exact input observations");
  }
  const progress = observation.progress as JsonObject;
  if (progress.result_class === "unchanged" || !(progress.evidence_ids as string[]).length) {
    reject("an execution result needs typed progress and evidence; a read or ACK is insufficient");
  }
  return observation;
}
