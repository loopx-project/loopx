# One writable shadow lineage

- Inventory baseline: `d64c4d377` (`main`), 2026-09-24.
- Goal: #4574 R5/G2, shared authority §12 question 14, TS T2/T4.
- Gap: post-commit observation still writes a second history after transaction-bound outbox capture shipped.
- Outcome: remove the obsolete writer, expose retirement at configuration/CLI/runtime/settings boundaries, preserve historical reads and require explicit replacement bootstrap.

## Reconciled delivery plan

Do not reuse the old “5–8” or “7–9” ranges. They mixed new implementation,
open PRs and qualification into one number. This snapshot has **four proposed
new implementation batches including this retirement**, alongside already-open
work. After this PR, **three named batches remain planned**; that is a delivery
plan, not proof that precisely three future PRs will suffice for every Goal.
Split a batch only when a demonstrated integration defect requires it, and
record that defect rather than keeping an unchanged numerical range.

| Batch | Observable completion | Current boundary |
| --- | --- | --- |
| Retire duplicate observation | No post-commit resampling or second-store writes; old settings cannot enable it; historical data survives; explicit outbox bootstrap works. | Independent cleanup of the already-shipped capture owner; no provider-default claim. |
| Executor liveness | A real Host renews during execution and is cancelled on fence loss; expired/reclaimed execution cannot settle as its successor. | Current acquire/readback is point-in-time proof. RFC §12 question 6 remains open. External systems still need their own effect identity/fencing. |
| Event capture + whole-Goal journey | Bind actual event publication to source locks/outbox lineage; verify mixed writers, drain, reviewed promotion, consumer reads, export/rollback. | Reuse #5003; preserve `event_log_writer_not_bound` until binding is proved. |
| Default onboarding + bounded Python retirement | Qualified profile selected consistently by new Goal creation/settings/install; explicit migration for existing Goals; remove switched business writers. | Depends on integration and applicable D1–D3 gates. Python host IO/rendering/import-export need not disappear. |

Existing open implementations are tracked separately: #5006 complete-source
transport, #5003 atomic event-owned completion, #4994 leased handoff continuation,
#4995 Monitor proof projection, #4991 reservation cleanup, #4992 deferred receipt
selection, and #4931 SQLite retained-proof optimization. Do not propose these
again. #4915 filesystem placement is not authority-provider selection.

Merged source assembly/capture delivery (#4967/#4968), canonical pagination
(#4922), reviewed cutover/drain planning (#4888/#4920), SQLite admission (#4960)
and display recovery (#4961) are existing implementations, not missing projects.
#5006 is still open at this baseline: its larger source transport should be
integrated, not copied into this independent retirement PR.


SQLite D2 is evidence, not an invented PR allocation. #4224 reports failed
1 MiB receipt/scan budgets and missing workload/RSS/recovery/restore/runtime/soak
coverage; #4931 addresses proof encoding but does not certify all those rows.
At least ten days of natural soak, exact-profile qualification and D3 cohort
approval remain separate. File-only cutover, qualified SQLite default and
migration of all existing Goals are different acceptance scopes. PostgreSQL
continues to reuse the typed contract while deployed transport, tenant policy,
operations and service qualification remain medium-term work.

## Semantics and evidence

[Operator transition](../../../../reference/authority-observation-retirement.md)
covers rejection, clearing, bootstrap and rollback. Primary Todo/lease rules,
source checks, event holds and active capture bindings do not change. The old
settings entry is read-only in both languages; stale clients fail before writes.
The old RPC is a typed rejection tombstone. Historical codecs and reads remain,
but Python post-commit projection/retry and TS observation commit are deleted.
State migration retains its response field with `outcome=retired` and `attempted=false`.

Validation exercises public CLI replacement bootstrap, lease writes with old
and absent settings, invalid-setting cleanup, retained-store readback, and
process death between source replacement and committed marker followed by
single-entry recovery. Existing transaction, source-fencing and configuration
suites remain the oracle; tests for intentionally removed observation behavior
are replaced rather than preserved as a second implementation.

A witness-checked detached snapshot of the real local Goal exercises Todo add,
retired-setting clear/readback and unchanged historical bytes. All writes target
the disposable runtime explicitly; private source content is not published.
This proves the affected upgrade path, not full migration or D2 qualification.
