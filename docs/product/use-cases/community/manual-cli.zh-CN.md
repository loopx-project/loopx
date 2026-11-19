# SQLite 命令行练习（开发者参考）

[English](manual-cli.md) · [从常用 AI 助手开始](README.zh-CN.md) · [常见问题](faq.zh-CN.md)

这份练习展示底层工作状态，供需要直接检查 CLI 的开发者选读。普通试用从 AI 助手入门开始。

创建一个临时 Goal，检查一份发布说明，再从新的 CLI 进程读回结果。
示例使用 POSIX shell、LoopX 2.3.0、受支持的 Node（22 系列需
22.22.3 或更新版本）和 `jq`。尚未安装 `loopx` 时，先看[安装指南](../../../guides/getting-started.md)。

| 术语 | 在这个例子里 |
| --- | --- |
| Goal（目标） | 核对一份发布说明。换一个终端或 Agent 会话，仍能找到这个目标。 |
| Todo（待办） | 检查 `NOTES.md` 有没有 `Summary` 标题。这是推进目标的一项可验证工作。 |
| Lease（租约） | 检查者在有效期内持有该 Todo 的执行权。领取时得到的凭证用于提交完成。 |
| Evidence（证据） | 标题检查指向 `NOTES.md:1`。后来的人可以重跑检查，核对记录的结果。 |

复制整段到终端。命令会创建临时项目和独立运行目录，并打印路径。
标题检查由本地 shell 执行。

```sh
(
  set -eu
  quickstart_root=$(mktemp -d)
  printf 'Quickstart directory: %s\n' "$quickstart_root"
  mkdir "$quickstart_root/project"
  cd "$quickstart_root/project"

  quickstart_loopx() {
    loopx --registry "$quickstart_root/project/.loopx/registry.json" \
      --runtime-root "$quickstart_root/runtime" --format json "$@"
  }

  quickstart_loopx bootstrap --project . --goal-id quickstart \
    --objective "Verify one release note" |
    jq -e '.storage_selection | select(.provider == "sqlite" and .authority_initialized) |
      {provider, handoff_mode}'
  quickstart_loopx register-agent --goal-id quickstart \
    --agent-id local-reviewer --require-new --execute |
    jq -e 'select(.ok and .written) | {registered_agents}'
  printf '# Summary\n\nExample release note.\n' > NOTES.md

  todo=$(quickstart_loopx todo add --goal-id quickstart --role agent \
    --text "Check that NOTES.md has a Summary heading" | jq -er '.todo_id')
  claim=$(quickstart_loopx todo claim --goal-id quickstart --todo-id "$todo" \
    --agent-id local-reviewer --claimed-by local-reviewer | jq -er '.claim_operation_id')
  quickstart_loopx task-lease inspect --goal-id quickstart --todo-id "$todo" |
    jq -e '.active and .lease.owner == "local-reviewer"'

  grep -n '^# Summary$' NOTES.md
  quickstart_loopx todo complete --goal-id quickstart --todo-id "$todo" \
    --agent-id local-reviewer --claim-operation-id "$claim" \
    --evidence "NOTES.md:1 contains the Summary heading (checked locally)." --execute |
    jq -e 'select(.ok and .completed) | {status}'
  quickstart_loopx todo list --goal-id quickstart --todo-id "$todo" |
    jq -e '.todo | select(.status == "done") | {status, evidence}'
  quickstart_loopx task-lease inspect --goal-id quickstart --todo-id "$todo" |
    jq -e '.ok and (.active | not)'
)
```

初始化结果会显示 `sqlite` 和 `hard_lease`。最后两次检查分别读回已完成的 Todo
及其证据，并确认租约已经结束。示例按独立运行目录的默认设置为 Goal 创建一个
本地 SQLite 权威数据库。项目注册表和发布说明另存。详见 [SQLite 存储与兼容性](faq.zh-CN.md)。

要检查权限边界，可在**带凭证的完成命令执行之前**，用同一个 Todo 和 Agent
试一次不带 `--claim-operation-id "$claim"` 的完成命令。操作应被拒绝，Todo
仍为 open。加回原领取凭证再完成练习。
