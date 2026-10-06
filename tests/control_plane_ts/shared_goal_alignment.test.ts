import assert from "node:assert/strict";
import test from "node:test";

import {
  SHARED_GOAL_ALIGNMENT_SCHEMA_VERSION,
  projectSharedGoalAlignment,
} from "../../loopx/control_plane/goals/shared_goal_alignment.ts";

const DIGEST = "sha256:" + "a".repeat(64);

function canonicalRequest(sourceAuthority: string) {
  return {...baseRequest(), source_basis: {revision_basis: "canonical_todo_snapshot",
    state_event_basis_sequence: 0, source_basis_digest: DIGEST, state_updated_at: null,
    todo_basis: {source_authority: sourceAuthority, provider_revision: "opaque-provider-token", records_sha256: "a".repeat(64)}},
    frontier_basis: {basis_source: "unbound", based_on_state_event_sequence: null, last_agent_event_id: null}};
}

for (const sourceAuthority of ["file_v0", "sqlite_v0"]) {
  test(`${sourceAuthority} Todo basis preserves identity without fabricating an event frontier`, () => {
    const request = canonicalRequest(sourceAuthority);
    const result = projectSharedGoalAlignment(request);
    assert.deepEqual(result.drift_facts, []);
    assert.deepEqual(result.source_basis, request.source_basis);
    assert.ok(result.conflict_facts.includes("frontier_basis_unverifiable"));
    assert.equal(result.read_only, true);
    assert.throws(() => projectSharedGoalAlignment({...request, work_items: [], observed_at: "2026-09-09T00:00:00Z"}), /cannot mix/);
  });

  test(`${sourceAuthority} rejects corrupt or absent canonical basis fields`, () => {
    const request = canonicalRequest(sourceAuthority);
    for (const patch of [
      {source_authority: "legacy"}, {source_authority: "nokv_v0"},
      {source_authority: "postgresql_v0"}, {source_authority: "sqlite_v1"},
      {source_authority: null}, {provider_revision: ""}, {provider_revision: " "},
      {provider_revision: 7}, {records_sha256: "a".repeat(63)},
      {records_sha256: "a".repeat(65)}, {records_sha256: "A".repeat(64)},
      {records_sha256: "sha256:" + "a".repeat(64)}, {records_sha256: null},
    ]) {
      assert.throws(() => projectSharedGoalAlignment({...request, source_basis: {
        ...request.source_basis, todo_basis: {...request.source_basis.todo_basis, ...patch},
      }}));
    }
    const {todo_basis: _basis, ...sourceBasis} = request.source_basis;
    assert.throws(() => projectSharedGoalAlignment({...request, source_basis: sourceBasis}), /requires a canonical Todo basis/);
  });
}

function baseRequest(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: "shared_goal_alignment_request_v0",
    goal_id: "goal-shared",
    agent_id: "agent-a",
    source_basis: {
      state_event_basis_sequence: 42,
      source_basis_digest: DIGEST,
      revision_basis: "state_event_log",
      state_updated_at: "2026-09-01T00:00:00+00:00",
    },
    frontier_basis: {
      based_on_state_event_sequence: 42,
      basis_source: "state_event_log",
      last_agent_event_id: "evt_agent_a_last",
    },
    frontier_counts: {
      current_agent_claimed_advancement_count: 1,
      unclaimed_advancement_count: 2,
      other_agent_claimed_advancement_count: 0,
    },
    claims: [
      {
        todo_id: "todo_lane_a",
        claimed_by: "agent-a",
        lease_epoch: 2,
        lease_owner: "agent-a",
      },
    ],
    unclaimed_eligible: [
      { todo_id: "todo_lane_b", task_class: "advancement_task", action_kind: "run" },
      { todo_id: "todo_lane_c", task_class: "advancement_task" },
    ],
    peer_claimed_bound_todo_ids: [],
    open_lane_replan_obligation_required: false,
    ...overrides,
  };
}

test("projects the full read-only alignment from typed facts", () => {
  const result = projectSharedGoalAlignment(baseRequest());

  assert.equal(result.schema_version, SHARED_GOAL_ALIGNMENT_SCHEMA_VERSION);
  assert.equal(result.goal_id, "goal-shared");
  assert.equal(result.agent_id, "agent-a");
  assert.equal(result.source_basis.state_event_basis_sequence, 42);
  assert.equal(result.source_basis.source_basis_digest, DIGEST);
  assert.equal(result.frontier_basis.based_on_state_event_sequence, 42);
  assert.equal(
    result.frontier_counts.unclaimed_advancement_count,
    2,
  );
  assert.deepEqual(result.unclaimed_eligible_work, [
    { todo_id: "todo_lane_b", claim_required_before_work: true },
    { todo_id: "todo_lane_c", claim_required_before_work: true },
  ]);
  assert.deepEqual(result.drift_facts, []);
  assert.deepEqual(result.conflict_facts, []);
  assert.equal(result.read_only, true);
});

