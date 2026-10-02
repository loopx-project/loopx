/** CLI guidance for the existing completion -> writeback -> spend protocol.
 * This is a projection, not a validation, lease or receipt authorization. */
import {
  settlementIdentity, type JsonObject, type SettlementIdentityInput,
  type SettlementPlan, type SettlementStep,
} from "../effect_program.ts";
import {parseExactGoalRef} from "../goals/goal_instance_identity.ts";
import {requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";

export function turnScopedCliSettlementPlan(params: JsonObject): SettlementPlan {
  const identity = settlementIdentity(
    requireJsonObject(params.identity, "identity") as unknown as SettlementIdentityInput,
  );
  const commands = requireJsonObject(params.command_templates, "command_templates");
  const writeback = requireNonEmptyString(commands.durable_writeback, "durable_writeback");
  const spend = requireNonEmptyString(commands.quota_spend, "quota_spend");
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
      "with its original declaration and current lease before writeback/spend. No invented successor " +
      "or --no-follow-up is required. Todo done does not settle the Turn";
  } else if (inFlight) {
    validation.precondition = "validate bounded in-flight progress; keep the Todo open and its " +
      "original completion declaration unchanged";
  }
  const steps: SettlementStep[] = [
    validation,
    {
      kind: "durable_writeback", owner: "agent", precondition: "validation succeeded",
      idempotency_key_ref: "$.identity.effect_id", expected_receipt: "durable_writeback_receipt",
      command_template: writeback,
    },
    {
      kind: "quota_spend", owner: "agent",
      precondition: "matching durable writeback exists and any declared completion validation " +
        "has completed the Todo, or the same Turn has qualified in-flight/replan progress",
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
