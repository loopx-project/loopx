# Unified Finance Gate Contract

`finance_case_contract_v1` is the common, provider-neutral contract for
research cases evaluated by this extension. It does not collect market data or
calculate a finance metric. Collectors and metric providers produce frozen
typed observations; the gate engine compares those values with contract-owned
rules and thresholds.

## Ownership

| Surface | Owner | Responsibility |
| --- | --- | --- |
| Contract | Finance extension | Revision, cutoff, frozen identities and thresholds, gate order, safety boundaries |
| Observation | Collector or metric provider | Public evidence references and a typed value, missing marker, or conflict marker |
| Gate engine | Finance extension | Typed comparison, deterministic short-circuiting, and disposition |
| Replay harness | Finance extension | Canonical hashes and byte-identical re-evaluation |
| Revision promotion | Human owner | Approval after historical, walk-forward, and shadow review |

The contract is intentionally smaller than any individual method. A de-beta,
quality, valuation, or market-regime method may choose different gate ids, but
all must use the same typed comparisons and transition rules. Boolean and
string gates support equality. Numeric gates support equality and ordered
comparisons. Providers cannot declare their own pass or fail result.

## Attribution And Industry Overlays

Layered beta attribution is deterministic arithmetic over caller-supplied,
point-in-time observations. The explained order is frozen as market, rate,
sector, narrow peer, cycle, and event. Residual is computed as total move minus
all six explained components only when every component is observed. The
`de_beta_residual` gate must use that computed value; an independent or
contradictory residual observation is rejected. Attribution does not estimate a
factor or select a source. It does execute the bound gate input and requires the
observation window to satisfy the contract cutoff before it can report a
complete result. Top-level `disposition` preserves the gate decision, including
`rejected`, while `completeness` independently reports whether the gate evidence
and all six components are complete.

An attribution is bound to one gated case identity. The gate input carries the
`case_id` and `subject_ref` of the case it evaluates, and the attribution's
`case_reference` must match both. A passing gate therefore cannot be reused to
present an attribution for a different case or a different security as
research-complete. The `observation_window` must be real ISO-8601 dates that sit
inside the contract's frozen `[point_in_time, evaluation_as_of]` window, so a
malformed or future window cannot be presented as complete.

Industry metric packs are semantic overlays on the same case contract. A pack
may require metric ids, value types, and allowed operator directions. It cannot
provide a threshold, reorder common source or cutoff gates, reinterpret missing
or conflicting evidence, or alter promotion authority. The required
`source_lineage` and `point_in_time` gates are provider-neutral
`boolean eq true` authority gates; a pack input cannot preserve their ids while
changing their type, operator, or reference semantics. Metric thresholds remain
inside the frozen case contract, and observations still pass through the common
gate engine.

## Gate States

| Result state | Meaning | Disposition |
| --- | --- | --- |
| `passed` | Evidence satisfies the frozen gate | Continue |
| `failed` | Evidence falsifies the frozen gate | `rejected` |
| `missing` | Required evidence is absent | `insufficient_evidence` |
| `conflict` | Valid evidence disagrees | `insufficient_evidence` |
| `not_run` | An earlier gate already blocked evaluation | No new conclusion |

Observations must exactly match the ordered `gates` list. An `observed` value is
typed and compared with the frozen rule to produce `passed` or `failed`.
Providers may instead report `missing` or `conflict`. The first `failed`,
`missing`, or `conflict` result blocks the case, and every later observation
must be `not_run`.

Evidence references are state-dependent: `observed` observations require at
least one reference, `conflict` observations require at least two references,
and `not_run` observations must contain none.

Running a later gate after a blocker is rejected as an invalid input rather
than silently accepted.

Passing every gate means only `eligible_for_research_successor`. It never means
method promotion, investment advice, or permission to trade.

## Replay

### Source query coverage (extension 0.5.0)

