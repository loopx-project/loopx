import assert from "node:assert/strict";
import test from "node:test";

import {
  GOAL_AMENDMENT_PROPOSAL_ADMISSION_SCHEMA_VERSION,
  admitGoalAmendmentProposal,
} from "../../loopx/control_plane/goals/goal_amendment_proposal.ts";

// The default proposal binds to the derived basis: equal sequence AND
// equal source basis digest. MISMATCHED_DIGEST exercises the binding.
const DIGEST = "sha256:" + "a".repeat(64);
const MISMATCHED_DIGEST = "sha256:" + "b".repeat(64);

test("canonical Todo bases cannot be declared fresh merely because both event sequences are zero", () => {
  const request = baseRequest();
  request.proposal.base_revision_basis = "canonical_todo_snapshot";
  request.proposal.base_state_event_basis_sequence = 0;
  request.derived_basis.revision_basis = "canonical_todo_snapshot";
  request.derived_basis.state_event_basis_sequence = 0;
  const fresh = admitGoalAmendmentProposal(request);
  assert.equal(fresh.admission, "admitted");
  assert.deepEqual(fresh.admission_facts, ["base_source_basis_unverifiable"]);
  request.derived_basis.source_basis_digest = MISMATCHED_DIGEST;
  const stale = admitGoalAmendmentProposal(request);
  assert.equal(stale.admission, "needs_rebase");
  assert.deepEqual(stale.admission_facts, ["base_source_basis_digest_mismatch"]);
  request.proposal.base_revision_basis = "markdown_active_state";
  assert.ok(admitGoalAmendmentProposal(request).admission_facts.includes("base_revision_basis_superseded"));
});

function baseRequest(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: "goal_amendment_proposal_request_v0",
    proposal: {
      schema_version: "goal_amendment_proposal_v0",
      proposal_id: "gap_stage2_001",
      goal_id: "goal-stage2",
      proposer_agent_id: "agent-a",
      amendment_class: "shared_acceptance",
      base_revision_basis: "state_event_log",
      base_state_event_basis_sequence: 17,
      base_source_basis_digest: DIGEST,
      retained: ["original outcome remains unchanged"],
      changed: ["acceptance now requires the recovered receipt"],
      stopped: [],
      evidence_refs: ["evidence:evt_stage2_001"],
      affected_todo_ids: ["todo_stage2_a", "todo_stage2_b"],
      replan_obligation_id: "replan-fe2d75e84da47ac3",
    },
    derived_basis: {
      state_event_basis_sequence: 17,
      revision_basis: "state_event_log",
      source_basis_digest: DIGEST,
    },
    // Authoritative admission-time inventories: the open replan obligation
    // this proposal's causal chain binds to, and the goal's actionable open
    // Todos. Bound to agent-a's lane so the default proposal admits.
    open_replan_obligations: [
      {
        schema_version: "autonomous_replan_obligation_v0",
        obligation_id: "replan-fe2d75e84da47ac3",
        goal_id: "goal-stage2",
        required: true,
        bound_agent_ids: ["agent-a"],
      },
    ],
    goal_todo_inventory: [
      {
        todo_id: "todo_stage2_a",
        status: "open",
        task_class: "advancement_task",
        claimed_by: "agent-a",
        bound_agent: null,
      },
      {
        todo_id: "todo_stage2_b",
        status: "open",
        task_class: "advancement_task",
        claimed_by: null,
        bound_agent: null,
      },
    ],
    ...overrides,
  };
}

/** Replace the single default obligation with the given inventory entries. */
function withObligations(
  request: Record<string, unknown>,
  obligations: unknown,
): Record<string, unknown> {
  return { ...request, open_replan_obligations: obligations };
}

/** Replace the default todo inventory with the given entries. */
function withTodoInventory(
  request: Record<string, unknown>,
  entries: unknown,
): Record<string, unknown> {
  return { ...request, goal_todo_inventory: entries };
}

test("admits a well-formed proposal with no canonical effect", () => {
  const result = admitGoalAmendmentProposal(baseRequest());

  assert.equal(
    result.schema_version,
    GOAL_AMENDMENT_PROPOSAL_ADMISSION_SCHEMA_VERSION,
  );
  assert.equal(result.proposal_id, "gap_stage2_001");
  assert.equal(result.goal_id, "goal-stage2");
  assert.equal(result.proposer_agent_id, "agent-a");
  assert.equal(result.amendment_class, "shared_acceptance");
  assert.equal(result.base_revision_basis, "state_event_log");
  assert.equal(result.admission, "admitted");
  assert.deepEqual(result.admission_facts, []);
  assert.equal(result.canonical_effect, "none");
  assert.match(result.proposal_digest, /^sha256:[0-9a-f]{64}$/);
  assert.deepEqual(result.retained, ["original outcome remains unchanged"]);
  assert.deepEqual(result.evidence_refs, ["evidence:evt_stage2_001"]);
});

