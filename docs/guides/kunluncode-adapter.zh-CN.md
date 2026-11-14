# 在 LoopX 中使用 KunlunCode

[独立 HTML 版本](kunluncode-adapter.zh-CN.html)

本文说明如何把当前主机上的 KunlunCode 接入 LoopX，使用独立项目绑定、原生 Goal/Goal
Pro app-server 生命周期和可恢复的 LoopX writeback 完成长任务。示例统一使用 `uv` 管理
Python 环境。

## 1. 接入后得到什么

KunlunCode 是 LoopX 的一个一等 host surface，但它仍然只是执行器；LoopX 继续负责
goal、todo、claim、quota、边界和持久 writeback。

| 部件 | 职责 | 不会自动获得的能力 |
| --- | --- | --- |
| LoopX | 保存 goal/todo、决定 `should_run`、校验 Agent 身份、记录完成证据和后继任务 | 不替 KunlunCode 推理，也不授予发布或生产权限 |
| `loopx-kunluncode` | 连接项目、安装可选 MCP、添加任务、创建/恢复原生 Goal、验证终态并事务化回写 | 不创建常驻后台调度器 |
| KunlunCode | 运行 native Goal continuation、工具调用和 Goal Pro 独立 verifier | 不绕过 LoopX 的 quota、todo 或 authority boundary |

一次正常执行的生命周期是：

```text
LoopX should_run -> 选择并 claim todo
  -> app-server initialize
  -> thread/start 或 thread/resume
  -> thread/goal/set(mode=strict)
  -> turn/start + KunlunCode native auto-continuation
  -> thread/goal/get(status=complete, verification_passed)
  -> LoopX refresh-state -> todo complete -> quota spend
```

KunlunCode 使用独立的 `.loopx/kunluncode.json` 绑定和注册身份。它不会读取
`.claude/loop.md`，也不会冒充 Claude Code 的 `cc` lane；连接已有 goal 时会保留其他
已注册 Agent。

## 2. 三套命令空间：并不完全一致

接入后同时存在三套不同的入口。名称相似不代表状态或生命周期相通：

| 命令空间 | 例子 | 所有者 | 当前适配器如何使用 |
| --- | --- | --- | --- |
| LoopX shell CLI | `loopx ...`、`loopx-kunluncode ...` | LoopX 控制面 | 连接、添加 todo、启动原生 Goal 控制器、读取合并状态 |
| KunlunCode TUI slash | `/goal`、`/goal-pro`、`/plan`、`/mcp` | KunlunCode session runtime | 仍由 TUI 解析；适配器通过等价 app-server API 激活 Goal，不把 slash 文本塞进 prompt |
| LoopX MCP tools | `should_run`、`list_todos`、`claim_task`、`complete_task`、`review_task_vision` | `loopx-kunluncode` MCP server | 供交互/兼容模式使用；native run 期间写工具会失败关闭，由外层控制器独占 writeback |

### `/goal` 是 KunlunCode 原生 Goal

本机 KunlunCode 的 `/goal` 用法是：

```text
/goal [status|history|pause|resume|clear|edit [--budget <tokens>] <objective>
      |--budget <tokens> <objective>|<objective>]
```

它在 KunlunCode 自己的 session 中保存 persistent objective、continuation status 和 token
budget。这个状态不是 LoopX registry 中的 goal。直接在 TUI 启动的 native Goal 不会完成
LoopX todo；只有 `loopx-kunluncode run` 创建并跟踪的 native Goal，才会在验收终态后做映射。

### `/goal-pro` 是原生 `/goal` 加独立完成验证

从用户可见的目标语义看，`/goal-pro` 不是另一套目标生命周期。它沿用 `/goal` 的
persistent objective、`status`、`history`、`pause`、`resume`、`clear`、`edit` 和 token
budget；核心增量是严格的完成门禁：主 Agent 请求完成时，KunlunCode 会运行独立 verifier
sub-agent。只有 verifier 通过才会进入完成状态；验证失败或验证基础设施出错时，Goal 保持
active。

