# Agent Operations And Projections

These are the detailed repository instructions linked from [AGENTS.md](../../AGENTS.md).
Read the applicable sections before the corresponding action; their requirements,
exceptions and authority boundaries remain mandatory. Commands and backticked
paths are relative to the repository root unless stated otherwise.

## Automation And Monitor Todos

Do not hard-code one-off project or PR monitor logic into a generic heartbeat
automation prompt. Recurring project-specific watches, such as "monitor PR #532
until merge", belong in LoopX state as `continuous_monitor` todos with compact
metadata such as `claimed_by`, `unblocks_todo_id`, and evidence notes. The
heartbeat prompt should remain generic and discover monitor work through
status, quota, and todo projection. Only update an automation prompt when the
heartbeat lifecycle contract itself changes or the user explicitly asks to
change the scheduler.

## Projection Sink Design

When adding an operator-facing display sink such as Lark Base, dashboards,
chat summaries, or reports, build it from LoopX's public-safe state and
projection surfaces instead of parsing project-specific private source files.
Valid display inputs include todo projection, quota/status contracts,
frontstage projections, compact run-history events, public-safe evidence
pointers, and redacted source warnings.

Do not make a generic sink depend on the shape of one local document, private
planning file, non-public wiki, raw transcript, local path, or connector payload.
If a source is valuable, first convert it into a bounded LoopX projection with
stable ids, source labels, evidence, and explicit gates; then let the sink
render that projection. Public or multi-user sinks must consume redacted
public-safe evidence. An explicitly owner-only operator board may sync private
planning evidence when the user authorizes that boundary, but it should still
scope rows by `agent_id` and avoid credentials or secrets unless the user
explicitly asks for a credential-handling workflow.

Projection sinks should preserve row lineage as data, not as ad hoc prose. When
rows supersede, migrate, or retire earlier display rows, represent that through
projection lifecycle fields such as `row_lifecycle`, `supersedes`,
`superseded_by`, `source_id`, and compact migration audit evidence. A sink may
render the lineage in existing evidence/history fields, but should not require
reading a project-private source document to understand why a row changed.

## LoopX Self-Repair

When LoopX behavior is surprising, too small, contradictory, or called
out by the user as likely wrong, use the project skill
`skills/loopx-self-repair/SKILL.md`. Treat recurring mistakes as product
or process gaps: update the skill, interaction docs, active-state projection,
or focused smoke so the lesson is durable. Do not resolve self-repair by
lowering gates, guessing around contradictory payloads, or committing private
logs and local state.

## Benchmark Smoke Classification

Use this classification when cleaning or reviewing benchmark-related changes:

- Keep focused boundary smokes such as
  `examples/benchmark-candidate-source-boundary-smoke.py`; they guard a reusable
  public/private source contract.
- Keep toolkit permission and integrity smokes while they validate the shipped
  provider-neutral capability contract.
- Keep benchmark-native runners, adapters, ledgers, scoring reducers, and dated
  experiment packets outside the active product surface. Historical versions
  follow the canonical archive placement rules in `benchmark/README.md`:
  retired implementations and dated packets belong under
  `deprecate/benchmark-legacy/`; explicitly identified immutable experiment
  snapshots may remain under `benchmark/` only under that document's conditions.
  Neither category is part of active CI benchmark execution.
- Add a new active benchmark smoke only when it protects a stable toolkit
  behavior; experiment-specific validation belongs with the research workspace.