test("a base sequence behind the derived head is retained as needs_rebase", () => {
  const result = admitGoalAmendmentProposal(
    baseRequest({
      proposal: {
        ...baseRequest().proposal,
        base_state_event_basis_sequence: 12,
      },
    }),
  );

  assert.equal(result.admission, "needs_rebase");
  assert.deepEqual(result.admission_facts, [
    "base_state_event_basis_sequence_behind_derived_head",
  ]);
  assert.equal(result.canonical_effect, "none");
});

test("an equal sequence with a mismatched base digest needs rebase", () => {
  // Same sequence, different source basis identity: the proposal is NOT
  // bound to the basis it claims, so it must never be admitted fresh.
  const result = admitGoalAmendmentProposal(
    baseRequest({
      proposal: {
        ...baseRequest().proposal,
        base_source_basis_digest: MISMATCHED_DIGEST,
      },
    }),
  );

  assert.equal(result.admission, "needs_rebase");
  assert.deepEqual(result.admission_facts, [
    "base_source_basis_digest_mismatch",
  ]);
  assert.equal(result.canonical_effect, "none");
});

test("a behind sequence with a mismatched digest reports both facts", () => {
  const result = admitGoalAmendmentProposal(
    baseRequest({
      proposal: {
        ...baseRequest().proposal,
        base_state_event_basis_sequence: 12,
        base_source_basis_digest: MISMATCHED_DIGEST,
      },
    }),
  );

  assert.equal(result.admission, "needs_rebase");
  assert.deepEqual(result.admission_facts, [
    "base_state_event_basis_sequence_behind_derived_head",
    "base_source_basis_digest_mismatch",
  ]);
});

test("a base sequence ahead of the derived head fails closed", () => {
  assert.throws(
    () =>
      admitGoalAmendmentProposal(
        baseRequest({
          proposal: {
            ...baseRequest().proposal,
            base_state_event_basis_sequence: 99,
          },
        }),
      ),
    /ahead of the derived state event basis head/,
  );
});

test("a markdown basis admits the real sequence 0 as unverifiable", () => {
  // Producer-consumer contract (review round 6 counterexample 2): a goal
  // without an event log projects state_event_basis_sequence=0, and the
  // proposal consumes that real 0 verbatim — the decoder must never force
  // the proposer to fabricate a positive sequence.
  const result = admitGoalAmendmentProposal(
    baseRequest({
      proposal: {
        ...baseRequest().proposal,
        base_revision_basis: "markdown_active_state",
        base_state_event_basis_sequence: 0,
      },
      derived_basis: {
        state_event_basis_sequence: 0,
        revision_basis: "markdown_active_state",
        source_basis_digest: DIGEST,
      },
    }),
  );

  assert.equal(result.admission, "admitted");
  assert.equal(result.base_state_event_basis_sequence, 0);
  assert.deepEqual(result.admission_facts, ["base_source_basis_unverifiable"]);
});

test("a changed markdown digest requires rebase even at sequence zero", () => {
  const request = baseRequest({
    proposal: {
      ...baseRequest().proposal,
      base_revision_basis: "markdown_active_state",
      base_state_event_basis_sequence: 0,
    },
    derived_basis: {
      state_event_basis_sequence: 0,
      revision_basis: "markdown_active_state",
      source_basis_digest: MISMATCHED_DIGEST,
    },
  });
  const input = JSON.stringify(request);
  const result = admitGoalAmendmentProposal(request);
  assert.equal(result.admission, "needs_rebase");
  assert.deepEqual(result.admission_facts, ["base_source_basis_digest_mismatch"]);
  assert.equal(result.canonical_effect, "none");
  assert.equal(JSON.stringify(request), input);
});

