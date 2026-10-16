import assert from "node:assert/strict";
import test from "node:test";

import {
  projectReplanSettlementContract,
  projectTodoLifecycleSettlementReentry,
  REPLAN_SETTLEMENT_REQUEST_SCHEMA,
  TODO_LIFECYCLE_REENTRY_REQUEST_SCHEMA,
} from "../../loopx/control_plane/work_items/replan_settlement.ts";

function request(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: REPLAN_SETTLEMENT_REQUEST_SCHEMA,
    selected_todo_id: null,
    semantic_replan_obligation_id: "replan-0000000000000001",
    ...overrides,
  };
}

test("Todo-bound replan keeps semantic obligation outside settlement identity", () => {
  assert.deepEqual(
    projectReplanSettlementContract(request({
      selected_todo_id: "todo_current001",
    })),
    {
      schema_version: "replan_settlement_contract_v0",
      single_binding_required: true,
      settlement_binding: {
        kind: "todo",
        id: "todo_current001",
        cli_argument: "--todo-id",
      },
      semantic_obligation: {
        kind: "autonomous_replan",
        id: "replan-0000000000000001",
        settlement_bound: false,
        discharge: "todo_bound_writeback",
      },
    },
  );
});

test("Todo-less replan binds the semantic obligation directly", () => {
  const contract = projectReplanSettlementContract(request());
  assert.equal(contract.settlement_binding.kind, "autonomous_replan");
  // The binding kind is the discriminant: after this check TypeScript knows
  // the matching settlement flag and discharge semantics without casts.
  assert.equal(contract.settlement_binding.cli_argument, "--replan-obligation-id");
  assert.equal(contract.semantic_obligation.settlement_bound, true);
  assert.equal(contract.semantic_obligation.discharge, "direct_settlement");
  assert.deepEqual(contract.settlement_binding, {
    kind: "autonomous_replan",
    id: "replan-0000000000000001",
    cli_argument: "--replan-obligation-id",
  });
  assert.deepEqual(contract.semantic_obligation, {
    kind: "autonomous_replan",
    id: "replan-0000000000000001",
    settlement_bound: true,
    discharge: "direct_settlement",
  });
});

test("typed projection rejects a missing semantic obligation", () => {
  assert.throws(
    () => projectReplanSettlementContract(
      request({
        semantic_replan_obligation_id: null,
      }),
    ),
    /semantic_replan_obligation_id must be a non-empty string/,
  );
});

test("Todo lifecycle reentry preserves exact completion identities", () => {
  assert.deepEqual(
    projectTodoLifecycleSettlementReentry({
      schema_version: TODO_LIFECYCLE_REENTRY_REQUEST_SCHEMA,
      goal_id: "goal-example",
      runtime_root: "/tmp/loopx runtime",
      triggers: [
        {
          kind: "completed_advancement_without_successor",
          todo_id: "todo_111111111111",
          completion_turn_key: "turn-implement",
        },
        {
          kind: "completed_advancement_without_successor",
          todo_id: "todo_222222222222",
        },
      ],
      lifecycle_actor_args: ["--agent-id", "current-agent"],
      quota_scoped_args: [
        "--agent-id",
        "current-agent",
        "--available-capability",
        "shell",
      ],
    }),
    {
      schema_version: "todo_lifecycle_settlement_reentry_v0",
      resolution_mode: "todo_lifecycle_settlement",
      triggers: [
        {
          kind: "completed_advancement_without_successor",
          todo_id: "todo_111111111111",
          completion_turn_key: "turn-implement",
          completion_identity_source: "turn_settlement",
        },
        {
          kind: "completed_advancement_without_successor",
          todo_id: "todo_222222222222",
          completion_turn_key:
            "local_completion_2fdda7fd139a77f97e72ca43d6e6a72a",
          completion_identity_source: "unscoped_completion",
        },
      ],
      next_cli_actions: [
        "when no real successor remains for completed Todo todo_111111111111: loopx --runtime-root '/tmp/loopx runtime' todo complete --goal-id goal-example --todo-id todo_111111111111 --completion-identity-key turn-implement --agent-id current-agent --no-follow-up",
        "when no real successor remains for completed Todo todo_222222222222: loopx --runtime-root '/tmp/loopx runtime' todo complete --goal-id goal-example --todo-id todo_222222222222 --completion-identity-key local_completion_2fdda7fd139a77f97e72ca43d6e6a72a --agent-id current-agent --no-follow-up",
        "otherwise link or create one real runnable successor for the exact completed Todo; do not create lifecycle-only filler",
        "loopx --runtime-root '/tmp/loopx runtime' --format json quota should-run --goal-id goal-example --agent-id current-agent --available-capability shell",
      ],
    },
  );
});

test("Todo lifecycle reentry rejects untyped succession triggers", () => {
  assert.throws(
    () => projectTodoLifecycleSettlementReentry({
      schema_version: TODO_LIFECYCLE_REENTRY_REQUEST_SCHEMA,
      goal_id: "goal-example",
      runtime_root: "/tmp/loopx runtime",
      triggers: [{ kind: "vision_acceptance_gap", todo_id: "todo_1" }],
      lifecycle_actor_args: [],
      quota_scoped_args: [],
    }),
    /kind is unsupported/,
  );
});

test("Todo lifecycle reentry rejects a non-string runtime root", () => {
  assert.throws(
    () => projectTodoLifecycleSettlementReentry({
      schema_version: TODO_LIFECYCLE_REENTRY_REQUEST_SCHEMA,
      goal_id: "goal-example",
      runtime_root: 42,
      triggers: [{
        kind: "completed_advancement_without_successor",
        todo_id: "todo_1",
      }],
      lifecycle_actor_args: [],
      quota_scoped_args: [],
    }),
    /runtime_root must be a non-empty string/,
  );
});

test("completion reentry always uses the stored completion identity key", () => {
  const projection = projectTodoLifecycleSettlementReentry({
    schema_version: TODO_LIFECYCLE_REENTRY_REQUEST_SCHEMA,
    goal_id: "goal-example",
    triggers: [{
      kind: "completed_advancement_without_successor",
      todo_id: "todo_333333333333",
      completion_turn_key: "local_completion_00000000000000000000000000000000",
    }],
    lifecycle_actor_args: [],
    quota_scoped_args: [],
  });

  assert.equal(
    (projection.triggers as Record<string, unknown>[])[0]
      ?.completion_identity_source,
    "turn_settlement",
  );
  assert.match(
    (projection.next_cli_actions as string[])[0] ?? "",
    /--completion-identity-key local_completion_0{32}/,
  );
});

test("Todo lifecycle reentry shell-quotes projected identities", () => {
  const projection = projectTodoLifecycleSettlementReentry({
    schema_version: TODO_LIFECYCLE_REENTRY_REQUEST_SCHEMA,
    goal_id: "goal with space",
    triggers: [
      {
        kind: "completed_advancement_without_successor",
        todo_id: "todo_1",
        completion_turn_key: "turn'quoted",
      },
    ],
    lifecycle_actor_args: ["--agent-id", "agent with space"],
    quota_scoped_args: ["--agent-id", "agent with space"],
  });

  assert.equal(
    (projection.next_cli_actions as string[])[0],
    "when no real successor remains for completed Todo todo_1: loopx todo complete --goal-id 'goal with space' --todo-id todo_1 --completion-identity-key 'turn'\"'\"'quoted' --agent-id 'agent with space' --no-follow-up",
  );
  assert.equal(
    (projection.next_cli_actions as string[]).at(-1),
    "loopx --format json quota should-run --goal-id 'goal with space' --agent-id 'agent with space'",
  );
});
