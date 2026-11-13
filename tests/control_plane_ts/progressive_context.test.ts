import assert from "node:assert/strict";
import test from "node:test";
import {settlementIdentity, settlementIdentityPayload} from "../../loopx/control_plane/effect_program.ts";
import {visionAuthoringContract} from "../../loopx/control_plane/goals/vision_checkpoint.ts";
import {contextReadSectionDigests, initialContextReadPointers,
  progressiveContextPlan} from "../../loopx/control_plane/quota/progressive_context.ts";
import {FULL_OUTCOME_READ_GUIDANCE, PROGRESSIVE_OUTCOME_READ_GUIDANCE} from "../../loopx/control_plane/work_items/interaction_contract.ts";

const outcome = {id: "o", objective: "Keep all requirements.", revision: 1, owner_agent_id: "a", todo_ids: ["t"],
  sources: [{ref: "original", summary: "Supporting history."}], requirements: [{id: "r", description: "Final stop.",
    status: "unverified", planning_satisfied: false, agent_review: {id: "r", conclusion: "unmet", note: "Why it failed.",
      evidence_refs: ["negative"], basis_current: false, independently_verified: false}}]};
const source = {source: "goal_acceptance", reason: "Adopt the current objective. " + FULL_OUTCOME_READ_GUIDANCE,
  content: {status: "loaded", goal_acceptance_contract: {outcome_requirements: {
    schema_version: "loopx_goal_outcomes_v0", outcomes: [outcome]}}}};

test("initial navigation keeps unknown supporting-index obligations whole", () => {
  const known = {coverage_confirmed: false, read_before_dependent_work: true,
    pointers: ["/supporting/evidence"], source: {path: "complete.json", sha256: "fixture-digest"}};
  const base = "/context_details/unread_context_index";
  for (const index of [{...known, future_obligation: "Preserve the newly required stop."},
    {...known, coverage_confirmed: true}, {...known, read_before_dependent_work: false},
    {...known, pointers: [42]}, {...known, source: null}, {...known, source_blocks: [42]}]) {
    const context = {interaction_contract: {schema_version: "loopx_interaction_contract_v0"},
      context_details: {instruction: "Read applicable supporting evidence before dependent work.", unread_context_index: index}};
    const before = structuredClone(context);
    assert.ok(initialContextReadPointers(context, []).includes(base));
    assert.deepEqual(context, before);
  }
  assert.ok(initialContextReadPointers({interaction_contract: {schema_version: "loopx_interaction_contract_v0"},
    context_details: {unread_context_index: known}}, []).includes(base));
  assert.deepEqual(initialContextReadPointers({interaction_contract: {schema_version: "loopx_interaction_contract_v0"}}, []), []);
  for (const index of [known, {...known, source_blocks: ["/supporting/source/content"]}]) {
    const pointers = initialContextReadPointers({interaction_contract: {schema_version: "loopx_interaction_contract_v0"},
      context_details: {instruction: "Read applicable evidence before dependent work.", unread_context_index: index}}, []);
    assert.ok(pointers.includes(`${base}/source`));
    assert.ok(pointers.includes(`${base}/read_before_dependent_work`));
    assert.ok(pointers.includes("/context_details/instruction"));
    assert.ok(!pointers.includes(base), "address inventories remain reachable without crowding out current obligations");
  }
});

test("initial outcome ids carry only verified supporting-material addresses", () => {
  const base = "/interaction_contract/agent_channel/work_context/sources/0/content/goal_acceptance_contract/outcome_requirements/outcomes";
  const context = {interaction_contract: {schema_version: "loopx_interaction_contract_v0",
    agent_channel: {work_context: {sources: [{...source, content: {goal_acceptance_contract: {
      outcome_requirements: {schema_version: "loopx_goal_outcomes_v0", outcomes: [
        {id: "canonical", supporting_material_pointer: base + "/0"},
        {id: "alternate", supporting_material_pointer: "/actual/source"},
        {supporting_material_pointer: base + "/2"},
      ]}}}}]}}},
    context_details: {path: "complete.json", sha256: "fixture-digest", outcome_support_from_id: true,
      instruction: "Derive the outcome address from the selected id.",
      unread_context_index: {coverage_confirmed: false, read_before_dependent_work: true, pointers: [base + "/0/sources"],
        source: {path: "complete.json", sha256: "fixture-digest"}}}};
  const canonicalAlias = base + "/0/supporting_material_pointer";
  const before = structuredClone(context);
  const first = initialContextReadPointers(context, []);
  assert.ok(first.includes(base + "/0/id") && !first.includes(canonicalAlias));
  assert.ok(first.includes(base + "/1/supporting_material_pointer"), "preserve an explicit different address");
  assert.ok(first.includes(base + "/2/supporting_material_pointer"), "a missing id cannot carry the address");
  assert.ok(first.includes("/context_details/instruction"));
  assert.deepEqual(context, before, "the captured aliases still exist");
  for (const changed of [{...context.context_details, outcome_support_from_id: undefined},
    {...context.context_details, path: "different.json"}, {...context.context_details, sha256: "different-digest"},
    {...context.context_details, instruction: undefined}, {...context.context_details,
      unread_context_index: {...context.context_details.unread_context_index, future_obligation: true}}]) {
    assert.ok(initialContextReadPointers({...context, context_details: changed}, []).includes(canonicalAlias));
  }
});

