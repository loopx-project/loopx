# Architecture RFCs

Architecture RFCs are public design proposals. Each RFC should name its
decision boundary, non-goals, smallest useful implementation slice, and
validation criteria. An RFC may describe future work; current behavior is
defined by the implementation and stable reference contracts.

Start new proposals from the [RFC template](TEMPLATE.md). Existing RFCs should
adopt its maintenance contract when substantial revision would otherwise mix
stable design, current progress, and historical evidence.

The [Current Technical Directions](../../project/technical-directions.md) page
maps RFCs to strategic programs, contribution routes, and promotion gates.

## How to read this index

The sections below are technical ownership areas, not maturity levels. Each
primary RFC appears once under its nearest architectural owner even when it
also affects other areas.

Within each entry, RFC maturity and delivery maturity remain separate facts:

- **RFC status** records the decision state written in the RFC. `Draft` work
  remains open to architectural change even when a bounded slice has shipped.
- **Delivery on `main`** records the implementation state audited against the
  repository. A merged experiment does not make its whole RFC accepted, and an
  accepted RFC may still have later adoption work.
- **Current boundary** names what is real now and what is still excluded. It is
  a navigation aid, not a replacement for stable protocol documentation.

Within an RFC, keep the same separation:

- normative sections define the current decision and acceptance contract;
- a dated execution ledger records shipped slices and experiments without
  silently changing that contract;
- a decision log records explicit approval and the sections it changed;
- an evidence registry maps claims to reproducible, public-safe proof.

Do not append progress reports to a normative delivery plan. Move long ledgers
to a linked `*-execution.md` companion. Schema reduction is never an incidental
cleanup: the RFC or PR must name each removed field, document producer/reader/
writer and compatibility research, define migration and rollback, prove the
claimed semantic equivalence, and record explicit maintainer approval.

This index was last audited against `main` on **2026-09-04**. Update an entry
whenever its RFC status, promoted behavior, or meaningful delivery boundary
changes.

## Overall Product and Delivery Roadmap

- [LoopX Overall Roadmap v0](loopx-overall-roadmap-v0.md)
  ([中文版](loopx-overall-roadmap-v0.zh-CN.md))
  - **RFC status:** Draft portfolio roadmap.
  - **Delivery on `main`:** Planning and audit evidence, not runtime promotion.
  - **Current boundary:** Maps all 30 pre-existing primary RFCs and important
    non-RFC domains into 13 streams, G0–G5 product milestones and R1–R7 core
    execution cards. Covers product, multi-LoopX-Agent collaboration/handoff,
    kernel, hosts, memory, cost, security, operations, research, releases,
    community and adoption. Domain contracts retain their authority gates.

- [App conversations and reusable asynchronous work delivery](app-conversation-and-async-inbox-v0.md)
  - **RFC status:** Draft integration proposal under R1–R3/T0–T4.
  - **Delivery on `main`:** Existing Chat, collaboration and provider inbox owners;
    the full managed/attached App journey is not qualified.
  - **Current boundary:** App-first conversation continuity, truthful activity,
    public-safe golden queries and a replacement-first TS async inbox plan.

## Control-Plane Kernel, State, And Migration

- [Automatic Execution Admission v0](automatic-execution-admission-v0.md)
  ([中文版](automatic-execution-admission-v0.zh-CN.md))
  - **RFC status:** Draft.
  - **Delivery on `main`:** Proposal; local implementation candidate under review.
  - **Current boundary:** S7 quota-owned minimum interval, S2 atomic local admission,
    S4 App recommendation floor first; managed Turn admission, App hook coverage and settings remain unqualified.

- [Human-confirmed domain operations v0](human-confirmed-domain-operations-v0.md)
  ([中文版](human-confirmed-domain-operations-v0.zh-CN.md))
  - **RFC status:** Draft.
  - **Delivery on `main`:** Proposal only.
  - **Current boundary:** Separates generic authenticated interaction, optional
    financial execution and venue adapters. Defines shared frontend/Lark
    confirmation and automatic outcomes; no runtime or trading permission added.
- [Agent Loop Effect Interpreter v0](agent-loop-effect-interpreter-v0.md)
  ([中文版](agent-loop-effect-interpreter-v0.zh-CN.md))
  - **RFC status:** Accepted.
  - **Delivery on `main`:** Core implemented; bounded adoption continues.
  - **Current boundary:** Effect request, interpretation, observation,
    settlement, and typed Effect Program foundations are shipped. Replan
    planning/ACK stays domain-local until a second real lifecycle justifies
    extraction.
