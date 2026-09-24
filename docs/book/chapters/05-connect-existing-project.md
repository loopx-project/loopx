# 连接你的 Git 项目

项目接入是一条独立实践路径。你不需要修改 LoopX Kernel，也不需要先开发 Extension。本章先建立
项目状态和 Git 边界；后两章再分别从 Codex App 和 Codex CLI 启动。

推荐做法是把接入任务直接交给当前 Agent。你负责给出目标、Host 和权限边界，Agent 负责检查
仓库、读取当前 LoopX 命令表面、执行安全的接入步骤并返回可验收的报告。手动命令仍然重要，
但主要用于理解 Agent 做了什么、复核结果和恢复失败。

!!! tip "快速阅读路线"
    只想完成基础接入：按第 1–6 节执行，到“验证 Git 隔离”即可结束。只有项目确实需要可选
    Capability 或 Extension 时，再读第 7 节。

## 成功标准

完成后，你应该能观察到：

- `loopx doctor` 报告安装可用；
- 项目存在 `.loopx/registry.json`；
- 项目存在 `.codex/goals/<goal-id>/ACTIVE_GOAL_STATE.md`；
- `loopx status` 能显示 active state 和当前 frontier；首连不会生成 onboarding todo，
  第一个交付 todo 由 Agent 与你确认后写入；
- `.loopx/` 与 `.codex/goals/` 不会进入 Git；
- 再次连接会按精确 `goal_id` 复用已有 Goal，而不是覆盖目标；
- 新接入的执行者使用 fresh `agent_id`，除非用户明确授权 takeover。

这些本地文件是控制面状态，不是项目源码。不要把它们提交到公开仓库。

## 1. 让 Agent 帮你接入

在目标仓库根目录打开你正在使用的 Agent 开发工具，把下面提示词中的目标和 Host 改成自己的
情况后直接发送：

```text
请把当前 Git 项目安全接入 LoopX。

目标：
- 为这个项目建立一条可恢复、可验证的发布流程。
- 当前 Host 是 Codex App。如果当前环境不是这个 Host，先告诉我，不要猜测。

执行合同：
1. 先只读检查项目根目录、当前分支、git status、.gitignore，以及是否已有
   .loopx/registry.json、.codex/goals/ 或其他 LoopX 状态。不要覆盖、reset 或清理现有内容。
2. 运行 loopx --version、loopx doctor，并读取本次实际需要的 --help。不要依赖记忆中的旧参数。
   如果 LoopX 尚未安装，先报告缺失和官方 installer 将写入的位置，得到我授权后再安装；不要把
   “找到安装命令”写成“安装已完成”。
3. 如果已有 LoopX 状态，先读 loopx registry、loopx status 和相关 history。优先复用精确
   goal_id；不要 force reconnect，不要按目标文字相似度选择 Goal。
4. 确保 .loopx/、.codex/goals/ 和 .local/ 被 Git 忽略。如果这些目录已有项目用途或已被跟踪，
   停下来报告冲突，不要擅自删除或 untrack。
5. 对尚未连接的项目，先运行 loopx connect --dry-run，展示将创建或修改的状态；确认没有冲突后
   再执行 loopx connect。已有 registry 时不要为了“重新开始”重复 bootstrap。
6. 如果有多个可选 Goal，停在只读 goal_selection_gate，把 choices 和推荐依据交给我选择；
   在选择前不要写 Todo、注册 Agent 或激活 Host loop。
7. 这是新的执行者时，选择一个新的 public-safe agent_id，先 preview，再用当前 CLI 支持的
   register-agent 命令执行并 read back。只有我明确要求 takeover 时才复用已有 agent_id。
8. 使用 loopx start-goal --guided --project . 和明确的 goal text 生成 transaction packet。
   Host 已知时显式传入正确的 --host-surface；只执行 packet 中与当前权限相符的步骤。
9. 任何用户审批、外部写操作、凭据、权限扩大、Host 选择或 destructive Git 操作都必须停在
   Gate，不能替我决定。
10. 完成后验证 loopx status、todo list、history、quota should-run、git status，以及
   git ls-files .loopx .codex/goals .local。
11. 不要提交或推送。最后给我一份“接入回报”，列出 goal_id、agent_id、Host、创建或修改的文件、
    当前 Todo/Gate、执行过的 mutation、验证结果、未解决问题和下一步。只完成 preview 时必须
    明确写“尚未接入完成”。
```

这份提示词不是把控制权交给 Agent。它把可执行工作委托给 Agent，同时把以下决定留给你：

- 多个 Goal 中选择哪一个；
- 是否 takeover 已有 Agent identity；
- 使用哪个 Host surface；
- 是否允许外部写操作、凭据或更大 write scope；
- 是否提交或推送仓库改动。

### 接入回报应该长什么样

一个可验收的接入回报至少包含：

