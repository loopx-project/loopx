/** Scoped per-Turn goal acceptance and lean routine settlement projections.
 *
 * Ordinary routine execution Turns keep full acceptance bodies only for
 * Turn-relevant outcomes (Todo linkage, active/unmet owned outcomes, open
 * gaps, or ambiguous/fail-closed linkage) and represent remaining historical or
 * other-agent outcomes as compact index entries with an explicit cold-read
 * command (`loopx goal-acceptance inspect`). Routine `durable_writeback` steps
 * keep the canonical complete `vision_authoring` contract intact in the
 * decision (trimmed only in progressive first-read opening groups) while
 * compacting non-triggered optional explore attachment prose into explicit
 * `cold_read_command` hints (`explore turn-context`) and preserving complete
 * optional attachments inside `deferred_settlement_details` for captured turns.
 */
import {isDeepStrictEqual} from "node:util";
import {exploreResultWritebackAffordance} from "../capabilities/explore_context_delivery.ts";
import type {JsonObject} from "../effect_program.ts";
import {OUTCOME_CONTEXT_NAVIGATION_SCHEMA} from "../goals/outcome_requirements.ts";
import {jsonObject} from "../runtime_decode.ts";
import {EffectiveAction} from "./effective_action.generated.ts";
import {canonicalHash, shellQuote} from "./turn_envelope_codec.ts";

const object = (value: unknown): JsonObject => jsonObject(value) ?? {};

export const SCOPED_OUTCOME_THRESHOLD = 6;
export const SCOPED_OUTCOME_PROJECTION_MODE = "turn_scoped_v0";

const UNVERIFIED_OR_FAILING_STATUSES = new Set(["failed", "stale", "unverified"]);
const EXPLORE_STATIC_GRAPH_KEYS = [
  ["result_attachment_template", "attachment_template"],
  ["path_delta_attachment_template", "path_delta_attachment_template"],
  ["linked_question_attachment_template", "linked_question_attachment_template"],
  ["optional_question_evolution_example", "optional_question_evolution_example"],
  ["optional_measurement_example", "optional_measurement_example"],
  ["optional_execution_observation_example", "optional_execution_observation_example"],
] as const;

function nonEmptyString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && item.length > 0)
    : [];
}

export type ColdReadSourceOptions = {
  cliArgv?: readonly string[];
  captureReferenceFile?: string;
  registry?: unknown;
  runtimeRoot?: unknown;
};

export type CompactSettlementOptions = ColdReadSourceOptions;
export type ScopeGoalAcceptanceOptions = ColdReadSourceOptions;

function goalAcceptanceColdReadCommand(options: {
  cliArgv: readonly string[];
  registry: unknown;
  runtimeRoot: unknown;
  goalId: string;
}): string {
  const prefix = cliPrefix(options.cliArgv, options.registry, options.runtimeRoot);
  return `${prefix} goal-acceptance inspect --goal-id ${shellQuote(options.goalId)}`;
}

function cliPrefix(
  cliArgv: readonly string[],
  registry: unknown,
  runtimeRoot: unknown,
): string {
  const argv = cliArgv.length > 0 ? cliArgv : ["loopx"];
  const parts = argv.map(shellQuote);
  const registryText = nonEmptyString(registry);
  const runtimeRootText = nonEmptyString(runtimeRoot);
  if (registryText) parts.push("--registry", shellQuote(registryText));
  if (runtimeRootText) parts.push("--runtime-root", shellQuote(runtimeRootText));
  parts.push("--format", "json");
  return parts.join(" ");
}

function exploreColdReadCommand(options: {
  cliArgv: readonly string[];
  registry: unknown;
  runtimeRoot: unknown;
  goalId: string;
  agentId: string;
  todoId: string | null;
}): string {
  const prefix = cliPrefix(options.cliArgv, options.registry, options.runtimeRoot);
  const parts = [
    prefix,
    "explore",
    "turn-context",
    "--goal-id", shellQuote(options.goalId),
    "--agent-id", shellQuote(options.agentId),
    ...(options.todoId ? ["--todo-id", shellQuote(options.todoId)] : []),
  ];
  return parts.join(" ");
}

function isRoutineNormalRun(payload: JsonObject): boolean {
  if (payload.effective_action !== EffectiveAction.NORMAL_RUN || payload.normal_delivery_allowed !== true) return false;
  if (payload.replan_action_packet != null || payload.autonomous_replan_obligation != null) return false;
  if (object(payload.vision_continuation_audit).required === true) return false;
  if (object(payload.goal_frontier_projection).replan_required === true) return false;
  if (payload.selected_todo_is_goal_completion === true) return false;
  if (object(payload.vision_checkpoint).required === true) return false;
  const todo = object(payload.selected_todo);
  if (todo.task_class === "goal_completion") return false;
  return true;
}

