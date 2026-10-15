# Finance Value Discovery

Status: co-located optional LoopX extension sample.

The separate read-only position operation accepts private normalized account
facts; its receipts must stay private and cannot be published to the research
dashboard. See [Position guard and portable installation](POSITION_GUARD.md).
独立的只读持仓操作接受私有账户归一化事实；回执必须保密，不能发布到研究
Dashboard。见[持仓守护与可重建安装](POSITION_GUARD.md)。

## Placement

- extension id: `loopx-finance-value-discovery`
- capability registration: none
- placement: `packages/loopx-finance-value-discovery/`

This optional workflow owns its command and packet contract. LoopX does not
need a provider-neutral finance capability, so installing the extension must
not add `finance-value-discovery` to the capability catalog. The package owns
its dependencies, installation, doctor, enablement, and upgrade lifecycle.

## Contract

The reducer accepts a frozen `finance_value_discovery_input_v0` object and
emits `finance_value_discovery_packet_v0`. It performs no network request. A
separate collector may prepare public evidence cards, but connector output is
input evidence, not accepted truth.

The additive [`finance_case_contract_v1`](CONTRACT.md) surface separates a
method's frozen contract from provider observations. Its deterministic gate
engine stops at the first failed, missing, or conflicting gate. The replay
harness binds the contract, input, and result with canonical SHA-256 receipts.
Passing every gate means only that a case is eligible for a bounded research
successor; it cannot promote a revision, advise, or trade.

Two P1 overlays reuse that contract without weakening it:

- `finance_beta_attribution_input_v1` decomposes a frozen total move into
  market, rate, sector, narrow-peer, cycle, event, and computed residual
  layers. If any explained component is missing or conflicting, the residual
  is not computed.
- `finance_metric_pack_input_v1` selects a bundled industry metric vocabulary.
  Packs define required metric ids, value types, and allowed comparison
  directions. They do not contain thresholds or evaluate provider-declared
  pass/fail states.

Extension 0.8.0 adds `finance_contract_liquidity_input_v0`, a provider-neutral
exit-liquidity admission for derivatives. Venue adapters remain responsible for
contract identity, precision, book collection and evidence timestamps. Finance
evaluates each frozen position direction and requested notional against one
freshness window, minimum executable-book coverage and maximum derived exit
cost. Exit cost is the deterministic sum of spread, price impact and fees; a
provider cannot submit its own total or pass/fail decision. Long positions must
use a sell exit and short positions a buy exit.

The result is deliberately independent of investment value and funding. Those
fields, venue names and account material are rejected rather than folded into
the liquidity decision. A fresh passing result means only
`eligible_for_research_successor`; it never creates a ready candidate or grants
order, signing or transfer authority. Stale measurements are
`insufficient_evidence`, while fresh amount/cost failures are
`insufficient_liquidity`.

0.8.0 新增 `finance_contract_liquidity_input_v0`，用于对衍生品退出流动性做
provider-neutral 准入。私有场所适配器继续负责合约身份、精度、订单簿采集和
证据时间；Finance 只按持仓方向与申请退出金额，使用冻结的新鲜度、可执行订单簿
覆盖率和最大退出成本阈值进行确定性判断。退出成本由点差、价格冲击和手续费相加
得到，不接受 provider 自报总成本或通过/失败；多头必须卖出退出，空头必须买入
退出。

该判断与投资价值和资金费明确隔离，相关字段、场所名称和账户材料会被拒绝，而
不是混入流动性结论。通过只表示可以进入下一段研究，不能升级 ready，更不授权
下单、签名或转账；过期测量是证据不足，新鲜但金额或成本不满足才是流动性不足。

Run the same contract through the direct CLI or managed extension runtime:

```bash
loopx-finance-value-discovery evaluate-contract-liquidity \
  --input-json packages/loopx-finance-value-discovery/examples/contract-liquidity-v0.json

loopx extension run loopx-finance-value-discovery \
  --input-json packages/loopx-finance-value-discovery/examples/contract-liquidity-v0.json \
  --execute --format json
```

This slice changes the CLI/managed-Turn contract only. Dashboard and Lark must
consume the same evaluation in their separately owned projection slice; until
that lands, this backend delivery is intentionally partial rather than an
end-to-end visual release.

The packet enforces:

- a cross-sectional screen before a named candidate is selected;
- at least three unrelated screen groups for the de-beta route;
- frozen controls and at least two same-group controls before an
  idiosyncratic de-beta claim can advance;
