import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import {
  MAX_COLLABORATION_OPPORTUNITIES,
  MAX_PEER_FRONTIER_ITEMS,
  projectPeerCoordination,
  type CallerLaneSummary,
  type CollaborationOpportunity,
  type PeerFrontierItem,
} from "../../loopx/control_plane/goals/peer_coordination.ts";
import {
  SHARED_GOAL_ALIGNMENT_SCHEMA_VERSION,
  projectSharedGoalAlignment,
} from "../../loopx/control_plane/goals/shared_goal_alignment.ts";
import { evaluateQuotaSchedulingWriteScopesOverlap } from "../../loopx/control_plane/work_items/task_lease_acquire_decision.ts";
import { repositoryScopesMayOverlap } from "../../loopx/control_plane/work_items/task_lease_repository.ts";

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

test("work_items project deterministic peer_frontiers, all four opportunity kinds, and idle discovery", () => {
  const workRequest = (agentId: string, workItems: unknown[]) => ({
    schema_version: "shared_goal_alignment_request_v0",
    goal_id: "personal-loopx-maintenance",
    agent_id: agentId,
    source_basis: {
      revision_basis: "canonical_todo_snapshot",
      state_event_basis_sequence: 0,
      source_basis_digest: DIGEST,
      state_updated_at: null,
      todo_basis: {
        source_authority: "sqlite_v0",
        provider_revision: "sqlite:rev:1",
        records_sha256: "b".repeat(64),
      },
    },
    frontier_basis: {
      basis_source: "unbound",
      based_on_state_event_sequence: null,
      last_agent_event_id: null,
    },
    work_items: workItems,
    observed_at: "2026-11-27T01:00:00Z",
    open_lane_replan_obligation_required: false,
  });

  const sharedWork = [
    {
      todo_id: "todo_caller_verify",
      status: "open",
      done: false,
      task_class: "advancement_task",
      archive_state: "active",
      claimed_by: "edgebench-analysis",
      priority: "P0",
      action_kind: "verify",
      task_domain: "control_plane",
      task_repository: "https://github.com/loopx-project/loopx.git",
      target_key: "quota-envelope",
      required_write_scopes: ["loopx/control_plane/quota/"],
      completion_validation_required: false,
      lease: {
        schema_version: "task_lease_v0",
        owner: "edgebench-analysis",
        lease_epoch: 1,
        expires_at: "2026-11-27T04:00:00Z",
      },
    },
    {
      todo_id: "todo_peer_slim",
      status: "open",
      done: false,
      task_class: "advancement_task",
      archive_state: "active",
      claimed_by: "antigravity",
      priority: "P0",
      action_kind: "refactor",
      task_domain: "control_plane",
      task_repository: "git:github.com/loopx-project/loopx",
      target_key: "quota-envelope",
      required_write_scopes: ["loopx/control_plane/quota/turn_envelope.ts"],
      completion_validation_required: true,
      lease: null,
    },
    {
      todo_id: "todo_peer_bench_domain",
      status: "open",
      done: false,
      task_class: "advancement_task",
      archive_state: "active",
      claimed_by: "antigravity",
      priority: "P2",
      action_kind: "code",
      task_domain: "control_plane",
      task_repository: "git:github.com/loopx-project/loopx",
      target_key: "hosts-consolidation",
      required_write_scopes: ["loopx/hosts/"],
      completion_validation_required: false,
      lease: null,
    },
    {
      todo_id: "todo_other_repo",
      status: "open",
      done: false,
      task_class: "advancement_task",
      archive_state: "active",
      claimed_by: "value-scout",
      priority: "P1",
      action_kind: "code",
      task_domain: "control_plane",
      task_repository: "git:github.com/loopx-project/loopx-cloud",
      target_key: "quota-envelope",
      required_write_scopes: ["loopx/control_plane/quota/"],
      completion_validation_required: false,
      lease: null,
    },
  ];

  const active = projectSharedGoalAlignment(workRequest("edgebench-analysis", sharedWork));
  assert.equal(active.read_only, true);
  assert.equal(active.peer_frontiers.length, 3);
  assert.equal(active.peer_frontiers[0].todo_id, "todo_peer_slim");
  assert.equal(active.peer_frontiers[0].lease_active, false);

  const kinds = active.collaboration_opportunities.map((o) => o.kind);
  assert.deepEqual(kinds, [
    "shared_target_collision_risk",
    "peer_verification_needed",
    "peer_unleased_lane_attention",
    "shared_domain_synergy",
  ]);
  for (const opp of active.collaboration_opportunities) {
    assert.equal(opp.grants_authority, false);
    assert.match(String(opp.opportunity_id), /^opp_[0-9a-f]{16}$/);
  }
  assert.ok(!active.collaboration_opportunities.some((o) => o.peer_todo_id === "todo_other_repo"));
  // Collision pair suppresses redundant shared_domain_synergy on todo_peer_slim,
  // while keeping shared_domain_synergy on todo_peer_bench_domain.
  const synergy = active.collaboration_opportunities.find((o) => o.kind === "shared_domain_synergy");
  assert.equal(synergy?.peer_todo_id, "todo_peer_bench_domain");

  const idle = projectSharedGoalAlignment(workRequest("review-a", sharedWork));
  assert.equal(idle.frontier_counts.current_agent_claimed_advancement_count, 0);
  const idleVer = idle.collaboration_opportunities.find((o) => o.kind === "peer_verification_needed");
  assert.equal(idleVer?.caller_todo_id, null);
  assert.deepEqual(idleVer?.matched_on, ["completion_validation_required", "idle_caller_discovery"]);
  assert.deepEqual(
    idle.collaboration_opportunities
      .filter((o) => o.peer_todo_id === "todo_peer_slim")
      .map((o) => o.kind),
    ["peer_verification_needed", "peer_unleased_lane_attention"],
  );
});

