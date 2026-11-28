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
`run_history.goals[].host_thread_activity` with up to 32 bound-thread rows:
`agent_id`, `host_surface`, `state`, and the `turn_started_at`,
`last_turn_ended_at` and `last_event_at` timestamps when known. Thread ids,
paths and message content are not included.

The observation's `completeness` is `complete` only when every valid binding
fits within that read budget; otherwise it is `incomplete`. The App can show
an observed recent open turn from either, but reports all hosts idle only from
a complete observation containing only idle or archived threads. Missing or
unrecognized completeness is treated as incomplete. Malformed event payloads
make that thread `unknown` without failing the status route; a later recognized
turn marker restores its observed state.

| `state` | Meaning |
| --- | --- |
| `turn_open` | The host recorded a turn start without an end. A host that exits mid-turn leaves this behind, so the App shows it as running only while `last_event_at` is recent. |
| `idle` | The latest recorded turn ended. |
| `archived` | The host archived the thread. |
| `unknown` | Not observable; `reason` is `unsupported_host`, `store_unavailable`, `thread_not_found`, `record_unrecognized` or `no_turn_marker`. |

Codex surfaces (`codex-app`, `codex-app-ssh`, `codex-cli-tui`, `codex-ide-plugin`) are read
from `<CODEX_HOME>/state_<n>.sqlite` and the thread's rollout JSONL. The Codex
store is not a public contract: any shape the adapter does not recognize is
`unknown`, never `turn_open`. An SSH session's store is local on its source
host, so `codex-app-ssh` now uses this same observer instead of always returning
`unsupported_host`. A missing store stays unknown; the observer does not connect
to another host, deliver a message or qualify live execution. Homes are searched
in the order `CODEX_HOME`, `~/.codex`,
then sibling `~/.codex-*` directories; set `LOOPX_CODEX_HOMES` (an
`os.pathsep`-separated list) to search exactly those homes instead.

## Missed Delivery Windows

The status route adds `delivery_window` to each observed lane of
`host_thread_activity`. For a `codex-app` lane the expected cadence comes from
the `rrule` of the installed heartbeat automation whose prompt names that Goal
and agent and whose `target_thread_id` is the lane's canonical binding; another
lane's automation is never read as this one's, and two automations claiming one
lane leave the window `unknown`. Other surfaces report `unknown` there.

Activity alone does not prove a scheduled prompt arrived: an empty Turn, a
resumed Turn and a hand-written message leave the same trace. `loopx doctor`
therefore also asks for *delivery evidence* and only reads a lane `fresh` when
the thread's newest Turn is one the host attributes to an automation — its
`task_started.turn_attribution.turn_trigger` is `automation_heartbeat` (the
routing contract's name) or `automation_heartbeat_scheduled` (the value the
current Codex App rollout writes for the same delivery), with the
`heartbeat_xml` / `user_input` / `user` transport the contract already requires
— and carries a user message matching the installed prompt. Matching text is
only a pointer: a `composer` Turn that repeats the prompt verbatim, with or
without the host's envelope, proves nothing, and a trigger this adapter has not
seen stays unverified rather than being read as a tick. When the envelope names an
`<automation_id>`, it must be the automation installed for this lane, so a
sibling automation's body cannot stand in. The matching message's time is
published as `delivery_evidence_at`. The scan reads a bounded 8 MiB tail of the
thread's own rollout and stops at the newest `task_started`; a Turn that arrives
without its opening record, without attribution, or one larger than that budget
is unattributed rather than guessed.

| `state` | Meaning |
| --- | --- |
| `fresh` | The lane received its installed prompt inside `expected_interval_minutes × tolerance` (tolerance defaults to 2). |
| `stale` | The lane's last recorded tick is older than that window. This names the symptom only; it does not name a cause. |
| `missing` | No observation of this lane was available at all. |
| `unknown` | The window could not be computed; `reason` is `no_expectation`, `no_observation`, `unparseable_observation` or `unverified_activity`. |

`unverified_activity` is the middle case: the lane moved inside its window but
nothing ties that activity to the installed prompt. Its last activity time is
still reported, so an operator can tell "quiet" from "busy, but not scheduled".
The Dashboard status route keeps its existing activity-only semantics; only the
`doctor` readback asks for delivery evidence.

`loopx doctor` reads the same projection once per run and reports it as the
optional `host_prompt_delivery_windows` check and the `host_prompt_delivery`
payload: a `status` of `fresh`, `stale`, `unverified` or `not_applicable`, a
per-state `counts` map, and one row per lane carrying `last_observed_at`,
`age_hours`, `window_minutes` and the cadence `source`. A `stale` lane carries
`diagnosis.code = host_prompt_delivery_missed` with the last tick the host did
record; `missing`/`unknown` lanes carry
`diagnosis.code = host_prompt_delivery_unverified` and their reason. Only a
missed window turns the check red — an absent receipt is not proof that the host
stopped delivering, so it is reported rather than counted as a fault. A run that
could not measure every lane also leaves the optional check red, with the gap
named in its detail, because a green check would claim coverage the run did not
have. The check stays optional either way, so it never fails `loopx doctor` on
its own.

Stopped Goals are skipped: their automation is no longer a live promise.
Observation is bounded to 64 lanes per run (`truncated` says whether the bound
was hit). The shared observer also keeps at most 32 bindings per Goal; a Goal
past that cap is counted in `incomplete_goals` and also sets `truncated`, since a
silent lane can hide among the bindings that were not read.

Lane identity is resolved over the Goal's complete binding list, before that
bound trims what the run reads. An agent bound to two threads on one surface has
no canonical thread, so its window stays `unknown/no_expectation` whether the
second binding sits at position 2 or past the cap; truncation removes
observations, never identities.

A run that did not measure every lane never reports `fresh`: the lanes it did
read may all be inside their window while an unread lane is silent. Such a run
reports `unverified` with `reason = scan_truncated` when the lane bound was hit,
or `not_applicable` with the same reason when nothing was left to read. A
binding on an observable surface that names no thread (`unmeasured_bindings`) or
a Goal whose activation record cannot be read
(`unreadable_activation_goals`) reports `unverified` with
`reason = unmeasured_lane`.

Doctor reads each lane's store read-only and never starts a Turn, writes the host
store or spends quota. The diagnostic registry is the one the run resolved: an
explicit `--registry` replaces the default global path for every diagnostic this
run projects, so a moved registry is measured where it lives instead of reading
as an empty workspace. A registry this build cannot decode is reported as
`not_applicable` with `reason = registry_requires_upgrade`, and a missing or
unreadable one as `registry_unavailable`, so a broken workspace never turns this
optional check into a doctor failure. `loopx doctor --installation-only` does
not read host state at all and therefore omits this projection.

A Goal, agent or host surface identifier is free-form registry text, so a lane
can be named after a directory or a token-shaped string. Such a label is
replaced before publication by `<redacted:<sha256-prefix>>` plus, when the last
path segment is itself safe, `:<segment>`; the row carries
`label_redacted = true`. The digest is stable across runs and differs per
identifier, so two path-shaped Goals stay distinguishable and an operator can
still match the lane to the silent Goal. If any unanticipated field still
violates the public-output boundary, the projection keeps its `status`, counts
and check verdict and sets `labels_withheld = true` instead of failing the
whole doctor run.