```yaml
onboarding:
  status: complete | blocked | preview_only
  project_root: <repository root>
  goal_id: <exact goal id>
  agent_id: <fresh id or explicitly approved takeover id>
  host_surface: <exact host or unresolved>
changes:
  - <changed path and why>
gates:
  - <decision still owned by the user>
verification:
  doctor: pass | fail
  status_readback: pass | fail
  local_state_ignored: pass | fail
  tracked_private_state: []
next_action: <one concrete next step>
```

不要接受“命令运行成功”作为唯一结论。Agent 应同时给出状态 readback 和 Git 隔离证据。

### 示例：首次接入一个项目

```text
请按本章的 Agent 接入合同，把当前项目接入 LoopX。
目标是“为每个发布候选建立构建、审批和 Pages 部署的可恢复流程”。
当前 Host 是 Codex CLI visible TUI。使用新的 public-safe agent_id。
不要提交、推送或触发发布；遇到 Goal 选择、权限和外部写操作时停下来让我决定。
```

### 示例：安全续接已有状态

```text
请先只读检查当前项目已有的 LoopX registry、Goal、Todo、Gate 和 history，再帮助我续接。
优先复用精确 goal_id，但不要自动 takeover 任何已有 agent_id。
如果存在多个 Goal、活动 lease、未完成 mutation 或 workspace 路由不一致，只给诊断和选择，
不要写状态。不要提交或推送。
```

## 2. 安装并检查 LoopX

要求：

- Python 3.11 或更高版本；
- Node.js 22.22.3 或更高版本，用于 LoopX 自动管理的 TypeScript Effect runtime；
- macOS/Linux shell，或 Windows PowerShell 7；
- 一个已有 Git 项目。

安装 PyPI release 及其 LoopX workflow skills：

```bash
python3 -m pip install --upgrade loopx
loopx workflow-skills --install
loopx doctor
```

!!! tip "为什么不先 clone LoopX"
    普通使用者需要的是已发布 CLI 和 workflow skills，不是 LoopX 源码 checkout。clone-based install 留给希望运行
    live canary 或贡献 Kernel 的开发者。

`loopx doctor` 是安装事实的入口。不要只以 `which loopx` 成功作为健康证明；doctor 还会检查
release snapshot、Python import、skill 安装、Host 集成和 TypeScript Effect runtime。运行时由
LoopX 自动启动并在空闲后退出，用户不需要手工维护 daemon；`stopped` 表示可按需重启的健康状态，
`missing`、`unsupported` 或 `probe_failed` 则需要先修复。

需要确认真实 runtime 与 journal checkpoint 时运行：

```bash
node --version
loopx doctor --deep
```

原生 Windows 的完整安装、升级与回滚命令见
[Installing LoopX](/loopx/docs/guides/installing-loopx/)；不要为了套用 POSIX 示例而要求 WSL。

## 3. 建立忽略规则

在连接前，将本地控制状态加入项目 `.gitignore`：

```text
.loopx/
.codex/goals/
.local/
```

如果项目已经使用这些目录名，先检查现有内容，不要直接覆盖。LoopX 状态目录可能包含 active
state、registry、lease 和本地证据指针；`.local/` 还可能包含其他私有工作材料。

用 Git 确认规则生效：

```bash
git check-ignore -v .loopx/registry.json
git check-ignore -v .codex/goals/example/ACTIVE_GOAL_STATE.md
```

文件尚不存在时，`git check-ignore` 可能需要 `--no-index`：

```bash
git check-ignore -v --no-index .loopx/registry.json
```

## 4. 理解 Agent 执行的连接流程

从项目根目录运行：

```bash
loopx connect --dry-run
loopx connect
loopx status
```

先检查 dry-run 中的项目根目录、`goal_id`、状态文件和 Git 边界，再执行真实连接。`connect`
应复用已有 registry 和 active state。如果项目还没有足够状态，它会给出下一步；此时优先使用
带明确任务的 guided start：

```bash
loopx start-goal \
  --guided \
  --project . \
  --goal-text "为这个项目建立一条可验证的发布流程"
```

这个命令生成 guided transaction packet。它默认是预览，不应被理解为已经完成 Todo 写回、Host
激活和 Agent Turn。Agent 或 Host 集成需要按 packet 执行计划、状态写回与启动步骤。

`connect` / `bootstrap` 只登记 Goal 并写入 active state：它不会生成首连 onboarding todo、
owner 决策门禁或 Host loop opt-in 门禁。首连之后状态里没有可执行的 agent todo，第一个交付
todo 由 Agent 或已接入的 domain adapter 写入，避免自动化从生成的 onboarding 队列而不是
调用方自己的工作队列开始。

### 先选择 Goal，再选择 Agent

Guided start 会把两个选择分开：

