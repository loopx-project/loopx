# RFC：Calli 工作台与会话底座合入 LoopX（v0）

- **RFC 状态：** 已接受（合入后生效；合入前为待评审提案）
- **替代 / 关闭：** 无
- **交付成熟度：** Proposal
- **作者 / 负责人：** [@maxliux5](https://github.com/maxliux5)（[Calli](https://github.com/maxliux5/calli) 维护者，MIT）；目录位置、L0 包边界与合并授权由 LoopX maintainer 决定
- **创建日期：** 2026-11-14
- **最近规范性修订：** 2026-11-14
- **实现基线：** LoopX `80c694972`；Calli `87adcde`
- **相关契约：**
  [Monorepo 分发拆分](monorepo-distribution-split-v0.zh-CN.md)、
  [Agent 会话执行模式](agent-session-execution-modes-v0.zh-CN.md)、
  [桌面执行前端](desktop-execution-frontends-v0.zh-CN.md)、
  [App 会话与异步收件箱](app-conversation-and-async-inbox-v0.zh-CN.md)、
  [总体路线图](loopx-overall-roadmap-v0.zh-CN.md)、
  [状态数据契约](../../status-data-contract.md)、
  [算力额度](../../quota-allocation.md)、
  [设计系统](../../development/design.md)
- **语言镜像：** [English](calli-workbench-and-session-fabric-v0.md)

## 文档地图与维护约定

第 1–10 节是设计与验收契约，第 11 节是规范性交付计划，第 12 节是未决问题（文中给出的建议不等于批准）。附录不具规范性。本 RFC 合入后在仓库中的文件名建议为 `docs/architecture/rfcs/calli-workbench-and-session-fabric-v0.md` 及同名 `.zh-CN.md` 镜像，两者在同一个 PR 中提交并通过 `scripts/check_rfc_bilingual.py`。

---

## 1. 决策摘要

1. **什么成为权威、什么行为改变。** Calli 以两个独立单元进入 LoopX：一个**产品面**（Calli 工作台 UI，放在 `apps/` 下的独立目录）和一个**可选会话底座分发包**（SSH 多机、异构 CLI 挂接、额度感知交接，放在 `packages/` 下）。两者都不成为 Goal/Todo/Gate 的权威。
2. **什么不变。** `loopx-core` 仍然是 Goal、Todo、claim、lease、quota、effect、Gate 状态的唯一写入方。官方 dashboard、`/status.json` 契约与 `/api/chat/*` 不因本 RFC 改变行为。
3. **默认与 opt-in 边界。** 两个单元都默认关闭：只装 `loopx-core` 或只用官方 dashboard 的用户看不到任何差异。工作台是一个可单独构建、可单独启动的前端；会话底座通过现有 `extension.toml` 生命周期安装与卸载。
4. **主要安全与兼容约束。** 不出现「双权威」：同一事实只有一个所有者。Calli 自建的任务系统（`owner: me|agent` 待办表、`waitingOn`、自建交接状态机、团队/协作表）**不进入 LoopX**，其存量数据通过 LoopX 公开的 Todo dry-run/apply 接口一次性迁入。
5. **本 RFC 不批准的事项。** 不批准任何对 `loopx/` 核心的修改、不批准新增默认行为、不批准额度自动交接的默认开启；每个阶段的 `apps/**` 与 `packages/**` PR 仍需 maintainer 的显式合并授权。

## 2. 问题与动机

[Calli](https://github.com/maxliux5/calli)（MIT）是一个多 agent 工作台：在同一个界面里接入本机或远端已经安装的 Claude Code、Codex、pi、TRAE、zcode、Antigravity、DeepSeek Harness 等 CLI，跨多台机器查看和接手会话，并在某个账号额度接近耗尽时把工作交给另一个账号或模型继续。LoopX 已经拥有严格的治理内核（Goal、Todo、Gate、lease、turn fencing、receipt），并在推进 Managed Host 与 Attached Session 两种执行模式。

两者独立演进时，用户实际遇到的问题是：

- **同一件事有两本账。** 用户在 Calli 里建了一条「等 agent 回复」的待办，又在 LoopX 里有一条对应 Todo。两边完成状态、等待对象和责任人各自演化，最终用户不知道该信哪一个。
- **同一个人有两个管家。** Calli 的「需要你」提醒和 LoopX 的 Lark 管家会对同一个 Gate 各发一次通知，确认了一边，另一边仍然挂着。
- **同一台机器有两套多机模型。** LoopX dashboard 已有 `ssh_host_catalog_v0` 与 `/api/ssh-source/*`，Calli 另有 `hosts.ts` 的 SSH 隧道登记。机器名、在线状态和失败原因可能互相矛盾。
- **同一次交接有两种语义。** Calli 在额度阈值处切换账号续跑；LoopX 以 lease 和 turn fencing 保证同一 Todo 同一时刻只有一个执行者。如果 Calli 绕过 fencing 切换，可能出现两个执行者同时推进同一 Todo。

这些问题不能在 Calli 内部单独解决：只要 Calli 保留自己的任务状态，任何同步机制都只是在两个权威之间做调和。正确的做法是让 Calli 放弃这些状态的所有权，只保留 LoopX 当前没有、且属于宿主层的能力。

### 不变量

- **I1 单一权威。** Goal/Todo/Gate/lease/quota 决策只由 `loopx-core` 写入；工作台与会话底座只读投影、只经控制面 API 提交命令。
- **I2 UI 不做第二状态源。** 工作台不持久化任何 Goal/Todo/Gate 事实；本地只允许保存纯展示偏好（主题、栏宽、当前选中的机器），且删除后不影响任何业务结果。
- **I3 一个 Gate 一次确认。** 同一个 Gate 在工作台和 Lark 管家里显示同一条记录；任一面确认后，另一面在下一次投影刷新时消失。
- **I4 交接受 fencing 约束。** 任何账号或模型切换都必须先在当前 turn 的 fence 处结束，再由新会话重新获得 lease；同一 Todo 不会同时有两个活跃执行者。
- **I5 默认关闭等价。** 未安装或未启用时，LoopX 的行为、CLI 输出与 dashboard 渲染与基线逐字节一致。
- **I6 宿主不越权。** 会话底座不修改各 agent 的配置、目录和会话文件，不向 agent 注入 prompt 或工具（沿用 Calli 现有原则与 LoopX Attached Session 的「外部宿主保留进程所有权」约定）。

## 3. 范围与非目标

### 范围内

本 RFC 用四层组织合入内容。这四层与 [Monorepo 分发拆分 §5](monorepo-distribution-split-v0.zh-CN.md#5-提议的架构) 中已有的所有权表一一对应，不引入新的层级概念：

| 层 | 对应现有所有权行 | Calli 带来的内容 | 去向 |
| --- | --- | --- | --- |
| **L0 宿主与会话底座** | Host | SSH 多机连接与隧道生命周期；异构 CLI 无侵入挂接（覆盖本机与远端的原生对话线程）；多账号额度感知交接 | `packages/loopx-session-fabric/`，与 Managed Host 拼成完整会话底座 |
| **L1 最小治理内核** | Governance kernel | **无**。Calli 的任务、交接状态机和团队协作全部删除 | 只使用 `loopx-core` 现有能力 |
| **L2 能力包** | Capability strategy | 无新增；Calli 现有的科研向扩展不进核心，默认对普通用户隐藏 | 维持现状，另行按能力包流程提交 |
| **L3 统一界面** | Extension and product surface | 三栏工作台：顶部机器切换、底部「宿主 + 机器 + 模型」启动条、右侧「需要你 / 在跑 / 出错」Triage 抽屉 | `apps/presentation/calli-workbench/`，与 Lark 管家共用同一 Inbox/Gate 投影 |

### 非目标

- **不重建 L1。** 不引入第二个任务表、第二个交接状态机、第二套团队或协作模型，也不把 Calli 的 `todos`、`todo_threads`、`todo_notes`、`todo_proposals`、`sections`、`wishes`、`todo_summaries`、`teams`、`coordination` 表搬进 LoopX。
- **不做第二个状态权威。** 工作台不缓存、不推断、不合成 Goal/Todo/Gate 状态。投影里没有的字段，以附加字段的方式先进入 LoopX 的状态契约，再由工作台读取。
- **不搬 Calli daemon。** Calli 的 Node daemon（上百条 `/api/*` 路由、SSE、SQLite）作为整体不进入 LoopX；只有 L0 模块经逐个审计后以包的形式移植。
- **不整体搬运 Calli 前端。** Calli web 约 4 万行，只有工作台外壳、Triage 抽屉、机器切换、启动条和会话视图进入；skills/MCP 资源管理、宠物、语音等外围功能不在本 RFC 范围内。
- **不替换官方 dashboard。** 两个前端并存，读取同一套投影；是否在未来合并由 maintainer 依据使用数据另行决定。
- **不改变 LoopX 的默认执行器、默认 provider 或默认额度策略。**

## 4. 当前系统契约（基线事实）

**LoopX（`80c694972`）**

- `apps/` 是产品面，`packages/` 是可选分发，`loopx/` 是核心。[`apps/presentation/README.md`](../../../apps/presentation/README.md) 约定：此目录下的应用只消费公开安全的投影与 fixture，不拥有控制面状态，也不直接写 LoopX registry。
- 官方 dashboard（React 19 / Vite 8）读取 `/status.json`，以 zod 契约校验（`apps/presentation/dashboard/src/data/status.ts`），并通过 `status_contract.schema_version` 与 `minimum_dashboard_schema_version` 判断兼容性。
- 写操作已经有公开入口：`/api/chat/todo/dry-run` 与 `/api/chat/todo/apply`（Todo 预览与提交）、`/api/actions/*`、`/api/chat/request-control`、`/api/chat/codex-attached`（Attached Session）、`/api/ssh-source/*` 与 `/ssh-hosts`（`ssh_host_catalog_v0`）。
- 会话执行模式由 [Agent 会话执行模式](agent-session-execution-modes-v0.zh-CN.md) 定义：Attached 模式由外部宿主保留进程与会话所有权；Managed 模式由 LoopX 启动并以 `loopx_turn_v0` 事务推进。
- 额度观测见 [算力额度](../../quota-allocation.md)：`loopx quota status/plan` 是按 Goal 过滤的只读观测。

**Calli（`87adcde`）**

- `packages/web`：React 18 / Vite 5 / 自写 CSS。三栏布局、机器切换条、启动条和 Triage 抽屉已经实现。
- `packages/daemon`：Node 后端，SQLite 存储，目前是 Calli 所有状态的拥有者。其中**属于 L0 的**：`hosts.ts`（SSH 隧道登记、退避重连、失败分类 `no_ssh/host_key/auth/unreachable/ssh_failed/daemon_missing/forward_denied`）、`adapters/*`（codex、claude、pi、traex、zcode、agy、dsh、acp 等）、`auto-handover.ts` 与 `quota-resume.ts`（额度窗口、阈值、跨账号顺序、额度恢复后切回）。**属于 L1、需要删除的**：`todos` 系列表、`teams`、`coordination`、自建交接状态机。
- `packages/ext-loopx`：stdio JSON-RPC 插件，经 `loopx status --format json` 读取 `worksource.snapshot`。源码以 `version === '1.4.2'` 判定兼容，与当前 LoopX 2.0 不匹配。

## 5. 提议架构

### 5.1 目录与放置

用户层面的硬要求是：Calli 界面单独一个目录，与 LoopX 核心目录明确分开。建议如下（**最终位置由 maintainer 决定，见 §12 D1**）：

```text
apps/presentation/
  dashboard/            # 官方 dashboard，不变
  calli-workbench/      # 新增：Calli 三栏工作台（L3）
    package.json        # 独立构建；React/Vite 版本与 dashboard 对齐
    src/data/           # 只放投影 schema 与只读查询；不放任何状态存储
    smoke/              # fixture 驱动的浏览器 smoke
    README.md
packages/
  loopx-session-fabric/ # 新增：L0 会话底座（可选分发，默认关闭）
    extension.toml
    src/hosts/          # SSH 多机：作为 ssh_host_catalog_v0 的 provider
    src/attached/       # 各 CLI 的 Attached 适配器，一个适配器一个 PR
    src/quota/          # 额度窗口观测与交接建议
```

理由：

- **符合现有分层。** 工作台只渲染投影、提交命令，正是 `apps/presentation/` 的定义；会话底座是可独立安装、可卸载的宿主能力，正是 `packages/` 的定义。两者都不碰 `loopx/`。
- **便于审核与回滚。** 每个目录有独立的 `package.json` 和 CODEOWNERS 行，可以按目录授予或撤回合并权限；删除一个目录就是完整回滚。
- **与官方 dashboard 并列而不是嵌入。** 嵌入 dashboard 会让两套交互模型在同一路由树里交叉，也违背「单独目录」的要求。

备选位置：`apps/calli-workbench/`（与 `apps/presentation/` 平级，隔离更强但脱离「展示应用」分组）；将 L0 拆为多个包（如 `packages/loopx-host-ssh/`、`packages/loopx-attached-<cli>/`）。

### 5.2 所有权与权威

| 事实 | 唯一所有者 | 工作台如何得到 | 工作台如何修改 |
| --- | --- | --- | --- |
| Goal / Todo / claim / Gate | `loopx-core` | `/status.json` 及其子投影 | `/api/chat/todo/dry-run` → `/api/chat/todo/apply`；`/api/actions/*`；`/api/chat/request-control` |
| 会话消息、流式输出 | 执行宿主（Attached 时为外部 CLI；Managed 时为 LoopX） | `/api/chat/sessions/*` | 同一接口发送 |
| Inbox / 需要人处理的 Gate | `loopx-core` | 与 Lark 管家相同的投影 | 同一个确认命令 |
| 机器拓扑与连接状态 | 会话底座（L0） | 以 `ssh_host_catalog_v0` 及其附加字段公开 | `/api/ssh-source/*` |
| 额度窗口观测 | 会话底座（L0），作为 quota 的观测 provider | 附加到额度观测投影 | 不可写，只可由用户在 Gate 中确认交接 |
| 主题、栏宽、当前机器 | 浏览器本地 | `localStorage` | 本地 |

被禁止的替代权威：工作台的本地状态、会话底座的私有 SQLite、Calli daemon 的任何表，都不得被读作 Todo 是否完成、Gate 是否已处理、谁持有 lease 的依据。

### 5.3 工作台改造为「LoopX 投影消费者」

- **读取。** 工作台的数据层只由 zod schema 和只读查询组成，复用或镜像 dashboard 的 `status.ts` 契约；启动时校验 `status_contract.schema_version`，不兼容时显示「需要更新本机 LoopX 服务」而不是尝试猜测。
- **Triage 抽屉的三组完全由投影派生：**
  - 「需要你」= 投影中待人处理的 Gate、待决定事项与 Inbox 条目；
  - 「在跑」= 持有有效 lease 的 Todo 与活跃会话；
  - 「出错」= 失败或被阻塞的 receipt、离线机器、额度耗尽的会话。
  如果现有投影缺少某个分组需要的字段，先单独提交一个对状态契约的**附加字段** PR（依 [RFC README](README.md#when-a-change-needs-an-rfc)，附加字段不需要 RFC），工作台再读取它。工作台自己不做任何「推断完成」。
- **写入。** 所有改变 Goal/Todo/Gate 的操作都先调用 dry-run 预览，再 apply，并读回投影确认结果。失败或冲突时展示控制面返回的类型化错误，不在本地乐观更新业务状态。
- **设计系统。** 遵循 [LoopX 设计系统](../../development/design.md)：Geist 字体、黑白细线面、克制的强调色、设计过的空态/加载态/错误态和状态字形；Calli 自写 CSS 迁移为 LoopX 的 CSS 自定义属性，并通过 `check:css-custom-properties` 同类检查。每个 UI PR 附公开安全的截图，并按该文档的 Populated Page Review 自查。React 与 Vite 升级到与 dashboard 一致的版本。

### 5.4 与 Lark 管家共用 Inbox/Gate 投影

工作台的「需要你」与 Lark 管家读取的是同一个 Inbox/Gate 投影，确认走同一个控制面命令，并带同一个幂等标识。工作台不发自己的通知；需要提醒时只显示投影里已有的条目。由此满足 I3：在 Lark 里确认的 Gate，工作台在下一次投影刷新时移除；反之亦然。

### 5.5 L0 会话底座

**SSH 多机：并入现有模型，而不是并列一套。** LoopX 已有 `ssh_host_catalog_v0` 与 `/api/ssh-source/*`。会话底座以 provider 的方式为它补充 Calli 已经验证过的能力：Unix socket 隧道、`BatchMode=yes`、不替用户接受未知主机密钥、2 秒到 2 分钟的退避重连、15 秒健康检查和类型化失败码。机器的身份与别名仍以 `ssh_host_catalog_v0` 为准，Calli 的 `hosts.json` 不再作为来源。远端访问方式保持可替换：本地 SSH 是第一个实现，未来的云端或网关来源按 [路线图 R6 本地/云端收敛](loopx-overall-roadmap-v0.zh-CN.md) 接入同一个接口。

**异构 CLI 挂接：对应 Attached Session。** 每个 Calli 适配器移植为一个 Attached 宿主适配器，遵守 [Agent 会话执行模式](agent-session-execution-modes-v0.zh-CN.md) 的准入契约：外部 CLI 保留进程、会话和中断所有权；LoopX 记录绑定和 turn 边界。适配器之间互不依赖，一个 PR 移植一个，从已有 LoopX 对应物的适配器开始（Codex、pi、dsh）。Managed Host 负责的进程启动、worktree 隔离与 turn fencing 不在本包中重复实现；本包只在 Attached 一侧补齐，两侧拼成完整的会话底座。

**额度感知交接：从「执行动作」改为「受治理的提议」。** Calli 现有策略是按可配置阈值行动（例如约 85% 时压缩上下文并切到更便宜的模型，约 90% 时跨账号交接，额度恢复后切回）。合入后：

1. 会话底座只负责**观测**：把每个宿主账号的额度窗口（已用百分比、重置时间、适用模型）作为 quota 观测 provider 上报，不对外暴露账号凭据或账号身份，只使用不透明的 profile 标识。
2. 是否切换由 LoopX 的额度与准入规则**决定**；切换在当前 turn 的 fence 处发生：旧会话结束本 turn → 释放 lease → 新会话以新的 profile 重新获得同一 Todo 的 lease → 由 receipt 记录交接。
3. 默认关闭；开启时每次切换在 Inbox 里留下可审计的记录；用户可以要求「切换前先问我」，此时它就是一个普通 Gate。
4. 「额度恢复后切回」同样走一次完整的 fence → lease 流程，不允许静默切换。

### 5.6 `ext-loopx` 插件：从版本硬编码改为契约探测

`ext-loopx` 只服务于继续单独使用 Calli 的用户（合入 LoopX 的工作台不需要它）。改动：

- 删除 `version === '1.4.2'` 判定；改为读取 `loopx status --format json` 的 `status_contract.schema_version`，与插件声明的已知区间比较。
- 结果分三类：`compatible`（正常显示）、`newer_unverified`（以只读方式显示，并标注「未验证的较新契约」）、`incompatible`（只显示原因，不解析）。
- 可选能力（例如 Inbox 投影、额度观测）按字段是否存在逐项启用，不再按版本号推断。
- 补充契约测试：1.4.2 fixture、2.0 fixture、缺字段 fixture、未知 schema 版本 fixture。

此项改动在 Calli 仓库内完成，不进入 LoopX PR。

### 5.7 命令生命周期与失败处理

- 所有写命令带幂等键；超时或连接中断后先读回投影判断是否已生效，再决定是否重试。
- 投影过期（超过刷新周期）时，工作台把相关区域标为「可能过时」，并禁用依赖该数据的写操作。
- 会话底座崩溃不影响 `loopx-core`：Attached 会话所有权本来就在外部 CLI，底座重启后按 `ssh_host_catalog_v0` 与绑定记录重新挂接。

## 6. 备选方案与取舍

| 方案 | 结论 | 原因 |
| --- | --- | --- |
| A. Calli 继续独立，只通过 `ext-loopx` 读 LoopX | 拒绝作为终态 | 双权威问题依旧存在，同步永远是调和 |
| B. 把 Calli daemon 整体搬进 LoopX，再逐步收敛 | 拒绝 | 合入当天即引入第二个状态权威，违反 I1；审核面过大 |
| C. 工作台作为 dashboard 内的一个路由 | 拒绝作为默认 | 违背单独目录要求；两套交互模型混在一个路由树中 |
| D. **独立前端目录 + 可选 L0 包 + 删除 Calli L1** | **采纳** | 满足 I1–I6；每一步可单独合并、单独回滚 |

## 7. 安全、隐私与兼容

- **默认关闭等价（I5）：** 两个目录不进入 `loopx-core` 的启动闭包；CI 增加一项检查，确认未安装时 `loopx status` 输出不变。
- **凭据边界：** SSH 凭据与主机密钥策略只在运行用户的 OpenSSH 中；会话底座不保存密钥、不转发浏览器 token。各 agent 的账号凭据不进入投影、日志或 receipt。
- **公开与私有边界：** fixture、截图和测试数据全部合成，不含真实会话、真实路径或真实账号；遵守 [公开与私有边界](../../public-private-boundary.md)。
- **许可：** Calli 为 MIT，LoopX 为 Apache-2.0。移植的代码由作者以 Apache-2.0 重新提交并附 DCO 签名；其他贡献者的代码需取得其签名或重写（见 §12 D5）。
- **混合版本：** 工作台通过 `status_contract.schema_version` 拒绝不兼容的服务端；会话底座在 `extension.toml` 中声明最低 `loopx-core` 版本。

## 8. 迁移与回滚

**Calli 现有用户**

1. 独立版 Calli 继续可用，`ext-loopx` 改为契约探测（§5.6）。
2. 提供一次性导出工具：把 Calli 的待办、备注和关联线程导出为 LoopX Todo 提案，**先 dry-run**，由用户逐项确认后经 `/api/chat/todo/apply` 写入，最后读回投影核对条数和文字。`owner: me|agent` 映射为 Todo 的负责方，`waitingOn` 映射为 Gate 或等待说明，二者的映射规则写在迁移工具的 README 里。
3. 导入成功后，Calli 的旧表变为只读并在界面标注「已迁入 LoopX」；用户明确确认后才删除。导出文件保留在用户本机，不上传。

**Calli daemon 状态表的收敛路线**

| 表 / 模块 | 去向 |
| --- | --- |
| `todos`、`todo_threads`、`todo_notes`、`todo_proposals`、`sections`、`wishes`、`todo_summaries` | 迁入 LoopX Todo 后删除 |
| `teams`、`coordination`、自建交接状态机 | 删除；由 LoopX Goal 协作与语义交接替代 |
| `schedules`、`schedule_runs` | 待定（§12 D4）：并入 LoopX 调度，或保持为纯宿主层的定时触发 |
| `hosts.json`、隧道 | 并入 `ssh_host_catalog_v0` provider |
| 额度窗口、交接设置 | 作为 L0 观测与用户偏好；交接决策移交 LoopX |
| 线程、事件、turn 文本 | Attached 时属于外部 CLI；工作台通过 `/api/chat/sessions/*` 读取 |

**回滚：** 每个阶段的 PR 只新增目录或附加字段，回滚就是 revert 该 PR。迁移工具写入的 Todo 是普通 LoopX Todo，不依赖 Calli 存在；旧表在用户确认删除前一直保留，所以迁移回滚只需停用导入结果。

## 9. 验证与验收

| 声明 | 测试或证据 | 要求结果 | 不证明什么 |
| --- | --- | --- | --- |
| 默认关闭等价 | 未安装两个目录时运行 `loopx status --format json` 与 dashboard 构建，与基线对比 | 输出一致 | 不证明启用后的行为 |
| 工作台无第二状态源 | 静态检查：`calli-workbench/src` 中无对 Goal/Todo/Gate 的持久化写入；清空浏览器存储后重新加载 | 页面内容与投影一致 | 不证明服务端正确 |
| 写操作只走控制面 | 浏览器 smoke：拦截网络，所有修改请求只命中 dry-run/apply/actions/request-control | 无其他写端点 | 不覆盖未来新增入口 |
| 一个 Gate 一次确认（I3） | fixture 驱动：在 Lark 侧确认同一 Gate 后刷新工作台 | 「需要你」中该条目消失 | 不证明真实 Lark 网络链路 |
| 交接受 fencing 约束（I4） | 两个模拟 profile，额度越过阈值时触发交接；检查 lease 与 receipt 序列 | 任意时刻同一 Todo 至多一个 lease；有交接 receipt | 不证明真实账号额度接口准确 |
| SSH 多机单一模型 | 同一台机器只在 `ssh_host_catalog_v0` 中出现一次；七种失败码各有 fixture | 无重复机器；失败码与界面文案一一对应 | 不替代真实机器部署验收 |
| `ext-loopx` 契约探测 | 1.4.2、2.0、缺字段、未知版本四个 fixture | 分别为 compatible / compatible / 只读降级 / incompatible | 不证明所有未来版本 |
| 设计一致 | 按设计系统的 Populated Page Review 提交桌面与手机、亮色与暗色截图 | 评审通过 | 截图不等于可用性研究 |
| 迁移可读回 | 合成 Calli 数据库 → dry-run → apply → 读回 | 条数与文字一致；重复执行幂等 | 不覆盖用户自定义字段 |

确定性测试、真实环境验收、性能数据与默认开启分开报告；未运行或跳过的行不算通过。

## 10. 运行契约

- **可观测性：** 工作台在页脚显示投影来源、`schema_version` 与新鲜度；会话底座通过现有 doctor 输出连接状态和失败码。
- **类型化失败：** SSH 失败码沿用 Calli 已验证的七类；额度观测缺失时标为「未知」，不做切换。
- **容量：** 每台机器一个 SSH 子进程；投影轮询沿用 dashboard 的频率，不另起高频轮询。
- **升级与降级：** 工作台与 `loopx-core` 通过 schema 版本协商；会话底座可以单独卸载，卸载后 Attached 会话仍由各 CLI 保有。

## 11. 规范性交付计划

每个里程碑都是独立、可合并、可回滚的小 PR（或一组小 PR），使用 `codex/` 分支和独立 worktree，每个 commit 带 `Signed-off-by`。

| 里程碑 | 交付行为 | 进入条件 | 退出证据 | 回滚 |
| --- | --- | --- | --- | --- |
| **M0 RFC** | 本 RFC 中英文一对 | — | `check_rfc_bilingual.py` 通过；maintainer 对 §12 D1–D3 给出决定 | revert |
| **M1 工作台外壳（最快路径）** | `apps/presentation/calli-workbench/`：三栏布局、机器切换、启动条、会话视图；只读 `/status.json` 与 `/api/chat/sessions/*`；不改 `loopx/` | M0 合入；D1 已定 | fixture smoke、构建、截图、无第二状态源检查 | 删除目录 |
| **M2 Triage 与写入** | 「需要你 / 在跑 / 出错」抽屉；Todo/Gate 写入走 dry-run/apply；与 Lark 管家共用 Inbox；必要时先提交状态契约附加字段的独立 PR | M1 合入 | I3 smoke；写端点拦截 smoke | revert；附加字段可保留 |
| **M3 SSH 多机 provider** | `packages/loopx-session-fabric/` 首个模块：为 `ssh_host_catalog_v0` 提供隧道、重连与失败分类 | M1 合入；D2 已定 | 七类失败码 fixture；单一机器模型检查 | 卸载包 |
| **M4 Attached 适配器** | 每个 CLI 一个 PR，按 Codex → pi → dsh → 其他顺序 | M3 合入 | 每个适配器的绑定/detach/turn 边界 smoke | 卸载单个适配器 |
| **M5 额度观测与受治理交接** | 额度窗口作为 quota 观测 provider；交接按 fence → lease → receipt；默认关闭 | M4 至少一个适配器合入；maintainer 对额度策略授权 | I4 smoke；默认关闭等价 | 关闭开关；卸载模块 |
| **M6 Calli 数据迁入与 L1 退役** | 一次性迁移工具；Calli 独立版删除任务、团队、协作表；`ext-loopx` 契约探测 | M2 合入 | 迁移读回测试；Calli 侧回归 | 旧表在确认删除前保留 |

M1 是「最快可用」的切面：只要 M0 合入并确定目录位置，M1 即可独立评审，不需要等待任何核心改动。

## 12. 待决事项

| 编号 | 问题 | 决定人 | 选项 | 建议 | 依赖 |
| --- | --- | --- | --- | --- | --- |
| D1 | 工作台目录位置 | maintainer | `apps/presentation/calli-workbench/` / `apps/calli-workbench/` | 前者，与 dashboard 并列 | M1 |
| D2 | L0 包边界 | maintainer | 一个 `loopx-session-fabric` 包 / 按能力拆为多个包 | 先一个包、内部按目录分模块，等第二个使用方出现再拆 | M3 |
| D3 | 命名 | maintainer | 是否保留「Calli」作为产品名，或改为中性名称（如「LoopX Workbench」） | 目录用 `calli-workbench` 便于溯源；界面名称由 maintainer 定 | M1 |
| D4 | 定时任务归属 | maintainer | 并入 LoopX 调度 / 留在宿主层 | 并入 LoopX 调度，避免第二个调度器 | M6 |
| D5 | 许可与历史 | 作者 + maintainer | 带历史合入 / 以新提交重新贡献 | 以新提交重新贡献，保持 LoopX 历史干净、DCO 完整 | M1 |
| D6 | 与官方 dashboard 的长期关系 | maintainer | 长期并存 / 未来合并组件 | 先并存，以使用数据决定 | M2 之后 |

建议在相应决策记录进入附录 B 之前均不具约束力。

---

## 附录 A：执行记录（非规范）

合入后在 `ledger/calli-workbench-and-session-fabric-v0/` 下记录。

## 附录 B：决策记录

| 日期 | 决定 | 负责人 / 批准 | 备选 | 修改的规范章节 |
| --- | --- | --- | --- | --- |
| — | — | — | — | — |
