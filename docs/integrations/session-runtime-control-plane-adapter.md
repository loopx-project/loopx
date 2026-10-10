# Session Runtime Control-Plane Adapter

Status: public-safe architecture target + read-only projection contract v0.

LoopX should be able to sit beside an existing agent host without
becoming that host. The target role is a long-horizon task control plane:
turn session-level execution facts into goal-level state that is recoverable,
auditable, gated, attention-ranked, and reusable across sessions.

## Layer Boundary

An agent host owns the execution plane:

- agent definitions and runtime configuration;
- environment and session lifecycle;
- append-only session events;
- tool and sandbox execution;
- host authentication, rate limits, billing, trace, and audit;
- raw transcripts, raw logs, and raw tool outputs.

LoopX owns the goal-level control projection:

- `goal_state`: objective, non-goals, authority sources, current boundary;
- `run_projection`: compact session/outcome summary with pointers to source
  facts;
- `operator_gate`: cross-session owner/controller decisions;
- `human_reward`: human judgment on a route or run result, separate from a
  task scorer;
- `work_lane_contract`: advancement, monitor, blocker, and user-gate routing;
- `quota_decision`: whether the goal should spend the next automatic agent
  turn;
- `handoff_packet`: the approved next action for a project agent;
- `dreaming_proposal`: background exploration that remains advisory until
  promoted.

A product surface owns the frontstage user experience: task cards, approvals,
progress, recovery entry points, and collaboration views. LoopX should
explain why a card exists, whether it may run, which gate blocks it, and how it
recovers after approval; it should not become the default end-user console.

## Core Principle

### Managed Codex home ownership (implemented)

The managed Chat adapter captures `LOOPX_CHAT_CODEX_HOME`, then `CODEX_HOME`,
then the host's default Codex home at controller startup. It explicitly passes
that home to each app-server process, including catalog-compatibility retries.
New managed Codex sessions persist this binding in owner-local session state.
Resume and submit reject a different home before starting an upstream process
or modifying a turn. Legacy sessions acquire the binding only after successful
upstream restoration; startup alone neither rebinds nor copies their history.

The macOS LaunchAgent installer preserves an existing binding on upgrade,
including older shell-export plists. Use `LOOPX_CHAT_CODEX_HOME` explicitly when
installing a deliberately different host profile. A later ambient `CODEX_HOME`
does not overwrite it. Restore the original home to recover a home-mismatch
gate; changing this variable is not a session-migration command.

The LaunchAgent keeps service execution `CODEX_HOME` separate from
`LOOPX_CHAT_CODEX_HOME`. Set both when a coordinator's workers resume in a
different existing home from its Chat app-server. Install/restart preserves the
recorded execution home when `CODEX_HOME` is omitted; an explicit value changes
only that service setting. A fresh install without an execution selection uses
the Chat home. Changing the Chat override no longer overwrites an existing
worker profile. This affects macOS-managed Chat/delegation launches, leaves
session-profile checks in force, and neither migrates sessions nor copies auth.

中文：macOS 登录服务分别保存执行端 `CODEX_HOME` 与聊天端
`LOOPX_CHAT_CODEX_HOME`。需要沿用不同的原会话时同时指定两者；升级或重启时未指定
`CODEX_HOME` 就保留已安装的执行目录，新安装未指定则沿用聊天目录。只修改聊天目录
不再覆盖已有 worker 配置；会话检查仍会拒绝不一致的恢复，不迁移历史或复制凭据。

Sharing a host home does not by itself prove a SQLite lock failure. A desktop
launcher should separate ordinary open (read-only identity/configuration checks)
from offline account switching, migration, or rollback (exclusive ownership).
Do not make a normal open perform hidden migrations, kill managed workers, or
move sessions into another home to pass an overly broad file-open check.
Sharing a home also does not authorize two clients to run the same thread
concurrently. Account changes still require quiescing all users of that home;
this binding does not implement credential copying or account-refresh logic.

Validation: `python -m pytest tests/test_chat_codex_home.py tests/test_chat_agent.py`
and `python examples/macos-dashboard-launchagent-status-smoke.py`. The latter
exercises the actual installer with fixture LaunchAgents, not real services.

### Projection boundary

The host session log is the raw fact source. LoopX run history is a
compact control projection. A projection may reference host ids such as session,
event, tool call, artifact, approval, or outcome ids, but it must not copy full
transcripts, credentials, raw logs, private traces, or sandbox internals into
LoopX state.

This avoids a second event store. If the host says a session completed and
LoopX says a goal is still blocked, the projection must explain the
reconcile rule: missing gate, missing validation, failed outcome, stale
artifact, or human decision not yet recorded.

## Adapter Phases

### Phase 1: Read-Only Projection

Input: compact host summaries for sessions, events, outcomes, approvals, and
artifacts.

Output: a LoopX attention item with:

- `waiting_on`;
- `next_action`;
- first open user todo;
- first executable agent todo;
- latest validation or blocker;
- gate state;
- compact source pointers.

This phase must not write back to the host, alter runtime behavior, or launch a
session. The first useful demo is a long-running task first screen that answers
four questions:

1. Who or what is the goal waiting on?
2. Can the agent continue now?
3. What gate or evidence is required before continuing?
4. What did the most recent run validate or block?

### Phase 2: Controlled Writeback

After the read-only projection proves useful, LoopX may map compact
control events back to host metadata or events. The stable boundary is
[`session_runtime_controlled_writeback_v0`](../reference/protocols/session-runtime-controlled-writeback-v0.md):