test("only supporting materials move; cumulative obligations and merged source guidance survive", () => {
  const payload = {ok: true, interaction_contract: {agent_channel: {work_context: {complete: true, sources: [source]}}}};
  const before = structuredClone(payload);
  const plan = progressiveContextPlan({payload, surface: "quota_packet"});
  const parent = ["interaction_contract", "agent_channel", "work_context", "sources", 0,
    "content", "goal_acceptance_contract", "outcome_requirements", "outcomes", 0];
  assert.deepEqual(plan.deferred, [
    {path: [...parent, "sources"], parent, index_key: "supporting_material_pointer"},
    {path: [...parent, "requirements", 0, "agent_review", "note"], parent, index_key: "supporting_material_pointer"},
    {path: [...parent, "requirements", 0, "agent_review", "evidence_refs"], parent, index_key: "supporting_material_pointer"},
  ]);
  assert.deepEqual(plan.instructions, [{path: ["interaction_contract", "agent_channel", "work_context", "sources", 0, "reason"],
    value: "Adopt the current objective. " + PROGRESSIVE_OUTCOME_READ_GUIDANCE}]);
  assert.deepEqual(payload, before);
  assert.deepEqual(progressiveContextPlan({payload, surface: "turn_envelope"}), {deferred: [], instructions: []});
});

test("unknown and failed source semantics remain whole rather than becoming guessed requirements", () => {
  for (const changed of [{...source, content: {...source.content, ok: false}},
    {...source, content: {...source.content, status: "failed"}}, {...source, source: "unregistered"}]) {
    assert.deepEqual(progressiveContextPlan({surface: "quota_packet", payload: {ok: true,
      interaction_contract: {agent_channel: {work_context: {sources: [changed]}}}}}), {deferred: [], instructions: []});
  }
  const custom = {...source, reason: "Unknown independently required instruction."};
  const plan = progressiveContextPlan({surface: "quota_packet", payload: {ok: true,
    interaction_contract: {agent_channel: {work_context: {sources: [custom]}}}}});
  assert.deepEqual(plan.instructions, []);
  assert.deepEqual(plan.deferred, []);
});

test("selected Todo append proofs require an exact current-source prefix", () => {
  const note = "Keep the complete acceptance evidence. ".repeat(40);
  const context = (value: string, source = "provider") => ({
    goal_id: "goal", registry: "registry", runtime_root: "runtime",
    selected_todo: {todo_id: "todo"},
    interaction_contract: {agent_channel: {work_context: {
      selected_todo_text_verified: true,
      sources: [{source: "selected_todo", content: {ok: true, matched: true, ambiguous: false,
        source, todo: {todo_id: "todo", note: value},
        authority_read: {decision_read_from_provider: true, legacy_fallback_used: false,
          source_authority: "provider"}}}]}}},
  });
  const path = ["interaction_contract", "agent_channel", "work_context", "sources", 0,
    "content", "todo", "note"];
  const address = "/interaction_contract/agent_channel/work_context/sources/0/content/todo/note";
  const section = (value: string, consumed?: string, append?: {pointer: string; prefix_bytes: number},
      source = "provider") => contextReadSectionDigests({context: context(value, source),
    ...(consumed === undefined ? {} : {consumed_pointers: {[address]: consumed}}),
    ...(append === undefined ? {} : {observed_text_append: append})},
    consumed === undefined ? [path] : [])[address];

  const original = section(note)!;
  assert.match(original, /^\d+:[a-f0-9]{64}$/);
  assert.equal(original.split(":")[0], String(Buffer.byteLength(note)));
  const suffix = "Appended requirement.";
  const appended = note + suffix;
  const current = section(appended, original,
    {pointer: address, prefix_bytes: Buffer.byteLength(note)});
  assert.match(current!, /^\d+:[a-f0-9]{64}$/);
  assert.deepEqual(section(appended, original,
    {pointer: address, prefix_bytes: Buffer.byteLength(note) - 1}), undefined);
  assert.equal(section(`Replacement. ${note}`, original,
    {pointer: address, prefix_bytes: Buffer.byteLength(note)}), undefined);
  assert.equal(section(appended, original,
    {pointer: address, prefix_bytes: Buffer.byteLength(note)}, "other-provider"), undefined);

  const legacy = original.slice(original.indexOf(":") + 1);
  assert.match(section(note, legacy)!, /^\d+:[a-f0-9]{64}$/);
  assert.equal(section(appended, legacy,
    {pointer: address, prefix_bytes: Buffer.byteLength(note)}), undefined);
});

