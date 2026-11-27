# RFC: Goal-Oriented Multi-Agent Peer Coordination (v0)

- **RFC status:** Accepted
- **Supersedes / closes:** none
- **Delivery maturity:** Partial (M0 read-only peer frontier and collaboration opportunity projection + M1 conditional default-on Turn-envelope digest shipped; M2–M3 remain follow-up slices)
- **Authors / owners:** existing control-plane alignment, collaboration, and directory owners
- **Created:** 2026-11-27
- **Last normative revision:** 2026-11-27
- **Implementation baseline:** `50b4cc9de4`
- **Related contracts:** [Shared Goal Alignment and Governed Amendment Protocol](shared-goal-alignment-and-governed-amendment-v0.md), [Peer Agent Directory and Observation](../../reference/protocols/peer-agent-directory-and-observation-v0.md), [Capable Agent Manager and Semantic Work Handoff](capable-manager-semantic-handoff-v0.md), [Live Team Workspace](live-team-workspace-v0.md)
- **Language mirror:** [中文版](goal-oriented-multi-agent-coordination-v0.zh-CN.md)

## Document map and maintenance contract

Sections 1–10 define the durable design, safety, and acceptance contract.
Section 11 defines the normative delivery plan (starting from the M0 slice
shipped against `50b4cc9de4`). Section 12 lists unresolved design decisions for
M1–M3. Appendices record non-normative execution lineage and evidence. The English document and its [Chinese mirror](goal-oriented-multi-agent-coordination-v0.zh-CN.md)
are semantic mirrors; any discrepancy between the two is a defect.

---

## 1. Decision summary

1. **Make same-Goal peer frontiers discoverable without adding a new database or authority.** Extend `shared_goal_alignment_v0` additively with bounded `peer_frontiers` and `collaboration_opportunities` derived deterministically in a single TypeScript owner (`loopx/control_plane/goals/peer_coordination.ts`) from existing canonical Todos and task leases.
2. **Preserve zero-authority read projections (`grants_authority: false`).** Discovering a collision risk, an independent verification need, an unleased high-priority peer lane, or shared-domain synergy never transfers claims, overrides leases, edits Todos, or triggers auto-delivery on its own.
3. **Shift from human-relayed pull to autonomous goal-oriented awareness and bilateral handshakes.** Keep explicit claim/lease/inbox owners unchanged (`shared_goal_alignment_v0`, `peer_agent_directory_v0`, `manager-inbox`), while shipping both bounded pull-side discovery (M0) and conditional default-on Turn-envelope digests (`peer_coordination_digest`, `<=300 B` serialized delta including field key, `~75 tokens` / `<0.04%` of a 200K context window; omitted at `0 B` when inactive) so specialized peers on one Goal discover urgent collisions, verification needs, or idle work without manual CLI pulls or human relays.
4. **Separate shipped M0+M1 read-only discovery from future M2–M3 write/host extensions.** M0 (`loopx shared-goal-alignment` / `goal-alignment`) and M1 conditional default-on Turn-envelope digests (`loopx quota should-run` -> `TurnEnvelope`, with explicit `off` opt-out retained for frozen benchmark baselines) require zero manual opt-in on normal product paths; cooldown-gated bilateral inbox handshakes (M2) and host-neutral Antigravity/non-Codex observer adapters (M3) remain follow-up slices gated by their own acceptance evidence. Note that `0 B` serialized omission delta when inactive is distinct from live runtime context-tax measurement against the `<5%` budget.
5. **This RFC does not approve** autonomous multi-agent self-merging, automatic Todo reassignment across owners, or unconditional Turn-envelope size growth.

## 2. Problem and motivation

When multiple specialist Agents share one LoopX Goal (for example, a microkernel/code-simplification Agent opening focused PRs while a benchmark/verification Agent validates efficiency gains and catches subtle regressions), today's coordination still falls back on a human operator to bridge the two sessions:

- A user frequently has to tell Agent B: *"Agent A has some simplification PRs; go coordinate, verify, and polish them together."*
- Meanwhile, peers lack a pull-side view of which peer lanes already overlap in target/write-scope, need independent verification, or sit unleased on a high-priority lane.

Five concrete structural gaps explain why spontaneous peer-to-peer coordination fails today:

