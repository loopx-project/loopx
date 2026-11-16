# Try LoopX in five minutes

[简体中文](README.zh-CN.md) · [Community FAQ](faq.md) · [Assistant evaluation](golden-queries.md)

Create a disposable Goal, check one release note, and read the result back
from a new CLI process. The example uses a POSIX shell, LoopX 2.3.0, a
supported Node runtime (22.22.3 or later on the Node 22 line), and `jq`.
Start with the [installation guide](../../../guides/getting-started.md) if
`loopx` is missing.

| Term | In this example |
| --- | --- |
| Goal | Verify one release note. The Goal retains the intended outcome across CLI and agent sessions. |
| Todo | Check that `NOTES.md` contains a `Summary` heading. This is one checkable step toward the Goal. |
| Lease | The checker holds a time-limited execution right for this Todo. The claim returns a reference used at completion. |
| Evidence | The heading check points to `NOTES.md:1`. A later reader can rerun the check and inspect the recorded result. |

Run the block in a terminal. It creates an isolated project and runtime under a
temporary directory and prints their location. The shell runs the heading
check locally.

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

The bootstrap output identifies `sqlite` and `hard_lease`. The last two checks read
the completed Todo with its evidence and confirm the lease has been retired.
With this isolated runtime's defaults, LoopX creates one local SQLite authority
database for the Goal. The registry and the example note are separate files.
See [SQLite storage and compatibility](faq.md#where-does-the-state-live).

For a real repository, use the [host's LoopX task entry](../../../guides/newcomer-command-path.md)
to let a running agent continue the work. If it stops during a Todo, read back
the original operation and current lease before retrying. A later run may hold
a new lease for the same Todo.
