# 用量心跳（usage ping）

[English](usage-ping.md)

LoopX 可以每天发送一次匿名、需主动开启的心跳，让维护者统计真实的活跃安装数，
而不是靠估算。它**默认关闭**：在机器所有者运行 `loopx usage-ping enable`
之前，不读取、不写入、不发送任何东西，LoopX 也不会弹出询问。

```bash
loopx usage-ping            # 查看授权状态、是否正在发送，以及将要发送的完整内容
loopx usage-ping enable     # 开启；生成一个随机安装 id
loopx usage-ping disable    # 关闭；丢弃安装 id
```

## 发送内容

请求体就是下面这个 JSON 对象（`loopx_usage_ping_v0`），没有其他字段；
collector 会拒绝任何多出字段的请求。

| 字段 | 示例 | 含义 |
|---|---|---|
| `schema` | `loopx_usage_ping_v0` | 载荷版本。 |
| `install_id` | `3f0c…-4…` | `enable` 时生成的随机 UUIDv4，不由机器、用户或账号推导。 |
| `version` | `1.2.0` | LoopX 包版本。 |
| `os` | `darwin` | `darwin`、`linux`、`windows`、`other` 之一。 |
| `python` | `3.12` | Python 主版本.次版本。 |
| `channel` | `pip` | 安装方式：`pip`、`local_release`、`source` 或 `unknown`。 |

LoopX 不读取也不发送项目名、goal / todo 内容、路径、主机名、用户名、账号 id、
命令行、环境变量或错误报告。`loopx usage-ping` 会原样打印下一次的载荷，便于核对。

## 何时发送

- 仅当授权为 `enabled`、已配置 collector 地址、且没有被下面的开关拦截时。
- 每个 UTC 日最多一次，由当天第一条 `loopx` 命令触发。请求在分离的后台进程中
  执行，超时 3 秒，不会拖慢命令或让命令失败；失败当天不重试。
- `loopx usage-ping` 本身永远不会触发发送。

地址优先取 `LOOPX_USAGE_PING_ENDPOINT`，否则用发布版默认值。必须是
`https://`（明文 `http://` 仅允许回环地址，用于本地测试）。项目 collector
部署之前，发布版默认值为空，因此开启只会记录授权、不会发送；此时
`loopx usage-ping` 显示 `sending: false`。

## 开关

以下任一变量都会拦截发送，与已保存的授权无关：

| 变量 | 拦截条件 |
|---|---|
| `LOOPX_USAGE_PING` | `0`、`false`、`no` 或 `off` |
| `DO_NOT_TRACK` | 非空且不为 `0` |
| `CI` | 非空且不为 `0` / `false` |

## 本地状态

授权保存在 `~/.codex/loopx/usage-ping.json`（`loopx_usage_ping_state_v0`，
文件权限 `0600`），按机器而不是按项目生效。`disable` 会重写该文件并去掉 id，
之后再 `enable` 就是一个新的、无法关联的安装。删除该文件即回到 `undecided`。

## Collector 与公开数据

Collector 是一个小型 Cloudflare Worker + D1 数据库，源码在
[`apps/usage-collector`](../../apps/usage-collector/README.md)。它只保存载荷字段
和 UTC 日期，不保存 IP、User-Agent 或请求元数据，400 天后删除。

公开的 `GET /v0/stats` 接口发布：

- `monthly_active`：某个自然月（UTC）内至少发过一次心跳的不同安装 id 数；
- `new_installs`：当月首次出现的 id 数；
- 最近 30 天的 `rolling_30d_active` 与 `daily_active`；
- 当月按版本、OS、安装方式的分布，少于 5 个安装的分桶合并为 `other`。

这些数字是下限：只统计主动开启的机器，且关闭后再开启会算作新安装。
