<p align="center">
  <img src="packages/web/public/calli-icon.png" alt="Calli：浅蓝背景上举着一大一小双钳的蓝色螃蟹" width="160" />
</p>

<h1 align="center">Calli</h1>

<p align="center">随时叫上你的 AI 伙伴。</p>

Calli 是一个开源的 agent 工作台。用简单的界面管理 TRAE、zcode、Claude Code、Codex、pi、Antigravity 等国内外 agent，通过插件把好用的工具接进来。电脑和手机都能用，也欢迎每个人参与改进。

打开网页就能：

- 看每个 agent 在做什么——进展、审批、用量
- 随时发消息、补充要求、处理审批
- 一处管理多个 agent 和多台机器，保存团队分工、交接任务
- 额度快用完时，等本轮结束后自动写摘要、倒计时交给下一个账号或模型；支持时直接在原会话换模型
- 用实时语音和 agent 边聊边做：说出任务交给后台，接着聊别的，结果回来再念给你听
- 按需安装线程看板、Memory 记忆看板，或连接 LoopX 查看目标进度

Calli 不动 agent 本身的目录、配置和会话，装好即用。Calli 是自创品牌名，呼应随时叫上 AI 伙伴的感觉；图标采用一只举着大小双钳的蓝色小螃蟹，配浅蓝背景，保持简单、亲切的形象。

## 界面预览

| 桌面 · 亮色 | 桌面 · 深色 |
|---|---|
| ![桌面亮色](docs/screenshots/desktop-light.png) | ![桌面深色](docs/screenshots/desktop-dark.png) |

| 手机 · 亮色 | 手机 · 深色 |
|---|---|
| ![手机亮色](docs/screenshots/phone-light.png) | ![手机深色](docs/screenshots/phone-dark.png) |

实时语音：开启后输入框换成一个会随声音起伏的球，你说的话以浅蓝气泡记进线程；后台任务和待批准事项一行可见；结束后逐句对话折成一行，下面留一份文字总结。

![实时语音（亮色）：连接中、对话中、静音、结束后的总结](docs/screenshots/voice-light.png)

![实时语音（深色）](docs/screenshots/voice-dark.png)

用量与诊断：多 agent 的 token 用量、TPM 走势和费用估算。

![用量与诊断](docs/screenshots/usage-light.png)

写作模式：在加号菜单里一点就开。你口述观点，agent 用你的原话记成初稿、标出【待补】、追问具体场景，再按二稿、三稿、配图、润色一稿一稿往下走。正文是 `writing/` 下的 Markdown，按文章排版，画出 Mermaid 图、显示配图；读和写是同一张纸，可以直接改（键盘或语音输入法都行），agent 同时改过时不会覆盖你的版本。在正文里选中一句，一点「删」「待补」「为什么」「改成…」就成了给 agent 的注释。说「复盘」，agent 把你反复改的地方提炼成写作习惯，记进 `~/.calli/notes/writing.md` 等你确认，之后每篇都照着写。设计见 [写作流程设计](docs/writing-design.md)。

![写作模式（亮色）：加号菜单、选阶段、正文、选中一句加注释](docs/screenshots/writing-light.png)

![写作模式（深色）](docs/screenshots/writing-dark.png)

![写作模式（桌面）：对话上方是阶段条，右边是正文](docs/screenshots/writing-desktop-light.png)

> 状态：可用，正在迭代。产品方向见 [愿景与设计思路](docs/VISION.md)，现有架构见 [PLAN](docs/PLAN.md)，进度见 [TODO](docs/TODO.md)，扩展方案见 [扩展设计](docs/extensions-design.md)。

## 已支持

