# 从聚焦验证到 Pull Request

一个 Control-Plane PR 的价值，不由测试数量决定，而由证据是否覆盖了被修改的协议链决定。
只跑一个巨大 smoke 可能找不到语义错误；只写一个 unit test 又可能漏掉 projection、scheduler 或
writeback 的跨层漂移。

## 从一个坏的结局开始

考虑另一个教学情境：一处 authority 写入规则改动通过了单元测试与静态检查，PR 描述因此写成“已验证”。但受影响的 PostgreSQL 路径尚未在隔离实例上运行，durability、事务隔离和并发冲突的证据仍然缺失。

同样，修改默认行为后，测试可能已经按新行为通过，而测试名和说明还在描述旧默认。通过的断言证明了它检查的内容，不能替代未运行的后端验证或未披露的合同变更。

这两个问题都要求把“执行了什么检查”与“结论适用于哪里”分开记录。

本章继续上一章的 Gate scope 修复，组织一份外部贡献者可以公开提交的证据包：

```text
independent invariant
  -> focused deterministic proof
  -> real public path
  -> risk-based cross-surface checks
  -> public/private scan
  -> reviewable commits and PR
```

目标并非模仿维护者的本地自动化，而是让 reviewer 能根据协议、不变量和回执判断这项改动。

## 为什么"跑完全部命令"不够

自然的反应是：那就把所有能跑的都跑一遍。这条路在 LoopX 上走不通，原因有三个。

**全套验证跑不完。** PR 基线包含 lint、type、output budget smoke、分层的 `pytest`、canary 和
public/private scan；完整公开 smoke fleet 是另一回事。更慢的是真实路径：影响 PostgreSQL
authority 的改动需要一个一次性隔离实例，跑完整套件是分钟到十几分钟量级。每个 PR 都等最宽矩阵，
代价会摊到所有贡献者身上。

**跑得多不等于覆盖对。** 一百个命令如果都落在同一层，结论仍然只有一层。开头情境中的检查覆盖的是内存中的规则与静态约束，仍需补充受影响后端的证据。

**有些结论需要人工判断。** 自动检查可以提示默认行为变化是否有说明，但披露是否准确、证据是否覆盖真实影响，仍需要 review。

所以顺序应当是：先从风险推出需要哪些证据，再决定跑什么。

## 先建立证据矩阵

不要先运行仓库里所有命令。先把改动的风险列成矩阵：

| 风险 | 独立 Oracle | 最近验证 | 跨层验证 | 禁止结果 |
| --- | --- | --- | --- | --- |
| Missing scope 被误授权 | `decision_scope_v0` | decision table | source-to-quota replay | protected action runs |
| Missing scope 被猜成 global | explicit global scope invariant | negative test | agent frontier smoke | unrelated work freezes |
| Repair 被低层 flag 覆盖 | final interaction contract authority | precedence test | scheduler replay | host runs stale action |
| Repair 重试重复写入 | write correctness contract | idempotency test | interrupted writeback smoke | duplicate event/spend |
| 新字段膨胀热路径 | output contract | shape/budget check | actual CLI diff | agent loses next action |

如果一项验证不能对应风险，它可能只是惯例，而非本 PR 的证据。

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
协议，而非被测代码。

### 4. Risk-based canary

Canary 根据 Git diff 选择最小的跨 surface 检查：

```bash
loopx canary premerge --from-git-diff
```

它适合捕获"scope policy 修好了，但 scheduler、output budget 或另一个 consumer 漂移"的问题。
Canary 不能替代聚焦回归，因为它不一定精确命名本次错误。

### 5. Full-public smoke fleet

完整公开 smoke 适合 `main`、每日或显式手动运行：

```bash
loopx canary smoke-suite --suite full-public --jobs 4 --timeout-seconds 120
```

普通 PR 不应默认同步等待最宽矩阵。完整 fleet 的职责是广覆盖、inventory 与健康观察，无法替代
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
5. 最终断言是否检查 receipt，而不只检查"调用发生过"。

例如，文件创建或清理可能只是 test setup，不应自动计为 material progress。只有协议明确标记的
effect 和独立验证后的 postcondition，才允许形成 delivery receipt。

如果 fake 与真实 contract 不一致，先修测试基础设施，再判断产品是否回归。

## 真实路径验证：代价最高的一层

前面五层都可以在本机几十秒内跑完。这一层不行，而它恰好覆盖最容易出错的地方。

### 为什么 fake 不足以证明 authority 写入