test("corrupt peer lease degrades safely to lease_active null and caps peer outputs", () => {
  const peers = Array.from({ length: 15 }, (_, idx) => ({
    todo_id: `todo_peer_${String(idx).padStart(2, "0")}`,
    status: "open",
    done: false,
    task_class: "advancement_task",
    archive_state: "active",
    claimed_by: "agent-b",
    priority: "P0",
    action_kind: "code",
    task_domain: "kernel",
    required_write_scopes: ["loopx/control_plane/"],
    completion_validation_required: true,
    lease_read_error: idx === 0 ? "corrupt peer lease" : null,
    lease: null,
  }));
  const res = projectSharedGoalAlignment({
    ...baseRequest(),
    claims: undefined,
    frontier_counts: undefined,
    unclaimed_eligible: undefined,
    peer_claimed_bound_todo_ids: undefined,
    work_items: peers,
    observed_at: "2026-11-27T01:00:00Z",
  });
  assert.equal(res.peer_frontiers.length, 12);
  assert.equal(res.peer_frontiers[0].lease_active, null);
  assert.equal(res.collaboration_opportunities.length, 8);
});

test("corrupt active peer lease epoch or owner degrades to lease_active null without false unleased attention", () => {
  const makePeer = (todoId: string, lease: unknown) => ({
    todo_id: todoId,
    status: "open",
    done: false,
    task_class: "advancement_task",
    archive_state: "active",
    claimed_by: "agent-b",
    priority: "P0",
    action_kind: "code",
    task_domain: "kernel",
    completion_validation_required: false,
    lease,
  });

  const res = projectSharedGoalAlignment({
    ...baseRequest(),
    claims: undefined,
    frontier_counts: undefined,
    unclaimed_eligible: undefined,
    peer_claimed_bound_todo_ids: undefined,
    work_items: [
      makePeer("todo_peer_epoch_zero", {
        schema_version: "task_lease_v0",
        status: "active",
        expires_at: "2099-01-01T00:00:00Z",
        owner: "agent-b",
        lease_epoch: 0,
        version: 1,
      }),
      makePeer("todo_peer_bad_owner", {
        schema_version: "task_lease_v0",
        status: "active",
        expires_at: "2099-01-01T00:00:00Z",
        owner: "",
        lease_epoch: 2,
        version: 1,
      }),
      makePeer("todo_peer_missing_owner", {
        schema_version: "task_lease_v0",
        status: "active",
        expires_at: "2099-01-01T00:00:00Z",
        lease_epoch: 2,
        version: 1,
      }),
      makePeer("todo_peer_valid_active", {
        schema_version: "task_lease_v0",
        status: "active",
        expires_at: "2099-01-01T00:00:00Z",
        owner: "agent-b",
        lease_epoch: 2,
        version: 1,
      }),
      makePeer("todo_peer_released", {
        schema_version: "task_lease_v0",
        status: "released",
        expires_at: "2099-01-01T00:00:00Z",
        owner: "agent-b",
        lease_epoch: 2,
        version: 1,
      }),
    ],
    observed_at: "2026-11-27T01:00:00Z",
  });

  const byTodo = new Map(res.peer_frontiers.map((p) => [p.todo_id, p]));
  assert.equal(byTodo.get("todo_peer_epoch_zero")?.lease_active, null);
  assert.equal(byTodo.get("todo_peer_bad_owner")?.lease_active, null);
  assert.equal(byTodo.get("todo_peer_missing_owner")?.lease_active, null);
  assert.equal(byTodo.get("todo_peer_valid_active")?.lease_active, true);
  assert.equal(byTodo.get("todo_peer_released")?.lease_active, false);
  // Only the genuinely unleased (released) peer should emit peer_unleased_lane_attention.
  assert.deepEqual(
    res.collaboration_opportunities.map((o) => [o.kind, o.peer_todo_id]),
    [["peer_unleased_lane_attention", "todo_peer_released"]],
  );
});

