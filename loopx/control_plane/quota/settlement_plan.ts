/** CLI guidance for the existing completion -> writeback -> spend protocol.
 * This is a projection, not a validation, lease or receipt authorization. */
import {
  settlementIdentity, type JsonObject, type SettlementIdentityInput,
  SETTLEMENT_PLAN_SCHEMA_VERSION, type SettlementPlan, type SettlementStep,
} from "../effect_program.ts";
import {isDeepStrictEqual} from "node:util";
import {VISION_MATERIAL_CLOSEOUT_HINT, visionAuthoringContract} from "../goals/vision_checkpoint.ts";
import {parseExactGoalRef} from "../goals/goal_instance_identity.ts";
import {jsonObject, requireBoolean, requireJsonObject, requireNonEmptyString, requireStringArray, requireStringLiteral} from "../runtime_decode.ts";
import {MATERIAL_DELIVERY_OUTCOMES} from "../work_items/delivery_outcome.ts";
import {DELIVERY_BOUNDARIES} from "../turn_driver/delivery_continuity.ts";
import {PROGRESS_RESULT_CLASSES} from "../work_items/replan_history.ts";
import {HANDOFF_MODES} from "../coordination/handoff_mode_vocabulary.ts";
import {EXECUTION_PREPARATION_SCHEMA_VERSION} from "../work_items/execution_preparation.ts";
import {contextProjectionChanges} from "./diagnostic_projection.ts";
import {EffectiveAction} from "./effective_action.generated.ts";

export const COMPOSED_CLI_SETTLEMENT_PLAN_SCHEMA_VERSION = "quota_settlement_plan_v2";

/** A transport projection over the existing plan, never a new settlement identity. */
export function composedCliSettlementChannel(cli: JsonObject): JsonObject {
  const plan = jsonObject(cli.settlement_plan), actions = cli.next_cli_actions;
  if (plan?.schema_version !== SETTLEMENT_PLAN_SCHEMA_VERSION || !Array.isArray(plan.ordered_steps)
      || !Array.isArray(actions)) return cli;
  const steps = plan.ordered_steps.map(jsonObject);
  if (steps.some(step => !step)) return cli;
  const writebacks = steps.filter(step => step?.kind === "durable_writeback");
  const spends = steps.filter(step => step?.kind === "quota_spend");
  const [writeback] = writebacks, [spend] = spends;
  if (writebacks.length !== 1 || spends.length !== 1 || !writeback || !spend
      || steps.indexOf(writeback) >= steps.indexOf(spend)
      || writeback.owner !== "agent" || spend.owner !== "agent"
      || typeof writeback.command_template !== "string" || typeof spend.command_template !== "string"
      || typeof writeback.precondition !== "string" || typeof spend.precondition !== "string") return cli;
  const writebackCommand = writeback.command_template, spendCommand = spend.command_template;
  if (actions.filter(command => command === writebackCommand).length !== 1
      || actions.filter(command => command === spendCommand).length !== 1
      || actions.indexOf(writebackCommand) >= actions.indexOf(spendCommand)) return cli;
  const composed = (command: string) => command + " --settle-turn --settlement-receipt";
  const newWriteback = {...writeback, command_template: composed(writebackCommand),
    ...(typeof writeback.semantic_closeout_command_template === "string"
      ? {semantic_closeout_command_template: composed(writeback.semantic_closeout_command_template)} : {})};
  const {command_template: _manualCommand, ...receiptStep} = spend;
  const newSpend = {...receiptStep, owner: "runtime", precondition: spend.precondition +
    ". The writeback command runs this step and returns its exact receipt. " +
    "After a partial failure follow only settlement_owed for this Turn. After settlement do not run another spend."};
  return {...cli, settlement_plan: {...plan, schema_version: COMPOSED_CLI_SETTLEMENT_PLAN_SCHEMA_VERSION,
    ordered_steps: steps.map(step => step === writeback ? newWriteback : step === spend ? newSpend : step)},
  next_cli_actions: actions.filter(command => command !== spendCommand)
    .map(command => command === writebackCommand ? newWriteback.command_template : command)};
}