A gate can opt into receipt-derived source coverage with a `source_coverage`
requirement. It must use `boolean eq true`. The requirement freezes
`request_identity` (`market`, `universe_id`, `query_id`) and
`collection_started_at`; its universe must match the case contract. `query_id`
identifies immutable query parameters in the adapter's evidence catalog; a
reused label cannot prove that those parameters actually stayed unchanged.

The matching observation contains **only** `gate_id` and `source_pages`.
Callers cannot also supply a value, state, reason, or computed coverage result.
The engine derives the observation and includes a `finance_source_coverage_v1`
report in that gate's result. A coverage gate after an earlier blocker instead
uses the existing `not_run` observation. Unbound gates reject receipt fields.
The complete runnable input is
[`finance-source-coverage-v1.json`](examples/finance-source-coverage-v1.json).

Each normalized page contains its request identity, positive `page_number`,
`source_status`, `started_at`, `observed_at`, `rows`, `total_count`, `has_more`,
`snapshot_id`, and `snapshot_evidence_ref`. Row metadata consists of `id`,
`market`, `content_sha256`, and optional `publication_at`. Bodies and unmodeled
fields are rejected. Receipts require timezone-aware timestamps ordered inside
the frozen collection window. A date-only case evaluation cutoff is midnight
UTC; use a timestamp for intraday or end-of-day collection. The collection start
must lie inside the case window. Collection time does not prove availability at
the case's earlier historical `point_in_time`.

| Coverage state | Evidence condition | Gate / case result |
| --- | --- | --- |
| `complete` | Contiguous pages from 1, a unique terminal page, one reported total matching unique IDs, consistent versions, common nonempty snapshot identity and evidence reference | `passed`; later gates still determine the case |
| `partial` | Missing pages, unknown total/end/snapshot assertion, or no observations | `missing` / `insufficient_evidence` |
| `unstable` | Cross-page overlap, changed repeated page, conflicting record versions, totals, terminal pages, or snapshots | `missing` / `insufficient_evidence`, with explicit instability reasons |
| `source_error` | Source failure, missing rows, mismatched query/market, or invalid row metadata | `missing` / `insufficient_evidence`, with explicit error reasons |

Source errors take precedence over instability, then gaps; all reason lists
remain visible. Instability is not an economic hypothesis failure. The engine
uses `missing` rather than inventing multiple independent evidence references
to satisfy the existing `conflict` observation contract. A complete query may
contain zero records; an empty error response or absent receipt never proves
that. Identical retries preserve unique IDs; changed versions remain recorded.

Completeness is **conditional on adapter assertions**. The engine does not fetch
or authenticate snapshot evidence, establish source truth, prove historical
publication time, discover new economic events, or validate an investment edge.
`snapshot_evidence_state=adapter_asserted` makes that limit explicit. The
coverage digest identifies computed evidence, not independent corroboration.
Only normalized, authorized public metadata should enter this public reducer.

Inputs are bounded to 128 receipts, 500 rows per receipt and 5,000 rows including
retries. Exceeding a bound is an explicit error; no truncation is called complete.
Page-number validation does not allocate memory proportional to a reported last
page. Freeze a narrower query or use a domain adapter with its own explicit
partition coverage contract for larger collections.

No new collector, scheduler, builtin capability, or CLI command is added.
Existing `evaluate`, `replay`, and the managed extension entrypoint consume the
same input. Replay binds the requirement, receipt metadata and computed report.

### Source-period metric projection / 来源期次指标投影 (extension 0.6.0)

`finance_research_dashboard_input_v0` may add `source_period_metrics`. This is
an optional Finance-owned read model, not a new Core capability. Each row binds
one metric to a calendar period, an explicit `metric_basis`
(`realized_cash`, `period_estimate`, or `annualized_estimate`), value origin and
precision, numerator/denominator scope, expected and observed components,
double-count exclusions, an upstream lineage id, methodology state, anomaly
state, and a public-safe evidence reference. Each row also carries a composite
event identity: namespace, source event id, event timestamp, instrument and an
anonymous scope id. A source event id or hour is never sufficient on its own.

