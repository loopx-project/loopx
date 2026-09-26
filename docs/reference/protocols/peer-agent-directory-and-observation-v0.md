# peer_agent_directory_v0

`peer_agent_directory_v0` is the reusable LoopX contract for one Agent
discovering, observing and delivering a bounded request to another Agent. It is
the Agent-facing companion of
[`agent_management_projection_v0`](agent-management-projection-v0.md): that
projection answers "what does the operator see", this contract answers "what may
a peer or a steward see and do about it", under the identity and authority rules
of [`peer_agent_runtime_v1`](peer-agent-runtime-v1.md) and the shared-intent
rules of
`docs/architecture/rfcs/shared-goal-alignment-and-governed-amendment-v0.md`.

It exists because both the steward channel and the peer Agents inside one Goal
need the same three abilities, and each of them is currently answered by a
different internal surface:

1. **Directory** — which Agents exist for this Goal, and which of them is
   running right now;
2. **Observation** — what bounded state and output may I read about one of them;
3. **Delivery** — how may I hand one of them a bounded request, and what does a
   successful hand-off actually prove?

The contract is provider-neutral. A host surface that owns a terminal space may
supply *presence* and *live output* (see
[the reference implementation](#reference-implementation-herdr)); a prompt-only
transport supplies neither, and the contract still works with the durable half.

The same three abilities answer two audiences that must not be conflated, and
both are callers of this one contract:

| Audience | Who asks | Where its scope comes from |
| --- | --- | --- |
| Steward (manager channel) | the Agent a person talks to, about any Goal its channel covers | the channel's Goal binding |
| Peer (`peer_v1`) | a registered Agent of one Goal, about its peers | the Goal's registered Agents, plus Goals explicitly delegated to it |

They share one contract because they ask one question -- who else is working
here, what may I read about them, and what may I hand them -- and because a
second, steward-only directory would become a second source of truth for facts
the registry, the work graph and the lane contract already own.

## Sources Of Truth

Nothing here is a new source of truth.

| Field group | Canonical owner |
| --- | --- |
| Agent identity, registration, `agent_model` | Goal registry (`registered_agents`) |
| Work item, claim, lease/fence | `todo_id`, task lease, per-Agent frontier |
| Canonical intent and its revision | `shared_goal_intent_v0` |
| Delivery of context or a bounded request | `context_handoff` (with its receipt) |
| Lane, quota and next action | quota `interaction_contract`, lane contract |
| Terminal layout, pane and live process | the host surface that owns the terminal space |

Two consequences follow, and both are rules rather than observations:

- a live session never creates an Agent identity, and an Agent that has no live
  session is still registered, still owns its claims, and is still a delivery
  target;
- a host surface's view of the terminal is **advisory**. It is not evidence of
  LoopX progress, and it may not overwrite any row in the table above.

## Space, Caller Context, And Layers

A provider that owns terminals usually defines a **space** -- one session whose
members can see and address each other -- and installs an **in-space skill**
that teaches an Agent running inside it to use that space. LoopX's space is the
Goal execution space: a Goal identity, its registered Agents, the shared work
graph and each Agent's frontier, that Goal's lane and quota contract. A host
surface -- a Chat steward session, a CLI, a desktop app, a terminal-space
provider -- is a *transport inside* that space. It never defines membership.

The same contract is reachable at three layers, and the layers must agree:

| Layer | LoopX surface | What it may answer |
| --- | --- | --- |
| Typed state and commands | `shared-goal-alignment`, `agent-context`, `agent-capabilities`, `manager-inbox`, `todo`, `chat` | the durable facts, and the governed writes |
| In-space skill | the shipped manager and peer guidance an Agent loads while it runs | how an Agent inside the space asks |
| Provider surface | a host integration or extension that owns live presence | live presence and bounded output, and nothing else |

Two rules follow, and they are the reason the layers are named at all:

- **A layer may narrow authority, never widen it.** The skill layer and the
  provider layer report and request; only the typed layer decides. An Agent able
  to address a peer has gained no claim, no lease, no priority and no work edit.
- **Membership is proven, not asserted.** A provider injects caller context --
  its own space flag plus the identifiers of the caller's location -- and its
  in-space skill refuses to act when that proof is absent. LoopX's counterpart
  is the caller's own `goal_id` and `agent_id`, resolved from the channel, lease
  or transport binding that the caller arrived on, plus the registry's answer
  about whether that Agent is registered for that Goal. A caller whose
  membership cannot be established reports a scope gap
  (`audience_not_authorized`, or `unknown` where the provider cannot classify),
  never a listing of another Goal's Agents.

A caller resolves **itself from the binding it arrived on and its targets from
the directory**. Naming a target never establishes the caller's scope, and any
identifier a provider hands back is a location inside one provider session, not
an identity.

## Directory Packet

```json
{
  "schema_version": "peer_agent_directory_v0",
  "goal_id": "loopx-meta",
  "collected_at": "2026-09-16T10:00:00Z",
  "scope": "goal_registered_agents",
  "rows": [
    {
      "agent_id": "codex-alpha",
      "registered": true,
      "work": {
        "todo_id": "todo_ab12",
        "claimed": true,
        "lease": "active"
      },
      "presence": {
        "provider": "terminal_space",
        "provider_session_ref": "w1:p2",
        "liveness": "working",
        "observed_at": "2026-09-16T09:59:58Z",
        "basis": "provider_detection"
      },
      "observation_limits": ["provider_scrollback_bounded"],
      "peer_route": {
        "schema_version": "loopx_agent_binding_route_summary_v0",
        "agent_id": "codex-alpha",
        "outcome": "multiple_candidates",
        "address_shared": false,
        "candidate_count": 5,
        "candidates": [
          { "thread_id": "thread-7f3", "host_surface": "codex-cli" },
          { "thread_id": "thread-91c", "host_surface": "traex" }
        ],
        "scope": "goals_supplied",
        "limitations": ["candidates_truncated_at_cap"],
        "provenance": {
          "source": "coordination.thread_agent_bindings",
          "goals_supplied": 2,
          "selects_route": false
        }
      }
    }
  ],
  "limitations": ["presence_is_advisory", "presence_stale_after_provider_restart"]
}
```

Rules:

- a row exists per registered Agent of the Goal, whether or not it is running;
- `presence` is optional and must carry `provider`, `observed_at` and `basis`,
  so a reader can tell "not running" from "this machine cannot see it";
- `provider_session_ref` is an opaque handle **inside one provider session**.
  It is never a Goal identity, never stable across providers, and must not be
  compared across machines or used as a Todo/Agent key;
- a provider's own in-space proof of context (for example an environment flag and
  injected pane identifiers) may strengthen "I am inside this space". It never
  replaces registry registration, and a failure of that proof means the reader
  reports `unknown`, not `absent`;
- `peer_route` reports what the supplied Goals record about addressing that peer.
  `candidates` is a bounded, first-seen list of distinct `{thread_id,
  host_surface}` entries and `candidate_count` is the full distinct total, so a
  list shorter than the count reads as a cap rather than as a disproved
  remainder. Neither field selects a route: `outcome` is `single_candidate`,
  `multiple_candidates` or `no_candidate`, and `address_shared` marks a candidate
  whose host thread also names a different registered Agent — the condition the
  forward resolver answers `conflict` for. `scope` is `goals_supplied`, so a lone
  candidate here is not a project-level uniqueness claim. A candidate withheld by
  the public boundary stays counted and is declared in `limitations`.

## Presence Vocabulary

Presence answers "is this Agent runnable right now", not "is its work done".

| `liveness` | Meaning | Must not be read as |
| --- | --- | --- |
| `working` | the provider observed the Agent executing | progress, or evidence of an outcome |
| `blocked` | the provider recognized a question or approval gate | work done, or permission to answer the gate |
| `idle` | the Agent is ready for input | a delivered request, or an available lease |
| `done` | the Agent settled and is ready for input | task completion, or a closed Todo |
| `unreachable` | the provider knows the target, and cannot reach it now | an empty lane, or missing work |
| `unknown` | the provider cannot classify the target | completion, or absence of progress |

`done` and `idle` are both "ready for input" for a directory reader; the
provider's seen/unseen bookkeeping distinguishes them and is deliberately not
part of this contract. A reader that cannot obtain presence reports `unknown`
and names the coverage gap instead of inferring anything about the work.

## Bounded Observation

Observation prefers typed state and falls back to bounded output.

1. **Typed first.** Work state, frontier, claims, lease facts, gates and
   evidence come from LoopX projections (`shared_goal_alignment_v0`,
   `agent_management_projection_v0`, the Agent-scoped evidence ledger), never
   from parsing a terminal.
2. **Bounded output second.** When a caller needs what a peer actually said or
   did, the provider may return a bounded excerpt: an explicit source
   (rendered viewport, recent output, unwrapped recent output, detection
   snapshot), an explicit line bound, and an explicit "this is advisory" label.
3. **Declared limits.** A provider must state its limits instead of silently
   truncating: alternate-screen output that never enters scrollback, a cleared
   viewport, a restarted server, a disconnected machine.
4. **Durable fallback.** When bounded output cannot carry the answer, the caller
   asks the peer to write a durable artifact (file, Todo note, delivery
   receipt) and reads that. A screen excerpt is never promoted to evidence.

## Bounded Delivery

Delivery hands a peer a bounded request or context. The contract separates four
facts that are easy to conflate:

1. **Refusal before write.** If the target is at a question or approval gate, the
   delivery is refused with a typed blocker (`agent_blocked`-style) and writes
   nothing. Resolving that gate belongs to the gate's owner, not to the sender.
2. **Submission is not execution.** A successful submission proves bytes were
   written in order. It does not prove the peer started a turn.
3. **Observed activity is the weaker-but-real signal.** Where the provider can
   observe lifecycle, a delivery should also report whether activity followed
   inside a declared window, with a typed `stalled` outcome when it did not, and
   an expiry outcome when the sender's own timeout elapsed first.
4. **No blind resend.** A timeout or a stall does not prove the request was never
   delivered, so the sender inspects state before repeating; `context_handoff`
   delivery receipts remain the durable record that a delivery happened.

## Target Identity Pinning

A bounded wait, or the readback that a delivery produced a turn, must be pinned
to the identity it was started against and to an observation sequence that can
only move forward. Three rules:

1. **Resolve once, then pin.** The request resolves the target once -- Agent
   identity, work identity (`todo_id`), and the provider location -- and pins
   that resolution, so a *replacement* occupant of the same location cannot
   satisfy it. A replacement is a new identity that needs a new request.
2. **Require observed change.** A wait for a settled state must also require that
   the observed state changed after the request began. Otherwise a stale re-read
   of the state the caller was already looking at satisfies the wait and proves
   nothing about the delivery.
3. **Disappearance is typed.** If the pinned identity stops running, the wait
   ends as `unreachable` / `not_running`. It is not success, and it is not a
   silent timeout that leaves the caller guessing.

LoopX already implements this shape for its own governed writes: a quota guard
and the settlement that closes it are bound to the same turn instance, Goal,
Agent and Todo, and a settlement whose binding does not match is refused rather
than applied. This section states the same requirement for the directory's
bounded waits and delivery readbacks, where a provider supplies the location and
LoopX supplies the identity.

## Attention Rollup

A directory is also asked a routing question: *which of these Agents needs a
decision now?* A provider may publish a bounded rollup for that question, under
two rules:

- **Rollup is typed; liveness is colour.** Which rows appear, and their order of
  urgency, comes from typed state: a registered Agent sitting on a blocked gate,
  a claim with no recent advancement, work waiting on an owner, a delivery that
  stalled. Presence may annotate a row. It may not create, promote or remove
  one.
- **A rollup is not a scheduler.** The answer routes a person's or a steward's
  attention. It assigns no work, no priority, no lease, and it is not the input
  to any automatic assignment.

## Authority And Scope

- **Observation grants nothing.** Discovery and observation confer no claim, no
  lease, no priority, no plan change, no merge and no permission.
- **Delivery is not a work edit.** Handing a peer context or a request stays
  delivery. Changing what the Goal asks for stays an amendment
  (`shared_acceptance`, `protected_authority`), and changing work state stays
  with the canonical Todo, quota and lane owners.
- **No leader Agent.** A directory reader is not a scheduler for its peers. The
  rules that forbid a leader agent, hidden scheduler, promotion authority or
  second source of truth apply to this contract exactly as written for the
  multi-agent launcher.
- **Scope is authorization, not convenience.** A reader sees only the Agents and
  Goals its channel or Goal authorization covers. The directory must not become
  a cross-tenant enumeration surface, and an out-of-scope target is reported as
  a scope gap rather than as a missing Agent.
- **Host-surface control stays with the host.** Closing, moving or reconfiguring
  another actor's terminal space is a host-surface action with the host's own
  consent rules; it is not part of peer delivery.

## Provider Contract

A provider that supplies presence and live output must declare:

1. how a caller proves it is inside the space (and that failing the proof means
   `unknown`, not control);
2. opaque, session-scoped identifiers for its locations and occupants, plus the
   rule for what happens to an identifier after a move, close or restart;
3. its liveness vocabulary and the mapping into the vocabulary above;
4. its observation sources and bounds, including what it cannot recover;
5. its refusal and error taxonomy for delivery (blocked target, stalled
   submission, expired timeout, unreachable host);
6. its persistence claim: what survives a client detach, a server restart and a
   machine restart;
7. whether it supports identity-pinned waits, and the monotonic sequence it
   exposes so a stale re-read cannot satisfy one;
8. the bounded rollup it can publish, or an explicit statement that it publishes
   none.

LoopX ships no requirement that a provider exists. With no provider, the
directory degenerates to registered identity plus durable work state, presence
is omitted, and delivery remains available through the durable hand-off path.

### Local producer (shipped)

The first producer is `loopx agent-directory --goal-id <goal> [--agent-id
<caller>]` (`loopx/cli_commands/agent_directory.py` over
`loopx/control_plane/agents/directory.py`). It reads the Goal's existing agent
management projection, so identity, work, claims and staleness keep their
current owners and the packet adds no second read of the registry or a lease
store. It is the same surface for both audiences: a peer Agent inside the Goal
and the steward channel call one command.

What it emits, and what it refuses to imply:

- one row per registered Agent, whether or not that Agent holds projected work,
  and no `presence` block at all while no provider is registered;
- explicit `limitations` (`presence_provider_unavailable`,
  `presence_is_advisory`, `lease_state_not_projected`,
  `caller_identity_not_supplied`, `rows_truncated_at_cap`) together with
  `registered_agent_count` and `omitted_row_count`, so a truncated directory
  cannot be read as a complete one;
- a typed scope gap (`audience_not_authorized`) and zero rows when the named
  caller is not a registered Agent of the Goal, instead of a listing that caller
  has no scope over;
- a typed-only rollup that orders attention by projected work state and assigns
  nothing.

It writes nothing. Reading it grants no claim, no lease, no priority and no work
edit.

## Reference Implementation: Herdr

[Herdr](https://github.com/herdrdev/herdr) is a terminal-space provider whose
Agent-facing skill solves the same three problems from the other direction. It
was studied as the reference implementation of the provider half of this
contract (source and bundled docs read at `master`, `1806119`, 2026-09-16).
This section records what it does, so the rules above can be checked against a
real implementation rather than derived from LoopX alone, and so the parts
LoopX deliberately does differently are stated with their reason.

### Shape: one space, one control surface, three layers

- A Herdr **space** is a server session that owns workspaces, tabs, panes and
  the agents recognized inside them. The bundled agent skill is installed into
  the Agents that run inside that space, and the same control surface is exposed
  at three layers -- agent skill, CLI wrappers, and a raw dot-named JSON-RPC
  socket API -- with the explicit statement that the layers share one control
  surface (`docs/next/website/src/content/docs/socket-api.mdx`).
- The protocol schema is printed by the installed binary
  (`herdr api schema`), so the binary is the authority for what exists, and a
  client is told that client and server versions may differ:
  *"a missing method is not permission to stop or upgrade a server."*
- Three primitives are kept non-equivalent rather than merged: layout
  (topology), pane (a real terminal), and agent (the recognized coding agent in
  it). `agent start` requires an existing shell pane and "never creates, splits,
  or moves layout" (`agent-automation.mdx`).

This is the layering this contract generalizes: typed state and governed writes
below, an in-space skill for the Agent that is asking, and a provider surface
that supplies live presence and bounded output only.

### Caller context is injected, and its absence stops the caller

- The PTY spawn path sets the space flag on every managed pane
  (`src/pty/backend/unix.rs`); the pane base environment adds the socket path
  and the Herdr binary path, and the workspace, tab and pane identifiers are
  injected for pane and plugin processes (`src/integration/env.rs`,
  `src/app/api/plugins/runtime.rs`).
- The skill's first instruction is a membership test: if the flag is not `1`,
  say that you are not inside Herdr and stop. It also forbids inspecting or
  controlling the session from outside (`skills/herdr/SKILL.md`).
- Identifiers are public but deliberately location-scoped: `w1`, `w1:t1`,
  `w1:p1`; closed ids are not reused; a pane moved to another workspace receives
  a new workspace-qualified id, and the old value keeps resolving only for the
  moved process's inherited caller context.

The matching LoopX rule is above: resolve the caller's own `goal_id` and
`agent_id` from the binding it arrived on, and report a scope gap when that
cannot be established.

### One status authority per target, and metadata that is not state

- Herdr arbitrates a single status authority per pane. Lifecycle-hook
  integrations are authoritative while they report; otherwise a screen manifest
  classifies the live bottom-buffer snapshot. It does not run both for the same
  lifecycle authority, "[t]his avoids two competing sources of truth", and
  session-identity-only integrations are explicitly *not* lifecycle authorities
  (`agents.mdx`, `src/detect/mod.rs`: `full_lifecycle_hook_authority`,
  `session_identity_only_integration`).
- Semantic state and display metadata are separated: `state` controls waits,
  notifications and rollups, while display tokens are display-only and may not
  affect them.
- Lifecycle is a small closed vocabulary -- `blocked`, `working`, `done`,
  `idle`, `unknown` -- with `unknown` documented as *not* proof of completion,
  and blocked detection deliberately strict, falling back to `idle` with a
  named `default_known_agent_idle_fallback` reason rather than guessing.

That is the same rule LoopX states as one writer per Todo, claim and frontier:
presence may annotate a row, and a second observer must not become a second
authority over the same fact.

### Bounded observation, and an honest fallback

- Reads name an explicit source -- rendered `visible` viewport, `recent`,
  `recent-unwrapped`, or the plain-text `detection` snapshot -- plus an explicit
  line bound, and the documentation names the limit that cannot be worked
  around: transcript rows on an agent's alternate screen never enter the host
  scrollback, so a larger line count cannot recover them
  (`agent-automation.mdx`, `skills/herdr/SKILL.md`).
- The documented fallback is exactly durable-first: ask the Agent to write its
  complete response as a file and reply only with the path, then read the file.
  A screen excerpt is never promoted to evidence.

This contract keeps observation typed-first and output bounded-second for the
same reason, and requires declared limits instead of silent truncation.

### Delivery: refuse before write, distinguish submission from execution

- A prompt to an agent that is already at an approval or question gate is
  refused with a typed `agent_blocked` **before any input is written**
  (`src/app/api/agents.rs`), and `agent_not_ready` is returned while the
  target is not ready for interactive input.
- A submission that produces no observed `working` or `blocked` activity inside
  a declared window returns typed `agent_prompt_stalled`, and a caller timeout
  that expires first returns `timeout` -- both distinct from success
  (`src/api/wait.rs`).
- The docs state the consequence this contract also requires: a timeout or a
  stall "does not prove that no input was sent", so the caller reads state
  before retrying rather than submitting the same prompt twice.

### Watches pin identity and require forward movement

`agent.wait` is server-owned and event driven, and it pins the resolved
occupant so a replacement cannot satisfy it. In the code, a candidate result
must match the pinned terminal id, name and agent kind
(`agent_wait_identity_matches`) *and* either be in an accepted status while the
monotonic `state_change_seq` moved past the baseline, or time out
(`agent_wait_matches`, `src/api/wait.rs`). A pinned agent that disappears ends
the wait as `agent_not_running` rather than as a success.

LoopX's own governed writes already bind a settlement to one turn instance,
Goal, Agent and Todo; the rules above extend the same shape to bounded waits on
peers.

### Attention rollups, not scheduling

State rolls upward pane -> tab -> workspace so a person can see which project
needs a decision, and a blocked agent makes its pane, tab and workspace look
blocked. Which completion a given client has already seen is deliberately *not*
shared: each client tracks its own view, while the CLI and API report the
server's seen state. Rollups route attention; they assign nothing.

### Extensibility: report state without core changes

Integrations can be installed per agent, agents can be started manually and
addressed by pane, and third-party hooks can report state, session identity and
display metadata over the socket API (`pane.report_agent`,
`pane.report_agent_session`, `pane.report_metadata`, `pane.clear_agent_authority`,
`pane.release_agent`). Detection rules for already-known agents can be patched
by a signed remote manifest; adding a genuinely new detectable agent still
requires a binary update, which is stated rather than implied.

The LoopX counterpart is the provider-registration rule in this contract: a new
presence provider is added at the extension boundary and registers against this
contract, without core gaining a second registry, session table or message bus.

### What LoopX adopts, adapts, and rejects

| Decision | Item |
| --- | --- |
| Adopt | proven membership over asserted membership; one contract across layers; one status authority per target; typed delivery outcomes that separate refusal, submission, observed activity and expiry; identity-pinned waits with forward-only observation; declared observation sources, bounds and unrecoverable cases; durable artifact fallback; version-skew tolerance, where a missing capability is a typed gap rather than permission to upgrade a provider |
| Adapt | Herdr's terminal topology becomes LoopX's Goal execution space, and a provider location becomes a `host-session` scope rather than an identity; an agent name alias becomes a display convenience bound to a location, never a Goal identity; an attention rollup becomes a bounded, typed-only routing view |
| Reject | raw terminal control as a LoopX authority surface; parsing a peer's screen to decide LoopX state; promoting liveness, a rollup or a delivery into a claim, lease, priority or work edit |

What LoopX adds, and a terminal-space provider cannot supply: durable Agent
identity, the canonical intent revision an Agent's frontier is based on,
claim/lease ownership, typed gates, and the authority rule that observation and
delivery grant nothing.

## Non-Goals

- No new agent registry, session table, pane inventory or message bus.
- No cross-machine identity: two providers may use the same identifiers for
  different Agents, and neither is authoritative.
- No screen scraping as evidence, and no parsing a peer's terminal to decide
  LoopX state.
- No control of another actor's terminal space, and no remote upgrade of a
  provider to unlock a missing capability.

## Acceptance Checks

- A Goal with two registered Agents and one live session returns two rows: the
  live one with presence, the other with registry identity and no presence.
- A provider that cannot classify a running Agent yields `unknown` with a named
  coverage gap, and the answer never claims the peer made no progress.
- A delivery to a gated peer is refused with a typed blocker and writes nothing;
  a stalled delivery reports `stalled` rather than success; a timeout never
  triggers an automatic resend.
- A caller whose own membership cannot be established receives a scope gap, not
  a directory of Agents it has no scope over.
- A wait pinned to one resolved target is not satisfied by a replacement
  occupant of the same provider location, and a re-read of the state the caller
  was already looking at does not satisfy it either; the pinned target
  disappearing ends the wait as unreachable rather than as success.
- A rollup changes what a reader looks at first; it changes no typed fact, and
  it assigns no work, priority or lease.
- With no provider at all, the directory still lists registered Agents and the
  durable delivery path still works.
- No field added by this contract changes a Todo, a claim, a lease, quota or the
  canonical intent.
