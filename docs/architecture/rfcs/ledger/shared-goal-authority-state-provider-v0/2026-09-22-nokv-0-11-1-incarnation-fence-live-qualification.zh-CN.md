# NoKV 0.11.1 incarnation 围栏在可复现栈上的 live 资格

对共享 Goal authority 模型是非规范性的：不移动任何资格 hold，不改任何 RFC 条款。它记录
[#4774](https://github.com/loopx-project/loopx/pull/4774) 接入的发布围栏在一条命令即可拉起的栈上
的 live 复测，让 RFC 仍列着的那条 hold 的移除有可引用的记录，而不是某位贡献者的 scratch 目录。

- **改动是什么。** #4774（批准的 head `9cffc78be`，2026-09-20 squash 合并为 `be7789fd95`）让每次
  `NoKVAuthorityStore` 发布都携带 `expected_workspace_incarnation_id`，即读取信封时所在的
  incarnation。NoKV 0.11.1 在任何 operation 行、artifact revision 或对象存在之前，把这个围栏与
  `expected_generation` 一起原子校验，并用 typed 的 `WorkspaceIncarnationMismatch` 拒绝过期
  incarnation，helper 将其映射为 `failed/store_identity_mismatch`；helper 钉死 SDK 0.11.1，只接纳
  显式命名围栏参数并导出该 typed 拒绝的 wheel。批准评审保留了 profile hold
  `atomic_workspace_incarnation_publication_fence` 与附录 A 的措辞，理由是 maintainer 没有 live
  owner 可以亲自重跑这条会写入的探针。
- **复测靠什么变得可复现。** NoKV-Lab/NoKV#518 新增 `scripts/workbench/loopx_stage2a_stack.py`：启动一个隔离的
  etcd 成员、一个 digest 固定的 RustFS 容器（无 Docker 时改用 `moto` S3 服务），provision 一个
  owner，创建一个 workbench，并写出 `env:nokv_legacy` 与 `env:nokv_authority` 两个门读取的客户端配置
  与环境文件。本条目所用的栈：NoKV `590d3a4bdc`（即 `v0.11.1` tag；owner 由脚本 `--build` 从该提交
  构建，SHA-256 `1c468a7b…`），已发布的 `nokv==0.11.1` macOS arm64 wheel（SHA-256 `e33f318e…`，
  已对照 release 的 `SHA256SUMS` 校验），etcd 3.7.1，LoopX `main` `4bed6ed3d` 干净树，Node 22.22.3
  （CI 跑 Stage 2C 作业所用的运行时）。测量日期 2026-09-22。
- **正向行。** `s0.nokv_live_matrix` 与 `s2a.nokv_live_qualification` 通过。资格报告含 15 项检查全部
  通过，包括 `stale_incarnation_fence_rejected`（把 generation-1 的信封以另一个 incarnation 为围栏
  原样重发，被 typed 拒绝）和 `stale_incarnation_fence_left_generation_unchanged`（generation 仍为 1，
  workbench 身份不变）；最终 generation 3，SDK `0.11.1`，API `1`。同一栈上完整的 23 行 ladder：22
  pass，`s2b.postgresql_conformance_live` unverified（未配置 PostgreSQL），
  `s2c2.sustained_parity_soak` 按声明 pending；隐私扫描 0 命中。`moto` 备选栈同样以 15 项检查通过 `s2a`。
- **同一栈上的负向配对。** 换成已发布的 `nokv==0.11.0` wheel：`s0` 仍通过（矩阵不钉 SDK），`s2a`
  typed 失败：探针报 `nokv_transport_protocol_failed`，因为 helper 在 admission 阶段、构造任何 client
  之前就拒绝了该 wheel；没有任何发布抵达 owner。
- **一个只在 macOS 出现的 ladder 条件，因为它耗掉了一轮运行所以记下。** 在 macOS 默认的 `TMPDIR`
  （`/var/folders/…` 软链接）下，全部 11 行 `s2c2` 以 `shadow_management_state_invalid` 失败：TypeScript
  侧对 runtime root 的真实路径（`realpathSync`）做摘要，Python 侧对 `os.path.abspath` 做摘要，两边的
  `source_root_digest` 不一致。改用无软链接的 `TMPDIR` 后，Node 22.22.3 与 Node 26 下都通过；Linux CI
  没有这个软链接，不受影响。这是 LoopX 侧的条件而非 NoKV 的，本条目只做记录。
- **本条目没有确立的事。** incarnation 轮转本身没有在这里被演练：NoKV 没有客户端侧的 retire 或
  recreate 动词，探针只能证明过期围栏在当前 incarnation 上被拒绝；轮转由 NoKV-Lab/NoKV#514 的
  executor 测试覆盖。一个 owner、一个节点、一个 workbench：对可用性、故障切换、重启或恢复、容量
  （见 2026-09-19 条目）、认证传输不作任何断言。profile hold 由 maintainer 决定去留；RFC 头部与附录 A
  仍写 0.11.0，而 helper、ladder、探针已钉 0.11.1，本条目记录这一不一致但不解决它。
