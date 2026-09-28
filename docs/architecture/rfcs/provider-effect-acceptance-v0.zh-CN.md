# RFC：Provider 在效果接受点执行授权（v0）

- **RFC 状态：** 已接受
- **替代 / 关闭：** 无
- **交付成熟度：** 仅设计；尚未接入运行时，也未准入任何 provider
- **作者 / 负责人：** LoopX capability、安全与 provider 维护者
- **创建日期：** 2026-09-28
- **最近规范修订：** 2026-09-28
- **实现基线：** `6643f367064b9921c864db75a042979fddc4b8c3`
- **相关契约：** [总体路线图](loopx-overall-roadmap-v0.zh-CN.md)、
  [效果解释器](agent-loop-effect-interpreter-v0.zh-CN.md)、
  [共享权威](shared-goal-authority-state-provider-v0.zh-CN.md)、
  [Goal 实例身份](goal-instance-identity-and-orphan-recovery-v0.zh-CN.md)、
  [能力组合](goal-scoped-capability-portfolio-v0.zh-CN.md)和
  [人工确认操作](human-confirmed-domain-operations-v0.zh-CN.md)
- **语言镜像：** [English](provider-effect-acceptance-v0.md)

## 文档结构与维护契约

第 1-10 节是长期设计与验收契约。第 11 节是规范性交付计划。第 12 节记录
未决事项。附录不具规范性。中英文文档互为语义镜像，必须同步修改。

---

## 1. 决策摘要

LoopX 将为受保护的外部效果定义一套可选的 provider 效果接受契约。该契约只
解决一个明确缺口：LoopX 在 dispatch 前执行授权检查，不能证明效果 owner 在
提交效果时仍允许该操作。

合规 provider 在自己拥有的同一个线性化点检查当前 provider 可见策略，排序该
检查与撤销，预留效果与授权身份，提交效果或事实性无变化结果，并保存不可变
决策回执。精确重放返回该历史回执的相同 public-safe projection。使用相同身份
绑定不同内容时返回冲突。

LoopX 保留现有职责。它拥有 Goal 与 Todo 权威、准入、governed dispatch journal、
结算顺序、quota 和后续状态转换。provider 拥有外部效果及其事实回执。Provider
记录不进入 `AuthorityStore`，provider 回执也不授予 LoopX 状态转换权限。

该契约默认不存在。现有 provider 与 operation 保持当前行为。未来某个 operation
若要求严格 provider acceptance，而所选 provider 没有满足必需 guarantee 的已
准入声明，则必须在 dispatch 前失败。owner 明确授权的较弱 operation 可以采用
禁止盲目重试规则，仅 dispatch 一次，并保留 `outcome_unknown`。

本 RFC 不增加运行时代码，不启用 provider，不创建分布式事务，也不声称独立
系统之间存在 exactly-once effect。

## 2. 问题与不变量

当前 governed 路径在 dispatch 前持久化 invocation，并向 provider 传递稳定的
`effect_id`。它可以恢复 LoopX writeback 与 quota settlement，而不重复已完成的
本地阶段。然而，如果第三方 provider 接受请求后丢失响应，本地 journal 无法
证明 provider 是否检查了当前授权、是否消费了该身份，也无法证明效果是否只
提交一次。

考虑以下顺序：

```text
T0  LoopX 准入受保护操作
T1  LoopX 或 gateway 检查策略
T2  请求离开 LoopX
T3  策略被撤销
T4  provider 接受请求
T5  provider 提交效果
T6  响应到达或丢失
```

`T1` 的检查能缩短时间间隔，但不能决定 `T4` 或 `T5` 的策略状态。gateway 在
消费本地授权后调用另一个效果 owner 时，也存在同一缺口。

### 不变量

- 最终效果 owner 决定 provider acceptance。
- LoopX 的 Goal、Todo、lease、gate、quota 与 settlement owner 保持不变。
- 精确 `GoalRef` 将授权绑定到一个 Goal 生命周期。
- sender 身份、授权新鲜度、单次消费、重试幂等和效果提交是不同事实。
- provider 根据自己将要应用的值重新计算 request 与 payload digest。
- 撤销和首次接受在 provider 中具有唯一可观察顺序。
- 响应丢失不授权创建新的 effect identity。
- 在判断新效果的当前策略前，精确重放先返回历史事实。
- 已认证的策略拒绝是终态、可重放的决策。
- 未认证请求或 sender 无效请求不能消费或预留可信身份。
- 过期不会让旧 effect identity 或 authorization identity 重新可用。
- 不支持的 strict operation 在 dispatch 前失败，且不得静默降级。
- provider readback 无法证明结果时，unknown 必须保持 unknown。

## 3. 范围与非目标

### 范围内

- provider-neutral 的 requirement 与 guarantee 词汇；
- 精确 Goal 生命周期、effect、authorization、audience、action、target、request、
  payload、policy、sender 与 provider epoch 绑定；
- provider 侧撤销排序和单次授权；
- 效果或无变化结果与不可变决策回执的原子提交；
- 精确重放、查询、冲突、拒绝、压缩与退休规则；
- 明确的 strict、no-blind-retry 与 unsupported 行为；
- 受控 provider conformance 计划与有界资格结论。

### 非目标