In-memory fake 可以验证状态规则及调用顺序，但无法单独证明真实后端的 durable write、事务隔离、schema 失败和跨进程竞争。需要在对应后端复现这些边界，才能把规则测试的结论扩展到实际存储路径。

LoopX 对这类改动的门是明确的：重构交付前必须验证受影响的生产入口和真实后端。影响 PostgreSQL
authority 时，配置指向隔离临时实例的 `LOOPX_TEST_POSTGRES_URL`，运行
`npm run test:postgresql-authority-store`，并记录精确 commit、后端版本、验证行为与失败或局限。
`PostgreSQL Integration` workflow 会在临时实例上运行同一条 ladder row 与 service admission
套件，所以这条路径在 CI 与本地都被验证。

### 隔离是这条门的前提

测试必须使用一次性数据库或 tenant、独立 runtime 目录，输入为合成 fixture 或经 owner 授权的
只读快照。禁止连接共享或生产库：集成测试会创建角色并注入 schema 级失败 trigger，这是它发现
问题的机制，也正是它不能对着真实数据跑的原因。

同样禁止为测试晋升正在运行的 goal、切换 provider，或修改其 registry、writer fence、Todo 与
lease。发现并发源变更时只报告，不擅自覆盖或恢复。私有快照和原始输出不进 Git，也不进公开
review。测试结束后停止临时数据库。

### 环境不可用时的正确动作

如果没有安全的隔离实例可用，正确结果是**暂停交付并报告证据缺口**，跳过这一层继续则失去意义。

通过的测试为它实际执行过的输入、实现路径和断言提供证据。被跳过的真实后端测试则没有验证该后端。单元测试全绿，不能补上未运行的数据库集成验证。

所以 PR 中应分别列出 passed、failed 与 skipped，并说明每层覆盖什么。成功退出、测试数量和绿色汇总都不能扩大证据的适用范围。

写进 PR 的诚实版本是：这一层需要什么环境、当前为什么不可用、哪些结论因此仍然未经验证、
以及谁可以解除这个阻塞。

## 选择本地验证命令

LoopX 官方贡献基线（见[测试与质量指南](/loopx/docs/development/testing-and-quality/)）从
`uv sync --extra test` 开始，并在 checkout 内用所选解释器运行：

```bash
uv sync --extra test
uv run --extra test python -m ruff check tests loopx/canary loopx/control_plane loopx/domain_packs loopx/presentation
uv run --extra test python -m mypy
uv run --extra test python examples/control_plane/cli-output-budget-regression-smoke.py
uv run --extra test python -m pytest -q
git diff --check
```

若已有一个把当前 checkout 安装进去的显式环境，也可以在该环境里直接运行同样这组 `python -m ...`
命令；裸 `python` 可能指向另一个安装版本，先确认 `sys.executable` 与 `loopx --version`。

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

协议文档若改变 shipped behavior，还应运行对应 contract/smoke；"只改 Markdown"不代表行为风险为零。

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

## 默认行为变化必须披露

默认行为披露在下面的验证结果表里没有对应的命令可跑，也是 review 最容易漏掉的一类。

如果一个规则的默认行为发生了变化，下面三件事必须同时发生：

1. 重命名或改写编码旧默认的 smoke，让名字描述新契约；
2. 更新 docs 与 release note，写明变化前后的行为；
3. 在 PR 描述里点名受影响的 lane。

漏掉第 1 条的症状最隐蔽：测试仍然是红的会被人发现，而**名字在说谎的测试永远是绿的**。断言
已经跟着实现改了，名字和 release note 却还停在旧契约上，于是仓库里同时存在两句关于同一行为的
真话，其中一句是过期的。

披露还有一层要求容易和它混在一起：**"指导"与机器强制义务必须分开写。** 一个 `must_attempt_work`
这类字段是机器会拒绝的状态，而一句"建议先检查"没有这种效力。把后者写成前者会让人以为系统会拦，把前者
写成建议会让 reviewer 以为可以商量。两者都要在合同里显式标注，不能靠散文推断。

这一类没有自动检查，是因为它要求的是关于"当前默认是什么"的判断，而这正是实现本身已经改变
的东西。任何断言都只能读到新值。所以它只能落在 reviewer 身上，这也是它必须写进 PR 描述的原因：
reviewer 的输入是描述和 diff，他能发现的边界就是描述里提到的边界。

## 正确解释验证结果

验证并非 `passed: true/false` 两种状态：

