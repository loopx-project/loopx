# Host Integration Plugin Plan v0

`host_integration_plugin_plan_v0` describes the product path from today's
skill-level LoopX slash-command fallback to a host-owned command registry
plugin. It is a planning contract, not a shipped plugin manifest.

The goal is to let a host such as Codex App recognize `/loopx` commands,
install or refresh the thin heartbeat body, apply `scheduler_hint`, and protect
private runtime data while keeping LoopX CLI as the source of truth.

This plan composes existing contracts:

- [`codex_app_host_command_registry_v0`](codex-app-host-command-registry-v0.md)
  for `/loopx`, `/loopx <goal text>`, and `/loopx-global-*` parsing.
- [`host_integration_surface_v0`](host-integration-surface-v0.md) for lifecycle
  reads, controlled writes, CLI fallback, and public/private boundaries.
- [`session_runtime_loopx_projection_v0`](session-runtime-loopx-projection-v0.md)
  for compact runtime projections without raw transcripts.

## Non Goals

- Do not replace the CLI or make a host-specific state machine authoritative.
- Do not store raw transcripts, raw tool output, credentials, billing data,
  local absolute paths, or private project material in LoopX public state.
- Do not treat a chat slash command as approval for destructive git,
  production actions, private-material reads, external publication, or reward
  writes.
- Do not make hidden headless execution the default for visible TUI workflows.

## Plugin Capability Set

The eventual host plugin should be small and explicit:

| Capability | Host Responsibility | LoopX Source Of Truth |
| --- | --- | --- |
| Command registry | Parse `/loopx`, `/loopx <goal text>`, `/loopx-global-*`, and legacy aliases before ordinary chat. | `loopx slash-commands`, `bootstrap-command-pack`, global manager commands. |
| Project identity | Resolve the workspace root, public-safe root label, goal id, and registered agent id. | Registry goal entry and `quota should-run` agent identity checks. |
| Lifecycle reads | Surface status, quota, review packet, and command-pack output as compact host packets. | CLI JSON from `status`, `quota should-run`, `review-packet`, and `bootstrap-command-pack`. |
| Controlled writes | Offer only CLI-equivalent todo/gate/reward/refresh/spend operations, with dry-run when required. | LoopX CLI commands and active-state/event ledger writes. |
| Automation install | Create or refresh the host heartbeat using `heartbeat-prompt --thin` and scoped agent identity. | Generated heartbeat prompt and registry coordination fields. |
| Scheduler adapter | Apply `scheduler_hint.app_automation.recommended_rrule` through `automation_update` only when `stateful_backoff.apply_needed=true`, then run `app_automation.ack_hint.cli_args`; when only `ack_needed=true`, skip the host write and run the bound ack directly. | `quota should-run.scheduler_hint`, `quota scheduler-ack-current`. |
| Privacy guard | Redact local paths and reject raw transcript/session-file/credential payloads. | Public/private boundary plus host projection boundary checks. |

## Phased Path

### Phase 0: Skill Fallback

Agents recognize `/loopx` and `/loopx <goal text>` through the `loopx-project`
skill and call `loopx bootstrap-command-pack`. This is good enough for early
testing, but it is prompt-level behavior and can be polluted by conversation
history.

Exit criteria:

- `/loopx` stays read/status-first.
- `/loopx <goal text>` produces an ordered plan before writing todos.
- The fallback always shows the CLI command that a host plugin should call.

### Phase 1: Host Command Alias

The host parses the slash command and passes a structured handoff packet to the
agent or directly to the CLI. The packet contains command kind, goal text,
public-safe project label, optional goal id, registered agent id, authority
flags, and CLI fallback. It does not include local absolute paths in public
output.

Exit criteria:

- `/loopx-global-summary`, `/loopx-global-gates`, `/loopx-global-todos`, and
  `/loopx-global-risks` are read-only global manager commands.
- Unknown `/loopx-*` commands fail closed with `loopx slash-commands` help.
- The host still falls back to the CLI when parsing or command execution is
  unavailable.

### Phase 2: Heartbeat Installer

After a project is connected, the host can offer to install or refresh the
recurring LoopX heartbeat. It should generate the thin scoped task body:

```bash
loopx heartbeat-prompt --thin --goal-id <goal-id> \
  --agent-id <registered-agent-id> \
  --agent-scope "<public-safe agent scope>"
```

The plugin should not hand-copy project-specific policy into the automation.
It should store only host-owned scheduling metadata, such as the current
`reset_token`, last applied RRULE, and unchanged-poll state.

