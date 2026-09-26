# DSH / Pi：L1 观察与 Managed Runtime 选型

- **RFC 状态：** 已接受
- **替代 / 关闭：** 无

范围：[Reliability Diagnostics](./long-running-agent-reliability-diagnostics-governed-delivery-v0.zh-CN.md)
与 [Desktop Execution Frontends](./desktop-execution-frontends-v0.zh-CN.md) 的共同目标。
[English](./harness-selection-dsh-pi-v0.md)

## 决策

保留 **DSH 作为 L1 首个事件源**，但不据此宣布它已成为生产 Mode B 的最终首选。
Pi 保留为 managed runtime 候选。前者利用已存在的被动 observer 降低验证成本；后者
必须证明生命周期、provider、崩溃恢复和真实结果，插件事件 fixture 不能替代这些证据。
本评估不提供缺乏测量依据的评分或性能排名。

本文区分 DSH 的两个角色，二者不可混同：**托管有界 Turn 宿主**（LoopX 为一次受治理
Turn 选择的适配器）与凭据绑定，其出货默认宿主的解析随下文记录的托管栈落地
（PR #4443 已合并），且显式选择始终优先；管家通道则通过下文的单段 chat 传输抵达它；
**L1 事件源与会话归属 runtime** 角色仍是 opt-in，不因前者被晋级，仍需本文 C0、
C1、开销、保留与 Mode B 各行。

## 托管执行面（2026-09-15）

选型受仓库今天实际交付的能力约束，而不只取决于上游 harness 能做什么。托管的单次执行
单元是有界 Turn：

- `loopx turn run-once` 接受 `--host codex-cli|dsh|generic-cli` 与
  `--execution-mode isolated-headless`：LoopX 决定，宿主适配器调用 agent CLI，独立
  validator 证明后置条件，只有通过的结果才会被提交；
- `loopx host-mode-plan` 只有在宿主声明 `typed_host_adapter` 时，才为
  `continue_without_ui` 意图选择 `isolated_headless_turn`；缺少该声明时报该模式未就绪，
  并指出缺失的能力；
- 会话归属（`managed_runtime` 与 `attached_host`）不在本文决定，属于
  [Agent 会话执行模式](./agent-session-execution-modes-v0.zh-CN.md)，该文档同时拥有
  M1-M4 接入里程碑与跨前端投影行。

下文区分三种证据状态，不可互相套用；该列表以 2026-09-15 为准，并计划与托管栈一起
落地：

- **2026-09-15 已在 `main` 上**：`loopx turn run-once --host
  codex-cli|dsh|generic-cli`、`dsh` 宿主适配器、上面的 `host-mode-plan` 门槛，以及
  `main` 固定的 `deepseek-harness-sdk==0.1.2a3`；
- **2026-09-15 尚未进入 `main`、计划随本文落地**：显式选择宿主的默认值
  （`loopx/control_plane/turn_driver/host_binding.py`，PR #4443）与 `0.1.5rc1` 的 dsh
  固定版本（PR #4420）。后续读者若看到这两个 PR 已合并，可把这两行读作已交付；
  否则只能按栈内状态理解。三者已于 2026-09-15 合并，因此这些行读作已交付；下文的
  管家行随后又被修订过两次：首次修订仍把管家通道默认到 `codex`，并让托管宿主无法
  从该通道抵达；第二次修订把该默认值改成按凭据分派，于是"发现一个凭据"就会改指
  这个人正在对话的面。本次修订把管家通道的出货默认值恢复为每台机器都是 `codex`，
  托管宿主只经由显式选择抵达，下文表格即按此表述；
- **本地真实验证，不是仓库门禁**：下文标注为本地证据的行。它们需要 operator 凭据
  才能复现，CI 不做断言。

| 角色 | 来源 | 当前选型 | 晋级门槛 |
| --- | --- | --- | --- |
| 默认托管执行宿主 | LoopX Turn 加 `dsh` 宿主适配器，并绑定到运维方提供的模型端点 | 出货默认值：配置了运维方凭据时托管有界 Turn 走 `dsh`，没有凭据时走个体 `codex-cli`；显式 `LOOPX_TURN_HOST` 可改指，显式 `--host` 优先 | 保持类型化 host request/result、独立验证与凭据归属运维方的边界；没有同等或更强的契约不替换 |
| 管家通道执行器 | 管家回答所依赖的交互式 Chat 传输 | 三层依次决定：本机 `steward_executor` machine-config 命名空间（前端可改，`loopx machine-config describe`/`inspect` 可回读，2026-09-16 落地）、`LOOPX_MANAGER_ENDPOINT`（用于引导或指向未列出的适配器）、出货默认值 `codex`（每台机器一致）；选择托管宿主（`dsh`）时执行器、模型与推理档位一起跟随 | 单段传输的类型化边界（无流式、无跨 turn 宿主会话、沙箱只读）必须持续披露并可回读；任何托管通道都不得依赖个人订阅；该命名空间不保存凭据、不授予任何权限 |
| 受支持的替代 Turn 宿主 | LoopX Turn 加 `codex-cli` 适配器 | 可显式选择，也是上一行托管默认值在没有 operator 凭据的机器上的解析结果；它属于 `individual` 执行器类型，账落在某个人的 CLI 登录上 | 任何托管通道都不得*静默*依赖某个人的 CLI 订阅：个体宿主只会作为那条凭据解析默认值被走到，并以 `no_operator_credential` 回读，绝不被替换成运维方已选定的宿主 |
| L1 事件源与会话归属 runtime 候选 | DSH | opt-in，未晋级；有界 Turn 宿主角色见上一行默认值 | 本文 C0、C1、开销、保留与 Mode B 各行被真实执行并通过评审 |
| 可选的可见宿主循环 | Pi | 不是 managed runtime | 先声明按绑定持久化且可回读的会话模式，证明重启下的单执行器行为、"对话不是回执"、宿主本地状态非权威，并提供一条真实宿主重启行 |

### 可选 Ark 受控 Turn 档位

