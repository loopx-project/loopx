# 从聚焦验证到 Pull Request

一个 Control-Plane PR 的价值，不由测试数量决定，而由证据是否覆盖了被修改的协议链决定。
只跑一个巨大 smoke 可能找不到语义错误；只写一个 unit test 又可能漏掉 projection、scheduler 或
writeback 的跨层漂移。

本章继续上一章的 Gate scope 修复，组织一份外部贡献者可以公开提交的证据包：

```text
independent invariant
  -> focused deterministic proof
  -> real public path
  -> risk-based cross-surface checks
  -> public/private scan
  -> reviewable commits and PR
```

目标不是模仿维护者的本地自动化，而是让 reviewer 能根据协议、不变量和回执判断这项改动。

## 本章目标

读完后，你应该能：

- 从协议风险选择 unit、contract、smoke、replay、canary 与模型验证；
- 区分 semantic oracle、characterization、integration receipt 与 release evidence；
- 把失败分类为产品失败、基础设施失败、manual hold 或 deferred gap；
- 组织一个不混入本地状态、私有证据和无关重构的 Git/PR 交付；
- 用“改变了哪份合同、如何证明”说明 PR，而不是罗列修改过的函数；
- 知道哪些改动可以继续，哪些必须等待 maintainer 或 owner 决策。

## 先建立证据矩阵

不要先运行仓库里所有命令。先把改动的风险列成矩阵：

| 风险 | 独立 Oracle | 最近验证 | 跨层验证 | 禁止结果 |
| --- | --- | --- | --- | --- |
| Missing scope 被误授权 | `decision_scope_v0` | decision table | source-to-quota replay | protected action runs |
| Missing scope 被猜成 global | explicit global scope invariant | negative test | agent frontier smoke | unrelated work freezes |
| Repair 被低层 flag 覆盖 | final interaction contract authority | precedence test | scheduler replay | host runs stale action |
| Repair 重试重复写入 | write correctness contract | idempotency test | interrupted writeback smoke | duplicate event/spend |
| 新字段膨胀热路径 | output contract | shape/budget check | actual CLI diff | agent loses next action |

如果一项验证不能对应风险，它可能只是惯例，而不是本 PR 的证据。

## 六层质量证据