/** The heartbeat publisher uses only its current admitted Todo and exact receipt. */
function receiptBoundCliSettlement(params: JsonObject, requirePreparation: boolean): JsonObject | null {
  const cli = jsonObject(params.cli), receipt = jsonObject(params.receipt);
  const preparation = jsonObject(cli?.execution_preparation);
  const identity = jsonObject(jsonObject(cli?.settlement_plan)?.identity);
  if (!cli || (Object.hasOwn(cli, "execution_preparation") && !preparation)
      || cli.selection_required === true || cli.spend_after_validation !== true
      || ((preparation || requirePreparation) && (preparation?.schema_version !== EXECUTION_PREPARATION_SCHEMA_VERSION
        || preparation.state !== "ready" || preparation.delivery_allowed !== true))
      || receipt?.closeout_required !== true
      || receipt.settlement_binding_owed === true
      // Claiming within the original Turn can requalify its receipt.
      || (receipt.status !== "committed" && receipt.status !== "upgraded" && receipt.status !== "replayed")
      || !identity || typeof params.todo_id !== "string" || !params.todo_id || params.todo_id !== identity.todo_id
      || typeof identity.turn_instance_id !== "string" || receipt.turn_instance_id !== identity.turn_instance_id
      || (preparation && (preparation.todo_id !== identity.todo_id || preparation.agent_id !== identity.agent_id))
      || !isDeepStrictEqual(identity, receipt.settlement_identity)) return null;
  return composedCliSettlementChannel(cli);
}

export function nativeCliSettlementProjection(params: JsonObject): JsonObject {
  const cli = jsonObject(params.cli), composed = receiptBoundCliSettlement(params, true);
  return !cli || !composed || composed === cli ? {} : contextProjectionChanges(cli, composed);
}

/** Explicit complete-contract consumers use the same decided plan and receipt.
 * A soft claim has no preparation record; an existing non-ready record never
 * disappears. The later writeback and spend still perform their own checks. */
export function completeCliSettlementChannel(payload: JsonObject): JsonObject {
  const cli = jsonObject(jsonObject(payload.interaction_contract)?.cli_channel) ?? {};
  const identity = jsonObject(jsonObject(cli.settlement_plan)?.identity);
  if (payload.should_run !== true || payload.effective_action !== EffectiveAction.NORMAL_RUN
      || payload.normal_delivery_allowed !== true
      || jsonObject(jsonObject(payload.interaction_contract)?.agent_channel)?.delivery_allowed !== true
      || !identity || identity.goal_id !== payload.goal_id
      || identity.agent_id !== jsonObject(payload.agent_identity)?.agent_id) return cli;
  const composed = receiptBoundCliSettlement({cli, receipt: payload.heartbeat_receipt,
    todo_id: jsonObject(payload.selected_todo)?.todo_id}, false);
  return composed ?? cli;
}

