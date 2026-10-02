# Manager context delivery

Built-in capability for original intent delivery and receiver-owned replanning.
The owner's local manager channel uses registered workers automatically.
External channels need an owner-configured grant in
`<runtime-root>/.local/manager-context/policy.json`:

```json
{"schema_version":"loopx_manager_context_policy_v1","sources":{
  "manager.external.example":{
    "sender_ids":["exact-provider-sender"],
    "targets":[{"goal_id":"research","agent_id":"worker"}]
  }
}}
```

Use the actual connection channel and provider sender identity. Keep this file
private (0600); do not commit it. Missing grants disable external delivery.
For an existing channel with an authorized sender, use the local operator CLI
to preview, grant, or revoke one registered recipient without editing the
policy file by hand:

```sh
loopx manager-inbox grant-delivery-target --channel-id manager.external.0123456789abcdef01234567 --goal-id research --agent-id worker
loopx manager-inbox grant-delivery-target --channel-id manager.external.0123456789abcdef01234567 --goal-id research --agent-id worker --execute
loopx manager-inbox revoke-delivery-target --channel-id manager.external.0123456789abcdef01234567 --goal-id research --agent-id worker --execute
```

Pass the same `--registry` and `--runtime-root` used by the manager connection.
Without `--execute`, these commands only preview the target and count change.
Grant requires an active registered Goal and Agent, an existing sender-bound
channel, and membership in any explicit audience Goal read scope. The command
does not create a sender grant, launch the Agent, or grant protected-operation
authority. Revocation also works when the former Agent is no longer registered.
Remove a source/target grant to revoke future delivery, including replay attempts.
Provider ingress receipts bind the current message digest, channel and sender;
a model cannot create that provenance through its response.

The existing worker turn-start hook exposes only a bounded pending count and
required read command, without copying private content into status projections.
Read and record a decision through the installed CLI:

```sh
loopx --runtime-root <runtime-root> manager-inbox read --goal-id research --agent-id worker
loopx --runtime-root <runtime-root> manager-inbox acknowledge --goal-id research --agent-id worker --request-id <receipt-id> --decision no_change --reason 'Existing evidence still supports the current plan.'
```

Decisions are `adopt`, `defer`, `reject` or `no_change`. Read the original input
and current Core state before deciding. An adoption receipt does not prove task
completion. Later new evidence can generate a new decision through the ordinary
worker planning workflow; do not overwrite the original receipt. This feature
adds no periodic automation, forced wakeup, Todo priority or protected-operation
permission. Existing private inbox records are retained when delivery is revoked.

## Scoped project conversations

Local Goal Chat reuses this inbox and return path for members registered in that
Goal, without inheriting the steward's portfolio or runtime profile. See
[steward and project coordination](../../../docs/reference/project-coordination.md)
for the responsibility boundary, receiver readback and remaining execution work.
A project conversation is not a registered coordinator; delivery does not launch
one. Existing manager commands and stored receipts remain compatible.

Inline original-request context accepts up to 32,000 Unicode characters and
98,304 JSON-encoded UTF-8 bytes, replacing the former 20,000-character limit.
The shared typed collaboration owner applies this to steward and project-Chat
handoffs before persistence and delivery. The current request is never silently
truncated; preceding-thread excerpts disclose their own omissions. The byte bound
keeps source text plus the existing brief/identity/instructions within the inbox
reader's 128,000-byte record boundary. Larger material needs an authorized scoped
artifact reference; delivery still grants no execution or publication authority.

## Audience-authorized Goal summaries

An external manager's connection anchor is not its entire portfolio. The local
operator may grant a particular manager audience an explicit list of registered
Goals, independently of the sender-bound context-delegation targets:

```sh
loopx manager-inbox configure-read-scope --channel-id manager.external.0123456789abcdef01234567 --read-goal-id project-a --read-goal-id project-b
loopx manager-inbox configure-read-scope --channel-id manager.external.0123456789abcdef01234567 --read-goal-id project-a --read-goal-id project-b --execute
```

