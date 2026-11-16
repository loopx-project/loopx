# C1 source convergence and historical integration checkpoints

- **Date:** 2026-10-30 (rolling integration checkpoints through `#8266`)
- **Status:** Unmerged integration evidence; C1 remains open.
- **Source:** Unmerged C1 unification branch integrating target `main` checkpoints (see [packaged heartbeat comparison](2026-10-28-packaged-heartbeat.md)).
- **Language mirror:** [中文版](2026-10-30-c1-source-convergence.zh-CN.md)

## Shipped vs. unshipped convergence boundaries

| Boundary / owner | Current C1 state | Retained compatibility & exit gate |
| --- | --- | --- |
| Complete-source `work_contract` (`compose_turn_decision`, `completePlanningFacts`) | **Unmerged in C1:** unifies guard, Host, driver, MCP, benchmark, Kiro, DSH, SSH, and bound `generic_cli` (`quota_settlement_plan_v2`, `task_context` via `#8691`). | Unbound turnless observation (`--scheduler-observation`), raw `--turn-envelope` v0 display (`EdgeBench` / explicit compatibility callers), and historical v0 captures keep explicit readers until replacement and recovery windows qualify. |
| File-first progressive/delta & field reuse (`#7560`, `#7627`, `#7935`, `#8186`) | **Shipped on `main` & adopted by C1:** invocation-local initial plan, observed-field reuse, and digest-bound file deltas replace the retired unmerged `outcome_context_projection_v0` mandatory reading plan. | Partial/truncated reads never establish baselines; independent `delta_delivery=auto` with `context_delivery=off` emits no progressive navigation; default ordinary CLI adoption requires §9 qualification. |
| Numeric integrity (`#7557`, `#7633`, `#7643`, [numeric checkpoint](2026-10-29-complete-numeric-integrity.md)) | **Shipped owners + C1 transport:** sparse numeric tokens (`1.0` vs `1`, `-0.0`, unsafe integers) and `hash_codec` v1/v2 preserve original Python JSON values across TypeScript edit plans. | Persisted Python JSON/action-hash preimage stays unchanged so existing Turn keys remain valid; exact-byte capture digests remain separate from normalized JS hashes. |
| Native selection & replay checkpoints (`codex_context_history.py`) | **Unmerged in C1:** binds hook replay (`native_resume` / `selected_resume`) to physical inode/interval digests and append-only transcript coverage across repeated resumes. | Missing output, changed Todo/body, non-append-only tail, or compaction forces a fresh read; `cli_version` covers writer provenance only and never grants model-retention proof. |

## Current private-plan boundary

New complete-source Turns write `loopx_turn_plan_v1` with one original
`work_contract`, its source digest, and capture header; the complete planner no
longer emits a duplicate v0 executor adapter (`planning_facts`, Host input, and
`turnPlanWorkSource` share TypeScript owners while Python keeps persisted
action-hash numeric encoding).

| Production chain | C1 behavior & owner | Retained compatibility |
| --- | --- | --- |
| Turn CLI & launch decision → plan | Passes complete source directly; planner refuses complete source paired with a v0 adapter. | Default inspection retains full source, portfolio, and writeback without becoming executable (`12,388 → 26,524 B` readback delta). |
| Capture → Host / `turn_driver` / MCP / DSH / Ark | `host_dispatch_context` & `driver_lineage` check complete source & `selectedWorkTodo` once before dispatch, session lineage, or completion intent. | Omitted contracts retain historical v0 decoding; explicit `null`, non-object, malformed, or source/adapter-mismatched `work_contract` refuses before Host/writeback/spend. |
| Journal write, inspection & resume | Validates v1 and transitional `v0+work_contract` via shared TypeScript reader; enforces hard-lease execution key & lock sharing before `turn/start`. | Source-absent v0 records keep raw identity checks and original bytes during their supported recovery window (`14` File/SQLite plan JSON `-29.33%`, journal bytes `-28.32%`, final Host bytes equal). |

## Current quota and managed-controller cutover

C1 removes normal quota presentation's duplicate v0 builder branch and aligns
managed `turn managed-step` fresh preview and `refresh-state --settle-turn` with
the same TypeScript decision, route, and `quota_settlement_plan_v2` owners.