1. **Opaque scalar peer counts:** `shared_goal_alignment_v0` currently exposes only `other_agent_claimed_advancement_count` (an integer) and `peer_claimed_bound_todo_ids` (`bound_agent === caller`). Peer priorities, domains, repositories, `target_key`s, write scopes, and `completion_validation_required` flags stay hidden.
2. **Push-only inbox delivery without pull-side synergy matching:** `manager-inbox` works well once Sender A already knows exact Peer B and crafts a bespoke brief, yet neither agent can deterministically ask *"which active peer lane needs independent verification or touches my current domain right now?"*
3. **Collision blind spots prior to lease conflicts:** Two peers working on overlapping `required_write_scopes` or identical `target_key` only discover contention at hard lease/worktree failure time rather than via an advisory pre-edit signal.
4. **Context-budget hazard for peer awareness:** Naively dumping full peer work trees into Turn envelopes violates the `<5%` control-plane context tax invariant; discovery must be pull-first and tightly bounded (`MAX_PEER_FRONTIER_ITEMS = 12`, `MAX_COLLABORATION_OPPORTUNITIES = 8`), with deterministic cursor/ID deduplication.
5. **Asymmetric host reachability:** `resolve-peer-route` reports `unavailable (host_observer_unavailable)` for hosts without an implemented observer adapter (such as Antigravity desktop sessions vs. Codex app-server threads), forcing senders to degrade to unrouted inbox writes or manual prompt copy-paste.

### Invariants

- **Single state authority:** Peer coordination introduces zero parallel Todo tables, shadow claim files, or secondary intent stores; every field projects from canonical Todo/lease snapshots (`file_v0` / `sqlite_v0`).
- **Single semantic reducer:** One TypeScript module (`peer_coordination.ts`) defines eligibility, overlap matching, priority ordering, deduplication, and caps; Python/CLI callers are pure transport/formatting adapters.
- **Deterministic & authority-free outputs:** Identical input snapshots yield byte-identical sorted outputs and deterministic `opportunity_id`s, always carrying `grants_authority: false` and `read_only: true`.
- **Fail closed for caller authority, fail bounded for corrupt peer metadata:** Unreadable caller lease metadata continues to reject alignment projection; unreadable peer lease metadata yields `lease_active: null` rather than breaking the caller's read-only view.
- **Public-safe boundaries:** No private local paths, raw chat transcripts, or credentials appear in `peer_frontiers` or `collaboration_opportunities`.

## 3. Scope and non-goals

### In scope

- Additive M0 extension of `shared_goal_alignment_v0` (`peer_frontiers`, `collaboration_opportunities`) computed by TypeScript and exposed via `loopx shared-goal-alignment` / `goal-alignment`.
- Normative protocol specification for pull-side matching, deterministic `opportunity_id` deduplication, conditional default-on bounded Turn-envelope digest injection (M1, `<=300 B`), cooldown-gated bilateral `manager-inbox` handshake (M2), and host-neutral route observation profiles (M3).

### Non-goals

- Replacing `shared_goal_intent_v0`, `goal_amendment_proposal_v0`, `task_lease`, or `peer_agent_directory_v0`.
- Granting agents unilateral claim-stealing, priority mutation, or self-merge permission.
- Adding host-specific chat polling loops or scraping desktop transcripts outside registered public-safe bindings.

## 4. Current-system contract

At baseline `50b4cc9de4`:

- **Goal membership & directory (`loopx/control_plane/agents/directory.py`, `agent-directory`):** Projects registered agents, current Todo summary (`todo_id`, `status`, `claimed_by`, `title`), `stale_claim_hint`, and `peer_route`, without computing pair-wise work synergy, write-scope collisions, or verification requests.
- **Shared Goal alignment (`loopx/control_plane/goals/shared_goal_alignment.ts`, `shared_goal_work.ts`, `shared_goal_work_source.py`):** Reads one canonical snapshot (`canonical_todo_snapshot`), counts `current_agent_claimed_advancement_count`, `unclaimed_advancement_count`, and `other_agent_claimed_advancement_count`, checks caller leases and `peer_claimed_bound_todo_ids`, and returns `read_only: true`. It currently discards peer `task_domain`, `task_repository`, `target_key`, `required_write_scopes`, `completion_validation_required`, `priority`, and `lease_active`.
- **Execution orchestration (`loopx/control_plane/quota/peer_orchestration.ts`):** Handles task-scoped coordinator activation of `peer_agent_activation` lanes (`bound` / `executing`) when the caller holds coordinator capability, not equal-rank `peer_v1` horizontal discovery.
- **Write-scope overlap & repository identity (`work_items/task_lease_acquire_decision.ts`, `work_items/task_lease_repository.ts`, `todos/work_requirements.ts`):** Existing TS owners already normalize `task_repository`, normalize `required_write_scopes`, and test overlap (`evaluateQuotaSchedulingWriteScopesOverlap`, `repositoryScopesMayOverlap`).

## 5. Proposed architecture

