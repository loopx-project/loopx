# 退役实验性 Todo 事件来源

## 交付摘要

- 目标／来源：roadmap R5/S12、TypeScript T4 与 shared-authority D1–D3，
  以及维护者在 #5054 中删除旧 Todo 事件路径的明确方向。
- 缺口：为捕获一个退役来源而继续扩展第二套 Todo 投影和写回；其中仍有独立用途的
  caller 是 supervisor 提案／回执日志。
- 结果：删除 Todo replay、overlay、backfill、completion 与未被产品调用的迁移桥。
  保留 Markdown 兼容及 provider 权威；拒绝非空旧来源，避免无声丢失记录。
- 归属：Todo 来源准入、现有 TS completion／authority owner，以及
  `control_plane/agents` 下的实验 supervisor 日志。
- 验收：各来源别名拒绝且不写数据／不执行验收命令；canonical 读取忽略遗留文件；
  普通 completion／successor 与 status／quota／review-packet 链路正常；
  supervisor 覆盖并发回执、预览、身份冲突和提交结果未知后的重放。

## 兼容与边界

非空 `events.jsonl`、`state_event_log`、`state_events_file` 或 `event_log` 来源
会阻止旧 Todo 读写和 shadow 资格验证。操作者须保留原文件，先用兼容旧版本检查／
导出其中 Todo，再有意移除活动来源位置的绑定或文件。没有自动回放、删除、
Markdown 降级或新增迁移 API。缺失及零字节文件不承载事件 Todo。
以前准备但不受支持的事件 outbox 记录仍会被拒绝，本 PR 不为其提供资格认证。

Supervisor 保持实验性、默认关闭；新 `supervisor_log_event_v0` 只接收本地私有的
提案和回执。旧实验格式须人工归档后启用新日志；未知格式会明确拒绝，不自动改写。
准入与发布共用日志锁；重试比较完整语义身份，允许观察时间变化。
预览不发布或同步日志。持久化 executed 回执阻止第二份 executed 回执，但不解决
“宿主外部动作执行后、回执落盘前崩溃”的区间；外部动作保护继续遵循独立验收。

## 剩余边界

本决策替代旧交付记录中“补事件 writer 绑定／捕获”的计划，不重复计数已交付的
事务捕获，也不机械地从固定 PR 数量中减一。剩余退出条件仍是执行器外部动作保护、
整 Goal 迁移／回退资格、默认启用和可达 Python writer 的有界删除。
D2 后端／容量／持续运行证据与 #4931 单列。PostgreSQL 继续复用原 authority 合同；
本 PR 不添加 provider。
