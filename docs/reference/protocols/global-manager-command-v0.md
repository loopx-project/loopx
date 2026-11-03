# global_manager_command_v0

`global_manager_command_v0` is a read-first protocol for operator commands
such as `/loopx-global-summary`, `/loopx-global-gates`,
`/loopx-global-todos`, and `/loopx-global-risks`.

The product goal is to let a user act as a manager across long-running agent
work: ask for the last day of progress, see blocked decisions, compare agent
lanes, and choose the next safe action without reading every thread.

The staged [manager evidence and continuity design](manager-evidence-and-continuity-v0.md)
extends this read-first boundary toward complete Goal coverage, scoped
cross-entrypoint continuity, and restart-safe reporting. Its proposed Goal
Portfolio provider is not an additional shipped CLI command. This protocol's
existing command behavior remains the baseline until each stage is qualified.

This protocol is not a general chat-command router yet. It defines the
request, allowed sources, response shape, privacy boundary, and action ladder
for Codex hosts, CLI wrappers, or dashboard command palettes. The four
implemented global-manager CLI wrappers are `loopx global-summary`, for the
broad compact `/loopx-global-summary` digest; `loopx global-gates`, for the
focused current-state gate inbox; `loopx global-todos`, for the focused
current-state work inbox; and `loopx global-risks`, for the focused
current-state risk inbox.

## Command Set

Recommended first commands:

| Command | User intent | Default source window |
| --- | --- | --- |
| `/loopx-global-summary <time range>` | Show progress, completed work, active lanes, and next decisions. | 24 hours |
| `/loopx-global-gates` | Show open user/controller gates and what each blocks. | current state |
| `/loopx-global-todos` | Show top runnable, blocked, deferred-ready, and review todos. | current state |
| `/loopx-global-risks` | Show stale runs, public/private boundary warnings, failing checks, and rollback candidates. | 24 hours |
| `/loopx-pr-review` | Walk the current project's or explicit repository's open and merged GitHub PRs one by one with motivation, scope, checks, risks, and review prompts. | current open + merged PRs, optionally bounded by `--since` |
| `/loop-goal-summary <goal id>` | Drill into one goal without scanning unrelated projects. | 24 hours |

Only `/loop-goal-summary` remains host-only under this protocol;
`/loopx-global-risks` uses the canonical `loopx global-risks` CLI wrapper.

Commands are read-only by default. They can propose follow-up actions, but
they do not approve gates, promote suggested todos, spend quota, merge PRs,
pause automations, or run destructive operations.

Legacy `/loop-global-*` forms may be accepted as aliases during migration, but
hosts should canonicalize command packets and user-facing help to the
`/loopx-global-*` names. These are host/slash aliases, not CLI command names:
unknown commands and legacy CLI aliases fail closed with help instead of
falling back to a broader status or summary dump.

| Legacy alias | Canonical command |
| --- | --- |
| `/loop-global-summary` | `/loopx-global-summary` |
| `/loop-global-gates` | `/loopx-global-gates` |
| `/loop-global-todos` | `/loopx-global-todos` |
| `/loop-global-risks` | `/loopx-global-risks` |

Related project-local command: `/loopx <goal text>` is covered by
[`loopx_goal_command_v0`](loopx-goal-command-v0.md). It is not a global manager
command: it starts one project goal, plans ranked todos, writes them in order,
and then enters the quota-gated automation flow.

Related repo-review command: `/loopx-pr-review` is covered by
[`pr_review_command_v0`](../../../loopx/capabilities/pr_review_queue/README.md). It is read-only and helps a
human review open and merged PRs in the caller's current project or an explicit
`--repo owner/repo` target; it does not approve, comment, merge, or spend quota.

## Request Shape