| Replaced caller / surface | Shared owner & deletion | Remaining boundary & obligation |
| --- | --- | --- |
| Quota `_project_quota_cli_payload` & `should-run` | Deletes v0 builder fallback branch; `should-run` emits composed `loopx_turn_envelope_v1` or original pre-decision failure diagnostic only. | Rejected composition (`quota_decision_transport_failed`) saves `source_diagnostic`, `composition_error`, and original `heartbeat_receipt` with `ok=false`/`should_run=false`, zero handoff, and zero spend. |
| Managed fresh preview & `managed-step` | `compose_turn_decision`, `completePlanningFacts`, and fused envelope/route call remove `TurnControllerInput`, `build_turn_controller_input`, and `build_controller_input`. | Failed Turn journals retain raw v0 decoding; injected signed v0 preview at live boundary is refused before Host/journal/spend. |
| Bound CLI, Kiro, DSH, SSH, EdgeBench & Windows bounded recovery | Explicit `--complete-turn` / bound Agent+Turn `should-run`, generated onboarding launchers, and Windows/PowerShell private-directory reservation (`1,700 B` cap passes; `96 B` refuses pre-admission). | Explicit `--turn-envelope` with `--context-delivery off` remains bounded v0 linked to immutable v1 capture; ordinary unbound/turnless adoption remains unapproved (§9 gate). |

### Executable required reads and native-runner attribution

`assertInteractionRequiredReads` (called once from the complete-source
validator across quota, preview, planning, and Host) requires explicit
command-bearing required reads to carry a non-empty, NUL-free command with
`ordering == "before_work"`, while preserving prose-only obligations, quoted
Unicode commands, and pending unread states without inventing a blanket
material-read gate. Complete-source checks also enforce Goal narrative readiness
before automatic-start interval reservation (`5→2` / `6→3` RPCs on refusal so
subsequent valid Turns are not falsely delayed) and verify declared Vision audit
triggers in both structured and Codex prompts.

In the frozen premerge budget counterexample, direct output was `20,333` chars
and the native runner emitted `20,501` chars (`20,533` in premerge layout, above
the unchanged `20,500` ceiling) solely because isolation adds `21` chars per
fixture root occurrence (`8 × 21 = 168` chars) on both main `bd79adc64968` and
C1 `f1614358ac13`. Budget ownership stays with `#8069`; no ceiling was raised.

### Retained consumer & recovery obligations

- **Initial-read Goal revision & inline diagnostics:** bound initial reads
  deliver the provider-backed Goal objective, revision, and digest ahead of the
  original Goal narrative (`+552 B` initial response, saving `1` reader call and
  `629 B` over separate selection); bound inline `generic_cli` JSON reuses
  compact projection rows and `<lane>_ref` references (`55.7–55.8 KB → 54.3–54.4 KB`
  Off; `47.4 → 45.5 KB` initial replan), rejecting tampered compact captures
  even with recomputed file digests.
- **Bounded Windows / PowerShell & Benchmark recovery:** Windows bounded restarts
  and capped JSON guards (`1,700 B` passes; `96 B` refuses before capture or
  receipt) use the shared private capture allocator and accept explicit
  PowerShell script launchers without POSIX `+x` (tested on macOS PowerShell 7;
  native Windows host DACL and Python execution remain untested). Non-native
  Benchmark preflight prompts carry the saved action plus replan/writeback
  reads (`47.4–47.5 KB` full vs `18.8 KB` Todo field select), while preserving
  EdgeBench's explicit `--turn-envelope` compatibility toggle and detached
  external-worker cancellation readback (`0` debit).
- **Relocated checkpoint debit readback:** `a1f34097805a` verifies copied local
  debits against their original index row and committed receipt after moving the
  source runs directory, rejecting symlinks or missing/altered artifacts/receipts.

## Matched engineering cohorts & negative/mixed cost evidence

All quantitative observations below use isolated synthetic File/SQLite tasks and
do **not** measure real model tokens, cache hits, billing, human attention,
controlled latency, or §9 single-session outcome/complete-cost benefit.

