# RFC: Session and Quota Fabric for Single-Workbench Convergence (v0)

- **RFC status:** Accepted
- **Supersedes / closes:** none
- **Delivery maturity:** Partial. The additive L0 codecs and handover guard (`session_quota_fabric.ts`) ship on this baseline; host runtime adapters and dashboard UI composition remain staged follow-on slices.
- **Authors / owners:** Architecture convergence workgroup; control-plane ownership, package boundaries, and merge authorization belong to LoopX maintainers
- **Created:** 2026-11-17
- **Last normative revision:** 2026-11-17
- **Implementation baseline:** `31944d8354`
- **Related contracts:**
  [Agent session execution modes](agent-session-execution-modes-v0.md),
  [Desktop execution frontends](desktop-execution-frontends-v0.md),
  [Single-owner local daemon](single-owner-local-daemon-v0.md),
  [Monorepo distribution split](monorepo-distribution-split-v0.md),
  [Overall roadmap](loopx-overall-roadmap-v0.md),
  [Status data contract](../../status-data-contract.md),
  [Compute quota](../../quota-allocation.md),
  [Design system](../../development/design.md)
- **Language mirror:** [中文版](session-and-quota-fabric-v0.zh-CN.md)

## Document map and maintenance contract

Sections 1–10 define the durable design and acceptance contract. Section 11
defines the normative delivery plan. Section 12 records open decisions where
recommendations are non-binding until accepted by maintainers. No dated log
headings appear above appendices; historical checkpoints belong in
[`ledger/`](ledger/README.md). This English specification and its
[Chinese mirror](./session-and-quota-fabric-v0.zh-CN.md) are semantic mirrors and stay synchronized for
all normative rules.

---

## 1. Decision summary

1. **What becomes authoritative or changes.** LoopX extracts multi-host SSH
   readiness (`session_fabric_ssh_observation_v0`), opaque-profile quota-window
   observations (`session_quota_window_observation_v0`), and fence-bound handover
   evaluation (`governed_session_handover_proposal_v0`) into a standalone L0
   control-plane contract implemented in
   [`session_quota_fabric.ts`](../../../loopx/control_plane/collaboration/session_quota_fabric.ts),
   independently deliverable without waiting for a new frontend shell.
2. **What stays unchanged.** `loopx-core` remains the sole writer of Goal, Todo,
   claim, lease, quota, Turn, and Gate state; `ssh_host_catalog_v0` remains the
   authoritative SSH alias catalog; the single Goal Workbench in
   `apps/presentation/dashboard` remains the official presentation entry point.
3. **Default and opt-in boundary.** Feature-off behavior is byte-for-byte
   identical to baseline: when L0 observations are absent,
   `ssh_host_catalog_v0` emits `{ alias }` entries only, and governed handover
   never executes without explicit Turn/lease fencing or (when enabled) shared
   Gate confirmation.
4. **Principal safety and UI convergence constraint.** No 40k–192k LOC second
   frontend application (`apps/presentation/calli-workbench/`, `apps/workbench/`)
   or duplicated task/daemon authority is introduced. Session, host, quota, and
   triage surfaces embed directly into the existing Goal Workbench views.
5. **What this RFC does not approve.** Automatic credential migration, silent
   `attached_host` <-> `managed_runtime` mode switching, parallel Node Todo
   daemons, or automatic quota spend on failed/unknown windows remain rejected.

## 2. Problem and motivation

