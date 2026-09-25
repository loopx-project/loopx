---
title: "Codex, Claude Code and agent runtime connectors"
description: "Compare LoopX connectors for Codex, Claude Code, OpenCode, DeepSeek Harness and custom runtimes: wake triggers, state writeback, budgets and authority boundaries."
---

# Runtime Connector Catalog

Status: public-safe v0 catalog for LoopX host/runtime connectors.

LoopX can run beside many execution surfaces without becoming the execution
runtime. The connector catalog names those surfaces in user-facing terms and
maps each one back to the same kernel contracts: registry, active state, todo,
quota, scheduler hints, gates, evidence, and public/private boundary.

The catalog is not a second source of truth. It is a frontstage index over the
host loops that can wake an agent, ask LoopX whether work is allowed, write back
validated state, and expose enough liveness for users and maintainers to reason
about the work.

## Connector Fields

| Field | Meaning |
| --- | --- |
| `id` | Stable connector id used by docs, status projections, and smokes. |
| `surface` | User-visible host surface or runtime family. |
| `execution_mode` | How the host loop runs: visible TUI, app heartbeat, local scheduler, webhook, or bridge. |
| `wake_triggers` | What starts one LoopX-controlled turn. |
| `state_writeback` | The LoopX write path after validated work. |
| `liveness_signal` | Minimal signal that the host loop is alive without copying raw logs. |
| `stop_reset_policy` | How scheduler hints, final checks, or host stop rules apply. |
| `budget_meter` | How the connector maps work to quota or no-spend monitor policy. |
| `human_visibility` | What the user can see without reading private state. |
| `boundary` | What the connector must not copy, infer, or mutate. |
| `smoke_expectation` | Focused public check that protects the connector contract. |

## Initial Catalog