### Ownership and authority

`loopx/control_plane/goals/peer_coordination.ts` is the sole semantic owner of multi-agent goal-oriented matching projections. `shared_goal_work.ts` and `shared_goal_alignment.ts` compose it cleanly without re-implementing matching heuristics in Python. All writeback, lease acquisition, and inbox acceptance continue to run strictly through their existing owners (`todo claim`, `task-lease`, `manager-inbox`).

### State model and schema

`shared_goal_alignment_v0` retains all existing top-level fields identically and adds two read-only arrays (defaulting to `[]` when callers supply legacy preselected inputs):

1. **`peer_frontiers`** (sorted by priority rank `P0..P4`, `agent_id`, `todo_id`; capped at `12`):
   - `todo_id`, `agent_id`, `priority`, `action_kind`, `task_domain`, `task_repository`, `target_key`, `required_write_scopes`, `completion_validation_required`, `lease_active` (`true | false | null`).
2. **`collaboration_opportunities`** (sorted by severity rank -> peer priority -> `peer_todo_id` -> `caller_todo_id` -> `opportunity_id`; capped at `8`):
   - `opportunity_id`: `"opp_" + sha256(canonical_json([kind, peer_agent_id, peer_todo_id, caller_todo_id ?? "", ...matched_on]))[0..16]`
   - `kind`: one of:
     - `shared_target_collision_risk` (`advisory_action: "deconflict_before_edit"`) — shared non-null `target_key` or overlapping `required_write_scopes` on compatible repositories.
     - `peer_verification_needed` (`advisory_action: "offer_independent_verification"`) — `completion_validation_required === true` matching verifier `action_kind`, shared domain/repository, or an idle caller discovering verification demand.
     - `peer_unleased_lane_attention` (`advisory_action: "inspect_peer_status_before_duplicating"`) — `lease_active === false` on high-priority (`P0`/`P1`) peer work matching the caller's domain/target (or idle caller discovery).
     - `shared_domain_synergy` (`advisory_action: "align_with_peer_deliverable"`) — matching non-null `task_domain` across compatible repositories (suppressed on pairs already flagged as collision risks).
   - `peer_agent_id`, `peer_todo_id`, `caller_todo_id` (`string | null`), `matched_on` (`string[]`), `advisory_action`, `grants_authority: false`.

### Command and handshake lifecycle

- **M0 (Shipped):** On-demand or pre-amendment pull via `loopx shared-goal-alignment --goal-id <g> --agent-id <a>` recomputes full `peer_frontiers` and `collaboration_opportunities` (including `shared_domain_synergy`) over canonical `file_v0`/`sqlite_v0` snapshots.
- **M1 (Shipped — conditional default-on):** Normal `loopx quota should-run` Turns automatically invoke TS `peer_coordination_digest.ts` without manual opt-in. When the Goal has `>= 2` registered agents AND unseen qualifying `opportunity_id`s relative to the recipient's live Turn cursor (`peer-coordination-cursors/<agent_id>.json`), `TurnEnvelope` (`v0` / `v1`) embeds `peer_coordination_digest` (`schema: "peer_digest_v0"`, `read_only: true`, `grants_authority: false`) capped strictly at `<=300 B` serialized JSON delta (including `,"peer_coordination_digest":` field key overhead). Busy Turns surface only `shared_target_collision_risk` (`"collision"`) and `peer_verification_needed` (`"verify"`); idle Turns (`caller_lanes == []`) surface `peer_verification_needed` (`"verify"`) and high-priority `peer_unleased_lane_attention` (`"unleased"`). Single-agent Goals, empty qualifying sets, unchanged cursors, `--dry-run`, scheduler-suppressed skip states, and explicit `off` overrides (`coordination.peer_coordination_digest: "off"` or `LOOPX_PEER_COORDINATION_DIGEST=off`) omit the field (`0 B` serialized delta). Live non-preview non-skipped Turns commit and auto-prune the cursor to current active IDs.
- **M2 (Future opt-in):** Structured `offer` / `accept` / `decline` handshake over `manager-inbox` keyed by `(opportunity_id, basis_digest)` with sender cooldown after decline or unreachable host route.

### Provider or extension contract

Logical alignment projection stays 100% provider-neutral. Host observers (Codex app-server, Antigravity desktop/daemon, IDE sidecars) expose live presence and submission handles through optional adapter profiles; absence of a live host observer only leaves `peer_route` in `host_observer_unavailable` while durable Todo/lease coordination and `manager-inbox` persistence continue uninterrupted.

## 6. Alternatives and design choices

