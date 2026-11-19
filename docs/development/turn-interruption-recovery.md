# Bounded Turn interruption recovery

This implementation addresses an interrupted `turn run-once` on the same
original Turn. The export failure used in tests is synthetic. Its accepted
requirement is to preserve input row order, including equal keys. It is not a
historical incident. No controller/executor split or scheduler is introduced.

## Source and scope

Initial baseline: `47a525dcc8ca943c7e4ab39834c92e905892839c`, read 2026-10-27 04:56 UTC.
Implementation rebased on `218aa087b66bb99038b1ce7d782110741a5e42ae` at 05:23 UTC.
Design: [RFC #7416](https://github.com/loopx-project/loopx/pull/7416),
head `9d2d022d14732b3b2dac309013fe9538b1d9ba5d`,
base `99921829f2c5fa2ad35a024c72abb88717003ef3`,
merge `b3ce2cfe77dc99136411279c7bdb2f4db87f37f4`.
Its English and Chinese recovery, continuity, stop and settlement clauses are
requirements. They do not certify an implementation.

#7420 and #7426 are ancestors of this baseline through their respective merges
`d5d72cad4b4b8b6ecbb89682fd9ab161efcfb7b3` and
`0bd508b9445cb60544f5c9f69a07e0a1b72a0d23`.
#7430 (initial `411707c067e70493c08ce23079af6e2eedb03e96`; refreshed
`89d5f0717bfcb1c88231847a8b737cbc0365b096`, then
`275e59c25686f9e353e3c5966b4d06c289a6ed0b`) is
unmerged; none of its complete-contract claims are inherited here.
The existing #3412 proposal concerns prepared settlement effects. This change
concerns an earlier Host dispatch intent, before a typed result exists, and the
current-lifetime preflight on original-journal recovery. #6838 shares the existing
legacy launch helper but does not add this executor recovery callsite. No new
permission engine or admission schema is added. The executing CLI now passes
the existing legacy lifetime admission to the driver as well as the Host. The
driver reuses `launch_lifetime` for both source instances and legacy registrations
before recovery, without a new quota admission. Preview retains its no-capture
behavior.

The revoke-after-result counterexample found that the CLI discarded legacy
admission before calling the driver, and `require_current()` deliberately
returns without checking legacy registrations. Consequently, a cached result could
write back and spend after its Goal was retired during the interruption. The
new recovery callsite checks its current registration/activation before journal
continuation. Source-session recovery retains its existing exact lifetime check.
This is a recovery preflight, not continuous fencing of legacy in-flight tools.

| Requirement | Responsible code | Durable record | Check | Gap / boundary |
| --- | --- | --- | --- | --- |
| Same original work and settlement | `turn_driver/driver.py`, `turn_journal.ts` | Saved plan, Turn key, settlement identity | Identity drift and replay checks | A new task/revision is not an original-Turn retry |
| Complete original context | `decision_capture.py`, `quota/context_delivery.ts` | Immutable capture and session-bound delivery | Hash/readback and full/delta tests | A reference alone is not delivery |
| One dispatch after restart | `executor.py`, `turn_journal.ts` | Attempt count before Host call, result checkpoint | Kill/restart and independent downstream log | Added refusal when the outcome is unknown |
| Current authority | `first_party_host_admission.py`, `first_party_host_runtime.ts` | Registry lifetime/witness and current admission | Recreated/revoked Goal checks | Restoring capture cannot grant authority |
| No duplicate settlement | `settlement.py`, `effect_program.ts` | Prepared effect reference, provider receipt, journal | Provider readback and original-identity replay | Existing mechanism; external tools are separate |
| Physical stop | `host_process.ts`, `host_process_transport.py` | Optional process record and OS group observation | Child termination and stop readback | Not a sandbox or external service fence |

## State machine and commit boundaries

State names remain the existing journal vocabulary. Unknown is a recovery
decision (`blocked`, reason `host_outcome_unknown`), not a successful result.

