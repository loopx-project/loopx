# Research And Evidence

This area holds public research and inspectable evidence. It is not the source
of truth for current product behavior; stable conclusions should be promoted
into product, architecture, or reference documentation with source links.

## Featured Result: LHTB

**GPT-5.6 Sol + LoopX 1.0.3 Heartbeat: 0.4948 mean reward across 46 tasks**,
versus 0.4218 for Plain Codex (**+17.3%**) and 0.4475 for native Codex Goal
(**+10.6%**). Strict solves (reward ≥0.95) are **7 / 7 / 4** for LoopX / Plain /
Goal. Supplementary pass rates at reward ≥0.80 are **15/46 (32.6%) / 12/46
(26.1%) / 14/46 (30.4%)** in that same order. This post-hoc threshold does not
replace the strict solved metric; no score equals 0.80 in this study.
The tasks extend beyond coding to research, science, multimodal analysis,
games, and professional workflows.

This is a same-model exploratory system comparison: one effective trial per
task-arm, designated replacements, and unequal runtime/budgets. It does not
establish an equal-budget efficiency gain or a general performance guarantee.

[Interactive brief](https://loopx-project.github.io/loopx/benchmarks/lhtb/)
· [LHTB official website](https://zli12321.github.io/LHTB/index.html)
· [中文](https://loopx-project.github.io/loopx/benchmarks/lhtb/?lang=zh)
· [Study, all five arms and limitations](../../benchmark/LHTB/studies/five-arm-gpt56sol-max/README.md)
· [Task-level scores](../../benchmark/LHTB/studies/five-arm-gpt56sol-max/data.json)

## Research Index

- [Benchmark research workspace](https://github.com/huangruiteng/loopx/blob/main/benchmark/README.md): current protocols,
  benchmark-specific practice, and links to the governing research RFC.
- [Legacy benchmark archive](https://github.com/huangruiteng/loopx/blob/main/deprecate/benchmark-legacy/README.md):
  non-active runners, adapters, and historical packets retained for archaeology.
- [Agent workflow audits](agent-workflow-audits/): bounded reviews of external
  agent workflow patterns.
- [Jev external evidence supplement v0 (Chinese)](agent-workflow-audits/jev-external-evidence-supplement-v0.zh-CN.md):
  non-normative evidence about model quality, evaluation limits, and integration
  patterns for the Jev RFC.

Raw private runs, trajectories, credentials, local paths, internal links, and
non-public source material do not belong in this directory.