| id | surface | execution_mode | wake_triggers | state_writeback | liveness_signal | stop_reset_policy | budget_meter | human_visibility | boundary | smoke_expectation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `codex_app_heartbeat` | Codex App automation | Scheduled headless app thread | Codex App heartbeat RRULE; `scheduler_hint.reset_policy.reset_token` | `todo` lifecycle, `refresh-state`, then `quota spend-slot` after validation | Heartbeat run plus quota event | Apply `scheduler_hint.app_automation` reset/backoff; cadence-only changes do not spend | Per-goal/per-agent quota slot after validated writeback | Visible thread, heartbeat XML, concrete user todo when required | Generated heartbeat prompt; scoped `--agent-id`; no project-specific prompt branches | Prompt smoke covers scheduler hint, reset token, identity, and no-spend cadence change. |
| `trae_app` | Trae App automation | Scheduled app thread | Trae App heartbeat schedule; `scheduler_hint.reset_policy.reset_token` | `todo` lifecycle, `refresh-state`, then `quota spend-slot` after validation | Heartbeat run plus quota event | Apply the provider-neutral `scheduler_hint.app_automation` cadence packet with `host_surface=trae_app` through Trae `automation_update`; keep non-terminal settled turns active; no Codex packet or local-store fallback | Per-goal/per-agent quota slot after validated writeback | Visible Trae thread and concrete user todo when required | Generated heartbeat prompt; scoped `--agent-id`; ambient `TRAECLI_THREAD_ID`; no Codex local automation-store mutation | Host activation smoke covers distinct runtime identity, automation setup, three-minute initial cadence contract, and non-terminal continuation. |
| `codex_app_ssh_goal` | Codex App connected to a remote workspace over SSH | Visible interactive Goal loop | Host `/goal` continuation; no automation-tool dependency | Same CLI todo/refresh/spend path, with `--source visible-goal` after validation | Visible Goal state plus compact LoopX status | Complete only on terminal no-follow-up; after three unchanged blocked turns, native `update_goal(status=blocked)` stops host continuation while LoopX remains active; user `/goal resume` reactivates it | Quota slot after validated writeback; no spend for gates, waits, final checks, or host blocking | User sees the active Codex task and its concrete gate or next action | Generated body stays within the `/goal` 4000-character limit and never invents `LOOPX_TURN` or calls `automation_update` | Agent-onboard smoke proves exact host selection, typed scheduler context, body budget, bounded quiet stop, and absence of heartbeat-only instructions. |
| `codex_cli_tui` | Codex CLI TUI | Visible interactive terminal loop | User bootstrap, `/goal`, or visible continuation | Same CLI todo/refresh/spend path | TUI transcript plus compact LoopX status | Uses the same native `update_goal(status=blocked)` and `/goal resume` contract after the typed unchanged limit | Quota slot after validated writeback; no spend for blocked/final-check transitions | User sees the active TUI turn | Do not silently switch to hidden headless execution or copy raw transcripts | TUI prompt/bootstrap smoke covers scoped identity and native blocked/resume semantics. |
| `claude_code_loop` | Claude Code loop | Visible local agent loop | Slash command, local loop tick, or host loop continuation | Same CLI todo/refresh/spend path | Loop status plus compact transcript pointer | Final quota/replan check before stop when unchanged limit is configured | Quota slot after validated writeback; no spend for stop/final check | User sees local loop status and response | No private material, credentials, production action, or hidden approval bypass | Loop smoke covers scoped identity, unchanged final check, and stop-without-spend. |
| `opencode_goal_loop` | OpenCode goal mode | Visible OpenCode goal plugin loop gated by LoopX | `/loopx <task>` with the opt-in OpenCode goal bridge installed | Same CLI todo/refresh/spend path through the bridge | Bridge status plus compact LoopX status | Final quota/replan check before loop stop when unchanged limit is configured | Quota slot after validated writeback; no spend for stop/final check | User sees the OpenCode goal turn and bridge status | Bridge install stays opt-in; do not copy raw transcripts, credentials, or local session paths | OpenCode bridge tests and host-mode selector smoke cover scoped identity and catalog parity. |
| `opencode2_goal_worker` | OpenCode 2 goal mode | Persistent out-of-process worker driving a visible OpenCode 2 session over its HTTP API | `loopx opencode2-goal-worker --goal-id <id> --directory <dir>` | Same CLI todo/refresh/spend path through the worker | Worker state file, host poll receipt, and compact LoopX status | Unchanged-poll limit stops the loop with a visible synthetic notice; probe failures retry with bounded backoff | Quota slot after validated writeback; no spend for waits, notices, or final checks | User sees the OpenCode 2 session turns, continuation prompts, and stop/pause notices | Worker state stays under `$XDG_STATE_HOME/loopx/opencode2`; do not copy raw transcripts, credentials, or local session paths | Worker contract tests cover the turn loop, visible stops, lease fencing, and user-message continuation. |
| `pi_goal_loop` | Pi goal mode | Visible Pi goal extension loop gated by LoopX | `/loopx <task>` with the opt-in Pi goal extension installed | Same CLI todo/refresh/spend path through the extension | Extension status plus compact LoopX status | Final quota/replan check before loop stop when unchanged limit is configured | Quota slot after validated writeback; no spend for stop/final check | User sees the Pi goal turn and extension status | Extension install stays opt-in; bindings stay under the project `.loopx/` tree; do not copy raw transcripts, credentials, or local session paths | Pi extension source-contract tests and host-mode selector smoke cover scoped identity and catalog parity. |
| `shell_worker` | Shell, cron, launchd, or service timer | Headless local command | Cron/service/manual shell wakeup | CLI writeback commands from the project checkout | Exit code, run id, and compact status | Obey local `scheduler_hint` backoff/reset; fail closed on missing goal or agent id | Quota slot for delivery; monitor-only polls stay no-spend | Logs or status command, not raw state files | Do not bake local paths, secrets, or project policy into reusable scripts | Command examples use global registry, `--agent-id`, and no-spend monitor behavior. |
| `http_webhook` | HTTP webhook or local daemon | Request-driven bridge | Loopback callback, webhook, or host event | Adapter validates, then emits LoopX todo/gate/evidence events | Request log plus compact status export | Webhooks do not self-poll; scheduler hints are advisory unless a scheduler owns the retry | Quota spend only after accepted writeback | Dashboard/status feed | Loopback by default; write endpoints require explicit dry-run/preview and CLI-equivalent fallback | Loopback smoke rejects remote status/write authority and proves preview-gated writes. |
| `worker_bridge` | Worker bridge | External executor, task container, or remote worker bridge | Worker event, bridge message, or runner sidecar | Bridge emits compact public-safe state, todo, or evidence | Worker heartbeat/status and compact counter trace | Host-specific stop/reset maps back to scheduler hints and outcome policy | Quota event for accepted work; bridge evidence grants no task-score authority | Dashboard/frontstage projection and compact evidence timeline | Strip raw logs, local paths, private traces, task text, and credentials | Worker bridge install/status smoke proves source mount, counter trace, and private-boundary stripping. |
| `computer_use_runtime` | Browser, desktop, or app automation runtime (a provider/runtime execution surface, not a LoopX product capability or a `value-connectors` connector) | Visible or replayable UI execution surface | Bounded action request from an owning capability, host replay event, or user takeover handoff | Compact typed receipt (facts only) handed back to the owning capability's domain-local reducer, which proposes the LoopX gate/evidence writeback for Kernel validation | Host-owned replay/screenshot pointer plus compact receipt fields | Stop at unknown modal, privacy ambiguity, or final external-write action; scheduler hints still come from LoopX quota | Quota spend only after the owning capability's reducer proposal is accepted by the Kernel; readiness/profile checks stay no-spend | Review card with intended action, forbidden effect classes, evidence handle, and takeover path | Do not copy credentials, cookies, raw screenshots, private UI bodies, or perform sends/purchases/production mutations without exact gate; a provider must never author the Kernel writeback itself | Synthetic runtime-profile/action-request/receipt smokes prove gate-before-write, raw-evidence stripping, and rejection of provider-authored writeback fields. |
| `deepseek_harness_loop` | DeepSeek Harness | Isolated headless bounded execution through LoopX Turn | `loopx turn run-once` with `loopx.dsh_goal_mode` (compat: `scripts/dsh_turn_host_adapter.py`) | Same CLI todo/refresh/spend path after validated dsh result | Compact Turn receipt plus local dsh session pointer | Outer controller obeys scheduler hints; preview/failed validation stays no-spend | Quota slot only after validated writeback | Operator sees typed result, validator receipt, and next preview command | Do not copy dsh JSONL sessions, raw transcripts, credentials, local paths, or model tool logs | Adapter smoke, fake-dsh e2e, and real-dsh e2e prove typed result shaping, real dsh runtime startup, writeback, and replay idempotence. |
| `loopx_turn` | LoopX Turn host adapter | Isolated headless bounded execution | `loopx host-mode-plan` selects the mode, then `loopx turn plan` previews one typed decision | `loopx turn run-once --execute` writes back only after independent validation | Compact Turn receipt and scheduler execution context | Outer controller obeys scheduler hints; cadence-only/preview work stays no-spend | Quota slot only after validated writeback | Operator sees selected host, execution mode, validator requirement, and next preview command | Do not publish opaque session handles, raw transcripts, local paths, credentials, or host-local logs | Host-mode-plan smoke proves host-mode selection, scoped identity, Turn mapping, and visible/headless/hybrid handoff readiness. |