[Testing and Quality](https://github.com/huangruiteng/loopx/blob/main/docs/development/testing-and-quality.md)
定义了 LoopX 当前的质量分层。本书按外部贡献者的任务重新组织如下。

### 1. Unit 与 Contract

用于纯规则、schema、transition 和非法状态拒绝。

本案例应直接验证 decision table：

```text
matching scope -> operator gate
unrelated scope -> independent frontier
notice only -> authority remains unmet
ambiguous scope -> typed repair
explicit global scope -> global gate
```

这层最适合证明具体 invariant，但不证明 CLI、projection 与 scheduler 已正确串联。

### 2. Focused deterministic smoke

通过已交付入口验证一条真实路径，例如：

```text
public-safe source fixture
  -> real projection
  -> real quota decision
  -> interaction contract
```

Smoke 应薄而稳定。它保护已交付行为或历史回归，不应断言临时 builder 的每个字段，也不应包含
raw logs、真实项目状态或 dated research packet。

### 3. Public-safe decision replay

Replay 让 reviewer 看到：

```text
source facts
  + independently reviewed invariant
  -> expected decision and forbidden outcomes
```

然后用真实产品路径重新计算结果。

Replay 与 snapshot 不同。Snapshot 可能只是保存当前输出；Replay 的 expected outcome 必须来自
协议，而不是被测代码。

### 4. Risk-based canary

Canary 根据 Git diff 选择最小的跨 surface 检查：

```bash
loopx canary premerge --from-git-diff
```

它适合捕获“scope policy 修好了，但 scheduler、output budget 或另一个 consumer 漂移”的问题。
Canary 不能替代聚焦回归，因为它不一定精确命名本次错误。

### 5. Full-public smoke fleet

完整公开 smoke 适合 `main`、每日或显式手动运行：

```bash
loopx canary smoke-suite --suite full-public --jobs 4 --timeout-seconds 120
```

普通 PR 不应默认同步等待最宽矩阵。完整 fleet 的职责是广覆盖、inventory 与健康观察，不是替代
每个 PR 的语义设计。

### 6. Model behavior 与 Release qualification

只有确定性检查无法回答的问题才需要真实模型，例如：Agent 是否正确理解一份压缩后的默认
packet。

本章的 Gate precedence 是确定性规则，模型行为层通常是 `not_applicable`。让模型裁判 scope
coverage 既昂贵，又会弱化清晰合同。

Release qualification 还要绑定 exact commit、tree、version 与 clean state。普通贡献者 PR
可以提供代码级证据，但不能把它描述成 release 已通过或生产已生效。

## 先证明语义，再证明实现

验证顺序应保持：

```text
Is the intended rule correct?
  -> Does the pure implementation conform?
  -> Does the shipped path preserve it?
  -> Do adjacent surfaces remain compatible?
```

反过来的危险顺序是：

```text
run current code
  -> save output
  -> assert output never changes
```

后一种方式只能做 characterization。若当前输出与协议矛盾，刷新 golden 会把 bug 固化成合同。

### 一个独立 Oracle 应包含什么

至少写清：

- source facts；
- authority owner；
- allowed outcome；
- forbidden outcome；
- 不相关变化；
- freshness/revision 条件。

对 Gate scope 修复：

```text
Authority owner:
  valid decision-scope relation and its lifecycle writer

Allowed:
  typed repair before normal gated delivery

Forbidden:
  approval, implicit global block, or hidden gate

Irrelevant mutations:
  wording, unrelated agent gates, unrelated backlog size

Freshness:
  decision is recomputed from current source revision
```

这份 Oracle 可以先由 reviewer 审查，再落成测试。

## 测试反例，而不只是 happy path

Control Plane 的 bug 常来自组合。为每条规则至少设计：

### 正例

它在合法条件下确实触发。

### 抑制例

存在更高优先级 owner 或安全 frontier 时，它不触发。

### 非法状态

缺字段、冲突、重复、过期 revision 时 fail closed 或进入 repair。

### Metamorphic 例

改变无关输入，输出保持不变。例如：

```text
add unrelated gate
change user-facing prose
increase other-agent backlog
reorder projection rows
```

都不能让 ambiguous Gate 变成授权。

### Retry 与 interruption

在 prepare、host result、validation、writeback 或 spend 间中断，恢复后不能重复 effect 或记账。

这些维度比十份完整 JSON snapshot 更能保护协议。

## 测试替身必须服从真实合同

Fake Host、fake clock 或 in-memory store 可以降低测试成本，但不能创造新的产品语义。

检查 fake：

1. 默认参数是否与真实 adapter 一致；
2. observation、housekeeping 与 meaningful effect 是否分开；
3. denied、timeout、non-zero 与 malformed result 是否保持不同分支；
4. idempotency 与 proposal identity 是否被记录；
5. 最终断言是否检查 receipt，而不只检查“调用发生过”。

例如，文件创建或清理可能只是 test setup，不应自动计为 material progress。只有协议明确标记的
effect 和独立验证后的 postcondition，才允许形成 delivery receipt。

如果 fake 与真实 contract 不一致，先修测试基础设施，再判断产品是否回归。

## 选择本地验证命令

LoopX 官方贡献基线包括：

```bash
python -m pip install -e ".[test]"
python -m ruff check tests loopx/canary loopx/control_plane loopx/domain_packs loopx/presentation
python -m mypy
python examples/control_plane/cli-output-budget-regression-smoke.py
python -m pytest -q
git diff --check
```

但开发阶段应从最聚焦的命令开始：

```text
changed decision rule
  -> related unit/contract test
  -> one real-path focused smoke
  -> affected output/compile/lint check
  -> diff-selected canary
```

具体 smoke 名称由当前仓库、Issue 和 quality catalog 决定。本书不维护一份易漂移的命令全集。

### 文档与协议 PR

只改 public docs 时，通常至少需要：

```bash
git diff --check
loopx check --scan-path <changed-doc-or-directory>
```

协议文档若改变 shipped behavior，还应运行对应 contract/smoke；“只改 Markdown”不代表行为风险为零。

### Python 规则 PR

至少考虑：

- touched modules 的 lint/type/compile；
- pure decision table；
- focused public smoke；
- CLI output budget（若热路径受影响）；
- `loopx canary premerge --from-git-diff`；
- public/private scan。

### Host、writeback 与 scheduler PR

额外覆盖：

- fake Host/clock；
- interrupted phase replay；
- no-effect/no-spend path；
- idempotency 与 revision conflict；
- scheduler ACK 与 reset identity；
- capability/authority denied。

不要用一个成功 happy path 给外部副作用背书。

## 正确解释验证结果

验证不是 `passed: true/false` 两种状态：

| 结果 | 含义 | PR 中怎么写 |
| --- | --- | --- |
| `pass` | 当前检查满足 | 命令、scope 与结果 |
| `blocking_failure` | 已违反 invariant | 不提交为 ready；修复或缩小范围 |
| `infra_failure` | 环境未形成产品结论 | 记录 runner/provider 问题，不写“产品失败” |
| `manual_hold` | 自动证据不足，需要 owner | 明确问题与所需决定 |
| `advisory` | 风险信号，不构成当前阻断 | 说明为什么仍可继续 |
| `deferred_gap` | 有价值但尚无覆盖 | 写 owner/successor，不伪装 covered |
| `not_applicable` | 该层不适合本语义 | 给稳定理由 |

例如，真实模型服务不可用不能证明 Gate policy 错误；反过来，unit test 已通过也不能覆盖一个
确定性的 canary failure。

## 在 Git 中保持变更可审阅

开始非 trivial 贡献前：

1. 从最新默认分支创建干净分支或 worktree；
2. 确认工作树没有混入其他任务；
3. 只修改完成同一协议结果所需的文件；
4. 在 staging 前重新分类所有路径；
5. 通过明确 pathspec 暂存，不使用宽泛的 `git add .`。

推荐先检查：

```bash
git status --short --branch
git diff --stat
git diff --name-only
git ls-files --others --exclude-standard
```

这不是 Git 形式主义。LoopX 仓库同时可能存在源码、公开 fixture、本地 runtime state 与生成证据，
必须在提交前区分。

## 提交前的路径分类

把每个变化路径归入：

| 类别 | 示例 | 处理 |
| --- | --- | --- |
| Product code | protocol policy、writer、projection | 若属于本 PR，提交 |
| Public docs | protocol、contributor guide | 若解释当前行为，提交 |
| Durable validation | contract test、public-safe smoke | 若保护本规则，提交 |
| Local/private state | `.loopx/`、`.loopx/goals/`、live state | 不提交 |
| Generated/raw evidence | logs、transcript、verifier tail | 不提交 |
| Unrelated artifact | 其他实验或格式化 | 留在 PR 外 |

在 staging 前扫描候选路径是否包含：

- credential、token 或 secret；
- 本机绝对路径；
- private Issue、文档或内部链接；
- raw benchmark task、trajectory 或 verifier output；
- active Goal/Todo 的真实本地内容；
- 自动生成的大型日志与截图。

公开 fixture 只保留重现状态机所需的最小合成事实。

## 如何拆 commit

按 reviewer 的判断任务拆分，而不是按文件类型机械拆分。

一个小型规则修复可以是单个 cohesive commit：

```text
repair ambiguous decision-scope routing
  - policy correction
  - focused contract and replay
  - protocol clarification only if needed
```

如果包含行为保持的 mechanical move，通常先单独提交 characterization/move，再提交规则改变：

```text
commit 1: characterize existing protocol behavior
commit 2: move cohesive rule family without behavior change
commit 3: change rule and add negative/replay evidence
```

不要把 formatter、无关 rename、另一个 Extension 和本次 Gate 修复混在同一提交。

Commit message 应说明结果，例如：

```text
fix(control-plane): repair ambiguous decision scopes
```

而不是：

```text
update quota helpers
```

## PR 描述应是一份协议证据包

一份高质量 PR 可以按以下结构：

### Problem

描述 reader-visible 或 state-machine failure，不从文件名开始。

### Protocol and invariant

说明由哪份合同拥有语义，哪些结果必须允许或禁止。

### Change

说明 source、projection、decision、effect 或 writeback 中哪一层改变；明确未改变什么。

### Validation

按风险列出：

- unit/contract；
- focused smoke/replay；
- output/boundary；
- canary；
- 未运行或 not applicable 的层及原因。

### Compatibility and recovery

说明 public field、migration、retry、rollback、manual hold 或 release 影响。

### Public boundary

确认没有本地状态、私有证据、凭据、raw session 与本机路径。

Reviewer 应能从这份描述回答：

```text
What contract changed?
Why is the new decision correct?
Which consumers were checked?
What remains owner-held?
```

## 关联 Issue 与公开任务

非 trivial 工作优先关联
[Contributor Task Board](https://github.com/huangruiteng/loopx/blob/main/docs/development/contributor-tasks.md)
或 GitHub Issue：

- 在开始大改前声明准备处理的 slice；
- 保持 scope 接近已认领任务；
- 需要改变 public schema、scoring、permission、release 或 production behavior 时先获得设计/owner
  反馈；
- 卡住时公开具体 blocker 与已尝试验证；
- 不复制 `Maintainer-owned` live run。

Issue 是公开协作边界，不是把本地 Goal state 整体粘贴上去的地方。

## 哪些 PR 必须停下来等待

出现以下情况时，不应仅靠多跑测试继续：

- public JSON/schema 字段需要删除或改名；
- canonical state storage 或 migration 要改变；
- permission、production effect 或 credential boundary 要改变；
- benchmark scoring、task semantics、submission 或 leaderboard 行为要改变；
- 默认 agent-facing packet 的 authority 字段要移除；
- 需要 private source 或 maintainer-owned live evidence；
- 结果必须由 release/merge owner 决定；
- first screen、hero 或主要 CTA 需要 owner presentation review。

这些是 decision gate，不是测试缺口。验证无法替用户或 maintainer 授权。

## Review feedback 也是协议校验

收到 review 后，不要机械修改每条建议。先判断：

1. Reviewer 指出的是 invariant、实现、可读性还是 scope 问题？
2. 建议是否与当前协议和证据一致？
3. 修改会不会影响其他 consumer、migration 或 validation？
4. 是否需要补反例，而不只是改代码？
5. PR 描述和文档是否也要重组？

如果 review 暴露协议歧义，先达成语义共识；不要让两个相互矛盾的 test 同时“通过”。

## 两个真实社区贡献案例

本节与英文对应章节保持语义镜像；案例事实、结论、链接目标或风险边界的实质差异属于文档缺陷。

<!-- community-casebook:small-pr:start -->
<!-- community-casebook:small-pr-problem -->

### 案例一：把一个 CLI 不一致修成完整小 PR

[PR #3540](https://github.com/huangruiteng/loopx/pull/3540)
来自一次真实 first-run/deep-use 观察：`loopx --format json doctor` 可以工作，但更符合用户直觉的
`loopx doctor --format json` 会在 argparse 阶段失败。贡献者没有新建第二套 renderer，而是复用
现有 subcommand format contract。

<!-- community-casebook:small-pr-scope -->

最终改动只涉及 parser wiring、doctor handler 与一条端到端 CLI regression。验证同时覆盖：

- 新的子命令参数位置；
- 旧的全局参数位置；
- 二者同时出现时的 precedence；
- 非法格式在执行诊断前 fail closed。

<!-- community-casebook:small-pr-lesson -->

这个案例的价值不在“只改了几行”，而在于它形成了完整链条：

```text
真实用户摩擦
  -> 已有公共合同
  -> 正确 owner
  -> 正向、兼容与负向验证
  -> reviewer 可独立判断
```

小 PR 不等于低标准。它只是把同一个用户结果控制在更容易验证和回滚的范围内。
<!-- community-casebook:small-pr:end -->

<!-- community-casebook:review-repair:start -->
<!-- community-casebook:review-repair-problem -->

### 案例二：用反例评审高风险状态写入

[PR #3529](https://github.com/huangruiteng/loopx/pull/3529)
把 shared-goal coordination 的 aggregate head、file provider 和 `claim_work` executor 做成一个
provider-neutral Stage 2 切片。第一版已有大量正向测试，但 reviewer 仍构造了更强的非法状态：
partial write 被误报为 `applied`、损坏 receipt 导致未分类异常、超大 TTL 越过 typed boundary、
Windows 无法导入锁实现，以及无时区时间和 bool-as-int 进入持久状态。

<!-- community-casebook:review-repair-response -->

作者没有给这些输入逐个加字符串特判，而是修复它们共同指向的 trust boundary：

- durable write 必须 write-all、fsync、atomic replace，并在不确定时返回 `ambiguous`；
- persisted receipt、timestamp 与 generation 在进入 executor 前完成 typed validation；
- lock 复用仓库已有跨平台 owner；
- RFC、负例测试和 CI 同步记录 machine-enforced obligation。

<!-- community-casebook:review-repair-lesson -->

这个案例说明，review 不是在代码完成后“挑风格”。高价值 review 会提出一个能击穿当前 invariant
的具体 counterexample，并要求修复落在真正的 owner；作者则用新的负例证明同类非法状态不能再次
进入 semantic core。最终 `APPROVE` 只适用于重新验证后的 exact head。
<!-- community-casebook:review-repair:end -->

## Merge 后还要验证什么

PR 合并不自动证明部署、release 或所有外部 Host 已更新。根据改动类型，后续可能需要：

- main 上的 full-public smoke；
- release qualification；
- packaged install check；
- Host/plugin compatibility；
- documentation site deployment；
- fresh external readback。

在 PR 或发布说明中区分：

```text
merged
released
deployed
observed in target environment
```

不要把前一状态写成后一状态。

## 本章检查表

打开 PR 前，确认：

- [ ] 每个风险都有独立 Oracle 和禁止结果；
- [ ] Unit、focused smoke、replay、canary 等层按风险选择，而不是越多越好；
- [ ] Characterization 没有被当成 correctness authority；
- [ ] Fake、fixture 与 snapshot 没有发明产品语义；
- [ ] 验证失败被正确分类，没有把 infra failure 写成产品结论；
- [ ] 所有变化路径已分类并通过显式 pathspec 暂存；
- [ ] `.loopx/`、`.loopx/goals/`、live state、凭据、私有链接、raw logs 和本机路径未提交；
- [ ] Commit 与 PR 都以协议结果组织，不以函数列表组织；
- [ ] Compatibility、recovery、未验证项和 owner gate 已明确；
- [ ] PR 关联公开 Issue/任务，且未复制 maintainer-owned work；
- [ ] “merged、released、deployed、observed” 没有混为一谈。

需要为不同风险 surface 选择 deterministic test、decision replay、canary、模型行为验证或
release gate 时，继续阅读
[Control-Plane Course 第 10 讲](/loopx/docs/development/control-plane-course/10-autonomous-agent-quality-gates/)。
课程提供组合风险 case；本章保留从本地证据到公开 PR 的交付主线。

至此，你完成了开发者贡献中的一条 Control-Plane 路径：从贡献地图选择 owner，沿协议链定位
实现，修改一条规则，再以独立证据交付 PR。Capability、Provider、Host/Runner、
Projection/Docs/fixtures 与 Extension 等贡献面会有不同的最近验证，但复用同一原则：先确认
协议与权限 owner，再让证据覆盖真实交付边界。

你不需要记住 LoopX 当前所有函数；你需要能说明事实从哪里来、谁有权改变、哪条不变量保护
决策，以及什么回执足以让下一轮继续。若贡献需要独立版本、可选安装或独立生命周期，再进入
[Extension 的放置决策](./08-extension-placement.md)，而不是把所有贡献都包装成 Extension。
