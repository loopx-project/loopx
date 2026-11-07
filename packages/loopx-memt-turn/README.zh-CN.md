# 可选 MemT Nano Turn 执行器

[English](README.md)

此实验包让 LoopX Turn 使用 MemT-1.0-Nano，并恢复先前 Turn 的原生记忆。
需要独立安装和显式选择；`enabled` 默认关闭。LoopX 的默认模型、文本召回、
Reward Memory 和状态存储不变。选中的 Turn 由 Nano 执行；此包不能给
Codex 或 Claude 加入原生记忆。长程效果与总成本收益尚未验证。

接入复用 `generic-cli`。准入、独立验收、状态写回和额度仍归 LoopX 所有。
MemT 客户端使用 Python，所以此包作为 Python 传输适配器；没有新增能力 ID、
控制面决策源或调度器。这是 CLI/provider 前置实现；App/Lark 配置和真实 GPU
验证尚未完成。现有 Turn 前端仍需完成用户操作链路验证，才能称为完整产品设置。

## 安装与许可证

需要 Python 3.11+、含受支持 `extensions.turn_host` 接缝的 LoopX 2.1.0+，
以及独立部署的 Nano SGLang 服务。GPU 部署按上游
[推理指南](https://github.com/MemTensor/MemT-1.0/blob/e75c13d090a6a9d34b29146d901bd84323185f24/inference/README.md)
操作。本地客户端只加载匹配的 tokenizer 和配置，不加载模型权重；首版仅支持 Nano。

上游客户端与推理代码采用 **PolyForm Noncommercial 1.0.0**。权重许可证另计。
使用前核对上游与第三方条款；独立进程不消除许可义务。商业用途需先解决授权。
此包不打包、下载或自动安装上游代码、权重或 CUDA 依赖。

在 LoopX 源码根目录创建独立环境：

```sh
python3 -m venv .venv-memt
. .venv-memt/bin/activate
python -m pip install -e . -e packages/loopx-memt-turn
```

审阅上游许可证后，单独安装已测试的客户端版本：

```sh
python -m pip install 'memt-sglang[client] @ git+https://github.com/MemTensor/MemT-1.0.git@e75c13d090a6a9d34b29146d901bd84323185f24#subdirectory=inference'
```

该客户端 extra 固定 Transformers 5.12.1。本适配器安装 Jinja2 来渲染聊天模板；
不安装 PyTorch 或 SGLang 服务端。执行前先启动独立服务。

## 配置与启用

将操作员配置放在任务工作区外，例如 `/srv/operator/memt.json`。
把以下路径换成自己的绝对路径：

```json
{
  "enabled": false,
  "workspace": "/srv/work/project",
  "state_dir": "/srv/operator/memt-state",
  "tokenizer": "/srv/models/MemT-1.0-Nano",
  "base_url": "http://127.0.0.1:30000",
  "timeout_seconds": 180,
  "max_new_tokens": 4096,
  "max_tool_calls": 16,
  "mcp_command": [],
  "tool_names": []
}
```

执行本地检查：

```sh
loopx-memt-turn --config /srv/operator/memt.json --doctor
```

doctor 检查客户端模块是否可发现、`config.json` 是否存在。
它报告 `network_checked=false` 与 `model_inference_verified=false`。
成功不证明 tokenizer 完整、服务可用或记忆有效。关闭配置时，即使未安装客户端
或模型资源也可通过检查。仅为选定配置将 `enabled` 设为 true。
凭据通过 `MEMT_API_KEY` 提供；不要写入配置或 URL。远程服务须使用 HTTPS；
HTTP 只允许回环地址。

需要工具时，将操作员选定的 stdio MCP 服务 argv 写入 `mcp_command`，
在 `tool_names` 中填写至多八个精确名称。适配器读取所有工具分页；名称缺失或重复
时失败。只支持文本或结构化 JSON 结果。两个空列表表示没有工具。
适配器不启动 shell，也不自动暴露文件系统工具。

MCP 服务必须自行强制执行当前签名请求的资源与操作范围。服务收到
`LOOPX_TURN_KEY`、`LOOPX_TURN_GOAL_ID`、`LOOPX_TURN_AGENT_ID`、
`LOOPX_TURN_GOAL_INSTANCE_ID`、`LOOPX_TURN_TODO_ID` 与 `LOOPX_TURN_WORKSPACE`；这些是范围输入，不是身份认证。
适配器不转发模型服务凭据或任意父进程环境变量。它不提供操作系统沙箱。
不要选用仅凭模型参数或历史记忆就授予无限制操作的服务。

对已注册 Goal、Agent 和当前可执行的已认领 Todo，沿用现有参数和独立验收器：

```sh
loopx turn run-once --goal-id demo --agent-id analyst --todo-id todo_demo \
  --project /srv/work/project --host generic-cli --iteration-context fresh \
  --host-command-json '["loopx-memt-turn","--config","/srv/operator/memt.json"]' \
  --validation-command-json '["python3","/srv/operator/check_artifact.py"]'
```

上述命令只预览。先提供能独立检查真实产物的操作员验收器，再为已审阅命令加
`--execute`。已有准入与权限规则仍适用。适配器返回一个
`loopx_turn_result_v0` 候选；模型回答成功不等于验收成功。
执行目录须与配置的工作区一致；正常 Turn 启动器会设置该目录。不一致时，适配器在
调用服务前失败。
`fresh` 每轮创建新执行会话，并显式恢复建议性原生记忆；不会恢复旧会话的权限。

## 持久化、失败与恢复

记忆按解析后的工作区、精确 Goal 生命周期和 Agent 隔离。请求必须携带 executor
给出的匹配 `goal_ref` 及合法 `goal_instance_id`；只有 alias 或实例不匹配时，在
任何 IO 前失败。既有仅 alias 的记忆目录不会自动导入或删除；其中 pending 效果
须单独核对，新实例从空记忆开始。在任何 provider 或 MCP IO 前，宿主同时持有旧
alias 游标锁和新实例锁。旧目录的 pending head、未完成或孤立回执会返回
`legacy_memory_requires_operator_reconciliation`；无法读取的状态也拒绝继续。
旧记录无法标明 Goal 生命周期，因此换实例不能绕过这道门。先停旧 worker，沿
既有 Turn 恢复 owner 核对精确效果，再显式选择新 `state_dir`；不得删除原证据。
旧的已完成记录保留原样、不导入，也不阻止新实例从空记忆开始。
新 Turn 创建 MemT 会话，导入最近
确认的快照，执行模型/工具链，导出新快照，关闭会话，再写回本地游标。
服务重启后使用新的 epoch 和显式快照导入。服务端快照目录须持久保存；
本地回执不包含记忆张量。适配器不记录对话原文、token 数组或模型推理；
私有回执包含配置/请求哈希、会话/快照引用和有界候选结果。

完整模型/工具 Turn 在外层独立验收前保存。因此，记忆可能包含未验证观察和失败
工作，不是已验收的经验库。每轮仍须读取 LoopX 当前任务与权限，并核对当前产物。
原生记忆不能替代这些记录。

保存时，完整编码 Turn 必须能放入服务端 prefill 限额。溢出、输出截断或候选格式
错误时显式失败；不会静默裁剪或伪造成功。配置限额约束单次模型请求与工具次数；
进程总时限由外层 Turn 监督器负责。
每一轮工具调用（包括 continuation）都须先确认生成完整，再派发工具。截断轮不能
执行本轮工具或发布快照；之前已经发生的工具效果仍须按原恢复流程核对。

MCP SDK 会将 stdio 子进程脱离父进程组。因此，原始 stdio relay 将实际工具交给
LoopX 已有 TypeScript 进程监督器。已验证的 POSIX 路径在父进程 EOF、取消或
父进程强制退出后，先终止工具，再返回监督结果。这不是沙箱，不能约束工具主动
再次脱离监督。Windows 进程树回收仍是尽力而为，本包未证明其严格停止边界。

重复的完整请求直接返回已存候选，不重复执行模型或工具。同一 Turn key 下请求
变化、操作员配置变化或工具 schema 变化时失败。重放旧的完整 Turn 不会将新的
记忆游标倒退。

中断或结果不明时保留私有 `running` 回执，并阻止该记忆范围继续执行。错误包括
`interrupted_turn_requires_operator_reconciliation` 和
`prior_turn_requires_operator_reconciliation`。SDK/MCP 错误仅输出异常类型，
不输出服务响应正文。先停止旧 worker，检查回执中的精确服务会话及当前工具/产物
效果，再通过现有 Turn 恢复流程处理。不要删除 pending 回执后重跑工具。
完成核对后，操作员可选择新的私有 `state_dir` 和新准入 Turn；这会从空记忆开始，
不会撤销已有操作。首版不提供自动恢复。

上游快照校验模型配置与 tokenizer 身份，但不哈希权重内容。操作员须固定服务端
checkpoint；替换 checkpoint 后即使配置未变，也须换新的 `state_dir`。
此包不删除服务端快照，也不提供保留策略。私有存储、备份和停用后的清理由操作员负责。

停用时，先停止活动工作，再移除显式 host-command 选择，或将 `enabled` 设为
false；恢复先前 host 配置。卸载命令：`python -m pip uninstall loopx-memt-turn`。
卸载不会删除 LoopX、停止外部服务、删除快照或修改任务状态。

## 验证范围

安装上述已审阅客户端后，在 LoopX 源码根目录执行：

```sh
python -m pip install -e '.[test]' -e packages/loopx-memt-turn
python -m pytest -q packages/loopx-memt-turn/tests
python -m ruff check packages/loopx-memt-turn
```

测试使用真实 MemT 客户端、本地 HTTP 协议夹具、小型真实 tokenizer 和真实 MCP
子进程，覆盖快照导入/导出、重启 epoch、范围隔离、防重复操作、结果契约、关闭行为
和失败路径。模型输出为合成数据。这些测试不证明 GPU 推理、服务端张量持久化、
App/Lark 采用或长程收益。推广前须固定任务和预算，对比同一 Nano 开启/关闭原生记忆。