| 结果 | 含义 | PR 中怎么写 |
| --- | --- | --- |
| `pass` | 当前检查满足 | 命令、scope 与结果 |
| `blocking_failure` | 已违反 invariant | 不提交为 ready；修复或缩小范围 |
| `infra_failure` | 环境未形成产品结论 | 记录 runner/provider 问题，不写"产品失败" |
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

这不属于 Git 形式主义。LoopX 仓库同时可能存在源码、公开 fixture、本地 runtime state 与生成证据，
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

按 reviewer 的判断任务拆分，而非按文件类型机械拆分。

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

而较差的一种是：

```text
update quota helpers
```

### 每个 commit 都要有 DCO sign-off

PR 分支上的每个 commit 都必须带一行 trailer：

```text
Signed-off-by: Your Name <your.email@example.com>
```

用 `git commit -s` 生成。漏掉时用 `git commit --amend -s`，多个 commit 则用 interactive rebase
补齐后更新分支；`Sign-off` 检查会拒绝未签名的 commit，`merge-gate` 与它共同构成 required check。
手工 merge commit 与网页端编辑同样需要签名，因为检查只豁免可验证的 GitHub 双父合并提交。

这条要求与测试无关，却和它同级：它认证的是"这个提交由谁做出、以什么许可提交"。一个通过了
全部验证但没有签名的分支，仍然进不了 `main`。

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

说明 public field、migration、retry、rollback、manual hold 或 release 影响。默认行为变化在此点名
受影响的 lane，并链接被重命名的 smoke。

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

Issue 是公开协作边界，不能把本地 Goal state 整体粘贴上去。

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

这些是 decision gate，并非测试缺口。验证无法替用户或 maintainer 授权。

## Review feedback 也是协议校验

收到 review 后，不要机械修改每条建议。先判断：

1. Reviewer 指出的是 invariant、实现、可读性还是 scope 问题？
2. 建议是否与当前协议和证据一致？
3. 修改会不会影响其他 consumer、migration 或 validation？
4. 是否需要补反例，而不只是改代码？
5. PR 描述和文档是否也要重组？

如果 review 暴露协议歧义，先达成语义共识；不要让两个相互矛盾的 test 同时"通过"。

### review 要执行，不只是阅读

上面的判断有一条执行层面的要求。对行为承载的改动，review 不能从标题推断等价性：reviewer 要
在不可变 baseline 上列出旧调用方的合法与非法分支，再用同一批合成输入、经同一 public entrypoint
在精确 head 上对照，覆盖成功与拒绝路径、诊断与修复提示、参数被省略或被清空的情形、持久化回读、
ownership 与 receipt。共享同一套新规则的多个 provider 互相一致，构不成前后兼容证据。

还要证明敏感性：聚焦用例必须在历史缺陷或人为 mutation 上失败、在修复后通过。缺少对照证据时，
不能批准一个以"行为不变"为由的改动。这里的证据必须可执行：baseline、精确 head 与敏感性用例各自
记录可重放命令或脚本调用、真实受影响后端、不可变 fixture 指纹、退出状态与归一化观测指纹。

需要说明的是，这条要求在实现上被投影成 review packet 里的一项检查，而 packet 测试只证明要求
被投影，并不证明 reviewer 真的执行了它，更不证明产品无缺陷。这正是它为什么必须和"默认行为
披露"归为同一类：**这类义务的最后一个执行者是 review，绝非 CI。**

## 两个真实社区贡献案例

本节与英文对应章节保持语义镜像；案例事实、结论、链接目标或风险边界的实质差异属于文档缺陷。

<!-- community-casebook:small-pr:start -->
<!-- community-casebook:small-pr-problem -->

### 案例一：把一个 CLI 不一致修成完整小 PR

