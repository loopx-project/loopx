# Finance Value Discovery

Status: co-located optional LoopX extension sample.

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

Render the exact published-view semantics for an authorized Lark delivery
without sending anything:

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
