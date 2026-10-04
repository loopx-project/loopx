# Ecosystem Adoption and Derivatives

> [简体中文](ecosystem-adoption.zh-CN.md)

This maintainer-observed inventory links public evidence of projects using,
integrating, studying, or reimplementing LoopX. Start with
[workflows and integrations](#1-workflows-and-integrations) for concrete use;
[proposals and deferred adoption](#3-proposals-and-deferred-adoption) records
work that has not become an adopted runtime.

> **Evidence boundary:** inclusion is a factual record, not an endorsement or
> a production-deployment claim. A merged PR proves that code or documentation
> landed; a published release, bounded pilot, and open proposal prove different
> things. Validation results in linked reports are their authors' reports,
> not independent reproductions by this inventory.

[`ADOPTERS.md`](../../ADOPTERS.md) is the separate, voluntary directory where
projects and users describe their own use. Its empty registration table does
not mean there is no observed use: the evidence below does not depend on an
owner submitting a directory entry.

## 1. Workflows and Integrations

- **CGC 2046** (CodingGirlsClub) — merged
  [PR #838](https://github.com/CodingGirlsClub/cgc_2046/pull/838) records a move
  to LoopX with Codex CLI for development orchestration; merged
  [PR #841](https://github.com/CodingGirlsClub/cgc_2046/pull/841) adds conditional
  merging to `develop` while retaining human control for specified sensitive
  changes, `main`, and releases. **Status: development-workflow configuration
  merged**; this is not a dependency in the application's production runtime.
  Checked September 30, 2026.
- **NoKV** — merged [PR #518](https://github.com/NoKV-Lab/NoKV/pull/518)
  adds a single-node test stack for LoopX's NoKV authority qualification.
  The author reports live conformance checks; the tool explicitly does not
  establish production availability, failover, or multi-owner operation.
  The separate metadata-runtime promotion gate
  [issue #511](https://github.com/NoKV-Lab/NoKV/issues/511) remains open.
  Its seed-routing helper [PR #4724](https://github.com/loopx-project/loopx/pull/4724)
  is merged, while the companion ladder
  [PR #4726](https://github.com/loopx-project/loopx/pull/4726) remains open;
  the helper merge alone does not close that promotion gate.
  **Status: qualification tooling merged, with separate acceptance still open**.
  This is concrete integration work beyond the README relationship below,
  not proof that NoKV runs LoopX in production. NoKV's
  [current README evidence boundary](https://github.com/NoKV-Lab/NoKV/blob/b8d59c4ff30f2cdfcd8eb4a70cc6d3ec8ab9c420/README.md#evidence-and-qualification)
  separately marks full-surface native CLI and installed Python wheel acceptance
  against real services as not qualified. The historical single-node stack
  result does not establish those current, broader acceptances.
  Checked October 4, 2026.
- **GoTry** (Danceiny) — uses LoopX goals, Codex task bindings and heartbeats
  for multiple development lanes. [Issue #18](https://github.com/Danceiny/gotry/issues/18)
  records setup; [PR #187](https://github.com/Danceiny/gotry/pull/187), merged,
  records delivery validation. **Status: reported development-workflow use**;
  this does not establish a LoopX dependency in the travel application's runtime.
- **mimofan** (XiaomingX) — organizes UI/engine repairs through LoopX Todos.
  [PR #738](https://github.com/XiaomingX/mimofan/pull/738) explicitly names that
  workflow and is merged. **Status: development-workflow evidence**.
- **Meta-RLR** (hk20013106) — [PR #17](https://github.com/hk20013106/RLR/pull/17),
  merged, adds a CLI/JSON maintenance boundary. LoopX owns maintenance
  goal/todo/evidence/monitor/replan state while `research_loop` owns scientific
  state. **Status: maintenance integration merged**; later auto-wake
  [PR #26](https://github.com/hk20013106/RLR/pull/26) is closed unmerged.
- **LoopX Console** (xielixing) — a third-party BitFun MiniApp uses the local
  LoopX CLI, `quota should-run` and host Agent execution for GitHub issue repair.
  [Source and installation](https://github.com/xielixing/loopx-console) and
  [releases](https://github.com/xielixing/loopx-console/releases) are public.
  **Status: independently published**; OpenBitFun upstream inclusion is a
  separate proposal below.
- **zyra** (BingruL) — its [packaging configuration](https://github.com/BingruL/zyra/blob/fix/execution-timeouts-and-diagnostics/pyproject.toml)
  includes an embedded LoopX runtime and CLI entry points.
  **Status: source and packaging integration observed**; deployment and
  sustained runtime use were not verified.
- **Hufu** (Blicae8917) — [PR #70](https://github.com/Blicae8917/hufu/pull/70),
  merged, adds an opt-in LoopX v0.5.2 RunOnce Consumer. The deployment provider
  supplies the real transport and Host invocation; [issue #76](https://github.com/Blicae8917/hufu/issues/76)
  remains open for status projection. **Status: bounded integration merged,
  companion work remains**.
- **benjamin-plugins** (Yidada) — [PR #1](https://github.com/Yidada/benjamin-plugins/pull/1),
  merged, adds a Codex plugin calling the official LoopX kernel. The author
  reports a source-checkout CLI contract smoke; PyPI installation was not
  verified and background scheduling remains host-owned.
  **Status: plugin merged**.
- **Adaptive-Agent-Orchestration-Protocol** (YuemingHub) —
  [PR #41](https://github.com/YuemingHub/Adaptive-Agent-Orchestration-Protocol/pull/41),
  merged, registers LoopX as an optional execution-continuity provider.
  [Issue #42's pilot report](https://github.com/YuemingHub/Adaptive-Agent-Orchestration-Protocol/issues/42#issuecomment-5249833699)
  reports a bounded Linux CLI recovery/gate test; [later readback](https://github.com/YuemingHub/Adaptive-Agent-Orchestration-Protocol/issues/42#issuecomment-5287847666)
  does not establish ongoing adoption. **Status: protocol integration and
  non-production pilot**, not default production adoption.

## 2. Mechanism Borrowing

These projects explicitly credit LoopX ideas. Native implementations and
accepted design documents are distinct from depending on the LoopX runtime.

- **surogates** (invergent-ai) — [comparison and adoption plan](https://github.com/invergent-ai/surogates/blob/master/docs/superpowers/plans/2026-08-03-loopx-adoption.md)
  selects durable grants, objective budgets and evaluator memory, while
  retaining its own storage and runtime. [PR #188](https://github.com/invergent-ai/surogates/pull/188),
  [#190](https://github.com/invergent-ai/surogates/pull/190) and
  [#191](https://github.com/invergent-ai/surogates/pull/191) are merged.
  **Status: code-level borrowing**; live PR status supersedes the plan's older table.
- **future-os** (futuregene) — [PR #253](https://github.com/futuregene/future-os/pull/253)
  and [#255](https://github.com/futuregene/future-os/pull/255), merged, implement
  selected multi-agent and goal-frontier mechanisms in Rust with explicit
  LoopX references. **Status: native reimplementation**.
- **gptme-contrib** — [PR #1373](https://github.com/gptme/gptme-contrib/pull/1373),
  merged, credits LoopX research for public/private evidence sanitization.
  **Status: code-level borrowing**.
- **KiroCrew** — [PR #3229](https://github.com/kirodotdev/KiroCrew/pull/3229),
  merged, adopts durable typed gates and debit-after-writeback in its
  perpetual-agent RFC. It deliberately retains different wake and work-selection
  mechanisms. **Status: design adoption, documentation only**.
- **multica** (LRM-Teams) — [PR #2174](https://github.com/LRM-Teams/multica/pull/2174),
  merged, records five LoopX-inspired collaboration principles.
  **Status: documentation only**, with no LoopX runtime change.

## 3. Proposals and Deferred Adoption

- **MilkSU** — [issue #189](https://github.com/MilkSU-Official/milksu/issues/189)
  asks whether to adopt LoopX, borrow its durable Goal/state/retry ideas, or
  retain the existing ACP approach. **Status: open design evaluation**, not an
  accepted integration or implementation. Checked October 3, 2026.
- **ai-skills** — [issue #468](https://github.com/MichaelHeaton/ai-skills/issues/468)
  proposes LoopX-inspired quota-gated cooldown and self-wake checks without a
  LoopX runtime dependency. The
  [September 23 follow-up](https://github.com/MichaelHeaton/ai-skills/issues/468#issuecomment-5796952500)
  leaves hook/upstream design work open because `/loop` is a built-in command.
  **Status: historical open proposal**, not a shipped skill or runtime adoption.
  Checked October 3, 2026.
- **OpenViking / VikingBot** — open
  [PR #5223](https://github.com/volcengine/OpenViking/pull/5223) proposes
  optional, default-off LoopX-backed background long tasks. LoopX owns
  Goal/Todo state and execution gates; the Bot supplies the worker, model
  and tools. The author reports real CLI tests with a model substitute;
  real-model end-to-end acceptance and Docker build validation remain open.
  The proposal's [pinned packaging](https://github.com/volcengine/OpenViking/blob/406827d73594fda9def251912be64bf06b013773/pyproject.toml)
  places `loopx==1.0.5` in the optional `longtask` extra;
  [current main packaging](https://github.com/volcengine/OpenViking/blob/9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14/pyproject.toml)
  does not declare that extra. These are proposal and main snapshots,
  not an instruction to install an unreleased extra.
  **Status: runtime integration proposed, not merged or released**.
  Checked October 4, 2026.
- **Opensiro VSM harness index** — merged
  [PR #607](https://github.com/opensiro/vsm-harness-index/pull/607) records
  a feasibility stop for a frozen LoopX comparison; a subsequent
  [registered-peer harness](https://github.com/opensiro/vsm-harness-index/pull/614)
  was built without live model execution. Merged
  [PR #618](https://github.com/opensiro/vsm-harness-index/pull/618) then retires
  that operated experiment under a public-evidence-only research policy.
  **Status: historical research artifacts, live execution not performed or
  planned for that study**; neither a new benchmark result nor runtime adoption.
  Checked September 30, 2026.
- **OpenBitFun** (GCWing) — built-in console [PR #2836](https://github.com/GCWing/OpenBitFun/pull/2836)
  is open and replaces closed, unmerged #2382. The maintainer
  [prioritizes beta stability before evaluating the larger feature](https://github.com/GCWing/OpenBitFun/pull/2836#issuecomment-5613986161).
  **Status: upstream integration proposed**, separate from the published
  third-party LoopX Console.
- **codexia** — upstream [PR #71](https://github.com/milisp/codexia/pull/71)
  was closed unmerged after the author explained it targeted the wrong repository;
  downstream [PR #1](https://github.com/connorodea/codexia-task-management/pull/1)
  remains open. **Status: downstream proposal**; the author explicitly reports
  that the CLI contract has not been checked against a live installation.
- **spoon-core** — [issue #285](https://github.com/XSpoonAi/spoon-core/issues/285)
  proposes optional read-only LoopX control context before a model call.
  **Status: open proposal**.
- **GENesis-AGI** — [issue #2123](https://github.com/WingedGuardian/GENesis-AGI/issues/2123)
  proposes evaluating LoopX before building a durable goal backend.
  **Status: open evaluation request**.
- **Orca** — [issue #12628](https://github.com/stablyai/orca/issues/12628)
  requests LoopX-like goal-driven iteration. **Status: open user request**,
  not maintainer acceptance or implementation evidence.
- **OpenAgentEmail** — [discussion #180](https://github.com/openagentemail/openagentemail/discussions/180)
  explores optional control-plane compatibility. The
  [September 12 follow-up](https://github.com/openagentemail/openagentemail/discussions/180#discussioncomment-18407019)
  suggests a small reversible provider trial rather than critical-path adoption.
  That comment identifies itself as AI-authored. **Status: discussion/pilot proposal**.
- **Quesen** — [discussion #3735](https://github.com/huangruiteng/loopx/discussions/3735)
  led to a [prepared-Effect risk-admission packet](https://github.com/Shxnque/quesen/blob/main/docs/integrations/loopx-prepared-effect-packet.md).
  It explicitly proposes shadow mode and preserves human authority.
  **Status: integration packet, not a code proposal**.
- **8x8-user-edition** — [PR #63](https://github.com/8x8org/8x8-user-edition/pull/63),
  merged, defers runtime adoption because of overlap with existing authority and
  state systems, while selecting protocol ideas. **Status: runtime adoption deferred**.
- **Mindthus** — compare-and-absorb [issue #132](https://github.com/rv198-star/Mindthus/issues/132)
  is closed. **Status: recorded evaluation**; issue closure alone does not
  establish runtime adoption.
- **GovernLoop** — Phase 0 capability-mapping [PR #23](https://github.com/liangzhipengdamon-maker/GovernLoop/pull/23)
  is closed unmerged. **Status: historical evaluation proposal**.
- **hartevo-desktop** — [issue #55](https://github.com/tangpingqingwa/hartevo-desktop/issues/55)
  proposes a Mission Control kernel informed by Prime Agent and LoopX.
  **Status: open design issue**.
- **polyphemus** — [issue #89](https://github.com/Diekgbbtt/polyphemus/issues/89)
  studies LoopX primitives. **Status: open research request**.

## 4. Learning, Coverage and Collaboration

- **General Loop** — its
  [pinned README comparison](https://github.com/CosmosShadow/general-loop/blob/0bd8fb3b5b5c2e8e02b35e627ad715cade23bba0/README.md)
  contrasts LoopX's control-plane protocol with Markdown coordination on
  existing agent hosts. **Status: comparative reference**; the mention does not
  establish a LoopX dependency or adopted mechanism. Checked October 3, 2026.
- **GitHub-Michelin** — merged
  [PR #128](https://github.com/ZhenningLang/GitHub-Michelin/pull/128) adds a bilingual
  [LoopX editorial entry](https://github.com/ZhenningLang/GitHub-Michelin/blob/a75520b79cff3d9818ab6b60100677b93beaef81/categories/agent-tooling/work-state/loopx.md).
  **Status: editorial coverage**, not runtime adoption. Its September 27
  assessment and snapshots are the editor's dated observations, not a live
  qualification of current LoopX behavior. Checked October 3, 2026.
- **ai-agent-book / Understanding AI Agents** (bojieli) —
  [PR #614](https://github.com/bojieli/ai-agent-book/pull/614), merged, introduces
  LoopX as a concrete Loop Engineering framework. [Chapter 10](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter10.md)
  cites a fixed version and preserves its experimental evidence boundary.
  **Status: teaching material**, not reader adoption statistics.
- **NAVER fe-news** — the [September 2026 newsletter](https://github.com/naver/fe-news/blob/master/issues/2026-09.md)
  explains LoopX and its installation path in Korean. **Status: editorial coverage**,
  not a NAVER deployment claim.
- **OpenViking / NoKV** — [OpenViking's README](https://github.com/volcengine/OpenViking/blob/9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14/README.md)
  lists LoopX; [NoKV's README](https://github.com/NoKV-Lab/NoKV/blob/b8d59c4ff30f2cdfcd8eb4a70cc6d3ec8ab9c420/README.md)
  names an active open-source collaboration. **Status: public project relationships**;
  these listings alone do not establish a runtime dependency. Checked October 4, 2026.
- **loopx-book / loopx-book-labs** (cocolord) — a bilingual, protocol-first
  [developer book](https://github.com/cocolord/loopx-book) and
  [runnable labs](https://github.com/cocolord/loopx-book-labs) cover onboarding,
  issue-to-PR work and standalone extensions. **Status: educational resources**.

## 5. Derivatives

- **michaelx1993/loopx → foolzzz/loopx** — one related fork family, not two
  independent adopters. The
  [downstream changelog](https://github.com/foolzzz/loopx/blob/6c16e4a06797f2b0a81a53827e7fd8082913b36d/CHANGELOG.md)
  documents divergence from upstream and role-based orchestration. Merged
  [michaelx1993 PR #27](https://github.com/michaelx1993/loopx/pull/27) introduces
  deterministic bookkeeping and a bounded orchestrator digest; merged
  [foolzzz PR #18](https://github.com/foolzzz/loopx/pull/18) prepares that fork's
  2.0.0 release. **Status: downstream code merged**; these are fork changes,
  not an upstream LoopX release or independently verified performance gain.
  Checked October 3, 2026.
- **loopx-HPC** (Sande33p) — [PR #1](https://github.com/Sande33p/loopx-HPC/pull/1)
  is merged in an independent fork, adding optional scientific campaigns,
  PBS/Slurm and MLflow integration. **Status: downstream code merged**;
  the author explicitly leaves live HPC acceptance pending.
- **foreman** (needware) — [PR #1](https://github.com/needware/foreman/pull/1)
  proposes a native TypeScript migration of the LoopX kernel pinned to an
  upstream commit. **Status: open proposal**, not a merged runtime migration.

## Evidence Limits

- A project's own development workflow, a packaged integration, protocol
  borrowing and public coverage are different relationships; do not add them
  together as a production-adopter count.
- Stars, unchanged forks, automated Trending/digest posts and unrelated
  same-name projects are not adoption evidence.
- Creator dogfooding and user-attributed showcases retain their own source
  boundaries in the [Showcase catalog](../showcases/README.md). For example,
  the [MFS refactor case](../showcases/cases/independent-public-engine-refactor.md)
  distinguishes publicly merged PRs from user-reported LoopX attribution.

## Maintenance

- Refresh both language versions through a pull request, checking source
  content, current PR merge state, replacement links and later comments.
  Record the date rather than treating an old label as current evidence.
- Discovery queries include `gh search code "huangruiteng/loopx"`,
  `gh search issues loopx`, `gh search prs loopx` and `gh search repos loopx`;
  review only public evidence and remove duplicate or unrelated matches.
- Keep observed entries here; projects may voluntarily confirm their own use
  in [`ADOPTERS.md`](../../ADOPTERS.md). Do not create self-attested entries on
  their behalf from this inventory.
- Last reviewed: **2026-09-19**. Public-source research: September 18;
  linked PR/issue statuses refreshed September 19.
- Scoped update: **2026-09-30**, covering CGC 2046, NoKV qualification tooling,
  the VikingBot proposal and the retired Opensiro study above. Other entries
  retain their earlier review boundary; this is not a full-table revalidation.
- Scoped update: **2026-10-03**, covering MilkSU, ai-skills, General Loop,
  GitHub-Michelin and the michaelx1993/foolzzz fork family. Current issue/PR
  states, later comments and pinned source files were checked; this update
  adds no confirmed runtime adopter and does not revalidate the rest of the table.
- Scoped update: **2026-10-04**, rechecking OpenViking and NoKV against current
  public PR/issue states and pinned source files. This distinguishes proposal
  packaging, historical qualification tooling and current acceptance limits;
  no live integration was reproduced and other entries were not revalidated.
