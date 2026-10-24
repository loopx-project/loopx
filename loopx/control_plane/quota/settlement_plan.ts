/** CLI guidance for the existing completion -> writeback -> spend protocol.
 * This is a projection, not a validation, lease or receipt authorization. */
import {
  settlementIdentity, type JsonObject, type SettlementIdentityInput,
  type SettlementPlan, type SettlementStep,
} from "../effect_program.ts";
import {VISION_MATERIAL_CLOSEOUT_HINT, visionAuthoringContract} from "../goals/vision_checkpoint.ts";
import {parseExactGoalRef} from "../goals/goal_instance_identity.ts";
import {requireJsonObject, requireNonEmptyString, requireStringArray} from "../runtime_decode.ts";
import {MATERIAL_DELIVERY_OUTCOMES} from "../work_items/delivery_outcome.ts";
import {DELIVERY_BOUNDARIES} from "../turn_driver/delivery_continuity.ts";
import {PROGRESS_RESULT_CLASSES} from "../work_items/replan_history.ts";

export function turnScopedCliSettlementPlan(params: JsonObject): SettlementPlan {
  const identity = settlementIdentity(
    requireJsonObject(params.identity, "identity") as unknown as SettlementIdentityInput,
  );
  const commands = requireJsonObject(params.command_templates, "command_templates");
  const writeback = requireNonEmptyString(commands.durable_writeback, "durable_writeback");
  const spend = requireNonEmptyString(commands.quota_spend, "quota_spend");
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
    "Typed evidence: --progress-result-class " + PROGRESS_RESULT_CLASSES.join("|") +
    ". For blocked outcome_gap, --progress-work-item-id must match this plan's Todo/obligation id " +
    "and --progress-blocker-id names the blocker. Repeat --progress-evidence-id for evidence. " +
    "IDs are 1-128 characters, start with an ASCII letter/digit, then letters/digits or ._:/- " +
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
  const validation: SettlementStep = {
    kind: "validation", owner: "agent", idempotency_key_ref: "$.identity.effect_id",
    expected_receipt: "validation_receipt",
    precondition: "delivery result is independently validated",
  };
  if (identity.binding_kind === "todo" && !inFlight) {
    validation.command_template = requireNonEmptyString(commands.todo_completion, "todo_completion");
    validation.command_condition = "todo_deliverable_complete";
    validation.precondition = "validate the deliverable; when complete, run ordinary Todo completion " +
      "with its original declaration and current lease before writeback/spend. Use --evidence for " +
      "an artifact pointer; --result-file requires approved Goal acceptance criteria bound to this Todo, " +
      "not merely a Todo validator. No invented successor " +
      "or --no-follow-up is required. Todo done does not settle the Turn";
  } else if (inFlight) {
    validation.precondition = "validate bounded in-flight progress; keep the Todo open and its " +
      "original completion declaration unchanged";
  }
  const steps: SettlementStep[] = [
    validation,
    {
      kind: "durable_writeback", owner: "agent",
      precondition: (inFlight || identity.binding_kind !== "todo" ? "validation succeeded"
        : "validation succeeded; " + VISION_MATERIAL_CLOSEOUT_HINT +
          " Pass a new packet with --agent-vision-json.") +
        " Route elimination needs evidence; failure alone is not progress. " +
        "outcome_progress may use in_flight_continuation; outcome_gap requires " +
        "semantic_closeout plus blocked + blocker/evidence IDs and continuation checks. " +
        "For an admitted open continuous Monitor whose observation is unavailable, " +
        "use this original identity for typed blocked outcome_gap; it settles without " +
        "poll or spend and preserves observation clocks/hash/generation. " +
        "An already committed Monitor poll needs neither refresh nor spend." + inputHint,
      idempotency_key_ref: "$.identity.effect_id", expected_receipt: "durable_writeback_receipt",
      command_template: writeback,
      // In-flight continuation uses its projected flags without a material vision packet.
      // Autonomous replan already carries this owner in its writeback contract.
      ...(identity.binding_kind === "todo" && !inFlight ? {vision_authoring: visionAuthoringContract()} : {}),
    },
    {
      kind: "quota_spend", owner: "agent",
      precondition: "matching durable writeback exists and any declared completion validation " +
        "has completed the Todo, or the same Turn has qualified in-flight/replan progress; " +
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
    command_template: requireNonEmptyString(commands.terminal_closeout, "terminal_closeout"),
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
