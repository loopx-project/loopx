# Local authority provider selection

LoopX now has one typed local-provider boundary for every provider-first
coordination command. When a goal has no selector, the boundary resolves the
`file` profile (`source_authority=file_v0`). This makes File/SQLite provider
semantics the default local contract without silently promoting an existing
Markdown goal or changing its writer fence.

## Selection contract

`openLocalAuthorityStoreHandle(runtime_root, goal_id)` resolves a handle with:

| Field | Meaning |
| --- | --- |
| `store` | The provider-neutral `AuthorityStore` implementation |
| `provider` | `file`, `sqlite`, or `postgresql` |
| `sourceAuthority` | The provider evidence label (`*_v0`) |

An absent selector is the explicit default File profile. A SQLite selector uses
the existing `loopx_local_authority_provider_v0` marker and its database
incarnation. A PostgreSQL selector uses the same marker schema plus a
`tenant_id` and `postgresql:<32 lowercase hex>` store identity.

The PostgreSQL marker contains no URL, credential, or database client. Opening
it requires a service-owned `openPostgresqlStore` factory. The factory receives
only the validated public binding facts and must return a PostgreSQL-labelled
`AuthorityStore` whose identity matches the selector. This is the runtime seam
for the medium-term switchable PostgreSQL profile; it does not ship an
authenticated service or grant an Agent database access.

## Failure and compatibility rules

- A selected provider never falls back to File when its selector, database,
  factory, identity, or metadata is unavailable.
- `source_authority` identifies the selected provider even when opening it
  fails; unresolved or malformed selection reports `null`.
- `decision_read_from_provider` is false for selection/open failures, and
  `legacy_fallback_used` remains false.
- The legacy `openLocalAuthorityStore` function still returns only the store,
  so existing callers remain source-compatible. Runtime entrypoints use one
  shared opening seam and no longer duplicate provider construction.
- Provider identity is observability metadata. It does not decide Todo
  eligibility, claims, leases, receipts, or promotion.

The default profile is a routing decision, not a migration. Existing Markdown
state, writer fences, qualification gates, and explicit File/SQLite promotion
holds remain unchanged. The released SQLite profile remains opt-in until the
shared-authority RFC's D2 evidence and owner approval are complete. PostgreSQL
remains an independent service-provider qualification path.

## Validation

The provider selection matrix is exercised with the production-scale synthetic
coordination fixture. Tests cover the default File handle, SQLite persistence,
selected-provider failure without fallback, PostgreSQL factory identity
fencing, and the factory's rejection of a different provider. File, SQLite,
and PostgreSQL continue to share the provider-neutral transaction conformance
contract; PostgreSQL's real-server qualification remains a separate gate.

See [reviewed promotion and recovery](reviewed-coordination-promotion.md) for the explicit saved-plan CLI journey.

## New Goal authority (machine setting)

**Settings → Capability Center → Device defaults → New Goal authority** selects
File or SQLite independently of the execution policy. In the new-Goal default
candidate, a device without a `goal_storage` namespace creates canonical SQLite
with `hard_lease`; CLI bootstrap and App creation reuse the same configuration
and typed initialization owner. **Create canonical authority** can be disabled
explicitly, or used with File and either `soft_claim` or `hard_lease`. Agents
inherit the Goal policy; this grants no tool, repository,
scheduler, account or network permission and does not migrate existing data.

The candidate changes only unconfigured new creation. Released v0 settings and
v1 with `canonical_creation=false` retain the post-promotion target behavior.
Opening v0 in the guided editor previews a v1 envelope upgrade with creation
still disabled; switching editor modes cannot enable it through the new default.
The CLI continues to accept v0. Existing Goals keep their recorded target,
including its absence, even when device settings change. Invalid configuration
fails before registry publication rather than being treated as an absent setting.
Registry-relative runtime paths are compared by their resolved filesystem
location, so an equivalent spelling cannot masquerade as an authority move.

Save this namespace document as `goal-storage.json`:

```json
{
  "schema_version": "loopx_goal_storage_defaults_v1",
  "new_goal_provider": "sqlite",
  "canonical_creation": true,
  "new_goal_handoff_mode": "hard_lease"
}
```

Preview, apply the exact reviewed revision, then inspect and create:

```sh
loopx machine-config preview --namespace goal_storage --config-json goal-storage.json
loopx machine-config apply --namespace goal_storage --config-json goal-storage.json \
  --expected-plan-revision PLAN_REVISION --execute
loopx machine-config inspect
loopx bootstrap --project ./new-project --goal-id new-project --dry-run
loopx bootstrap --project ./new-project --goal-id new-project
loopx todo list --goal-id new-project
```

Creation reports `storage_selection.authority_initialized=true`, its original
operation and provider receipt, and `legacy_writer_fenced=true`. Complete Todo
reads report canonical `source_authority` and `legacy_fallback_used=false`.
Saving a preference or publishing a registry entry alone is not successful
creation. Run artifacts and independently owned stores do not move.

CLI and App reuse one typed creation owner. The registry atomically freezes the
original operation and target before initialization. The owner verifies the
registered source, complete empty Todo/lease inventory and current bytes under
the existing writer locks. It engages a creation fence, commits the native
projection and original receipt, and durably records completion before success.
Fresh creation has no shadow qualification and never fabricates capture events.
Nonempty or captured sources require reviewed migration.

