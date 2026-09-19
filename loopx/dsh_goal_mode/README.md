# LoopX DeepSeek Harness (dsh) goal mode

First-class host adapter that turns a DeepSeek Harness session into a
LoopX-governed goal loop. LoopX keeps authority (goal/todo state, quota,
validation); the adapter only translates one governed Turn into one bounded
dsh work segment and shapes the reply into a typed result.

## Surface

The `deepseek-harness` host surface (aliases: `deepseek_harness`,
`DeepSeek Harness`, `dsh`) is an external loop driver:

1. `loopx start-goal --guided --project . --host-surface deepseek-harness`
   returns the host-loop activation packet after todo writeback.
2. Every automatic tick starts from `loopx quota should-run`
   (`--runtime-profile generic_cli`) and stops when it says stop.
3. Each approved tick runs `loopx turn run-once` with this adapter as the
   generic-cli host adapter command.
4. The adapter reads one `loopx_turn_host_request_v0` JSON object on stdin,
   extracts the signed TurnEnvelope authority, asks dsh to execute the bounded
   `primary_action`, and emits exactly one `loopx_turn_result_v0` JSON object
   on stdout.
5. LoopX validates the typed result independently before writing any state or
   spending quota.

## Run the adapter

```bash
python -m loopx.dsh_goal_mode \
  --cordis /path/to/cordis.yml \
  --model deepseek-v4-flash \
  --provider deepseek-official
```

The historical launcher still works and resolves to the same implementation:

```bash
python3 scripts/dsh_turn_host_adapter.py --cordis /path/to/cordis.yml
```

Flags: `--provider`, `--model`, `--max-tokens`, `--workspace`,
`--dsh-home` (`--session-root` remains a compatibility alias), `--cordis`,
`--runtime-bin`, `--request-timeout-seconds`, and `--dsh-runner`. The runner
option is an explicit test hook; LoopX does not machine-enforce where it may be
used, and the caller already owns local host-execution authority.

## In-process host (`--host dsh`)

`loopx turn run-once --host dsh` runs the same adapter inside the CLI process
instead of a generic subprocess. Provider failures then reach the Turn
journal as typed `loopx_turn_host_failure_v0` kinds instead of collapsing to
`unknown` through a bare nonzero exit, so bounded same-Turn retry stays
available. Both failure shapes are covered: a raised transport exception and
the SDK's normal terminal report (`RunResult.finish_reason == "error"` with a
structured `turn/end` reason and no Python exception at all).

Classification follows the `loopx-turn-v0` precedence: a known provider
`error.code` wins over HTTP status and prose, an unknown non-empty code fails
closed to `unknown` and blocks every lower tier, HTTP status decides only
when no more specific code exists, and bounded message matching applies only
when no structured signal exists. Signals that disagree within one tier fold
to `unknown`. The SDK's stable `SERVER` code covers HTTP 5xx responses and maps
to the retryable `provider_overloaded` bucket even when an intermediate
serializer omits the duplicate HTTP status. Its hard-quota `QUOTA` code remains
non-retryable, while its explicitly retryable `EMPTY_RESPONSE` code maps to the
closest LoopX transient bucket, `transport_lost`.

CLI flags: `--dsh-provider`, `--dsh-model`, `--dsh-reasoning-effort`,
`--dsh-max-tokens`, `--dsh-home`, `--dsh-cordis`, `--dsh-runtime-bin`, and
`--dsh-runner`.
Against the current SDK config surface the home maps to `dsh_home`, the runtime
binary maps to `dsh_bin`, and a cordis file rides as one `patches` entry.
Home precedence is explicit CLI value, then `DSH_HOME`, then
`<workspace>/.local/.dsh-sessions`; LoopX creates the selected local directory
before SDK launch. Session persistence still belongs to the selected dsh
composition, so this mode does not promise cross-turn dsh session continuity.
The generic-cli subprocess preserves its legacy contract: an exception-free
terminal SDK error produces a typed `wait` result, while the in-process host
turns the same outcome into a retry-aware host failure. Hermetic verification:
`python3 examples/loopx-turn-dsh-builtin-host-e2e-smoke.py`.

The SDK derives `finish_reason` from the last `turn/end.reason.kind`; the
adapter rejects contradictory or malformed runner outcomes as
`contract_rejected`. Result parsing and shaping failures use the same typed
contract failure instead of collapsing into `unknown`.

### Output budget semantics / 输出预算语义

