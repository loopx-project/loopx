# Agent Profile Contract

`agent_profile_v1` is an optional registry-owned description of a recurring
LoopX peer. It gives prompt generation and task selection a compact capability
and scope hint without assigning durable rank.

Every registered identity has the same runtime authority. Claims, task leases,
goal/write boundaries, capability gates, typed continuation policy, and
repository policy decide what a peer may do. A profile cannot make an agent the
default leader, reviewer, merger, or owner of another peer's work.

## Layering

Keep these concepts separate:

| Layer | Question answered | Source of truth |
| --- | --- | --- |
| `agent_profile_v1` | What work is this peer usually useful for? | `coordination.agent_profiles` |
| `agent_member_v1` | What should status or a review packet show now? | Registry, quota, todos, and run history |
| `claimed_by` / `task_lease_v0` | Who owns this exact todo? | Active-state todo metadata and lease records |
| Task/repository policy | Does this task require isolation, review, or a merge gate? | Todo metadata, goal boundary, and repository policy |

Profile matching is advisory. It must not override a user gate, quota state,
required capability, protected write scope, workspace guard, or another peer's
claim or lease.

## Registry Shape

```json
{
  "coordination": {
    "agent_model": "peer_v1",
    "registered_agents": ["codex-runtime", "codex-product"],
    "agent_profiles": {
      "codex-runtime": {
        "schema_version": "agent_profile_v1",
        "agent_id": "codex-runtime",
        "profile_role": "runtime-validation",
        "scope_summary": "Runtime changes, integration checks, and release validation.",
        "default_task_classes": ["advancement_task"],
        "preferred_action_kinds": ["runtime_*", "validation_*"],
        "avoid_action_kinds": ["production_*"]
      },
      "codex-product": {
        "schema_version": "agent_profile_v1",
        "agent_id": "codex-product",
        "profile_role": "product-documentation",
        "scope_summary": "Product ergonomics, examples, documentation, and focused smokes.",
        "default_task_classes": ["advancement_task", "continuous_monitor"],
        "preferred_action_kinds": ["product_*", "docs_*", "smoke_*"],
        "avoid_action_kinds": ["production_*"]
      }
    }
  }
}
```

Profiles are written through the same validated registry path as other goal
coordination settings:

```bash
loopx configure-goal --goal-id <goal-id> \
  --agent-profile-json '{"schema_version":"agent_profile_v1","agent_id":"codex-runtime","profile_role":"runtime-validation","preferred_action_kinds":["runtime_*"]}' \
  --execute
```

Use `--clear-agent-profile <agent-id>` to remove one profile. The command
rejects unregistered peers, unknown fields, hierarchy roles, invalid task
classes, and unsafe action-kind globs before writing the registry.

`profile_role` is a human-readable functional label. It is advisory and must
not use hierarchy labels such as `primary-agent` or `side-agent`.

Do not put these in a profile:

- a parent, leader, or default reviewer identity;
- identity-level merge permission;
- identity-level workspace isolation rules;
- an implicit handoff target.

Those rules vary by task and repository. Encoding them in identity recreates
durable hierarchy and makes the same peer behave incorrectly when it changes
tasks.

## Prompt Generation

The common path resolves the registered profile automatically:

```bash
loopx heartbeat-prompt --thin \
  --goal-id loopx-meta \
  --agent-id codex-product
```

The prompt may include the profile's scope summary, task classes, and action
preferences. It still tells the peer to claim or lease work, follow the current
task policy, and run `quota should-run` before delivery. `--agent-scope` remains
an explicit temporary override for detached validation or one-off automation.

If profiles are configured and the requested registered peer has no profile,
prompt generation may continue with peer identity only.

## Selection Rules

Profile-backed selection may:

1. prefer current-agent claims, then unclaimed eligible todos;
2. rank matching task classes or action kinds higher;
3. avoid mismatched action kinds when another eligible peer or unclaimed task
   is available;
4. project a concrete reassignment request when only other-peer claims remain.

The preference rank is applied inside the existing claim bucket. A current
peer claim remains ahead of an unclaimed todo even when the unclaimed todo is a
better profile match. An explicit active-next todo also keeps its existing
route. Without a valid profile, candidate ordering is unchanged.

Selection must then enforce capability gates, task leases, workspace guards,
write scope, and continuation policy. A profile preference never transfers a
claim and never turns other-peer work into an executable candidate.

## Workspace, Review, And Completion

Workspace isolation is derived from the selected task and repository policy.
A repository-writing task may require an independent worktree for any peer;
read-only and monitor-only tasks do not require isolation merely because of an
identity label.

Completion is also task-scoped:

- direct validated completion uses the repository's normal merge policy;
- `independent_handoff` creates a non-blocking successor;
- `same_agent_non_delivery` keeps a same-peer continuation.

Review is an `action_kind` over an ordinary independent handoff. No profile
supplies an implicit reviewer; `excluded_agents` is available only when the
task needs executor separation.

## Projection

`agent_member_v1` is a read-only observation, not an authority record:

```json
{
  "schema_version": "agent_member_v1",
  "agent_id": "codex-product",
  "agent_model": "peer_v1",
  "profile_role": "product-documentation",
  "profile_role_is_advisory": true,
  "scope_summary": "Product ergonomics, examples, documentation, and focused smokes.",
  "current_claims": ["todo_abc123"],
  "handoff_assignment_status": "task_policy_selected"
}
```

Dashboards may render this projection. Writes still go through LoopX todo,
gate, lease, quota, reward, and refresh commands.

## Migration

The v0.1-to-peer migration treats `agent_profile_v0` as legacy input. After the
host updates its installed automation and acknowledges the stable migration id,
LoopX atomically:

1. removes goal-level leader and default handoff fields;
2. canonicalizes `registered_agents` to peer ids;
3. upgrades profiles to `agent_profile_v1`;
4. removes hierarchy roles plus identity-level workspace, review, and handoff
   policy;
5. records `completed_migrations.peer_agent_runtime_v1`.

The completion command is idempotent. Once the marker is written, quota does
not ask for that migration again.
