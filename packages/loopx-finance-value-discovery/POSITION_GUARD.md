# Position guard / 持仓守护

## Placement and scope / 归属与范围

Extension 0.8.1 adds `evaluate-position` to the existing optional finance
package. It evaluates one filled position episode from four normalized read
receipts. It does not register a new capability or change the extension's
enablement, Core scheduler, Todo lifecycle, permissions or channel delivery.
The existing public-safe discovery and dashboard operations retain their
contracts. This operation's input and output are **private account material**.

0.8.1 在现有可选 finance 包里增加 `evaluate-position`，根据四类归一化读回
评估一个已成交持仓周期。不注册新 capability，不改变已有启用入口、Core
调度、Todo 生命周期、权限及群投递。原公开安全研究与 Dashboard 操作契约
保持原样；本操作的输入与输出属于**私有账户材料**。

| Owner / 归属 | Responsibility / 职责 |
| --- | --- |
| LoopX Core | Existing monitor identity, ownership, due time, settlement and notification delivery / 已有监控身份、责任、到期、结算及通知投递 |
| LoopX finance package | Financial protection, cost and exit reconciliation assessment / 财务保护、成本及退出对账评估 |
| Private venue adapter | Account binding, pagination, episode isolation, payoff/unit normalization, trigger semantics and actual receipts / 账户绑定、分页、周期隔离、损益和单位归一化、触发语义及真实回执 |
| Human | Final financial submit/sign action / 最终金融提交与签名 |

The reducer stays Python because it is specialized decimal financial
calculation inside the existing Python finance package. No second control-plane
orchestration owner is introduced.
该 reducer 沿用 Python，因为它是现有 Python finance 包内的专用十进制金融
计算；没有新增第二个控制面或编排权威。

## Assessment / 评估

The request binds `position`, `orders`, `fills` and `monitor` receipts by
canonical digest, account scope, asset and stable episode target key. Every
read has an observation time and completeness declaration. The decision clock
must be current; stale, future, incomplete or excessively skewed reads cannot
claim protection or verified closure. These checks establish consistency, not
authenticity of a provider declaration or an atomic venue snapshot.

请求使用 canonical 摘要、账户 scope、资产和稳定持仓周期 target key，绑定
`position`、`orders`、`fills`、`monitor` 四类回执。每类读回声明观察时间及
完整性；决策时钟必须当前。过期、未来、缺项或时间偏差过大的读回不能证明
保护有效或已平仓。这些检查证明一致性，不能认证 provider 的声明真实性，
也不能把分次读回变成交易所原子快照。

### Holding deadline precedence (0.8.7) / 持有期限优先级

At or after `max_hold_until`, reported open exposure always returns
`exit_review_required`. A later `next_due_at`, missing costs, incomplete orders
or an expired monitor cannot reduce that obligation to a readback repair.
All verification reasons remain visible and unverified risk estimates stay
null. An exit draft is withheld unless the position receipt is complete,
fresh, bound to the episode/account/asset, and has matching side and a quantity
no larger than the opening quantity. A draft remains a human final check,
never an executable order or authority grant.

到达或超过 `max_hold_until` 后，读回的开放敞口始终产生退出复核义务。
下一次轮询较晚、成本缺证、订单不完整或监控过期不能把它降成仅修复读回。
所有缺证原因仍保留，未核实风险金额为 null。只有持仓回执完整、新鲜、
账户/资产/周期绑定正确、方向相符且数量不超过初始数量才生成草案；草案
仍须本人最终核对，不授权下单。

Callers must include the holding deadline and its current disposition in the
material observation they pass to Core's existing monitor followthrough.
Price/position equality alone does not establish no change across a deadline.
A decision receipt does not discharge an open or partially exited position.
Only verified flat exposure with reconciled fills and no residual orders can
propose monitor closure. A human-admitted extension is a separate upstream
decision with retained provenance: changing the poll schedule does not extend
the frozen deadline. This reducer neither authenticates extensions nor owns
Todo, lease, notification or trading authority.

调用者须把持有期限及当前处置状态纳入已有 Core monitor followthrough 的
材料观察。价格/仓位相同不能证明跨期限无变化；裁决回执或部分退出不解除
剩余义务。完整退出对账与无残余订单才可建议关闭。显式人工延期由上游准入并
保留依据；轮询时间变动不延长原期限。本 reducer 不认证延期或接管 Todo、
lease、通知及交易权限。CLI/managed 修复仍是后端切片，App/Lark 与真实场所
消费者须沿原 owner 单独验收。

