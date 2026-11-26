# Optional Explore & Research workflows for LoopX Core

Install `loopx-capability-research` into a matching standalone `loopx-core`
environment to enable **Explore** as the primary optional research workflow
(orchestration, multi-branch todo/worker planning, replay & counterfactual
harness, source reconciliation, and Feishu/external-evidence bridges), alongside
the existing standalone `deepresearch` evidence ledger and `external-evidence`
contracts.

Core (`loopx-core`) retains only Explore's canonical result-event schema,
lightweight projection helpers, machine/goal configuration adapters, and Goal
permission/settlement validation. Installing `loopx-capability-research` adds no
separate Goal write authority and never executes external providers without
explicit authorization. The ordinary `loopx` distribution already includes these
modules.

Build from the same checkout as the Core wheel and install it into that
environment:

```sh
uv run --extra test python packages/loopx-core/prepare.py \
  --distribution loopx-capability-research --output /tmp/loopx-research-source
uv build --wheel /tmp/loopx-research-source --out-dir /tmp/loopx-research-dist
uv pip install --python /tmp/loopx-core-env/bin/python \
  /tmp/loopx-research-dist/loopx_capability_research-*.whl
```

Check readiness with `loopx doctor --installation-only` and inspect
`loopx capability show explore`, `loopx capability show deep-research`, and
`loopx capability show external-evidence-research`.

Primary Explore & bridge workflows:

- Run Explore planning and result recording against an enabled Goal:
  `loopx explore node ...`, `loopx explore finding ...`, `loopx explore summary ...`,
  `loopx explore worker-branch-plan ...`.
- Project explicitly parent-admitted external evidence into a same-goal Explore
  node without auto-executing external providers:
  `loopx explore observe-external --goal-id <goal-id> --node <node-id> --plan-json plan.json --receipt-json receipt.json --admission-json admission.json [--execute]`
  (or `loopx external-evidence readback ... --goal-id <goal-id> --explore-node <node-id> [--execute]`).
- One-way read-only export of authorized answered `deepresearch` citations into
  an enabled Explore Goal without modifying `.loopx/deepresearch/` or leaking
  saved local paths, private sources, or report prose:
  `loopx explore import-citations --goal-id <goal-id> --node <node-id> --project <project> [--execute]`.

## Transition & Rollback for Existing Standalone `.loopx/deepresearch/` Ledgers

- Existing standalone `loopx deepresearch status|add-source|resolve-question|close|report`
  commands continue reading and continuing `.loopx/deepresearch/research.json`
  with identical schema, lock, and archive semantics.
- Open research sessions do not require immediate migration: continue or close
  the active session normally, then optionally run `loopx explore import-citations`
  to project sanitized citation references into an enabled Goal Explore graph.
  `.loopx/deepresearch/research.json` and `report.md` remain untouched.
- To roll back to Core-only operation after exiting open research commands, run
  `uv pip uninstall --python /tmp/loopx-core-env/bin/python loopx-capability-research`.
  Goal `explore_results.jsonl`, Goal permissions, and standalone
  `.loopx/deepresearch/` ledgers remain intact on disk and resume cleanly once
  the matching wheel is reinstalled.