[`loopx-ark-turn`](../../../packages/loopx-ark-turn/README.md)单独安装，通过
`--host generic-cli` 显式选择并使用 fresh iteration context。它复用 dsh 的签名
请求/候选转换；admission、独立验收、工作写回及 quota 仍由 LoopX 拥有。每次 Turn
绑定一个 stdio MCP 进程，只暴露 operator 选择的工具和绑定的工作身份。Provider
模型用量、资源清理回执是观测，不是已验收工作 quota 或第二份任务生命周期。

本档位不改变默认宿主，也不改变原有 Ark `goal_once` 档位。原生 Goal 自驱和外层
LoopX Turn 驱动不能同时驱动同一绑定。[投研组合示例](../../../examples/managed-research-team/README.md)
验证 managed 协调员委派本地 worker，不据此晋升持久管家 Chat、递归团队监督、完整
实时 steer 或共享权威服务。按示例显式配置、回读和清理，保留失败与未验证的区别。

示例已集成五个预授权 canonical 任务和启动时一次性 owner 配置。两类宿主通过
`turn --todo-id` 选择精确工作；TS Todo 新鲜验收完成后才返回 accepted 结果。
综合任务检查子任务当前完成状态、绑定和产物哈希。Provider 清理、Turn 进展和
canonical 完成仍是不同事实；本切片不提供动态派生授权或另一份 Python 生命周期。
默认示例由本地 DSH 协调员组织两个 DSH 与两个 Ark 成员；云端核验员消费已完成的
本地分析，再返回自己的产物。辅助云端协调档位验证反向委派。两者复用相同 Turn
适配器，不改变管家默认执行器。

### 托管宿主绑定与真实环境验证（2026-09-15）

一个托管宿主绑定要说明四件事：宿主适配器、provider、模型，以及凭据来自哪里。
DSH 绑定是 DSH Turn 宿主 + provider `deepseek-official` + 模型 `deepseek-v4-flash`
（DeepSeek V4.1 Flash）+ 推理档位 `high`，端点取自运维方环境（`DEEPSEEK_BASE_URL`），
凭据取自运维方环境（`DEEPSEEK_API_KEY`）。

LoopX **选择**托管有界 Turn 的默认宿主，而从不由启动时的意外推断
（`loopx/control_plane/turn_driver/host_binding.py`）：显式 `--host` 或
`LOOPX_TURN_HOST` 始终优先；两者都没配置时，出货默认值由运维方自己的凭据事实解析
——配置了凭据就是托管 `dsh` 宿主，没有凭据则是个体 `codex-cli` 宿主，因为无法认证的
托管宿主只会拒绝运行。真正需要区分的是**默认值**与**决定**：凭据可以解析一个本来
无从选择的默认值，但它永远不会改指运维方已经显式选定的宿主。因此解析到 DSH 宿主的
通道不会依赖某个开发者本机 CLI 订阅是否可用、是否还有额度或是否已登录；没有运维方
凭据的通道也不会悄悄借用别人的订阅。

本次变更同时改写了上表中"受支持的替代 Turn 宿主"的晋级门槛：它原文是"个人通道必须被
显式选择，而不是默认走到"，而上面的凭据解析默认值与它冲突。改写后的规则保留原意——
任何通道都不得在运维方看不见的情况下依赖某个人的登录——并改为指明让这层依赖可见的
回读，而不是禁止这条已披露的默认值。

管家通道是**另一个**面；经上文记录的两次修订后，它的默认值是一个端点而不是一条规则：
每台机器都是 `codex`，即交互式 CLI 端点。三层按同一顺序决定它：本机
`steward_executor` machine-config 命名空间、`LOOPX_MANAGER_ENDPOINT`、出货默认值。
选择托管宿主（`dsh`）时，执行器、模型与推理档位一起移动，通道不可能出现"operator
模型跑在个人 CLI 登录上"的组合。这里凭据的作用与 Turn 行**相反**：凭据为被选中的
端点提供认证，从不会改指这个人正在对话的面——环境里冒出一个 key，不该让一段对话中途
换手。回读仍会给出端点来自哪里（`executor_endpoint_source`，现在包含
`machine_configuration`），以及出货默认值对应的是哪一条决定
（`executor_endpoint_default_reason`），因此运维方读到的是一个已决定的默认值，而不是
从解析出的宿主名去反推。

管家连接不会保存这份决定的第二份副本。连接记录把解析出的端点连同来源作为**观测值**
存下来；所有读取路径——Lark 路由、授权连接解析、以及在该通道上应答的 Turn——都重新
从本机解析。因此在另一个默认值仍生效时写下的记录，无法继续在被运维方替换过的端点上
应答——而这正是"回读说 `dsh`、管家却仍在 `codex` 上跑"的来路。当本机确实改了选择时，
通道上已绑定的 Session 仍跑在旧端点上；该 Turn 会以类型化回执
`manager_channel_executor_rebind_required` 被拒绝，回复直接给出唯一能修复它的动作——
重新应用一次该连接，把通道 Session 开在本机当前选择的端点上。

两个托管面从同一个所有者解析**执行档位**
（`loopx/control_plane/turn_driver/execution_profile.py`）：provider
`deepseek-official`、模型 `deepseek-v4-flash`（DeepSeek V4.1 Flash）、推理档位
`high`，可由 `LOOPX_TURN_PROVIDER` / `LOOPX_TURN_MODEL` /
`LOOPX_TURN_REASONING_EFFORT` 覆盖，更低优先级为历史变量 `DSH_PROVIDER` /
`DSH_MODEL`。回读是一行 `execution_profile`：出货形态为 `deepseek-v4-flash@high`，
仅当 provider 不是出货值时前置为 `<provider>/…`。之所以只有一行，是因为每个 plan
载荷都携带它，而面向 agent 的输出预算是一份契约；该行写出什么值，就是实际会跑的值，
因此 owner 自己设定的模型会以自身出现。凭据为选定档位提供认证，从不参与选型；凭据
唯一解析的是"无人显式选择时有界 Turn 的出货宿主默认值"，且该解析自带来源回读。

该绑定的证据按来源区分：

