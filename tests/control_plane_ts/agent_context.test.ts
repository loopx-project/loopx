import assert from "node:assert/strict";
import test from "node:test";
import { AGENT_CONTEXT_PHASES, projectAgentContext, type AgentContextProvider } from "../../loopx/control_plane/agent_context.ts";
import { evaluateSubagentContext } from "../../loopx/control_plane/subagent_context.ts";

const scope = { goal_id: "goal-a", agent_id: "parent", todo_id: "todo-a" };
const policy = { mode: "multi_subagent", spawn_allowed: true, max_children: 2,
  model_config: { model: "example-small", reasoning_effort: "max" } };
const provider: AgentContextProvider = {
  hookId: "example.context", capabilityId: "example", revision: "v1",
  phases: AGENT_CONTEXT_PHASES,
  produce: () => ({ guidance: ["Inspect original evidence."], facts: {}, source_refs: ["core"] }),
};
const input = { phase: "before_plan", scope, capabilities: { example: { enabled: true } } };

test("disabled capability never calls provider or emits context", () => {
  assert.equal(projectAgentContext({ ...input, capabilities: {} }, [{ ...provider,
    produce() { throw new Error("must not run"); } }]), null);
  for (const orchestration of [{}, { ...policy, spawn_allowed: false },
    { ...policy, mode: "default" }, { ...policy, max_children: 0 },
    { model_config: policy.model_config }]) {
    assert.equal(evaluateSubagentContext({ phase: "before_plan", scope, orchestration }), null);
  }
});

test("all phases supply coordinator guidance without claiming adoption or execution", () => {
  for (const phase of AGENT_CONTEXT_PHASES) {
    const packet = evaluateSubagentContext({ phase, scope, orchestration: policy })!;
    assert.equal(packet.phase, phase);
    assert.equal(packet.delivery, "projected");
    assert.equal(packet.target, "coordinator");
    assert.equal(packet.authority, "guidance_only");
    const contribution = (packet.contributions as Record<string, any>[])[0];
    assert.deepEqual(contribution.facts.model_preference, policy.model_config);
    if (phase === "after_delegate_result") assert.equal(contribution.facts.receipt_observation, "not_supplied");
  }
});

test("stable context ids bind phase, scope, content and policy revision", () => {
  const id = (value: any) => value.contributions[0].context_id;
  const original = projectAgentContext(input, [provider]);
  assert.equal(id(original), id(projectAgentContext(input, [provider])));
  for (const patch of [{ phase: "before_delegate" }, { scope: { ...scope, goal_id: "goal-b" } }]) {
    assert.notEqual(id(original), id(projectAgentContext({ ...input, ...patch }, [provider])));
  }
  assert.notEqual(id(original), id(projectAgentContext(input, [{ ...provider, revision: "v2" }])));
});

test("provider failures, forged control fields and oversize context are isolated", () => {
  for (const produce of [
    () => { throw new Error("private credential text"); },
    () => ({ ...provider.produce({} as any, {}), scope: { goal_id: "other" } }),
    () => ({ ...provider.produce({} as any, {}), action: "approve" }),
    () => ({ ...provider.produce({} as any, {}), facts: { text: "x".repeat(4096) } }),
    () => ({ ...provider.produce({} as any, {}), guidance: [] }),
  ]) {
    const packet = projectAgentContext(input, [
      { ...provider, hookId: "example.bad", produce }, provider,
    ])!;
    assert.equal((packet.contributions as any[]).length, 1);
    assert.equal((packet.failures as any[]).length, 1);
    assert.ok(!JSON.stringify(packet).includes("private credential"));
    assert.deepEqual(packet.scope, scope);
  }
});

test("one provider cannot mutate the dispatcher or another provider inputs", () => {
  const packet = projectAgentContext(input, [{ ...provider, hookId: "example.mutate",
    produce(value) { value.scope.goal_id = "other"; value.capabilities.example = null;
      return provider.produce(value, {}); } }, provider])!;
  assert.deepEqual(packet.scope, scope);
  assert.equal((packet.contributions as any[]).length, 2);
  assert.deepEqual(input.scope, scope);
});

test("aggregate budget and duplicate producer ids remain bounded", () => {
  const packet = projectAgentContext(input, Array.from({ length: 40 }, (_, i) => ({
    ...provider, hookId: `example.${i}`, produce: () => ({
      guidance: ["Evidence."], facts: { text: "中".repeat(300) }, source_refs: ["core"],
    }),
  })))!;
  assert.ok(Buffer.byteLength(JSON.stringify(packet)) <= 3072);
  assert.ok((packet.failures as any[]).length > 0);
  const duplicate = projectAgentContext(input, [provider, provider])!;
  assert.equal((duplicate.contributions as any[]).length, 1);
  assert.equal((duplicate.failures as any[]).length, 1);
});

