# Source CLI entrypoint / 源 CLI 入口

`python -m loopx.cli` uses the same `loopx.entrypoint.main` bootstrap as the
console command. Importing `loopx.cli.main` or `build_parser` remains the full
in-process compatibility API. This is a Python host-bootstrap adapter repair,
not a new Python policy owner, command registry, validator or provider.

`python -m loopx.cli` 复用 console command 的 `loopx.entrypoint.main`。以库方式
导入 `loopx.cli.main` 或 `build_parser` 仍保留完整 parser 与进程内兼容调用。
本次只修复 Python 宿主启动适配器，不增加 Python 策略 owner、命令目录、validator
或 provider。

## Owning boundary / 所属边界

- Version and root help avoid full command imports. Selected common commands
  reuse the existing grammar, command registrars and handlers. Rejected selected
  arguments return to the full parser for canonical diagnostics; other commands
  retain the full-parser fallback.
- Receipt-bound scheduler follow-ups reuse the existing TS executable. Manual
  or unbound follow-ups retain their compatibility route. Missing Node on the
  bound route fails closed, not back into a weaker interpreter path.
- Outer-controlled calls stay in the selected dispatcher so its existing native
  controller write guard runs before process replacement or provider effects.
  The guard still owns Goal matching; the bootstrap does not implement another
  permission decision.
- Quota, Todo completion, declaration/source/lease checks, provider commits and
  settlement receipts retain their owners. No assertion, frozen completion
  deadline or one-debit requirement changes.

对应中文：

- Version 与根 help 不加载全部命令。常用命令复用既有 grammar、registrar 和
  handler；selected parser 拒绝的参数交还完整 parser 输出原诊断，其他命令继续
  走完整 parser。
- 已绑定回执的 scheduler follow-up 复用现有 TS executable；手工或无绑定调用
  保留兼容路线。绑定路线缺失 Node 时关闭失败，不退回较弱的解释器路径。
- 外层控制器调用先经过 selected dispatcher 中现有 native controller write
  guard，再决定写入。Goal 匹配仍由 guard 判断，bootstrap 不复制权限规则。
- Quota、Todo 完成、declaration/source/lease fence、provider commit 与结算
  receipt 的 owner 不变。断言、冻结完成期限和一次扣额规则均不修改。