test("a real markdown base superseded by a later event log is retained as needs_rebase", () => {
  // Review round 8 counterexample: a proposal that legitimately bound the
  // real markdown basis (sequence 0, the only sequence the Stage 1 producer
  // emits for an event-less Goal) must not be called a fabricated history
  // after the same Goal later gains its first state event. The proposal
  // carries its own base_revision_basis identity, so validating the sequence
  // against the *claimed* basis — not against the current derived basis —
  // lets this type transition resolve to an explicit, read-back
  // reconciliation outcome instead of a request rejection.
  const result = admitGoalAmendmentProposal(
    baseRequest({
      proposal: {
        ...baseRequest().proposal,
        proposal_id: "gap_stage2_superseded",
        base_revision_basis: "markdown_active_state",
        base_state_event_basis_sequence: 0,
        base_source_basis_digest: MISMATCHED_DIGEST,
      },
      // derived_basis stays the default event-log head (17, DIGEST): the
      // Goal has evolved past the proposal's markdown base.
    }),
  );

  assert.equal(result.admission, "needs_rebase");
  assert.deepEqual(result.admission_facts, ["base_revision_basis_superseded"]);
  assert.equal(result.canonical_effect, "none");
  assert.equal(result.base_revision_basis, "markdown_active_state");
  assert.equal(result.base_state_event_basis_sequence, 0);
});

test("a claimed state_event_log base of sequence zero still fails closed", () => {
  // Not every zero under an event-log derived basis is a superseded markdown
  // base: a proposal claiming revision_basis=state_event_log with sequence 0
  // asserts an event append that can never have existed (append sequences
  // start at 1) and stays a request rejection, not a needs_rebase retention.
  assert.throws(
    () =>
      admitGoalAmendmentProposal(
        baseRequest({
          proposal: {
            ...baseRequest().proposal,
            base_state_event_basis_sequence: 0,
          },
        }),
      ),
    /must be a positive integer when the proposal's base_revision_basis is state_event_log/,
  );
});

test("an event-log proposal against a markdown derived basis fails closed as ahead", () => {
  // The reverse transition is not a real producer path: an event-log base
  // can never become markdown, and its positive sequence is ahead of the
  // markdown derived head (0), so it stays the existing future rejection.
  assert.throws(
    () =>
      admitGoalAmendmentProposal(
        baseRequest({
          derived_basis: {
            state_event_basis_sequence: 0,
            revision_basis: "markdown_active_state",
            source_basis_digest: DIGEST,
          },
        }),
      ),
    /ahead of the derived state event basis head/,
  );
});

test("a fabricated positive base sequence under a markdown basis fails closed", () => {
  // The inverse counterexample: 0 is the only markdown sequence the Stage 1
  // producer can emit, so 17 is not a producible base and must be rejected
  // instead of being admitted as unverifiable.
  assert.throws(
    () =>
      admitGoalAmendmentProposal(
        baseRequest({
          proposal: {
            ...baseRequest().proposal,
            base_revision_basis: "markdown_active_state",
            base_state_event_basis_sequence: 17,
          },
          derived_basis: {
            state_event_basis_sequence: 0,
            revision_basis: "markdown_active_state",
            source_basis_digest: DIGEST,
          },
        }),
      ),
    /must be 0 when the proposal's base_revision_basis is markdown_active_state/,
  );
});

test("a zero base sequence under an event-log basis fails closed", () => {
  // Event append sequences start at 1: 0 cannot exist under
  // state_event_log and is a request rejection, not a behind-head
  // needs_rebase retention.
  assert.throws(
    () =>
      admitGoalAmendmentProposal(
        baseRequest({
          proposal: {
            ...baseRequest().proposal,
            base_state_event_basis_sequence: 0,
          },
        }),
      ),
    /must be a positive integer when the proposal's base_revision_basis is state_event_log/,
  );
});

test("a negative base sequence is rejected", () => {
  assert.throws(
    () =>
      admitGoalAmendmentProposal(
        baseRequest({
          proposal: {
            ...baseRequest().proposal,
            base_state_event_basis_sequence: -1,
          },
        }),
      ),
    /must be a non-negative integer/,
  );
});

test("an unknown amendment class is rejected", () => {
  assert.throws(
    () =>
      admitGoalAmendmentProposal(
        baseRequest({
          proposal: {
            ...baseRequest().proposal,
            amendment_class: "emergency_powers",
          },
        }),
      ),
    /amendment class is unsupported/,
  );
});

test("every amendment class in the RFC table is admissible", () => {
  for (const amendmentClass of [
    "lane_route",
    "shared_work_graph",
    "shared_acceptance",
    "protected_authority",
  ]) {
    const result = admitGoalAmendmentProposal(
      baseRequest({
        proposal: { ...baseRequest().proposal, amendment_class: amendmentClass },
      }),
    );
    assert.equal(result.amendment_class, amendmentClass);
    assert.equal(result.canonical_effect, "none");
  }
});

