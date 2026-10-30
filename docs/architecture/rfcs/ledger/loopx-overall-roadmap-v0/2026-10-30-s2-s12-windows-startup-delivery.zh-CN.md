# S2/S12 启动诊断：Windows 送达窗口（2026-10-30）

在 `main` 的 `d3d9ad209d` 上测量。本条目记录
[2026-10-04 启动诊断检查点](2026-10-04-s2-s12-startup-diagnostic.zh-CN.md)
在 Windows 上没有确立的部分，并纠正由此产生的一个关于启动预算的错误结论。
本条目不改变任何运行时行为。

## 旧检查点确立了什么，未确立什么

那个检查点记录：当受管 Effect runtime 无法发布其启动 locator 时，故障通过类型化启动
信封传递并复用共享的文件系统/锁码；并记录一个持有活 mutation lock 的真实 fixture
"恰好失败一次并给出精确的码"。它的证据来自真实 fixture；它没有给 Windows 资格，
并且明确没有声称 Windows CI 通过。

## 实测

Windows lane 在"启动 Effect runtime 并等待其就绪"这类操作上间歇失败。每次 run 的失败
集合都不同：`test_effect_runtime_integration` 的 `live_lock` 用例、
`test_shadow_observation_retirement`、`test_chat_manager_inspection`。

在六次 job 日志仍可获取的 Windows lane run 中，失败文件集合分别是：只有
`test_self_update_runtime_activation`（三次）；该文件加上
`test_shadow_observation_retirement`（一次）；该两者再加上
`test_effect_runtime_integration`（一次）；只有 `test_chat_manager_inspection`
（一次）。

`live_lock` 用例在 Windows 的 `1e3401591` 上以
`assert 'runtime_startup_timeout' == 'mutation_lock_timeout'` 失败过，而在本条目
自身测量的后来几次 Windows run 中通过。它没有平台 skip，因此确实会在 Windows lane
上运行。

启动成本分别在 Windows runner 的 `2f544f9692` 与 macOS 的 `d3d9ad209d` 上测量：

| 宿主 | 就绪启动 | live-lock 总计 | 送达的诊断 |
| --- | --- | --- | --- |
| Windows (`nt`) | 0.406 秒 | 5.813 秒 | `mutation_lock_timeout` |
| macOS (`posix`) | 0.170 秒 | 5.140 秒 | `mutation_lock_timeout` |

两个 revision 的 `effect_runtime.py` 有差异，但只在运行时源码元数据读取、
`_control_plane_root` 和 `_request_with_info`。启动路径本身——`_start_runtime`、
`STARTUP_READY_TIMEOUT_SECONDS`、`_read_startup_stderr` 与 `process.poll()` 检查——
在两者之间未变，因此两个宿主测的是同一份启动代码。

live-lock 总计等于就绪启动加上 TypeScript 的 mutation lock 等待。两个预算都有充裕余量：
`STARTUP_READY_TIMEOUT_SECONDS` 为 15.0，`MUTATION_LOCK_TIMEOUT_MS` 为 5,000。

**本条目纠正一个此前的结论。** "Windows 上 node 冷启动加上 5 秒锁等待会超过 15 秒就绪
预算"这一假设是错的。冷启动为 0.406 秒，且同一次运行中 live-lock 用例返回了精确的
`mutation_lock_timeout`。两个预算都不应为了解释这个失败而放宽。

## 精确诊断在哪里丢失

TypeScript 算出了类型化码并发布它：`effect_runtime_server.ts` 的 `failStartup` 用
`writeSync` 把启动信封写到 stderr，然后调用 `process.exit`。Python 只在就绪循环
*内部*的轮询显示子进程已经退出时才读那份信封（`effect_runtime.py`）；循环结束后它
还会再轮询一次，但那次轮询只用来决定是否终止子进程，随后就报告通用的
`runtime_startup_timeout`，不会再去读捕获文件。

因此只要宿主没有在就绪循环里观察到子进程的退出，精确码已经产生却没有送达。

这条路径上的锁**不是**陈旧锁。fixture 把当前进程自己的 PID 写成锁属主，因此属主是
活着的，`reclaimStaleMutationLock` 正确地拒绝回收它；等满 5 秒 mutation lock 预算
是设计行为，在所有平台上都一样。`effect_runtime_io.ts` 的属主存活判定只记录 PID，
因此被复用的 PID 或 `EPERM` 的探测结果也会被读成存活，但 fixture 并未触及这一点，
本条目也不把它当作此处的原因。

