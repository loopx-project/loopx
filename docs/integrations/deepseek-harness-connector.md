---
title: "DeepSeek Harness integration with LoopX"
description: "Run bounded DeepSeek Harness agent sessions under LoopX goals, quota and validation. Install the optional SDK connector and verify its control-plane boundary."
---

# DeepSeek Harness Connector

Status: public-safe v0 connector for using DeepSeek Harness (`dsh`) as a
bounded agent execution host behind LoopX.

DeepSeek Harness is an open-source agent harness by DeepSeek AI. LoopX does not
replace dsh's model loop, tools, sandbox, or session log. Instead, the connector
lets LoopX govern one dsh-backed work segment at a time through the existing
LoopX Turn protocol:

```text
LoopX quota should-run
    -> loopx turn run-once
    -> loopx.dsh_goal_mode adapter (python -m loopx.dsh_goal_mode)
    -> DeepSeek Harness Python SDK / dsh runtime
    -> typed loopx_turn_result_v0
    -> independent validator
    -> LoopX writeback + quota spend
```

## What This Connector Adds

- A thin adapter, now a first-class goal-mode subpackage at
  `loopx/dsh_goal_mode/` (run with `python -m loopx.dsh_goal_mode`; the
  historical `scripts/dsh_turn_host_adapter.py` launcher still works),
  that translates
  `loopx_turn_host_request_v0` into one bounded dsh session prompt and parses
  the model's final JSON result back into `loopx_turn_result_v0`.
- A `deepseek-harness` agent type in LoopX onboarding so users can request the
  exact host instead of the generic `other-agent`.
- Optional dependency `loopx[deepseek-harness]` for the validated
  `deepseek-harness-sdk==0.1.5rc1` Python client.

## Install

Install LoopX's optional DeepSeek Harness extra:

```bash
python -m pip install 'loopx[deepseek-harness]'
```

The pin tracks the newest published dsh release channel rather than an
unreleased tag: `0.1.5rc1` for the PyPI SDK/runtime wheels and `0.1.5-rc.1` for
the npm `@deepseek-ai/dsh` `latest` tag. Upstream also publishes newer
`next`/`alpha` tags that are not the released channel. This release keeps the
Python client surface of the previously pinned `0.1.2a3` and moves the bundled
dsh runtime; LoopX selects the SDK's default `sdk` profile unless the operator
supplies an explicit cordis composition.

The DeepSeek Harness SDK spawns the bundled `dsh-jsonrpc-agent` runtime. It
uses the explicit adapter configuration plus normal provider environment
variables:

```text
DEEPSEEK_API_KEY
DEEPSEEK_BASE_URL
DSH_HOME
```

The adapter resolves its SDK home in this order: explicit `--dsh-home`, then
`DSH_HOME`, then `<workspace>/.local/.dsh-sessions`. It passes that path as the
SDK's `dsh_home` field; the SDK does not implicitly select `~/.dsh`.

