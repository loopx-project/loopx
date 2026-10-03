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
holds remain unchanged. SQLite stays an opt-in qualified candidate until the
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

## New Goal storage target (machine setting)

The **New Goal storage target** setting fixes a File or SQLite target at
creation. It is not live inheritance, automatic promotion, or an existing-Goal
migration. Until separately reviewed promotion, the existing legacy source is
still authoritative. After promotion the selected provider serves canonical
Todo/lease state; Run artifacts and other independently owned stores are not
moved by this preference.

Use **Settings → Capability Center → Device defaults → New Goal storage target**
or the revision-checked CLI:

```sh
# goal-storage.json:
# {"schema_version":"loopx_goal_storage_defaults_v0","new_goal_provider":"sqlite"}
loopx machine-config preview --namespace goal_storage --config-json goal-storage.json
loopx machine-config apply --namespace goal_storage --config-json goal-storage.json \
  --expected-plan-revision PLAN_REVISION --execute
loopx machine-config inspect
loopx bootstrap --project ./new-project --goal-id new-project --dry-run
```

The preview reports `storage_target`; creation reports `storage_selection` with
`promotion_performed=false`. CLI and App creation share the same bootstrap
owner. Creation stores its intent before provider initialization, so retry after
interruption uses the same target even if the machine preference changed.
If App creation fails during initialization, use **Retry original operation**
on that creation card. It resumes the recorded target before adding initial
Todos or starting a Turn. A persistent initialization failure remains an error;
the presence of a registry entry alone is not successful creation. Recovery must
match the original App operation and its validated workspace. Registration
records `creation_operation_id` atomically with the Goal; a competing creation
of the same id, even in the same workspace, is rejected before initialization
or initial Todos. The create-only check is repeated under the registry lock.
Older incomplete cards without this binding require inspection of the existing
Goal and its canonical bootstrap/recovery path; they cannot adopt it by id.
Already-applied cards continue to return their original receipt.
Reconnecting an existing Goal, including an implicit File Goal, does not adopt
a newer machine default. Importing existing Markdown does not count as a new
empty Goal. Explicit provider selection never falls back on failure.

Without this namespace, existing behavior remains unchanged. To stop applying
the preference to future Goals, preview `loopx machine-config remove
--namespace goal_storage`, then use its returned plan revision with `--execute`.
Configuration rollback also affects future creation only. Neither operation
switches existing storage or removes data. A File target keeps implicit File
routing until a committed authority exists; it does not create a dangling
identity-bound selector for an empty File document.

For already-promoted Goals use the [reviewed File/SQLite cutover](file-authority-state-log.md#reviewed-filesqlite-cutover):
stop writers, settle leases, review the saved plan, retain verified backups,
then migrate. Reverse migration must preserve newer writes. New-Goal defaults
and current-provider selection are separate facts. This opt-in setting does
not change the release default or complete D2/D3 qualification.

### 新 Goal 的目标存储

这是创建时固定的目标，审核晋升后才接管 canonical Todo/lease；不是“所有数据
已经存入 SQLite”。更改默认值只影响此后创建的空 Goal，既有 Goal、重新连接或
导入已有 Markdown 均不自动切换。创建中断后重试沿用已记录的选择。关闭或回滚
设置不迁回数据；已有 Goal 需停止写入、结算租约，走独立的备份和审核迁移流程。