```json
{
  "schema_version": "global_manager_command_request_v0",
  "command": "/loopx-global-summary",
  "legacy_aliases": ["/loop-global-summary"],
  "time_range": "24h",
  "goal_filter": ["loopx-meta"],
  "agent_filter": ["codex-main-control", "codex-side-bypass"],
  "include": ["progress", "gates", "todos", "risks", "next_actions"],
  "privacy_mode": "public_safe_summary",
  "dry_run": true
}
```

Request rules:

- `privacy_mode` defaults to `public_safe_summary`.
- `goal_filter` and `agent_filter` narrow the read; omitted filters mean all
  registered goals or agents visible in the local control plane.
- For `loopx global-gates`, `--agent-id` excludes goals where the selected
  agent is not registered; an unavailable per-goal quota projection is not a
  substitute for agent filtering.
- For `loopx global-todos`, `--agent-id` is forwarded into each per-goal quota
  read and applied before the global limit. The successful quota packet must
  confirm an exact `agent_identity.agent_id` match; a default-lane packet or a
  failed quota read cannot satisfy the filter.
- For `loopx global-risks`, `--agent-id` uses exact compact-history goal
  membership. Global risks remain visible, and any unresolved candidate goal
  fails closed instead of being silently excluded.
- `dry_run=true` is the default because the first implementation should be a
  report, not an executor.
- Unknown commands must fail closed with a help packet, not a broad status
  dump.

## Source Reads

Implementations may read only compact LoopX control-plane surfaces:

- global registry and project-local registry entries;
- `loopx status` / status JSON;
- `loopx quota plan` and `quota should-run` summaries;
- active-state todo projections;
- run history summaries;
- rollout event log summaries;
- review packets for explicit goal drilldown.

`loopx global-todos`, `loopx global-summary`, and `loopx global-gates` each read
status exactly once and reuse that observation when collecting quota facts.
Status internally consumes compact run-history state to build its attention
queue. These manager builders do not fetch a second history projection or read
the authority store directly; source reads inside the quota owner retain their
existing semantics. Manager queries invoke the read-only quota builder, not a
Turn admission, claim, scheduler mutation or quota spend.

The per-goal quota-evaluation budget is:

- With an explicit `--agent-id`, at most one packet for that exact identity.
  The successful packet must confirm the requested identity; an unscoped or
  default-lane packet cannot satisfy the filter.
- Without an agent filter and without discovered agent candidates, one
  unscoped packet.
- Without an agent filter and with `A` distinct agent candidates, at most one
  unscoped packet plus one scoped packet per candidate. With a discoverable
  roster, collect its complete identities and existing lane recommendations;
  repeated identities or queue entries must not multiply reads.

This replaces the former one-packet-per-goal budget for unscoped goals with
agent candidates. The current implementation evaluates the scoped packets
sequentially; its quota-builder call count is `O(G + sum(A))` for `G` inspected
unique goals. Individual evaluation cost also depends on goal/history size.
The existing goal scan and result limits remain command-specific: a display
limit does not cap the roster or the complete counts within an inspected goal.
For `global-todos`, `goal_scan_limit` and `goal_scan_truncated` still describe
the earlier goal scan. Projection-envelope goal counts describe coverage, not
physical store reads or the number of scoped quota evaluations.

Keep each agent's eligibility with the quota owner. The manager may aggregate
its structured facts but must not infer eligibility from a title, a registry
entry or the first healthy lane. A failed scoped read cannot establish complete
coverage: `global-todos` retains healthy lanes with its redacted source warnings
and degraded-goal count, or omits the goal if no lane can be verified. Summary
and gate reads retain their own source-failure rules. Warning-list truncation
must not erase the full warning count. No result authorizes the suggested work.

The cost tradeoff was checked on the same synthetic nine-agent goal: runnable
work belongs only to agent nine, while agents one and two each have a distinct
user gate. Immutable base `f941b5939345c6d1c3d770a982182624e7ef393f`
used one quota evaluation per command; the complete collection at
`afb3b5c1787dad1293da1150d93da8c22afe729a` used ten, with one status call
in both. The base omitted the ninth runnable entry from `global-todos` and
showed only one gate; the complete collection retained the work and both gates.
An isolated one-quota shortcut using the current gate consumer again lost one
gate on File and SQLite. A future complete batch may reduce the read count;
truncating candidates or returning the first healthy lane is not an equivalent
optimization.