Multi-host, multi-CLI workbench proposals (#8778, #8782–#8784, #8843) demonstrated
strong demand for unifying local and SSH-remote agent sessions alongside quota-aware
profile switching. However, shipping that capability via a standalone 40k–192k LOC
frontend and parallel Node daemon (`apps/presentation/calli-workbench/` in
#8783/#8784; `apps/workbench/` across 881 files in #8843) fragments the product:

- Users are forced between the official Goal Workbench (`apps/presentation/dashboard`)
  and a second unrelated shell with separate proxy paths, CSS tokens, and routes.
- Existing baseline seams — `configured_ssh_host_aliases` (`ssh_host_catalog_v0`),
  `/api/chat/codex-attached`, and `GoalAgentWorkPage` — are duplicated instead
  of extended.
- Daemon-local state stores (`todos`, schedules, revert handlers) race with Core's
  single-owner lease and Turn fencing guarantees.

Decomposing L0 host/session/quota primitives away from frontend chrome lets LoopX
land the real substrate gaps first and embed the UI cleanly inside the existing
single Goal Workbench.

### Invariants

- **I1 Single host and work authority.** Goal/Todo/lease/Gate state stays owned
  by `loopx-core`; SSH host identity stays owned by `ssh_host_catalog_v0`. L0
  observations targeting aliases absent from `ssh_host_catalog_v0` fail closed.
- **I2 Opaque quota privacy boundary.** Quota observations carry opaque
  `profile_id` tokens, never emails, secrets, bearer tokens, or home paths.
- **I3 Shared Gate confirmation.** When `ask_before_switch=true`, handover resolves
  to `user_gate_required` on the existing Inbox/Gate projection rather than a
  custom modal authority.
- **I4 Turn and lease fencing across handover.** Handover cannot implicitly flip
  `attached_host` <-> `managed_runtime` modes and remains `await_turn_fence`
  until both `current_turn_fenced=true` and `previous_lease_released=true`.
- **I5 Feature-off parity.** Absent L0 observations yield byte-identical
  `ssh_host_catalog_v0` output; `"unknown"` quota availability never triggers
  handover (`no_handover_needed`).

## 3. Scope and non-goals

### In scope

- Typed TypeScript L0 codecs and evaluators for additive SSH readiness
  observations (`session_fabric_ssh_observation_v0`), opaque quota windows
  (`session_quota_window_observation_v0`), and fenced handover proposals
  (`governed_session_handover_proposal_v0`) in `loopx/control_plane/collaboration/session_quota_fabric.ts`.
- Architectural convergence rules embedding multi-host, session, and triage
  surfaces into `apps/presentation/dashboard` instead of adding a second app.

### Non-goals

- Importing a standalone `apps/workbench/` or `apps/presentation/calli-workbench/`
  package, Node SQLite daemon, or browser build toolchain.
- Bypassing `loopx/presentation/codex_attached_api.py` ownership boundaries or
  shipping concrete OpenSSH child-process supervisors inside this RFC slice.

## 4. Current-system contract (baseline `31944d8354`)

- **SSH catalog:** [`ssh_host_catalog.py`](../../../loopx/control_plane/status/ssh_host_catalog.py)
  serves `/ssh-hosts` with `{ ok: true, schema_version: "ssh_host_catalog_v0", hosts: [{ alias }] }`.
- **Dashboard SSH client:** [`ssh-host-catalog.ts`](../../../apps/presentation/dashboard/src/data/ssh-host-catalog.ts)
  already parses `ssh_host_catalog_v0` and invokes `/api/ssh-source/ensure` and
  `/api/ssh-source/goal-lifecycle`.
- **Attached sessions & Goal UI:**
  [`codex_attached_api.py`](../../../loopx/presentation/codex_attached_api.py)
  currently filters `row["host_surface"] == "codex-app"` in `bound_conversations()`,
  while [`attached-agent-conversations.tsx`](../../../apps/presentation/dashboard/src/features/personal-workspace/attached-agent-conversations.tsx)
  and [`goal-agent-work-page.tsx`](../../../apps/presentation/dashboard/src/features/personal-workspace/goal-agent-work-page.tsx)
  render bound attached and managed conversations inside one Goal view.
- **L0 contract module (delivered in this baseline):**
  [`session_quota_fabric.ts`](../../../loopx/control_plane/collaboration/session_quota_fabric.ts)
  provides zero-I/O validated projections and handover state transitions.

## 5. Proposed architecture

### Ownership and authority

| Layer | Owner | Contract / Surface |
| --- | --- | --- |
| L0 SSH & quota observation | Optional host provider implementing `session_quota_fabric.ts` | Emits `session_fabric_ssh_observation_v0` & `session_quota_window_observation_v0` |
| L1 Work, quota & handover fence | `loopx-core` (`control_plane/collaboration/`, `quota/`) | Validates handover via `evaluateGovernedSessionHandover`, enforces lease/Turn fence |
| L1/L2 Attached API generalization | [`codex_attached_api.py`](../../../loopx/presentation/codex_attached_api.py) follow-on | Broadens `host_surface` filter by registered capability rather than hardcoding `"codex-app"` |
| L3 Single Goal Workbench | `apps/presentation/dashboard` | Embeds host selector, session timeline, quota indicator, and Gate triage drawer |

### State model and schema

1. `session_fabric_ssh_observation_v0`: validates `alias`,
   `connection_state` (`connected | degraded | disconnected | unknown`),
   `failure_code` (strictly the seven codes `no_ssh | host_key | auth | unreachable | ssh_failed | daemon_missing | forward_denied`,
   required when `degraded | disconnected` and forbidden otherwise),
   `checked_at`, and `tunnel_latency_ms`. `projectSshHostCatalogPayload` attaches
   observations strictly to known `ssh_host_catalog_v0` aliases and leaves
   un-observed hosts as bare `{ alias }`.
2. `session_quota_window_observation_v0`: validates opaque `profile_id`
   (forbidding `@`, `/`, `.ssh/`, `sk-`, `ghp_`, `bearer`), `availability`
   (`available | near_limit | exhausted | unknown`), `used_ratio` (`[0, 1]` or
   strictly `null` when `unknown`), `resets_at`, and `eligible_models`.
3. `governed_session_handover_proposal_v0`: rejects cross-mode switches
   (`from_session_mode !== to_session_mode`), rejects `from_profile_id === to_profile_id`,
   returns `no_handover_needed` when `unknown` or below `threshold_ratio`,
   `user_gate_required` when `ask_before_switch=true`, `await_turn_fence` when
   fencing/release is incomplete, and `ready_for_lease_reacquisition` only when
   all preconditions hold.

## 6. Alternatives and design choices

Directly answers open convergence review question D1/D2 across #8778, #8782–#8784, and #8843:

| Option | Verdict | Architectural trade-off |
| --- | --- | --- |
| A. Ad-hoc per-CLI patches inside existing views | Rejected | Repeats SSH error handling and quota logic across dashboard hooks with no shared contract |
| B. **Independent L0 Session & Quota Fabric + single Goal Workbench embedding** | **Chosen** | Delivers validated SSH/quota/handover contracts immediately without waiting on UI overhaul; avoids duplicating app shells or daemons; keeps incremental PRs < 1k LOC |
| C. Separate 40k–192k LOC frontend/daemon (`calli-workbench` / `apps/workbench`) | Rejected | Splits navigation, styling, proxy security, and daemon ownership; duplicates existing `ssh-host-catalog.ts` and `GoalAgentWorkPage`; large review surface |

## 7. Safety, privacy, and compatibility

- **Default-off parity:** Absence of L0 observation payloads keeps `ssh_host_catalog_v0`
  output identical to `loopx/control_plane/status/ssh_host_catalog.py`.
- **Credential & path fail-closed:** `session_quota_fabric.ts` rejects any
  SSH alias or quota `profile_id` / model string carrying `/Users/`, `/home/`,
  `.ssh/`, `@`, `/`, `sk-`, `ghp_`, or `bearer`.
- **Unknown availability safety:** Missing/unknown quota observations force
  `handover_eligible=false` and `disposition="no_handover_needed"`.

## 8. Migration and rollback

Existing `apps/presentation/dashboard` workflows continue unmodified. Each L0
slice (`session_quota_fabric.ts`, optional backend endpoints, optional dashboard
drawer panels) is purely additive and reversible via standard commit revert with
no persisted schema migration required.

## 9. Validation and acceptance

| Claim | Evidence / Command | Required result | Exclusions / Boundary |
| --- | --- | --- | --- |
| Bilingual & RFC index integrity | `python3 scripts/check_rfc_bilingual.py` and `python3 scripts/generate_rfc_status_index.py --check` | Exit `0` | Does not verify runtime behavior |
| L0 TS type safety & digest boundary | `npm run typecheck:control-plane` and `node --test tests/control_plane_ts/content_digest_single_owner.test.ts` | Exit `0`, all pass | Static contract check only |
| L0 codecs, feature-off parity, handover fencing & privacy fail-closed | `node --test tests/control_plane_ts/session_quota_fabric.test.ts` | All cases pass | Unit-level fixtures; live SSH tunnel process remains M1/M2 |
| Vocabulary convergence & public scan | `python3 examples/semantic-vocabulary-drift-smoke.py` and `loopx check` | `ok` | Does not cover downstream packages |

## 10. Operational contract

Unknown SSH or quota state never elevates privileges or triggers background
handover. Surfacing remains read-only through `ssh_host_catalog_v0` and
Goal-scoped projections; failures emit deterministic typed errors.

## 11. Normative delivery plan

| Milestone | Shipped behavior | Entry gate | Exit evidence | Rollback |
| --- | --- | --- | --- | --- |
| **M0 Convergence RFC + L0 Contract** | Bilingual RFC (`session-and-quota-fabric-v0`) + `session_quota_fabric.ts` + TS unit tests | Baseline `31944d8354` | `session_quota_fabric.test.ts`, `docs-governance-smoke.py`, `semantic-vocabulary-drift-smoke.py` | Revert RFC & module |
| **M1 Attached API & Host Provider** | Generalize `codex_attached_api.py` `host_surface` routing + opt-in SSH probe wiring into `ssh_host_catalog_v0` | M0 merged | Feature-off parity test + seven SSH failure-code fixture tests | Disable provider flag |
| **M2 Single Goal Workbench UI & Handover Wire-up** | Embed host readiness, quota window badge, and Gate-bound handover trigger inside `GoalAgentWorkPage` (`apps/presentation/dashboard`) | M1 merged | Playwright desktop/narrow viewport smoke + visual evidence + turn-fence handoff integration test | Revert UI panel slice |

## 12. Open decisions

| Id | Question | Owner | Recommendation | Dependent milestone |
| --- | --- | --- | --- | --- |
| D1 | Exact generalization list for non-Codex `host_surface` names in `codex_attached_api.py` | Maintainers | Resolve dynamically from registered `agent_capabilities` host surfaces | M1 |
| D2 | Default `ask_before_switch` policy for quota handover | Maintainers | Default `true` (`user_gate_required`) until operator explicitly opts into fenced auto-switch | M2 |
