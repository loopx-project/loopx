import assert from "node:assert/strict";
import test from "node:test";

import { interpretQuotaShouldRunPacket } from "../../loopx/control_plane/effect_program.ts";
import {
  decodeInteractionContract,
  type AgentInteractionChannel,
} from "../../loopx/control_plane/work_items/interaction_contract.ts";

// The host-facing type cannot represent delivery without an agent attempt.
// @ts-expect-error delivery requires must_attempt=true
const invalidDeliveryChannel: AgentInteractionChannel = {
  must_attempt: false,
  delivery_allowed: true,
  quiet_noop_allowed: false,
};
void invalidDeliveryChannel;

function successorReplanContract(): Record<string, unknown> {
  return {
    schema_version: "loopx_interaction_contract_v0",
    mode: "successor_replan_required",
    user_channel: {
      action_required: false,
      notify: "NOTIFY",
      non_blocking: true,
      actions: ["Review the optional setting."],
    },
    agent_channel: {
      must_attempt: true,
      delivery_allowed: false,
      quiet_noop_allowed: false,
      primary_action: "reopen the ready deferred successor",
    },
    cli_channel: {
      next_cli_actions: [
        "loopx todo update --todo-id todo_ready_deferred --status open",
      ],
      spend_allowed_now: false,
    },
  };
}

test("decodes non-delivery successor work as a required agent channel", () => {
  const contract = decodeInteractionContract(successorReplanContract());

  assert.equal(contract.user_channel.action_required, false);
  assert.equal(contract.user_channel.non_blocking, true);
  assert.equal(contract.agent_channel.must_attempt, true);
  assert.equal(contract.agent_channel.delivery_allowed, false);
  assert.equal(contract.agent_channel.quiet_noop_allowed, false);
});

test("Effect interpretation consumes the decoded interaction contract", () => {
  const turn = interpretQuotaShouldRunPacket({
    decision: "successor_replan_required",
    should_run: true,
    effective_action: "successor_replan_required",
    recommended_action: "Reopen the ready successor.",
    interaction_contract: successorReplanContract(),
    work_lane_contract: {},
    scheduler_hint: { action: "run_now", cadence_class: "active_work" },
  });

  assert.equal(
    turn.interpretation.interaction_mode,
    "successor_replan_required",
  );
  assert.deepEqual(turn.next_effect.cli_actions, [
    "loopx todo update --todo-id todo_ready_deferred --status open",
  ]);
});

test("rejects contradictory host-facing channel states", () => {
  const requiredNotice = successorReplanContract();
  requiredNotice.user_channel = {
    action_required: true,
    notify: "NOTIFY",
    non_blocking: true,
  };
  assert.throws(
    () => decodeInteractionContract(requiredNotice),
    /both required and non-blocking/,
  );

  const falseNonBlockingMarker = successorReplanContract();
  falseNonBlockingMarker.user_channel = {
    action_required: false,
    notify: "NOTIFY",
    non_blocking: false,
  };
  assert.throws(
    () => decodeInteractionContract(falseNonBlockingMarker),
    /non_blocking must be true when present/,
  );

  const deliveryWithoutAttempt = successorReplanContract();
  deliveryWithoutAttempt.agent_channel = {
    must_attempt: false,
    delivery_allowed: true,
    quiet_noop_allowed: false,
  };
  assert.throws(
    () => decodeInteractionContract(deliveryWithoutAttempt),
    /delivery without an attempt/,
  );

  const quietRequiredAction = successorReplanContract();
  quietRequiredAction.agent_channel = {
    must_attempt: false,
    delivery_allowed: false,
    quiet_noop_allowed: true,
  };
  quietRequiredAction.user_channel = {
    action_required: true,
    notify: "NOTIFY",
  };
  assert.throws(
    () => decodeInteractionContract(quietRequiredAction),
    /quiet no-op conflicts/,
  );
});

test("scoped override preserves admission context without selecting new work", async () => {
  const {projectScopedOverride} = await import("../../loopx/control_plane/quota/scoped_override.ts");
  const override = {kind: "agent_scoped_user_gate_override", from_state: "operator_gate",
    to_state: "eligible", selected_action: "old candidate"};
  const interaction = successorReplanContract();
  const selected = {todo_id: "todo_bound", text: "Current work", selection_binding: "heartbeat_receipt"};
  const before = structuredClone({override, selected});
  assert.deepEqual(projectScopedOverride({override, selected_todo: selected,
    interaction_contract: interaction}), {kind: override.kind, from_state: "operator_gate", to_state: "eligible"});
  interaction.agent_channel = {must_attempt: true, delivery_allowed: true, quiet_noop_allowed: false};
  assert.equal(projectScopedOverride({override, selected_todo: selected,
    interaction_contract: interaction}).selected_action, "Current work");
  assert.equal(projectScopedOverride({override, selected_todo: null,
    interaction_contract: interaction}).selected_action, undefined);
  assert.deepEqual({override, selected}, before);
  assert.throws(() => projectScopedOverride({override, selected_todo: {text: "No identity"},
    interaction_contract: interaction}), /selected_todo.todo_id/);
});
