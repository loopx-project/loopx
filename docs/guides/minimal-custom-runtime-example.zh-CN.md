# 最小自定义 Runtime 示例

[English](minimal-custom-runtime-example.md)

LoopX 对 agent loop 保持中立，但大多数宿主**不需要**实现 typed runtime
adapter。本页对应
[issue #2835](https://github.com/huangruiteng/loopx/issues/2835)
要求的最短接入契约。

有两种接入深度。除非你需要程序化的 commit/replay/recovery，否则从路径 A 开始。

## 路径 A — 主流：skill + 唤醒 + LoopX CLI

| 部件 | 负责 | 不负责 |
| --- | --- | --- |
| **宿主唤醒** | 何时开始一轮（cron、队列、可见 `/goal`、原生 loop 或人工） | Todo/gate/quota/证据存储 |
| **轻量 skill / 再进入指令** | agent 如何读取新鲜 packet、认领工作、校验并写回 | 第二套状态机 |
| **LoopX CLI** | 持久 goal、todo、claim、gate、证据、refresh、quota、scheduler hint | 模型工具与宿主进程生命周期 |

标准一轮：

```text
宿主唤醒
  -> 轻量 skill / 再进入指令
  -> loopx status
  -> loopx quota should-run
  -> loopx todo claim
  -> agent 执行一个有界切片
  -> 独立校验真实后置条件
  -> loopx todo complete（evidence）+ refresh-state
  -> loopx quota spend-slot --execute
  -> 宿主应用 scheduler_hint，准备下一次唤醒
```

可执行公共 smoke（仅合成 fixture）：

```bash
python3 examples/custom-runtime-minimal-cli-turn-smoke.py
```

该 smoke 在临时项目上验证已发布 CLI 序列：认领 todo、写入公开 marker、
校验 marker、带 evidence 完成、refresh，并花费一个 controller slot。
它不会复制私有日志、凭证或真实 agent transcript。

日常 onboarding（`agent-onboard`、skill 投递、scheduler ACK）见更长的
[把 LoopX 嵌入你的 Agent Runner](custom-agent-runner-integration.zh-CN.md)。

## 路径 B — 进阶：typed LoopX Turn adapter

当宿主需要以程序方式驱动外部 worker，并需要 preview/commit/replay/recovery
语义时，使用此路径，而不是依赖协作式 agent 自行调用 CLI：

```text
新鲜 LoopX decision / TurnEnvelope
  -> host adapter
  -> loopx_turn_result_v0
  -> 独立 effect 校验
  -> 持久 commit / replay / recovery
```

可执行公共 smoke：

```bash
python3 examples/loopx-turn-fake-host-walkthrough-smoke.py
```

路径 B 是可选的。它不取代 Codex、Claude Code、Cursor、shell、Grok Build
等协作式宿主上的路径 A。

## 自定义 runtime 不应做的事

- 再实现一套 Todo / gate / 证据 / quota / scheduler 存储。
- 在验证写回之前 `spend-slot`。
- 把模型完成文本当成证明，而不读取真实产物。
- 提交 `.loopx/`、活的 `ACTIVE_GOAL_STATE.md`、凭证或原始 session。
- 把可见宿主静默切换成隐藏无人值守执行。

## 相关页面

- [Custom agent runner integration](custom-agent-runner-integration.md)
- [Runtime connector catalog](../integrations/runtime-connector-catalog.md)
- [Host integration surface v0](../reference/protocols/host-integration-surface-v0.md)
- [LoopX Turn fake-host walkthrough smoke](https://github.com/loopx-project/loopx/blob/main/examples/loopx-turn-fake-host-walkthrough-smoke.py)