function allRequirementsSatisfied(requirements: unknown[]): boolean {
  if (requirements.length === 0) return false;
  return requirements.every(value => {
    const req = jsonObject(value);
    if (!req || req.planning_satisfied !== true) return false;
    if (typeof req.status === "string" && UNVERIFIED_OR_FAILING_STATUSES.has(req.status)) return false;
    const review = jsonObject(req.agent_review);
    if (review && (review.conclusion === "unmet" || review.conclusion === "uncertain")) return false;
    return true;
  });
}

interface InlineOutcomeOwner {
  kind: "work_context" | "planning_horizon" | "agent_todo_summary";
  sourceIndex?: number;
  contract: JsonObject;
  requirements: JsonObject;
  sourceContent?: JsonObject;
}

function findSingleInlineOutcomeOwner(payload: JsonObject): InlineOutcomeOwner | null {
  const owners: InlineOutcomeOwner[] = [];
  const register = (owner: InlineOutcomeOwner | null): void => {
    if (owner) owners.push(owner);
  };
  const extract = (
    contractValue: unknown,
    build: (contract: JsonObject, requirements: JsonObject) => InlineOutcomeOwner,
  ): InlineOutcomeOwner | null => {
    const contract = jsonObject(contractValue);
    if (!contract || Object.hasOwn(contract, "outcome_requirements_ref")) return null;
    const requirements = jsonObject(contract.outcome_requirements);
    return requirements ? build(contract, requirements) : null;
  };

  const sources = object(object(object(payload.interaction_contract).agent_channel).work_context).sources;
  if (Array.isArray(sources)) {
    sources.forEach((value, index) => {
      const source = object(value);
      const content = object(source.content);
      if (source.source !== "goal_acceptance") return;
      register(extract(content.goal_acceptance_contract, (contract, requirements) => ({
        kind: "work_context", sourceIndex: index, contract, requirements, sourceContent: content,
      })));
    });
  }
  const horizonCore = object(object(payload.planning_horizon).core_goal);
  if (!Object.hasOwn(horizonCore, "acceptance_contract_ref")) {
    register(extract(horizonCore.acceptance_contract, (contract, requirements) => ({
      kind: "planning_horizon", contract, requirements,
    })));
  }
  const summary = object(payload.agent_todo_summary);
  if (!Object.hasOwn(summary, "goal_acceptance_contract_ref")) {
    register(extract(summary.goal_acceptance_contract, (contract, requirements) => ({
      kind: "agent_todo_summary", contract, requirements,
    })));
  }
  const replanCore = object(object(object(payload.autonomous_replan_obligation).replan_context).core_goal);
  if (!Object.hasOwn(replanCore, "acceptance_contract_ref") &&
      Object.hasOwn(replanCore, "acceptance_contract") &&
      jsonObject(object(replanCore.acceptance_contract).outcome_requirements)) {
    return null;
  }
  return owners.length === 1 ? owners[0] : null;
}

function collectAcceptanceGaps(
  payload: JsonObject,
  requirements: JsonObject,
  agentId: string,
): {globalAmbiguity: boolean; gapOutcomeIds: Set<string>} {
  const gapOutcomeIds = new Set<string>();
  let globalAmbiguity = false;
  const lists = [
    requirements.acceptance_gaps,
    object(payload.goal_frontier_projection).acceptance_gaps,
  ];
  for (const list of lists) {
    if (list === undefined || list === null) continue;
    if (!Array.isArray(list)) return {globalAmbiguity: true, gapOutcomeIds};
    for (const rawGap of list) {
      const gap = jsonObject(rawGap);
      if (!gap) return {globalAmbiguity: true, gapOutcomeIds};
      const outcomeId = nonEmptyString(gap.outcome_id);
      const gapAgentId = nonEmptyString(gap.agent_id);
      if (outcomeId !== null) {
        gapOutcomeIds.add(outcomeId);
      } else if (gapAgentId === null || gapAgentId === agentId) {
        globalAmbiguity = true;
      }
    }
  }
  return {globalAmbiguity, gapOutcomeIds};
}

