# LoopX KunlunCode adapter

The KunlunCode adapter is a first-class LoopX host surface. It uses its own
project binding and registered agent identity; it does not read
`.claude/loop.md` or execute as Claude Code's `cc` lane.

## Command boundaries

LoopX and KunlunCode do not share one command namespace:

- `loopx ...` and `loopx-kunluncode ...` are shell commands owned by the
  LoopX control plane;
- `/goal`, `/goal-pro`, `/plan`, and `/mcp` are native KunlunCode TUI slash
  commands with KunlunCode session state;
- `should_run`, `list_todos`, `claim_task`, and `complete_task` are MCP tools
  called by the model, not slash commands typed by the user.

Within KunlunCode's native Goal family, `/goal-pro` keeps `/goal`'s persistent
objective lifecycle and adds a mandatory independent verifier before completion.
Its Strict/Arrangement delegation rules are the execution mechanism for that
completion gate, not a separate LoopX lifecycle.

`loopx-kunluncode run` now uses KunlunCode's machine-readable app-server. The
default `--mode goal-pro` creates or resumes a real Kunlun thread, calls
`thread/goal/set` in `strict` mode, starts the first turn, lets KunlunCode drive
native auto-continuations, and accepts completion only after
`verification_passed`. `--mode goal` selects native Arrangement mode without
the strict verifier gate. Neither mode types a slash command into a prompt;
they activate the same native lifecycle through its deterministic API.

LoopX remains the outer controller. It selects and claims one todo before host
execution, journals the opaque native thread/goal identity in ignored local
state, and performs delivery/todo/quota writeback only after the native terminal
state is accepted. During a native run the model-visible MCP `claim_task` and
`complete_task` tools, plus direct LoopX lifecycle CLI writes targeting the
bound goal, fail closed. The model therefore cannot commit LoopX before the
native verifier. `--mode headless` preserves the earlier one-turn MCP worker as
an explicit compatibility mode.

## Install and connect

For a packaged install, install LoopX normally and let the adapter provision its
owned MCP environment through `uv`:

```bash
python3 -m pip install --upgrade loopx
loopx-kunluncode install
loopx-kunluncode connect \
  --project . \
  --goal-id my-goal \
  --agent-id kunlun
```

The provisioner installs the same LoopX distribution version and `mcp==1.28.1`
outside a source checkout. Contributors can instead use one checkout-local
uv-managed environment:

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e . 'mcp==1.28.1'
.venv/bin/loopx-kunluncode connect \
  --project . \
  --goal-id my-goal \
  --agent-id kunlun \
  --python .venv/bin/python
```

The commands below use the packaged `loopx-kunluncode` entry on `PATH`;
checkout users can substitute `.venv/bin/loopx-kunluncode`.

KunlunCode currently stores MCP registrations in its user configuration even
when project or overlay settings contain `mcp_servers`. The installer therefore
creates one explicitly named global entry, `loopx-kunluncode`; project and
identity selection still come from the current working directory and the
ignored `.loopx/kunluncode.json` binding.

The installer never overwrites a foreign same-name entry unless `--replace` is
explicit. If adding the replacement fails, it restores a command-based previous
entry; registrations that cannot be safely reconstructed are rejected before
removal.

Read the connection back:

```bash
kunluncode --cwd "$PWD" mcp test loopx-kunluncode
loopx-kunluncode status --project .
```

## Run a native Goal

Add one bounded task and run the default native Goal Pro controller:

```bash
loopx-kunluncode add --project . "Run the focused check and record the result"
loopx-kunluncode run --project . --permission-mode auto
```

The native transaction is:

```text
LoopX should-run / selected todo / claim
  -> app-server initialize
  -> thread/start or thread/resume
  -> thread/goal/set(mode=strict)
  -> turn/start + native auto-continuations
  -> thread/goal/get(status=complete, verification_passed)
  -> LoopX refresh-state / todo complete / quota spend
```

The default app-server path is non-interactive, so its default permission mode
is `auto`; `ask` fails with an actionable error instead of hanging on an
approval request. This selects KunlunCode's permission behavior but grants no
new LoopX authority. Use `--controller-timeout-secs` for the total native Goal
window, `--max-duration-secs` for KunlunCode's per-turn soft budget, and
`--token-budget` for an optional native Goal token budget.

Inspect the native and LoopX state together:

```bash
loopx-kunluncode status --project .
.venv/bin/python examples/kunluncode-app-server-goal-pro-smoke.py --require
```

The ignored `.loopx/kunluncode-runtime.json` journal contains only binding
identity, opaque native ids, an objective digest, compact terminal state, and
writeback receipts. If the controller is interrupted, rerun the same command:
it resumes the same native thread, or reconciles an already verified terminal
state without repeating completed LoopX writeback phases.

Use native `/goal` semantics without the strict verifier with
`--mode goal`. Use the former one-turn MCP lifecycle only when compatibility is
required:

```bash
loopx-kunluncode run --project . --mode goal
loopx-kunluncode run --project . --mode headless --permission-mode auto
```

## Disable and remove

Stop invoking `run` to disable execution without changing state. A later native
run resumes the same active journal. Remove the host-wide MCP entry with:

```bash
loopx-kunluncode uninstall
```

Native app-server mode does not require MCP writeback, so uninstalling the MCP
entry does not disable native execution. Remove `.loopx/kunluncode.json` only
when the project should no longer resolve a KunlunCode identity. Delete
`.loopx/kunluncode-runtime.json` only after its phase is `committed`, or when
you intentionally abandon the recorded native thread. Removing either local
file does not delete LoopX goals, todos, run history, KunlunCode's persisted
thread, or another host's adapter.

## Authority and privacy boundary

Activation grants no repository write, publish, destructive, credential,
external-sink, or production authority. The selected todo, checkpointed LoopX
write boundary, and KunlunCode permission mode all still apply. Native terminal
proof is a completion gate, not an authority grant. The controller suppresses
external sink delivery during its compact refresh-state writeback. Binding,
runtime journal, active goal state, and run evidence stay below ignored
`.loopx/` or the private LoopX runtime and must not be committed.