从产品语义看，你的理解是对的：`/goal-pro` 就是在普通 native Goal 生命周期上增加强制、
独立的完成验证。协议字段中普通 `/goal` 对应 `arrangement`，`/goal-pro` 对应 `strict`；
Strict 还约束主 Agent 的协调/委派方式，但真正决定能否完成的是 verifier gate。它仍然使用
KunlunCode 自己的 goal state，不是 LoopX goal 的“高级版”。

```text
/goal-pro [status|history|pause|resume|clear|edit [--budget <tokens>] <objective>
          |[--answer] [--budget <tokens>] [--] <objective>]
```

若 KunlunCode 提示 Goal continuation disabled，可在 TUI 中用
`/set goal_enabled true` 启用原生 Goal。这个开关只控制 KunlunCode 自己的 `/goal` 与
`/goal-pro`，不会启用、暂停或恢复 LoopX goal。

### 原生 Goal 由谁激活

`/goal` 本身由 KunlunCode TUI 解析，不是模型能够输入给自己的普通文本命令。不过本机
KunlunCode 还提供两条非 TUI 激活路径：

| 激活路径 | 调用者 | 约束 |
| --- | --- | --- |
| TUI `/goal ...` 或 `/goal-pro ...` | 人工操作者 | 明确选择原生 Goal；TUI 负责创建并激活 |
| 模型工具 `create_goal` | KunlunCode 模型 | 只有用户明确要求创建 persistent Goal 时才允许调用；不能由模型自行把普通任务升级为 Goal |
| app-server `thread/goal/set` | 桌面端或外部控制器 | 确定性写入 materialized thread 的 Goal；控制器仍需启动或恢复首个 turn |
| `loopx-kunluncode run` | LoopX 外层控制器 | 先由 LoopX 选择 todo，再调用 app-server；默认创建 `strict` Goal Pro |

Goal 被激活后，KunlunCode runtime 才会注入 continuation 指令并驱动后续 Goal turns。普通
headless prompt 不会因为任务看起来很长就自动获得 native Goal 生命周期。

### 当前适配器如何组合两个循环

默认 `loopx-kunluncode run` 让 LoopX 成为唯一外层 lifecycle owner，让 KunlunCode native
Goal Pro 成为该 todo 的内层长任务执行器：

```text
loopx-kunluncode run
  -> LoopX should_run + claim
  -> KunlunCode app-server thread/start|resume
  -> thread/goal/set(mode=strict) + turn/start
  -> native continuation + independent verifier
  -> verifier PASS + status=complete
  -> LoopX durable writeback + quota
```

它不会伪造 slash 文本，也不依赖模型自行判断何时调用 `create_goal`。控制器直接调用
`thread/goal/set`，KunlunCode runtime 随后自动注入 continuation。进程中断时，忽略的
`.loopx/kunluncode-runtime.json` 保存 thread/goal lineage；再次执行同一命令会恢复同一
thread，或继续完成已经通过 verifier 但尚未写完的 LoopX 事务。

推荐用法是：

- 需要 LoopX goal/todo/quota/writeback + KunlunCode 原生长任务：使用默认
  `loopx-kunluncode run --mode goal-pro`；不要为同一 todo 再手工启动第二个 native Goal；
- 需要 native Goal 但不要求独立 verifier：使用 `--mode goal`；
- 需要旧的一轮一进程 MCP worker：显式使用 `--mode headless`；
- 只想使用 KunlunCode 原生持久目标：直接使用 `/goal` 或 `/goal-pro`，但不要把其状态当作
  LoopX 已完成的证据；
- `/mcp` 只用于查看 KunlunCode 已加载的 MCP 工具；它不会把 LoopX CLI 变成 slash 命令；

不要同时启动适配器和另一个 TUI Goal 跑同一 todo；那会形成两个 native 执行器。适配器
已经完成外层 LoopX 与内层 Kunlun Goal 的唯一映射。

## 3. 前置条件

确认以下命令可用：

```bash
python3 --version
kunluncode --version
uv --version
git --version
```

你还需要：

- 一份包含 KunlunCode 适配器的 LoopX 发布包，或用于开发的源码 checkout；
- 一个要由 KunlunCode 操作的项目目录；
- 已经能正常启动的 KunlunCode provider 配置；
- 若要安装 MCP/使用 legacy headless，对 KunlunCode 用户级 MCP 配置有写权限。

本文用以下变量标识命令入口与目标项目。请替换为真实值：

