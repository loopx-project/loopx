# RFC: Calli Workbench and Session Fabric in LoopX (v0)

- **RFC status:** Accepted (effective on merge; a proposal under review until then)
- **Supersedes / closes:** none
- **Delivery maturity:** Proposal
- **Authors / owners:** [@maxliux5](https://github.com/maxliux5) ([Calli](https://github.com/maxliux5/calli) maintainer, MIT); directory placement, L0 package boundary and merge authorization belong to LoopX maintainers
- **Created:** 2026-11-14
- **Last normative revision:** 2026-11-14
- **Implementation baseline:** LoopX `80c694972`; Calli `87adcde`
- **Related contracts:**
  [Monorepo distribution split](monorepo-distribution-split-v0.md),
  [Agent session execution modes](agent-session-execution-modes-v0.md),
  [Desktop execution frontends](desktop-execution-frontends-v0.md),
  [App conversations and async inbox](app-conversation-and-async-inbox-v0.md),
  [Overall roadmap](loopx-overall-roadmap-v0.md),
  [Status data contract](../../status-data-contract.md),
  [Compute quota](../../quota-allocation.md),
  [Design system](../../development/design.md)
- **Language mirror:** [中文版](calli-workbench-and-session-fabric-v0.zh-CN.md)

## Document map and maintenance contract

Sections 1–10 are the design and acceptance contract, Section 11 is the
normative delivery plan, and Section 12 lists open decisions (proposed answers
are not approval). Appendices are non-normative. The proposed in-repository
file name is `docs/architecture/rfcs/calli-workbench-and-session-fabric-v0.md`
with a same-basename `.zh-CN.md` mirror, submitted in the same PR and checked
by `scripts/check_rfc_bilingual.py`.

---

## 1. Decision summary

1. **What becomes authoritative or changes.** Calli enters LoopX as two
   separate units: a **product surface** (the Calli workbench UI, in its own
   directory under `apps/`) and an **optional session-fabric distribution**
   (SSH multi-host, non-invasive attachment of heterogeneous CLIs,
   quota-aware handover, under `packages/`). Neither becomes an authority for
   Goal/Todo/Gate.
2. **What stays unchanged.** `loopx-core` remains the only writer of Goal,
   Todo, claim, lease, quota, effect and Gate state. The official dashboard,
   the `/status.json` contract and `/api/chat/*` keep their behavior.
3. **Default and opt-in boundary.** Both units are off by default: a user who
   installs only `loopx-core` or uses only the official dashboard sees no
   difference. The workbench is a separately built and launched frontend; the
   session fabric installs and uninstalls through the existing
   `extension.toml` lifecycle.
4. **Principal safety and compatibility constraint.** No dual authority: each
   fact has exactly one owner. Calli's own task system (the `owner: me|agent`
   todo table, `waitingOn`, its handover state machine, team/coordination
   tables) **does not enter LoopX**; existing data is imported once through
   LoopX's public Todo dry-run/apply interface.
5. **What this RFC does not approve.** No change to `loopx/` core, no new
   default behavior, and no default-on quota handover. Every `apps/**` and
   `packages/**` PR still requires explicit maintainer merge authorization.

## 2. Problem and motivation