test("unknown phases and missing coordinator scope fail before invoking a provider", () => {
  assert.throws(() => projectAgentContext({ ...input, phase: "after_execute" }, [provider]));
  assert.throws(() => projectAgentContext({ ...input, scope: {} }, [provider]));
});

test("return phase projects reconciliation counts without copying raw child material", () => {
  const packet = evaluateSubagentContext({ phase: "after_delegate_result", scope,
    orchestration: policy, observations: {
      reconciliation_counts: { planned: 2, observed: 1, incomplete: 1, bad: "secret" },
      child_response: "private original text",
    } })!;
  const facts = (packet.contributions as any[])[0].facts;
  assert.equal(facts.receipt_observation, "host_reconciled");
  assert.deepEqual(facts.reconciliation_counts, { planned: 2, observed: 1, incomplete: 1 });
  assert.ok(!JSON.stringify(packet).includes("private original text"));
});

test("delegation routes and explicit result receipts are bounded public-safe facts", () => {
  const delegationContext = {
    schema_version: "loopx_delegation_context_v0",
    configuration_state: "ready",
    observed_at: "2026-09-19T04:20:00+00:00",
    authorized_count: 8,
    projected_count: 8,
    reason_code: "unused-private-reason",
    routes: [
      {
        binding_id: "review-route", agent_id: "reviewer", todo_id: "todo-review",
        runtime_id: "managed-runtime", executor_kind: "managed", readiness: "ready",
        entrypoint: "malicious replacement", execution_profile: "model-a@high",
        host_args: ["--secret", "credential"], workspace: "/private/worktree",
      },
      {
        binding_id: "bad route with spaces", agent_id: "ignored", todo_id: "ignored",
        runtime_id: "ignored", readiness: "ready",
      },
    ],
  };
  const before = evaluateSubagentContext({ phase: "before_plan", scope,
    orchestration: policy, observations: { delegation_context: delegationContext } })!;
  const beforeFacts = (before.contributions as any[])[0].facts;
  assert.equal(beforeFacts.delegation_context.projected_count, 1);
  assert.equal(beforeFacts.delegation_context.entrypoint, "loopx delegation");
  assert.equal(beforeFacts.delegation_context.execution_scope, "bound_delegation");
  assert.equal(beforeFacts.delegation_context.preflight, "required");
  assert.equal(beforeFacts.delegation_context.routes[0].readiness, "unknown");
  assert.equal(beforeFacts.delegation_context.routes[0].runtime_readiness, "ready");
  assert.equal(beforeFacts.delegation_context.routes[0].execution_profile, "model-a@high");
  assert.equal(beforeFacts.delegation_context.operation_receipts, undefined);
  assert.ok(!JSON.stringify(before).includes("credential"));
  assert.ok(!JSON.stringify(before).includes("/private/worktree"));
  assert.ok(!JSON.stringify(before).includes("raw child material"));

  const after = evaluateSubagentContext({ phase: "after_delegate_result", scope,
    orchestration: policy, observations: { delegation_context: {
      ...delegationContext,
      operation_receipts: {
        observed: 12, accepted: 3, unavailable: 1, recovery_required: 1,
        private_result: "raw child material",
      },
    } } })!;
  const afterFacts = (after.contributions as any[])[0].facts;
  assert.equal(afterFacts.delegation_context, undefined);
  assert.deepEqual(afterFacts.delegation_receipts, {
    configuration_state: "ready",
    observed_at: "2026-09-19T04:20:00+00:00",
    operation_receipts: {
      observed: 12, accepted: 3, unavailable: 1, recovery_required: 1,
    },
  });
  assert.ok(Buffer.byteLength(JSON.stringify(after)) <= 3072);
});

test("maximum delegation directory stays within provider budget", () => {
  const routes = Array.from({ length: 6 }, (_, index) => ({
    binding_id: `review-route-${index}`, agent_id: `reviewer-${index}`,
    todo_id: `todo-review-${index}`, runtime_id: "managed-runtime",
    executor_kind: "managed", readiness: "ready",
    execution_profile: "fixture-provider/fixture-model@max",
  }));
  const packet = evaluateSubagentContext({ phase: "before_plan", scope,
    orchestration: { ...policy, max_children: 6 }, observations: {
      delegation_context: {
        schema_version: "loopx_delegation_context_v0", configuration_state: "ready",
        observed_at: "2026-09-19T04:20:00+00:00", authorized_count: 6,
        projected_count: 6, routes,
      },
    } })!;
  assert.deepEqual(packet.failures, []);
  const delegation = (packet.contributions as any[])[0].facts.delegation_context;
  assert.ok(delegation.projected_count >= 1 && delegation.projected_count <= 6);
  assert.equal(delegation.routes.length, delegation.projected_count);
  assert.equal(delegation.routes_truncated, delegation.projected_count < 6 || undefined);
  assert.ok(Buffer.byteLength(JSON.stringify(packet.contributions[0])) <= 2048);
});