- Stop and take-profit coverage are checked separately. Each accepted leg must
  independently cover the remaining position, use the opposite side and
  `reduce_only=true`, and match the frozen price and normalized trigger basis.
  Alternative or split legs are not added without venue tranche proof.
- Fees are counted once per unique fill, including any already-inclusive
  builder component. Signed funding, realized partial-exit PnL and a remaining
  exit-cost reserve contribute to the estimated loss at the frozen stop.
- `next_due_at` and maximum holding deadline are distinct. The suggestion never
  postpones an earlier existing Core review. An expired monitor cannot protect
  an open position.
- A partial exit keeps the monitor open. Flat position alone is insufficient:
  complete entry/exit history must reconcile and all protective orders must
  be gone. Fill/order closure reads must follow or coincide with the position
  read. Core decides whether and when to close the monitor.

中文：止损与止盈分别核对，单条被接受的保护单须独立覆盖剩余仓位、方向相反、
`reduce_only=true`，并匹配冻结价格和归一化触发依据；没有场所分段证明时不
累加替代单。逐 fill 去重手续费，已含 builder 部分不重复收费；资金费、部分
退出已实现损益和剩余成本预留参与止损情景估计。下一次复核与最长持有期限
区分，建议不能推迟已有 Core 复核；监控过期不能证明持仓已受保护。部分退出
保留监控；仅仓位为零不够，还须完整成交对账、保护单全部消失，以及成交和
订单关闭读回不早于仓位读回。是否关闭监控仍由 Core 决定。

M1 supports only `payoff_model=linear_quote_cash`, `quantity_unit=base_asset`,
`unit_multiplier=1`, with matching normalized price/cash currency. Options,
inverse contracts and unnormalized contract multipliers are rejected. The
private adapter must prove this normalization before calling the operation.
An estimated loss is never a guaranteed loss cap: gaps, slippage, funding and
venue failure may exceed a stop-based estimate. The operation makes no financial
or monitor write. A due exit yields only a human-check draft with fresh venue
preview required; it supplies no executable order or signing authority.

M1 仅接受线性 quote-cash 损益、base-asset 数量、单位乘数 1，且归一化价格与
现金币种一致。期权、反向合约或未经归一化的合约乘数会被拒绝；私有适配器在
调用前须证明归一化有效。风险估计不保证亏损上限：跳空、滑点、资金费或场所
故障可能超过止损估计。本操作不写订单或监控；退出到期时仅输出需用户核对、
重新取得场所预览的草案，不输出可执行订单或签名权限。

## Explicit partial readback (0.8.8) / 显式缺证读回

The same operation accepts an opt-in `position_guard_partial_request_v1` and
returns `position_guard_partial_result_v1`. Keep using the strict v0 request
for financial protection, cost estimates and verified closure. The partial
schema is not a relaxation of v0: it represents an unresolved episode when a
source adapter cannot supply four complete receipts. A null position and a
null displayed quantity are admitted without manufacturing account facts.

同一操作显式接受 `position_guard_partial_request_v1`，返回独立的 v1 结果。
保护、成本估计及可核验结案仍使用严格 v0。缺证路径单独表示尚未解决的周期，
允许仓位或显示数量为 null，不伪造四类完整回执。

The request retains four distinct source projections: the original episode
and holding deadline, an optional observed position, the decision disposition,
and execution disposition. Bind each non-null source reference to its component's
canonical digest in `context_refs`; retain the original source digest and clock.
This binding checks supplied-input consistency, not source authenticity. Neither
the caller's deadline nor an observed flat quantity becomes trusted authority.
A source adapter must retain the original episode and separately authenticate any
human-admitted extension; changing its poll schedule cannot extend that deadline.

输入分别保留原周期/期限、可选仓位观察、裁决及执行状态，按组件 canonical
摘要绑定来源。原来源摘要与时钟继续保留；绑定仅证明输入一致，不能认证
来源、期限或显示的零仓位。适配器须保留原周期，人工延期另行认证，轮询
频率变化不能延长期限。

At or after the supplied original deadline, the result is `exit_review_required`
and urgent even when quantity is missing or unchanged. Every partial result
keeps `pending=true`, `closeout_verified=false`, `position_verified=false` and
`deadline_authority_verified=false`; risk, protection and exit draft are null.
`partial_exit_observed` and `flat_observed_unverified` preserve that obligation.
A mismatched observed asset adds `source_conflict` without masking stale reads.
The decision clock must be current, while the observation's original clock stays
unchanged and may be classified stale. Private source references never authorize
an order, monitor closure, installation or a new timer.

