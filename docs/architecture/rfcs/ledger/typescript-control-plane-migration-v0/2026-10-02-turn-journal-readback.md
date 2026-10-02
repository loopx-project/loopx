# One owner for settlement-addressed Turn journal readback

The existing Turn journal owner now supplies both delegation recovery lookup and
completion capability evidence through one native File query. This advances
roadmap S2/S3/S10 and the composition RFC's typed recovery boundary; it does not
qualify the complete ownership → writeback → settlement journey or provider
acceptance. [中文镜像](2026-10-02-turn-journal-readback.zh-CN.md)

## Delivered boundary

Previously Python separately selected journals for recovery and capability
readback. Recovery skipped unreadable files and checked less lineage than the
capability reader. A lost Turn reply followed by unreadable history could be
mistaken for absence, allowing the delegation caller to select a fresh Turn.

`turn_journal_query.ts` owns scanning, structured settlement matching,
uniqueness and evidence selection. It imports the existing settlement decoder
and journal inspector. A readable, consistent in-progress journal remains a
recovery address; only terminal replay-legal history lends capabilities. Invalid
or opaque history cannot establish absence or uniqueness. Identifiable unrelated
Turns and non-journal sidecars remain irrelevant. The two Python public functions
are transport/result adapters; no Python selection rule remains.

The related refactor moves the writer's existing status/phase constraints into
`journalPhaseViolation`, shared by inspection and write admission. Impossible
combinations now also block `turn inspect-journal`, recovery selection and
capability evidence. No state enum or persisted schema is added. The new query
methods and conflict/inspection diagnostic remain local to the journal owner.
Malformed non-string capability declarations no longer become string evidence.

## Evidence and limits

Independent counterexamples cover unreadable history, identity/envelope conflicts,
invalid phase order, contradictory status/phase pairs, foreign settlement fields,
delimiter collisions, duplicate identity in both filename orders, invalid UTF-8,
non-regular files and scoped identities rejected by the existing Todo-only
journal binding. Real File writer checkpoints are read back at each legal prefix;
queries leave bytes and directory contents unchanged.

The real delegation/CLI/Turn journey uses an isolated fixture host: commit the
Turn, lose its reply, damage its journal, retry the same operation, restore the
fixture's original bytes and recover. Host invocation remains one throughout;
the blocked operation exposes `recovery_required` and its error. The scenario
runs with File and SQLite coordination authority while the Turn journal remains
File. The completion CLI retains gated/ungated successor selection behavior.
These fixtures make no model-quality, PostgreSQL or power-loss claim.

Migration measurements use base `31480ca039d36fcb07d6e6d8447082bf803d4a96` and the
same 64-file synthetic capability-read workload, 16 warm samples per arm. Unique
Turn ids retain one runtime request: base/head p50 15.72/18.50 ms and p95
34.67/36.06 ms. Reused Turn ids across distinct Todos drop from 64 requests to one:
p50 846.91/17.79 ms and p95 1015.74/34.14 ms. These are component query timings,
not full CLI, production SLO or sustained-scale qualification.

Migration economics: production code is +232/−244 lines (net −12), including
219 removed Python lines and 31 added Python transport/compatibility lines.
Validation/configuration is +358/−11 lines; docs are separate. The shared TS
status-rule extraction is a move, not Python deletion payoff. The PR records
exact final test, package, semantic-check and premerge results. Temporary benchmark and baseline
comparison scripts remain local. Durable invariant and real-entrypoint tests
remain in the repository. The Python query facades exit when delegation and
completion callers run in-process TypeScript and their Python API compatibility
window closes.

Two legacy characterization expectations were corrected against the existing
writer invariant: stopped-before-validation and failed-without-phase remain
retained but blocked. The pinned base incorrectly reports both replay-legal;
six status/phase counterexamples independently check writer/read agreement.

## Compatibility and rollback

No configuration, frontend control, receipt, journal format, authority provider
or migration changes. Existing delegation error/readback and CLI inspection
surface blocked histories. Default lookup now refuses ambiguous/incomplete
history instead of choosing a fresh Turn; capability reads return no evidence
on such failures. Restore verified history and resume the original operation;
do not delete receipts or change ids to force progress.

A scan reads atomic file versions, not a directory-wide snapshot or authoritative
provider absence. Execution single-flight, current permissions, lease fencing
and commit-time checks stay with their existing owners. Historical evidence
cannot grant current authority. Reverting this code restores the earlier readers
without rewriting data, but also restores the demonstrated weaker checks.