test("a base digest that is not sha256 hex is rejected", () => {
  assert.throws(
    () =>
      admitGoalAmendmentProposal(
        baseRequest({
          proposal: {
            ...baseRequest().proposal,
            base_source_basis_digest: "md5:zz",
          },
        }),
      ),
    /sha256/,
  );
});

test("duplicate affected todo ids are rejected", () => {
  assert.throws(
    () =>
      admitGoalAmendmentProposal(
        baseRequest({
          proposal: {
            ...baseRequest().proposal,
            affected_todo_ids: ["todo_stage2_a", "todo_stage2_a"],
          },
        }),
      ),
    /duplicate todo_id/,
  );
});

test("duplicate evidence pointers are rejected", () => {
  assert.throws(
    () =>
      admitGoalAmendmentProposal(
        baseRequest({
          proposal: {
            ...baseRequest().proposal,
            evidence_refs: [
              "evidence:evt_stage2_001",
              "evidence:evt_stage2_001",
            ],
          },
        }),
      ),
    /duplicate pointer/,
  );
});

test("evidence pointers over the budget are rejected", () => {
  assert.throws(
    () =>
      admitGoalAmendmentProposal(
        baseRequest({
          proposal: {
            ...baseRequest().proposal,
            evidence_refs: Array.from(
              { length: 9 },
              (_, index) => `evidence:evt_stage2_${index}`,
            ),
          },
        }),
      ),
    /exceeds 8 pointers/,
  );
});

test("an over-long evidence pointer is rejected", () => {
  assert.throws(
    () =>
      admitGoalAmendmentProposal(
        baseRequest({
          proposal: {
            ...baseRequest().proposal,
            evidence_refs: ["evidence:" + "x".repeat(200)],
          },
        }),
      ),
    /exceeds 200 characters/,
  );
});

test("a proposal with no changed statement is rejected", () => {
  assert.throws(
    () =>
      admitGoalAmendmentProposal(
        baseRequest({
          proposal: {
            ...baseRequest().proposal,
            changed: [],
          },
        }),
      ),
    /goal_amendment_proposal.changed requires at least 1/,
  );
});

test("a proposal with no retained statement is rejected", () => {
  assert.throws(
    () =>
      admitGoalAmendmentProposal(
        baseRequest({
          proposal: {
            ...baseRequest().proposal,
            retained: [],
          },
        }),
      ),
    /goal_amendment_proposal.retained requires at least 1/,
  );
});

test("a missing replan obligation id is rejected", () => {
  const proposal = baseRequest().proposal as Record<string, unknown>;
  delete proposal.replan_obligation_id;
  assert.throws(
    () => admitGoalAmendmentProposal(baseRequest({ proposal })),
    /replan_obligation_id/,
  );
});

test("a malformed proposal id is rejected", () => {
  assert.throws(
    () =>
      admitGoalAmendmentProposal(
        baseRequest({
          proposal: {
            ...baseRequest().proposal,
            proposal_id: "proposal-1",
          },
        }),
      ),
    /gap_<slug>/,
  );
});

test("a request schema mismatch is rejected", () => {
  assert.throws(
    () =>
      admitGoalAmendmentProposal(
        baseRequest({ schema_version: "goal_amendment_proposal_request_v1" }),
      ),
    /schema mismatch/,
  );
});

test("a proposal schema mismatch is rejected", () => {
  const request = baseRequest();
  (request.proposal as Record<string, unknown>).schema_version =
    "goal_amendment_proposal_v1";
  assert.throws(
    () => admitGoalAmendmentProposal(request),
    /schema mismatch/,
  );
});

test("the proposal digest is deterministic and content-bound", () => {
  const first = admitGoalAmendmentProposal(baseRequest());

  // Reordered keys inside the proposal must not change the digest: the
  // digest binds proposal content, not serialization order.
  const reordered: Record<string, unknown> = baseRequest();
  reordered.proposal = Object.fromEntries(
    Object.entries(reordered.proposal as Record<string, unknown>).reverse(),
  );
  const second = admitGoalAmendmentProposal(reordered);

  // A changed statement must change the digest.
  const edited: Record<string, unknown> = baseRequest();
  edited.proposal = {
    ...(edited.proposal as Record<string, unknown>),
    changed: ["acceptance now requires two independent receipts"],
  };
  const third = admitGoalAmendmentProposal(edited);

  assert.equal(second.proposal_digest, first.proposal_digest);
  assert.notEqual(third.proposal_digest, first.proposal_digest);
});

