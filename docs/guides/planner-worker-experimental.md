# Experimental Planner-Worker Mode

Status: **experimental**, opt-in, provider-neutral. One call runs one
bounded Planner → Worker → validation slice and returns a typed receipt.

Use the shipped fake adapters in
`examples/experiments/planner_worker/runtime-smoke.py` to learn the contract
without a live model provider. TraeX is only one optional extension provider.

## Operator contract

| Rule | Requirement |
| --- | --- |
| Clean worktree | The workspace must be a git worktree with no dirty or untracked changes before Planner runs. |
| Explicit model routes | Callers pass `model_routes` for `planner`, `cheap_worker`, and `strong_worker`. Missing routes fail closed. |
| Caller-approved validation | Every `validation_commands` entry must appear in the caller allowlist. Unapproved commands stop before Worker writes. |
| One-step receipt | Each `run_planner_worker_once` selects at most one executable step, runs it once, and returns `planner_worker_receipt_v0`. |
| Incomplete cost | Receipts always set `cost.complete=false` until pricing is supplied; token `usage` may still be complete. |
| Provider opt-in | Core owns the contract and fake runtime. Live providers (for example TraeX) stay optional and must be invoked explicitly. |
| Stop | Do not restart or schedule another slice automatically. Read the receipt `status`/`reason` and exit; clean or reset the worktree before any later call. |

## Fake runtime walkthrough

```bash
python3 examples/experiments/planner_worker/contract-smoke.py
python3 examples/experiments/planner_worker/runtime-smoke.py
```

The runtime smoke builds a temporary clean git fixture, injects fake Planner and
Worker adapters, allowlists `python3 verify.py`, and asserts a completed
receipt with validation pass. It proves:

- dirty worktrees are rejected at the observer boundary;
- Worker sees the explicit cheap-worker model route;
- validation runs only approved commands;
- `usage.complete` can be true while `cost.complete` stays false.

## Optional TraeX provider

Only when you intentionally opt in to a live TraeX binary:

```bash
python3 scripts/experiments/traex_planner_worker_probe.py \
  --cwd /path/to/clean/worktree \
  --validation-command 'python3 -m pytest -q tests/test_target.py'
```

Pass every approved validation command explicitly. Keep the worktree clean
before the probe. Treat TraeX output as one provider probe payload wrapping the
same typed receipt—not as LoopX kernel writeback authority.

## What this mode is not

- Not a heartbeat, cron, or resident multi-agent scheduler.
- Not a replacement for `loopx turn run-once` or host-mode connectors in the
  [runtime connector catalog](../integrations/runtime-connector-catalog.md).
- Not automatic quota spend, todo writeback, or default product orchestration.

Stop after one receipt. Any follow-up slice is a new caller decision with a
fresh clean worktree, explicit routes, and a fresh approved validation set.

## Related surfaces

- Contract and runtime: `loopx/experiments/planner_worker/`
- Public smokes: `examples/experiments/planner_worker/`
- Catalog index: [Runtime connector catalog](../integrations/runtime-connector-catalog.md)
