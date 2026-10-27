import assert from "node:assert/strict";
import test from "node:test";
import { AGENT_CONTEXT_PHASES, projectAgentContext, type AgentContextProvider } from "../../loopx/control_plane/agent_context.ts";
import { readFileSync } from "node:fs";
import { evaluateGoalAgentContext } from "../../loopx/control_plane/goal_agent_context.ts";
import { evaluateSubagentContext, projectDelegationContext } from "../../loopx/control_plane/subagent_context.ts";

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

test("planning progressively discloses probe scope without crowding out a second route", () => {
  const probe = {schema_version: "managed_runtime_probe_v0", scope: "probing_interpreter",
    module: "deepseek_harness", available: false};
  const routes = [
    {binding_id: "managed-evidence/" + "x".repeat(34),
      agent_id: "managed-worker-" + "x".repeat(12), todo_id: "todo_" + "a".repeat(12), runtime_id: "dsh",
      readiness: "blocked", runtime_readiness: "blocked", executor_kind: "managed",
      execution_profile: "deepseek-v4-flash@high", reason_code: "dsh_runtime_unavailable",
      runtime_probe: {...probe, private_path: "/private/interpreter"},
      unavailable_remediation: ["configure_dsh_runtime", "select_individual_host"]},
    {binding_id: "generic-verifier/" + "x".repeat(34),
      agent_id: "generic-worker-" + "x".repeat(12), todo_id: "todo_" + "b".repeat(12), runtime_id: "generic-cli",
      readiness: "unknown", runtime_readiness: "unknown", executor_kind: "generic",
      runtime_probe: null, unavailable_remediation: []},
  ];
  for (const phase of ["before_plan", "before_delegate"]) {
    const packet = evaluateSubagentContext({phase, scope, orchestration: policy, observations: {
      delegation_context: {schema_version: "loopx_delegation_context_v0", configuration_state: "ready",
        observed_at: "2026-09-19T04:20:00+00:00", authorized_count: 2, projected_count: 2, routes},
    }})!;
    assert.deepEqual(packet.failures, []);
    const [contribution] = packet.contributions as Record<string, any>[];
    const context = contribution.facts.delegation_context;
    assert.equal(context.projected_count, 2);
    assert.equal(context.preflight, "required");
    assert.deepEqual(context.routes.map((route: any) => route.probe_scope), ["probing_interpreter", undefined]);
    assert.deepEqual(context.routes.map((route: any) => route.readiness), ["blocked", "unknown"]);
    assert.ok(Buffer.byteLength(JSON.stringify(context)) <= 900);
    assert.ok(Buffer.byteLength(JSON.stringify(contribution)) <= 2048);
    assert.ok(!JSON.stringify(packet).includes("/private/interpreter"));
    assert.ok(!JSON.stringify(packet).includes("configure_dsh_runtime"));
  }
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


test("host receipt provenance survives projection without raw child content", () => {
  for (const observation of ["host_observed", "mixed"]) {
    const packet = evaluateSubagentContext({ phase: "after_delegate_result", scope,
      orchestration: policy, observations: { native_child_activity: {
        schema_version: "native_subagent_activity_v0", entrypoint_scope: "host_native_child_tools",
        observation, host_attested: true, configured_limit: 3, launched_count: 1,
        attempted_count: 1, parent_accepted_count: 1, raw_host_result: "private child result",
      } } })!;
    const facts = (packet.contributions as Record<string, any>[])[0].facts;
    assert.equal(facts.native_child_activity.observation, observation);
    assert.equal(facts.native_child_activity.host_attested, observation === "host_observed");
    assert.equal(facts.native_child_activity.parent_accepted_count, 1);
    assert.ok(!JSON.stringify(packet).includes("private child result"));
  }
});

// --- Delegation facts projection (the TypeScript owner of loopx_delegation_context_v0) ---

const fixture = JSON.parse(readFileSync(new URL("./fixtures/goal_agent_context_delegation.json", import.meta.url), "utf8"));
const observedAt = "2026-09-19T04:20:00+00:00";
const genericExecutor = { schema_version: "managed_executor_binding_v0", executor: "generic-cli", executor_kind: "generic",
  credential_env: null, endpoint_env: null, execution_profile: null, operator_credential_bound: false,
  available: null, unavailable_reason: null, unavailable_remediation: [], runtime_probe: null };
const binding = (index: number, extra: Record<string, unknown> = {}) => ({
  id: `binding-${index}`, agent_id: `worker-${index}`, todo_id: `todo_review000${index}`, requesters: ["coordinator"],
  workspace: `/workspace/worker-${index}`, host_args: ["--host", "generic-cli"], timeout_seconds: 60,
  output_refs: ["result.json"], ...extra,
});
const delegationFacts = (bindings: Record<string, unknown>[], extra: Record<string, unknown> = {}) => ({
  config: { schema_version: "loopx_local_delegation_v0", bindings }, config_error: null,
  registered_agent_ids: ["coordinator", ...bindings.map(row => row.agent_id)],
  executors: [{ host_args: ["--host", "generic-cli"], executor: genericExecutor }],
  operation_inventory: null, ...extra,
});
const project = (facts: unknown, phase = "before_plan", agentId = "coordinator") =>
  projectDelegationContext(facts, agentId, phase, observedAt);
const blocked = (phase = "before_plan") => ({
  schema_version: "loopx_delegation_context_v0", configuration_state: "blocked", observed_at: observedAt,
  authorized_count: 0, projected_count: 0, entrypoint: "loopx delegation", execution_scope: "bound_delegation",
  preflight: "required", routes: [],
  ...(phase === "after_delegate_result" ? { operation_receipts: { observed: 0 } } : {}),
  reason_code: "delegation_context_unavailable",
});

test("delegation facts reproduce the pinned Python projection byte for byte, context_id included", (t) => {
  for (const [name, scenario] of Object.entries<any>(fixture.scenarios)) {
    t.mock.timers.enable({ apis: ["Date"], now: Date.parse(scenario.observed_at) });
    const packet = evaluateGoalAgentContext({ phase: scenario.phase, scope: fixture.scope,
      orchestration: scenario.orchestration, observations: {}, delegation_facts: scenario.delegation_facts });
    assert.equal(JSON.stringify(packet), JSON.stringify(scenario.expected_context), name);
    t.mock.timers.reset();
  }
});

test("ready routes derive readiness once from the executor fact and join one probe per host argv", () => {
  const managed = { ...genericExecutor, executor: "dsh", executor_kind: "managed", execution_profile: "model-a@high",
    available: false, unavailable_reason: "dsh_runtime_unavailable",
    unavailable_remediation: ["configure_dsh_runtime"], credential_env: "PRIVATE_CREDENTIAL",
    runtime_probe: { schema_version: "managed_runtime_probe_v0", scope: "probing_interpreter", module: "dsh", available: false } };
  const facts = delegationFacts([binding(0), binding(2, { host_args: ["--host", "dsh"] })], {
    executors: [{ host_args: ["--host", "generic-cli"], executor: genericExecutor },
      { host_args: ["--host", "dsh"], executor: managed },
      { host_args: ["--host", "generic-cli"], executor: { ...genericExecutor, available: true } }],
  });
  const context = project(facts);
  assert.equal(context.configuration_state, "ready");
  assert.equal(context.authorized_count, 2);
  assert.deepEqual(context.routes, [
    { binding_id: "binding-0", agent_id: "worker-0", todo_id: "todo_review0000", runtime_id: "generic-cli",
      readiness: "unknown", runtime_readiness: "unknown", executor_kind: "generic" },
    { binding_id: "binding-2", agent_id: "worker-2", todo_id: "todo_review0002", runtime_id: "dsh",
      readiness: "blocked", runtime_readiness: "blocked", executor_kind: "managed",
      execution_profile: "model-a@high", reason_code: "dsh_runtime_unavailable", probe_scope: "probing_interpreter" },
  ]);
  assert.equal(context.operation_receipts, undefined);
  assert.equal(context.routes_truncated, undefined);
  assert.ok(!JSON.stringify(context).includes("PRIVATE_"));
  assert.ok(!JSON.stringify(context).includes("/workspace/"));
  const ready = project(delegationFacts([binding(0)], {
    executors: [{ host_args: ["--host", "generic-cli"], executor: { ...genericExecutor, available: true } }] }));
  assert.deepEqual([ready.routes[0].runtime_readiness, ready.routes[0].readiness], ["ready", "unknown"]);
});

test("runtime availability does not admit uninspected delegation or expose host configuration", () => {
  const projectPath = "/fixture/project";
  const hostArgs = ["--host", "dsh", "--project", projectPath];
  const facts = delegationFacts([binding(0, { workspace: `${projectPath}/worker`, host_args: hostArgs,
    output_refs: ["result.json"] })], {
    executors: [{ host_args: hostArgs, executor: { ...genericExecutor, executor: "managed-runtime",
      executor_kind: "managed", execution_profile: "model-a@high", available: true,
      credential_env: "PRIVATE_CREDENTIAL", endpoint_env: "PRIVATE_ENDPOINT" } }],
  });
  const context = project(facts);
  assert.equal(context.configuration_state, "ready");
  assert.equal(context.preflight, "required");
  assert.equal(context.execution_scope, "bound_delegation");
  assert.equal(context.authorized_count, 1);
  assert.equal(context.projected_count, 1);
  assert.equal(context.operation_receipts, undefined);
  assert.deepEqual(context.routes, [{ binding_id: "binding-0", agent_id: "worker-0", todo_id: "todo_review0000",
    runtime_id: "managed-runtime", readiness: "unknown", runtime_readiness: "ready",
    executor_kind: "managed", execution_profile: "model-a@high" }]);
  const packet = evaluateGoalAgentContext({ phase: "before_plan", scope: { ...scope, agent_id: "coordinator" },
    orchestration: policy, delegation_facts: facts })!;
  assert.deepEqual(packet.failures, []);
  assert.equal((packet.contributions as any[])[0].facts.delegation_context.projected_count, 1);
  const encoded = JSON.stringify(packet);
  for (const privateValue of ["host_args", "output_refs", projectPath, "/workspace/", "PRIVATE_"]) {
    assert.ok(!encoded.includes(privateValue), privateValue);
  }
});

for (const runnerConfigured of [false, true]) {
  test(`planning retains original probe scope without admission (runner_configured=${runnerConfigured})`, () => {
    // managed_executor_binding("dsh", environ={}, module_probe=False) with each runner state.
    const executor = {
      schema_version: "managed_executor_binding_v0", executor: "dsh", executor_kind: "managed",
      credential_env: "PRIVATE_CREDENTIAL", endpoint_env: "PRIVATE_ENDPOINT",
      execution_profile: "deepseek-flash@high",
      output_token_budget: { schema_version: "dsh_output_token_budget_v0", scope: "per_model_request",
        max_tokens: 16384, valid: true, source: "product_default",
        final_response_reserve_supported: false, hard_tool_budget_supported: false },
      operator_credential_bound: runnerConfigured, available: runnerConfigured,
      unavailable_reason: runnerConfigured ? null : "dsh_runtime_unavailable",
      unavailable_remediation: runnerConfigured ? [] : ["configure_dsh_runtime", "select_individual_host"],
      runtime_probe: { schema_version: "managed_runtime_probe_v0",
        scope: runnerConfigured ? "configured_runner" : "probing_interpreter",
        module: runnerConfigured ? null : "deepseek_harness", available: runnerConfigured },
    };
    const facts = delegationFacts([binding(0, { host_args: ["--host", "dsh"] })], {
      executors: [{ host_args: ["--host", "dsh"], executor }],
    });
    const context = project(facts);
    const route = context.routes[0];
    assert.equal(route.probe_scope, executor.runtime_probe.scope);
    assert.equal(route.runtime_readiness, runnerConfigured ? "ready" : "blocked");
    assert.equal(route.readiness, runnerConfigured ? "unknown" : "blocked");
    assert.equal(route.reason_code, executor.unavailable_reason ?? undefined);
    assert.equal(context.preflight, "required");
    assert.equal(context.operation_receipts, undefined);
    // Detailed probes and remediation stay in host facts; planning retains their scope and verdict.
    assert.equal(route.runtime_probe, undefined);
    assert.equal(route.unavailable_remediation, undefined);
    const packet = evaluateGoalAgentContext({ phase: "before_plan", scope: { ...scope, agent_id: "coordinator" },
      orchestration: policy, delegation_facts: facts })!;
    assert.deepEqual(packet.failures, []);
    const projected = (packet.contributions as any[])[0].facts.delegation_context;
    assert.equal(projected.routes[0].probe_scope, executor.runtime_probe.scope);
    assert.equal(projected.routes[0].readiness, runnerConfigured ? "unknown" : "blocked");
    assert.equal(projected.preflight, "required");
    assert.ok(!JSON.stringify(packet).includes("PRIVATE_"));
  });
}

test("requester-scoped directory ignores other requesters and blocks on unregistered or ungranted bindings", () => {
  const foreign = binding(9, { id: "foreign", requesters: ["someone-else"] });
  const scoped = project(delegationFacts([binding(0), foreign]));
  assert.equal(scoped.authorized_count, 1);
  assert.deepEqual(scoped.routes.map((route: any) => route.binding_id), ["binding-0"]);
  for (const facts of [
    delegationFacts([binding(0)], { registered_agent_ids: ["coordinator"] }),
    delegationFacts([binding(0)], { registered_agent_ids: ["worker-0"] }),
    delegationFacts([binding(0)], { registered_agent_ids: null }),
    delegationFacts([binding(0, { agent_id: "coordinator" })]),
    delegationFacts([binding(0, { workspace: "relative/worker" })]),
    delegationFacts([binding(0, { host_args: [] })]),
    delegationFacts([binding(0), binding(0)]),
  ]) assert.deepEqual(project(facts), blocked());
  assert.equal(project(delegationFacts([binding(0)]), "before_plan", "worker-0").authorized_count, 0);
});

test("malformed or unreadable facts degrade to a blocked observation instead of a handler failure", () => {
  for (const facts of [
    null, "facts", [], {}, { config: null, config_error: "unavailable" },
    delegationFacts([binding(0)], { config_error: "unavailable" }),
    { ...delegationFacts([binding(0)]), config: { schema_version: "loopx_local_delegation_v0" } },
    { ...delegationFacts([binding(0)]), config: ["binding"] },
    delegationFacts([binding(0)], { executors: [] }),
    delegationFacts([binding(0)], { executors: [{ host_args: ["--host", "other"], executor: genericExecutor }] }),
    delegationFacts([binding(0)], { executors: [{ host_args: ["--host", "generic-cli"],
      executor: { ...genericExecutor, runtime_probe: { schema_version: "other" } } }] }),
    delegationFacts(["binding"]),
  ]) assert.deepEqual(project(facts), blocked());
  assert.deepEqual(project(delegationFacts([binding(0)], { config_error: "unavailable" }), "after_delegate_result"),
    blocked("after_delegate_result"));
  const packet = evaluateGoalAgentContext({ phase: "before_plan", scope, orchestration: policy,
    delegation_facts: "garbage" })!;
  assert.deepEqual(packet.failures, []);
  assert.equal((packet.contributions as any[])[0].facts.delegation_context.configuration_state, "blocked");
});

test("operation receipts are summarized only in the result phase with the Python key order", () => {
  const inventory = { items: [
    { status: "running" }, { status: "rejected", recovery_required: true }, { status: "accepted" },
    { status: "accepted", recovery_required: false }, { status: "" }, { status: "exotic" }, "not a record",
  ], has_more: true };
  const after = project(delegationFacts([binding(0)], { operation_inventory: inventory }), "after_delegate_result");
  assert.deepEqual(after.operation_receipts, { observed: 7, running: 1, accepted: 2, rejected: 1, unavailable: 1,
    recovery_required: 1, has_more: true });
  assert.deepEqual(Object.keys(after), ["schema_version", "configuration_state", "observed_at", "authorized_count",
    "projected_count", "entrypoint", "execution_scope", "preflight", "routes", "operation_receipts"]);
  const before = project(delegationFacts([binding(0)], { operation_inventory: inventory }));
  assert.equal(before.operation_receipts, undefined);
  assert.deepEqual(project(delegationFacts([binding(0)]), "after_delegate_result"), blocked("after_delegate_result"));
  assert.deepEqual(project(delegationFacts([binding(0)], { operation_inventory: { items: [], has_more: false } }),
    "after_delegate_result").operation_receipts, { observed: 0 });
  const packet = evaluateGoalAgentContext({ phase: "after_delegate_result",
    scope: { ...scope, agent_id: "coordinator" }, orchestration: policy,
    delegation_facts: delegationFacts([binding(0)], { operation_inventory: inventory }) })!;
  const facts = (packet.contributions as any[])[0].facts;
  assert.equal(facts.delegation_context, undefined);
  assert.deepEqual(facts.delegation_receipts, { configuration_state: "ready", observed_at: facts.delegation_receipts.observed_at,
    operation_receipts: after.operation_receipts });
});

test("routes stay within the six-route and 900-byte budgets with truncation marked", () => {
  const bindings = Array.from({ length: 8 }, (_, index) => binding(index));
  const context = project(delegationFacts(bindings));
  assert.equal(context.authorized_count, 8);
  assert.ok(Number(context.projected_count) >= 1 && Number(context.projected_count) <= 6);
  assert.equal((context.routes as any[]).length, context.projected_count);
  assert.ok(Buffer.byteLength(JSON.stringify(context)) <= 900);
  assert.equal(context.routes_truncated, true);
  assert.equal(Buffer.byteLength(JSON.stringify(project(delegationFacts(bindings.slice(0, 2))))) <= 900, true);
  assert.equal(project(delegationFacts(bindings.slice(0, 2))).routes_truncated, undefined);
  const dropped = project(delegationFacts([binding(0, { id: "bad route with spaces" }), binding(1)]));
  assert.deepEqual([dropped.authorized_count, dropped.projected_count, dropped.routes_truncated], [2, 1, true]);
});

test("a signed planning observation replayed by the host wins over fresh facts", () => {
  const signed = { schema_version: "loopx_delegation_context_v0", configuration_state: "ready", observed_at: observedAt,
    authorized_count: 1, projected_count: 1, routes: [{ binding_id: "signed", agent_id: "reviewer", todo_id: "todo-review",
      runtime_id: "generic-cli", readiness: "unknown" }] };
  const packet = evaluateGoalAgentContext({ phase: "before_delegate", scope, orchestration: policy,
    observations: { delegation_context: signed }, delegation_facts: delegationFacts([binding(0)]) })!;
  const context = (packet.contributions as any[])[0].facts.delegation_context;
  assert.deepEqual(context.routes, [{ binding_id: "signed", agent_id: "reviewer", todo_id: "todo-review",
    runtime_id: "generic-cli", readiness: "unknown", runtime_readiness: "unknown" }]);
  const none = evaluateGoalAgentContext({ phase: "before_plan", scope, orchestration: policy, delegation_facts: null })!;
  assert.equal((none.contributions as any[])[0].facts.delegation_context, undefined);
});

test("replayed routes keep the exact legacy field semantics of the signed observation", () => {
  const replay = (route: Record<string, unknown>) => {
    const packet = evaluateGoalAgentContext({ phase: "before_delegate", scope, orchestration: policy,
      observations: { delegation_context: { schema_version: "loopx_delegation_context_v0", configuration_state: "ready",
        observed_at: observedAt, authorized_count: 1, projected_count: 1, routes: [route] } } })!;
    return (packet.contributions as any[])[0].facts.delegation_context;
  };
  const base = { binding_id: "signed", agent_id: "reviewer", todo_id: "todo-review" };
  // No executor_kind: the key stays absent; it is never defaulted on replay.
  assert.deepEqual(replay({ ...base, runtime_id: "generic-cli", readiness: "unknown" }).routes[0],
    { ...base, runtime_id: "generic-cli", readiness: "unknown", runtime_readiness: "unknown" });
  // A missing runtime_id drops the route and marks truncation, as before.
  const dropped = replay({ ...base, readiness: "unknown" });
  assert.deepEqual([dropped.authorized_count, dropped.projected_count, dropped.routes, dropped.routes_truncated],
    [1, 0, [], true]);
  // readiness is authoritative on replay; runtime_readiness never rewrites it.
  for (const [readiness, runtimeReadiness] of [["blocked", "ready"], ["unknown", "blocked"]] as const) {
    assert.deepEqual(replay({ ...base, runtime_id: "dsh", executor_kind: "managed",
      readiness, runtime_readiness: runtimeReadiness }).routes[0],
      { ...base, runtime_id: "dsh", readiness, runtime_readiness: runtimeReadiness, executor_kind: "managed" });
  }
});

test("registration is checked even when the requester has zero binding rows", () => {
  // Unregistered requester, zero rows: blocked (the old service init raised).
  assert.deepEqual(project(delegationFacts([], { registered_agent_ids: ["worker-0"] })), blocked());
  // Missing registry (null list), zero rows: ready/0; any row: blocked.
  const empty = project(delegationFacts([], { registered_agent_ids: null }));
  assert.equal(empty.configuration_state, "ready");
  assert.deepEqual([empty.authorized_count, empty.projected_count, empty.routes, empty.routes_truncated],
    [0, 0, [], undefined]);
  assert.deepEqual(project(delegationFacts([binding(0)], { registered_agent_ids: null })), blocked());
});