test("an empty evidence_refs array is rejected", () => {
  assert.throws(
    () =>
      admitGoalAmendmentProposal(
        baseRequest({
          proposal: {
            ...baseRequest().proposal,
            evidence_refs: [],
          },
        }),
      ),
    /requires at least one pointer/,
  );
});

test("affected todo ids over the budget are rejected", () => {
  assert.throws(
    () =>
      admitGoalAmendmentProposal(
        baseRequest({
          proposal: {
            ...baseRequest().proposal,
            affected_todo_ids: Array.from(
              { length: 17 },
              (_, index) => `todo_stage2_${index}`,
            ),
          },
        }),
      ),
    /exceeds 16 ids/,
  );
});

test("a malformed affected todo id is rejected", () => {
  assert.throws(
    () =>
      admitGoalAmendmentProposal(
        baseRequest({
          proposal: {
            ...baseRequest().proposal,
            affected_todo_ids: ["todo-stage2-a"],
          },
        }),
      ),
    /must be a valid Todo id/,
  );
});

test("a blank stopped statement is rejected", () => {
  assert.throws(
    () =>
      admitGoalAmendmentProposal(
        baseRequest({
          proposal: {
            ...baseRequest().proposal,
            stopped: ["   "],
          },
        }),
      ),
    /stopped\[0\] must be a non-empty statement/,
  );
});

test("replan obligation ids follow the normalize_todo_replan_obligation_id contract", () => {
  // The repository authority is TODO_REPLAN_OBLIGATION_ID_PATTERN:
  // "replan-" + 16 lowercase hex, e.g. "replan-fe2d75e84da47ac3".
  // A second obligation namespace must not be invented here.
  const result = admitGoalAmendmentProposal(
    baseRequest({
      proposal: {
        ...baseRequest().proposal,
        replan_obligation_id: "replan-0123456789abcdef",
      },
      open_replan_obligations: [
        {
          schema_version: "autonomous_replan_obligation_v0",
          obligation_id: "replan-0123456789abcdef",
          goal_id: "goal-stage2",
          required: true,
          bound_agent_ids: ["agent-a"],
        },
      ],
    }),
  );
  assert.equal(result.replan_obligation_id, "replan-0123456789abcdef");
  for (const replanObligationId of [
    "replan:stage2-001",
    "replan-FE2D75E84DA47AC3",
    "replan-fe2d75e84da47ac",
    "replan-fe2d75e84da47ac33",
    "obligation-stage2-001",
  ]) {
    assert.throws(
      () =>
        admitGoalAmendmentProposal(
          baseRequest({
            proposal: {
              ...baseRequest().proposal,
              replan_obligation_id: replanObligationId,
            },
          }),
        ),
      /replan-<16 lowercase hex>/,
    );
  }
});

test("a replan obligation id absent from the inventory fails closed", () => {
  assert.throws(
    () =>
      admitGoalAmendmentProposal(
        baseRequest({
          proposal: {
            ...baseRequest().proposal,
            replan_obligation_id: "replan-deadbeefdeadbeef",
          },
        }),
      ),
    /does not match an open replan obligation of goal goal-stage2: replan-deadbeefdeadbeef/,
  );
});

test("an affected todo absent from the inventory fails closed", () => {
  assert.throws(
    () =>
      admitGoalAmendmentProposal(
        baseRequest({
          proposal: {
            ...baseRequest().proposal,
            affected_todo_ids: ["todo_stage2_a", "todo_missing"],
          },
        }),
      ),
    /references a todo that is not open on goal goal-stage2: todo_missing/,
  );
});

test("a closed obligation in the inventory is a decode rejection", () => {
  // Python filters settled obligations before building the inventory; a
  // required=false entry reaching the reducer is an authority regression
  // and must fail closed at decode time.
  assert.throws(
    () =>
      admitGoalAmendmentProposal(
        withObligations(baseRequest(), [
          {
            schema_version: "autonomous_replan_obligation_v0",
            obligation_id: "replan-fe2d75e84da47ac3",
            goal_id: "goal-stage2",
            required: false,
            bound_agent_ids: [],
          },
        ]),
      ),
    /required must be true for listed obligations/,
  );
});