- supporting facts and counterevidence on every card;
- point-in-time source cutoffs, terminal-risk, dilution, and fully diluted
  valuation gates;
- at most one bounded successor, with no threshold relaxation or continuous
  watch.

It rejects raw provider bodies, private paths, credentials, account or
portfolio material, future-dated evidence, unsupported fields, and malformed
public URLs. The discovery reducer never emits investment advice, a price
target, a trade, or an automatic watch.

## Simulation-only transaction confirmation / 仅模拟交易确认

Extension 0.7.0 adds the
`finance_transaction_approval_input_v0` consumer, deliberately separate from
the discovery reducer. It turns an explicit, already-bounded candidate action
into Core's canonical `loopx_operation_request_v0`. The request freezes the
order action, public evidence links, evidence observation time, expected
economics, expiry, and no-trade conditions into the same projection shown by
the Dashboard and the Goal Channel Lark card. It always targets
`account:simulation` and the `finance.operation.simulate` executor permission.
It cannot place an order, sign, transfer, infer an approver, or create standing
trading authority.

0.7.0 新增的 `finance_transaction_approval_input_v0` consumer 与发现 reducer
明确隔离。它只把一个已经收敛、显式提出的候选动作转换成 Core 的
`loopx_operation_request_v0`，并把下单动作、公开证据链接、证据观察时间、
预期经济性、过期时间和禁交易条件冻结进 Dashboard 与 Goal Channel 飞书卡片
共用的投影。目标固定为 `account:simulation`，权限固定为
`finance.operation.simulate`；它不能真实下单、签名、转账、推断审批人或产生
持续交易权限。

The executor revision must come from the enabled `loopx-finance-execution`
extension readback. Authorized principals must be explicit provider-qualified
identities from the current Goal Channel operator authority; the Finance builder
neither discovers nor broadens them. Build the packet once, retain both its
idempotency key and canonical request, then let Core persist and deliver it
through the existing two-step entrypoint:

```bash
loopx-finance-value-discovery build-operation-request \
  --input-json transaction-approval.json > approval-packet.json
jq '.operation_request' approval-packet.json > operation-request.json
loopx goal-channel prepare-operation \
  --goal-id <goal> --agent-id <agent> \
  --summary "Review one simulated finance transaction" \
  --idempotency-key "$(jq -r '.idempotency_key' approval-packet.json)" \
  --request-json operation-request.json --execute --format json
loopx goal-channel deliver-operation \
  --goal-id <goal> --proposal-id <operation-id> --execute --format json
```

`prepare-operation` only writes Core's canonical proposal; `deliver-operation`
projects that same proposal to the bound Lark group. The Dashboard reads the
same safe projection. Confirm/reject callbacks remain Core-owned and produce an
idempotent receipt. Managed Turn callers may submit the input schema to the
extension runtime and select `operation_request` from its returned packet.

`executor_revision` 必须来自已启用 `loopx-finance-execution` 的真实 readback；
审批人必须来自当前 Goal Channel 操作权限，并以 provider-qualified principal
显式传入，Finance builder 不发现或扩大审批人范围。两者都不会被猜测。
`prepare-operation` 只写入 Core 的 canonical proposal，`deliver-operation` 再将
同一 proposal 投影到 Goal 绑定群；Dashboard 也读取同一安全投影。确认/拒绝
callback 与幂等 receipt 继续由 Core 负责。

## Public-Safe Research Surface

The extension also declares a public-safe `investment-research` presentation
surface. A `finance_research_dashboard_input_v0` packet validates Finance
semantics and maps them to the finance-owned
`decision_research_dashboard_v0` view. The contract keeps beta, cycle,
company-value, and residual-alpha reasoning distinct; requires supporting
evidence, counterevidence, thesis breakers, scenario assumptions, and frozen
event gates; and can publish compact research-artifact pointers with the
evidence references used to produce them. Artifact pointers contain no raw
content or local paths. The view preserves insufficient or rejected conclusions
without allowing free labels or tones to override canonical adjudication, zero
validated alpha, or the unchanged active method, or turn them into an investment
recommendation.

[`examples/research-dashboard.json`](examples/research-dashboard.json) is a
fully synthetic public-safe example. It deliberately reports zero validated
company alpha and an unchanged active method.