After interruption, rerun the same CLI bootstrap, or use **Retry original
operation** on the App card. Changed device defaults cannot retarget that
operation. Recovery must match its operation and workspace; a competing creator
cannot adopt it. The original receipt survives later native writes, so replay
cannot erase Todos or repeat their creation. An unavailable selected provider
fails visibly without Markdown fallback. Lost completed authority requires full
backup recovery and cannot be treated as empty creation. Generic forced
bootstrap cannot rebuild a canonically created Goal.

The App also retains the original card after Goal/initial-task commits when
first-Host startup fails or its response is lost. Its recorded Goal, Agent and
initial-task steps stay visible; **Retry original operation** resumes them in
the frozen server workspace, including an ordinary non-Git workspace. Native
File/SQLite Todo operation ids and the existing Session/Turn acceptance owner
prevent duplicate writes or first-Turn submission. A started Turn is read back
from its original Session, not inferred from a creation checkpoint. This
recovery does not certify model execution or Goal completion. A missing original
workspace, conflicting identity or unavailable authority still stops recovery.
Target-only historical creation retains its adapter; it does not gain the
canonical transaction's response-loss guarantee.

If the Host dies after Session acceptance but before upstream startup, the local
queued Turn is still the original request. Session restoration replays it through
the same Turn acceptance owner before preparing the adapter. A refused upstream
resume retains that queued request and reports `resume_failed`; it must not turn
it into a `server_restarted` outcome. A previously ended Turn without startup
readback cannot qualify `first_turn_started` or a successful creation receipt.
Keep the original operation and Session when the Host cannot restore its empty
thread; do not fabricate a rollout, switch identity or repeat committed Todos.
This affected Host profile remains unqualified for first-Turn recovery until its
original resume works; CLI authority creation and unrelated profiles retain their
own qualification boundaries.

首 Host 启动失败或响应丢失时，App 保留原创建卡片，并分别展示已提交的 Goal、
Agent 和首批任务。使用“重试原操作”在冻结的服务端工作区恢复，普通非 Git
工作区同样适用。原生 File/SQLite 复用 Todo 操作身份与既有 Session/Turn
接纳规则；已启动的 Turn 必须从原 Session 读回。创建步骤不代表模型运行或
Goal 验收完成；原工作区、操作身份或 authority 不可用时仍须停止恢复。
历史 target-only 创建保留其适配器，不获得原生事务的响应丢失恢复保证。

Host 在 Session 接纳后、上游启动前退出时，本地排队 Turn 仍是原请求。
Session 恢复须先通过既有 Turn 接纳 owner 重放该请求，再准备适配器。
上游拒绝恢复时保留排队请求并报告 `resume_failed`，不能将其结算为
`server_restarted`。已终止但没有启动读回的 Turn 不能生成
`first_turn_started` 或成功创建回执。Host 无法恢复空会话时保留原操作与
Session，不伪造 rollout、不换身份、不重建已提交的 Todo。该 Host 的首
Turn 恢复支持仍待实测；CLI authority 创建与其他支持范围分别验收。

<details>
<summary>Settings and recovery views / 设置与恢复界面</summary>

Synthetic workspace data on the candidate's packaged App and installed wheel;
model execution is disabled in this isolated creation/configuration rehearsal.

Released v0 preferences remain target-only when opened in the candidate editor.

![Existing File preference stays disabled](images/new-goal-authority/v0-retained.png)

Removing that preference restores canonical SQLite/hard-lease defaults, with
native removal readback. It does not migrate the existing Goals.

![Candidate SQLite defaults after preference removal](images/new-goal-authority/sqlite-default.png)

The existing preview/apply revision gate is unchanged. Unsupported `legacy`
policy is rejected before apply; correct the policy and preview again. No new
layout or responsive navigation is introduced.

</details>

In 2.0, `canonical_creation=false` preserves an inspectable preference but
refuses new Goal creation. Enable canonical creation and review the preview
before retrying. Removing the preference restores the candidate
SQLite/hard-lease default for future new Goals; removal is **not** opt-out.
To remove the whole preference:

```sh
loopx machine-config remove --namespace goal_storage
loopx machine-config remove --namespace goal_storage \
  --expected-plan-revision PLAN_REVISION --execute
loopx machine-config inspect
```

