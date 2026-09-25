# Bounded file outbox correctness

An active shadow captures each mutation of its bound primary once. A cursor is a
position hint: only the actual file provider's committed transaction and receipt
can authorize deletion of an outbox file. Qualification folds the complete
baseline and transaction history, then compares the current Todo, handoff mode,
and lease state. Equal final snapshots alone are insufficient.

This is a pre-promotion `file_outbox_v1` contract. Native canonical Todo keeps its
existing contract. Older file-v0 mirror or mixed histories remain unchanged,
read-only, and ineligible for this qualification; there is no automatic migration.
The separate observation handler remains available within its original scope.

## Operator lifecycle

Enable the existing per-goal `coordination.runtime_shadow` configuration with
`enabled: true` and `provider: file_v0`. Select the intended common runtime root
and state file in the registry before bootstrap. The new history binds that
source; conflicting `--runtime-root`, `--project`, or `--state-file` overrides
cannot establish a second authority for it. Default-off primary writes and the
independent observation path retain their own override behavior.
Registry-relative runtime paths also bind rollout-event logging to the registry's
owning project, including when capture is disabled and the CLI runs elsewhere.
This corrects a split-log defect; it does not activate capture for that Goal.

Use the same registry and Goal throughout:

```bash
loopx --registry registry.json --format json coordination-shadow bootstrap --goal-id goal-a
loopx --registry registry.json --format json coordination-shadow bootstrap --goal-id goal-a --execute
loopx --registry registry.json --format json authority-shadow status --goal-id goal-a
loopx --registry registry.json --format json authority-shadow drain --goal-id goal-a
loopx --registry registry.json --format json coordination-shadow inspect --goal-id goal-a
loopx --registry registry.json --format json coordination-shadow qualify --goal-id goal-a
loopx --registry registry.json --format json coordination-shadow read-candidate --goal-id goal-a --todo-id TODO_ID
```

Bootstrap imports the complete current Todo/handoff and lease baseline. It does
not count as a mutation. Qualification requires three actual primary mutations
by default; replay and proven abandonment also do not count. `read-candidate`
uses that default policy and repeats the same validator, returning from its
verified head rather than trusting a previous successful qualification.

Turning off the configuration does not cancel an already active capture
obligation. Todo/handoff/followup writers and native lease writers must still
prepare before changing their bound primary. A failed durable prepare holds
that mutation. A failure after primary replacement retains recovery evidence;
it cannot truthfully report that the primary was unchanged. No-change, preview,
idempotent retry, and prose-only changes produce no mutation receipt. Managed event batches now capture under source locks. Unmanaged event edits
remain source drift and prevent qualification; an event id alone is not proof.

To retire the candidate, obtain the exact current `provider_revision` from
inspection and preview the target before execution. If an invalid cursor or
outbox manifest blocks inspection, use `authority-shadow status` and its
read-only provider proof. If the provider itself cannot be proved, preserve the
scene and hold; do not guess a revision.

```bash
loopx --registry registry.json --format json coordination-shadow rollback --goal-id goal-a --provider-revision EXACT_REVISION
loopx --registry registry.json --format json coordination-shadow rollback --goal-id goal-a --provider-revision EXACT_REVISION --execute
```

To terminate a bootstrap interrupted before active publication, use its exact
operation identity from the management result/journal:

```bash
loopx --registry registry.json --format json coordination-shadow rollback --goal-id goal-a --bootstrap-operation-id EXACT_BOOTSTRAP_OPERATION --execute
```

The selectors are mutually exclusive. Neither is an unconditional cleanup.
Rollback preserves the candidate and the complete Goal outbox, including pending
entries, markers, and malformed cursor bytes. It publishes `inactive` only after
the archive matches the immutable manifest. The shared store identity and other
Goals are unchanged. Primary writes then resume; capture reports
`bootstrap_required`. A new bootstrap imports that current state and starts a
new capture lineage, even if its data equals the old baseline.

## Recovery and holds

Management uses `bootstrapping / active / rolling_back / inactive`. The stable
management directory is under `authority-transition/file-v0/`, outside the
outbox being archived. Its immutable manifest and intent bind the operation,
request, source, provider revision, and file hashes. Retrying the same operation
reconciles actual source/archive existence and hashes; a phase string alone
cannot prove completion. Conflicts hold without overwriting or deleting either
copy. A delayed exact old request only returns its historical result. Reusing
its operation ID with different request contents returns an identity mismatch.
Cached results must match their immutable operation manifest, lineage and archive
references. A result copied from another Goal cannot establish successful replay.