test("idle caller emits both verification and unleased attention for a single P0/P1 peer", () => {
  const mkIdleReq = (lease: unknown, priority = "P0") => ({
    ...baseRequest(),
    claims: undefined,
    frontier_counts: undefined,
    unclaimed_eligible: undefined,
    peer_claimed_bound_todo_ids: undefined,
    work_items: [
      {
        todo_id: "todo_single_peer",
        status: "open",
        done: false,
        task_class: "advancement_task",
        archive_state: "active",
        claimed_by: "agent-b",
        priority,
        action_kind: "refactor",
        task_domain: "control_plane",
        completion_validation_required: true,
        lease,
      },
    ],
    observed_at: "2026-11-27T01:00:00Z",
  });

  const unleased = projectSharedGoalAlignment(mkIdleReq(null, "P0"));
  assert.deepEqual(
    unleased.collaboration_opportunities.map((o) => o.kind),
    ["peer_verification_needed", "peer_unleased_lane_attention"],
  );
  assert.ok(unleased.collaboration_opportunities.every((o) => o.caller_todo_id === null));

  const leased = projectSharedGoalAlignment(mkIdleReq({
    schema_version: "task_lease_v0",
    status: "active",
    expires_at: "2099-01-01T00:00:00Z",
    owner: "agent-b",
    lease_epoch: 1,
    version: 1,
  }, "P0"));
  assert.deepEqual(
    leased.collaboration_opportunities.map((o) => o.kind),
    ["peer_verification_needed"],
  );
});

test("unknown lease schema_version or invalid status degrades only that peer to lease_active null", () => {
  const mkReq = (lease: unknown) => ({
    ...baseRequest(),
    claims: undefined,
    frontier_counts: undefined,
    unclaimed_eligible: undefined,
    peer_claimed_bound_todo_ids: undefined,
    work_items: [
      {
        todo_id: "todo_peer_unknown_schema",
        status: "open",
        done: false,
        task_class: "advancement_task",
        archive_state: "active",
        claimed_by: "agent-b",
        priority: "P0",
        action_kind: "code",
        task_domain: "control_plane",
        completion_validation_required: false,
        lease,
      },
      {
        todo_id: "todo_peer_absent_lease",
        status: "open",
        done: false,
        task_class: "advancement_task",
        archive_state: "active",
        claimed_by: "agent-c",
        priority: "P1",
        action_kind: "code",
        task_domain: "control_plane",
        completion_validation_required: false,
        lease: null,
      },
      {
        todo_id: "todo_peer_expired_known",
        status: "open",
        done: false,
        task_class: "advancement_task",
        archive_state: "active",
        claimed_by: "agent-d",
        priority: "P1",
        action_kind: "code",
        task_domain: "control_plane",
        completion_validation_required: false,
        lease: {
          schema_version: "task_lease_v0",
          status: "active",
          expires_at: "2020-01-01T00:00:00Z",
          owner: "agent-d",
          lease_epoch: 1,
          version: 1,
        },
      },
    ],
    observed_at: "2026-11-27T01:00:00Z",
  });

  for (const invalidLease of [
    {
      schema_version: "task_lease_v99",
      status: "active",
      expires_at: "2099-01-01T00:00:00Z",
      owner: "agent-b",
      lease_epoch: 1,
      version: 1,
    },
    {
      schema_version: "task_lease_v0",
      status: "suspended",
      expires_at: "2099-01-01T00:00:00Z",
      owner: "agent-b",
      lease_epoch: 1,
      version: 1,
    },
    {
      schema_version: "task_lease_v0",
      status: "released",
      expires_at: "bad-timestamp",
      owner: "agent-b",
      lease_epoch: 1,
      version: 1,
    },
    {
      owner: "agent-b",
      status: "active",
      expires_at: "2099-01-01T00:00:00Z",
    },
  ]) {
    const res = projectSharedGoalAlignment(mkReq(invalidLease));
    const byId = new Map(res.peer_frontiers.map((p) => [p.todo_id, p]));
    assert.equal(byId.get("todo_peer_unknown_schema")?.lease_active, null);
    assert.equal(byId.get("todo_peer_absent_lease")?.lease_active, false);
    assert.equal(byId.get("todo_peer_expired_known")?.lease_active, false);
    assert.ok(
      !res.collaboration_opportunities.some((o) => o.peer_todo_id === "todo_peer_unknown_schema"),
      "uninterpretable peer lease must not emit peer_unleased_lane_attention",
    );
    assert.ok(
      res.collaboration_opportunities.some(
        (o) => o.kind === "peer_unleased_lane_attention" && o.peer_todo_id === "todo_peer_absent_lease",
      ),
    );
  }

  // Switching the exact fixture record back to task_lease_v0 yields lease_active === true.
  const valid = projectSharedGoalAlignment(mkReq({
    schema_version: "task_lease_v0",
    status: "active",
    expires_at: "2099-01-01T00:00:00Z",
    owner: "agent-b",
    lease_epoch: 1,
    version: 1,
  }));
  const validById = new Map(valid.peer_frontiers.map((p) => [p.todo_id, p]));
  assert.equal(validById.get("todo_peer_unknown_schema")?.lease_active, true);
});

