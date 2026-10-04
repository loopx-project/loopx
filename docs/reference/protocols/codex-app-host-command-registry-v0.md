# codex_app_host_command_registry_v0

`codex_app_host_command_registry_v0` defines how a host such as Codex App
should recognize LoopX slash commands before they become ordinary agent chat.
The host owns parsing, project-root resolution, permission framing, and the
structured handoff packet. LoopX CLI remains the source of truth.

This contract sits above:

- [`loopx_goal_command_v0`](loopx-goal-command-v0.md) for the project-local
  `/loopx` status entry and `/loopx <goal text>` start entry.
- [`global_manager_command_v0`](global-manager-command-v0.md) for read-only
  `/loopx-global-*` manager commands.
- [`pr_review_command_v0`](../../../loopx/capabilities/pr_review_queue/README.md) for the `/loopx-pr-review`
  review queue command.
- [`host_integration_surface_v0`](host-integration-surface-v0.md) for general
  host lifecycle reads and controlled writes.

It is not a replacement for the CLI, a hidden automation runner, or a broad
natural-language router.

## Command Registry

The host registry should expose this minimal command set:

| Command | Canonical target | Default authority |
| --- | --- | --- |
| `/loopx` | `loopx bootstrap-command-pack --project .` | Read/status-first. |
| `/loopx <goal text>` | `loopx start-goal --guided --project . --goal-text "<goal text>"` | Explicit project-local start intent; must activate or gate the host loop after todo writeback. |
| `/loopx-global-summary` | `global_manager_command_v0` summary request | Read-only global control-plane digest. |
| `/loopx-global-gates` | `global_manager_command_v0` gates request | Read-only gate inbox. |
| `/loopx-global-todos` | `global_manager_command_v0` todos request | Read-only work queue view. |
| `/loopx-global-risks` | `global_manager_command_v0` risks request | Read-only risk view. |
| `/loopx-pr-review` | `pr_review_command_v0` review request | Must run the PR review CLI first; cannot be answered as a generic chat summary. |

Legacy `/loop-global-*` forms may be accepted during migration, but the host
must canonicalize packets, help, and user-visible labels to `/loopx-global-*`.
Do not add extra summary aliases; `/loopx-global-summary` is the canonical
global progress digest.

Example registry entry:

```json
{
  "schema_version": "codex_app_host_command_registry_v0",
  "host_kind": "codex_app",
  "commands": [
    {
      "command": "/loopx",
      "kind": "project_bootstrap_preview",
      "protocol": "loopx_goal_command_v0",
      "cli_baseline": "loopx bootstrap-command-pack --project .",
      "mutation_policy": "read_first"
    },
    {
      "command": "/loopx <goal text>",
      "kind": "project_task_start",
      "protocol": "loopx_goal_command_v0",
      "cli_baseline": "loopx start-goal --guided --project . --goal-text \"<goal text>\"",
      "mutation_policy": "explicit_goal_start"
    },
    {
      "command": "/loopx-global-summary",
      "kind": "global_manager_summary",
      "protocol": "global_manager_command_v0",
      "legacy_aliases": ["/loop-global-summary"],
      "mutation_policy": "read_only"
    },
    {
      "command": "/loopx-pr-review",
      "kind": "repo_pr_review",
      "protocol": "pr_review_command_v0",
      "cli_baseline": "loopx pr-review",
      "mutation_policy": "must_run_cli_first"
    }
  ],
  "unknown_command_policy": "fail_closed_with_slash_help"
}
```

Hosts that do not know their exact runtime type should first call
`loopx agent-onboard --list-agent-types`, then pass a canonical value such as
`codex-app`, `codex-cli`, `opencode`, or `claude-code`. Ambiguous values such as `codex`
are invalid because Codex App heartbeat automation and Codex CLI `/goal` have
different activation procedures.

## Host Parse Rules

The host should parse LoopX slash commands before the agent prompt sees the
message:

1. Match only an exact command token at the beginning of the visible user
   message.
