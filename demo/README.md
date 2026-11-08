# LoopX Demo Workspace

> **DEMO — NOT an official product capability.**
>
> Everything under `demo/` is an exploratory prototype / showcase, not part of
> the shipped LoopX product. It is **not** installed with the LoopX wheel, not
> registered in the product capability catalog, and not exposed through the
> product CLI.

This directory holds self-contained demos that are kept runnable from the repo
checkout for reference and experimentation:

- [`workspace/`](workspace/README.md) — local, repeatable workspace stories
  backed by isolated LoopX state.

The demos depend on the real LoopX product modules (`loopx.quota`,
`loopx.todos`, `loopx.status`, …) and are run by importing the `demo` package
from the repo root (examples and smokes already add the repo root to
`sys.path`).

The retired tmux multi-agent and auto-research prototypes are available in Git
history. For current collaboration and research examples, see
[`collaboration-delivery`](../examples/collaboration-delivery/README.md) and
[`managed-research-team`](../examples/managed-research-team/README.md).
