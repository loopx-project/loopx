# Query-ready Reward Memory consumption / 决策就绪的经验消费

This optional caller API closes **qualified recall → private context delivery →
explicit semantic assessment**. It does not generate a query, enable a provider,
write memory, or prove utility. Existing experiment configuration, corpus scope,
freshness/conflict checks and provider adapters remain their owners.

此可选 API 连接「合格召回 → 私有上下文交付 → 显式语义判断」。它不生成问题、
开启 provider、写入记忆或证明效果；配置、corpus 范围、时效/冲突和 provider
仍由既有 owner 持有。应先冻结具体问题和当前产物，再调用，不在每次心跳重复召回。

## Entry and ownership / 入口与归属

Import `run_reward_memory_decision` and `assess_reward_memory_decision` from
`loopx.capabilities.reward_memory`. Resolve the original normalized configuration
with `resolve_reward_memory_experiment`; an unavailable binding must not be
replaced with an unverified configuration. Pass the existing automatic-recall
hook arguments unchanged: surface, workspace/project, revision, bounded queries,
observation time, freshness/conflict, read-authority checkpoints and provider.
Add `query_ready`, the consumer mode and an explicit application strategy.
`application_id` and `artifact_ref` identify this question and current artifact,
not a fresh identity on every retry. The caller's baseline/arguments must be
JSON-compatible; non-serializable input fails before recall.

Read-authority checkpoints must match the exact consumer surface and corpus;
a turn-admission checkpoint cannot authorize a different review surface.
`freshness_context.age_seconds`, when supplied, is a nonnegative integer.
Rejected requests expose only the original hook's allowlisted
`boundary_reason_code` and, for typed input errors, `boundary_detail_code`,
never exception text or private input values. The details distinguish
`freshness_age_invalid`, `freshness_context_invalid`,
`read_authority_checkpoint_missing` and `read_authority_checkpoint_invalid`.
Existing reason codes, ValueError compatibility, validation order and gates remain unchanged.

使用上述导出入口和 `resolve_reward_memory_experiment` 的原配置读回；不可用时
不能拿未经验证的配置替代。原 hook 的范围、revision、问题、时点、时效/冲突、
读授权 checkpoint 和 provider 原样传入，另加 `query_ready`、模式和应用策略。
`application_id` / `artifact_ref` 绑定同一问题和当前产物，不因重试换身份。
基线和参数须可 JSON 序列化，非法输入在 provider 调用前 fail-open。

读授权 checkpoint 必须匹配本次 surface/corpus，不能拿 Turn 准入的 checkpoint
授权另一评审入口；age_seconds 如提供，须为非负整数。拒绝回执仅投影原 hook
白名单内的 boundary_reason_code，以及输入错误的 boundary_detail_code；细分年龄非法、
时效上下文非法、读授权缺失和读授权格式非法，不暴露异常正文或私有参数。
保留原错误码、ValueError 兼容、校验顺序与门禁。

Use `build_reward_memory_surface_read_authority_checkpoints(config, surface_id,
verified=original_proof_verified, source_ref=original_read_authority_source)`
from the same package. It selects only that surface's configured corpora through
the existing configuration owner, then TS assembles the exact workspace/project,
optional user/peer/session, read-authority and surface references. The caller must
actually verify its original read authority: an enabled config or ingest policy
alone is not read proof. `verified=False` stays false and blocks recall. It does
not infer a proof source, enable the capability or contact a provider. The Turn
wrapper uses this same projection and retains its verified registry source.

通用 helper 按实际 surface 和原配置选择 corpus，由 TS 组装精确范围；调用方仍须
真实核验原读权限并显式传入 verified/source_ref，不能把开关或写入 policy 当作读授权。
False 不会升级为 True；不推断授权来源、不启用能力、不调用 provider。原 Turn wrapper
复用该投影并保留 registry 来源。不要以生成了 checkpoint 为由宣称授权核验已完成。

Checkpoint transport failure remains optional-enrichment failure: managed Turn
admission returns its existing fail-open `runtime_unavailable` packet. The explicit
`agent-turn-recall --execute` CLI returns a safe `runtime_unavailable` packet and
exit code 2. Neither path calls the provider or writes a successful same-Turn
receipt when checkpoint construction fails; a later healthy retry uses the same
Turn identity. These zero-call guarantees apply before provider invocation only.

checkpoint 传输失败不成为普通 Turn 的新门禁：managed 准入沿用原 fail-open
`runtime_unavailable`；显式 CLI 返回安全的同类 packet 和退出码 2。构建失败时
均不调用 provider、不写成功的同 Turn 回执；恢复后沿用原 Turn 身份重试。
零调用保证仅适用于 provider 调用前的构建失败，不覆盖调用后的异常。

If computing age from timestamps, first reject an observation in the future;
then round elapsed seconds upward to an integer. Never clamp a negative age,
refresh the original observation time, or change policy to make recall pass.
This helper intentionally does not calculate or correct age for the caller.

由时间戳计算年龄时，先拒绝未来观察，再将经过秒数向上取整；不能截断负值、
刷新原观察时间或改 policy 来过门。helper 不替调用方计算或纠正年龄。