- operator gate requested/resolved;
- human reward or route judgment;
- handoff packet accepted;
- quota decision as a scheduler hint, not billing;
- artifact pointer or run projection pointer.

Writeback must remain compact and reversible. It should not copy raw evidence
or turn LoopX into the host's permission system.

### Phase 3: Product Surface Integration

The product surface should display LoopX projections instead of asking
users to read the LoopX dashboard directly. LoopX remains the
reliability and governance layer behind the product view.

## Non-Goals

LoopX should not:

- reimplement the host's agent loop or model strategy;
- reimplement the host's event store;
- run tools or sandboxes directly when the host already owns them;
- replace host authentication, billing, rate limits, or trace;
- become the product frontstage for ordinary end users;
- store raw transcripts, private traces, credentials, or raw benchmark logs.

## First Public Contract

A minimal read-only adapter contract can be shaped as:

```json
{
  "schema_version": "session_runtime_readonly_projection_v0",
  "source": {
    "host_kind": "session_runtime",
    "source_ids_redacted": true
  },
  "session_facts": {
    "session_count": 1,
    "latest_event_at": "2026-01-01T00:00:00Z",
    "outcome_status": "blocked",
    "approval_state": "none",
    "raw_transcript_copied": false
  },
  "goal_projection": {
    "waiting_on": "codex",
    "next_action": "write compact blocker or continue approved handoff",
    "first_user_todo": null,
    "first_agent_todo": "advance one bounded segment",
    "latest_validation": "compact validation summary"
  }
}
```

The public fixture should prove only projection semantics. Private source ids,
raw event bodies, exact host URLs, raw logs, credentials, and local paths stay
outside the repository.

The current v0 implementation is a pure builder,
`loopx.session_runtime.build_session_runtime_readonly_projection(...)`.
It accepts compact session, event, outcome, gate, artifact, and decision-result
summaries, then returns:

- `first_screen`: waiting owner, user action, agent action, validation, blocker,
  and recommended next step;
- `attention_item`: a compact dashboard/status item with source pointers;
- `work_lane_contract`: `user_gate`, `advancement_task`, `blocker`, or
  `monitor`;
- `reconcile_rule`: the rule that host logs remain raw facts while LoopX stores
  only compact control projection.

### Raw-Material Key Classification

The builder never reads input values to decide whether they are raw material;
it classifies input key names with a typed, word-level rule. Keys are split
into words on `_`, `-`, and camelCase and matched as exact keys, whole words,
or exact word sequences, never as substrings. Every key lands in one of three
states:

| State | Effect | Examples |
| --- | --- | --- |
| compact | allowed | keys the projection reads (`status`, `summary`, `next_action`), timestamps, pointer/count suffixes only when no raw evidence is present (`catalog_id`, `login_at`), explicit safe collisions (`trace_id`, `message_id`, `log_count`), usage metrics (`token_count`, `max_tokens`) |
| raw material | `raw_material_detected`, `agent_can_continue=false`, category recorded in `raw_material_categories`; its value is never copied | `credential` (`api_key`, `access_token`, `password`, `secret_id`, `api_key_id`), `transcript` (`message`, `raw_transcript`, `messages`, `prompt`, `body`, `transcript_id`), `log` (`log_path`, `stack_trace`), `local_path` (`file_path`), `raw_output` (`stdout_tail`, `diff`, `raw_id`) |
| unclassified | reported in `unclassified_key_names` (bounded), never blocks | `backlog`, `changelog`, `logical_clock` |

The word `token` is a credential only in auth forms (`token`, `access_token`,
`auth_token`, `api_token`, `bearer_token`, `refresh_token`, `id_token`); count
forms such as `tokens_used` are compact, but a raw-material word or phrase in
the same key takes precedence over both metric and pointer shortcuts
(`tokens_password`, `raw_tokens`, `secret_id`, and `api_key_id` are raw).
`trace_id` is an explicitly safe pointer; `trace`, `stack_trace`, and
`trace_path` are logs. `log_count`, `prompt_tokens`, and `prompt_token_count`
are explicitly safe aggregates and `conversation_id` is an explicitly safe
pointer. Transcript evidence otherwise matches the exact key `message` and the
whole words `messages`, `prompt`, `prompts`, and `conversation`: `prompt_id`,
`prompt_text`, and `conversation_ref` stay raw, `message_count` and
`message_ref` are compact pointers, and `message_text` is reported as
unclassified rather than guessed either way. `log`
matches only as a whole word, so `catalog_id`, `login_at`, and `changelog` are
not flagged.

Run:

```bash
python3 examples/session_runtime/session-runtime-readonly-projection-smoke.py
```

OpenViking-style issue-fix memory is covered by the public specialization
[`openviking_session_memory_adapter_v0`](../reference/protocols/openviking-session-memory-adapter-v0.md).
That adapter keeps per-goal and per-issue session memory as compact refs and
retrieval gates only: no live OpenViking retrieval, memory writeback, issue or
comment body read, raw trajectory, or raw tool-output ingest is authorized by
the public fixture. Validate it with:

```bash
python3 examples/openviking-session-memory-adapter-smoke.py
```

## Metrics

The integration is valuable if it improves:

- interrupted task recovery rate;
- duplicate compute avoided;
- owner gates preserved across sessions;
- cross-session handoff success;
- stale projection detection;
- time from "task is blocked" to "the right owner sees the blocker".

These metrics are goal-control metrics, not model-quality scores.