Extension 0.6.0 adds the optional `source_period_metrics` section to that same
view. It records calendar-period completeness, realized-versus-estimated basis,
value origin and precision, numerator/denominator scope, component coverage,
double-count exclusions, upstream lineage, methodology verification, and
anomaly state. The validator recomputes coverage and lineage status. Missing is
`null`, not zero; repeated wrappers around one upstream period are not
independent evidence; and every row remains evidence-only with
`ready_eligible=false`. Deduplication uses the full event namespace/id/time,
instrument, anonymous scope, period, semantics and unit. Explicit authority
makes fill-derived VWAP outrank rounded position entry. Signed cash change,
cumulative funding cost and inclusive fill fees stay distinct; unified-account
NAV, venue composition, venue withdrawable and external-asset coverage cannot
be added or relabeled as one another.

0.6.0 版本在同一规范 view 中增加可选的 `source_period_metrics`：显式记录
日历周期完整性、实际现金/估算口径、数值来源与精度、分子分母范围、子项
覆盖、double-count 排除、上游 lineage、方法学互证和异常状态。完整性与
lineage 去重由校验器重算；缺失保持 `null` 而不是 0；同一上游周期的多层
封装不算独立证据；复合事件 identity 包含 namespace/id/time、instrument、
匿名 scope、周期、语义和单位，fill-derived VWAP 显式高于 rounded entry。
现金变化、累计 funding、已含 builder 的 fill fee，以及 unified-account NAV、
venue 组成/可取金额、外部资产覆盖均保持不同口径；每行固定
`ready_eligible=false`，不能自动升级 ready。

The same view can carry an optional spot identity join. Contexts are matched by
pair name and assets by explicit token indexes; input order is irrelevant and
missing, duplicate or unmatched identities fail closed. Noncanonical naming is
shown without inferring fraud or backing. 中文：spot context 按 pair name、
资产按 token index 连接，不做 positional zip；`is_canonical=false` 不被解释
为欺诈或无 backing。

After separately installing, enabling, and doctor-validating the extension,
publish a validated local projection with:

```bash
loopx extension publish-projection \
  loopx-finance-value-discovery investment-research \
  --input-json owner-research.json \
  --execute \
  --format json
```

Publication is explicit and local. It validates and stores a read-only
projection bound to the currently active extension revision. A disabled,
doctor-stale, missing, or revision-mismatched extension projection is hidden
from status and Dashboard surfaces. Publishing does not install or enable the
extension, activate or replace a Finance method, spend LoopX quota, consume a
learning queue, create a trade, or place an order.

Prepare a card from a research input without publishing or sending it:

```bash
loopx-finance-value-discovery render-lark-card \
  --input-json owner-research.json
```

The command returns a card payload only. Existing Goal Channel routing and
message authority still own any external send. Dashboard and Lark both consume
the same validated Finance view; neither reads raw provider material or carries
a separate readiness rule. 中文：该命令只生成卡片、不发消息；外部发送仍需
既有 Goal Channel 授权。停用可继续使用
`loopx extension disable loopx-finance-value-discovery --execute`；若只回退
期次指标，删除可选字段并重新发布旧 view 即可。

For an already published result, use the extension state file selected for
that runtime (by default `~/.loopx/extensions/state.json`, or
`<runtime-root>/extensions/state.json` with a custom runtime root) and the exact
revision/hash reference from the Goal's result index:

```bash
loopx-finance-value-discovery render-lark-card \
  --published-state-file extensions-state.json \
  --goal-id example-goal \
  --extension-revision EXACT_REVISION \
  --payload-sha256 EXACT_PAYLOAD_SHA256
```

This reads through Core's existing projection reader, verifies the requested
Goal, and includes the original source/version, publication and review dates,
supersedes references, revision and payload digest. It does not rebuild the
research, renew its review deadline or fall back to an input on failure. Wrong
hashes, old revisions, another Goal, missing publication and disabled extensions
fail before a card is returned. A missing review date stays `unknown`.
`--surface-id` can select another declared Finance surface; the default is
`investment-research`. The input and publication-reference modes are exclusive.

中文：已发布成果使用原生状态文件与精确版本/摘要引用，卡片保留原 Goal、来源版本、
复核期限和摘要；错误、停用或缺失时拒绝，不重建旧稿或延长时效。生成卡片仍不代表
外部送达、读者采用或交易授权。旧 `--input-json` 入口继续兼容；停用仍用既有
extension disable，回退用既有 `loopx extension rollback` 选择已保留版本。