2. Canonicalize legacy `/loop-global-*` aliases to `/loopx-global-*`.
3. Treat `/loopx` with no trailing text as bootstrap/status preview.
4. Treat text after `/loopx` as explicit task text. Preserve the exact
   user task text in the handoff packet, but quote it safely for CLI display.
5. Route `/loopx-pr-review` to the PR review command contract. Do not send it
   through the project bootstrap command.
6. Fail closed for unknown `/loopx-*` commands and return `loopx slash-commands`
   help instead of falling through to ordinary chat.

Skill-level recognition may remain a fallback, but the preferred product path
is host parsing. Prompt-only recognition is useful for early testing but can be
polluted by conversation history and should not be the long-term authority.

## Project Root And Agent Identity

Project-local commands require a resolved project root. The host may use the
current workspace, selected file, or explicit project parameter, but it must not
scan unrelated home directories or guess from private paths.

Required fields:

| Field | Rule |
| --- | --- |
| `project_root` | Absolute local root for CLI execution; omit from public packets. |
| `project_root_label` | Public-safe label such as repo name or `current workspace`. |
| `goal_id` | Existing runtime state id field; keep the field name for CLI compatibility, but present it to users as the active state id. |
| `agent_id` | Registered LoopX agent id when the host is acting for an agent. |
| `thread_id` | Optional stable opaque host-thread token used to resolve a durable thread-to-agent binding. |
| `host_surface` | `chat_box`, `command_palette`, `codex_cli_tui`, or another compact host label. |

If the host cannot resolve a project root, `/loopx` and `/loopx <goal text>`
must produce a setup/help packet, not state writes. Global commands may still
run against the shared global registry when available.

## Handoff Packet

After parsing, the host hands the agent or CLI a compact packet:

```json
{
  "schema_version": "codex_app_host_command_handoff_v0",
  "command": "/loopx",
  "raw_command": "/loopx design an issue-fix workflow",
  "canonical_command": "/loopx <goal text>",
  "task_text": "design an issue-fix workflow",
  "host_surface": "chat_box",
  "project_root_label": "current workspace",
  "goal_id": "loopx-meta",
  "agent_id": "codex-product-capability",
  "thread_id": "opaque-host-thread-123",
  "protocol": "loopx_goal_command_v0",
  "cli_preview": "loopx start-goal --guided --project . --goal-text \"<goal text>\"",
  "authority": {
    "read_allowed": true,
    "project_local_write_allowed": true,
    "global_control_write_allowed": false,
    "production_action_allowed": false
  },
  "next_step": "run_guided_start_preview_then_plan_before_todo_write"
}
```

Codex App CLI reads `CODEX_THREAD_ID` when `--thread-id` is omitted, including
`codex-app-ssh` remote sessions. A stable thread ID with no binding returns an
identity gate that requires selecting an existing lane when registered agents
exist; fresh registration is the default only for a goal with no registered
lanes or explicit `--new-peer`. Selecting an existing lane is an explicit
takeover choice. The selected fresh or existing identity must be persisted with
`loopx bind-agent-thread --execute`, and its source/global readback must verify
before Todo writeback. Later `/loopx` calls in the same
`(host_surface, goal_id, thread_id)` reuse that agent ID and carry it through
start, heartbeat, quota, refresh-state, and Todo commands. If the host cannot
supply a stable thread ID, callers must keep using explicit `--agent-id` or
explicit new-session intent with `--new-peer`; the identity gate remains
fail-closed.
Thread IDs are opaque public-safe tokens only; raw transcripts, credentials, and
local paths are never persisted.

### Deep-link task rendezvous