[Calli](https://github.com/maxliux5/calli) (MIT) is a multi-agent workbench. It attaches Claude Code, Codex, pi, TRAE,
zcode, Antigravity, DeepSeek Harness and other CLIs already installed on local
or remote machines to one interface, lets the user view and take over sessions
across machines, and moves work to another account or model when one account
nears its quota. LoopX already owns a strict governance kernel (Goal, Todo,
Gate, lease, turn fencing, receipt) and is advancing two execution modes,
Managed Host and Attached Session.

Evolving separately, users hit these failures:

- **Two ledgers for one task.** A user creates a "waiting for the agent" todo
  in Calli and has a matching Todo in LoopX. Completion, waiting target and
  owner drift apart, and the user no longer knows which to trust.
- **Two stewards for one person.** Calli's "needs you" reminder and the LoopX
  Lark steward notify about the same Gate separately; confirming one leaves the
  other pending.
- **Two multi-host models for one machine.** The LoopX dashboard already has
  `ssh_host_catalog_v0` and `/api/ssh-source/*`; Calli has its own SSH tunnel
  registry in `hosts.ts`. Host names, online state and failure reasons can
  contradict each other.
- **Two meanings of handover.** Calli switches accounts at a quota threshold;
  LoopX uses leases and turn fencing so one Todo has at most one executor at a
  time. A Calli switch that bypasses fencing could let two executors advance
  the same Todo.

Calli cannot fix this locally: as long as it keeps its own task state, any
sync is reconciliation between two authorities. The correct fix is for Calli to
give up ownership of that state and keep only the host-layer capabilities
LoopX does not yet have.

### Invariants

- **I1 Single authority.** Goal/Todo/Gate/lease/quota decisions are written
  only by `loopx-core`; the workbench and session fabric read projections and
  submit commands only through control-plane APIs.
- **I2 The UI is not a second state source.** The workbench persists no
  Goal/Todo/Gate fact. It may store only display preferences (theme, pane
  widths, selected machine) locally, and clearing them changes no business
  outcome.
- **I3 One Gate, one confirmation.** A Gate appears as the same record in the
  workbench and the Lark steward; after either side confirms it, the other
  drops it on its next projection refresh.
- **I4 Handover respects fencing.** Any account or model switch first ends the
  current turn at its fence, then the new session reacquires the lease; one
  Todo never has two active executors.
- **I5 Feature-off parity.** When not installed or not enabled, LoopX behavior,
  CLI output and dashboard rendering match the baseline byte for byte.
- **I6 The host stays in its lane.** The session fabric does not modify an
  agent's configuration, directories or session files, and injects no prompts
  or tools (Calli's existing principle, and LoopX's Attached Session rule that
  the external host keeps process ownership).

## 3. Scope and non-goals

### In scope

This RFC organizes the contribution into four layers. They map one-to-one onto
the ownership table already in
[Monorepo distribution split §5](monorepo-distribution-split-v0.md#5-proposed-architecture)
and introduce no new layering concept:

| Layer | Existing ownership row | What Calli brings | Destination |
| --- | --- | --- | --- |
| **L0 Host & Session Fabric** | Host | SSH multi-host connection and tunnel lifecycle; non-invasive attachment of heterogeneous CLIs (local and remote native conversation threads); multi-account quota-aware handover | `packages/loopx-session-fabric/`, combined with Managed Host into a complete session substrate |
| **L1 Minimal Governance Kernel** | Governance kernel | **Nothing.** Calli's tasks, handover state machine and team collaboration are removed | Existing `loopx-core` only |
| **L2 Capability Packs** | Capability strategy | Nothing new; Calli's research-oriented extensions stay out of core and hidden from general users by default | Unchanged; submitted separately through the capability-package process |
| **L3 Unified Surfaces** | Extension and product surface | Three-pane workbench: machine switcher on top, "host + machine + model" launch bar at the bottom, right-side "Needs you / Running / Failed" triage drawer | `apps/presentation/calli-workbench/`, sharing one Inbox/Gate projection with the Lark steward |

### Non-goals

- **No rebuilt L1.** No second task table, handover state machine, team or
  collaboration model. Calli's `todos`, `todo_threads`, `todo_notes`,
  `todo_proposals`, `sections`, `wishes`, `todo_summaries`, `teams` and
  `coordination` tables are not moved into LoopX.
- **No second state authority.** The workbench does not cache, infer or
  synthesize Goal/Todo/Gate state. A missing field first enters the LoopX
  status contract as an additive field, then the workbench reads it.
- **No Calli daemon transplant.** The Calli Node daemon (hundreds of `/api/*`
  routes, SSE, SQLite) does not enter LoopX as a whole; only audited L0 modules
  are ported, one package module at a time.
- **No wholesale frontend move.** Of Calli's ~40k-line web app, only the
  workbench shell, triage drawer, machine switcher, launch bar and session view
  enter. Skills/MCP resource management, pets, voice and other peripheral
  features are out of scope.
- **No replacement of the official dashboard.** Both frontends coexist and read
  the same projections; any future merge is a separate maintainer decision
  based on usage evidence.
- **No change to LoopX's default executor, provider or quota policy.**

## 4. Current-system contract (baseline facts)

**LoopX (`80c694972`)**

- `apps/` is the product surface, `packages/` holds optional distributions,
  `loopx/` is core. [`apps/presentation/README.md`](../../../apps/presentation/README.md)
  states that apps there consume only public-safe projections and fixtures,
  own no control-plane state and never write LoopX registries directly.
- The official dashboard (React 19 / Vite 8) reads `/status.json`, validates
  it with zod (`apps/presentation/dashboard/src/data/status.ts`), and checks
  compatibility via `status_contract.schema_version` and
  `minimum_dashboard_schema_version`.
- Public write entrypoints already exist: `/api/chat/todo/dry-run` and
  `/api/chat/todo/apply`, `/api/actions/*`, `/api/chat/request-control`,
  `/api/chat/codex-attached` (Attached Session), `/api/ssh-source/*` and
  `/ssh-hosts` (`ssh_host_catalog_v0`).
- Session execution modes are defined by
  [Agent session execution modes](agent-session-execution-modes-v0.md):
  in Attached mode the external host keeps process and session ownership; in
  Managed mode LoopX launches the runtime and advances bounded `loopx_turn_v0`
  transactions.
- Quota observation is described in [Compute quota](../../quota-allocation.md):
  `loopx quota status/plan` are Goal-scoped read-only observations.

**Calli (`87adcde`)**

- `packages/web`: React 18 / Vite 5 / hand-written CSS. The three-pane layout,
  machine switcher, launch bar and triage drawer already ship.
- `packages/daemon`: Node backend with SQLite; currently the owner of all
  Calli state. **L0 parts:** `hosts.ts` (SSH tunnel registry, backoff
  reconnect, typed failures `no_ssh/host_key/auth/unreachable/ssh_failed/daemon_missing/forward_denied`),
  `adapters/*` (codex, claude, pi, traex, zcode, agy, dsh, acp, …),
  `auto-handover.ts` and `quota-resume.ts` (quota windows, thresholds,
  cross-account order, switch-back on reset). **L1 parts to remove:** the todo
  tables, `teams`, `coordination`, and the handover state machine.
- `packages/ext-loopx`: a stdio JSON-RPC plugin that reads
  `worksource.snapshot` via `loopx status --format json`. It accepts only
  `version === '1.4.2'`, which no longer matches LoopX 2.0.

## 5. Proposed architecture

### 5.1 Directory and placement

The hard requirement is that the Calli UI lives in its own directory, clearly
separated from LoopX core. Proposed layout (**final placement is a maintainer
decision, see §12 D1**):

```text
apps/presentation/
  dashboard/            # official dashboard, unchanged
  calli-workbench/      # new: Calli three-pane workbench (L3)
    package.json        # built independently; React/Vite aligned with dashboard
    src/data/           # projection schemas and read-only queries only; no state store
    smoke/              # fixture-driven browser smokes
    README.md
packages/
  loopx-session-fabric/ # new: L0 session fabric (optional, default-off)
    extension.toml
    src/hosts/          # SSH multi-host, as a provider for ssh_host_catalog_v0
    src/attached/       # Attached adapters per CLI, one adapter per PR
    src/quota/          # quota-window observation and handover proposals
```

Rationale:

- **Fits existing layering.** The workbench only renders projections and
  submits commands, which is exactly what `apps/presentation/` means; the
  session fabric is an independently installable and removable host
  capability, which is exactly what `packages/` means. Neither touches
  `loopx/`.
- **Easy to review and roll back.** Each directory has its own
  `package.json` and CODEOWNERS line, so merge permission can be granted or
  withdrawn per directory; deleting a directory is a complete rollback.
- **Beside the official dashboard, not inside it.** Embedding would interleave
  two interaction models in one route tree and contradict the separate
  directory requirement.

Alternatives: `apps/calli-workbench/` (a sibling of `apps/presentation/`;
stronger isolation but outside the presentation grouping); splitting L0 into
several packages (e.g. `packages/loopx-host-ssh/`,
`packages/loopx-attached-<cli>/`).

### 5.2 Ownership and authority

| Fact | Sole owner | How the workbench reads it | How the workbench changes it |
| --- | --- | --- | --- |
| Goal / Todo / claim / Gate | `loopx-core` | `/status.json` and its sub-projections | `/api/chat/todo/dry-run` → `/api/chat/todo/apply`; `/api/actions/*`; `/api/chat/request-control` |
| Session messages, streaming output | Executing host (external CLI when Attached; LoopX when Managed) | `/api/chat/sessions/*` | Same interface |
| Inbox / Gates needing a human | `loopx-core` | Same projection as the Lark steward | Same confirmation command |
| Machine topology and connection state | Session fabric (L0) | Exposed via `ssh_host_catalog_v0` plus additive fields | `/api/ssh-source/*` |
| Quota-window observation | Session fabric (L0), as a quota observation provider | Added to the quota observation projection | Not writable; the user can only confirm a handover in a Gate |
| Theme, pane widths, selected machine | Browser-local | `localStorage` | Local |

Forbidden alternate authorities: the workbench's local state, the session
fabric's private storage, and any Calli daemon table must never be read as
evidence of whether a Todo is done, a Gate was handled, or who holds a lease.

### 5.3 The workbench as a LoopX projection consumer

- **Reads.** The workbench data layer consists only of zod schemas and
  read-only queries, reusing or mirroring the dashboard's `status.ts`
  contract. On start it validates `status_contract.schema_version`; on
  mismatch it shows "update the local LoopX service" instead of guessing.
- **The triage drawer's three groups derive entirely from projections:**
  - "Needs you" = Gates awaiting a human, pending decisions and Inbox items;
  - "Running" = Todos with a live lease and active sessions;
  - "Failed" = failed or blocked receipts, offline machines, quota-exhausted
    sessions.
  If a group needs a field the projection lacks, a separate **additive field**
  PR to the status contract comes first (additive fields need no RFC per the
  [RFC README](README.md#when-a-change-needs-an-rfc)); the workbench reads it
  afterwards. The workbench never "infers completion" on its own.
- **Writes.** Every operation that changes Goal/Todo/Gate calls dry-run first,
  then apply, then reads the projection back. On failure or conflict it shows
  the control plane's typed error and performs no optimistic local update of
  business state.
- **Design system.** It follows the [LoopX design system](../../development/design.md):
  Geist type, black-and-white hairline surfaces, restrained accents, designed
  empty/loading/error states and status glyphs. Calli's hand-written CSS
  migrates to LoopX CSS custom properties and passes the same kind of
  `check:css-custom-properties` check. Every UI PR attaches public-safe
  screenshots and self-reviews with that document's Populated Page Review.
  React and Vite are upgraded to the dashboard's versions.

### 5.4 One Inbox/Gate projection shared with the Lark steward

The workbench's "Needs you" and the Lark steward read the same Inbox/Gate
projection and confirm through the same control-plane command with the same
idempotency identity. The workbench sends no notifications of its own; it only
shows entries already in the projection. This satisfies I3: a Gate confirmed in
Lark disappears from the workbench on the next refresh, and vice versa.

### 5.5 L0 session fabric

**SSH multi-host: join the existing model rather than add a parallel one.**
LoopX already has `ssh_host_catalog_v0` and `/api/ssh-source/*`. The session
fabric acts as a provider that adds what Calli has already validated: Unix
socket tunnels, `BatchMode=yes`, never accepting unknown host keys on the
user's behalf, 2 s–2 min backoff reconnect, a 15 s health check and typed
failure codes. Host identity and aliases remain owned by
`ssh_host_catalog_v0`; Calli's `hosts.json` stops being a source. Remote access
stays replaceable: local SSH is the first implementation, and future cloud or
gateway sources plug into the same interface under
[roadmap R6 Local/Cloud Convergence](loopx-overall-roadmap-v0.md).

**Heterogeneous CLI attachment: maps to Attached Session.** Each Calli adapter
is ported as an Attached host adapter that follows the admission contract in
[Agent session execution modes](agent-session-execution-modes-v0.md): the
external CLI keeps process, session and interruption ownership; LoopX records
the binding and turn boundaries. Adapters are independent and ported one per
PR, starting with those that already have a LoopX counterpart (Codex, pi,
dsh). Process launch, worktree isolation and turn fencing owned by Managed Host
are not reimplemented here; this package fills the Attached side so the two
together form the full session substrate.

**Quota-aware handover: from "executing an action" to "a governed
proposal".** Calli currently acts at configurable thresholds (for example,
around 85% it compacts context and moves to a cheaper model; around 90% it
hands over to another account; after the quota resets it switches back).
After the merge:

1. The session fabric only **observes**: it reports each host account's quota
   windows (used percent, reset time, applicable models) as a quota
   observation provider, exposing no credentials or account identity, only
   opaque profile ids.
2. LoopX's quota and admission rules **decide** whether to switch; the switch
   happens at the current turn's fence: the old session ends its turn →
   releases the lease → the new session, under a new profile, reacquires the
   lease on the same Todo → a receipt records the handover.
3. It is off by default; when on, every switch leaves an auditable Inbox
   record. A user can require "ask me before switching", in which case it is
   an ordinary Gate.
4. "Switch back after reset" runs the same fence → lease flow; silent
   switching is not allowed.

### 5.6 `ext-loopx`: from a hard-coded version to contract probing

`ext-loopx` serves only users who keep running Calli standalone (the workbench
inside LoopX does not need it). Changes:

- Drop the `version === '1.4.2'` check; read `status_contract.schema_version`
  from `loopx status --format json` and compare it with the plugin's declared
  known range.
- Three outcomes: `compatible` (normal display), `newer_unverified` (read-only
  display labelled "newer, unverified contract"), `incompatible` (show the
  reason, do not parse).
- Optional capabilities (Inbox projection, quota observation) are enabled per
  field presence, not inferred from a version number.
- Contract tests with 1.4.2, 2.0, missing-field and unknown-schema fixtures.

This change lands in the Calli repository, not in a LoopX PR.

### 5.7 Command lifecycle and failure handling

- Every write carries an idempotency key; after a timeout or disconnect the
  client reads the projection back to see whether it took effect before
  retrying.
- When a projection is stale (older than its refresh period), the workbench
  marks the affected area "possibly stale" and disables writes that depend on
  it.
- A session-fabric crash does not affect `loopx-core`: Attached session
  ownership already lies with the external CLI, and after restart the fabric
  reattaches from `ssh_host_catalog_v0` and the binding records.

## 6. Alternatives and design choices

| Option | Verdict | Reason |
| --- | --- | --- |
| A. Calli stays standalone and only reads LoopX via `ext-loopx` | Rejected as an end state | Dual authority remains; sync is permanent reconciliation |
| B. Move the whole Calli daemon into LoopX, converge later | Rejected | Introduces a second state authority on day one, violating I1; review surface too large |
| C. Workbench as a route inside the dashboard | Rejected as default | Contradicts the separate-directory requirement; mixes two interaction models in one route tree |
| D. **Separate frontend directory + optional L0 package + removal of Calli L1** | **Chosen** | Satisfies I1–I6; every step is independently mergeable and revertible |

## 7. Safety, privacy, and compatibility

- **Feature-off parity (I5):** neither directory enters the `loopx-core` boot
  closure; CI adds a check that `loopx status` output is unchanged when they
  are not installed.
- **Credential boundary:** SSH credentials and host-key policy stay in the
  running user's OpenSSH; the fabric stores no keys and forwards no browser
  token. Agent account credentials never enter projections, logs or receipts.
- **Public/private boundary:** fixtures, screenshots and test data are fully
  synthetic, with no real sessions, paths or accounts, per the
  [public/private boundary](../../public-private-boundary.md).
- **License:** Calli is MIT; LoopX is Apache-2.0. Ported code is resubmitted
  by its author under Apache-2.0 with DCO sign-off; code by other contributors
  needs their sign-off or a rewrite (see §12 D5).
- **Mixed versions:** the workbench rejects incompatible servers through
  `status_contract.schema_version`; the session fabric declares a minimum
  `loopx-core` version in `extension.toml`.

## 8. Migration and rollback

**Existing Calli users**

1. Standalone Calli keeps working; `ext-loopx` switches to contract probing
   (§5.6).
2. A one-time export tool turns Calli todos, notes and linked threads into
   LoopX Todo proposals. It **dry-runs first**; the user confirms item by item;
   it writes through `/api/chat/todo/apply` and reads the projection back to
   compare counts and text. `owner: me|agent` maps to the Todo's responsible
   party and `waitingOn` maps to a Gate or a waiting note; the mapping rules
   live in the tool's README.
3. After a successful import, Calli's old tables become read-only and are
   labelled "moved to LoopX"; they are deleted only after explicit user
   confirmation. The export file stays on the user's machine and is never
   uploaded.

**Convergence of Calli daemon tables**

| Table / module | Destination |
| --- | --- |
| `todos`, `todo_threads`, `todo_notes`, `todo_proposals`, `sections`, `wishes`, `todo_summaries` | Deleted after import into LoopX Todos |
| `teams`, `coordination`, handover state machine | Deleted; replaced by LoopX Goal collaboration and semantic handoff |
| `schedules`, `schedule_runs` | Open (§12 D4): merge into LoopX scheduling, or keep as a pure host-layer timer |
| `hosts.json`, tunnels | Folded into the `ssh_host_catalog_v0` provider |
| Quota windows, handover settings | L0 observation and user preferences; the decision moves to LoopX |
| Threads, events, turn text | Owned by the external CLI when Attached; read through `/api/chat/sessions/*` |

**Rollback:** each milestone PR only adds a directory or an additive field;
rollback is a revert. Todos written by the import tool are ordinary LoopX
Todos that do not depend on Calli; old tables remain until the user confirms
deletion, so rolling back the migration only requires disabling the imported
results.

## 9. Validation and acceptance

| Claim | Test or evidence | Required result | Boundary / exclusions |
| --- | --- | --- | --- |
| Feature-off parity | Run `loopx status --format json` and a dashboard build without the two directories; diff against baseline | Identical | Says nothing about enabled behavior |
| No second state source in the workbench | Static check: no persisted Goal/Todo/Gate writes under `calli-workbench/src`; reload after clearing browser storage | Page matches projection | Does not prove server correctness |
| Writes only via the control plane | Browser smoke intercepting network: every mutating request hits dry-run/apply/actions/request-control | No other write endpoint | Does not cover future entrypoints |
| One Gate, one confirmation (I3) | Fixture-driven: confirm a Gate on the Lark side, refresh the workbench | Item leaves "Needs you" | Does not prove the live Lark network path |
| Handover respects fencing (I4) | Two simulated profiles; quota crosses threshold; inspect lease and receipt sequence | At most one lease per Todo at any time; a handover receipt exists | Does not prove real quota APIs are accurate |
| One SSH multi-host model | A machine appears once in `ssh_host_catalog_v0`; a fixture per failure code | No duplicate machine; codes map 1:1 to UI copy | Does not replace real-machine deployment acceptance |
| `ext-loopx` contract probing | 1.4.2, 2.0, missing-field and unknown-version fixtures | compatible / compatible / read-only degrade / incompatible | Does not prove every future version |
| Design consistency | Desktop and mobile, light and dark screenshots per the design system's Populated Page Review | Review passes | Screenshots are not a usability study |
| Migration readback | Synthetic Calli DB → dry-run → apply → readback | Counts and text match; reruns are idempotent | Does not cover user-defined fields |

Deterministic conformance, live qualification, performance evidence and
default promotion are reported separately; an unrun or skipped row is not green.

## 10. Operational contract

- **Observability:** the workbench footer shows projection source,
  `schema_version` and freshness; the session fabric reports connection state
  and failure codes through the existing doctor output.
- **Typed failures:** SSH failures keep Calli's seven validated codes; missing
  quota observation is "unknown" and never triggers a switch.
- **Capacity:** one SSH child process per machine; projection polling follows
  the dashboard cadence with no new high-frequency polling.
- **Upgrade/downgrade:** the workbench and `loopx-core` negotiate through the
  schema version; the session fabric can be uninstalled on its own, after
  which Attached sessions remain owned by their CLIs.

## 11. Normative delivery plan

Each milestone is a small, independently mergeable and revertible PR (or a set
of small PRs) on a `codex/` branch in a dedicated worktree, with
`Signed-off-by` on every commit.

| Milestone | Shipped behavior | Entry gate | Exit evidence | Rollback |
| --- | --- | --- | --- | --- |
| **M0 RFC** | This RFC as an English/Chinese pair | — | `check_rfc_bilingual.py` passes; maintainers decide §12 D1–D3 | revert |
| **M1 Workbench shell (fastest path)** | `apps/presentation/calli-workbench/`: three panes, machine switcher, launch bar, session view; reads `/status.json` and `/api/chat/sessions/*` only; no `loopx/` change | M0 merged; D1 decided | Fixture smoke, build, screenshots, no-second-state-source check | Delete directory |
| **M2 Triage and writes** | "Needs you / Running / Failed" drawer; Todo/Gate writes via dry-run/apply; Inbox shared with the Lark steward; a separate additive status-contract PR first if needed | M1 merged | I3 smoke; write-endpoint interception smoke | revert; additive fields may stay |
| **M3 SSH multi-host provider** | First module of `packages/loopx-session-fabric/`: tunnels, reconnect and failure classification for `ssh_host_catalog_v0` | M1 merged; D2 decided | Seven failure-code fixtures; single-host-model check | Uninstall package |
| **M4 Attached adapters** | One PR per CLI, in the order Codex → pi → dsh → others | M3 merged | Bind/detach/turn-boundary smoke per adapter | Uninstall one adapter |
| **M5 Quota observation and governed handover** | Quota windows as an observation provider; handover via fence → lease → receipt; default-off | At least one M4 adapter merged; maintainer authorization of the quota policy | I4 smoke; feature-off parity | Turn off; uninstall module |
| **M6 Calli data import and L1 retirement** | One-time import tool; standalone Calli drops task, team and coordination tables; `ext-loopx` contract probing | M2 merged | Migration readback test; Calli-side regression | Old tables kept until confirmed deletion |

M1 is the fastest usable slice: once M0 merges and the directory is decided, M1
can be reviewed on its own without waiting for any core change.

## 12. Open decisions

| Id | Question | Owner | Options | Recommendation | Dependent milestone |
| --- | --- | --- | --- | --- | --- |
| D1 | Workbench directory | Maintainers | `apps/presentation/calli-workbench/` / `apps/calli-workbench/` | The former, beside the dashboard | M1 |
| D2 | L0 package boundary | Maintainers | One `loopx-session-fabric` package / several per-capability packages | One package with internal module directories; split when a second consumer appears | M3 |
| D3 | Naming | Maintainers | Keep "Calli" as the product name, or a neutral name (e.g. "LoopX Workbench") | Directory `calli-workbench` for traceability; UI name decided by maintainers | M1 |
| D4 | Ownership of scheduled tasks | Maintainers | Merge into LoopX scheduling / keep in the host layer | Merge into LoopX scheduling to avoid a second scheduler | M6 |
| D5 | License and history | Author + maintainers | Merge with history / recontribute as new commits | Recontribute as new commits for clean LoopX history and complete DCO | M1 |
| D6 | Long-term relationship with the official dashboard | Maintainers | Coexist / merge components later | Coexist first; decide from usage evidence | After M2 |

Recommendations are non-binding until recorded in Appendix B.

---

## Appendix A: Execution ledger (non-normative)

After merge, entries live under `ledger/calli-workbench-and-session-fabric-v0/`.

## Appendix B: Decision log

| Date | Decision | Owner / approval | Alternatives | Normative sections changed |
| --- | --- | --- | --- | --- |
| — | — | — | — | — |