During an unfinished management operation, canonical and whole-state prose
writes return a maintenance hold before their first side effect. Management lock
order is M → T → S → L → K. Native lease writers check under their existing K;
ordinary writers release primary locks before drain. Legacy fence engagement
also takes the actual source S and therefore requires its absolute state path
in the internal RPC request.
For source owners declared in frontmatter or registered to that state path,
selecting another Goal cannot bypass the existing binding or fence. Unbound
legacy shared-state writes retain their existing behavior; prose-only writes
retain their separate maintenance boundary.

### Fenced legacy write response

A legacy Todo, handoff-mode, followup, registry-state, or native task-lease
write against a goal whose durable legacy writer fence is present is rejected
before its first primary side effect: no lease record, Markdown state,
canonical store document, outbox entry, or lifecycle-fence receipt is written.
The shared write check (`coordination.local_authority.legacy_write_check`) owns
only the stable machine reason and the fence binding facts: `reason_code`
`legacy_coordination_writer_fenced` with `authority_mode` and `fence_id` for an
engaged fence; `legacy_writer_fence_read_failed` with the technical `reason`
for a present but unreadable or invalid fence; every non-allowed result fails
closed. Each caller adapter (the TypeScript
`requireLegacyCoordinationPrimaryWriteAllowed` wrapper used by native acquire,
renew, transfer, release, every terminal or holder verify branch, and a
committed releasing fence-close; the Python
`require_legacy_coordination_write_allowed` wrapper used by every legacy Todo,
handoff, followup, registry, bootstrap, and monitor writer) maps the reason to
`error_code`, carries the complete check result unchanged under `write_check`
(never spread into its own envelope, so the envelope keeps its own
`schema_version`), and renders one provider-neutral remediation for an engaged
fence:

```
legacy coordination writer is fenced; use the promoted canonical authority
(<authority_mode>) for goal <goal_id>; fence <fence_id>; the primary record
was not changed
```

A fence that cannot be validated reports the check's technical reason
verbatim. The profile label is opaque data from the check (`file_v0` today),
never a provider path, table, or command, so a provider-neutral binding needs
no template change.

A native task-lease envelope keeps `schema_version: task_lease_v0` and its
`action`; its settlement is `{effect_id, receipts: [], failure: {step:
"validation", kind: "permission_denied", code: <reason_code>}}` at every
native guard site alike: no receipt exists because no settlement step ran, and
`permission_denied` marks the rejection terminal for this writer (RFC section
5 `rejected` and its no-fabricated-receipt rule, section 5.6, question 11,
Appendix C). `effect_id` follows each verb's existing rule: acquire reports
`null` whenever no receipt exists; a lifecycle operation reports the
settlement identity when the request carries `owner` and `idempotency_key`
and `null` otherwise.

Previews: `archive-completed` without `--execute` and `todo add --dry-run`
write nothing. After promotion, public terminal/archive commands
route to canonical authority instead of treating the promotion fence as an
error. A hard-lease terminal preview therefore rejects with
`handoff_mode_requires_lease` when its target has no lease, succeeds for a
matching lease, and may preview the declared user-gate auto-acquire path; all
three leave the primary record and receipts unchanged. Legacy-only writers
such as `todo add` and `todo update` remain fenced. A fenced
committed releasing `fence_close` releases the caller's claimed mutation lock
in `finally` while the lease stays `active` and its `held` receipt is
untouched; the caller's fence token is spent, a retry reports
`fence_token_invalid`, and recovery is lease expiry or a canonical release.

A promoted `archive-completed --execute` that selects no records is also a
provider no-op: it returns `no_change` without advancing the canonical cursor,
revision, or receipt history. Archive operation identity is bound to the
canonical source revision, so concurrent attempts over one snapshot share the
same logical operation. Projection recovery may create a missing machine-owned
User, Agent, or non-empty archive region next to an existing Todo region; it
still proves byte-for-byte preservation of all narrative outside those regions
and remains idempotent on replay. Empty successor intent is an identity at the
typed derivation boundary: it does not impose successor-only Agent registry or
completion-policy admission on an otherwise unchanged legacy terminal call.

