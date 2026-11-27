# Dual-Model `Behavior Constraint × Efficiency` Microbenchmark (M1/M2/M3)

This suite measures whether control-plane efficiency optimizations preserve strict behavioral constraints across two real host/model paths:

- **Codex GPT** (`benchmark/runtime/codex.py` isolated settings/environment + `loopx.control_plane.quota.codex_session_usage.read_codex_session_usage` + `benchmark/runtime/session_observation.py` + optional Harbor `BenchmarkCodex._totals` telemetry), and
- **DeepSeek V4.1 Flash** (`deepseek-v4-1-flash-260910` via Ark `DoubaoExecToolClient` + `_direct_ark_transport` in `loopx/control_plane/testing/`).

## Scope Boundary (Strictly Disjoint From M4/M5)

- **Owns (M1/M2/M3):**
  - **M1 (`m1_envelope_scoped_recovery`):** Scoped Turn envelope & progressive recovery (`#9498`/`#9504`): selects runnable work (`todo_selected_work`), preserves blocked P0 (`todo_primary_blocked`) in `blocked_priority_fallback`, resolves pointer readback (`quota context-read --select`) cleanly, and measures delivered wire bytes, verifiable `o200k_base` prompt tokens, and Tier-1 **Peak Context Window Occupancy** (`initial_turn_context_tokens`, `per_turn_context_tokens`, `peak_context_tokens`, `effective_context_window`, `min_headroom_tokens`, `peak_window_ratio`, `compaction_events`, `truncation_events`, `occupancy_measurement_kind`). Use `--context-delivery {auto,off,on}` to run `off` vs `on` contrast (`auto`) or pin both arms to one setting (`off`/`on`) for same-config version diffs or A/A baselines.
  - **M2 (`m2_epoch_prefetch_semantic_fold`):** Stage-then-Swap Epoch Prefetch & Semantic-Unit Fold (`#9503`): verifies net-positive folding (`EpochReset` / `FoldToReceipt`) retains all `failure_facts` and `open_obligations` while failing closed to `KeepRaw` on negative token savings or missing completion receipts.
  - **M3 (`m3_claim_block_settle_invariants`):** Basic claim, gate, and settle invariants: blocking `user_gate` refuses delivery/spend; single-owner `todo claim` rejects competing peer steals; C1 settlement spends at most once and stays idempotent on replay.
- **Excludes (M4/M5):** Long-horizon multi-task FrontierCS-2 BLC (5h), multi-wake SForge FFmpeg trials, H1C / H2 cliff-guard evaluations, and online judge containers in [`../edgebench/README.md`](../edgebench/README.md).

## Running Locally

### Tier-0 (`<15s` deterministic differential)

```bash
uv run --extra test python -m benchmark.microbench.run --tier 0 --format markdown
```

Optional cross-revision check against an ancestor commit (reusing `cli-output-base-head-differential-smoke.py` Base checkout helpers, optionally pinning `--context-delivery on` and saving sanitized JSON via `--output-json`):

```bash
uv run --extra test python -m benchmark.microbench.run \
  --tier 0 \
  --base-ref <BASE_COMMIT> \
  --context-delivery on \
  --format markdown \
  --output-json .local/microbench-runs/tier0.json
```

Public offline smoke (covers Tier-0 + stubbed Tier-1 conformance with zero API calls):

```bash
uv run --extra test python examples/benchmark-dual-model-microbench-smoke.py
```

### Tier-1 (bounded 1–3m Base vs Head dual-model)

Live provider execution requires explicit `--live` opt-in and isolated operator credentials (`ARK_API_KEY` for Ark `deepseek-v4-1-flash-260910`, plus `OPENAI_BASE_URL`+`OPENAI_API_KEY` or `CODEX_AUTH_JSON_PATH` for Codex GPT):

```bash
uv run --extra test python -m benchmark.microbench.run \
  --tier 1 \
  --live \
  --max-total-seconds 180 \
  --format markdown
```

## Public-Safety & Measurement Rules

- All fixtures live inside disposable temporary directories created per run.
- Credentials, local home paths, raw prompts, and raw provider responses are never serialized into emitted receipts or tests (`assert_public_safe_payload`).
- Missing model usage counters remain `null` (`None`), never `0`; partial multi-round metric coverage is labeled explicitly in `telemetry_coverage` and excluded from whole-run efficiency deltas (`excluded_partial_metrics`).
- Every scenario records explicit `source_attribution` (`external_base_checkout` vs `candidate_head`, `comparison_kind`, `production_hot_path_covered`) and top-level `claim_scope`; same-commit mechanism ablations (such as M2, where `semantic_unit_settlement_hook_available` is diagnostic-only unless a real production settlement entrypoint executes and reads back turn settlement) remain excluded from cross-commit and production hot-path conclusions.
- Byte reductions cannot mask behavior constraint failures (`behavior_preserved` requires all constraint checks and item-by-item M2 clauses/Todo ID matching to pass).