1. **Goal selection**：如果项目只有一个已注册 Goal，复用它的精确 `goal_id`；如果有多个，返回
   只读 `goal_selection_gate`。从 `choices` 中选择一个精确重跑命令，在此之前不写 Todo、不注册
   Agent，也不激活 Host loop。
2. **Agent identity**：对带任务文本的新接入，未指定 `--agent-id` 时，只有 Goal 没有任何已注册
   lane（或显式 `--new-peer`）才默认 fresh identity；已有已注册 lane 时，`start-goal` 会返回
   identity gate，要求选择一个已有 lane。已有 Agent 是 takeover choice，不是自动默认值。

不要根据 objective 的文字相似度选择 Goal，也不要因为 registry 中只有一个 Agent 就自动接管它。
推荐路径是先预览、再原子注册一个新的 public-safe id：

```bash
loopx register-agent \
  --goal-id <selected-goal-id> \
  --agent-id <new-public-safe-agent-id>

loopx register-agent \
  --goal-id <selected-goal-id> \
  --agent-id <new-public-safe-agent-id> \
  --execute
```

Preview 只用于检查计划。继续 Todo writeback 前，应确认 execute result 的 `ok`、`changed` 和
`written` 为 true，global sync 成功，并且 source/global registration readback 已验证。若用户确实
要求接管旧 lane，则直接选择 packet 中绑定该精确 `agent_id` 的 takeover 命令，不要伪造 fresh
registration。

如果你已经知道当前 Host，可以显式指定，避免错误路由：

```bash
# Codex App
loopx start-goal --guided --project . \
  --goal-text "为这个项目建立一条可验证的发布流程" \
  --host-surface codex-app

# Codex CLI visible TUI
loopx start-goal --guided --project . \
  --goal-text "为这个项目建立一条可验证的发布流程" \
  --host-surface codex-cli-tui
```

如果不确定 Host 类型，先省略 `--host-surface`。LoopX 会返回只读 selection gate，而不是猜测。

## 5. 读取当前状态

先使用短路径：

```bash
loopx registry
loopx status
loopx todo list --goal-id <goal-id>
loopx history --goal-id <goal-id>
loopx quota should-run --goal-id <goal-id> --agent-id <agent-id>
```

这些命令回答不同问题：

| 命令 | 主要问题 |
| --- | --- |
| `registry` | 当前项目连接到哪些 active state |
| `status` | 谁应该行动、有什么 Gate 和风险 |
| `todo list` | 当前工作单元、owner 与 lifecycle |
| `history` | 哪些有界事件已经写回 |
| `quota should-run` | 当前是否允许下一轮交付 |

不要把 `should_run: true` 简化为“立即执行任意动作”。还要读取 `interaction_contract`、
`selected_todo`、capability gate、write scope 和 scheduler hint。

## 6. 验证 Git 隔离

连接后运行：

```bash
git status --short
git ls-files .loopx .codex/goals .local
```

第二条命令应无输出。如果输出了路径，说明本地控制状态已经被 Git 跟踪；仅增加 `.gitignore`
不会自动解除跟踪。先检查是否包含应保留的历史，再从 index 中移除，避免误删本地状态。

## 7. 可选：启用 Provider 与 Goal 功能

基础接入到这里已经完成。只有当前项目确实需要可选能力时，才继续本节。

先完成能力发现和 Goal 配置；只有需要独立分发的 Provider 时，再继续 Extension 示例。

### 发现 Capability 与可选功能

Capability catalog、Goal feature config 和 Extension activation 是三种不同表面：

用 `loopx capability list` 发现当前 Capability；用
`loopx --format json configure-goal --goal-id <goal-id>` 读取当前 Goal 的可选功能。

```bash
loopx capability list --format json
loopx capability show <capability-id> --format json
loopx --format json configure-goal --goal-id <goal-id>
loopx extension list --format json
```

`capability list/show` 是只读 catalog，不修改 Goal，也不安装 Provider。传入
`--extension-manifest` 只影响本次 catalog read；`declared=true` 不等于 installed、enabled 或
ready。

`configure-goal` 不带 setting flag 时也是只读。当前没有“enable 任意 capability id”的通用命令；
每项 default-off 功能都有明确配置字段。以 change-quality 为例：

```bash
loopx configure-goal --goal-id <goal-id> --change-quality-enabled
loopx configure-goal --goal-id <goal-id> --change-quality-enabled --execute
```

对于 `multi_subagent`、Explore Graph、Explore Harness、Reward Memory、Lark inbox 等功能，读取
当前 help 和 catalog delta，不要从名称猜参数。始终按“读 catalog -> preview -> 检查 delta ->
execute -> readback”执行。

配置入口可以使用全局 registry，但 Goal 的配置权威仍是 `source_registry` 指向的项目源。
CLI 和前端设置的读取、预览、版本检查与写入都先解析该源，再同步全局投影；
`--runtime-root` 选择投影目标，不改变配置权威。源不可读取时会报错，不会退回镜像写入并声称成功。
这样后续项目同步不会撤销刚刚保存的设置。