test("a frontier basis behind the state event basis head drifts behind", () => {
  const result = projectSharedGoalAlignment(
    baseRequest({
      frontier_basis: {
        based_on_state_event_sequence: 39,
        basis_source: "state_event_log",
        last_agent_event_id: "evt_agent_a_39",
      },
    }),
  );

  assert.deepEqual(result.drift_facts, ["frontier_basis_behind"]);
  assert.deepEqual(result.conflict_facts, []);
});

test("an equal frontier basis never drifts behind", () => {
  const result = projectSharedGoalAlignment(baseRequest());

  assert.deepEqual(result.drift_facts, []);
});

test("an unbound frontier basis is unverifiable, never behind", () => {
  const result = projectSharedGoalAlignment(
    baseRequest({
      source_basis: {
        state_event_basis_sequence: 0,
        source_basis_digest: DIGEST,
        revision_basis: "markdown_active_state",
        state_updated_at: null,
      },
      frontier_basis: {
        based_on_state_event_sequence: null,
        basis_source: "unbound",
        last_agent_event_id: null,
      },
    }),
  );

  assert.deepEqual(result.drift_facts, []);
  assert.deepEqual(result.conflict_facts, ["frontier_basis_unverifiable"]);
  assert.equal(result.source_basis.state_event_basis_sequence, 0);
});

test("mixed revision bases are rejected instead of compared", () => {
  assert.throws(
    () =>
      projectSharedGoalAlignment(
        baseRequest({
          source_basis: {
            state_event_basis_sequence: 0,
            source_basis_digest: DIGEST,
            revision_basis: "markdown_active_state",
            state_updated_at: null,
          },
        }),
      ),
    /cannot be compared/,
  );
});

test("conflict facts project from lease, replan, and peer-claim facts", () => {
  const result = projectSharedGoalAlignment(
    baseRequest({
      claims: [
        {
          todo_id: "todo_lane_a",
          claimed_by: "agent-a",
          lease_epoch: 2,
          lease_owner: "agent-b",
        },
      ],
      peer_claimed_bound_todo_ids: ["todo_peer_claimed"],
      open_lane_replan_obligation_required: true,
    }),
  );

  assert.deepEqual(result.drift_facts, []);
  assert.deepEqual(result.conflict_facts, [
    "lease_owner_mismatch",
    "open_lane_replan_obligation",
    "peer_claimed_lane_conflict",
  ]);
});

test("a matching lease owner is not a conflict", () => {
  const result = projectSharedGoalAlignment(baseRequest());

  assert.ok(!result.conflict_facts.includes("lease_owner_mismatch"));
});

test("half-present lease epoch/owner pairs are rejected", () => {
  // Lease facts travel as a pair: an active lease must carry both a
  // positive generation and a valid owner. A half-present pair is corrupt
  // authority that must fail closed at decode instead of projecting the
  // claim as conflict-free alignment.
  const epochWithoutOwner = {
    todo_id: "todo_lane_a",
    claimed_by: "agent-a",
    lease_epoch: 4,
  };
  assert.throws(
    () =>
      projectSharedGoalAlignment(
        baseRequest({ claims: [{ ...epochWithoutOwner, lease_owner: null }] }),
      ),
    /lease_epoch and lease_owner must be both present/,
  );
  assert.throws(
    () =>
      projectSharedGoalAlignment(
        baseRequest({ claims: [epochWithoutOwner] }),
      ),
    /lease_epoch and lease_owner must be both present/,
  );
  assert.throws(
    () =>
      projectSharedGoalAlignment(
        baseRequest({
          claims: [
            {
              todo_id: "todo_lane_a",
              claimed_by: "agent-a",
              lease_epoch: 5,
              lease_owner: "",
            },
          ],
        }),
      ),
    /lease_owner must be a non-empty string/,
  );
  assert.throws(
    () =>
      projectSharedGoalAlignment(
        baseRequest({
          claims: [
            {
              todo_id: "todo_lane_a",
              claimed_by: "agent-a",
              lease_epoch: null,
              lease_owner: "agent-b",
            },
          ],
        }),
      ),
    /lease_epoch and lease_owner must be both present/,
  );
});

test("request schema mismatch is rejected", () => {
  assert.throws(
    () =>
      projectSharedGoalAlignment(
        baseRequest({ schema_version: "shared_goal_alignment_request_v1" }),
      ),
    /schema mismatch/,
  );
});