| Trigger / owner | Durable state after atomic checkpoint | Crash observation / next action |
| --- | --- | --- |
| Executor creates journal | `in_progress`, attempt 0, no phases | No dispatch was authorized; continue the original attempt |
| Executor saves dispatch intent | `in_progress`, attempt > 0, no typed result | Delivery/execution is unknown; refuse automatic redispatch |
| Host returns; executor saves result | `in_progress`, `host_execute, typed_result`, saved result | Validate saved result; do not call Host |
| Validator accepts | Append `validation` | Continue original settlement |
| Settlement prepares a provider effect | Existing `effect_attempts` with original effect reference | Query provider; committed means reuse; absent means retry same reference; unknown means stop |
| Provider checkpoint commits | Append `durable_writeback` / `quota_spend` | Resume only remaining phases |
| Scheduler closeout commits | `committed` or `stopped`, original receipt | With a current Goal lifetime, return retained receipt without another debit |

The journal owner checks transitions and writes a temporary file then replaces
the journal under mutation locks. Provider commits and the journal checkpoint
are separate transactions. Their gap is resolved by provider readback, not by
pretending the transactions are atomic together. Process-kill tests establish
restart behavior; they do not establish power-loss durability or directory-sync
ordering.

## Identities, invariants and continuation

Work is the Goal and selected Todo/obligation plus its accepted source revision.
The original settlement identity is Goal, Agent, binding and Turn instance;
its `effect_id` is independent of the host attempt counter. A native session
identifies conversation continuity, not permission. Context epoch/profile binds
retention. Goal instance and delegated lease epochs fence their existing owned
boundaries; this patch does not invent a universal execution generation.

Executable assertions must establish:

1. An unresolved dispatch intent never starts a replacement Host, even with
   `--retry-failed-turn`. Unknown/invalid attempt evidence cannot prove absence.
2. A saved typed result resumes validation/settlement without another Host.
   Replaying a terminal receipt does not produce another settlement.
3. Recovery preserves the original requirement and immutable capture; current
   authority is checked separately. Missing/corrupt context uses existing full
   recovery or refuses dependent work.

The lane and journal locks serialize cooperative writers on one local runtime.
They are not an external fencing token. This slice has no replacement executor
while an original dispatch outcome is unknown. It does not claim that an
arbitrary detached process, remote tool, or another newly admitted Turn cannot
write. A generic Host with unqueryable, non-idempotent tools cannot be safely
replayed just because its process has exited.

Under available storage and valid current authority, proven non-dispatch
continues; a durable result finishes its remaining settlement; a terminal Turn
returns its receipt. An unknown Host outcome returns a finite refusal requiring
operator/provider reconciliation. There is no polling loop or retry budget
that eventually guesses absence. This patch adds no generic reconciliation
API for arbitrary Host tools. Do not clear the journal or choose another Turn
identity to bypass that refusal.

## Compatibility and rollback

No new schema or ID is introduced. New initial journals explicitly persist the
existing attempt counter as zero. Historical incomplete journals without that
counter fail closed because non-dispatch cannot be proven. Terminal journals
and journals with a saved typed result retain their recovery route while the
Goal remains active. Retired Goals have no executing-recovery compatibility
path, including terminal receipt replay. Refusal preserves the original journal,
provider receipts and committed debit; `inspect-journal` remains read-only.
No retirement fallback, alternate identity or registration migration is added.
Explicitly recorded failed-Host retry policy is unchanged and is not qualified
as safe replay of arbitrary external effects by this change.

Old versions can parse these records but lack the new refusal. Therefore a
downgrade must first reconcile or quarantine unresolved Host intents; do not
silently resume them with the old binary. Captures/receipts are never rewritten
to simulate continuity. At-most-once settlement depends on provider atomic
idempotency/readback and retained history. Exactly-once arbitrary external
effects is not promised.


## Fault evidence and qualification limits

The original measurements in this section and `evidence.json` describe head
`335eae25f2544456c9bb644ea5db311210f747e0`. They remain historical evidence;
their source hashes do not certify later integration repairs. Fresh integration
validation is recorded separately below and in the PR description.

All experiments use disposable workspaces. `test_turn_interruption_recovery.py`
pauses the real CLI at a journal boundary, sends SIGKILL, and starts another
process with the original `--resume-turn-key`. The Codex executable is an
injected deterministic task worker. It repairs a deliberately broken exporter,
reads the original requirement, and calls a separate downstream process. That
service commits every operation to its own SQLite database without deduplication.
Two normal controls use the installed CLI and the same inputs, requirement and
independent CSV oracle as the fourteen interruption cases.
The independent oracle reads that database and the CSV directly. File and SQLite
refer to LoopX authority backends; the Turn journal itself remains a JSON file.

