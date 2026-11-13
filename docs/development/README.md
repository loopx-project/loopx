# LoopX Developer Guide / LoopX 开发者指南

This directory is the stable entry point for contributors changing LoopX
runtime behavior, public contracts, tests, or release gates. Product users do
not need these documents to start LoopX.

本目录是修改 LoopX 运行时、公开合同、测试和发布门禁时的稳定入口。普通产品
用户接入 LoopX 时不需要先阅读或配置这些开发者能力。

## Start Here / 从这里开始

1. Read [Contributing](https://github.com/huangruiteng/loopx/blob/main/CONTRIBUTING.md) for repository boundaries and the
   pull-request checklist.
2. Read [Current technical directions](../project/technical-directions.md) to
   choose an active program, understand its maturity, and find its tracker.
3. Follow the [Developer Book](/loopx/docs/book/) for the curated external
   learning path from control-plane foundations to project onboarding and
   developer contributions.
4. Read the [LoopX project code tour](project-code-tour.zh-CN.md) for a Chinese,
   code-linked overview of the architecture and one complete execution path.
5. Follow the [control-plane developer course](control-plane-course/README.md)
   for an eleven-lecture, code-led path through the real CLI, state machine, and
   layered quality gates.
6. Read [Testing and quality](testing-and-quality.md) before changing agent-facing
   output, scheduler decisions, todo/gate semantics, onboarding, or release
   promotion.
7. Use [What counts as a good smoke](good-smokes.md) before adding, retaining,
   or consolidating a public smoke.
8. Use [Architecture](../architecture.md) and the
   [core control-plane graphs](../product/core-control-plane/README.md) to find
   the bounded context that owns the behavior.
9. Check [Public/private boundaries](../public-private-boundary.md) before adding
   fixtures, examples, evidence, or provider-backed evaluation.
10. Follow the [documentation layout policy](documentation-layout.md) before
   adding or moving public documentation.

1. 先阅读[贡献指南](https://github.com/huangruiteng/loopx/blob/main/CONTRIBUTING.md)，了解仓库边界和 PR 检查项。
2. 阅读[当前技术方向](../project/technical-directions.zh-CN.md)，选择活跃计划、了解
   成熟度并找到对应 tracker。
3. 按顺序阅读[开发者手册](/loopx/docs/book/)，从控制面基础到项目接入和开发者贡献。
4. 先阅读[LoopX 项目代码导读](project-code-tour.zh-CN.md)，通过源码链接了解架构和
   一次完整执行链路。
5. 按顺序学习[控制面开发者 11 讲](control-plane-course/README.md)，沿真实 CLI、
   状态机、核心函数和分层质量门禁建立代码心智模型。
6. 修改 agent-facing 输出、调度决策、todo/gate 语义、新用户接入或发布流程前，
   阅读[测试与质量体系](testing-and-quality.md)。
7. 新增、保留或合并公开 smoke 前，阅读
   [什么是好的 Smoke](good-smokes.md)。
8. 通过[架构文档](../architecture.md)和
   [控制面核心图](../product/core-control-plane/README.md)定位真正拥有该行为的
   bounded context。
9. 添加 fixture、示例、证据或模型测试前，检查
   [公开/私有边界](../public-private-boundary.md)。
10. 新增或移动公开文档前，遵循
   [文档布局规则](documentation-layout.md)。

## Core References / 核心参考

| Area / 领域 | Reference / 文档 |
| --- | --- |
| Current strategic programs / 当前战略方向 | [Technical directions](../project/technical-directions.md) · [中文](../project/technical-directions.zh-CN.md) |
| Curated learning path / 开发者学习路径 | [Developer Book](/loopx/docs/book/) |
| Project architecture tour / 项目架构导读 | [LoopX project code tour](project-code-tour.zh-CN.md) |
| Control-plane code reading / 控制面代码领读 | [Eleven-lecture developer course](control-plane-course/README.md) |
| Quality layers and commands / 质量分层与命令 | [Testing and quality](testing-and-quality.md) |
| Durable smoke design and cleanup / 稳定 Smoke 的设计与清理 | [What counts as a good smoke](good-smokes.md) |
| Agent-facing size budgets / Agent 输出体积预算 | [Interface budget contract](../reference/contracts/interface-budget-contract.md) |
| Status and decision payloads / 状态与决策载荷 | [Status data contract](../status-data-contract.md) |
| Quota and spend semantics / Quota 与 spend 语义 | [Quota allocation](../quota-allocation.md) |
| Model-behavior shadow qualification / 模型行为影子验证 | [Model behavior qualification v0](../reference/protocols/model-behavior-qualification-v0.md) |
| Release promotion / 发布晋级 | [Release readiness](../product/release-readiness.md) |
| Benchmark research / Benchmark 研究 | [Benchmark workspace](https://github.com/huangruiteng/loopx/blob/main/benchmark/README.md) · [Research RFC](../architecture/rfcs/long-horizon-harness-benchmark-research-program-v0.md) |

## Change Loop / 变更闭环

Keep the accepted outcome and its observable boundary as the source of truth.
Characterize the current behavior, deliver a complete outcome slice in its owning
boundary, then choose validation by risk. The slice can reshape internal modules;
its size follows the outcome, not a preference for small diffs. Do not create a
second product path solely for a test.

以已接受的目标和可观察边界为准：先刻画现状，在行为所属边界交付一个完整成果切片，
再按风险选择验证层。切片可以重塑内部模块，范围由成果决定，不以小 diff 为目标。
不要为了测试而维护第二套产品路径。

The usual loop is:

```text
issue or regression
  -> deterministic characterization
  -> focused implementation
  -> focused tests and durable smoke
  -> catalog-selected canary
  -> owner review when behavior is sensitive
  -> release outcome observation when needed
```

通常闭环为：问题或回归 -> 确定性刻画 -> 聚焦实现 -> 单测与 durable smoke ->
catalog 选择的 canary -> 敏感变更 owner review -> 必要时观察发布结果。

## Engineering Scope And Refactoring

Before adding a module, protocol field, CLI option, fixture or abstraction,
apply the scope-fit review below. During development and again before approval
or merge, apply the bounded refactoring pass. These are engineering decisions
within the accepted task, not additional user-approval gates.

### Structural Convergence / 结构融合

Hold the task's input and accepted outcome stable while improving the structure
between them. The boundary includes authority, side effects, failure/recovery,
latency and complete cost, not only matching input/output JSON. Corrected user
requirements can change that boundary explicitly; an implementation's current
shape is not the specification.

固定当前任务的输入与验收结果，持续改进中间结构。边界包含权限、副作用、失败恢复、
时延和完整成本，不只是输入输出 JSON 相同。用户修正需求时显式更新边界，不能把现有
实现形状当成规范。

Start with the smallest extensible capability that serves a real caller. New
structures are useful experiments when they expose a demonstrated gap. After
learning from them, absorb their valid behavior into the owning structure and
retire redundant entrypoints, writers, state and branches. Keep failed approaches
as bounded evidence or independent regression oracles, not permanently active
fallback implementations. Convergence is complete when the next change to a
shared rule has one owner and affected callers use it; a wrapper, moved files,
fewer lines or more modules does not establish that outcome. Independently
evolving rules remain separate modules rather than one universal orchestrator.
Keep exploratory interfaces internal or explicitly experimental until repeated
real caller use justifies a stable public contract; do not promise compatibility
for every intermediate abstraction merely because it appeared in a prototype.

从能服务真实调用方的最小可扩展能力出发。新增结构可以用于暴露和解决已证明的缺口；
获得证据后，把有效行为吸收到所属结构，退役重复入口、writer、状态和分支。失败方案
保留为有界证据或独立回归判据，不永久保留成运行中的 fallback。下一次修改共同规则时
只有一个 owner，相关调用方也实际采用它，才算完成融合；包一层、移文件、减行数或
拆模块均不能单独证明。不同原因驱动的规则仍分模块演进，不汇成万能编排器。
探索中的接口留在内部或明确标为实验，经过真实调用方反复使用后再稳定公开；不能因为
某个中间抽象曾出现在原型里，就永久承担兼容成本。

For an authorized convergence change, reuse the delivery brief and existing
`repository_reuse.rule_ownership`, `observable_semantics` and validation evidence:

- State the fixed start/end behavior and the gap that justifies restructuring.
- Map each affected entry, rule, writer and recovery path to its final owner;
  name what disappears and any real compatibility consumer that remains.
- Change internal APIs decisively when needed, migrate active callers together,
  and validate success, failure, cancellation and pending-state recovery through
  the real entry. Public contracts and durable state need an explicit migration;
  internal breaking changes do not authorize silently breaking deployed users.
- Compare outcomes and complete costs at the same boundary. Preserve negative
  results; improve the design rather than weakening acceptance to fit it.
- Close the slice by removing superseded normal producers. If a staged cutover
  is needed, retain one existing checkpoint with the caller, exit condition and
  next integration step; temporary coexistence is not the target architecture.

在已授权的融合任务中，复用交付简报和现有 review 证据：明确固定的起终点与缺口；
逐项映射入口、规则、writer、恢复路径及退役项；允许必要的内部 breaking change，
同步迁移调用方并实测成功、失败、取消和在途恢复；同边界比较成果与完整成本；完成后
删除被替代的正常 producer。对外契约和持久状态另有明确迁移，不能无声破坏已部署用户。
需要分阶段时，在既有 checkpoint 写清调用方、退出条件和下一接入步骤，不把长期并存
作为终态。无需新增表单、状态词汇或逐 PR 的仪式性重构指标。

### Bounded Future-Facing Refactoring

During development and again before approving or merging each PR, explicitly
ask whether the touched behavior or its adjacent owning boundary has a small,
related, behavior-preserving refactor that would make the next likely product
or control-plane change easier. Prefer removing duplicate authority,
strengthening typed contracts, narrowing module ownership, and retiring
obsolete compatibility seams over adding speculative frameworks. For
control-plane work, keep state-machine and effect authority in the typed
TypeScript boundary when that is the established owner; Python may adapt or
bridge that contract, but must not silently recreate a second source of truth.

Apply the refactor in the current PR when it shares the same domain or change
reason, remains locally reviewable and reversible, and is covered by
characterization or parity validation; it need not be strictly required for
the immediate fix. Do not use this principle to justify broad migration or
unrelated cleanup. When the valuable related refactor is larger than a bounded
companion change, record a focused follow-up instead. When restructuring itself
is the accepted task, apply the structural convergence rule above and deliver a
complete owner/caller migration; this companion-refactor limit is not a reason
to stop at an adapter or repeatedly defer the requested integration. PR review and merge notes
should state whether this future-facing pass was applied, deferred, or found
unnecessary, with the concrete boundary considered.

### Scope-Fit Review

Before adding a new module, builder, protocol field, CLI option, fixture, smoke
section, or abstraction, pass a scope-fit review:

- Identify the shipped behavior, active call site, or explicit compatibility
  contract that needs it. If the value is only an uncommitted future runner, a
  design note, or a hypothetical extension with no validation contract, keep the
  design in docs or todo state until the real call site appears.
- Prefer a cohesive behavior-preserving seam. For example, let the ledger first
  recognize one compact public-safe row shape before adding a dedicated
  benchmark-specific builder, arm constants, or wide field-level smoke. Do not
  split so narrowly that reviewers must reconstruct one logical behavior from
  several dependent PRs.
- Design tests from semantics, not observed output. Independently review the
  intended invariant and legal or illegal transitions before testing the
  implementation. Never derive expected results from the implementation under
  test or its current output; characterization fixtures are non-authoritative,
  and contradictions require rule repair plus negative or mutation coverage.
- Characterize before moving code. For status, quota, review-packet, scheduler,
  monitor, and handoff behavior, add or extend parity fixtures first, then
  extract the proven rule or cohesive rule group.
- Reuse existing repository patterns and bounded contexts. Add code where its
  change reason belongs, such as `control_plane/runtime`, `control_plane/quota`,
  or `control_plane/todos`; do not create generic sink directories or helper
  layers just because several files share a similar shape.
- Treat large or hot files as warning signals. When a change would grow an
  already oversized module, first look for a narrow read model, domain helper,
  or bounded-context home. For internal module moves, update active call sites
  and delete the old entry point; leave a compatibility wrapper only when a
  real external import, persisted state, CLI/API contract, or migration window
  requires it.
- Distinguish duplicate knowledge from duplicate-looking code. Collapse shared
  state rules, protocol semantics, serialization contracts, and lifecycle
  invariants; avoid a parameter-heavy abstraction when two callers merely look
  similar but will evolve for different reasons.
- Keep smokes thin and durable. They should prove shipped behavior, boundary
  enforcement, and regression contracts, not every incidental field produced by
  a temporary builder. Large smokes are a prompt to move reusable logic into
  product modules or to narrow the assertion surface.
- Make illegal states hard to express. For status, quota, scheduler, monitor,
  todo, and handoff flows, prefer explicit enums, schemas, and transition
  helpers over scattered booleans and prose-only assumptions.
- State-classification and delivery-semantics rules belong in typed enums,
  schemas, or transition helpers. Substring denylists and prose heuristics must
  carry a documented reason and a typed follow-up; PR review must flag them
  with the concrete false-positive/negative risk.
- Core control-plane obligations and error text must stay domain-neutral. Do
  not put product- or benchmark-specific wording (for example "product
  advancement") into generic work-lane, quota, todo, or settlement contracts;
  prefer goal-agnostic phrasing.
- Default behavior changes must be disclosed: rename the smoke that encoded the
  old default, update docs/release notes, and name the affected lanes.
  "Guidance" versus machine-enforced obligations (such as `must_attempt_work`)
  must be explicit in the contract, not inferred from prose.
- Fail fast with actionable context at input, config, permission, and state
  boundaries, but do not replace clear control flow with broad exception
  plumbing or silent fallback.
- Ship right-sized, reversible batches. A PR should be theme-unified, locally
  validated, and reviewable as a complete stage package. A few hundred to
  roughly one or two thousand lines can be appropriate when the diff is cohesive
  and avoids hidden future scaffolding; a 30-line PR can still be too small if it
  leaves behavior split across follow-up PRs. Separate characterization/parity
  fixtures, mechanical moves, behavior changes, and cleanup when that makes
  review and rollback clearer.
- Keep public PRs concise and current-purpose focused. Future extension points
  are allowed when they reduce near-term churn, preserve compatibility, or
  define a real contract that is documented and tested. Do not bundle
  private/local experiment scaffolding, diagnostic run dumps, unused speculative
  plumbing, or long background narratives with the code path needed by the
  current behavior.
- Compress rather than append. For docs, fixtures, dashboards, and examples,
  replace or retire stale material when adding new current truth; do not let
  canonical surfaces accumulate multiple versions of the same conclusion.

Use this checklist to delete, defer, or right-size code as actively as you add
it. A PR that removes an unused abstraction, narrows a smoke to the real
contract, or moves a rule into the right bounded context is often more valuable
than one that adds a larger framework around the same behavior.

## Agent Instruction Loading And Maintenance

The repository shares one policy entry: `AGENTS.md` for Codex and the explicit
`@AGENTS.md` import in `CLAUDE.md` for Claude Code. Keep the import single-source;
a prose request to read the file does not ensure startup loading. Do not eagerly
import the detailed procedures: read their linked owner when its task applies.

Codex's default `project_doc_max_bytes` is 32 KiB across discovered instructions,
and Antigravity caps individual rule files (`AGENTS.md`) at 24,000 bytes (24 KB).
The root regression checks that `AGENTS.md` fits within the stricter 24,000-byte
per-file host cap as well as the 32 KiB discovery budget, while ancestor
instructions, nested files and user overrides still require a host-specific
loading check. Claude's import/discovery settings have different semantics;
neither cap is a universal Claude limit. Inspect actual startup context after
changing the entry or its alias, and distinguish assembled text from model
compliance and live effects. Existing sessions may retain an older copy.

Run `uv run --extra test python -m pytest -q tests/test_repository_instruction_delivery.py`
for root size, shared import, local routes and existing inbound anchors. Review
a moved obligation clause by clause: owner, modality, scope, order, exceptions,
evidence provenance, continuation and stop conditions. A link test or shorter
file does not establish equivalence. Keep operational rules in their existing
owners rather than adding another parallel policy collection.

Design sources and the disposition of the earlier proposal are recorded in
[the instruction/skill/prompt study](../research/2026-10-17-agent-instruction-skill-prompt-guidance.md).


### Progressive Context For Development

The default working context should answer the current decision: the accepted goal,
applicable constraints and stop conditions, current work, acceptance criteria,
relevant changes and unresolved gaps. Keep it sufficient to act correctly. Preserve
complete source material durably without equating storage completeness with a
requirement to load every historical record into every model input.

Read known paths or record IDs directly, batching independent required reads
within the combined output budget. When a location is unknown, use the existing
index, heading tree or bounded search to discover the relevant parent, then read
the sections or records needed for the actual decision. Keep dependent reads and
mutations ordered; reuse an applicable captured response instead of rerunning its
producer, and refresh authoritative facts after relevant state changes.
A summary must not silently replace a requirement or conceal an exception. Retain
source identity, revision and a reachable detail route; expose omissions and unknown
coverage. Ownership alone does not establish irrelevance: shared constraints,
cross-work dependencies and contrary or unresolved evidence require expansion
before a dependent decision. Reuse an existing typed scope owner instead of adding
keyword classifiers or another relevance engine.

Keep file availability separate from model retention. Saving a complete capture
permits exact file comparison, not a claim that the model read it. In native
context delivery, a new compaction epoch starts with a full saved baseline on
the next work Turn; ordinary resume and selective reads preserve the epoch.
Recovery continues the original Turn and reads missing necessary facts, without
replaying admission or draining an entire packet to enable later deltas.

Separate **acceptance criteria** from **acceptance evidence**. Criteria and current
unmet/unknown status guide execution; historical reviewer notes, full provenance,
logs and detailed evidence support a specific verification, dispute, replan or
closeout. Keep relevant failure/staleness signals visible so the agent knows when
to inspect that material. Before claiming acceptance, inspect the evidence the
claim actually needs; an index or prior agent declaration is not verification.

Apply the same rule to repository instructions and skills: keep the durable core
in the entry file and link detailed procedures with clear triggers. Discover the
needed reference before opening it; do not eagerly import the whole catalog or
all linked files. When guidance moves, preserve its actor, ordering, modality,
scope, exceptions and stopping conditions at the point where it is required.
Existing sessions may retain old instructions; source edits alone do not prove
native startup adoption or agent compliance.

For product context delivery, selection, continuation and replanning have different
information needs. A suggested `selected_todo` is not proof of an actual work choice.
A qualified continuation can retain the bound work in full and make other candidates
discoverable with exact details; a new choice must inspect what that choice needs.
Do not implement this by filtering a full-required response in the caller. Change
the typed scope, required-read instructions, detail reader, actual input and recovery
binding together. Undelivered details must not enter a retained-context baseline.
Recover the original decision's reference after loss rather than rerunning admission
to obtain a different decision. Until that joint change is delivered, the existing
runtime contract still governs its captures.

Measure a useful task segment, including discovery, detail expansion, history replay,
compaction/recovery and subsequent validated work. A smaller first page can increase
total cost; successful pagination can still prevent the task from advancing. Use
[output-budget and semantic comparisons](testing-and-quality.md#progressive-context-qualification)
with cold, warm, changed-requirement and context-loss cases. Preserve negative results
and unknown model cost/adoption rather than inferring benefit from fewer bytes.

渐进读取的默认上下文应足够支持当前决定：目标、适用约束和停止条件、当前工作、
验收标准、相关变化及未解决缺口。完整材料持久保存，不等于每轮都必须全量输入模型。
先用既有目录、标题树、检索或精确记录入口定位，再展开当前判断需要的正文；摘要
不能替代要求或隐藏例外。保留来源、版本、详情入口及遗漏/未知标记；跨工作依赖、
共享约束和反面证据不能仅因属于其他 Agent 而排除。

区分验收标准和验收材料：标准与当前未满足/未知状态指导执行；历史评审、来源、
日志和完整证据在核验、争议、重规划、收口等需要它们的环节展开。默认仍显示相关
失败或过期信号；验收声明必须读取对应证据，索引和旧 Agent 结论不代替核验。
入口指引保留核心义务，详细流程以明确触发条件链接，不急切加载全部文档/Skill。
移动指导须保留主体、顺序、义务强度、范围、例外和停止条件；源文件更新不证明
当前宿主已加载，也不证明模型实际遵从。

产品侧按选择、继续执行、重规划区分需要的信息；`selected_todo` 不证明模型已选择。
合格的已绑定任务接续可保留当前工作全文和其他候选的可发现入口，但不能在仍要求
全读的接口外手动裁剪。范围 owner、必读指引、详情接口、实际输入及恢复绑定应一起
替换；未交付正文不能进入保留基线，丢失上下文须恢复原决定而非重新准入。
联合替换前，旧捕获仍遵守原合同。对比整段工作及补读、历史回放、压缩/恢复成本和
后续验收成果，保留失败与未测，不把分页成功或首屏变短当成长程收益。