```bash
export LOOPX_KUNLUN=loopx-kunluncode
export TARGET_PROJECT=/path/to/your-project
export LOOPX_GOAL_ID=my-long-running-goal
export KUNLUN_AGENT_ID=kunlun
```

不要把 token、provider 密钥或内部地址写进这些变量、文档、todo 或提交。

## 4. 安装适配器

普通用户直接安装发布包；`install` 会用 `uv` 创建独立的 MCP 环境，并安装与当前命令
相同版本的 LoopX 和固定版本 `mcp==1.28.1`：

```bash
python3 -m pip install --upgrade loopx
loopx-kunluncode install
loopx-kunluncode --help
```

贡献者才需要源码环境：

```bash
export LOOPX_SOURCE=/path/to/loopx
cd "$LOOPX_SOURCE"
uv venv .venv
uv pip install --python .venv/bin/python -e . 'mcp==1.28.1'
export LOOPX_KUNLUN="$LOOPX_SOURCE/.venv/bin/loopx-kunluncode"
"$LOOPX_KUNLUN" install --python "$LOOPX_SOURCE/.venv/bin/python"
```

验证命令入口：

```bash
"$LOOPX_KUNLUN" --help
kunluncode config get mcp_servers
```

帮助应列出：

```text
connect  install  uninstall  add  run  status
```

内置 provisioner 不会修改系统 Python；它只通过 `uv` 管理 adapter-owned MCP 环境。
发布包路径不要求用户持有 LoopX 源码 checkout。

## 5. 连接项目

### 5.1 新项目：同时创建 LoopX goal

目标项目还没有 `.loopx/registry.json` 时，`connect` 需要 `--objective`：

```bash
"$LOOPX_KUNLUN" connect \
  --project "$TARGET_PROJECT" \
  --goal-id "$LOOPX_GOAL_ID" \
  --agent-id "$KUNLUN_AGENT_ID" \
  --objective "持续完成该项目中经过验证的实现任务"
```

该命令会：

1. 在目标项目中 bootstrap 一个 LoopX goal；
2. 以 `peer_v1` 注册 `kunlun` Agent；
3. 在 registry 的 `agent_backends` 中加入 `kunluncode`；
4. 写入忽略的 `.loopx/kunluncode.json` 项目绑定；
5. 安装名为 `loopx-kunluncode` 的 KunlunCode 用户级 MCP entry。

### 5.2 已有 LoopX goal：保留其他 Agent

如果项目已经有 LoopX registry，使用其中真实存在的 goal id，不需要 `--objective`：

```bash
"$LOOPX_KUNLUN" connect \
  --project "$TARGET_PROJECT" \
  --goal-id "$LOOPX_GOAL_ID" \
  --agent-id "$KUNLUN_AGENT_ID"
```

连接过程会保留该 goal 里已有的 `cc`、Codex 或其他注册 Agent，再追加 KunlunCode
身份。不存在的 goal id 会失败关闭，不会静默绑定到第一个 goal。

### 5.3 只绑定项目，暂不安装 MCP

需要把主机配置变更拆开时：

```bash
"$LOOPX_KUNLUN" connect \
  --project "$TARGET_PROJECT" \
  --goal-id "$LOOPX_GOAL_ID" \
  --agent-id "$KUNLUN_AGENT_ID" \
  --skip-mcp

"$LOOPX_KUNLUN" install
```

可以先加 `--dry-run` 查看将使用的路径。若同名 MCP entry 不是 LoopX 管理的 entry，
安装器会拒绝覆盖；只有确认旧 entry 可以替换时才使用 `--replace`。替换新 entry 失败时，
安装器会恢复可重建的旧 command entry；无法安全恢复的 entry 会在删除前被拒绝。

默认 native `goal-pro`/`goal` 路径本身不依赖 MCP 完成回写，因此 `--skip-mcp` 后仍可运行；
MCP entry 用于 readback、交互使用和 `--mode headless` 兼容路径。

## 6. 验证连接

先从目标项目读取连接状态并验证可选 MCP：

```bash
"$LOOPX_KUNLUN" status --project "$TARGET_PROJECT"
kunluncode --cwd "$TARGET_PROJECT" mcp test loopx-kunluncode
```

