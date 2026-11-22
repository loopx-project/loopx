# LoopX Global Commander Quantitative Evaluation Ledger

This ledger unifies LoopX's release history, active release posture, long-horizon roadmap targets, and retired-module hygiene across four strict state directories:

1. [`horizon.md`](horizon.md) / [`horizon.json`](horizon.json) — Constant 8-domain / 41-battleline ruler (`16/16/15/14/12/10/10/7%`), anti-bloat `C/D` caps, `open_protocol_core` boundaries, `1.x -> 2.x -> 3.x -> 4.x` exit gates, and targets `v3.0 = 76.1`, `v4.0 = 90.8`.
2. `history/` — Continuous cross-generation ledger across all **15 real `a.b` series** (`v0.1.3` through `v2.4.0`). No historical version is ever hidden in `archive/`; `0.x/1.x` are tagged `eol`, pre-`2.3` snapshots use `retrospective_git_audit`, and `2.3+` snapshots use `release_gate_qualified`.
3. [`current.md`](current.md) / [`current.json`](current.json) — Active release assessment (`v2.4.0` stable baseline: `a`-Scale **`54.6`**, `b`-Scale **`63.8`**) plus post-`v2.4.0` rolling `main` context and `2.x -> 3.0` ceiling blockers.
4. [`archive/README.md`](archive/README.md) — Physically retired components (`#6396`, `#8190`, `#8431`, `#8928`, Plan B server PG `#9223`) and frozen rubric snapshots ([rubric_v0.json](archive/rubrics/rubric_v0.json)).

## Cross-Generation Progression Table (All 15 Series + Horizon)

