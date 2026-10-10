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

### Source period assessment (extension 0.8.3)

The additive Python APIs `assess_period_encoding(context)` and
`assess_period_comparison(input)` share their result with the direct CLI:

```sh
loopx-finance-value-discovery assess-period --input-json period.json
```

The input is `finance_period_comparison_input_v1` with exactly `left`, `right`
and `period_intent` (`same_period` or `cross_period`) in addition to its version.
Each operand has `source_digest`, `context` and `economic_period`. Null context
means the original context is missing; a source API's projected end or a stored
observation's dates cannot manufacture one. A finite context contains exactly
`startDate`/`endDate` or `instant`, retaining the original literal strings.

The Finance-owned encoding API follows fixed [XBRL 2.1 §4.7.2, Recommendation
with 2013 errata](https://www.xbrl.org/Specification/XBRL-2.1/REC-2003-12-31/XBRL-2.1-REC-2003-12-31%2Bcorrected-errata-2013-02-20.html#_4.7.2):
date-only starts mean the same midnight; date-only ends/instants mean the next
midnight; explicit times receive no extra day. It retains an absent timezone,
reports local-only or unproven duration ordering separately from absolute
boundaries, and never rewrites source labels. The bounded lexical subset uses
AD years 0001–9999, at most six fractional second digits, and explicit offsets
up to ±14:00. Unsupported encodings, forever, incomplete contexts and calendar
overflow fail closed. This is not complete XML Schema, taxonomy or DTS validation.

An economic declaration has exactly `context_source_digest`, `evidence_ref`,
`role`, `start`, `end` and `start_basis`, with optional `event_instant` and
`event_mapping_evidence_ref`. Boundaries are explicit dateTimes or null; an
unstated timezone stays unknown. `role` is an exact, bounded source-parent
token, not a global alias or inferred role. `start_basis` is `calendar` or
`event`; an event start requires an explicit instant, timezone and evidence
reference for its relationship to the duration. Calendar starts cannot silently
consume event metadata. The declaration's context digest must match the
operand's source digest, and its absolute boundaries must match the encoded
duration. Missing evidence, unknown ordering, distinct roles, wrong bindings or
missing/mismatched event mappings make this assessment ineligible. Equal dates
or known numeric accuracy do not supply those declarations.

The result `finance_period_comparison_assessment_v1` preserves both encodings,
parent declarations and reason codes. `period_evidence_eligible` applies only
to this declared period axis: same-period intent requires equal absolute
boundaries and exact roles; cross-period intent requires distinct boundaries
and exact roles. It does not assert non-overlap, comparable metric semantics,
source truth, source authenticity, lifecycle, PIT or financial admission.
Economic admission currently covers finite durations; an encoded instant is
still interpreted but cannot be promoted to a return duration. Source context
identity/QName, original bytes, extraction and economic evidence remain with
the source/parent caller. That caller must independently admit those inputs,
match its retained current payload/handoff pin, and enforce subject/unit/basis,
purpose, withdrawal/conflict and cutoff rules before consuming the period axis.
Digest equality or a reference alone authenticates nothing: the output fixes
`source_evidence_authenticated`, `source_lifecycle_assessed`,
`financial_admission` and `trading_allowed` to false.

A processed ineligible assessment exits 0; inspect eligibility and reasons.
Malformed input exits 1 with an error packet. Existing reducers/replays and
numeric accuracy keep their original bytes and behavior. Extension 0.8.5 routes
the same input version through the no-argument stdin provider entrypoint used
by `loopx extension run`. Direct and managed assessment use the same reducer;
ineligible results remain processed results rather than transport failures.
Lusen immutable input version/pin integration, App/Lark and financial utility
remain separate stages.
No catalog entry, Core authority, default installation or source call is added.
Consumers must qualify the exact new API/schema/version pair; restore their
original inputs, pins and binary pair to opt out or roll back, rather than
deleting declarations from frozen evidence. Binaries before 0.8.3 do not supply
this API.

The 0.8.3 API/direct CLI already supplies this assessment, but its managed
entrypoint does not route period inputs. Keep the original binary, manifest
and input pins together when reverting to that version; a successful doctor
alone does not qualify managed period dispatch.

中文：新增期间 API/CLI 分开编码边界与父审经济期间声明，原字面值和时区缺证
保持。事件到收益 duration 的映射必须显式提供，日期相等或精度已知不能填补。
`period_evidence_eligible` 只说明这个声明维度通过检查，不认证来源、PIT、生命周期
或金融/交易准入。当前经济检查限定有限 duration，instant 不自动变成 duration。
源 context/bytes/父审、原 payload/pin 与撤回/时点门禁沿既有调用者 owner；Lusen
成对协议、App/Lark、发布和效用分别验收，旧回放不随升级改写。

### Disclosure identity and presentation scope (extension 0.8.6)

`finance_period_comparison_input_v2` extends the existing `assess-period`
operation. Its exact fields are `schema_version`, `left`, `right`,
`period_intent` and `basis_bridge`. Each operand retains the v1 period fields
and adds `statement_basis`: null, or exactly `subject_ref`, `metric_ref`,
`filing_ref`, `version_ref`, `scope_ref`, `evidence_ref`, `unit` and `value`.
References are bounded nonempty strings or explicit nulls. Unit and bounded
signed numeric strings follow the cash reconciliation rules; null prevents
comparison eligibility. Source digest belongs to the original period operand;
event metadata cannot substitute for source content.

With `basis_bridge: null`, declared subjects, metrics, units, sources,
filings, versions and scopes must match. Matching periods or labels alone do
not join disclosure bases. Different bases require a parent-reviewed bridge
with exactly `evidence_ref`, `target_scope_ref`, `target_component_refs`,
`left` and `right`. Each side contains its operand's exact `source_digest`,
`filing_ref`, `version_ref` and `scope_ref`, a `coverage_evidence_ref`, and
`components`. Components have exactly `component_ref` and signed `value`;
lists contain 1..32 unique references. All selected targets must exist on
both sides. Target meanings and complete partition coverage remain parent
declarations; a sum cannot authenticate them.

Each side's full signed component sum must equal its original reported
value. Nonzero residuals, missing values/coverage or changed pins hold the
relation. The selected subset projects both declarations to the explicit
target scope without replacing the original aggregate. Selected amounts need
not equal: different periods or revisions may have different values. No unit
conversion, residual balancing, precision recovery or clock inference is
performed. Bounded Decimal arithmetic reports exact lexical sums; precision
assurance remains unknown.

The result `finance_period_comparison_assessment_v2` retains the v1-owned
`period_evidence_eligible` economic-period axis. Additional
`statement_basis_assessment` reports `match`, `bridged` or `unproven`,
original bases, decomposition residuals and reason codes. Combined
`comparison_evidence_eligible` requires both axes. It does not authenticate
sources, assess lifecycle/PIT, admit financial evidence or authorize trading.
Original v1 input/output and persisted clocks remain unchanged. Malformed
closed shapes fail; missing declarations remain explicit holds.

This vocabulary is local to the existing Finance extension. No Core authority,
catalog entry, provider installation or account effect is added. Real source
consumers own extraction, revision chains, context/accuracy and first-public
evidence. App/Lark presentation and independent provider adoption remain
companion acceptance stages.

### Producer-declared numeric accuracy (extension 0.8.2)

The Python API `assess_numeric_accuracy(value, declaration)` is an additive,
Finance-owned interpretation of ordinary numeric `decimals`/`precision`
attributes. It returns `finance_numeric_accuracy_assessment_v1`. Source
adapters retain the original QName, context, unit, scale, label roles, source
version and clocks; they pass the already decoded numeric string and only the
accuracy attributes to this function. Raw value/attribute strings are retained.

Missing declarations and `precision="0"` remain unknown. Positive precision
infers decimals without floating-point arithmetic; zero with positive precision
infers `INF`. Both accuracy attributes, malformed attributes and non-finite
values fail closed. Displayed decimal places never supply missing accuracy.
`INF` means producer-declared exactness for that lexical fact, not independently
verified financial truth. These rules follow [XBRL 2.1 §4.6.3–6](https://www.xbrl.org/Specification/XBRL-2.1/REC-2003-12-31/XBRL-2.1-REC-2003-12-31%2Bcorrected-errata-2013-02-20.html).
Nil and fraction facts are outside this ordinary numeric API.

The assessment does not invent a rounding interval or certify an economic
ratio, calculation-linkbase consistency, global concept aliases, first
availability or PIT eligibility. Calculation consistency requires its own
relationship, context/unit and coverage evidence. Source labels cannot replace
fact contexts. `independent_truth_verified`, `arithmetic_identity_verified` and
`trading_allowed` remain false.

```python
from loopx_finance_value_discovery import assess_numeric_accuracy

assessment = assess_numeric_accuracy("14.5000", {"decimals": "INF"})
assert assessment["state"] == "producer_declared_exact"
assert not assessment["independent_truth_verified"]
```

This helper does not change prior evaluation/replay bytes or install another
provider. Consumers opt in through their owning input version and must report
missing Finance support on that path rather than silently drop the declaration.
Existing consumers that do not call it retain their previous behavior. Rollback
uses the original consumer/input and Finance version, without rewriting stored
evidence. The paired source consumer and actual wheel qualification remain
separate delivery steps; no new CLI operation, App view or Lark renderer is
claimed by this Python prerequisite.

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
# Signed cash reconciliation

`finance_cash_reconciliation_input_v1` has exactly `schema_version`,
`subject_ref`, `column_ref`, `unit`, `period`, and `facts`. The Finance extension
owns this local domain contract and its metric/state vocabularies. It reuses
the existing numeric-accuracy, period and canonical-input-digest owners; it
introduces no capability catalog entry or control-plane authority.

`facts` contains exactly one row for each metric:

| Metric | Signed amount convention |
| --- | --- |
| `operating_cash_flow` | Reported signed operating cash flow |
| `gross_capex` | Nonpositive cash spent on property, plant and equipment |
| `asset_sale_proceeds` | Nonnegative cash proceeds |
| `government_incentive_proceeds` | Nonnegative cash proceeds |
| `net_capex` | Reported signed sum of gross capex and those two proceeds |
| `adjusted_free_cash_flow` | Reported operating cash flow plus net capex |

Each row has exactly `metric`, `state`, `measurement_kind`, `value`, `unit`,
`source_refs`, and `accuracy`. `state` is `observed`, `missing` or `conflict`;
missing/conflict retain null value and accuracy rather than becoming zero.
An observed row needs one source reference; a conflict needs two. A reference
has `digest` (exact `sha256:`), `locator`, `column_ref`, and original `label`.
Every reference must bind to the root source digest and source column. This
checks declarations, not the retained document's actual labels or table layout.

`measurement_kind` is `reported_cash_flow`, `stock`, `commitment`, `forecast`,
or `rounded_display`. Only reported cash flow can enter this calculation.
The producer must verify this classification; a renamed stock/display value
cannot be detected from its numeric string alone. Extra gate booleans or final
financial-admission fields are rejected rather than used as operands.

`unit` is null (unknown) or `{ "currency": "USD", "scale": 6 }`: currency is a
bounded producer literal, not inferred ISO identity, and scale is the decimal
power 0, 3, 6 or 9. All row units must match the root exactly. Amounts are finite
numeric strings, at most 128 characters, with decimal exponent and adjusted
exponent within ±64. Calculation uses bounded Decimal arithmetic, not binary
float. `accuracy` is the existing `decimals`/`precision` declaration or null;
unknown measurement accuracy remains unknown even when the lexical sum is exact.

`period` is one existing period operand: `source_digest`, `context` and
`economic_period`. The existing period owner assesses this same declared axis
on both sides. It does not independently compare six extracted contexts.
Arithmetic can reconcile a labelled column whose finite context is unknown;
the nested `period_evidence_eligible` stays false. A consumer must require its
own period/PIT/lifecycle admission before financial use.

The `finance_cash_reconciliation_assessment_v1` result distinguishes
`consistent` (zero lexical residuals), `conflict` (nonzero residual),
`incomplete` (missing/conflicting facts) and `ineligible` (sign/unit/reference/
measurement-kind failures). The latter two omit calculation. Result facts
retain lexical values, labels, references and numeric-accuracy assessments.
Signed net capex remains signed in the one/two-decimal billion display; display
is not a new observation. Repeat assessment of a frozen input returns the same
result and canonical input digest. This is calculation replay, not current
source-lifecycle validation.

Source authenticity, lifecycle, distributable cash, financial admission and
trading remain false. No collection, account access or effects are performed.
The direct `assess-cash --input-json` command and schema-selected stdin runtime
call the same assessment. Malformed input fails with the existing error packet;
well-formed incomplete/ineligible/conflicting results are successful assessments
with `ok: true`, not successful financial admission.