function referencePeerCoordination(
  callerLanes: readonly CallerLaneSummary[],
  peerLanes: readonly PeerFrontierItem[],
) {
  const rankMap: Record<string, number> = { P0: 0, P1: 1, P2: 2, P3: 3, P4: 4 };
  const pRank = (p: string | null) => (p !== null && p in rankMap ? rankMap[p] : 50);
  const kindRank: Record<string, number> = {
    shared_target_collision_risk: 0,
    peer_verification_needed: 1,
    peer_unleased_lane_attention: 2,
    shared_domain_synergy: 3,
  };
  const verifiers = new Set(["verify", "review", "benchmark", "evaluate", "audit"]);
  const oppId = (kind: string, peerAgent: string, peerTodo: string, callerTodo: string | null, matched: string[]) =>
    `opp_${createHash("sha256").update(JSON.stringify([kind, peerAgent, peerTodo, callerTodo ?? "", ...matched]), "utf8").digest("hex").slice(0, 16)}`;

  const sortedCallers = [...callerLanes].sort((a, b) => a.todo_id.localeCompare(b.todo_id));
  const sortedPeers = [...peerLanes].sort(
    (a, b) => pRank(a.priority) - pRank(b.priority)
      || a.agent_id.localeCompare(b.agent_id)
      || a.todo_id.localeCompare(b.todo_id),
  );
  const peerRankByTodo = new Map(sortedPeers.map((p) => [p.todo_id, pRank(p.priority)]));
  const raw: CollaborationOpportunity[] = [];
  if (sortedCallers.length === 0) {
    for (const peer of sortedPeers) {
      if (peer.completion_validation_required) {
        const matched = ["completion_validation_required", "idle_caller_discovery"];
        raw.push({
          opportunity_id: oppId("peer_verification_needed", peer.agent_id, peer.todo_id, null, matched),
          kind: "peer_verification_needed", peer_agent_id: peer.agent_id, peer_todo_id: peer.todo_id,
          caller_todo_id: null, matched_on: matched, advisory_action: "offer_independent_verification", grants_authority: false,
        });
      }
      if (peer.lease_active === false && (peer.priority === "P0" || peer.priority === "P1")) {
        const matched = ["lease_inactive", "idle_caller_discovery"];
        raw.push({
          opportunity_id: oppId("peer_unleased_lane_attention", peer.agent_id, peer.todo_id, null, matched),
          kind: "peer_unleased_lane_attention", peer_agent_id: peer.agent_id, peer_todo_id: peer.todo_id,
          caller_todo_id: null, matched_on: matched, advisory_action: "inspect_peer_status_before_duplicating", grants_authority: false,
        });
      }
    }
  } else {
    for (const caller of sortedCallers) {
      for (const peer of sortedPeers) {
        const repoCompat = repositoryScopesMayOverlap(caller.task_repository, peer.task_repository);
        const sameTarget = Boolean(caller.target_key && peer.target_key && caller.target_key === peer.target_key);
        const scopeHit = repoCompat && caller.required_write_scopes.length > 0 && peer.required_write_scopes.length > 0
          && evaluateQuotaSchedulingWriteScopesOverlap({
            left: [...caller.required_write_scopes],
            right: [...peer.required_write_scopes],
          }).overlap === true;
        const sameDomain = Boolean(caller.task_domain && peer.task_domain && caller.task_domain === peer.task_domain);
        const sameRepo = Boolean(
          caller.task_repository && peer.task_repository
          && caller.task_repository.toLowerCase() === peer.task_repository.toLowerCase(),
        );
        const cSignals: string[] = [];
        if (repoCompat && sameTarget) cSignals.push("target_key");
        if (scopeHit) cSignals.push("required_write_scopes");
        if (cSignals.length > 0) {
          raw.push({
            opportunity_id: oppId("shared_target_collision_risk", peer.agent_id, peer.todo_id, caller.todo_id, cSignals),
            kind: "shared_target_collision_risk", peer_agent_id: peer.agent_id, peer_todo_id: peer.todo_id,
            caller_todo_id: caller.todo_id, matched_on: cSignals, advisory_action: "deconflict_before_edit", grants_authority: false,
          });
        }
        const isVer = Boolean(caller.action_kind && verifiers.has(caller.action_kind));
        if (peer.completion_validation_required && repoCompat && (isVer || sameDomain || sameRepo)) {
          const matched = ["completion_validation_required"];
          if (isVer) matched.push("caller_verifier_action_kind");
          if (sameDomain) matched.push("task_domain");
          if (sameRepo) matched.push("task_repository");
          raw.push({
            opportunity_id: oppId("peer_verification_needed", peer.agent_id, peer.todo_id, caller.todo_id, matched),
            kind: "peer_verification_needed", peer_agent_id: peer.agent_id, peer_todo_id: peer.todo_id,
            caller_todo_id: caller.todo_id, matched_on: matched, advisory_action: "offer_independent_verification", grants_authority: false,
          });
        }
        if (peer.lease_active === false && (peer.priority === "P0" || peer.priority === "P1") && repoCompat && (sameTarget || sameDomain)) {
          const matched = ["lease_inactive"];
          if (sameTarget) matched.push("target_key");
          if (sameDomain) matched.push("task_domain");
          raw.push({
            opportunity_id: oppId("peer_unleased_lane_attention", peer.agent_id, peer.todo_id, caller.todo_id, matched),
            kind: "peer_unleased_lane_attention", peer_agent_id: peer.agent_id, peer_todo_id: peer.todo_id,
            caller_todo_id: caller.todo_id, matched_on: matched, advisory_action: "inspect_peer_status_before_duplicating", grants_authority: false,
          });
        }
        if (cSignals.length === 0 && repoCompat && sameDomain) {
          const matched = ["task_domain"];
          if (sameRepo) matched.push("task_repository");
          raw.push({
            opportunity_id: oppId("shared_domain_synergy", peer.agent_id, peer.todo_id, caller.todo_id, matched),
            kind: "shared_domain_synergy", peer_agent_id: peer.agent_id, peer_todo_id: peer.todo_id,
            caller_todo_id: caller.todo_id, matched_on: matched, advisory_action: "align_with_peer_deliverable", grants_authority: false,
          });
        }
      }
    }
  }
  const deduped = new Map<string, CollaborationOpportunity>();
  for (const opp of raw) {
    const k = `${opp.kind}|${opp.peer_agent_id}|${opp.peer_todo_id}|${opp.caller_todo_id ?? ""}`;
    if (!deduped.has(k)) deduped.set(k, opp);
  }
  const opps = [...deduped.values()].sort(
    (a, b) => kindRank[a.kind] - kindRank[b.kind]
      || (peerRankByTodo.get(a.peer_todo_id) ?? 50) - (peerRankByTodo.get(b.peer_todo_id) ?? 50)
      || a.peer_todo_id.localeCompare(b.peer_todo_id)
      || (a.caller_todo_id ?? "").localeCompare(b.caller_todo_id ?? "")
      || a.opportunity_id.localeCompare(b.opportunity_id),
  );
  return {
    peer_frontiers: sortedPeers.slice(0, MAX_PEER_FRONTIER_ITEMS),
    collaboration_opportunities: opps.slice(0, MAX_COLLABORATION_OPPORTUNITIES),
  };
}