export function turnScopedCliSettlementPlan(params: JsonObject): SettlementPlan {
  const identity = settlementIdentity(
    requireJsonObject(params.identity, "identity") as unknown as SettlementIdentityInput,
  );
  const commands = requireJsonObject(params.command_templates, "command_templates");
  const writeback = requireNonEmptyString(commands.durable_writeback, "durable_writeback");
  const spend = requireNonEmptyString(commands.quota_spend, "quota_spend");
  const semanticWriteback = commands.semantic_closeout === undefined ? null
    : requireNonEmptyString(commands.semantic_closeout, "semantic_closeout");
  // Scale remains CLI-owned. Project its actual accepted inputs rather than
  // copying another enum or asking callers to load the entire command help.
  const scales = requireStringArray(params.delivery_batch_scale_choices, "delivery_batch_scale_choices");
  requireNonEmptyString(scales.map(scale => requireNonEmptyString(scale, "delivery batch scale")).join("|"),
    "delivery batch scale choices");
  const inputHint = " Fill placeholders from the actual result: classification is a descriptive label; " +
    "scale=" + scales.join("|") + "; outcome=" + MATERIAL_DELIVERY_OUTCOMES.join("|") + ". " +
    "surface_only cannot settle this Turn. Do not relabel preparation-only work as progress; " +
    "continue authorized work within existing limits or report a genuine typed blocker. " +
    "Set --delivery-boundary " + DELIVERY_BOUNDARIES.join("|") + " for the applicable case above. " +
    "Add typed progress flags only when this Turn's evidence supports the observation. " +
    "Use --progress-result-class " + PROGRESS_RESULT_CLASSES.join("|") +
    ". For no_followup or exploration_exhausted, pass --progress-coverage-scope-id from verified scope coverage; " +
    "exploration_exhausted also needs --progress-coverage-complete. For a final Todo, terminal closeout follows spend " +
    "under its own plan step. A typed no_followup requires scope coverage beyond Todo completion. " +
    "For blocked outcome_gap, --progress-work-item-id must match this plan's Todo/obligation id " +
    "and --progress-blocker-id names the blocker. Repeat --progress-evidence-id for observed evidence identifiers. " +
    "Use opaque public-safe IDs of 1-128 characters, starting with an ASCII letter/digit, then letters/digits or ._:/- " +
    "(e.g. evidence:validation-1); an ID does not upload a file. " +
    "Use --agent-vision-json <file> for the material vision packet; " +
    "--vision-unchanged-reason <reason> only reuses an already evidence-linked vision. " +
    "These are authoring choices, not defaults, proof of progress or new requirements.";
  const parsedGoalRef = params.goal_ref === undefined
    ? null
    : parseExactGoalRef(params.goal_ref);
  if (
    parsedGoalRef !== null
    && (
      parsedGoalRef.kind === "invalid"
      || parsedGoalRef.value.goalId.value !== identity.goal_id
    )
  ) {
    throw new Error("settlement plan GoalRef is invalid or does not match identity");
  }
  const inFlight = params.delivery_boundary === "in_flight_continuation";
  const autonomousReplan = (params.autonomous_replan === undefined ? false
    : requireBoolean(params.autonomous_replan, "autonomous_replan")) || identity.binding_kind === "autonomous_replan";
  const todoSemanticWriteback = identity.binding_kind === "todo" && !autonomousReplan
    ? semanticWriteback : null;
  const mode = params.handoff_mode == null ? null
    : requireStringLiteral(params.handoff_mode, HANDOFF_MODES, "handoff_mode");
  const managedClaim = identity.binding_kind === "todo" && mode === "hard_lease";
  const proofSupplied = params.completion_proof_supplied === undefined ? false
    : requireBoolean(params.completion_proof_supplied, "completion_proof_supplied");
  const completionProof = managedClaim && !proofSupplied
    ? " --claim-operation-id '<original claim_operation_id>'" : "";
  const validation: SettlementStep = {
    kind: "validation", owner: "agent", idempotency_key_ref: "$.identity.effect_id",
    expected_receipt: "validation_receipt",
    precondition: "delivery result is independently validated",
  };
  if (identity.binding_kind === "todo") {
    validation.command_template = requireNonEmptyString(commands.todo_completion, "todo_completion") + completionProof;
    validation.command_condition = "todo_deliverable_complete";
    const claimHint = managedClaim ? "Before task execution, prepare it with ordinary Todo claim: `" +
      requireNonEmptyString(commands.todo_claim, "todo_claim") +
      "`. Reuse an already accepted current claim or explicit lease proof; keep the original " +
      "claim_operation_id for managed progress/completion retries. claimed_by alone is not an execution lease. " +
      "An expired, released or replaced execution requires normal ownership recovery; " +
      "this plan neither acquires nor renews execution. " : "";
    validation.precondition = claimHint + "validate the original Todo deliverable; when complete, run ordinary Todo completion " +
      "with its original declaration and current lease before writeback/spend. Use --evidence for " +
      "an artifact pointer; --result-file requires approved Goal acceptance criteria bound to this Todo, " +
      "not merely a Todo validator. Link known follow-up work with --successor-todo-id <existing-id>. No invented successor " +
      "or --no-follow-up is required. Todo done does not settle the Turn. " +
      "Keep an unfinished Todo open with its original declaration.";
  }
  const steps: SettlementStep[] = [
    validation,
    {
      kind: "durable_writeback", owner: "agent",
      precondition: "validation succeeded; " +
        (autonomousReplan ? "Autonomous replan uses --delivery-boundary semantic_closeout even while its Todo remains open. " +
            "Bind new decision evidence from this admitted work to its artifact/input; follow the remaining plan, " +
            "then yield for freshly guarded continuation. "
          : (inFlight ? "Use command_template for ordinary in_flight_continuation without new decision evidence. " : "") +
            "Use " + (todoSemanticWriteback !== null
              ? "semantic_closeout_command_template with --agent-vision-json"
              : "--delivery-boundary semantic_closeout") +
            " for new decision evidence, route change or Todo completion. " +
            "Bind new evidence produced or obtained by this admitted work to its artifact/input in fresh Vision/path_delta " +
            "with --agent-vision-json, including continue/no_change when retaining the route. " +
            "Use this same Turn; no new guard or binding. Follow the remaining plan, then yield for freshly guarded continuation. ") +
        "Prior-feedback readback, bookkeeping and deterministic repetition without new information are context, " +
        "not this Turn's progress; use them to advance the task first. " +
        (!inFlight && identity.binding_kind === "todo" ? VISION_MATERIAL_CLOSEOUT_HINT + " " : "") +
        "Route elimination needs evidence; failure alone is not progress. outcome_gap requires " +
        "semantic_closeout plus blocked + blocker/evidence IDs and continuation checks. " +
        "For an admitted open continuous Monitor whose observation is unavailable, " +
        "use this original identity for typed blocked outcome_gap; it settles without " +
        "poll or spend and preserves observation clocks/hash/generation. " +
        "A successful check whose condition is unmet (including an absent watched file) is " +
        "an unchanged observation, not unavailable: record quota monitor-poll with its actual " +
        "result digest; it advances next_due_at from cadence without refresh or spend. " +
        "An already committed Monitor poll needs neither refresh nor spend." + inputHint,
      idempotency_key_ref: "$.identity.effect_id", expected_receipt: "durable_writeback_receipt",
      command_template: writeback,
      ...(todoSemanticWriteback !== null ? {semantic_closeout_command_template: todoSemanticWriteback} : {}),
      // The same open Todo can reach a semantic feedback checkpoint. Deliver
      // its existing authoring contract with the executable command variant.
      // Autonomous replan already carries this owner in its writeback contract.
      ...(identity.binding_kind === "todo" && (!inFlight || semanticWriteback !== null)
        ? {vision_authoring: visionAuthoringContract(autonomousReplan)} : {}),
    },
    {
      kind: "quota_spend", owner: "agent",
      precondition: "exact original-identity durable writeback receipt exists; any declared completion validation " +
        "has completed the Todo, or that receipt qualifies same-Turn in-flight/replan progress " +
        "or a fresh open-Todo semantic checkpoint. Progress does not waive terminal completion validation; " +
        "skip this step after typed_blocked_writeback_no_spend",
      idempotency_key_ref: "$.identity.effect_id", expected_receipt: "quota_spend_receipt",
      command_template: spend,
    },
  ];
  if (identity.binding_kind === "todo") steps.push({
    kind: "terminal_closeout", owner: "agent",
    precondition: "the selected Todo is final with no runnable successor and matching " +
      "writeback and quota spend receipts exist",
    idempotency_key_ref: "$.identity.effect_id", expected_receipt: "terminal_closeout_receipt",
    command_template: requireNonEmptyString(commands.terminal_closeout, "terminal_closeout") + completionProof,
    conditional: true,
  });
  return {
    identity,
    steps,
    ...(parsedGoalRef === null ? {} : {
      goal_ref: {
        goal_id: parsedGoalRef.value.goalId.value,
        goal_instance_id: parsedGoalRef.value.goalInstanceId.value,
      },
    }),
  };
}