Prepare a dsh `cordis.yml` when the default bundled composition is not
appropriate. See the
[DeepSeek Harness Python SDK reference](https://github.com/deepseek-ai/deepseek-harness/blob/master/python/sdk/README.md)
for runtime selection and configuration.

## Onboard

```bash
loopx doctor --agent-type deepseek-harness

loopx agent-onboard \
  --agent-type deepseek-harness \
  --project . \
  --goal-id <goal-id> \
  --agent-id deepseek-worker \
  --available-capability shell
```

`deepseek-harness` maps to the generic CLI agent loop and uses
`--runtime-profile generic_cli` in quota/heartbeat commands.

## Run One Governed Turn

```bash
loopx turn run-once \
  --goal-id <goal-id> \
  --agent-id deepseek-worker \
  --host generic-cli \
  --execution-mode isolated-headless \
  --project "$PWD" \
  --host-adapter-command-json '["python3", "-m", "loopx.dsh_goal_mode", "--dsh-home", "/path/to/dsh-home", "--cordis", "/path/to/cordis.yml", "--model", "deepseek-v4-flash"]' \
  --validation-command-json '["python3", "/path/to/verify-postcondition.py"]' \
  --execute
```

The adapter uses `<workspace>/.local/.dsh-sessions/` as its workspace-local
SDK home by default. Override it with `--dsh-home <path>`; the historical
`--session-root` spelling remains an adapter-command compatibility alias.
Session persistence itself is owned by the selected dsh composition and is not
implied by the home-directory name.

The headless adapter derives its local session id from a versioned digest of
`[goal_id, agent_id, todo_id]`, preserving component positions so delimiters
inside an id cannot alias another lineage. The same lineage produces the same
id on retries; when all three components are absent, the existing Turn-key
fallback is retained. These opaque ids stay out of public LoopX state.

Upgrading from the former hyphen-joined naming scheme selects a new session id
for a populated lineage. The adapter does not fall back to the ambiguous old
name, rename sessions, or delete existing session files. Any persistence or
resume behavior for the newly selected id remains owned by the dsh composition.

### Task identity and context lifetime / 任务身份与上下文生命周期

`--iteration-context fresh` selects a session scoped to the current Turn key;
retrying that same transaction keeps its identity. The default
`resume-if-available` retains the Goal/Agent/Todo lineage behavior described
above. A durable Agent identity is not a reason to reuse another Todo's task
packet or chat context. Independent questions should select fresh context;
continuations must keep the exact task lineage and refresh explicit inputs.

The built-in DSH adapter forwards `LOOPX_TURN_GOAL_ID`,
`LOOPX_TURN_AGENT_ID`, `LOOPX_TURN_TODO_ID`, and `LOOPX_TURN_WORKSPACE`
from the verified invocation to runtime tools. Those values override stale
caller environment identities; an absent Todo becomes an empty value.
This mapping is an override, not an environment-isolation boundary: the
pinned SDK inherits the parent process environment before applying it, including
any unrelated credentials present there. Launch DSH from an appropriately
scoped environment. These variables bind tools to a request; they do not grant
additional permissions. Domain task packages and
artifact validators must still verify their own task identity and revision.

`fresh` 按本次 Turn 选择新上下文，同一事务重试保持身份；默认
`resume-if-available` 按 Goal/Agent/Todo 延续。Agent 可以长期存在，但独立
问题应使用新上下文，不能把另一个 Todo 的旧任务包当成交接。宿主将上述四个
本次调用身份变量传给运行时工具，覆盖陈旧值；无 Todo 时为空。这是覆盖映射，
不是环境隔离边界：当前 pin 的 SDK 先继承父进程环境，再应用映射，因此父进程
中的无关凭据也会被继承。应从权限适当的环境启动 DSH。身份变量不扩大权限；
领域任务包和产物仍须校验身份与版本。

## Run One Governed Turn In Process (`--host dsh`)

The built-in host runs the same adapter inside the CLI process:

```bash
loopx turn run-once \
  --goal-id <goal-id> \
  --agent-id deepseek-worker \
  --host dsh \
  --execution-mode isolated-headless \
  --project "$PWD" \
  --dsh-home /path/to/dsh-home \
  --dsh-cordis /path/to/cordis.yml \
  --dsh-model deepseek-v4-flash \
  --validation-command-json '["python3", "/path/to/verify-postcondition.py"]' \
  --execute
```

Unlike the subprocess mode, provider failures reach the Turn journal as typed
`loopx_turn_host_failure_v0` kinds (including the SDK's exception-free
`RunResult.finish_reason == "error"` terminal report), so bounded same-Turn
retry stays available. This mode does not promise cross-turn dsh session
continuity or an outer wake/timer. See the adapter README for the home and
classification precedence, plus the hermetic verification smoke
(`examples/loopx-turn-dsh-builtin-host-e2e-smoke.py`).

## Host Selection And Managed Executor Readback

The Turn host is **selected, never inferred from an incidental environment**. An
explicit `--host` (or `--host-adapter-command-json`) or `LOOPX_TURN_HOST` always
wins. With neither configured, the shipped default is resolved from the
operator's own credential facts: `dsh` is the default when `DEEPSEEK_API_KEY` is
configured, because it is the managed execution unit the steward drives and that
credential authenticates it, and `codex-cli` is the default when no credential is
configured, because an unauthenticated managed host would refuse to run.

What runs on that host is a separate resolution. The managed execution profile
defaults to `deepseek-official` / `deepseek-v4-flash` / `high`, overridden by
`LOOPX_TURN_PROVIDER` / `LOOPX_TURN_MODEL` / `LOOPX_TURN_REASONING_EFFORT` and, at
lower precedence, by the legacy `DSH_PROVIDER` / `DSH_MODEL`; an explicit
`--dsh-provider` / `--dsh-model` / `--dsh-reasoning-effort` wins over both. The
steward channel resolves the same profile when it selects the managed host, so
the channel and the bounded Turns it drives cannot land on two different managed
models.

The credential those two surfaces authenticate with is a machine setting rather
than a launch-file variable: `loopx machine-config credential set`, the
Dashboard's machine capability settings, or the service environment, resolving
in that order. The key is write-only and never enters the machine-configuration
document, so an operator changes it from a product surface instead of editing a
launch file and restarting the service. See
[Operator Model Credential](../reference/operator-model-credential.md).

Both `loopx turn plan` and `loopx turn run-once` report a `managed_executor`
block, so a caller reads the planned executor instead of inferring it from a
host id:

```json
{
  "schema_version": "managed_executor_binding_v0",
  "executor": "dsh",
  "executor_kind": "managed",
  "credential_env": "DEEPSEEK_API_KEY",
  "endpoint_env": "DEEPSEEK_BASE_URL",
  "operator_credential_bound": true,
  "execution_profile": "deepseek-v4-flash@high",
  "output_token_budget": {
    "schema_version": "dsh_output_token_budget_v0",
    "scope": "per_model_request",
    "max_tokens": 16384,
    "valid": true,
    "source": "product_default",
    "final_response_reserve_supported": false,
    "hard_tool_budget_supported": false
  },
  "available": true,
  "unavailable_reason": null
}
```

`executor_kind` names where the Turn's model work is billed and bounded:
`managed` for a host bound to an operator credential, `individual` for a host
that runs on one person's own CLI login, and `generic` for a caller-supplied
adapter command. `operator_credential_bound` is the narrower claim: it is `true`
only when the operator credential or an explicit injected runner hook is
configured. `available` is `false` only when LoopX can prove the planned host
cannot launch here, and `null` for executors this projection does not probe
rather than an unproven claim. Only the credential variable *name* is reported;
the value is never read back. `execution_profile` is the resolved profile as one
line, `deepseek-v4-flash@high` in the shipped shape, with the provider prepended
only when it is not the shipped one -- it is one line because every plan carries
it, and the agent-facing output budget is a contract.

`output_token_budget` is deliberately explicit about scope. With the validated
DeepSeek Harness 0.1.5rc1 runtime, `maxTokens` caps each conversation-model
request, not the sum of a tool-using Turn, and reasoning tokens are part of the
reported output-token count. LoopX uses a product default of `16384` for both managed Turns and Chat
segments, replacing the `256000` default observed on this SDK route. Explicit
positive `--dsh-max-tokens` values retain their meaning; the cap reduces the
maximum output allowance and is not a promise that a request will fit.
The SDK exposes neither a hard tool-call budget nor a final-response reserve,
so both are reported as unsupported instead of inferred. A `max-tokens` stop is
`output_budget_exhausted`, never ordinary success or an automatic same-request
retry; `no_final` and `partial` are distinguished in the public-safe failure
reason while the raw session remains local.

`output_token_budget` 会明确说明预算作用域。经验证的 DeepSeek Harness
0.1.5rc1 中，`maxTokens` 限制每次模型请求，而不是整个含工具调用的 Turn；推理
token 也计入输出 token。LoopX 使用 `16384` 的有界产品默认值，不静默继承适配器
更大的精确路由默认值。SDK 没有提供硬工具调用预算或最终答复预留，因此控制面会
明确标记为不支持。托管 Turn 与 Chat segment 使用同一默认上限，替换该 SDK
路由原有的 `256000`；显式正整数上限仍有效，有界默认值不保证请求必定完成。
`max-tokens` 必须归类为 `output_budget_exhausted`，不能当作
普通成功或自动重试；公开安全的 reason 区分 `no_final` 与 `partial`，原始 Session
仍只保留在本地。

`runtime_probe` distinguishes an import probe (`scope: "probing_interpreter"`,
`module: "deepseek_harness"`) from an injected runner (`scope: "configured_runner"`,
`module: null`, no import attempted). Availability applies to the answering
interpreter or runner, not the whole machine, and does not prove provider
authentication. The Chat refusal directs the operator to run `loopx doctor`
in the service environment, check `python.executable`, and install the SDK in
that same environment before restarting. Interpreter paths stay in local doctor
output, outside the Turn payload.

`run-once --execute` fails closed on that verdict: status `unavailable`, no host
invocation, no journal write, and no quota spend, with
`dsh_runtime_unavailable`, `operator_credential_unconfigured`, or
`invalid_reasoning_effort` / `invalid_output_token_limit` naming the missing
fact. `plan` reports the same verdict without refusing.

## Boundaries

- LoopX keeps the durable goal, todo, claim, gate, quota, evidence, and
  scheduler authority.
- dsh owns model calls, tools, sandboxing, and the raw session log.
- The adapter must not publish raw transcripts, dsh JSONL sessions, credentials,
  local absolute paths, or unbounded tool output into LoopX state.
- `DeepSeekHarness.run()` returns the candidate result; it is not proof of
  completion. An independent validator is required before LoopX writeback.
- The dsh Python SDK is an optional dependency. Core LoopX remains runtime
  dependency-free.
- The adapter derives an owner-local session id, but LoopX does not project or
  validate a DSH Host Session Binding. This surface therefore does not claim a
  managed supervisor or cross-process resume guarantee.

## Hermetic Validation

The repository includes four validation paths. The first three do not require the
DeepSeek Harness SDK or a real dsh runtime:

```bash
python3 examples/dsh-turn-host-adapter-smoke.py
python3 examples/loopx-turn-dsh-e2e-smoke.py
python3 examples/loopx-turn-dsh-builtin-host-e2e-smoke.py
```

The first guards adapter translation and result shaping. The second drives the
full `loopx turn run-once -> adapter -> fake dsh -> validator -> writeback ->
quota spend -> idempotent replay` chain. The third proves the built-in host's
success path plus three bounded provider-capacity attempts, retry-budget
exhaustion without a fourth Host invocation, zero failure spend/writeback, and
provider-prose non-persistence.

The fourth uses the real `deepseek-harness-sdk` and the bundled dsh JSON-RPC
runtime. It still avoids a real model call by serving a local mock OpenAI-compatible
SSE endpoint, so it is hermetic and does not require `DEEPSEEK_API_KEY`:

```bash
python3 examples/loopx-turn-dsh-real-e2e-smoke.py --host generic-cli
python3 examples/loopx-turn-dsh-real-e2e-smoke.py --host dsh
```

The real-dsh smoke clears ambient DSH home variables and proves that both host
paths can supply an explicit SDK home, start the actual dsh runtime, run one
bounded turn through the real JSON-RPC agent loop, parse a typed JSON final
message, and complete LoopX validation/writeback/quota spend.

## Related Contracts

- [DeepSeek Harness control-plane adapter](deepseek-harness-control-plane-adapter.md)
- [Runtime connector catalog](runtime-connector-catalog.md)
- [LoopX Turn v0](../reference/protocols/loopx-turn-v0.md)
- [Host integration surface v0](../reference/protocols/host-integration-surface-v0.md)
- [Embed LoopX In Your Agent Runner](../guides/custom-agent-runner-integration.md)
