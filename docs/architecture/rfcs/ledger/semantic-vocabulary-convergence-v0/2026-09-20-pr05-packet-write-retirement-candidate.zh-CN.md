# PR-05：protocol action packet 写入退休

> PR-05 历史检查点：下文描述当时的 writer 退休及其兼容 reader。
> #7161 随后取消 reader 兼容并删除当时的验证脚本；当前行为以所链接的
> 协议决定为准。原有历史证据和记录保持，不把旧测试结果改写为当前资格。

对应 [#4794](https://github.com/loopx-project/loopx/pull/4794)、
[#4447](https://github.com/loopx-project/loopx/issues/4447)。
[协议决定](../../../../reference/protocols/protocol-action-packet-decision-v0.md)
拥有迁移契约。首次包含 #4794 的正式发行版切换新写入，源码版本按相应代码行为
运行；已经发布的 v1.1.0 产物不变。历史 v0 格式在 v0 reader 生命周期内继续支持。
协议明确 bundled 消费者与 v1.1.0 回退范围；正常维护者评审/发布控制仍保留，本条
不关闭 tracker。

- **交付变化。** 删除四个 quota 模块内六个现行写入点，以及失去调用者的 packet
  builder/import。普通、暂停、required-read、capability-intent、host-recovery
  路径使用现有类型化契约。默认完整 decision 也不再输出旧字段，不只是 compact
  view 省略。未新增 flag、schema 词表或决策 owner。
- **保留责任。** Python Markdown 和 TS Effect/Envelope 的历史 reader、
  `protocol_action_packet_fields` 有序语义投影、summary 重建、opaque fallback、
  residue 和签名拒绝仍保留，不重写旧记录。实测 Python 字段迁移面由 5 降为 1，
  TypeScript 仍为 2；同 diff 锚点保留这些兼容 reader，不虚报字段全仓消失。
- **行为证据。** 八组完整新旧 quota payload 只去除 packet 后相等；规范签名文档
  只去除 capsule 中对应见证后相等，摘要值可能因此变化。真实 CLI/重入/Envelope/
  live 测试 149 项通过。更名后的 `quota-without-legacy-packet-smoke.py` 会拒绝旧
  默认输出；原 decision-note 文案 smoke 退役，行为由实际运行和兼容回归保护，
  文档结构继续由 docs governance 校验。
- **版本化读回。** 实际 v1.1.0 源码 `607c11d75` 读取八组新输出和八组带 packet 的
  基线样本，签名含义与 host admission 均通过。安装后的候选 wheel 验证普通/暂停
  输出、打包 TS/JSON 资源、真实 bridge 与 host admission。这是有界合成证据，
  不代表完整历史存档或全部 host 资格已验证。
- **持久历史证据。** 实际 v1.1.0 源码生成四组固定 decision/Envelope，保留原始
  哈希。当前 reader 验证原签名，24 个篡改变体被拒绝。
  `scripts/verify_protocol_packet_migration.py` 要求准确且未修改的发行源码，复核
  八组新输出与四组历史对象。真实 CLI 重放及 required-read/capability-intent
  组合测试验证身份、命令和读取顺序在缺少旧字段时保持正确。
- **有界完成。** 默认 writer 已全部停止写入。协议已给出具体消费者/格式范围和
  回滚步骤，不承诺私有客户端或完整历史存档兼容。依赖旧字段的外部客户端需先迁移
  或保留 v1.1.0。未来移除 v0 reader 必须另行做破坏性迁移。相邻 quota PR 在 rebase
  时须保留这些不变量；其未合并改动不被悄悄计入本 PR 的 main 基线。