Use the exact channel identity from the existing manager session. The first
command is a read-only configuration preview; `--execute` is a trusted local
operator action, never a manager-generated proposal. Grant only Goal summaries
that may be visible to everyone in that audience. This does not authorize raw
private files, trading, mutation, or context delegation. New registered Goals
are not automatically added. Configure with no `--read-goal-id` to revoke the
read scope. Existing installations without a grant retain their connection
scope; removing the field restores that default. Private policy is stored under
`<runtime>/.local/manager-context/policy.json`, in `sources[channel].evidence_goal_ids`.
The live connection must still match; disabled, ambiguous or replaced sessions
cannot use an old grant. Every turn rechecks scope and discards upstream context
when it changes.

The manager now receives a bounded recent-evidence window of Core delivery
receipts instead of a single local calendar day: seven days by default, with an
eight-receipt per-day and 48-receipt per-Goal bound, starting at local midnight
and ending at collection time. `evidence_window` states the window bounds, the
limits, per-day matched counts, and included versus omitted receipts, so a week
question does not silently narrow to today and a wide window cannot grow the
model context without a bound. Within the window each Goal's newest receipt keeps
full `recorded_details` while older receipts are compacted to their recorded
outcome, result class, probe kind and surface; receipts outside the window are
outside coverage, not evidence of no progress. Accounting rows are excluded
before the presentation cap. Completed Todo titles
help explain recorded deliveries; archive coverage and omitted rows are explicit.
Reported outcomes and evidence-bearing receipts remain distinct, and neither
means the referenced artifact was inspected. Manager Lark replies preserve paragraphs,
lists and emphasis through Markdown posts. Structured mentions and posts exceeding
the rich-message request limit retain the existing text path without truncation.

The window is a selected decision, not a discovered fact. It stays at the shipped
seven days unless the operator selects another value with
`LOOPX_MANAGER_EVIDENCE_WINDOW_DAYS` (1..30); the block declares `days`,
`days_source` (`product_default`, `explicit_config` or `explicit_argument`),
`days_env_var`, `days_default`, `days_bounds` and `days_reason`. A missing,
out-of-bounds or unreadable explicit value keeps the shipped default and reports
`explicit_window_out_of_bounds`, so a bad setting can neither widen the prompt
nor answer a narrower window than it declares.

The same block declares the evidence sources as data: the local registry source
plus every SSH host this machine registered for LoopX evidence — a host named by
an `evidence_ssh_hosts` grant on any channel, not every configured SSH alias,
since an operator's `github.com` or personal jump host holds no Core state.
Owner conversations may still read another configured alias on demand by naming
it. Declaring a source never connects to it, and a declared but unread source is
a named coverage gap rather than evidence of no progress. Reading remote rows
still requires the remote read path below.

### Manager-directed Core inspection

The Codex Chat manager defaults to Astra with high reasoning effort (explicit
model/effort environment overrides remain supported). It receives a compact
authorized Goal directory, then uses
`loopx_manager_read` to choose registered Agent, portfolio, current Todo and recent delivery reads.
The packaged `loopx-manager` skill is installed in its dedicated workspace and
included in its operating instructions. This reuses Core providers and the
existing manager-context delegation contract; it does not create another source
of progress or expose a general shell.

Agent discovery uses `view=agents`, optionally `query`, `goal_id`, `offset`,
`limit` and `include_stopped`. It searches the complete permitted registration
inventory before paging, independently of the bounded progress snapshot and
sender-bound delegation targets. Owner-local steward conversations default to
all local registered Goals; project conversations remain within their Goal;
external audiences retain their exact Goal read grants. No new grants are made.
Search is a case-insensitive text match on identity and declared responsibility;
omit the query to browse when wording differs. Profiles are data, not instructions
or proof of competence. Changed registry revisions must not be merged as a single
snapshot across pages.

Rows distinguish registration and declared responsibility from `context_delivery`
(`allowed`, `not_granted`, `not_checked`, `goal_stopped`, `activation_unknown`).
Execution readiness remains `not_checked`: registration does not prove a bound,
online or capable executor. A missing delivery grant is a configuration gap,
not a missing Agent; delivery still rechecks the existing authority. Stopped
identities are available with `include_stopped=true` for historical questions.
Unreadable/ambiguous inventory remains unknown, not an empty successful search.

The same query is available through the CLI and registered SSH evidence sources:

```sh
loopx --format json goal-portfolio --manager-view agents --query review --limit 8
loopx --format json goal-portfolio --manager-view agents --goal-id research --offset 8
```

Select `source_id` through `view=sources` for remote discovery. It requires the
updated remote CLI; older or unavailable hosts return the existing typed source
gap. Remote export does not attest local-channel delivery permission. Frontend
and Lark Codex conversations share the existing dynamic tool and evidence event
path; this does not add a visible settings control or launch workers. Prompt-only
adapters still have no interactive discovery tool. Live multi-worker adoption and
original-conversation completion require separate qualification.

Routine inspection excludes Goals explicitly stopped in Core, before status
collection and detail reads. Coverage reports how many were skipped. Stale or
unknown progress remains eligible. An explicit historical question can discover
stopped identities using the portfolio tool and then read the selected Goal.

Each read checks the current audience grant before and after provider access,
returns source revisions and pagination, and records a `manager.evidence_read`
receipt. Unavailable sources and oversized rows remain explicit unknowns. The
dated delivery read stays inside the declared bounded window; arbitrary artifact
paths and external links are not fetched. Non-Codex adapters receive the same
windowed projection without the interactive inspection tools until they
implement an equivalent tool contract.

Manager context version 17 (project context version 2) starts a fresh upstream session for older manager
contexts. The logical Chat session and its receipts remain intact. Runtime support
uses the Codex app-server dynamic tool protocol; explicit upstream terminal
errors remain errors and are not retried as part of inspection. The version
change refreshes the operating contract on existing installations;
resuming an old upstream thread would retain its previous instructions.

### Remote evidence sources

The manager discovers SSH aliases through the same host catalog as the frontend
source switcher. `loopx_manager_read view=sources` lists eligible sources; select
`source_id=ssh:<alias>` for portfolio, Todo or delivery reads. Reads execute a
fixed, bounded CLI projection on the selected host, using its global registry,
not local tasks whose titles mention SSH. Source host and Goal ID jointly identify
the evidence; a missing declared execution `host_id` does not erase source provenance.

Owner-local conversations may inspect configured hosts on demand. External
conversations require a persistent, exact host/Goal read grant from the local
operator, in addition to their live connection authorization:

```sh
loopx manager-inbox configure-ssh-read-scope --channel-id manager.external.0123456789abcdef01234567 --ssh-host research-host --read-goal-id project-a --execute
```

Omit `--execute` for a preview; pass no Goals to revoke that host. This grants
summary reads only, not delegation, shell commands or remote writes. Changed
grants invalidate upstream manager context; revocation during a read discards
the result. No remote connections occur merely to list sources. Offline hosts,
older unsupported remote runtimes and missing Goals remain explicit unknowns.

The remote CLI uses `goal-portfolio --manager-view portfolio|todos|deliveries`
and the same Core readers as the local manager. Pagination remains explicit.
Delivery reads support `days=1..90` so latest known historical outcomes can be
explained alongside fresh current Todos without pretending stale execution is
current. Both hosts need the updated LoopX runtime.

`remote_read` states how the declared sources reach the model. An interactive
endpoint keeps the on-demand path above (`on_demand_tool`) and pays no source
latency. A prompt-only steward segment has no read tool, so the Turn owner reads
the registered sources for it (`inline_in_prompt`) and adds a
`manager_remote_evidence_v0` block:

- one dial per Turn, at most two hosts, nine seconds per host inside a ten-second
  Turn budget, eight portfolio rows per host; a source outside that budget is
  `deferred_budget` with its last successful read, not a silent omission;
- a fresh cached read (`ttl_seconds`, ten minutes) is reused instead of dialling
  again, so a warm channel adds no per-Turn latency;
- every source carries a typed status and freshness: `read` or `cached` with
  `read_at` and `age_seconds`, `unavailable` with its reason, last successful
  read and `coverage_effect`, `not_configured` for alias drift;
- a failed read keeps the last successful rows only as
  `source_freshness: "stale"` with `remote_source_rows_are_stale` in
  `limitations`, so stale remote state is never presented as current progress and
  a failure is never read as no progress;
