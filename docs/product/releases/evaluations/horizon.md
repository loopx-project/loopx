# LoopX Global Commander Horizon (`horizon`)

Canonical generational rubric (`loopx_commander_horizon_v0`), invariant 8-domain / 41-battleline ruler, anti-bloat scoring caps, and `open_protocol_core` boundaries.

## Dual-Scale Measurement Model (`a`-Scale vs `b`-Scale)

- **`a`-Scale (Global Cross-Generation Commander Scale, 41 battlelines)**: Constant denominator and weights across `v0.1.3 -> v4.0.0`. Every historical and future release is graded on the same 0-100 scale so architectural breakthroughs (`1.4 -> 2.0`), incremental tightening (`2.3 -> 2.4`), and cross-generation targets (`3.0 = 76.1`, `4.0 = 90.8`) remain strictly comparable.
- **`b`-Scale (Intra-Generation Core Readiness Scale)**: Restricts each domain average to battlelines whose `target_generation_major <= generation_major` (`30` `2.x_core` battlelines in `2.x`; `36` lines in `3.x`; `41` lines in `4.x`), measuring whether the current generation finishes what it promises before paying tax on future-generation ceilings.

## Scoring Dimensions & Anti-Bloat Caps (4 x 25 = 100)

| Dim | Focus | Hard Anti-Bloat Cap |
|---|---|---|
| **A** (0-25) | Architecture, typed state kernel & formal soundness | Grounded in single-owner contract & failure-mode closure |
| **B** (0-25) | Engineering determinism, verification & anti-tamper gates | Audited against real source/test gates and non-mocked paths |
| **C** (0-25) | Paired causality, external benchmark uplift & complete-cost ROI | **Max 15/25** when `paired_public_evidence == false` |
| **D** (0-25) | Default-on product usability, ergonomics & ecosystem adoption | **Max 12/25** when `default_off == true` |

## 8 Strategic Domains & 41 Battlelines (`16/16/15/14/12/10/10/7%`)

| Domain | Weight | `2.x_core` Lines | `3.x` / `4.x` Horizon Lines | `v2.4.0` (`a`) | `v3.0` Target (`a`) | `v4.0` Target (`a`) |
|---|---:|---:|---:|---:|---:|---:|
| **D1** Typed State Kernel, Authority Stores & Formal Recovery | 16% | 4 | 2 | 54.7 | 76.0 | 91.0 |
| **D2** Prefix-Preserving Context, Compaction & Stride Control | 16% | 4 | 2 | 51.8 | 75.0 | 90.0 |
| **D3** Non-Scalar Yardstick Calibration, Out-of-Tree Verifier & Domain RSI | 15% | 3 | 2 | 49.4 | 74.0 | 90.0 |
| **D4** Multi-Agent Collaboration, Steward Handoff & Fleet Scale | 14% | 4 | 2 | 50.7 | 75.0 | 91.0 |
| **D5** Runtime/Model/Sandbox Neutrality & L1-L4 Adoption Connectors | 12% | 4 | 1 | 63.2 | 79.0 | 92.0 |
| **D6** Omnichannel Human-Agent Workbench (Desktop, Web, Lark) | 10% | 6 | 0 | 61.0 | 78.0 | 90.0 |
| **D7** Long-Horizon Public Benchmarks, Causal Uplift & Statistical Inference | 10% | 2 | 1 | 47.0 | 76.0 | 91.0 |
| **D8** Open-Source Governance, Protected Domain Packs & Commercial Ecosystem | 7% | 3 | 1 | 66.2 | 78.0 | 92.0 |
| **Total `a`-Scale** | **100%** | **30** | **11** | **54.6** | **76.1** | **90.8** |

## Full 41-Battleline Matrix & `open_protocol_core` Public Criteria

