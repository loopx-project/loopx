# GitHub 仓库维护与运营自动化最佳实践（以 LoopX 为例）

> [English](github-maintenance-ops-best-practices.md)

LoopX 用 LoopX 维护自己的开源仓库。这份文档记录实践中沉淀的运营模式与
最佳实践，并做了泛化，其他开源维护者可以直接复用。

## 1. 核心判断

仓库维护与运营是长程、可中断、高上下文的工作：triage、review、发布、安全
响应、生态运营和内容，都不是一次 prompt 就能做完的。这正好是 LoopX 为
长程 Agent 解决的问题形态，所以用 LoopX 运营仓库既是 dogfooding，也是
最佳实践的来源。

实践中沉淀出三种运营模式：

1. 维护循环：负责 PR review 纪律、发布与安全、triage、基础设施修复；
2. 生态与价值循环：观察采纳情况、评估 fork 与衍生项目、邀请上游贡献，
   并把公开证据变成文档与社媒内容；
3. issue→PR bot：把一条公开 issue 持续推进到 focused fix、证据、reviewer
   路由与 merge 观察。

## 2. 三种运营模式

### 模式 A：维护循环

维护循环运行在 heartbeat automation 之下，有自己的 goal、todo、quota 和
monitor 契约，承担维护者工作中重复但需要判断的部分：

- PR review 走显式 skill 与 exact-head 证据门禁，而不是凭记忆临时评审；
- issue/PR triage 用类型化分类，进入的工单被打 tag 并路由；
- 发布与安全响应（含协调披露）有记录的决策边界；
- 基础设施修复（比如修一个不更新的 star-history 服务、按 hint 更新自动化
  调度）由小的可复用工具完成：读 hint → 备份状态 → 应用变更 → ACK。

公开证据：v0.4.5 至 v0.4.7 的 release、PR review skill 的持续精化、仓库
历史中记录的评审与发布纪律。

### 模式 B：生态与价值循环

生态循环把仓库当产品来观察、评估和投喂：

- 每周扫描把提及分为真实集成、采样借鉴、衍生周边与覆盖信号四类；
- 每次扫描通过 PR 更新公开的采纳清单，清单是持续维护的产物而不是一次性
  报告；
- 对 fork 与同名项目评估可吸收能力（offline 打包、fork 安全 CI、平台适配、
  本地化、质量门禁工作流）；
- 有价值的来源直接在其 PR/issue 上发一条上游 PR 邀请；
- 社媒内容从同一份公开证据出发，走 content-ops 管线并带显式 review 状态，
  发布前始终由 owner 审批。

