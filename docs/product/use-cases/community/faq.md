# LoopX community FAQ

[简体中文](faq.zh-CN.md) · [Try a task in your assistant](README.md)

## Where does the state live?

For one computer, start with local SQLite. A new Goal using the unconfigured
defaults shown in the [SQLite developer exercise](manual-cli.md) gets one SQLite authority
database in the selected runtime. It holds the durable work state, including
Todos, leases and operation receipts. SQLite may create WAL and shared-memory
sidecars while open. Git files, the registry and host session artifacts live in
their own locations.

Use `loopx --format json todo list --goal-id <goal-id>` from the project that
owns the Goal. Check `authority_read.source_authority` for `sqlite_v0` on the
SQLite path. Existing Goals keep their recorded provider. Use the
[storage selection and migration guide](../../../reference/local-authority-provider-selection.md)
before moving them. Your agent host runs the model and tools. LoopX retains
the work state used to decide what can continue after that host stops.

## When should a stronger model explore and a cheaper one replay?

Use a stronger model when the route is open-ended, the evidence conflicts, or
the failure calls the plan into question. Have it leave a reviewable
contract with a versioned input, the action and its limits, an acceptance
check, and a condition to return control. A cheaper model can then apply that
contract to another eligible item. Run a deterministic check directly when
it can do the whole job.

For example, first work out which test distinguishes a regression from a
stale fixture. Then hand off the test, the exact fixture version and the
expected result. A worker can repeat the check and attach evidence. A changed
fixture, an unexplained failure, or newly required authority sends the task
back for a decision. A write with an uncertain result needs the original
receipt readback before another attempt.

Compare accepted results on the same tasks and budgets. Count cached and
uncached input, output, extra calls and recovery, retries, elapsed time, and
human review. Report any unknowns. The [measurement guide](../../../guides/context-delivery.md#a-minimum-discriminating-study)
shows how to compare model and context choices under declared conditions.

## What does Context Delivery save?

The current decision gives the agent an opening read and an index into an
immutable capture, a saved copy of the applicable context. Read the required
facts for the action at hand. When more detail is needed, use that capture's
returned reader and follow its pages. For a compatible continuation,
verified saved-file differences can reuse unchanged data. A lost context
baseline can require a full read again. Validate scope and freshness before
using a source to make a decision. See the [operator guide](../../../guides/context-delivery.md#read-and-recover-the-right-object).

| If a status looks current but… | Look for |
| --- | --- |
| the underlying source is old | When the source was last read, against its freshness window. |
| a required source was never read | Its missing or unbound read status. |
| a capture producer is failing | Last success time, separately from the last check time. |
| a read returned partly unreadable records | The unreadable count and affected source. |
| the summary covers the wrong registry or only part of the scope | Expected scope, actual coverage and typed omissions. |

The [five-incident postmortem](https://loopx-project.github.io/loopx/blog/five-freshness-incidents/)
walks through these checks. A current timestamp can coexist with incomplete
coverage, so inspect both.

The public eight-task, four-arm Context ON audit reports **3.0% for the first
read body and 5.0% including CLI JSON**. It sums 374,449 and 630,550 bytes
against approximately 12.53 MB of full packets for **112 in-cycle
continuations**. The 213 total wakes also include 101 new-session or
post-compaction baseline reads. End-to-end model tokens, cost and task outcomes
have their own measurements. [Aggregate and source revision](https://github.com/loopx-project/loopx/blob/b9f2e2a516b58e62cbb0da6978625388322b5a90/apps/presentation/site/public/blog/data/long-horizon-audit.json).

The **November 12, 2026, 22:40 CST** EdgeBench snapshot lists Context ON ahead
or marginally ahead on **7 of 10 rows**. Rust is a small native-score edge
(27.1801 vs 27.1757) and ties at two decimal places. OFF leads on Portfolio,
Codec and OpenTTD. Only four reported pairs are within 15 minutes of one another
near 18 hours. The other six involve historical runs, early stops or incomplete
verification. Treat the tally as a description of this snapshot. Isolating
the effect requires the exact versions, treatment, evaluator and budgets for
both arms. The public aggregate also leaves ANN OFF's 3,746 QPS and both
actual durations unverified. [Revised score table and provenance](https://github.com/loopx-project/loopx/blob/b9f2e2a516b58e62cbb0da6978625388322b5a90/apps/presentation/site/public/blog/context-delivery/index.html#L114-L206).

Check the [2.3.0 release](https://github.com/loopx-project/loopx/releases/tag/v2.3.0)
and your installed host for the supported native path. Later changes on
`main` and research measurements carry their own revisions.

## Can different people share PostgreSQL and wait for approvals?

The [remote authority task service](../../../reference/remote-authority-task-service-v0.md)
documents an opt-in source-checkout candidate. It accepts authenticated
claim, progress, lease and read operations through a service. PostgreSQL holds
the authoritative transactions and receipts. A compare-and-set (CAS) check
prevents an outdated writer from committing against a newer state. If a
response is lost, read back the original operation under the same identity
before retrying. Evidence for the candidate includes real-backend tests with
independent clients, but enabling cross-host execution requires its own deployed
host and service qualification. The [current scoped-read candidate](https://github.com/loopx-project/loopx/blob/c3e3ac56586fbbc686e3e2d01b979fe59a62dd19/docs/reference/remote-authority-task-service-v0.md#scoped-todo-reads)
adds explicit Todo and audience checks. Its scoped listener rejects writes.

[Open PR #8747](https://github.com/loopx-project/loopx/pull/8747) proposes broader resource
access control for cross-person collaboration. The first candidate's coarse
scope is tenant, Goal, actor and allowed operations, pinned to a store
incarnation. The proposal intersects principal grants, credential scope,
resource ACL, and applicable delegation/source permissions. Missing or
unverifiable grants are denied. Resource access, execution leases and budgets
continue to use their respective authority checks.

A human Gate can name one decision, such as whether to publish an already
reviewed note. Keep the publication Todo waiting with the exact decision
and target visible. An independent verification Todo can continue under its
own admission. Approval resolves the named Gate. The publish step still needs
applicable execution authority. [Work graph and Gate scope](https://github.com/loopx-project/loopx/blob/c3e3ac56586fbbc686e3e2d01b979fe59a62dd19/docs/book/en/chapters/work-graph-and-authority.md#L31-L43).

The [community assistant evaluation](golden-queries.md) uses these sources for
onboarding and boundaries (CQ01–03, CQ05, CQ08), versioned mechanisms and
experiments (CQ11, CQ14), and collaboration and durable FAQ updates (CQ15,
CQ20). Check the version in the question before quoting an answer.
