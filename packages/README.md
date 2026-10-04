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

Current co-located extensions include:

- [`loopx-codex-provider-routing`](loopx-codex-provider-routing/README.md):
  public-safe Codex App + CPA catalog compilation, qualification and upgrade
  planning;
- [`loopx-repo-health`](loopx-repo-health/README.md): public-safe GitHub
  repository health snapshots;
- [`loopx-text-stats`](loopx-text-stats/README.md): the Developer Book's zero-permission
  text-statistics companion, with a complete installable package and contract tests.
  It is a teaching distribution, not a default capability or catalog entry.

The default destination is `packages/<extension-id>/`.
