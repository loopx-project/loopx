# LoopX Product Capabilities

A LoopX capability is a stable, provider-neutral contract for producing one
bounded, verifiable caller outcome from LoopX state. It owns the domain policy,
normalizes provider observations, validates the result, and proposes a typed
transition to the Kernel.

Each built-in capability owns its canonical README, registration metadata, and
implementation in one package below this directory. The documentation site is
a build-time projection of those package-owned Markdown files, not a second
authored copy. A directory still does not become a shipped capability merely by
existing: registration metadata, a real entrypoint, and durable validation are
required.

That makes it different from the surrounding boundaries:

- the [Kernel](../../docs/architecture.md#runtime-responsibility-model) owns durable
  goal, todo, gate, quota, recovery, and scheduling truth;
- a provider performs a bounded external or local operation and returns
  readback;
- an [extension](../../docs/reference/extensions.md) packages and operates an optional
  provider without acquiring Kernel authority;
- host declarations such as `shell` and `network` describe runtime capacity,
  not a product capability or a permission grant.

## Inspect What This Release Can Do

The runtime registry is authoritative. Directories and documentation do not
become shipped capabilities merely by existing:

```bash
loopx capability list --format json
loopx capability show issue-fix --format json
```

`list` reports registered capability and provider readiness. `show` adds the
user value, maturity, entry commands, explicit write boundary, implemented
protocols, and durable validation for one capability. Use that readback before
enabling an advanced path or optional provider.

## Choose By Outcome

The selected documents below explain human usage paths behind registered
capabilities. The CLI remains the source for the complete catalog and for exact
availability and maturity in the installed release.

### Engineering Delivery

| You need to... | Capability path |
| --- | --- |
| Prepare and qualify local no-upload benchmark experiments through a fail-closed evidence lifecycle | [Benchmark Toolkit](benchmark_toolkit/README.md) |
| Turn public issue and PR signals into a focused, reviewable fix with validation evidence | [issue-fix](issue_fix/README.md) capability ([中文](issue_fix/README.zh-CN.md)) |
| Qualify the exact final diff through bounded review, safe repair, and strict receipts | [Change Quality](change_quality/README.md) |
| Review a changing public PR queue against exact-head evidence and typed completion rules | [Pull Request Review](pr_review_queue/README.md) |
| Detect source-head drift and safely rebuild a local stack of already reviewed branches | [Integration Branch](integration_branch/README.md) |
| Gate local repository changes by a typed schedule and retain blocked unmerged work across restarts | [Repository Change Window](repository_change_window/README.md) |

### Research And Decision Continuity

| You need to... | Capability path |
| --- | --- |
| Preserve questions, hypotheses, experiments, findings, and composition frontiers across a long exploration | [Explore](explore/README.md) ([中文版](explore/README.zh-CN.md)) |
| Separate current evidence, advisory proposals, and verified outcomes before making a decision | [Decision Context](decision_context/README.md) ([中文](decision_context/README.zh-CN.md)) |
| Recall a settled autonomous turn without manufacturing a new user prompt | [Agent Turn Recall](agent_turn_recall/README.md) |
| Add optional, provider-neutral preference recall without making memory the state authority | [Semantic Preference](semantic_preference/README.md) |
| Preserve typed feedback memory and evaluate bounded recall/application pilots | [Reward Memory](reward_memory/README.md) ([中文](reward_memory/README.zh-CN.md)) |

### Operations And Projection

| You need to... | Capability path |
| --- | --- |
| Deliver release-owned skills into selected project-local host surfaces | [Project Skill Delivery](project_skill_delivery/README.md) |
| Compose scheduled or progress-triggered reports with source, archive, delivery, and settlement receipts | [Periodic Report](periodic_report/README.md) |
| Turn public/private content signals into reviewable source, angle, draft, feedback, and publish-gate packets | [Content Operations](content_ops/README.md) |
| Inventory, archive, migrate, and rerank a material store without losing raw source authority | [Material Lifecycle](material_lifecycle/README.md) ([中文](material_lifecycle/README.zh-CN.md)) |
| Inspect compatibility routes for public-safe external-value intake while callers migrate to outcome-owned capabilities | [Value Connectors](value_connectors/README.md) |
| Observe a long-running harness session one-way and read back an integrity receipt and stall/repetition/recovery projection with no runtime authority | [Reliability Diagnostics](reliability_diagnostics/README.md) ([中文](reliability_diagnostics/README.zh-CN.md)) |

## Contributor Navigation And Ownership

Every registered built-in capability package contains a `catalog_entry.py`.
That record declares its stable id, commands, provider boundary, canonical
documentation source, published route, implemented protocols, and durable
validation. The root [`catalog.py`](catalog.py) only composes package-owned
entries; it does not maintain a second capability-to-document map.

Use the runtime readback for the installed release:

```bash
loopx capability list --format json
loopx capability show <capability-id> --format json
```

`show` reports `canonical_doc`, `documentation_route`, commands, write
boundaries, protocols, smokes, and provider readiness from the same registered
record used to build the documentation site.

A capability README can also carry a `## Code Map` section. This is a table
with one row per module that states which step it owns, followed by the files
to touch for common changes. It lets a contributor add a field without first
reading the whole package. Once a README has a code map,
`tests/test_capability_code_maps.py` requires the map to list every module in
the package, and the Chinese mirror to name the same modules, so the map
cannot silently go stale. Add a code map when a package grows past a handful of
modules.

Supporting packages such as shared context-provider helpers may live under
this namespace without a `catalog_entry.py`; they are internal modules, not
product capabilities. Optional providers and extension-delivered capabilities
keep their documentation beside the owning extension package and use the same
build projection without gaining Kernel authority.

## From Capability To Provider

The execution and control paths deliberately run in opposite directions:

```text
Agent -> Capability -> Provider -> external system
Provider readback -> Capability transition proposal -> Kernel
```

Start from the caller outcome, not from an extension name. A built-in provider
may already implement the capability. When an optional implementation is
needed, inspect its declared permissions and readiness, then use the explicit
install, doctor, enable, disable, upgrade, and rollback lifecycle documented in
[Extensions and Capabilities](../../docs/reference/extensions.md). Installing an
extension does not grant new authority.

## Architecture Rule: Domain Lanes, Not Kernel Columns

An operator surface may render LoopX as an agent-native Kanban. The Kernel
supplies generic lifecycle operators such as claim, gate, monitor, complete,
supersede, quota, and writeback. A capability may add a domain lane that
interprets provider observations, but it must not create parallel todo or
scheduling authority.

For example, Issue Fix can project
`feasibility -> patch -> checks -> review -> merge`, while an experiment path
can project `hypothesis -> execute -> evaluate -> promote/retire`. These labels
come from capability-owned domain state and accepted Kernel transitions; they
are not new core lifecycle statuses. If a domain stage changes permission,
claim eligibility, quota, a user gate, or terminal closure, the capability must
propose a typed transition through the existing Kernel contract.

Keep Kernel control-plane code generic. Put scenario-specific contracts,
implementation modules, CLI entrypoints, package documentation, and smokes
under the capability they serve. Cross-capability architecture, Kernel and
extension contracts, and general guides remain under `docs/`. Do not add a
registered capability until there is a package-owned catalog entry, canonical
README, real entrypoint, and durable smoke. Future ideas belong in product
planning docs until they have executable evidence.
