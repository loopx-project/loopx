# S2/S12 启动诊断检查点（2026-10-04）

本条目为 [#5551](https://github.com/loopx-project/loopx/pull/5551) 记录；
[总纲](../../loopx-overall-roadmap-v0.zh-CN.md) 正文只保留指向本条目的一行指针。

managed Effect runtime 无法发布启动 locator 时，失败现在走既有 typed 启动
envelope，并复用共享的文件系统/锁诊断码（`io_is_directory`、
`mutation_lock_timeout`，Windows 占用 locator 目录时为 `io_permission_denied`），
不再只表现为 listen callback 未处理拒绝导致的 `runtime_exited_before_ready`。
该固定消息不包含原始 Node 错误、locator 路径、token 或堆栈。

真实 fixture 分别用目录占用 locator、以及活进程持有 mutation lock 两种故障：
两者都只失败一次并返回准确 code，不修改外来占用与其锁，清理 runtime 自己的
start lock，并在移除注入故障后恢复正常的 ping/shutdown。没有 typed envelope 的
退出仍然报告 `runtime_exited_before_ready`。

本次只关闭已复现的“缺失诊断”缺口：既不为无关的 Linux unexpected-exit CI 失败
归因，也不宣称 Linux/Windows CI 通过，更不代表安装、发布或严格 publication
readiness。面向操作者的边界见
[安装指南](../../../../guides/installing-loopx.md)（中文说明在该文件内）。