源码贡献者还可以运行真实 app-server 协议 smoke：

```bash
"$LOOPX_SOURCE/.venv/bin/python" \
  "$LOOPX_SOURCE/examples/kunluncode-app-server-goal-pro-smoke.py" --require
```

通过时应看到 `protocol_version: 2026-07-27`、`mode: strict`、
`status: complete` 和 `verification_passed: true`，且 resumed goal id 与首次运行一致。
这个 smoke 只保留摘要，不记录原始输出、trajectory、凭据或绝对路径。

MCP 握手成功时应看到五个工具：

```text
should_run
list_todos
claim_task
complete_task
review_task_vision
```

`review_task_vision` 用于在原 Todo 完成后补齐遗漏的 vision 判断，不会重复完成
Todo 或扣减额度；native run 期间同样由外层控制器限制写入。

再读取 LoopX lane：

```bash
"$LOOPX_KUNLUN" status \
  --project "$TARGET_PROJECT"
```

重点检查：

| 字段 | 期望 |
| --- | --- |
| `ok` | `true` |
| `goal_id` | 你连接的 goal id |
| `agent_identity.agent_id` | `kunlun` 或你指定的 Agent id |
| `agent_identity.registered` | `true` |
| `scheduler_hint.execution_context.host_surface` | `kunluncode` |
| `scheduler_hint.execution_context.source` | `runtime_profile:kunluncode` |
| `should_run` | 当前确有可执行 todo 时为 `true`；没有工作时为 `false` 是正常结果 |
| `kunluncode_native_goal.phase` | 首次运行前可为空；运行后依次可能为 `native_active`、`native_verified`、`delivery_recorded`、`todo_completed`、`committed` |
| `kunluncode_native_goal.verification_passed` | 成功的 `goal-pro` 为 `true` |

如需查看 KunlunCode 保存的 MCP 配置，只做 readback：

```bash
kunluncode config get mcp_servers
```

当前 KunlunCode 把 MCP registration 放在用户配置中，而项目/Agent 选择仍由当前工作目录
及 `.loopx/kunluncode.json` 决定。因此同一个全局 MCP entry 可以服务多个已绑定项目，
但必须从正确项目目录启动或显式传入 `--cwd`。

## 7. 添加并运行任务

### 7.1 添加一个属于 KunlunCode 的 todo

任务文本应描述一个有明确完成条件的有界工作段：

```bash
"$LOOPX_KUNLUN" add \
  --project "$TARGET_PROJECT" \
  "修复配置解析问题，运行相关测试，并记录通过结果"
```

`add` 会把 todo 绑定并 claim 给当前 KunlunCode Agent。不要在 todo 中放原始日志、token、
客户信息或私有链接；使用紧凑、可验证的公开安全描述。

### 7.2 默认：运行原生 Goal Pro

默认模式是 `goal-pro`，权限模式是非交互的 `auto`：

```bash
"$LOOPX_KUNLUN" run \
  --project "$TARGET_PROJECT" \
  --mode goal-pro \
  --permission-mode auto \
  --max-duration-secs 600 \
  --controller-timeout-secs 3600
```

其中 `--max-duration-secs` 是 KunlunCode 单 turn 的软时间预算，
`--controller-timeout-secs` 是包含自动 continuation 在内的整个 native Goal 等待预算。
需要限制 native Goal 总 token 时再加 `--token-budget N`。

app-server 不能弹出 TUI 审批，因此 native 模式不接受 `ask`，会直接给出可操作错误而不是
挂起。若必须逐项人工确认，请直接在 KunlunCode TUI 使用 `/goal-pro`；那是独立原生运行，
不会自动回写 LoopX。

### 7.3 普通 Goal 与旧 headless 兼容模式

不需要独立 verifier 时：

```bash
"$LOOPX_KUNLUN" run \
  --project "$TARGET_PROJECT" \
  --mode goal
```

这会创建 `mode=arrangement` 的真实 native Goal。若必须保留旧版“一次调用只跑一个普通
headless turn，并由模型调用 MCP”的行为：

```bash
"$LOOPX_KUNLUN" run \
  --project "$TARGET_PROJECT" \
  --mode headless \
  --permission-mode auto
```

