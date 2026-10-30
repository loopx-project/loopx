# S2/S12 启动诊断：在就绪预算之后取回已发布的诊断码（2026-10-30）

本条目记录对受管 runtime 启动路径的一次修复。它扩展
[2026-10-04 启动诊断检查点](2026-10-04-s2-s12-startup-diagnostic.zh-CN.md)，
并修正一个 runtime 已经算好、却没有送达的诊断。

## 缺口

2026-10-04 那个检查点确立：持有活 mutation lock 的 fixture 必须以精确的
`mutation_lock_timeout` 码失败，并且明确没有给 Windows 资格。Windows lane
在"启动 Effect runtime 并等待其就绪"这类操作上间歇失败，其中一次仍可获取的
run（`1e3401591`）以

```
assert 'runtime_startup_timeout' == 'mutation_lock_timeout'
```

失败。`runtime_startup_timeout` 只在"就绪预算耗尽而子进程仍在运行"时产生，
因此那次运行里子进程没有在 15 秒内退出。同一个 job 的 pytest 步骤用 5 分 31 秒
跑完 439 个用例，含失败的那一段用两分钟，而前一段只用六秒，说明 runner 负载很重。

启动成本本身有充裕余量。分别在 Windows runner 的 `2f544f9692` 与 macOS 的
`d3d9ad209d` 上测量：

| 宿主 | 就绪启动 | live-lock 总计 | 送达的诊断 |
| --- | --- | --- | --- |
| Windows (`nt`) | 0.406 秒 | 5.813 秒 | `mutation_lock_timeout` |
| macOS (`posix`) | 0.170 秒 | 5.140 秒 | `mutation_lock_timeout` |

`STARTUP_READY_TIMEOUT_SECONDS` 为 15.0，TypeScript 的 `MUTATION_LOCK_TIMEOUT_MS`
为 5,000。两个预算都不是约束，这里也不放宽任何一个。

精确码是丢失了，而不是没有产生。TypeScript runtime 在放弃时算出类型化码并把它
发布到 stderr；Python 只在 `process.poll()` 显示子进程已经退出时才读那份信封，
否则就在预算处终止子进程并报告通用的 `runtime_startup_timeout`。信封是在子进程
退出那一刻才发布的，因此子进程仍在运行时根本没有类型化的内容可读：在 1 秒、
3 秒和 5 秒，子进程仍在运行且 stderr 有 0 字节；信封只在子进程退出时才出现，
约 5.8 秒，与退出码 2 同时。

于是宿主丢弃了一个 runtime 已经算好的诊断，改报一个通用超时。而类型化信封的全部
意义正在于这个诊断差异：`loopx/cli_commands/start_goal.py` 为
`runtime_startup_timeout` 准备了专门的修复提示，而 `mutation_lock_timeout`
落到共享兜底提示上，两个码会把调用方引向不同的修复动作。

## 修法

`loopx/control_plane/effect_runtime.py` 现在会给"就绪预算耗尽时仍在运行"的
runtime 一个有界窗口，让它在此期间发布自己的 locator 或类型化诊断。宿主在这个
窗口里读取已发布的信封，并报告它携带的精确码。

窗口由仓库已经拥有的某个预算来界定，而不是引入一个新数字：
`STARTUP_DIAGNOSTIC_GRACE_SECONDS` 就是一个就绪预算，因此一次启动尝试最多等待
两倍 `STARTUP_READY_TIMEOUT_SECONDS`。当窗口关闭时仍没有发布任何内容，runtime
会被终止并报告通用的 `runtime_startup_timeout`，等待因此仍然有界。

在窗口内发布 locator 的 runtime 现在会被接受，而不是被终止。修法之前，只是启动
偏慢的 runtime 会在就绪预算处被杀掉；这个结果在种类上未变，只是发生得更晚。

## 两个验收点

这次修法留下两个验收点，本条目两条都满足。

**收尾窗口必须由仓库已经拥有的东西来界定。** 它是的：窗口就是一个就绪预算，
`STARTUP_DIAGNOSTIC_GRACE_SECONDS = STARTUP_READY_TIMEOUT_SECONDS`，因此代码里
没有引入新预算，每次尝试的总等待至多是仓库已经定义的就绪预算的两倍。

**用户可见的指引会随诊断码一起改变。** 收到精确 `mutation_lock_timeout` 的调用方，
现在会被告知修复或重装 LoopX，而此前被告知"启动超时"。这个差异是取回精确码的
预期后果，应当被这样理解，而不是当作回归。

## 本条目没有确立什么

这里不提出也不认证任何预算变更：实测表明两个预算都足够，两者都未放宽。这里也
不做属主存活性变更：这条路径上的锁由一个存活属主持有，属主存活判定对它行为正确。

这次修法是诊断信息上的改进，不是正确性修复。在 live-lock 情形下，runtime 仍然
无法发布其 locator，调用方仍然启动失败；改变的是调用方现在被告知了原因。本条目
不构成 Windows CI 通过的资格，间歇失败集合被原样保留，没有被重新归类。

启动失败现在最多阻塞调用方两个就绪预算，而此前是一个。
`effect_runtime_request` 对可重试的调用会在启动失败后重试一次，因此一个正在启动
失败的调用方最多可能等待两次尝试。这里的重试策略没有改变。

## 证据

这次修法由一个测试固定下来：它不需要 Windows runner 就能复现已观察到的失败。把
就绪预算调短并让当前进程持有一个活的 mutation lock，修法之前的宿主会报告通用
超时，其断言正是 Windows lane 产生过的那一条：

```
assert 'runtime_startup_timeout' == 'mutation_lock_timeout'
```

修法之后，同一个 fixture 送达精确的 `mutation_lock_timeout`、已发布的信封，以及
被保留的活锁。第二个测试固定住边界：什么都不发布的 runtime 会被终止，并且在正好
一个就绪预算加一个收尾窗口之后仍然报告 `runtime_startup_timeout`。

## 验证

```
uv run --extra test python -m pytest \
  tests/control_plane/test_effect_runtime_integration.py -q                    102 passed, 1 skipped
uv run --extra test python -m pytest \
  tests/control_plane/test_effect_runtime_host_permission.py \
  tests/control_plane/test_effect_runtime_node_probe.py \
  tests/control_plane/test_start_goal_compact_projection.py \
  tests/test_chat_server_cors.py -q                                            201 passed
python examples/docs-governance-smoke.py                                        docs-governance-smoke ok
python scripts/generate_rfc_status_index.py --check                             clean
scripts/check_rfc_bilingual.py --base origin/main --head HEAD                   passed
uv run --extra test loopx canary premerge --from-git-diff --git-diff-base origin/main
```

促成这次修法的测量可以被重新推导，而不必只能被采信：产生那两个数字的提交在 fork 的
`codex/measurement-archive` 分支上仍然可达，它用一个刻意抛出的 `AssertionError`
携带测量值，因为 Windows lane 跑 pytest 时没有 `-s`，否则输出会被吞掉。
