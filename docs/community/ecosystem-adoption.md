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
- **GoTry** (Danceiny) — [issue #18](https://github.com/Danceiny/gotry/issues/18),
  opened August 28, 2026, reports LoopX goals, Codex task bindings and initial
  heartbeat acknowledgements. A [September 19 follow-up](https://github.com/Danceiny/gotry/issues/18#issuecomment-5740371337)
  reports continued engineering automation and independent worktree delivery.
  [PR #187](https://github.com/Danceiny/gotry/pull/187) merged September 7; its
  reported local checks and [successful exact-head CI](https://github.com/Danceiny/gotry/actions/runs/34110550580)
  concern a product delivery slice, whose body still lists real-inventory UAT
  as open. Merge and CI success do not close that acceptance.
  The [October 2 checkpoint](https://github.com/Danceiny/gotry/issues/18#issuecomment-5950941798)
  retains real-user evidence gates; issues [#20](https://github.com/Danceiny/gotry/issues/20),
  [#22](https://github.com/Danceiny/gotry/issues/22) and
  [#142](https://github.com/Danceiny/gotry/issues/142) remain open at review.
  **Status: dated, author-reported development-workflow use**. This does not
  establish a LoopX dependency in the travel application's runtime, independently
  reproduce the scheduler, or prove product acceptance. Checked October 5, 2026.
- **mimofan** (XiaomingX) — organizes UI/engine repairs through LoopX Todos.
  [PR #738](https://github.com/XiaomingX/mimofan/pull/738) explicitly names that
  workflow and is merged. **Status: development-workflow evidence**.
- **Meta-RLR** (hk20013106) — [PR #17](https://github.com/hk20013106/RLR/pull/17),
  merged August 13, 2026, adds the external CLI/JSON maintenance boundary;
  scientific state remains owned by `research_loop`. Closed, unmerged
  [PR #26](https://github.com/hk20013106/RLR/pull/26) was superseded by
  [PR #34](https://github.com/hk20013106/RLR/pull/34), merged August 23, 2026.
  The checked main [root entry point](https://github.com/hk20013106/RLR/blob/214b8a143e9007ea6750413bad0eac7d30d675fc/research_loop_v04.py)
  composes an outer maintenance adapter. Its [activation and failure boundary](https://github.com/hk20013106/RLR/blob/214b8a143e9007ea6750413bad0eac7d30d675fc/src/rlr_maintenance/autowake.py)
  requires explicit `RLR_META_RLR_AUTOWAKE_CONFIG`, classifies eligible failures
  and resolves verified repair provenance; the [adapter](https://github.com/hk20013106/RLR/blob/214b8a143e9007ea6750413bad0eac7d30d675fc/src/rlr_maintenance/autowake_adapter.py)
  preserves the original failure if maintenance is unavailable. The
  [LoopX boundary](https://github.com/hk20013106/RLR/blob/214b8a143e9007ea6750413bad0eac7d30d675fc/src/rlr_maintenance/loopx_cli.py)
  uses external JSON CLI calls. [Windows test jobs](https://github.com/hk20013106/RLR/actions/runs/32649860637)
  succeeded at PR #34's exact head; its continuous failure/repair/resume scenario
  remains an author-reported result. **Status: optional maintenance and auto-wake
  integration merged**. Current LoopX compatibility and sustained research use
  were not independently reproduced. Checked October 5, 2026.
- **LoopX Console** (xielixing) — a third-party BitFun MiniApp for GitHub issue
  repair. Independent [v3.9.29](https://github.com/xielixing/loopx-console/releases/tag/v3.9.29)
  was published August 18, 2026, with a MiniApp ZIP asset.
  The [checked main worker](https://github.com/xielixing/loopx-console/blob/dca7883ea5d4e2a0d48c0af5a1764c51a5023b53/source/worker.js)
  calls the local LoopX CLI and `quota should-run` using the `outer_controller`
  profile; its fallback source installation pins LoopX **v0.2.13**.
  The [same snapshot's README](https://github.com/xielixing/loopx-console/blob/dca7883ea5d4e2a0d48c0af5a1764c51a5023b53/README.md)
  assigns scheduling and execution to the MiniApp/BitFun host and describes
  human approval before PR publication. **Status: independently published,
  historical CLI integration observed**. Release metadata and current source
  are separate evidence; the ZIP, real host execution, current LoopX compatibility
  and sustained use were not independently tested. OpenBitFun upstream inclusion
  remains a separate proposal below. Checked October 5, 2026.
- **zyra** (BingruL) — the checked default branch is
  `fix/execution-timeouts-and-diagnostics`, pinned here to the September 19, 2026
  [source snapshot](https://github.com/BingruL/zyra/blob/3e20698e0182ec291cbdd87e7206fbae87706004/pyproject.toml).
  Packaging includes the embedded LoopX modules and CLI entry points. Its
  [source manifest](https://github.com/BingruL/zyra/blob/3e20698e0182ec291cbdd87e7206fbae87706004/packages/integrations/loopx_runtime/SOURCE-MANIFEST.json)
  pins **LoopX v0.2.13**; the listed source files match the upstream tag's tree.
  The [runtime resolver](https://github.com/BingruL/zyra/blob/3e20698e0182ec291cbdd87e7206fbae87706004/packages/integrations/zyra_integrations/loopx/runtime/resolver.py)
  requires the embedded source or a Zyra-owned installed distribution, without
  an archive fallback. **Status: pinned source and packaging integration observed**.
  No GitHub release or tag was listed at review; this does not establish whether
  a package was published elsewhere. Wheel installation, live Web/API execution,
  compatibility with current LoopX and sustained use were not independently
  verified. Checked October 5, 2026.
- **Hufu** (Blicae8917) — [PR #70](https://github.com/Blicae8917/hufu/pull/70),
  merged August 23, 2026, adds an opt-in LoopX v0.5.2 RunOnce Consumer.
  The checked main snapshot [exports the consumer and its ports](https://github.com/Blicae8917/hufu/blob/51adc0918c6e904ce904f2be93a4d1a76fcef502/src/hufu/index.ts);
  its [compatibility record](https://github.com/Blicae8917/hufu/blob/51adc0918c6e904ce904f2be93a4d1a76fcef502/docs/COMPATIBILITY.md)
  retains the exact v0.5.2 baseline and no bundled LoopX dependency.
  The deployment provider supplies real transport and Host invocation.
  [Issue #76](https://github.com/Blicae8917/hufu/issues/76) reports an owner-local
  pilot with committed/replayed RunOnce receipts, while general status projection
  remains incomplete; the issue is still open. **Status: bounded integration
  merged, local pilot reported, companion projection open**. That report was not
  independently reproduced and does not establish current-version compatibility
  or sustained deployment. Checked October 4, 2026.
- **benjamin-plugins** (Yidada) — [PR #1](https://github.com/Yidada/benjamin-plugins/pull/1),
  merged September 5, 2026, adds a Codex plugin calling the official LoopX kernel.
  The checked main [marketplace entry](https://github.com/Yidada/benjamin-plugins/blob/2baf35b4dcc64190e8012daf39054d83c46e6f22/.agents/plugins/marketplace.json)
  still registers the plugin. Its [source record](https://github.com/Yidada/benjamin-plugins/blob/2baf35b4dcc64190e8012daf39054d83c46e6f22/plugins/loopx/SOURCE.md)
  pins CLI qualification to a LoopX 0.5.4 checkout; the PR reports one independent
  model status scenario, with other scenarios structurally checked only.
  The [preflight implementation](https://github.com/Yidada/benjamin-plugins/blob/2baf35b4dcc64190e8012daf39054d83c46e6f22/plugins/loopx/skills/loopx/scripts/preflight.py)
  locates an executable and reads registry shape without executing LoopX;
  it explicitly leaves runtime and driver verification false.
  **Status: plugin merged, source-checkout validation reported**. PyPI installation,
  actual background execution and current-version compatibility were not verified;
  scheduling remains host-owned. Checked October 4, 2026.
- **Adaptive-Agent-Orchestration-Protocol** (YuemingHub) —
  [PR #41](https://github.com/YuemingHub/Adaptive-Agent-Orchestration-Protocol/pull/41),
  merged August 11, 2026, registers LoopX as an optional execution-continuity
  provider. The [pinned pilot report](https://github.com/YuemingHub/Adaptive-Agent-Orchestration-Protocol/blob/baa3f7805cc391ada34feb50707a2c1d3c151b54/docs/LOOPX_PILOT_EVIDENCE.md)
  reports Linux direct-CLI/custom-runner tests against LoopX v0.4.3: fresh-process
  recovery, validation and human gates, bounded handoff, accounting and rollback.
  The linked [consumer Actions run](https://github.com/YuemingHub/mingos-foundation/actions/runs/31465474613)
  succeeded at the recorded consumer head; this does not independently reproduce
  every reported assertion. The [August 14 follow-up](https://github.com/YuemingHub/Adaptive-Agent-Orchestration-Protocol/issues/42#issuecomment-5287847666)
  explicitly leaves LoopX unadopted, rather than promoting the pilot to ongoing
  adoption. AAOP is now archived; its [retirement record](https://github.com/YuemingHub/Adaptive-Agent-Orchestration-Protocol/blob/baa3f7805cc391ada34feb50707a2c1d3c151b54/RETIREMENT.md),
  effective September 25, 2026, retains frozen research and releases without
  ongoing compatibility or support promises. **Status: historical optional
  integration and bounded pilot in a retired project**. Current LoopX compatibility,
  Windows/WSL and production host/session restart behavior remain unqualified;
  AAOP's retirement is not a demonstrated failure of LoopX. Checked October 5, 2026.

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
  third-party LoopX Console. Neither the independent release nor the author's
  reported local checks establishes an upstream merge or release.
  Checked October 5, 2026.
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
- Scoped update: **2026-10-04–05**, rechecking OpenViking, NoKV, Hufu and
  benjamin-plugins against current public PR/issue states and pinned source files.
  AAOP's historical pilot/retirement and LoopX Console's independent release,
  historical CLI pin, zyra's default branch and embedded source pin, and the separate
  OpenBitFun proposal were reconciled on October 5. GoTry's dated workflow reports
  were separated from its open product acceptance. Meta-RLR's merged auto-wake
  successor and current source wiring were reconciled with its historical PRs.
  This distinguishes proposal packaging, historical tooling and pilots, plugin
  registration and current acceptance limits. No live integration was
  independently reproduced and other entries were not revalidated.