| Agent | 接入方式 | 审批 | 发图片 | 导入历史 / 分叉 |
|---|---|---|---|---|
| Claude Code | ACP（`@agentclientprotocol/claude-agent-acp`，随项目安装） | 有 | 有 | 有 |
| pi | `pi --mode rpc` | 无（pi 本身没有） | 有。纯文本模型需要装 pi 扩展 `pi-aux-vision` | 有 |
| Antigravity | `agy` 的无界面 stream-json 模式 | 无（不能询问，只有"自动改文件"和"完全访问"两档） | 有（按文件路径交给它） | 暂无 |
| Codex | `codex app-server` | 有 | 有 | 有 |
| TRAE CLI | app-server 执行，ACP 模型与历史接口 | 有 | 有 | 有 |
| zcode | `zcode app-server` 精简接入 | 有 | 按原生模型能力 | 暂无 |
| DeepSeek Harness | `dsh --profile acp`（ACP） | 有（仅允许一次/拒绝） | 有（按当前模型路由） | 暂无 |
| Doubao CLI | `doubao -p` / `--resume`，可选实验流式桥 | 无（无界面下需要批准的操作由 CLI 拒绝） | 暂无 | 暂无 |

各家 agent CLI 需要先在主机上装好并完成登录或鉴权。没装的 agent 在界面里显示为不可用；各家的能力和接入边界见 [架构说明](docs/PLAN.md)。Doubao CLI 使用原生默认模型，可通过 `CALLI_DOUBAO_BIN` 指定入口；已有安装可从 Agent 管理的添加入口接入，在添加页或实例「接入设置」里可开启版本限定的实验流式桥（2.31.2 / Linux x64，原安装不变）；也支持 `CALLI_DOUBAO_STREAM=1`，具体范围见 [Doubao CLI 接入](docs/doubao-cli-design.md)。

## 插件与参与

在「配置 → 扩展」按需安装：

- **线程看板**：按进行中、等你、待验收、已完成排列线程；agent 每轮结束报告进度，完成由你勾选。
- **Memory 记忆看板**：只读浏览 Codex 已有的记忆文档，不生成或同步记忆。
- **实时语音**（试验中，真人听说仍在验收）：用这台机器上已登录的 Codex 原生语音，不需要另配 API Key；会消耗现有额度，具体计费归属尚未核实。在任意支持协作的线程里都能开，任务可以交给当前线程的 agent（Claude Code、pi 等）去做。浏览器开麦需要 HTTPS 或 localhost，见 [语音设计](docs/voice-design.md)。
- **LoopX**：需先安装并配置 LoopX，关联目标和线程后，在会话与看板中查看目标、待办和待决定事项。当前不提供自动推进。

skills 和 MCP 属于 agent 资源，通过各自的管理页操作，与 Calli 插件分开。想接入工具、贡献一个工作视图，或者反馈使用中的问题，可以从 [参与和插件指南](docs/contributing-extensions.md) 开始。独立数据源可放在自己的仓库；当前还没有自动收录的插件市场。

## 快速开始

需要 Linux 或 macOS、git 和 Node.js 22 或更高版本。

```bash
curl -fsSL https://raw.githubusercontent.com/maxliux5/calli/main/install.sh | bash
```

脚本把代码装到 `~/.local/share/calli` 并构建，生成 `~/.config/calli/.env`（随机 token；线程数据放在 `~/.local/state/calli`；agent 默认在家目录工作），在 Linux 上装成 systemd 用户服务，最后打印地址和 token。打开地址，点「连接 hub」粘贴 token 即可；没连接时先显示一套示例数据，可以随便点开看。

- 先看会做什么：`… | bash -s -- --dry-run`
- 更新：再运行一次。设置、token 和线程数据都保留，等正在跑的线程结束后才重启。
- 卸载：`… | bash -s -- --uninstall`。只删服务和代码，设置和数据保留。
- 不装服务：`--no-service`，脚本打印手动启动的命令。没有 systemd（如 macOS）时也是这样。

**让 agent 帮你装**：把这句话发给这台机器上的 Claude Code、Codex 等：

```text
按 https://raw.githubusercontent.com/maxliux5/calli/main/docs/install-agent.md 在这台机器上安装 Calli
```