- 仓库覆盖、无需任何 provider 调用：配置了运维方凭据时出货默认值是 `dsh`，没有时
  是 `codex-cli`；显式 `LOOPX_TURN_HOST` 可改指任一默认值，显式 `--host` 优先于
  全部（`tests/test_turn_default_host_binding.py`、
  `tests/test_turn_managed_executor_binding.py`、
  `examples/loopx-turn-managed-executor-binding-smoke.py`、
  `examples/loopx-turn-managed-default-flow-smoke.py`）；
- 本地真实验证：在真实 SDK 与 runtime（`deepseek-harness-sdk==0.1.5rc1`，即 PR
  #4420 提出的固定版本；`main` 今天仍固定在 `0.1.2a3`，同一对路径在那里也通过）下，
  进程内 `--host dsh` 路径与 `generic-cli` 子进程路径均通过；
- 本地真实验证：一次托管 Turn 达到 `validated_progress`，宿主执行有界动作，独立
  validator 证明后置条件，随后才发生写回与配额扣减；
- 本地真实验证：一次后置条件未被证明的 Turn 反向失败关闭，没有写回，配额槽消耗计数
  保持为 0。

在该绑定成为正式默认值之前仍存在的缺口：

- `deepseek-harness-runtime-bin==0.1.5rc1` 捆绑的 runtime 快照无法按原样启动
  `headless` profile：其中一行会拉起
  `@deepseek-ai/dsh-session-title-first-prompt-llm`，该包 import 了未被收录的
  `@deepseek-ai/dsh-session-title-llm`；解析发生在打包快照内部，因此把该包装进
  profile 目录不会改变结果。当前本地做法是用一条绑定 overlay 关闭受影响的行。
  托管宿主路径不受影响：它不选择 `headless` profile，默认 `sdk` profile 能正常
  启动并干净退出；
- LoopX 的 DSH Turn 组合必须显式列出托管动作所需的工具行
  （`@deepseek-ai/dsh-tool-fs`、`@deepseek-ai/dsh-tool-bash`）。缺少它们时，真实模型
  只能作答而无法动手，Turn 会以验证失败而不是产出工作结束。
- 宿主模式计划此前把无人值守意图映射到兼容路径：`isolated_headless_turn` 的
  `turn_host` 取 `generic-cli`（`loopx/host_mode_planner.py`），因此它打印的
  `loopx turn plan` 命令写的是 `--host generic-cli`，而不是上文记录的已选 `dsh`
  默认值；作为回滚路径本身没错，但没有被标注为回滚路径。**已决并已落地：**计划采用
  "写出解析后的默认值"这一支——预览命令不再 pin 任何 host，pin 死的兼容变体报为
  `plan_command_rollback`，类型化的 `turn_mapping.host_selection` 说明该命令属于
  哪一种；真正需要可见身份的路径（转入 `visible_tui` 的 transition）仍然 pin 具体
  宿主。`docs/reference/protocols/host-mode-plan-v0.md` 已把 `turn_mapping.host`
  定义为该模式的声明宿主与调度上下文，而不是"具体宿主已经解析完成"。该计划的
  `--host-identity` 列表仍只覆盖可见宿主，因为像 `dsh` 这种仅 headless 的宿主无法
  拥有可见会话。

### 混合本地/云端 managed 资格化（提案）

