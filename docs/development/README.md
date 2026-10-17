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

Keep one shipped behavior as the source of truth. Characterize it first, make a
small change in its owning module, then choose validation by risk. Do not create
a second product path solely for a test.

始终只保留一套真实交付行为作为 source of truth：先刻画现状，在行为所属模块中做
小变更，再按风险选择验证层。不要为了测试而维护第二套产品路径。

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
companion change, record a focused follow-up instead. PR review and merge notes
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

Codex's default `project_doc_max_bytes` is 32 KiB across discovered instructions.
The root regression checks that this file fits, but ancestor instructions, nested
files and user overrides still require a host-specific loading check. Claude's
import/discovery settings have different semantics; 32 KiB is not a universal
Claude limit. Inspect actual startup context after changing the entry or its
alias, and distinguish assembled text from model compliance and live effects.
Existing sessions may retain an older copy.

Run `uv run --extra test python -m pytest -q tests/test_repository_instruction_delivery.py`
for root size, shared import, local routes and existing inbound anchors. Review
a moved obligation clause by clause: owner, modality, scope, order, exceptions,
evidence provenance, continuation and stop conditions. A link test or shorter
file does not establish equivalence. Keep operational rules in their existing
owners rather than adding another parallel policy collection.

Design sources and the disposition of the earlier proposal are recorded in
[the instruction/skill/prompt study](../research/2026-10-17-agent-instruction-skill-prompt-guidance.md).
