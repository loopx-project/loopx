# LoopX Presentation Layer

This directory owns the code that turns LoopX state into human-facing display
surfaces. It must not become the control-plane source of truth.

## Layout

| Directory | Owns | Does not own |
| --- | --- | --- |
| `renderers/` | Transport-neutral text or Markdown rendering over already-built LoopX payloads. | Scheduler, quota, todo, or evidence decisions. |
| `sinks/` | External display sinks such as Lark/Feishu tables and cards. | Connector login authority, private reads, or production actions. |
| `projections/` | Future display-specific intermediate read models that join or reshape public-safe LoopX evidence for surfaces. | Persistent control state or benchmark scoring. |

Shared Markdown primitives such as scalar escaping and payload shape guards live
in `loopx.presentation.markdown`. Renderers should reuse those helpers instead
of defining local table-cell or scalar escaping rules.

Python code that starts as a user-visible capability may keep a thin facade
under `loopx.capabilities.*`, but the reusable display implementation should
live here.

`codex_activity.py` projects Codex host items into redacted, owner-visible Turn
steps. It owns display normalization only; step completion does not establish
Goal acceptance or execution authority. The Chat adapter consumes this projection
without exposing a separate top-level module.

## Explore Result Layer

Only the display side of software-exploration topology belongs here:

- graph/table/card projections that only reshape public-safe explore state for
  humans may live in `loopx/presentation/projections/explore/`;
- Lark/Feishu table sync and card output live behind the Lark extension in
  `loopx/extensions/lark/presentation/`;
- the core explore log, findings, and replan briefing inputs remain in the
  explore capability or a future control-plane explore/read-model boundary.

That keeps topology cards, tables, and graph views close to the dashboard
without turning presentation code into the source of evidence for vision and
replan.

## Static Site Delivery Contract

`loopx presentation package` turns an already-built, public-safe site directory
into a provider-neutral publish artifact. The artifact always remains usable
locally and carries both a stable latest layout and an immutable
`revisions/<revision>/` snapshot. The command writes a deterministic manifest
and a deploy receipt into the artifact; identical content and publication
parameters are a semantic no-op.

Packaging requires caller-supplied `passed` receipts for desktop visual,
mobile visual, and link checks. LoopX validates the file manifest and common
public-boundary leaks, but it does not pretend to have run the caller's browser
suite.

```bash
loopx --format json presentation package \
  --site-dir output/site \
  --output-dir output/publish \
  --site-id public-frontstage \
  --revision <public-revision> \
  --publisher github-pages \
  --base-url https://example.github.io/project/ \
  --desktop-visual-check passed \
  --mobile-visual-check passed \
  --link-check passed \
  --execute
```

`--publisher local` is the default and requires no network configuration.
`github-pages` is the first optional URL adapter; it only maps the prepared
artifact to stable latest and revision URLs, so repository credentials and
Pages enablement stay in the host workflow. After the host deploys the
artifact, `presentation verify-readback` compares the served deploy receipt
with the local receipt and persists a compact verification event. Use
`presentation rollback` to rebuild latest from a retained revision before the
host republishes the artifact.
