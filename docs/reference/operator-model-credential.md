# Operator Model Credential

Status: shipped. Applies to the steward channel and to the managed Turn host.

LoopX runs two model surfaces on an operator-supplied credential: the steward
channel a person talks to, and the managed Turn host the steward drives. Both
authenticate with a provider API key and, when the endpoint is not the provider
default, a base URL.

This document is the contract for where that pair lives, what may read it, and
which layer wins when more than one layer sets a field.

## Where It Lives

The credential is its own file, not a machine-configuration namespace:

```text
<machine-runtime-root>/machine/credentials/operator_provider.json
```

The canonical machine runtime is `~/.loopx` for new installations; a
legacy-only machine stays on `~/.codex/loopx` until the
[explicit migration](../product/migrations/local-state-path-migration.md).
This selection is independent of `CODEX_HOME`
and any Goal's `common_runtime_root` or Turn `--runtime-root`. Turn planning,
default host selection, dispatch and delegation readiness use this machine
store. A Goal-local credential file does not override it. Explicit machine
credential APIs retain their root parameter for operating an isolated machine
store; a Goal runtime override is not that parameter.

This corrects the previous behavior where isolated Goal runtimes missed an
already configured machine credential, and parser defaults consulted only the
process environment. With a valid machine credential, an otherwise unspecified
Turn host now consistently resolves to `dsh`; explicit `--host` and
`LOOPX_TURN_HOST` still win. SDK availability is checked in the launching
interpreter separately from credential availability.

机器凭证由机器运行目录统一管理，Goal 只决定 Agent 分配、执行配置和授权。
隔离 Goal 的运行目录不再遮蔽已配置的机器凭证；未指定 Turn host 时，
有效的机器凭证会按既有规则选择 `dsh`。显式 host 选择保持优先，
DSH SDK 是否安装仍按实际执行的 Python 环境检查。

The directory is mode `0700` and the file is mode `0600`, written atomically.
It is deliberately **not** part of `machine/configuration.json`: that document
is projected to the browser, read back by `loopx machine-config describe` and
`inspect`, and copied into per-transaction backups and rollback plans, so a
secret stored there would be readable from four surfaces and copied by every
unrelated settings change.

```json
{
  "schema_version": "operator_provider_credential_v0",
  "provider_key": "<provider key>",
  "base_url": "https://endpoint.example/v1"
}
```

## What May Read It

The key is **write-only**. No readback returns its value:

| Surface | Reads |
| --- | --- |
| `GET /api/chat/operator-credential` | status, each field's source, the key's truncated `sha256` fingerprint |
| `loopx machine-config credential status` | the same projection |
| the machine-configuration document | nothing -- the key is never stored there |
| the host process | `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` in its own environment |

The fingerprint exists so an operator can answer "is the key I just stored the
one that is running?" without any surface being able to read the key back.

## Resolution Order

Resolution is field by field, and the machine store outranks the process
environment:

1. **machine store** -- the operator's explicit choice, made in a product
   surface and read back with its own source;
2. **service environment** -- `DEEPSEEK_API_KEY` and `DEEPSEEK_BASE_URL`, the
   bootstrap for a machine whose store is not written yet and the escape hatch
   for a launch file that must override one field.

A store that holds only a base URL does not hide an environment-provided key:
each field resolves on its own.

This order matches the `steward_executor` machine setting, where the machine
value also outranks the service environment, so the two machine-level settings
a person edits do not follow two different precedence rules.

A credential **authenticates** the configuration that runs; it never
**selects** one. Storing a key here does not move the steward off its resolved
endpoint or the managed host off its resolved execution profile. It does
resolve the shipped default of a surface that would otherwise have to run on an
individual CLI login, which is why a stored key makes `dsh` the default managed
host.

## Failure Behaviour

A record this machine cannot read resolves to **no credential**, not to the
environment. Authenticating a surface with a credential the operator can no
longer see in the product surface that owns it is worse than refusing, so the
managed surfaces report `invalid` with the repair step instead, and the steward
stays on the individual executor.