This belongs to S2's single-authority boundary and S10's evidenced recovery-cost
work, not full TS cutover or sustained provider qualification. See the
[TS migration RFC](../architecture/rfcs/typescript-control-plane-migration-v0.md),
[overall roadmap](../architecture/rfcs/loopx-overall-roadmap-v0.md) and
[optimization evidence guide](../development/testing-and-quality.md#roadmap-aligned-optimization).

这属于 S2 单一权威边界和 S10 有证据的恢复成本优化，不代表 TS 全量切换或长程
provider 验收完成。沿用上述 RFC、roadmap 与验收规则，不新建平行规划。

## User-visible compatibility / 用户可见兼容性

Source invocations now also share the existing console help and usage-statistics
policy. The first eligible source command may disclose on stderr; subsequent
commands follow the machine-local choice and existing TS policy. JSON stdout
stays pure; help/version do not start observation. `LOOPX_USAGE_PING=0`,
`DO_NOT_TRACK=1` and the existing disabled setting retain their opt-out behavior.
This intentionally removes entrypoint drift; it is not byte parity with the
old source entry's absence of disclosure.

源调用也使用现有 console help 和 usage-statistics 策略。首条符合条件的源命令
可能在 stderr 展示告知；后续沿用机器设置及既有 TS 策略。JSON stdout 保持纯净，
help/version 不启动观测；上述环境变量及既有 disabled 设置继续关闭观测。
这是有意消除入口漂移，不宣称与旧源入口“不展示告知”的行为逐字等价。

The affected journey is source CLI invocation, including managed/canary callers
that execute that module. Frontend and Lark do not gain a new control, schema or
configuration authority. They consume the same existing command projections and
receipts; this repair does not newly qualify their delivery transports. The
existing usage-settings HTTP interaction verifies the shared machine choice.
No frontend asset or layout changes, so no repackaging is needed for this slice.

受影响路径是源 CLI，以及执行该模块的 managed/canary caller。前端和 Lark 不增加
控件、schema 或配置 owner，继续消费同一既有投影及回执；本次不新验收它们的投递
transport。现有 usage-settings HTTP 交互验证共享机器设置。没有前端资源或布局
改动，因此此切片无需重新打包前端。

## Qualification / 验收

Fresh-interpreter regressions compare source and console entries, preserve
canonical fallback diagnostics, prove unrelated owners remain unloaded, and
exercise outer-controller rejection before any Goal-mutating effect. Real File and SQLite
reads still work with the Markdown display removed, with exact provider-revision
readback and no mutation. Existing completion tests retain actual declaration
binding, validator execution, failure and replay. Existing scheduler tests keep
all callers, storage layouts, cross-agent identity and state-key assertions.
Source and console callers also preserve reads, permit existing and newly
registered independent Goals, reject actor changes as a scope escape, and
resume actual receipt-bound scheduler writes after the controller releases its scope.

新解释器回归对比源入口与 console：保留 fallback 诊断，证明不加载无关 owner，
验证外层控制器拒绝发生在任何 Goal-mutating effect 之前。真实 File/SQLite 读回在移除 Markdown
展示后仍成功，保持精确 provider revision 且不修改权威。既有完成测试保留真实
声明绑定、validator 执行、失败及重放；调度测试不删 caller、存储布局、跨 Agent
身份或 state-key 断言。
源入口与 console 还验证了读取、现有及后注册的独立 Goal 不被误拦、改变 actor
不能逃逸 Goal 范围，以及控制器解除保护后真实的回执绑定 scheduler 写回。

```sh
uv run --extra test python -m pytest \
  tests/control_plane/test_source_cli_entrypoint.py \
  tests/test_cli_entrypoint.py \
  tests/test_usage_ping.py \
  tests/control_plane/test_completion_validation_initial_binding.py \
  tests/control_plane/test_completion_validation_lane_scope.py \
  tests/control_plane/test_scheduler_ack_current_host_binding.py \
  tests/control_plane/test_scheduler_compat_state_key.py \
  tests/control_plane/test_scheduler_host_followup_hint_transport.py \
  tests/test_kunluncode_goal_mode.py \
  tests/control_plane/test_public_boundary_parallel_reads.py -q --durations=10
```

The 2026-09-28 development-host ABBA comparison retained the unchanged 28-case
scheduler matrix on base `f679911568eee2b3c3cfa1a6ade43dcb17cfb06e` and the
pre-rebase source-entry candidate on that base, using the same Python 3.13
interpreter. Subsequent inclusion of the separately merged reader optimization
is not retroactively part of these measurements:

| Arm / 分组 | First wall time / 首轮 | Second wall time / 次轮 |
| --- | --- | --- |
| Base / 主干 | 129.28 s | 123.24 s |
| Candidate / 候选 | 120.63 s | 191.03 s |

All four runs passed the same 28 assertions. The candidate has an adverse slow
sample and does **not** establish a stable whole-batch improvement. The host and
OS/service caches were shared, not isolated cold-machine samples. The original
20-second budget was not met or increased. Independent import-loading and
outer-controller oracles reject both the old eager source entry and deliberately
reintroduced eager loading/unsafe process replacement; they qualify the repaired
boundary, not the batch deadline.

四轮保留同一 28 项断言并全部通过，但候选存在变慢样本，不能宣称整批稳定提速。
宿主及 OS/service cache 共享，不是隔离冷机器测量；原 20 秒预算未达成，也没有
调高。独立加载与权限反例会拒绝旧 eager 入口，以及故意重新引入的 eager loading
或提前 process replacement；它们只证明修复边界，不证明整批期限已通过。

On that active shared host, an eight-arm alternating fresh-process startup
probe (four samples per revision and command, shared OS caches) measured
`--version` medians of 0.759 s → 0.040 s and `quota --help` medians of
0.805 s → 0.072 s. These measure only the tiny/help startup boundary, not a
business operation, cold machine, isolated load or p95 latency.

同一活动共享宿主上的八组交替新进程 probe（每版本每命令四次，共享 OS cache）
测得上述 version/help 中位数。它们仅测量 tiny/help 启动边界，不代表业务操作、
冷机器、隔离负载或 p95 延迟。

Startup savings are not whole-batch qualification. Measure the unchanged real-CLI
matrix with the same interpreter and alternating base/head arms; disclose shared
OS/service caches rather than calling a fresh Python process a cold-machine run.
The original multi-command synchronous-deadline acceptance remains open until
the complete workload passes its frozen budget. Source entry repair neither
approves a validator timeout increase nor makes progress writeback terminal.

启动开销减少不等于整批验收通过。使用同一解释器交替测量未修改的真实 CLI 矩阵，
披露共享 OS/service cache，不把新 Python 进程叫作冷机器测量。只有完整负载通过
冻结预算，才算完成原同步期限验收。本修复不批准扩大 validator timeout，也不把
阶段写回转成 terminal 完成。
