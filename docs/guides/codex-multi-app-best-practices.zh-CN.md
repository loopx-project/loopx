# Codex 多 App 隔离与运维最佳实践（zh-CN）

## 定位

面向在单台 macOS 上同时运行多个 Codex / ChatGPT App（例如官方 GPT 版 + DeepSeek 版）的操作者 runbook。
与 [Codex App 多 Provider 切换](../../packages/loopx-codex-provider-routing/RUNBOOK.md) 互补：那边讲「同一个 App 内切换 provider」，这里讲「多个 App 并存、隔离与排障」。

本文来自 2026-08-22 的真实线上故障复盘，包含结论、证据和可复现的检查命令。

## 目标与硬约束

- 两个 App 并存：GPT 官方模型（`gpt-5.6-sol`）与 DeepSeek（DeepSeek / 方舟 / OpenCode Go），各自独立的 `CODEX_HOME` 与前端数据目录，session / config / auth 互不污染。
- 官方 App 的单实例锁按 `--user-data-dir` 区分：**不同 `--user-data-dir` 可以同时多开**（已实测 `/Applications/ChatGPT.app` 同时跑两个实例、两个 app-server）。
- 隔离的 `CODEX_HOME` 是硬边界；跨 home 只读查阅用 peer bridge，不共享、不复制 SQLite。
- 开发机（SSH 远程项目）只挂在 GPT home；DS home 不挂远程开发机。

## 推荐方案：官方 App 双实例（不要做签名克隆）

启动两个实例的统一形状（由 launcher 脚本固化）：

```sh
/usr/bin/env \
  -u OPENAI_API_KEY -u CODEX_API_KEY -u CODEX_ACCESS_TOKEN \
  /usr/bin/open -n -a "/Applications/ChatGPT.app" \
  --env "CODEX_HOME=$HOME/.codex" \
  --env "CODEX_ELECTRON_USER_DATA_PATH=$HOME/Library/Application Support/Codex" \
  --env "CODEX_PROFILE_ROLE=DS" \
  --env 'DISABLE_AUTO_UPDATE=true' \
  --args "--user-data-dir=$HOME/Library/Application Support/Codex"
```

GPT 实例除 `CODEX_HOME=$HOME/.codex-gpt` 与 `--user-data-dir=.../Codex GPT` 外完全相同。

目录约定：

| 实例 | CODEX_HOME | 前端数据（--user-data-dir） | 模型 |
| --- | --- | --- | --- |
| Codex GPT | `~/.codex-gpt` | `~/Library/Application Support/Codex GPT` | gpt-5.6-sol（含开发机） |
| Codex DS V4 Flash | `~/.codex` | `~/Library/Application Support/Codex` | DeepSeek / 方舟 / OpenCode Go |

## 踩坑与教训

### 1. 不要给官方 App 做 ad-hoc 签名克隆（bundle id + 单实例锁 patch）

隔离克隆（`codesign --force --sign -`，Team ID 未设置，bundle id 改为 `com.openai.codex.gpt-isolated`）会导致 Apple Events 授权失败：

- Computer Use / SkyCUA 服务（`com.openai.sky.CUAService`）只接受 OpenAI 官方签名（Team ID `2DC432GLL2`）。
- 克隆发 Apple Events 报 `Sender process is not authenticated`（错误 `-10000`），App 每 ~0.4 秒重试一次、每次泄漏一个 `SkyComputerUseService` 子进程，几分钟内打爆进程表。
- 症状：zombie 上千、load average 400+、CLI / 测试全部 `fork: Resource temporarily unavailable`。

需要 Computer Use 时请用官方签名实例。

### 2. 不要用 `launchctl submit` 做「延迟重启」

`launchctl submit -l <label> -- ...` 会注册 **keepalive 常驻任务**：进程退出后 launchd 反复拉起（实测几十到上百次）。

正确做法：

- 重启用一次性 `open -n -a`；不要 `launchctl submit`。
- 若已误用，逐个清理：
  ```sh
  launchctl list | rg 'codex-gpt-runtime'   # 找出残留标签
  launchctl bootout gui/$(id -u)/<label>    # 逐个移除
  ```

### 3. 实例级操作必须按 `--user-data-dir` 匹配 PID

- 不能用 bundle id（`com.openai.codex`）全局 `quit` / `osascript quit`：会同时退掉两个实例。
- 判断「某 App 是否在跑」不能按可执行路径（两个实例同路径），要按 `--user-data-dir` 匹配进程命令行。

### 4. 进程表被耗尽时，agent 会主动重启宿主 App

LoopX agent 在检测到 zombie 积压（父进程不响应回收）时会强制重启宿主 App 来统一回收。此时要**先修泄漏源**（本例是 Computer Use worker），而不是对抗 agent 的重启；否则会形成「泄漏 → 重启 → 再泄漏」死循环。

### 5. 配置会被 App 回写

App 启动时会重写 `config.toml` 并回填它管理的键（例如 Computer Use 的 `notify` hook、`SKY_CUA_SERVICE_PATH`）。因此：

- 想禁用一个 App 级能力，优先从 App 管理方（能力目录 / 插件开关 / 服务路径）下手。
- 修改 config 前先备份，并在 App 重启后复查是否被回写。

## 验证清单

```sh
codex-app-status                      # 两个 App 各自 Running: yes
ps axww | rg "ChatGPT.app/Contents/MacOS/ChatGPT"   # 应有两行，user-data-dir 不同
pgrep -f SkyComputerUse | wc -l       # 官方实例应保持低位稳定
launchctl list | rg codex-gpt-runtime # 应为空
ps -Ao state= | awk 'substr($1,1,1)=="Z"{z++} END{print z+0}'   # zombie 回到个位数
```