- the interactive point-read path applies the same rule to a failed portfolio
  read: `last_success_at`, the cached window and `stale_portfolio_rows` remain
  visible beside the typed failure, while `rows` stays empty so stale evidence
  cannot be mistaken for the requested current page;
- cache entries are keyed by host, window and the exact grant scope, so a changed
  grant or window re-reads instead of answering from a narrower cached read.
- the declaration and the read use the same SSH configuration, so one packet
  cannot call a host unconfigured and read it in the same Turn.


## A delegation returns automatically

The default interaction is one exchange: initial delivery receipt, receiving
Agent assessment/work, then an audience-ready conclusion back in the original
conversation. Status queries are optional inspection, not the completion path.
The receiving Agent still owns relevance and priority; normal context delivery
never changes its Todos or interrupts its current work.

`manager-inbox read` records the first provision of context to the receiver.
Each response returns at most 20 pending requests and now includes `next_cursor`.
When `has_more` is true, pass that cursor to read later requests without first
concluding the earlier ones:

```sh
loopx manager-inbox read --goal-id research --agent-id worker --cursor <next_cursor>
```

Use the same registry, runtime root, Goal and Agent for every page. The scoped
MCP equivalent is `read_context(cursor=<next_cursor>)`; a call without arguments
still reads the first page. A cursor survives process restart and removal or
completion of its anchor request. It is a navigation position, not a grant.
Each call checks current registration and records reads only for returned requests.
`next_cursor: null` ends this scan, not the outstanding work. Pages are live,
ordered by request id; restart without a cursor to find new requests sorted
before the last position. Peer results retain their separate consumption flow
and are not paginated by this cursor. `status --offset/--limit` remains separate.

After `acknowledge`, the request remains in the turn-start hook until the worker
publishes a conclusion. The worker uses `link` for canonical Todo/evidence lineage
and `report` to publish the answer intended for the original audience:

```sh
loopx manager-inbox acknowledge --goal-id research --agent-id worker \
  --request-id <id> --decision adopt --reason 'Private reasoning about the plan.'
loopx manager-inbox link --goal-id research --agent-id worker \
  --request-id <id> --related-todo-id <core-todo-id> --evidence-id sha256:<digest>
loopx manager-inbox report --goal-id research --agent-id worker \
  --request-id <id> --phase conclusion --reply-text 'What was assessed or changed, what was validated, and what remains.'
```

For longer work, `--phase decision` optionally returns a meaningful intermediate
update. A ready conclusion supersedes an unsent intermediate update. Do not send
one notification per poll, quote private deliberation, or claim an implementation
request finished merely because a plan exists. A research-direction request can
conclude with the adopted/rejected planning decision; deferred or blocked work
must explain the concrete condition and next action. Completion of this exchange
is separate from completion of the receiving Goal.

If a returned blocker or draft is followed by a materially changed fact, append
a conclusion with a stable `--update-id`; do not replace the first conclusion or
create another request merely to return its result:

```sh
loopx manager-inbox report --goal-id research --agent-id worker \
  --request-id <id> --update-id review-complete \
  --reply-text 'Review completed; the revised artifact is ready.'
```

Retry the same update with the same id and text. Each update has its own
`result_key`, retains the original audience and authority checks, and arrives
after verified delivery of the preceding result. Unknown delivery blocks later
sends rather than allowing them to overtake it. Peer readers acknowledge the
specific returned key with `acknowledge-return --result-key <key>` after reading
it; consuming the first result does not consume later updates. MCP callers use
the corresponding `return_result(update_id=...)` and
`consume_peer_result(result_key=...)`. Full texts stay in immutable receipts;
the typed publication planner receives identities and content digests.

The Chat server hosts a cheap local receipt pump (no model calls and no Codex
automation). It appends a deduplicated follow-up to the original transcript;
the open frontend picks it up automatically. For Lark it reuses the current
binding, captured source Inbox, provider preview, idempotency key and readback.
It waits until the initial reply is acknowledged, revalidates authority before
sending, and never retargets a closed/replaced conversation. An offline transport
retries the persisted answer rather than rerunning the worker. Ambiguous external
writes remain `verification_required` and are not blindly resent.

