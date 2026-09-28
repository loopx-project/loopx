# Targeted diagnosis and command cost

Choose the missing fact, then the existing owner that can answer it. Preserve
the current Goal, runtime/registry route, Agent and Turn identity; diagnosis
does not authorize borrowing another session's identity or changing providers.

| Missing fact | First useful surface |
| --- | --- |
| Why the current operation was rejected | Its existing JSON error, typed recovery and operation receipt; look up the error before opening other projections |
| Whether an ambiguous Todo create committed | `loopx --format json todo receipt --goal-id <goal> --operation-id <original-id>`; inspect the receipt before any retry with the same identity |
| Current lease ownership/version | `loopx --format json task-lease inspect --goal-id <goal> --todo-id <todo>` |
| One Todo's current state | `loopx --format json todo list --goal-id <goal> --todo-id <todo>`; use `--agent-id` when needed by the existing lane |
| Why an existing quota packet selected recovery | Inspect that packet's recovery and interaction fields first; request fresh quota when binding/settling the current Turn or after a relevant state change |
| Overall unexplained Goal health | `loopx --format json diagnose --goal-id <goal> --agent-id <owned-agent>`; this composes status and quota reads |
| A dashboard discrepancy | `loopx --format json status --goal-id <goal> --limit 5` |
| Recent execution order | `loopx --format json history --goal-id <goal> --limit 5` |

These are alternatives, not a sequence. Use the resolved registry/runtime
options for all calls. `quota should-run` may establish host-Turn state; it is
not a harmless latency probe. Never remove a Turn id, capability declaration,
lease proof, revision or receipt check to make a command faster.

## Read one response more than once, not one command

When JSON may exceed the tool output budget, capture it once in an ignored
private file with restricted permissions, or in the tool runtime's value store.
Keep the command exit status and validate that the capture is complete JSON.
Select fields from that saved response; if you need additional fields, read
the same capture. Do not rerun a costly or stateful command just to increase
`max_output_tokens`. A tool's truncated display does not mean the command
failed or did not commit.

Use server-side selectors where they exist: `todo list --todo-id` for a known
Todo and a bounded `pr-review --limit` for queue selection. A shell `jq` filter
reduces displayed text but does not avoid constructing the full upstream
response. Keep total/completeness markers when limiting discovery; a bounded
page cannot prove the absence of other work. Do not silently truncate evidence
used for authorization, review or settlement.

On an ambiguous write, follow its typed recovery contract. An absent receipt
does not prove that a timed-out writer stopped: retain the original operation
id and payload, wait/inspect as directed, and avoid concurrent retries. After
a relevant mutation, refresh the facts needed by the next operation; a cached
diagnostic is not current authority.

## Measure the right latency

Distinguish command start-to-exit duration from the tool's initial yield or
poll wait. Separately record response bytes, output truncation, read count and
time between useful operations. A fast `cat` can still consume substantial
context and reasoning time when its output is repeatedly expanded.

Before changing runtime budgets, identify the installed source revision and
provider, compare the same workload on base/head, and separate startup, lock
wait, backend verification and response construction. Use an isolated runtime
and synthetic fixture or authorized read-only snapshot. Preserve integrity,
receipt recovery and lease/CAS semantics; do not benchmark by mutating an active
Goal. Check existing PRs before starting an overlapping store refactor.

A different registry, `--runtime-root`, archive destination or worktree does not
isolate the Effect server: identical source fingerprints can reuse the same
process through the user's temporary directory. Follow the testing guide's
**Isolate the managed Effect process as well as the data** procedure: use a
private existing directory for `TMPDIR`, `TEMP` and `TMP`, account for Python's
cached `tempfile.tempdir`, and check the serving PID before heavy work. Stop
only the isolated server after requests settle. If a rehearsal shared the live
server, retain its correctness/receipt evidence but exclude affected timings;
resample without overlapping heavy work before attributing a regression to a
provider or upgrade. Process isolation alone does not remove host CPU contention.

When unrelated lightweight rules and `runtime.ping` slow down together, test
shared event-loop starvation before attributing the timeout to the named rule.
Compare cold, warm and alternating-Goal reads in a separate runtime using fixed
snapshot bytes; capture a CPU profile there, not by restarting a shared live
service. A hot cache can hide full-history CPU work. Keep the original response
budget, preserve uncertain-write recovery, and distinguish lower CPU cost from
cooperative scheduling. Yielding between verified transactions must not publish
an incomplete proof; concurrent identical reads may share only an exact-input
in-flight proof, with failures removed so a later read can revalidate.

Searchable reference lookup has no Goal authority and can stay in the skill.
Runtime admission, recovery decisions and provider integrity remain in their
existing typed owners. This is the S10 diagnostic-efficiency boundary alongside
S2 transaction migration; shorter prompts alone do not qualify provider cutover.

## Provider-neutral optimization boundary

Apply query selection and response reuse above the provider interface: File,
SQLite and PostgreSQL all benefit when consumers avoid redundant requests.
Reuse within one observation only; a provider revision, current lease or CAS
precondition must still come from its authority owner. Do not add a Python
cache that bypasses the typed owner, or assume different providers share a
filesystem invalidation rule.

Separate this from storage-specific work. Current File checkpoint/delta
verification and replay, SQLite transactions/indexes, and PostgreSQL queries and
network round trips have different costs. Identify the actual stored format;
do not apply retired File-v0 whole-history-write assumptions to File-v1.
Prove a shared optimization through
the common read/transaction contract, then qualify each affected real backend.
Preserve original-receipt recovery, stale-revision rejection and missing-state
fail-closed behavior. A successful promotion establishes authority ownership;
it does not establish latency or capacity equivalence to the previous path.