With JSON output, this command returns the versioned
`heartbeat_agent_input_v1` projection. Host adapters consume `goal_id`, the
optional `agent_id`, and `task_body`; they must not depend on generator paths,
duplicate lifecycle commands, or diagnostic metadata. Those details remain
available through human-readable Markdown output and non-thin generator modes.

Exit criteria:

- Missing agent identity fails closed when the goal has registered agents.
- Updating a heartbeat body does not spend quota.
- The host can show the generated body or a compact install packet before
  writing automation settings.

### Phase 3: Scheduler Hint Adapter

The host applies `quota should-run.scheduler_hint` after each heartbeat result:

- `run_now` restores or keeps active cadence.
- wait/backoff states expose `app_automation.recommended_rrule` only when host update
  work is needed.
- `app_automation.stateful_backoff.apply_needed=true` means call `automation_update`
  for that RRULE; after success, run `app_automation.ack_hint.cli_args` so LoopX
  persists reset token, identity signature,
  progression index, and
  last applied RRULE.
- `apply_needed=false` means the desired RRULE is already applied; skip the host
  update. If `ack_needed=true`, run the bound `ack_hint.cli_args` directly so
  LoopX persists the matching host readback; otherwise no scheduler action is
  needed.
- Codex CLI TUI and Claude Code loops run the final quota/replan check before
  self-stop.

Cadence-only updates, reset-to-initial changes, final checks, and self-stop
decisions do not spend quota. Delivery turns spend only after validation and
durable writeback.

A host ACK is separate from delivery settlement. Completing or replaying a
settled delivery does not by itself suppress an uncommitted ACK from its still
current heartbeat receipt. A newer Turn supersedes the older receipt for host
writeback: reject the old ACK before writing scheduler state, then use the new
Turn's emitted command. The `scheduler-ack-current` command returns the native
`loopx_scheduler_host_followup_result_v0` projection with mode `scheduler-ack`.
With a current receipt, exact retries replay the scheduler commit without
another state write or quota debit; they never reconstruct authority from the live work frontier.

Host ACK 与交付结算分开：交付已结算时，仍是最新的 heartbeat receipt 可提交尚未
写入的 ACK；较新 Turn 会使旧 ACK 失效，必须先拒绝旧写入，再使用新 Turn 的命令。
精确重试只重放 scheduler commit，不再次写入或扣额，也不从当前工作 frontier 重建权限。

### Phase 4: Controlled Write Tools

The plugin may expose controlled writes only after the CLI-equivalent command
and preview path are documented. Todo lifecycle and state refresh can be
available earlier; gate decisions, human reward, lease writes, production
actions, and browser/frontstage-triggered writes require stricter preview and
approval semantics.

Exit criteria:

- Each write advertises its CLI fallback.
- Missing authority returns a structured blocker.
- Host approval does not impersonate user reward or controller approval.

## Acceptance Matrix

| Scenario | Required Result |
| --- | --- |
| `/loopx` in a connected project | Host returns a read/status-first command pack and does not write state. |
| `/loopx fix issue triage` | Host preserves goal text, runs bootstrap command pack, writes ordered todos only through explicit goal-start flow. |
| `/loopx-global-summary` | Host returns a read-only global digest and cannot mutate project state. |
| Unknown `/loopx-debug-me` | Host fails closed with `loopx slash-commands` help. |
| Missing registered agent id | Heartbeat install/refresh fails closed for coordinated goals. |
| Scheduler reset token changed | Host restores initial RRULE and clears unchanged-poll state without spending quota. |
| Raw transcript offered to plugin | Plugin rejects or redacts the payload and records no raw transcript in public state. |
| CLI unavailable | Plugin reports install/doctor blocker instead of inventing a host-only state transition. |

## Minimal Public Fixture Shape

```json
{
  "schema_version": "host_integration_plugin_plan_v0",
  "host_kind": "codex_app",
  "command_registry": "codex_app_host_command_registry_v0",
  "automation": {
    "heartbeat_prompt_mode": "thin",
    "requires_agent_identity": true,
    "scheduler_hint_source": "quota_should_run"
  },
  "privacy": {
    "raw_transcripts_accepted": false,
    "credentials_accepted": false,
    "public_local_paths_allowed": false
  },
  "fallbacks": ["loopx slash-commands", "loopx doctor", "loopx bootstrap-command-pack"]
}
```

## Open Implementation Questions

- Which host API owns command registry installation and command palette labels?
- Should automation install be an explicit host action, a LoopX CLI helper, or
  both?
- How should the host expose `scheduler_hint` state so the user can understand
  backoff and reset without reading JSON?
- What is the smallest tool surface for controlled writes that remains useful
  without creating a second LoopX runtime?
