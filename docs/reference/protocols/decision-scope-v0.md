# Decision Scope v0

Status: public-safe protocol contract for scoped user/controller decisions.

User gates are not global booleans. A user or controller decision should say
which authority is still needed, and an agent action should say which authority
it depends on. LoopX can then decide whether the selected action is blocked,
whether a safe fallback may continue, or whether the projection itself needs
repair.

This contract turns the interaction catalog's Decision Scope Model into a
machine-facing schema. The shared TypeScript decision-dependency owner evaluates
it for status/quota; Python adapters normalize inputs and validate responses.

## Fields

### `decision_scope`

Attached to a user todo, operator gate, or controller decision.

| Field | Required | Meaning |
| --- | --- | --- |
| `kind` | yes | `private_read`, `write_scope`, `resource`, `production`, `public_claim`, `direction`, or `other`. |
| `granularity` | yes | `action`, `lane`, `goal`, `project`, or `global`. |
| `scope_key` | yes | Public-safe key that names the blocked authority, path, lane, resource, or decision. |
| `decision_id` | no | Stable todo/gate/run id when the decision already exists. |
| `expires_at` | no | Optional ISO timestamp for temporary authority. |
| `reason_summary` | no | Public-safe one-line reason shown in status/UI. |

### `required_decision_scopes`

Attached to an agent todo, next action, handoff packet, or candidate runtime
action. Each item uses the same `kind`, `granularity`, and `scope_key` fields
as `decision_scope`.

An action is covered by a gate when at least one unresolved decision scope
matches or dominates one of its required scopes. Dominance is intentionally
small in v0:

- same `kind` and same `scope_key`;
- same `kind` and broader `granularity` over the same goal/project boundary;
- explicit `scope_key="*"` only when the owner/controller recorded it.

If the relation is ambiguous, status/quota must repair projection or ask the
user/controller; it must not infer permission from prose.

### Markdown metadata compact form

Todo metadata stores decision scopes as a compact public-safe token instead of
inline JSON:

```md
<!-- loopx:todo decision_scope=direction:action:benchmark_target_choice -->
<!-- loopx:todo required_decision_scopes=direction:action:benchmark_target_choice -->
```

The token is `kind:granularity:scope_key`. `decision_scope` is singular on a
user gate; `required_decision_scopes` may contain a comma-separated list on an
agent todo. Status/quota normalize those tokens back into
`decision_scope_v0` objects before evaluating gate coverage.

### `safety_class`

Attached to agent work candidates and selected actions.

| Value | Meaning |
| --- | --- |
| `read_only` | May inspect public/local allowed state without mutation. |
| `local_write` | Mutates repository or LoopX state within the current write boundary. |
| `external_run` | Launches or advances external compute, benchmark, CI, or hosted runtime work. |
| `protected_write` | Writes protected state, production systems, private materials, public submissions, or external authority surfaces. |

`safety_class` does not grant permission. It lets LoopX choose the correct gate
comparison and notification behavior.

## Minimal Shape

```json
{
  "schema_version": "decision_scope_v0",
  "user_todo": {
    "todo_id": "todo_user_123",
    "decision_scope": {
      "kind": "private_read",
      "granularity": "project",
      "scope_key": "private_authority_source",
      "reason_summary": "owner must approve reading private source material"
    }
  },
  "agent_todo": {
    "todo_id": "todo_agent_123",
    "required_decision_scopes": [
      {
        "kind": "private_read",
        "granularity": "project",
        "scope_key": "private_authority_source"
      }
    ],
    "required_write_scopes": ["docs/**"],
    "safety_class": "read_only"
  },
  "scope_relation": {
    "state": "gate_covers_action",
    "fallback_available": true,
    "user_channel": "notify_concrete_gate",
    "agent_channel": "execute_independent_fallback"
  }
}
```

## Status And Quota Rules

### Typed transport boundary

The internal `todo.decision_scope.evaluate` transport retains `consistency`,
`standing`, `relation`, `relations`, `fallback`, and `gate_scopes`. The unused
scalar operations `covers`, `scope_relation`, and `exact_relation`, and their
Python adapters, have been removed; those operation requests fail explicitly.
The underlying TypeScript coverage and exact-target rules remain part of the
combined evaluation, including rejection of conflicting exact targets.

This retirement changes an internal transport surface, not Todo metadata,
user-facing CLI operations, provider defaults, permissions, or approval
consumption. Existing input codecs and response schema/cardinality checks remain.

Status and quota should read decision scopes in this order:

1. explicit `decision_scope`, `required_decision_scopes`, and `safety_class`;
2. structured todo fields such as `task_class`, `required_write_scopes`, and
   action kind;
3. compatibility inference from legacy title/body text;
4. projection repair when no confident relation exists.