1. **Rejected: Storing a separate "team coordination board" file or SQLite table.** Creates split-brain drift vs. canonical Todos/leases and requires dual-write synchronization. *Reopen only if* cross-Goal ephemeral rendezvous proves unrepresentable in canonical Todo metadata.
2. **Chosen: Additive fields on existing `shared_goal_alignment_v0` over existing TS scope/repo codecs.** Avoids creating a redundant CLI surface or duplicating glob/repository normalization, stays backwards-compatible with legacy callers, and preserves zero-cost read-only invariants.
3. **Rejected: Automatic peer interruption on every Todo change.** Causes thundering-herd prompt noise and blows the `<5%` context budget. Cursor-deduplicated M1 digests plus cooldowns avoid runaway chatter.

## 7. Safety, privacy, and compatibility

- **Default parity:** Existing `shared_goal_alignment` JSON callers receive identical top-level fields with `peer_frontiers` and `collaboration_opportunities` appended; legacy requests omitting `caller_lanes`/`peer_frontiers` receive `[]` safely.
- **Authority & write isolation:** Read projections enforce `read_only: true` and `grants_authority: false`; host prompts still require explicit lease/inbox gates before external writes or PR merges.
- **Public-safe filtering:** Only validated public identifiers (`todo_id`, `agent_id`, `task_domain`, `task_repository`, `target_key`, normalized relative write scopes) enter the projection; free-text prose notes and private paths remain excluded.
- **Downgrade/mixed versions:** Older Python/CLI readers ignore unknown additive keys cleanly; TS decoder checks `caller_lanes` and `peer_frontiers` strictly when supplied and falls back to `[]` when absent.

## 8. Migration and rollback

Not applicable for M0 beyond additive code rollback: M0 adds read-only fields with zero persistent schema migration or disk format change. Reverting the commit cleanly restores exact prior output.

## 9. Validation and acceptance

| Claim | Test or evidence | Required result | Boundary / exclusions |
| --- | --- | --- | --- |
| TS coordinator deterministically detects all 4 opportunity kinds, collision suppression, idle caller discovery, scope/repo boundaries, and caps | `node --experimental-strip-types --test tests/control_plane_ts/shared_goal_alignment.test.ts` | All TS unit assertions pass; zero regressions on 28 existing tests | Synthetic typed payloads; does not test SQLite I/O |
| Canonical `file_v0` and `sqlite_v0` snapshots project matching `peer_frontiers`, `collaboration_opportunities`, and conditional M1 Turn digests without single-agent bloat | `uv run --extra test python -m pytest tests/control_plane/test_shared_goal_alignment.py tests/control_plane/test_shared_goal_alignment_cli.py tests/control_plane/test_peer_coordination_digest.py tests/control_plane/test_cli_output_budget.py` | All parametrized File+SQLite, CLI, TurnEnvelope, cursor-dedup, and `0 B` baseline output-budget assertions pass | Single-host canonical snapshots; no M2 handshake or M3 host daemon wiring |
| Bilingual RFC index & governance stay valid | `python3 scripts/generate_rfc_status_index.py --check` & `python3 scripts/check_rfc_bilingual.py --base origin/main --head HEAD` | Exit `0` | Structural mirror parity only, not automated translation grading |

## 10. Operational contract

- **Bounded output:** At most `12` `peer_frontiers` and `8` `collaboration_opportunities` per call; CLI markdown renders compact tables/bullets sorted by severity.
- **Diagnosable failure:** Caller lease corruption continues failing closed (`EffectRuntimeRequestError`), while malformed peer lease records degrade locally to `lease_active: null` so peer errors never DoS unrelated agents.

## 11. Normative delivery plan

| Milestone | Shipped behavior | Entry gate | Exit evidence | Rollback |
| --- | --- | --- | --- | --- |
| **M0** | TS `peer_coordination.ts`, canonical `shared_goal_work_source.py` field forwarding, additive `shared_goal_alignment_v0` & CLI Markdown/JSON | Clean `origin/main` alignment baseline | TS + File/SQLite pytest + CLI + premerge canaries pass | Revert additive PR |
| **M1** | Conditional default-on Turn-envelope `peer_coordination_digest` (`<=300 B` incl. key, `0 B` omitted when inactive/unchanged, busy/idle filtering, live cursor commit & prune, explicit `off` override) | Canonical `file_v0`/`sqlite_v0` snapshot + `TurnEnvelope` v0/v1 integration | TS `peer_coordination_digest.test.ts` + File/SQLite `test_peer_coordination_digest.py` + zero baseline `cli-output-budget` regression (`canary premerge`) | Set `coordination.peer_coordination_digest="off"` or revert M1 wiring |
| **M2** | Bilateral `manager-inbox` offer/decline handshake keyed by `(opportunity_id, basis_digest)` with cooldown | M1 + exact-idempotency review | Multi-agent inbox handshake integration suite passes | Disable opt-in auto-offer |
| **M3** | Host-neutral observer profile (Antigravity/desktop alongside Codex) + multi-agent efficiency telemetry | M2 + host-adapter opt-in | Live dual-agent qualification without human intermediary | Retain `host_observer_unavailable` fallback |

