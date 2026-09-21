# Status Data Contract

`loopx --format json status` is the stable first-screen data contract for
agents, heartbeat jobs, dashboards, and local UI experiments.

The command is an export: it reads the registry, compact run indexes, and the
public/private contract check, then emits one JSON object. It does not inspect
private run payloads beyond compact index fields and does not mutate files.
The export is agent-facing machine state. A dashboard should consume this
contract and translate it into a human operator view rather than treating raw
CLI fields as product copy.
In particular, first-screen dashboards should translate raw machine fields such
as `single_surface`, `focus_wait`, `quota_slot_spent`, or concurrency snippets
into the operator's language before presenting them as primary copy. The
machine tokens may remain in drill-down views, logs, or packets where exact
debuggability matters.

When a command is run outside a project-local `.loopx/registry.json`,
the CLI falls back to the shared local global registry at
`~/.codex/loopx/registry.global.json` if it exists. That registry is
maintained automatically by `connect` and `refresh-state` so each project agent
can update its own local state while dashboards still see the multi-project
view.

For local dashboards, the same JSON shape can be served over loopback HTTP:

```bash
loopx serve-status --global-registry --port 8766 --limit 80
```

The default endpoint in that example is `http://127.0.0.1:8766/status.json`.
`--global-registry` is the canonical multi-project dashboard mode: it serves
the shared global registry even when launched from inside a project checkout,
avoiding a project-local registry scope warning. For project-local debugging,
omit `--global-registry` or pass the project registry explicitly. The server is
intended for local dashboard development and includes CORS headers so a Vite app
on another localhost port can fetch it.
The disposable `loopx demo` path intentionally uses a project-local
server on `127.0.0.1:8765`; it does not sync the temporary demo into the shared
global registry.

Each project-backed `attention_queue.items[]` row may include a read-only
`goal_channel_projection` object with
`schema_version=goal_channel_projection_v0`. This is the dashboard/frontstage
projection of the same status item: current decision frame, user and agent
todos, quota, soft claims, compact run events, source warnings, and an explicit
truth contract. It is not a write API. Dashboards may render it as a channel
card or timeline, but project truth still comes from the registry, active
state, quota guard, and append-only run history.

Rows may also include an optional `task_graph_projection` object with
`schema_version=task_graph_projection_v0`. The default `status` hot path omits
this graph to preserve the dashboard interface budget; callers that need the
graph can request it with `loopx --format json status --include-task-graph`, or
use a full review packet. This is a compact graph-shaped view over existing
todos, gates, leases, run ids, and event-ledger state. It is read-only and
exists only to show dependency, validation, repair, audit, continuation, and
handoff relationships that are hard to scan in a flat todo list. It must not
introduce a second scheduler, graph write API, hidden lease store, or alternate
task truth.
The protocol is defined in
[`docs/reference/protocols/task-graph-projection-v0.md`](reference/protocols/task-graph-projection-v0.md);
consumers should ignore the field when absent.

Rows may also include an optional `openviking_session_memory_adapter` object
with `schema_version=openviking_session_memory_adapter_v0`. This is a
public-safe session-runtime specialization for OpenViking-style issue memory:
compact issue refs, session refs, memory refs, retrieval gates, status
projection, and evidence projection. It is read-only and must not perform live
OpenViking retrieval, write memory, read issue/comment bodies, ingest raw tool
outputs or trajectories, or publish external issue comments/PRs. The protocol
is defined in
[`docs/reference/protocols/openviking-session-memory-adapter-v0.md`](reference/protocols/openviking-session-memory-adapter-v0.md);
consumers should ignore the field when absent.

Rows may also include an optional `local_agent_launch_plan` object with
`schema_version=local_agent_launch_plan_v1`. This is a dry-run preview over
configured agents, role assignments, non-executable launch preview rows,
status projection, evidence projection, and future gates. It is read-only and
must not start local workers, call external agent services, expose shell
commands, write LoopX state, or grant host authority. The protocol is defined
in
[`docs/reference/protocols/local-agent-launch-plan-v1.md`](reference/protocols/local-agent-launch-plan-v1.md);
consumers should ignore the field when absent.

Loopback status exports include `status_contract.schema_version`. The dashboard
uses that small protocol marker to detect when `127.0.0.1:8766` is still served
by an older daemon or release snapshot after the checkout has moved forward. If
the field is absent or below the dashboard's expected version, the product UI
should warn the operator and point to the safe reload path:
`scripts/macos-dashboard-launchagent.sh restart`.

The same local server exposes `POST /reward/dry-run` for dashboard reward
validation. It accepts the selected `goal_id`, `run_generated_at`, compact
reward fields, and public-safe summary text, then returns a compact validation
result with `appended=false`. It also returns the same coordination fields as
the CLI: `active_state_summary` and `project_agent_visibility`. It does not
mutate the run index and does not return private artifact paths.

When status is served over loopback HTTP, `/status.json` also includes an
optional `local_dashboard_api` capability block. CLI status exports may omit it
because they are plain read-only snapshots. The block is the dashboard's machine
contract for local write affordances:

```json
{
  "local_dashboard_api": {
    "source": "serve-status",
    "reward_dry_run_url": "/reward/dry-run",
    "reward_append_url": null,
    "reward_write_enabled": false,
    "configure_goal_dry_run_url": "/control-plane/configure-goal/dry-run",
    "configure_goal_apply_url": null,
    "control_plane_write_enabled": false
  }
}
```

The deprecated dashboard diagnostics route uses a TanStack Query-backed local
status reader at
`/deprecated/frontstage/ops?statusUrl=<relative-or-loopback>`. The old
`/frontstage?mode=ops` form redirects to that namespace. Showcase mode still
ignores `statusUrl`; only the explicit deprecated diagnostics route may fetch a
status feed.
The query layer validates `status_contract.schema_version` before trusting a
loopback feed. If the feed is below the dashboard's expected schema version,
the route shows stale-daemon repair copy using `status_contract.reload_hint`
instead of silently rendering an old protocol.

The same frontstage query layer projects `local_dashboard_api` as capabilities,
not browser authority. It may show that reward dry-run or control-plane dry-run
URLs are advertised, but write affordances stay disabled unless the feed is
relative or loopback, the matching URL is present, and the corresponding
`*_write_enabled` flag is true. The frontstage must remain read-only by default;
any future write UI must still use preview-locked local APIs and keep CLI/event
ledger state as the source of truth.

The control-plane settings write path follows the same opt-in rule as reward
append. By default `serve-status` validates dashboard setting drafts through
`POST /control-plane/configure-goal/dry-run` but does not expose an apply
capability. Starting the loopback server with
`--enable-control-plane-write-api` exposes
`POST /control-plane/configure-goal/apply`; the dashboard should enable Apply
only when `local_dashboard_api.control_plane_write_enabled=true` and the apply
URL is present. Apply requests must reuse the fresh `preview_id` from the
dry-run response.

Control-plane setting drafts may use `multi_subagent_feature="enabled"` to opt
into bounded child-agent orchestration, or `"off"` to keep the default
single-agent mode. This is a product wrapper over the registry `spawn_policy`;
dashboard surfaces should prefer it over exposing raw `orchestration_mode` plus
`spawn_allowed` toggles.

## Command

```bash
loopx --format json status > goal-status.json
```

By default, `status` scans the LoopX install root for public/private
contract health. Use narrow scan paths only when you intentionally want the
status export to check a specific public-safe project surface:

```bash
loopx --format json status \
  --scan-path README.md \
  --scan-path docs/ \
  --scan-path examples/
```

For compute allocation, `loopx quota status` and
`loopx quota plan` derive an agent-facing grouping from this same status
payload. `loopx --registry "$HOME/.codex/loopx/registry.global.json"
quota should-run --goal-id <goal-id>` derives a per-goal automation guard from
that grouping for project heartbeats. These are read-only views, not a separate
source of truth. Scripts should treat `summary.next_automatic_turn` in the
quota-plan JSON as advisory and still respect the displayed health, operator,
and evidence gates.
The per-goal guard also emits `heartbeat_recommendation`, a compact executor
hint for generic lifecycle cases such as first read-only map runs and quiet
mapped no-ops. Project-specific policy should still come from the registry,
active state, adapter output, or boundary rules rather than ad hoc scheduler
prompt branches.
Registry entries may also declare compact `control_plane` settings. These
settings are per-goal policy, not global prompt text. By default
`control_plane.self_repair.enabled=false`; only registry-enabled goals can turn
health or waiting-projection stalls into a `quota should-run` self-repair
machine contract.
When the selected attention item is project-asset backed, the per-goal guard
also carries compact `handoff_readiness` with `handoff_status` and
`post_handoff_run_seen`. Heartbeat jobs can therefore tell whether the selected
goal is still waiting for a target run or has already seen post-handoff work
without parsing the full status payload.
For replan, the guard carries a host-built `replan_context_v0` and the compact
`replan_action_packet_v0`. The context projects a bounded coverage ledger from
the agent-scoped evidence history, an uncovered frontier, and a delivery
receipt. The acting model therefore chooses a direction from delivered context;
it does not have to discover and execute an evidence-log command as a protocol
preflight. `loopx evidence-log --goal-id <goal-id> --agent-id <agent-id> --thin`
remains the cold-path diagnostic chronology, and its read receipt remains useful
for observability, but a read or legacy ACK cannot close a replan obligation.
Closure requires a typed semantic delta accepted against the current obligation:
a new surface, hypothesis, probe family, state-grounded runnable successor,
fresh evidence-linked vision path, concrete new blocker, or coverage-backed
terminal result. A runnable successor is recorded by one `todo add` carrying
the exact `replan_obligation_id`; the Todo write is the receipt and returns the
fine-grained turn boundary, so no second ACK command is required. The same
goal-frontier reducer is used by quota and
`refresh-state`, so a vision/frontier-derived obligation cannot be bypassed by a
maintenance classification or an ACK for an older obligation.

For a turn-scoped executable settlement,
`interaction_contract.cli_channel.replan_settlement_contract` keeps that
semantic obligation separate from the Turn's causal settlement identity. It
projects exactly one `settlement_binding`. When the quota receipt already owns
a selected Todo, refresh and spend use only `--todo-id`; the replan obligation
remains the semantic target discharged by that Todo-bound writeback and must
not be added as a second settlement flag. A Todo-less replan instead binds the
obligation directly with `--replan-obligation-id`. Both paths retain the same
typed semantic-delta validation and ordered refresh-then-spend chain. Reads
without a turn identity stay compact and do not project an executable
settlement contract or spend action.

The same guard may include `work_lane_contract`. Schema
`work_lane_contract_v1` is the compatibility drill-down for monitor versus
advancement routing under the guard's first-class `interaction_contract`. It
distinguishes `lane=continuous_monitor` from `lane=advancement_task`, carries
the next lane, and exposes one `obligation` string such as
`advance_unless_material_monitor_transition`. Agent todo items
may include `task_class=advancement_task` or
`task_class=continuous_monitor`, plus optional `action_kind` such as
`run_eval`, `validate`, `rebuild`, `writeback`, `monitor`, or `poll`. Explicit
`task_class` is authoritative for the todo item itself; recognized generic
`action_kind` can infer the lane when `task_class` is absent; legacy todo text
is only a compatibility fallback. The selected goal's
`next_action`/`recommended_action` can still promote an otherwise monitor-only
todo set back to `lane=advancement_task` when it names an executable chain such
as collecting repeats, rebuilding labels, rerunning a scorer, or validating an
eval gate. Hidden open todos are treated as advancement work rather than as
monitor-only work, so a truncated top-N todo projection cannot accidentally
silence an executable backlog.
For a dependency-observation projection with open agent todos, the guard sets
`next_lane=advancement_task`, `must_attempt_work=true`, and
`reason_codes=["dependency_observation", "open_agent_todo"]` only when at least
one open todo is advancement-class work. If all visible open todos are
monitor-class and no open todo is hidden, the guard sets
`obligation=quiet_until_material_monitor_transition` and
`must_attempt_work=false`. One narrow exception prevents long autonomous
projects from stalling after finishing their last visible delivery todo: when
the current `next_action`/`recommended_action` explicitly points at an
advancement-class executable chain, monitor-only todos are promoted to
`lane=advancement_task` with
`obligation=materialize_advancement_todo_or_blocker`. That obligation requires
the worker to create a concrete advancement todo or write a blocker instead of
quietly waiting on monitors. The heartbeat recommendation may say
`follow_work_lane_contract` or `monitor_quiet_until_material_transition`, but it
should not restate the lane semantics; unchanged monitor polls remain quiet
no-spend checks with `should_run=false` and
`effective_action=monitor_quiet_skip`, while a material dependency-state
transition may be written back once when it changes the selected goal decision.
When final agent-scope projection selects a non-execution wait such as
`effective_action=agent_scope_wait`, the exposed `work_lane_contract` must also
be non-executing (`must_attempt_work=false`) even if a goal-level next action
would otherwise derive an advancement obligation. The original goal-level lane
may remain as compact deferred diagnostic context, but executors must not treat
it as a competing obligation.
`handoff_readiness.handoff_interface_budget` declares the machine-readable
budget for the minimal project-agent handoff: `mode=project_agent_handoff`,
`max_lines=16`, and `max_chars=1800`. `loopx review-packet
--handoff-only --format json` returns the same contract plus live `line_count`,
`char_count`, and `within_budget`, so a short heartbeat can reject handoff
bloat without carrying this rule in prompt text.
For spend accounting, status derives `spent_slots` from compact
`quota_slot_spent` runtime events in the current quota window. The registry
remains the policy source for compute share and window size, not the spend
ledger.
`quota_slot_spent` is status-neutral accounting: it must remain visible in run
history for quota audit, but status and attention queues should use the latest
non-accounting run as the current work state.
More generally, status should expose projections over the append-only event
ledger rather than acting as the ledger itself. Work classifications, human
reward overlays, operator-gate resume contracts, quota spend rows, evidence
polls, blocker writebacks, and artifact validations should remain auditable as
events; dashboards and heartbeat prompts consume compact projections of those
events for current-state decisions.
In lane terms, `next_automatic_turn` may only name the first eligible goal;
operator-gated, focus-waiting, waiting, throttled, paused, and health-blocked
goals must stay out of the eligible lane even when they have a high
`quota.compute`.

## Top-Level Shape