TypeScript owns admission and completion (`reward_memory.decision.plan/project`);
Python adapts the existing provider/applier and retains transient private values.
TS receives only compact references, typed statuses, counts and receipt digests:
never the query, lesson, baseline, provider payload or model rationale.
No new store, SDK, key, enablement switch or action authority is introduced.

TS 负责准入和完成语义；Python 只适配现有 provider/applier 并保留瞬时私有值。
TS 只收到引用、状态、计数与摘要，不收到问题、经验正文、原产物或模型判断内容；
不新增存储、SDK、密钥、开关或行动授权。

This slice does not migrate the existing Python SDK's scope/freshness validation;
it adds no second TS admission rule for those checks. Python remains the original
configuration/provider adapter and input-error source; TS owns the shared
checkpoint projection and allowlisted decision diagnostics.

此切片不迁移原 Python SDK 的范围/时效校验，也不在 TS 复制准入规则。Python 保留
原配置/provider 适配与输入错误来源，TS 持有共享 checkpoint 投影及白名单诊断。

| Mode / 模式 | Provider / 调用 | Meaning / 意义 |
| --- | --- | --- |
| Disabled/unconfigured / 未配置或关闭 | Zero; returns `None` / 零调用，无新 packet | Original path unchanged / 原路径不变 |
| `preview` | Zero / 零调用 | Admission preview, never adoption / 预检，不代表采用 |
| `recall_only` | Existing bounded route / 原有界路由 | Redacted recall observation, base unchanged; no semantic claim / 脱敏观察，不表示语义采用 |
| `execute` without strategy/artifact / 缺策略或产物 | Zero / 零调用 | `incomplete`, research may continue / 不完整，研究可继续 |
| `execute` + `context_delivery` | Existing bounded route / 原有界路由 | Private context available, **not** semantic application / 私有上下文可读，非语义采用 |
| `execute` + `semantic_application` | Existing bounded route / 原有界路由 | Exact attributed `applied/ignored/refuted` / 当前产物的归因判断 |

## Minimal caller integration / 最小接入

`hook_arguments` below are the already-qualified arguments from the caller's
existing surface. The callback is the original SDK applier contract, not a
provider response interpreted as authority:

下例 `hook_arguments` 来自既有已验证 surface；callback 复用原 SDK 的 applier
契约，不把 provider 内容解释成授权。

```python
from loopx.capabilities.reward_memory import (
    run_reward_memory_decision, assess_reward_memory_decision,
)

def deliver_context(base, items):
    return {
        "outcome": "applied",  # SDK delivery; NOT semantic-use evidence
        "output": {"baseline": base, "private_context": items},
        "memory_refs": [item.memory_ref for item in items],
        "current_artifact_verified": True,  # caller must actually verify it
        "reasoning_summary": "Qualified context delivered for separate review.",
    }

delivered = run_reward_memory_decision(
    config, query_ready=True, mode="execute",
    application_kind="context_delivery", apply_memory=deliver_context,
    **hook_arguments,
)
if delivered is not None and delivered.public_packet["context_delivery_verified"]:
    # The real caller/model reads delivered.output and the CURRENT artifact.
    # judge returns output, outcome, current_artifact_verified, memory_refs
    # from these exact items, and a bounded evidence-backed reasoning_summary.
    assessed = assess_reward_memory_decision(delivered, apply_memory=judge)
    public_receipt = assessed.public_packet
```

`judge` must represent actual comparison, not unconditional adoption. For
`ignored/refuted`, keep the original baseline. All semantic dispositions require
nonempty attribution to these exact recalled items and verified current artifact.
An applied lesson may preserve the decision; application is still not utility.

`judge` 必须表达真实比较，不能无条件采用；ignored/refuted 保持原基线。
所有语义判断都需引用本次实际条目并核验当前产物。经验被采用也可能不改变结论，
更不代表质量、成本、收益或 alpha 已改善。

Only `public_packet` is a display projection. Output, session, original
application receipt, baseline and request remain caller-private, never generic
frontend/Lark/registry payloads. Retain the result in the caller's existing
execution context. `previous_result=result` replays only an exact request digest
(configuration and input included) without another provider call; changed input
returns `replay_request_mismatch`. Reassessment uses retained qualified items and
the original **cumulative** multi-corpus counters, not a second query. This is
caller-retained replay, not automatic cross-process persistence or a new cache.

Retain the complete private result, not just context/public_packet/application
receipt. `assess_reward_memory_decision` needs the exact recall session and
attribution. A lost session after EOF/restart is incomplete, even if context was
delivered; do not re-query or fabricate semantic completion. There is currently
no supported cross-process restore API. Caller-owned persistence and a future
validated restore contract remain separate from this in-process replay API.
The private result retains the **original context-delivery receipt** separately
from the later semantic receipt. TypeScript revalidates its application, artifact,
surface and lesson attribution, so assessment (including an incomplete assessment)
does not erase a previously verified delivery. A direct semantic callback without
that receipt leaves `context_delivery_verified=false`; disposition and utility
are separate facts. This flag attests the caller's verified SDK context callback,
**not** frontend/Lark transport delivery or model utility. Public packets alone
cannot recreate that private lineage or upgrade historical receipts.

