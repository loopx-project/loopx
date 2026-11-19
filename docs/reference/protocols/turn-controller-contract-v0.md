# Shared Turn vocabularies and controller decision contract

`loopx/control_plane/turn_loop_controller_contract_v0.json` owns the Turn result,
route and loop-disposition value sets, the route projection, and the complete
ordered controller rule table on qualified inputs. Generate its Python and
TypeScript bindings with:

```sh
uv run --extra test python scripts/generate_turn_contract.py
uv run --extra test python scripts/generate_turn_contract.py --check
```

The generator validates every input before writing. `--check` is deterministic
and never repairs a stale artifact. Do not edit generated files manually.

## Runtime adoption and compatibility

Python `transaction.py`, `driver.py` and `loop_controller.py` import the generated
enums and retain their previous import paths as compatibility exports. TypeScript
`settlement.ts` imports and re-exports the generated `TURN_RESULT_KINDS` and
`TurnResultKind`. The three value sets and every existing spelling remain
separate; persisted plans are not rewritten.

The public projection is:

```python
from loopx.control_plane.turn_driver import LoopXTurnRoute, project_turn_route

assert project_turn_route(LoopXTurnRoute.BLOCKED).value == "wait"
```

Every route is covered. `wait` and `blocked` both project to `wait`.
`contract_error` raises `ValueError`; it is not a disposition. Errors on mapped
routes remain failures in the semantic verifier.

## The decision is not a result-kind string map

There is no unary result-kind-to-route projection. The existing TypeScript
`quota/turn_envelope.ts::turnEnvelopeRoute` qualifies the fresh quota envelope
into a route; the Python driver and loop controller consume that same decision.
The controller then evaluates:

`decide_loop_disposition(receipt, quota_decision, predecessor_turn_key, budget)`.

For example, the same `validated_progress` result can continue immediately,
require replan after exhaustion, yield to a user gate, or require a capability
adapter. A string-to-string result map would discard these inputs and precedence.

The generated contract contains 29 ordered rows: 10 admission checks and 19
returns. Conditions use finite partitions; the first matching return wins.
The existing Python controller remains the sole evaluator of these ordered
disposition rows. TypeScript owns envelope route qualification and the shared
data/types; it does not implement another receipt/budget disposition engine.

Receipt settlement/completion qualification, budget construction and retry
normalization retain their existing owners. Python preserves the historical
scalar/numeric codecs used by persisted Turn identities; TypeScript owns route
and envelope-signature interpretation. Plan projection returns route and
signature together, and managed-step consumes an immutable local envelope/route
result from one producer call. An extra field in a wire dictionary cannot carry
that qualification; ordinary or historical dictionaries still use the shared
route reader. The JSON specifies when those checks run; it is not a
self-contained validator for arbitrary raw objects. Rejection is part of the
function contract, not another enum value. No host launch, quota spend or state
write is authorized by a controller output.

## Production evidence and generated provenance

The semantic guard calls the actual controller with fixed qualified inputs and
independently specified expected outcomes and refusals. It also exercises the
public projection. Owner declarations and values merely appearing in JSON do
not establish liveness. Unknown static flows remain explicit.

The generated Python and TypeScript files deliberately share the same basename.
Raw physical twin counts remain visible. Only this fixed pair, after both files
match deterministic regeneration from the shared contract, is excluded from the
count of independently maintained twins. A filename alone grants no exclusion;
stale bindings or an additional hand-maintained pair fail the existing gate.
Inventory and retirement budgets are unchanged.

## Reviewed semantic inventory exceptions (Q7)

Q7 adopts `evaluate_maintainability_findings(findings, reviewed_exceptions=...)`
from `loopx.canary.maintainability_ratchet`. The existing semantic drift smoke
adapts each inventory budget overrun into a stable
`semantic_inventory_budget:<metric>` finding, with the actual metric and its
unchanged target. It does not share canary's module/dependency findings or targets.

The code-owned `REVIEWED_SEMANTIC_INVENTORY_EXCEPTIONS` map is empty by default;
no current exception or budget increase is justified. Future reviewed entries
must include nonempty `reason`, `retirement_plan` and exact `metric_ceilings`.
Unreviewed overruns fail. Invalid or stale entries and growth beyond the reviewed
ceiling fail through the existing evaluator. Returning within the target removes
the finding and makes the old exception stale; retire the exception as well.
With no exceptions, the previous inventory budget pass/fail behavior is retained.

This lifecycle covers inventory budget findings only. Anchor equality,
freshness, owner/value/production checks, retirement budgets and module-twin
checks remain independent. Generated twins remain verified derivations, not
reviewed temporary waivers; byte-freshness verification cannot be bypassed by an
exception. Q2/Q10 retain three vocabularies and all existing spellings. Reverting
the Turn generation batch restores its prior owners and resources together;
no state migration is required.

### 语义清单评审例外（Q7）

Q7 已采用 `loopx.canary.maintainability_ratchet` 的既有评估器。
语义漂移 smoke 将每个清单预算超限转换为稳定的
`semantic_inventory_budget:<metric>` 发现，保留实际指标和未修改的债务目标，
不与 canary 的模块/依赖发现或目标合并。

代码拥有的 `REVIEWED_SEMANTIC_INVENTORY_EXCEPTIONS` 默认为空，当前没有合理的
例外或预算上调。未来评审条目须包含非空 `reason`、`retirement_plan` 和精确的
`metric_ceilings`。未评审超限、无效条目、过期例外及超过评审上限的增长均失败。
指标回到目标以内后，发现消失，旧例外也必须删除。例外表为空时，预算检查的通过/
失败结果与此前一致。

该生命周期仅覆盖清单预算发现。锚点相等性、新鲜度、owner/值域/生产验证、退休
预算及模块孪生检查保持独立。生成孪生属于已验证派生，不是评审后的临时豁免，
例外不能绕过逐字节新鲜度证明。Q2/Q10 仍保留三套词表和既有拼法；回滚生成批次时
一起恢复先前 owner 与资源，无须迁移运行状态。