| Series | Tag Anchor | Date | Status | Mode | D1 | D2 | D3 | D4 | D5 | D6 | D7 | D8 | `a`-Scale | `Δ` | `b`-Scale |
|---|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| [0.1.3](history/v0.1.3.md) | `v0.1.3..v0.1.16` | 2026-07-02 | `eol` | `retrospective_git_audit` | 11.0 | 7.0 | 6.0 | 6.0 | 10.0 | 9.0 | 7.0 | 14.0 | **`8.4`** | baseline | — |
| [0.2.0](history/v0.2.0.md) | `v0.2.0..v0.2.13` | 2026-07-11 | `eol` | `retrospective_git_audit` | 13.0 | 8.5 | 8.0 | 7.5 | 12.0 | 11.0 | 8.5 | 17.0 | **`10.3`** | `+1.9` | — |
| [0.3.0](history/v0.3.0.md) | `v0.3.0` | 2026-07-30 | `eol` | `retrospective_git_audit` | 14.5 | 9.5 | 9.5 | 9.0 | 15.0 | 12.0 | 10.5 | 19.0 | **`11.9`** | `+1.6` | — |
| [0.4.0](history/v0.4.0.md) | `v0.4.0..v0.4.9` | 2026-08-02 | `eol` | `retrospective_git_audit` | 16.5 | 11.5 | 11.5 | 10.5 | 18.0 | 14.0 | 13.5 | 22.0 | **`14.1`** | `+2.2` | — |
| [0.5.0](history/v0.5.0.md) | `v0.5.0..v0.5.4` | 2026-08-20 | `eol` | `retrospective_git_audit` | 21.0 | 13.5 | 13.0 | 13.0 | 21.0 | 17.0 | 15.0 | 25.0 | **`16.8`** | `+2.7` | — |
| [1.0.0](history/v1.0.0.md) | `v1.0.0..v1.0.5` | 2026-09-06 | `eol` | `retrospective_git_audit` | 24.0 | 16.0 | 15.0 | 17.0 | 25.0 | 22.0 | 16.5 | 29.0 | **`19.9`** | `+3.1` | — |
| [1.1.0](history/v1.1.0.md) | `v1.1.0` | 2026-09-20 | `eol` | `retrospective_git_audit` | 26.0 | 18.0 | 17.0 | 20.0 | 27.5 | 24.5 | 18.0 | 31.5 | **`22.1`** | `+2.2` | — |
| [1.2.0](history/v1.2.0.md) | `v1.2.0..v1.2.4` | 2026-09-25 | `eol` | `retrospective_git_audit` | 28.0 | 20.0 | 19.0 | 22.5 | 30.0 | 27.0 | 19.5 | 33.5 | **`24.3`** | `+2.2` | — |
| [1.3.0](history/v1.3.0.md) | `v1.3.0..v1.3.1` | 2026-10-06 | `eol` | `retrospective_git_audit` | 29.5 | 22.0 | 20.5 | 24.0 | 32.0 | 28.5 | 21.0 | 35.0 | **`25.9`** | `+1.6` | — |
| [1.4.0](history/v1.4.0.md) | `v1.4.0..v1.4.2` | 2026-10-11 | `eol` | `retrospective_git_audit` | 31.0 | 24.0 | 22.0 | 25.0 | 34.0 | 30.0 | 22.0 | 36.0 | **`27.4`** | `+1.5` | — |
| [2.0.0](history/v2.0.0.md) | `v2.0.0` | 2026-10-20 | `superseded` | `retrospective_git_audit` | 42.0 | 33.0 | 32.0 | 34.0 | 44.0 | 42.0 | 31.0 | 48.0 | **`37.5`** | `+10.1` | — |
| [2.1.0](history/v2.1.0.md) | `v2.1.0..v2.1.1` | 2026-10-22 | `superseded` | `retrospective_git_audit` | 45.0 | 37.0 | 35.0 | 39.0 | 48.0 | 47.0 | 34.0 | 52.0 | **`41.3`** | `+3.8` | — |
| [2.2.0](history/v2.2.0.md) | `v2.2.0` | 2026-11-02 | `superseded` | `retrospective_git_audit` | 49.0 | 44.0 | 40.0 | 43.0 | 54.0 | 52.0 | 39.0 | 59.0 | **`46.6`** | `+5.3` | — |
| [2.3.0](history/v2.3.0.md) | `v2.3.0` | 2026-11-09 | `superseded` | `release_gate_qualified` | 52.0 | 48.0 | 45.0 | 46.5 | 58.5 | 56.5 | 43.5 | 63.0 | **`50.7`** | `+4.1` | **`59.1`** |
| [2.4.0](history/v2.4.0.md) | `v2.4.0` | 2026-11-22 | `active_stable` | `release_gate_qualified` | 54.7 | 51.8 | 49.4 | 50.7 | 63.2 | 61.0 | 47.0 | 66.2 | **`54.6`** | `+3.9` | **`63.8`** |
| [3.0](horizon.md) | `target` | — | `horizon` | `target` | 76.0 | 75.0 | 74.0 | 75.0 | 79.0 | 78.0 | 76.0 | 78.0 | **`76.1`** | `+21.5` vs `2.4` | **`79.8`** |
| [4.0](horizon.md) | `target` | — | `horizon` | `north_star` | 91.0 | 90.0 | 90.0 | 91.0 | 92.0 | 90.0 | 91.0 | 92.0 | **`90.8`** | `+14.7` vs `3.0` | **`90.8`** |

## Generational S-Curve Laws Observed

1. **Micro-iteration phase (`0.1 -> 1.4`, `8.4 -> 27.4`)**: High patch velocity (`57` `0.x/1.x` releases) proved multi-surface feasibility (`+1.5` to `+3.1` per series) before hitting the Markdown dual-write & dynamic-prompting structural ceiling around `~27`.
2. **Architectural cutover (`1.4 -> 2.0`, `27.4 -> 37.5`, `+10.1`)**: Switching authority ownership to TypeScript state machines, retiring dual-write bridges, and instituting hard lease/CAS invariants unlocked a step-function jump.
3. **Deterministic hardening (`2.0 -> 2.4`, `37.5 -> 54.6`)**: Four releases pushed core readiness (`b`-Scale `63.8`) via `EffectTurn`, settlement idempotency, Steward async closure, and strict preflight gates — exposing the next ceiling: **external paired benchmark causality (`7.2`), default-on `C1` (`2.3`) + compaction recovery (`2.5`), and Plan B cross-host `RemoteAuthority` (`1.5`/`4.5`)**.

## Companion Policy & Release Contracts

- [Release Readiness & a/b Dual-Scale Gate](../../release-readiness.md#versioned-evaluation-gate-ab-dual-scale)
- [SaaS Opportunity & Open/Closed Commercial Synergy](../../roadmaps/saas-opportunity-assessment.md)
- [Open-Core Licensing Boundary](../../../project/licensing.md)