test("exact repeated Todo rows preserve membership without collapsing distinct evidence or revisions", () => {
  const row = {schema_version: "todo_item_v0", todo_id: "same-title", text: "Shared title.",
    note: "Keep this complete final constraint. ".repeat(20), content_revision: "revision:one",
    claimed_by: "peer", resume_condition: {schema_version: "todo_resume_condition_v0", satisfied: false}};
  const revised = {...row, content_revision: "revision:two", note: "Contrary evidence requires reconsideration."};
  const summary = {schema_version: "todo_summary_v0", first_open_items: [row, revised],
    backlog_items: [row, revised, {...row, claimed_by: "other"}]};
  const payload = {ok: true, agent_todo_summary: summary};
  const before = structuredClone(payload);
  const plan = progressiveContextPlan({surface: "quota_packet", payload});
  assert.deepEqual(plan.references, [
    {path: ["agent_todo_summary", "backlog_items", 0], from: ["agent_todo_summary", "first_open_items", 0],
      value: {todo_id: row.todo_id, context_ref: "/agent_todo_summary/first_open_items/0"}},
    {path: ["agent_todo_summary", "backlog_items", 1], from: ["agent_todo_summary", "first_open_items", 1],
      value: {todo_id: row.todo_id, context_ref: "/agent_todo_summary/first_open_items/1"}},
  ]);
  assert.deepEqual(plan.deferred, []);
  assert.deepEqual(payload, before);
  const pendingSource = {...source, content: {...source.content, read_complete: false}};
  const pendingPlan = progressiveContextPlan({surface: "quota_packet", payload: {...payload,
    interaction_contract: {agent_channel: {work_context: {sources: [pendingSource]},
      required_reads: [{source: "goal_acceptance", reason: FULL_OUTCOME_READ_GUIDANCE}]}}}});
  assert.deepEqual(pendingPlan.deferred, []);
  assert.deepEqual(pendingPlan.instructions.map(row => row.path), [["context_reference_instruction"]]);
  for (const unknown of [{...row, new_obligation: "Keep inline."}, {...row, schema_version: "future"},
    {...row, resume_monitor_generation: 2 ** 60}, {...row, resume_monitor_generation: 1.25}]) {
    const view = {ok: true, agent_todo_summary: {...summary, first_open_items: [unknown], backlog_items: [unknown]}};
    assert.equal(progressiveContextPlan({surface: "quota_packet", payload: view}).references, undefined);
  }
  assert.equal(progressiveContextPlan({surface: "quota_packet", payload: {...payload,
    contract_binding: {signature: "signed"}}}).references, undefined);
});

test("registered claim-scope rows reuse exact literals without changing membership or counts", () => {
  const row = {schema_version: "todo_item_v0", todo_id: "peer-work", claimed_by: "peer",
    text: "Preserve the complete peer constraints. ".repeat(16), content_revision: "one"};
  const revised = {...row, content_revision: "two", note: "Keep new evidence."};
  const scope = {schema_version: "agent_claim_scope_v0", other_agent_claimed_open_count: 7,
    other_agent_claimed_items: [revised, row], blocked_claimed_open_count: 7,
    blocked_claimed_items: [row, {...row, claimed_by: "another-peer"}],
    future_items: [row]};
  const payload = {ok: true, agent_todo_summary: {schema_version: "todo_summary_v0",
    claimed_by_others_items: [row, revised], claim_scope: scope}};
  const before = structuredClone(payload);
  const plan = progressiveContextPlan({surface: "quota_packet", payload});
  const literal = (index: number) => ["agent_todo_summary", "claimed_by_others_items", index];
  const reference = (lane: string, index: number, owner: number) => ({
    path: ["agent_todo_summary", "claim_scope", lane, index], from: literal(owner),
    value: {todo_id: row.todo_id, context_ref: "/agent_todo_summary/claimed_by_others_items/" + owner},
  });
  assert.deepEqual(plan.references, [reference("other_agent_claimed_items", 0, 1),
    reference("other_agent_claimed_items", 1, 0), reference("blocked_claimed_items", 0, 0)]);
  assert.deepEqual(plan.deferred, []);
  assert.deepEqual(payload, before);
  for (const changed of [{...scope, schema_version: "future_scope"}, {...scope, schema_version: undefined}]) {
    const future = {...payload, agent_todo_summary: {...payload.agent_todo_summary, claim_scope: changed}};
    assert.equal(progressiveContextPlan({surface: "quota_packet", payload: future}).references, undefined);
  }
  for (const changed of [{...row, future_obligation: "Keep inline."},
    {...row, resume_monitor_generation: 1.25}, {...row, schema_version: "future_item"}]) {
    const unknown = {...payload, agent_todo_summary: {...payload.agent_todo_summary,
      claim_scope: {...scope, other_agent_claimed_items: [changed], blocked_claimed_items: []}}};
    assert.equal(progressiveContextPlan({surface: "quota_packet", payload: unknown}).references, undefined);
  }
});