实测的 live-lock 总计在 Windows 上是 5.813 秒、macOS 上是 5.140 秒，而就绪预算是
15 秒，两个宿主都送达了精确码。因此在实测的那几次运行里，预算不是约束。

`1e3401591` 上那次间歇的 Windows 失败是另一种情形。它的 job 日志仍可获取，显示
`assert 'runtime_startup_timeout' == 'mutation_lock_timeout'`。这个码在宿主没有于就绪
循环里观察到子进程退出时产生，而那次历史运行无法区分两种情形：预算耗尽时子进程
尚未发布，或者子进程其实已经发布并退出、而宿主在离开循环前没有观察到那次退出。
同一个 job 的 pytest 步骤用 5 分 31 秒跑完 439 个用例，含失败的那一段用两分钟，
而前一段只用六秒，说明 runner 负载很重。这种负载是"发布迟到"的一个合理成因，但不是
被证明的根因，本条目不确定那次究竟属于哪一种。

在预算耗尽的那一刻补读捕获文件，只能在第二种情形里拿回信封。跟踪一个子进程直接给出
了时序：在 1 秒、3 秒和 5 秒，子进程仍在运行且 stderr 有 0 字节；信封只在子进程退出
时才出现——约 5.8 秒，与退出码 2 同时。TypeScript 侧是在 mutation lock 预算耗尽之后
才走到 `failStartup`，因此还没退出的子进程没有发布任何东西；而已经退出的子进程会把
信封留在捕获文件里，基线却不去读它。

但先让子进程结束、或在子进程退出后再读一次捕获文件，就能拿回。改为等待退出而不是
在预算处终止，在每个实测情形里都拿到了精确码，而且这段额外等待是有上界而非无界的：
无论就绪预算是 0.5 秒、1 秒还是 2 秒，子进程都在 5.13 到 5.17 秒退出，因为它等的是
TypeScript 的 mutation lock 预算。因此修法是：在就绪预算之后给子进程一个有界的收尾
窗口，读到信封就用它，只有子进程在这个窗口内仍未结束时才报告通用超时。这样的修法
同时覆盖上面两种情形，因此它的收益不依赖于判定那次记录在案的失败究竟属于哪一种。
这个修法不在本条目里实施。

## 本条目没有确立什么

本条目不提出也不认证任何预算变更——实测表明两个预算都足够。这里也不提出属主存活性
变更：这条路径上的锁由一个存活属主持有，属主存活判定对它行为正确，而改变属主身份的
记录方式是一次带自有验收的运行时变更，本次测量并不足以支撑它。

间歇性 Windows 失败现在归因于：宿主没有在就绪循环里观察到子进程的退出；历史记录
无法区分"子进程尚未发布"与"子进程已经发布并退出但没被观察到"，繁忙的 runner 只是
一个合理成因而不是被证明的根因。本条目指出的修法是那个预算之后的有界收尾窗口，
它同时覆盖这两种情形，因此不依赖于判定那次究竟属于哪一种。这个修法本身不在此实施：给子进程一个收尾
窗口会改变一次失败启动阻塞调用方多久，这需要它自己的验收，以及能容忍更长等待的调用方。

由此产生两个验收点。其一，调用方等待的时间变长了，因此收尾窗口必须由仓库已经拥有的
某个量来界定，而不是引入一个新常量。其二，用户可见的指引会随诊断码一起改变：
`loopx/cli_commands/start_goal.py` 为 `runtime_startup_timeout` 准备了专门的修复提示，
而 `mutation_lock_timeout` 落到共享兜底提示上；因此收到精确码的调用方，之前被告知
"启动超时"，之后会被告知"修复或重装 LoopX"。这个差异是取回精确码的预期后果，
应当被这样理解，而不是当作回归。

送达的类型化码是诊断信息上的改进，不是正确性修复：runtime 仍然无法发布其 locator，
调用方仍然启动失败。本测量不构成 Windows CI 通过的资格，间歇失败集合被原样保留，
没有被重新归类。

测量本身是一次性诊断，读到数字后已移除；它不是留在本仓库里的资格产物。产生这两个
数字的提交在 fork 的 `codex/measurement-archive` 分支上仍然可达，因此这两个数字可以被
重新推导，而不必只能被采信：它用一个刻意抛出的 `AssertionError` 携带测量值，因为
Windows lane 跑 pytest 时没有 `-s`，否则输出会被吞掉。