Legacy worker 仍要求模型在 `next_agent_todo` 与 `no_follow_up=true` 中二选一；这条规则不
适用于由外层事务控制器完成 writeback 的 native 模式。

默认 native Goal Pro 控制器按以下顺序工作：

1. 外层直接读取 `quota should-run`，只选择并 claim 当前 Agent 的一个 todo；
2. app-server `initialize` 后创建或恢复同一 thread；
3. 用 `thread/goal/set(mode=strict, requireNoGoal=true)` 激活 native Goal Pro；
4. 启动首个 turn，后续 continuation 由 KunlunCode runtime 自动驱动；
5. 每次 readback `thread/goal/get`，只有 `status=complete` 且存在
   `verification_passed` 才验收；
6. 依次写入 LoopX delivery record、todo completion 和 quota spend；
7. 每个阶段立即写本地事务 journal，重启时读取 evidence log 对账。

需要机器读取 KunlunCode 输出时加 `--json`；想预览启动命令而不执行时加 `--dry-run`。
中断后直接重跑完全相同的命令，不要手动新增同一 todo 或另开 `/goal-pro`。

### 7.4 什么时候继续下一轮

每轮结束后运行：

```bash
"$LOOPX_KUNLUN" status \
  --project "$TARGET_PROJECT"
```

- `should_run=true`：还有当前 Agent 可执行的工作，可以再调用一次 `run`；
- `should_run=false` 且 `effective_action=terminal_no_followup`：目标已明确收口，停止运行；
- `should_run=false` 且显示 wait、gate、quota 或 monitor 原因：按 packet 的具体原因处理，
  不要绕过 gate 反复调用模型；
- 身份、registry 或 health 错误：先修复连接，不要用另一个 Agent id 猜测重试。

## 8. 权限模式怎么选

| 模式 | 适用场景 | 注意事项 |
| --- | --- | --- |
| `ask` | 只适合 TUI 或 legacy headless 的人工流程 | native app-server 控制器不处理交互审批，因此会失败关闭 |
| `auto` | 默认 native Goal/Goal Pro | 非交互工具策略；仍不扩大 LoopX authority |
| `accept-edits` | KunlunCode 原生的编辑确认策略 | 仍应检查写入范围和 postcondition |
| `dont-ask` | 不希望出现交互提示且接受拒绝相关工具 | 可能让 native Goal 保持 active 或进入 blocked |
| `bypass` / `yolo` | 仅限用户明确授权、强隔离的环境 | 高风险；不能把模式本身当作生产或发布授权 |

先用 `status`、`--dry-run` 和协议 smoke 确认绑定，再在已有 authority boundary 内使用默认
`auto`。不要为了让任务“跑起来”而使用 `bypass` 或 `yolo`。

## 9. 身份、状态与安全边界

| 状态 | 位置 | 是否应提交 |
| --- | --- | --- |
| LoopX registry | `<project>/.loopx/registry.json` | 否；项目本地私有状态 |
| KunlunCode binding | `<project>/.loopx/kunluncode.json` | 否 |
| Native runtime journal | `<project>/.loopx/kunluncode-runtime.json` | 否；只存 opaque id、digest、摘要状态和回执 |
| Active goal state | `<project>/.loopx/goals/...` | 否 |
| MCP registration | KunlunCode 用户配置；用 `kunluncode config get mcp_servers` 回读 | 否 |
| 适配器 Python 环境 | uv 管理的 `.venv` | 否 |
| 公共产品文档和代码 | LoopX repository | 可以，在边界扫描后通过 PR 提交 |

关键规则：

- native Goal 激活不授予 repository write、push、publish、destructive、credential、外部 sink
  或 production 权限；selected todo、checkpointed write scope 与 KunlunCode permission mode
  仍同时生效；
- `claim_task` 和 `complete_task` 会校验请求 Agent 与绑定身份一致，冒充其他 lane 会失败；
- native run 期间 MCP `claim_task`/`complete_task`，以及通过 shell 直接发往当前绑定 goal 的
  LoopX lifecycle CLI 写命令，都会被外层所有权 guard 拒绝，防止模型在 verifier 前绕行提交；
- `goal-pro` 必须同时满足 native `complete` 和 `verification_passed`；只有之后才执行
  `refresh-state -> todo complete -> quota spend`；