If the SDK provider/application has finished but TS result projection is
temporarily unavailable, the complete result retains its private observation
and pending output. An exact `previous_result` replay retries only the existing
TS projection. Assessment first recovers the projection; it may then make the
first semantic judgment after verified context delivery, but does not repeat
an already-attempted semantic callback, even when its evidence is invalid.
A later explicit assessment may correct that incomplete SDK evidence without
recall. While TS remains unavailable, the same
incomplete result and baseline remain; after recovery, TS revalidates the
original receipts before exposing the retained output or completion. Changed
configuration/question/scope/artifact still fails the exact request fence;
invalid application evidence is not upgraded. Pre-provider failures are not
automatically retried. No new provider permission, persistent store, retry loop
or cross-process restore API is introduced.

仅 `public_packet` 用于展示，其余结果私有。通过既有执行上下文保留结果；
`previous_result` 仅复用配置和输入均匹配的请求，变化则拒绝复用。后续判断使用
原条目和累计多 corpus 遥测，不重复查询。这不是自动跨进程存储或新的缓存。

需保留完整私有 result，不能只存 context/public_packet/application receipt。
EOF/重启丢失 recall_session 时，交付过上下文也不能完成 assessment；不重查、不补造
语义完成。当前没有受支持的跨进程恢复 API，持久化与后续验证恢复合同是独立缺口。
私有结果分别保留原上下文交付回执和后续语义回执，TS 对应用、产物、surface 与
经验归因重新核验；评估成功或不完整均不抹掉此前已验证的交付。直接语义 callback
没有该回执时仍为 `context_delivery_verified=false`，语义判断与效果另行记录。
该标记证明调用方已验证的 SDK 上下文 callback，不证明前端/飞书传输或模型收益；
仅凭公开 packet 不能重建这条私有链路，也不追溯升级历史回执。

若原 SDK 的 provider/应用已完成，但 TS 结果投影暂时不可用，完整 result 保留
私有观察和待确认产物。相同请求的 previous_result 仅重试既有 TS 投影；assessment
先恢复投影，可在交付验证后作第一次语义判断，但不重复已尝试的语义 callback，
即使其证据无效。后续显式 assessment 可纠正不完整的 SDK 证据，但不重新召回。
故障期间仍返回同一 incomplete 结果和
基线；恢复后由 TS 重新核验原回执，再披露已保留产物或完成状态。配置、问题、范围
或产物变化仍被精确请求 fence 拒绝，无效判断不能升级；provider 前的失败不自动
重试。不新增 provider 权限、持久存储、自动重试循环或跨进程恢复 API。

Empty/filtered/unavailable and invalid model/transport results preserve the base
and allow ordinary research. Post-provider transport failure retains actual
call/filter counts and the original private receipt. The existing route still
owns its call cap: one query per corpus can mean multiple provider calls.
A caller with a one-call budget must use a qualified one-corpus route.

空结果、过滤、故障和无效判断不阻塞独立研究。provider 后 TS 故障仍保留真实
计数和原私有回执。预算由原路由负责；每 corpus 一次不等于全路由一次，单次预算
须使用已验证的单 corpus 路由。

## Validation, surfaces and rollback / 验证、产品入口与回滚

```sh
uv run --extra test pytest -q tests/capabilities/test_reward_memory_decision.py
node --experimental-strip-types --test tests/control_plane_ts/reward_memory_decision.test.ts
```

This delivery adds a caller API for CLI/managed decision boundaries. Configuration
is unchanged: Dashboard's existing Reward Memory editor still controls
`config_path` and `enabled_agents` through the same preview/apply/readback owner;
Lark keeps its existing status-only coverage. No new frontend control or packaged
frontend change is needed for these unchanged settings. The new typed receipt
can be returned by an integrated caller; **universal frontend/Lark consumption
and cross-session persistence are not delivered by this API**.

本次交付是 CLI/managed 决策边界的调用 API。配置未变，Dashboard 继续通过原
preview/apply/readback 编辑 config_path 和 enabled_agents；Lark 仍为原状态
投影。无需新增配置控件或前端包；接入的调用方可回传同一 typed receipt，
但此 API 不宣称已打通所有前端/Lark 或自动跨 session 持久化。

Rollback the caller to `run_reward_memory_automatic_recall_hook`, whose optional
callback/`available_not_applied` behavior is unchanged. To disable recall, use
the original configuration owner to set `automatic_recall=false` and requalify
the binding, or disable the experiment with `configure-goal --clear-reward-memory-config`.
No recall, disposition or receipt grants orders, signing, transfer, publishing
or legacy-record migration rights.

回滚调用方至原 automatic hook 即可；其可选 callback 语义未变。关闭召回通过
原配置 owner 设置 automatic_recall=false 后重新验证绑定，或用原
configure-goal --clear-reward-memory-config 关闭实验。任何回执均不扩大交易、
签名、转账、发布或旧记录迁移权限。