[PR #3540](https://github.com/huangruiteng/loopx/pull/3540)
来自一次真实 first-run/deep-use 观察：`loopx --format json doctor` 可以工作，但更符合用户直觉的
`loopx doctor --format json` 当时会在 argparse 阶段失败（该写法在今天已经可用）。贡献者没有新建第二套 renderer，而是复用
现有 subcommand format contract。

<!-- community-casebook:small-pr-scope -->

最终改动只涉及 parser wiring、doctor handler 与一条端到端 CLI regression。验证同时覆盖：

- 新的子命令参数位置；
- 旧的全局参数位置；
- 二者同时出现时的 precedence；
- 非法格式在执行诊断前 fail closed。

<!-- community-casebook:small-pr-lesson -->

这个案例的价值不在"只改了几行"，而在于它形成了完整链条：

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

这个案例说明，review 并非在代码完成后"挑风格"。高价值 review 会提出一个能击穿当前 invariant
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

不能把前一状态写成后一状态。

## 代价与边界：这套方法放弃了什么

**代价一：最贵的那层最慢，而且无法缓存判断。** 单元测试给你秒级反馈，隔离实例上的真实后端
测试给你分钟级反馈，后者才是 authority 写入行为的唯一证据。想在开发循环里保持速度，唯一办法
是接受"本地绿"只是中间状态，算不上结论。

**代价二：一个测试同时服务两个目标时会失守。** smoke 既要断言行为、又要在名字里说明行为，
默认行为一变，两件事就分开了。代价是每次默认变更都要动到测试名、docs 与 release note，而
这些改动没有编译错误提醒你漏了哪一处。

**代价三：证据必须公开安全，这限制了 fixture 的形状。** 最有说服力的证据往往是真实运行状态，
而它不能进 Git。合成 fixture 更弱，但它可以被 reviewer 独立复现，这是它被接受的原因。

**边界一：完整套件属于 `main`、每日与手动通道，不属于每个 PR。** 这对贡献者是好消息，也意味着
PR 上没见过的最宽矩阵，确实可能在你合并之后才第一次运行。你的选择无关"要不要跑全套"，而在于
"有没有点名本改动最高风险的那一层"。

**边界二：`not_applicable` 是一种有效回答，前提是给出理由。** 本章的 Gate precedence 是确定性
规则，模型行为层就是 `not_applicable`。把每一层都跑一遍并不会让结论更强，只会让 PR 更慢。

**边界三：release、deploy 与 observed 是三种状态。** 合并、发布、部署到目标环境、在目标环境
被观察到，每一步都需要自己的证据。普通贡献者 PR 能提供代码级证据，不能声称生产已生效。

**边界四：这一章不替你认领任务。** 哪些 slice 可以被外部贡献者拿走，由 Contributor Task Board
和 Issue 决定；需要 private source、maintainer-owned live evidence 或 presentation review 的
改动，验证再多也仍然需要 owner 的决定。

## 本章检查表

打开 PR 前，确认：

- [ ] 每个风险都有独立 Oracle 和禁止结果；
- [ ] Unit、focused smoke、replay、canary 等层按风险选择，数量本身无意义；
- [ ] Characterization 没有被当成 correctness authority；
- [ ] Fake、fixture 与 snapshot 没有发明产品语义；
- [ ] 真实路径（如 PostgreSQL 集成）已在隔离实例上运行，或已把缺口写成阻塞项；
- [ ] 默认行为变化已重命名 smoke、更新 docs/release note，并在 PR 中点名受影响 lane；
- [ ] 每个 commit 都带 `Signed-off-by` trailer；
- [ ] 验证失败被正确分类，没有把 infra failure 写成产品结论；
- [ ] 所有变化路径已分类并通过显式 pathspec 暂存；
- [ ] `.loopx/`、`.loopx/goals/`、live state、凭据、私有链接、raw logs 和本机路径未提交；
- [ ] Commit 与 PR 都以协议结果组织，不以函数列表组织；
- [ ] Compatibility、recovery、未验证项和 owner gate 已明确；
- [ ] PR 关联公开 Issue/任务，且未复制 maintainer-owned work；
- [ ] "merged、released、deployed、observed" 没有混为一谈。

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
[Extension 的放置决策](./08-extension-placement.md)，不要把每项贡献都包装成 Extension。

## 不变式

读完这一章，你应该能带走五句可以自己检查的话。

1. **每一项验证都要对应一个具名风险。** 对应不上的命令属于惯例，构不成本 PR 的证据。
2. **期望值先于实现。** 从当前输出反推的断言只能做 characterization，不能作为 correctness
   authority。
3. **跳过不等于通过。** 拿不到隔离环境时，正确动作是报告证据缺口并暂停交付。
4. **默认行为变化有三件必做：重命名 smoke、更新 docs/release note、点名受影响 lane。**
   漏掉第一件的测试仍然是绿的，这正是它危险的原因。
5. **merged、released、deployed、observed 是四种状态。** 不能用前一种的证据宣称后一种。

这五条回答的是同一个问题：**当"我本地跑过了"既是贡献者最有力的说法、也是最容易误导别人的
说法时，reviewer 凭什么判断这项改动真的被证明过了？**