```json
{
  "ok": true,
  "registry": ".loopx/registry.json",
  "runtime_root": "./runtime",
  "goal_count": 3,
  "run_count": 2,
  "goal_filter": null,
  "status_contract": {
    "schema_version": 2,
    "minimum_dashboard_schema_version": 2,
    "producer": "loopx status",
    "reload_hint": "scripts/macos-dashboard-launchagent.sh restart"
  },
  "contract": {
    "ok": true,
    "summary": {
      "errors": 0,
      "warnings": 0,
      "checks": 4
    },
    "errors": [],
    "warnings": [],
    "checks": [
      "registry goals checked: 3",
      "runtime indexes checked: 3",
      "run-history goals=3 runs=2",
      "public boundary scan clean: 12 files"
    ]
  },
  "global_registry": {
    "available": true,
    "ok": true,
    "registry": "~/.codex/loopx/registry.global.json",
    "current_registry": ".loopx/registry.json",
    "current_registry_is_global": false,
    "global_goal_count": 4,
    "current_goal_count": 3,
    "source_registry_count": 2,
    "summary": {
      "high": 0,
      "action": 0,
      "info": 1,
      "checks": 2,
      "findings": 1
    },
    "findings": [],
    "checks": []
  },
  "attention_queue": {
    "available": true,
    "item_count": 2,
    "needs_user_or_controller": 1,
    "needs_controller": 0,
    "needs_codex": 1,
    "watching_external_evidence": 0,
    "watching_monitor": 0,
    "autonomous_backlog_candidates": {
      "source": "attention_queue.agent_todos",
      "open_count": 1,
      "task_class": "advancement_task",
      "items": [
        {
          "goal_id": "loopx-meta",
          "quota_state": "eligible",
          "priority": "P1",
          "todo_index": 1,
          "task_class": "advancement_task",
          "text": "Add an autonomous backlog candidate surface for eligible goals.",
          "source": "agent_todos"
        }
      ]
    },
    "items": []
  },
  "run_history": {
    "available": true,
    "goal_count": 3,
    "run_count": 2,
    "goals": [],
    "recent_runs": []
  },
  "event_ledger_summary": {
    "available": true,
    "source": "run_history",
    "sample_run_count": 2,
    "proxy_note": "append-only run-history projection; compact event-class counts only",
    "event_classes": ["accounting", "decision", "evidence", "state", "work"],
    "totals": {
      "events_24h": 2,
      "events_7d": 2,
      "by_class_24h": {
        "accounting": 1,
        "decision": 0,
        "evidence": 0,
        "state": 1,
        "work": 0
      },
      "by_class_7d": {
        "accounting": 1,
        "decision": 0,
        "evidence": 0,
        "state": 1,
        "work": 0
      }
    },
    "goals": []
  },
  "promotion_readiness_summary": {
    "available": true,
    "source": "runtime_release_ledger",
    "evidence_scope": "runtime_release",
    "goal_id": null,
    "dashboard_readiness": "passed",
    "generated_at": "2026-06-01T00:08:00+00:00",
    "classification": "canary_promotion_readiness_smoke_group",
    "delivery_batch_scale": "multi_surface",
    "delivery_outcome": "primary_goal_outcome",
    "json_exists": true,
    "markdown_exists": true,
    "freshness_window_hours": 24,
    "freshness_status": "fresh",
    "is_fresh": true,
    "requires_readiness_run": false,
    "age_seconds": 120,
    "age_hours": 0.03,
    "sample_run_count": 1,
    "proxy_note": "canary promotion-readiness projection from the runtime release ledger with legacy goal-history fallback; exact evidence stays in append-only artifacts"
  },
  "promotion_gate": {
    "ok": true,
    "gate": "promotion_readiness",
    "gate_state": "ready",
    "can_promote": true,
    "should_warn": false,
    "non_blocking": true,
    "recommended_action": "promotion readiness is fresh",
    "readiness": {
      "freshness_status": "fresh",
      "requires_readiness_run": false
    }
  },
  "decision_freshness_summary": {
    "available": true,
    "source": "run_history",
    "sample_run_count": 2,
    "window_days": 7,
    "proxy_note": "checkpointed decision freshness projection; rebase old decisions at the decision point before reuse",
    "summary": {
      "decision_count": 1,
      "stale_count": 0,
      "rebase_required_count": 1,
      "fresh_count": 0
    },
    "items": [
      {
        "goal_id": "loopx-meta",
        "decision_kind": "operator_gate",
        "decision_at": "2026-06-01T00:05:00+00:00",
        "classification": "operator_gate_approved",
        "age_days": 0.2,
        "stale_by_age": false,
        "newer_event_count_7d": 1,
        "newer_event_classes_7d": {
          "accounting": 1,
          "decision": 0,
          "evidence": 0,
          "state": 0,
          "work": 0
        },
        "freshness_state": "rebase_required",
        "requires_decision_point_rebase": true,
        "reason": "newer sampled events exist after decision; rebase at decision point"
      }
    ]
  },
  "usage_summary": {
    "available": true,
    "source": "run_history",
    "sample_run_count": 2,
    "proxy_note": "run-history proxy; carries aggregate token/cost/duration, excludes raw thread logs",
    "totals": {
      "runs_24h": 2,
      "runs_7d": 2,
      "quota_spend_slots_24h": 1,
      "quota_spend_slots_7d": 1,
      "automation_run_count_24h": 1,
      "automation_run_count_7d": 1,
      "progress_signal_run_count_24h": 1,
      "progress_signal_run_count_7d": 1
    },
    "goals": []
  }
}
```

By default `loopx status` is the multi-goal dashboard/control-plane view.
`loopx status --goal-id <goal-id>` keeps global health fields such as
`global_registry`, while its `contract` projection contains only global errors
plus errors owned by the selected goal. It also focuses goal-scoped sections
such as `attention_queue`, `run_history`, `event_ledger_summary`,
`usage_summary`, and `todo_index` on the requested goal. Use
`loopx diagnose --goal-id <goal-id>` when an agent needs the richer reasoning
packet for one goal.

Consumers should treat unknown fields as additive. Required fields for a
first-screen UI are `ok`, `contract`, and `attention_queue`.
`status_contract`, `event_ledger_summary`, `promotion_readiness_summary`,
`promotion_gate`, `decision_freshness_summary`, and `usage_summary` are optional and should be
treated as compact protocol or run-history projections, not as the ledger
itself, authoritative billing telemetry, or a release operation source.
`usage_summary` carries aggregate token/cost/duration when runs report a typed
`run_usage_v0` usage row, but it remains a run-history proxy, not billing of
record. Malformed, negative, or non-finite usage fails closed at ingest/read rather than
appearing as zeros. A missing `status_contract` means an older status producer;
loopback dashboards should surface that as a daemon freshness warning rather
than silently hiding newer panels.

## Interface Budget Cadence

When a run-history record includes `interface_budget_cadence`,
`loopx status` projects a compact copy under
`attention_queue.items[].project_asset.interface_budget_cadence`. For the
selected goal, `quota should-run` also mirrors the same object at top level as
`interface_budget_cadence`.

This field is a restraint signal for heartbeat workers, not a dashboard feature
request. It records the latest clean hot-path budget check, the tightest
headroom observed, and when the next check is due. Fresh clean checks can
support a quiet skip for the ongoing interface-budget guard only while the
tightest metric still has positive headroom; overdue, out-of-budget, or
zero-headroom checks should prompt `python3
examples/control_plane/hot-path-interface-budget-smoke.py` or an equivalent explicit
drift-check run.

Stable fields:

- `checked_at`
- `freshness_hours`
- `next_check_due_at`
- `overdue`
- `within_budget`
- `surface_count`
- `minimum_headroom_ratio`
- `tightest_surface`
- `tightest_metric`
- `headroom_remaining`
- `recommendation`

## Promotion Gate JSON

`loopx promotion-gate --format json` is the compact machine-readable
gate for local release promotion. It reads the same append-only readiness event
as `doctor`, `status`, and `install-local.sh`, then returns a small operation
result that scripts can assert without parsing installer stderr.

This command is read-only and non-blocking. `can_promote=false` means the
installer should warn before promotion, not that the CLI refuses to install.
The warning remains a human-facing guardrail; automation should use
`gate_state`, `can_promote`, `should_warn`, and `readiness.freshness_status` as
the stable fields.
`loopx status --format json` embeds the same compact result under
`promotion_gate`, so dashboard panels, installer smoke, and CLI gate checks
consume one state contract instead of re-deriving release-readiness state
separately.

After the release checks pass, the canary uses the explicit write boundary:

```bash
loopx promotion-readiness record \
  --dashboard-readiness passed \
  --execute
```

Without `--execute`, this command only previews the runtime-level append. The
canary supplies `skipped` only for an explicit dashboard omission; dependency
failures on a present dashboard source must not be recorded as `passed`.

Fresh shape:

```json
{
  "ok": true,
  "registry": ".loopx/registry.json",
  "runtime_root": "~/.codex/loopx",
  "gate": "promotion_readiness",
  "gate_state": "ready",
  "can_promote": true,
  "should_warn": false,
  "non_blocking": true,
  "recommended_action": "promotion readiness is fresh",
  "readiness": {
    "available": true,
    "source": "runtime_release_ledger",
    "evidence_scope": "runtime_release",
    "goal_id": null,
    "dashboard_readiness": "passed",
    "classification": "canary_promotion_readiness_smoke_group",
    "freshness_status": "fresh",
    "requires_readiness_run": false,
    "freshness_window_hours": 24,
    "json_exists": true,
    "markdown_exists": true
  }
}
```

Missing or stale shape:

```json
{
  "ok": true,
  "gate": "promotion_readiness",
  "gate_state": "warning",
  "can_promote": false,
  "should_warn": true,
  "non_blocking": true,
  "recommended_action": "python3 examples/canary/canary-promotion-readiness-smoke.py",
  "warning_message": "promotion-readiness evidence is stale; ...",
  "readiness": {
    "freshness_status": "stale",
    "requires_readiness_run": true
  }
}
```

`warning_message` is intentionally human-facing and may change. It exists so
`scripts/install-local.sh` can keep its operator warning, but automation should
prefer the structured fields above.

## Global Registry Health

`global_registry` is the local multi-project health surface. It checks the
shared `registry.global.json` even when the current command is pointed at a
project-local registry, so dashboard users can see registry scope problems
before they turn into ghost projects.

Health findings are compact and local-only. They may include local filesystem
paths in a developer machine export, so do not publish a raw local status JSON
outside the machine.

Registry boundary should be checked with:

```bash
loopx registry-boundary --path <registry.json> --require-gitignored
```

The shared `registry.global.json` is classified as `shared_local_registry` and
must not be pushed. Project `.loopx/registry.json` files are
`project_local_private_registry`. Generated public-safe registry projections are
still runtime artifacts by default: they can be useful for review or handoff,
but `github_push_allowed=false` unless the file is an explicitly authored
example fixture under `examples/`. Dashboard/status JSON should therefore treat
registry data as a local control-plane view, not as a repository artifact.

Finding shape:

```json
{
  "kind": "stale_source_registry",
  "severity": "action",
  "goal_id": "project-main-control",
  "message": "`project-main-control` source registry changed after its last global sync",
  "recommended_action": "run `loopx sync-global --goal-id project-main-control` from the source project",
  "path": "/path/to/project/.loopx/registry.json"
}
```

Current finding kinds:

- `duplicate_goal_id`: the global registry contains more than one entry for the
  same goal id; this is high severity because routing is ambiguous.
- `source_registry_missing`: a global entry points at a source registry that no
  longer exists.
- `stale_source_registry`: the source registry changed after the last recorded
  `synced_at`, so the global entry may be stale.
- `state_file_missing`: the goal's declared active state file no longer exists.
- `state_file_not_declared`: the global entry has no durable active state file.
- `current_registry_scope_excludes_global_goals`: informational reminder that a
  project-local registry view excludes goals that are present in the shared
  global registry.

High and action findings are also lifted into `attention_queue.items` with
`source=global_registry`; informational scope findings stay in the global
registry panel so local project views are not noisy. Source-registry provenance
findings (`source_registry_missing` / `stale_source_registry`) are collapsed into
the live quota-backed queue item for the same `goal_id` when one exists, under
`global_registry_shadow_findings`, so stale source shadows do not become a second
quota health blocker while the fact remains visible in `global_registry.findings`.

## Contract Health

`contract.ok=false` means the UI should show a blocking health state before
encouraging more adapter work.

The summary counters are intentionally small:

- `errors`: boundary or registry problems that should block progress.
- `warnings`: non-blocking issues worth showing in a secondary health panel.
- `checks`: successful observations, useful for audit trails.

`error_diagnostics` is the source of truth for error ownership. Each row has a
stable `code`, a human-readable `message`, and either `scope=global` or
`scope=goal` with a `goal_id`; a finding shared by a known subset may use
`scope=goals` with `goal_ids`. Registry parse failures, ambiguous goal identity,
registry boundary violations, and public-boundary violations are global.
Goal-entry, active-state, and todo contract failures are owned by the goal or
known goal set that produced them.

`errors`, `global_errors`, and `goal_errors` are compatibility projections
derived from those structured diagnostics; consumers must not infer ownership
from message prefixes. `warnings` and `checks` remain short strings. Checks
should be concrete enough to support an operator decision without exposing
local paths or private evidence. They must be public-safe before a project
exposes this export outside the local machine.

## Attention Queue

The attention queue is sorted by LoopX status logic. A UI should render
it as the primary worklist.

Counters:

- `item_count`: all visible queue items.
- `needs_user_or_controller`: items waiting on either a human user or a target
  controller.
- `needs_controller`: subset waiting on a target controller or adapter
  connection.
- `needs_codex`: items ready for a Codex action.
- `watching_external_evidence`: items that should be monitored but not acted on
  until outside evidence changes.
- `watching_monitor`: monitor-only items that remain visible without implying
  immediate Codex work.

Item shape:

```json
{
  "goal_id": "complex-project-main-control",
  "status": "ready_for_controller_opt_in",
  "lifecycle_phase": "controller_gated",
  "lifecycle_flags": [
    "controller_gated",
    "adapter_inspected"
  ],
  "waiting_on": "user_or_controller",
  "severity": "action",
  "recommended_action": "先在 LoopX 完成 operator 判断；同意后项目 Agent 只执行 read-only map dry-run",
  "project_asset": {
    "owner": "user_or_controller",
    "gate": "operator_question",
    "next_action": "先在 LoopX 完成 operator 判断；同意后项目 Agent 只执行 read-only map dry-run",
    "stop_condition": "record aligned eval evidence and one human reward event",
    "user_todos": {
      "open": 1,
      "done": 2,
      "total": 3,
      "next": "Record the owner/SOP conclusion in the review worksheet."
    },
    "agent_todos": {
      "open": 1,
      "done": 0,
      "total": 1,
      "next": "Run the read-only validation map after approval."
    },
    "quota": {
      "compute": 0.5,
      "state": "operator_gate",
      "spent_slots": 0,
      "allowed_slots": 720,
      "reason": "operator gate blocks gated delivery"
    },
    "latest_validation": {
      "generated_at": "2026-06-02T12:00:00+00:00",
      "classification": "ready_for_controller_opt_in",
      "summary": "read-only map available; write-control not approved"
    }
  },
  "handoff_readiness": {
    "ready": false,
    "codex_ready": false,
    "source": "project_asset",
    "quota_state": "operator_gate",
    "handoff_status": "not_ready",
    "post_handoff_run_seen": false,
    "checks": {
      "project_asset_backed": true,
      "same_source_should_run": true,
      "codex_ready": false,
      "handoff_has_next_action": true,
      "handoff_has_stop_condition": true,
      "handoff_sanitized_surface": true
    },
    "next_probe": "loopx review-packet --goal-id complex-project-main-control --handoff-only"
  },
  "operator_question": "是否同意 `complex-project-main-control` 先执行 read-only map opt-in？",
  "agent_command": "loopx read-only-map --goal-id complex-project-main-control --dry-run",
  "quota": {
    "compute": 0.5,
    "window_hours": 24,
    "slot_minutes": 1,
    "allowed_slots": 720,
    "spent_slots": 0,
    "state": "operator_gate",
    "reason": "planned goal needs operator opt-in before spending agent turns"
  },
  "source": "latest_run",
  "controller_stage": "ready_for_read_only_not_decision",
  "missing_gates": [
    "human_reward_capture",
    "aligned_eval_decision_evidence"
  ],
  "next_handoff_condition": "record aligned eval evidence and one human reward event"
}
```