- [TypeScript Control-Plane Migration v0](typescript-control-plane-migration-v0.md)
  ([中文版](typescript-control-plane-migration-v0.zh-CN.md))
  - **RFC status:** Accepted; transaction-payoff phase in progress.
  - **Delivery on `main`:** Substantially implemented; active migration.
  - **Current boundary:** Stage 1 and 2A are complete. Stage 2B is cutting over
    whole semantic transactions and retiring Python facades under parity and
    differential gates.
- [Semantic Vocabulary Convergence and Commit-Time Drift Checks v0](semantic-vocabulary-convergence-v0.md)
  ([中文版](semantic-vocabulary-convergence-v0.zh-CN.md))
  - **RFC status:** Draft.
  - **Delivery on `main`:** M0 registry, generated inventory, and drift smoke
    shipped with the RFC.
  - **Current boundary:** Repository-wide. A curated registry names 26 kernel
    and cross-runtime vocabularies with `module::Symbol` owners, relations,
    the route-to-disposition projection, the Turn Envelope schema owner, and
    ratchets for legacy should-run fields, py/ts twins, and constant forks; a
    generated inventory maps every other closed-set carrier under `loopx/`; a
    public smoke fails closed on drift in either runtime and on any narrowing
    of the registry itself. No enum merge, slot split, field removal, or code
    generation is approved yet.
