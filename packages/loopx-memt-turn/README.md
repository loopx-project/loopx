# Optional MemT Nano Turn host

[中文版](README.zh-CN.md)

This experimental package lets a LoopX-governed Turn use MemT-1.0-Nano and
restore native memory from earlier Turns. It is installed and selected separately;
LoopX's default models, text recall, Reward Memory and state stores do not change.
It does not add native memory to Codex or Claude: the selected Turn runs on Nano.
Improved long-horizon outcomes and lower total costs remain unverified.

The existing `generic-cli` Turn contract owns admission, independent artifact
validation, canonical writeback and quota. This package is a Python provider
transport because MemT's public client is Python. It adds no capability id,
control-plane decision owner, scheduler or automatic host selection.
This is the bounded CLI/provider prerequisite. App/Lark configuration and real
GPU qualification are not delivered by this package; the existing Turn frontend
owner would need to qualify that journey before calling this a complete product
memory setting.

## Prerequisites and licensing

Use LoopX 2.1.0+ with its supported `extensions.turn_host` seam, Python
3.11+, and a separately operated Nano SGLang service. See the upstream
[inference guide](https://github.com/MemTensor/MemT-1.0/blob/e75c13d090a6a9d34b29146d901bd84323185f24/inference/README.md)
for GPU deployment. The local client needs matching tokenizer and configuration
assets; it does not load model weights. This adapter supports Nano only.

The upstream **client and inference code use PolyForm Noncommercial 1.0.0**.
Nano's weight license is separate. Review upstream and third-party terms before
use; placing the server in another process does not remove license obligations.
This package does not bundle, download or automatically install upstream code,
weights or CUDA packages. Arrange the required permission for commercial use.

Install into an isolated environment from the LoopX checkout root:

```sh
python3 -m venv .venv-memt
. .venv-memt/bin/activate
python -m pip install -e . -e packages/loopx-memt-turn
```

After reviewing the upstream license, install its client at the tested revision:

```sh
python -m pip install 'memt-sglang[client] @ git+https://github.com/MemTensor/MemT-1.0.git@e75c13d090a6a9d34b29146d901bd84323185f24#subdirectory=inference'
```

The tested client extra pins Transformers 5.12.1. Jinja2 is installed by this
adapter for chat-template rendering. No PyTorch or SGLang server dependency is
installed by the adapter. Start the separately provisioned server before use.

## Configure and select

Create an operator-owned JSON file **outside the task workspace**, for example
`/srv/operator/memt.json`. Replace the paths with your own absolute locations:

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

`enabled` defaults to false if omitted. Run the local preflight:

```sh
loopx-memt-turn --config /srv/operator/memt.json --doctor
```

Doctor checks client-module availability and the existence of `config.json`.
It reports `network_checked=false` and `model_inference_verified=false`; success
does not prove tokenizer completeness, server readiness or memory quality. A
disabled configuration can pass doctor without the client or model assets.
Set `enabled` to true only for the selected profile. Keep credentials in
`MEMT_API_KEY`, never in the file or URL. Remote endpoints require HTTPS;
unencrypted HTTP is restricted to loopback.

For work that needs tools, set `mcp_command` to an operator-selected stdio MCP
server argv and `tool_names` to at most eight exact names. All tool pages are
discovered; missing or duplicate names fail. Only text/structured-JSON results
are supported. Empty lists provide no tools. There is no shell or automatic
filesystem tool exposure.

The selected MCP server must enforce the current signed resource/operation
scope. It receives `LOOPX_TURN_KEY`, `LOOPX_TURN_GOAL_ID`, `LOOPX_TURN_AGENT_ID`,
`LOOPX_TURN_GOAL_INSTANCE_ID`, `LOOPX_TURN_TODO_ID` and `LOOPX_TURN_WORKSPACE`; those are scope inputs, not
authentication. Provider credentials and arbitrary parent environment values
are not forwarded. This adapter is not an OS sandbox. Do not select a server
that grants unrestricted effects based on model arguments or remembered text.

For an existing registered Goal, Agent and eligible claimed Todo, use the
normal Turn arguments and independent validator, with these explicit options:

```sh
loopx turn run-once --goal-id demo --agent-id analyst --todo-id todo_demo \
  --project /srv/work/project --host generic-cli --iteration-context fresh \
  --host-command-json '["loopx-memt-turn","--config","/srv/operator/memt.json"]' \
  --validation-command-json '["python3","/srv/operator/check_artifact.py"]'
```

This previews the Turn. Supply an operator-authored validator that independently
checks the real artifact, then add `--execute` to the reviewed command. Existing
admission and permission requirements still apply. The adapter returns one
`loopx_turn_result_v0` candidate; a successful model reply is not acceptance.
Execution must start in the configured workspace; the normal Turn launcher sets
that working directory. A mismatch fails before any provider calls.
`fresh` creates a new execution session while explicitly restoring advisory
native memory, not an old host conversation or its authority.

## Persistence and failure recovery

Memory scopes are isolated by resolved workspace, exact Goal lifetime and Agent.
The request must carry the executor's matching `goal_ref` with a valid
`goal_instance_id`; alias-only or mismatched requests fail before IO. Existing
alias-only memory directories are not imported or deleted. Reconcile their
pending effects separately; a new lifetime begins with empty memory.
Before provider or MCP IO, the host holds the old alias cursor's lock alongside
the new instance lock. A pending head, unfinished receipt or orphaned receipt
in that old directory fails with `legacy_memory_requires_operator_reconciliation`;
unreadable state also fails. Old records cannot identify a Goal lifetime, so
changing the instance does not bypass this gate. Stop the old worker, reconcile
its exact effects through the existing Turn recovery owner, then explicitly
select a new `state_dir`. Do not delete the evidence. Completed old records stay
untouched and are not imported; they do not block empty new-instance memory.
Each new Turn
opens a MemT session, imports the scope's last confirmed snapshot, runs the
model/tool chain, exports a new snapshot, closes the session, then records the
new cursor. Server restarts use a new epoch and explicit snapshot import.
Keep the server's snapshot directory durable; local receipts do not contain
the memory tensors. No transcript, token arrays or model reasoning are logged
by this adapter; private local receipts contain configuration/request hashes,
session/snapshot references and the bounded candidate.

Completed logical model/tool Turns are saved **before** the outer independent
validator accepts or rejects the artifact. Memory therefore includes unverified
observations and failed work. It is not a store of accepted procedural lessons.
Current task state, authority and evidence must be read from LoopX and current
artifacts on every Turn. Native memory never replaces those records.

The complete encoded model/tool Turn must fit the server's prefill limit when
saved. Overflow, truncation and malformed candidate output fail explicitly;
the adapter does not silently trim history or fabricate a success. Limits apply
per model request/tool count; the outer Turn supervisor owns the process deadline.
Every tool round, including a continuation, must complete generation before
dispatch. A truncated round cannot execute its tools or publish a snapshot;
earlier tool effects remain real and require ordinary reconciliation.

The MCP SDK detaches its stdio child. A raw stdio relay therefore keeps the
actual tool under LoopX's existing TypeScript process supervisor. Parent EOF,
cancellation and hard parent exit terminate the tool before that supervisor
returns on qualified POSIX paths. This is not a sandbox and does not constrain
deliberately detached tools. Windows tree cleanup remains best effort and is not
qualified as a strict stop boundary by this package.

Repeating an identical completed request returns its saved candidate without
running tools or the model again. A changed request under the same Turn key,
changed operator configuration or changed tool schemas fails. Replaying an old
completed Turn never moves a newer memory cursor backward.

An interrupted/uncertain attempt leaves a private `running` receipt and blocks
that scope. Errors include `interrupted_turn_requires_operator_reconciliation`
and `prior_turn_requires_operator_reconciliation`; SDK/MCP failures are emitted
as bounded exception-type codes without provider bodies. Stop the old worker,
inspect the receipt's exact server session and current tool/artifact effects,
then reconcile through the existing Turn recovery owner. Do not delete a pending
receipt and rerun tools. After reconciliation, an operator can select a new
private `state_dir` and a newly admitted Turn. This starts with empty memory;
it does not undo earlier effects. Automatic reconciliation is not implemented.

Snapshots bind to the upstream model/configuration/tokenizer identity. That
identity does not hash weight contents. Pin the server checkpoint yourself and
use a new `state_dir` when changing it, even if its configuration is unchanged.
The adapter does not delete server snapshots or implement retention. The
operator owns private storage, backups and removal after active use ends.

To disable, stop active work, remove the explicit host-command selection and/or
set `enabled` to false. Restore the prior host profile. Uninstall with
`python -m pip uninstall loopx-memt-turn`. This does not uninstall LoopX, stop
the external server, delete memory snapshots or alter canonical work state.

## Validation boundary

After installing the reviewed client above, run from the LoopX checkout:

```sh
python -m pip install -e '.[test]' -e packages/loopx-memt-turn
python -m pytest -q packages/loopx-memt-turn/tests
python -m ruff check packages/loopx-memt-turn
```

The suite uses the real MemT client, a local HTTP protocol fixture, a small real
tokenizer and a real stdio MCP subprocess. It checks snapshot import/export,
restart epochs, owner isolation, duplicate-effect prevention, candidate
compatibility, disabled behavior and failure handling. Model outputs are
synthetic. These checks establish adapter contracts, not real GPU inference,
server tensor durability, App/Lark adoption or long-horizon benefit. Measure
Nano with/without native memory under fixed tasks and budgets before promotion.