- 将外部效果或 provider policy 移入 `AuthorityStore`；
- 将 Goal enablement 或 capability registration 当作安全 token；
- 通用 token 格式、credential store、policy language 或 key service；
- LoopX 与外部 provider 之间的分布式事务；
- 自动切换到其他 provider；
- 追溯取消已提交效果；
- 自动补偿、provider promotion 或默认行为变更；
- 根据未文档化 header 或观测到的 provider 行为声称支持。

## 4. 当前系统与标准契约

在实现基线中：

- command 专属 TypeScript reducer 拥有 coordination lifecycle 与 settlement
  reduction；
- `AuthorityStore` 原子提交 LoopX coordination state、event 与 receipt，但它是
  storage contract，不是外部效果 owner；
- `CoordinationCommandReceipt` 根据 operation identity 和 request digest 恢复
  历史 LoopX command；
- File 与 SQLite store 能验证相同的历史 operation，且不会回退当前 authority head；
- Effect Program 将有序 settlement step 绑定到一个 `effect_id`；
- Python capability admission 选择一个 enabled 且 doctor-ready 的 provider，并
  校验当前只含 `goal_id`、不含 Goal instance 的 Goal capability binding；
- Python governed capability execution 记录请求、dispatch provider、调用
  TypeScript result reduction 并恢复 settlement；
- governed result path 校验返回的 `loopx_external_effect_receipt_v0`；
- provider effect 的不确定性继续由 provider 或 effect ledger 负责。

历史 coordination receipt 不授予当前执行权限。同样，provider effect receipt
报告 provider 事实，但不能完成 Todo，也不能授权其他 LoopX 状态转换。

本设计引用的标准分别定义不同性质：