### Reusable shell_worker reference

`scripts/external_scheduler_worker.py` is a scheduler-hint-aware `shell_worker`
for generic visible CLI loops (for example TraeX CLI). Each tick runs
`quota should-run --include-detail scheduler`, projects a one-line public-safe
status (`waiting`/`should_run`/`terminal`, cadence class, next check minutes,
unchanged count), and sleeps per the `local_scheduler` progression ladder. It
tracks the consecutive-unchanged index in a small state file and resets it when
`scheduler_hint.reset_policy.reset_token` changes. It is observe-only by
default; pass `--wake-cmd` only to trigger a bounded headless turn (for example
`loopx turn run-once ... --execute`). It cannot type into a visible TUI, so it
does not replace the interactive host loop. A launchd template ships at
`examples/external-scheduler-worker.launchd.plist` and the contract is guarded
by `examples/external-scheduler-worker-smoke.py`. Producer-generated bounded-wait
scheduler hints have a thin consumer-parity contract with Pi, including the
final quota/replan recheck triggered by the third unchanged poll; it is guarded
by `tests/test_host_loop_runtime_parity.py` and the Pi runtime tests in
`tests/pi_goal_loop_runtime.test.mjs`.

## External Tool Extension Candidates

An MCP server can play two different roles around LoopX:

- a **LoopX host adapter** exposes LoopX lifecycle reads and controlled writes
  to an MCP-capable host;
- an **external tool extension** exposes another product's tools to the host and
  remains a replaceable provider behind a LoopX outcome capability.

The entries below are discovery notes, not availability claims. Cataloging an
extension does not add a LoopX feature, advertise an available capability, or
grant credentials, network access, private reads, or external-write authority.