test("an obligation bound to another goal fails closed", () => {
  assert.throws(
    () =>
      admitGoalAmendmentProposal(
        withObligations(baseRequest(), [
          {
            schema_version: "autonomous_replan_obligation_v0",
            obligation_id: "replan-fe2d75e84da47ac3",
            goal_id: "goal-other",
            required: true,
            bound_agent_ids: ["agent-a"],
          },
        ]),
      ),
    /belongs to another goal: goal-other/,
  );
});

test("an obligation bound to another agent lane fails closed", () => {
  assert.throws(
    () =>
      admitGoalAmendmentProposal(
        withObligations(baseRequest(), [
          {
            schema_version: "autonomous_replan_obligation_v0",
            obligation_id: "replan-fe2d75e84da47ac3",
            goal_id: "goal-stage2",
            required: true,
            bound_agent_ids: ["agent-b", "agent-c"],
          },
        ]),
      ),
    /is bound to another agent lane: replan-fe2d75e84da47ac3/,
  );
});

test("a shared obligation with no lane binding still admits", () => {
  const result = admitGoalAmendmentProposal(
    withObligations(baseRequest(), [
      {
        schema_version: "autonomous_replan_obligation_v0",
        obligation_id: "replan-fe2d75e84da47ac3",
        goal_id: "goal-stage2",
        required: true,
        bound_agent_ids: [],
      },
    ]),
  );

  assert.equal(result.admission, "admitted");
  assert.equal(result.canonical_effect, "none");
});

test("a proposal with no affected todos still admits", () => {
  // A pure intent amendment (no affected Todo) has no inventory membership
  // to check beyond the obligation binding.
  const result = admitGoalAmendmentProposal(
    baseRequest({
      proposal: {
        ...baseRequest().proposal,
        affected_todo_ids: [],
      },
    }),
  );

  assert.equal(result.admission, "admitted");
});

test("an empty obligation inventory fails closed for any reference", () => {
  assert.throws(
    () => admitGoalAmendmentProposal(withObligations(baseRequest(), [])),
    /does not match an open replan obligation/,
  );
});

test("duplicate obligation ids in the inventory are rejected", () => {
  const duplicate = {
    schema_version: "autonomous_replan_obligation_v0",
    obligation_id: "replan-fe2d75e84da47ac3",
    goal_id: "goal-stage2",
    required: true,
    bound_agent_ids: ["agent-a"],
  };
  assert.throws(
    () => admitGoalAmendmentProposal(withObligations(baseRequest(), [duplicate, duplicate])),
    /duplicate obligation_id/,
  );
});

test("duplicate todo ids in the inventory are rejected", () => {
  const duplicate = {
    todo_id: "todo_stage2_a",
    status: "open",
    task_class: "advancement_task",
    claimed_by: "agent-a",
    bound_agent: null,
  };
  assert.throws(
    () => admitGoalAmendmentProposal(withTodoInventory(baseRequest(), [duplicate, duplicate])),
    /duplicate todo_id/,
  );
});

test("an inventory entry missing required fields is rejected", () => {
  assert.throws(
    () =>
      admitGoalAmendmentProposal(
        withObligations(baseRequest(), [
          { schema_version: "autonomous_replan_obligation_v0" },
        ]),
      ),
    /obligation_id must be a non-empty string/,
  );
  assert.throws(
    () =>
      admitGoalAmendmentProposal(
        withTodoInventory(baseRequest(), [{ status: "open" }]),
      ),
    /todo_id must be a non-empty string/,
  );
});

test("a malformed bound agent id in the inventory is rejected", () => {
  assert.throws(
    () =>
      admitGoalAmendmentProposal(
        withObligations(baseRequest(), [
          {
            schema_version: "autonomous_replan_obligation_v0",
            obligation_id: "replan-fe2d75e84da47ac3",
            goal_id: "goal-stage2",
            required: true,
            bound_agent_ids: ["_not_an_agent_id"],
          },
        ]),
      ),
    /must be a public-safe agent id/,
  );
});

test("non-array inventories are rejected", () => {
  assert.throws(
    () =>
      admitGoalAmendmentProposal(
        withObligations(baseRequest(), "not-an-array"),
      ),
    /open_replan_obligations must be an array/,
  );
  assert.throws(
    () =>
      admitGoalAmendmentProposal(
        withTodoInventory(baseRequest(), null),
      ),
    /goal_todo_inventory must be an array/,
  );
});
