# Canonical Todo snapshot pagination

After whole-Goal promotion, collection consumers use
`coordination.local_authority.todo_snapshot`. Python assembles its result
into the existing Todo-list result before returning to CLI, status, Turn, Chat,
team planning or projection delivery. Before promotion, the durable writer
fence is absent and the existing legacy path remains unchanged.

## Observable contract

A large, valid canonical collection no longer has to fit in a single inline RPC
response. The complete-read entrypoint uses the existing opt-in same-UID private
file transport when needed. It validates the provider head once and returns a
complete snapshot when its UTF-8 JSON fits within 62 MiB, reserving the remaining
2 MiB of the existing 64 MiB private-file capacity for the RPC envelope. Smaller
responses still travel inline. File ownership, permissions, byte count and SHA-256
are verified before Python admits the result; the temporary files are reclaimed
after the call. No provider head is cached between calls.

When a complete snapshot exceeds that capacity, the same owner returns the
original bounded first page instead. Subsequent requests retain that page's
continuation, so a large valid population remains readable without raising a
transport limit or retrying a lost oversized response. The standalone wire-only
`coordination.local_authority.todo_snapshot_page` endpoint remains compatible.
Its pages, and capacity fallback pages, contain up to 4,096 Todo/lease records and
1,792 KiB of UTF-8 JSON. That page budget still fits the original 2 MiB wire
with 256 KiB for its envelope; it does not grow with the shared 8 MiB RPC ceiling
described below. Normal collections can still complete in one page. A record
and its acceptance guard travel together; records are never shortened to meet a
budget. Archived dependency/decision records remain in the full collection.

Todo IDs use the authority codec's deterministic Unicode code-point ordering.
Todos precede leases, with separate offsets and counts. A canonical empty
collection is a complete result; it does not authorize Markdown fallback.
Pagination is a transport mechanism, not a UI display limit or a partial domain
query. Summary computation still receives the complete population.

Status shares one checked revision between contract diagnostics and its Todo
summaries. Its internal reply carries the diagnostic fields for every record,
including archived records, alongside the existing summaries and acceptance
facts. The response does not carry a second copy of the full records. Exact
Todo detail and summary replay still use their original full-source reads.

状态读取的合同诊断与 Todo 摘要使用同一个已校验的版本。内部响应为每条记录（含归档）
返回诊断所需的字段，并保留原有摘要及验收事实，响应不再另附完整记录。
精确 Todo 详情与摘要重放仍使用原有的完整来源读取。

Each request has `schema_version`, `runtime_root`, `goal_id`, `include_leases`,
`projection_readback` and `after`. The first `after` is null; subsequent calls
send the previous page's `next` unchanged. A continuation contains:

`projection_readback` is the typed confirmation request (`provider_revision`,
`changed`, `attempt`, `target`); it carries the caller's pinned-or-latest intent
and attempt budget, so the page returns one confirmation for the same snapshot
as every page instead of letting the display lock hold a provider commit.

| Field | Meaning |
| --- | --- |
| `snapshot.goal_id` | Goal whose complete collection is being read |
| `snapshot.store_identity` | Provider incarnation; replacing a store invalidates old positions |
| `snapshot.provider_revision`, `snapshot.cursor` | Exact committed authority version |
| `snapshot.query_sha256` | Digest of Goal, lease inclusion and projection-confirmation request |
| `snapshot.todo_count`, `snapshot.lease_count` | Complete populations at that version |
| `todo_offset`, `lease_offset` | Already consumed prefixes within that snapshot |

A page carries the same snapshot, collection metadata, complete `todos`, optional
`leases`, page-local `goal_acceptance_work_guards`, and `next` (null at the end).
Metadata includes the existing read-model declaration, acceptance projection,
handoff mode and requested projection readback. The old direct TS `todo_list`
endpoint remains compatible. The complete-read and wire-only entrypoints share
the same request, snapshot, metadata and page schemas, including a null `next`
for a complete answer. The Python adapter's public result shape stays unchanged.

## Consistency and recovery

The provider is checked again on every page. If a writer commits between pages,
`canonical_snapshot_changed` fails the whole read even when that write happens
to leave Todo data unchanged. Consumers discard every earlier page. The caller
may start a new complete read; the adapter does not silently loop or retry
against a moving head. This also rejects changed queries and store identities.

The Python transport checks page identity, unchanged metadata, count/offset
agreement, progress, record order, duplicates and guard membership. Invalid
transport returns `canonical_snapshot_result_invalid` without partial rows.
The existing TS domain read-model and acceptance owners continue to validate
semantics; Python does not acquire a second Todo rule engine.

A missing local File provider is opened with `existingOnly`; attempting a read
cannot initialize a replacement identity or directory. It still returns the
existing `missing` result, so callers retain the canonical-authority recovery
path instead of treating absence as a new identity error. An existing head with
an unreadable identity remains an identity failure. Selected SQLite and
PostgreSQL profiles retain their existing identity checks and provider failures.
No failure path falls back to a display file.

A single record or metadata envelope that cannot fit a fallback page is rejected with
`canonical_snapshot_record_too_large` or
`canonical_snapshot_metadata_too_large`. These explicit limits do not qualify
arbitrarily large individual records. A complete snapshot within private-file
capacity can carry records or metadata beyond the inline page limit; the
standalone wire-only endpoint retains its previous rejection behavior.
Projection confirmation remains tied to
the snapshot read; pending delivery must not trigger a second business mutation.

## Cost and qualification boundary

This implementation bounds **transport responses**, not the whole collection's
memory or provider IO. A complete private-file read loads and validates the head
once. Capacity fallback and standalone wire pages load and validate the current
head on every page; Python retains the assembled collection. A busy writer may force a caller to
restart. Database cursor streaming or retained read transactions would need a
separate provider lifecycle/cleanup contract and are not implied here.

