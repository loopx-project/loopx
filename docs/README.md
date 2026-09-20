# LoopX Documentation

LoopX is a control plane for long-running agent work. Use this documentation
home to choose the shortest path for what you are trying to do; deeper indexes
keep product direction, operations, protocols, evidence, and historical
material available without putting all of it on one page.

## Choose Your Path

| You want to... | Start here | Continue with |
| --- | --- | --- |
| Understand LoopX before installing | [Public homepage](https://loopx-project.github.io/loopx/) | [Project README](../README.md) |
| Follow the curated developer curriculum | [Developer Book](/loopx/docs/book/) | [English edition](/loopx/docs/book/en/) |
| Try LoopX in a repository | [Getting started](guides/getting-started.md) | [Newcomer command path](guides/newcomer-command-path.md) |
| Run or recover a long-lived goal | [Operations](operations/README.md) | [Integration guide](integration.md) |
| Understand the control plane | [Architecture](architecture.md) | [Concepts](concepts/README.md) |
| Connect an agent runtime or provider | [Integrations](integrations/README.md) | [Extensions and capabilities](reference/extensions.md) |
| See what contributors are building now | [Current technical directions](project/technical-directions.md) | [Contributor tasks](development/contributor-tasks.md) |
| Build or review LoopX | [Developer guide](development/README.md) | [Testing and quality](development/testing-and-quality.md) |
| Inspect real outcomes | [Showcases](showcases/README.md) | [Research and evidence](research/README.md) |

The [public homepage](https://loopx-project.github.io/loopx/) is the shortest
product overview. The [project README](../README.md) keeps the source-linked
quick start and capability map, while the
[public user manual](https://my.feishu.cn/wiki/CaL5wMk9ui17ngkWzeUcMlAYnZg) provides
a longer onboarding path.

## Core References

- [Architecture](architecture.md): control-plane layers and ownership.
- [State interaction model](state-interaction-model.md): user, agent, and state
  channel flow.
- [Project agent todo contract](project-agent-todo-contract.md): work,
  ownership, gates, and continuation.
- [Quota allocation](quota-allocation.md): `should-run` and spend semantics.
- [Heartbeat automation prompt](heartbeat-automation-prompt.md): scheduled
  continuation contract.
- [Status data contract](status-data-contract.md): status and dashboard payloads.
- [Effect interpreter packet](reference/effect-interpreter-packet.md): canonical
  effect-request/interpretation/observation lens for `quota should-run`.
- [Public/private boundary](public-private-boundary.md): what may be retained or
  published.
- [Release readiness](product/release-readiness.md): supported v0.x install,
  compatibility, and promotion gates.

## Browse By Subject

- [Guides](guides/): onboarding and task-oriented walkthroughs.
- [Developer Book](/loopx/docs/book/): bilingual foundations, project onboarding, and
  contribution paths.
- [Concepts](concepts/README.md): mental models and reusable design patterns.
- [Operations](operations/README.md): running goals, cadence, attention, and
  authority sources.
- [Architecture and RFCs](architecture/README.md): system boundaries and design
  proposals.
- [Product](product/README.md): foundations, runtimes, surfaces, use cases, and
  roadmaps.
- [Integrations](integrations/README.md): host, runtime, collaboration, and
  external-system adapters.
- [Reference](reference/README.md): stable contracts and versioned protocols.
- [Development](development/README.md): contributor workflows and quality gates.
- [Capabilities](../loopx/capabilities/README.md): outcome-owned capability surfaces.
- [Showcases](showcases/README.md): public-safe cases and reproducible demos.
- [Research](research/README.md): public evidence and benchmark investigations.
- [Update notes](update-notes/README.md): current public progress notes.
- [Archive](archive/README.md): superseded and dated records.

## Project And Community

- [Community entry points](community.md) ([中文](community.zh-CN.md))
- [Current technical directions](project/technical-directions.md)
- [Open strategy reviews](community/open-strategy-reviews.md)
  ([中文](community/open-strategy-reviews.zh-CN.md))
- [Contributing](../CONTRIBUTING.md)
- [Contributor tasks](development/contributor-tasks.md)
- [Governance](../.github/GOVERNANCE.md)
- [Authors and contributors](project/authors.md)
- [Licensing and the v0.4.8 transition](project/licensing.md)
- [Project history](project/history.md)
- [Name and marks](project/trademarks.md)
- [Brand guide for external use](project/brand-guide.md) ([中文：对外品牌使用指南](project/brand-guide.zh-CN.md))
- [ADOPTERS](../ADOPTERS.md): voluntary self-attested adoption directory

## Documentation Policy

New documents belong in the narrowest owning directory, not the `docs/` root.
Read the [documentation layout and migration policy](development/documentation-layout.md)
for placement, compatibility, coverage, and public-boundary rules. Every
public document must be reachable from this page or a category index.
