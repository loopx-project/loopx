# Attached Agent Session Broker

The attached Agent session broker represents an already-running host session
inside the owner-local LoopX Chat store. It does not start, resume, or replace
an Agent runtime.

The first bridge stage supports:

- exact `(Goal, registered Agent, host surface, host session)` admission;
- distinct LoopX `agent_id` and executor `executor_endpoint_id` identities;
- one shared ordered queue for Web and Connector messages with `origin`;
- duplicate-safe host claim and completion receipts;
- response readback through the existing Chat turn and Lark reply path; and
- content-free Session list projections.

The host session must already have an exact `bind-agent-thread` registration.
Bind it to Chat with owner-local opaque values:

```bash
loopx worker-bridge attached-session-bind \
  --goal-id <goal-id> \
  --agent-id <registered-agent-id> \
  --host-surface <host-surface> \
  --host-session-id <opaque-host-session-id> \
  --executor-endpoint-id <executor-endpoint-id> \
  --execute
```

Web or Lark `session_queue` input is then claimed by the existing host:

```bash
loopx worker-bridge attached-session-claim \
  --session-id <loopx-chat-session-id> \
  --host-surface <host-surface> \
  --host-session-id <opaque-host-session-id> \
  --claim-id <stable-claim-id> \
  --wait-seconds 30 \
  --format json
```

`--wait-seconds` turns claim into a bounded host subscription. An existing host
bridge can keep one claim request open and wake as soon as the oldest queued
message is available, instead of polling the command in a tight loop. The wait
is capped at 30 minutes and never starts or resumes an Agent runtime. A timeout
returns `claimed=false`; the host chooses whether to subscribe again.

Write the Agent response to an owner-local JSON file containing at least a
`message` field, then complete the exact claim:

```bash
loopx worker-bridge attached-session-complete \
  --session-id <loopx-chat-session-id> \
  --turn-id <loopx-chat-turn-id> \
  --host-surface <host-surface> \
  --host-session-id <opaque-host-session-id> \
  --claim-id <stable-claim-id> \
  --completion-id <stable-completion-id> \
  --response-json <owner-local-response.json>
```

`session_queue`, bounded claim wait, and reply readback are enabled in this
stage. `live_steering` is explicitly reported as unavailable until the host
exposes a push transport for the already-running Turn. LoopX fails closed
instead of starting a managed runtime or silently degrading one event into
another ingress mode.

An attached Session with an active host claim cannot be closed. Complete the
claimed Turn first, then close the Session. This preserves the host's writeback
authority and prevents a closed Session from stranding a running Turn.

Opaque host identifiers, message bodies, and response files stay in the local
runtime store. Public Session projections contain only the LoopX Session id,
Goal/Agent binding, executor endpoint label, host surface, capability booleans,
and lifecycle state.

## Bound Codex Thread Activity

The broker only sees turns a host claims. A host thread registered with
`bind-agent-thread` but never attached is observed separately, read-only, from
the host's own local store. The App status route (`/status.json`) adds
`run_history.goals[].host_thread_activity` with one row per bound thread:
`agent_id`, `host_surface`, `state`, and the `turn_started_at`,
`last_turn_ended_at` and `last_event_at` timestamps when known. Thread ids,
paths and message content are not included.

| `state` | Meaning |
| --- | --- |
| `turn_open` | The host recorded a turn start without an end. A host that exits mid-turn leaves this behind, so the App shows it as running only while `last_event_at` is recent. |
| `idle` | The latest recorded turn ended. |
| `archived` | The host archived the thread. |
| `unknown` | Not observable; `reason` is `unsupported_host`, `store_unavailable`, `thread_not_found`, `record_unrecognized` or `no_turn_marker`. |

Codex local surfaces (`codex-app`, `codex-cli-tui`, `codex-ide-plugin`) are read
from `<CODEX_HOME>/state_<n>.sqlite` and the thread's rollout JSONL. The Codex
store is not a public contract: any shape the adapter does not recognize is
`unknown`, never `turn_open`. Remote surfaces such as `codex-app-ssh` are
`unsupported_host`. Homes are searched in the order `CODEX_HOME`, `~/.codex`,
then sibling `~/.codex-*` directories; set `LOOPX_CODEX_HOMES` (an
`os.pathsep`-separated list) to search exactly those homes instead.
