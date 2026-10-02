# 本地性能诊断

[English](README.md)

这是按需调用的能力：为明确归属的慢操作生成采集命令，并读取采集到的栈。
TypeScript 负责工具配方和结果解释，Python 只负责 CLI 与本地文件传输。
Host 负责在已有授权内执行；LoopX 不自动安装工具、附加生产进程、提权、上传、
修改 Goal，也不据此放行性能预算。没有后台采集或新增回执门槛。

没有适合所有问题的最强工具。Python 的启动、阻塞和墙钟时间先用 Pyinstrument；
Linux 的线程或 native 栈用 py-spy；分配和内存峰值用 Memray；JS/TS 用 Node 内置
CPU/heap profiler。要区分 Python/native/system 和复制成本，可用 Scalene 复核；
磁盘、内核或 off-CPU 等待用 perf/BCC、samply 或平台 Instruments；Go 用 pprof，
JVM 用 async-profiler。[英文工具表](README.md#choose-the-observation)给出官方来源与盲区。
不要只 profile launcher：实际 Node worker、Python 子进程可能是另一个成本来源。

## 使用

采集目录应是新的、被 Git 忽略的本地目录。先确认目标归属、源码和运行时版本、
数据量、并发、冷暖条件及工具可用性。写命令用隔离 fixture，保持所选解释器；
可在隔离环境安装工具，不静默替换生产依赖。栈中可能包含路径和参数，原始产物
属于本地私有证据，不可直接用于公开 PR 或遥测。

```bash
mkdir -p .local/diagnosis/run-1
printf '%s\n' '["python", "-m", "loopx.cli", "--format", "json", "version"]' > .local/diagnosis/command.json
loopx performance-diagnosis plan --tool pyinstrument \
  --command-json .local/diagnosis/command.json \
  --output-directory .local/diagnosis/run-1 --format json
```

Host 按数组执行返回的 `profile_argv`，不进行 shell 插值。`baseline_argv` 保留原
命令。计划本身不会执行或证明工具就绪：还需核对退出状态、实际产物和结果回读。
py-spy 的进度信息会和目标 stdout 混合；需要结构化输出时，由 Host 单独保存目标
输出和退出状态，不把 profiler 成功当成目标成功。

```bash
loopx performance-diagnosis inspect \
  --profile-json .local/diagnosis/run-1/profile.speedscope.json --top 15 --format json
```

现有工具配方是 `pyinstrument`、`py-spy`、`memray`、`node-cpu`、`node-heap`。
Node 用准确可执行文件和目标脚本生成计划，CPU 产物是 `profile.cpuprofile`；
Memray/V8 heap 用工具自己的 allocation reporter。其余工具是后续诊断建议，
不声称已提供或验证对应 adapter。

读取器支持 Speedscope sampled/evented 与 V8 CPU JSON，拒绝空或损坏的记录，
统一时间单位，分别返回 self/inclusive 热点。线程和 profile 保持独立，递归
不会让同一个栈样本重复计时。不要把 inclusive 行相加，也不要把线程权重当作
进程执行时长。
输入限 16 MiB，超出时缩短采集；重复展示的 profile 名称与热点标签共用 1 Mi 字符
上限，超出时明确拒绝，不静默截断。较大的录制复用 Effect 的私有本地快照传输，
不会提高普通命令的 2 MiB 传输上限，也不会把原始 profile 返回到展示结果。

## 验证结论

先取得未插桩的原命令基线，再采集实际成本所属进程。明确未覆盖的线程、子进程、
native 和内核等待。栈只提供假设；用受控干预区分调用方、共享语义/传输和 provider
成本，再复跑原来的未插桩负载与语义回读。单次观测不是 p95，profile 时间不是
准入证据；通过、失败、未覆盖须分别保留。沿用现有
[优化证据规则](../../../docs/development/testing-and-quality.md#roadmap-aligned-optimization)。

能力归属为 `performance-diagnosis`，内置 provider 为 `loopx-core`，复用 Effect、
catalog 与 managed skill，不新增执行引擎。现有 `reliability-diagnostics` 的被动
observer 合同不能表达命令，`external-evidence-research` 的归属是外部研究源，
都不能替代本地性能诊断合同。工具是可选依赖，计划不把它们视为已就绪 extension。

停止调用即可关闭，没有后台 hook。可卸载隔离环境中的工具并按本地证据保留规则
清理产物。能力不授予生产附加、提权、Goal 修改、上传或合并权限。

## 前端查看采样

进入 **设置 → 能力中心 → 此设备默认 → 性能诊断**，选择本地 Speedscope 或
V8 CPU JSON（最多 16 MiB）。实际打包页面通过可取消的 Worker 复用 CLI 的同一套
TypeScript 解析规则，分别展示各线程 / profile，可按自身或含子调用时间排序。
无效采样会清除前一份结果，并显示错误。

文件和结果只留在浏览器内存，不上传、不保存配置、不附加进程、不自动采集。
“清除 / 取消”会丢弃结果并终止 Worker；离开设置也会终止解析。采样仍由 Host
对明确有权限的目标执行；前端入口不会增加进程访问权限。

Speedscope 中未知的源码位置（包括 Pyinstrument 的 `null` 文件名/行号）保持未知，不影响采样权重。V8 的同叶节点采样先聚合，再展开调用链，原始采样数仍保留。整个请求共享解析工作量上限，覆盖采样、事件、调用栈遍历及跨 profile 的共享帧展开；超限会明确建议缩短采样、减少 profile 或降低栈深，不返回部分成功，也不提高运行时超时。