Item fields:

- `goal_id`: stable goal identifier from registry or runtime.
- `status`: adapter classification or derived status.
- `lifecycle_phase`: derived state-interaction phase for first-screen
  visualization.
- `lifecycle_flags`: all compact phases that apply to the latest state.
- `waiting_on`: `user_or_controller`, `controller`, `codex`,
  `external_evidence`, or `monitor_signal`.
- `severity`: `high`, `action`, or `watch`.
- `recommended_action`: exactly one next action.
- `project_asset`: a compact control-plane projection derived from the same
  item. It must carry `owner`, `gate`, `support_mode`, `next_action`, and
  `stop_condition`, and may include compact `user_todos`, `agent_todos`, `quota`, and
  `latest_validation` summaries. Registry-backed project assets also include
  `execution_profile`, the project-level delivery floor created by
  `loopx connect`, and `orchestration`, the compact projection of
  registry `spawn_policy` with `mode`, `spawn_allowed`, `max_children`, and
  optional `allowed_domains`. They may also include `control_plane`, the
  compact per-goal policy projection for settings such as self-repair, and
  `stale_latest_run_warning`, a compact warning that the current active-state
  file appears newer or different from the latest run's captured state
  projection. This warning is a repair hint, not a scheduler gate: consumers
  should run `refresh-state` before trusting latest-run-derived routing, while
  quota eligibility still comes from the quota guard. When orchestration mode
  is `multi_subagent`, project assets may also include `subagent_activity`, a
  compact child-run projection derived from run history. It records child run
  ids, roles, state, parent links, public-safe scope summaries, and quota-spend
  counts for observation only; it is not a lock service or write arbiter.
  This is the first-screen project asset surface for
  agents and dashboards; it lets consumers avoid reconstructing owner, gate,
  support mode, next action, stop condition, todo counts, compute state, and
  latest validation from scattered fields. It also keeps delivery-floor and
  orchestration policy close to the project asset instead of forcing agents to
  infer them from history.
  `support_mode` is a compact product-mode label: `read_only_observer`,
  `decision_support`, `reward_capture`, or `selective_assist`. It describes the
  current operator/agent relationship; it is not a permission bit and does not
  override `gate`, `quota`, or `agent_command`. When an approved
  `agent_command` is present and public-safe, `project_asset.next_safe_command`
  may repeat that command so first-screen dashboards and handoff packets can
  show the next executable local step without scanning top-level queue fields.
  Advisory dreaming outputs may include both `dreaming_proposal` and
  `dreaming_lane_badge`. The proposal carries compact rationale and promotion
  requirements; the badge carries only routing facts for UI/heartbeat
  consumers: `lane=dreaming`, `advisory=true`,
  `interrupts_delivery=false`, `review_required=true`,
  `execution_allowed=false`, `delivery_spend_allowed=false`, and
  `promoted_to_delivery=false`. Consumers should render the badge as a
  separate Dreaming lane or secondary badge and must not treat it as delivery
  authorization.
  Markdown renderers should
  include the first unfinished user and agent todo here when available, so
  hot-path readers do not need to scan the detailed todo sections. The richer
  top-level `user_todos`, `agent_todos`, and `quota` fields remain available
  for detailed views.
- `goal_boundary.peer_task_coordination`: optional quota-only coordination
  authority. It appears only when the registry explicitly selects a
  `coordinator_agent_id`. Registered peers are otherwise independent; child
  spawn policy never implies registered-peer coordination authority. A blocked
  explicit bundle never displaces the coordinator's own runnable Todo; with no
  local fallback it projects `interaction_contract.mode=peer_coordination_blocked`
  so schedulers stop until a material coordination input changes.
- Current routing authority: consumers should choose the current owner, gate,
  waiting party, and next action from `attention_queue.items` and its
  `project_asset`. `run_history.latest_runs` is an evidence and drill-down
  surface; it may be limited by status command limits or filters, so consumers
  must not use it as the sole source for deciding whether a gate is still
  pending or already approved.
- `handoff_readiness`: optional project-asset consistency and follow-through
  summary. `ready=true` means the current queue item is Codex-runnable under the
  same source, quota, next-action, stop-condition, and public-safety checks.
  `handoff_status=ready_waiting_for_run` means a handoff is ready or approved
  but no later non-accounting run has appeared in the compact history window.
  `handoff_status=post_handoff_run_seen` means a later non-neutral run exists;
  `post_handoff_latest_run` identifies that latest seen run by timestamp and
  classification, and `delivery_batch_scale` labels whether the observed
  delivery is test-only, single-surface, multi-surface, implementation-shaped,
  or still unknown. Delivery scale and outcome come only from explicit typed
  fields. Classification names, health text, recommendations, and the mere
  presence of evidence objects never establish delivery semantics.
  `delivery_turn_kind` comes from an explicit kind, the explicit outcome, or
  a scoped typed blocker observation accepted by the existing settlement
  predicate. `outcome_gap` alone does not prove blocker writeback.
  `post_handoff_recent_runs` is a compact newest-first slice of recent
  post-handoff work runs. `post_handoff_small_scale_streak` counts only leading
  explicit `test_only` / `single_surface` scales; unknown breaks the streak.
  Explicit `delivery_outcome` values are `outcome_progress`, `surface_only`,
  `outcome_gap`, and `primary_goal_outcome`. Missing or invalid historical
  semantics stay unknown. A missing outcome with no configured floor retains
  the internal `not_configured` sentinel and is omitted from the compact run.
  Legacy outcome-marker/hint lists retain floor-configuration compatibility,
  but their words no longer classify runs. With that floor configured,
  `post_handoff_outcome_gap_streak` counts only consecutive explicit
  `surface_only` / `outcome_gap` outcomes; unknown breaks the streak.
  Follow-through obligations require typed fields, never narrative inference.
  New delivery claims use the existing writer enum fields; state-only refresh
  without a delivery claim remains legal. This deliberately changes decisions
  previously inferred from old untyped labels without rewriting run history.
  `quota_slot_spent` events do not count as post-handoff work.
- `operator_question`: optional human-facing gate to show in the LoopX
  operator view. This is the canonical place for user/controller judgment.
- `agent_command`: optional command or instruction for the target project agent
  after the operator gate is approved. Dashboard consumers should not treat it
  as approval by itself.
- `quota`: optional compact compute-quota state. It should summarize the
  compute share (`1.0`, `0.5`, `0.3`, or `0`), eligibility, recent spend, and
  a public-safe reason without exposing private evidence. See
  [quota-allocation.md](quota-allocation.md).
- `control_plane`: optional compact registry policy for this goal. Current
  settings include `self_repair.enabled`,
  `self_repair.allow_health_blocker_repair`, and
  `self_repair.allow_waiting_projection_repair`. Missing settings mean default
  off, so ordinary goals remain in their existing skip/wait lanes.
- `user_todos`: optional checkbox summary parsed from the active state's
  `## User Todo ...` / `## Owner Review Reading Queue` section. Dashboard
  consumers should render the first unfinished item as the human-facing next
  step, while keeping `recommended_action` as routing context. The compact
  `project_asset.user_todos` projection keeps legacy `next` / `next_index`
  fields and may also include up to three unfinished `items` for thin workers
  that need more than the first open todo.
- `agent_todos`: optional checkbox summary parsed from `## Agent Todo`,
  `## Codex Todo`, or `## Project Agent Todo`. Agent-facing consumers may use
  it to choose implementation work after gates and quota allow execution; it is
  not a user approval signal. Status, quota, and review-packet projections
  should preserve up to three unfinished agent todo items so short heartbeats do
  not confuse "first visible item" with the whole backlog. Todo summaries also
  expose `first_executable_items` for advancement-class work and
  `monitor_open_items` for continuous-monitor work; executable items are the
  primary action surface, while monitor items are supplemental context that
  should not consume the selected goal's advancement slot unless they record a
  material transition or blocker.
- `issue_meta_surface`: optional public-safe issue/PR anchor projection parsed
  from an active-state `## Issue Meta Surface` section and mirrored under
  `project_asset.issue_meta_surface`. Schema `issue_meta_surface_v0` carries a
  bounded list of compact `issue_meta_surface_item_v0` rows with
  `repo_handle`, `issue_handle`, GitHub-style `labels`, `owner_route`,
  `related_code_hint`, `validation_surface`, `promotion_target`, `status`, and
  `freshness`. It is a scenario state surface for issue/PR solver anchors, not
  a command to read private source, publish comments, or open PRs.
- `capability_gate`: optional per-goal quota projection derived from visible
  executable agent todos that declare `required_capabilities`. When
  `action=run`, the gate projects `runnable_candidates` and
  `blocked_candidates`; `decision_owner=agent` means the agent chooses the
  actual todo from the runnable set during its steering audit. The gate must
  not rewrite `recommended_action` into a single selected todo.
  `required_capabilities` means a prerequisite for directly executing that
  todo, not the capability the todo is trying to build. A todo may separately
  declare `target_capabilities` for capabilities it is developing, repairing,
  materializing, or parity-checking. Target capabilities are not hard gates. If
  a target bridge such as `benchmark_runner` is absent, the candidate may still
  appear in `runnable_candidates` with `capability_repair_mode=true`,
  `capability_action=repair_bridge`, and `missing_target_capabilities` on the
  candidate. When no visible executable candidate is runnable, the gate owns
  the decision and returns `repair_bridge`, `ask_owner`, or `skip` with
  concrete missing capability details.
- `project_asset.todo_projection_gap`: optional explicit gap object emitted
  when status cannot project `user_todos` and/or `agent_todos` for a connected
  project. This means "the first-screen todo state is unknown", not "there are
  zero todos". Consumers should surface the missing roles and ask for a
  parseable active-state todo section or state-file repair before treating the
  project asset as first-screen complete.
- Todo summaries use `schema_version=todo_summary_v0`; parsed todo items use
  `schema_version=todo_item_v0`. The source active state can remain ordinary
  Markdown checkboxes, but status/quota/dashboard consumers should prefer the
  structured item fields when present: `todo_id`, `role`, `status`,
  `priority`, `title`, `archive_state`, `source_section`, `index`, `text`,
  `task_class`, `action_kind`, `note`, `evidence`, `reason`, `completed_at`,
  `updated_at`, `superseded_by`, and `claimed_by`. Todo summaries may also
  expose `claimed_open_count` and `unclaimed_open_count` so dashboards and
  heartbeat dispatchers can show soft ownership without inferring a lock.
  `claimed_by` is a visibility hint written through the todo CLI, not a lease
  or permission grant; claim ids must be registered on the goal's coordination
  contract, and agents must still obey quota, gates, write-scope checks, and
  their automation/handoff scope. `todo_id` is first-class when written by the
  todo CLI; legacy Markdown without metadata still gets a parser-derived
  compatibility id from the current item text and section, and the first
  lifecycle command materializes that id back into metadata.
  The first open item is still available through `first_open_items` for older
  heartbeats. Frontstage consumers should not treat that top-N scheduler view
  as the whole backlog. Todo summaries may also project visibility lanes:
  `unclaimed_priority_open_items` for priority-ranked unclaimed candidates,
  `claimed_open_items` for claimed work that may be outside the top-N,
  `claimed_advancement_open_items` for claimed executable delivery work, and
  `claimed_monitor_open_items` for claimed continuous-monitor work. Agent-aware
  quota/status projections may further include
  `current_agent_claimed_open_items`,
  `current_agent_claimed_advancement_items`,
  `current_agent_claimed_monitor_items`, and `claimed_by_others_items`.
  The scheduler can still select from a narrower executable candidate set; the
  dashboard/frontstage uses these lanes to keep ownership visible. Visibility
  lanes may be wider than scheduler lanes, but remain bounded; the default
  agent-facing cap is 16 items per lane. Consumers should use the corresponding
  counts to indicate when more claimed work exists than is expanded in the
  current payload, and richer frontstage views should use a future
  paged/filtered projection instead of forcing larger heartbeat payloads.
  The canonical todo drill-down contract is
  `docs/reference/protocols/todo-detail-cold-path-v0.md`: hot-path summaries
  may carry only a compact `todo_detail_ref_v0` pointer for one selected item,
  while full notes, evidence summaries, related lifecycle references, and page
  tokens stay in `todo_detail_cold_path_v0` cold-path responses. Status,
  quota, heartbeat, and handoff payloads must not inline full todo detail in
  order to make hidden backlog visible.
  When claimed lanes exceed the cap, producers should avoid raw top-N
  truncation. Sort by priority and source position, group by `claimed_by`, take
  a fair per-claimant slice, then fill remaining slots from the sorted
  remainder. Agent-scoped projections should then order focus as
  current-agent claimed items, unclaimed items, and lower-weight other-agent
  claimed items. Other-agent claims are visibility context and last-resort
  candidates; they are not a hard lock, but they also should not outrank the
  current agent's own claimed work or unclaimed work.
  Deferred todos are projected after sorted open todo lanes through
  `deferred_items` and, when a machine-readable resume condition is satisfied,
  `deferred_resume_candidates`. This is a gate-resume lane, not no-candidate
  evidence and not executable backlog. The default deferred visibility cap is
  eight items. Parsed deferred items may include `resume_when`,
  `resume_condition`, and `resume_ready`; consumers should not merge these into
  `first_open_items` or executable backlog until a lifecycle command reopens or
  supersedes the todo. Agent-scoped quota may further split ready candidates
  into `current_agent_deferred_resume_candidates`,
  `unclaimed_deferred_resume_candidates`, and
  `other_agent_deferred_resume_candidates`, where only the first two can wake
  the current peer before an agent-scoped no-candidate wait is allowed.
  Open todos may also carry `resume_when`; status should attach
  `resume_condition` / `resume_ready` but keep the item out of executable
  backlog until `resume_ready=true`. A `monitor_changed:<todo_id>` condition
  additionally carries `resume_monitor_generation`, while the target monitor
  carries monotonic `material_change_generation`; readiness requires the
  latter to be strictly greater. This lets agents see not-yet-unlocked
  successors without accidentally selecting them as current work or waking on
  an unchanged/replayed monitor result.
  A `resume_at:<timezone-aware-rfc3339-timestamp>` condition carries
  `scheduled_for`, `evaluated_at`, `clock_provider=runtime_clock`, a one-shot
  material-change generation, and—once due—a stable `resume_receipt`. The
  receipt remains identical across later reads and restarts. Presentation
  clients may show its id for diagnosis, but must not treat it as execution
  authority or maintain a separate local countdown state.
  Optional future fields such as `created_at`, lease TTLs, dependencies, or
  evidence links should extend this item shape rather than inventing another
  todo surface.
