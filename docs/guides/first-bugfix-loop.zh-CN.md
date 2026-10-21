# LoopX 查 bug 入门

[English](first-bugfix-loop.md)

先用一个小型购物车项目走完流程：看到测试失败，让 Agent 通过 LoopX 修复，
重新测试，再检查并提交补丁。跑通之后，把同一套流程用于自己仓库的持续查错。

练习需要 Python 3.11+。使用 LoopX 还需要 Node.js 22.22.3+（推荐 24 LTS）、
LoopX，以及你已经在用的 Agent 宿主。练习测试只用 Python 标准库，不需要模型。

## 1. 准备一个可以丢弃的小项目

打开[练习项目](../../examples/bugfix-loop/README.md)，把 `cart.py` 和 `checks.py`
下载到 **LoopX 源码目录之外的新目录**。示例属于你正在阅读的仓库版本，旧版已安装
LoopX 不一定包含这些文件。普通使用无需克隆 LoopX 源码。

在终端进入这个目录，运行：

```bash
python3 checks.py
```

预期结果是 **6 项检查、2 项失败，退出码为 1**。这正是练习的起点。
如果报的是 Python 或导入错误，先修好环境，再让 Agent 修购物车逻辑。

需求很简单：金额使用整数分；空购物车费用为零；商品总价达到 10,000 分免运费；
其他非空购物车加 500 分运费；负价格必须拒绝，输入列表不能被修改。
初始代码在空购物车和刚好达到免运费门槛时有 bug。

初始化这个复制出来的项目，保存故意带错的起点：

```bash
git init -b main
printf '__pycache__/\n.loopx/\n' > .gitignore
git add cart.py checks.py .gitignore
git commit -s -m "demo: record intentionally failing cart exercise"
git switch -c codex/cart-bugfix
```

如果 Git 要求设置姓名、邮箱，配置你自己的提交身份后重试。起点提交故意保留失败
测试，修复提交则必须通过后面的检查。本地练习没有远端，不涉及 push 或合并。

## 2. 安装一次，选择当前真正使用的宿主

尚未安装时，先看[安装指南](installing-loopx.md)。普通 Python 包安装使用：

```bash
python3 -m pip install --upgrade loopx
loopx workflow-skills --install
loopx doctor
```

先处理 `doctor` 报出的 Python、Node.js 或 CLI 就绪问题。升级沿用同一个安装渠道，
不要为了绕过环境错误同时安装多个互相竞争的副本。

- **Codex App：** 把复制出来的项目设为工作目录，在项目对话里发送下面的 `$loopx`
  请求。App 使用 heartbeat automation 自动续跑。
- **Codex CLI：** 在练习项目目录运行 `codex`，在 Codex 内发送同一条 `$loopx`
  请求，不是在 shell 中执行它。CLI 使用当前会话可见的原生 `/goal` 续跑。
  OpenAI 文档注明 Goals 从 Codex 0.128.0 开始提供，先确认当前版本有 `/goal`。