The following are instrumented CLI-entrypoint wall-time medians in seconds over
three invocations per cell on macOS, Python 3.14.8 and Node 24.21.0. The first todos invocation in each
isolated runtime was cold; later calls reused that runtime. These small,
contended samples explain the additional cost, not a latency limit or a speedup.

| Provider | Command | One-packet base | Complete collection |
| --- | --- | ---: | ---: |
| File | global-todos | 1.154 | 1.611 |
| File | global-summary | 0.344 | 1.513 |
| File | global-gates | 0.319 | 1.563 |
| SQLite | global-todos | 0.478 | 1.246 |
| SQLite | global-summary | 0.412 | 1.172 |
| SQLite | global-gates | 0.375 | 0.892 |

Across those cells, Python caller CPU medians were 0.170–0.187 seconds for the
base and 0.352–0.372 for complete collection; caller peak-RSS medians were
46.4–46.8 MiB and 47.1–49.2 MiB. Wall time includes service waits, but Node
CPU/RSS and fixture setup are outside those resource figures. Canonical Todo
records/provider revisions and source/registry bytes remained unchanged.
These five-Todo fixtures do not qualify large histories, hundred-agent scale,
installed-host adoption or long-horizon efficiency. The original budget failure
remains recorded; this revision changes the declared source budget, not a frozen
experiment, runtime deadline, transport size limit or execution allowance.

`loopx global-risks` reads status exactly once. It accepts structured contract
diagnostics, global registry findings, attention-queue stale-run warnings, and,
only for an explicit agent filter, compact run-history coordination. It performs
no quota fan-out and does not use the display-limited
`agent_management_projection` to decide goal membership.

They must not include raw transcripts, raw benchmark logs, raw connector
payloads, credentials, local absolute paths, or private source bodies.

## Response Shape

`global_manager_command_response_v0`:

```json
{
  "schema_version": "global_manager_command_response_v0",
  "request": {
    "command": "/loopx-global-summary",
    "time_range": "24h"
  },
  "generated_at": "2026-06-24T00:00:00Z",
  "summary": {
    "headline": "Three active goals advanced; one user decision is open.",
    "progress_count": 3,
    "open_gate_count": 1,
    "runnable_todo_count": 4,
    "risk_count": 2
  },
  "lanes": [
    {
      "goal_id": "loopx-meta",
      "agent_id": "codex-product-capability",
      "status": "eligible",
      "top_todo_id": "todo_example",
      "last_event_id": "event_example",
      "next_safe_action": "Review and merge the public-safe protocol PR."
    }
  ],
  "gates": [
    {
      "gate_id": "gate_example",
      "owner": "user",
      "blocks": ["todo_example"],
      "question": "Approve promoting the candidate todo?",
      "next_safe_action": "Wait for explicit approval."
    }
  ],
  "risks": [
    {
      "kind": "public_boundary_warning",
      "severity": "high",
      "evidence_refs": ["check_public_boundary"],
      "next_safe_action": "Run the public/private boundary scan before merge."
    }
  ],
  "actions": [
    {
      "action_id": "act_review_pr",
      "kind": "review",
      "requires_user_approval": false,
      "requires_executor_separation": true,
      "target_agent_id": "codex-reviewer",
      "preview": "Assign protocol review to the selected registered peer."
    }
  ],
  "omissions": [
    "Raw logs and private connector payloads were intentionally omitted."
  ]
}
```

Focused gate responses follow these relation rules:

- a formal user gate comes from the interaction contract or a formal open
  user-gate todo, not merely from the count of all open user todos;
- `blocks` uses the gate todo's `unblocks_todo_id` when present, then a verified
  decision-scope relation, and otherwise falls back to the goal scope rather
  than guessing from the selected runnable todo;