LoopX accepts a task link copied from Codex App. The
[Codex App command reference](https://learn.chatgpt.com/docs/reference/commands)
documents `codex://threads/<thread-id>` as the canonical form for opening an
existing local task.
LoopX treats that link as a host-session locator for review, handoff, and
coordination, not as a second agent identity or an authority grant. A receiving
Codex task can confirm the target's existing project-local LoopX binding without
changing it:

```bash
loopx resolve-agent-thread \
  --thread-link 'codex://threads/<thread-id>'
```

A deep link does not encode the task's execution surface. Without an explicit
`--host-surface`, LoopX searches only the Codex host family (`codex-app`,
`codex-app-ssh`, `codex-ide-plugin`, and `codex-cli-tui`) and reports the
matched surface. The caller may pass one of those surfaces to narrow lookup. A
`bound` result names exactly one `goal_id` and `agent_id`. A `missing` result
means only that the link parsed successfully; it does not authorize takeover or
prove that the target task is connected to this LoopX project. `ambiguous` and
invalid results fail closed. The command stores nothing and returns the parsed
locator with `authority=locator_only`. The sender must still register and bind
its own agent lane through the normal guided-start flow, and both tasks must
state their responsibilities explicitly. The deep link does not share chat
context, permissions, credentials, workspace access, leases, or write scope.
Concurrent writers still need separate worktrees or equivalent isolation.
After a unique binding is confirmed, a host that exposes read-only task
inspection may use the parsed `thread_id` to inspect or cite that task. Tool
availability and access are checked separately; if inspection is unavailable,
the sender must relay the required context explicitly.

中文：在目标 Codex 任务菜单中选择“复制 -> 复制深度链接”，把
`codex://threads/<thread-id>` 连同双方分工发给另一个任务。接收方先运行
上面的只读命令；只有返回 `bound` 且 `goal_id`、`agent_id` 与预期一致时，
才把它视为已确认的 LoopX 会话绑定。`missing` 只表示链接格式有效但尚无本
项目绑定。深度链接只负责定位，不同步权限、目录、对话上下文、lease 或写
入范围；并行修改代码仍需独立 worktree。

For same-project context sharing, the parsed locator also returns a
provider-neutral `context_scope_ref`. An explicitly enabled Decision Context
extension may use that scope as a one-off `recall-context` argument for bounded
advisory recall without modifying its persistent profile. The bundled
`loopx-obelisk` package is one optional provider; another harness adapter may
implement the same protocol. Provider output remains transient and fail-open.
Authoritative alignment outranks chat, and durable conclusions remain owned by
Goal Todos, evidence events, registered material, or governed amendments.

The packet may be rendered to the agent prompt, passed to a tool call, or used
to run the CLI directly. It must not contain raw transcripts, credentials,
private document bodies, or local absolute paths in user-visible output.

`loopx start-goal --guided` defaults to a compact command-pack projection. The
hot path keeps the ordered transaction, planning contract, safety contract,
goal and agent identity, action commands, host activation contract, and an
executable cold-path command. It does not nest the complete bootstrap message,
slash-command discovery catalog, or repeated connection and next-step
diagnostics a second time. Consumers that genuinely need those lower-level
fields can rerun the advertised command or pass
`--include-command-pack-detail`. Both modes must produce the same host-action
projection before a compact default is promoted.

`start-goal --project <path>` keeps that exact project route. In particular, a
fresh linked worktree must not silently inherit a goal registered by another
worktree that shares its Git common directory. The lower-level
`bootstrap-command-pack` remains allowed to resolve a linked worktree to its
canonical registered source when inspecting or repairing an existing
connection.

`start-goal` does not guess among Codex App automation, Codex App over SSH, the
Codex IDE plugin, Codex CLI TUI, or OpenCode. Callers should pass
`--host-surface codex-app`, `codex-app-ssh`, `codex-ide-plugin`,
`codex-cli-tui`, or `opencode` for the exact current host.
`codex-app-ssh` means the desktop app is attached to a remote workspace and
cannot expose its automation tools, so the visible `/goal` owns continuation.
`codex-ide-plugin` means the installed IDE plugin host, not any Codex session
used alongside an editor. The legacy `codex-ide` value remains accepted as a
compatibility alias but is not offered by the selection gate. If the option is
omitted, the command returns a read-only `host_surface_selection` gate with
exact rerun commands; it must not connect a project, write todos, activate a
host, or spend quota.

## Permission Boundary

Host command parsing does not grant new LoopX authority. It only converts
visible user intent into the existing CLI lifecycle:

- `/loopx` can read status and preview command packs. It stops before writes
  that need confirmation.
- `/loopx <goal text>` is explicit intent to start project-local work: plan
  first, write ordered todos, refresh state, activate the correct host loop
  when missing/stale, run `quota should-run`, and execute only when the guard
  allows.
- Host loop activation is runtime-specific: Codex App uses heartbeat
  automation, Codex App over SSH and Codex CLI use visible
  `/goal <task_body>`, Claude Code uses native `/loop`, and custom agents must
  declare their loop driver through `loopx agent-onboard`.
- `/loopx-global-*` commands are read-only and must not approve gates, add
  todos, spend quota, merge PRs, publish externally, or pause/resume loops.
- `/loopx-pr-review` must run the PR review CLI first and then review PRs under
  the `pr_review_command_v0` response contract. It is not a project bootstrap
  command and should not mutate project state unless the review contract
  explicitly records a public-safe follow-up.
- Destructive git, credentials, private material reads, production actions, and
  external publication still require explicit user/controller approval.

## CLI Fallback

Every host command must expose a deterministic CLI fallback:

```bash
loopx slash-commands
loopx slash-commands --install
loopx agent-onboard --list-agent-types
loopx agent-onboard --agent-type codex-cli --project .
loopx agent-onboard --agent-type codex-app-ssh --project .
loopx bootstrap-command-pack --project .
loopx start-goal --guided --project . --goal-text "<goal text>" --host-surface codex-cli-tui
loopx --format json start-goal --guided --project . --goal-text "<goal text>" --host-surface codex-ide-plugin --include-command-pack-detail
loopx bootstrap-command-pack --project . --goal-text "<goal text>"
loopx pr-review
loopx global-summary
loopx --format json --registry "$HOME/.loopx/registry.global.json" quota should-run --goal-id <goal-id> --agent-id <agent-id>
```

If host command parsing is unavailable, the user or a skill fallback can still
run these commands and preserve the same LoopX state machine. Prefer
`loopx start-goal --guided` for agent/manual task starts; use
`loopx bootstrap-command-pack --goal-text` when implementing or debugging the
lower-level host handoff packet.

## Userland Registration Fallback

Until every host exposes a native command registry, LoopX installs slash-command
facades into the user-level discovery locations that current hosts already
support:

- `~/.codex/skills/loopx*/SKILL.md` for explicit Codex command-facade
  invocation through `$loopx` or `/skills`; the primary `LoopX` command facade
  remains distinct from the `LoopX Project` workflow skill;
- `~/.claude/skills/loopx*/SKILL.md` for Claude Code skill-based slash
  commands.

This fallback does not replace host parsing. It gives users an explicit Codex
skill entry point now, while preserving the same CLI baselines and permission
boundaries defined above. The Codex command facades are explicit-only; the
richer workflow skills remain available for implicit invocation. The installer
overwrites LoopX-managed files and known legacy LoopX-generated command files,
but it skips same-name user files without a LoopX managed marker or legacy
signature.

## Acceptance Checks

A host command registry implementation is acceptable when:

1. `/loopx` and `/loopx <goal text>` route to `loopx_goal_command_v0`.
2. `/loopx-global-summary`, `/loopx-global-gates`, `/loopx-global-todos`, and
   `/loopx-global-risks` route to `global_manager_command_v0`.
3. `/loopx-pr-review` routes to `pr_review_command_v0` and runs the CLI first.
4. Legacy `/loop-global-*` inputs canonicalize to `/loopx-global-*`.
5. Unknown `/loopx-*` commands fail closed with `loopx slash-commands` help.
6. The handoff packet includes project root label, optional active state id,
   agent id, protocol, authority, and CLI fallback, without public local
   absolute paths.
7. Host parsing is treated as the preferred path, while skill-level recognition
   remains only a compatibility fallback.
