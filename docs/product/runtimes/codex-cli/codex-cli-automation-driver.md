# Codex CLI Adapter for LoopX Turn

Status: experimental product route with a shipped isolated-headless driver.

For the partner-facing path, start with
[Run One LoopX Turn With Codex CLI](loopx-turn-codex-cli-quickstart.md). It
reduces this maintenance reference to the built-in adapter, one independent
validator, and one command.

The product goal is one reusable mechanism: LoopX CLI decides what may run,
Codex CLI performs one bounded agent turn, and LoopX validates and records the
outcome. It should approach the control-plane behavior available in Codex App
without copying App-specific heartbeat logic or turning Codex session files
into project state.

The host-neutral lifecycle is defined by
[`loopx_turn_v0`](../../../reference/protocols/loopx-turn-v0.md). This page records
the Codex CLI adapter policy and the current parity gap.

## Current Verdict

`loopx turn plan` and `loopx turn run-once` now provide the host-neutral driver
for explicit `isolated-headless` work. The built-in `codex-cli` host and the
typed `generic-cli` adapter both consume the checked complete source for a new
Turn. A material result commits only after independent task validation, durable state writeback,
one quota spend, and a final scheduler check.

The Codex host keeps its resume id in private local runtime state keyed by goal,
agent, and todo. Bounded interruption may preserve an observed session;
incompatible host versions, rejected output contracts, missing sessions, and
legacy eligibility records start cleanly instead. Session recovery never counts
as task evidence.

The remaining parity gaps are narrower:

- `interactive-visible` still needs attach, idle, interruption, and takeover
  proof before it can become a supported Turn execution mode;
- a non-Codex conversational CLI such as Trae still needs a thin adapter that
  supplies a deterministic typed result channel;
- recurring external scheduling must compose the existing `run-once` receipt
  and scheduler hint without overlapping a live host turn; and
- benchmark promotion still requires matched, countable comparative evidence.

The older `codex-cli-local-scheduler-*` commands remain diagnostics and
compatibility probes. They are not the default orchestration narrative and must
not be composed manually as a second control plane.

## Codex App Parity Matrix

| Capability | Codex App baseline | Current Codex CLI route | v0 driver requirement |
| --- | --- | --- | --- |
| Persistent identity | Automation thread plus registered LoopX agent | Goal, agent, and todo are authoritative; the resume id stays in private runtime state | Keep the session handle opaque, local, and non-authoritative |
| Wake and resume | Heartbeat wakes the existing thread | `run-once` starts or resumes only an eligible local session | Add a non-overlapping recurring wake host and interactive attach proof |
| Fresh control decision | Agent runs live `quota should-run` and follows `interaction_contract` | `turn plan` and `run-once` use a live TurnEnvelope | Keep fixtures test-only and re-decide before every host attempt |
| User gate | Concrete projected action is shown; host work stops | Routed before host invocation | Preserve exact projected action and no-spend behavior |
| Todo continuation | Selected todo, claim, continuation, and successor policy survive turns | Todo identity is preserved through plan, host request, writeback, and receipt | Add broader scheduled and interactive continuation qualification |
| Tool capability | Observed capabilities are passed to quota routing | `--available-capability` feeds the live decision | Add host-specific discovery helpers without creating user gates |
| Workspace isolation | Agent obeys workspace guard and repository policy | Caller supplies an explicit project; repository worktree policy remains external | Integrate a first-class workspace guard before write-capable hosts |
| Bounded execution | Heartbeat prompt asks for one validated segment | Built-in and generic hosts require typed results and explicit timeout | Qualify longer repository turns and interactive interruption |
| Validation and writeback | Validate, refresh, then spend one slot | Independent command validation gates durable writeback and one spend | Keep validators task-specific and outside the host |
| Scheduler/backoff | App RRULE is applied and acknowledged without spend | Final live scheduler check is part of the Turn receipt | External recurring hosts must apply required host actions without overlap |
| Repair/replan | Typed control state can preserve, repair, or replace the current route | Host and validation failures route to typed repair/replan; two stalls require a todo or vision delta | Expand real-host negative-path qualification |
| Privacy | Raw host material stays outside LoopX state | Existing boundaries are strong | Preserve current boundary and add a typed result channel |

This matrix is an implementation checklist, not evidence that the capabilities
already match.

## Product Shape

The shipped experimental command group is:

```bash
loopx turn plan \
  --goal-id <goal-id> \
  --agent-id <agent-id> \
  --host codex-cli \
  --execution-mode isolated-headless

loopx turn run-once \
  --project . \
  --goal-id <goal-id> \
  --agent-id <agent-id> \
  --host codex-cli \
  --execution-mode isolated-headless \
  --validation-command-json '["./verify-postcondition"]' \
  --execute
```

`plan` is read-only. `run-once` composes a live decision, one host attempt,
typed closeout, independent validation, writeback, spend, and scheduler final
check. Use `--host generic-cli --host-adapter-request-schema loopx_turn_host_request_v1`
and `--host-adapter-command-json ...` for a compatible Trae or
another CLI after its wrapper implements the typed stdin/stdout contract in the
host-neutral protocol.