test("bounded indexed projectPeerCoordination matches exhaustive reference parity across globs, ties, and 160x160 scale", () => {
  const scopesPool = [
    [],
    ["loopx/control_plane/goals/shared_goal_work.ts"],
    ["loopx/control_plane/goals/*.ts"],
    ["loopx/control_plane/**"],
    ["LoopX/Control_Plane/Goals/"],
    ["loopx/a*.ts"],
    ["*.md"],
    ["./"],
    ["packages/dsh-loopx-plugin/src/driver.ts"],
    ["docs/architecture/rfcs/"],
  ];
  const repos = [null, "git:github.com/loopx-project/loopx", "git:github.com/loopx-project/loopx-cloud"];
  const domains = [null, "control_plane", "microkernel", "docs"];
  const targets = [null, "quota", "hosts", "fold"];
  const priorities = ["P0", "P1", "P2", null];
  const actions = ["code", "verify", "review", "refactor"];
  const leaseStates: (boolean | null)[] = [true, false, null];

  let seed = 0x9502cafe;
  const nextInt = (max: number) => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return seed % max;
  };

  for (let trial = 0; trial < 50; trial += 1) {
    const numCallers = trial % 7 === 0 ? 0 : 1 + nextInt(18);
    const numPeers = 1 + nextInt(22);
    const callers: CallerLaneSummary[] = Array.from({ length: numCallers }, (_, i) => ({
      todo_id: `todo_c_${i}`,
      priority: priorities[nextInt(priorities.length)],
      action_kind: actions[nextInt(actions.length)],
      task_domain: domains[nextInt(domains.length)],
      task_repository: repos[nextInt(repos.length)],
      target_key: targets[nextInt(targets.length)],
      required_write_scopes: scopesPool[nextInt(scopesPool.length)],
      completion_validation_required: nextInt(2) === 1,
      lease_active: leaseStates[nextInt(leaseStates.length)],
    }));
    const peers: PeerFrontierItem[] = Array.from({ length: numPeers }, (_, i) => ({
      // Include occasional tied peer_todo_id across different agent_ids to exercise opportunity_id tie-breaks.
      todo_id: `todo_p_${i > 0 && nextInt(8) === 0 ? i - 1 : i}`,
      agent_id: `agent-${nextInt(5)}`,
      priority: priorities[nextInt(priorities.length)],
      action_kind: actions[nextInt(actions.length)],
      task_domain: domains[nextInt(domains.length)],
      task_repository: repos[nextInt(repos.length)],
      target_key: targets[nextInt(targets.length)],
      required_write_scopes: scopesPool[nextInt(scopesPool.length)],
      completion_validation_required: nextInt(2) === 1,
      lease_active: leaseStates[nextInt(leaseStates.length)],
    }));

    assert.deepEqual(
      projectPeerCoordination(callers, peers),
      referencePeerCoordination(callers, peers),
      `parity mismatch on trial ${trial}`,
    );
  }

  // Large 160x160 scale parity + bounded wall-time regression guard (disjoint + dense cases).
  const largeCallers: CallerLaneSummary[] = Array.from({ length: 160 }, (_, i) => ({
    todo_id: `todo_c_${String(i).padStart(3, "0")}`,
    priority: "P0",
    action_kind: i === 150 ? "verify" : "code",
    task_domain: `dom_${i % 10}`,
    task_repository: "git:github.com/loopx-project/loopx",
    target_key: `tgt_c_${i}`,
    required_write_scopes: [`loopx/c_mod_${i}/file.ts`],
    completion_validation_required: false,
    lease_active: true,
  }));
  const largePeers: PeerFrontierItem[] = Array.from({ length: 160 }, (_, i) => ({
    agent_id: `agent-${i}`,
    todo_id: `todo_p_${String(i).padStart(3, "0")}`,
    priority: i % 2 === 0 ? "P0" : "P1",
    action_kind: "refactor",
    task_domain: `dom_${i % 10}`,
    task_repository: "git:github.com/loopx-project/loopx",
    target_key: i === 155 ? "tgt_c_12" : `tgt_p_${i}`,
    required_write_scopes: [`loopx/p_mod_${i}/sub.ts`],
    completion_validation_required: true,
    lease_active: false,
  }));

  const expectedLarge = referencePeerCoordination(largeCallers, largePeers);
  const t0 = performance.now();
  const actualLarge = projectPeerCoordination(largeCallers, largePeers);
  const elapsedMs = performance.now() - t0;
  assert.deepEqual(actualLarge, expectedLarge);
  assert.ok(elapsedMs < 25, `expected <25ms for 160x160 projection, got ${elapsedMs.toFixed(2)}ms`);
});