LoopX defaults `--dsh-max-tokens` and managed Chat segments to `16384`. DeepSeek Harness 0.1.5rc1 applies
that value to **each model request**, includes reasoning tokens inside output
tokens, and does not expose either a whole-Turn hard tool budget or a reserved
final-response budget. The managed-executor projection therefore reports
`scope=per_model_request`, the effective limit and both unsupported controls;
callers must not present it as a whole-run budget.

When the SDK terminates with `finish_reason=max-tokens`, the built-in host emits
the non-retryable `output_budget_exhausted` failure. Empty output is recorded as
`dsh_output_budget_exhausted_no_final`; non-empty output is still treated as an
untrusted partial fragment and recorded as
`dsh_output_budget_exhausted_partial`. Neither path validates, writes state,
spends quota, or automatically repeats the request. The subprocess adapter
returns the same distinction as a typed `iteration_failed` result because a
nonzero process exit would erase it. A recovery is allowed only after a caller
proves an explicit remaining budget and reusable evidence from the retained
local session. Managed Chat uses the same limit resolver and terminal classifier;
a truncated segment emits a typed error without publishing an answer or adding
the fragment to visible history. This applies to frontend and Lark consumers of
the shared Chat runtime.

LoopX 将 `--dsh-max-tokens` 默认设为 `16384`。在 DeepSeek Harness 0.1.5rc1
中，这个值限制的是**每次模型请求**，推理 token 计入输出 token；SDK 目前不提供
整 Turn 的硬工具调用预算，也不支持为最终答复预留 token。因此控制面会明确投影
作用域、有效上限和两项不支持的能力，调用方不得把它描述为整次运行预算。

SDK 以 `finish_reason=max-tokens` 结束时，内置 host 会产生不可自动重试的
`output_budget_exhausted`。空输出标记为 `no_final`，非空输出也只视为不可信的
`partial` 残片；两者都不会进入验证、写状态或消耗配额。只有在调用方能够证明
显式剩余预算和可复用本地证据时，才可以规划新的有界恢复，禁止盲目重跑。
托管 Chat 复用同一上限与终止分类；截断 segment 只返回类型化错误，不发布答复、
不写入可见对话历史。前端和 Lark 都通过共享 Chat runtime 获得此行为。

## Requirements

- Optional dependency group `loopx[deepseek-harness]`, currently pinned to the
  validated `deepseek-harness-sdk==0.1.5rc1` API, or a compatible runner via
  `--dsh-runner`.
- A dsh `cordis.yml` plus any `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL`
  settings for the real runtime.
- Provider, model and reasoning effort come from the managed execution profile
  (`loopx/control_plane/turn_driver/execution_profile.py`): product defaults
  `deepseek-official` / `deepseek-v4-flash` / `high`, overridden by
  `LOOPX_TURN_PROVIDER` / `LOOPX_TURN_MODEL` / `LOOPX_TURN_REASONING_EFFORT` and,
  at lower precedence, by the legacy `DSH_PROVIDER` / `DSH_MODEL`. An explicit CLI
  value wins over both, and the resolved profile reports its own source. A
  reasoning effort this adapter cannot honour is a typed
  `dsh_execution_profile_rejected` failure instead of a silent coercion.
  `DSH_HOME` may override the workspace-local home when no CLI home is supplied.

## Boundary

The explicit dsh runtime home (default `<workspace>/.local/.dsh-sessions`)
stays local and never enters public LoopX evidence. The composition, not this
path name, owns session persistence. The adapter never reads goal/todo state,
builds prompts from todo ids, writes LoopX state, spends quota, or validates
its own work. A valid final response with no typed JSON candidate becomes a
conservative `wait`; an invalid runner/result shape is a typed
`contract_rejected` host failure. Neither path may spend quota.

The adapter derives a stable owner-local session id, but LoopX does not yet
project or validate a DSH Host Session Binding. The built-in surface therefore
does not claim managed supervisor recovery, outer wake/timer ownership, or a
cross-process resume guarantee.

See `docs/integrations/deepseek-harness-connector.md` for the full connector
walkthrough and `examples/dsh-turn-host-adapter-smoke.py` plus
`examples/loopx-turn-dsh-e2e-smoke.py` for hermetic smokes. With the optional
SDK installed, run `examples/loopx-turn-dsh-real-e2e-smoke.py` once with
`--host generic-cli` and once with `--host dsh`; both paths clear ambient DSH
home variables and prove the explicit SDK-home wiring.