- Agent-scoped quota payloads may include
  `agent_todo_summary.claim_scope` with
  `schema_version=agent_claim_scope_v0`. The quota guard should select
  current-agent claimed todos before unclaimed todos, then expose other-agent
  claimed todos as lower-weight candidates. Compatibility payloads may still
  include `blocked_claimed_items`, but new consumers should prefer
  `other_agent_claimed_items` and `other_agent_claimed_open_count`. This is
  claim-aware routing, not a hard lease: every peer can inspect the goal-wide
  backlog, while claims, task policy, capabilities, and boundaries determine
  what it may execute. A current-agent claimed todo may be selected even when
  the active state's global `Next Action` names another peer's lane; that is not
  a state projection mismatch.
- Agent-scoped quota payloads also expose the versioned `task_scope` enum
  `goal_all_read_claimed_run_global_read_v0`. It means the peer may
  read all ordinary todos in the current goal, may consider its own claimed or
  an eligible unclaimed candidate, must claim before execution, and may execute
  only its claimed eligible todo. Other-agent claims are diagnostic only.
  Cross-goal reads stay outside goal-local routing and require an explicit
  read-only global-manager inventory such as `loopx global-summary`; they never
  grant cross-goal execution. Existing goal, agent, and cold-path command
  fields supply the binding parameters without duplicating them. TurnEnvelope
  retains this compact enum so the model sees the same boundary as the full
  quota decision.
- `dependency_blockers`: optional compact summary of unfinished user todos from
  other current attention-queue goals. This lets dashboards and heartbeat
  dispatchers show sibling/project dependency gates separately from the current
  goal's own `user_todos`; it is visibility context only and must not by itself
  change the current goal's quota or owner decision.
- Local active-state file paths are intentionally omitted from queue items.
  They are useful debug/source metadata, but the public-safe status queue should
  identify work by `goal_id`, project asset state, and compact todos instead of
  exposing machine-specific paths.
- `source`: `contract`, `registry`, `run_history`, or `latest_run`.
- `controller_stage`: optional compact controller-readiness classification from
  the latest run.
- `missing_gates`: optional public-safe gate ids that explain why a goal cannot
  advance to the next controller stage yet.
- `next_handoff_condition`: optional public-safe condition for advancing the
  controller handoff.

`status=unregistered_runtime_goal` is emitted when runtime has an actionable
goal that is not present in the registry. Dashboard consumers should show this
as controller work: register the goal if it is active, or archive the runtime
record if it is old. Watch-only legacy records remain visible in run history
without entering the queue.

`status=state_refreshed` is emitted for registered goals when the latest compact
run came from `loopx refresh-state`. Dashboard consumers should show it
as Codex-ready work: the controller state changed, and the next agent turn
should inspect the refreshed active state before continuing.
If the refresh command was run without `--recommended-action`, the compact
`recommended_action` should be the first local control-plane item from the
refreshed active state's `## Next Action`, including wrapped continuation
lines; only when that durable section is absent should it fall back to the first
open Agent Todo, and finally to a generic refresh notice. This field may carry
stable local routing references such as todo ids, branch names, agent ids, PR
refs, or private-material pointers because it serves the individual operator's
local loop. It must not carry credentials, auth headers, or inline secrets.
Public/export sinks are responsible for redacting or omitting local/private
references before rendering shareable surfaces. The record includes
`recommended_action_source` (`explicit_arg`, `active_state_next_action`,
`agent_todo_fallback`, or `default_refresh_action`) so consumers can distinguish
run guidance from durable-state projection and last-resort compatibility
fallback.
`--recommended-action` describes the appended run record; it does not rewrite
the active state's durable `## Next Action`. To intentionally change that
durable route in a multi-agent goal, a registered peer must run
`refresh-state --agent-id <registered-peer> --progress-scope goal --next-action
<local control-plane action>`. Status projections may expose both
`active_state_next_action` and
`latest_run_recommended_action`; when they differ, `next_action_projection_warning`
marks the drift instead of silently choosing one as the only truth.
Executable dispatch should use `agent_lane_next_action` / todo projection rather
than treating shared `## Next Action` as a per-agent work item.

In a multi-agent goal, `refresh-state` requires an explicit `--agent-id`; text
or todo-title inference is not a valid identity source. When a refresh is
scoped with `--agent-id` and no `--progress-scope`, the run records
`progress_scope=agent_lane`. This is a lane note, not a project-level status
transition: status/quota keep selecting the latest non-agent-lane run for the
goal-level `status` and `recommended_action`, while exposing the lane note as
`agent_lane_recommendation` on the attention item and project asset. A
goal-level refresh in a multi-agent goal must use a registered peer with
`--progress-scope goal`.
Use agent-lane scope for a peer-local recommendation that should not replace the
durable goal route.

If a peer self-merged slice materially advanced the public product or case
path, that peer or another registered peer should also write a project-level
refresh with the matching `delivery_outcome=outcome_progress` (or skip the
extra project-level sync entirely). A later `surface_only` project-level sync
will become the latest non-agent-lane run, so quota may correctly ask for
follow-through even though the peer-lane note recorded real progress.

For registered `connected`, `connected-read-only`, and `pre-tick-runnable`
adapters, custom compact progress classifications that are not blocker, gate,
or watch classifications also remain Codex-ready. This lets a controller record
a validated progress run before quota accounting without forcing an extra
state-only refresh solely to make `quota spend-slot` eligible.

### Autonomous Backlog Candidates

`attention_queue.autonomous_backlog_candidates` is an optional compact list of
unfinished `advancement_task` agent todos from current queue items where
`waiting_on=codex` and the goal quota is `eligible`. Monitor-class todos are
intentionally excluded from this backlog so dependency/readiness observation
cannot crowd out implementation, planning, or blocker-writeback candidates.
`attention_queue.autonomous_monitor_candidates` may separately expose compact
`continuous_monitor` todos from `waiting_on=codex` or
`waiting_on=monitor_signal` queue items so heartbeat dispatchers still see the
current watch surfaces without treating them as primary advancement work.
Both candidate surfaces preserve `action_kind` when the agent registered one
with the todo CLI.

Both lists are candidate surfaces only: consumers must still obey the selected
goal's quota, `goal_boundary`, owner/gate, public/private boundary, and
validation/writeback rules before spending a turn.

`quota should-run` includes `goal_boundary.orchestration` when the selected goal
has registry `spawn_policy` or project-asset orchestration state. Consumers use
that boundary to decide whether the next bounded turn should stay in default
single-worker mode or may launch child workers under the declared limits.

Registry entries may override first-screen attention with optional public-safe
fields: `waiting_on`, `attention_status`, `recommended_action`,
`operator_question`, and `next_handoff_condition`. Status respects this before
classifying the latest run, so a goal with a fresh `state_refreshed` record can
still remain in `waiting_on=user_or_controller` when the active state says a
human or target controller decision is the real next gate. This is intended for
state-truth corrections, not for granting project-agent execution. Global
registry sync preserves an existing attention override when a different later
source for the same goal omits these fields, so a controller-authored gate is
not accidentally lost during ordinary project sync. When the same source
registry syncs again and omits these fields, that source is treated as
authoritative and the stale override is cleared. A syncing registry entry can
also set `clear_attention_override=true` to clear an override explicitly.

Todo extraction is independent of attention overrides. A goal can stay in the
operator lane because the registry says `waiting_on=user_or_controller`, while
the active state exposes a more concrete user checklist. This is the preferred
shape for complex project review: keep `recommended_action` short enough to
route the queue, and put ordered user work in checkbox sections the dashboard
can summarize. This routing text belongs to the user's local control plane: it
may include private project refs, but it must not include AK/SK values, tokens,
auth headers, passwords, or inline credentials.

For registered planned high-complexity goals with a compatible
`*_read_only_map_v0` adapter and no run yet, status keeps the queue item in
`waiting_on=user_or_controller`, emits an `operator_question` for the Goal
Harness operator view, and puts the dry-run preview in `agent_command`. The
preview should report `opt_in_required=true` and append nothing; dashboard
consumers must not treat the command as controller opt-in or a durable map run.
The human-readable Markdown status view may also render an
`operator_gate_dry_run` helper before `agent_command`; that helper is a
user-owned gate recording preview, not a JSON contract field or project-agent
command.
Executor-facing guards are stricter than status display: `quota should-run`
must keep these planned items at `should_run=false`, `state=operator_gate`, and
must not include `agent_command` until an approved operator-gate run makes the
goal eligible. This keeps a preview command from becoming an automatic project
agent handoff. When the quota payload includes `agent_todo_summary`, the target
project agent can use it as the safe follow-up checklist for its own next action
instead of re-reading chat history. When the quota payload includes
`safe_bypass_allowed=true`, that permission only covers independent read-only
steering or analysis from the active state's priority stack; it still must not
execute the gated preview command, adapter work, write-control, or production
actions. When the payload also includes `gate_prompt`, `operator_question`,
`user_todo_summary`, or `agent_todo_summary`, the executor should ask that
concrete gate in the visible thread with `NOTIFY` unless the same unresolved
gate was already asked recently; the guard should not collapse a user decision
into a silent skip.
When a goal has `coordination.registered_agents`, identity-aware heartbeat
prompts should call `quota should-run --agent-id <registered-agent>`. If an
old installed prompt omits that flag, the quota payload should include
`decision=automation_prompt_upgrade`,
`effective_action=automation_prompt_upgrade_required`,
`automation_prompt_upgrade.required=true`, `blocks_should_run=true`, and
example `heartbeat-prompt --agent-id ... --agent-scope ...` commands. Executors
should treat this as a prompt-upgrade action, not as delivery permission, a
quiet no-op, or a new operator gate. `should_run`, `normal_delivery_allowed`,
and `interaction_contract.agent_channel.delivery_allowed` must stay `false`
until the automation reruns `quota should-run` with a registered `--agent-id`.
When v0.1 hierarchy fields are still present, the same object also carries a
stable `migration_id`, `host_update_idempotency_key`, and `completion_command`.
The host may retry regeneration and automation update with that same id; the
registry cutover happens only after the host update succeeds and the completion
command acknowledges that exact id. Completion removes the hierarchy fields,
records `coordination.completed_migrations.peer_agent_runtime_v1`, and is an
idempotent no-op when repeated with the completed id. Once that marker exists,
`quota should-run` must never project this registry migration again. This is a
stable, retryable migration until acknowledgment, not a recurring notification
and not permission to update an automation more than once under different keys.
The selected identity is part of the turn envelope. Follow-up lifecycle
commands that interpret or account for the same turn, including scoped
`refresh-state` and `quota spend-slot`, should preserve the same `--agent-id`
when the subcommand supports it. A spend preview that drops the identity may
correctly show `automation_prompt_upgrade_required` for an unscoped automation,
but that is an accounting/projection mismatch for the scoped turn, not evidence
that the earlier peer guard was invalid.
An accountable `refresh-state` normally records the current checkout as
`delivery_workspace`. When implementation and validation happened in an
independent worktree but registry/state projection must run from another
checkout, pass `--delivery-workspace-path <delivery-worktree>`. LoopX validates
the referenced checkout against the peer-isolation policy and persists only its
credential-free repository identity and workspace class, never the local path.
An explicit canonical checkout is rejected for peer delivery, so this causal
override cannot turn non-isolated work into an accountable delivery.
For registered agent-scoped turns, `quota should-run --agent-id` may include
`agent_lane_next_action.schema_version=agent_lane_next_action_v0`. This is a
read-only derived pointer to the current agent's selected advancement slice,
chosen from runnable capability candidates first and then the agent-scoped
executable todo summary. It may point to a current-agent claimed todo even when
the active state's global `Next Action` is still owned by another route; the
field must therefore carry `preserves_goal_next_action=true` and must not be
treated as a project-level status overwrite. `status --agent-id` may reuse the
same quota-derived object as item/project-asset observation data; consumers must
render it as an agent-lane pointer, not as `recommended_action` replacement.
Human markdown should label this pointer as the current agent's todo and mark
co-displayed global agent todo rows as goal-wide, so `--agent-id` is not
mistaken for a filter that replaces the goal-wide queue.
When more than one already-admitted advancement todo remains runnable, the
same guard also includes `action_portfolio.schema_version=
quota_action_portfolio_v2`. `primary` is the ordered recommendation, while
`suggested_actions` is the single canonical bounded convenience view: it carries
the recommendation plus at most two ordered, agent-scoped, capability-ready
alternatives and labels them `recommended` or `alternative`. The portfolio does
not duplicate those candidates under a second fallback view.
`selection_policy.candidate_scope=current_authoritative_eligible_todos` keeps
the legal choice boundary separate from the displayed suggestions;
`suggestions_exhaustive=false` makes that distinction machine-readable.
`selection_policy.decision_owner=agent` and
`recommendation_role=default_not_binding` make the priority order advisory at
this boundary rather than silently binding the first Todo.

Version 2 adds the optional `continuation_hint` field to each
`suggested_actions[]` item so an agent can see the candidate's next execution
boundary without joining the compact packet back to the full Todo summary.
All v1 selection, ordering, identity, and permission semantics remain
unchanged. The default producer now emits only v2; `quota should-run`, compact
CLI output, TurnEnvelope, and the model-free `loopx turn` controller all carry
or consume that version. The compact CLI projection separately reports
`suggested_action_details.schema_version=
quota_cli_action_portfolio_compaction_v1` and inlines `todo_id`,
`selection_role`, `priority`, `action_kind`, `text`, and `continuation_hint`.
Hosts that match schema versions exactly must upgrade their portfolio decoder
before consuming the new default. There is no v1 dual-emission or downgrade
negotiation; an unknown version must fail closed rather than silently treating
the recommendation as delivery authority. Consumers that already ignore
unknown additive item fields still need to accept the explicit v2 version.

The first quota response sets `selection_required=true` and exposes one typed
`selection_command.command_args_template` with a `{todo_id}` placeholder plus a
shared bound `route_prefix` and compact `candidate_discovery_args` for the
authoritative open agent queue. Its
heartbeat receipt has no settlement identity, so direct delivery and spend fail
closed. The template is deliberately independent of the bounded suggestions:
the agent may discover and request any currently projected, agent-scoped,
capability-ready Todo. That request is a pending selection, not a committed
receipt identity. Quota first re-runs current lane arbitration and eligibility;
a newly due hard-priority monitor, blocking user gate, or other preemption
defers the request and keeps the receipt identity-less. A qualified request does
not need to have appeared in the bounded suggestions. Only the upgraded response
restores delivery and its settlement plan. If there is only one admitted action,
no portfolio selection phase is added.

When selected work has meaningful typed lineage, waiting, sibling, or
goal-acceptance context, the same default guard may also include
`planning_horizon.schema_version=quota_planning_horizon_v0`. It is a bounded
read-only projection: at most five Todo items, eight typed relations, two
acceptance gaps, and three attention ids. `source_context_todo_count` covers
open plus deferred source Todos; omission and text-truncation counters make an
incomplete slice explicit. `successor` remains `lineage_only`, while
`resumes_when` and `unblocks` retain their existing typed lifecycle semantics.
The horizon does not make another Todo executable and does not change the
selected Todo. Consumers must use the existing explicit selection re-entry for
another runnable action and follow `detail_refs` before treating an incomplete
horizon as exhaustive.