到达原期限后，即使数量缺失或相同，也输出紧急退出复核；所有缺证结果均
保留待处理义务，并明确仓位、期限与结案未经认证。风险、保护及退出草稿
为 null；部分退出或未核验零仓位不能结案。资产不符与时点失效分别呈现。
决策时钟须当前，观察时钟不能刷新成现在。来源引用不授予交易、关监控、
安装或新定时任务权限。

`material_projection` and its digest omit poll timestamps, invocation IDs and
repeated capture digests, but retain the original episode digest, deadline phase,
normalized observed quantity/unit/asset, source freshness class, decision,
execution and evidence gaps. Pass this material observation to the existing Core
followthrough owner. A repeated equivalent capture is quiet; crossing a deadline,
partial execution or a changed gap is material. The reducer writes no monitor or
Todo and does not perform that consumer adoption itself.

材料摘要排除轮询时钟、调用 nonce 及重复采集摘要，保留原周期摘要、期限
阶段、归一化显示数量/单位/资产、新鲜度类别、裁决、执行与缺证。调用者将
该材料交给已有 Core followthrough；相同采集保持安静，跨期限、部分执行或
缺证变化属于实质变化。本 reducer 不写监控/Todo，也不代替消费者采用。

Use the existing CLI and enabled extension entry points shown below, with a
request whose `schema_version` explicitly selects v1. The combined
[`position-guard-partial.schema.json`](src/loopx_finance_value_discovery/schemas/position-guard-partial.schema.json)
is the input and output contract. Do not put this private result on the existing
public-safe research dashboard. The private source producer, App and Lark receipt
consumers still require their own integration and delivery acceptance. To fall
back, keep the original strict v0 request; disable/restore the extension using
its existing lifecycle owner. A v1 request sent to an older package must fail
admission, not silently lose the obligation. Rollback preserves private episode
and execution evidence instead of overwriting it with an earlier capture.

用下方既有 CLI/extension 入口，输入版本明确选择 v1；同一 schema 定义输入与
结果。结果保持私有，不能放入公开研究 Dashboard。私有来源生产者、App 与
Lark 消费/投递须各自验收。回退沿用严格 v0 和既有 extension 生命周期；旧包
应拒绝 v1，不能静默丢掉义务。回滚保留后续周期/执行证据，不用旧采集覆盖。

### Independent capture clocks (0.8.9) / 独立捕获时钟

The partial request optionally accepts `input.source_clocks`, with all three
`positions`, `orders` and `history` keys. Each value is its original capture
timestamp or explicit `null`. Optional `input.source_capture_digest` retains
the adapter's capture-bundle digest; it requires `source_clocks`, and omission
means unknown. Neither field authenticates the source or account. Use the
existing `evaluate-position --input-json FILE` or managed stdin entrypoint.

The result's opt-in `source_clock_projection` preserves every timestamp and
the declared digest, even when `position=null`. Each clock is evaluated
independently against `decision_at` and `max_age_seconds`: equality at the age
limit is available but unverified; a future or older clock is stale or future;
null is missing. A fresh orders capture cannot mask stale history. Clock states
enter the semantic material digest, so eligibility changes are observable;
new timestamps or raw capture hashes alone do not constitute material change.
Deadline precedence, pending obligations, missing facts, and the prohibition on
verified closure or an executable draft are unchanged. Consumers must use the
same provider version and pass original capture clocks rather than poll time.

Omit both fields to restore the legacy request/result shape and material digest.
The strict v0 receipt request is unchanged and does not accept these fields.
Disable or roll back through the existing extension lifecycle; this addition
does not add a new activation switch, scheduler, permission, or Memory policy.
CLI and managed-provider behavior are covered here; automatic venue bridging,
App/Lark presentation and investment utility require their own adoption evidence.

These are account-source capture clocks. A market query's start/end bounds,
each returned candle's economic period, capture version, first-public time,
vendor finalization and current quote expiry are different facts. Do not map
query bounds or a historical candle close into these capture fields. Retain
both raw captures when values change under the same period identity; an elapsed
period alone proves neither an immutable vendor revision nor historical PIT.

显式 partial 请求可选传入 `input.source_clocks`，须包含 `positions`、
`orders`、`history` 三个键；每项保留原捕获时间，未知写 null。可选
`source_capture_digest` 保存适配器声明的捕获包摘要，须同时传时钟；省略
表示摘要未知。它们不认证来源或账户，沿用已有 direct/managed 入口。

输出 `source_clock_projection` 在 position 为 null 时仍保留全部原时钟和
摘要，分别评估新鲜度。恰等于时效上限为可得但未验证；未来/过旧为不可用，
null 为缺证。不能用新鲜订单遮蔽过旧历史。语义 material digest 包含各项
时效状态，捕获时间或原件摘要变化本身不算材料变化。到期优先级、待办、
未知及禁止认证结案/执行草案的边界保持；调用者按同版本传原捕获时间。