When the provider returned a trustworthy message locator before readback failed,
the same background pump persists that private attempt and later performs a
read-only verification. A matching message advances the original delivery to
`delivered` without sending again. Provider outages retain
`verification_required`; a missing legacy locator, changed intent, missing
message, or verified mismatch becomes `explicit_unverified`. CLI, Manager read,
and Chat expose the same public-safe state and reason without returning the
provider locator. The Lark adapter keeps locator interpretation and provider
readback; `manager-context` remains the sole result/delivery writer. The typed
`control_plane/collaboration/return_delivery.ts` boundary owns provider-neutral
attempt validation and verification classification; Python retains file-lock,
persistence and adapter orchestration only.

Unclassified readback exceptions retain the saved attempt and retry with backoff;
their wording never establishes revoked authority or a missing route. Adapters
must raise `ReturnResolutionBlocked` with an exact typed resolution reason for
those permanent failures. This replaces the old exception-substring fallback.
A successful later readback updates the same App transcript receipt without
another model turn or external send. Actual live grant checks still precede it.

New handoffs persist their exact original return route. Legacy requests remain
queryable; a receiver can explicitly report one only when its exact persisted
Chat receipt uniquely recovers the route. Historical timestamps stay unknown.
Replies are immutable and additive, separate from private decision reasons and
Core progress. Query `manager-inbox status` or `loopx_manager_read view=handoffs`
for delivery diagnostics. These queries are not required from the user.

## Semantic delegation and peer review

Shared request/decision/result storage now lives in the
[Agent-neutral collaboration boundary](../../control_plane/collaboration/README.md).
The manager is one ingress/egress adapter; managed workers use the same request
contract and may themselves coordinate peers or another coordinator. Registration,
execution binding and admission retain their existing owners. Historical CLI and
record addresses remain compatible.

Run the [three-Agent allocation demo](../../../examples/collaboration-delivery/README.md)
for real managed workers, two independent review rounds and owner correction.
The guide includes Chinese operating instructions.

A manager may attach a `collaboration_brief_v0` to its existing
`context_handoff={goal_id,agent_id,brief}` response. The host keeps the original
owner message unchanged alongside the brief. The brief carries the purpose,
relevant conversation and corrections, constraints, input references, acceptance
criteria and expected return. It is model-authored context, not a confirmed
Goal amendment or additional authority. The owner-local conversation displays
this brief and live receiver/return facts in place. Compatible requests without
a brief retain their existing shape and identity. A changed brief under the same
ingress identity is a conflict, not a second delegation.

Registered workers can ask another worker of the **same Goal on the same host**
for help or independent review:

```sh
loopx manager-inbox request --goal-id allocation --agent-id builder \
  --peer-agent-id reviewer --operation-id review-round-1 \
  --brief-file review.json --parent-request-id <received-request-id>
```

When the intended recipient is an existing Codex host task, resolve that peer
before substituting a temporary child. First inspect `agent-directory` for the
named Agent and its candidate count. Use
`loopx resolve-peer-route --goal-id allocation --agent-id reviewer` for an
observed local route. Archived history permits a unique readable route without
a task link; missing or unknown alternatives preserve ambiguity. If the result
is still ambiguous, supply the exact user-selected task link with
`--thread-link codex://threads/<id>`; never choose
the last binding by order or recency. Then record the request with
`manager-inbox request ... --require-host-route --peer-thread-link
codex://threads/<id>`. The result contains the same stable request id and a
`host_delivery` notification targeted to that task. An authorized host tool
must verify its own target/profile and submit that notification. The CLI has
not sent it: `host_delivery.status=not_attempted` is deliberate. After a lost
host response, inspect the target before resending; an Inbox replay only
deduplicates the stored request. The receiver's read, adoption and report are
separate readback steps, as below. If the route is unavailable or unauthorized,
keep the intended peer and report the specific gap.

An example `review.json`:

```json
{
  "schema_version": "collaboration_brief_v0",
  "purpose": "Independently review the allocation plan",
  "context": "The owner rejected proportional rounding and requires an exact optimum.",
  "constraints": ["Do not place orders", "Use integer cents"],
  "inputs": [{"ref": "outputs/plan.json", "description": "Candidate allocation"}],
  "acceptance": ["Check budget, shared stock, region capacity and zero demand"],
  "return_requirement": "Return concrete findings and the checks actually performed"
}
```