The budget search measures `Buffer.byteLength(JSON.stringify(page), "utf8")`.
It checks the complete candidate first: removing `next` on a final page can make
the envelope smaller, so byte size is not globally monotone at that last step.
The remaining prefix search retains a continuation and is monotone. Raising the
RPC budget or dropping metadata is not the recovery strategy.

Qualification covers native/imported complex populations, archives, leases,
acceptance guards, complete/wire result parity, capacity fallback, store replacement
during complete admission, overlapping commits, query/incarnation mismatch, malformed
continuations, Unicode ordering and real File/SQLite RPC-to-CLI readback.
The shared authority-store conformance registers the same snapshot tests for
PostgreSQL and other real providers. The real PostgreSQL suite requires an
isolated server/database; a skipped run is not qualification.

This closes the bounded collection-read part of T3/L5. It does not establish D1
permanent projection freshness, SQLite D2 durability/soak, D3 whole-Goal
migration, or default-on qualification. See the
[remaining PR sequence](../architecture/rfcs/shared-goal-authority-state-provider-v0.md).

## Shared RPC byte budget

Ordinary Python/TypeScript Effect requests and inline responses admit up to
8 MiB, including the newline-delimited envelope. Python emits compact UTF-8
JSON instead of ASCII-escaping every non-ASCII character. JSON values, escaped
control characters and lone-surrogate compatibility remain intact. The server
counts UTF-8 bytes and searches each new socket chunk for the delimiter, then
parses the complete first frame once. Earlier chunks cannot contain that
delimiter; repeatedly searching the accumulated string needlessly flattens it.

This is an intentional increase from 2 MiB. A complete digest-bound source
containing Chinese text and emoji occupied 1,000,387 UTF-8 parameter bytes but
2,400,387 ASCII-escaped bytes: the old ordinary call refused before dispatch,
while the explicitly allowed snapshot call wrote the inflated parameters.
The UTF-8 call returns the same complete source, including its final constraint.
It does not shorten Goal text or remove required reads to fit a budget.

The capacity decision compared 2, 4 and 8 MiB on the same disposable Node 24
worker with the UTF-8 encoder held fixed. Each trial read complete, hash-bound
1/3/6 MiB sources, then four concurrent 6 MiB sources; a separate Python process
sent small pings. Three uninstrumented trials per variant gave:

| Ordinary cap and delimiter scan | 6 MiB warm roundtrip (ms) | Worker peak RSS (MiB) | Busy ping observed p95 (ms) |
| --- | ---: | ---: | ---: |
| 2 MiB, accumulated string; snapshot fallback | 68.7–79.5 | 196.8–218.0 | 20.8–26.8 |
| 8 MiB, accumulated string; inline | 79.5–85.8 | 254.7–320.7 | 154.7–203.5 |
| 8 MiB, new chunk only; inline | 57.7–60.0 | 214.6–222.4 | 28.0–66.8 |

Raising the cap alone was rejected. V8 samples identified the repeated data
callback work; changing only the delimiter scan removed that hotspot, and the
uninstrumented comparison confirmed lower cost. The selected 8 MiB cap lets
the complete 3/6 MiB sources stay inline. The 4 MiB candidate still sent the
6 MiB source through a private file. Larger inline calls retain a concurrency
cost: in a separate four-by-3 MiB workload, busy ping maxima were 72–101 ms
with 8 MiB versus 17–19 ms with 2 MiB and snapshot fallback. These short local
samples are observations, not a stable tail-latency SLO or long-horizon/model
qualification. Roundtrip includes encoding, safe refusal/fallback when needed,
transport and result validation; it excludes fixture preparation and startup.
Peak RSS covers the worker's whole trial, including startup. The four large
callers share one Python process; only the ping caller runs independently.

The 64 MiB opt-in private-file capacity, method allowlist, same-UID ownership,
permissions, digests, cleanup, deadlines and ambiguous-response recovery remain
unchanged. Over-limit ordinary requests still refuse before dispatch; a possible
commit followed by an oversized or lost response still requires receipt recovery.
Pagination budgets and frozen experiment/promotion criteria remain independent.
These transport changes add no capability, setting, execution or merge authority.
Existing consumers that derive a limit from `MAX_REQUEST_BYTES` follow the same
8 MiB budget: context-capture/native-input file and event readers, Todo summary
and handoff batches, and task-graph/replan-history transport selection. Their
source identity, file/privacy, digest and replay checks remain in force. Host
loading limits, progressive presentation budgets and frozen experiment bounds
are separate; increasing the shared wire does not change them.

普通 Effect RPC 的请求和内联响应上限从 2 MiB 放宽到 8 MiB，按包含换行封包的
实际 UTF-8 字节计量。保留完整正文、JSON 值及孤立代理项兼容，不删约束或必要读取。
同时只扫描新增 socket 块中的换行，避免反复展开累积正文；单独提高上限的内存和
延迟回退保留为失败对照。8 MiB 可让完整 3/6 MiB 来源走内联路径，但并发大请求仍
可能增加小请求等待；以上为短时本机观测，不代表稳定尾延迟、真实模型或长程收益
已验收。64 MiB 私有文件容量、方法和权限边界、摘要、清理、超时及不确定结果恢复
不变；分页预算与已冻结实验／晋升条件独立，不追认历史失败。
既有引用共享常量的上下文捕获／原生输入读取、Todo 批次与 task-graph/replan-history
传输选择同步沿用 8 MiB；来源身份、文件隐私、摘要和重放检查保留。宿主加载和渐进
展示预算各自独立，不随 RPC 上限放宽。
