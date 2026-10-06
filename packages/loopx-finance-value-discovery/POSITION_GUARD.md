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

### Holding deadline precedence (0.8.7 candidate) / 持有期限优先级

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