Markdown text inference is a lint, not gate truth. A legacy `Next Action`
regex may detect suspicious prose and create a projection-gap warning, but it
must not override an explicit `interaction_contract`, structured todo fields,
or an open runnable agent todo.

LLM-assisted interpretation belongs only in cold-path authoring helpers or
repair proposals. It may suggest a structured decision scope, but it must not
decide delivery gates, spend policy, write permission, or safe fallback at
runtime.

## Approval Consumption Lifecycle

`loopx todo complete` resolves authority only for an explicitly linked
`user_gate`:

1. the completed todo has `task_class=user_gate`, a normalized
   `decision_scope`, and `unblocks_todo_id=<target>`;
2. the target is an agent todo whose `required_decision_scopes` contain scopes
   covered by that gate;
3. completion removes only the covered requirements and preserves every
   uncovered scope;
4. the transition returns a public-safe `todo_decision_scope_resolution_v0`
   receipt with resolved and remaining scopes.

This consumption also applies when the target todo is already `open`, such as
publication performed immediately after approval. A completed `user_action`
may still use the exact unblock relation for compatibility, but it does not
consume decision authority. `todo supersede` records replacement;
it never implies approval and therefore never consumes a required scope.

Completion consumes requirements only on active, nonterminal Agent targets.
A blocked target resumes only when no active linked User Todo, unresolved
requirement or recorded negative outcome remains; explicit blockers still need
repair. Completed/deferred/archived targets are unchanged. File, SQLite and the
service-owned PostgreSQL transaction commit these effects with the source
completion. See [linked effects and historical recovery](../canonical-todo-completion-update.md#linked-user-completion-effects).

## Standing Approval Receipts

Some owner decisions are operating policies rather than one-action gates.
LoopX projects such a decision as `standing_decision_authority_v0` only when
all of these conditions hold:

- the source item explicitly has `role=user`, `task_class=user_gate` and completed status;
- it carries a normalized `decision_scope` and an explicit
  `decision_outcome=approve|reject|cancel`;
- its granularity is `goal`, `project`, or `global`;
- it has explicit `blocks_agent` or `global_gate=true` ownership; and
- it has no `unblocks_todo_id`, which remains the one-action consumption path.

The latest receipt for the exact scope and owner identity wins. `approve`
activates it; a later `reject` or `cancel` revokes it. The shared typed owner
`todos/standing_decision.ts` defines both read eligibility and archive retention.
Compaction keeps all eligible standing receipts, including revocations. Reads
also consider retained archived receipts with explicit user role: moving a
decision to history must not reactivate an earlier approval. Reads do not move
records back into the active display or repair storage.

### Decision chronology, not display order

`completed_at` identifies decision time; `updated_at` is a compatibility fallback
only if completion time is absent. Comparison uses parsed instants with
microsecond precision, not timestamp strings. Editing an old receipt's note must
not outrank a later revocation when completion time is present.

For an all-undated legacy group, the existing source-order convention remains.
Captured legacy records may use unique persisted indexes from the same source
section. Native records must never use Todo ID, array position, or synthesized
display indexes as chronology. Positions across active and archive sections are
not comparable. Missing, invalid, mixed or tied chronology with contradictory
outcomes yields `conflicts` and `conflict_count` on `standing_decision_authority_v0`,
with `reason_code=standing_decision_order_unresolved`, scope, owner and source Todo
IDs. That group supplies no active receipt; no rejection is fabricated and no
history is erased. Identical outcomes need no ordering decision. Agent filtering
preserves applicable conflicts even when `entries` is empty.
Required-scope consistency exposes the same conflict code and source IDs rather
than reporting a missing gate. Its repair hint requires reconciling explicit
owner evidence, not deleting required scopes or inventing approval to clear the
diagnostic. This does not change open-gate or explicit terminal-outcome precedence.

These are intentional corrections, not full parity: list-order approval
resurrection is removed, ambiguous contradictory canonical history fails closed,
and prose/action-kind heuristics no longer establish standing authority. Malformed
scope or exact-link metadata is not silently upgraded into broad permission.
Resolve conflict through explicit owner-confirmed decision history with valid
chronology; never edit the display to override canonical authority. Other scopes,
other owners and open-gate precedence retain their existing rules.

A standing receipt does not make work implicitly privileged. The selected
agent todo must still declare a covered `required_decision_scope`; quota
filters receipts to the current agent lane before required-scope consistency
is evaluated. A newer open gate may still block the exact work through normal
gate routing. Chat prose, completed `user_action` items, inferred intent, and
unscoped multi-agent decisions never grant standing authority.

## Migration Phases

1. **Contract only:** document this schema and keep current behavior unchanged.
2. **State authoring:** teach todo/gate write paths to accept and preserve
   `decision_scope` and `required_decision_scopes`. `safety_class` remains a
   later authoring field.
3. **Projection:** surface the fields in status, quota, review packets, and
   frontstage local ops mode.
4. **Hot path:** status/quota fallback uses structured scope relations, then
   exact legacy action labels; title/body word overlap is not dependency authority.
5. **Lint fallback:** keep regex and optional LLM proposals as projection-gap
   repair helpers, not runtime authority.

## Failure Semantics

### Shared read-policy owner

`control_plane/todos/decision_scope.ts` evaluates coverage, exact-target relations
and consistency from decoded source facts. `global_gate=true` addresses all
agents; otherwise an explicit `blocks_agent` determines the recipient even when
`claimed_by` names another agent. Claim attribution is not a veto on that explicit
recipient. With neither explicit field, the existing claim-scoped compatibility
rule remains; multi-agent gates without explicit scope still require repair.

A gate whose scope covers work A but whose `unblocks_todo_id` names work B produces
`required_decision_scope_target_mismatch`. Another matching gate does not erase
the contradictory record. Repair must reconcile owner intent; it cannot silently
retarget the gate, remove the requirement or synthesize approval. This deliberately
replaces a formerly false `consistent` diagnostic. A consistent open dependency
still means waiting for a decision, not permission to execute.

Legacy metadata codecs and operator repair copy remain in Python. Candidate-pair
consumers use batched relations. No provider commit, lease, source promotion or
Markdown writeback authority is added by this read-only contract.

### Scoped fallback selection

The same typed owner now selects fallback candidates, rather than letting Python
reinterpret a relation matrix with token-overlap heuristics. It applies these
rules in order:

1. Explicit `global_gate=true` still blocks fallback for every addressed lane.
2. Exact Todo links and decision scopes retain their existing precedence,
   including contradictory-target diagnostics. Explicit independence is not
   overridden by matching action labels.
3. Only when neither relation exists, two nonempty legacy `action_kind` values
   are compared as complete trimmed, case-normalized keys. Equal keys retain
   blocking compatibility; distinct keys do **not** prove independence (for
   example, `approve_release` versus `release`). This is not a new permission
   scope or an approval receipt. Use explicit dependencies when authoring gates.
4. Distinct or missing keys do not prove independence: that candidate cannot be advertised
   as safe fallback. This does not rewrite the gate or promote it to global scope.

Intentional correction: word overlap no longer claims a known dependency,
and disjoint words no longer claim safe independence. English and Chinese task
prose cannot alter the relation. Without structural scope, both remain unproven
for safe fallback; adding an explicit exact link or decision scope makes the
intended boundary readable without teaching the Agent a naming convention.
This may withhold previously offered legacy fallback until its scope is
clarified. It never removes, retargets or automatically broadens a User gate.

Selection rejects completed, archived, blocked, removed-continuation and
actor-ineligible candidate rows. A ready deferred row retains its separate replan
meaning, not permission to execute deferred work. Priority and persisted index
retain precedence; Monitor debt only prefers advancement at equal priority, and
source position breaks remaining ties. Deduplication retains the first source
identity. An authoritative empty capability result is never refilled from backlog;
due Monitor and evaluated deferred lanes keep their separate existing contracts.
All supplied candidates are considered before the three-item diagnostic limit.
The adapter returns existing compact display rows, without another provider read,
wait evaluation, Todo mutation, permission grant or notification-policy change.

The inputs are evaluated quota lanes, not a claim that every compact-summary
consumer now reads the complete inventory. Missing/stale display is handled by
the existing canonical source adapter; permanent Markdown remains a projection.

- Missing structured fields on legacy state: fall back to compatibility lint
  and emit a projection-gap repair hint.
- Conflicting structured fields: fail closed with a concrete blocker.
- User todo requires action but has no concrete payload: report
  `具体 user todo 未投影，需修复 LoopX 状态投影`.
- Action claims no gate but requires protected write: block and repair scope.
- Safe fallback exists outside the gate scope: notify the concrete gate, run
  the independent fallback, validate, write back, and spend once.

## Acceptance Checks

A decision-scope implementation is acceptable when:

1. structured fields can be authored without hand-editing Markdown;
2. status and quota expose the computed scope relation;
3. explicit fields outrank title/body regex inference;
4. ambiguous scope fails closed instead of guessing;
5. safe fallback continues only when its required scopes are independent; and
6. completing an exactly linked user gate consumes only its covered required
   scopes while superseding it consumes none;
7. a broad completed user gate becomes reusable only through an explicit,
   agent-compatible standing receipt, and later reject/cancel revokes it;
8. compacting completed todos does not erase active standing authority; and
9. legacy regex/LLM assistance remains a cold-path repair signal, not runtime
   gate truth.
