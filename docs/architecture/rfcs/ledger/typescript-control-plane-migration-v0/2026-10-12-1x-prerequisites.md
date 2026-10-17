# Historical 1.x prerequisite checkpoints (through 2026-10-12)

Moved from the [TypeScript migration RFC](../../typescript-control-plane-migration-v0.md), former section “Historical 1.x prerequisite checkpoints (through 2026-10-12)”, on 2026-10-16 at RFC baseline `b2aca67d76af45e554c620818a982a81f8d2d52b`. Preserve the original evidence and unqualified boundaries. The English source recorded `38cb407ed`; the Chinese source recorded `06b6caa07`. Neither is a new qualification of the current candidate.

[中文版](2026-10-12-1x-prerequisites.zh-CN.md).

The following records explain the prerequisites and validation at their named
sources. Their references to reachable writers and a “next” cutoff describe
that 1.x baseline, not remaining implementation work in the candidate above.

Rechecked at main `38cb407ed`: #5413/#5466/#5283 and the affected creation,
Host-stop and UI-history owners #5500/#5805/#5308/#5398 are merged. Reuse
their delivered boundaries rather than reopening the October 2 merge queue.
The [current validation, migration and deletion plan](../shared-goal-authority-state-provider-v0/2026-09-28-retirement-cadence.md#current-closeout-validation-migration-and-deletion-2026-10-02)
now prioritizes installed reversible qualification, affected consumer adoption,
policy migration and last-caller deletion; it records the merged sources and
the remaining evidence separately.

Canonical new-Goal creation is already implemented by #5500/#5805. The existing
machine setting and `coordination/local_authority_defaults.ts` choose canonical
SQLite with `hard_lease` for unconfigured new Goals; CLI bootstrap and App
creation share the typed initialization and original-operation recovery owner.
Explicit v0 settings and v1 `canonical_creation=false` preserve post-promotion
target behavior. Existing Goals keep their recorded selection. See the
[configuration and disable contract](../../../../reference/local-authority-provider-selection.md#new-goal-authority-machine-setting).
This is the source candidate, not certification of installed adoption, a released
default, full shared Goal intent or D2/D3. Do not rebuild a creation/default owner.

Canonical Todo entrypoint separation (T4): all five mutation facades now share
`todos/mutation_api.py`; historic imports are lazy and the unpromoted writer is
isolated in `todos/legacy_mutation.py`. The installed File/SQLite oracle can
remove the whole writer module while exercising lifecycle and settlement
recovery. The cold-import oracle now composes reviewed import, fresh leased
updates/new writes and original-operation recovery with that writer absent;
source-display loss and provider outage cannot replace the current authority
or erase later writes. The original import cursor remains historical while the
current provider revision is preserved. This prepares last-caller deletion
without claiming code retirement:
bootstrap, handoff, team-plan and Monitor still have supported source callers;
keep capture/outbox and historical recovery until their own exits. [Caller and
rollback inventory](../../../../reference/canonical-todo-completion-update.md#canonical-mutation-entrypoint-isolation).
Production Todo consumers use the concrete owners instead of the historic `loopx.todos` re-export:
CLI registration/actions, quota and planning, capability writeback, source lease
reads and the Lark source projection import the same concrete mutation, full-read,
path or Markdown display owners. No new wrapper, decision or RPC is introduced.
The historic public imports and their supported unpromoted IO remain available.
Disposable source/normal-wheel probes remove the facade, the whole legacy Todo
writer and four shadow producers together; real File/SQLite CLI lifecycle,
once-only settlement, Monitor replay and App cold-import/action/restart journeys
must still work, with missing-provider/lease and changed-source/backup refusals.
The App HTTP receiver now also removes `bootstrap.py` from that same copied
distribution and checks the complete removed set before each server start,
restart and auxiliary CLI. Separate fixture results cannot prove this combined
absence. The ordinary canonical CLI creation entrypoint remains with its owner;
removing it from the cold-import receiver is a disposable qualification probe,
not a supported package format or permission to break the additive 1.x line.
中文：App 冷导入、后续写入和重启使用同一份缺少旧入口、writer 与 source producer 的
隔离副本；不能拼接两组各自缺少一部分模块的结果。正常 canonical 创建仍保留其
现有入口，这个临时卸除验收不等于已完成整族退役、自然试用或正式安装采用。
Lark dry-run and Explore replay qualify local consumers only, not live external
adoption. The fresh research execution path added by [#5280](https://github.com/loopx-project/loopx/pull/5280)
reintroduced a function-local historic import after the earlier caller inventory.
It now imports `todos/list_readback.py` directly, preserving the same typed
current-Todo validation and the historical replay branch before that read.
The qualification includes fresh research execution, actor/runnable/input
refusals, exact lineage readback and replay after current input/Todo changes
in the same facade-present/absent source and normal-wheel File/SQLite matrix.
A whole-production Python import guard resolves relative imports and aliases;
dynamic loading still requires the isolated real journeys. Keep this guard as
new capabilities arrive, rather than treating a past inventory as a permanent
exit. Last supported source writers, historical recovery, installed cohort
and release exits remain independently open.

The next T4 package is the [import-first normal-writer cutoff](../../../../reference/local-authority-provider-selection.md#proposed-compatibility-cutoff-and-release-sequence),
not another re-export or producer-loading extraction. At `38cb407ed`, live
unpromoted callers remain in ordinary mutation, bootstrap/creation opt-out,
team-plan, Monitor, ownership/source lease and active capture/binding. Reuse
their typed admission/transaction owners; treat retained source capture,
historical receipts and permanent document IO as recovery obligations.
The existing delivery Todos should carry these concrete exits:

- Qualify the installed import/backup/recovery journey for the declared source
  matrix; importer implementation is already present and is not a missing-code
  dependency. Do not reinterpret complete coordination backup as complete Goal
  backup.
- Run the bounded reversible core-developer SQLite trial before the breaking
  cutoff, recording real Host adoption/use separately from conversion drills.
- Deliver the cohesive 2.0 normal-writer cutoff: fresh unmigrated writes and
  execution grants refuse before effects; canonical work and original-operation
  recovery pass with the old writer physically absent. Keep the additive 1.x
  import/deprecation line compatible.
  Admission must preserve original lease release and receipt/outbox recovery
  before import; denying the existing shared legacy fence indiscriminately
  would also block these prerequisites. Remove the Python missing-fence bypass
  with the typed cutoff and validate the combined recovery-to-import journey
  specified in the linked release contract.
- After the last source caller exits, reconcile pending outboxes and delete
  unused capture producers/private decisions together. Keep supported historical
  readers and current-head rollback; do not wait for every external user to
  migrate or claim full T4/D2/default qualification from internal deletion.

This updates the existing R5/T4 frontier and release proposal; it creates no
parallel migration owner, runtime gate or release activation.

Team-plan recovery prerequisite: an unpromoted plan that published its original
receipt can now recover through the Chat confirmation owner while shadow
bootstrap is interrupted. Registry/Todo/source locks still protect the read,
and the existing typed identity validator rejects changed intent, actor or
reviewed basis. Recovery verifies source durability without loading the capture
producer or admitting new writes; fresh plans still obey all source, management
and promotion guards. This repairs the existing 1.x recovery path and does not
activate the 2.0 cutoff or settle the remaining lease/outbox/Host obligations.


The retained import-compatible field writer now composes resume normalization
and contradictory-clear rejection in its existing typed field plan. Keep blank
Python input omission and locked IO; retire the duplicate normalization crossing,
not the still-supported writer or migration reader.

The remaining legacy writer and bootstrap capture adapter now avoids loading
rollout history and constructing a Todo projector when runtime shadow is off
and there is no active durable binding. It still resolves the source identity;
an active binding continues to require durable preparation even after config
is disabled. This removes optional capture work from ordinary legacy writes,
without changing their output, history retention or outbox recovery. Python
retains this Host IO adapter until its last supported source writer is replaced;
this is neither whole-writer retirement nor default-provider qualification.
Unpromoted lease acquire now loads the capture adapter only when the typed
result carries a capture request. The real CLI and normal-wheel oracle cover
acquire/replay, rejected stale renewal, renewal, transfer and release with the
four legacy shadow producer modules physically absent and with no enabled
capture or active binding. An active binding still delivers capture after the
setting is removed. This retires a default-off dependency, not the supported
source writer or pending capture/history recovery.

Handoff/continuation display decisions now belong to the existing TypeScript Todo
context owner. Summary reuses its normalized identity/claim/exclusion/class
facts in one batch; the index keeps source compaction before labelling and audit
merging in one native batch. Planning and quota collectors project whole chosen
sets. Oversized display context uses byte-bounded read-lens batches while
counts, dependency decisions and closure retain their full-source owner. A
single oversized historical record reuses the existing private snapshot transport:
credential checks see its full text before display truncation. The
Python facade keeps shared metadata codecs and historical tuple/SHA-1 identity
encoding, with no Python decision fallback. Retain nested-empty precedence,
Unicode whitespace/codepoint limits and credential rejection before truncation;
these are compatibility obligations. File/SQLite CLI and normal-wheel recovery
must preserve exact source and provider revision when Markdown display is absent
or the selected provider is unavailable. This read-model retirement does not
retire the supported mutation/Host callers or certify a released SQLite default.

Public add now composes scope, supported wait and bounded Monitor metadata in
one typed creation-scope request. The legacy add writer consumes that same
fresh draft rather than re-planning it; standalone line codecs and successor
creation retain their own admission. Priority and validation-declaration
checks keep their prior diagnostic order, and canonical provider transactions
still recheck registry/claim/lease/CAS authority. This retires duplicate add
composition, not the supported Markdown writer or bootstrap/Host/history IO.

The canonical team-plan caller now loads capture and Markdown editing only in
its supported unpromoted arm. Real File/SQLite Chat and normal-wheel source
removal cover confirmed batch commit, later-write replay, provider restoration,
projection recovery and imported historical receipts. The existing TS
transaction, source guards and projection owner remain unchanged; the legacy
batch IO instrumentation seam stays lazy for its current consumers. This is a
caller-isolation exit, not deletion of the live legacy writer or completion of
receiver adoption, Host execution, PostgreSQL or D2 qualification.
[Contract and retained duties](../../../../reference/canonical-todo-completion-update.md#team-plan-source-writer-isolation).

Public CLI isolation now keeps shadow configuration in `coordination/configuration.py`
and complete source capture in `coordination/authority_source_capture.py`. Cold import,
source inspection and historical promotion recovery reuse those retained owners;
only explicit enabled shadow actions load the old producers. Ordinary new-Goal
SQLite creation, leased work, once-only settlement and reversible exit, plus cold
File/SQLite import and original-operation replay, run through the real CLI with
all four shadow producers physically absent in disposable source/normal-wheel
copies. The old module still re-exports the retained APIs for current consumers;
enabled capture, outbox and legacy commands remain supported. Preserve exact
source identity, archive dependencies, safe lease reads and later-write recovery.
This removes an eager-import blocker; deleting live producers still requires
their last-caller exit. App/Lark contracts, provider transactions, cohort adoption
and frozen D2 evidence remain separate.

The Monitor router likewise isolates its live source writer. Historical receipt
codec/replay has a small shared scheduler IO owner, while the existing TS batch
still owns operation identity and decisions. File/SQLite CLI and normal-wheel
source removal must preserve later canonical writes and original successors;
promoted replay leaves old outbox bytes inert. Unpromoted markerless batch
recovery retains its existing bounded drain. Business-receipt replay does not
guarantee shadow catch-up: lock deferral or an expired proof budget preserves
pending evidence for an independent `authority-shadow drain`. Recovery keeps
the original source bytes, successor IDs and once-only quota effect; it does
not replay the writer to finish catch-up or increase the inline limits.
This removes a canonical import dependency,
not the supported legacy writer. The absent arm also removes all four shadow
producer modules using the qualified CLI startup isolation above.
[Retained duties and rollback](../../../../reference/canonical-todo-completion-update.md#monitor-source-writer-isolation).

Shadow diagnostics now have an independent read-only boundary: retained outbox
disk codecs live in `local_authority_shadow_history.py`, and status/candidate
read transport in `local_authority_shadow_readback.py`. The public status and enabled
`coordination-shadow inspect`/`qualify`/`read-candidate` commands call that owner
without loading source producers. Full source and normal-wheel observations
retain matched/refused, default-off, source-drift and missing/corrupt-store
recovery behavior with all four producer modules absent. Historical imports
remain exact aliases; bootstrap, rollback and promotion retain their live adapter.
The existing TS qualifier now propagates provider read refusals before lineage
validation can mislabel corruption as `bootstrap_required`; valid/missing stores
retain their behavior. This is an explicit failure-diagnostic correction, with
no new RPC or migration authority; drain still uses its existing orchestration.
Original entry identity, malformed evidence, cursor residue and missing-store
behavior remain unchanged. Python retains historical filesystem decoding and
presentation IO; TS retains history proof, delivery and cleanup authority.
Existing producer imports are aliases for supported callers, not duplicate
implementations. Source and normal-wheel File/SQLite recipient checks compare
complete status results and unchanged original bytes with producers present or
absent. This is a historical-reader exit prerequisite, not writer deletion,
outbox disposition, installed cohort adoption or release-default qualification.
See [operator readback and rollback](../../../../reference/authority-observation-retirement.md#retained-readback-without-source-producers).

Existing Goal migration, two-policy ownership retirement and storage-format
upgrade retain separate receipts and exits. Original-receipt recovery does not
justify retaining `legacy` as a live policy. Required migration readers remain.

Imported canonical bootstrap reconnect now reuses the existing typed authority
reader, keeping current Todos, leases and original import receipts without
entering the Markdown writer. File/SQLite real CLI coverage includes an old v0
preference naming the other provider, missing display, missing selected authority,
force/route rejection and later-write replay with legacy producers absent.
Python retains bootstrap registry/Host IO; it adds no decision owner or creation
receipt. This closes a reconnect dependency, not the remaining unpromoted writer,
active outbox, Host or release-default acceptance.

A bounded cohort can start after its installed recovery and relevant execution
controls pass; it does not certify a released default or formal ten-day D2.
Frozen failures/missing evidence remain visible. T4 deletes proven redundant
owners alongside implementation, without waiting for R6 or all Python to vanish.
This replaces stale current-count estimates, not historical execution evidence.

Public-scan privacy checkpoint: the retained Python filesystem/regex owner
classifies canonical inputs before reader submission, independently of display
paths. Overlapping roots cannot remove a private classification; reports use the
widest requested root consistently. Narrow explicit files/directories retain
private ancestry. Git-verified worktrees remain their own namespace beneath
private parent directories, while a directory merely named `.git` grants no
exception. Outside a worktree, explicit inputs beneath private state remain
excluded. Tracked-file policy, fresh symlink resolution and existing error rules
remain in this owner; no second control-plane decision source or new setting is
introduced. This safety repair does not qualify scanner latency, SQLite release
defaults or whole T4 retirement. CLI checks and quota preparation use the rule;
App status continues to report repository scanning as deferred.