## 12. Open decisions

1. **D1 (Resolved — 2026-11-27, Turn-envelope default path & filtering — M1):** M1 requires **no manual opt-in** on normal product paths (`loopx quota should-run` -> `TurnEnvelope`). When `>= 2` registered agents share a Goal and unseen qualifying `opportunity_id`s exist relative to the cursor, emit `peer_coordination_digest` (`<=300 B` serialized delta including field key): busy Turns include only `shared_target_collision_risk` + `peer_verification_needed`; idle Turns include `peer_verification_needed` + high-priority `peer_unleased_lane_attention`. Single-agent Goals, zero new qualifying opportunities, unchanged cursors, skipped/dry-run modes, and explicit `off` settings omit the field (`0 B`). `shared_domain_synergy` remains pull-only via `shared-goal-alignment` / `goal-alignment`. Live runtime `<5%` control-plane tax remains verified explicitly (serialized `0 B` omission does not waive benchmark runtime verification).
2. **D2 (Decline cooldown window — M2):** What default cooldown duration/epoch binding prevents repeated bilateral inbox offers on unchanged frontiers? **Owner:** `manager-inbox` maintainer.
3. **D3 (Antigravity route observer protocol — M3):** Should host presence come from a signed local daemon endpoint or per-app file lease projection? **Owner:** desktop/host maintainers.

---

## Appendix A: Execution ledger (non-normative)

- M0+M1 shipped cleanly on the updated PR branch (`peer_coordination.ts` + `peer_coordination_digest.ts` + `TurnEnvelope` v0/v1 wiring; subsequent M2–M3 checkpoint ledgers may open under `ledger/`).

## Appendix B: Decision log

| Date | Decision | Owner / approval | Alternatives | Normative sections changed |
| --- | --- | --- | --- | --- |
| 2026-11-27 | Adopt M0 additive projection over `shared_goal_alignment_v0` reusing `evaluateQuotaSchedulingWriteScopesOverlap` | Pending maintainer PR review | Dedicated standalone `peer-synergy` table/RPC | §§1–11 |
| 2026-11-27 | Resolve D1 & ship M1 conditional default-on `peer_coordination_digest` (`<=300 B`, `0 B` omitted when inactive, busy/idle filtering, cursor auto-prune, explicit `off` escape hatch) | User-approved direction ("为啥仍需要主动调用…路径能足够自动和智能么") + maintainer PR review | Require manual per-Goal opt-in flag (`opt-in` default-off) or pull-only CLI | §§1, 3, 5, 9, 11, 12 |

## Appendix C: Evidence registry

| Evidence id | Claim | Baseline / environment | Artifact or command | Result | Privacy / validity boundary |
| --- | --- | --- | --- | --- | --- |
| E1 | TS + File/SQLite alignment parity and M0 peer coordination unit coverage | Clean PR worktree | `tests/control_plane_ts/shared_goal_alignment.test.ts` & `tests/control_plane/test_shared_goal_alignment*.py` | Pass in local worktree | Synthetic temp fixtures |
| E2 | M1 automatic Turn-envelope digest (`<=300 B`), cursor dedup/prune, explicit opt-out, and zero baseline CLI output regression across `file_v0`/`sqlite_v0` | Clean PR worktree | `tests/control_plane_ts/peer_coordination_digest.test.ts`, `tests/control_plane/test_peer_coordination_digest.py`, & `test_cli_output_budget.py` | Pass in local worktree | Synthetic temp fixtures; live `<5%` multi-agent benchmark telemetry stays scoped separately |

## Appendix D: Rejected or superseded alternatives

- **Python-side matching heuristics:** Rejected because `shared_goal_alignment.ts` and `task_lease_acquire_decision.ts` already own alignment and scope-overlap normalization in TypeScript; splitting matching rules into Python would reintroduce py/ts semantic drift.

## Appendix E: Incident and review lessons

- Multi-agent coordination failures in practice stem less from lack of raw inbox transport and more from invisible per-lane state (`other_agent_claimed_advancement_count` integer blindness) plus high host-route unavailability between heterogeneous agents, requiring manual human relays until made queryable and bounded.