- `waiting_on=user_or_controller` is routing metadata, not a new gate-owner
  enum; gate owners continue to use the protocol's user, controller,
  registered-agent, or external-system owner classes.

### Focused Global Todos Response

`loopx global-todos` keeps `global_manager_command_response_v0`. Each retained
item in the flat `todos` list and its corresponding readiness and review groups
uses this compact public-safe shape:

```json
{
  "todo_id": "todo-123",
  "goal_id": "goal-456",
  "role": "agent",
  "status": "open",
  "priority": "P1",
  "title": "Review the quota projection",
  "claimed_by": "codex-reviewer",
  "action_kind": "review_pr",
  "readiness": "runnable",
  "work_kind": "review",
  "next_safe_action": "Continue the selected quota-authorized todo."
}
```

The focused response contains:

- canonical request metadata for `/loopx-global-todos`, its
  `/loop-global-todos` slash alias, and `loopx global-todos`;
- a flat `todos` list of unique `(goal_id, todo_id)` rows;
- `groups.runnable`, `groups.deferred_ready`, `groups.blocked`, and
  `groups.review`, derived from the retained flat list;
- `summary.matched_todo_count` before the global result limit and
  `summary.returned_todo_count` after it;
- full-match `runnable_count`, `deferred_ready_count`, `blocked_count`, and
  `review_count`, plus `truncated` when the result limit removed rows;
- `goal_scan_limit` and `goal_scan_truncated` for the earlier bounded goal scan,
  which is distinct from result truncation; and
- bounded, redacted `source_warnings`, while `source_warning_count` reports all
  warnings observed before that warning list is capped.

Readiness and work kind are orthogonal. Only readiness-classified items enter
the response: `runnable` requires the quota-selected todo and
`normal_delivery_allowed=true`; `blocked` requires an explicit blocked
projection or a formally verified todo-level gate relation; and
`deferred_ready` requires structured resume readiness. `work_kind=review`
requires an exact underscore-separated `review` or `reviewer` token in
`action_kind`, never title or text inference. Therefore, review counts overlap readiness counts;
do not add them to readiness counts to derive a total.

When projections disagree, the command fails closed to the precedence
`blocked`, then `deferred_ready`, then `runnable`, and emits a redacted source
warning. It never maps a goal-level gate fallback to a guessed todo ID. Agent
filtering, normalization, classification, and deduplication all happen before
the global limit.

Global and per-goal failures have different envelopes. If the global status
source is unhealthy, the command returns `ok=false` with a public-safe error
and exits non-zero; it must not look like a successful empty inbox. A known
out-of-scope agent identity remains a filter miss. If a goal's other initial
quota read raises or returns `ok=false`, the command skips that
unverifiable goal and records a bounded redacted warning. During scoped
collection, a failed agent read warns without hiding verified peers; if every
scoped read fails, the goal is omitted. The full source-warning count and
degraded-goal coverage remain visible, including when displayed warnings are
capped. Healthy goals remain available.

### Focused Global Risks Response

`loopx global-risks` keeps `global_manager_command_response_v0` and returns
`ok=true` when it successfully reports unhealthy state. In particular,
`status.ok=false` remains reportable risk data, while
`summary.source_health_ok` preserves source health separately from command
success. Both successful and error responses carry top-level `generated_at`.

The canonical request uses `/loopx-global-risks`, the `/loop-global-risks`
slash alias, `loopx global-risks`, a normalized positive `Nh` or `Nd`
`time_range`, the four risk includes, `privacy_mode=public_safe_summary`, and
`dry_run=true`. Each retained row has this occurrence-aware public-safe shape:

```json
{
  "goal_id": "goal-123",
  "category": "boundary_warning",
  "kind": "public_boundary_violation",
  "severity": "high",
  "summary": "A public boundary check failed.",
  "occurrence_id": "f42d9c9f6d497b35",
  "occurrence_count": 1,
  "evidence_refs": [
    "status.contract.error_diagnostics:public_boundary_violation:f42d9c9f6d497b35"
  ],
  "next_safe_action": "Inspect and remove the boundary violation before delivery.",
  "requires_user_approval": false
}
```

The response contains:

- the normalized request and top-level generation time;
- `summary.source_health_ok`, full-match and returned risk and occurrence
  counts, category counts, bounded-read and truncation facts, and warning count;
- the authoritative flat `risks` list;
- `groups.stale_runs`, `groups.boundary_warnings`, and
  `groups.failing_checks`, which partition the flat `risks` list;
- the overlapping `groups.rollback_candidates` facet, currently empty, plus
  `summary.rollback_candidates_overlap_risks=false`;
- a capped `source_warnings` list, its uncapped count, and
  `source_warnings_truncated`; and
- structured omissions and the standard public-safe boundary.

#### Structured risk sources and classification

| Output category | Accepted source | Rule |
| --- | --- | --- |
| `stale_run` | `attention_queue.items[].stale_latest_run_warning` | Require exact `kind=stale_latest_run_projection`; retain its structured reason and valid timestamps. |
| `boundary_warning` | `contract.error_diagnostics[]` | Accept exact codes `public_boundary_violation` and `registry_boundary_risk`. |
| `failing_check` | remaining `contract.error_diagnostics[]` | Preserve structured scope, exact goal ids, code, and a redacted message. |
| `failing_check` | `global_registry.findings[]` | Accept exact `severity=high` or `severity=action`; informational findings stay out of this focused inbox. |
| agent scope only | `run_history.goals[].coordination.registered_agents` | Verify exact candidate goal membership for an explicit `--agent-id`; this source never creates a risk row. |
| `rollback_candidates` | no current accepted source | Keep the facet empty and record the missing formal producer as an omission. |

Structured `code` values, not prose, determine classification. Contract
diagnostics map source `severity=error` to risk `severity=high`; other supported
severities keep their stable ordering. Contract diagnostics scoped to goals
expand into one row for each exact `goal_ids` member, while global rows have no
`goal_id` and affect the whole control plane.

Stable next actions also follow structured kind rather than source prose:

- `public_boundary_violation`: inspect and remove the violation before delivery;
- `registry_boundary_risk`: inspect and repair the registry boundary projection;
- other contract diagnostics: inspect and resolve the named contract check;
- registry findings: use the redacted structured recommendation or the stable
  inspect-and-resolve fallback; and
- `stale_latest_run_projection`: run `refresh-state` before trusting latest-run
  routing.

An occurrence identity is the first 16 lowercase hexadecimal characters of a
SHA-256 digest over a canonical JSON object containing only the public source
surface, original source-list index, structured kind, scope, and exact goal id.
It never hashes source prose, paths, credentials, or other text removed by
redaction. Aggregation merges only identical category, kind, goal id, and
occurrence id rows; it retains the highest severity and increments
`occurrence_count`. Distinct source positions therefore remain distinct even
when their redacted summaries match.

Ordering and reads are hard-bounded. The caller can receive at most 100 results,
and `source_scan_limit` caps inspection at 400 rows per accepted source.
`source_rows_truncated` and count-only warnings expose any bounded source scan;
matched row and occurrence counts are calculated before the result limit, and
groups derive only from the retained flat list. Source warnings are capped at
eight while their summary count remains uncapped.

The default `24h` request window is retained for protocol compatibility, but
the first accepted sources describe current state. An active stale-state
mismatch is never aged out by `time_range`, even when its valid latest-run
timestamp predates the requested window. Missing or invalid timestamps produce
a bounded warning and omit only that display field; they do not hide the active
risk or cause an age guess.

#### Exact agent scope and failure behavior