The brief is bounded to 16,000 UTF-8 bytes. Input references are relative
workspace files, with an optional exact `sha256` digest. Deliver artifacts into
the receiver's worktree through the project's existing Git/artifact workflow;
the request itself does not transfer files. `read` reports `available`,
`changed`, `unavailable`, `outside_workspace` or `too_large` for each input,
with the actual digest when readable. It uses the receiver's current worktree
only when the existing Git common-directory resolver proves that it belongs to
the registered Goal; otherwise it uses the Goal workspace. A digest/readiness
check is not proof that the Agent understood the material. Files above 4 MiB
remain explicitly unchecked.

For a request received from an external conversation, peer consultation uses
the original source's current sender and recipient grants. Both the current
requester and the peer, including intermediate recipients, must still be
authorized for that source. Each further hop rechecks the original ingress and
return route; a parent peer request is not an authorization bypass. A missing
grant names the blocked recipient without recording a request. This replaces
the previous blanket refusal of external-source peer forwarding, without
granting execution, changing priorities or broadening the return audience.

The peer independently `acknowledge`s and `report`s a conclusion through the
same commands as a manager request. The original requester receives it in
`manager-inbox read` under `peer_returns`; its Turn-start hook keeps requiring
a read until the requester explicitly consumes the result:

```sh
loopx manager-inbox read --goal-id allocation --agent-id builder
loopx manager-inbox acknowledge-return --goal-id allocation --agent-id builder \
  --request-id <peer-request-id>
```

Consumption does not mark a Todo done or certify peer acceptance. The requester
checks the actual artifact, incorporates or rejects the findings, and reports
the original owner conclusion through its original request. A second review
round uses a new operation id. Repeating an operation recovers the same request;
changing its content or recipient is rejected. Peer replies currently support
one conclusion (including an explicit blocker/defer result), not interim replies.

Parent lineage preserves the original owner context through peer requests.
External-audience parent requests cannot be forwarded through this owner-local
peer route. Lark's existing direct manager delegation and original-audience
return remain available; the peer workspace and its full context are not
projected into an external conversation. Cross-host file transfer, automatic
worker launch, cancellation/amendment transactions and lease transfer are not
provided by this path.

### Sandboxed managed dsh workers

A dsh `workspace-write` sandbox may read the Goal context but cannot write a
shared Inbox outside its worktree. Keep that sandbox enabled and explicitly
configure the built-in, identity-scoped stdio tools for each worker. The host
starts this command with trusted configuration:

```sh
python -m loopx.collaboration_mcp \
  --registry <registry.json> --runtime-root <runtime-root> \
  --goal-id allocation --agent-id builder --workspace <builder-worktree>
```

For dsh SDK/runtime **0.1.5rc1 / 0.1.5-rc.1**, add this per-worker Cordis patch
and pass it to `loopx turn run-once --host dsh --dsh-cordis <patch.yml>` alongside
the normal governed Turn arguments:

```yaml
- insert:
    - id: loopx-collaboration
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        transport: stdio
        serverName: loopx_collaboration
        command: python
        args:
          - -m
          - loopx.collaboration_mcp
          - --registry
          - <registry.json>
          - --runtime-root
          - <runtime-root>
          - --goal-id
          - allocation
          - --agent-id
          - builder
          - --workspace
          - <builder-worktree>
        failOnStartupError: true
```

Use the Python interpreter with this LoopX checkout/release installed and
absolute configuration paths. Each server exposes only `read_context`,
`assess_request`, `request_peer`, `return_result` and `consume_peer_result`.
Identity and filesystem roots are host-bound, absent from model tool arguments;
every call rechecks the registered actor. The server has no shell, Todo/lease
writer, credential tools or network listener. Installing/configuring it does not
launch another worker or grant access to another Goal. Removing the patch and
restarting the worker disables these tools without deleting pending requests or
replies. The trusted local CLI remains available under its existing host rights.

Minimum readback: call `read_context` from the configured worker and compare its
request id with `manager-inbox status --goal-id allocation --agent-id builder`.
Retain the returned model/runtime version and actual artifact checks when
qualifying a managed journey; successful tool registration is not collaboration
acceptance.