test("first read keeps decision rules and preserves nonstandard Vision obligations whole", () => {
  const base = "/interaction_contract/cli_channel/settlement_plan/ordered_steps/0/vision_authoring";
  for (const replan of [true, false]) {
    const schema = visionAuthoringContract(replan);
    const required = {kind: "durable_writeback", precondition: "Verify current evidence first.", vision_authoring: schema};
    const context = {interaction_contract: {schema_version: "loopx_interaction_contract_v0", cli_channel: {
      settlement_plan: {schema_version: "quota_settlement_plan_v1", ordered_steps: [required]},
    }}};
    const pointers = initialContextReadPointers(context, []);
    assert.ok(pointers.includes(base + "/rule"));
    assert.equal(pointers.includes(base + "/continuation_review"), Object.hasOwn(schema, "continuation_review"));
    assert.ok(!pointers.includes(base));
    required.vision_authoring = {...schema, new_stop: "Keep this changed policy visible."};
    assert.ok(initialContextReadPointers(context, []).includes(base));
  }
});

test("soft-claim first read shares only the original bound settlement commands", () => {
  const identity = settlementIdentityPayload(settlementIdentity({goal_id: "goal", agent_id: "agent",
    todo_id: "todo_work", turn_instance_id: "turn"}));
  const receipt = {status: "upgraded", closeout_required: true, turn_instance_id: "turn", settlement_identity: identity};
  const actions = ["refresh-state --settle-turn --settlement-receipt"];
  const plan = {schema_version: "quota_settlement_plan_v2", identity, ordered_steps: [
    {kind: "durable_writeback", owner: "agent", command_template: actions[0]},
    {kind: "quota_spend", owner: "runtime"},
  ]};
  const cli = {spend_after_validation: true, next_cli_actions: actions, settlement_plan: plan};
  const interaction = {schema_version: "loopx_interaction_contract_v0", user_channel: {action_required: false},
    agent_channel: {delivery_allowed: true}, cli_channel: cli};
  const context = {ok: true, should_run: true, effective_action: "normal_run", normal_delivery_allowed: true,
    goal_id: "goal", agent_identity: {agent_id: "agent"}, selected_todo: {todo_id: "todo_work"},
    goal_boundary: {handoff_mode: "soft_claim"}, heartbeat_receipt: receipt, interaction_contract: interaction};
  const next = "/interaction_contract/cli_channel/next_cli_actions";
  const kept = initialContextReadPointers(context, [], context.goal_boundary.handoff_mode);
  assert.ok(!kept.includes(next));
  assert.ok(kept.includes("/interaction_contract/cli_channel/settlement_plan/ordered_steps/0/command_template"));
  const withCli = (changed: unknown) => ({...context, interaction_contract: {...interaction, cli_channel: changed}});
  for (const changed of [
    {...context, heartbeat_receipt: {...receipt, turn_instance_id: "another-turn"}},
    {...context, heartbeat_receipt: {...receipt, settlement_binding_owed: true}},
    {...context, goal_boundary: {handoff_mode: "hard_lease"}},
    withCli({...cli, selection_required: true}),
    withCli({...cli, execution_preparation: {state: "claim_required"}}),
    withCli({...cli, next_cli_actions: [...actions, "recover a separate effect"]}),
    withCli({...cli, settlement_plan: {...plan, schema_version: "future"}}),
  ]) assert.ok(initialContextReadPointers(changed, [], changed.goal_boundary.handoff_mode).includes(next));
});