`action_portfolio`, `planning_horizon`, and the explicit agent-Todo detail lens
derive from one `todo_planning_inventory_v0`. `planning_state` and `claim_state`
are orthogonal: runnable unclaimed work carries
`claim_required_before_work=true`, while work claimed by another agent is not
silently made executable. `quota should-run --include-detail agent-todos`
exposes the larger bounded `todo_planning_inventory_detail_v0` projection;
`item_detail_ref=$.agent_todo_summary` avoids copying full Todo payloads. Its
completeness counters remain authoritative for omissions. Consumers that need
the complete open source follow `detail_refs.full_todo_list` rather than
assuming either bounded projection is exhaustive.

Correctly typed future work is handled earlier. A higher-priority
`continuous_monitor` with a valid future `next_due_at` is not executable; quota
selects the next ready advancement todo and records the future monitor under
`action_portfolio.unavailable_higher_priority` with
`availability_reason=scheduled_for_future`. Legacy state that labels such work
as `advancement_task` cannot be reclassified from phrases such as “Monday” or
“after the window opens”; explicit `task_class` remains authoritative. The
portfolio still exposes bounded alternatives for that compatibility case, so
the agent can bind a different ready action without replaying the entire cold
diagnostic packet.
The same scoped guard may include
`goal_route_hint.schema_version=goal_route_hint_v0`. This is a goal-level
read-path synthesis over the current `agent_lane_next_action`,
`agent_scope_frontier`, and compact per-agent todo lanes. It carries
`preserves_goal_next_action=true` and `goal_next_action_mutation=none` so hosts
can explain the lane decision without mutating shared `## Next Action` or
collapsing other agents' queues into the current agent's route.
Within a candidate source, selection is ordered by current-agent claim first,
then `capability_repair_mode=true`, then priority/index. A repair-mode item
therefore stays visible as the suggested agent-lane slice even when an older
ordinary runnable P0 todo appears earlier in the active state; otherwise a todo
that exists to build the missing capability can be starved by work that depends
on that capability becoming reliable.
For any registered peer, `quota should-run` also enforces the workspace
boundary when the selected task writes repository state. If the guard is being
run from a non-git directory, from an unrelated git worktree, or from a checkout
that does not satisfy the task/repository isolation policy, the payload should include
`workspace_guard.schema_version=agent_workspace_guard_v1`,
`workspace_guard.action=move_to_independent_worktree`,
`workspace_repair_allowed=true`, `normal_delivery_allowed=false`, and
`effective_action=agent_workspace_repair`. The interaction contract should
use `mode=agent_workspace_repair`, require the peer to create or switch to
an independent worktree/branch, and require rerunning `quota should-run` with
the same `--agent-id` before repository edits. This preflight does not spend
quota; `quota spend-slot` should fail closed until the guard is rerun from the
independent worktree.
Workspace and boundary guards must bind to the final work-lane `selected_todo`
after due-monitor, capability-fallback, and scoped-gate routing. They must not
inherit repository or write-scope semantics from an unrelated first executable
backlog item; in particular, a selected read-only continuous monitor stays
monitor work even when a separate repository repair is also runnable.
If the selected todo declares `task_repository`, the guard should also project
that credential-free identity with
`workspace_guard.repository_source=selected_todo.task_repository`; otherwise
`repository_source=goal.repo`. A matching repository identity is necessary but
not sufficient: the current checkout must still be a linked worktree rather
than that repository's canonical checkout. `task_repository` is not a write
scope or permission grant.
Dashboard and Review Packet consumers should project `workspace_guard` as an
agent-channel workspace repair, not as an operator or user gate. The first
screen can render the current workspace class, required workspace class, repair
action, and whether normal delivery is allowed. It should not ask the user to
approve the move, mark the selected todo as blocked by the user, or hide open
same-scope work. If a peer is also looking at a todo claimed by another peer,
the packet should explain both boundaries separately: the workspace guard
requires moving to an independent worktree, while the claim boundary requires
choosing an in-scope current-agent or unclaimed todo, transferring the claim, or
creating an explicit successor.
When the payload includes `notify_user_on_open_todo=true`, the open
`user_todo_summary` is the current blocker-push surface even if there is no
operator gate. This is intended for `focus_wait`, `waiting`,
and `external_evidence` lanes where a short user/owner answer can unlock
progress or stop repeated meaningless polling. Executors should list at most
three open todos, include
`open_todo_notify_reason`, skip implementation work, and skip quota spend for
that blocker-push turn. When the payload also includes
`open_todo_notification_policy=repeat_until_resolved`, the
executor should repeat the notification until the todo is done, deferred, or
replaced. A `user_gate_notification_cooldown_v0` packet with
`notification_suppressed=true` is the narrow exception: the gate and open-count
remain visible, while `interaction_contract.user_channel` becomes
`action_required=false`, `notify=DONT_NOTIFY` until the bounded reminder window
or a material gate/host change. Other blocker-push
cases may still be de-duplicated when the same blocker was surfaced recently.

The suppression decision also removes `actions` and `non_blocking` from the
final user channel, so a remaining `user_action` cannot re-promote the same
contract to `NOTIFY`. Provider sinks must deduplicate delivery by gate identity
plus material state generation, with an additional reminder generation only
for an explicitly due reminder window; presentation text is not delivery
identity.
Eligible monitor-only no-transition polls keep open user todos in
`user_todo_summary`, but do not force repeated notification or set
`requires_user_action=true`; they should surface as a quiet
`monitor_quiet_skip` rather than an executable run.
When the payload includes `external_evidence_observation`, the goal is waiting
on an external monitor that still requires a read-only observation contract.
This is not prompt-specific advice: `quota should-run` should also set
`effective_action=external_evidence_observe` and
`execution_obligation.kind=external_evidence_observation_required`. Executors
must check for a concrete observable handle or compact writeback surface, such
as a thread id, automation id, job id, lock/result marker, or result path. If no
handle exists, the correct action is a compact blocker or launch-readiness
writeback, not a quiet no-op and not benchmark execution.
When the payload includes `heartbeat_recommendation`, executors should follow
that generic lifecycle hint before inventing local automation behavior:
`run_first_read_only_map` runs and saves one real read-only map before spending
once, while `mapped_noop_if_unchanged` returns a quiet no-op without another
dry-run or quota spend if no new instruction, evidence, todo, stale source, or
safe handoff exists.
When the payload includes `stale_latest_run_warning`, the current active-state
projection has moved ahead of the latest run-history snapshot. Executors should
repair the control-plane projection with a fresh state refresh before relying on
latest-run status, review packets, or handoff fields, but the warning alone does
not authorize production actions or override `should_run`.
When the payload includes `backlog_hygiene_warning`, the active state has
multiple public-safe durable follow-up items in `Next Action` or
`Operating Lessons` while the active `Agent Todo` checklist has no open item.
Executors should mirror the durable follow-up work into concrete Agent Todo
checkboxes before heartbeat scheduling relies on those narrative sections. The
warning is a checklist hygiene signal only: it does not change quota eligibility,
grant write or production permission, or make a quiet no-op valid when
`execution_obligation.must_attempt_work=true`.
When the payload includes `completed_todo_archive_warning`, the active
`Agent Todo` checklist has accumulated too many completed entries for the
dashboard/status surface to keep current open work visible. Executors should
move older completed entries into a dedicated `Completed Work Archive` section
and keep only current open work plus a small recent-done tail under active
`Agent Todo`. The warning's `archive_command_template` includes the projected
`default_archive_keep_count` as `--max-active-done`, so the copyable command and
the warning's recent-done tail contract stay aligned. Archive sections are
intentionally ignored by active todo parsing.
This warning is a checklist hygiene signal only: it does not change quota
eligibility, grant write or production permission, or supersede open user/agent
todo blockers. It also does not mark an open todo complete; executors should
use `loopx todo complete`, `todo update`, or `todo supersede` for
structured lifecycle transitions by `todo_id`.
When the payload includes `autonomous_replan_obligation`, the active state's
current `Next Action` or `Operating Lessons`, or the recent public run history,
carries public-safe evidence that the controller may be stuck in a
periodic-review threshold, no-progress streak, repeated-action loop, phase
transition, backlog mismatch, evidence contradiction, or two repeated public
monitor/no-progress run records. Historical progress entries and completed
todos are intentionally not active-state trigger sources. Executors should
treat the object as a machine-readable planning contract, not prompt advice:
inspect `triggers`, apply the compact `todo_actions` as split/add/retire
guidance, write the selected todo/vision/blocker delta, and stop at
`stop_condition`. Validation remains part of the normal delivery evidence or
PR review path, not a command projected to the runtime agent. The default stall
threshold is 2 consecutive stalled turns or
public run records. A `quota_monitor_poll` record is status-neutral for latest
dashboard state, but it is still public stalled-run evidence for this specific
replan detector. For eligible goals,
`heartbeat_recommendation.recommended_mode` may become
`autonomous_replan_required`, and `execution_obligation.kind` may become
`autonomous_replan_required` with `must_attempt_work=true`, even when open user
todos remain visible, as long as the selected slice stays outside private,
destructive, production, or owner-only authority and honors `stop_condition`.
An open typed `user_action` remains a non-blocking notice even when no agent
todo is currently runnable; it must not force `waiting_on=controller`. When two
bounded stalls leave that agent frontier empty, the obligation requires one
typed semantic outcome. When the bounded frontier already owns a
concrete typed target, the interaction contract projects a claimed `todo add`
with `--replan-obligation-id <exact-id>`, `--action-kind`, and a stable
`--target-key` or Explore node ref. That one mutation atomically records the
runnable successor and its causal obligation; it returns
`host_action=end_current_heartbeat`, and the successor runs on the next
heartbeat. A generic planning instruction is never compiled into a Todo. When
no executable target is known, the packet instead requires a typed semantic or
coverage-backed terminal writeback. No second repair-ACK write is required.
The user reminder stays visible throughout this replan path.
This is intended to keep monitor-only work from consuming the primary
executable backlog, not to bypass real gates.
`quota should-run` and `status --agent-id` may also expose
`goal_frontier_projection.schema_version=goal_frontier_projection_v0`. This
projection is owned by `loopx.control_plane.goals.goal_frontier`: it is a
compact per-goal progress/frontier view, not another quota sub-state. When it contains
`autonomous_replan_decision`, that decision is made before lane-local
`monitor_quiet_skip`, `agent_scope_wait`, or `agent_scope_exhausted` projection,
so those local no-candidate states cannot mask a required bounded replan.
The payload includes `interaction_contract.schema_version =
loopx_interaction_contract_v0`, which is the primary user/agent/CLI
protocol for a selected goal. It groups the current turn into a stable
`mode` such as `bounded_delivery`, `user_gate`, `user_todo_blocker_push`,
`external_evidence_observation`, `monitor_quiet_skip`, `autonomous_replan`,
`outcome_floor_recovery`, `mapped_noop_if_unchanged`, or `quota_throttled`.
Its `user_channel` says whether to interrupt the user and why;
`agent_channel` says whether Codex must attempt work, whether delivery is
allowed, whether quiet no-op is allowed, and the primary action; `cli_channel`
says which CLI transitions and spend policy apply. Executors should read
`interaction_contract` first, with
`interaction_contract.agent_channel.primary_action` as the only executable
action entrypoint for the current turn. Optional
`agent_channel.resolution_trace.summary` is diagnostic: it compactly records
the source signal matched by `primary_action` and whether drift was detected. Existing
`state_action_projection_warning` / `next_action_projection_warning` fields
carry any writeback review guidance. The trace is not an independent
next-action authority and does not imply automatic active-state writeback.
`execution_obligation`,
`heartbeat_recommendation`, `work_lane_contract`,
`external_evidence_observation`, `goal_boundary`, and
`protocol_action_packet` remain compatibility and drill-down fields under that
contract, not competing sources of truth.
The same payload includes `scheduler_hint.schema_version=scheduler_hint_v0`.
This is the scheduling contract for host runtimes, not a delivery permission:
Codex App can back off its automation cadence for long waits, while Codex CLI
TUI and Claude Code loops can run one final quota/replan check after repeated
unchanged polls, then exit/stop only if the guard is still unchanged. Cadence
changes, final checks, and loop self-stop never spend quota. Host schedulers
apply `recommended_interval_minutes` as the next target interval and multiply
subsequent unchanged intervals by `unchanged_poll_backoff_multiplier` until
`max_interval_minutes`; `example_progression_minutes` exposes the compact
human-readable sequence. The hint also includes a compact `reset_policy`:
hosts compare `reset_token` between polls and clear the unchanged/backoff
streak when that token changes, or when a user reply, new/reassigned todo,
resolved gate, or material transition makes the goal actionable again. The
token is derived from scheduler action plus identity/profile inputs, while the
hot path carries only action fields plus a short `identity_signature`; the
profile signature, reset-condition summary, and full stateful-backoff policy are
available from `scheduler_hint.cold_path_detail` when callers request
`loopx quota should-run --include-detail scheduler`. The reset moves Codex
App/local cadence back to the current profile's initial interval before
unchanged backoff resumes, and does not spend quota.
Codex App heartbeats should use `automation_update` only when
`app_automation.stateful_backoff.apply_needed=true` and
`app_automation.recommended_rrule` is present. If that update succeeds, the agent
must run `app_automation.ack_hint.cli_args`;
current payloads use `quota scheduler-ack-current` so LoopX re-reads the latest
hint, then persists `reset_token`, `identity_signature`, `progression_index`,
and `last_applied_rrule` under the runtime root. When the same identity repeats,
LoopX advances the progression after the applied interval has elapsed, until
the max interval. An immediate post-ACK readback remains on the acknowledged
RRULE so repeated reconciliation converges rather than oscillates. When the reset token
changes, the next projected RRULE returns to
`reset_policy.app_automation_initial_rrule`. If the current desired RRULE is already
applied, `recommended_rrule` is omitted and the host update should be skipped.
When that matching readback still needs a reset-token/identity binding,
`ack_needed=true`; run the bound ack directly. Otherwise no scheduler action
is needed.
For CLI payloads, `ack_hint.cli_args` begins with the registry and effective
runtime-root binding used by the originating `should-run` call. Consumers must
preserve that prefix so the ACK cannot split scheduler state between project
and shared registries.
`scheduler-ack` only records the applied host cadence; the next RRULE, if any,
is projected by a future `quota should-run`, not by the ack response.
The payload also includes `execution_obligation`, which is the compatibility
entry point for older workers deciding whether a quiet no-op is allowed.
`heartbeat_recommendation.notify` is only a user-facing notification policy. It
must not be interpreted as an execution gate. `heartbeat_recommendation.
agent_must_attempt` mirrors `execution_obligation.must_attempt_work` as a
single-field shortcut so thin heartbeat prompts can key work obligation off
one boolean instead of parsing notify semantics. If
`autonomous_replan_required`, `heartbeat_recommendation.notify` is `NOTIFY`:
replan turns are machine execution contracts and must not be projected as
`DONT_NOTIFY`, which agents can misread as permission for a quiet no-op. If
`execution_obligation.kind=external_evidence_observation_required`, ordinary
delivery is still blocked, but the worker must perform one read-only
observation or write a compact missing-handle blocker before it may stop. If
`execution_obligation.must_attempt_work=true`, the worker should choose one
bounded segment under `work_lane_contract` when present, otherwise under
`effective_action` / `goal_boundary`, validate it, write durable state/events,
and spend once after delivery even when `notify=DONT_NOTIFY`. A quiet no-op
requires `execution_obligation.must_attempt_work=false` and no blocker-push
notification such as `notify_user_on_open_todo=true`; when both are present,
notify the user and do not spend. Verified `mapped_noop_if_unchanged` remains a
quiet no-op case.
The guard also emits `protocol_action_packet.schema_version =
protocol_action_packet_v0`, a compact rule-only packet for executor and future
LLM-router experiments. It distills the same quota guard into one primary actor,
user/agent action requirement, quiet-noop allowance, execution lane, and a short
`llm=no_api` marker inside a single `summary` string so the hot path stays
within interface budget. The detailed spend policy remains in
`heartbeat_recommendation.spend_policy`. This packet is not a new source of
authority and does not authorize model/API use; it is the deterministic baseline
that an optional Codex/LLM summarizer must beat on payload shrinkage and
user/agent action clarity before direct LLM API wiring is added. When an open
todo uses the common `[P*] short title: details` shape, the packet uses the
short title as the action label so long progress notes do not re-enter the hot
path.
If open user todos coexist with executable agent work, the packet keeps the
primary actor as `agent` but adds `user_action_pending=true` plus a compact
`user_action` label. This preserves the owner-visible blocker without
mislabeling that owner todo as `agent_action`.
When a registry-enabled goal has `control_plane.self_repair.enabled=true`,
`quota should-run` may return `decision=self_repair`,
`self_repair_allowed=true`, `stall_self_repair`, and an `effective_action` such
as `control_plane_health_repair` or `control_plane_projection_repair`. This is
the machine-readable stall-repair contract for short heartbeats: repair the
control-plane projection or write back the concrete blocker, validate, record a
durable event, then spend once. Goals without that registry policy must not get
this lane by default.
When the payload includes `decision_freshness_warning`, the goal may still be
eligible, but sampled reward/gate state for that same goal is stale or has newer
events after it. Executors should not reuse that old decision as authority until
they re-read the current registry, ACTIVE_GOAL_STATE, quota, policy, and run
status at the decision point. This warning is a guardrail for decision reuse,
not a repository rewind or a replacement for `should_run`.
If the payload's `handoff_readiness.post_handoff_outcome_gap_streak` has reached
the `project_asset.execution_profile.outcome_floor.surface_streak_threshold`,
`quota should-run` should also enforce the handoff contract by returning
`should_run=true`, `state=focus_wait`, `blocked_action_scope=delivery_outcome_floor`,
`safe_bypass_allowed=true`, `safe_bypass_kind=outcome_floor_recovery`,
`recovery_delivery_allowed=true`, `effective_action=outcome_floor_recovery`,
`decision=safe_bypass_recovery`, and
`heartbeat_recommendation.recommended_mode=outcome_floor_recovery` when the
floor declares a concrete `must_advance` target. In this shape `should_run`
means there is a Codex-actionable turn, while `normal_delivery_allowed=false`
explains that ordinary delivery is blocked. Executors should treat
`effective_action=outcome_floor_recovery` as recovery permission, spend only
after validated ranker/cross-domain evidence or concrete blocker writeback, and
avoid continuing a surface-only loop or waiting passively.
The status export should apply the same quota guard to `attention_queue.items[]`
and `project_asset.quota`, while preserving `handoff_readiness` run evidence:
`codex_ready` may become false, but `post_handoff_latest_run`,
`post_handoff_recent_runs`, and `post_handoff_outcome_gap_streak` should remain
visible for dashboards and handoff packets.

