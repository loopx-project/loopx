# LoopX Finance Execution

Status: optional M1 simulator.

This distribution is the first financial consumer of LoopX's human-confirmed
operation envelope. It validates one immutable `finance_order_intent_v0` and
returns a deterministic simulated fill. It contains no venue client, signer,
credential reader, transfer path, reservation authority, or retrying network
submission.

The Core typed-action store owns confirmation and the single execution claim.
This package accepts only a consumed claim whose operation and payload digests
match. Its `finance.operation.simulate` permission does not authorize a real
order. Every result is marked `simulation=true` and
`external_write_performed=false`.

Install and enable it through the normal LoopX extension lifecycle before using
the M1 operation-card flow:

```bash
python3 -m pip install ./packages/loopx-finance-execution
loopx extension install \
  --manifest packages/loopx-finance-execution/extension.toml \
  --execute --format json
loopx extension enable loopx-finance-execution --execute --format json
```

Read the registered provider-neutral contract back before preparing a card:

```bash
loopx capability show human-confirmed-operation-executor --format json
loopx goal-channel prepare-operation --help
loopx goal-channel deliver-operation --help
```

The formal product entry is Core's two-step Goal Channel path. The finance
extension is never called as a confirmation shortcut: `prepare-operation`
persists the immutable `loopx_operation_request_v0`, `deliver-operation`
projects that same request to the bound channel, and only an authenticated,
unexpired callback may consume the execution claim. Replaying the same claim
returns the existing result instead of executing again.

Real venue adapters are intentionally out of scope. They require the later
finance reservation, ambiguity/reconciliation and venue conformance milestones.

## Financial pre-submit and retained-result readback

The same installed command also exposes two explicit **read-only** M2 building
blocks. They do not change the default M1 simulator, install a venue adapter,
authenticate a click, consume approval, reserve capacity, or grant permission to
submit. Passing financial validation is one necessary check, not an execution
permit. Core's original operation/claim/consumption remains the sole authority.

```bash
loopx-finance-execution --check-order-summary < summary-check.json
loopx-finance-execution --reconcile-execution < retained-execution.json
```

`summary-check.json` contains `terms`, `summary`, `observed_at`, `now` and an
explicit `maximum_age_seconds` (1..300, caller policy rather than vendor TTL).
Terms use `finance_execution_terms_v0`, the final visible summary uses
`finance_order_summary_v0`. Both require account reference, venue, product,
instrument, side, quantity/unit, limit price/unit, time-in-force, reduce-only,
margin mode, leverage, maximum fee/unit, protective orders and original expiry.
Each protection requires kind, trigger price/unit, quantity/unit and reduce-only.
Numbers are decimal strings. Missing/unknown fields, different parameters, stale
or future summaries and equality with the original expiry stop the attempt.
Equivalent decimal formatting is accepted for top-level numbers; protection
lists remain exact. The original terms digest always binds the original
JSON values, not a rounded or reformulated order.

Numeric inputs retain every supplied digit within an explicit bounded policy:
at most 80 characters, with both the stored decimal exponent and the adjusted
exponent in -256..256. Unsupported magnitudes/scales fail before classification.
Up to 1000 fills are summed using aligned integer coefficients and exact Decimal
tuple construction; caller precision, rounding and traps cannot turn a tiny
underfill/overfill or fee-cap excess into a complete result. No global Decimal
context is modified. The original M1 simulator keeps its existing numeric policy.

`retained-execution.json` contains those same `terms`, one `observation`, the
original `operation_id` and the original `attempt_id`. The observation schema is
`finance_execution_observation_v0`: exact operation/attempt/terms digest and
account/venue/instrument, observation time, explicit coverage/submission flags,
order ID/state, fills and independent protection state. Each fill requires its
own ID, the order ID, side, quantity/unit, price/unit and fee/unit (fee may be
null). These are caller-retained venue facts, not model-invented execution or
authentication receipts. Source authenticity, account-wide capacity, precision,
credential access and submission remain with their respective owners.