export function compactOutcomeIndexEntry(
  outcome: JsonObject,
  goalId: string,
  options: ScopeGoalAcceptanceOptions = {},
): JsonObject {
  const todoIds = Array.isArray(outcome.todo_ids) ? outcome.todo_ids : [];
  return {
    id: outcome.id,
    owner_agent_id: outcome.owner_agent_id ?? null,
    basis_digest: outcome.basis_digest ?? null,
    revision: outcome.revision ?? null,
    todo_count: todoIds.length,
    summary_only: true,
    cold_read_command: goalAcceptanceColdReadCommand({
      cliArgv: options.cliArgv ?? ["loopx"],
      registry: options.registry,
      runtimeRoot: options.runtimeRoot,
      goalId,
    }),
  };
}

function replaceInlineOutcomeRequirements(
  payload: JsonObject,
  owner: InlineOutcomeOwner,
  scopedRequirements: JsonObject,
): JsonObject {
  if (owner.kind === "work_context") {
    const interaction = object(payload.interaction_contract);
    const agent = object(interaction.agent_channel);
    const workContext = object(agent.work_context);
    const sources = Array.isArray(workContext.sources) ? workContext.sources : [];
    const targetIndex = owner.sourceIndex!;
    return {
      ...payload,
      interaction_contract: {
        ...interaction,
        agent_channel: {
          ...agent,
          work_context: {
            ...workContext,
            sources: sources.map((entry, idx) => {
              if (idx !== targetIndex) return entry;
              const row = object(entry);
              const content = object(row.content);
              return {
                ...row,
                content: {
                  ...content,
                  goal_acceptance_contract: {
                    ...owner.contract,
                    outcome_requirements: scopedRequirements,
                  },
                },
              };
            }),
          },
        },
      },
    };
  }
  if (owner.kind === "planning_horizon") {
    const horizon = object(payload.planning_horizon);
    const core = object(horizon.core_goal);
    return {
      ...payload,
      planning_horizon: {
        ...horizon,
        core_goal: {
          ...core,
          acceptance_contract: {
            ...owner.contract,
            outcome_requirements: scopedRequirements,
          },
        },
      },
    };
  }
  const summary = object(payload.agent_todo_summary);
  return {
    ...payload,
    agent_todo_summary: {
      ...summary,
      goal_acceptance_contract: {
        ...owner.contract,
        outcome_requirements: scopedRequirements,
      },
    },
  };
}