| ID | Domain | Tier | License Scope | Battleline | `v2.4.0` | `v3.0` | `v4.0` |
|---|---|---|---|---|---:|---:|---:|
| `1.1` | D1 | `2.x_core` | `apache_2_0_in_repo` | Single-owner AuthorityStore, conditional CAS, hard lease & generation fencing (File/SQLite) | 75.0 | 86.0 | 95.0 |
| `1.2` | D1 | `2.x_core` | `apache_2_0_in_repo` | Effect-program Turn kernel (EffectTurn), quota_settlement_plan_v2 & once-only idempotent settlement | 72.0 | 86.0 | 95.0 |
| `1.3` | D1 | `2.x_core` | `apache_2_0_in_repo` | Canonical storage backup, authority-archive upgrade & controlled Goal reactivation (M1-M3) | 62.0 | 80.0 | 92.0 |
| `1.4` | D1 | `2.x_core` | `apache_2_0_in_repo` | Low-ceremony lease & write-scope ergonomics (wildcard collapse & zero-tax routine updates) | 50.0 | 80.0 | 92.0 |
| `1.5` | D1 | `3.x_horizon` | `open_protocol_core` | Outbound RemoteAuthority wire protocol, openPostgresqlStore SPI & lossless portable export | 47.0 | 78.0 | 90.0 |
| `1.6` | D1 | `4.x_north_star` | `apache_2_0_in_repo` | Formalized small-kernel invariants & checked transition certificates (T05) | 22.0 | 46.0 | 82.0 |
| `2.1` | D2 | `2.x_core` | `apache_2_0_in_repo` | Append-only canonical prefix stability & exact differential delta delivery | 67.0 | 82.0 | 94.0 |
| `2.2` | D2 | `2.x_core` | `apache_2_0_in_repo` | Progressive JSON-pointer context-read & strict model-facing CLI output budgets | 73.0 | 84.0 | 94.0 |
| `2.3` | D2 | `2.x_core` | `apache_2_0_in_repo` | Unified C1 TurnEnvelope default cutover (<5% control tax) & legacy quota_packet retirement (C9) | 47.0 | 78.0 | 92.0 |
| `2.4` | D2 | `2.x_core` | `apache_2_0_in_repo` | Event-triggered Stride & evidence-conditioned replan governance | 57.0 | 76.0 | 90.0 |
| `2.5` | D2 | `3.x_horizon` | `apache_2_0_in_repo` | Decision-preserving compaction (FoldToReceipt / EpochReset) under native host compression | 42.0 | 76.0 | 88.0 |
| `2.6` | D2 | `4.x_north_star` | `apache_2_0_in_repo` | Recoverable history working sets & multiscale predictive control state (T09) | 25.0 | 54.0 | 82.0 |
| `3.1` | D3 | `2.x_core` | `apache_2_0_in_repo` | Non-scalar Target/Yardstick/Stride co-decision & proxy-vs-native reversal detection | 59.0 | 80.0 | 92.0 |
| `3.2` | D3 | `2.x_core` | `apache_2_0_in_repo` | Out-of-tree deterministic Merge, Visual & Benchmark verifier isolation (anti-reward-hacking) | 72.0 | 86.0 | 96.0 |
| `3.3` | D3 | `2.x_core` | `apache_2_0_in_repo` | Authority source registry, material lifecycle & decision provenance binding | 62.0 | 80.0 | 92.0 |
| `3.4` | D3 | `3.x_horizon` | `apache_2_0_in_repo` | Held-out Kendall tau-b fast-yardstick calibration & controlled Domain RSI loop (T07) | 30.0 | 70.0 | 86.0 |
| `3.5` | D3 | `4.x_north_star` | `apache_2_0_in_repo` | Causal memory utility attribution, contrastive curriculum & active experiment design (T04/T06/T08) | 24.0 | 54.0 | 84.0 |
| `4.1` | D4 | `2.x_core` | `apache_2_0_in_repo` | Steward async manager_context inbox, read_peer_returns & original-route return closure | 75.0 | 86.0 | 95.0 |
| `4.2` | D4 | `2.x_core` | `apache_2_0_in_repo` | Small-team 2-3 worker two-cycle dependency collaboration, handoff & recovery (G1/G2/R2) | 62.0 | 84.0 | 94.0 |
| `4.3` | D4 | `2.x_core` | `apache_2_0_in_repo` | Shared Goal alignment, governed amendment & cross-Agent acceptance aggregation (R4) | 58.0 | 78.0 | 92.0 |
| `4.4` | D4 | `2.x_core` | `apache_2_0_in_repo` | Dual-role Controller-Executor separation & non-blocking busy-time steering (C6-C8) | 50.0 | 76.0 | 90.0 |
| `4.5` | D4 | `3.x_horizon` | `open_protocol_core` | Cross-host task division, shared budget fencing & returning-worker lease protocol (G3/R6) | 36.0 | 72.0 | 89.0 |
| `4.6` | D4 | `4.x_north_star` | `open_protocol_core` | 10-to-100+ Agent cohort convergence, bounded projections & fairness/anti-loop contract (G4/R7) | 23.0 | 54.0 | 86.0 |
| `5.1` | D5 | `2.x_core` | `apache_2_0_in_repo` | Neutral CLI/ACP host adapters (Codex App/CLI, Antigravity, Claude Code, OpenCode, ZCode, Pi) | 76.0 | 88.0 | 96.0 |
| `5.2` | D5 | `2.x_core` | `apache_2_0_in_repo` | dsh-loopx-plugin admission, PreStepDecision hook & version-fenced registry integration | 72.0 | 84.0 | 93.0 |
| `5.3` | D5 | `2.x_core` | `apache_2_0_in_repo` | Bidirectional executor failover (codex <-> antigravity), endpoint profiles & capacity aliases | 70.0 | 84.0 | 93.0 |
| `5.4` | D5 | `2.x_core` | `apache_2_0_in_repo` | Ephemeral sandbox / worktree isolation, Windows PowerShell parity & rollout fencing | 70.0 | 83.0 | 92.0 |
| `5.5` | D5 | `4.x_north_star` | `apache_2_0_in_repo` | Third-party agent framework L1->L4 zero-rewrite progressive adoption standard | 28.0 | 56.0 | 86.0 |
| `6.1` | D6 | `2.x_core` | `apache_2_0_in_repo` | Named release lifecycle, one-command upgrade & atomic rollback (update / authority-archive) | 72.0 | 84.0 | 93.0 |
| `6.2` | D6 | `2.x_core` | `apache_2_0_in_repo` | macOS/Windows Desktop shell, startup freshness reconciliation & signed preview updater | 58.0 | 76.0 | 89.0 |
| `6.3` | D6 | `2.x_core` | `apache_2_0_in_repo` | Goal workspace, live team map (W1 / L1 correction view) & full-history conversation search | 60.0 | 80.0 | 92.0 |
| `6.4` | D6 | `2.x_core` | `apache_2_0_in_repo` | Research breakthrough workbench & 5-minute verifier-backed case diff inspection | 62.0 | 78.0 | 90.0 |
| `6.5` | D6 | `2.x_core` | `apache_2_0_in_repo` | Experimental LooChat (@loopx/loochat) standalone web conversation surface | 52.0 | 72.0 | 86.0 |
| `6.6` | D6 | `2.x_core` | `apache_2_0_in_repo` | Hardened Lark group / private channel cards & App-Lark cursor continuity | 62.0 | 78.0 | 90.0 |
| `7.1` | D7 | `2.x_core` | `apache_2_0_in_repo` | Benchmark toolkit, quiescent same-state checkpoint (tau_B) & matched Stop-Hook isolation | 67.0 | 84.0 | 94.0 |
| `7.2` | D7 | `2.x_core` | `apache_2_0_in_repo` | Six public benchmarks reproducible paired net uplift (Delta J > 0) & complete-cost accounting | 46.0 | 78.0 | 92.0 |
| `7.3` | D7 | `3.x_horizon` | `open_protocol_core` | Anytime-valid sequential inference (e-processes / confidence sequences) & scientific campaign infrastructure | 28.0 | 66.0 | 87.0 |
| `8.1` | D8 | `2.x_core` | `apache_2_0_in_repo` | Protected domain capability packs (domain_pack_contract_v0), human gates & third-party SDK | 58.0 | 76.0 | 91.0 |
| `8.2` | D8 | `2.x_core` | `apache_2_0_in_repo` | Exact-head pr-review, risk canary, bilingual RFC parity, a/b eval gate & attested artifacts | 83.0 | 88.0 | 96.0 |
| `8.3` | D8 | `2.x_core` | `apache_2_0_in_repo` | Global contributor ladder governance, complexity ratchets & zero-Any type-safety baseline | 74.0 | 80.0 | 92.0 |
| `8.4` | D8 | `3.x_horizon` | `open_protocol_core` | Independent external adoption funnel, reproducible cases & Apache-2.0 OSS / loopx-cloud commercial boundary | 50.0 | 68.0 | 89.0 |