| id | upstream surface | status | possible LoopX binding | boundary before integration | promotion evidence |
| --- | --- | --- | --- | --- | --- |
| `official_xmcp` | X Developer Platform [XMCP](https://github.com/xdevplatform/xmcp) and [official MCP documentation](https://docs.x.com/tools/mcp) | `catalogued_not_integrated`; no LoopX install check or live qualification | Optional MCP provider for the existing `content-ops` / `social_browser_x` outcome path; not a new capability and not a replacement control plane | Host owns OAuth material and X API cost/rate limits. Start with an explicit read-only tool allowlist. Posting, replies, likes, follows, DMs, account changes, paid queries, and private expansion require exact LoopX gates. Do not project raw posts, timelines, DMs, tokens, or MCP payloads into public state. | Provider-neutral operation map; credential-free install/readiness check; metadata-only public-read packet; exact write/private/cost gate plan; compact receipt; focused contract smoke. Live X E2E is a later owner-authorized qualification, not required for catalog status. |

XMCP is an official local MCP server that derives more than 200 tools from the
X API OpenAPI specification at startup. Its upstream allowlist is therefore a
security and cost boundary, not only an output-budget optimization. A LoopX
adapter should expose a small operation profile such as public account lookup
and bounded recent search instead of forwarding the complete generated tool
surface. Streaming and webhook endpoints are outside XMCP's request/response
surface; durable monitoring still needs a separate host scheduler or event
connector governed by normal LoopX monitor contracts.

X also hosts a documentation-only MCP at `https://docs.x.com/mcp`. That server
may help an implementation agent inspect current X API documentation, but it
does not provide social-source evidence and must not be reported as
`social_browser_x` runtime readiness.

Promote an extension candidate to a capability path only when the user outcome,
provider-neutral packet, CLI entrypoint, gate model, and focused smoke are all
stable. Until then, the extension remains interchangeable infrastructure behind
an existing capability.

## Projection Rules

- LoopX kernel objects remain authoritative: registry, active goal state, todo,
  run history, quota ledger, gates, and evidence pointers.
- A connector may project host facts into status or dashboard cards, but it must
  not store raw transcripts, raw logs, credentials, local absolute paths, or
  private artifacts in public state.
- Every delivery turn starts with `quota should-run` scoped by `goal_id` and
  registered `agent_id`.
- Cadence updates, reset-token handling, final quota checks, loop exits, and
  monitor-only polls do not spend delivery quota.
- User gates must surface concrete user todos or questions. If the payload is
  missing, the connector reports a projection bug instead of silently waiting.
- Validated delivery ends with durable writeback before quota spend:
  todo/state/evidence update, `refresh-state`, and one quota spend event.

## Smoke Expectations

Connector smokes should stay narrow. They protect reusable contracts, not one
maintainer's local automation:

- prompt and bootstrap smokes cover scoped identity, `scheduler_hint`, reset
  policy, and no-spend cadence or final-check behavior;
- local status/server smokes cover loopback-only defaults, read-only browser
  projection, and explicit dry-run/preview before writes;
- bridge smokes cover compact writeback payloads, liveness counters, and
  private-boundary stripping;
- todo/writeback smokes cover the validated-work sequence and prove
  monitor-only or stop-only paths do not spend quota.

## Experimental planner-worker mode

Planner-worker is an **opt-in experimental** slice, not a catalog connector and
not a resident scheduler. One call plans, executes at most one worker step, runs
caller-approved validation, and returns a typed receipt. Model routes stay
explicit; cost remains incomplete until pricing is supplied; live providers
(such as TraeX) stay optional.

Operator guide (provider-neutral fake runtime):
[Experimental planner-worker mode](../guides/planner-worker-experimental.md).

Public checks:

```bash
python3 examples/experiments/planner_worker/contract-smoke.py
python3 examples/experiments/planner_worker/runtime-smoke.py
```

## Related Contracts

- [Host mode plan v0](../reference/protocols/host-mode-plan-v0.md)
- [LoopX Turn v0](../reference/protocols/loopx-turn-v0.md)
- [Heartbeat automation prompt](../heartbeat-automation-prompt.md)
- [Host integration surface v0](../reference/protocols/host-integration-surface-v0.md)
- [Computer-use runtime v0](../reference/protocols/computer-use-runtime-v0.md)
- [Value connectors](../../loopx/capabilities/value_connectors/README.md)
- [Session runtime to LoopX projection v0](../reference/protocols/session-runtime-loopx-projection-v0.md)
- [Worker bridge install contract](worker-bridge-install-contract.md)
- [Quota allocation](../quota-allocation.md)
- [Experimental planner-worker mode](../guides/planner-worker-experimental.md)