// Rich model identifiers and the complete participation guidance must survive
// the actual provider budget, not disappear as an isolated provider failure.
test("coordinator participation guidance survives all bounded lifecycle projections", () => {
  for (const phase of AGENT_CONTEXT_PHASES) {
    const packet = evaluateSubagentContext({ phase, scope, orchestration: {
      ...policy, max_children: 4,
      model_config: { model: "m".repeat(160), reasoning_effort: "max" },
    } })!;
    assert.deepEqual(packet.failures, []);
    const [contribution] = packet.contributions as Record<string, any>[];
    assert.equal(contribution.revision, "v6");
    assert.equal(packet.authority, "guidance_only");
    assert.ok(Buffer.byteLength(JSON.stringify(contribution)) <= 2048);
    assert.ok(Buffer.byteLength(JSON.stringify(packet)) <= 3072);
  }
});

test("configured child limit stays distinct from typed native host capacity", () => {
  const before = evaluateSubagentContext({ phase: "before_plan", scope,
    orchestration: { ...policy, max_children: 6 } })!;
  const beforeFacts = (before.contributions as Record<string, any>[])[0].facts;
  assert.equal(beforeFacts.max_children, 6);
  assert.deepEqual(beforeFacts.capacity_contract, {
    schema_version: "multi_subagent_capacity_v0",
    configured_limit_kind: "upper_bound",
    live_availability: "not_observed",
  });

  const after = evaluateSubagentContext({ phase: "after_delegate_result", scope,
    orchestration: { ...policy, max_children: 6 }, observations: {
      native_host_capacity: {
        schema_version: "native_subagent_capacity_observation_v0",
        operation: "followup",
        outcome: "agent_thread_limit_reached",
        child_count: 1,
        raw_error: "private host detail",
      },
    } })!;
  const afterFacts = (after.contributions as Record<string, any>[])[0].facts;
  assert.equal(afterFacts.capacity_contract.live_availability, "capacity_exhausted");
  assert.deepEqual(afterFacts.native_host_capacity, {
    schema_version: "native_subagent_capacity_observation_v0",
    operation: "followup",
    outcome: "agent_thread_limit_reached",
    retry_same_turn: false,
    child_count: 1,
    reason_code: "agent_thread_limit_reached",
    recovery_actions: [
      "continue_parent_work",
      "defer_unlaunched_children",
      "retry_after_capacity_change",
    ],
  });
  assert.ok(!JSON.stringify(after).includes("private host detail"));

  const succeeded = evaluateSubagentContext({ phase: "after_delegate_result", scope,
    orchestration: { ...policy, max_children: 6 }, observations: {
      native_host_capacity: {
        schema_version: "native_subagent_capacity_observation_v0",
        operation: "spawn",
        outcome: "succeeded",
      },
    } })!;
  const succeededFacts = (succeeded.contributions as Record<string, any>[])[0].facts;
  assert.equal(succeededFacts.capacity_contract.live_availability, "attempt_observed");
  assert.equal(succeededFacts.native_host_capacity.retry_same_turn, undefined);
});

test("durable native child report is bounded and does not claim host attestation", () => {
  const packet = evaluateSubagentContext({ phase: "after_delegate_result", scope,
    orchestration: { ...policy, max_children: 6 }, observations: {
      native_child_activity: {
        schema_version: "native_subagent_activity_v0",
        entrypoint_scope: "host_native_child_tools",
        observation: "coordinator_reported",
        host_attested: true,
        configured_limit: 6,
        attempted_count: 2,
        launched_count: 1,
        skipped_count: 0,
        capacity_rejected_count: 1,
        host_failed_count: 0,
        parent_accepted_count: 0,
        retry_same_turn: false,
        raw_host_result: "private result",
      },
    } })!;
  const facts = (packet.contributions as Record<string, any>[])[0].facts;
  assert.equal(facts.native_child_activity.host_attested, false);
  assert.equal(facts.native_child_activity.launched_count, 1);
  assert.equal(facts.native_child_activity.retry_same_turn, false);
  assert.equal(facts.native_receipt_observation, "coordinator_reported");
  assert.ok(!JSON.stringify(packet).includes("private result"));
});