### `open_protocol_core` Public-Repository Criteria (3.x / 4.x Open-Core Splits)

- **`1.5`**: Apache-2.0 permanently owns local File/SQLite AuthorityStore, StorageConfig & openPostgresqlStore SPI, wire-neutral RemoteAuthority outbound client schemas, and lossless authority-archive export; private loopx-cloud owns multi-tenant hosted PostgreSQL runtime, connection pooling, RLS isolation, and managed backup/failover.
- **`4.5`**: Apache-2.0 permanently owns single-writer claim/lease, handoff envelope, returning-worker fencing, and shared budget contracts; private loopx-cloud owns multi-region hosted worker fleets, encrypted secret brokering, and cross-tenant scheduler placement.
- **`4.6`**: Apache-2.0 permanently owns bounded projection contracts, fairness/anti-loop schemas, and self-hosted small-cohort execution; private loopx-cloud owns 10-to-100+ hosted cohort control, quota billing/metering, and elastic fleet autoscaling.
- **`7.3`**: Apache-2.0 permanently owns the open benchmark toolkit, quiescent tau_B & Stop-Hook anti-confounding contracts, and anytime-valid e-process verification scripts; enterprise hosted replay clusters, confidential customer dataset vaults, and regression dashboards belong to loopx-cloud.
- **`8.4`**: Apache-2.0 permanently owns domain_pack_contract_v0, PR/merge/canary/a+b qualification gates, and portable open artifacts with zero vendor lock-in; private loopx-cloud owns SaaS/BYOC billing, SOC2/audit trails, org SSO/RBAC, and managed FDE enterprise onboarding connectors.