The validator derives rather than trusts `coverage_state`, missing components,
lineage deduplication, gap reasons, and readiness. A missing value remains JSON
`null`; it is never rewritten to zero. A non-null value with an omitted child
is `partial`. Source errors cannot carry a value or observed components.
Components marked double-counted must be observed and cannot remain in the
numerator scope. Rows share one upstream evidence chain only when lineage,
composite event identity, period, metric semantics and unit all match. The
primary first prefers a complete, verified and anomaly-clear row, then explicit
observation authority and stable metric id; an available fill-derived VWAP
outranks a rounded position entry instead of relying on row order or a
lexicographic naming accident, while an unavailable fill does not suppress a
usable fallback. Conflicting exact values within
one chain receive a typed `lineage_value_conflict` hold; a rounded fallback may
differ without overriding or falsely invalidating the exact primary.

Metric semantics keep signed bases separate: account cash change, cumulative
funding cost and fill fee are distinct conventions. A fill fee declares the
builder fee included, preventing a second addition. Account-value rows also
declare scope and role. Only a unified-account row that already includes
isolated margin may be the authoritative NAV total; product/venue rows are
composition or reconciliation evidence, a venue withdrawable amount is
venue-liquidity evidence only, and external-asset coverage is not account NAV.
Units remain exact, so USD and USDC are never silently collapsed.

The optional `spot_market_identity` section keeps source pairs, token metadata
and contexts in the same validated view. It joins a context only by exact
`context.coin == pair.name`, then resolves both assets through explicit token
indexes. Pair, context and token identities must be unique and complete;
positional zip, unmatched/perpetual context rows and missing token indexes fail
closed. `is_canonical=false` is projected only as a noncanonical naming fact;
it does not infer fraud, missing backing or investment risk.

Methodology that is only declared, unverified, or conflicting remains visible
as a typed hold. Rounded values and unresolved anomalies remain visible too;
the reducer does not silently correct or promote them. Every row has
`ready_eligible=false` and
`admission_reason=source_period_metric_is_evidence_only`. A complete row can
support later research, but it cannot create a ready candidate, investment
recommendation, order, signature, transfer, or performance claim.

`finance_research_dashboard_input_v0` 可选携带
`source_period_metrics`。这是 Finance 所有的只读视图，不是新的 Core
能力。每行把一个指标绑定到明确日历周期，并机器化记录：收益口径
（实际现金、周期估算或年化估算）、数值来源与精度、分子/分母范围、
预期与已观察子项、重复计数排除、上游 lineage、方法学状态、异常状态和
公开安全证据引用。

完整性、缺失子项、lineage 去重、hold 原因和 ready 资格都由校验器计算，
不接受调用方自报。缺失值保持 `null`，绝不改写成 0；子项未齐即使已有
数值也只能是 `partial`；source error 不得携带数值或已观察子项；标记为
double-counted 的子项必须从分子范围排除。同一周期、同一 lineage 的多层
封装只有在事件 namespace、source event id、事件时间、instrument、匿名
scope、指标语义和单位也一致时才算一条上游证据链；不能只用 event hash
或小时去重。主记录按显式 observation authority 选择，fill-derived VWAP
高于 rounded position entry，不再依赖数组顺序或名称字典序。现金变动、
累计 funding cost 与 fill fee 使用不同 signed basis；fill fee 明确已含
builder fee。只有已包含 isolated margin 的 unified-account 总值可作为 NAV
owner；产品/场所小计只作 composition/reconciliation，单场所 withdrawable
只说明该场所即时流动性，外部资产覆盖也不是账户 NAV。USD 与 USDC 保持
不同单位。方法学未互证、页面口径冲突、rounded 数值和
未解释异常都会保留为 typed hold，不做静默修正。所有行固定
`ready_eligible=false`，只能服务后续研究，不能自动升级 ready，也不授予
投资建议、下单、签名、转账或绩效声明权限。

