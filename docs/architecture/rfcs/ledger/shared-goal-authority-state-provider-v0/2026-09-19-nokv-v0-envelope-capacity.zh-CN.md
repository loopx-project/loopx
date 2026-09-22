# NoKV v0 单信封容量实测

对共享 Goal authority 模型是非规范性的：不改任何代码，也不改任何 profile。它记录当前
NoKV 候选布局在 fail-closed 之前能装下多少，让 §7.2 的有界 head 讨论和
[#4727](https://github.com/loopx-project/loopx/issues/4727) 的有界布局设计建立在实测而非
估算之上。

- **测的是什么。** `NoKVAuthorityStore` 为每个 goal 保存一个 JSON 信封，每次提交都把整段
  保留 journal 重新发布；信封上限 16 MiB（`DEFAULT_MAX_ENVELOPE_BYTES`），超过后
  `commitAuthority` 在任何 CAS 之前返回 `failed/authority_envelope_too_large`。一个仓库外的
  探针通过真实 store 逐笔提交（`commitAuthority`，每 50 笔采样一次首末操作的 `readReceipt`
  与 `loadAuthority`），直到收到该拒绝；一次走内存传输，一次走真实 JSON-lines helper 对单节点
  NoKV 0.11.1 owner。每笔事务带固定投影加约 0.6 KiB 的事件与收据。投影尺寸取生产规模
  history fixture 的自然大小（21,858 字节）和一个 64 KiB 的合成投影。测量基线为 LoopX
  `da6d79778`（2026-09-19，incarnation 围栏合入之前；信封布局在 `main` 上未变）。
- **信封在哪里停。** 内存下，21,858 字节投影在第 739 笔到顶（最终信封 16,758,084 字节，每笔约增
  22.7 KB）；64 KiB 投影在第 252 笔到顶（16,762,707 字节，每笔约 66.7 KB）。live 下 64 KiB 档
  同样停在第 252 笔，16,762,708 字节。所以到顶的是保留的投影而不是收据：每一行已提交记录都留着
  完整投影。
- **折成天数。** 按 §7.2 每天 864 笔的最低连续性负载，21,858 字节档约撑 0.86 天，64 KiB 档约
  0.29 天；按每天 10,000 笔，分别是 1.8 小时和 0.6 小时。
- **延迟随历史增长。** live 下提交延迟从第 2 笔的 341 ms 涨到第 251 笔的 3,460 ms（251 笔的
  p50 1,340 ms、p95 3,139 ms）；首个操作的 `readReceipt` 从 66 ms 到 1,307 ms；`loadAuthority`
  从 57 ms 到 1,334 ms。读是 O(历史)，因为每次读都要解码并重新校验整段 journal。
- **流量。** live 下到顶共写 2,128,646,475 字节、读 4,627,769,098 字节，252 笔耗时 409.6 秒；
  内存下 739 笔累计写 6,208,337,638 字节、读 12,526,179,676 字节。每笔提交读两次信封，每次读附带
  一次 `find_workspaces` 身份校验。
- **约束设计空间的 NoKV 事实。** 用同一 `operation_id`/`artifact_revision_id` 重发同一字节是
  `applied`（幂等回放）；已存在路径上换新 id 的 create-only 发布是 `conflict`；复用 id 换字节或用
  过期 generation 的拒绝，helper 只能报 `ambiguous`，所以发布 id 在首次尝试后即为终态。4、8、
  16、24 MiB 的单对象都能经同一传输发布并读回（24 MiB 需把传输的响应上限从默认 32 MiB 调高），
  因此 16 MiB 是 LoopX 常量，不是 NoKV 的限制。
- **本条目没有确立的事。** 有界布局尚不存在；#4727 是设计，不是交付。测量只覆盖一个 owner、
  一个 workbench、一个 goal；对重启/恢复、可用性、HA、保留策略不作任何断言，也不移动任何资格
  hold。拒绝码仍是 `authority_envelope_too_large`；NoKV 面是否采用 Appendix C 的
  `store_capacity_exhausted` 是记录在 #4727 里的 owner 决定。
