# LoopX Developer Book

From control-plane protocols to shippable development.

A bilingual Dev Book for external developers: understand LoopX state, authority, and Turns, then connect an
existing project or contribute to LoopX.

[简体中文版](/loopx/docs/book/)

<div class="grid cards" markdown>

-   :material-map-marker-path: **Control-plane foundations**

    Learn sessions, durable state, work graphs, authority, governed Turns, recovery, and runtime boundaries.

    [:octicons-arrow-right-24: Start with foundations](chapters/01-from-session-to-loop.md)

-   :material-source-repository: **Project onboarding**

    Delegate onboarding to an Agent, verify Goal, identity, and Git boundaries, then start from Codex App or
    the visible Codex CLI TUI.

    [:octicons-arrow-right-24: Connect a project](chapters/05-connect-existing-project.md)

-   :material-source-branch: **Developer contributions**

    Choose the right protocol and owner across the Control Plane, Capabilities, Providers, Hosts, projections,
    and Extensions.

    [:octicons-arrow-right-24: Make a contribution](chapters/source-protocol-map.md)

-   :material-school: **Control-Plane Course**

    For developers entering Kernel, CLI, state projection, or scheduler implementation, with Showcases,
    decision tables, and source walkthroughs.

    [:octicons-arrow-right-24: Enter the developer course](chapters/12-control-plane-course.md)

</div>

## What this book helps you do

A normal agent session is good at reasoning and execution inside one context. Real development work also
waits for CI, crosses sessions, changes owners, encounters approval gates, and reacts to external state.
LoopX keeps the goal, work queue, authority, evidence, and recovery conditions in a project-owned control
plane.

This book is not a copy of the LoopX CLI reference, and it does not assume that you want to contribute to
the Kernel. It gives external developers a stable path for deciding:

- whether a task needs only one session, a persistent Host goal, or a project-level control plane;
- when Todo, Gate, Evidence, Quota, monitor, and recovery contracts add real value;
- how to delegate project onboarding to an Agent without surrendering Goal, identity, authority, or Git boundaries;
- how to locate the right contribution owner, change an implementation, and validate it from protocols and
  invariants;
- how to decide whether a capability belongs in core, a Capability, a Provider, a Host, a projection, or an
  independently delivered Extension.

## Three parallel learning paths

After the control-plane chapters, choose the path that matches your job:

1. **Connect an existing project:** begin with
   [Connect an existing Git project](./chapters/05-connect-existing-project.md).
2. **Make a developer contribution:** begin with the
   [Developer contribution map](./chapters/source-protocol-map.md).
3. **Control-Plane Course:** enter Kernel implementation through the
   [independent course chapter](./chapters/12-control-plane-course.md).

The paths share the same foundations but are independent. Project onboarding does not require a LoopX
source change. Contributions are not limited to Kernel maintainers, and the Course takes the Dev Book's
mechanism model further into implementation, rules, and validation. You can work on the Control Plane,
Capabilities and Domain State, Providers, Host or Runner integration, projections, documentation and
fixtures, or Extension and package lifecycle. An Extension is one delivery choice for independently
versioned or optional functionality, not the default shape of every contribution.

## Current validation baseline

- Source format: Markdown
- Site generator: MkDocs Material
- Hosting: GitHub Pages
- LoopX release anchor: `v1.1.0`
- Runtime prerequisites: Python 3.11+ and Node.js 22.22.3+

The official public protocols remain authoritative for protocol facts. Commands that change across
releases remain authoritative in the release you use, its current `--help`, and official documentation.
This book owns the learning path and explanatory model, not another complete command reference.

In `v0.5.4`, key semantics for the Effect Program, Turn settlement, Todo completion, quota delivery
routing, workspace causality, and scheduler state sit behind typed TypeScript owners. During the
migration, the Python CLI still owns transport, legacy projections, external Provider calls, and some
durable writeback. These are not two independently evolving control planes. Treat the
[TypeScript Control-Plane Migration RFC](/loopx/docs/architecture/rfcs/typescript-control-plane-migration-v0/)
on current `main` as the direction for later stages, not as shipped `v0.5.4` behavior.