Without `--agent-id`, the command does not inspect coordination. With a filter,
it indexes at most `source_scan_limit` compact history goal rows by exact id and
reads only `run_history.goals[].coordination.registered_agents`. A global row
remains visible. A goal-scoped row is excluded only after its exact, well-formed
history row confirms the requested agent is absent; an empty registered-agent
list is a valid verified absence.

If a candidate has no exact inspected history row, lies beyond the scan bound,
or has malformed coordination or registered-agent data, the command must fail
closed with `agent_scope_unavailable`. It must not rescue or suppress the row
through quota health, a second history read, a per-goal read, or
`agent_management_projection`.

Command failure is narrower than unhealthy state. Status collection exceptions,
a non-object status payload, any missing or malformed required projection
container, and unverifiable explicit agent scope return `ok=false` with a
compact, redacted error and a non-zero CLI exit. Omitted empty source-list fields
are valid empty sources; the same field present with a non-list value is
malformed. A present, well-formed `global_registry` with
`global_registry.available=false` is a valid empty source and contributes one
bounded availability warning, not a command failure.

#### Rollback omission and authority

No current accepted source proves a rollback candidate. Boundary warnings and
failed checks are never guessed into that facet. Until a formal producer
supplies an allowed rollback trigger, affected durable scope, and causal
todo/event/commit/PR/external-resource linkage, the group remains empty and the
response records `rollback_candidate_source_unavailable`.

A global-risks response is a read-only report. It does not authorize rollback,
history rewrite, external cleanup, or merge. Any future candidate must still use
`rollback_packet_v0` and obtain every approval required for protected or
destructive action; this command never sets `requires_user_approval=true` on a
first-version risk row merely to imply that authority.

## Action Ladder

Responses may include actions, but each action must declare its authority:

| Action kind | Default authority |
| --- | --- |
| `read_more` | Agent may run another read-only compact command. |
| `review` | Use an ordinary claim or independent handoff; declare executor separation only when required. |
| `promote_todo` | Requires user/controller approval before `loopx todo add`. |
| `ask_user` | User-facing question; no delivery on blocked path until answered. |
| `pause_or_resume` | Requires explicit operator approval. |
| `merge_or_publish` | Requires repository policy, clean validation, and any explicit review or operator gate. |
| `rollback_or_history_rewrite` | Requires a `rollback_packet_v0` and explicit approval. |

The protocol should make it obvious when the user is being asked to decide,
when an explicit peer review is required, and when the current peer can safely continue.

## Privacy Boundary

Every response must include or imply these boundary facts:

```json
{
  "raw_logs_recorded": false,
  "raw_transcripts_recorded": false,
  "raw_connector_payloads_recorded": false,
  "credential_values_recorded": false,
  "absolute_paths_recorded": false,
  "private_source_bodies_recorded": false
}
```

If a useful summary needs private material, the command should return a gate
or omission, not the material itself.

## Acceptance Checks

A first implementation is acceptable when:

- command responses are read-only by default;
- `loopx global-summary`, `loopx global-gates`, `loopx global-todos`, and
  `loopx global-risks` emit their matching canonical command responses, while
  only goal summary stays host-only;
- each command names its compact LoopX source surfaces;
- gates name owner, formally related blocked todo or goal scope, question, and
  next safe action;
- global-risks source health stays separate from successful reporting, accepts
  omitted empty source lists and an unavailable global registry, and fails on
  malformed required projections;
- global-risks category groups partition its bounded occurrence-aware flat
  list, and current stale-state mismatches remain visible across time windows;
- global-risks agent filtering verifies exact compact-history membership and
  fails closed when any candidate goal cannot be verified;
- global-risks exposes no guessed rollback candidates and grants no rollback or
  other protected authority;
- agent filtering excludes goals where the selected agent is not registered;
- unknown commands and legacy CLI aliases fail closed with help;
- actions declare approval and ownership requirements;
- risks carry public-safe evidence refs;
- no raw logs, transcripts, credentials, local paths, or private source bodies
  are recorded;
- `python3 examples/project/global-manager-command-protocol-smoke.py` passes.
