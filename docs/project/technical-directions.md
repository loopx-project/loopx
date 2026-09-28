# Current Technical Directions

This page is the maintainer-curated map of LoopX's current strategic programs.
It helps contributors understand where the project is investing, how mature
each direction is, and where a useful contribution can begin. It is not a
delivery promise, a release plan, or a replacement for shipped contracts.

> Language note: the
> [Chinese version](technical-directions.zh-CN.md) and this English version are
> semantic mirrors. A material difference between them is a defect.

## How To Read This Map

- Code on `main`, released artifacts, and stable reference contracts define
  shipped behavior.
- An RFC records a proposal or accepted architectural decision at the status
  stated in that RFC. It does not make unimplemented behavior real.
- An integration branch is an implementation candidate. It is not a second
  product baseline and does not change `main` contracts until promoted.
- A direction tracker records outcomes, boundaries, and material decisions.
  A separate bounded issue or task-board row is required before work is
  claimable.
- The pinned
  [Current technical directions and known limitations](https://github.com/huangruiteng/loopx/discussions/2851)
  Discussion is the community-facing projection of this page.

Merged active RFCs are Accepted and claimable under the [RFC lifecycle contract](../architecture/rfcs/README.md#how-to-read-this-index). Claiming work does not satisfy live qualification, spending, deployment or promotion gates.

Use these delivery maturity terms consistently:

| Stage | Meaning |
| --- | --- |
| Shipped / hardening | The behavior or architectural contract is in `main`; work improves reliability, parity, or usability. |
| Incubating / qualification | A real candidate exists, but compatibility, evidence, or promotion gates remain. |
| Active research | The program is running evidence-producing experiments; results do not automatically become defaults or product claims. |
| Design accepted / implementation pending | The merged RFC is a qualified design basis and bounded work may be claimed; implementation and promotion evidence remain separate. |
| Held | The direction remains visible, but implementation should not begin until the stated gate changes. |

## Stable Foundation: Control-Plane Reliability

Goals, typed todos, quota, scheduler hints, evidence, Effect Program
settlement, recovery, and host parity remain the shared substrate under every
strategic program. Their reliability work continues through the
[Contributor Task Board](https://github.com/huangruiteng/loopx/blob/main/docs/development/contributor-tasks.md)
and the `control-plane`
label. It is ongoing product hardening, not a competing source of direction.

Cross-domain ordering, complete RFC coverage and product acceptance are maintained in the [LoopX overall roadmap](../architecture/rfcs/loopx-overall-roadmap-v0.md). This page retains contribution routing; S streams, G milestones and R cards do not replace domain contracts or actual Todos.

## Strategic Programs

| Direction | Outcome | Stage | Start here |
| --- | --- | --- | --- |
| Long-Horizon Benchmarks and Evidence | Produce benchmark-native, reproducible evidence for long-horizon capability and use controlled tasks to study mechanisms. | Active research | [Tracker #3243](https://github.com/huangruiteng/loopx/issues/3243) · [RFC](../architecture/rfcs/long-horizon-harness-benchmark-research-program-v0.md) |
| Reliability Diagnostics and Governed Delivery | Prove an observer-first product entry that diagnoses long-running workflows without changing agent execution, then adds authority only at accepted seams. | Design accepted / delivery qualification | [RFC](../architecture/rfcs/long-running-agent-reliability-diagnostics-governed-delivery-v0.md) |
| Operator Surface and IM Integration | Make goals, sessions, decisions, evidence, and bounded collaboration legible through a coherent operator workspace. | Partial delivery / unified-journey qualification | [Tracker #3244](https://github.com/huangruiteng/loopx/issues/3244) · [integration branch](https://github.com/huangruiteng/loopx/tree/frontend-control-plane-im-prototype-rfc) |
| Shared Goal Authority and Cross-host Coordination | Coordinate explicitly shared goals across hosts without turning a provider or host session into control-plane authority. | Design accepted / provider qualification | [Tracker #3245](https://github.com/huangruiteng/loopx/issues/3245) · [RFC](../architecture/rfcs/shared-goal-authority-state-provider-v0.md) |
| Architecture and Research Incubator | Qualify architectural changes and research mechanisms before they expand production scope. | Mixed; see the portfolio below | [Tracker #3246](https://github.com/huangruiteng/loopx/issues/3246) · [RFC index](../architecture/rfcs/README.md) |

## Long-Horizon Benchmarks And Evidence

The benchmark program has two separate lanes:

1. **Capability evidence** asks whether LoopX changes benchmark-native outcome,
   efficiency, or recovery under matched conditions.
2. **Mechanism research** asks why a change occurred by studying stride,
   evidence delivery, replan, exploration, human attention, memory utility,
   and capability evolution.

ALE, LHTB, and DeepSWE provide complementary external-validity environments.
LoopX preserves each benchmark's native result and does not publish a synthetic
aggregate score. Contributor-ready work includes deterministic adapter
fixtures, treatment-integrity checks, public-safe reducers, and analysis
contracts. Live cases, raw tasks, trajectories, verifier output, uploads,
official scoring, and unpublished comparisons remain maintainer-owned.

## Reliability Diagnostics And Governed Delivery

A default-off L1 prototype and DSH event adapter exist in the [capability README](../../loopx/capabilities/reliability_diagnostics/README.md); C0/C1 and overhead qualification remain open. This product direction turns the broader commercialization thesis into a
bounded entry offer. Its first operating level is a shadow observer between a
native harness and full LoopX adoption: it consumes one-way events, writes an
independent diagnostic ledger, and may not inject prompts, schedule, retry,
stop, resume, gate, or mutate worker state. A matched native/passive benchmark
arm must prove both diagnostic value and non-interference before any control
authority is considered.

Later levels separate advisory recommendations, seam-scoped governed commands,
and full semantic-control-plane adoption. Every formal pilot requires an
outcome owner, fixed budget, matched or explicitly weaker baseline, acceptance
criteria, reusable asset path, and rollback. This is a draft product and
delivery contract, not evidence of paid PMF or permission to build a managed
service before the promotion gates pass.

## Operator Surface And IM Integration

At `f1166e81e`, local steward chat, executor settings, team-plan confirmation, attached/managed foundations and partial Lark verticals exist on `main`. The complete cross-entry execution/recovery journey remains unqualified. [#3167](https://github.com/huangruiteng/loopx/pull/3167) and its integration branch are historical incubation inputs, not evidence that every current frontend is unshipped. Use the [Desktop RFC](../architecture/rfcs/desktop-execution-frontends-v0.md), selected release artifact and roadmap S4/S5 entrypoint qualification.

Promotion to `main` follows this ledger:

1. characterize shared projection and session contracts;
2. isolate provider-neutral backend, delivery, and receipt boundaries;
3. promote cohesive runtime or projection slices with parity checks;
4. promote UI only after its source projections and authority boundaries are
   stable, with owner preview for first-screen changes;
5. keep credentials, provider payloads, private receipts, local paths, and raw
   sessions outside public fixtures and browser state.

`@maxliux5` is the current implementation lead, not a repository-wide
maintainer appointment. Lark-specific paths follow the subsystem review route
recorded in [project governance](https://github.com/huangruiteng/loopx/blob/main/.github/GOVERNANCE.md);
cross-subsystem
and mainline promotion decisions remain with the lead maintainer.

## Shared Goal Authority And Cross-Host Coordination

This direction is intentionally not called a "shared metadata database."
NoKV is an unpromoted optional provider candidate behind LoopX authority, not
the authority itself. Agents do not connect directly to NoKV. Run history,
status, quota, scheduler state, host sessions, and evidence retain their
existing owners.

Provider-neutral stores, File/SQLite candidates, PostgreSQL conformance and in-process service admission/identity rotation already exist. Next are roadmap R5 local D1–D3 durability qualification and R6 authenticated real cross-host service, distributed budgets and recovery. Do not repeat the initial `claim_work` extraction or treat a service seam as a deployment. NoKV and other provider promotions retain their real-backend, compatibility, retention and explicit-approval gates.

## Architecture And Research Incubator

| Exploration | Stage | Current entry | Implementation rule |
| --- | --- | --- | --- |
| Frontier science research portfolio | Design accepted / proposal only | [Bilingual RFC](../architecture/rfcs/frontier-science-research-program-v0.md) | Route ten tracks through existing owners; qualify sequential evidence, continuation compression and stride shadow experiments before active treatment. No research uplift or resource commitment is implied. |
| Effect Program and settlement algebra | Accepted / runtime hardening | [RFC](../architecture/rfcs/agent-loop-effect-interpreter-v0.md) | Improve the shared typed contract and negative coverage; keep scheduler ownership and domain-local ACK semantics explicit. |
| TypeScript control-plane migration | Accepted / transaction-payoff phase | [RFC](../architecture/rfcs/typescript-control-plane-migration-v0.md) | Cut over complete transactions, delete Python semantic/facade debt, and report bridge traffic plus migration economics; preserve delivery/vision decisions as domain-local reducers rather than generic Effect Program steps. |
| Hierarchical agent stride | Active research | [#3203](https://github.com/huangruiteng/loopx/issues/3203) | Qualify read-only and shadow evidence before adaptive selection. |
| Research exploration control plane | Design accepted / typed frontier | [RFC](../architecture/rfcs/research-exploration-control-plane-v0.md) | Keep Explore, goal-frontier, and execution authority separate. |
| Human Attention Wishlist | Design accepted / non-blocking sidecar | [#3179](https://github.com/huangruiteng/loopx/issues/3179) | Do not change user gates, selected work, quota, or notification authority. |
| Goal artifact lifecycle projection | Design accepted / read model | [RFC](../architecture/rfcs/goal-artifact-lifecycle-projection-v0.md) | Derive milestones and legal next transitions read-only before adding writes. |
| Post-outcome memory utility | Design accepted / research | [#3214](https://github.com/huangruiteng/loopx/issues/3214) | Attribute utility only after verified outcomes; retrieval and model judgment remain advisory. |
| Goal Channel and Agent IM/OpenViking boundaries | Design accepted / integration exploration | [RFC index](../architecture/rfcs/README.md) | Keep delivery, durable control state, and scoped context under separate owners. |
| Agent session execution modes | Design accepted / cross-host admission contract | [RFC](../architecture/rfcs/agent-session-execution-modes-v0.md) | Require one explicit per-binding mode, one executor, and validated writeback before a host binds sessions; keep Desktop product flows, service lifecycle, and continuation in their owning documents. |

An exploration becomes implementation-ready only when it has a real caller or
compatibility contract, an agreed smallest slice, and focused qualification.
Do not add speculative modules or duplicate authority merely because an RFC
describes a possible future.

## Contribution And Governance Loop

The [first-review roster](../../.github/GOVERNANCE.md#first-review-responsibilities)
routes module-level feedback and records pending scope invitations. It does
not change the stages, promotion gates or implementation authorization here.

1. Choose the closest direction tracker and read its current stage and
   boundary.
2. Find a bounded task on the
   [Contributor Task Board](https://github.com/huangruiteng/loopx/blob/main/docs/development/contributor-tasks.md),
   or open a contributor
   task issue that names the direction, intended base branch, smallest slice,
   non-goals, and validation.
3. For incubation work, state whether the PR targets `main` or an integration
   branch. A `main` PR must not silently depend on an unpromoted branch-only
   contract.
4. Keep discussion and direction tracking in the umbrella issue; keep concrete
   implementation and review in its own issue or PR.

Periodic [Open Strategy Reviews](../community/open-strategy-reviews.md) may
compare up to four directions when cross-cutting questions benefit from live
discussion. The review is advisory: it records a disposition, owner, next
artifact or evidence request, and review trigger. It does not vote a direction
into `main`, change an RFC stage, or authorize implementation.

A material stage, owner, integration-branch, promotion-gate, or scope change
must update this page through a PR. The RFC index and task board should change
in the same PR when their routing changes. After merge, maintainers update the
pinned Discussion; the Discussion does not override merged repository truth.

The four `direction/*` labels are routing aids, not maturity or authority
claims. Implementation-lead recognition records current public work and does
not silently grant repository permissions or maintainer status.
