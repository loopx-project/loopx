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