它会先预览、再安装、确认服务在运行，最后告诉你地址和 token；装 Node.js、编译工具这类改系统的事会先问你。

各家 agent CLI 要自己装好并登录，Calli 只调用这台机器上现有的。

### 从源码开发

```bash
npm install
npm run setup               # 生成仓库根目录的 .env 和随机 CALLI_TOKEN，并打印出来
npm run build
CALLI_ENV_FILE=../../.env npm start -w @calli/daemon    # 默认监听 127.0.0.1:8787
```

要让 agent 在哪些目录工作，编辑 `.env` 里的 `CALLI_WORKDIRS`。开发时可以分开启动：

```bash
CALLI_ENV_FILE=../../.env npm run dev:daemon   # 后端
npm run dev:web                              # Vite，http://127.0.0.1:5173，/api 代理到后端
```

daemon 在 `packages/daemon` 目录里运行，所以用 `CALLI_ENV_FILE` 指向根目录的 `.env`。没有设置 `CALLI_ENV_FILE` 时，读取的是当前目录下的 `.env`。

## 产品官网与引导体验

官网：[maxliux5.github.io/calli](https://maxliux5.github.io/calli/)。

仓库包含独立的中文产品官网，展示「简单管理更多国内外 agent、按需安装插件、一起参与建设」的方向。官网展示自动交接、agent 协作、看板与记忆等可操作例子：体验账号接力、取消倒计时或原会话换模型，派发任务、查看成员与返回结果，勾选看板验收，以及搜索记忆、阅读原文；同时保留任务、审批和方案体验，介绍可安装的 LoopX 工作数据源，并提供插件贡献与需求反馈入口。技能和 MCP 通过各自的资源页管理；BotMux 提醒与通用任务控制仍在规划，具体状态见 [扩展设计](docs/extensions-design.md)。

```bash
npm run dev:site       # 开发官网，默认端口 5174
npm run build:site     # 输出 packages/site/dist
npm run preview:site   # 预览构建后的静态站点
npm run test:site      # 构建并运行官网浏览器测试
```

`packages/site/dist` 可以单独部署到静态托管服务，支持网站根目录和子目录。首页是 `index.html`，独立体验入口是 `demo/index.html`；托管服务需要支持目录首页。开发与本地预览默认只监听本机。

GitHub Pages 通过 `.github/workflows/pages.yml` 发布：推送到 `main` 后，自动安装依赖、检查并构建官网，再上传 `packages/site/dist`；也可在 Actions 中手动运行「Deploy website」。首次启用需在仓库 Settings → Pages 选择 GitHub Actions 作为发布来源。Pages 仅托管官网和本地模拟体验，运行 agent 的 hub 仍部署在自己的主机上。

体验区进入可视范围或点击场景后才加载，手机保留内嵌交互并提供「全屏体验」。官网使用自己的轻量图标和静态资源目录，不复制 hub 的应用清单。安装区包含启动后打开工作台、输入 token 及远程访问说明。

引导体验仅在浏览器内回放预置事件，不需要 daemon，不读取已保存的 hub token，不发送 `/api/` 请求，也不运行真实 agent。回复、命令结果和文件改动都是示例；切换任务可浏览不同流程，重新体验会清空本轮演示。普通 hub 的入口、登录和原有只读示例保持不变。官网独立构建，不由 `scripts/restart.sh` 部署。

## 远程访问

- 默认只监听 `127.0.0.1`。
- 远程使用时，通过 VPN 访问。把 `CALLI_HOST` 设为 VPN 网卡的地址，不要设为 `0.0.0.0`。
- 使用 HTTP + token。VPN 已加密传输，项目不额外处理证书。
- 注意：`http://<IP>` 不是安全上下文，因此 PWA 的离线缓存和"添加到主屏幕"不可用，网页里的"拷贝"按钮需要降级处理。

## 管理另一台机器

浏览器只连主 hub，其他机器上的 daemon 主动通过 relay 连到 hub。线程、文件和 agent 都留在各自机器上，节点无需开放入站端口。

1. **浏览器**：打开「配置 → 机器 → 添加机器」，填写机器名称，生成接入配置。
2. **节点**：安装 Calli，将 `CALLI_HUB_URL` 和 `CALLI_HUB_TOKEN` 写进 `~/.config/calli/.env`。保持私有 socket 开启，安装服务用 `scripts/install-service.sh`，已有服务重启用 `scripts/restart.sh`。
3. **连接**：节点自动接入，浏览器在线程列表顶部切换机器。断线后节点自动重连。

接入令牌只显示一次，hub 只保存其哈希；丢失后移除再添加。旧 SSH 机器登记会被忽略并提示重新添加，加载时不修改原 hosts.json，节点会话和文件不受影响。

| 配置 | 用途 |
| --- | --- |
| `CALLI_HUB_URL` | 节点可访问的 hub 地址，建议 HTTPS |
| `CALLI_HUB_TOKEN` | hub 为此节点生成的接入令牌，与浏览器登录 token 不同 |
| `CALLI_SOCKET` | 本机私有 socket，默认 `~/.local/state/calli/daemon.sock`；接入节点不可设为 `off` |

私有 socket 由本机用户文件权限保护。hub token 能操作登记的节点；节点令牌只能领取自己的 relay 任务。移除机器会撤销令牌，不删除节点线程和文件。

`scripts/update-hosts.sh [机器ID] [--check]` 可经 relay 更新在线节点。节点需要 systemd 用户服务与仓库克隆（默认 `~/code/calli`，hosts.json 的 `repo` 可覆盖）。更新任务独立于节点服务运行，构建快照、等待空闲后切换、失败回滚；hub 在重连后读取结果。`--check` 会 fetch 并快进克隆，但不构建或切换服务。真实 systemd 更新与跨网络部署仍待验收，机制和限制见 [多机管理设计](docs/multi-host-design.md)。

## 开机自启

```bash
scripts/install-service.sh              # 装成 systemd 用户服务并启动；默认读 ~/.config/calli/.env
scripts/install-service.sh --remove     # 停止并移除
journalctl --user -u calli -f
```

服务通过登录 shell 启动，所以 agent 能拿到你在 shell 配置里设置的 API key。不登录也要在开机时启动，需要执行一次 `loginctl enable-linger $USER`。

更新代码后用 `scripts/restart.sh` 重启：先构建，构建失败就不动正在跑的服务；再等所有线程跑完这一轮，然后重启并检查健康状态。`--force` 不等，正在跑的那一轮会以错误结束。在 hub 的线程里由 agent 调用时，脚本会在后台等，等包括它自己在内的所有线程都空闲后再重启，日志在 `~/.local/state/calli/restart.log`。

### 从 Limi 升级

应用由 Limi（更早叫 remote-agent-hub）更名为 Calli。旧名字都还认：`limi` 命令是 `calli` 的别名；`RAH_*`、`LIMI_*` 环境变量按对应的 `CALLI_*` 读取（`LIMI_SOCKET`、`LIMI_KEY` 对应 `CALLI_TEAM_SOCKET`、`CALLI_TEAM_KEY`），同时设置时以新名字为准；只有旧目录 `~/.local/state/remote-agent-hub`、`~/.limi` 时继续用旧目录；浏览器里保存的 token、草稿和偏好在第一次打开新版时复制到新名字下。

升级后照常用 `scripts/restart.sh` 重启也行，它会把旧服务指向改名后的 workspace。想把服务和目录也换成新名字，就运行：

```bash
scripts/migrate-name.sh --dry-run   # 只列出会改什么
scripts/migrate-name.sh             # 构建，等线程空闲后切换，服务停几秒
```

脚本把服务 `remote-agent-hub` 改名为 `calli`，名为 `remote-agent-hub-*` 的附属服务改成 `calli-*`；把 `~/.config/remote-agent-hub` 和附属服务的目录移到新名字下，旧路径留一个指向新目录的链接；把 `.env` 里的键改成 `CALLI_*`，值不变，token 也不变。数据目录 `~/.local/state/remote-agent-hub` 和 `~/.limi` 不移动：线程在里面工作，agent 可能按所在目录找回会话。这两个目录保留原名，另建 `~/.local/state/calli` 链接到数据目录。新服务 30 秒内没有通过健康检查，脚本就把一切恢复原样，用这次的构建启动旧服务；备份在 `~/.local/state/remote-agent-hub/migrate-backup`。

## 发布前检查

```bash
npm run check:publish
```

扫描会被发布的文件，查找你的用户名、主机名、家目录路径、内网 IP、长密钥、邮箱地址。公司名、项目名这类私人词放在仓库外的 `~/.config/calli/publish-denylist`（每行一个），也会一起检查。

## 目录结构

```
packages/daemon   网关：HTTP/SSE API、鉴权、SQLite 存储、各 agent 的适配器
packages/web      界面：React + Vite，手机与桌面共用一套组件
docs/             计划与界面说明
```

## 开发

```bash
scripts/init.sh   # 装依赖、构建，起一个用假 agent 的临时 hub 并跑一轮冒烟测试；加 --check 跑完即停
scripts/demo.sh   # 起一个浏览器可打开的体验 hub：假 agent，绑定所有网卡，打印地址和 token；用 stop 子命令停止并清理
npm test          # daemon 与 web 的全部单元测试和 API 测试
npm run typecheck
npm run test:e2e   # 统一构建一次，再运行浏览器 E2E（最多并行 2 个测试文件）
```

测试不需要真实的模型账号：适配器测试使用 `packages/daemon/test/fixtures` 里的假 agent 进程。

E2E 需要本机有 Playwright Chromium 或系统 Chrome。根目录的 `npm run test:e2e` 和 `npm test -w @calli/e2e` 共用入口，构建失败就停止；测试进程只消费构建产物，不再自行构建。`npm test` 也会验证这个入口的构建次数、执行顺序、并发上限和失败退出行为，不启动浏览器。

只跑部分 E2E 时，文件路径相对于 `packages/e2e`，Node 测试参数放在文件路径前，带值参数使用 `--参数=值`：

```bash
npm run test:e2e -- test/smoke.spec.mjs test/appearance.spec.mjs
npm test -w @calli/e2e -- --test-concurrency=1 test/smoke.spec.mjs
```

需要复用已构建产物时，先单独运行一次 `npm run build`，再用 `node --test --test-concurrency=2 packages/e2e/test/*.spec.mjs`。截图脚本 `packages/e2e/shots.mjs` 和 `packages/e2e/overview-shots.mjs` 同样需要先构建。直接运行测试或截图脚本不会触发隐式构建；产物缺失时会提示先构建。以上限制针对单次 E2E 命令，不要同时启动多条 E2E 命令。

## 安全

- HTTP 监听上的 API 需要 `Authorization: Bearer <CALLI_TOKEN>`，`/api/health` 除外；仅本机用户能访问的私有 Unix socket 免 token，由本机用户文件权限保护。
- daemon 等同于远程 shell。只把它暴露在可信网络（VPN）中。
- 工作目录必须在 `CALLI_WORKDIRS` 白名单内。

## 许可证

[MIT](LICENSE)

### 安装为桌面应用

在 Chrome 中打开 Calli 后，可通过浏览器菜单将它安装为应用。安装名称为 Calli，图标使用蓝白配色的大小钳小螃蟹。

如果此前安装的应用仍显示 Limi、AgentHub 或旧图标，先尝试关闭所有应用窗口后重新打开，并接受 Chrome 提供的名称或图标更新。如果仍未更新，可卸载旧应用后从当前网页重新安装；卸载时不要勾选清除网站数据，以保留本机登录和偏好。