export function scopeTurnGoalAcceptance(
  payload: JsonObject,
  options: ScopeGoalAcceptanceOptions = {},
): JsonObject {
  if (!isRoutineNormalRun(payload)) return payload;
  const goalId = nonEmptyString(payload.goal_id);
  const agentId = nonEmptyString(object(payload.agent_identity).agent_id ?? payload.agent_id);
  const selectedTodo = jsonObject(payload.selected_todo);
  const todoId = nonEmptyString(selectedTodo?.todo_id);
  if (!goalId || !agentId || !selectedTodo || !todoId) return payload;

  const owner = findSingleInlineOutcomeOwner(payload);
  if (!owner) return payload;
  const {contract, requirements, sourceContent} = owner;
  if (requirements.projection_mode === SCOPED_OUTCOME_PROJECTION_MODE) return payload;
  if (requirements.current_lifetime !== true) return payload;
  const outcomes = requirements.outcomes;
  if (!Array.isArray(outcomes) || outcomes.length <= SCOPED_OUTCOME_THRESHOLD) return payload;

  const contractScope = jsonObject(contract.scope);
  if (contractScope?.kind === "all_advancement") return payload;
  if (Array.isArray(contract.criteria) && contract.criteria.length > 0) return payload;

  if (sourceContent) {
    if (sourceContent.read_complete === false || sourceContent.legacy_fallback_used === true) return payload;
    if (sourceContent.status !== undefined && sourceContent.status !== "loaded") return payload;
  }

  const outcomeContext = jsonObject(sourceContent?.outcome_context);
  let suggestedComplete: Set<string> | null = null;
  if (outcomeContext !== null) {
    if (outcomeContext.schema_version !== OUTCOME_CONTEXT_NAVIGATION_SCHEMA) return payload;
    const limitations = stringList(outcomeContext.limitations);
    if (limitations.includes("goal_wide") || limitations.includes("lifetime") || limitations.includes("missing_relation")) {
      return payload;
    }
    if (outcomeContext.coverage_complete === false) {
      if (limitations.length === 0 || !limitations.every(item => item === "unbound_work")) return payload;
    } else if (outcomeContext.coverage_complete === true) {
      if (!Array.isArray(outcomeContext.suggested_outcome_ids)) return payload;
      suggestedComplete = new Set(stringList(outcomeContext.suggested_outcome_ids));
    } else {
      return payload;
    }
  }

  const gaps = collectAcceptanceGaps(payload, requirements, agentId);
  if (gaps.globalAmbiguity) return payload;

  const relevantTodoIds = new Set<string>([
    todoId,
    ...stringList(selectedTodo.dependency_todo_ids),
    ...stringList(selectedTodo.successor_todo_ids),
    ...(nonEmptyString(selectedTodo.unblocks_todo_id) ? [nonEmptyString(selectedTodo.unblocks_todo_id)!] : []),
  ]);

  const cliArgv = options.cliArgv ?? ["loopx"];
  const registry = options.registry ?? payload.registry;
  const runtimeRoot = options.runtimeRoot ?? payload.runtime_root;
  const coldReadOptions: ScopeGoalAcceptanceOptions = {cliArgv, registry, runtimeRoot};
  const coldReadCommand = goalAcceptanceColdReadCommand({
    cliArgv,
    registry,
    runtimeRoot,
    goalId,
  });

  let compactedCount = 0;
  const scopedOutcomes: unknown[] = [];
  for (const rawOutcome of outcomes) {
    const outcome = jsonObject(rawOutcome);
    if (!outcome) return payload;
    const outcomeId = nonEmptyString(outcome.id);
    const todoIds = outcome.todo_ids;
    const reqs = outcome.requirements;
    const ownerAgentId = nonEmptyString(outcome.owner_agent_id);
    const declOwnerId = nonEmptyString(outcome.declaration_owner_agent_id);

    const ambiguous = !outcomeId
      || !Array.isArray(todoIds)
      || todoIds.some(item => nonEmptyString(item) === null)
      || !Array.isArray(reqs)
      || (ownerAgentId === null && todoIds.length === 0);

    const linkedToTodo = Array.isArray(todoIds) && todoIds.some(item => typeof item === "string" && relevantTodoIds.has(item));
    const linkedToGap = outcomeId !== null && gaps.gapOutcomeIds.has(outcomeId);
    const suggested = outcomeId !== null && suggestedComplete !== null && suggestedComplete.has(outcomeId);
    const ownedRelevant = (ownerAgentId === agentId || declOwnerId === agentId)
      && (Array.isArray(todoIds) && todoIds.length === 0
        || !Array.isArray(reqs)
        || !allRequirementsSatisfied(reqs)
        || suggestedComplete === null);

    if (ambiguous || linkedToTodo || linkedToGap || suggested || ownedRelevant) {
      scopedOutcomes.push(rawOutcome);
    } else {
      compactedCount += 1;
      scopedOutcomes.push(compactOutcomeIndexEntry(outcome, goalId, coldReadOptions));
    }
  }

  if (compactedCount === 0) return payload;

  const fullDigest = canonicalHash(outcomes);
  const scopedRequirements: JsonObject = {
    ...requirements,
    projection_mode: SCOPED_OUTCOME_PROJECTION_MODE,
    full_outcome_count: outcomes.length,
    inline_outcome_count: outcomes.length - compactedCount,
    compacted_outcome_count: compactedCount,
    outcome_revision: requirements.revision ?? null,
    full_outcomes_sha256: fullDigest,
    cold_read_command: coldReadCommand,
    cold_read_instruction:
      "cold_read_command queries current live Goal acceptance in the bound workspace registry and runtime root (check outcome_revision/full_outcomes_sha256); use the original capture or --include-detail goal_acceptance for immutable Turn-baseline readback.",
    outcomes: scopedOutcomes,
  };
  return replaceInlineOutcomeRequirements(payload, owner, scopedRequirements);
}

function compactExploreGraphBoilerplate(
  sources: unknown[],
  affordance: JsonObject,
  coldReadCommand: string,
): {sources: unknown[]; changed: boolean} {
  let changed = false;
  const next = sources.map(value => {
    const item = jsonObject(value);
    if (!item || item.source !== "turn_start_capability_hook"
        || item.hook_id !== "explore.turn_context" || item.capability_id !== "explore") {
      return value;
    }
    const content = jsonObject(item.content);
    const graph = jsonObject(content?.graph);
    if (!content || !graph || graph.result_writeback_guidance !== affordance.guidance) {
      return value;
    }
    const nextGraph: JsonObject = {...graph};
    delete nextGraph.result_writeback_guidance;
    for (const [graphKey, affordanceKey] of EXPLORE_STATIC_GRAPH_KEYS) {
      if (Object.hasOwn(nextGraph, graphKey) && isDeepStrictEqual(nextGraph[graphKey], affordance[affordanceKey])) {
        delete nextGraph[graphKey];
      }
    }
    nextGraph.result_writeback_guidance_summary_only = true;
    nextGraph.result_writeback_guidance_cold_read_command = coldReadCommand;
    changed = true;
    return {...item, content: {...content, graph: nextGraph}};
  });
  return {sources: next, changed};
}