- refresh-state 显式 suppress 外部 sinks；协议 proof 不保存原始 output 或 trajectory；
- 原始 transcript、长日志、provider 响应、密钥和本机绝对路径不得进入公共提交。

## 10. 常用维护命令

### 重新安装或刷新 MCP entry

升级发布包或源码环境搬家后，用当前命令入口刷新 adapter-owned MCP 环境和 entry：

```bash
"$LOOPX_KUNLUN" install

kunluncode --cwd "$TARGET_PROJECT" mcp test loopx-kunluncode
```

### 停止执行但保留状态

不再调用 `run` 即可。适配器没有自行常驻的后台进程；若中断时 native Goal 仍 active，
`.loopx/kunluncode-runtime.json` 会保留其 opaque thread/goal identity，之后运行同一命令即可
恢复。

### 卸载用户级 MCP entry

```bash
"$LOOPX_KUNLUN" uninstall
```

卸载器只删除能确认由 LoopX 管理的 `loopx-kunluncode` entry；遇到同名外部 entry 会拒绝
删除。native `goal`/`goal-pro` 使用 app-server 外层回写，不依赖 MCP entry，因此卸载 MCP
不会解除项目绑定或禁止 native run。

### 解除单个项目绑定

确认项目不再需要 KunlunCode lane 后，删除：

```text
<project>/.loopx/kunluncode.json
```

这不会删除 goal、todo、run history、其他 Agent、KunlunCode 持久 thread 或 MCP entry。
`.loopx/kunluncode-runtime.json` 只应在 `phase=committed` 后删除；若在 active 阶段删除，代表
你明确放弃恢复该 thread。若所有项目都不再使用，再执行 `uninstall`。

## 11. 故障排查

### `kunluncode is not on PATH`

先确认主机安装和 PATH：

```bash
command -v kunluncode
kunluncode --version
```

### `uv is required`

适配器拒绝修改系统 Python。安装 `uv` 后重新运行 install/connect，或显式传入一个已经
包含 LoopX 和 `mcp==1.28.1` 的 uv Python。

### Python 不能 import LoopX 或 MCP 版本不对

重新同步同一个环境：

```bash
cd "$LOOPX_SOURCE"
uv pip install --python .venv/bin/python -e . 'mcp==1.28.1'
```

然后用绝对 `--python` 路径重新执行 `install`。

### MCP test 无法连接或缺少工具

依次检查：

```bash
kunluncode config get mcp_servers
"$LOOPX_KUNLUN" install
kunluncode --cwd "$TARGET_PROJECT" mcp test loopx-kunluncode
```

不要把服务器脚本命名为 `mcp.py`，也不要手工把 `-m` 塞进 KunlunCode 的 `--args`；
适配器已经使用绝对 Python 与 `server.py` 路径规避命令行参数冲突。

### `agent_id does not match the host binding`

说明 MCP 请求身份与 `.loopx/kunluncode.json` 不一致。用期望的注册 Agent 重新 connect，
不要修改 MCP 请求去冒充另一个 Agent：

```bash
"$LOOPX_KUNLUN" connect \
  --project "$TARGET_PROJECT" \
  --goal-id "$LOOPX_GOAL_ID" \
  --agent-id "$KUNLUN_AGENT_ID"
```

### `goal-mode is not active`

从正确目标项目启动；确认 `.loopx/registry.json` 与 `.loopx/kunluncode.json` 存在并可读，
再重新执行 connect。

### 同名 MCP entry 被拒绝覆盖或删除

这是所有权保护。先用 `kunluncode config get mcp_servers` 检查 entry。仅当确认同名 entry
应由 LoopX 接管时，才在 install/connect 上加 `--replace`；卸载器不会删除外部 entry。

### `dont-ask` 下工具全部被拒绝

这是 KunlunCode 权限模式的预期行为。native 自动路径使用 `--permission-mode auto`，并保持
现有 authority boundary；需要逐项确认时改用 KunlunCode TUI，而不是给 app-server 使用
`ask`。

### `native app-server runs cannot service interactive approvals`

你给 native `goal`/`goal-pro` 传了 `--permission-mode ask`。app-server 没有可交互 TUI，
控制器会提前失败关闭。确认任务边界后使用 `auto`/其他非交互模式，或直接在 TUI 手动运行
`/goal-pro`。