| 机制 | 能证明什么 | 缺失的性质 |
| --- | --- | --- |
| [OAuth token introspection](https://www.rfc-editor.org/rfc/rfc7662.html#section-4) | authorization server 观测点的 token 状态 | 效果提交时的 provider policy；缓存响应可能过期 |
| [JWT `aud`](https://www.rfc-editor.org/rfc/rfc7519.html#section-4.1.3)、[`exp`](https://www.rfc-editor.org/rfc/rfc7519.html#section-4.1.4) 与 [`jti`](https://www.rfc-editor.org/rfc/rfc7519.html#section-4.1.7) | audience、时间范围和 identifier | 原子单次消费 |
| [DPoP](https://www.rfc-editor.org/rfc/rfc9449.html) | sender 持有与请求 metadata 绑定的 key | 当前业务策略；默认也不绑定 request body |
| [HTTP idempotency](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.2.2) | server contract 定义的重复请求语义 | 授权新鲜度 |
| [PATCH 与 `If-Match`](https://www.rfc-editor.org/rfc/rfc5789.html#section-2) | 支持时的原子 patch 与资源 revision 条件 | provider policy、sender 身份与单次授权 |

任何单独一行都不能提供效果提交时的 provider 侧授权。

## 5. 提议架构

### 5.1 调用方视角

operation owner 声明要求。executor 派生 binding，持久化 governed intent，并
负责恢复。

```ts
const outcome = await executeProtectedEffect({
  requirement: {
    kind: "strict_provider_acceptance_v0",
    sender_binding: "proof_of_possession",
    minimum_receipt_retention_seconds: 604800,
  },
  goal_ref,
  effect_id,
  provider,
  request: { action: "report.publish", target, payload },
  authorization,
});

if (outcome.kind !== "terminal" || outcome.projection.decision === "rejected") {
  return holdWithoutLoopXWriteback(outcome);
}
```

三类代表性 caller 使用同一接口：

- publisher 在一个 operation 中提交 provider 自有对象及其决策回执。
- 远程 job service 将持久 job 创建视为受保护效果。后续 job 成功是另一个结果。
- legacy API 只有取得精确 owner authorization 时才使用
  `explicit_no_blind_retry_v0`。响应丢失后进入 readback 或
  `outcome_unknown`，不得自动重发。

### 5.2 所有权与权威

```text
LoopX typed owner
  -> 获取当前 source-owned Goal admission witness
  -> 解析独立 provider qualification receipt
  -> 准入一个精确 Goal/effect request
  -> 持久化 governed dispatch intent
  -> 发送有界 authorization presentation

provider effect owner
  -> 认证 presentation 与 sender
  -> 排序当前 provider policy 与撤销
  -> 预留身份
  -> 提交 effect 或 no-change 及 decision receipt
  -> 返回或查询同一历史结果

LoopX typed owner
  -> 校验并记录 provider projection
  -> 提交 writeback
  -> 消耗 quota
  -> 准入后续 Goal 或 Todo transition
```

provider 接收 `ExactGoalRef` 作为不可变绑定数据，但不会获得 Goal 写权限。gateway
只有在拥有最终 effect state、共享同一个 atomic log，或收到兼容 downstream
decision receipt 时才满足本契约。

精确 Goal identity 不是 authorization grant。LoopX 必须在写 journal 或 dispatch
前从 source project registry 获取当前 admission witness，并把其 digest 绑定到
authorization。source state 缺失、过期或不可用时必须 fail closed。

### 5.3 Requirement 与 guarantee 声明

operation requirement 与 provider guarantee 相互独立。operation 不能根据所选
provider 降低自己的要求。

```ts
type ProviderAcceptanceRequirement =
  | { readonly kind: "off" }
  | {
      readonly kind: "strict_provider_acceptance_v0";
      readonly sender_binding:
        | "proof_of_possession"
        | "authenticated_channel"
        | "not_required";
      readonly minimum_receipt_retention_seconds: number;
    }
  | {
      readonly kind: "explicit_no_blind_retry_v0";
      readonly owner_authorization_ref: string;
      readonly readback: "required" | "when_available";
    };

type ProviderAcceptanceGuarantees = Readonly<{
  provider_identity_epoch: string;
  policy_ordering: "provider_atomic" | "not_supported";
  single_use: "provider_atomic" | "not_supported";
  request_binding: "canonical_request_and_payload" | "not_supported";
  sender_binding:
    | "proof_of_possession"
    | "authenticated_channel"
    | "not_supported";
  recovery:
    | "exact_replay_and_query"
    | "query_only"
    | "none";
  full_receipt_retention_seconds: number;
}>;

type ProviderAcceptanceQualificationReceipt = Readonly<{
  schema_version: "provider_acceptance_qualification_receipt_v0";
  qualification_id: string;
  qualification_revision: string;
  status: "qualified" | "revoked";
  provider: Readonly<{
    provider_id: string;
    implementation_id: string;
    implementation_version: string;
    deployment_identity_sha256: `sha256:${string}`;
    audience: string;
    provider_identity_epoch: string;
  }>;
  guarantees: ProviderAcceptanceGuarantees;
  evidence_sha256: `sha256:${string}`;
  not_before: string;
  expires_at: string;
  previous_receipt_sha256: `sha256:${string}` | null;
  receipt_sha256: `sha256:${string}`;
}>;
```

strict admission 要求 provider-atomic policy ordering 与 single-use
consumption、canonical request 与 payload binding、可查询的精确重放，以及足够
的 receipt retention。它还要求 operation 指定的 sender guarantee。provider
声明是 evidence input，不是 qualification。LoopX-owned qualification owner 校验
命名 implementation、version、deployment、audience、identity epoch、guarantee
与 evidence 后签发当前 receipt。strict admission 要求最新、未过期且状态为
`qualified` 的 receipt，并将其 digest 与 provider revision、integration-profile
digest 一起绑定。Provider readiness 不能创建该 receipt。

### 5.4 核心类型

以下类型描述 domain semantics。adapter 将 credential、proof bytes、transport
object、raw provider payload 与 storage row 留在私有边界。

```ts
type ProviderOrder = Readonly<{
  ordering_domain_sha256: `sha256:${string}`;
  provider_identity_epoch: string;
  sequence: `uint:${string}`;
}>;

type ProviderSecurityContextBinding = Readonly<{
  tenant_sha256: `sha256:${string}`;
  issuer_sha256: `sha256:${string}`;
  subject_or_client_sha256: `sha256:${string}`;
  sender:
    | {
        mechanism: "proof_of_possession" | "authenticated_channel";
        principal_sha256: `sha256:${string}`;
      }
    | { mechanism: "not_required"; principal_sha256: null };
  context_sha256: `sha256:${string}`;
}>;

type ProviderIdentityScope = Readonly<{
  provider_id: string;
  audience: string;
  provider_identity_epoch: string;
  security_context_sha256: `sha256:${string}`;
}>;

type ProviderEffectKey = ProviderIdentityScope & Readonly<{
  effect_id: string;
}>;

type ProviderAuthorizationKey = ProviderIdentityScope & Readonly<{
  authorization_id: string;
}>;

type ProviderEffectQuery = Readonly<{
  effect_key: ProviderEffectKey;
  authorization_key: ProviderAuthorizationKey;
  binding_sha256: `sha256:${string}`;
  query_sha256: `sha256:${string}`;
}>;

type ProviderEffectBinding = Readonly<{
  schema_version: "provider_effect_binding_v0";
  effect_key: ProviderEffectKey;
  authorization_key: ProviderAuthorizationKey;
  goal_ref: Readonly<{
    goal_id: string;
    goal_instance_id: string;
  }>;
  goal_admission: Readonly<{
    source_authority_sha256: `sha256:${string}`;
    source_revision: string;
    goal_ref_sha256: `sha256:${string}`;
    receipt_sha256: `sha256:${string}`;
  }>;
  action: string;
  target_sha256: `sha256:${string}`;
  canonical_request_sha256: `sha256:${string}`;
  payload_sha256: `sha256:${string}`;
  policy_basis_sha256: `sha256:${string}`;
  security_context: ProviderSecurityContextBinding;
  validity: Readonly<{
    not_before: string;
    expires_at: string;
  }>;
  acceptance: Readonly<{
    requirement: "strict_provider_acceptance_v0";
    sender_binding:
      | "proof_of_possession"
      | "authenticated_channel"
      | "not_required";
    minimum_full_receipt_retention_seconds: number;
    required_guarantees_sha256: `sha256:${string}`;
    qualification_receipt_sha256: `sha256:${string}`;
  }>;
  provider_profile_sha256: `sha256:${string}`;
  binding_sha256: `sha256:${string}`;
}>;

type ProviderPolicyOrderReceipt = Readonly<{
  schema_version: "provider_policy_order_receipt_v0";
  policy_event: "activated" | "revoked" | "superseded";
  policy_basis_sha256: `sha256:${string}`;
  scope_sha256: `sha256:${string}`;
  provider_order: ProviderOrder;
  receipt_sha256: `sha256:${string}`;
}>;

type ProviderEffectDecisionReceipt =
  | Readonly<{
      schema_version: "provider_effect_decision_receipt_v0";
      decision: "committed" | "no_change";
      authorization_disposition: "consumed";
      binding: ProviderEffectBinding;
      provider_order: ProviderOrder;
      policy_order_receipt_sha256: `sha256:${string}`;
      decided_at: string;
      external_object_ref_sha256: `sha256:${string}`;
      evidence_sha256: `sha256:${string}`;
      receipt_sha256: `sha256:${string}`;
    }>
  | Readonly<{
      schema_version: "provider_effect_decision_receipt_v0";
      decision: "rejected";
      authorization_disposition: "terminally_denied";
      reason:
        | "not_yet_valid"
        | "expired"
        | "revoked"
        | "policy_basis_stale"
        | "policy_denied";
      policy_reason_code: string | null;
      binding: ProviderEffectBinding;
      provider_order: ProviderOrder;
      policy_order_receipt_sha256: `sha256:${string}`;
      decided_at: string;
      receipt_sha256: `sha256:${string}`;
    }>;

type ProviderEffectDecisionProjection = Readonly<{
  schema_version: "provider_effect_decision_projection_v0";
  provider_ref_sha256: `sha256:${string}`;
  provider_identity_epoch_sha256: `sha256:${string}`;
  effect_id_sha256: `sha256:${string}`;
  authorization_id_sha256: `sha256:${string}`;
  goal_ref_sha256: `sha256:${string}`;
  security_context_sha256: `sha256:${string}`;
  binding_sha256: `sha256:${string}`;
  decision: ProviderEffectDecisionReceipt["decision"];
  reason:
    | "not_yet_valid"
    | "expired"
    | "revoked"
    | "policy_basis_stale"
    | "policy_denied"
    | null;
  policy_reason_code: string | null;
  provider_order: Readonly<{
    ordering_domain_sha256: `sha256:${string}`;
    provider_identity_epoch_sha256: `sha256:${string}`;
    sequence: `uint:${string}`;
  }>;
  policy_order_receipt_sha256: `sha256:${string}`;
  external_object_ref_sha256: `sha256:${string}` | null;
  evidence_sha256: `sha256:${string}` | null;
  receipt_sha256: `sha256:${string}`;
}>;

type ProviderAcceptResult =
  | {
      readonly kind: "terminal";
      readonly replay: "new" | "exact";
      readonly projection: ProviderEffectDecisionProjection;
    }
  | {
      readonly kind: "presentation_rejected";
      readonly reason:
        | "malformed"
        | "untrusted_issuer"
        | "binding_mismatch"
        | "sender_authentication_failed";
    }
  | {
      readonly kind: "conflict";
      readonly reason:
        | "effect_identity_reused"
        | "authorization_identity_reused";
    }
  | {
      readonly kind: "epoch_rejected";
      readonly presented_epoch: string;
      readonly current_epoch: string;
      readonly retirement_receipt_sha256: `sha256:${string}`;
    }
  | {
      readonly kind: "outcome_unknown";
      readonly recovery: "query_or_exact_replay";
    };

type ProviderQueryResult =
  | {
      readonly kind: "found";
      readonly projection: ProviderEffectDecisionProjection;
    }
  | {
      readonly kind: "tombstone";
      readonly query_sha256: `sha256:${string}`;
      readonly decision: ProviderEffectDecisionReceipt["decision"];
      readonly receipt_sha256: `sha256:${string}`;
    }
  | {
      readonly kind: "absent";
      readonly query_sha256: `sha256:${string}`;
      readonly observed_order: ProviderOrder;
      readonly absence_receipt_sha256: `sha256:${string}`;
    }
  | {
      readonly kind: "epoch_retired";
      readonly presented_epoch: string;
      readonly current_epoch: string;
      readonly retirement_receipt_sha256: `sha256:${string}`;
    }
  | { readonly kind: "unavailable"; readonly reason_code: string };

interface ProviderEffectAcceptance<Request> {
  accept(
    binding: ProviderEffectBinding,
    request: Request,
    authorization_presentation: Uint8Array,
  ): Promise<ProviderAcceptResult>;

  query(
    query: ProviderEffectQuery,
    query_authentication: Uint8Array,
  ): Promise<ProviderQueryResult>;
}
```

`canonical_request_sha256` 覆盖 provider 带版本的 canonical request。
`target_sha256` 与 `payload_sha256` 覆盖精确 target 和 effect payload。provider
根据已认证 tenant、issuer、subject 或 client 与 sender 事实派生 security
context，并与 binding 比较。`binding_sha256` 按 schema 顺序覆盖除自身外的所有
binding 字段。effect key 与 authorization key 都包含 provider、audience、
identity epoch 与 security-context digest。每个 `receipt_sha256` 覆盖该 receipt
中除自身外的所有字段。provider 计算其拥有的 digest，不能信任 caller 提供的值。
`query_sha256` 覆盖两个 identity key 与 binding digest。

`accept` 隐藏 authorization parsing、sender authentication、digest 重算、policy
lookup、identity reservation、effect commit 与 receipt persistence。`query`
在读取历史前认证 tenant、issuer、subject 或 client 与 recovery principal。它是
响应丢失后的恢复边界。公共方法不暴露分离的 check、consume、apply 和 record 阶段。

`ProviderEffectDecisionReceipt` 包含完整 binding，因此留在 provider 私有边界。
`accept` 与 `query` 都返回 `ProviderEffectDecisionProjection`，LoopX journal
也只保存该 projection。projection 使用固定 allowlist，只含 digest、不透明 reason
code 与 ordering value，不得包含 raw target、provider object reference、identity、
policy document 或 authorization presentation。

query authentication 可以使用当前 recovery credential。它必须匹配原始 tenant、
issuer 与 subject 或 client。不同 sender principal 需要 provider 显式签发绑定
`query_sha256` 的 recovery delegation。它不需要复用已过期的 authorization
presentation，只允许读取历史，不能授权新 effect。

`ProviderOrder.sequence` 是带 `uint:` 前缀的规范无符号十进制整数。只有 ordering
domain digest 与 identity epoch 都相同时，两个位置才可比较。provider 将 policy
变更、acceptance decision 与 epoch transition 写入该顺序。
`ProviderPolicyOrderReceipt` 让 decision 使用的 policy event 可独立检查。

### 5.5 Acceptance transaction

provider 执行以下步骤：

1. 解析请求，并认证 authorization presentation、issuer、tenant、subject 或
   client、transport 与所需 sender mechanism。
2. 派生可信 security context。预期 tenant、issuer、subject 或 client、sender
   digest 不匹配时拒绝。
3. 根据 provider 将要应用的值重算 target、canonical request、payload、profile、
   requirement 与完整 binding digest。
4. 进入 provider serialization boundary。先确认提交的 identity epoch 是当前
   epoch，且 effect 与 authorization scope 完全相同，再读取两个 identity index。
5. 两个 identity 与完整 binding 都匹配时，返回精确历史结果。任一 identity
   绑定到不同内容时返回冲突。
6. 对新的可信 identity，在与撤销相同的顺序中，将有效时间与 policy basis 和
   当前 provider-visible policy 比较。
7. policy 拒绝 operation 时，预留两个 identity 并保存终态 rejected receipt。
   其他已认证拒绝使用 `policy_denied` 与有界不透明 code。
8. policy 允许 operation 时，在一个 transaction 或等价 atomic log append 中
   预留两个 identity、消费授权、提交 effect 或事实性 no-change，并保存 receipt。

malformed、untrusted、binding 不匹配或 sender 无效的 presentation 在 identity
reservation 前失败。攻击者不能借此消费可信授权。可信 policy denial 是终态，
因为后续策略变化需要新的 authorization identity。

精确重放发生在 caller 与 presentation 认证之后，但先于新 effect 的当前 policy
与有效期检查。重放只读取历史，不授权另一个效果，后续撤销也不能抹去原始结果。

LoopX 在写 journal 或 dispatch strict work 前校验 source-owned Goal admission 与
独立 provider qualification。provider 绑定这些 receipt digest，但不成为它们的
authority。provider 还校验请求的 guarantee set 与自身可执行 contract 一致。

### 5.6 撤销与状态转换

```text
未见过的可信授权
  -> committed       accept 先排序；effect 与 receipt 一起提交
  -> no_change       accept 先排序；事实性无变化与 receipt 一起提交
  -> rejected        尚未生效、过期、旧 basis、已撤销或 policy denied
  -> 无记录          provider transaction commit 前失败

committed | no_change | rejected
  -> 同一结果        精确重放
  -> conflict        effect 或 authorization binding 改变
  -> tombstone       超过声明期限后压缩完整 receipt

已退休 identity epoch
  -> epoch_rejected  acceptance 不能使用旧 epoch
  -> epoch_retired   query 不返回已删除的 operation metadata
```

revocation 与 acceptance 共享 provider ordering domain。revocation 先排序时，
effect 被拒绝。acceptance 先排序时，其 receipt 保持已提交历史事实，revocation
只影响未来授权。该规则对并发排序，但不声称零宽竞态，也不声称独立 policy
authority 能即时传播。

LoopX 本地 revocation 只有在 provider 接受对应 policy update 后才具有 provider
排序意义。Provider adapter 必须报告最后一个 provider-visible policy basis，不能
把本地 intent 改写成远程事实。
decision receipt 与 policy-order receipt 携带可比较位置。因 revoked 被拒绝的
decision 引用排在它之前的精确 revocation receipt。

### 5.7 重放、恢复与保留

恢复遵循最后一个已证明边界：

| 已证明边界 | 允许的恢复 |
| --- | --- |
| 没有 governed journal | 不把任何 dispatch 归因于本次 attempt |
| 已记录 intent，但没有 provider result | 认证后查询相同 effect 与 authorization key；只有线性化 `absent` witness 与当前 Goal、qualification、operation admission 都成立时，才允许精确重试 |
| provider 有终态 decision，但本地响应丢失 | 返回同一 projection 与 receipt digest；不重复 effect |
| provider projection 已写 journal，但 writeback 未完成 | 只恢复 LoopX writeback |
| writeback 已提交，但 quota 未完成 | 只恢复 quota settlement |
| provider query 不可用 | 保持 `outcome_unknown`；不创建另一个 identity |

provider 只有在校验当前 identity epoch，并在线性化位置读取两个 identity index
后，才能创建 `absent` witness。旧 epoch 绝不返回 `absent`。query 返回
`epoch_retired`，acceptance 返回 `epoch_rejected`。

provider 至少在 operation 声明的 settlement、replay、reconciliation 与 audit
horizon 内保留完整 receipt。声明的 exact replay 期限不能超过完整 receipt
保留期限。

超过该期限后，provider 可以把 receipt 压缩为 tombstone。tombstone 保留 effect
key、authorization identity、binding digest、terminal decision、receipt digest
和 provider identity epoch。只有在 provider 持久退休整个 identity epoch，并
拒绝该 epoch 的所有请求与查询后，才能删除逐 operation tombstone。删除后，旧
epoch query 只返回 `epoch_retired`，不能声称已删除的 operation metadata。过期
本身不会释放 identity。

current 与 retired epoch registry 不能随 effect-store snapshot 一起回退。
provider 可以使用独立 monotonic store 或等价 append-only recovery boundary
满足该规则。restore 无法证明最新 epoch 时，provider 保持 unavailable。

### 5.8 兼容模式

| Requirement | Provider guarantee | 行为 |
| --- | --- | --- |
| 缺失或 `off` | 任意现有 profile | 保持当前 governed 行为 |
| Strict | 所需 guarantee 全部存在并已固定 | 使用 provider acceptance |
| Strict | 任一 guarantee 缺失、过期或未准入 | dispatch 前失败 |
| Explicit no-blind-retry | 精确 owner authorization 与声明的 readback | 持久化 intent，只 dispatch 一次，随后 query |
| Explicit no-blind-retry | dispatch 后 readback 不可用 | 保留 `outcome_unknown`，不重发 |

现有 `loopx_external_effect_receipt_v0` 保持当前含义，且永远不能满足 strict
acceptance。未来 receipt 可增加指向 `provider_effect_decision_receipt_v0` 的
紧凑引用。历史 receipt 不会获得更强解释。

## 6. 替代方案与设计取舍

- **将 provider acceptance 放入 `AuthorityStore`。** 拒绝。它会混合 LoopX
  coordination 与 provider effect truth，仍无法原子提交远程 effect。
- **暴露 check、consume、apply 与 record 调用。** 拒绝。这种按时间阶段拆分的
  接口迫使 caller 处理相同的 crash 与 revocation 窗口。
- **使用短期 sender-bound token 加 idempotency key。** 拒绝把它当作充分证明。
  这些机制约束不同风险，但不能证明当前 provider policy、原子消费和效果提交。
- **在 gateway 消费。** 除非 gateway 拥有最终 effect、共享其 atomic log，或
  收到等价 downstream receipt，否则拒绝。
- **使用通用 distributed transaction。** 拒绝。普通 provider 无法参与，而且
  coordinator 会扩大 authority 与 availability 边界。
- **把所有 denial 都视为 consumed。** 在认证前拒绝该做法，否则攻击者可以
  消耗其他 caller 的 identity。可信 policy denial 保持终态且可重放。
- **固定时间后删除 tombstone。** 如果没有 identity epoch fence，则拒绝该
  做法，因为旧请求可能重新成为新请求。

## 7. 安全、隐私与兼容性

- Authorization presentation、sender proof、credential、raw payload 和私有
  policy document 留在 provider 私有存储与 transport。
- 完整 provider receipt 保持私有。LoopX 只持久化固定
  `ProviderEffectDecisionProjection` allowlist。
- 每个 projection reason code 都必须是 profile 批准的 ASCII token，最多 64 个
  字符。Provider message 与 stack trace 保持私有。
- provider 认证 tenant、issuer、subject 或 client、audience 与所需 sender。
  caller 提供的 policy basis 或 authorization ID 不是 authority。
- provider 隔离 tenant、issuer、subject 或 client、sender、audience 与 identity
  epoch。acceptance 与 query 不能跨越这些边界。
- strict admission 读取当前 LoopX-owned qualification state。Provider readiness、
  profile field 或旧 qualification revision 不能代替该状态。
- strict admission 读取当前 source-owned Goal admission witness。精确 `GoalRef`
  或缓存 capability binding 不能代替该 witness。
- provider restore 或 rollback 必须保留当前 policy、receipt 与 tombstone
  历史。推进 epoch 时必须使用不可回退的 epoch registry。
- 时钟回拨必须 fail closed。每个 profile 声明 clock skew 处理和 time source
  前提。
- key rotation 必须保持 retained receipt 可验证，或者记录不可变 verification
  key reference。
- strict profile 按实现、版本、部署边界和 audience 分别准入。profile 名称本身
  不能证明任何性质。
- feature-off 行为保持不变。strict operation 绝不 fallback。
- 除非 operation owner 明确要求本契约，现有 provider 只按其当前 contract
  保持可用。

## 8. 迁移与回滚

本 RFC 不修改持久化格式或运行时行为。

未来集成采用增量方式：

1. 在 production composition 外增加 provider-neutral type、独立 qualification
   owner 与 controlled provider。
2. 增加 source-owned Goal admission、可选 operation requirement 与已固定
   provider guarantee。
3. 在现有 effect receipt 旁增加版本化 public projection。
4. 为一个模拟 protected operation 显式启用。
5. 分别准入每个真实 provider 与 trust boundary。

启用 strict operation 前，必须固定 operation schema、canonical encoding、
recovery horizon、provider identity epoch 和 rollback procedure。mixed-version
host 在 dispatch 前拒绝未知 strict requirement。

rollback 停止新的 strict admission，并保留所有 decision receipt、tombstone
与 unresolved outcome。它绝不把 strict work 转为较弱模式，不删除 unknown
operation，也不使用新 identity 重试。

## 9. 验证与验收

| 声明 | 测试或证据 | 必须结果 | 边界与排除项 |
| --- | --- | --- | --- |
| 精确绑定 | 逐项改变 Goal、Goal admission、effect、authorization、tenant、issuer、subject 或 client、audience、action、target、request、payload、policy、sender、requirement、qualification、profile 与 epoch 字段 | presentation rejection 或 conflict；原结果不变 | 不验证业务价值 |
| 独立 qualification | 将 profile 标记为 ready，但不提供当前 qualification，再撤销当前 qualification 或令其过期 | strict work 在 dispatch 前失败 | qualification owner 与 provider 保持独立 |
| Goal provenance | 对精确 Goal 值使用过期、缺失或不可用的 source witness | strict work 在写 journal 或 dispatch 前失败 | `GoalRef` 是绑定数据，不是 authority |
| 单次授权 | 让同一 authorization 在不同 effect 间竞争 | 一个终态决策预留 authorization；没有第二个 effect | 仅 controlled provider |
| 精确重放 | 并发相同调用，并在后续 operation 后重放历史调用 | 同一 receipt，只有一个 effect | 历史结果不授予当前 LoopX authority |
| 崩溃原子性 | 在每个 provider transaction 边界前后停止进程 | 不存在半消费、半 effect 或仅 receipt 状态 | 进程崩溃，不含断电 |
| 响应丢失 | commit 后丢响应，重启、query 并精确重放 | 同一 receipt；恢复阶段零 effect | 需要 query 可用 |
| 权威 absence | 首次 acceptance 前查询当前 epoch，再让 acceptance 与 retry 竞争 | 有序 `absent` witness 或终态 receipt 决定恢复 | 普通 `missing` 不充分 |
| 撤销先行 | acceptance 前提交 revocation | 终态拒绝且零 effect | 仅 provider-visible revocation |
| 接受先行 | revocation 前提交 acceptance | 一个 effect 和不可变的更早顺序 | 不包含追溯取消 |
| 并发撤销 | 在固定 barrier 同时释放两个 operation | 可比较的 policy 与 decision receipt 解释一个持久顺序 | 不声称零宽竞态 |
| Security-context 隔离 | 跨 tenant、issuer、subject 或 client 与 sender 复用有效 identifier，并使用各 context 查询 | 不发生跨 context 冲突、读取或 receipt 泄漏 | 不证明设备完整性 |
| Sender 隔离 | 使用错误的 sender mechanism 或 principal 携带有效 authorization | 非消费型 presentation rejection | authenticated channel 不是 proof of possession |
| 可信 denial 重放 | policy 变化后重试尚未生效、已过期、已撤销、旧 basis 或普通 policy-denied authorization | 同一 rejected receipt 与有界 reason | 新 authorization 可以形成新 operation |
| 保留 | 压缩 receipt、恢复 backup、退休 epoch、删除 tombstone，并重放旧请求 | tombstone 或 `epoch_retired` 阻止新 effect；旧 epoch 不返回 `absent` | horizon 由 profile 决定 |
| Restore fence | 将 effect store 恢复到 durable epoch registry 之前的状态 | provider 保持 unavailable 或拒绝旧 epoch | 不准入 power-loss durability |
| Strict 兼容性 | strict work 搭配过期、较弱或不支持的 profile | dispatch 前类型化失败 | 现有 off 行为不变 |
| 较弱模式不确定性 | no-blind-retry provider 丢失响应 | readback 或终态 `outcome_unknown`；一次 dispatch | 不声称 exactly-once |
| 隐私 | 扫描 fixture、projection 与文档 | 无 credential、proof bytes、raw payload、identity、target、provider object reference、private link 或 local path | 私有 provider receipt 不进入 LoopX journal |

首份可执行证据使用真实 SQLite 文件、独立进程、确定性 clock 与 transaction
barrier。其 verdict 只准入受控实现及已测试的进程崩溃边界，不准入 independent
trust domain、network partition、power-loss durability、第三方 provider、
production scale 或 promotion。

## 10. 运维契约

operator 必须能够检查：

- operation requirement、qualification state 与已固定的 provider guarantee；
- provider、identity epoch、Goal admission、security context、effect ID 与
  authorization ID 的 digest；
- binding、policy-order 与 receipt digest；
- terminal decision 与 provider-order reference；
- full-receipt 与 tombstone retention horizon；
- readback 是否可用；
- `unsupported`、`conflict`、`outcome_unknown` 与 provider-unavailable reason；
- 不含私有策略内容的最后 provider-visible policy basis。

恢复使用一个 identity 和一个 provider boundary。operator 不得清除 tombstone、
更换 effect ID 或改选 provider 来推进 unknown operation。credential revocation
阻止新的认证，但不能抹去已提交效果或其恢复义务。

backup 与 restore procedure 保留 decision record 与 identity epoch。provider
若不能证明恢复状态包含最新 receipt 与 tombstone 历史，必须停止接收工作。只有
通过不可回退的 epoch registry，恢复过程才能推进并发布新 epoch。旧 epoch 请求
继续被拒绝，其 query 返回 `epoch_retired`。

## 11. 规范性交付计划

| 里程碑 | 交付行为 | 入口门禁 | 退出证据 | 回滚 |
| --- | --- | --- | --- | --- |
| M0 | 本中英文 RFC、索引与 roadmap 映射 | 当前所有权审计和公开标准研究 | 文档治理与 public/private 扫描通过 | 回退文档；运行时不变 |
| M1 | production composition 外的 provider-neutral contract、qualification receipt、controlled SQLite provider 与 deterministic conformance suite | M0 accepted；canonical encoding 与 retention 决策解决 | 第 9 节 controlled matrix、mutation case 与进程崩溃恢复通过 | 删除隔离 fixture 与 type；无 production state |
| M2 | source-owned Goal admission、可选 requirement 与 guarantee 声明、public receipt projection，以及一个模拟 governed consumer | M1；精确 Goal lifetime 与 receipt version 集成获批 | strict 分发前失败、off parity、响应丢失恢复、隐私扫描与完整 settlement 通过 | 关闭 opt-in；保留 receipt 与 unknown outcome |
| M3 | 一个独立部署的 provider 与 policy authority | M2；认证 issuer、audience、sender、tenant、restore 与 rotation profile | independent-trust-domain revocation、network、restart、backup 与 upgrade 资格通过 | 停止新准入；保留 query 与 reconciliation |
| M4 | 一个命名 production provider 与 protected operation | M3 加 provider-specific 运维批准 | 真实 entrypoint、provider receipt、recovery、privacy 与 rollback 证据 | 只关闭该命名 operation；不得静默降级 |

不得根据一个 milestone 类推晋升其他 provider。

## 12. 未决事项

### 12.1 Canonical request encoding

Capability 与 provider 维护者负责该决策。选项包括 JSON Canonicalization Scheme、
带版本的 binary encoding，或带版本的 provider-native encoding。建议每个
operation 使用一个带版本 encoder，不定义通用 encoding。cross-language golden
vector 与 mutation test 必须证明每个有语义的变化都会改变 digest。该决策阻塞 M1。

### 12.2 Authorization presentation

安全与 provider 维护者负责该决策。V0 可以标准化一种 wire format，也可以只
标准化解析后的 semantics。建议将 JWT、COSE、SSH signature、mutual TLS 与
authenticated channel 留在 adapter 后面。跨 adapter 的 issuer、audience、tenant、
subject 或 client、sender、expiry 与 replay 负例必须产生相同 domain result。
该决策阻塞 M1。

### 12.3 Qualification owner

Capability 与安全维护者负责该决策。选项是专用 qualification registry，或现有
独立 owner 下的 typed record。除非已有 owner 的 lifecycle 与 revocation rule
完全相同，否则使用专用 typed owner。证据必须证明 provider registration、
enablement 与 readiness 不能创建或重新激活 qualification，还必须证明撤销
qualification 会阻止新的 strict admission。该决策阻塞 M1。

### 12.4 Sender requirement

每个 operation owner 在 proof of possession、authenticated channel 与不要求
sender binding 之间选择。高风险跨主机 effect 使用 proof of possession。只有在
qualification 绑定 deployment 与 channel principal 时，才能使用 authenticated
channel。跨 principal replay 与 mechanism downgrade test 必须通过。该决策对模拟
operation 阻塞 M2，对独立部署阻塞 M3。

### 12.5 Retention horizon

governed execution owner 选择一个全局 minimum 或按 operation 声明的 minimum。
建议以 operation requirement 为下限，并允许 provider 延长完整 receipt 保留期。
crash、restore、reconciliation 与 audit test 必须覆盖声明区间及向 tombstone 的
转换。该决策阻塞 M1。

### 12.6 Policy replication

provider owner 在 co-located policy、ordered push replication 与 ordered pull
replication 之间选择。transport 可以由 provider 自定，但 acceptance ordering
domain 必须生成 `ProviderPolicyOrderReceipt`。并发 revoke、stale replica、
restore 与 rollback test 必须证明记录的顺序。该决策阻塞 M3。

### 12.7 首个模拟 consumer

Capability 维护者在 controlled store 中的 durable job creation 与 publication
之间选择。建议使用 durable job creation，因为其 provider-owned commit boundary
与后续 job outcome 是不同事实。fixture 必须暴露 effect count、decision history、
query 与 deterministic crash barrier。该决策阻塞 M2。

## 附录 A：公开资料

- [RFC 7519：JSON Web Token](https://www.rfc-editor.org/rfc/rfc7519.html)
- [RFC 7662：OAuth 2.0 Token Introspection](https://www.rfc-editor.org/rfc/rfc7662.html)
- [RFC 8693：OAuth 2.0 Token Exchange](https://www.rfc-editor.org/rfc/rfc8693.html)
- [RFC 8705：OAuth 2.0 Mutual-TLS client authentication](https://www.rfc-editor.org/rfc/rfc8705.html)
- [RFC 8785：JSON Canonicalization Scheme](https://www.rfc-editor.org/rfc/rfc8785.html)
- [RFC 9110：HTTP Semantics](https://www.rfc-editor.org/rfc/rfc9110.html)
- [RFC 9449：OAuth 2.0 Demonstrating Proof of Possession](https://www.rfc-editor.org/rfc/rfc9449.html)
- [GitHub REST API endpoints for Gists](https://docs.github.com/en/rest/gists/gists?apiVersion=2022-11-28)

GitHub 的公开 Gist create 与 update contract 是不具备本 acceptance contract
的外部 provider 示例。已文档化 operation 没有定义自定义单次授权消费、operation
result query 或 `Idempotency-Key` 语义。这是对公开 contract 的说明，不是对
GitHub 内部实现的断言。