Use the removal preview's revision. Rollback also affects future creation only;
neither operation switches existing storage, removes its fence or reopens its
old writer. Reconnection and import keep their recorded route. Existing Markdown
Goals use [reviewed promotion and recovery](reviewed-coordination-promotion.md);
already-canonical Goals use the [reviewed File/SQLite cutover](file-authority-state-log.md#reviewed-filesqlite-cutover).
Retain verified backups, stop writers, settle leases and preserve newer writes
on reverse migration. Supported historical backup/format/receipt readers remain.

After reviewed import or promotion, reconnect the registered Goal with ordinary
bootstrap, without `--force`:

```sh
loopx --format json --registry REGISTRY bootstrap --project PROJECT \
  --goal-id GOAL --objective OBJECTIVE --no-global-sync
```

The reconnect reports `state_action=kept-existing` and `authority_read` from the
current canonical provider. It preserves later Todos, leases and the original
import receipt; an old v0 creation preference cannot retarget that provider.
Missing Markdown display is not recreated. Missing selected authority fails,
even when the old display still exists. `--force` and redirected state routes
are rejected; use the owning restore/migration operation instead. Reconnection
does not create a new creation receipt or execution grant.

This implementation candidate does not close full existing-Goal upgrade, D2
sustained qualification or authorize release-default activation. Trial admission and release
default admission remain separate; the existing RFC acceptance is unchanged.

### 新 Goal 的权威存储

在“设置 → 能力中心 → 此设备默认 → 新 Goal 的权威存储”中，分别选择 File/SQLite
和 `soft_claim`/`hard_lease`。候选实现让未配置此命名空间的新 Goal 默认建立
canonical SQLite、使用 `hard_lease`，CLI 与 App 共用配置和类型化创建归属。
旧 v0 或显式关闭状态仍只固定晋升后的目标；表单以关闭状态预览 v1 升级，
切换编辑模式也不会自动启用。CLI 继续接受旧格式，已有 Goal 保留原路径。
Registry 中的相对 runtime 路径按实际文件位置比较；等价路径不会误触发权威迁移检查。
Agent 继承 Goal 策略；此设置不授予工具、仓库、账户或网络权限。

CLI 使用上面的完整 JSON 和 preview/apply/inspect/bootstrap 命令；App 用现有
表单预览、应用并读回。成功须含 `authority_initialized=true`、原创建回执和已
读回的写入 fence；Todo list 须显示 canonical provider。保存偏好或出现 registry
记录本身不算成功，也不代表 Run 等独立存储已经迁移。

失败时重新执行原 bootstrap，或在 App 创建卡上“重试原操作”。目标和身份已固定，
后续默认值不能改写它。旧写入先被 fence；原生提交和回执核对后才持久记录完成。
已有 Todo、租约历史或 capture 的源须走独立审核迁移，不伪造 shadow 资格。完成
后的存储丢失须恢复完整备份，不能重新创建空库；通用 force 不能重建。原回执在
后续写入后仍可读回，重试不得丢失或重复 Todo。

2.0 中，显式 `canonical_creation=false` 保留可查看的偏好，但拒绝之后的新建；
须启用并审核配置，再重试创建。remove 删除偏好会恢复候选
SQLite/hard-lease 默认，不能用于关闭。两者均不会迁回已有数据、
删除 fence 或重新开放旧 writer。既有 Goal 升级仍需备份、停止写入、结算租约和
审核计划；反向迁移须保留新增写入。受支持的旧备份、格式和原回执恢复能力保留。
配置损坏明确失败，不视为未配置；候选实现不代表完整升级、D2 长期资格或发布默认已通过。

审核导入或晋升后，使用上面的普通 bootstrap 命令重连已注册 Goal，不加
`--force`。结果含 `state_action=kept-existing` 和当前 canonical provider 的
`authority_read`；保留后续 Todo、租约及原导入回执，旧 v0 创建偏好不能重新选源。
缺失 Markdown 显示不补建；选定权威丢失时，即使旧显示仍在也明确失败。
`--force` 和改向其他 state 路径被拒绝，恢复或迁移须走其归属操作。
重连不生成新的创建回执，也不增加执行授权。

Prose-only source maintenance compares the exact canonical JSON bytes of the
existing stable partition view. Only the resume evaluation clock is excluded;
boolean and integer facts remain distinct even where Python object equality
would equate them. This uses the same encoding boundary as capture identity,
without granting a prose writer any Todo mutation or provider fallback.

## 2.0 retirement boundaries

Normal mutation now requires canonical authority. Python still owns supported
Markdown projection, reviewed cold import and historical recovery adapters.
Changing the creation default does not migrate an existing Goal or settle its
historical obligations. The existing [retirement cadence](../architecture/rfcs/ledger/shared-goal-authority-state-provider-v0/2026-09-28-retirement-cadence.md)
records how each former writer family reached this cutoff.

| Retained boundary | Current value and owner | Required evidence |
| --- | --- | --- |
| `todos.py`, `bootstrap.py`, canonical Todo and lease adapters | Public mutation entrypoints admit only File, SQLite or PostgreSQL authority. Markdown remains a display and import source, not a write authority. | Run creation, update and recovery with the removed writer modules absent. Keep Markdown projection and supported import/export. |
| `local_authority_shadow_outbox.py` and its typed reader | New outbox production is removed. Historical prepared and committed entries remain available for bounded disposition and exact receipt replay. | Seed historical bytes directly, classify each entry against its source and receipt, and prove restart recovery without a business write. |
| `local_authority_shadow_projection.py` | Python transports full source artifacts, rejects floats and unsafe integers before Python/JS digest comparison, and checks identity, size, digest and regular-file readback. `coordination.source.project` remains the typed projection owner. | Preserve exact-byte and malformed-input behavior in the replacement, including symlink rejection, bounded transfer and ambiguous-operation errors. A language-only rewrite is insufficient. |
| `runtime_shadow.py`, `local_authority_shadow_adapter.py`; retained `configuration.py`, `authority_source_capture.py`, `local_authority_shadow_history.py`, `local_authority_shadow_readback.py` | Configuration, cold-source capture and read-only diagnostics now have concrete owners independent of producers. Explicit shadow bootstrap/drain/promotion and recovery still have live callers. | Retire obsolete producer aliases only after their supported callers exit. Preserve the retained import/history/readback owners and outbox recovery; inspect or cold import succeeding without producers does not make a live drain unused. |
| `authority_core.py` | Live Todo adapters delegate to typed mutation/ownership rules; scope overlap delegates to the typed lease owner. The lease-mode input compatibility contract is explicitly retained. | Trace direct, internal, dynamic and exported callers separately. Review registered historical input support before retiring its vocabulary; do not infer a fresh grant from old `legacy` inputs. |

Paths in the table are under `loopx/` or its `control_plane/coordination/` and
`control_plane/todos/` subdirectories. Keep host locking, atomic replacement,
process cleanup, private validation declarations and supported backup/original
receipt readers where they still serve real callers. Their I/O or recovery
value is distinct from a duplicated decision owner.

The cutoff qualification records the supported caller, replacement owner,
persisted obligations and rollback. It exercises the real CLI and installed
package with the retired writer path absent. The provider fails visibly without
falling back to a Markdown writer. Historical recovery tests seed old evidence
directly instead of calling a retired producer.

Useful existing regressions include `test_source_projection.py`,
`test_source_transfer.py`, `test_shadow_writer_boundaries.py`,
`test_cold_source_inspection.py`, `test_shadow_registry_source.py` and
`test_shadow_management_variant_e2e.py` under `tests/control_plane/`. These
tests cover cold source admission, historical management evidence and
producer-free recovery. Provider conformance and installed-package tests cover
the canonical write path separately.

### 2.0 compatibility cutoff and release sequence

The implementation still decodes target-only v0 settings and explicit
`canonical_creation=false` for inspection and reviewed configuration upgrade.
It rejects normal writes to unpromoted Markdown Goals. This is an incompatible
change for the 2.0 release, not a compatible 1.x update. The release decision
remains subject to the existing
[shared-authority RFC](../architecture/rfcs/shared-goal-authority-state-provider-v0.md)
and [retirement cadence](../architecture/rfcs/ledger/shared-goal-authority-state-provider-v0/2026-09-28-retirement-cadence.md).

The cutoff is **canonical authority before normal mutation**, not SQLite for
every Goal and not removal of every Python module:

| Existing state | 2.0 behavior |
| --- | --- |
| Unpromoted Markdown authority | Retain inspection and explicit upgrade/recovery; reject normal mutation before any write or new execution grant, with an actionable supported migration route. Do not silently initialize an empty canonical store. |
| Canonical File or SQLite | Keep the recorded provider and either supported `soft_claim` or `hard_lease` policy. New unconfigured Goals default to SQLite; existing File Goals do not have to switch. Missing providers remain errors. |
| Canonical store with `legacy` ownership policy | Normal ownership mutations and execution proofs return `handoff_mode_retired`; old receipt recovery and exact release remain available. Review a claim-preserving policy migration to `soft_claim` or `hard_lease`. This does not require another storage promotion or provider switch. |
| Target-only v0 or v1 creation disabled | Keep decoding for inspection and reviewed configuration upgrade. Require an explicit compatible creation choice before creating a writable Goal; do not erase the preference or reinterpret disabled as enabled. |
| Historical ownership inputs, source captures and backups | Recognize them in declared migration/recovery contracts. Historical leases, receipts and a restored source do not grant new execution authority. Preserve supported original-operation recovery and permanent Markdown display. |

Pre-cutoff promotion depended on an enabled shadow qualified by source
mutations. A cold Goal can no longer manufacture that capture history. The 2.0
upgrade path uses a separately qualified **reviewed import of the existing
source**. It does not lower operation counts, fabricate capture history or ask
a retired 1.x writer to create new evidence. Canonical create and isolated
archive restore remain separate workflows.

The import package must close one installed user journey through the existing
typed coordination owner and Goal storage settings, shared by CLI and App:

- Inventory and back up the complete supported source: active and archived
  Todos, metadata, identities, ownership facts, captures and original receipts.
  Reject unsupported or ambiguous source content explicitly; never omit it or
  equate an empty target with successful import.
- Source lease JSON inventory is fail-closed: unsupported record names,
  non-regular files and linked source subtrees refuse observation/revalidation.
  Exact byte witnesses include retained orphan records; those records do not
  become live leases. Non-record host lock files remain outside that inventory.
  This existing source-admission repair does not qualify the complete importer.
- Stop source writers and affected Hosts, settle active leases and reconcile
  prepared/committed outbox entries against source bytes and original receipts.
  Preserve history without converting old leases or receipts into new grants.
- Preview an immutable source/target-bound plan, then require explicit confirm.
  Detect source drift before publishing; fence the old writer before accepting
  canonical mutation. A cold source is qualified by the import's own checks,
  without inventing source mutation history.
- Recover the same operation after interruption, lost response or restart.
  Read back complete imported state and the new import receipt separately from
  original receipts; retries must neither duplicate effects nor erase later
  canonical writes. Test both File and SQLite with a physically absent old
  normal writer, including provider failure and stale-plan refusal.

This imports an unpromoted Goal into canonical authority. It is distinct from
File/SQLite provider cutover of an already-canonical Goal and from isolated
archive restore into a new identity. Do not substitute either journey's
acceptance for this one. Once this route qualifies, an old Goal's next normal
write requires reviewed import; installing the binary alone does not migrate it.

The CLI stage uses the typed `coordination.cold_source.import` transaction
(`loopx_cold_source_import_request_v0`). First stop affected writers through
their owning Host and verify that their processes have exited. Inspect each
retained task lease and release it through `task-lease release`, using its
original owner/key and current `--expected-version`. An expired lease is still
unsettled; stopping a process does not release its lease. Dispose of pending
capture/outbox through its owning workflow before preparing this import.
Then execute `backup-state` with the source state, registry, coordination
evidence and runtime root. Lease release after a saved preview changes its
source: make a fresh backup and reviewed preview. `--writers-stopped` records
an operator attestation, not an automatic process stop. For example, after
shutdown and settlement, from the registered project:

```bash
loopx --format json backup-state --project . --execute --no-automations --no-skills
loopx --format json coordination-shadow prepare-import --goal-id GOAL --operation-id IMPORT \
  --backup-manifest SAVED-MANIFEST.json --provider sqlite --target-handoff-mode hard_lease
# Inspect the immutable plan at plan_path. Confirm using its exact plan_sha256.
loopx --format json coordination-shadow apply-import --goal-id GOAL --operation-id IMPORT \
  --plan-sha256 SAVED-SHA256 --writers-stopped --execute
# After interruption, use the original operation and digest; do not prepare a new source.
loopx --format json coordination-shadow recover-import --goal-id GOAL --operation-id IMPORT \
  --plan-sha256 SAVED-SHA256 --execute
```

Prepare reads complete active and archived Todo records and preserves metadata,
original evidence and settled lease history. The trusted Python tar adapter
reads actual archive members, including older backups without a member list;
the TypeScript owner verifies coverage of the coordination source bytes and
pins both saved artifacts. A missing source path in the backup refuses import.
CLI and App retain the full backup and validate every archive member, but
transport only member digests covering the registered Goal's source files,
registry, evidence and lease directory. Unrelated backup inventory does not
inflate the bounded import request; unsafe members and changed complete
artifacts still reject. This does not reduce imported Todo records or qualify
whole-Goal reactivation.
Prepare may select an empty target and persist its identity and immutable plan,
but does not fence the writer, import state or grant execution authority.

Apply rechecks the original source and backup under the owning locks before
engaging `loopx_cold_source_import_writer_fence_v0`. Missing identity, changed
source, unsettled leases (including expired active or orphan records) and
unresolved capture/outbox fail closed. Recovery requires that original fence,
accepts no replacement source snapshot, and reads the original receipt without
overwriting later canonical writes. Source bytes remain retained history; the
import writes a separate new receipt. Recovery is not a provider rollback or a
whole-Goal restore, and replacing the binary cannot clear the writer fence.

`coordination_source_backup_verified=true` qualifies the prepared coordination
source witness only. `complete_goal_backup_verified=false` preserves the full
backup and original-history recovery acceptance. The packaged Goal storage
settings use the same transaction for cold import:

1. Select the Goal and open **Goal settings → Task ownership → Data storage**.
   Choose File or SQLite and an explicit supported execution policy.
2. Stop affected writers/Hosts, verify their exit and settle/dispose of refused
   work through its owning workflow. The preview refuses unsettled leases,
   including expired active records; a checkbox cannot bypass this refusal.
3. **Back up and preview import** creates a private local archive using the
   existing backup owner, then displays the complete active/archive inventory.
   Preview does not import, stop a Host, settle leases or grant execution.
   Confirm **Import reviewed Markdown source**. Apply
   rechecks the bound original source and backup; a changed source needs a new
   reviewed preview.
4. After a lost response, reload or native App restart, **Read original preview and current storage**
   observes the saved operation and current store independently. Reload never
   completes an unfinished import. Confirm again to retry the original operation;
   do not discard its carrier while the commit is ambiguous.

The local server retains only the existing Goal/operation/digest identifiers as
editor intent, scoped to the runtime, registry and Goal. This also covers a
canonical File/SQLite migration preview. A native App can recover the same
original preview after restarting on another loopback port; recovery does not
apply it. Browser storage is a compatibility cache, not the durable owner. Full
plans, source bytes and backup paths stay local to the server. Completed receipt
readback neither overwrites later writes nor reselects a provider.

**Discard original preview** clears only the matching editor intent and survives
restart. A stale tab cannot discard or apply a newer preview. Discard retains
plans, backups and original receipts; it does not roll back a committed migration.
A corrupt saved editor record makes inspection and mutation unavailable rather
than guessing a replacement; explicit original-receipt readback remains available.
Pre-upgrade browser-only handles remain explicitly readable through the original
API/CLI recovery path. A new server does not automatically adopt an unscoped
browser cache from a possibly different runtime or registry.
The operator-led POSIX stop path is exercised with actual owned Host processes
and native unpromoted-source leases on File/SQLite: import refuses while the
Host runs and after it exits with an active lease; native release permits
cutover. The imported released lease retains its identity and history, and a
restart using the old grant is rejected before the actual Host launches.
A fresh native acquisition advances that retained lease's version and execution
epoch and can start a real supervised Host under the selected canonical provider.
With that Host running, original-import recovery still works after the Markdown
source is removed and preserves the current active lease. The old token refuses
launch even while a new lease for the same Todo is active. Stopping the new Host
also leaves its lease active until its owner releases it; import replay preserves
the new settlement. Reacquire through the normal `task-lease acquire` workflow
with a new execution key and the current version, rather than reusing the old
execution receipt. Keep the supervisor/OS transport: writer retirement does not
remove its process-exit and current-execution proof duties.
This qualifies operator-led POSIX reactivation of synthetic work, not automatic
Host discovery/stop, pending outbox disposition, live model sessions or identity
adoption after full-history restore. The App loading boundary is qualified
separately below. The cold-import CLI reuses the
selected command dispatcher and
the existing Goal path resolver; its File/SQLite import and original-receipt
recovery run with `todos.py`, `bootstrap.py`, `runtime_shadow_writer_adapter.py`
and `local_authority_shadow_outbox.py` physically absent in a disposable package.
The retired writer and producer modules are absent from normal and installed
qualification. Historical outbox inspection and disposition use independent
readers. Pure runtime-root routing belongs to
`paths.effective_runtime_root`; canonical Todo, terminal lifecycle, acceptance,
Chat and CLI callers import that owner directly. Source and fresh-wheel
File/SQLite qualification runs the complete `serve_chat` entrypoint with the
retired modules physically absent. The packaged Goal settings
journey performs backup/preview, reload and original-operation readback before
explicit confirmation. Full current and archived requirements survive import;
subsequent reviewed App creation, editing and completion work with the explicitly
selected `soft_claim` policy. Restart/replay preserves those later writes.
Missing selected storage and missing `hard_lease` execution proof still refuse
without a Markdown fallback. App reads use the existing readback owner, and
shared input transport reaches the existing typed mutation owners. Reviewed
canonical Todo creates use request v2 to bind the preview's provider
revision to the existing command identity and transaction CAS. Concurrent
changes reject the stale request; original-operation recovery runs first and
retains subsequent writes. Unreviewed v0/v1 callers keep their existing intent
and receipt identity. Import and original-receipt recovery refresh current
ownership independently on the same settings screen, including retryable read
failures; a saved receipt does not substitute for today's policy.
Read-only compatibility APIs retain their declared historical inputs. They do
not reexport a Markdown business writer or grant fresh execution authority.

已审核的 canonical Todo 创建使用请求 v2，将预览的 provider revision 绑定到既有
命令身份和事务 CAS；并发修改会拒绝过期请求。原操作恢复先于当前 head 校验，保留
之后的新写入；未审核的 v0/v1 调用保留原有意图和回执身份。导入或原回执恢复后，
同一设置页面独立回读当前所有权，读取失败时可重试；保存的回执不代替当前策略。
Source checks accept registered runtime directory aliases only when both paths
resolve to the same physical directory. Retargeting an alias after preview still
rejects before the import fence; original source observations are not rewritten.
App backup IO resolves its configured registry path to the same physical filename
used by the source witness, including registries outside conventional Goal folders.

来源校验只接受指向同一实际目录的 runtime 路径别名。预览后将别名改指向另一个
目录，仍在写入导入 fence 前拒绝；原来源观察不会被改写。
App 备份使用与 source witness 一致的 registry 实际路径，也覆盖位于常规 Goal
目录之外的 registry。

The 2.0 cutoff was qualified in this order:

1. **Direct source import and support boundary.** Qualify the reviewed
   cold-source import on the installed CLI and packaged App, alongside
   existing new-Goal creation/retry and provider migration. Cover both
   qualified and cold unpromoted sources, target-only settings, stopped writers,
   active/expired leases, pending captures, missing providers and original-plan
   recovery. The importer does not call the retired writer.
2. **Normal writer family cutoff.** Enforce the boundary through the existing
   typed admission owner and remove the
   corresponding Markdown create/update/claim/complete/supersede/archive paths
   together with their private dispatch and writer-only tests. Trace CLI,
   internal, dynamic and supported import callers; keep shared rendering,
   validation effects and migration/recovery readers. Run the real wheel and
   packaged user journey with the retired paths physically absent, including
   refusal and recovery for an unmigrated Goal.
3. **Capture producer and duplicate decision retirement.** Remove producers and
   bridges without supported callers.
   Reconcile each prepared/committed outbox entry against source bytes and its
   original receipt first. Retain the codecs/readers needed by supported old
   backups and interrupted migrations. Prove restart/replay and forward recovery
   without recreating a business write or losing a later canonical write.

Software installation, authority-format upgrade, provider migration and
ownership-policy migration remain distinct operations. Binary updates do not
automatically switch authority. Reverse File/SQLite cutover starts from the
current head and retains all acknowledged post-migration writes.

Data loss, duplicate effects, identity errors and incorrect settlement remain
hard stops. Record representative usability/cost evidence separately from frozen
D2 capacity/soak qualification; missing or failed D2 evidence stays visible.
A microbenchmark failure alone does not block an unrelated, proven internal
deletion, and passing those deletions does not certify the release default.

### Retirement proposal reconciliation

The contributor-led [#6396](https://github.com/loopx-project/loopx/pull/6396)
and [supplement #2](https://github.com/Duang777/loopx/pull/2) carry the useful
contracts from [#6366](https://github.com/loopx-project/loopx/pull/6366).
Reconciliation follows supported behavior, rather than preserving every old
module, response alias or test filename:

| Contract | Current owner and regression evidence |
| --- | --- |
| Normal writer cutoff and deletion | Canonical mutation/creation/Monitor adapters; cold-source import and writer-isolation tests. Markdown mutation producers stay retired. |
| Imported completion, succession and policy repair | Typed terminal/update owners; shared provider conformance and actual CLI consumer smokes in the supplement. |
| Claim/lease ownership and atomic completion | `test_canonical_handoff_invariants.py`, canonical mutation/claim-transfer tests and provider conformance. Metadata cannot implement the retired two-write handoff. |
| Original-operation recovery | Fixed 1.x lease, Monitor and team-plan artifacts guard exact replay, prepared matching effects, original release, lost close response and preservation of later state. Selected-provider failure remains a refusal; original receipts are checked before fresh-work admission. Fresh legacy verification/grants remain retired. |
| Current ownership policy | Only `soft_claim` and `hard_lease` admit current work. Imported `legacy` records and original receipts cannot grant execution; reviewed backup/plan/migrate and exact release remain available. |
| Retired lease helpers | Delete the unused Python lease writer/factory, generation/CAS helpers, owner-validation chain, and source Todo projection. The typed transaction remains the decision owner; retained JSON reading stays read-only. Release corruption/CAS assertions run through the actual native adapter with fixed 1.x records. |
| Reviewed App creation and configuration | Chat binds the accepted operation to the reviewed provider revision and Todo identity; stale preview rejects, corrected preview can retry. Settings preserve a disabled preference and explain the required upgrade. |
| Mutation-oracle strength and packaging | The fence oracle observes actual exclusive-open contention, including delayed startup. Existing prerelease bundle and installed-package checks retain their independent scope. |

The ordinary lease CLI routes `renew`, `transfer` and `release` by the recorded
provider. Unpromoted retries reach the retained typed lifecycle owner, which
recognizes the original prepared/committed receipt before refusing fresh work.
A successful historical renewal or transfer replay is not a current execution
proof. Changed intent, ambiguous prepared effects and unavailable selected
providers still refuse; the public adapter does not implement receipt decisions.

Recovery and new execution retain separate admission paths. An unmigrated
Monitor may finish its original quota transaction only from its matching retained
business receipt; missing or changed evidence cannot rerun the observation,
create a new reservation, or revive an old producer. Selected-provider failure
never authorizes a fallback to historical Markdown. These cases run through the
ordinary CLI as well as the retained receipt reader.

For completed canonical creation and team plans, a missing or unreadable
Markdown display is not a new-operation precondition. Exact creation/plan
receipts remain recoverable after later Todos have been committed. Project
registration keeps its original registry intent and frozen target; incomplete
creation still validates its original source. Missing display recovery uses the
current canonical projection and explicitly restores Todo sections only, never
an empty registration template. An unreadable existing display remains intact:
registration returns only the original creation readback, while a team-plan
projection attempt reports its pending delivery. Creation readback does not
certify display health.

User actions retain their administrative operation through the bound Agent;
they cannot acquire, renew or transfer an execution lease. Current proof and
inspection use the same typed eligibility owner. An old invalid lease can still
be released with its exact identity; the bound Agent can then resume the normal
administrative edit or completion without creating another execution lease.

The absorption does not qualify a release, contributor adoption, sustained D2
operation or an unobserved Host. Full current-head CI and the combined adopted
candidate retain their own acceptance. Historical output aliases and tests
whose only subject was a deleted producer are superseded by the typed current
contract and its supported recovery tests.

Retirement removes internal Python helper exports with no shipped caller or
registered import contract; it does not remove CLI/API lease operations, the
historical wire vocabulary or receipt decoding. Test fixtures describe source
records directly instead of keeping a production writer solely to construct
fixtures. Owner eligibility remains covered at its typed owner and by real
canonical claim/lease transactions. Historical generation checks belong to
native recovery/release, including corrupt boolean generations and unchanged
bytes after rejection.

### 2.0 退役边界

正常写入现在只接受 canonical authority；旧 writer、producer 和无调用内部桥接
已删除。默认值修改仍不迁移已有 Goal 或自动处置历史 outbox。受支持的备份、格式、
原回执恢复与 Markdown 展示继续保留。

上表中的精确数字与摘要校验、安全文件传输、prepare→主写入→commit 顺序、游标
恢复和 Host I/O 都有实际价值。替代实现须保留它们；Python 行数减少不能代替语义
验收。静态引用、内部调用、动态入口、导出兼容与安装态要分别核验。删除前在隔离
环境中让旧路径实际不存在，验证创建、修改、中断、重启、原操作重放及 provider
不可用时明确失败；不降级到 Markdown writer，不破坏历史恢复，回退保留迁移后的
新写入。现有回归覆盖不等于长期 SQLite、真实 PostgreSQL、打包 App 或发布默认
已通过，trial 与正式默认的资格继续分别记录。

普通 lease CLI 的 renew／transfer／release 按原 provider 路由；未迁移的原操作重试
先由既有 typed lifecycle owner 核验 prepared／committed 回执，再拒绝真正的新工作。
历史 renew／transfer 的成功回读不提供当前执行证明；改意图、prepared 效果歧义或
已选 provider 不可用仍拒绝，Python adapter 不新增回执判断规则。

恢复与新执行分开准入：先验证已选 provider 可用性，再核验原操作的 typed 回执，
最后才判断新写入。未迁移 Monitor 只能凭原业务回执完成原 quota 事务；缺失、损坏
或身份改变不能重新观察、创建新 reservation 或恢复旧 producer。canonical 创建和
team plan 的原回执不依赖 Markdown 展示仍然存在；重复注册保留原 registry 意图与
已提交的新 Todo，缺展示时只从当前 canonical head 恢复 Todo 区域，不重写空模板。
已有坏字节保留；注册只读回原创建回执，team-plan 的投影失败如实返回 pending，
不把创建回执当成展示健康证明。User action 仍由绑定 Agent 执行行政编辑或完成，
不能申请、续期或转移执行租约；历史错误租约可以精确释放，再继续原行政操作。

**2.0 支持矩阵：**未迁移 Markdown Goal 的正常写入返回
`canonical_authority_required`；v0 target-only 和显式关闭 canonical creation
只用于检查与审核升级。要求是“正常写入前必须有 canonical authority”，不是所有
Goal 强制 SQLite。已有 canonical File/SQLite 和两种 ownership policy 保持。
旧备份、格式、原回执和永久 Markdown 投影继续按其恢复契约支持。

旧 Goal 通过独立验收的**旧源审核导入**迁移，无需调用退役 writer 新增 capture。
流程先完整盘点和备份源，停止 Host、结算 lease 并逐项对账 outbox，再预览、明确
确认、fence 旧 writer、导入并读回。未支持或歧义内容明确拒绝，旧 lease/回执不
转为新授权；导入新回执与历史原回执分别保存。中断、失响应和重启恢复同一操作，
不重复副作用，也不丢后续 canonical 新写入。

实现按三个包完成：安装态 CLI/App 直接导入与支持边界；删除
create/update/claim/complete/supersede/archive 旧 writer 及私有 dispatch；逐项
对账 outbox 后删除无调用 capture producer 和重复决策。验收覆盖原生拒绝与恢复，
并在旧路径物理缺席时运行真实入口。二进制更新不自动切 authority，反向切换须
保留当前 head 的新增写入。

## Existing canonical Goal storage in the App

Open a Goal’s settings → Task ownership → **Goal data storage**. This control
uses the same typed local-provider migration, archive and publication owner as
`authority-archive plan-migration/migrate`; it changes neither new-Goal defaults
nor ownership policy. Previous Markdown Goals must first use reviewed promotion.
PostgreSQL cutover is outside this local File/SQLite operation.

1. Stop writers and settle active leases, including expired active leases. An
   active capture blocks preview/apply even when its outbox currently looks
   empty. Finish the existing capture rollback/recovery; do not delete its queue.
2. Read current storage, select File or SQLite and preview. The immutable plan
   pins the source identity, revision, cursor, projection and writer fence.
3. Confirm the reviewed operation. Apply verifies the full backup and target
   history before publishing and independently reading the selected provider.
4. After a lost response or server restart, **recover the original preview**.
   The browser retains only its opaque handle and digest. Recovery reports the
   original prepared/completed record separately from today’s source; it never
   replays a superseded operation or changes the provider. Retry that same plan
   to resolve an interrupted publication. A stale source needs a fresh preview.
5. To return to File, preview a new migration from the **current SQLite head**.
   This retains writes made after the first migration. Restoring the original
   backup alone would discard those writes and is not a reverse cutover.

HTTP accepts registered Goal and opaque preview/digest bindings, never caller
paths, source overrides or complete state. Its current-source/recovery reads are
observations, not execution grants or whole-Goal backups. Configuration remains
unchanged until explicit apply. Real File/SQLite HTTP and packaged-App recovery
validation does not qualify sustained D2, release defaults, a stopped external
Host or all historical-source upgrade paths.

### 既有 canonical Goal 的 App 存储切换

进入 Goal 设置 → 任务所有权 → **Goal 数据存储**。新 Goal 默认值、任务所有权
策略与此处的既有存储切换分别管理，复用同一 TS migration/archive/provider owner。
旧 Markdown Goal 先走有备份的审核晋升；这里不提供 PostgreSQL 切换。

先停止写入方并结算 active lease，过期不证明 Host 已停。active capture 即使暂时
空队列也拒绝切换；按原生 rollback/recovery 处理，不能删队列。读回当前来源、
选择 File/SQLite、预览并明确确认后才备份和发布。来源变化拒绝应用。

丢失响应或重启后恢复同一 opaque 预览与摘要。历史 completed 回执与当前来源
分开读回，旧操作被后续切换替代后不会重新激活。中断操作重试原计划；源变更才
审核新计划。回退 File 必须从当前 SQLite head 新建计划，保留升级后的新增写入，
不能直接恢复旧备份替换当前数据。本入口不授予执行权限或迁移所有 Goal；D2、
发布默认、完整旧源/Host 恢复继续按原验收分别资格化。