The built-in Codex result schema accepts `validated_completion` for a fully
delivered selected Todo. After independent validation, the adapter invokes the
existing Todo lifecycle, including its controller-declared validator, before
accountable refresh and one quota debit. It retains the original launch lease
generation in the Turn journal for completion and cached-result recovery;
lease loss or a newer same-Agent generation cannot authorize the old result.
`validated_progress` remains partial progress and never silently completes work.
Goal acceptance and terminal closeout remain separate owner checks.

内置 Codex 的结果格式支持 `validated_completion`，用于已满足全部要求的已选 Todo。
独立验证通过后，adapter 先调用现有 Todo 生命周期及 controller 声明的完成验证器，
再写回可核算进展并扣额一次。Turn journal 保留启动时的原租约代次，完成和缓存结果
恢复都使用该代次；租约丢失或同 Agent 的新代次不能授权旧结果。`validated_progress`
仍表示部分进展，不会被静默升级为完成；Goal 验收和终态关闭仍由各自 owner 检查。

That wrapper is deliberately thin: it translates one Turn request into one
bounded Agent CLI invocation, preserves only an opaque local resume handle, and
returns one typed candidate result. LoopX remains responsible for the control
decision, todo continuation, independent validation, durable writeback, quota,
and scheduler state.

Codex CLI policy supports two explicit modes:

- `isolated-headless` is the currently supported experimental worker and
  benchmark route. It uses an isolated workspace and never claims to preserve
  a visible TUI.
- `interactive-visible` remains the intended user-visible route. It is not a
  supported `run-once` mode until attach, idle, interruption, and takeover proof
  are integrated.

The driver must never switch from `interactive-visible` to
`isolated-headless` as a fallback. This preserves the existing `/goal`
visible-first promise while allowing controlled non-interactive dogfood to test
the host-neutral mechanism.

## Run-once Algorithm

```text
1. Resolve project, goal, registered agent, execution mode, and Codex capability.
2. Run live quota should-run --turn-envelope with observed capabilities.
3. Route user notification, quiet wait, repair, or delivery exactly as decided.
4. Claim/preserve the selected todo and satisfy workspace guard.
5. Start or resume one Codex turn with the thin task body and TurnEnvelope.
6. Require a typed result; validate the material artifact or state change.
7. Update/complete the todo or write a repair/replan delta; refresh state.
8. Spend once only for validated delivery; apply and ack scheduler state.
```

The host adapter may use existing session proof, runtime idle, timeout, and
command-prefix helpers. It should not make callers assemble the old probe chain
manually.

## Typed Repair And Replan

LoopX Turn uses typed repair when the current todo is still correct but the
host, workspace, capability, validation, or writeback path is recoverable. It
uses typed replan when the route itself is no longer a valid way to close the
goal acceptance gap.

Replan is triggered by any of these conditions:

- the active vision remains open but no runnable todo exists;
- negative evidence invalidates the selected route;
- host capabilities make the todo non-executable and repair would change its
  intent; or
- two eligible turns repeat the same no-progress result.

A replan turn must write a bounded todo delta or vision replan trigger. If it
cannot produce a material delta, it returns a concrete blocker instead of
polling indefinitely.

## Experimental Stages

1. **Contract - complete**: the host-neutral lifecycle, typed result, and
   independent validator gate are shipped.
2. **Shadow - complete for the current matrix**: state fixtures preserve action
   signatures and typed routes without host execution.
3. **One turn - complete for isolated Codex CLI**: a real resumed host session
   returned a typed result, passed independent tests, wrote state, spent once,
   and completed the scheduler final check.
4. **Scheduled continuation - partial**: resume/new-session eligibility and
   timeout recovery are proven; a generic non-overlapping recurring host loop
   and `interactive-visible` mode remain open.
5. **Benchmark dogfood - active**: compare the driver with Codex App and the
   canonical countable `/goal` baseline under matched source, budget,
   concurrency, no-feedback, no-sync, no-upload, and no-submit boundaries.
6. **Promotion review - pending**: decide whether to keep the adapter
   experimental, retire older probes, or promote another CLI host.

Benchmark dogfood records compact parity, trajectory, and closeout evidence. It
must not commit raw task text, raw trajectories, verifier output, credentials,
or local artifact paths.

## Rollback And Non-goals

The adapter must be disableable without changing LoopX goal state, normal CLI
commands, or Codex App heartbeat operation. Old probe commands may remain as
diagnostics until the consolidated driver covers their durable boundaries; they
must not be the default product narrative.

This route does not:

- replace Codex App before measured parity evidence exists;
- make Codex CLI session data authoritative;
- silently answer user gates or handle credentials;
- launch benchmark jobs, upload artifacts, or submit leaderboard results; or
- treat process exit zero, generated prose, or a session resume as validated
  task progress.