公开证据：生态采纳清单
（[`ecosystem-adoption.md`](ecosystem-adoption.md)）、TypeScript 迁移 RFC
（[#3225](https://github.com/huangruiteng/loopx/issues/3225)、
[#3226](https://github.com/huangruiteng/loopx/pull/3226)）、content-ops
能力。

### 模式 C：Issue→PR Bot

issue-fix 能力把一条公开 issue 变成小而聚焦、验证充分、可审阅的 PR，并跟进
到 merged、closed 或明确的 no-follow-up。它组合四层能力且边界清晰：

- 状态内核提供跨 turn 的 goal、todo、quota、authority、monitor、replan 与
  终态收口；
- 可选仓库记忆提供 advisory 上下文，命中必须回到当前 checkout 验证；
- 垂域状态记录 feasibility、仓库上下文、交付证据、reviewer 路由、PR
  lifecycle 与 outcome；
- Agent 运行时提供理解、编码与执行。

核心承诺：该能力由长程维护 goal 驱动，而不是一条命令。goal 持续选择候选、
产出 focused PR、观察 PR lifecycle，并接续下一个 issue。
`/loopx Fix <issue-url>` 只是种下一个候选；feasibility、PR lifecycle 与
outcome 持久化在垂域状态里，跨越 turn、模型切换、CI 等待和 review 往返。

公开证据：[issue-fix 能力文档](../../loopx/capabilities/issue_fix/README.zh-CN.md)、
showcase 附录中的 OpenViking pilot。

## 3. 最佳实践

### 3.1 用状态替代聊天记忆

每个重复操作都需要持久状态面：todo、quota、monitor、replan。当维护或
outreach 被打断，下一轮从状态恢复，而不是从对话历史猜。Heartbeat automation
用 backoff 和静默等待，而不是轮询或强行推进。

### 3.2 把判断沉淀成契约、skill 和 CLI

维护者最高杠杆的工作是判断。一旦识别出一种判断，就把它编码：带 exact-head
证据的 PR review skill、拒绝无真实调用方模块的 scope-fit 门禁、类型化决策
packet、CLI 入口而不是聊天里的指令。这把"认真 review"从提醒变成可检查的
门禁。

### 3.3 分类入口，跑采纳漏斗

进入的 issue、PR 和提及先分类再干活。issue/PR 打类型化 tag；生态提及分为
集成、采样、衍生与覆盖。每周 monitor 通过 PR 更新公开清单，material 变化
生成具体跟进 todo，而不是靠记忆。

### 3.4 安全与发布纪律显式化

安全报告、披露与发布切点是记录的决策路径，不是即兴发挥。模式是：复现并
修复 → exact-head 自审 → 合适时发布协调披露 → 带可读 changelog 与验证命令
的 release。披露什么、何时披露，最终由人决定。

### 3.5 Outreach 是节奏，不是战役

fork 与衍生评估按计划执行。对每个有价值的来源，在作者自己的 PR/issue 上发
一条聚焦邀请，给出具体上游目标并承诺 review。outreach 本地留痕，后续跟进
交给 monitor，而不是在聊天里轮询。

### 3.6 内容运营与仓库互相喂养

公开仓库证据是文档与社媒内容的 source map。内容走门禁管线：source → angle
→ draft → feedback → publish gate → readback。已批准内容回链仓库，读者
信号再变成新证据。原始私有状态不进公开 packet。

### 3.7 收敛先固定 parity

当两条执行路径发生语义漂移（比如 turn settlement 与 quota settlement），
先用 parity fixture 固定两边行为，再抽取共享 driver，最后删除重复分支。
语言迁移同理：先契约冻结、parity 门禁，再逐块替换并留回滚记录。

### 3.8 人的门禁始终真实

自动化降低重复劳动成本，但不把人移出判断：发布、披露、合并决策与范围变更
始终是显式门禁。审批绑定具体 revision，修改已批准内容会使审批失效。

## 4. 失败模式与边界

- 长程探索可以很宽仍然漏掉关键细节；harness 解决"跑得久"，但"读得全"
   还需要更好的 tool 设计。
- 低质量贡献者 PR 在临时评审下可能漏过；显式 review skill 与 exact-head
   证据让信号可见。
- monitor 阈值是启发式；根据观察调整默认值（比如提高 replan 前的
   unchanged 次数）并记录原因。
- 不要一次性重写大核心；用契约、parity 与可回滚切片。
- 公开材料不泄漏内部语境：无私有链接、raw logs、凭据或未经批准的内部
   指标。

## 5. 上手

### 5.1 最小接入

要求 Python 3.11+ 与 `curl`、`tar`。无需 clone，先安装再在项目根目录接入：

```bash
curl -fsSL https://loopx-project.github.io/loopx/install.sh | bash
export PATH="$HOME/.local/bin:$PATH"
loopx doctor

cd /path/to/your-project
loopx connect
loopx start-goal --guided --project . --goal-text "你的长程目标"
```

保持 `.loopx/`、`.codex/goals/`、`.local/` 忽略。接入后从宿主 agent 用
`/loopx <task>`（Codex App/CLI）、`/loopx` + `/loop`（Claude Code）、goal
bridge（OpenCode）或 Pi goal 扩展驱动目标。

### 5.2 指令速查

```text
loopx status                    # goal/registry 健康与下一步
loopx todo list --goal-id <id>  # 项目 todos
loopx quota should-run          # 这个 agent 现在该动吗
loopx todo claim                # 谁拥有这个切片
loopx todo update               # 发生了什么变化
loopx refresh-state             # 下一轮应该看到什么
loopx quota spend-slot          # 结算一个完成并验证过的切片
```

能力入口：

```text
loopx issue-fix workflow-plan                      # 规划 issue 修复路线
/loopx Fix https://github.com/owner/repo/issues/123
loopx content-ops queue-status --item-json <item>  # 内容管线队列
loopx value-connectors source-map --format json    # connector-first source map
```

### 5.3 建第一个 continuous monitor

维护 monitor 就是一个带 cadence 的类型化 todo。下表是本仓库当前实际在跑的
监控，按运营循环分组：

| 循环 | 监控（`target_key`） | cadence | 做什么 |
| --- | --- | --- | --- |
| 维护 | GitHub issue intake（`github-open-issue-intake`） | 6h | 新 issue 分类并路由 |
| 维护 | Open PR review queue（`github:huangruiteng/loopx:open-pr-review-queue`） | 3m | 扫 PR 队列，门禁通过才评审 |
| 维护 | Public smoke quality repair（`github:huangruiteng/loopx:public-smoke-quality`） | 15m | 发现并修复 public smoke 失败 |
| 维护 | Non-benchmark quality watch（`public-nonbenchmark-quality-watch`） | 6h | 观察 benchmark 之外的质量回归 |
| 维护 | Repository quality（`repository-quality-monitor`） | 14d | README 首屏、quickstart、公开边界扫描 |
| 维护 | Community feedback funnel（`community-feedback-funnel`） | 14d | 反馈入口与 triage 对齐 |
| 维护 | Collaboration/showcase tracker（`collaboration-showcase-candidate-tracker`） | 14d | 跟踪合作与 showcase 候选 |
| 生态 | GitHub mention scan（`github-loopx-mention-scan`） | 7d | 扫描公开提及，通过 PR 更新采纳清单 |
| 生态 | Fork contribution outreach（`github-loopx-fork-outreach-scan`） | 7d | 评估 fork/衍生，发上游 PR 邀请 |
| 生态 | Content-ops cadence（`loopx-x-content-ops-cadence`） | 1d | 管理内容管线节奏 |
| 生态 | X profile readback（`x-profile-public-readback`） | 7d | 回读公开 X 主页 |
| 生态 | X distribution funnel（`x-public-distribution-funnel`） | 7d | 观察 X 分发漏斗 |

可直接复制的生态提及扫描示例：

```bash
loopx todo add --goal-id <goal-id> --project . --role agent \
  --claimed-by <your-agent-id> --task-class continuous_monitor \
  --action-kind github_mention_scan --target-key github-mention-scan \
  --cadence 7d --next-due-at "2026-08-22T10:00:00+08:00" \
  --expires-at "2027-08-15T10:00:00+08:00" \
  --continuation-policy same_agent_non_delivery \
  --text "[P2] 每周扫描仓库公开提及，分类并更新采纳清单"
```

旁边的 fork outreach 扫描：

```bash
loopx todo add --goal-id <goal-id> --project . --role agent \
  --claimed-by <your-agent-id> --task-class continuous_monitor \
  --action-kind github_loopx_fork_contribution_outreach \
  --target-key github-loopx-fork-outreach-scan \
  --cadence 7d --next-due-at "2026-08-22T10:30:00+08:00" \
  --expires-at "2027-08-15T10:00:00+08:00" \
  --continuation-policy same_agent_non_delivery \
  --text "[P2] 每周评估 fork/衍生项目可吸收能力，有价值来源发上游 PR 邀请"
```

Heartbeat automation 会在到期时拾起这个 monitor。每次运行把 evidence 写回
同一条 todo；material 变化生成跟进 todo，无变化时保持 quiet no-op，不硬推。

### 5.4 启用 goal 驱动的 issue→PR 自动化

issue-fix 是 goal 循环，不是一次性命令。先建一个维护 goal（或复用当前仓库
goal），目标设为持续修复公开 issue 并把每条 PR 跟进到终态：

```text
/loopx 持续修复仓库公开 issue：选择可修复候选，产出 focused PR，
跟进 CI 与 review 到 merged/closed，再接下一个 issue
```

`/loopx Fix https://github.com/owner/repo/issues/123` 只是往该 goal 种下一个
候选。能力会构建 feasibility、仓库上下文、reviewer、validation 与 PR
lifecycle packet，并给出一条明确路线：`fix_pr` / `comment_only` /
`triage_only`。同一份垂域状态跨多个 issue 累积 feasibility、PR lifecycle 与
outcome；一条 PR merged/closed 后自动接续下一个候选，而不是停掉循环。

宿主 agent 只有在 LoopX 状态记录了该权限且仓库策略允许时才能创建/更新
PR；merge 始终是独立决策，除非显式授权。

### 5.5 可选内容运营管线

创作者/运营工作流先拿 source map，再投影 item 队列：

```bash
loopx value-connectors source-map --format json
loopx content-ops queue-status --item-json path/to/item.json --format json
```

item 走 source → angle → draft → feedback → publish gate → readback，
发布前始终被显式 owner 决策挡住。

## 6. 可复用 Playbook

1. 每周：扫描公开提及，通过 PR 更新采纳清单。
2. 每条进入的 issue/PR：分类、打 tag、路由；可修复的 issue 走 issue-fix
   循环。
3. 每个 fork/衍生：与上游 diff 对比，判断可吸收性，发一条上游 PR 邀请，
   留痕。
4. 每个安全报告：复现、修复、exact-head 自审、决定披露、发布。
5. 每个社媒想法：建 public-safe source map，走 content-ops 管线，owner
   审批后发布，再回读。

## 7. 证据索引

- 生态采纳清单：[`ecosystem-adoption.md`](ecosystem-adoption.zh-CN.md)
- TypeScript 迁移 RFC：issue
  [#3225](https://github.com/huangruiteng/loopx/issues/3225)、RFC PR
  [#3226](https://github.com/huangruiteng/loopx/pull/3226)
- Issue-fix 能力：
  [`loopx/capabilities/issue_fix/README.zh-CN.md`](../../loopx/capabilities/issue_fix/README.zh-CN.md)
- Content-ops 能力：
  [`loopx/capabilities/content_ops/README.md`](../../loopx/capabilities/content_ops/README.md)
- v0.4.5 至 v0.4.7：
  [releases](https://github.com/huangruiteng/loopx/releases)