可选 `spot_market_identity` 在同一 view 中保存 pair、token metadata 与
context。context 只能按 `context.coin == pair.name` 精确连接，两侧资产再按
显式 token index 解析；缺失、重复、未匹配/perp context 与 positional zip
均 fail closed。`is_canonical=false` 只表示命名不是 canonical，不能推导为
欺诈、无 backing 或投资风险。

CLI/managed extension 产生规范 view；Dashboard 解析并显示同一 view；
`render-lark-card` 只从该规范 view 生成 Lark 卡片，不读取 provider 原始
payload、不发送消息，也不维护第二套口径。

The replay receipt binds three SHA-256 values:

- the normalized contract;
- the complete input, including evidence references;
- the evaluation before the receipt is attached.

Canonicalization uses compact ASCII JSON with sorted keys. Replay recomputes
the evaluation and requires matching hashes plus byte-identical canonical
output. Changing a threshold, cutoff, observation, reason, or result fails
closed.

## Compatibility

### Contract exit liquidity / 合约退出流动性 (extension 0.8.0)

`finance_contract_liquidity_input_v0` is an additive Finance-owned contract.
It does not alter `finance_case_contract_v1`. The input freezes an instrument
reference, contract kind, quote unit, observation/evaluation timestamps,
freshness limit, cost and book-coverage limits, plus one or more amount- and
direction-specific exit scenarios. Scenarios are unique by position direction
and requested notional. The engine derives book coverage and total exit cost;
it does not accept provider-declared totals, readiness or disposition.

Venue-specific instrument discovery, tick/lot precision and book collection
remain adapter responsibilities. The public Finance contract rejects venue,
account, funding and investment-value fields. Its output records those
boundaries, labels venue semantics as `adapter_asserted`, and fixes both
`trading_allowed` and `automatic_ready_allowed` to false. Stale evidence cannot
become a negative liquidity conclusion.

`finance_contract_liquidity_input_v0` 是新增的 Finance 合约，不修改
`finance_case_contract_v1`。输入冻结合约引用、类型、报价单位、观察/评估时点、
新鲜度、成本和订单簿覆盖阈值，以及按持仓方向和退出金额区分的场景。场景按方向
与金额唯一；覆盖率和总退出成本由引擎计算，不接受 provider 自报总数、ready 或
裁决。场所侧合约发现、tick/lot 精度和订单簿采集仍归私有适配器。公共 Finance
合约拒绝场所、账户、资金费和投资价值字段，并固定不授予交易或自动 ready 权限；
过期证据不能伪装成负面的流动性结论。

The existing `finance_value_discovery_input_v0` reducer and
`finance_value_discovery_extension_v0` provider protocol remain supported.
`finance_case_gate_input_v1` now requires a `subject_ref` naming the case
subject, so a gate input packet must declare the subject it evaluates; this
binds the case identity end to end. The `finance_value_discovery_input_v0`
packets are not reclassified and gain no new fields.

The 0.5.0 coverage gate is opt-in. Inputs without `source_coverage` retain their
existing normalized contract, result fields and replay bytes. Older extension
versions reject the new fields; install 0.5.0 before invoking this form. To stop
using it, choose a separately frozen contract revision without that gate, rather
than removing evidence from an already evaluated contract. Prior evaluations
must be replayed with their original contract and implementation version. To
disable the entire optional workflow, use `loopx extension disable
loopx-finance-value-discovery --execute`; this does not authorize any financial
operation or remove evidence already stored by its owner.

The 0.6.0 `source_period_metrics` view is also opt-in. Inputs that omit it keep
their prior view shape. To roll back this surface, omit the field and republish
the previous projection, or install/enable the prior extension revision through
the existing extension lifecycle. Rendering a Lark card is read-only and does
not change the Goal Channel or send an external message.