## Major `(a)` Exit Gates (`2.x -> 3.0` & `3.x -> 4.0`)

### `2.x -> 3.0` Promotion Gates (`G-2to3-*`)

- **`G-2to3-1`**: v3.0 candidate achieves a_scale >= 72.0 and D1..D5 >= 72.0 with b_scale >= 78.0.
- **`G-2to3-2`**: Reproducible paired net benchmark uplift (Delta J > 0) across >= 3 external public benchmarks under quiescent tau_B & Stop-Hook isolation, unlocking battleline 7.2.
- **`G-2to3-3`**: Plan B Open-Core separation live: clean openPostgresqlStore SPI, outbound RemoteAuthority client, and cross-host task/lease open protocols (1.5, 4.5).
- **`G-2to3-4`**: C1 TurnEnvelope default cutover (<5% control tax, 2.3) and FoldToReceipt / EpochReset compaction recovery (2.5) enabled cleanly.
- **`G-2to3-5`**: Documented, smoke-verified 2.x -> 3.0 upgrade/archive/downgrade migration contract.

### `3.x -> 4.0` Promotion Gates (`G-3to4-*`)

- **`G-3to4-1`**: v4.0 candidate achieves a_scale >= 88.0 across all 41 battlelines.
- **`G-3to4-2`**: T05 checked transition certificates (1.6) and multiscale recoverable working sets (2.6) enabled by default.
- **`G-3to4-3`**: 10-to-100+ Agent cohort convergence, bounded projection, and fairness/anti-loop contract (4.6) proven at scale.
- **`G-3to4-4`**: Third-party L1->L4 zero-rewrite adoption standard (5.5) and causal memory utility attribution (3.5) verified externally.

See [INDEX.md](INDEX.md), [current.md](current.md), [licensing.md](../../../project/licensing.md), and [saas-opportunity-assessment.md](../../roadmaps/saas-opportunity-assessment.md).