The Goal's **Files & Outputs** tab loads the published research only when
opened. Chat and `serve-status` expose the same extension index and exact
revision/hash readback. Only a public-safe view belonging to that Goal is
rendered; refresh clears previous content when the read fails or the extension
is disabled. Research availability does not certify an economic conclusion.

`render-lark-card` includes the canonical adjudication, original evidence
clocks, exact metric strings, counterevidence, event review conditions and
artifact references, even when no source-period metrics exist. It rejects a
research card exceeding 18,000 UTF-8 bytes rather than silently cutting its
review basis. The existing source-period-only Python renderer remains available.
For an exact published receipt, use
`build_published_decision_research_lark_card` or the publication-reference CLI
above. `build_decision_research_lark_card(view)` remains a view-only renderer;
rebuilding an input is not publication readback, and preparing a card is not
sending or adopting a method. The same no-truncation limit includes the
publication reference as well as the research text.

中文：Goal 的“成果”页按需读取已发布的研究，绑定当前 Goal、扩展版本和内容摘要；
读取失败或停用时清除旧内容。通知卡保留研究结论、反证、未知、原始时点和下一步，
没有期次指标也可显示；超过容量时拒绝裁切。卡片准备、外部送达和方法采用分别验收。

Public synthetic examples: [desktop results](../../docs/assets/personal-workspace/research-results-desktop.png)
and [mobile results](../../docs/assets/personal-workspace/research-results-mobile.png).
The expired review warning is intentional; these screens do not represent a
live financial account or an externally delivered notification.

## Worked Method: How PayPal Surfaced

The historical PayPal exercise started with a fresh de-beta scout, not a
PayPal thesis. The bounded universe covered five unrelated groups: legacy
payments, packaging, agriculture cyclicals, staffing, and freight. Public
filing facts and adjusted price history were used for a first-pass comparison
of growth, margins, cash conversion, balance-sheet resilience, drawdown, and
residual performance.

PayPal surfaced because operating and cash-flow quality remained meaningfully
better than its price-history position suggested. FIS, GPN, and WEX stayed in
the packet as controls. That mattered: the controls separated a possible
PayPal-specific residual from a broad legacy-payments de-rating and kept GPN's
value-trap risk visible instead of averaging the whole group into one bullish
story.

The screen did not produce an investment conclusion. It produced one bounded
successor: review branded checkout and transaction-margin durability, free
cash-flow quality, credit exposure, debt and liquidity, dilution and buybacks,
concentration, competition, regulation, and valuation history. The reusable
lesson is the sequence:

```text
broad blind screen
  -> named candidate
  -> frozen peer controls
  -> idiosyncratic-versus-group-wide test
  -> filing falsification
  -> one successor or close
```

[`examples/paypal-debeta-discovery.json`](examples/paypal-debeta-discovery.json)
encodes that method as an illustrative historical packet. It is not a current
view on PayPal or any control company.

## Install And Run

### Cumulative flow difference (candidate 0.8.11)

To derive a tail flow, provide a cumulative total and its prefix. The new
`assess-period-difference` operation requires the same subject, metric, unit and
economic role, source-bound finite duration contexts with a common absolute
start, an earlier prefix end, compatible filing versions, and producer-declared
exact accuracy on both amounts. The synthetic example derives 95 − 60 = 35
over July through September. It checks declarations, never source truth or PIT.

```bash
loopx-finance-value-discovery assess-period-difference \
  --input-json packages/loopx-finance-value-discovery/examples/flow-difference-v1.json
loopx extension run loopx-finance-value-discovery \
  --input-json packages/loopx-finance-value-discovery/examples/flow-difference-v1.json \
  --execute --format json
```