- **Claude Code：** 打开同一目录，把 `$loopx` 换成已安装的 `/loopx` 命令技能。
  其他差异见[宿主选择表](newcomer-command-path.md#choose-the-loop-driver)。

在 Codex 中，用 `$loopx` 或 `/skills` → `loopx` 显式调用技能。用户自定义的原生
`/loopx` 不是这里的入口。LoopX 生成的入口关闭了隐式调用，单发普通任务文本不保证
启动 LoopX。找不到技能时，核对当前 Codex 进程使用的安装位置，包括 `CODEX_HOME`；
修好安装后若仍不可见，再重启 Codex。

## 3. 给 Agent 一个完整任务

把下面的内容粘贴到 **Agent 对话里**，工作目录保持为复制出来的练习项目：

```text
$loopx 修复当前购物车练习。先运行 python3 checks.py，按 cart.py 中的需求解释
失败原因，再修好空购物车和免运费门槛的 bug。保留已有 6 项检查、负价格拒绝和
输入列表不变的要求。只有发现缺失需求时才补回归，不要削弱测试来获得通过。
项目已经连接 LoopX 就复用；否则按当前实际宿主完成引导式初始化，注册自己的
Agent，写入有实际工作的 Todo。确认宿主的自动续跑已激活，不能只报告初始化完成。
遵守 LoopX 当前的额度和停止条件。修改后运行全部 6 项检查，展示实际 diff 和结果。
先保留未提交补丁，让我在第 5 步检查后提交。不要 push、新建远端仓库、合并或调用
额外的外部服务。全部需求验证后停止；若有阻塞，说明具体原因。
```

你应该看到一次失败复现、一份小补丁，以及最后的测试结果。6 项检查通过，且 diff
保留全部需求，练习就完成了。这一轮无需一直运行。

### 确认它真的开始执行

让 Agent 说明当前 LoopX 目标、自己的 Agent、下一个 Todo，以及已经激活的宿主驱动。
**只生成初始化命令包，仍然是预览。**

Codex CLI 中输入 `/goal`，查看当前原生 Goal。如果尚未激活，让 Agent 给出准确的、
生成好的 `/goal <task_body>`，粘贴到同一个 CLI 会话。保留生成的 LoopX loader，
不要换成绕过 LoopX 检查的普通目标。需要恢复初始化时，在项目终端运行
`loopx codex-cli-bootstrap-message --project .`，把返回的初始化消息交给 Codex，
完成后再恢复修复任务。

Codex App 中确认对应的 heartbeat automation 已实际保存。若当前宿主没有 automation
操作能力，Agent 应明确报告缺少哪一步，不能说自动执行已经就绪。

两条路径都依赖宿主可用。安装 LoopX 不会让已经关闭的终端或不可用的 App 自动运行。

## 4. 改成持续查 bug 的工作方式

完成小练习后，可以在真实项目使用下面的请求。把范围和测试命令替换成项目实际值：

```text
$loopx 持续检查 <我负责的模块或目录> 中可复现的 bug。先读仓库说明，确定相关
测试命令、工作范围和已有预算，在仓库要求的独立分支或 worktree 中工作。
每个真实问题依次完成：复现、失败回归测试、最小且完整的修复、相关测试与回归、
按仓库规则准备带 sign-off 的提交和可评审 PR。把结果与下一步写回既有 LoopX 工作。
没有新证据支持行动时，按 LoopX 调度等待，记录下一次有意义的触发条件，不重复
执行同样的空扫描。遵守当前额度和费用边界；如实报告环境失败和未解决的测试。
合并与发布留给我明确决定。
```

这里的“无限循环”，是允许有用且已授权的工作持续推进或恢复。每次修复仍有验收终点。
没有新问题时，监控按事件或计划等待；唤醒频率更高，不代表取得了更多进展。
初次只选一个模块、一个明确的测试命令，以及自己接受的费用边界。

暂停时，让 Agent 暂停这个 LoopX 目标及其对应驱动，并读回两者的实际状态。
CLI 可以先用 `/goal pause`，App 暂停对应 automation。暂停宿主驱动和暂停 LoopX
目标是两次不同的状态变化，不要靠删除项目状态停止循环。恢复时打开同一项目，明确
恢复同一个 Agent 的工作，读已有状态和上次证据，再激活当前宿主的驱动。

## 5. 测试、检查、提交，再进入评审

Agent 完成本地练习后，自己再运行：

```bash
python3 checks.py
git diff --check
git diff -- cart.py checks.py
git status --short
```

预期结果是 **6 项检查全部通过，退出码为 0**。确认修的是 `cart.py`，没有删除检查、
削弱预期或改变运费规则。没有实际运行的测试，不能记为通过。

然后只提交预期修复的文件，通常只有 `cart.py`：

```bash
git add cart.py
git diff --cached
git commit -s -m "fix: handle empty carts and free-shipping boundary"
git show --stat --oneline HEAD
```

如果确实增加了有价值的回归，也要先检查，再显式暂存 `checks.py`。不要把 `.loopx/`、
凭证、本地配置或 Agent 原始日志放进提交。到这里，本地 demo 已完成，不需要远端。

真实项目继续遵守仓库要求的测试与评审流程：核对正确的远端和目标分支，在已有授权
内推送工作分支，再创建说明实际原因、行为变化和测试结果的 PR，读回其 head 和检查
状态。本地 commit、推送分支、打开 PR、合并 PR 是不同结果。CI 或评审反馈可能带来
下一轮修复，创建 PR 本身不授予合并权限。

## 准备成本、持续费用与适用范围

复制练习只需两个文件和 Python 标准库。LoopX 另外需要一次 CLI/技能安装和宿主激活。
升级后检查当前宿主仍能找到技能、激活驱动；不同 Codex profile 可能使用不同安装目录。

6 项本地检查不调用模型或付费服务。Agent 修复会消耗宿主的模型额度和本机测试资源。
周期自动执行还会带来唤醒与检查工作，具体成本取决于频率、上下文、重试和提供商计费。
本教程不承诺固定价格或节省比例，继续采用 LoopX 的额度和调度，不另写 shell
`while true` 循环。

示例从红到绿，只验证这份购物车逻辑；它不能证明宿主会无限无人值守地恢复，也不
保证找到真实项目的所有 bug。进一步操作见[长程编程任务指南](long-running-coding-agents.md)。
Codex 原生 Goal 行为见 [OpenAI 官方指南](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex)。
