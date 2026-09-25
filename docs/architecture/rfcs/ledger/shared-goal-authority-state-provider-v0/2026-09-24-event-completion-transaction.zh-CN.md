# 事件源完成事务：先建立完整提交，再接入捕获

> Superseded by [Todo event retirement](2026-09-25-todo-event-retirement.md): this historical implementation is removed in #5054.

基线：`90f21a5299188d54f984a5313e774c9ac48d6595`。对应总路线 R5/G2、
shared-authority L2/L7 与 TS T1/T2。本批修复已有事件写入者的正确性，
不授予该写入者 shadow capture 资格。

## 核对已经交付的工作

绑定事务的 Markdown/lease shadow outbox 早已存在（#3870）。#4967 已把完整
来源组装移到 TS，#4968 已把 prepared-entry 的来源判定与交付移到 TS，必须复用。
笼统地说“事件源捕获还没做”不准确：剩下的是把事件日志写入者的实际提交绑定到
既有捕获生命周期，再验证混合写入者和整 Goal 恢复。来源组装不应重复列成待做 PR。
Bootstrap、capture 和 delivery 中的 `event_log_writer_not_bound` 阻塞保持。

## 行为与所有权

之前，事件源 Todo 完成会先写入后继 add/claim 事件，再编码父任务完成事件。
后续出错会留下可运行后继，却没有完成父任务。原来的上下文检查也没有在追加锁内
比较真实事件日志。这两个反例均能通过公开完成函数在基线复现。

- 事件适配器现在只编码既有 TS 后继提案，把后继和完成事件作为一个完整批次提交。
  删除 Python 后继 helper 中重复的归一化、默认值和所有权决定。
- `goals/state_event_append.ts` 负责整批身份冲突、回放、序号分配和来源校验准入。
  Python 持有既有 sibling lock，传输紧凑身份/hash 事实，保留旧格式 codec 和 IO。
  每批一次规划 RPC，不逐条历史事件调用。
- 精确的 `list`/`tuple` 批次先完整验证，再原子替换并 fsync 文件和目录。读者看到
  全部旧字节或全部新字节；已有字节与事件 schema 不变。惰性迭代器和子类保留逐条
  可见与可重入合同；请求原子且绑定来源的批次前，调用者须先物化集合。
- 来源变化返回既有完成校验失败，不留下部分后继。发布确认丢失时报告结果不确定；
  应回读原 Todo 再重试完成。终态重放重新确认日志持久性，不生成更多后继。

这是有意改变 eager batch 的失败/可见性语义。日志在逻辑上只追加，但物理 inode
会替换，读者需重新打开；长期持有文件描述符不构成实时尾读合同。不修改事件 schema
版本、capture gate、迁移权限或 provider 默认值。没有前端设置变化：入口仍是既有
CLI/API 和完成结果，修复的是失败原子性与重放。

## 证据与代价

公开入口反例在基线失败、在本批通过。覆盖批末重复/非法事件、来源变化、多个进程
整批竞争、替换前失败、替换后 fsync 失败、精确重试、历史 CRLF/无末尾换行保留、
两种角色的后继及 dry-run。保留事件独有来源捕获阻塞与非 Todo 的 supervisor/read
消费验证。另将来源投影返回值绑定到经校验的局部变量，修复已有 mypy 失败，运行时
准入语义不变。

只读源副本演练读取 6,111,476 字节 Markdown、回填 874 条事件。在临时 registry
中通过真实 CLI 完成合成 Todo，追加三个事件，耗时 1,531 ms；重试字节不变，真实
来源 digest 回读一致。使用真实记录多样性/规模，不激活真实 Goal 配置、不执行或
晋升真实 Goal，也不宣称完整 archive capture。可通过
`examples/control_plane/event-completion-rehearsal.py` 复现。

同一个 707,414 字节隔离日志，七次预热后三事件批次，中位耗时基线 11.33 ms、
本批 28.94 ms。这是准入与崩溃持久性的成本，不是性能提升。既有整日志读取仍在，
原子发布还增加整文件复制。该旧适配器不是未来高吞吐 provider，应在迁移后随最后
调用者退役。不提高 RPC 预算，本批不修改 File/SQLite/PostgreSQL store。

## 到本地默认切换的剩余交付

条件估算仍为 **5–8 个完整交付 PR**，取决于集成发现和已有开放前置 PR。
本次事务修复属于真实写入者/捕获收口的前置项，不能据此机械减去一个完整包。
旧的 7–9 批估算对应更早的检查点。

| 交付包 | PR 数 | 具体出口 |
| --- | --- | --- |
| 公开 caller 与执行边界 | 1–2 | 核对 CLI/Turn/Chat 真实写入者及外部效果消费者，收口当前证明/fence 缺口，删除被替换的 Python 规则；复用进行中的 leased handoff/selection/receipt 工作。 |
| 消费与显示闭合（D1） | 1 | 集成已合入的 #4961 投影恢复、#4964 完整来源摘要、#4922 快照分页，沿真实客户端证明缺失/过期显示恢复，不重写这些 owner。 |
| 选定 SQLite profile（D2） | 1–2 | 继续 #4224、协调 #4931，完成容量、崩溃/恢复/升级、lag、支持的 runtime/OS 与适用的真实持续运行验证；测试次数不等于持续时间。 |
| 捕获连续性与整 Goal 演练（L7/L8、D3） | 1–2 | 将实际事件事务绑定 prepared/committed outbox 身份；在 File/SQLite 验证混合写入者、中断、排空、旧写入者 fencing、canonical 回读和受 fencing 保护的回滚。通过前保持 unbound 阻塞。 |
| 默认选择与有限退役（L9/T4） | 1 | 新 Goal 创建/设置/安装采用已资格化 profile；迁移获准旧 cohort，最后调用者与兼容窗口关闭后删除旧业务 writer。Markdown import/export/rendering 不属于过时业务 writer。 |

File 保持显式参考 profile，SQLite 是长期本地候选。PostgreSQL 已有 provider 和
受作用域约束的 service factory；真实服务认证、部署、恢复/故障切换与容量资格是
独立中期工作，不应让本地默认切换等它完成，本批也不授予这些资格。
