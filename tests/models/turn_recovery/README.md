# Recovery model and reproducible checks

The implementation note is [turn interruption recovery](../../../docs/development/turn-interruption-recovery.md).
`requirements.txt` pins the Python 3.11 dependencies for its bounded verification
selection. `package-lock.json` at the repository root pins the TypeScript tools.

## Model scope

One work item, two competing actors (`1`, `2`), at most two process failures,
one original settlement identity, and a finite set of durable phases. `live`
can retain an old actor after a crash; `admitted` represents current logical
execution authority. The model does not equate physical liveness and permission.

`OneAuthority` checks the admitted set and its owner. `NoReplacement` checks
that a saved, unresolved dispatch cannot admit a replacement attempt.
`NoDuplicateEffect` and `AtMostOneSettlement` check the corresponding abstract
counters. `NoUnauthorizedAcceptance` checks acceptance under the current
abstract authorization. `Terminates` requires eventual settlement, an explicit
unknown outcome, or revocation.

Weak fairness applies to `Advance` and `Recover`: continuously enabled progress
and recovery eventually run. Storage is available and each modeled checkpoint
is atomic and retained. Failures stop after two occurrences. Authorization may
be revoked, which ends this bounded work. Without these assumptions, the model
does not establish eventual completion.

## Actions and code boundaries

| Model action | Production boundary | Assumption / omitted detail |
| --- | --- | --- |
| `Admit` | `lane_fence.py::turn_lane_singleflight`, journal lock in `run_loopx_turn_once` | Cooperative clients share functioning local locks; not distributed consensus |
| `Dispatch` | `_host_result_stage` persists attempt count before invoking Host | Host launch never precedes this checkpoint |
| `Tool` | Owned Host executes the synthetic exporter | No universal external fencing or rollback is modeled |
| `SaveResult` | `_host_result_stage` writes `host_result` and the first two completed phases | Atomic journal snapshot, no torn write |
| `Crash` | Process termination releases its kernel locks | Durable facts remain; an old physical actor may still exist |
| `Recover` | `turn_journal.ts::recoveryDecision` and `require_turn_recovery_continuation` | Saved result resumes settlement; unknown intent refuses another Host |
| `Settle` | Existing prepared-effect/readback/checkpoint owners and original receipt replay | Abstracts an atomic provider debit per retained identity; the separate provider/journal crash windows are checked by pytest |
| `Revoke` | Existing first-party Goal lifetime checks | Boolean abstraction of current authority; not a lease, scope or version engine |

The model omits context encoding, schema migration, corruption, power loss,
remote-service failure, arbitrary tool APIs and a universal generation fence.
It cannot establish those properties for code. In particular, its settlement
abstraction does not make the provider and journal one transaction. The
subprocess suite tests their distinct checkpoints. Native context qualification
has a separate retained failure, documented in the implementation note.

## TLC commands

Use Java 17.0.17. The tool fetched for this run reports
`TLC2 Version 2026.10.26.231022 (rev: fad899c)`.
The download path is a release asset, but its observed SHA-256 is the pin:

```sh
curl -fL https://github.com/tlaplus/tlaplus/releases/download/v1.8.0/tla2tools.jar -o tla2tools.jar
printf '%s\n' '78f7e2c6bf9a8b65eda665a23e94431255587e0b43a834a4e2af1723562d9b4a  tla2tools.jar' | sha256sum -c -
cd tests/models/turn_recovery
java -Xmx256m -XX:+UseParallelGC -cp ../../../tla2tools.jar tlc2.TLC -seed 7416 -fp 0 -workers 1 -metadir ../../../recovery-model-safe -config TurnRecovery.cfg TurnRecovery.tla
java -Xmx256m -XX:+UseParallelGC -cp ../../../tla2tools.jar tlc2.TLC -seed 7416 -fp 0 -workers 1 -metadir ../../../recovery-model-unsafe -config Unsafe.cfg TurnRecovery.tla
```

The safe run explored 80 distinct states (160 generated), depth 10, and completed
its invariant and temporal checks. This supports the stated finite model only.
`Unsafe.cfg` removes the unknown-outcome guard. Its expected failure is exit 12
at `NoReplacement`, with this seven-state counterexample:

```text
new -> Admit(1) -> Dispatch(attempt 1) -> Crash
    -> Recover(new) -> Admit(1) -> Dispatch(attempt 2)
```

The unsafe run generated 84 states and found 55 distinct states before stopping.
The production mutation check independently removes the corresponding guard
and requires real assertion failures. Its other mutations test stale Goal
instances, current stop authority, settlement deduplication and the original
export requirement. Neither a model counterexample nor an assertion failure
is a human maintainer's approval.
