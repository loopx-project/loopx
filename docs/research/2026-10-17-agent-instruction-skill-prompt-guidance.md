---
date: 2026-10-17
query: "AGENTS.md、项目 Skill 和运行时 Prompt 的分层及持续执行"
type: breadth
tags: [agent-instructions, skills, prompts, context-engineering]
---

# AGENTS、Skill 与运行时 Prompt：实现依据与验收边界

本文承接 [PR #6723](https://github.com/loopx-project/loopx/pull/6723) 的研究，
说明 [PR #6725](https://github.com/loopx-project/loopx/pull/6725) 的整合取舍。
代码比较基线为 `d4487a7c0a332365fc80ee3ca43d421bdf510d42`。外部材料解释
写作原则，仓库实现决定实际权限与动作；两者均不能替代模型行为或真实运行验收。

## 外部规范和公开实现

| 一手来源 | 借鉴的做法 | 在 LoopX 中的应用与限制 |
| --- | --- | --- |
| [OpenAI AGENTS.md 加载规则](https://learn.chatgpt.com/docs/agent-configuration/agents-md) | 按路径发现指令，默认合计 32 KiB | 40,363 字节的旧根文件超限；检查 UTF-8 字节和路径，不能用行数或 token 估算替代。祖先文件、目录指令和覆盖设置仍需单独检查 |
| [OpenAI skill/prompt 维护建议](https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra) | 最小入口负责按任务分流；支持材料按需读取 | 项目 skill 从 1,133 行改为 203 行入口及六份 reference；没有据此删除授权、恢复和验收义务 |
| [Claude Code memory/import](https://code.claude.com/docs/en/memory#share-one-file-with-other-coding-tools) | 显式导入共享指令；导入内容进入上下文 | `CLAUDE.md` 仅含 `@AGENTS.md`，避免维护第二份政策。详细流程不用自动导入；32 KiB 不是 Claude 的通用硬上限 |
| [Agent Skills 规范](https://agentskills.io/specification) | metadata、入口和资源分层；reference 使用相对路径 | 六份 reference 一起打包，并从仓库外实际安装、逐项回读。规范中的长度建议不是效果保证 |
| [Anthropic skill 编写指导](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices) | 从实际任务和评测迭代，保持必要操作精度 | 覆盖准入、停止、选择分支和结果回读；离线任务对照与真实模型成绩分开 |
| [Apache Airflow 固定版本](https://github.com/apache/airflow/blob/20ccfcd7457a6a5032eefce0d18c09f9db9bcb27/AGENTS.md) | 项目特有验证命令、模块边界和安全文档 owner | 明确源码环境及规则归属；不复制 Airflow 命令或把项目知名度当有效性证据 |
| [Temporal Java SDK 固定版本](https://github.com/temporalio/sdk-java/blob/eaa7b337338ebd143a4c6df46b16251f3c130dc3/AGENTS.md) | 模块背景、兼容性、构建和测试约定 | 为根指令补项目定位与源码导航；不引入 Java 项目的工具和规则 |

这些是公开实现样本，并非热度排名或“更短必然更好”的因果证据。

## 复用的仓库实现

| 现有 owner | 本次复用 | 修复的问题 |
| --- | --- | --- |
| [self-repair skill](../../skills/loopx-self-repair/SKILL.md) | 按症状选择 reference | 项目 skill 不再要求每个任务都读取完整安装、配置和诊断流程 |
| [项目 skill](../../skills/loopx-project/SKILL.md) | 第一份当前 packet、身份与场景路由 | 排除普通任务和例行 settlement，保留正确的 onboarding/诊断入口 |
| [settlement plan](../../loopx/control_plane/quota/settlement_plan.ts) | 当前 `ordered_steps` / `next_cli_actions`、原始 ID 和真实结果 | 删除固定 refresh/spend、无 adapter 免结算、固定继任任务等第二套操作规则 |
| [interaction contract](../../loopx/control_plane/work_items/interaction_contract.ts) | typed required reads 与当前准入边界 | 项目/Goal/Todo 上下文仍由现有 owner 生成；内容发现不授予执行权限 |
| [heartbeat rules](../../loopx/control_plane/heartbeat/rules.py) | 共享授权、持续执行和停止条款 | 多模式文字统一；通知静默不取消已准入工作，纯通知等待不产生 spend |
| [project prompt](../../loopx/project_prompt.py) | 宿主对应的 bootstrap loader | 自动化保存 loader 而非冻结某次 Turn 或旧手工命令序列 |
| [PR review capability](../../loopx/capabilities/pr_review_queue/README.md) | 当前 review action、完整 packet、精确 head 和结果校验 | null action 不再被解释为必有现成结论；作者提交状态与 reviewer 权限分开 |
| [CLI 差异验证](../../loopx/control_plane/testing/cli_output_differential.py) | 相同 workload 的 base/head 测量及语义审查 | 不通过删去恢复/继续条件来满足旧长度阈值 |

新文字明确要求：围绕当前结果继续完成实现、集成、负例和回读；上下文压缩或
会话恢复后先读当前状态。只有受限动作等待授权，其他独立授权工作仍可继续。
目标已完成时不为保持忙碌而造新任务。Todo 完成与 Turn 结算分别回读，结果不明时
先查效果再决定重试。这些是可检查的行为契约，尚不是 long-horizon 成功率提升证明。

## 与 #6723 的整合

| #6723 的内容 | #6725 的处理 | 原因 |
| --- | --- | --- |
| 截断定位、关键约束和完整加载 | 保留问题与字节回归，逐条核对必要义务 | 默认加载缺口是真问题；删除尾部约束不是修复 |
| 项目定位、核心能力与源码 owner | 纳入根文件 | 帮助 Agent 在选择任务前理解项目目标 |
| `CLAUDE.md` 共享导入 | 纳入并检查唯一入口 | 支持不同宿主，不复制政策正文 |
| 外部规范和公开项目研究 | 在本文承接并更新到本次实现 | 旧研究把 skill/runtime 改造列为未来工作，不能原样当当前结果 |
| 三份新 `agent-*` 流程文档 | 不引入；采用已有 development、release、state-machine、extension 和 smoke owner | 避免在既有规范之外再复制完整政策集合 |
| 12 KiB / 200 行的额外维护预算 | 不照搬；使用真实 32 KiB 默认加载边界 | 先保留授权与验收语义。当前根 27,976 字节，仍需防止后续增长，不宣称它是最优长度 |
| 独立整份 instruction smoke | 将共享导入检查放入已有 root-delivery pytest | 保留对应验证价值，避免两套扫描器与阈值 |

两个重叠根改造不应独立合并。#6723 的关闭只表示提案被整合替代，不表示它已
合入 main，也不把其旧 head 的审查、宿主观测或绿色 CI 自动转移给 #6725。

## 证据与未证明的部分

根字节/链接检查、条款审查、实际 wheel 安装、安装脚本 smoke、当前 packet
对照和 CLI base/head 矩阵分别证明不同层面。具体 revision、执行结果和未通过项
记录在 PR 的 validation/review 中，不在本研究文档复制会持续变化的 CI 状态。

四个离线 skill 场景的旧版与新版均通过八项断言：这能检查选路和授权保留，
区分能力有限，不能证明任务成功率提升。scripted host oracle 检查评测规则和
负例，也不能冒充真实模型成绩。尚无本次真实 provider 连续运行、安装宿主采用、
完整请求 token/缓存/费用或长期完成率的对照结果。

同输入的实际 CLI 矩阵保留 full/compact 的原预算；thin/brief 为完整恢复、授权
和结算条件做有证据的小幅调整。显示回归预算与 quota、准入和冻结实验标准分属
不同 owner，前者调整不放宽后者。

后续若验证效果，应固定宿主、模型、输入、reference 可用性和调用边界，分别
统计触发/误触发、正确读取、停止条件、实际工具效果、持久化和回读。至少包含
普通任务不触发 onboarding、只读资料不扩权、等待后恢复、通知静默仍有可做工作、
未知结果不重复结算、目标完成不造继任任务等案例。沿现有 evaluation owner
收集证据，无需建立另一套决策源或把真实实验作为本次文档维修的伪完成声明。