Review Packet source-of-truth rule:

- the dashboard/operator view owns the human decision;
- the copied Review Packet is a bridge from that decision surface to a local
  operator preview and a target project-agent instruction;
- `loopx review-packet --goal-id <goal-id>` may generate the same
  packet from the status contract for CLI-facing agents, but it is still a
  read-only packaging command;
- when status includes same-goal `decision_freshness_summary` items that require
  rebase, the full Review Packet should render a compact human-visible
  freshness warning before the operator approves or relays work; this warning
  stays out of the minimized handoff-only text so the project agent still
  receives a small current instruction;
- `loopx review-packet --goal-id <goal-id> --handoff-only` is the
  copy-minimal form for an already selected or approved target-agent relay: it
  prints only the `project_agent_handoff` text in markdown output, while JSON
  output returns a minimized handoff payload instead of the full operator
  packet. To keep the hot path compact, handoff-only JSON does not expose a separate
  `handoff_followthrough_summary` prose field; that prose remains available in
  the full Review Packet and embedded handoff text;
- project-agent handoff commands redact local absolute registry/runtime paths
  before they enter `project_agent_command`, `project_agent_handoff`, or
  `handoff_text`;
- project-agent handoff text is an interface-budgeted hot-path artifact: it
  should stay within 16 lines and 1800 characters, include at most one command
  block, and carry only the target goal guard, minimal-context rule, source
  label, optional compact post-handoff delivery scale, optional delivery
  contract, forwarding/execution boundary, command, and stop condition;
- `handoff_delivery_contract` is optional structured guidance derived from the
  current `handoff_readiness` plus `project_asset.execution_profile`, not a
  target-specific hack. When repeated small-scale follow-through reaches the
  profile's `degradation_policy.small_scale_streak_threshold`, packets may set
  `mode=expand_after_repeated_small_delivery` and ask the target agent to run
  one coherent batch at the profile's `minimum_scale` with the declared
  `must_include` surfaces, or report a blocker without spending quota. When
  implementation-shaped runs are still only forecast/runbook/queue/field
  propagation and `post_handoff_outcome_gap_streak` reaches the profile's
  `outcome_floor.surface_streak_threshold`, packets may instead set
  `mode=expand_after_surface_progress_loop` and require the next delivery to
  advance the declared outcome floor, or report a blocker without spend;
- handoff-only output must not carry the full Review Packet, human decision
  section, local operator-gate preview, operator decision payload fields, raw
  `run_history`, or `latest_runs` cold-path evidence;
- the local `operator_gate_dry_run` preview belongs to the user or controller,
  not the target project agent;
- the project-agent command is the after-approval dry-run path for controller
  gates, or the quota guard for connected-delivery Codex goals. Connected
  delivery handoffs may authorize bounded write-scope delivery after
  `should_run=true`; they must still stop for unapproved scopes, production
  actions, destructive git, private material, or surface-only loops.

For controller opt-in packets, the operator question must appear before any
local gate preview, and the local gate preview must appear before any
project-agent command. A dashboard, script, or agent must not infer approval,
reward, write-control, or a real map run from the presence of a copied packet,
review URL, selected `goal_id`, or `agent_command`.

The project-agent section of a Review Packet should be short and operational:
name the current context source, the forwarding condition, the execution
boundary, and the stop condition before showing any command. The context source
rule keeps agent handoffs from bloating: the packet carries only the minimal
current instruction; if the target agent needs more context, it reads the
current active state, status, history, and command output instead of rebuilding
truth from old chats or old packets. For controller opt-in, that means the
section is only forwarded after an explicit human/controller agreement, the
agent only runs the read-only or dry-run project path, and it must stop if it
needs a real approval, write-control, run-history append, production action, or
if the command fails. This keeps the packet easy for target agents to follow
while preserving the dashboard as the human decision surface and the archival
evidence trail as the cold path.

For focus-wait packets, the Review Packet should surface the first open
owner/user todo as the unlock condition, not as ordinary delivery work. The
human section should explain why the project is quiet, who can unblock it, and
which evidence is needed before delivery resumes. The project-agent section
must not present a safe-local delivery path; it should only point to status or
history inspection and tell the target agent to keep `focus_wait` until new
owner evidence, a clean baseline, or external eval changes the state.
Dashboard action packets and first-screen cards should follow the same rule:
label the item as `Focus wait` / owner blocker, show the first open owner/user
todo as the unlock condition, and make the copy affordance status/history-only
rather than an approved handoff or read-only map delivery path.

`status=read_only_project_map` is emitted when the latest compact run came from
`loopx read-only-map`. Dashboard consumers should show it as Codex-ready
work with a map-specific badge or drill-down: the project is connected and has
a read-only map run, but the next useful action still needs a controller or
agent to use that map. Compact run records may include a public-safe
`project_map` object:

```json
{
  "adapter_kind": "read_only_project_map_v0",
  "adapter_status": "connected-read-only",
  "authority_source_count": 1,
  "authority_registry_declared": true,
  "authority_registry_path_exists": true,
  "authority_registry_default_entry_count": 2,
  "authority_registry_default_entries_present": 2,
  "topic_authority_count": 8,
  "authority_registry_conflict_risk": "low",
  "guard_count": 3,
  "sections_found": 4,
  "sections_checked": 7,
  "project_registry_exists": true,
  "goal_state_dir_exists": true,
  "active_state_file_exists": true,
  "files_present": 4,
  "files_checked": 9,
  "residual_risk_count": 1
}
```

The full run payload may also include `residual_risks`, a compact public-safe
list such as `planned_adapter_requires_controller_opt_in` or
`project_local_goal_state_not_detected`. If an optional authority registry is
declared, missing registry files, missing default entries, deprecated sources,
or medium/high conflict risk are reported with stable `authority_registry_*`
labels. Project agents should relay that list directly rather than inventing a
free-form risk summary.

For same-repo multi-goal projects, `project_registry_exists`,
`goal_state_dir_exists`, and `active_state_file_exists` are goal-scoped health
signals. A project can have both `main-control` and `side-bypass` in the same
repo, but each selected `goal_id` should have its own
`.codex/goals/<goal-id>/` directory. If that directory is missing, the map
reports `project_goal_state_dir_not_detected:<goal-id>` and the legacy
`project_local_goal_state_not_detected` risk even when another goal in the same
repo is healthy.

The CLI cleanup path is `loopx archive-runtime --goal-id <goal-id>`. It
defaults to dry-run and requires `--execute` before moving the runtime directory
under `<runtime-root>/archived-goals/`.

## Run History

`run_history` is a compact, public-safe drill-down surface for the dashboard.
It mirrors the compact run index, but strips local artifact paths. UIs should
show artifact availability with `json_exists` and `markdown_exists` instead of
linking directly to local files.

On the `status`, `quota should-run`, and `history` read paths, relative
`common_runtime_root` values, relative `--runtime-root` overrides, and relative
run-index artifact paths are resolved against the project root that owns the
selected registry, not the caller's current working directory. This keeps
those read surfaces stable when they are invoked from an independent worktree.

Goal shape:

```json
{
  "id": "complex-project-main-control",
  "domain": "complex-project-control",
  "status": "active-read-only",
  "lifecycle_phase": "controller_gated",
  "lifecycle_flags": [
    "controller_gated",
    "adapter_inspected"
  ],
  "registry_member": true,
  "legacy_runtime_goal": false,
  "adapter_kind": "complex_project_read_only_map_v0",
  "adapter_status": "connected-read-only",
  "authority_registry": {
    "declared": true,
    "path": "docs/meta/DOC_REGISTRY.yaml",
    "path_exists": true,
    "default_entry_count": 3,
    "default_entries_checked": 3,
    "default_entries_present": 3,
    "topic_authority_count": 8,
    "project_material_count": 6,
    "project_material_repository_count": 2,
    "project_material_owner_review_required_count": 1,
    "project_material_stale_count": 1,
    "project_material_current_authority_count": 1,
    "deprecated_source_count": 0,
    "conflict_risk": "low"
  },
  "quota": {
    "compute": 0.5,
    "window_hours": 24,
    "slot_minutes": 1,
    "allowed_slots": 720,
    "spent_slots": 0,
    "state": "operator_gate",
    "reason": "operator gate blocks gated delivery; safe non-gated steering may continue",
    "blocked_action_scope": "gated_delivery",
    "safe_bypass_allowed": true
  },
  "index_exists": true,
  "raw_index_records": 2,
  "unique_runs": 2,
  "subagent_activity": {
    "source": "run_history",
    "parent_goal_id": "complex-project-main-control",
    "child_count": 2,
    "visible_child_count": 2,
    "completed_count": 1,
    "active_count": 1,
    "quota_spend_slots": 2,
    "items": [
      {
        "run_id": "docs-map-001",
        "goal_id": "docs-map-subagent",
        "parent_run_id": "controller-run-001",
        "spawned_by_goal_id": "complex-project-main-control",
        "agent_role": "explorer",
        "state": "completed",
        "work_scope": [
          "docs/**"
        ],
        "touched_paths": [],
        "touched_path_count": 0,
        "handoff_summary": "Mapped task clusters without editing files.",
        "quota_spend_slots": 1
      }
    ],
    "proxy_note": "compact child-run projection only; parent controller remains the authority for locks, writes, and merge decisions"
  },
  "latest_runs": []
}
```

`authority_registry` on the goal comes from the registry and stays visible even
when the latest run is an operator gate or reward overlay rather than a fresh
project map. Dashboard consumers should translate it into one human-facing line
such as "default entries 3/3, topic 8, materials 6, owner review 1, risk low"
before asking for operator decisions. Material details stay project-local:
public status exposes compact counts for material roles, repository links,
owner-review gaps, stale sources, and current authorities instead of URLs,
repository roots, product configs, or raw review notes.
The Markdown status renderer should expose the same compact context as an
`authority_material` line on attention-queue items, so agent-facing handoffs
see freshness and owner-review pressure without needing internal material
links or source text.

`quota` on the goal comes from the registry and defaults to `compute=1.0` when
not declared. In v0.1, status derives only a compact product state from hard
gates and attention ownership: `eligible`, `focus_wait`, `throttled`,
`waiting`, `operator_gate`, `paused`, or `blocked_health`. It is not a
permission signal and does not replace human reward, operator gates, write
approval, or production-action authorization.

Run shape:

```json
{
  "generated_at": "2026-05-31T21:15:00+00:00",
  "goal_id": "complex-project-main-control",
  "classification": "ready_for_controller_opt_in",
  "lifecycle_phase": "controller_gated",
  "lifecycle_flags": [
    "controller_gated",
    "adapter_inspected"
  ],
  "recommended_action": "ask the target controller to opt into a read-only map before any mutation",
  "health_check": "8/8",
  "run_id": "controller-run-001",
  "subagents": [
    {
      "run_id": "docs-map-001",
      "goal_id": "docs-map-subagent",
      "parent_run_id": "controller-run-001",
      "spawned_by_goal_id": "complex-project-main-control",
      "agent_role": "explorer",
      "state": "completed",
      "work_scope": [
        "docs/**"
      ],
      "touched_path_count": 0,
      "handoff_summary": "Mapped task clusters without editing files.",
      "quota_spend_slots": 1
    }
  ],
  "subagent_count": 1,
  "controller_readiness": {
    "classification": "ready_for_read_only_not_decision",
    "read_only_observer_ready": true,
    "decision_advisor_ready": false,
    "write_controller_ready": false,
    "missing_gates": [
      "human_reward_capture",
      "aligned_eval_decision_evidence"
    ],
    "review_judgment": "safe to connect read-only observation, but not decision advice",
    "next_handoff_condition": "record aligned eval evidence and one human reward event",
    "gates": [
      {
        "id": "durable_goal_context",
        "ok": true,
        "review": "goal state and run history are available"
      },
      {
        "id": "human_reward_capture",
        "ok": false,
        "review": "no operator reward has been recorded yet"
      }
    ]
  },
  "human_reward": {
    "recorded_at": "2026-06-01T00:05:00+00:00",
    "decision": "continue_route",
    "reward": "positive",
    "reason_summary": "operator accepted the route because the comparable metric improved and validation was aligned",
    "follow_up": "promote the route to a longer-window check"
  },
  "json_exists": true,
  "markdown_exists": true
}
```

