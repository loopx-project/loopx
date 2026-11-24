import assert from "node:assert/strict";
import test from "node:test";
import {exploreResultWritebackAffordance} from "../../loopx/control_plane/capabilities/explore_context_delivery.ts";
import {type JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {visionAuthoringContract} from "../../loopx/control_plane/goals/vision_checkpoint.ts";
import {progressiveContextPlan} from "../../loopx/control_plane/quota/progressive_context.ts";
import {evaluateTurnEnvelope} from "../../loopx/control_plane/quota/turn_envelope.ts";
import {canonicalHash} from "../../loopx/control_plane/quota/turn_envelope_codec.ts";
import {replanGoalContext} from "../../loopx/control_plane/quota/turn_envelope_references.ts";
import {
  SCOPED_OUTCOME_PROJECTION_MODE,
  compactRoutineSettlementPlan,
  scopeTurnGoalAcceptance,
} from "../../loopx/control_plane/quota/turn_envelope_scoping.ts";

function routineWritebackStep(agentId = "a1", todoId = "todo-current"): JsonObject {
  return {
    kind: "durable_writeback",
    owner: "agent",
    precondition: "Persist evidence after independent checks.",
    idempotency_key_ref: "$.identity.effect_id",
    expected_receipt: "durable_writeback_receipt",
    command_template: `loopx refresh-state --goal-id g1 --agent-id ${agentId} --todo-id ${todoId} --settle-turn --settlement-receipt`,
    semantic_closeout_command_template: `loopx refresh-state --goal-id g1 --agent-id ${agentId} --todo-id ${todoId} --agent-vision-json <vision.json> --settle-turn --settlement-receipt`,
    vision_authoring: visionAuthoringContract(false),
    optional_attachments: [exploreResultWritebackAffordance()],
  };
}

function outcomeRow(index: number, overrides: JsonObject = {}): JsonObject {
  return {
    id: `out-${index}`,
    owner_agent_id: `peer-${index % 4}`,
    objective: `Verify historical deliverable ${index} cleanly.`,
    revision: 2,
    todo_ids: [`todo-hist-${index}`],
    sources: [{ref: `docs/evidence-${index}.md`, summary: `Historical evidence summary ${index} ` + "x".repeat(900)}],
    requirements: [
      {
        id: `req-${index}-a`,
        description: `Independent acceptance requirement for ${index} ` + "y".repeat(220),
        status: "verified",
        planning_satisfied: true,
        agent_review: {
          id: `req-${index}-a`,
          conclusion: "met",
          basis_current: true,
          independently_verified: false,
          note: `Detailed agent verification note ${index} ` + "z".repeat(450),
          evidence_refs: [`evidence:item-${index}`],
        },
      },
    ],
    basis_digest: "sha256:" + String(index % 10).repeat(64),
    work: "complete",
    ...overrides,
  };
}

function baseNormalPayload(count = 33, agentId = "a1", todoId = "todo-current"): JsonObject {
  const outcomes: JsonObject[] = [
    outcomeRow(0, {
      id: "out-current",
      owner_agent_id: agentId,
      todo_ids: [todoId],
      requirements: [{
        id: "req-current",
        description: "Current deliverable must pass strict parity verification " + "p".repeat(200),
        status: "unverified",
        planning_satisfied: false,
      }],
    }),
  ];
  for (let i = 1; i < count; i++) outcomes.push(outcomeRow(i));

  const affordance = exploreResultWritebackAffordance();
  const requirements = {
    schema_version: "loopx_goal_outcomes_v0",
    revision: 33,
    current_lifetime: true,
    outcomes,
  };
  const contract = (goalObjective: JsonObject): JsonObject => ({
    checks: ["pytest"],
    goal_objective: goalObjective,
    outcome_requirements: structuredClone(requirements),
  });

  return {
    ok: true,
    goal_id: "g1",
    agent_id: agentId,
    decision: "run",
    should_run: true,
    normal_delivery_allowed: true,
    effective_action: "normal_run",
    state: "eligible",
    reason: "admitted selected todo",
    action_required: false,
    open_count: 1,
    recommended_action: "Deliver scoped verification slice.",
    agent_identity: {agent_id: agentId, role: "worker"},
    selected_todo: {
      todo_id: todoId,
      text: "Deliver scoped verification slice.",
      status: "open",
      claimed_by: agentId,
      task_repository: "repo",
    },
    work_lane_contract: {
      schema_version: "work_lane_contract_v0",
      must_attempt_work: true,
    },
    heartbeat_receipt: {
      schema_version: "heartbeat_receipt_v0",
      status: "committed",
      closeout_required: true,
      turn_instance_id: "turn-1",
      settlement_identity: {
        schema_version: "quota_settlement_identity_v0",
        effect_id: `g1:${agentId}:${todoId}:turn-1`,
        goal_id: "g1",
        agent_id: agentId,
        todo_id: todoId,
        turn_instance_id: "turn-1",
      },
    },
    goal_frontier_projection: {
      schema_version: "goal_frontier_projection_v0",
      replan_required: false,
      acceptance_gaps: [],
    },
    interaction_contract: {
      schema_version: "loopx_interaction_contract_v0",
      mode: "bounded_delivery",
      agent_channel: {
        must_attempt: true,
        delivery_allowed: true,
        quiet_noop_allowed: false,
        primary_action: "Deliver scoped verification slice.",
        required_reads: [{source: "goal_acceptance", reason: "Read all Goal acceptance criteria."}],
        work_context: {
          complete: true,
          selected_todo_ref: "selected_todo",
          selected_todo_text_verified: true,
          instruction: "Read full task requirements.",
          sources: [
            {
              source: "turn_start_capability_hook",
              hook_id: "explore.turn_context",
              capability_id: "explore",
              kind: "explore_turn_context",
              content: {
                graph: {
                  counts: {node_count: 1, finding_count: 0},
                  writeback_results: [],
                  result_attachment_template: affordance.attachment_template,
                  path_delta_attachment_template: affordance.path_delta_attachment_template,
                  result_writeback_guidance: affordance.guidance,
                  guidance: "Use explore when branching.",
                },
              },
            },
            {
              source: "goal_acceptance",
              content: {
                ok: true,
                status: "loaded",
                decision_read_from_provider: true,
                legacy_fallback_used: false,
                read_complete: true,
                provider_revision: "sqlite:abc:33",
                goal_acceptance_contract: contract({schema_version: "goal_objective_revision_v0", objective: "Deliver X"}),
                outcome_context: {
                  schema_version: "outcome_context_navigation_v1",
                  goal_id: "g1",
                  agent_id: agentId,
                  todo_id: todoId,
                  outcome_revision: 33,
                  coverage_complete: true,
                  limitations: [],
                  suggested_outcome_ids: ["out-current"],
                  provider_revision: "sqlite:abc:33",
                },
              },
            },
          ],
        },
      },
      user_channel: {action_required: false, notify: "DONT_NOTIFY"},
      cli_channel: {
        execution_preparation: {state: "ready", todo_id: todoId},
        spend_allowed_now: false,
        spend_after_validation: true,
        spend_policy: "spend once after validated writeback",
        next_cli_actions: [`loopx refresh-state --goal-id g1 --agent-id ${agentId} --todo-id ${todoId} --settle-turn --settlement-receipt`],
        settlement_plan: {
          schema_version: "quota_settlement_plan_v2",
          identity: {
            schema_version: "quota_settlement_identity_v0",
            effect_id: `g1:${agentId}:${todoId}:turn-1`,
            goal_id: "g1",
            agent_id: agentId,
            todo_id: todoId,
            turn_instance_id: "turn-1",
            binding_kind: "todo",
          },
          ordered_steps: [{kind: "validation", owner: "agent"}, routineWritebackStep(agentId, todoId), {kind: "quota_spend", owner: "runtime"}],
        },
      },
    },
    planning_horizon: {
      core_goal: {
        goal_id: "g1",
        objective: "Deliver X",
        acceptance_contract: contract({schema_version: "goal_objective_revision_v0", objective: "Deliver X", adoptions: []}),
      },
    },
    agent_todo_summary: {
      schema_version: "todo_summary_v0",
      open_count: 1,
      all_open_count: 1,
      open: [],
      goal_acceptance_contract: contract({schema_version: "goal_objective_revision_v0", objective: "Deliver X", agent_adoption: {adopted: false}}),
    },
  };
}

test("scoped per-Turn goal_acceptance shrinks 33+ outcomes while retaining relevant, gap, and ambiguous rows", () => {
  const source = baseNormalPayload(33);
  const ambiguousRow: JsonObject = {
    id: "out-12",
    objective: "Ambiguous unowned unbound outcome",
    revision: 1,
    todo_ids: [],
    sources: [{ref: "unknown", summary: "Ambiguous origin."}],
    requirements: [{id: "amb", description: "Preserve ambiguous outcome in full.", status: "unverified", planning_satisfied: false}],
  };
  const carriers: JsonObject[] = [
    ((((source.interaction_contract as JsonObject).agent_channel as JsonObject)
      .work_context as JsonObject).sources as JsonObject[])[1].content as JsonObject,
    (source.planning_horizon as JsonObject).core_goal as JsonObject,
    source.agent_todo_summary as JsonObject,
  ];
  for (const holder of carriers) {
    const contract = (holder.goal_acceptance_contract ?? holder.acceptance_contract) as JsonObject;
    ((contract.outcome_requirements as JsonObject).outcomes as JsonObject[])[12] = structuredClone(ambiguousRow);
  }
  const fullOutcomes = carriers[0].goal_acceptance_contract as JsonObject;
  const origOutcomes = (fullOutcomes.outcome_requirements as JsonObject).outcomes as JsonObject[];
  // Inject: (1) open gap referencing out-5, (2) dependency todo matching out-8,
  // (3) ambiguous outcome missing owner and todos at out-12.
  (source.selected_todo as JsonObject).dependency_todo_ids = ["todo-hist-8"];
  (source.goal_frontier_projection as JsonObject).acceptance_gaps = [
    {kind: "outcome_gap", outcome_id: "out-5", agent_id: "peer-1"},
  ];
  const expectedDigest = canonicalHash(origOutcomes);

  const composed = evaluateTurnEnvelope({
    operation: "compose_complete",
    payload: source,
    scheduler_execution_args: "",
    capture_reference_file: "state/captures/decision.reader.json",
  });
  const wcSources = (((composed.interaction_contract as JsonObject).agent_channel as JsonObject)
    .work_context as JsonObject).sources as JsonObject[];
  const gaContract = (wcSources[1].content as JsonObject).goal_acceptance_contract as JsonObject;
  assert.deepEqual(gaContract.goal_objective, {schema_version: "goal_objective_revision_v0", objective: "Deliver X"});
  assert.deepEqual(gaContract.checks, ["pytest"]);

  const reqs = gaContract.outcome_requirements as JsonObject;
  assert.equal(reqs.projection_mode, SCOPED_OUTCOME_PROJECTION_MODE);
  assert.equal(reqs.full_outcome_count, 33);
  assert.equal(reqs.inline_outcome_count, 4);
  assert.equal(reqs.compacted_outcome_count, 29);
  assert.equal(reqs.outcome_revision, 33);
  assert.equal(reqs.full_outcomes_sha256, expectedDigest);
  assert.equal(
    reqs.capture_read_command,
    "loopx --format json quota context-read --reference-file state/captures/decision.reader.json --select /deferred_settlement_details/goal_acceptance",
  );
  assert.equal(reqs.cold_read_command, "loopx --format json goal-acceptance inspect --goal-id g1");
  assert.match(String(reqs.cold_read_instruction), /capture_read_command reads immutable admission-time goal_acceptance/);
  assert.match(String(reqs.cold_read_instruction), /--include-detail goal_acceptance/);
  assert.deepEqual((composed.deferred_settlement_details as JsonObject).goal_acceptance, fullOutcomes);

  const outcomes = reqs.outcomes as JsonObject[];
  assert.equal(outcomes.length, 33);
  for (const id of ["out-current", "out-5", "out-8", "out-12"]) {
    const row = outcomes.find(o => o.id === id)!;
    assert.equal(row.summary_only, undefined);
    assert.ok(Array.isArray(row.requirements), `${id} must stay full`);
  }
  const compactRow = outcomes.find(o => o.id === "out-1")!;
  assert.equal(compactRow.summary_only, true);
  assert.equal(compactRow.owner_agent_id, "peer-1");
  assert.equal(compactRow.revision, 2);
  assert.equal(compactRow.todo_count, 1);
  assert.equal(compactRow.basis_digest, "sha256:" + "1".repeat(64));
  assert.equal(compactRow.cold_read_command, "loopx --format json goal-acceptance inspect --goal-id g1");
  assert.equal(compactRow.requirements, undefined);
  assert.equal(compactRow.sources, undefined);
  assert.equal(compactRow.work, undefined);

  // Ref deduplication still holds across planning_horizon and agent_todo_summary.
  const expectedRef = "$.interaction_contract.agent_channel.work_context.sources[1].content.goal_acceptance_contract.outcome_requirements";
  assert.equal((((composed.planning_horizon as JsonObject).core_goal as JsonObject).acceptance_contract as JsonObject).outcome_requirements_ref, expectedRef);
  assert.equal(((composed.agent_todo_summary as JsonObject).goal_acceptance_contract as JsonObject).outcome_requirements_ref, expectedRef);

  // Envelope + host view + progressive unread_context_index stay valid and vastly smaller.
  assert.deepEqual(evaluateTurnEnvelope({operation: "complete_consumer", payload: composed}), {ok: true});
  const envelope = evaluateTurnEnvelope({operation: "build", payload: composed, scheduler_execution_args: ""});
  assert.equal((envelope.action_signature as JsonObject).matches, true);

  const planFull = progressiveContextPlan({payload: source, surface: "quota_packet"});
  const fullComposed = evaluateTurnEnvelope({
    operation: "compose_complete",
    payload: source,
    scheduler_execution_args: "",
    include_goal_acceptance_detail: true,
    include_vision_detail: true,
  });
  const progBaseline = evaluateTurnEnvelope({
    operation: "progressive_context_delivery",
    surface: "complete_turn",
    context: fullComposed,
    native_projection: {path: "source.json", sha256: "a".repeat(64)},
  });
  const progScoped = evaluateTurnEnvelope({
    operation: "progressive_context_delivery",
    surface: "complete_turn",
    context: composed,
    native_projection: {path: "source.json", sha256: "a".repeat(64)},
  });
  assert.ok((planFull.deferred as unknown[]).length > 30);
  const fullWcBytes = Buffer.byteLength(JSON.stringify(((((fullComposed.interaction_contract as JsonObject).agent_channel as JsonObject).work_context as JsonObject).sources as JsonObject[])[1]));
  const scopedWcBytes = Buffer.byteLength(JSON.stringify(wcSources[1]));
  // Including capture_read_command + read_modes on all 29 summary_only rows brings wcSources[1]
  // from ~20% to ~33% of fullWcBytes (>65% savings) while keeping admission readback addressable.
  assert.ok(scopedWcBytes < fullWcBytes * 0.38);
  // First-read progressive delivery strips deferred_settlement_details while keeping captured decision complete.
  assert.ok(Buffer.byteLength(JSON.stringify(progScoped)) < Buffer.byteLength(JSON.stringify(progBaseline)) * 0.30);
});

test("small Goals, uncaptured runs, replan turns, audit required, closeout, detail flags, and fail-closed gates preserve full outcomes", () => {
  const captureOpts = {captureReferenceFile: "state/captures/decision.reader.json"};

  // Small Goal <= 6 outcomes stays verbatim even when captured.
  const small = baseNormalPayload(4);
  assert.equal(scopeTurnGoalAcceptance(small, captureOpts), small);

  // Uncaptured call without captureReferenceFile always returns payload unchanged (100% inline outcomes).
  const uncaptured33 = baseNormalPayload(33);
  assert.equal(scopeTurnGoalAcceptance(uncaptured33), uncaptured33);
  assert.equal(scopeTurnGoalAcceptance(uncaptured33, {}), uncaptured33);

  const uncapturedComposed = evaluateTurnEnvelope({
    operation: "compose_complete",
    payload: uncaptured33,
    scheduler_execution_args: "",
  });
  const uncapturedGa = (((((uncapturedComposed.interaction_contract as JsonObject).agent_channel as JsonObject)
    .work_context as JsonObject).sources as JsonObject[])[1].content as JsonObject)
    .goal_acceptance_contract as JsonObject;
  const uncapturedReqs = uncapturedGa.outcome_requirements as JsonObject;
  assert.equal(uncapturedReqs.projection_mode, undefined);
  assert.equal((uncapturedReqs.outcomes as JsonObject[]).length, 33);
  assert.equal((uncapturedReqs.outcomes as JsonObject[]).every(o => o.summary_only === undefined), true);
  assert.equal(uncapturedComposed.deferred_settlement_details, undefined);

  // Replan, vision audit, closeout, legacy lifetime, goal-wide criteria, missing relation, global gap, detail flag.
  for (const [name, mutate] of [
    ["non_normal_run", (p: JsonObject) => { p.effective_action = "autonomous_replan_required"; }],
    ["replan_action_packet", (p: JsonObject) => { p.replan_action_packet = {obligation_id: "rep-1"}; }],
    ["vision_audit_required", (p: JsonObject) => { p.vision_continuation_audit = {required: true}; }],
    ["closeout_flag", (p: JsonObject) => { p.selected_todo_is_goal_completion = true; }],
    ["legacy_lifetime", (p: JsonObject) => {
      const c = (((((p.interaction_contract as JsonObject).agent_channel as JsonObject).work_context as JsonObject).sources as JsonObject[])[1].content as JsonObject).goal_acceptance_contract as JsonObject;
      (c.outcome_requirements as JsonObject).current_lifetime = false;
    }],
    ["goal_wide_criteria", (p: JsonObject) => {
      const c = (((((p.interaction_contract as JsonObject).agent_channel as JsonObject).work_context as JsonObject).sources as JsonObject[])[1].content as JsonObject).goal_acceptance_contract as JsonObject;
      c.criteria = [{id: "global"}];
    }],
    ["missing_relation", (p: JsonObject) => {
      const oc = (((((p.interaction_contract as JsonObject).agent_channel as JsonObject).work_context as JsonObject).sources as JsonObject[])[1].content as JsonObject).outcome_context as JsonObject;
      oc.coverage_complete = false;
      oc.limitations = ["missing_relation"];
    }],
    ["unlinked_agent_gap", (p: JsonObject) => {
      (p.goal_frontier_projection as JsonObject).acceptance_gaps = [{kind: "vision_gap", agent_id: "a1"}];
    }],
    ["divergent_carriers", (p: JsonObject) => {
      const s = (((((p.interaction_contract as JsonObject).agent_channel as JsonObject).work_context as JsonObject).sources as JsonObject[])[1].content as JsonObject).goal_acceptance_contract as JsonObject;
      ((s.outcome_requirements as JsonObject).outcomes as JsonObject[])[10].objective = "Diverged carrier objective";
    }],
  ] as const) {
    const candidate = baseNormalPayload(33);
    mutate(candidate);
    const out = scopeTurnGoalAcceptance(candidate, captureOpts);
    assert.equal(out, candidate, `${name} must preserve full outcomes`);
  }

  // Explicitly requesting include_goal_acceptance_detail keeps all 33 outcomes verbatim even with captureReferenceFile.
  const source = baseNormalPayload(33);
  const full = evaluateTurnEnvelope({
    operation: "compose_complete",
    payload: source,
    scheduler_execution_args: "",
    capture_reference_file: "state/captures/decision.reader.json",
    include_goal_acceptance_detail: true,
  });
  const wcReqs = (((((full.interaction_contract as JsonObject).agent_channel as JsonObject)
    .work_context as JsonObject).sources as JsonObject[])[1].content as JsonObject)
    .goal_acceptance_contract as JsonObject;
  const outcomeReq = wcReqs.outcome_requirements as JsonObject;
  assert.equal(outcomeReq.projection_mode, undefined);
  assert.equal((outcomeReq.outcomes as JsonObject[]).every(o => o.summary_only === undefined), true);
  assert.equal((full.deferred_settlement_details as JsonObject).goal_acceptance, undefined);
});

test("lean routine settlement_plan compacts static prose and preserves commands, identity, and triggered turns", () => {
  const source = baseNormalPayload(4);
  const composed = evaluateTurnEnvelope({operation: "compose_complete", payload: source, scheduler_execution_args: ""});
  const cli = (composed.interaction_contract as JsonObject).cli_channel as JsonObject;
  const plan = cli.settlement_plan as JsonObject;
  const step1 = (plan.ordered_steps as JsonObject[])[1];

  // Routine step keeps command templates, precondition, identity ref, and expected receipt intact.
  assert.match(String(step1.command_template), /--settle-turn/);
  assert.match(String(step1.semantic_closeout_command_template), /--agent-vision-json/);
  assert.equal(step1.precondition, "Persist evidence after independent checks.");
  assert.equal(step1.idempotency_key_ref, "$.identity.effect_id");
  assert.equal(step1.expected_receipt, "durable_writeback_receipt");

  const va = step1.vision_authoring as JsonObject;
  assert.deepEqual(va, visionAuthoringContract(false));
  assert.equal(Object.keys(va).length, 11);

  const att = (step1.optional_attachments as JsonObject[])[0];
  assert.equal(att.capability_id, "explore");
  assert.equal(att.required, false);
  assert.equal(att.summary_only, true);
  assert.equal(att.option, "--explore-result-json <result.json>");
  assert.match(String(att.cold_read_command), /explore turn-context/);
  assert.equal(att.guidance, undefined);
  assert.ok(Buffer.byteLength(JSON.stringify(att)) < 420);

  // Explore work_context graph drops matching static boilerplate without leaving a dangling ref.
  const exploreGraph = (((((composed.interaction_contract as JsonObject).agent_channel as JsonObject)
    .work_context as JsonObject).sources as JsonObject[])[0].content as JsonObject).graph as JsonObject;
  assert.equal(exploreGraph.result_writeback_guidance, undefined);
  assert.equal(exploreGraph.result_writeback_guidance_ref, undefined);
  assert.equal(exploreGraph.result_writeback_guidance_summary_only, true);
  assert.match(String(exploreGraph.result_writeback_guidance_cold_read_command), /explore turn-context/);

  // Consumer and envelope remain valid.
  assert.deepEqual(evaluateTurnEnvelope({operation: "complete_consumer", payload: composed}), {ok: true});
  const env = evaluateTurnEnvelope({operation: "build", payload: composed, scheduler_execution_args: ""});
  assert.equal((env.action_signature as JsonObject).matches, true);

  // Triggered cases preserve full attachment prose verbatim alongside complete vision_authoring.
  for (const [name, mutate] of [
    ["vision_continuation_audit", (p: JsonObject) => { p.vision_continuation_audit = {required: true}; }],
    ["vision_checkpoint_required", (p: JsonObject) => { p.vision_checkpoint = {required: true}; }],
    ["explore_required_cap", (p: JsonObject) => { (p.selected_todo as JsonObject).required_capabilities = ["explore"]; }],
    ["required_attachment", (p: JsonObject) => {
      const steps = (((p.interaction_contract as JsonObject).cli_channel as JsonObject).settlement_plan as JsonObject).ordered_steps as JsonObject[];
      ((steps[1].optional_attachments as JsonObject[])[0] as JsonObject).required = true;
    }],
  ] as const) {
    const triggered = baseNormalPayload(4);
    mutate(triggered);
    const out = compactRoutineSettlementPlan(triggered);
    const outStep1 = ((((out.interaction_contract as JsonObject).cli_channel as JsonObject).settlement_plan as JsonObject).ordered_steps as JsonObject[])[1];
    assert.deepEqual(outStep1.vision_authoring, visionAuthoringContract(false), `${name} keeps full authoring`);
    assert.ok(typeof ((outStep1.optional_attachments as JsonObject[])[0]).guidance === "string", `${name} keeps attachment`);
  }

  // Tampered scoped capture is rejected fail-closed by complete_consumer.
  const tampered = structuredClone(composed);
  (tampered.contract_binding as JsonObject).content_hash = "sha256:" + "0".repeat(64);
  assert.throws(() => evaluateTurnEnvelope({operation: "complete_consumer", payload: tampered}));
});

test("compactRoutineSettlementPlan preserves immutable deferred attachment details and binds explore cold-reads", () => {
  const source = baseNormalPayload(4);
  source.registry = "state/registry.json";
  source.runtime_root = "state/runtime";
  source.goal_ref = {goal_id: "g1", goal_instance_id: "gi-1"};

  // Captured mode keeps full 11-field vision_authoring at its canonical step location and stores
  // full optional_attachments in decision.json under deferred_settlement_details, dropping them
  // from progressive delivery while binding explore cold-reads to registry & runtime-root.
  const captured = evaluateTurnEnvelope({
    operation: "compose_complete",
    payload: source,
    scheduler_execution_args: " --codex-app",
    cli_argv: ["loopx"],
    capture_reference_file: "state/captures/decision.reader.json",
  });
  const capturedCli = (captured.interaction_contract as JsonObject).cli_channel as JsonObject;
  const capturedStep1 = ((capturedCli.settlement_plan as JsonObject).ordered_steps as JsonObject[])[1];
  assert.deepEqual(capturedStep1.vision_authoring, visionAuthoringContract(false));
  assert.equal(Object.keys(capturedStep1.vision_authoring as JsonObject).length, 11);
  const capturedAtt = (capturedStep1.optional_attachments as JsonObject[])[0];
  assert.equal(
    capturedAtt.cold_read_command,
    "loopx --registry state/registry.json --runtime-root state/runtime --format json explore turn-context --goal-id g1 --agent-id a1 --todo-id todo-current",
  );
  const deferred = captured.deferred_settlement_details as JsonObject;
  assert.deepEqual(deferred.optional_attachments, [exploreResultWritebackAffordance()]);
  assert.deepEqual(evaluateTurnEnvelope({operation: "complete_consumer", payload: captured}), {ok: true});
  const capturedEnv = evaluateTurnEnvelope({operation: "build", payload: captured, scheduler_execution_args: " --codex-app"});
  assert.equal((capturedEnv.action_signature as JsonObject).matches, true);

  const stripped = structuredClone(captured);
  delete stripped.schema_version;
  delete stripped.contract_binding;
  const plan = progressiveContextPlan({payload: stripped, surface: "quota_packet"});
  assert.ok((plan.deferred as JsonObject[]).some(
    entry => JSON.stringify(entry.path) === JSON.stringify(["deferred_settlement_details"]),
  ));

  const deliveryInput = {
    operation: "progressive_context_delivery",
    surface: "complete_turn",
    context: captured,
    native_projection: {path: "state/captures/decision.json", sha256: "a".repeat(64)},
  };
  const at = (root: unknown, path: (string | number)[]) => path.reduce<unknown>(
    (row, key) => (row as Record<string | number, unknown>)[key],
    root,
  );
  const delivered = structuredClone(deliveryInput);
  for (const change of evaluateTurnEnvelope(deliveryInput).changes as JsonObject[]) {
    const path = change.path as (string | number)[];
    const parent = at(delivered, path.slice(0, -1)) as Record<string | number, unknown>;
    const key = path.at(-1)!;
    if (change.remove) delete parent[key];
    else parent[key] = structuredClone(change.from ? at(deliveryInput, change.from as (string | number)[]) : change.value);
  }
  assert.equal((delivered.context as JsonObject).deferred_settlement_details, undefined);

  // Uncaptured mode omits deferred_settlement_details while still inheriting registry & runtime-root
  // in explore cold_read_command and retaining complete 11-field vision_authoring.
  const uncaptured = evaluateTurnEnvelope({
    operation: "compose_complete",
    payload: source,
    scheduler_execution_args: " --codex-app",
    cli_argv: ["loopx"],
  });
  assert.equal(uncaptured.deferred_settlement_details, undefined);
  const uncapturedCli = (uncaptured.interaction_contract as JsonObject).cli_channel as JsonObject;
  const uncapturedStep1 = ((uncapturedCli.settlement_plan as JsonObject).ordered_steps as JsonObject[])[1];
  assert.deepEqual(uncapturedStep1.vision_authoring, visionAuthoringContract(false));
  assert.equal(
    ((uncapturedStep1.optional_attachments as JsonObject[])[0]).cold_read_command,
    "loopx --registry state/registry.json --runtime-root state/runtime --format json explore turn-context --goal-id g1 --agent-id a1 --todo-id todo-current",
  );
});

test("scoped goal_acceptance cold_read_command binds original cli_argv, registry, and runtime_root across summary, rows, and first read", () => {
  const source = baseNormalPayload(9);
  source.registry = "state/ws a/registry.json";
  source.runtime_root = "state/ws a/runtime";
  const rawOutcomes = (((((((source.interaction_contract as JsonObject).agent_channel as JsonObject)
    .work_context as JsonObject).sources as JsonObject[])[1].content as JsonObject)
    .goal_acceptance_contract as JsonObject).outcome_requirements as JsonObject).outcomes as JsonObject[];
  const expectedOutcomesSha = canonicalHash(rawOutcomes);

  const composed = evaluateTurnEnvelope({
    operation: "compose_complete",
    payload: source,
    scheduler_execution_args: "",
    cli_argv: ["/opt/custom loopx", "--cli-bin", "/opt/custom loopx"],
    capture_reference_file: "state/captures/decision.reader.json",
  });
  const expectedCmd =
    "'/opt/custom loopx' --cli-bin '/opt/custom loopx' --registry 'state/ws a/registry.json' --runtime-root 'state/ws a/runtime' --format json goal-acceptance inspect --goal-id g1";
  const expectedCaptureCmd =
    "'/opt/custom loopx' --cli-bin '/opt/custom loopx' --format json quota context-read --reference-file state/captures/decision.reader.json --select /deferred_settlement_details/goal_acceptance";
  const wcReqs = ((((((composed.interaction_contract as JsonObject).agent_channel as JsonObject)
    .work_context as JsonObject).sources as JsonObject[])[1].content as JsonObject)
    .goal_acceptance_contract as JsonObject).outcome_requirements as JsonObject;
  assert.equal(wcReqs.projection_mode, SCOPED_OUTCOME_PROJECTION_MODE);
  assert.equal(wcReqs.full_outcome_count, 9);
  assert.equal(wcReqs.outcome_revision, 33);
  assert.equal(wcReqs.full_outcomes_sha256, expectedOutcomesSha);
  assert.equal(wcReqs.capture_read_command, expectedCaptureCmd);
  assert.equal(wcReqs.cold_read_command, expectedCmd);
  assert.match(String(wcReqs.cold_read_instruction), /capture_read_command reads immutable admission-time goal_acceptance/);
  assert.match(String(wcReqs.cold_read_instruction), /cold_read_command and --include-detail goal_acceptance query current live workspace state/);

  const compactRows = (wcReqs.outcomes as JsonObject[]).filter(row => row.summary_only === true);
  assert.equal(compactRows.length, 8);
  assert.ok(compactRows.every(row => row.cold_read_command === expectedCmd));

  // Progressive first-read retains summary metadata, full_outcomes_sha256, outcome_revision, and bound commands.
  const deliveryInput = {
    operation: "progressive_context_delivery",
    surface: "complete_turn",
    context: composed,
    native_projection: {path: "state/captures/decision.json", sha256: "a".repeat(64)},
  };
  const at = (root: unknown, path: (string | number)[]) => path.reduce<unknown>(
    (row, key) => (row as Record<string | number, unknown>)[key],
    root,
  );
  const delivered = structuredClone(deliveryInput);
  for (const change of evaluateTurnEnvelope(deliveryInput).changes as JsonObject[]) {
    const path = change.path as (string | number)[];
    const parent = at(delivered, path.slice(0, -1)) as Record<string | number, unknown>;
    const key = path.at(-1)!;
    if (change.remove) delete parent[key];
    else parent[key] = structuredClone(change.from ? at(deliveryInput, change.from as (string | number)[]) : change.value);
  }
  const deliveredReqs = (((((((delivered.context as JsonObject).interaction_contract as JsonObject)
    .agent_channel as JsonObject).work_context as JsonObject).sources as JsonObject[])[1].content as JsonObject)
    .goal_acceptance_contract as JsonObject).outcome_requirements as JsonObject;
  assert.equal(deliveredReqs.cold_read_command, expectedCmd);
  assert.equal(deliveredReqs.full_outcomes_sha256, expectedOutcomesSha);
  assert.equal(deliveredReqs.outcome_revision, 33);
  assert.equal(deliveredReqs.cold_read_instruction, wcReqs.cold_read_instruction);
  const deliveredCompact = (deliveredReqs.outcomes as JsonObject[]).filter(row => row.summary_only === true);
  assert.equal(deliveredCompact.length, 8);
  assert.ok(deliveredCompact.every(row => row.cold_read_command === expectedCmd));

  // summaryOutcomeIndexRow via replanGoalContext preserves source-bound cold_read_command on summary_only rows.
  const gaContract = (((((composed.interaction_contract as JsonObject).agent_channel as JsonObject)
    .work_context as JsonObject).sources as JsonObject[])[1].content as JsonObject).goal_acceptance_contract as JsonObject;
  const indexed = replanGoalContext({
    ...composed,
    autonomous_replan_obligation: {replan_context: {core_goal: {acceptance_contract: gaContract}}},
  }, {acceptance: "loopx quota replan-view --decision-file d.json"});
  const indexRows = ((indexed.acceptanceIndex as JsonObject).outcomes as JsonObject[]).filter(r => r.summary_only === true);
  assert.equal(indexRows.length, 8);
  assert.ok(indexRows.every(row => row.cold_read_command === expectedCmd));

  // cli_registry / cli_runtime_root overrides bind cold reads without altering payload.registry/runtime_root.
  const anchored = evaluateTurnEnvelope({
    operation: "compose_complete",
    payload: source,
    scheduler_execution_args: "",
    cli_argv: ["loopx"],
    capture_reference_file: "state/captures/decision.reader.json",
    cli_registry: "/workspace/ws a/.loopx/registry.json",
    cli_runtime_root: "/workspace/ws a/runtime",
  });
  const anchoredReqs = ((((((anchored.interaction_contract as JsonObject).agent_channel as JsonObject)
    .work_context as JsonObject).sources as JsonObject[])[1].content as JsonObject)
    .goal_acceptance_contract as JsonObject).outcome_requirements as JsonObject;
  assert.equal(
    anchoredReqs.cold_read_command,
    "loopx --registry '/workspace/ws a/.loopx/registry.json' --runtime-root '/workspace/ws a/runtime' --format json goal-acceptance inspect --goal-id g1",
  );
  assert.equal(anchored.registry, "state/ws a/registry.json");
  assert.equal(anchored.runtime_root, "state/ws a/runtime");
});

test("captured scoped goal_acceptance preserves full admission outcomes in deferred_settlement_details and separates capture_read_command from live cold_read_command", () => {
  const source = baseNormalPayload(9);
  source.registry = "state/ws a/registry.json";
  source.runtime_root = "state/ws a/runtime";
  const originalContract = structuredClone(
    ((((source.interaction_contract as JsonObject).agent_channel as JsonObject)
      .work_context as JsonObject).sources as JsonObject[])[1].content as JsonObject,
  ).goal_acceptance_contract as JsonObject;
  const originalReqs = originalContract.outcome_requirements as JsonObject;
  const expectedOutcomesSha = canonicalHash(originalReqs.outcomes);

  const captured = evaluateTurnEnvelope({
    operation: "compose_complete",
    payload: source,
    scheduler_execution_args: "",
    cli_argv: ["/opt/custom loopx", "--cli-bin", "/opt/custom loopx"],
    capture_reference_file: "state/captures/decision.reader.json",
  });

  // 1. Captured decision preserves BOTH optional_attachments and unmodified admission-time goal_acceptance.
  const deferred = captured.deferred_settlement_details as JsonObject;
  assert.deepEqual(deferred.optional_attachments, [exploreResultWritebackAffordance()]);
  assert.deepEqual(deferred.goal_acceptance, originalContract);

  // 2. Summary & summary_only rows distinguish immutable capture_read_command from live cold_read_command.
  const wcReqs = ((((((captured.interaction_contract as JsonObject).agent_channel as JsonObject)
    .work_context as JsonObject).sources as JsonObject[])[1].content as JsonObject)
    .goal_acceptance_contract as JsonObject).outcome_requirements as JsonObject;
  const expectedSummaryCaptureCmd =
    "'/opt/custom loopx' --cli-bin '/opt/custom loopx' --format json quota context-read --reference-file state/captures/decision.reader.json --select /deferred_settlement_details/goal_acceptance";
  const expectedLiveCmd =
    "'/opt/custom loopx' --cli-bin '/opt/custom loopx' --registry 'state/ws a/registry.json' --runtime-root 'state/ws a/runtime' --format json goal-acceptance inspect --goal-id g1";
  assert.equal(wcReqs.capture_read_command, expectedSummaryCaptureCmd);
  assert.equal(wcReqs.cold_read_command, expectedLiveCmd);
  assert.equal(wcReqs.full_outcomes_sha256, expectedOutcomesSha);
  assert.equal(wcReqs.outcome_revision, 33);
  assert.match(String(wcReqs.cold_read_instruction), /capture_read_command reads immutable admission-time goal_acceptance/);
  assert.match(String(wcReqs.cold_read_instruction), /cold_read_command and --include-detail goal_acceptance query current live workspace state/);

  const compactRows = (wcReqs.outcomes as JsonObject[]).filter(row => row.summary_only === true);
  assert.equal(compactRows.length, 8);
  compactRows.forEach((row, idx) => {
    const outcomeIndex = idx + 1;
    assert.equal(
      row.capture_read_command,
      `'/opt/custom loopx' --cli-bin '/opt/custom loopx' --format json quota context-read --reference-file state/captures/decision.reader.json --select /deferred_settlement_details/goal_acceptance/outcome_requirements/outcomes/${outcomeIndex}`,
    );
    assert.equal(row.cold_read_command, expectedLiveCmd);
    assert.match(String(row.read_modes), /capture_read_command=immutable admission baseline/);
    assert.match(String(row.read_modes), /cold_read_command=live workspace inspect/);
  });

  // 3. Consumer validation, build, progressive_context_delivery, and replanGoalContext keep deferred_settlement_details pruned in first read.
  assert.deepEqual(evaluateTurnEnvelope({operation: "complete_consumer", payload: captured}), {ok: true});
  const env = evaluateTurnEnvelope({operation: "build", payload: captured, scheduler_execution_args: ""});
  assert.equal((env.action_signature as JsonObject).matches, true);

  const stripped = structuredClone(captured);
  delete stripped.schema_version;
  delete stripped.contract_binding;
  const plan = progressiveContextPlan({payload: stripped, surface: "quota_packet"});
  const deferredEntry = (plan.deferred as JsonObject[]).find(
    entry => JSON.stringify(entry.path) === JSON.stringify(["deferred_settlement_details"]),
  )!;
  assert.match(String(deferredEntry.read_instruction), /capture_read_command/);

  const deliveryInput = {
    operation: "progressive_context_delivery",
    surface: "complete_turn",
    context: captured,
    native_projection: {path: "state/captures/decision.json", sha256: "a".repeat(64)},
  };
  const at = (root: unknown, path: (string | number)[]) => path.reduce<unknown>(
    (row, key) => (row as Record<string | number, unknown>)[key],
    root,
  );
  const delivered = structuredClone(deliveryInput);
  for (const change of evaluateTurnEnvelope(deliveryInput).changes as JsonObject[]) {
    const path = change.path as (string | number)[];
    const parent = at(delivered, path.slice(0, -1)) as Record<string | number, unknown>;
    const key = path.at(-1)!;
    if (change.remove) delete parent[key];
    else parent[key] = structuredClone(change.from ? at(deliveryInput, change.from as (string | number)[]) : change.value);
  }
  assert.equal((delivered.context as JsonObject).deferred_settlement_details, undefined);
  const deliveredReqs = (((((((delivered.context as JsonObject).interaction_contract as JsonObject)
    .agent_channel as JsonObject).work_context as JsonObject).sources as JsonObject[])[1].content as JsonObject)
    .goal_acceptance_contract as JsonObject).outcome_requirements as JsonObject;
  assert.equal(deliveredReqs.capture_read_command, expectedSummaryCaptureCmd);
  assert.equal(deliveredReqs.cold_read_command, expectedLiveCmd);
  assert.equal(deliveredReqs.cold_read_instruction, wcReqs.cold_read_instruction);
  const deliveredCompact = (deliveredReqs.outcomes as JsonObject[]).filter(row => row.summary_only === true);
  assert.equal(deliveredCompact.length, 8);
  assert.equal(deliveredCompact[0].capture_read_command, compactRows[0].capture_read_command);
  assert.equal(deliveredCompact[0].read_modes, compactRows[0].read_modes);

  const gaContract = (((((captured.interaction_contract as JsonObject).agent_channel as JsonObject)
    .work_context as JsonObject).sources as JsonObject[])[1].content as JsonObject).goal_acceptance_contract as JsonObject;
  const indexed = replanGoalContext({
    ...captured,
    autonomous_replan_obligation: {replan_context: {core_goal: {acceptance_contract: gaContract}}},
  }, {acceptance: "loopx quota replan-view --decision-file d.json"});
  const indexRows = ((indexed.acceptanceIndex as JsonObject).outcomes as JsonObject[]).filter(r => r.summary_only === true);
  assert.equal(indexRows.length, 8);
  assert.equal(indexRows[0].capture_read_command, compactRows[0].capture_read_command);
  assert.equal(indexRows[0].read_modes, compactRows[0].read_modes);
});
