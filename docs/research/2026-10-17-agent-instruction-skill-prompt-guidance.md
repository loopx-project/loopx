---
date: 2026-10-17
query: "主流 AGENTS.md 写法、Skill 优化及 LoopX 项目 Prompt 优化"
type: breadth
tags: [agent-instructions, skills, prompts, context-engineering]
---

# AGENTS、Skill 与运行时 Prompt：对标及 LoopX 优化边界

本报告补充 [PR #6723](https://github.com/loopx-project/loopx/pull/6723) 的设计依据。
项目观察基于 `0c5da025f`：该提交已经调整根指令加载与文档归属，尚未修改
`skills/` 或运行时 prompt。以下将官方建议、公开仓库实例、当前源码观察和
待验证方案分开；没有执行真实模型调用或宣称任务成功率提高。

## 三类指令承担不同职责

| 层级 | 应保留的内容 | 应用方式 | 主要验证 |
| --- | --- | --- | --- |
| `AGENTS.md` / `CLAUDE.md` | 项目目的、关键约束、能力与源码 owner、任务入口 | 随宿主会话加载 | 是否完整加载、版本与来源是否正确 |
| `SKILL.md` | 适用条件、首个动作、必读资源、操作顺序、停止和回读条件 | 选中工作流后读取，参考资料按需展开 | 是否选对 skill、是否读取正确分支、授权和结果是否正确 |
| 运行时 prompt / packet | 当前任务、已准入身份与边界、必读来源、动作、结算 | 从当前 typed 状态投影 | 决策、工具效果、持久化和结果回读是否正确 |

加载完整、任务路由、模型行为和机器强制是四层证据。CLI 请求包含完整文本
不能证明后续一定执行；prompt 中的义务也不能替代权限、validator 和状态机。

## 主流 AGENTS.md 的可借鉴写法

[AGENTS.md 开放格式](https://agents.md/)没有规定固定章节模板，常见内容包括
项目概况、环境和测试命令、代码约定及安全边界。具体宿主的发现和优先级仍需
按其实现核实，不能从开放格式站点推导所有宿主都以同一种方式加载。

[OpenAI Docs 的维护建议](https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra)
强调按任务指向有关材料，避免每次编辑都读取整套架构、数据库和部署文档。
对 LoopX 的适配是保留关键规则，并明确什么动作前必须读取哪个详细 owner。
这项建议不能用于删减现有授权、真实验收或停止条件。

[Claude Code 官方建议](https://code.claude.com/docs/en/best-practices#write-an-effective-claudemd)
侧重项目特有命令、约定和难以从代码猜出的陷阱。长流程可归入按需 skill；
应通过实际行为检查指令修改的效果。200 行是维护建议，不能当作通用加载硬上限。

两个项目实例也体现了不同侧重：

| 一手实例及读取版本 | 可借鉴之处 | 不应外推的结论 |
| --- | --- | --- |
| [Apache Airflow](https://github.com/apache/airflow/blob/20ccfcd7457a6a5032eefce0d18c09f9db9bcb27/AGENTS.md) | 具体开发/验证工具，安全文档 owner，按动作进入贡献 skill | 文件长度或项目知名度不能证明其规约对 LoopX 更有效 |
| [Temporal Java SDK](https://github.com/temporalio/sdk-java/blob/eaa7b337338ebd143a4c6df46b16251f3c130dc3/AGENTS.md) | 模块背景、Java 兼容性、Gradle 测试及 PR 检查 | Java 项目的命令和约束不应复制到 LoopX |

PR #6723 已覆盖共享入口、核心功能说明、关键约束、按场景读取细则、稳定锚点和
加载回归检查。仍需用真实任务观察：是否读对细则、是否漏读、是否无谓加载全量文档。

## Skill：先提高触发与分流精度

[Agent Skills 规范](https://agentskills.io/specification)采用 metadata、完整入口正文、
按需资源的分层加载。入口应说明用途和触发条件；500 行及约 5000 token 是建议，
不是宿主截断承诺。确定性机械工作适合 scripts，条件性背景适合 references；
不要把已有 capability 的决策逻辑复制进新的 skill 脚本。

[Anthropic 的 skill 编写指导](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
建议先收集失败任务和建立评测，再用最小必要指令迭代。高风险操作仍需要准确的
顺序、命令和验证门，不能机械应用“越短越好”。

当前抽样检查的结果：

| 项目入口 | 已有好模式 | 有界改进建议 |
| --- | --- | --- |
| [loopx-project](../../skills/loopx-project/SKILL.md) | 明确排除普通任务与例行 settlement；资料登记转到更窄 skill | 1133 行 / 56,400 UTF-8 字节是分流审查信号。保留身份、首个权威 packet、准入和停止条件，将安装、heartbeat 配置、人类 reward、多项目状态等条件流程移到直接链接的 reference |
| [loopx-pr-review](../../skills/loopx-pr-review/SKILL.md) | capability 拥有评审策略；保留完整 packet、当前 policy revision、exact head 与发布回读 | 先建立入口条款到 capability 字段的归属对照，证明重复后再处理。180 行不是可以无条件继续压缩的理由 |
| [loopx-self-repair](../../skills/loopx-self-repair/SKILL.md) | 先复用证据，按症状检索 reference，禁止全量加载 catalog | 可作为 project skill 的渐进披露参考，保留公开 issue 的资格、授权、去重和回读门 |
| [loopx-doc-registry](../../skills/loopx-doc-registry/SKILL.md) | 围绕一个连贯的材料登记任务组织 | 可评测“source id 可找回、刷新后的 authority projection 可发现”的回读；无需为了短而继续拆分 |

建议先冻结旧 skill、完整技能目录、宿主/模型版本和输入，再比较候选版。每类放
少量代表案例即可开始，不能以一次成功替代分布观察：

| 案例类 | LoopX 示例 | 检查工具事件与回读 |
| --- | --- | --- |
| 正触发 | 连接项目、配置 Goal、显式 review PR | 选择正确 skill，先取得对应权威 packet |
| 负触发 | 已有 contract 下的普通修复/settlement | 不因出现 LoopX、registry、refresh-state 就重跑 onboarding |
| 邻近歧义 | 临时阅读与长期资料登记；状态咨询与 self-repair | 选择符合实际结果的流程，记录误触发/漏触发 |
| 授权停止 | 无身份绑定、他人 Draft、仅获 review 权限 | 不接管、改 Draft、发布或合并；继续授权范围内的准备工作 |
| 结果回读 | head 变化、登记未投影、命令失败 | 重取证据并保留失败/未验证，不能凭命令调用或 HTTP 成功结案 |

当前没有本轮采集的 skill 混淆矩阵、旧新行为对照或实际任务完成率，因此这些
是候选优化方向，不能作为已经完成 skill 优化的声明。

## 运行时 Prompt：复用现有 owner 和评测

[Anthropic context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
强调提供充分且高信号的信息，并用清晰分区、轻量引用和运行时检索管理上下文。
最小充分信息不等于最短文字；LoopX 的授权、证据、顺序、恢复和结算义务必须保留。

源码中已经具备可以继续复用的基础：

- [project_prompt.py](../../loopx/project_prompt.py) 区分一次性 bootstrap 与后续 heartbeat。
  源文件 1291 行 / 51,616 字节包含多个 renderer，不能据此推算实际输入 token。
- [heartbeat/task_body.py](../../loopx/control_plane/heartbeat/task_body.py) 的 captured
  TurnEnvelope 路径按需读同次保存的 `detail_ref.full_decision`；读取该观察不构成
  新 admission。状态或需求变化后仍按现有规则重新进入 guard。
- [interaction_contract.ts](../../loopx/control_plane/work_items/interaction_contract.ts)
  的 `projectInteractionRequiredReads` 从 typed owner 生成必读来源，保留 Goal、
  acceptance 和 Todo 的完整性；内容本身不授予执行权限。不要新增 Python 决策源。
- [answer_instruction.py](../../loopx/presentation/answer_instruction.py) 统一回答表达，
  明确不持有工作授权。优化表达与改变状态/权限语义必须区分。
- [heartbeat/budget.py](../../loopx/control_plane/heartbeat/budget.py) 已区分实际字符数、
  路径归一化后的预算字符数和模式上限。这是展示回归预算，不是外部 transport 上限。
- [host_prompt_behavior.py](../../loopx/control_plane/testing/host_prompt_behavior.py)
  已有五类独立 oracle：静默工作、通知等待、静默等待、replan、等待后的独立工作。
  expected 不发给模型，重复独立，明确不证明 host execution/settlement。

### 本次纯渲染观察

使用生产 builder，输入为合成的 `codex_app_heartbeat`、单一 `worker-a`、不存在真实
Goal 的 fixture id 和相对状态文件名；没有启动模型或写入活动 Goal。以下是
**字符预算**结果，不能换算为 token、费用或模型表现：

| 模式 | Reward Memory | 实际字符 | 归一化预算字符 | 该模式上限 | 结果 |
| --- | --- | ---: | ---: | ---: | --- |
| thin | 默认启用 | 3063 | 3019 | 3840 | 通过 |
| brief | 默认启用 | 4607 | 4519 | 5240 | 通过 |
| compact | 默认启用 | 7145 | 6981 | 7340 | 通过 |
| full | 默认启用 | 13435 | 13277 | 12640 | 超出 637 |
| full | 关闭 | 13015 | 12857 | 12000 | 超出 857 |

非 native/Ark 的默认 Reward Memory 投影在命中相应 marker 时有 640 字符 headroom；
上表启用/关闭结果均按现有预算代码取得。full 在此 fixture 下不满足自身预算，
这是值得单独定位的信号；未证明线上失败，也不是本 PR 引入的运行时改动。
该观察不构成直接增加预算或删减义务的理由，应先对同 workload 做 base/head 对照。

另行运行现有 `tests/control_plane/test_host_prompt_behavior.py`：**8 passed**。
该测试使用 scripted client，证明 oracle/负例与默认关闭的资格逻辑，并非真实模型成绩。

### 建议的下一批次与验收

1. **计量与复现。**沿既有预算矩阵记录 host/profile/mode、可选功能和完整请求各分段；
   先复现 full 的字符差额。真实输入包含系统提示、工具 schema、历史与工具结果，
   需要明确模型/分词器版本和计数覆盖范围。当前没有这些完整 token 数据。
2. **语义与行为。**复用既有 oracle，覆盖静默但须工作、来源过期、等待后继续、
   禁止重复结算等独立对照；把决策正确与真实 effect/持久化/回读分开评分。
   [OpenAI eval 指导](https://developers.openai.com/api/docs/guides/evaluation-best-practices)
   支持固定环境并覆盖正常、边界和困难用例，不能把文本自述当作执行证据。
3. **有证据的文本调整。**只改一个已定位的共享规则或 renderer，以相同输入和
   独立 oracle 比较 base/head；再决定按条件投影、重排、保留或有依据地调整预算。
   授权、identity、新鲜证据和结算 owner 继续留在现有 typed 边界。

若研究缓存，应分别记录输入、缓存命中、延迟和费用。稳定前缀与动态后缀可能有
成本价值，但缓存不会让上下文变短，也不证明决策更可靠。
[OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)
提供具体机制；是否适用于某个宿主需检查其真实请求和支持能力。

## 可复核方式与研究边界

现有无 provider 单测：

```bash
uv run --no-sync --extra test python -m pytest -q tests/control_plane/test_host_prompt_behavior.py
```

纯渲染复现：

```python
from pathlib import Path
from loopx.heartbeat_prompt import build_heartbeat_prompt

for mode in ("thin", "brief", "compact", "full"):
    packet = build_heartbeat_prompt(
        goal_id="instruction-research-fixture",
        active_state=Path("ACTIVE_GOAL_STATE.md"),
        agent_id="worker-a", registered_agents=["worker-a"],
        runtime_profile="codex_app_heartbeat", **{mode: True},
    )
    print(mode, packet["interface_budget"])
```

外部材料是写作原则与实例，不是 LoopX 效果证明；本轮为定向源码抽样，不是
全仓 prompt 审计。PR #6723 已验证根指令加载完整；skill 触发改进、运行时文本
调整、模型行为比较和真实执行验收仍需各自的有界实现与证据。