Optional compact fields such as `active_task_count`, `active_priorities`, and
`cache_check` may appear when an adapter records them. Experiment-controller
adapters may also include compact `controller_readiness` and `human_reward`
summaries.

`lifecycle_phase` is derived by the status layer so the dashboard can separate
state interaction stages from adapter-specific classifications:

- `connected`: the goal is registered with a connected adapter but has no run.
- `mapped`: the latest run is a generic `read_only_project_map`.
- `refreshed`: the latest run is a state-only `state_refreshed` update.
- `adapter_inspected`: a project adapter produced a compact run.
- `reward_judged`: a human reward overlay is attached to the run.
- `operator_approved`: an operator gate was approved and the approved
  `agent_command` may be handed to the target project agent.
- `operator_gated`: an operator gate was rejected or deferred, so the goal stays
  gated.
- `controller_gated`: controller readiness evidence is present, but the goal is
  still missing a gate such as human reward or comparable evidence.
- `controller_ready`: decision-advisor or write-controller readiness is present.
- `planned`, `registered`, and `run_recorded`: fallback phases for goals that
  are not yet connected or have an unclassified run.

`lifecycle_flags` may contain more than one phase. For example, a run can be
both `adapter_inspected` and `reward_judged`, both `adapter_inspected` and
`operator_approved`, or both `adapter_inspected` and `controller_gated`. UIs
should show the primary phase first and use flags as secondary badges.
When a Codex-owned goal carries `lifecycle_phase=focus_wait` or a
`continuation_boundary` flag, quota should surface `state=focus_wait` instead
of `eligible`. This keeps compute quota separate from delivery focus: the goal
can remain healthy and visible while automatic turns wait for new evidence,
owner input, external eval, or a clean baseline.
Quota may also use `state=focus_wait` when post-handoff delivery has repeatedly
missed the declared outcome floor. In that case `blocked_action_scope` should
identify `delivery_outcome_floor`, and the target agent should either return
with outcome-scale evidence or report a blocker without spending another slot.
When the floor declares `must_advance`, the quota payload should split ordinary
delivery from recovery delivery with `normal_delivery_allowed=false`,
`recovery_delivery_allowed=true`, `should_run=true`, and
`effective_action=outcome_floor_recovery`
so dashboard and heartbeat consumers do not mistake the recovery lane for a
quiet skip.

For `controller_readiness`, the status export keeps only controller-stage
booleans, missing gate names, operator-facing review text, next handoff
condition, and compact gate rows with `id`, `ok`, and `review`. For
`human_reward`, the status export keeps only `recorded_at`, `actor_kind`,
`decision`, `reward`, `reason_summary`, and `follow_up`. For `operator_gate`,
the status export keeps only `recorded_at`, `gate`, `decision`, `operator_question`,
`reason_summary`, `follow_up`, and `agent_command`. Operator-gate runs may also
include a compact `operator_gate_resume_contract` with
`version=operator_gate_resume_contract_v0`, `gate_id`, `created_state_ref`,
`latest_state_ref`, `operator_decision`, freshness/precondition checks, rebase
result, resulting action, and validation-after-resume text. This contract is
the public checkpointed-decision surface. Its rebase is scoped to the approval /
resume decision point only; it is not a repo/worktree rollback, restore, or
time-travel mechanism. Richer evidence belongs in private run payloads.

Operator gate decisions answer "may the project agent cross this gate?" and are
separate from reward signals. Use them for approvals such as read-only map
opt-in:

```bash
loopx operator-gate \
  --goal-id complex-project-main-control \
  --decision approve \
  --reason-summary "同意先执行 read-only map opt-in"
```

The dry-run form appends nothing. A real append writes an
`operator_gate_approved`, `operator_gate_rejected`, or
`operator_gate_deferred` compact run. Approved gates are surfaced as
Codex-ready with the approved `agent_command`; rejected/deferred gates stay in
the user/controller lane with the recorded reason. The approved command is not a
time-travel replay of the old checkpoint: at the approval/resume decision point,
the target turn must re-read current registry, `ACTIVE_GOAL_STATE`, quota, repo
dirty/ref snapshot, policy, and run status. That check decides whether the
approved action is still valid now; it must not carry the whole repository state
back to the old gate. Quota spend, eval/experiment launches, production writes,
or external messages belong after the fresh approved resume.

Operators can append `human_reward` with the CLI:

```bash
loopx reward \
  --goal-id example-experiment-goal \
  --actor-kind owner \
  --decision continue_route \
  --reward positive \
  --reason-summary "comparable validation improved and the route is worth extending"
```

The command appends a compact overlay row to the goal's `index.jsonl`. History
loading merges later rows with the same run key, so feedback can be added
without rewriting the original run JSON or Markdown payload.

When the operator feedback is a route, priority, benchmark-protocol, safety, or
operating-rule correction, the overlay may also include a compact lesson:

```bash
loopx reward \
  --goal-id example-experiment-goal \
  --actor-kind owner \
  --decision route_correction \
  --reward mixed \
  --reason-summary "run the driver repair before expanding cases" \
  --lesson-kind route \
  --lesson-summary "Do not expand cases before driver repair is validated" \
  --lesson-avoid "expand cases before driver repair" \
  --lesson-prefer "validate driver repair first"
```

`human_reward.lesson` is a warning/rebase signal, not write-control. Status
exports the compact lesson, and `quota should-run` may emit
`reward_lesson_projection_warning` when the current `recommended_action`
overlaps a recent lesson's `avoid` phrase. Agents should then update the
affected todo or Next Action before continuing.

Both dry-run and append responses include:

```json
{
  "active_state_summary": "dry-run：将记录目标 `example-experiment-goal` ...",
  "project_agent_visibility": {
    "source_of_truth": "run_bound_human_reward_overlay",
    "history_command": "loopx history --goal-id example-experiment-goal --limit 3",
    "active_state_role": "summary_only",
    "review_packet_role": "optional_handoff_only"
  }
}
```

Agents should treat `history_command` as the standard visibility path. Active
state can repeat the summary and next action for context, but it is not the
durable reward store.

When `loopx status` renders Markdown, a latest run with `human_reward`
should expand the compact reward fields under `Run History` and repeat the same
project-agent history lookup. This keeps the dashboard as the operator surface
while making CLI status sufficient for project agents that only need to notice
and inspect a recorded reward.

The Markdown response should also show a short `Write Effect` section near the
top so the operator can see the selected run, whether the overlay was actually
appended or only previewed, whether active-state writeback would happen, and
the one project-agent history lookup.

The CLI can also preview or perform the active-state summary write when the
operator explicitly asks for it:

```bash
loopx reward \
  --goal-id example-experiment-goal \
  --actor-kind owner \
  --decision continue_route \
  --reward positive \
  --reason-summary "comparable validation improved and the route is worth extending" \
  --write-active-state-summary
```

That response includes `active_state_update`, for example:

```json
{
  "active_state_update": {
    "requested": true,
    "section": "Progress Ledger",
    "would_write": false,
    "written": true,
    "already_present": false
  }
}
```

The loopback dashboard write path is opt-in. By default `serve-status` exposes
only `POST /reward/dry-run`; when started with `--enable-reward-write-api` on a
loopback host, it also exposes `POST /reward/append`. The append request must
reuse the `preview_id` returned by the dry-run response, so a changed payload,
changed selected run, or changed raw index count forces the operator to preview
again. The compact browser response does not expose index paths, state file
paths, or raw private evidence.

## Event Ledger Summary

`event_ledger_summary` is an optional dashboard-friendly projection over the
compact run index. It makes the durable-execution contract visible without
requiring a dashboard or heartbeat prompt to read the full run history.

The summary classifies sampled run records into:

- `accounting`: quota and spend rows such as `quota_slot_spent`;
- `decision`: operator gates, resume contracts, deferrals, approvals, and
  `human_reward` overlays;
- `evidence`: eval, metric, CI, deploy, artifact, blocker, failure/done, or
  read-only evidence-poll observations;
- `state`: state refresh and other compact state-projection rows;
- `work`: remaining bounded delivery or implementation progress rows.

The summary reports 24h/7d totals and per-goal counts by event class. It must
not replace append-only run, reward, quota, validation, artifact, blocker, or
evidence events.
Each per-goal row may also include `latest_event_class` and `latest_event_at`,
which are compact routing hints for the dashboard, not replacements for the
latest run record.

## Promotion Readiness Summary

`promotion_readiness_summary` is an optional release-control projection over the
runtime release ledger, with legacy Goal run-history events retained as a
read-compatible fallback only when the runtime ledger has no valid readiness
event. It finds the latest
`canary_promotion_readiness_smoke_group` event and reports whether that evidence
is fresh enough to trust before promoting a live checkout into the default local
release snapshot. New evidence is runtime-scoped because release readiness is
shared by every Goal using that local installation; it does not require or
mutate a project Goal.

The summary reports:

- `freshness_status`: `fresh`, `stale`, `missing`, or `unknown`.
- `freshness_window_hours`: the freshness window, currently 24 hours.
- `is_fresh` and `requires_readiness_run`: compact guards for installers,
  dashboards, and heartbeat jobs.
- `age_seconds` / `age_hours`: evidence age when the event timestamp is
  parseable.
- `json_exists` / `markdown_exists`: whether the latest evidence artifacts still
  exist.
- `dashboard_readiness`: `passed` or `skipped` for runtime-level evidence, so a
  deliberate omission is never indistinguishable from a successful check.

This projection does not promote anything and does not replace the append-only
release artifact under the LoopX runtime root.
`scripts/install-local.sh` consumes the same readiness fact only to print a
non-blocking warning; operators should still run `loopx doctor` or the
canary-promotion readiness smoke for exact local release evidence.

## Decision Freshness Summary

`decision_freshness_summary` is an optional checkpointed-decision projection over
the sampled run history. It exists because a chat thread is not the source of
truth for long-running control decisions: the durable source is the append-only
run history and event ledger. A Codex thread may remember an old approval or
reward, but before spending quota, launching work, or mutating external state it
must rebase that decision point against the latest registry, active state,
quota, policy, and run-status facts.

The summary treats compact `human_reward`, `operator_gate`, operator-gate resume
contracts, and reward/gate-like classifications as checkpointed decisions. For
each sampled decision it reports:

- `freshness_state`: `fresh`, `rebase_required`, or
  `stale_rebase_required`.
- `stale_by_age`: whether the decision is older than the freshness window.
- `newer_event_count_7d` and `newer_event_classes_7d`: newer sampled events for
  the same goal inside the seven-day window.
- `requires_decision_point_rebase`: whether the worker should refresh current
  control-plane state before reusing the old decision.

This is a decision-point rebase helper, not a repository reset or time-travel
mechanism. Newer events mean the worker should reinterpret the old reward or
gate in the current state; they do not roll the project back to the old chat
context. If `status --limit` omits older runs, the summary may miss a stale
decision, so consumers should treat it as an operational warning surface and
drill into the project history for exact replay when needed.
`quota should-run` consumes this projection for the selected goal as
`decision_freshness_warning` whenever a sampled decision has
`requires_decision_point_rebase=true`. That warning is deliberately additive:
it does not flip `should_run`, but it tells the worker that any old reward or
gate it plans to reuse must be rebound to the current control-plane state first.
Dashboard project/detail and share surfaces should render non-empty same-goal
items as a compact Chinese operator warning before approval or relay, explicitly
clarifying that decision-point rebase means rereading current control-plane state,
not rolling the repository or project back to the old chat context.

`quota should-run` also consumes `promotion_readiness_summary` as
`promotion_readiness_warning` when the sampled canary promotion-readiness
evidence is missing, stale, or unknown. The warning is a release-readiness guard
surface, not a scheduling decision: it does not flip `should_run`, but it lets a
heartbeat worker report that the release snapshot should not be promoted until
fresh canary promotion-readiness evidence is written to the shared runtime
release ledger. This keeps release readiness in queryable control-plane
state instead of relying on dashboard prose, `doctor` output, or a chat thread.
The warning message names the writeback command,
`python3 examples/canary/canary-promotion-readiness-smoke.py`. A run with
`--no-write-evidence` validates the canary without refreshing this durable
projection and cannot clear the warning.

## Usage Summary

`usage_summary` is an optional dashboard-friendly proxy derived from the same
compact run history. When a run carries a typed `run_usage_v0` usage row, the
summary aggregates input/output token counts and, when measured, cache tokens,
estimated cost, and wall-time duration for the goal; runs that report nothing
contribute nothing. It remains a proxy, not billing telemetry: it still excludes
raw thread logs, local project paths, private artifact contents, or anything that
would require reading a Codex session transcript. Only aggregate numeric usage is
captured. There is no second usage ledger — run history plus this typed row is
the single source of truth.

Hosts and writers ingest usage through
`loopx.control_plane.quota.usage_collector.ingest_usage_into_run_record`, wired
into `write_reserved_run_artifacts` and into the `refresh-state` run-write
path. The first shipped measurement source is the Codex CLI session rollout:
`loopx refresh-state --usage-codex-session <rollout.jsonl>` reads only the
newest aggregate `token_count` totals, the model id, the session id, and event
timestamps — never prompts, completions, or tool output. The model label is
bound to the context observed before the selected token snapshot; a later
context without new usage cannot relabel that snapshot. A snapshot without a
preceding model context is rejected. This binding does not allocate cumulative
session usage across models. The session must be bound explicitly; there is no
automatic session discovery, because guessing a concurrent session risks
attributing one session's spend to another run. A host that measures usage
itself can instead pass one finished per-run
measurement with `--usage-json`. Without either flag, usage stays unknown.

Cumulative host snapshots are converted to non-negative deltas at that
producer boundary, and the run index append is the single commit point: each
session's delta basis is reconstructed from its own already-booked rows
(telescoping absolute + delta sums per session id), not from a second state
file, so a crash or retry can never leave the basis behind the ledger.
Baselines are per session and each observation is bound to
`usage.source_snapshot_id`, so interleaved sessions never rebase against each
other: a returning session books only its own increment instead of re-booking
its full cumulative total. Basis read and row append happen under a per-goal
usage booking lock, so concurrent refreshes cannot fund two deltas from one
stale basis. Replaying the same snapshot identity with an identical
observation is an idempotent zero delta; the same identity carrying any
different counter or binding label fails closed instead of silently zeroing
real usage. The Codex adapter has one bounded legacy-label exception: an
unmarked same-snapshot baseline can be reconciled only when its old model is
the final post-snapshot context observed by the first durable booking time.
The rollout must provide ordered, timezone-aware timestamps, and every
cumulative metric (including optional-field presence) must still match the
strict collector. Missing or inconsistent evidence remains a conflict. A
context appended after first booking cannot justify a historical correction.

