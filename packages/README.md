# Co-located Packages

This directory contains independently installable distributions developed next
to LoopX. Each child owns its packaging metadata, dependencies, and release
lifecycle; it is not included in the LoopX wheel merely because it is tracked
in this repository.

LoopX-owned source remains under `loopx/`:

- `loopx/capabilities/` owns caller-facing capability contracts and built-in
  implementations;
- `loopx/extensions/` owns extension lifecycle machinery and providers bundled
  in the LoopX wheel.

Create a standalone extension package with:

```bash
loopx extension init <extension-id> --execute
```

Current co-located distributions, in directory order:

- [`dsh-loopx-plugin`](dsh-loopx-plugin/README.md): the DeepSeek Harness
  plugin, an independently versioned npm package that drives `loopx` through
  its CLI without importing LoopX Python;
- [`loopx-ark-turn`](loopx-ark-turn/README.md): an optional host for one
  LoopX-governed work unit on Ark Managed Agents (Python distribution, no
  `extension.toml`; selected with `turn run-once --host generic-cli`);
- [`loopx-codex-provider-routing`](loopx-codex-provider-routing/README.md):
  public-safe Codex App + CPA catalog compilation, qualification and upgrade
  planning;
- [`loopx-community-discussion`](loopx-community-discussion/README.md):
  public-safe community discussion scanner producing compact
  `discussion_fact_v0` facts;
- [`loopx-finance-execution`](loopx-finance-execution/README.md): optional
  simulator for the human-confirmed finance operation envelope;
- [`loopx-finance-value-discovery`](loopx-finance-value-discovery/README.md):
  optional finance value-discovery sample that replaced the retired core
  finance connector;
- [`loopx-jev`](loopx-jev/DESIGN_DECISIONS.md): optional scoped
  progress-shadow observations (distribution `loopx-jev-pilot`, no
  `extension.toml`);
- [`loopx-obelisk`](loopx-obelisk/README.md): optional advisory context
  provider for `decision-context`;
- [`loopx-repo-health`](loopx-repo-health/README.md): public-safe GitHub
  repository health snapshots;
- [`loopx-text-stats`](loopx-text-stats/README.md): the Developer Book's
  zero-permission text-statistics companion, with a complete installable
  package and contract tests. It is a teaching distribution, not a default
  capability or catalog entry.

Not every distribution is an extension: a package without `extension.toml`
installs through its own Python or npm lifecycle and never enters
`loopx extension install`.

The default destination is `packages/<extension-id>/`.
