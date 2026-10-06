# Peer Agent Runtime v1

## Purpose

`peer_v1` removes durable agent rank from LoopX runtime decisions. Registered
agents have equal identity authority. Work ownership comes from todo claims,
task leases, explicit continuation policy, and bounded task-scoped assignment.

Functional profiles may still describe capabilities or preferred scopes. They
must not grant one identity implicit review, merge, routing, or replan authority
over every other identity.

## Canonical Identity

A peer identity contains:

```json
{
  "schema_version": "peer_agent_identity_v1",
  "agent_model": "peer_v1",
  "agent_id": "codex-alpha",
  "registered": true,
  "registered_agents": ["codex-alpha", "codex-beta"]
}
```

Canonical peer output must not contain `primary_agent`, `handoff_agent`, or a
rank-bearing `role` field, including null-valued placeholders.

## Work Ownership

1. An explicit todo `claimed_by` or active task lease wins.
2. An unclaimed todo must be claimed or leased before delivery.
3. An explicitly agent-scoped replan obligation stays with that agent.
4. An unscoped replan obligation is assigned to exactly one registered peer by
   hashing a canonical work key over the sorted registered-agent set.
5. Registration order must not change deterministic assignment.

The deterministic assignment is coordination for one work item. It does not
change identity authority and must not be persisted as an agent rank.

## Completion And Review

Continuation behavior is task policy:

- `independent_handoff`: leave the successor unclaimed unless an explicit peer
  is selected;
- `same_agent_non_delivery`: keep the successor with the completing peer.

Review is an `action_kind`, not a continuation type. Use an ordinary
`independent_handoff`; when the task must stay open for any eligible peer except
the author, add that author to `excluded_agents`. An explicit `claimed_by` must
never name an excluded peer.

Repository merge permission remains governed by the repository's maintainer
policy. Peer identity alone neither grants nor removes self-merge permission.
The canonical completion flag is `--self-merged`.

## Workspace Isolation

Every peer is subject to the same workspace rule. `agent_workspace_guard_v1`
requires an independent git worktree when the selected todo declares write
scopes, uses a write-class action kind, or goal policy explicitly requires
isolation. Read-only observation and monitor work do not trigger the guard by
identity alone.

For a task owned by a repository other than the goal repository, the agent todo
may declare `task_repository` as a credential-free canonical identity such as
`git:github.com/owner/repo`. The guard then requires a linked worktree whose
origin matches that identity. The field selects the repository used for
workspace isolation only: it is not an agent scope, write scope, permission
grant, or replacement for claim/lease and goal-boundary checks. Without the
field, the goal repository remains authoritative.

Accountable refresh applies Git isolation to Git delivery. A registered non-Git
Goal can instead record the existing path-free `local_goal_workspace` receipt
for peer research or material work. Capture must occur inside that Goal root;
an explicit Git task repository or owner isolation requirement cannot use this
route. Completion validation remains independent, and in-flight writeback and
spend leave the Todo open. Do not reclassify a local deliverable as
`same_agent_non_delivery` to settle it. `--evidence` records a local pointer;
`--result-file` requires approved Goal acceptance criteria bound to the Todo.

## Task-Scoped Coordination

When bounded multi-agent orchestration is enabled, LoopX hashes the canonical
task bundle and selects one temporary coordinator. The resulting
`task_orchestration_contract_v1`:

- is scoped to that task bundle;
- activates or resumes eligible peer lanes;
- gives the coordinator writeback responsibility only for accepted bundle
  evidence;
- does not make the coordinator a durable leader.

## Migration

For an old registry, first let `quota should-run` or `upgrade-plan` project the
stable migration id and per-peer heartbeat commands. Update each installed host
automation idempotently with that migration id, then acknowledge the completed
host update:

```bash
loopx configure-goal \
  --goal-id <goal-id> \
  --ack-automation-prompt-migration <migration-id> \
  --execute
```

The acknowledgement validates the current migration id, creates a timestamped
registry backup, atomically removes hierarchy authority fields, and records the
completed migration. Repeating the same acknowledgement is a no-op, and future
quota checks do not project the completed migration again.

The completion marker is final for this migration version. If a stale v0.1
writer later reintroduces a hierarchy field, peer runtime ignores that field
and does not wake the user with the same automation migration again. Upgrade
diagnostics may still expose the stale input for cleanup.

Implementation boundary: legacy field names, detection, profile conversion,
and completion bookkeeping live in the isolated `legacy_migration` module.
`runtime_model` contains only the live `peer_v1` model and does not branch on
primary/side identity concepts.

Rollback restores the returned `backup_path`, then regenerates installed host
loops from that restored registry. Registry restoration and host-loop
regeneration are one operational rollback.

## v0.2 Cutover Gate

The peer runtime may land internally before the public v0.2 cutover so its
migration can be validated against v0.1 state. v0.2 is not releasable until:

- canonical fixtures and docs use `peer_v1`;
- heartbeat, quota, status, completion, workspace, and orchestration runtime no
  longer execute hierarchy branches;
- old hierarchy fields are accepted only by migration/history readers;
- the peer-agent canary profile and full smoke suite pass.

An optional supervisor is an overlay on this peer model, not a replacement for
it. See [Peer Supervisor v0](peer-supervisor-v0.md).