[会话执行 RFC](agent-session-execution-modes-v0.zh-CN.md#reusable-agent-operations-and-continuation-ownership)
区分 provider、会话归属和续跑 owner。把同一 managed 工作合同扩展到合格云端宿主，
不能编码成本地=Turn、云端=原生 Goal；上文现有 managed-host 与管家默认保持不变。

LoopX 公开的 [Ark Managed Agent host contract](../../../loopx/ark_managed_agent_host.py)
当前规定一次 Goal 激活、原生续跑、无外层 Turn driver。云端 governed-Turn adapter
是**独立、尚未资格化、需显式选择的候选**，不能重新解释该 profile。DSH 的
[有界 Turn adapter](../../../loopx/dsh_goal_mode/README.md)与
[同会话 plugin](../../../packages/dsh-loopx-plugin/README.md)也有不同续跑合同，资格
不能互借。

先验收一次完整的本地/云端工作单元：真实工具/产物传递、typed result、独立拒绝错误
产物、验收后写回、usage 读回、取消与恢复；再验两轮依赖工作及 worker 主动委派，
复用统一协作合同。Host adapter 传输 Agent 的决定，不包含场景业务 phase 顺序。
等待中的父任务不能占满子任务需要的全部执行槽。

原生 Goal 还需公开文档支持的激活与身份、重复激活行为、评估/终态读回、有界资源使用、
defer/wake 和重启验证。消息成功或 session idle 都不足以证明。Provider API 或 slash
包装只放在其 adapter，不成为 LoopX 通用命令。Provider 能力声明必须有公开版本化
来源及可复现资格证据；本提案仅引用 LoopX 自身合同，不新增对 Ark 部署或 API 保证的声明。

实际 provider 用量与 LoopX 已验收工作 quota 分开报告：被拒绝的工作仍可能产生推理
费用，unknown 不能记零。只晋升已测试 profile，明确不可用能力；回滚到原选定 profile
时不能产生并行 driver。

## 证据基线

LoopX 检查基线为 `bf217e1e01bec79f357c9ecbd580cf2dfa73db8b`：

- `packages/dsh-loopx-plugin/src/observer.ts`：完整身份激活、事件压缩、首次落盘安全、
  有界 buffer 和 flush 隔离。
- `loopx/capabilities/reliability_diagnostics/{receipt,projection}.py`：独立验证、
  integrity 分类和无控制权限的诊断输出。
- `loopx/dsh_goal_mode/turn_host_adapter.py`：有界 Turn、session lineage、SDK 调用和
  失败映射，并不是完整 Desktop 外循环。
- `loopx/pi_goal_mode/{loopx-goal.ts,pi-goal-loop-runtime.mjs}`：有绑定及 continuation
  行为的可见宿主集成，不是被动 observer。
- `apps/desktop/loopx-control-plane/src-tauri/src/services.rs`：已有服务进程管理不等于
  RFC 所要求的完整 managed Agent 生命周期。

dsh 固定版本经历了两步，理解本文需要同时知道这两个状态：今天的 `main` 固定
`deepseek-harness-sdk==0.1.2a3`；托管栈把该固定版本升到最新发布通道，而不是未发布的
tag：PyPI 上的 `deepseek-harness-sdk==0.1.5rc1` /
`deepseek-harness-runtime-bin==0.1.5rc1`（PR #4420），与 npm `@deepseek-ai/dsh` 的
`latest` 一致（2026-09-15 核对）。上游 `next` 与 `alpha` tag 比该通道更新，这里不
采纳。

2026-09-06 独立检查的上游版本，不等同于 LoopX 已验证的安装版本：

- [DSH d347e703 README](https://github.com/deepseek-ai/deepseek-harness/blob/d347e703908d0406b7a7ef80e3a0e594d86b2215/README.md)：
  Cordis/plugin 架构，明确处于可能不兼容升级的 developer preview。
- [Pi 9767ba27 SDK](https://github.com/earendil-works/pi/blob/9767ba275f3e9a5ee0f5c5342249b629ab1b2282/packages/coding-agent/docs/sdk.md)：
  subscribe、session 操作及 runtime replacement API。
- [Pi 9767ba27 extensions](https://github.com/earendil-works/pi/blob/9767ba275f3e9a5ee0f5c5342249b629ab1b2282/packages/coding-agent/docs/extensions.md)：
  部分 hook 可以注入上下文、阻止工具调用、修改结果。

历史 Pi 仓库地址目前跳转至 `earendil-works/pi`，本次 SDK 文档使用
`@earendil-works/pi-coding-agent`。这是升级时要核对的差异，不是立即替换本地依赖
或假定新旧 API 兼容的理由。

## 按产品要求对比

| 要求 | DSH 证据 | Pi 证据 | 对选型的影响 |
| --- | --- | --- | --- |
| 被动观察 | 已有独立 observer entry、三个 session publication hook、首次落盘拒绝 | SDK 提供 subscribe，extensions 还提供干预型 hook | DSH 已有可验证切片；Pi 应优先订阅而非拦截，并证明隔离 |
| 身份与恢复 | Turn connector 派生 lineage；observer 另外要求精确 goal/session/run | SDK 将 AgentSession 与负责 replacement/resume 的 AgentSessionRuntime 分开 | 两边都要测重启、fork 后身份；有 API 不等于恢复可靠 |
| 单次有界执行 | 已有 timeout 和失败映射 | 当前 Pi Goal 集成含 continuation/pause | 不允许 native loop 与 Desktop supervisor 同时充当外循环 |
| 打包 | 独立 export/bundle、packed smokes | SDK resource loading 会发现 extensions | 检查实际加载的包及 profile；二者都不是 OS 进程隔离 |
| Provider | connector 版本与 SDK 约束明确 | SDK 暴露 runtime/model 构造 | 同 route/model/tools/budget 验证，harness 选择不代表 provider 兼容 |
| 数据安全 | producer/consumer 独立校验，共享反事实 | 工具/context hook 可接触和修改原文 | Pi 需补首次落盘安全及负向测试，不能复制 transcript |
| 开销 | 已有 buffer/count/flush 统计，没有本次匹配实测 | 有订阅接口，没有本次 LoopX observer 测量 | 未实测前不作数字排名 |
| 维护 | 上游明确可能 breaking，LoopX connector 有固定验证版本 | 当前包名与 runtime API 不能直接套用旧集成假设 | 两边升级分别固定版本，不拿已安装 DSH 对比未验证最新 Pi |

这是接入成本和合同差异，不是说 Pi 没有事件，或 DSH 不能使用其他模型。
两个 harness 都有控制 API；“被动”是具体 adapter 和实际加载依赖的性质。

依赖 dsh 的两个 LoopX 面并不一起移动：有界 Turn 宿主使用上文记录的 Python
SDK/runtime 固定版本（`0.1.5rc1`，已发布通道）；而 dsh 侧插件
（`packages/dsh-loopx-plugin`）的开发、宿主与客户端面现已统一构建在同一已发布的
`0.1.5-rc.2` 线上，不再停留在 `0.1.1-rc.2`，其 npm peer 范围只接受
`>=0.1.5-rc.1`。这是上游三处变化逼出来的，因此它是一条新的发布线而不是原地补丁：
0.1.5 线不再发布 `@deepseek-ai/dsh-client-runtime`（最后发布版本为 `0.1.1-rc.2`），
`slots` service 座位随之移到 `@deepseek-ai/dsh-client-ui-renderer`，也就是本 manifest
现在写入 `dsh.client.inject` 的包；`Session.events` 变为 `Session.snapshotEvents()`，
`Inbox.hasPending` 变为两个 pending 队列；共享 `/api` bridge 用
`<namespace>/<method>` 寻址 Remote 方法，并只接受一个 `args` payload 字段。一份
`dsh.client.inject` 无法同时为两代排序 boot row，所以插件不能同时声明两代。上文的
L1 observer 契约不变：observer 仍只消费 `session/created`、`session/event`、
`session/disposed`，只是把 token 级 `assistant/chunk` 行视为旧 durable 日志重放出来的
已退场输入，而不是现存事件类型。

## 数据流与权限

用户需要区分“没有证据”“执行有异常”“观察过程不可信”，而不是只得到一个绿灯：

```text
native session publication
  -> isolated observer: compact / validate / count / append
  -> independent ledger validation
  -> integrity receipt + diagnostic projection
  -> 仅供操作者展示

canonical eligibility -> Desktop supervisor -> bounded Turn -> validation/writeback
```

诊断不得反向进入 eligibility。`valid` 只代表观察合同通过，不代表任务成功；stall
信号不是重试授权，observer 故障也不能被当成 worker 故障。

## 本次落地的读取增量

```bash
loopx reliability-diagnostics status --goal-id <goal-id> --with-receipt --format json --as-of "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

上述 POSIX shell 示例使用当前 UTC 时间评估年龄；其它客户端应传入带时区的当前时间。
仅在历史重放时省略 `--as-of`：默认使用最后事件时间，因此最后事件年龄为零，不能
作为实时存活检查。分别显示观察时间和评估时间；推进评估时钟不改变 integrity。

显式选项让 receipt 与 projection 来自同一次 ledger 读取的内存结果，避免分别执行
两次 CLI 时观察到不同追加状态。不加选项保持原输出。这不提供文件并发追加的原子
快照；末尾半行仍按无效输入报告，不能悄悄丢掉。命令不激活 observer、不发现绑定、
不写 ledger、不调用模型，也不改变 Goal/Todo/lease。

这是可执行的读取接口，**不是已交付的 Mode B 面板或 supervisor**。未来面板必须
绑定精确 goal/session/run，分别展示观察时间、integrity 与任务状态；多 run 或过期
goal ledger 不得被标成当前 session 健康。输出只供操作者，不得进入 prompt 或调度。
现有 CLI 全量 ledger 读取没有大小上限，在引入经过评审的读取预算／快照策略之前，
不能直接拿这个命令做自动轮询。

## 验收方案与停止条件

1. **C0 保真**：比较 native 与 observer 关闭的 managed adapter。固定 model、route、
   tools、prompt、环境、预算、包／adapter 版本及起始 session，计入失败和重试；
   treatment 不一致则不采纳比较结果。
2. **C1 被动观察**：在通过 C0 的 adapter 上只开启 observer。记录完整身份、
   accepted/persisted/rejected/drop 数、receipt、endpoint 和 worker/scheduler influence。
   fixture 通过不等于 C1；非 valid receipt 不作为 eligible C1。
3. **开销**：成对重复测 baseline/observer 的 wall time、CPU、peak RSS、写入字节、
   吞吐、flush latency；报告样本数、分布、不确定性、冷／热启动条件。预算及验收
   阈值在运行前约定，本次不虚构阈值或性能结果。
4. **保留／删除**：owner 选择最大年龄／字节、活跃 writer 处理、支持访问、备份范围
   和删除验证。先 dry-run 盘点再删除，不能为满足大小限制截断活跃 ledger。
5. **Mode B**：在可丢弃 runtime 验证 start/resume/interrupt/close、进程崩溃、过期身份、
   重复完成、超时及 provider 失败；同一时间一个 Turn，canonical validation/writeback
   通过后才扣 quota 或请求下一 Turn。

原始日志、凭据留在 owner-local；公共材料只保留通用方法、固定版本、聚合结果和
安全引用。本文件不授权真实模型执行或删除现有记录。

里程碑归属仍由
[Agent 会话执行模式](./agent-session-execution-modes-v0.zh-CN.md) 决定：本文负责
L1 observer 这条臂的 C0、C1、开销与保留证据，以及上面针对会话归属 runtime 的 Mode B
验收；M1-M4 接入里程碑与跨前端投影行仍归该文档，本文不定义模式推断，也不定义第二个
执行器。

## 后续交付次序

先评审对比结论和 CLI 读取增量；Mode B 面板必须先具备精确 session 读取与有界刷新，
而不是新造一套通用监控。C0/C1 与开销作为单独预算实验，仅把复用修复和安全证据
提交仓库。删除功能等 retention profile 决定后再做。若 Pi 在相同隔离及生命周期
验收下具有更低的实测接入／运维成本，或 DSH 无法通过，再调整偏好。
不得为了让 L1 实验通过而加入 L2 建议、重试权限或新 scheduler。

## 管家通道的会话传输（2026-09-15）

受治理的 Turn 面与管家（manager）会话通道需要的宿主形态不同，如今解析默认值的方式也
不同：无人显式选择的有界 Turn 仍回落到由运维方凭据解析出的宿主，而管家通道在运维方
显式选择托管宿主之前一直停在交互式 CLI 端点。Turn 是一次有界工作片段，现有 DSH
adapter 已支持；管家通道还需要一个能持有交互会话的传输，而现有 DSH 面明确不承诺跨
turn 的 DSH 会话连续性。

路线 A 已落地，因此本节现在记录传输本身，而不是一个计划。管家通道通过
`loopx/chat_dsh.py` 持有托管宿主：每个 chat turn 在解析出的执行档位上启动**一次有界
dsh 片段**，把通道可见的有界历史与当前消息交给它，并返回最终的 assistant 消息。片段
看到的内容由 LoopX 组装，且片段的沙箱通过 `DSH_PERMISSION_MODE` 固定为只读，因此回答
不可能来自通道从未授予的环境写入或 shell 权威。

该传输**刻意不声称**以下能力，否则通道回读会被误读为提供了它们：

* **无流式**：回答以一条最终消息返回；
* **无跨 turn 宿主会话**：每个片段都是新的，可见历史属于 Chat 侧上下文，而不是通道
  恢复的宿主会话；
* **无工具权威**：片段伸手写文件时会被 dsh 沙箱本身拒绝，通道据实回报
  `trust_scope: read_only`。

正因为片段自己读不到任何东西，它**被期望谈论的每一个来源都必须由 LoopX 放进同一份有界
提示**：声明的证据窗口与已注册来源的读取由 Turn 侧组装，并带缓存与预算，因此「看得更远」
不会拖慢每一轮。这是传输形态的后果，而不是新增权威：片段仍不能扩大自己的范围，它没有收到
的来源是一条具名的覆盖缺口，而不是「没有进展」的证据。

早先的 typed reason `managed_host_chat_transport_unsupported` 随本次变更退役：它描述的
是当时并不存在的传输缺口，保留它会让一个可用的宿主无法被选择。通道仍可回报的原因就是
托管宿主自己的可启动性事实（`dsh_runtime_unavailable`、
`operator_credential_unconfigured`、`invalid_reasoning_effort`）；对不可用宿主的会话
请求会以 typed host-tool gate 失败，而不会静默回落到个人 CLI 登录。

| 路线 | 形态 | 代价与风险 |
| --- | --- | --- |
| A. turn-backed 管家传输（**已落地**） | 每个管家 chat turn 通过受治理 Turn 解析出的同一个执行档位，在托管宿主上执行一次有界受治理片段，把有界会话历史作为上下文 | 无双工流式、无跨 turn 宿主会话，每个 turn 都是新 segment；工具／沙箱权威由通道固定为只读，单 turn 上限即通道自身的硬超时 |
| B. ACP 或 stdio 适配 | 当托管宿主暴露此类接口时，复用 ACP stdio 适配路径（Kiro CLI chat 端点已走此路） | 传输成本最低，但依赖上游接口，目前没有已交付证据 |
| C. codex 端点绑定 operator provider | 让 Codex app-server 直接以 operator provider 启动，保留现有传输与工具面 | 保留流式，但必须证明会话不再以个人登录认证；provider 配置成为宿主状态权威，需要单独 gate |

选型规则：优先 A，因为它复用 LoopX 已经验证过的 Turn 权威、typed host failure、
journal 与配额语义；B 作为上游接口出现时的低成本替代；只有在管家体验确需双工
流式时才评估 C。无论采用哪条路线，都必须证明「管家所驱动的受治理工作不会落到个人
订阅上」，并且对于运维方显式放到托管宿主上的管家会话，「其模型工作落在 operator
凭据上」。管家自身出货默认值是交互式 CLI 端点，账落在某一台机器的登录上；这是一条
已披露的默认值而不是隐藏默认值，因为通道会回报端点、端点来源以及它背后的那条出货
决定。本文件不授权为此新增 scheduler、重试权限或第二套监控子系统。

落地的是路线 A，其证明是一条仓库 smoke 而不是真实会话原文：
`examples/loopx-steward-managed-chat-smoke.py` 用真实内置 dsh 片段对接本地 mock 模型
端点，断言解析出的绑定、真正上线的模型与档位、已持久化的回答，以及只读沙箱确实拒绝
一次写入。真实管家会话的人设与受众不进入本文件。

## 管家执行器的 Machine Configuration（2026-09-16）

此前管家执行器只能通过 Chat 服务环境变量选择，于是"本机决定"落在启动文件里，而不是
落在产品设置上：没有任何界面能展示它，也没有任何界面能修改它，读者必须知道当时进程
里有哪些变量。现在执行器、模型与推理档位是一个类型化的 machine-config 命名空间
`steward_executor`（`loopx/capabilities/steward_executor/machine_defaults.py`），
本机的管家选择因此成为一等公民。

该命名空间只有三个字段，且不保存任何凭据：

```json
{
  "schema_version": "steward_executor_machine_defaults_v0",
  "executor_endpoint": "codex",
  "executor_model": null,
  "executor_reasoning_effort": null
}
```

`executor_endpoint` 必填，且仅限 LoopX 作为通道执行器出货的端点；模型或推理档位留空
表示本机对该字段不做决定，通道继续从更低层解析。未知字段、未知 schema 版本、未列出
的端点、不支持的档位都会在任何生效前 fail closed。需要命名空间未列出适配器的运维方
仍然可以使用 `LOOPX_MANAGER_ENDPOINT`。

优先级只在通道所有者（`loopx/chat_manager.py`）声明一次：machine configuration、
服务环境变量、出货默认值。机器层才是产品界面拥有的那一层，因此
`loopx machine-config describe` 发布模板，前端通过既有的 revision 锁定事务编辑同一份
文档；通道回读新增 `executor_endpoint_source: machine_configuration` 以及该文档的
`status` 与 `configuration_revision`，无需读取存储即可区分"机器决定"与"服务环境值"。

本次不做改变的边界：出货默认值在每台机器上仍是 `codex`；凭据仍然只做认证、不做选择；
托管宿主仍然需要自己的凭据与 runtime；该选择不授予任何权限——它只命名一个由运维方
计费的 runtime，`manager_runtime` 仍是另一项机器决定。管家值损坏或存储不可读时，回落
到更低层并给出类型化原因（`configuration_invalid`、`unavailable`），而不是让人正在
对话的界面失败；**兄弟**命名空间损坏也不会改写有效的管家选择。

验证：`tests/capabilities/test_steward_executor_machine_defaults.py`、
`tests/test_manager_channel_binding.py`、`tests/test_chat_machine_configuration_api.py`、
`tests/capabilities/test_capability_configuration_ui.py`，以及
`examples/loopx-steward-channel-binding-smoke.py`。

### 管家回答身份与 Runtime 选择（2026-09-16）

机器可以声明管家执行器之后，Dashboard 仍在用一个值回答两个不同的问题：**谁在回答我**，
以及**哪个 runtime 跑了这一回合**。会话记录用聊天 Runtime 选择器当时持有的值给管家回答
署名；而选择器本身只要在本机发现 `Codex` 适配器就优先选它——于是解析到托管宿主的通道
仍可能把自己呈现成个人 CLI 登录，页头 chip、输入框与回答可以各说各的执行器。

管家通道上现在有两条规则：

* **回答身份署名说话者。** 会话记录在**所有**能产生管家回答的路径上署名
  `LoopX 管家` / `LoopX Manager`：交接回执、恢复的历史、恢复流式、流式占位、
  完成兜底、中断与失败。执行器与模型留在机器能力 chip 上，那才是回报它们的表面。
  Goal 通道继续署名该 Goal 自己的 Agent。
* **Runtime 选择按通道的解析方式解析。** 在管家上下文里，聊天 Runtime 选择器遵循通道
  属主的优先级：先看本机声明的管家执行器，只有本机什么都没声明时才回落到出货默认值。
  一个被发现的适配器永远不会被呈现成管家。

运维方的显式选择仍然在该上下文里优先；运维方没有选择时，客户端依旧不发送任何端点，
因此 `loopx/chat_server.py` 的建会话契约——"每个通道通过自己的属主解析自己的默认值"
——没有变化。显式选择也是这条通道离开已声明执行器的唯一路径，这让一个被发现的 CLI
不会静默改写一项机器决定。

证据：`examples/personal-workspace-browser-smoke.mjs`（`execution-chip` 场景覆盖选择器与
输入框的解析，`chat-recovery` 场景覆盖回答身份），以及在已安装 Dashboard 上的一次现场
回读：页头 chip 解析为 `dsh`，而选择器此前报的是 `Codex`。

## 管家团队入端口径（2026-09-16）

在 `43d362532`，团队入端已交付有界预览、业主确认与首批 Todo 物化；尚未验收正在运行、预算受约束或完整意图对齐的团队。[整体路线总纲](loopx-overall-roadmap-v0.zh-CN.md) 拥有跨 RFC 优先级及 F1–F7/R1–R7；本文保留 runtime 选型及其资格证据，不另建团队架构。

已交付边界为 `steward_team_plan_preview_v0`、kind `steward_team_plan_preview`，经 `loopx/control_plane/work_items/governed_transition_proposal.py` 与 Chat `team.plan` 分派。计划点名确切 Goal、1–8 条 lane、注册 Agent、首个 Todo 的 text/priority/class/action、acceptance、quota envelope 与 stop condition。校验结果 `applies: false`；预览不等于执行。

| 边界 | 已交付行为 | 剩余限制 |
| --- | --- | --- |
| 校验/准入（#4519/#4522/#4532/#4533） | 确切 Goal、注册 Agent、支持的 advancement kind、有界公开安全字段；按通道范围查询 Goal；缺少事实时丢弃提案并保留答案正文 | `ready` 只校验注册/action 支持，不证明执行器健康、工具资格或预算准入 |
| Staffing gap | 未注册 Agent 产生 `agent_not_registered` 并保留 `declined_first_todo`；显式 `capability_not_granted` / `audience_not_authorized` gap 不允许工作 | 存在这些 reason code 不证明全部 capability/audience 条件已经自动检测 |
| 物化（#4524/#4528/#4535/#4538） | 重新校验点名 Goal，逐 ready lane 调 canonical Todo owner；回执保存 proposal digest 与有界 `lane_todo_ids`，兼容旧回执且没有 monitor key | 确认的 priority 丢失；acceptance/quota/stop 未成为此路径的执行约束；没有整队原子提交或自动 partial recovery 证明 |
| 确认（#4547/#4548/#4552） | 现有前端展示 lanes/gaps 并提交 `team.plan`，bundle 与 browser fixture 已交付；产生计划的**管家会话本身也会列出该通道存入的卡片**，业主在说出这句话的地方即可确认，而为已选 Goal 拉取的提案仍留在该 Goal 工作区 | 此 fixture 未验收 Lark 或真实 worker 执行；确认回读在该 fixture 之后已修复（确认后的 lane 保留声明的优先级，部分落地返回缺口数量，无 lane 可组建的计划记为 typed failure） |
| 新鲜度 | registry bytes 变化会使 Chat preview stale；可选 `intent_basis` 在物化前读取 alignment source facts | commit 未绑定精确 Goal intent/授权/工作前置条件；`intent_basis` 不是完整意图修订或 CAS fence |

现有测试覆盖未变化计划的重复提交，不能推广到并发计划、多 lane 中断或中途 Todo 被编辑的情况。R1 通过实际 action/recovery 路径补这些资格。

最新集成检查点：#4569（`f1166e81e`）将不支持的 action kind 保留为 lane 级 gap，区分计划声明的 gap 与 host 判断，复验不把已确认的 gap 静默转为工作，并将 admitted plan 投影到本地 owner channel 卡片。一条 lane 无法承接不再拒绝整份计划；Lark manager audience 仍无对应卡片。这些修复不代表团队已执行，也未闭合基线 F1–F4 的承诺与恢复问题。

后续 #4572（`0aa6179de`）在 manager 回答中追加 channel-authored 的确认位置提示，指出目标 Goal 工作区；本地与远端 manager 均收到提示，Goal channel 不追加。远端提示不等于在 Lark 生成卡片，也不证明卡片写入或团队执行成功；原模型正文保留。

产生计划的管家会话现在也会列出它自己通道存入的那张卡片，而不只是提示里点名的 Goal 工作区。这是同一份已校验提案上的展示改动：不新增 Lark 卡片，确认前不创建任何东西，而为已选 Goal 拉取的提案仍留在该 Goal 工作区，因为它属于那个上下文。

**Lark 卡片的复用边界。** LoopX 已经交付了卡片确认的 Lark 半边：`loopx/extensions/lark/goal_channel_operation.py` 会把 `operation.execute` 类型提案投递成不可转发的 Card 2.0（确认/拒绝按钮），`event_collector_runtime` 消费 `card.action.trigger`，传输层负责操作者成员与租户校验、重放保护、卡片读回与结果卡片修补。可复用的是这套外壳加 `presentation.action_review_plan.compile`；操作特有的部分是 operation envelope 身份、claim/execute 效果，以及投递所解析的 Goal channel 绑定。现在 `compileReviewCardFrame` 也会为已校验的 `team.plan` 返回语言中立的 `review_card_frame_v0`——身份是提案加 apply 会重新校验的 state fingerprint，字段是 `{key, value}` 对、固定标签保持为 key——因此团队计划卡片可以复用同一套外壳与回调消费者。仍缺的是**提问受众的投递路由**（管家群不是 Goal channel 绑定）以及一个**走 Chat action service 应用提案、而非 claim operation envelope 的回调效果**。目前还不会投递任何计划卡片；要从 Lark 确认，还需要明确回答"外部管家受众是否可以做这次持久写入"。

### 与 multi-agent / shared authority 契约的关系

[对齐 RFC](shared-goal-alignment-and-governed-amendment-v0.zh-CN.md) 拥有共享意图及受治理修订；[共享权威 RFC](shared-goal-authority-state-provider-v0.zh-CN.md) 拥有持久化/晋升。团队计划只提议已接受 intent 内的工作，不能靠 envelope 散文修改权限、共享验收或终止条件。Stage 3 amendment commit 仍未交付。回执可选 `intent_basis` 是既有 `source_basis_digest`，不是尚未实现的完整 intent envelope 版本，不能通过改名赋予历史回执更强语义。

`peer_v1` 允许管家组织、委托和综合，但不授予单方写入、claim、优先权或抢占权。lanes 指向同一 canonical 图与 per-Agent frontier；创建带 `claimed_by` 的 Todo 不等于取得 lease、启动 worker 或预留分布式 quota。

[Peer directory](../../reference/protocols/peer-agent-directory-and-observation-v0.md) 供 manager 与 peer 共用。`loopx agent-directory --goal-id <goal> [--agent-id <caller>]` 复用 management projection，本地最多 24 行并报告省略数；没有分页、presence provider 或 lease epoch。传入未注册 caller 得到 scope gap。本地 CLI membership 检查不认证远端 caller；未来远端入口必须从可验证 binding 派生身份。

每 lane 通过 `loopx shared-goal-alignment --goal-id <goal> --agent-id <agent>` 回读，仍限 Stage 1/2 source-facts 语义，计划 receipt 不投影完整状态。[三层合同](../../reference/protocols/multi-agent-three-layer-minimality-v0.md) 与[可见 launcher](../../reference/protocols/multi-agent-visible-launcher-v0.md) 保留独立归属：用户意图、preset 流程、kernel 声明按 Goal/Agent/Todo 身份连接，不再建 runner、pane owner、vision budget 或 evidence loop。选择执行器和保存凭据都不授予这些效果。

## 按里程碑看管家通道的就绪度（2026-09-16）

管家通道同时消费本文的宿主选型与
[管家语义交接](./capable-manager-semantic-handoff-v0.zh-CN.md) 中的管家里程碑。
本节记录这些里程碑当前可以依赖管家通道的哪些行为、哪些仍未验证。这里只写产品
契约，不写会话内容：不记录真实会话原文、受众身份、带日期的具体事故，或 operator
本机路径。

| 里程碑 | 管家通道在范围内的契约 | 2026-09-16 的证据状态 |
| --- | --- | --- |
| 管家 M1 — 可用的宿主 agent | 通道解析并回报其生效执行器、模型、推理档位与来源，执行器选型不跟随凭据；无法启动的宿主以 typed reason 失败，而不是静默回落到个人登录 | 已交付：带来源与默认规则原因的已选端点、执行器的 `execution_profile`、`executor_kind`、`channel_binding` 读取（PR #4446 与 PR #4443 的 Turn 侧读取；无条件默认值与单段传输随本次变更落地）。上游**会话身份**尚未投影到通道，因此通道回答还无法证明是哪一次会话给出的 |
| 管家 M2 — 语义续接 | 跨所有已注册运行中 lane 的接收者解析；按来源的 typed 覆盖与新鲜度；报告可以先用目标级里程碑开头，而不是先给覆盖免责声明 | 部分已实现。typed 来源失败与一次真实来源读取已记录在下节；本地 peer directory 已交付。跨目录的 receiver 解析、完整语义请求/回传及可综合的目标级里程碑仍未闭合，不把 source read 等同于 M2 完成 |
| 管家 M3 — 自动完成一次交流 | 超出或违反通道出站文本契约的已保存回答，按稳定答案身份分片重发；含糊或失败的发送要协调而不是用本地提示替代；回传路径要能跨传输重启存活；富文本要渲染成结构化文本 | 部分缓解。`loopx/extensions/lark/outbound.py` 在超限或载荷不合法时 fail closed，通道只回报这个本地失败、不重新投递已保存的回答；一条回答没有幂等身份，重试可能重复发送；结构化渲染没有保证 |
| 宿主模式 M0-M1 | 通道的执行器选型与其有界单段执行 | 选型由 PR #4446 覆盖，Turn 侧选型由 PR #4443 覆盖；有界单段执行由上面的 Mode B 验收覆盖。通道本身现在经单段传输抵达托管宿主，因此托管宿主自己的单段执行已可从通道抵达；仍未提供的是跨 turn 宿主连续性——片段不是会话 |
| 宿主模式 M2-M3 | attached-host 对齐、typed 不可用，以及不做模式推断、不引入第二执行器的模式感知投影 | 部分已实现：通道的托管段传输为每个绑定只保留一个执行器，第二次启动以 typed `managed_host_chat_segment_in_flight` 拒绝，被中断段的回答会被丢弃而不会进入可见历史。通道读回也带上了模式感知投影：引用 Session 自己的 `session_mode` 与 `status`，没有 Session 的通道读作 `unbound`，闭集之外的模式命名为 `unrecognized`，而不是从已解析的执行器反推模式。仍未实现：attached-host 对齐；外部受众仍降级为 `restricted` |

### 远程来源覆盖的现场验收（2026-09-16）

M2 的 typed 来源失败已交付；以下保留一次历史真实通道读取的验收边界，本次路线审计未复跑：

- 声明了却读不到的远端来源，会回报 typed 原因与清除该原因的修复动作（授权过期、
  远端客户端缺失、远端协议不可用、主机不可达），而不是一句没有类型的不可用；
- 2026-09-16 在一次真实管家通道提问上完成验收：该问题需要其已声明的远端来源。
  发布版本 `20260916T123949Z`（服务中的修订 `55ebbc6b7`，执行器 `dsh`，
  profile `deepseek-v4-flash@high`）。回答点名了它实际读到的那一个已声明来源并保留
  该次读取的新鲜度，说明了自己的证据窗口与所施加的上限，列出纳入的远端行，并明确
  表示没有读到的主机属于覆盖之外，而不是把它们呈现成"没有进展"。

这正是 M2 行要求的"按来源的 typed 覆盖与新鲜度"。M2 的另外两半——跨已注册运行中
lane 的接收者解析、报告可先用的目标级里程碑——仍然开放。这次验收是一次真实通道读取：
它需要运行中的通道、真实凭据与已声明的来源，因此作为**记录下来的流程**而不是 CI 任务；
失败那一半还需要一个真正读不到的来源才能复现。

五行的两条边界固定不变：通道始终是同一个 manager Session 的入口与投影，不拥有
profile、权限状态、第二执行器或工作权威，因此更丰富的回答契约不得扩大通道可读或
可改的范围；本文也不提升任何一行的状态——M1-M4 接入里程碑与跨前端投影行仍归
[Agent 会话执行模式](./agent-session-execution-modes-v0.zh-CN.md)。