| Boundary / race | Observation required after restart | Evidence type |
| --- | --- | --- |
| Intent durable, delivery unknown | No Host redispatch or debit, unchanged journal | Real CLI SIGKILL, both providers |
| Export committed, Host result not saved | One downstream operation, unknown refusal, old Host drained | Real CLI SIGKILL, both providers |
| Typed result durable, before validation | Original artifact/result reused, one settlement | Real CLI SIGKILL, both providers |
| Writeback committed, checkpoint absent | Original provider readback, no repeated Host/export | Real CLI SIGKILL, both providers |
| Quota committed, checkpoint absent | Read original debit, one spend record | Real CLI SIGKILL, both providers |
| Terminal commit, response lost | Replay original receipt, one spend record | Real CLI SIGKILL, both providers |
| Authority retired during interruption | No post-restart writeback/debit, evidence preserved | Real CLI SIGKILL, both providers |
| Terminal commit, then Goal retired | Refuse executing replay; preserve journal, one original debit and downstream operation | Real CLI SIGKILL, both providers |
| Old Goal generation returns a result | Reject result before journal/writeback | Existing source-lifetime driver tests and owner mutation |
| Context baseline lost/corrupt/reordered | Same-capture full, current control facts retained | Existing TS codec checks; native qualification below failed |
| Tool still runs when owner disappears | Observe real process termination separately from effect records | Existing process-supervisor tests and downstream readback |

The Hypothesis `RuleBasedStateMachine` generates dispatch, intent/result/spend
faults, recovery, repeated responses and Goal generation invalidation. Its
expected counts follow chosen actions, not the implementation's result fields.
It uses real journals and a separate SQLite receipt ledger. It runs 30 deterministic
examples of up to 12 actions with Hypothesis 6.142.4. These process-local faults
complement the SIGKILL matrix; they are not substitutes for it. The initial
shortened failure was a fixture `journal_path_mismatch`, corrected by using the
source registry's canonical runtime root, not by weakening a production check.

`check_turn_recovery_mutations.py` creates temporary sibling copies, removes one
protection, and requires assertion failures. It checks unknown dispatch, old Goal
generation, current stop authority, settlement deduplication and a dropped original
requirement. Parse/import errors do not count as detected mutations. The requirement
mutation probes the independent oracle; the other four mutate production owners.

Native Codex 0.164.0 with deterministic loopback inference **did not qualify**.
On baseline `218aa087b66bb99038b1ce7d782110741a5e42ae` and earlier candidate
`9d9b1183489164246c4ae770a06a8d43b28b00ef`, two compaction/process-resume
cases failed with missing `candidate`, and four history-change cases failed the
complete model-visible JSON assertion. No paid model ran. Initial missing-binary,
missing installed CLI and sandbox socket refusals are setup failures, not product
passes. The same six later semantic failures are retained. The subsequent legacy
recovery repair does not inherit native qualification from those earlier runs.
The final source hashes qualify the bounded tests listed below, not a final
native-profile pass. This patch does not
repair or qualify the unified writer proposed in #7430. Native installation,
all-host context continuity and model outcomes remain outside the demonstrated
claim, even though the injected-worker CLI path passes.

No independent maintainer or third party has yet signed off. Automatic Worker
routing was unavailable in this session; the same lead wrote the patch and the
separately specified oracle. Oracle code independence is not personnel independence.
A maintainer must review journal/provider atomicity, historical records, source
lifetime fencing and unknown effects before adoption.

## Reproduce from a clean checkout

