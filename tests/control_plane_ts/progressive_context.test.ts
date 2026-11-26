import assert from "node:assert/strict";
import test from "node:test";
import {visionAuthoringContract} from "../../loopx/control_plane/goals/vision_checkpoint.ts";
import {contextReadSectionDigests, initialContextReadPointers,
  prepareProgressiveContextDelivery, progressiveContextPlan} from "../../loopx/control_plane/quota/progressive_context.ts";
import {evaluateTurnEnvelope} from "../../loopx/control_plane/quota/turn_envelope.ts";
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

test("ownership transfers keep declaration and review attribution in the required view", () => {
  const review = outcome.requirements[0].agent_review;
  const transferred = (declarer: unknown, reviewer: unknown) => ({
    ...outcome, declaration_owner_agent_id: declarer, requirements: [{...outcome.requirements[0],
      agent_review: {...review, recorded_for_agent_id: reviewer}}],
  });
  const plan = (row: unknown) => {
    const outcome_requirements = {schema_version: "loopx_goal_outcomes_v0", outcomes: [row]};
    const content = {...source.content, goal_acceptance_contract: {outcome_requirements}};
    const work_context = {sources: [{...source, content}]};
    return progressiveContextPlan({surface: "quota_packet", payload: {
      ok: true, interaction_contract: {agent_channel: {work_context}}}});
  };
  const legacy = plan(outcome);
  assert.equal(legacy.deferred.length, 3);
  for (const reviewer of ["prior-owner", outcome.owner_agent_id, "another:reviewer"]) {
    assert.deepEqual(plan(transferred("prior-owner", reviewer)), legacy);
  }
  for (const malformed of ["", "owner/path", "owner\n", "a".repeat(129), null, {}]) {
    assert.deepEqual(plan(transferred(malformed, "prior-reviewer")).deferred, []);
    assert.deepEqual(plan(transferred("prior-owner", malformed)).deferred, []);
  }
  assert.deepEqual(plan({...transferred("prior-owner", "prior-reviewer"), future_stop: true}).deferred, []);
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

test("bounded ready opening discloses omitted suggested outcome parents before dependent work", () => {
  const identity = {schema_version: "quota_settlement_identity_v0", effect_id: "effect-1",
    goal_id: "goal-1", agent_id: "agent-1", todo_id: "todo-1", turn_instance_id: "turn-1"};
  const makeOutcome = (id: string, description: string) => ({
    id, objective: `Verify ${id}.`, revision: 1, owner_agent_id: "agent-1", todo_ids: ["todo-1"],
    sources: [{ref: `${id}:evidence`, summary: "Supporting evidence. ".repeat(120)}],
    requirements: [{id: `${id}-req`, description, status: "unverified", planning_satisfied: false}],
  });
  const baseContext = {
    ok: true, goal_id: "goal-1", agent_id: "agent-1", agent_identity: {agent_id: "agent-1"},
    should_run: true, effective_action: "normal_run", normal_delivery_allowed: true,
    selected_todo: {todo_id: "todo-1"}, heartbeat_receipt: {settlement_identity: identity},
    interaction_contract: {
      schema_version: "loopx_interaction_contract_v0",
      agent_channel: {delivery_allowed: true, work_context: {sources: [{
        source: "goal_acceptance",
        reason: FULL_OUTCOME_READ_GUIDANCE,
        content: {
          status: "loaded", ok: true, read_complete: true, decision_read_from_provider: true,
          legacy_fallback_used: false, provider_revision: "rev-1",
          goal_acceptance_contract: {outcome_requirements: {
            schema_version: "loopx_goal_outcomes_v0", current_lifetime: true, revision: 3,
            outcomes: [
              makeOutcome("first", "Retain complete requirement A. ".repeat(260)),
              {...makeOutcome("second", "Retain complete requirement B. ".repeat(260)), declaration_owner_agent_id: "original-agent"},
              makeOutcome("third", "Keep unrelated criterion separate."),
            ],
          }},
          outcome_context: {
            schema_version: "outcome_context_navigation_v1", goal_id: "goal-1", agent_id: "agent-1",
            todo_id: "todo-1", outcome_revision: 3, provider_revision: "rev-1",
            coverage_complete: true, suggested_outcome_ids: ["first", "second"],
          },
        },
      }]}},
      cli_channel: {
        execution_preparation: {state: "ready", todo_id: "todo-1"},
        settlement_plan: {schema_version: "quota_settlement_plan_v2", identity, ordered_steps: []},
      },
    },
  };
  const prefix = "/interaction_contract/agent_channel/work_context/sources/0/content/goal_acceptance_contract/outcome_requirements/outcomes";
  const readInitialPlan = (budget: number, captureId: string) => {
    const request = {
      scope: {goal_id: "goal-1", agent_id: "agent-1", session_id: "session-1", context_epoch: "epoch-1"},
      capture_id: captureId, surface: "quota_packet",
      context: baseContext, include_initial_read_plan: true,
      native_projection: {
        path: "projection.json", sha256: "a".repeat(64), initial_read_output_bytes: budget,
        command_template: "loopx --format json quota context-read --capture-file projection.json --capture-sha256 " + "a".repeat(64) + " --select '<pointer>'",
      },
    };
    const {changes} = prepareProgressiveContextDelivery(request, [], ["ok", "goal_id", "effective_action"]) as {
      changes: {path: (string | number)[]; from?: (string | number)[]; value?: unknown; remove?: true}[];
    };
    const applied: Record<string, unknown> = structuredClone(request);
    const get = (root: unknown, path: (string | number)[]) =>
      path.reduce<unknown>((node, key) => (node as Record<string | number, unknown>)[key], root);
    for (const edit of changes) {
      const parent = get(applied, edit.path.slice(0, -1)) as Record<string | number, unknown>;
      const key = edit.path.at(-1)!;
      if (edit.remove) delete parent[key];
      else parent[key] = structuredClone(edit.from ? get(request, edit.from) : edit.value);
    }
    return applied.initial_read_plan as {
      plan: {selections: {pointer: string}[]};
      deferred_suggested_outcomes?: string[];
      opening_context_instruction?: string;
    };
  };
  const initialPlan = readInitialPlan(12000, "cap-1");
  const pointers = initialPlan.plan.selections.map(row => row.pointer);
  assert.ok(!pointers.includes(prefix + "/0") && !pointers.includes(prefix + "/1"));
  assert.ok(pointers.includes(prefix + "/0/id") && pointers.includes(prefix + "/0/objective") && pointers.includes(prefix + "/0/revision"));
  assert.ok(pointers.includes(prefix + "/1/id") && pointers.includes(prefix + "/1/objective") && pointers.includes(prefix + "/1/revision"));
  assert.deepEqual(initialPlan.deferred_suggested_outcomes, [prefix + "/0", prefix + "/1"]);
  assert.ok(typeof initialPlan.opening_context_instruction === "string");
  assert.ok(!initialPlan.opening_context_instruction.includes("This view retains complete requirements"));
  assert.ok(initialPlan.opening_context_instruction.includes("Before dependent work"));
  assert.ok(initialPlan.opening_context_instruction.includes(prefix + "/0"));
  assert.ok(initialPlan.opening_context_instruction.includes(prefix + "/1"));
  assert.ok(initialPlan.opening_context_instruction.includes("recovery.command"));
  assert.ok(initialPlan.opening_context_instruction.includes("remove /id"));

  const largerPlan = readInitialPlan(64000, "cap-2");
  const largerPointers = largerPlan.plan.selections.map(row => row.pointer);
  assert.ok(largerPointers.includes(prefix + "/0") && largerPointers.includes(prefix + "/1"));
  assert.equal(largerPlan.deferred_suggested_outcomes, undefined);
  assert.equal(largerPlan.opening_context_instruction, undefined);
});

test("progressiveContextPlan defers large unscoped historical requirements and deduplicates blocked_priority_fallback", () => {
  const makeOutcome = (id: string, todoId: string) => ({
    id, objective: `Deliver ${id}.`, revision: 2, owner_agent_id: "a1", todo_ids: [todoId],
    sources: [{ref: `${id}:doc`, summary: "Supporting evidence summary. ".repeat(25)}],
    requirements: [{
      id: `${id}-req`, description: `Acceptance criterion for ${id}. `.repeat(20), status: "unverified", planning_satisfied: false,
      agent_review: {
        id: `${id}-req`, conclusion: "met", note: "Detailed agent review note. ".repeat(15), evidence_refs: [`ev:${id}`],
        basis_current: true, independently_verified: false,
      },
    }],
  });
  const outcomes = [
    makeOutcome("o-cur", "todo-cur"),
    makeOutcome("o-gap", "todo-gap"),
    makeOutcome("o-sug", "todo-sug"),
    ...Array.from({length: 5}, (_, idx) => makeOutcome(`o-hist-${idx}`, `todo-hist-${idx}`)),
  ];
  const fallback = {reason: "blocked by open prerequisite", priority_todo_id: "todo-cur", guidance: "Inspect dependency first. ".repeat(20)};
  const payload = {
    ok: true, goal_id: "goal-unscoped", agent_id: "a1",
    selected_todo: {todo_id: "todo-cur"},
    goal_frontier_projection: {acceptance_gaps: [{outcome_id: "o-gap"}]},
    blocked_priority_fallback: fallback,
    heartbeat_recommendation: {action: "retry", blocked_priority_fallback: structuredClone(fallback)},
    planning_horizon: {core_goal: {acceptance_contract: {outcome_requirements: {
      schema_version: "loopx_goal_outcomes_v0", outcomes: structuredClone(outcomes),
    }}}},
    interaction_contract: {
      schema_version: "loopx_interaction_contract_v0",
      agent_channel: {
        required_reads: [{source: "goal_acceptance", reason: "Check acceptance. " + FULL_OUTCOME_READ_GUIDANCE}],
        work_context: {sources: [{
          source: "goal_acceptance",
          reason: "Adopt goal objective. " + FULL_OUTCOME_READ_GUIDANCE,
          content: {
            status: "loaded", ok: true, read_complete: true,
            outcome_context: {suggested_outcome_ids: ["o-sug"]},
            goal_acceptance_contract: {outcome_requirements: {
              schema_version: "loopx_goal_outcomes_v0", outcomes: structuredClone(outcomes),
            }},
          },
        }]},
      },
    },
  };

  const plan = progressiveContextPlan({payload, surface: "quota_packet"}) as {
    deferred: {path: (string | number)[]; parent: (string | number)[]; index_key: string}[];
    instructions: {path: (string | number)[]; value: unknown}[];
    references?: {path: (string | number)[]; from: (string | number)[]; value: unknown}[];
  };

  const wcDeferredPaths = plan.deferred
    .filter(item => item.path[0] === "interaction_contract")
    .map(item => item.path.slice(-2).join("/"));
  assert.ok(wcDeferredPaths.includes("3/requirements"), "historical non-active outcomes defer requirements");
  assert.ok(wcDeferredPaths.includes("7/requirements"));
  assert.ok(!wcDeferredPaths.includes("0/requirements"), "selected_todo outcome keeps requirements inline");
  assert.ok(!wcDeferredPaths.includes("1/requirements"), "gap outcome keeps requirements inline");
  assert.ok(!wcDeferredPaths.includes("2/requirements"), "suggested outcome keeps requirements inline");
  const phDeferredPaths = plan.deferred
    .filter(item => item.path[0] === "planning_horizon")
    .map(item => item.path.slice(-2).join("/"));
  assert.ok(!phDeferredPaths.includes("0/requirements"));
  assert.ok(!phDeferredPaths.includes("1/requirements"));
  assert.ok(phDeferredPaths.includes("2/requirements") && phDeferredPaths.includes("7/requirements"));

  const reasonUpdate = plan.instructions.find(inst => inst.path.at(-1) === "reason");
  assert.ok(typeof reasonUpdate?.value === "string");
  assert.ok(!reasonUpdate.value.includes("cumulative outcome requirements"));
  assert.ok(reasonUpdate.value.includes("Active/suggested outcomes retain complete requirements here"));
  assert.ok(reasonUpdate.value.includes("historical non-selected outcome requirements remain in context_details"));

  // Autonomous replan obligation carriers retain full requirements inline for all outcomes.
  const replanPlan = progressiveContextPlan({
    payload: {
      ok: true,
      effective_action: "autonomous_replan_required",
      autonomous_replan_obligation: {replan_context: {core_goal: {acceptance_contract: {
        outcome_requirements: {schema_version: "loopx_goal_outcomes_v0", outcomes: structuredClone(outcomes)},
      }}}},
    },
    surface: "quota_packet",
  }) as {deferred: {path: (string | number)[]}[]};
  assert.equal(replanPlan.deferred.some(item => item.path.at(-1) === "requirements"), false);

  // Full evaluateTurnEnvelope context_delivery applies blocked_priority_fallback context_ref when equal,
  // leaves unequal fallbacks intact, and moves historical requirements cleanly.
  const {changes} = evaluateTurnEnvelope({
    operation: "context_delivery",
    scope: {goal_id: "goal-unscoped", agent_id: "a1", session_id: "s1", context_epoch: "e1"},
    capture_id: "cap-unscoped",
    surface: "quota_packet",
    context: payload,
    native_projection: {
      path: "projection.json", sha256: "b".repeat(64),
      command_template: "loopx --format json quota context-read --capture-file projection.json --capture-sha256 " + "b".repeat(64) + " --select '<pointer>'",
    },
  }) as {changes: {path: (string | number)[]; from?: (string | number)[]; value?: unknown; remove?: true}[]};
  assert.ok(changes.some(edit => edit.path.join("/") === "context/heartbeat_recommendation/blocked_priority_fallback"
    && (edit.value as {context_ref?: string} | undefined)?.context_ref === "/blocked_priority_fallback"));
  assert.ok(changes.some(edit =>
    edit.path.join("/") === "context/planning_horizon/core_goal/acceptance_contract/outcome_requirements/outcomes/3/requirements"
    && edit.remove === true));

  const {changes: unequalChanges} = evaluateTurnEnvelope({
    operation: "context_delivery",
    scope: {goal_id: "goal-unscoped", agent_id: "a1", session_id: "s1", context_epoch: "e1"},
    capture_id: "cap-unscoped",
    surface: "quota_packet",
    context: {
      ...payload,
      heartbeat_recommendation: {action: "retry", blocked_priority_fallback: {...fallback, priority_todo_id: "other"}},
    },
    native_projection: {
      path: "projection.json", sha256: "b".repeat(64),
      command_template: "loopx --format json quota context-read --capture-file projection.json --capture-sha256 " + "b".repeat(64) + " --select '<pointer>'",
    },
  }) as {changes: {path: (string | number)[]}[]};
  assert.equal(unequalChanges.some(edit =>
    edit.path.join("/") === "context/heartbeat_recommendation/blocked_priority_fallback"), false);
});

test("initial read omits redundant summary_only outcome rows when container read commands exist", () => {
  const identity = {schema_version: "quota_settlement_identity_v0", effect_id: "eff-1",
    goal_id: "goal-scoped", agent_id: "agent-1", todo_id: "todo-1", turn_instance_id: "turn-1"};
  const summaryRow = (idx: number) => ({
    id: `out-${idx}`,
    owner_agent_id: "peer-1",
    basis_digest: "sha256:" + "c".repeat(64),
    revision: 2,
    todo_count: 1,
    summary_only: true,
    capture_read_command: `loopx quota context-read --select /deferred_settlement_details/goal_acceptance/outcome_requirements/outcomes/${idx}`,
    cold_read_command: "loopx goal-acceptance inspect --goal-id goal-scoped",
    read_modes: "capture_read_command=immutable admission baseline; cold_read_command=live workspace inspect",
  });
  const scopedContext = {
    ok: true, goal_id: "goal-scoped", agent_id: "agent-1", agent_identity: {agent_id: "agent-1"},
    should_run: true, effective_action: "normal_run", normal_delivery_allowed: true,
    selected_todo: {todo_id: "todo-1"}, heartbeat_receipt: {settlement_identity: identity},
    interaction_contract: {
      schema_version: "loopx_interaction_contract_v0",
      agent_channel: {delivery_allowed: true, work_context: {sources: [{
        source: "goal_acceptance",
        reason: FULL_OUTCOME_READ_GUIDANCE,
        content: {
          status: "loaded", ok: true, read_complete: true, decision_read_from_provider: true,
          legacy_fallback_used: false, provider_revision: "rev-scoped",
          goal_acceptance_contract: {outcome_requirements: {
            schema_version: "loopx_goal_outcomes_v0", current_lifetime: true, revision: 9, outcome_revision: 9,
            projection_mode: "turn_scoped_v0", full_outcome_count: 9, inline_outcome_count: 1, compacted_outcome_count: 8,
            full_outcomes_sha256: "sha256:" + "d".repeat(64),
            capture_read_command: "loopx quota context-read --select /deferred_settlement_details/goal_acceptance",
            cold_read_command: "loopx goal-acceptance inspect --goal-id goal-scoped",
            cold_read_instruction: "Use --include-detail goal_acceptance or cold_read_command.",
            outcomes: [
              {
                id: "out-0", objective: "Active objective.", revision: 9, owner_agent_id: "agent-1", todo_ids: ["todo-1"],
                sources: [{ref: "spec:0", summary: "Active provenance"}],
                requirements: [{id: "req-0", description: "Active requirement.", status: "unverified", planning_satisfied: false}],
              },
              ...Array.from({length: 8}, (_, i) => summaryRow(i + 1)),
            ],
          }},
          outcome_context: {
            schema_version: "outcome_context_navigation_v1", goal_id: "goal-scoped", agent_id: "agent-1",
            todo_id: "todo-1", outcome_revision: 9, provider_revision: "rev-scoped",
            coverage_complete: true, suggested_outcome_ids: ["out-0"],
          },
        },
      }]}},
      cli_channel: {
        execution_preparation: {state: "ready", todo_id: "todo-1"},
        settlement_plan: {schema_version: "quota_settlement_plan_v2", identity, ordered_steps: []},
      },
    },
  };
  const prefix = "/interaction_contract/agent_channel/work_context/sources/0/content/goal_acceptance_contract/outcome_requirements/outcomes";
  const request = {
    scope: {goal_id: "goal-scoped", agent_id: "agent-1", session_id: "sess-1", context_epoch: "ep-1"},
    capture_id: "cap-scoped", surface: "quota_packet",
    context: scopedContext, include_initial_read_plan: true,
    native_projection: {
      path: "projection.json", sha256: "e".repeat(64), initial_read_output_bytes: 12000,
      command_template: "loopx --format json quota context-read --capture-file projection.json --capture-sha256 " + "e".repeat(64) + " --select '<pointer>'",
    },
  };
  const {changes} = prepareProgressiveContextDelivery(request, [], ["ok", "goal_id"]) as {
    changes: {path: (string | number)[]; from?: (string | number)[]; value?: unknown; remove?: true}[];
  };
  const applied = structuredClone(request) as Record<string, unknown>;
  const get = (root: unknown, path: (string | number)[]) =>
    path.reduce<unknown>((node, key) => (node as Record<string | number, unknown>)[key], root);
  for (const edit of changes) {
    const parent = get(applied, edit.path.slice(0, -1)) as Record<string | number, unknown>;
    const key = edit.path.at(-1)!;
    if (edit.remove) delete parent[key];
    else parent[key] = structuredClone(edit.from ? get(request, edit.from) : edit.value);
  }
  const initialSelections = ((applied.initial_read_plan as {plan: {selections: {pointer: string}[]}}).plan.selections)
    .map(sel => sel.pointer);
  const reqBase = "/interaction_contract/agent_channel/work_context/sources/0/content/goal_acceptance_contract/outcome_requirements";
  assert.ok(initialSelections.includes(reqBase + "/capture_read_command"));
  assert.ok(initialSelections.includes(reqBase + "/cold_read_command"));
  assert.ok(initialSelections.includes(prefix + "/0"));
  assert.equal(initialSelections.some(p => p.startsWith(prefix + "/1")), false);
  assert.equal(initialSelections.some(p => p.startsWith(prefix + "/8")), false);

  // Delivered required-context projection still preserves per-row #9395 commands and read_modes verbatim.
  const deliveredSummaryRow = (get(applied, ["context", "interaction_contract", "agent_channel", "work_context", "sources", 0,
    "content", "goal_acceptance_contract", "outcome_requirements", "outcomes", 1]) as Record<string, unknown>);
  assert.equal(deliveredSummaryRow.summary_only, true);
  assert.ok(typeof deliveredSummaryRow.capture_read_command === "string");
  assert.ok(typeof deliveredSummaryRow.cold_read_command === "string");
  assert.ok(typeof deliveredSummaryRow.read_modes === "string");

  // Boundary <= 6 outcomes retains summary_only rows in initial read selections.
  const smallContext = structuredClone(scopedContext);
  const smallReqs = (smallContext.interaction_contract.agent_channel.work_context.sources[0].content
    .goal_acceptance_contract.outcome_requirements as {outcomes: unknown[]; full_outcome_count: number; compacted_outcome_count: number});
  smallReqs.outcomes = smallReqs.outcomes.slice(0, 6);
  smallReqs.full_outcome_count = 6;
  smallReqs.compacted_outcome_count = 5;
  const smallPointers = initialContextReadPointers(smallContext, []);
  assert.ok(smallPointers.includes(prefix + "/1/capture_read_command"));
});