Configuration entry points may use the global registry, but the Goal's configuration authority
remains the project registry identified by `source_registry`. CLI and frontend reads, previews,
revision checks and writes resolve that source before synchronizing the global projection.
`--runtime-root` selects the projection target, not a different authority. An unreadable source
fails explicitly instead of falling back to a mirror write, so later project synchronization
cannot undo a successfully saved setting.

Todo 中的 `required_capabilities` 表示执行前必须已有的能力；`target_capabilities` 表示当前 Todo
正在建设、修复或验证的能力。缺失 target 可以进入 repair mode，不能反过来阻止建设它的 Todo。

因此，“catalog 可见”“Goal 已开启”“Provider doctor-ready”“本轮可用”是四种不同事实。

### 接入时启用已有 Extension

可选 Provider 的本地启用不是 `connect` 的隐式副作用。以当前
`loopx-finance-value-discovery` 为例，它是独立分发的零权限 Extension；只有你已经获得包含
`packages/loopx-finance-value-discovery/` 的 LoopX 源码 checkout 或等价 provider 源码包时，
Agent 才能安装。源码与 manifest 位于 LoopX 官方仓库的
[`packages/loopx-finance-value-discovery`](https://github.com/huangruiteng/loopx/tree/main/packages/loopx-finance-value-discovery)。

把这段补充到接入提示词：

```text
接入完成后，检查当前环境是否已经安装并启用 loopx-finance-value-discovery。

- 先运行 loopx extension list --format json，不要根据目录存在猜测 activation state。
- 如果 Extension 已安装且 enabled，执行一次只读 doctor readback；不要重复 install。
- 如果已安装但 disabled，在解释将重新运行 doctor 后，preview 并执行 extension enable。
- 如果尚未安装，先确认 provider source package 和
  packages/loopx-finance-value-discovery/extension.toml 存在。
- 修改 Python environment 属于本地环境写操作。先展示 pip install、extension install 和
  doctor 命令，得到我授权后再执行。
- package 必须安装到运行 `loopx` 的同一 Python environment，并让 provider entrypoint 出现在
  当前 shell 的 `PATH`；否则 doctor 应返回 `entrypoint_missing`，不能绕过。
- provider 源码包不存在时停下来报告：当前 release-only 环境不能隐式下载或启用这个 Extension。
- 不要把它描述成行情采集器或投资建议能力。它只把调用方提供的 frozen public-safe evidence
  归约成有界研究 packet，不执行网络读取、账户读取、交易或持续监控。
- 完成后回报 package install、extension enabled、doctor ready 和一次示例 run 的独立结果。
```

人工流程是：

```bash
loopx extension list --format json
python3 -m pip install ./packages/loopx-finance-value-discovery

# 使用 venv 时先激活，并确认两个命令来自同一 environment
command -v loopx
command -v loopx-finance-value-discovery

loopx extension install \
  --manifest packages/loopx-finance-value-discovery/extension.toml \
  --format json

loopx extension install \
  --manifest packages/loopx-finance-value-discovery/extension.toml \
  --execute \
  --format json

loopx extension doctor \
  loopx-finance-value-discovery \
  --execute \
  --format json
```

若已安装但 `enabled=false`，使用 `extension enable` preview，再添加 `--execute`。实际调用还需要
`finance_value_discovery_input_v0`。只有 `extension list`、executed doctor 和示例
`extension run --execute` 都成功，接入回报才可写“Extension 可用”。

## 恢复路径

### `loopx doctor` 失败

先查看报告中的 command path、release snapshot 和 skill 状态。升级后命令 skill 缺失时可以运行：

```bash
loopx slash-commands
loopx slash-commands --install
```

不要在不了解原因时复制另一个 checkout 的 `.loopx/`。

### 项目已有状态

默认保留它。先执行 `loopx registry`、`loopx status` 和 `loopx history`，再按精确 `goal_id`
选择要继续的 Goal；多个 Goal 必须经过 selection gate。然后为新执行者注册 fresh `agent_id`，
或在用户明确要求时 takeover 指定 identity。不要用 force reconnect 覆盖一个仍有价值的 Goal，
也不要把旧 Agent identity 当作 Goal 本身。

### linked worktree 指向错误目录

LoopX 的 delivery workspace 必须和实际修改所在 worktree 一致。先检查 registry，再使用官方
`refresh-state --delivery-workspace-path` 修复路由；不要通过复制 active state 制造第二份事实。

### global registry 不可写

项目本地状态与 global visibility 是不同层次。检查 `loopx doctor` 的 registry permission 报告，
修复文件所有权或权限后重新同步，不要把 global registry 提交到项目仓库。
