# Current Release Commander Evaluation (`current`)

- **Active Stable Baseline**: `v2.4.0` @ `a6ffb3be6e` (`2026-11-22T01:31:12+08:00`)
- **`a`-Scale Global Score**: **`54.6 / 100`** (`Δ +3.9` vs `v2.3.0`'s `50.7`)
- **`b`-Scale (`2.x` 30-Line Core Readiness)**: **`63.8 / 100`** (`Δb +4.7` vs `v2.3.0`'s `59.1`)
- **Rolling `origin/main` Base Tracked**: `ca0ab3b0ad` (`2026-11-22T14:05:08+08:00` — incorporates post-`v2.4.0` `#9244` release pipeline, `#9229` scheduler recovery gate, and `#9225` claim TTL ergonomics; plus active Plan B Open-Core track `#9223`).

## Domain Breakdown (`v2.3.0` -> `v2.4.0` -> `v3.0 Target`)

| Domain | Weight | `v2.3.0` (`a`) | `v2.4.0` (`a`) | `v2.4.0` (`b`, 30 lines) | `v3.0` Target (`a`) | Gap to `v3.0` |
|---|---:|---:|---:|---:|---:|---:|
| **D1** Typed State Kernel, Authority Stores & Formal Recovery | 16% | 52.0 | **54.7** (`+2.7`) | 64.8 | 76.0 | `-21.3` |
| **D2** Prefix-Preserving Context, Compaction & Stride Control | 16% | 48.0 | **51.8** (`+3.8`) | 61.0 | 75.0 | `-23.2` |
| **D3** Non-Scalar Yardstick Calibration, Out-of-Tree Verifier & Domain RSI | 15% | 45.0 | **49.4** (`+4.4`) | 64.3 | 74.0 | `-24.6` |
| **D4** Multi-Agent Collaboration, Steward Handoff & Fleet Scale | 14% | 46.5 | **50.7** (`+4.2`) | 61.2 | 75.0 | `-24.3` |
| **D5** Runtime/Model/Sandbox Neutrality & L1-L4 Adoption Connectors | 12% | 58.5 | **63.2** (`+4.7`) | 72.0 | 79.0 | `-15.8` |
| **D6** Omnichannel Human-Agent Workbench (Desktop, Web, Lark) | 10% | 56.5 | **61.0** (`+4.5`) | 61.0 | 78.0 | `-17.0` |
| **D7** Long-Horizon Public Benchmarks, Causal Uplift & Statistical Inference | 10% | 43.5 | **47.0** (`+3.5`) | 56.5 | 76.0 | `-29.0` |
| **D8** Open-Source Governance, Protected Domain Packs & Commercial Ecosystem | 7% | 63.0 | **66.2** (`+3.2`) | 71.7 | 78.0 | `-11.8` |
| **Total** | **100%** | **50.7** | **54.6** (`+3.9`) | **63.8** (`+4.7`) | **76.1** | **`-21.5`** |

## Top Ceiling Blockers Before `2.x -> 3.0` Promotion

- **[`7.2`, `3.2`] Paired external public benchmark uplift (Delta J > 0) still uncapped**: Battleline 7.2 stays C <= 15 (9/25) until matched tau_B + Stop-Hook paired runs prove positive complete-cost ROI on public benchmarks.
- **[`1.5`, `4.5`] Cross-host RemoteAuthority & Plan B Open-Core protocol parity incomplete**: PR #9223 establishes the physical split and outbound client/SPI; cross-host lease/budget fencing (4.5) and loopx-cloud end-to-end cutover remain ahead.
- **[`2.3`, `2.5`] C1 TurnEnvelope default cutover & FoldToReceipt compaction recovery remain non-default/partial**: Line 2.3 stays D-capped (D=8 <= 12) while legacy packet compatibility remains default; Line 2.5 needs receipt recovery after native host compaction.
- **[`1.4`, `2.4`, `4.4`] Routine lease ergonomics, event-triggered Stride, and busy-time steering need default-on proof**: Dual-role Controller-Executor steering (4.4) stays D-capped (D=10 <= 12) until default-on field latency and usability clear 2.x->3.0 exit criteria.

## Next-Minor Candidate Checklist (`v2.5.0` Gate Path)

- Every candidate `(a, b)` bump requires an exact-head evaluation artifact matching `a_scale`, `b_scale`, and all anti-bloat caps (`C <= 15` without `paired_public_evidence`, `D <= 12` when `default_off`), validated by `loopx canary release-qualification`.
- Patch `(c)` releases (`v2.4.1`, etc.) inherit `history/v2.4.0.json` cleanly with zero manual re-scoring overhead.

See [INDEX.md](INDEX.md), [horizon.md](horizon.md), [v2.4.0 snapshot](history/v2.4.0.md), and [release-readiness.md](../../release-readiness.md).
