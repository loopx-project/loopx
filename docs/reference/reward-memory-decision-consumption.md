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

使用上述导出入口和 `resolve_reward_memory_experiment` 的原配置读回；不可用时
不能拿未经验证的配置替代。原 hook 的范围、revision、问题、时点、时效/冲突、
读授权 checkpoint 和 provider 原样传入，另加 `query_ready`、模式和应用策略。
`application_id` / `artifact_ref` 绑定同一问题和当前产物，不因重试换身份。
基线和参数须可 JSON 序列化，非法输入在 provider 调用前 fail-open。

TypeScript owns admission and completion (`reward_memory.decision.plan/project`);
Python adapts the existing provider/applier and retains transient private values.
TS receives only compact references, typed statuses, counts and receipt digests:
never the query, lesson, baseline, provider payload or model rationale.
No new store, SDK, key, enablement switch or action authority is introduced.

TS 负责准入和完成语义；Python 只适配现有 provider/applier 并保留瞬时私有值。
TS 只收到引用、状态、计数与摘要，不收到问题、经验正文、原产物或模型判断内容；
不新增存储、SDK、密钥、开关或行动授权。

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

仅 `public_packet` 用于展示，其余结果私有。通过既有执行上下文保留结果；
`previous_result` 仅复用配置和输入均匹配的请求，变化则拒绝复用。后续判断使用
原条目和累计多 corpus 遥测，不重复查询。这不是自动跨进程存储或新的缓存。

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