test("unknown todo shape in unclaimed eligible work is rejected", () => {
  assert.throws(
    () =>
      projectSharedGoalAlignment(
        baseRequest({
          unclaimed_eligible: [
            { todo_id: "todo_lane_b", task_class: "continuous_monitor" },
          ],
        }),
      ),
    /task_class must be advancement_task/,
  );
});

test("a claim owned by another agent is rejected", () => {
  assert.throws(
    () =>
      projectSharedGoalAlignment(
        baseRequest({
          claims: [
            {
              todo_id: "todo_peer_claimed",
              claimed_by: "agent-b",
              lease_epoch: 1,
              lease_owner: "agent-b",
            },
          ],
        }),
      ),
    /claimed_by must match the projected agent/,
  );
});

test("a source basis digest that is not sha256 hex is rejected", () => {
  assert.throws(
    () =>
      projectSharedGoalAlignment(
        baseRequest({
          source_basis: {
            state_event_basis_sequence: 42,
            source_basis_digest: "md5:zz",
            revision_basis: "state_event_log",
            state_updated_at: null,
          },
        }),
      ),
    /source_basis_digest/,
  );
});

test("an unbound basis with a fabricated revision is rejected", () => {
  assert.throws(
    () =>
      projectSharedGoalAlignment(
        baseRequest({
          frontier_basis: {
            based_on_state_event_sequence: 7,
            basis_source: "unbound",
            last_agent_event_id: null,
          },
        }),
      ),
    /must be null when basis_source is unbound/,
  );
});

test("a todo both claimed and unclaimed-eligible is rejected", () => {
  assert.throws(
    () =>
      projectSharedGoalAlignment(
        baseRequest({
          unclaimed_eligible: [
            { todo_id: "todo_lane_a", task_class: "advancement_task" },
          ],
        }),
      ),
    /already claimed/,
  );
});

test("state_event_basis_sequence boundary values are rejected per revision basis", () => {
  assert.throws(
    () =>
      projectSharedGoalAlignment(
        baseRequest({
          source_basis: {
            state_event_basis_sequence: 0,
            source_basis_digest: DIGEST,
            revision_basis: "state_event_log",
            state_updated_at: null,
          },
        }),
      ),
    /must be a positive event append sequence/,
  );
  assert.throws(
    () =>
      projectSharedGoalAlignment(
        baseRequest({
          source_basis: {
            state_event_basis_sequence: 1,
            source_basis_digest: DIGEST,
            revision_basis: "markdown_active_state",
            state_updated_at: null,
          },
        }),
      ),
    /must be 0 when revision_basis is markdown_active_state/,
  );
  assert.throws(
    () =>
      projectSharedGoalAlignment(
        baseRequest({
          source_basis: {
            state_event_basis_sequence: "3",
            source_basis_digest: DIGEST,
            revision_basis: "state_event_log",
            state_updated_at: null,
          },
        }),
      ),
    /state_event_basis_sequence must be an integer/,
  );
});

test("a source basis digest of the wrong length or case is rejected", () => {
  const badDigests = [
    "sha256:" + "a".repeat(63),
    "sha256:" + "a".repeat(65),
    "sha256:" + "A".repeat(64),
  ];
  for (const source_basis_digest of badDigests) {
    assert.throws(
      () =>
        projectSharedGoalAlignment(
          baseRequest({
            source_basis: {
              state_event_basis_sequence: 42,
              source_basis_digest,
              revision_basis: "state_event_log",
              state_updated_at: null,
            },
          }),
        ),
      /must be a sha256:<hex> digest/,
    );
  }
});

test("agent_id and goal_id must survive strict decoding", () => {
  assert.throws(
    () => projectSharedGoalAlignment(baseRequest({ agent_id: 123 })),
    /agent_id must be a non-empty string/,
  );
  assert.throws(
    () => projectSharedGoalAlignment(baseRequest({ agent_id: "1agent" })),
    /agent_id must be a public-safe agent id/,
  );
  assert.throws(
    () => projectSharedGoalAlignment(baseRequest({ goal_id: null })),
    /goal_id must be a non-empty string/,
  );
});

test("frontier counts must be non-negative integers", () => {
  assert.throws(
    () =>
      projectSharedGoalAlignment(
        baseRequest({
          frontier_counts: {
            current_agent_claimed_advancement_count: -1,
            unclaimed_advancement_count: 2,
            other_agent_claimed_advancement_count: 0,
          },
        }),
      ),
    /current_agent_claimed_advancement_count must be non-negative/,
  );
  assert.throws(
    () =>
      projectSharedGoalAlignment(
        baseRequest({
          frontier_counts: {
            current_agent_claimed_advancement_count: 1,
            unclaimed_advancement_count: "2",
            other_agent_claimed_advancement_count: 0,
          },
        }),
      ),
    /unclaimed_advancement_count must be an integer/,
  );
});

