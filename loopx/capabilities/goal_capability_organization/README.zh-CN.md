# Goal 级能力改进（M1 后端预览）

语言：[English](README.md)。

本能力只拥有“是否主动寻找改进”的**意图**，不拥有能力启用。复用协调者既有
`before_plan` 入口；quota 路径从同一入口披露当前重规划。没有新增 phase、调度器、
记忆／采用账本、安装器或执行驱动。适用且已启用的能力仍可直接使用，不要求加入
portfolio，也不要求开启本策略。

## 原配置 owner

既有 Goal registry 持有 `control_plane.capability_improvement`。`configure-goal`
与 Goal 设置 preview/apply/CAS API 复用同一 writer、目录、来源版本和读回。
Python 只做 IO 适配；归一化、边界和建议决策唯一归属
`loopx/control_plane/capabilities/goal_capability_organization.ts`，纳入打包运行时
指纹边界。不新增 machine override。

```sh
loopx configure-goal --goal-id example --capability-improvement-mode bounded \
  --capability-discovery-budget-minutes 5 --capability-max-trials 1
# 先检查 preview，再明确追加 --execute 应用。
loopx capability inspect --goal-id example --format json
loopx configure-goal --goal-id example --clear-capability-improvement-configuration
# 明确追加 --execute 清除 override，恢复默认关闭。
```

未配置默认 off。mode 为 off 或 bounded；发现预算 1–30 分钟（默认 5），最多
试用提议 0–2 个（默认 1）。它们只约束建议工作，不是 quota 预留或已消费证明。
Todo、租约、quota、能力／provider readiness 与受保护操作仍由原 owner 准入。
改变该意图不启用其它能力。非法配置在落盘前拒绝；损坏的已存配置显示 invalid，
建议失败不阻塞工作，并可显式清除恢复。

## 渐进式披露

普通唤醒没有明确 Goal 缺口时返回 `no_goal_gap`，建议继续当前工作；不遍历目录、
不查询 provider、不创建任务。replan 标签只披露上下文，不证明存在能力缺口。
显式冷路径可提供公开安全的稳定缺口引用，以及最多八条原 owner 的候选观察：

```sh
loopx agent-context --goal-id example --agent-id coordinator --phase before_plan \
  --capability-planning-trigger replan --capability-gap-ref example/gap \
  --capability-candidate-json '{"capability_id":"example-source","applicable":true,"enabled":false,"configuration_ref":"owner/config-v1","effect_ref":"experiment/baseline","rollback_ref":"owner/rollback"}'
```

优先建议检查适用且已启用的直接能力。否则，试用提议要求明确适用性、关闭状态、
配置 owner 引用、效果基线与回滚引用。缺失／未知或试用预算为零时继续当前工作；
未给候选时可在预算内进行有界发现。引用和调用者观察**不认证执行授权**；行动前
仍读回原 owner。建议不授予 provider 调用、安装、配置变更、交易、签名或转账。

### 经同一规划入口消费试用反馈

试用提案现在返回 `trial_basis_digest`。原 owner 记录结果后，调用者可在
`--capability-candidate-json` 的同一候选中附加可选 `trial_feedback`，例如：

```json
{"outcome_ref":"owner/outcome-v1","trial_basis_digest":"sha256:0000000000000000000000000000000000000000000000000000000000000000","status":"failed"}
```

示意 digest 须替换成原提案返回值。规划 provider 对按下列顺序构造的对象执行 UTF-8
`JSON.stringify` 哈希：`goal_id`、`agent_id`、`todo_id`、`gap_ref`、
`capability_id`、`candidate_revision`、`configuration_ref`、`effect_ref`、
`rollback_ref`。缺失 scope 字段或 revision 为 `null`。可选 `candidate_revision`
与 `outcome_ref` 沿用有界引用格式；digest 是 `sha256:` 后接 64 位小写十六进制。
适用性与启用状态仍须满足原要求。

| 调用者声明的反馈 | 对该候选的建议 |
| --- | --- |
| basis 一致，`failed` 或 `no_evidence` | 继续工作，不再次提议相同输入的试用 |
| basis 一致，`succeeded` | 检查原 owner 结果，不推断效用或采用 |
| scope、revision 或引用变化 | 与原 owner 检查已过期反馈 |
| 没有反馈 | 保留原试用提案行为 |
| 反馈格式非法 | 隔离可选建议失败；取得新的 owner 观察后可恢复 |

后续独立且没有反馈的候选仍可使用；已启用直接候选在反馈校验前优先选中，
关闭策略时跳过反馈。这个确定性建议 consumer 不认证回执或持久化重试门禁。
digest 只标识声明，不认证真实性。重命名 revision 不证明试用改进，原准入与
效果评审仍须执行。记录、退役与回滚保留原 owner。

范围细化参考
[Rethinking the Evaluation of Harness Evolution for Agents](https://arxiv.org/html/2607.12227v3)：
有界工具反馈、任务效果、迁移和开发／评估总成本须分别留证，确定性测试不证明
实证效用。

使用／结果／效果和退役判定继续写回既有 Todo、outcome 与能力 owner。实际价值
必须相对基线改善目标结果或成本；调用、PR 和测试通过不是效用，未知效果保持未知。
经原能力 owner 回滚，不删除历史证据。空、非法或超预算建议独立失败，不能阻断
有效工作，也不能放宽既有准入。

## 交付与验收边界

已实现 CLI 配置／读回、共享 Goal 设置 API 和 live quota 注入。既有 schema-driven
App editor 收到新增字段，但**打包 App 交互／本地化及 Lark 控制／指导仍有配套工作，
因此产品交付为 partial**。配套必须复用同一投影，不能另建 UI/chat 真相源。
未宣称真实领域效用、默认采用、自主演化或完整 portfolio 里程碑完成。

这是“改进意图”的 M1 后端切片，不等于
[portfolio RFC](../../../docs/architecture/rfcs/goal-scoped-capability-portfolio-v0.zh-CN.md)
中同编号的外部证据／connector 里程碑；支持 direct-first，不新建组合 DSL。
验证纯策略和真实 CLI／配置路径：

```sh
node --experimental-strip-types --test tests/control_plane_ts/goal_capability_organization.test.ts
LOOPX_USAGE_PING=0 uv run --extra test python -m pytest tests/capabilities/test_capability_improvement.py -q
```