## 与 LoopX 的关系

- LoopX agent（loopx-meta 等心跳）在 DS home 运行；GPT home 用于官方模型 + 开发机。
- provider 切换（DeepSeek / 方舟 / OpenCode Go）只作用于 DS home，不碰 GPT home。
- 本 runbook 属于操作知识；只有当其中的切换机制被抽象成可安装、可测试的 adapter 时，才适合升级到 `docs/integrations/`。

## 相关工具生态：CC Switch / AgentSwap / CLIProxyAPI（CPA）

下面按「配置层 → 运行时韧性层 → 协议翻译层」三层拆解，明确边界与风险。

### 配置层：CC Switch（farion1231/cc-switch）

- 定位：跨平台桌面配置 / Provider 切换器，管理 Claude Code、Codex、OpenCode、Gemini CLI 的 provider 配置、认证与模型目录。
- 在我们方案中的角色：管理 DS home 的 provider（DeepSeek / 方舟 / OpenCode Go），**不碰 GPT home**；切换时统一写回 `CODEX_HOME/config.toml`。
- 注意点：模型 catalog 与 `contextWindow` 必须维护一致口径（见 [provider routing runbook](../../packages/loopx-codex-provider-routing/RUNBOOK.md) 的窗口/compaction 小节），避免切换后 catalog 被简化覆盖。
- 变体：`cc-switch-cli`（CLI）、`cc-switch-web`、`LoongPort`（中转站增强版）等；均属同一思路。

### 运行时韧性层：AgentSwap（bojieli/agentswap）

- 定位：本地代理 + 凭据池，做同 lane（同一 harness）内的 failover；再加一层**跨 harness 离线会话搬迁**（`teleport` / `handoff`）。
- 关键语义：engine 只返回「值得返回的成功」或「换到任何账号都会同样失败的 client error」两类结果；失败按五类分类（短限流同账号等待保留 prompt cache、窗口耗尽换账号、401 刷新一次、其他 4xx 原样交还、200 流内 in-band 错误吸收）；全部账号耗尽后 park 到最早 reset，由 `agentswap run` 调用原生 resume。
- 会话搬迁：把 Claude Code / Codex / OpenCode / Kimi 会话互译为 canonical event stream，保留消息、reasoning、tool call / result、plan、时间戳、模型元数据；source 只读、validation fail-closed；**不搬**凭据、provider KV cache、approval、live process 等运行态。
- 与 CC Switch / CPA 的边界：不做协议翻译、不做并行乘数，failover-only，ToS 风险由用户自担。
- 成熟度：很新（2026-08-15 创建），acceptance 显示 316 个测试入口、12 方向 teleport 实测通过，但仍是 early software；接入前应 `import --dry-run` / `teleport --dry-run` 验证，并聚焦 `internal/engine` classify/park 与 `internal/session` canonical schema 的保真边界。
- 对 LoopX 的借鉴：代理层「什么不该透传」是恢复语义最值钱的设计；`teleport / handoff` 是 Standard handoff 的一个落地样本（不自动跨 harness，用户显式决策）。

### 协议翻译层：CLIProxyAPI / CPA（router-for-me/CLIProxyAPI）

- 定位：把 Claude Code / Codex / Gemini / Grok / Kimi 等 CLI 订阅账号通过 OAuth 包装成 OpenAI / Gemini / Claude 兼容 API，支持多账号 round-robin 与自动故障转移。
- 生态：`EasyCLIProxyAPI`（桌面 GUI）、`Cli-Proxy-API-Management-Center`（WebUI）、`CPA-Manager-Plus`、`CPA-Codex-Manager` 等。
- 风险（已在个人知识库中明确）：「订阅转 API」属于灰色生态——对厂商是风控与 ToS 问题，对用户是账号封禁与合规风险；**不应作为企业生产依赖**。Codex 订阅 OAuth 与 API Key 是两条不同认证 / 计费路径，不能混进同一轮转池。
- 结论：可只读跟踪其分类方式（账号池、round-robin、用量面板）与风控口径，不作为生产依赖。

## 未来提升方向

1. **官方双实例方案固化**：把 `codex-app-gpt` / `codex-app-ds` 启动器、Dock 图标、`codex-app-status` 检查固化为可安装脚本 / 服务；文档化「按 `--user-data-dir` 匹配 PID」的运维惯例。
2. **订阅 / 额度韧性**：评估 AgentSwap 作为 Codex / Claude 额度兜底（同 lane failover + park/resume），覆盖「长重构中订阅耗尽」场景；注意 ToS 与账号风险。
3. **跨 harness 会话连续性**：把 AgentSwap `teleport / handoff` 作为 Standard handoff 的落地样本，评估与 LoopX 会话 / rollout 迁移的对接；保持「不自动跨 harness、用户显式决策」。
4. **CPA 生态只读观察**：不引入生产，但跟踪其账号池、用量面板与风控口径，作为厂商定价 / 风控趋势的参考。
5. **CC Switch 模板化**：provider 记录 + model catalog + context window parity 统一模板，避免切换后模型目录被简化覆盖；与 LoopX benchmark 三臂共用同一 catalog 口径。
6. **LoopX agent 与宿主 App 的治理**：agent 检测到进程表耗尽时会强制重启宿主 App；把「先修泄漏源、再让 agent 正常重启」写入 agent 的 self-repair 指引，避免「泄漏 → 重启 → 再泄漏」死循环。