The complete observable behaviour is pinned row by row in
`tests/fixtures/control_plane/legacy_writer_fence_caller_parity_v0.json`
(21 TypeScript entry rows, 21 real-process CLI rows; whole-object legacy
envelopes, stable-field subsets for provider-first rows, exact exit status,
exclusion-free effect snapshots, and declared after-state) and
enforced by `tests/control_plane_ts/legacy_writer_fence_caller_parity.test.ts`
and `tests/control_plane/test_shadow_fence_caller_parity_e2e.py`; the
`baseline` entries of that fixture document earlier revisions and are never
executed.

Promoted CLI acquire, renew, transfer and release rows assert provider commits and
independent lease readback; fresh acquisition C is separate from keyless A and
existing-lease B so one newly working caller does not invalidate another row's intended negative precondition. Acquisition replay verifies current proof.
The direct legacy-wire rows remain fenced. The CLI
scenario carries the validated current owner/key/version after renewal or
transfer, rejects the superseded completion proof, and previews with the current
proof before release. Release follows the active-lease/quiescence checks and
must leave completion unauthorized. Capture-enabled rows still create no second
legacy record or shadow outbox entry. When migrating an entry point, update this
caller matrix and its downstream proof flow together; focused transaction tests
or generic premerge canaries do not replace it. Run the owning matrix directly:

```bash
uv run --extra test python -m pytest -q tests/control_plane/test_shadow_fence_caller_parity_e2e.py
```

Baseline delta (0fb497af8 is the PR's diff baseline; ee1b17217 the previously
reviewed head):

| Row | 0fb497af8 | ee1b17217 | Now | Basis |
| --- | --- | --- | --- | --- |
| native acquire settlement | committed validation receipt, `durable_writeback` / `writeback_rejected` | `validation` / `writeback_rejected`, no receipt | `validation` / `permission_denied`, no receipt | the guard ran before `commitAcquire` on every revision; only the classifier drifted; RFC section 5 forbids a fabricated receipt for `rejected` |
| native renew, transfer, release settlement | `validation` / `permission_denied` | same | same | unchanged; acquire now matches its siblings |
| native error text | `legacy task-lease writer is fenced; use the canonical file authority` | `legacy coordination writer is fenced` | one rendered template | remediation belongs to the caller adapter and must not name a provider |
| Python error text | `legacy coordination writer is fenced; use the canonical file authority` | same | one rendered template | same |
| envelope `schema_version` | check schema overwrote `task_lease_v0`; every CLI lease rejection printed `RuntimeError` | same | `task_lease_v0` plus nested `write_check` | envelope-owned keys win; check result travels under its contract name |
| Python Todo rejections | no `error_code` | flat check keys, `schema_version` injected | `error_code` plus `write_check` | same |
| terminal or holder verify under a fence | fence bypassed on the held and holder branches; auto-acquire branch not consulted | auto-acquire branch guarded; held branch previews reported `ok: true` and wrote receipts | every verify branch guarded before its first receipt | a preview must not succeed for a write the fence forbids |
| committed releasing fence-close | fence bypassed | guarded | guarded; lock released in `finally`, retry `fence_token_invalid` | declared and pinned |
| public terminal/archive after promotion | legacy fence rejection | legacy fence rejection | canonical provider result; lease admission, preview, CAS, receipt and projection semantics come from the promoted authority | the fence blocks legacy writes; it is not a rejection of the canonical replacement path |
| generic CLI branch of `task-lease` and `turn` | typed payload spread after envelope keys | same | payload first, envelope keys win | `LocalCoordinationAuthorityUnavailable` carries a `schema_version` |

Known gap, unchanged on both revisions: `loopx quota monitor-poll --execute`
(and the scheduler monitor writeback it wraps) re-types every non-validation
exception to `quota_unexpected_collection_error` with reason `quota collection
failed`, so a fenced monitor writeback loses its `error_code` and remediation
at that boundary. The fix belongs to the quota domain's public-safety
contract (map the typed fence exception to its own code and pass `write_check`
through) and is not part of this batch.

Canonical FileAuthorityStore Todo updates, including compatibility v0 records
already held by that authority, use the same M and maintenance boundary as
canonical Todo creation. They retain their own transaction receipts and do not
produce legacy shadow captures. A waiting update rechecks management state after
acquiring M; invalid or unfinished management state holds before any commit.