### 中断、超时或进程退出后如何继续

先读状态：

```bash
"$LOOPX_KUNLUN" status --project "$TARGET_PROJECT"
```

若 `kunluncode_native_goal.phase` 是 `native_active`、`native_verified`、
`delivery_recorded` 或 `todo_completed`，直接重跑原命令。控制器会恢复相同 thread，或者只补齐
尚未完成的 LoopX 阶段；不要删除 journal、重复添加 todo 或手工启动第二个 Goal。

如果 app-server 明确无法恢复 journal 记录的 thread，控制器会失败关闭，不会静默创建第二个
Goal 重做同一任务。先确认旧执行器已经停止并检查持久 thread；只有在你明确放弃恢复后，才按
维护章节删除 runtime journal 并重新启动。

### KunlunCode 显示 complete，但 LoopX 没完成

对 `goal-pro`，仅有 `complete` 不够；readback 还必须包含 `verification_passed`。如果 verifier
失败、被拒绝或基础设施异常，控制器不会写 LoopX completion。修复 native Goal 中的验证问题
后重跑。如果已经 verifier PASS 而控制器在 writeback 中断，重跑会用 agent-scoped evidence
log 对账，并从缺失阶段继续。

### `should_run=false`

它不一定是故障。读取 `effective_action`、`reason`、`interaction_contract` 和
`scheduler_hint`：没有 todo、终态、quota、user gate 或 monitor cadence 都可能正确地
阻止本轮模型执行。

## 12. 完整验收清单

在称为“成功接入”前逐项确认：

- [ ] `uv --version` 正常；适配器与 `mcp==1.28.1` 在同一 uv 环境；
- [ ] `connect` 返回 `ok: true`，goal id 和 Agent id 正确；
- [ ] `.loopx/kunluncode.json` 未被 Git 跟踪；
- [ ] 连接已有 goal 时其他 registered agents 仍存在；
- [ ] 若安装了 MCP，`kunluncode mcp test loopx-kunluncode` 显示上述五个工具；
- [ ] `status` 显示 registered `kunlun`、host `kunluncode`、source
  `runtime_profile:kunluncode`；
- [ ] `examples/kunluncode-app-server-goal-pro-smoke.py --require` 证明 strict Goal、verifier PASS
  与跨进程 thread resume；
- [ ] 添加一个低风险 todo 后，真实默认 `run` 能 claim、创建/恢复 native Goal Pro，并只在
  verifier PASS 后完成 LoopX delivery/todo/quota writeback；
- [ ] `status.kunluncode_native_goal.phase=committed` 且
  `verification_passed=true`；
- [ ] 人为中断后重跑不会重复已经完成的 writeback 阶段；
- [ ] 错误 Agent id 会被拒绝；
- [ ] `ask` 不被误用为 app-server 自动审批模式；
- [ ] 终态或 gate 时停止重复运行；
- [ ] 凭据、私有状态、原始日志和本机路径没有进入公共提交。

## 13. 命令速查

```bash
# 连接或初始化项目
loopx-kunluncode connect --project DIR --goal-id ID [--agent-id ID] \
  [--objective TEXT] [--python PYTHON] [--skip-mcp] [--replace] [--dry-run]

# 单独安装 MCP
loopx-kunluncode install [--python PYTHON] [--replace] [--dry-run]

# 添加绑定到当前 KunlunCode Agent 的 todo
loopx-kunluncode add --project DIR TEXT...

# 默认：运行可恢复的原生 Goal Pro
loopx-kunluncode run --project DIR \
  [--mode goal-pro|goal|headless] \
  [--permission-mode ask|auto|accept-edits|dont-ask|bypass|yolo] \
  [--max-duration-secs N] [--controller-timeout-secs N] \
  [--token-budget N] [--json] [--dry-run]

# 读取 lane 状态
loopx-kunluncode status --project DIR

# 删除 LoopX 管理的用户级 MCP entry
loopx-kunluncode uninstall [--dry-run]
```

操作时始终以 `status`、native runtime journal 的摘要和 app-server/MCP readback 为准，不要
依赖上一轮聊天记录猜测当前 goal、todo、Agent 身份、verifier 或 gate。