An update is a **merge**: submitting only a key keeps the stored base URL.
Clearing is explicit (`clear_provider_key` / `clear_base_url`, or
`loopx machine-config credential clear`), so an empty form field can never
delete a credential the operator did not mean to touch. Clearing the last field
removes the file rather than leaving an empty record that would read as
"configured".

## Operating It

```bash
# The value stays out of shell history and argv.
printf '%s' '{"provider_key":"...","base_url":"https://endpoint.example/v1"}' \
  | loopx machine-config credential set --config-json -

loopx machine-config credential status
loopx machine-config credential clear
```

The Dashboard's machine capability settings expose the same read and write
through `/api/chat/operator-credential`.

## Choosing What Answers, And Where

Two surfaces answer on this machine, and they are selected separately.

- **The steward channel** is the conversation a person talks to: the Dashboard
  manager channel and the bound Lark/Feishu manager group. Its machine setting
  names a primary `steward_executor.executor_endpoint` (`codex`, or `dsh` for
  the managed host) and a `preferred`, `pinned`, or `flexible` selection policy.
  Flexible selection is confined to the configured eligible endpoint pool.
- **A managed Turn or managed agent** is bounded work that runs without a person
  in the loop. Its host resolves from the operator credential: with one stored,
  the shipped default is the managed host `dsh`, and without one it is the
  interactive CLI endpoint.

Choose the steward on the managed host when the machine should answer from an
API-backed host instead of an individual CLI login; keep the interactive CLI
endpoint when the steward must run as the operator's own logged-in session.
These are independent: setting the steward to `dsh` does not move any managed
Turn, and storing a credential does not switch the steward.

### Selecting and reading it back

```bash
# List the registered machine-configuration namespaces.
loopx machine-config describe

# Read the stored document and the effective steward resolution.
loopx machine-config inspect

# Read the effective steward binding and the current Session allocation.
loopx chat-endpoint inspect-steward

# Preview an exact change, then apply it with the plan revision it returned.
loopx machine-config preview
loopx machine-config apply
```

The service environment remains the bootstrap and escape hatch, and it is
lower precedence than the machine document: `LOOPX_MANAGER_ENDPOINT` selects the
steward endpoint, `LOOPX_MANAGER_MODEL` and `LOOPX_MANAGER_REASONING_EFFORT`
select its model and effort. A managed Turn resolves its profile from
`LOOPX_TURN_PROVIDER`, `LOOPX_TURN_MODEL` and `LOOPX_TURN_REASONING_EFFORT`,
then from the shipped managed profile.

Every entry point publishes the same readback, so a reader never has to infer
the host from the name it resolved: `/api/chat/capabilities` reports the
steward's `executor_endpoint`, its `executor_endpoint_source`
(`machine_configuration`, `explicit_config` or `product_default`), the
`execution_profile` (`deepseek-v4-flash@high` on the shipped managed profile),
`available`, the selection policy and allocation reason, and the bound
Session's `session_mode` and `session_status`. The Session persists its chosen
endpoint, model, effort, policy, pool and source revision, so later configuration
edits apply through a new allocation rather than rewriting an active conversation.
A connection record stores the resolved endpoint as an observation, so it cannot
outrank the machine setting.

### Disabling or rolling back

```bash
# Preview removing the namespace, then apply the returned plan revision.
loopx machine-config remove

# Preview a rollback to the previous revision, then apply it.
loopx machine-config rollback
```

Unsetting the environment variables restores the same lower layers. With no
machine document and no environment override, the steward resolves to the
shipped default (`codex`) and a credential-less machine keeps the interactive
CLI endpoint, exactly as a machine that never configured anything.

### What this does not authorize

The credential authenticates the selected endpoint; it never selects one. The
steward channel still only proposes: it may describe work, and it may hand an
authorized intent to a worker, but Todos, agent registration, quota and goal
policy change only through their canonical owners and only after the owner's
confirmation. Selecting the managed host grants no new filesystem, provider or
audience permission, and it does not let a conversation change hosts mid-thread.

## Authority Boundary

Storing a credential grants no authority. It does not select an executor, a
model, or a reasoning effort; it does not widen a Turn's tool scope, sandbox, or
declared evidence sources; and it does not let a managed surface run when the
selected runtime is missing. Those remain separate, typed decisions.