Both direct and schema-selected stdin use the same
[Finance contract](CONTRACT.md#cumulative-flow-difference-extension-0811).
Missing period/version/accuracy evidence keeps `derived_value` and
`derived_period` null; a conditional `candidate_lexical_difference` remains a
diagnostic. Missing amounts never become zero. Stocks, averages and ratios are
refused even if their naive difference happens to match a desired result.
Finite accuracy propagation, duration-weighted averages and EPS semantics are
outside this v1. App/Lark result interaction and source-provider adoption remain
companion work; this CLI/provider prerequisite is partial product delivery.

Use the install/enable and disable commands below for explicit opt-in and
restoration. To roll back, reinstall the prior pinned wheel and upgrade the
runtime registration to its matching manifest. 0.8.10 rejects this new schema;
existing period v1/v2, cash reconciliation and reducer inputs keep their routes.
No default registration, account access, financial permission or trade changes.

0.8.11 候选新增累计流量减前缀：只有同主体／指标／单位、共有绝对起点、
兼容来源版本与已声明精确值齐备时，才输出尾段期间值。缺证继续保留 null，
条件差额只作诊断；存量、平均量与比率不套用此算法。有限精度传播、时间加权、
EPS、来源有效性／PIT 和 App/Lark 交互仍待对应 owner 验收。

### Disclosure and presentation basis (candidate 0.8.6)

Same economic period does not establish the same filing version or row scope.
The v2 period input adds declared disclosure bases and an optional evidenced
signed decomposition. A full aggregate can project to a narrower common scope
only when each source-bound partition closes and the selected components are
present. Missing declarations, changed pins and nonzero residuals remain holds.

```bash
loopx-finance-value-discovery assess-period \
  --input-json packages/loopx-finance-value-discovery/examples/period-comparison-v2.json
loopx extension run loopx-finance-value-discovery \
  --input-json packages/loopx-finance-value-discovery/examples/period-comparison-v2.json \
  --execute --format json
```

Both paths use the same [Finance contract](CONTRACT.md#disclosure-identity-and-presentation-scope-extension-086).
The synthetic example has aggregate -340 = purchase -200 + disposal 30 + other
-170, alongside a separately declared other-only -170. It qualifies only the
declared period and presentation relation. Parent-declared partition coverage,
source authenticity, PIT, accuracy and financial permission remain separate.
v1 replay is unchanged; do not strip v2 fields to manufacture eligibility.
App/Lark projection and independent source consumer adoption remain unqualified
for this new version. No default install, Memory or trading change is implied.

Use the existing disable/enable commands below to opt out and restore. Rollback
requires the original pinned wheel and manifest; 0.8.5 supports v1 but rejects
v2, rather than silently interpreting it as an economic-only comparison.

0.8.6 候选新增披露身份与呈列范围轴：同名、同期间不自动合并。
显式父审分解必须绑定各自来源/版本/范围，完整带符号组成与原金额闭合，
目标组成两侧均存在。缺证和冲突继续 hold；算术与声明检查不代表来源真实、
首次可得时钟、金融准入或完整 App/Lark 验收，旧 v1 回放保持。

### Signed cash reconciliation (candidate 0.8.5)

`assess-cash` consumes six declared rows from one retained source column. It
calculates signed gross capex plus asset-sale and government-incentive proceeds,
then operating cash flow plus that net capex. Residuals compare the calculated
amounts with the reported net-capex and adjusted-FCF rows. This is a deterministic
arithmetic assessment; extraction, row classification and current source truth
remain upstream. See [the bounded input contract](CONTRACT.md#signed-cash-reconciliation).

```bash
loopx-finance-value-discovery assess-cash \
  --input-json packages/loopx-finance-value-discovery/examples/cash-reconciliation-v1.json
loopx extension run loopx-finance-value-discovery \
  --input-json packages/loopx-finance-value-discovery/examples/cash-reconciliation-v1.json \
  --execute --format json
```

Install and enable the extension using the lifecycle commands below; `--doctor`
checks the provider. Disable it with `loopx extension disable
loopx-finance-value-discovery --execute --format json`, then enable with the
corresponding `extension enable` command. Roll back by installing a previously
pinned package wheel and upgrading the runtime registration to that matching
manifest. An older provider without this input schema rejects the cash request;
rollback does not convert it into a successful assessment.

CLI/direct and managed-runtime paths share the same function. App/Lark cash
presentation and source-provider classification are companion work; this slice
does not complete that user journey or authorize financial/trading operations.
Existing period stdin dispatch is retained alongside the new cash schema.

0.8.5 候选通过同一 direct/managed 入口消费六项已声明现金行，计算带符号
gross capex + 销售及政府激励现金流入 = net capex，以及 CFO + net capex =
adjusted FCF。缺证、冲突、错单位、错来源列和错符号明确保留；显示舍入不生成
新的精确观察。算术一致不代表来源真实、PIT、可分配现金或金融准入。App/Lark
展示和上游来源分类仍是配套交付，关闭及旧 wheel 回退沿现有 extension 生命周期。

Install the extension package, then register its manifest with the LoopX
extension runtime:

```bash
python3 -m pip install ./packages/loopx-finance-value-discovery
loopx extension install \
  --manifest packages/loopx-finance-value-discovery/extension.toml \
  --execute \
  --format json
```

Invoke the enabled extension through LoopX's managed runtime:

```bash
loopx extension run loopx-finance-value-discovery \
  --input-json packages/loopx-finance-value-discovery/examples/paypal-debeta-discovery.json \
  --execute \
  --format json
```

The same managed entrypoint accepts a `finance_case_gate_input_v1` object:

```bash
loopx extension run loopx-finance-value-discovery \
  --input-json packages/loopx-finance-value-discovery/examples/finance-case-gates-v1.json \
  --execute \
  --format json
```

Layered attribution and industry packs use the same managed entrypoint:

```bash
loopx extension run loopx-finance-value-discovery \
  --input-json packages/loopx-finance-value-discovery/examples/beta-attribution-v1.json \
  --execute \
  --format json
loopx extension run loopx-finance-value-discovery \
  --input-json packages/loopx-finance-value-discovery/examples/software-metric-pack-v1.json \
  --execute \
  --format json
```

Extension 0.8.5 also dispatches `finance_period_comparison_input_v1` to the
existing period assessment. For the same frozen input, the managed provider
result matches the direct `assess-period` result:

```bash
loopx-finance-value-discovery assess-period \
  --input-json packages/loopx-finance-value-discovery/examples/period-comparison-v1.json
loopx extension run loopx-finance-value-discovery \
  --input-json packages/loopx-finance-value-discovery/examples/period-comparison-v1.json \
  --execute --format json
```

The example is synthetic. `period_evidence_eligible` applies only to the period
axis; source authentication, lifecycle, financial admission and trading remain
false. Missing declarations produce ineligible results; malformed inputs fail
with an error packet. Version 0.8.3 supports the direct API/CLI but does not route
these inputs through managed stdin. Qualify the exact binary/manifest/input
pair before opting in, and retain the old pair for rollback.

Run the provider dispatch regression cases from the repository root:

```bash
uv run --extra test pytest packages/loopx-finance-value-discovery/tests/test_period_dispatch.py
```

Developers can inspect or replay a frozen evaluation directly:

```bash
loopx-finance-value-discovery evaluate \
  --input-json packages/loopx-finance-value-discovery/examples/finance-case-gates-v1.json
loopx-finance-value-discovery replay \
  --input-json packages/loopx-finance-value-discovery/examples/finance-case-gates-v1.json \
  --expected-json evaluation.json
loopx-finance-value-discovery list-packs
loopx-finance-value-discovery attribute-beta \
  --input-json packages/loopx-finance-value-discovery/examples/beta-attribution-v1.json
loopx-finance-value-discovery evaluate-pack \
  --input-json packages/loopx-finance-value-discovery/examples/software-metric-pack-v1.json
```

In extension 0.5.0, a frozen gate can require source-query coverage computed from
normalized page receipts. Matching totals alone cannot pass it: missing pages or
snapshot assertions remain insufficient evidence. This does not verify source
truth or historical publication time. See the [coverage contract](CONTRACT.md#source-query-coverage-extension-050).

```bash
loopx extension run loopx-finance-value-discovery \
  --input-json packages/loopx-finance-value-discovery/examples/finance-source-coverage-v1.json \
  --execute --format json
loopx-finance-value-discovery evaluate \
  --input-json packages/loopx-finance-value-discovery/examples/finance-source-coverage-v1.json > evaluation.json
loopx-finance-value-discovery replay \
  --input-json packages/loopx-finance-value-discovery/examples/finance-source-coverage-v1.json \
  --expected-json evaluation.json
```

The synthetic example passes query coverage but still lacks economic evidence.
Existing inputs without the optional gate retain their result and replay bytes.

The manifest declares no permissions: this workflow is a deterministic reducer
over caller-supplied frozen public evidence. It performs no collection or other
effectful operation. Permissioned Finance work must use a capability or domain
command with an explicit typed authority decision rather than standalone run.

There is no `value-connectors` Finance execution route. The package binary is
a provider implementation and developer-debugging surface, not the supported
management entrypoint. Callers install and invoke this independently versioned
extension through `loopx extension`.

The retired `finance_market_snapshot` value-connector selectors remain only as
machine-readable migration tombstones for upgrades. They point to this
extension but cannot execute it, register a capability, or install an absent
provider implicitly.

### Same-event funding cash (candidate 0.8.13)

Assess two declared funding payments with explicit native periods, event,
payment direction, quantity basis and currency. Different native periods can
contribute to the same payment event; this does not compare naked rates.
Forecast estimates and declared realized cash use separate fields. Missing
second legs, event/period evidence, units or costs stay unknown; no trade or
guaranteed carry is admitted. See the [closed Finance contract](CONTRACT.md#same-event-funding-cash-candidate-extension-0813).

```sh
loopx-finance-value-discovery assess-funding-event \
  --input-json packages/loopx-finance-value-discovery/examples/funding-event-v1.json
loopx extension run loopx-finance-value-discovery \
  --input-json packages/loopx-finance-value-discovery/examples/funding-event-v1.json \
  --execute --format json
```

The synthetic example's declared same-event net cash is -0.4 SYN despite
different 1h/8h accrual periods. It authenticates neither source nor payment.
Install/doctor and enable through the existing lifecycle above. Disable with
`loopx extension disable loopx-finance-value-discovery --execute`; rollback to
the pinned 0.8.12 wheel and matching manifest rejects the new input schema while
preserving old inputs. The source-provider production consumer and financial
utility remain separate qualifications.

两腿分别声明原率、期间、事件、付收方向、数量与币种。同币且已明确结算于同一
事件的现金可以相加，期间长度不同本身不拒绝；裸率不直接比较。未来预测的条件
估计单列，realized net_cash 始终为空。缺腿、事件/期间、单位或费用不补零，
合成样例及本地计算不证明来源、成交、PIT、金融效用或交易授权。

### Funding research review (candidate 0.8.14)

`finance_funding_event_research_input_v1` wraps the original event-pair input
with the existing publication identity, version, supersedes and review deadline.
It returns the complete `funding_assessment` and the existing Investment Research
view, preserving each original leg, source digest/version/clocks, costs and holds.
Conditional forecasts and declared realized cash remain separate; unknown is
never displayed as zero. No scenario probability, source authentication or
trading admission is inferred. Private/unsafe display text is refused.

```sh
loopx extension publish-projection loopx-finance-value-discovery investment-research \
  --input-json packages/loopx-finance-value-discovery/examples/funding-research-v1.json \
  --execute --format json
loopx-finance-value-discovery render-lark-card \
  --input-json packages/loopx-finance-value-discovery/examples/funding-research-v1.json
```

Open that Goal's **Results / 成果** tab to read the published assessment. For an
exact published Lark draft, use the existing `--published-state-file`,
`--goal-id`, `--extension-revision` and `--payload-sha256` flags from its receipt;
wrong/stale references and disabled publications fail rather than choosing a
replacement. Rendering prepares a card and sends nothing. Disable through the
existing extension lifecycle; rollback to the pinned 0.8.13 wheel/manifest keeps
old arithmetic/research inputs and refuses this new wrapper.

同一原始资金费输入生成完整 assessment 与既有投研视图，成果页和 Lark 草稿
复用同一发布引用。预测、声明已实现、费用未知及来源版本/时钟分开；不补零、
不虚构概率，不据展示授予交易或金融准入。卡片只准备，不发送；错引用、
已关闭或旧 revision 拒绝，恢复后按原 hash 重新读取。


### Quantity stocks and signed flows (candidate 0.8.15)

`assess-quantity` compares **opening stock + declared increases − declared
decreases = closing stock** within one source artifact, instrument/class and
ownership pool. Its unit is a quantity (`share`, `native_contract` or `item`)
plus a scale; it never borrows the cash contract's `currency` field.

```bash
loopx-finance-value-discovery assess-quantity \
  --input-json packages/loopx-finance-value-discovery/examples/quantity-reconciliation-v1.json
# Installed extension/provider stdin uses the same schema and owner.
loopx-finance-value-discovery < quantity-reconciliation-v1.json
```

The example explicitly declares a single pool, exact producer accuracy, complete
two-flow coverage and source-row order: opening 100, increase 25, decrease 8,
closing 117. These are synthetic declarations, not authenticated ownership.
Each stock/flow carries its evidence reference, source digest/capture clock,
identity, quantity unit/scale, value/accuracy and source ordinal. Flows also
carry an explicit `increase` or `decrease`; a transaction code alone never
selects a sign. The original input is retained in `operands`.

Only compatible, ordered, unique and completely covered operands with
producer-declared exact accuracy can return `quantity_reconciled=true`. Missing
opening is retained; it is never inferred by subtracting flows from closing.
Missing flow/value/direction/coverage yields an incomplete assessment. Unknown
pool, class, version, unit, source or precision withholds a reconciled result.
A different class/pool/unit/scale/source digest, repeated flow reference or
invalid ordinal window also prevents combination. Finite decimals do not become
exact because an integer was displayed. A mismatching exact closing balance is
a conflict. Loaded zero and an explicitly covered zero-flow interval are valid.

Candidate lexical arithmetic can be retained when accuracy or other evidence
remains unproven, but the qualified result stays null. Quantities are at the
same declared scale; there is no unit/class/rights conversion. Two reporting
persons do not multiply a transaction: the source consumer must supply unique
flow refs and establish the actual ownership scope. Source row order and
capture clocks do not certify event time, earliest publication or PIT.

This is the optional provider's CLI/stdin computation stage. Source adapters
retain extraction, pool and coverage interpretation. Disable the isolated
Finance node through `loopx extension disable loopx-finance-value-discovery
--state-file <isolated-state> --execute`; a disabled native invocation refuses.
An explicitly called local binary is separate from native activation. Preserve
the previous revision and use the existing extension rollback/upgrade commands
for controlled recovery. No default node or account is activated by this API.

The existing numeric-accuracy owner now also supplies bounded Decimal parsing;
legacy cash/stock commands retain their input and error contracts. This arithmetic stage does not interpret or authenticate sources, derive cash,
or grant financial or trading permission. The review wrapper below supplies
the existing App/Lark presentation; source-consumer and financial utility
qualification remain separate.

份额/合约数与货币分别建模，同类别与同持有池的数量核对不推导现金或交易权限。
opening、覆盖、付收方向或精度未知时保留未知，不反推、不补零。合成示例通过
只证明声明下的词法算术；来源认证、PIT、界面/卡片与实际采用另行验收。


### Quantity research review (candidate 0.8.16)

`finance_quantity_research_input_v1` wraps the original quantity input and an
explicit review time, evidence cutoff, publication source/version, supersedes
and review deadline. It returns the complete `quantity_assessment` plus the
existing Investment Research view. Opening/closing stocks, every signed flow,
identity/unit/accuracy, source digest and original capture time remain visible.
Candidate arithmetic, reconciled closing and all holds remain distinct;
missing is not zero, and a conflict never becomes a qualified closing.

```sh
loopx-finance-value-discovery < packages/loopx-finance-value-discovery/examples/quantity-research-v1.json
loopx extension publish-projection loopx-finance-value-discovery investment-research \
  --input-json packages/loopx-finance-value-discovery/examples/quantity-research-v1.json \
  --execute --format json
loopx-finance-value-discovery render-lark-card \
  --input-json packages/loopx-finance-value-discovery/examples/quantity-research-v1.json
```

Open the selected Goal's **Results / 成果** tab, inspect the Quantity review
and its original source/reference details, and use **Refresh / 刷新** after
publication changes. The same revision/hash/deadline is used for a published
Lark draft: pass its receipt to the existing `--published-state-file`, `--goal-id`,
`--extension-revision` and `--payload-sha256` flags. Stale, wrong-Goal and disabled
publications refuse; recovery reads the current reference rather than silently
substituting a result. Card preparation sends nothing and does not renew a
review deadline or grant source, investment, cash or trading admission.

The complete review view supports at most **8 flows** within its existing
12-layer and text limits; larger/unsafe views are refused without truncation.
The arithmetic API still supports 256 flows. Exact long decimal results move
to the arithmetic detail rather than being rounded into a metric cell. Lark's
existing complete-card capacity may also refuse a long view; read the complete
published result in App. Disable using the existing extension lifecycle, and
retain the pinned 0.8.15 wheel/manifest for rollback: old quantity/cash/funding
inputs remain valid, while that old release refuses the new review wrapper.
No private source, account or raw provider response belongs in this public-safe
view. Source mapping, independent consumer adoption and financial utility
remain separate from presentation qualification.

数量复核复用既有成果页与 Lark 发布引用；原始 operands、缺证、冲突及来源时钟
完整保留。长精确数不截断、不四舍五入，超容量明确拒绝。关闭、旧引用与错 Goal
不返回缓存结果；恢复后重新核对当前引用。展示、来源准入、经验采用与金融效用
分别验收；卡片只准备不发送。