Drain checks the real receipt first, persists the cursor second, and reclaims
each individually verified file last. Missing cursors can be reconstructed from
complete continuous history; malformed or unreadable cursors are never silently
replaced. Unknown files, mismatched bytes, wrong identities, or incomplete proof
preserve the scene. An interrupted primary without a marker is either proven
under the source lock or left `unproved`; an A → B → A history cannot establish
that the first write was abandoned. TS repeats source proof under its own locks
after Python releases its locks to call the native commit operation.
Cursor position includes settled no-ops; its digest is the verified head's last
applied partition marker. It stays null through bootstrap and an abandoned-only
prefix, even with a nonempty baseline. Recovery and qualification consume that
same marker rather than hashing the current snapshot independently.
If cursor persistence or file reclamation fails after a verified commit, drain
still reports the verified candidate revision. The preserved outbox can be
replayed after repairing filesystem access; the failure does not undo the commit.

For unresolved evidence, retain the directory for inspection and use exact
rollback when abandoning the candidate. Do not delete the cursor to bypass a
failed history check, copy an old entry into a new lineage, or reseed implicitly.

Qualification explicitly reports bounded scope and
`sustained_parity_verdict=not_evaluated`. The current proof boundary is 10,000
transactions including the baseline; new commits hold at that boundary while
exact receipt replays remain valid. This is a bounded correctness limit, not a
performance result. Drain budgets bound work between operations; they do not
preempt an in-flight filesystem or RPC call.

## Roadmap and retirement boundary

This batch belongs to capture lane C in the [Shared Goal Authority RFC](../../docs/architecture/rfcs/shared-goal-authority-state-provider-v0.zh-CN.md).
Its rollback retires a **pre-promotion candidate**. Lane F still needs a separate
fenced export/rollback after canonical writes, following lane I's exact profile,
import, consumer and recovery qualification and explicit maintainer approval.
The 10,000-transaction bound here grants no sustained parity or promotion.
Long-running local use also needs lane L: an embedded store, bounded live-state
and receipt access, accelerated capacity tests and at least ten natural days of
soak. L can proceed alongside native Todo callers without waiting for PostgreSQL P.

Retire legacy capture only after every bound primary writer has cut over to the
qualified authority and its replay/recovery consumers have cut over too. Retire
the separate observation handler only with the owner's Q14 decision and verified
replacement of its remaining callers. Python guards stay until those writers no
longer own primary effects; remove the bridge/protocol once no production caller
needs it. The next cutover batch must report deleted product LOC, added bridge
LOC and happy/recovery runtime crossings under the [TS migration RFC](../../docs/architecture/rfcs/typescript-control-plane-migration-v0.zh-CN.md).

## Required validation

Install the repository test extra and Node dependencies. Run the long tests
separately without skip or relaxation flags:

```bash
python -m pip install -e '.[test]' 'build==1.6.0'
npm ci --ignore-scripts
python -m pytest -q -n 4 --dist loadfile -m stage2c_e2e --durations=20 --junitxml=stage2c-e2e.xml
python examples/shared-goal-authority-e2e/mutants.py --output .local/stage2c-mutants
python -m build
python examples/shared-goal-authority-e2e/installed.py --artifact dist/*.whl --report-json .local/installed-wheel.json
python examples/shared-goal-authority-e2e/installed.py --artifact dist/*.tar.gz --report-json .local/installed-sdist.json
python -m pytest -q -n 2
npm run test:control-plane
npm run typecheck:control-plane
python -m mypy
python -m ruff check tests loopx/canary loopx/control_plane loopx/domain_packs loopx/presentation
python scripts/generate_coordination_state_contract.py --check
python examples/control_plane/cli-output-budget-regression-smoke.py
loopx --format json canary premerge --from-git-diff
```

The Linux/Python 3.11 CI lanes use the exact dependency versions and hashes in
`tests/requirements-stage2c-linux-py311.txt`, including pytest 9.1.1. It builds
the checked-out source wheel, verifies its hash during installation, and removes
its generated build tree before normal pytest discovery. The workflow records
the complete installation sequence and retains normal source discovery.