test("corrupt claim lease fields fail closed", () => {
  const corruptLeases = [
    { lease_epoch: "2", lease_owner: "agent-a" },
    { lease_epoch: 0, lease_owner: "agent-a" },
    { lease_epoch: 2, lease_owner: 7 },
  ];
  for (const lease of corruptLeases) {
    assert.throws(
      () =>
        projectSharedGoalAlignment(
          baseRequest({
            claims: [
              {
                todo_id: "todo_lane_a",
                claimed_by: "agent-a",
                lease_epoch: lease.lease_epoch,
                lease_owner: lease.lease_owner,
              },
            ],
          }),
      ),
    );
  }
  assert.throws(
    () =>
      projectSharedGoalAlignment(
        baseRequest({
          claims: [
            {
              todo_id: "todo_lane_a",
              claimed_by: 42,
              lease_epoch: 2,
              lease_owner: "agent-a",
            },
          ],
        }),
      ),
    /claimed_by must be a non-empty string/,
  );
});

test("an unbound basis with a fabricated last event id is rejected", () => {
  assert.throws(
    () =>
      projectSharedGoalAlignment(
        baseRequest({
          frontier_basis: {
            based_on_state_event_sequence: null,
            basis_source: "unbound",
            last_agent_event_id: "evt_fabricated",
          },
        }),
      ),
    /last_agent_event_id must be null when basis_source is unbound/,
  );
});

test("duplicate todo ids and agent-claimed peer lanes are rejected", () => {
  assert.throws(
    () =>
      projectSharedGoalAlignment(
        baseRequest({
          claims: [
            {
              todo_id: "todo_lane_a",
              claimed_by: "agent-a",
              lease_epoch: 2,
              lease_owner: "agent-a",
            },
            {
              todo_id: "todo_lane_a",
              claimed_by: "agent-a",
              lease_epoch: 3,
              lease_owner: "agent-a",
            },
          ],
        }),
      ),
    /claims contains a duplicate todo_id/,
  );
  assert.throws(
    () =>
      projectSharedGoalAlignment(
        baseRequest({ peer_claimed_bound_todo_ids: ["todo_lane_a"] }),
      ),
    /cannot include a Todo already claimed/,
  );
  assert.throws(
    () =>
      projectSharedGoalAlignment(
        baseRequest({ peer_claimed_bound_todo_ids: ["peer-lane-9"] }),
      ),
    /peer_claimed_bound_todo_ids\[0\] must be a valid Todo id/,
  );
});

test("repeated projection of identical facts is deterministic", () => {
  const request = baseRequest({
    peer_claimed_bound_todo_ids: ["todo_peer_claimed"],
    open_lane_replan_obligation_required: true,
  });
  const first = projectSharedGoalAlignment(request);
  const second = projectSharedGoalAlignment(request);

  assert.deepEqual(first, second);
  assert.equal(JSON.stringify(first), JSON.stringify(second));
  assert.deepEqual(first.conflict_facts, [
    "open_lane_replan_obligation",
    "peer_claimed_lane_conflict",
  ]);
});

test("goal ids follow the repository safe single-segment contract", () => {
  // The repository Goal-ID contract (validate_goal_id_path_segment) is any
  // non-empty, safe single path segment — no "goal-" prefix required.
  // Registered ids such as "loopx-meta" must decode.
  for (const goalId of ["loopx-meta", "goal-shared", "Goal_X"]) {
    const result = projectSharedGoalAlignment(
      baseRequest({ goal_id: goalId }),
    );
    assert.equal(result.goal_id, goalId);
  }
  // Path traversal, separators, whitespace, and dot-only segments stay
  // rejected.
  for (const goalId of [
    ".",
    "..",
    "goal/../etc",
    "a\\b",
    "my goal",
    " goal-x",
    "goal-x ",
  ]) {
    assert.throws(
      () => projectSharedGoalAlignment(baseRequest({ goal_id: goalId })),
      /safe single-segment goal id/,
    );
  }
});

test("a non-boolean open lane replan flag is rejected", () => {
  assert.throws(
    () =>
      projectSharedGoalAlignment(
        baseRequest({ open_lane_replan_obligation_required: "true" }),
      ),
    /open_lane_replan_obligation_required/,
  );
  assert.throws(
    () =>
      projectSharedGoalAlignment(
        baseRequest({ open_lane_replan_obligation_required: 1 }),
      ),
    /open_lane_replan_obligation_required/,
  );
});
