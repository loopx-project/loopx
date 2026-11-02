# 进度评审 receipt 资格判定（2026-11-02）

## 所有者边界

本检查点将完整的已加载 receipt 资格判定，从
`capabilities/progress_review/context.py` 迁至
`control_plane/work_items/progress_review_evidence.ts`，通过一次
`progress_review.context.qualify` 请求暴露。现有内置 progress-review capability
保留 provider 边界；不新增 capability 或 provider。
TS 所有者在进程内组合 `inspectLocalGoalAcceptance` 和
`progressReviewCriterionBasis`，在批次内缓存每个 Todo 的检查，比较规范绑定，撤回
过期信号，以首个资格判定后的 receipt 选择最新 revision，并应用人工 pin。
Python 加载器的 `progress_review_receipt_order_key` 是唯一排序所有者：稳定的
最新优先输出同时保留并列时首个最大值的行为。TS 保持输入顺序，不再排序。
这些是先后发生的读取，**不是原子快照**。单个 Todo 不可用不会抑制其他 Todo。
精确 Goal 实例的拒绝仍由 acceptance 所有者负责。操作员研究与旧 receipt 的语义不变。

## 成本与保留的 Python

File 和 SQLite 上的插桩测得 context 读取跨运行时次数：

| 场景 | 之前 | 之后 |
| --- | ---: | ---: |
| 关闭 / 无 receipt / receipt 存储不可用 | 0 | 0 |
| 同一 Todo 的重复规范 receipt | 5 | 3 |
| 两个 Todo 的规范 receipt | 6 | 3 |
| criterion 被删除 / 较新 receipt 在 pin 之外 | 3 | 2 |
| 不可用 Todo 与当前 Todo 混合 | 5 | 3 |
| Goal 实例不符 | 2 | 2 |
| 操作员研究与旧 receipt 混合 | 1 | 2 |

通用资格判定边界由 E + T + R 变为 E + 1；E 仍包括 receipt IO 中逐条执行的
证据 scope 解码。关闭和空路径不调用批次。纯人工/旧 receipt 批次及实例被拒绝的
批次不一定减少跨运行时次数。不声称延迟或长程收益。人工/旧 receipt 场景从
1 增至 2，因为证据 scope IO 仍需一次调用，每个非空批次又增加一次资格判定。
没有 `goal_acceptance` 绑定且没有 pin 时，语义上不需要 TS 资格判定调用；本次
报告该浪费，不在 Python 中复制 TS 规则来绕过调用。产品代码物理行数
为 +111/−53，净增 58 行。Python context adapter 增加 32 行传输/codec 组合，
删除 50 行；handler 注册增加一行 bridge。此有界净增长换来一个完整判定所有者，
而不是再添一个叶子 facade。

Python 删除 Todo 缓存、acceptance 检查编排、criterion 比较、stale 改写、最新
revision 选择及 pin 过滤。保留 receipt 文件 IO、持久化格式/信号 codec、policy
解码、读取错误投影和摘要组合，服务现有 status、attention、refresh 与 Chat 调用者。
所选源索引使传输可保留历史数值事实（整数值浮点及大整数），而不重复判定规则。
响应移除未使用的 qualified 数组，并直接计算所选源索引。删除 sequence token
和 BigInt 排序；任意精度整数排序仍由 Python 加载器这一唯一排序所有者负责。acceptance 检查 facade 仍服务 CLI、Chat
和 delegation；criterion-basis 操作仍服务可选 observer 的规范 basis 读取。
二者均不是无调用者的兼容导出。

## 验证与退出条件

修改产品代码前，测试 setup 在合同哈希之前固定合成的相对 acceptance argv。
File 和 SQLite 在未改动产品代码上连续三轮通过 characterization，随后冻结完整的
逐场景 context 和 status 渲染 fixture，共 841 行，不使用输出 normalizer。
覆盖关闭、空集合、重复/多个 Todo、实例错误、criterion 删除、检查/存储不可用、
人工/旧观察与 pin 漂移。独立数值 IO 和运行排序测试保护传输与选择边界。

当 status、attention、refresh-state 和 Chat 不再依赖 Python receipt IO 或组合，
直接消费原生 receipt 读取/摘要所有者时，删除现有 Python context adapter 及此
bridge 操作。在同一次经过验证的调用者切换中删除临时 characterization 与跨运行时
次数 fixture，保留语义反例测试。本 slice 不改变公开用户旅程，也不证明已安装
发行版、provider 性能或长程成果。

## 评审修复验证

模拟加载器现复用真实加载器的排序 helper；两个冻结 JSON fixture 的 SHA-256
保持不变。TS 测试证明即使运行元数据暗示其他顺序，最新值仍遵循输入顺序。
既有 Python 运行排序及 pin characterization 继续通过。

- Ruff：2 个变更 Python 文件通过；pytest：77 通过，0 失败。
- 指定 Node 测试：39 通过，0 失败；control-plane typecheck 通过。
- 语义词汇 smoke 通过（29/29 vocabulary，56/56 owner symbol）。
- CLI base/head：File 与 SQLite 的 JSON、Markdown 共 4 项比较通过。
  Markdown 字节一致；JSON 仅源读取时钟/年龄不同，base 重复读取也会变化。
  progress-review payload 不变。
- `git diff --check` 通过；不保留生成的 `uv.lock`。全部修改保持 unstaged，
  未 commit、push 或更新 PR。

验证使用已核对导入来源的本 checkout `.venv`；`uv run` 在 sandbox 中无法
取得默认 cache lock。