E2E, deliberate mutants, and independently installed wheel/sdist qualification
run in separate jobs; the stable `stage2c-correctness-e2e` check requires all three
to succeed. No path-based skipping or reduced case selection is used. E2E uses
four file-grouped workers so a module's shared workspaces and ordered parity rows
stay together. Each lane has a distinct evidence artifact. Mutants remain serial
inside their isolated source copy: parallel edits to that copy would invalidate
the control/mutant comparison. Single-row fence probes initialize only the
workspace they use, without removing any baseline or mutant assertion.

The full TS suite's PostgreSQL conformance requires `LOOPX_TEST_POSTGRES_URL`
pointing to a disposable test database. Source smoke success does not replace
installed-package evidence: the package runner creates an empty environment,
clears source-path injection, runs outside the checkout, verifies installed
Python/TS/JSON provenance, and reads back through an independent native process.

| Obligation | Retained oracle |
| --- | --- |
| Full baseline, mixed Python/native writers, handoff, todo add, monitor successor, one receipt per mutation | `test_runtime_shadow_bounded_e2e.py`, `test_shadow_drain_e2e.py` |
| Cursor attacks, complete proof before bounded cleanup, missing cursor writer-first, dual drainers | `test_shadow_cursor_safety.py`, `test_shadow_drain_adversarial.py`, `shadow_cursor_safety.test.ts` |
| Todo/lease abandoned prefixes, later mutations, all cursor consumers and forged applied digests | `test_shadow_cursor_recovery_e2e.py` |
| Full caller diagnostics, argument readback and no-effect controls with absent/disabled/enabled capture | `test_shadow_observable_e2e.py` |
| Primary and drain process death, lost ACK, prepared-only A → B → A | `test_shadow_drain_e2e.py`, `test_shadow_management_e2e.py` |
| Every bootstrap/rollback durable window, raw archive fidelity, late requests, cached-result binding and other-Goal isolation | `shadow_management.test.ts`, `test_shadow_management_e2e.py`, `test_shadow_management_variant_e2e.py` |
| Fence and maintenance boundaries, source and Goal override races, whole-file durability, paragraph injection, refresh CAS | `test_shadow_writer_boundaries.py`, `test_shadow_writer_variant_e2e.py`, `shadow_native_writer_boundary.test.ts`, `test_shadow_drain_adversarial.py` |
| Canonical native/v0 Todo updates through CLI and native RPC, real pending management, M ordering, and unchanged authority on hold | `test_shadow_native_todo_update_e2e.py` |
| History flaws despite equal snapshots, legacy mixed profile, source drift, event-only hold, qualified reads | `coordination_runtime_shadow.test.ts`, `file_outbox_qualification.test.ts`, `test_runtime_shadow_bounded_e2e.py` |
| Ladder parity half through the public CLI and management interfaces: deferred entries, bounded and idempotent drain, primary and drain SIGKILL windows, rollback with pending entries, sustained mixed-writer parity, drift, the event-only hold, migration refusal and rebootstrap, growth measurement | `test_shared_goal_authority_e2e.py` (`s2c2.*` rows), `ladder.py --stage 2c2` |
| Installed lifecycle and resource provenance in wheel and sdist | `installed.py` |
| Missing checks, lock placement, duplicate mirror, early marker, cursor regression | `mutants.py` with unchanged GREEN controls and assertion RED results |

The mandatory repair set must have zero failures, skips, pending, or unverified
cases. Broader ladder rows retain their declared pending/environment gates:
`s2c2.archive_after_leased_completion_parity` is now an executable
deterministic row rather than a declaration, and `s2c2.sustained_parity_soak`
stays pending until the Section 7.2 soak exists; these tests grant neither
production promotion nor a completed Stage 2C claim.

For a caller comparison, run both `test_shadow_observable*_e2e.py` files with
`LOOPX_SHADOW_COMPARISON_SOURCE` set to an immutable baseline checkout, then to
the reviewed source. Set `LOOPX_SHADOW_COMPARISON_OUTPUT` to separate private
directories to retain full diagnostics and before/after fixture bytes. The oracle
is unchanged: the baseline must fail cases for defects this batch intentionally
repairs. Inspect every difference; normalize only documented time, path and
generated-identity variation, and disclose changes to rejection or receipt
behavior. CI retains the reviewed source's synthetic observations alongside the
crash, mutant and installed-package evidence.