Use the implementation PR's exact head commit, Python 3.11, Node 26.12.1 and Java
17.0.17. The qualification dependency file pins every Python package used by this
bounded selection; `package-lock.json` pins the existing TypeScript tools.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r tests/models/turn_recovery/requirements.txt
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q tests/test_stage2c_dependency_lock.py tests/test_loopx_turn_executor.py tests/control_plane/test_turn_host_decision_capture.py tests/control_plane/test_turn_interruption_recovery.py tests/control_plane/test_turn_recovery_state_machine.py tests/control_plane/test_host_process.py tests/control_plane/test_first_party_host_runtime.py tests/control_plane/test_effect_program_fault_replay_matrix.py --basetemp=./recovery-results
node --experimental-strip-types --test --test-isolation=none tests/control_plane_ts/turn_journal.test.ts tests/control_plane_ts/first_party_host_runtime.test.ts tests/control_plane_ts/effect_program.test.ts tests/control_plane_ts/turn_settlement.test.ts tests/control_plane_ts/context_delivery.test.ts
.venv/bin/python tests/control_plane/check_turn_recovery_mutations.py mutation-results.json
npm ci
npm run typecheck:control-plane
```

Run mutations serially after the ordinary checks; they create and remove temporary
sibling modules. Each process-fault case saves `before.json` and `downstream-before.json` before
termination. It also writes raw evidence into its pytest `tmp_path/evidence`: original journal, recovery response, downstream records,
artifact and settlement count. `--basetemp` must name a disposable empty directory;
pytest replaces an existing directory at that path. A failing run is not a pass.
SIGSTOP/SIGKILL qualification is POSIX-specific; Windows is not qualified here.

To reproduce the native **failures**, set `LOOPX_NATIVE_CHECKPOINT_BIN` to an explicit
Codex 0.164.0 binary and run the two named test functions in
`tests/control_plane/test_codex_context_capture.py`: `test_native_compact_recovery_survives_process_resume`
and `test_native_worker_resume_history_change_reads_current_full`. The fixture
uses loopback inference, an isolated Codex home and an installed source-checkout
CLI. Do not point it at an existing user session or a paid provider.

The bounded model and its action-to-code mapping are in
[`tests/models/turn_recovery/README.md`](../../tests/models/turn_recovery/README.md).
For the external-effect distinction, see the primary
[AWS idempotency guidance](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/).
For the stronger storage test category that this work does not perform, see
[SQLite crash testing](https://www.sqlite.org/testing.html#crash_testing).


## Claims and remaining work

The checked snapshot is [evidence.json](../../tests/models/turn_recovery/evidence.json).
It contains the source file hashes, all sixteen downstream/settlement observations,
model result, retained failures, and the complete set of 38 open PR heads from
the final overlap check (706 paginated changed-file records). The implementation
baseline is fixed above. The later observed target `bafbafc258de72029b54ea016103c0a7bd2409c9`
adds two commits in runtime projection routing and its tests; the submission
refresh observed `71057d44f0d50a3636fcd8796f7c98717ce41742`. These later target commits are not silently
included in this qualification snapshot. The PR targets `main`; integration CI
and maintainer review remain separate.

| Claim | Evidence | Applies to | Not established |
| --- | --- | --- | --- |
| Unknown Host outcome cannot automatically relaunch | Intent/tool SIGKILL cases, unchanged journal, independent downstream log, guard mutation | Resume of the original Turn under the patched driver | A fresh unrelated Turn or an old binary cannot be fenced by this patch |
| Retired Goal cannot resume cached-result writes | Revoked File/SQLite cases retain one export and zero spends | Current lifetime preflight at this CLI recovery boundary | Continuous fencing of legacy in-flight tools |
| Saved result and original receipt can finish without another debit | Result/writeback/spend/receipt cases and duplicate replay, exact original effect refs | Existing supported provider readback and retained journal | Exactly-once arbitrary external tools or power loss |
| Original sorting requirement remains available and satisfied | Original capture equality, actual delivered prompt, material-read file, CSV and independent service records | Synthetic export repair, normal versus interrupted execution | Real-model comprehension or a separate requirement-edit experiment |

The final process matrix passed 16 cases. The broader clean-environment selection
passed 173 cases and initially failed two new normal controls because their test
command used unsupported `python -m loopx`; the installed CLI correction then
passed both controls and the entire final matrix. Do not relabel the earlier
failed command as a pass. TypeScript owners passed 138 checks, including context
full fallback and prepared-effect outcomes. The separate delegated-lease process selection passed 16 cases, including renewal
faults, deadline/identity retention and control-pipe cleanup. Reproduce it with
`.venv/bin/python -m pytest -q tests/control_plane/test_leased_host_process.py`.
The fixed dependencies were installed
into a new environment without system site packages and passed `pip check`.

An isolated maintainer should now rerun the listed commands at the PR head and
review the original-journal authority binding. Full native continuity still has
six reproduced baseline/candidate failures. A real model, independent personnel
review, third-party execution, Windows process faults, power-loss testing and
continuous external fencing remain unqualified. The module's bounded proposal
is reviewable; the complete all-host RFC acceptance is not claimed.


CI subsequently caught two dependency-integration errors at `4472325f287a`:
the existing Stage 2C hash lock did not yet include Hypothesis, and this
qualification profile had pinned Pygments 2.19.2 from the original environment.
[GHSA-5239-wwwm-4pmq](https://github.com/advisories/GHSA-5239-wwwm-4pmq)
affects versions below 2.20.0. The profile now uses the repository's existing
2.21.0 pin. The Stage 2C lock adds only Hypothesis, sortedcontainers and their
published distribution hashes, without unlocking other packages or adding a
security exemption. After correction, a fresh environment passed the full
176-case selection (including lock coverage), the seven kernel invariant checks
and the complete kernel ruff scope. Earlier results remain historical; the JSON
contains the corrected dependency-file hashes and fresh sixteen-case records.
The separate 16-case lease run predates this formatter-only dependency correction
and is not relabeled as a run under the corrected environment.


The next dependency-review run also rejected the verifier environment's explicit
Setuptools 83.0.0 pin under
[GHSA-xmj3-9gf7-h52w](https://github.com/advisories/GHSA-xmj3-9gf7-h52w).
The verifier environment now pins the patched 84.1.0. Project installation uses
normal PEP 517 build isolation, which retains this baseline's existing
`pyproject.toml` build-system pin. This does not upgrade the repository's default
build tool or duplicate #7205/#7206; those PRs own that separate change. Windows
legacy wheel installation is not qualified here. The new default-build browser
failure is a Goal drawer geometry assertion in unchanged UI files; its cause is
unverified, and this recovery patch does not modify UI padding or test thresholds.

The isolated verifier was recreated with Setuptools 84.1.0 and Pygments 2.21.0,
then the complete 176-case selection passed again. The sixteen raw subprocess
records and dependency-file hash in `evidence.json` now refer to that run.
The UI build failure matches the existing `typed-actions.mjs` change in #7427
(`dc16fce9a8b34a073cc664643c7afd20201b2592` at inspection); it remains a separate
integration hold, not a recovery-module pass or an authored UI repair.

## Main integration and lifecycle support

The integration repair merges main
`15471f8e832d7fb5893539fa053170ea233de5a7` through
`c3c647d1409e2abe997304f9fcf7bbe2cb247763`. It retains main's
`prepare_turn_host_request` extraction and the recovery admission capture.
Recovery then delegates both source-instance and legacy-registration checks to
the existing `launch_lifetime` owner, instead of branching again in the executor.
It adds no lifecycle owner, migration or retired-Goal replay exception.

Using the same AST metric, original head `335eae25f2544456c9bb644ea5db311210f747e0`
and the integration baseline both have 92 statements and 56 decision points in
`run_loopx_turn_once`. Runtime repair
`fc9b182a5ce0094c3f698a9ef590677a1a62054a` has 90 statements and 55 decision
points. The original failure is retained; the unchanged 90/60 limits now pass.
The full maintainability ratchet passes without a budget or exception change.

The process matrix retains active-Goal terminal replay and adds retired-terminal
refusal for File and SQLite. It requires the original journal and single committed
debit to remain unchanged, alongside the independent downstream-operation check.
The refusal is the supported lifecycle boundary; it is not a compatibility gap
to fill with another branch.

Fresh bounded checks use Python 3.14.8 and Node 24.21.0: 139 TypeScript tests,
control-plane typechecking and all five mutation probes pass. An extra two-file
mypy invocation recursively includes the dependency graph and fails with 5,417
errors in 617 files on both the immutable integration baseline and repair.
Comparing the complete diagnostics with source line coordinates removed finds
no additions or removals. This is not a passing whole-repository Python typecheck.
The PR description records the final Python and premerge results. The model and
six historical native failures above are not rerun or newly qualified by this
bounded integration repair.