省略两个字段即恢复旧请求/输出形状及材料摘要，严格 v0 四回执契约不变。
沿已有 extension 生命周期关闭/回滚，不增加授权、定时器或 Memory 政策。
此为 CLI/managed provider 切片；场所自动桥、App/Lark 和投资效用另验。
查询范围、每根柱的经济期间、捕获版本、首次公开、供应商最终版本与报价
期限分别保留；同期间数值变化保留两个原件，窗口经过不证明最终版本或 PIT。

## Existing entry points / 已有入口

```sh
loopx-finance-value-discovery evaluate-position --input-json private-request.json

# The same request also works through the existing enabled extension runtime.
loopx extension run loopx-finance-value-discovery \
  --input-json private-request.json --execute --format json
```

Input schema: [`position-guard-request.schema.json`](src/loopx_finance_value_discovery/schemas/position-guard-request.schema.json).
Output schema: [`position-guard-response.schema.json`](src/loopx_finance_value_discovery/schemas/position-guard-response.schema.json).
Both CLI paths return the same typed receipt. The pure reducer performs no
network access. Keep requests, account hashes, fills and responses out of public
logs, fixtures and public-safe presentation surfaces.

两个 CLI 路径返回相同 typed receipt；纯 reducer 不访问网络。输入、账户摘要、
成交和响应不能进入公开日志、fixture 或公开安全展示面。

This is a bounded **backend prerequisite**, not complete monitor adoption.
Frontend and Lark integration must reuse the existing monitor/Todo and channel
owners, enforce private visibility, and verify receipt state, delivery and
error readback on each affected entry point. No extra configuration field is
introduced by this slice; no frontend switch is needed to expose one. Until
real consumers are verified, do not claim end-to-end notifications or exits.

该切片是有界的**后端前置能力**，不是监控全链路采用。Frontend 与 Lark 必须
复用已有监控/Todo 及 channel owner，执行私有可见性，并验收受影响入口的
状态、投递和错误读回。本切片没有新增配置字段，不需另造前端开关；在真实
消费者验收前，不能声称通知或退出闭环已完成。

## Rebuild without copying a workspace / 不复制工作区的重建

1. Clone `loopx-project/loopx` from its published history at the reviewed merged
   commit. Install Core through its existing release owner; retain the prior
   release for rollback. Build this package from that same source revision.
2. Rebuild an isolated finance environment. Record the source commit, package
   version, wheel SHA-256, Python version and resolved dependency inventory.
   Include `jsonschema>=4.23,<5`; never silently upgrade a private provider's
   differently pinned finance dependency to use this operation.
3. Reconnect the existing extension through its original configuration owner;
   run doctor and verify enabled-runtime readback. Installation is not adoption.
4. Restore private adapter configuration, journal and evidence only through an
   explicit owner-approved private handoff. Authenticate the venue afresh.
   Reacquire fresh position/order/fill reads, reconcile open episodes with Core
   monitors and prove one real read-only receipt before claiming readiness.
5. To roll back, restore the previous finance runtime/configuration through the
   same owner. Retain subsequent journal/evidence facts and reconcile monitor
   state; never overwrite new financial history with an older snapshot.

中文：从发布历史按已评审合并提交重建 Core 和本包，保留旧 release 供回滚；
隔离 finance 环境并记录源码提交、版本、wheel 摘要、Python 和实际依赖。
显式安装 jsonschema，不为本操作静默升级私有 provider 的其他固定依赖。
由原配置 owner 重新接入 extension，核对 doctor 与已启用 runtime；安装不等于
采用。私有适配配置、日记及证据只走 owner 批准的私有 handoff，场所重新认证；
重采真实仓位、订单、成交，与 Core 监控对账并验证一条只读回执后再称就绪。
回滚沿用原 owner，保留后续新增财务事实，不用旧快照覆盖新历史。

Validation / 验证：

```sh
python -m pytest -q tests/extensions/test_finance_position_guard.py
python -m compileall -q packages/loopx-finance-value-discovery/src
uv build --out-dir /tmp/finance-wheel packages/loopx-finance-value-discovery
```

The tests use synthetic data only and cover protection direction/coverage,
partial/full closure, stale identity/time, monitor expiry, risk accounting,
unsupported payoff/unit models and direct/managed CLI parity.
测试仅用合成数据，覆盖保护方向及数量、部分/完整退出、身份和时点失效、监控
过期、风险对账、不支持的损益/单位模型，以及直接/managed CLI 一致性。