The reducer distinguishes not submitted, submitted, partial, filled, rejected,
cancelled and submission unknown. Incomplete coverage, missing order identity
or contradictory terminal/fill evidence remains unknown. Expiry never erases an
attempt or authorizes resubmission. Duplicate fill IDs, excessive quantity and
different order identity, units or limit bounds are rejected. Missing fees stay
null; a filled entry with failed/unknown protection or unknown/excess fees is
not complete. Every response returns `execution_allowed=false`,
`resubmit_allowed=false` for reconciliation and `external_write_performed=false`.
Calling reconciliation again after restart only re-reads retained facts.

### Declared cash and loss bounds

`loopx-finance-execution --check-risk-bound < risk-readback.json` adds a
read-only pre-submit check. The request contains `terms`, `budget`,
`exposure_bound`, `observed_at`, `now` and `maximum_age_seconds` (1..300).
`terms` uses the same immutable `finance_execution_terms_v0` schema.

`budget` uses `finance_risk_budget_v0`: `cash_unit`, `maximum_input` and
`maximum_loss`, both positive decimal strings. `exposure_bound` is null when
unavailable, or a `finance_exposure_bound_v0` object with these required fields:

| Field | Meaning |
| --- | --- |
| `terms_digest`, `source_ref` | Original exact terms SHA256 and retained producer evidence |
| `cash_unit` | Same unit as the budget, price and fee; no inferred FX |
| `maximum_cash_committed` | Actual capped cash/collateral allocation, not order notional |
| `maximum_additional_liability` | Explicit bound on liability outside that allocation |
| `maximum_total_cost` | Upper bound covering fees, funding, exit/slippage and other charges |
| `liability_scope` | `capped`, `uncapped` or `unknown` |
| `automatic_margin_addition` | Boolean or null; only explicit false is supported |

The three amount fields are nonnegative decimal strings or null. Unknown is
never zero. Cross margin, uncapped/unknown liability and automatic/unknown
collateral addition are held. This initial check supports fully paid `spot`
buys in cash mode at 1x and `linear_perpetual` isolated orders, with quantity
in the instrument's base unit. Inverse/contract-multiplier products, shorts in
cash mode and undeclared unit conversions are unsupported. Cash allocation
must cover quantity times limit price; isolated allocation must at least cover
that notional divided by leverage. These are exact lower-bound comparisons,
not an estimate of a venue's actual liability cap.

Both conservative upper bounds include all committed cash, additional liability
and total cost. Stops and expected exit prices never reduce the loss bound.
Missing facts, digest/unit drift, stale evidence, expiry and budget excess are
held. Arithmetic does not depend on the caller's Decimal precision or traps.

`within_declared_bound` means only that these declared values fit the budget.
The venue producer must independently authenticate their scope, completeness
and maximum-liability mechanism. Every response explicitly returns
`venue_bound_authenticated=false`, `execution_allowed=false` and
`external_write_performed=false`. This does not reserve account capacity or
install a venue client; unknown production facts must not be filled by a model.
The schemas and closed vocabulary belong to this optional Finance provider,
not a new Core approval or permission contract.

A DSH/Ego executor can consume the readback checks after native approval consumption,
but this package does not yet qualify that transport, a live browser worker,
account reservation or original-group delivery. The configured model must be
read back from the actual runtime; `dsh` or `high` in a card is not proof of it.
The existing M1 card and result-return path stays unchanged. Disable the new
readback path by omitting its flag; rollback uses the original extension
revision and normal lifecycle. No new background worker or timer is installed.

中文：这些命令只核验金融条款与保留的场所事实，不批准、下单、重提或取得账户
权限。摘要缺字段、过期、参数变化即停止；未知提交与部分成交保留，费用缺证不补零，
成交与保护分别验收。原生批准/一次消费、DSH真实宿主、场所适配、账户总额度及原群
投递仍须独立资格化。省略新参数即保留原M1模拟路径，回滚复用原extension revision。