The correction appends a zero delta with the same snapshot ID and corrected
model; it never rewrites old records or totals. New Codex bookings carry a
`codex_usage_binding` record/index field with schema `codex_usage_binding_v1`,
`source_snapshot_id`, and `model`; a correction also records `legacy_model`.
Any existing binding metadata excludes that snapshot from this legacy path.
Subsequent replays use the corrected baseline and ordinary strict validation.
Old records contain no producer-version attestation: this exception proves
consistency with the historical reader's source semantics, not which binary
wrote the row, and is not a general model-conflict migration mechanism.

A new session starts a fresh absolute observation rather than a bogus reset error. Missing optional measurements stay omitted (unknown)
rather than zero-filled. Malformed, negative, non-finite (`NaN`/`Infinity`,
rejected at the typed builder, at the strict-JSON `--usage-json` boundary,
and again at durable serialization, which forbids non-standard JSON
constants), reset, or out-of-order observations fail closed with a typed
`UsageRowError`; they are never clamped or silently dropped, and a failed
usage observation blocks the whole refresh append. Rollout parsing tolerates
only a torn final line (concurrent-write noise); a malformed line with valid
events after it fails closed instead of booking a stale cumulative snapshot.
Quota/attempt accounting rows are not reinterpreted as token or dollar usage.

The typed compact usage row currently reports:

- `schema_version`: must be `run_usage_v0`
- `measurement_kind`: `absolute` or `delta`
- `source_snapshot_id`: stable host/runtime snapshot identity for idempotent
  replay
- `input_tokens` / `output_tokens`: required non-negative whole numbers
- `cache_tokens` / `cost_usd` / `duration_ms`: optional; omitted when unmeasured
- `provider` / `model`: public-safe runtime labels

The summary currently reports:

- `runs_24h` / `runs_7d`: observed compact run records in the current status
  sample.
- `quota_spend_slots_24h` / `quota_spend_slots_7d`: slots from
  `quota_slot_spent` events in that sample, using the same rule as the quota
  spend ledger (`goal_quota_with_spend_ledger`): the event's `event_type`
  decides, a spend is keyed by the run it was recorded against, and a void is
  clamped against the spend it names. A spend whose `quota_event` cannot be
  read contributes no slot, and a void that targets a spend outside the window
  does not reduce that window.
- `automation_run_count_24h` / `automation_run_count_7d`: quota spend events
  whose compact `quota_event.source` is `heartbeat`, `automation`, or `cron`.
  If the compact run index does not retain a source, `quota_slot_spent` is
  counted as an automation/spend proxy rather than dropped.
- `progress_signal_run_count_24h` / `progress_signal_run_count_7d`: compact
  run records that look like actual project or adapter progress rather than
  accounting/bookkeeping. This proxy excludes `quota_slot_spent` and
  `state_refreshed`, so dashboards can spot automation loops that keep spending
  or refreshing state without producing a fresh delivery, validation, mapping,
  blocker, or gate signal.
- `input_tokens_24h` / `input_tokens_7d`, `output_tokens_24h` /
  `output_tokens_7d`, `cache_tokens_24h` / `cache_tokens_7d`: aggregate token
  counts for runs that report a typed `run_usage_v0` block. Metric fields are
  omitted for a window (or optional metric) with no measured sample; a 24h field
  can be absent while the corresponding 7d field remains present.
- `cost_usd_24h` / `cost_usd_7d`: aggregate estimated cost in USD, rounded to
  six decimals, computed by the reporting runtime when measured.
- `duration_ms_24h` / `duration_ms_7d`: aggregate wall-time in milliseconds when
  measured.
- `project_share_24h`: per-goal share of observed 24h runs, rounded to three
  decimals.

The Personal Workspace preserves absent measurements through parsing and
active/stopped Goal merging. Goal details show “Not measured” / “未采集” for
an absent metric; an observed zero remains `0`, `$0.00`, or `0ms`. Token totals
require both measured input and output counts. The compact Goal header shows
only observed metrics, without inferring a cost from token counts.

Because `status --limit` can bound the recent run sample, consumers should
display `sample_run_count` and treat these values as operational signals for
finding busy project lines, not as precise historical accounting.
The markdown status renderer includes the same totals plus the top sampled
goals so heartbeat operators can notice low-progress loops without opening the
full JSON payload.

## Display Model

A first useful UI can be built from the export alone:

- Header: operator actions and selected-action sharing should be above
  auxiliary source controls, metrics, and raw drill-down, because the
  dashboard is a user decision surface rather than an agent CLI mirror.
- Metrics: `ok`, `goal_count`, `run_count`, and contract summary.
- Canonical home: the default dashboard route should render a Chinese-first
  control-plane home over the shared global status source when available. It
  should emphasize project cards, each project's top four todos with per-item
  status, true user todos, agent-priority todos, quota/guard state, and latest
  evidence before raw drill-down. This is a browser presentation over the
  status export, not a new status source.
- Detailed ops view: `?view=ops` may render the older debugging workbench with
  raw queue filters, selected-goal details, reward drafts, and run-history
  panels. The legacy `view=share` value may remain as a compatibility alias
  for the canonical home, but non-ops views should not be treated as separate
  durable modes.
- Usage snapshot: optional `usage_summary` proxy metrics for observed 24h/7d
  runs, quota spend slots, automation run count, progress-signal run count, and
  busiest goals by current sample share.
- Promotion readiness ops panel: optional `promotion_readiness_summary` status
  showing whether the latest canary promotion-readiness evidence is fresh,
  stale, missing, or unknown before a release snapshot is promoted.
- Promotion gate ops panel: optional `promotion_gate` status showing the compact
  `can_promote` / `should_warn` release-promotion decision derived from that
  same readiness event; dashboard code should display it, not recompute it.
- Decision freshness ops panel: optional `decision_freshness_summary` metrics
  for global decision count, stale count, rebase-required count, fresh count,
  and top affected goals. The panel is a routing warning for old reward/gate
  reuse; exact replay and event ordering remain in append-only run history.
- Compute quota summary: goals eligible for the next agent turn, focus-waiting
  goals, throttled goals, waiting goals, paused goals, and operator-gated goals
  should be visible on the first screen once quota fields are present.
  Automation cadence should be treated as execution detail, not the only
  priority signal.
- User action summary: first-screen cards should derive from the same selected
  operator decision and reward-default logic, grouping reward gates, controller
  opt-ins, evidence watches, Codex handoffs, and blocking health items before
  raw goal detail. Cards may show the matching safe CLI path label or command
  plus the reward-draft decision/reward hint, but those are affordances over
  the agent-facing status export, not browser-side writes. The dashboard can
  derive local action-kind filters from these cards, such as reward,
  controller, Codex, evidence, and health, without adding new status fields.
  Persisting that focus in a URL search parameter is dashboard UI state; it
  does not change the status contract or durable goal truth.
- Selected goal detail: the dashboard may persist the selected `goal_id` in
  URL search state so a review link can reopen the same run-history detail.
  This selected-goal state is not part of the status export and must not be
  treated as an approval, reward, or controller signal.
- Review link: the dashboard may copy a browser URL that includes local
  `actionKind`, selected `goalId`, source `statusUrl`, `lane`, `severity`, and
  optional `view=ops` search state. That link is a user review affordance over
  this export; it must not add fields to the status contract or mutate goal
  runtime state.
- Review Packet: the dashboard should expose one canonical copy affordance for
  the selected action card rather than separate link, reply, handoff, and agent
  prompt buttons. The packet may include the review link, Chinese
  agree/disagree/reason/next-step prompt, project-agent instructions, safe local
  path, reward/default hint, and local dry-run preview. For reward actions, the
  project-agent section should point to the run history lookup, not ask the
  target project agent to append or dry-run user reward on the user's behalf.
  For controller opt-in actions, the packet must keep this order: human
  question, user/controller-owned local gate dry-run preview, then
  project-agent dry-run instruction. The dashboard/operator view owns the human
  decision; the project-agent command is only the after-approval dry-run
  execution path. For an approved Codex action carrying `agent_command`, the
  copy affordance should switch to handoff-only content: no human gate wrapper,
  only the target goal guard, forwarding condition, execution boundary, stop
  condition, and command.
  It is still browser UI state and must not be parsed as durable reward,
  approval, controller opt-in, or write-control.
- Goal directory: all `run_history.goals`, grouped mentally by `domain` and
  enriched with matching attention items and lifecycle phase badges when a
  goal needs action.
- User review map: counts for connected, mapped, refreshed,
  adapter-inspected, reward-judged, and controller-ready goals, written as
  operator-facing states rather than raw adapter statuses. Goals with
  controller evidence but missing gates should be shown as controller-gated,
  not controller-ready.
- Primary queue: `attention_queue.items`.
- First-screen action cards: when a queue item carries `operator_question`, show
  that question as the primary operator prompt before `recommended_action`.
  `recommended_action` remains context; `agent_command` is displayed as the safe
  target-agent command only after the operator question has been answered.
  When a Codex-owned queue item has `quota.state=focus_wait`, show it as a
  focus-wait owner blocker even if `waiting_on=codex`: the card should say why
  it is quiet, who or what can unblock it, which evidence is needed, and that
  the copy packet is only for status/history inspection.
- Queue gate hints: show `controller_stage`, `missing_gates`, and
  `next_handoff_condition` directly in queue rows so an operator can see why a
  watched goal is not ready yet without opening the full run payload.
- User lane: items with `waiting_on=user_or_controller` or `controller`.
- Codex lane: items with `waiting_on=codex`.
- Watch lane: items with `waiting_on=external_evidence` or
  `waiting_on=monitor_signal`.
- Dreaming lane/badge: items with
  `project_asset.dreaming_lane_badge.schema_version=dreaming_lane_badge_v0`.
  These items are advisory review surfaces; delivery lanes continue to follow
  quota and current owner/gate routing.
- Health panel: contract `errors`, `warnings`, and `checks`.
- Run detail panel: selected goal from the attention queue, compact
  classifications, authority coverage, controller readiness, health checks,
  reward signals, and artifact availability.
- Reward CLI draft: selected goal plus latest compact run timestamp should be
  enough to generate a local `loopx reward --dry-run` command. Draft
  fields should default from the selected operator decision and missing gates,
  while remaining editable before validation. The dashboard should append
  feedback only when the live loopback status server explicitly exposes the
  reward write API.
- Reward dry-run check: when the dashboard is loaded from a loopback status
  server, it may validate the same draft through `POST /reward/dry-run` and
  display the compact result, including the Chinese active-state summary and
  project-agent history command. The response includes `preview_id`.
- Reward append: when the same loopback server is started with
  `--enable-reward-write-api`, the dashboard may send that exact preview to
  `POST /reward/append`. A successful append writes one run-bound
  `human_reward` overlay, refreshes status, and makes the next project-agent
  automation able to see the feedback through `loopx status` or
  `loopx history`.
- Reward source of truth: durable user reward belongs in a run-bound
  `human_reward` overlay appended through `loopx reward`. Active goal
  state can summarize that such a reward was recorded, and the Review Packet can
  be forwarded to another project agent for immediate coordination through the
  returned history lookup, but neither replaces the compact run overlay as the
  multi-agent reward signal.
- Operator decision: selected goal detail should translate `waiting_on`,
  `severity`, `lifecycle_phase`, `missing_gates`, and `recommended_action`
  into a human stance such as review/authorize, let Codex continue, wait for
  evidence, or fix health first. Raw classifications remain drill-down
  details.
- Safe CLI path: selected goal detail should also show the next safe local
  command class for that stance: status/history inspection, read-only-map or
  refresh-state dry-run, or reward dry-run through the Reward CLI Draft. This
  is a dashboard-to-agent bridge; it must not imply browser-side approval,
  reward append, or write-controller execution.

Browser-side reward append is outside the default status server behavior. If a
local server enables it, it must follow the explicit opt-in boundary in
[dashboard-reward-write-boundary.md](reference/contracts/dashboard-reward-write-boundary.md).

Suggested badge mapping:

- `severity=high`: blocking.
- `severity=action`: needs a decision or bounded work segment.
- `severity=watch`: no immediate action; wait for evidence or a material
  monitor transition.

## Static Dashboard Demo

The repository includes a no-dependency renderer that turns any status JSON
export into a static HTML dashboard:

```bash
loopx --format json status > /tmp/goal-status.json
python3 examples/render-status-dashboard.py /tmp/goal-status.json /tmp/goal-status.html
```

The generated page groups queue items into user/controller, Codex-ready, and
external-evidence lanes. It is a small demo for local inspection and UI
prototyping; it is not the product dashboard.

The official dashboard direction is a React/Vite control-plane app that can
render this same JSON contract with typed routes, filters, tables, charts, and
drill-down pages. See
[dashboard-frontend-selection.md](product/roadmaps/dashboard-frontend-selection.md).

## Adapter Responsibilities

Adapters should write compact run index records that include:

- `generated_at`
- `goal_id`
- `classification`
- `recommended_action`
- `json_path`
- `markdown_path`

Project adapters may add compact public-safe fields such as `health_check`,
`active_task_count`, `active_priorities`, or a compact `human_reward` summary.
Raw logs, prompts, private metrics, workspace paths, and internal document
links belong in private run payloads, not in compact index records.

Benchmark status snapshots may include
`runs[].observable_handle_policy` with
`schema_version=benchmark_observable_handle_policy_v0`. This is an additive,
public-safe lifecycle projection for one-shot benchmark schedulers. Consumers
may use it to decide whether a run should continue polling, unload/disable a
local `launchd` label, or write a precise missing-handle blocker before any
rerun. It must be derived from compact artifacts, pid liveness, and run labels
only; it must not expose raw logs, task text, trajectories, scheduler payloads,
or local paths. Snapshots may also include
`runs[].process_polling.schema_version=benchmark_process_polling_v0` to make
the polling boundary explicit. That object records that polling used the
private pid file and compact artifacts only; it must keep
`process_table_read=false`, `cmdline_read=false`, `argv_read=false`, and
`raw_process_payload_recorded=false` so benchmark task prompts embedded in
worker argv cannot leak into status, rollout logs, chat summaries, or
control-plane projections.

## Boundary

The JSON export is safe to feed to a local dashboard only if the registry ids,
adapter classifications, and recommended actions are sanitized. Before sharing
an export publicly, verify that it contains no:

- local absolute user paths,
- private document links,
- credentials,
- raw production logs,
- internal task ids,
- private metric values.

The public examples under `examples/` are sanitized and can be used for demos.

## Compatibility Rules

- Additive fields are allowed.
- Existing field meanings should remain stable across minor versions.
- Consumers should ignore unknown fields.
- Consumers should handle missing `attention_queue.items` as an empty queue.
- Consumers should handle missing `run_history` as unavailable.
- Consumers should treat `contract.ok=false` as a stronger signal than an empty
  attention queue.