export function compactRoutineSettlementPlan(
  payload: JsonObject,
  options: CompactSettlementOptions = {},
): JsonObject {
  if (!isRoutineNormalRun(payload)) return payload;
  const frontier = object(payload.goal_frontier_projection);
  if (Array.isArray(frontier.acceptance_gaps) && frontier.acceptance_gaps.length > 0) return payload;

  const interaction = jsonObject(payload.interaction_contract);
  const cli = jsonObject(interaction?.cli_channel);
  const plan = jsonObject(cli?.settlement_plan);
  if (!interaction || !cli || !plan || !Array.isArray(plan.ordered_steps)) return payload;

  const identity = object(plan.identity);
  const isTodoBound = identity.binding_kind === "todo"
    || (identity.binding_kind === undefined && nonEmptyString(identity.todo_id) !== null && identity.replan_obligation_id == null);
  if (!isTodoBound) return payload;
  const goalId = nonEmptyString(payload.goal_id ?? identity.goal_id);
  const agentId = nonEmptyString(object(payload.agent_identity).agent_id ?? payload.agent_id ?? identity.agent_id);
  const todoId = nonEmptyString(object(payload.selected_todo).todo_id ?? identity.todo_id);
  if (!goalId || !agentId) return payload;

  const requiredCaps = new Set(stringList(object(payload.selected_todo).required_capabilities));
  const exploreEnabled = requiredCaps.has("explore");
  const knownExplore = exploreResultWritebackAffordance();
  const cliArgv = options.cliArgv ?? ["loopx"];
  const captureReferenceFile = nonEmptyString(options.captureReferenceFile);
  const exploreColdRead = exploreColdReadCommand({
    cliArgv,
    registry: options.registry ?? payload.registry,
    runtimeRoot: options.runtimeRoot ?? payload.runtime_root,
    goalId,
    agentId,
    todoId,
  });
  let deferredOptionalAttachments: unknown[] | null = null;

  let planChanged = false;
  let compactedExploreAttachment = false;
  const nextSteps = plan.ordered_steps.map(rawStep => {
    const step = jsonObject(rawStep);
    if (!step || step.kind !== "durable_writeback") return rawStep;

    let stepUpdated = false;
    let nextStep: JsonObject = {...step};

    if (!exploreEnabled && Array.isArray(step.optional_attachments)) {
      let attachmentsChanged = false;
      const nextAttachments = step.optional_attachments.map(rawAtt => {
        const att = jsonObject(rawAtt);
        if (!att || att.required !== false || att.capability_id !== "explore") return rawAtt;
        if (!isDeepStrictEqual(att, knownExplore)) return rawAtt;
        attachmentsChanged = true;
        compactedExploreAttachment = true;
        return {
          capability_id: att.capability_id,
          option: att.option,
          inline_option: att.inline_option,
          inline_field: att.inline_field,
          attachment_schema: att.attachment_schema,
          required: false,
          summary_only: true,
          cold_read_command: exploreColdRead,
        };
      });
      if (attachmentsChanged) {
        deferredOptionalAttachments = step.optional_attachments;
        nextStep.optional_attachments = nextAttachments;
        stepUpdated = true;
      }
    }

    if (stepUpdated) {
      planChanged = true;
      return nextStep;
    }
    return rawStep;
  });

  if (!planChanged) return payload;

  let nextInteraction: JsonObject = {
    ...interaction,
    cli_channel: {
      ...cli,
      settlement_plan: {
        ...plan,
        ordered_steps: nextSteps,
      },
    },
  };

  if (compactedExploreAttachment) {
    const agent = jsonObject(interaction.agent_channel);
    const workContext = jsonObject(agent?.work_context);
    if (agent && workContext && Array.isArray(workContext.sources)) {
      const updated = compactExploreGraphBoilerplate(workContext.sources, knownExplore, exploreColdRead);
      if (updated.changed) {
        nextInteraction = {
          ...nextInteraction,
          agent_channel: {
            ...agent,
            work_context: {
              ...workContext,
              sources: updated.sources,
            },
          },
        };
      }
    }
  }

  let deferredDetails: JsonObject | null = null;
  if (captureReferenceFile && deferredOptionalAttachments !== null) {
    deferredDetails = {optional_attachments: deferredOptionalAttachments};
  }

  return {
    ...payload,
    interaction_contract: nextInteraction,
    ...(deferredDetails !== null ? {deferred_settlement_details: deferredDetails} : {}),
  };
}