- [Shared-goal Online Authority and Pluggable Coordination Provider v0](shared-goal-authority-state-provider-v0.md)
  ([中文版](shared-goal-authority-state-provider-v0.zh-CN.md),
  [validation boundary](shared-goal-authority-state-provider-v0-evidence.zh-CN.md))
  - **RFC status:** Draft, under maintainer review.
  - **Delivery on `main`:** Foundation, provider-contract, and local promotion
    preparation slices implemented.
  - **Current boundary:** Recoverable shared-authority foundations, the
    file-backed reference path, NoKV shadow/recovery evidence, the TypeScript
    store contract, PostgreSQL candidate/conformance coverage, and the
    default-off local shadow/cutover foundations are on `main`
    ([#3529](https://github.com/huangruiteng/loopx/pull/3529),
    [#3669](https://github.com/huangruiteng/loopx/pull/3669),
    [#3798](https://github.com/huangruiteng/loopx/pull/3798)). In-process PostgreSQL
    service admission and identity rotation also exist;
    deployed authenticated remote service and provider promotion remain distinct
    qualification gates.
- [Shared Goal Alignment and Governed Amendment Protocol v0](shared-goal-alignment-and-governed-amendment-v0.md)
  ([中文版](shared-goal-alignment-and-governed-amendment-v0.zh-CN.md))
  - **RFC status:** Draft, under maintainer review.
  - **Delivery on `main`:** Stage 1/2 read-only alignment and proposal-admission
    foundations implemented; the RFC remains a draft.
  - **Current boundary:** Current Todo/lease source-basis projection and retained
    amendment admission have no canonical effect. Full Goal-intent versioning,
    governed commit policy/verifier, lease-impact handling and Stage 3+
    qualification remain unshipped. Manager handoff consumes these boundaries;
    it does not provide another amendment writer.
- [Goal Direction Baseline v0](goal-direction-baseline-v0.md)
  ([中文版](goal-direction-baseline-v0.zh-CN.md))
  - **RFC status:** Draft, under maintainer review.
  - **Delivery on `main`:** Proposal only.
  - **Current boundary:** A provider-neutral, Agent-scoped read model and
    synthetic drift fixture plan are proposed. No direction declaration,
    reducer, runtime consumer, Vision writer, scheduler effect, or provider
    integration has shipped.
- [Goal Artifact Lifecycle Projection v0](goal-artifact-lifecycle-projection-v0.md)
  ([中文版](goal-artifact-lifecycle-projection-v0.zh-CN.md))
  - **RFC status:** Draft, under maintainer review.
  - **Delivery on `main`:** Proposal only.
  - **Current boundary:** Milestones, blocking guards, and legal transitions are
    specified as a read-only projection; no canonical lifecycle projection has
    shipped.
- [Goal Instance Identity and Orphan Recovery v0](goal-instance-identity-and-orphan-recovery-v0.md)
  ([中文版](goal-instance-identity-and-orphan-recovery-v0.zh-CN.md))
  - **RFC status:** Draft.
  - **Delivery on `main`:** Identity/recovery proposal; the guided orphan fence
    and M0 registry codec shipped separately in #4808 and #4917.
  - **Current boundary:** Defines R5 lifetime fencing for R2/R3 consumers,
    incompatible activation beyond codec-only v1, typed commit/recovery ownership,
    exact legacy cleanup and product readback. Instance minting, activation and
    resolution remain unshipped; R6 and D1–D3 keep their own qualification gates.

## Planning, Research, And Adaptive Intelligence

- [Goal-scoped Capability Portfolio and Connector Lifecycle v0](goal-scoped-capability-portfolio-v0.md)
  ([中文版](goal-scoped-capability-portfolio-v0.zh-CN.md))
  - **RFC status:** Draft, under maintainer review.
  - **Delivery on `main`:** Proposal; catalog, Agent-context hooks and connector
    inventory are prerequisites rather than a shipped portfolio.
  - **Current boundary:** Existing Goal enablement activates supported capability
    behavior without a second Portfolio opt-in. Direct work needs no DAG or
    adoption record; material dependencies use bounded composition. Policy stays
    capability-owned and existing config/effect/admission owners remain authoritative.
    M0 includes affected product entry points; merged #4813 supplies evidence
    infrastructure, not connector or Portfolio qualification. No new authority.

- [Agent Judgment and Optional Independent Assessment v0](optional-semantic-assistance-jev-v0.md)
  ([中文版](optional-semantic-assistance-jev-v0.zh-CN.md))
  - **RFC status:** Draft; M0 [accepted-for-discussion](https://github.com/loopx-project/loopx/pull/4749#pullrequestreview-5259253204) only; Q1–Q7 remain pending.
  - **Delivery on `main`:** Proposal only; no Jev integration or qualification.
  - **Current boundary:** Discusses eight Agent/assessment opportunities with a
    provisional expected-value investigation order led by same-priority Todo
    and Explore ranking; Jev is one comparator alongside existing models and workflow.
    M0 accepts discussion intake only; research, data/spend, qualification,
    product adoption and runtime authority remain pending and unapproved.

- [Frontier Science Research Program v0](frontier-science-research-program-v0.md)
  ([中文版](frontier-science-research-program-v0.zh-CN.md))
  - **RFC status:** Draft, under maintainer review.
  - **Delivery on `main`:** Proposal only.
  - **Current boundary:** Ten cross-disciplinary research tracks, existing-owner
    routing, evidence limits and staged experiment gates. Prioritizes sequential
    evidence, decision-preserving continuation and stride shadow evaluation;
    no runtime treatment, resource commitment or scientific uplift is promoted.
- [Research Exploration Control Plane v0](research-exploration-control-plane-v0.md)
  ([中文版](research-exploration-control-plane-v0.zh-CN.md))
  - **RFC status:** Draft, under maintainer review.
  - **Delivery on `main`:** Partially implemented.
  - **Current boundary:** The explicit composition projection and successor
    binding from M2 shipped in
    [#3173](https://github.com/huangruiteng/loopx/pull/3173). The canonical
    observation contract, shared write-time gate, model selection, and inferred
    triggers have not been promoted.
- [Hierarchical Agent Stride Control v0](hierarchical-agent-stride-control-v0.md)
  ([中文版](hierarchical-agent-stride-control-v0.zh-CN.md))
  - **RFC status:** Draft, research proposal.
  - **Delivery on `main`:** M1 observation slice implemented.
  - **Current boundary:** Read-only stride observation and its synthetic
    boundary fixture shipped in
    [#3207](https://github.com/huangruiteng/loopx/pull/3207) and
    [#3290](https://github.com/huangruiteng/loopx/pull/3290). Adaptive effect,
    delivery, and authority stride selection remains research-only.
- [Post-Outcome Memory Utility Attribution v0](post-outcome-memory-utility-attribution-v0.md)
  ([中文版](post-outcome-memory-utility-attribution-v0.zh-CN.md))
  - **RFC status:** Draft, under maintainer review.
  - **Delivery on `main`:** Stage 1 implemented.
  - **Current boundary:**
    [#3280](https://github.com/huangruiteng/loopx/pull/3280) binds utility
    observations to verified outcomes without changing retrieval ranking.
    Reducer/read projection, provider readback, ranking influence, and pilot
    promotion remain open.
- [Obelisk Session Evidence Provider v0](obelisk-session-evidence-provider-v0.md)
  ([中文版](obelisk-session-evidence-provider-v0.zh-CN.md))
  - **RFC status:** Draft integration proposal.
  - **Delivery on `main`:** Evaluation only.
  - **Current boundary:** The default-off, read-only evidence-provider boundary
    is documented. Obelisk is not installed, promoted, or authoritative for
    Replan settlement, memory, or action selection.

## Runtime, Capability, And Collaboration Integration

- [Capable Agent Manager and Semantic Work Handoff v0](capable-manager-semantic-handoff-v0.md)
  ([中文版](capable-manager-semantic-handoff-v0.zh-CN.md))
  - **RFC status:** Draft, under maintainer review.
  - **Delivery on `main`:** Partial; private runtime profile, executor settings,
    team-plan confirmation and initial Todo materialization shipped.
  - **Current boundary:** Proposes ordinary host-tool autonomy, persistent scoped
    conversations, long-horizon semantic continuation and automatic result delivery.
    Includes an official Grok Bot study distinguishing availability from goal
    continuation; M0–M4 and A1–A20 define delivery and acceptance, with explicit
    alignment, shared-authority and TS migration dependencies.
    Cross-session restoration, execution takeover and automatic return are specified
    separately, reusing #4094 with optional Obelisk gap recall under its own scope.
    Full runtime-profile qualification and generic handoff migration remain open.


- [Explicit Todo Continuation — Stage A](cross-session-memory-substrate-v0.md)
  ([中文版](cross-session-memory-substrate-v0.zh-CN.md))
  - **Delivery on `main`:** #4094 shipped the explicit local CLI and rich/legacy
    continuation note with revision-guarded ownership adoption.
  - **Current boundary:** Registered agents, same host/Goal, lease-free promoted
    local authority. No generic memory store, automatic host launch, cross-host
    artifacts or automatic result return. Manager/handoff §5.13 integrates this
    adapter into M2/M3; the shipped CLI contract remains until replacement qualifies.

- [Single-Owner Local Daemon v0](single-owner-local-daemon-v0.md)
  - **RFC status:** Draft.
  - **Delivery on `main`:** Proposal only; existing Desktop ownership repair is shipped.
  - **Current boundary:** Service-profile identity, component readiness, supervised
    composition, and recoverable migration are proposed for #3930. A unified
    `loopxd` service has not shipped.
- [Provider-Neutral Turn-Start Inbox Hook v0](provider-neutral-turn-start-inbox-hook-v0.md)
  - **RFC status:** Implemented behind explicit provider configuration.
  - **Delivery on `main`:** Implemented, opt-in.
  - **Current boundary:** The provider-neutral turn-start read contract and
    Lark ACK/replay path shipped in
    [#3678](https://github.com/huangruiteng/loopx/pull/3678) and
    [#3733](https://github.com/huangruiteng/loopx/pull/3733); no provider is
    enabled implicitly.
- [Provider-Neutral Post-Writeback Capability Hooks v0](provider-neutral-post-writeback-capability-hooks-v0.md)
  ([中文版](provider-neutral-post-writeback-capability-hooks-v0.zh-CN.md))
  - **RFC status:** Draft, under maintainer review.
  - **Delivery on `main`:** First end-to-end vertical implemented.
  - **Current boundary:** The periodic-report producer, durable intent
    lifecycle, terminal closeout dispatch, consumer, and approved Goal Channel
    delivery shipped through
    [#3691](https://github.com/huangruiteng/loopx/pull/3691),
    [#3748](https://github.com/huangruiteng/loopx/pull/3748),
    [#3749](https://github.com/huangruiteng/loopx/pull/3749), and
    [#3755](https://github.com/huangruiteng/loopx/pull/3755). General
    multi-capability promotion remains under review.
- [LoopX Desktop Execution Frontends v0](desktop-execution-frontends-v0.md)
  ([中文版](desktop-execution-frontends-v0.zh-CN.md))
  - **RFC status:** Draft.
  - **Delivery on `main`:** Supporting foundations implemented.
  - **Current boundary:** Attached and managed runtime, desktop, and connector
    pieces exist, but the unified execution-frontend/session-ownership contract
    and cross-transport convergence are not accepted as one shipped product
    boundary.
- [Agent Session Execution Modes v0](agent-session-execution-modes-v0.md)
  ([中文版](agent-session-execution-modes-v0.zh-CN.md))
  - **RFC status:** Draft, under maintainer review.
  - **Delivery on `main`:** Partial; the attached-host binding, broker, and
    runtime fencing are implemented, while cross-host admission is proposed.
  - **Current boundary:** Normalizes the attached/managed session-ownership
    decision into one cross-host admission rule: an explicit persisted mode per
    binding, one executor per Agent binding, capability-gated delivery that
    fails closed, and no implicit mode change. Desktop product flows stay with
    the Desktop frontends RFC, local service lifecycle with the daemon RFC, and
    continuation with the manager RFC. Optional preview hosts, session
    rotation, and host promotion remain unapproved.
- [Goal Channel Collaboration v0](goal-channel-collaboration-v0.md)
  ([中文版](goal-channel-collaboration-v0.zh-CN.md))
  - **RFC status:** Draft.
  - **Delivery on `main`:** Lark vertical implemented.
  - **Current boundary:** Goal-bound Lark groups, Kanban, gate notifications,
    shared targets, and Bot runtime integration are shipped. The
    provider-neutral multi-surface model remains draft, and interactive
    transport is refined by the Desktop Frontends RFC.
- [Agent IM, LoopX, and OpenViking Collaboration v0](agent-im-openviking-collaboration-v0.md)
  - **RFC status:** Draft.
  - **Delivery on `main`:** Proposal only.
  - **Current boundary:** LoopX, IM, and OpenViking have adjacent
    implementations, but this three-owner collaboration contract has not
    shipped as an integrated path.

## Operator Experience And Observability

- [Per-Goal Usage, Token, and Cost Surfacing v0](goal-usage-token-cost-v0.md)
  - **RFC status:** Draft.
  - **Delivery on `main`:** Core slice implemented.
  - **Current boundary:** Codex usage capture, normalized aggregation, and
    dashboard surfacing shipped in
    [#3117](https://github.com/huangruiteng/loopx/pull/3117); broader
    runtime/provider coverage and cost semantics remain incomplete.
- [Intelligent Review and Dynamic Presentation Surfaces v0](intelligent-review-presentation-surfaces-v0.md)
  ([中文版](intelligent-review-presentation-surfaces-v0.zh-CN.md))
  - **RFC status:** Draft, under maintainer review.
  - **Delivery on `main`:** Bounded action-review, attention-detail and local
    delivery-review verticals implemented.
  - **Current boundary:** Typed action plans serve Dashboard and the existing
    Lark operation cards. Direct Goal workspace navigation and Overview compose
    bounded graph/acceptance readback, source navigation and snapshot export. General cross-channel disclosure,
    living documents and governed amendment/settlement review remain open.
- [Live Team Workspace v0](live-team-workspace-v0.md)
  ([中文版](live-team-workspace-v0.zh-CN.md))
  - **RFC status:** Draft presentation slice under intelligent presentation.
  - **Delivery on `main`:** Design proposal only; no live team-stream qualification.
  - **Current boundary:** A command surface plus spatial research studio, evidence
    handoff motion, conclusion revision and replay. L1 requires one real
    objection→revision→acceptance→adoption journey with evidence and intervention/stop
    feedback in packaged UI; L2 requires original-coordinator two-cycle acceptance;
    L3 qualifies semantic zoom and display scale. No new scheduler or authority.
- [Human Attention Wishlist v0](human-attention-wishlist-v0.md)
  ([中文版](human-attention-wishlist-v0.zh-CN.md))
  - **RFC status:** Draft, under maintainer review.
  - **Delivery on `main`:** Intentionally deferred.
  - **Current boundary:** The RFC remains a discussion contract. Runtime work is
    held until repeated real usage demonstrates a second need without weakening
    non-blocking authority boundaries.

## Benchmark And Reliability Engineering

- [Benchmark Study Upload and Dashboard Projection v0](benchmark-study-upload-dashboard-v0.md)
  - **RFC status:** Draft integration proposal.
  - **Delivery on `main`:** Proposal only.
  - **Current boundary:** Experiment-board rows and benchmark-native scores
    remain authoritative; the study manifest, upload/readback envelope, and
    campaign-to-run dashboard projection are proposed but not implemented.
- [Long-Horizon Harness Benchmark and Research Program v0](long-horizon-harness-benchmark-research-program-v0.md)
  ([中文版](long-horizon-harness-benchmark-research-program-v0.zh-CN.md))
  - **RFC status:** Draft, research program.
  - **Delivery on `main`:** Active research and engineering program.
  - **Current boundary:** ALE, LHTB, and DeepSWE form the external-validity
    portfolio; benchmark infrastructure and evidence workflows are being built
    without treating the research program as a runtime protocol.
- [Long-Running Agent Reliability Diagnostics and Governed Delivery v0](long-running-agent-reliability-diagnostics-governed-delivery-v0.md)
  ([中文版](long-running-agent-reliability-diagnostics-governed-delivery-v0.zh-CN.md))
  - **RFC status:** Draft, product direction and delivery contract.
  - **Delivery on `main`:** Default-off L1 diagnostic prototype and first DSH
    event-source adapter implemented.
  - **Current boundary:** The capability-owned README records the prototype;
    C0 adapter fidelity, C1 non-interference and measured overhead remain
    required before P0 exit. Broader governed delivery and commercial adoption
    remain proposals.

RFCs must not contain internal conversations, private links, local filesystem
paths, credentials, raw transcripts, or non-public organizational context.