| Cohort (revisions) | Positive / convergent observations | Negative, mixed & failed observations (unchanged) |
| --- | --- | --- |
| Original quota vs C1 (`5f1a9d78ba` / `b8c2bf1dc`; `f04ec308f6` / `4ef428767c`; `6a92640346`; `0423908e9c`) | Warm RPCs `-12.65%`–`-12.88%` (`332→290` / `326→284`); duplicate capture projection removed (`30/30` captures, Python warm IO `-9.5%`); `refresh-state` settles/replays all 3 receipts. | Recurring warm stdout **+6.66% to +8.73%** (`~+7 KB` excl. prep); cold/recovery BODY reduction `0%`; warm framed bytes `+1.17%` in earlier cohorts (`-0.93%` only after clearing unused result-record bodies); observed Python IO `+3.2%–4%` before dedup. |
| Three-step Task after main field reuse (`d602248c53` / `b90e711cdb`; `918844a017` / `6f00db17c6`; `39770a9604` / `339f01e087`) | Complete 3-step stdout `-18.39%` (SQLite `225,841→184,316 B`), canonical correction BODY `-28.50%` (`149,223→106,688 B`), warm large-field BODY `14,386→2,583 B` in loopback Codex. | Warm 3-step BODY only `-2.09%` (`102,836→100,691 B`); framed RPC bytes **+0.83%** / **+2.14%–2.93%** after `39770a9604`; File instrumented time `+7.5%`; small 2-outcome sources grow `+1,655 B` each. |
| Hard-lease 3-step (`92294e7245cb` / `b3b3b232724d`) & bound CLI / Kiro / DSH (`f3bdad2387` / `0057a1dbd1`) | Hard-lease warm whole stdout `-19.5%`/`-19.9%`, RPCs `-31.44%` (`843→578`), framed bytes `-34.1%`; Goal revision initial-read saves 1 call/`629 B`; relocated-debit readback passes. | Direct bound CLI stdout grows `51.7–52.0 KB → 64.0–64.3 KB` (explicit Off `44.0 → 54.3–55.8 KB`, `~10.6 KB` from settlement plan); Kiro raw inline grows `2.4 → 48.2 KB` (Markdown) / `23.1 → 48.5 KB` (JSON). |
| Component boundaries & presentation budgets | Codex prompt single-call consolidation (`24.2→14.8 ms` median, `-39%`); Vision retry `6→2` CLI attempts (`11,473→9,269 B`). | Boundary checks add local cost: Host prep (`25.7→31.9 ms`), schema/material (`0.013→19.6 ms`), settlement recovery (`0.016→12.4 ms`), standalone route (`0.005→13.3 ms`), completion intent (`0.002→19.8–20.8 ms`). Budget holds FAIL: small Markdown `24,531 > 10,000`, crowded plans `24,044`/`27,508 > 22,500`, complete alias `24,252 > 13,000`, multi-agent `25,933 > 13,000`; thin prompt `5,519 > 5,400` repaired to `5,379` vs `5,363` main. |

## Verification status & open gates

- **Passed (recorded checkpoint cohorts):** `196`–`865` affected Python cases and
  `45`–`6,161` TypeScript cases across checkpoints; isolated `1,815`–`1,914`
  file wheel runs with zero checkout core contamination; default 17-root strict
  Mypy and changed-path Ruff pass; control-plane TypeScript typecheck passes.
- **Historical failures retained:** broad historical mypy runs (`4,809`–`4,831`
  diagnostics across 485 files), interrupted `9baa678db` 6-module run (`37`
  provider-startup failures), `2,015`-char long-namespace hook failures, and
  the five unrelaxed output-budget holds above remain open records.
- **Untested / remaining gates:** MemT end-to-end complete-input adoption
  blocked on missing `goal_ref` lifetime (`source_session_v1` must not be
  enabled as an adapter workaround); native Windows host DACL/PowerShell runtime;
  installed App/DSH/Kiro/MCP/Ark/PostgreSQL validation; real model tokens,
  invoices, and §9 matched single-session outcomes & complete costs; ordinary
  CLI default adoption approval; exact-head review, maintainability ratchet,
  and merge readiness.
