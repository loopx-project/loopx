# DSH / Pi: L1 Observation and Managed Runtime Selection

- **RFC status:** Accepted
- **Supersedes / closes:** none

Scope: the shared goals of [Reliability Diagnostics](./long-running-agent-reliability-diagnostics-governed-delivery-v0.md)
and [Desktop Execution Frontends](./desktop-execution-frontends-v0.md).
[中文](./harness-selection-dsh-pi-v0.zh-CN.md)

## Decision

Keep **DSH as the first L1 event source**. Do not infer that DSH is already the
preferred production Mode B runtime. Retain Pi as a managed-runtime candidate.
The first choice minimizes the cost of qualifying an existing passive observer;
the second requires lifecycle, provider, crash-recovery and outcome evidence
that a plugin event fixture cannot supply. No quantitative winner is claimed.

Two DSH roles appear in this document and must not be conflated. The **bounded
managed Turn host** (LoopX's adapter choice for one governed Turn) is
credential-bound; its default-host resolution shipped in PR #4443, and the
steward channel reaches it through the one-segment chat transport recorded
below. The **L1 event source and session-owning runtime** role stays opt-in and
is not promoted by that binding; it still needs the C0, C1, overhead, retention
and Mode B rows.

## Managed Execution Surface (2026-09-15)

Selection is constrained by what the repository ships today, not only by what an
upstream harness can do. The managed bounded execution unit is the governed Turn:

- `loopx turn run-once` accepts `--host codex-cli|dsh|generic-cli` with
  `--execution-mode isolated-headless`: LoopX decides, a host adapter invokes the
  agent CLI, an independent validator proves the postcondition, and only a passing
  result is committed;
- `loopx host-mode-plan` selects `isolated_headless_turn` for the
  `continue_without_ui` intent only when the host declares `typed_host_adapter`;
  without that declaration it reports the mode as not ready and names the missing
  capability;
- session ownership (`managed_runtime` versus `attached_host`) is not decided
  here. It belongs to
  [Agent Session Execution Modes](./agent-session-execution-modes-v0.md), which
  also owns the M1-M4 integration milestones and the cross-frontend projection row.

Three evidence states appear below and must not be read across them. The list is
dated 2026-09-15 and is written to land with the managed stack:

- **already on `main` on 2026-09-15:** `loopx turn run-once --host
  codex-cli|dsh|generic-cli`, the `dsh` host adapter, the `host-mode-plan` gate
  above, and the dsh pin `deepseek-harness-sdk==0.1.2a3`;
- **not on `main` on 2026-09-15; expected to land with this document:** the
  explicit host selection (`loopx/control_plane/turn_driver/host_binding.py`,
  PR #4443), the steward channel's explicit executor selection
  (`loopx/chat_manager.py`, PR #4446) and the `0.1.5rc1` dsh pin (PR #4420). A
  later reader who finds those PRs merged can read those rows as shipped; a
  reader who does not must treat them as stack-only. All three merged on
  2026-09-15, so those rows are shipped, and the steward rows below were
  revised twice afterwards: the first revision still defaulted the steward
  channel to `codex` and left the managed host unreachable from it, and the
  second made that default credential-conditional, so a discovered credential
  re-pointed the surface a person talks to. This revision restores the
  steward's shipped default to `codex` on every machine and reaches the managed
  host only by explicit selection, which is the shape the row below states;
- **local live qualification, not a repository gate:** rows marked as local
  evidence below. Reproducing them needs an operator credential, and CI asserts
  none of them.

| Role | Source | Selection today | Promotion gate |
| --- | --- | --- | --- |
| Default managed execution host | LoopX Turn plus the `dsh` host adapter, bound to an operator-supplied model endpoint | shipped product default, credential-resolved: the managed `dsh` host when the operator credential is configured, the individual `codex-cli` host when it is not; `LOOPX_TURN_HOST` re-points whichever resolved and an explicit `--host` wins (PR #4443, default resolution with this change) | keep the typed host request/result, independent validation, and the operator-owned credential boundary; do not replace it without an equal or stronger contract |
| Steward channel executor | the interactive Chat transport the steward answers on | one machine setting, then one service-environment value, then the shipped product default: this machine's `steward_executor` machine configuration (edited from the Dashboard, read back by `loopx machine-config describe`/`inspect`, landed 2026-09-16) selects the executor for that machine, `LOOPX_MANAGER_ENDPOINT` bootstraps or names an unlisted adapter, and the shipped default stays `codex` on every machine; a selection of the managed host (`dsh`) moves the model and the reasoning effort with it | the segment transport's typed limits (no streaming, no cross-turn host session, read-only sandbox) stay disclosed and read back, no managed lane may depend on an individual subscription, and the namespace stores no credential and grants no authority |
| Supported alternative Turn host | LoopX Turn plus the `codex-cli` adapter | explicitly selectable, and the credential-resolved default of the managed row above on a machine with no operator credential; it is the `individual` executor kind, so it is billed to one person's CLI login | no managed lane may *silently* depend on an individual's personal CLI subscription: the individual host is reached only as that credential-resolved default and is read back as `no_operator_credential`, never substituted for a host the operator selected |
| L1 event source and session-owning runtime candidate | DSH | opt-in, not promoted; the bounded Turn host role is the default row above | the C0, C1, overhead, retention and Mode B rows in this document being run and reviewed |
| Optional visible host loop | Pi | not a managed runtime | declare a per-binding session mode with readback, prove single-executor behavior under restart, "conversation is not a receipt", non-authoritative host-local state, and one real-host restart row |

### Optional Ark governed Turn profile

[`loopx-ark-turn`](../../../packages/loopx-ark-turn/README.md) is a separately
installed provider selected explicitly through `--host generic-cli` with fresh
iteration context. It shares DSH's signed request/candidate conversion; LoopX
still owns admission, independent validation, work writeback and quota. A
per-Turn stdio MCP process exposes only operator-selected tools with bound work
identity. Provider model usage and resource-cleanup receipts are observations,
not accepted-work quota or a second task lifecycle.

This profile does not change the default host or the native Ark `goal_once`
profile. Native Goal continuation and outer LoopX Turn continuation must not
drive the same binding. The [research composition example](../../../examples/managed-research-team/README.md)
exercises a managed coordinator delegating to local workers; it does not promote
a persistent steward Chat transport, recursive fleet supervision, full live
steering, or a shared authority service. Use its explicit setup/readback/cleanup
instructions and preserve failed versus untested qualification boundaries.

The example's integrated acceptance path uses five preauthorized canonical
tasks and startup-only owner configuration. Both hosts select exact work via
`turn --todo-id`; fresh TS Todo completion precedes accepted result return.
Synthesis checks current child completion, binding and artifact hashes.
Provider cleanup, Turn progress and canonical completion remain separate.
This does not supply dynamic work derivation or another Python lifecycle owner.
Its default profile uses a local DSH lead with two DSH and two Ark members;
the cloud reviewer consumes a completed local analysis before returning its own
result. A secondary cloud-led profile tests the inverse delegation direction.
Both reuse the same Turn host adapters; neither changes the steward default.

### Managed host binding and live qualification (2026-09-15)

A managed host binding names four things: the host adapter, the provider, the
model, and where the credential comes from. The DSH binding is the DSH Turn host
with provider `deepseek-official`, model `deepseek-v4-flash` (DeepSeek V4.1
Flash) at reasoning effort `high`, an endpoint from the operator environment
(`DEEPSEEK_BASE_URL`) and a credential from the operator environment
(`DEEPSEEK_API_KEY`).

LoopX **selects** the default host for bounded managed Turns and never infers it
from a launch-time surprise (`loopx/control_plane/turn_driver/host_binding.py`):
an explicit `--host` or `LOOPX_TURN_HOST` always wins, and only when neither is
configured is the shipped default resolved from the operator's own credential
facts -- the managed `dsh` host when a credential exists, and the individual
`codex-cli` host when one does not, because an unauthenticated managed host
would refuse to run. The distinction that matters is between a *default* and a
*decision*: a credential may resolve a default that would otherwise have to pick
a host at random, but it never re-points a host the operator already selected.
A lane resolved onto the DSH host therefore never depends on an individual
developer's CLI subscription being available, funded, or logged in, and a lane
without an operator credential never silently borrows one either.

This change also rewrites the promotion gate on the supported alternative host
in the table above. It read "an individual lane must be selected, not reached by
default", which the credential-resolved default contradicts. The rewritten rule
keeps the original intent -- no lane may depend on one person's login without
the operator being able to see that it did -- and names the readback that makes
the dependency visible instead of forbidding the disclosed default.

The steward channel is a **different** surface, and after the revisions recorded
above its default is one endpoint rather than one rule: `codex`, the interactive
CLI endpoint, on every machine. Three layers select it, in one order: the
machine's `steward_executor` machine configuration, then
`LOOPX_MANAGER_ENDPOINT`, then the shipped default. Selecting the managed host
(`dsh`) moves the endpoint, the model and the reasoning effort together, so the
channel can never end up with an operator model driven through an individual
CLI login. The rule that decides a credential here is the opposite of the Turn
row's: a credential authenticates the endpoint that was selected and never
re-points the surface a person talks to, because a conversation must not change
hands mid-thread when a key appears in the environment. The readback still names
where the endpoint came from (`executor_endpoint_source`, now including
`machine_configuration`) and, for a shipped default, which decision it was
(`executor_endpoint_default_reason`), so an operator reads a decided default
instead of inferring it from the resolved host name.

A manager connection does not keep a second copy of that decision. The
connection record stores the resolved endpoint as an **observation** with its
source, and every read path -- the Lark route, the authorized-connection
resolution, and the Turn that answers on the channel -- re-resolves from the
machine. A record written while a different default was in force therefore
cannot keep answering on an endpoint the operator has since replaced, which is
what previously let a machine whose readback said `dsh` keep running its
steward on `codex`. When the machine does change the selection, the Session
bound to the channel still runs on the earlier endpoint; that Turn is refused
with the typed `manager_channel_executor_rebind_required` receipt, and the reply
names the one action that repairs it -- re-applying the connection, which opens
the channel Session on the endpoint the machine now selects.

Both managed surfaces resolve their **execution profile** from one owner
(`loopx/control_plane/turn_driver/execution_profile.py`): provider
`deepseek-official`, model `deepseek-v4-flash` (DeepSeek V4.1 Flash) and reasoning
effort `high`, overridable by `LOOPX_TURN_PROVIDER` / `LOOPX_TURN_MODEL` /
`LOOPX_TURN_REASONING_EFFORT` and, at lower precedence, the legacy `DSH_PROVIDER`
/ `DSH_MODEL`. The readback is one line, `execution_profile`, shaped
`deepseek-v4-flash@high` in the shipped case, with the provider prepended only
when it is not the shipped one; it is one line because every plan payload carries
it and the agent-facing output budget is a contract, and whichever values the
line names are the values that run, so an owner-set model appears as itself.
Credentials authenticate the selected profile and never choose it; the one
thing a credential resolves is the shipped *host* default of a bounded Turn
nobody selected, and that resolution carries its own readback source.

Evidence for this binding, separated by source:

- repository-covered without any provider call: with an operator credential the
  shipped default is `dsh` and without one it is `codex-cli`, an explicit
  `LOOPX_TURN_HOST` re-points either default, and an explicit `--host` still
  wins over all of them (`tests/test_turn_default_host_binding.py`,
  `tests/test_turn_managed_executor_binding.py`,
  `examples/loopx-turn-managed-executor-binding-smoke.py`,
  `examples/loopx-turn-managed-default-flow-smoke.py`);
- local live qualification with the real SDK and runtime
  (`deepseek-harness-sdk==0.1.5rc1`, the pin PR #4420 proposes; `main` still
  pins `0.1.2a3` and the same pair also passed there): the in-process
  `--host dsh` path and the `generic-cli` subprocess path;
- local live qualification: one governed Turn reached `validated_progress` — the
  host executed the bounded action, an independent validator proved the
  postcondition, and only then did writeback and quota spend follow;
- local live qualification: a Turn whose postcondition was not proved
  fail-closed instead — no writeback, and the quota slot spend count stayed at
  zero.

Open gaps before this binding is a promoted production default:

- the runtime snapshot bundled as `deepseek-harness-runtime-bin==0.1.5rc1`
  cannot boot the stock `headless` profile as shipped: a profile row pulls
  `@deepseek-ai/dsh-session-title-first-prompt-llm`, which imports the omitted
  `@deepseek-ai/dsh-session-title-llm`, and resolution runs inside the packaged
  snapshot, so installing that package into a profile directory does not change
  it. The current local workaround is a binding overlay that disables the
  affected row. The managed host path is unaffected: it does not select
  `headless`, and the default `sdk` profile boots and exits cleanly;
- the LoopX DSH Turn composition must name the tool rows a managed action needs
  (`@deepseek-ai/dsh-tool-fs`, `@deepseek-ai/dsh-tool-bash`). Without them a live
  model can answer but cannot act, and the Turn ends in a validation failure
  rather than in work.
- the host-mode plan used to map the unattended intent to the compatibility
  path: `isolated_headless_turn` carried `turn_host: generic-cli`
  (`loopx/host_mode_planner.py`), so the `loopx turn plan` command it printed
  named `--host generic-cli` instead of the selected `dsh` default recorded
  above, which is correct as a labelled rollback path but was not labelled as
  one. **Decided and shipped:** the plan takes the "write out the resolved
  default" option. The preview command pins no host, the pinned compatibility
  variant is reported as `plan_command_rollback`, and the typed
  `turn_mapping.host_selection` states which of the two a command is; targets
  that genuinely need a visible identity (transitions into `visible_tui`) still
  pin their host. `docs/reference/protocols/host-mode-plan-v0.md` defines
  `turn_mapping.host` as the mode's declared host and scheduler context rather
  than as an already-resolved concrete host. The plan's `--host-identity` list
  still covers visible hosts only, because a headless-only host such as `dsh`
  cannot own a visible session.

### Mixed local/cloud managed qualification (proposal)

The [session execution RFC](agent-session-execution-modes-v0.md#reusable-agent-operations-and-continuation-ownership)
separates provider, session ownership and continuation owner. Extend the same
managed work contract to a qualified cloud host; do not encode local = Turn and
cloud = native Goal. Keep the current managed-host and steward defaults above.

LoopX's public [Ark Managed Agent host contract](../../../loopx/ark_managed_agent_host.py)
currently specifies one-shot Goal activation, native continuation and no outer
Turn driver. A cloud governed-Turn adapter is a **separate, unqualified opt-in
candidate**, not a reinterpretation of that profile. DSH's
[bounded Turn adapter](../../../loopx/dsh_goal_mode/README.md) and
[same-session plugin](../../../packages/dsh-loopx-plugin/README.md) likewise have
different continuation contracts; qualification cannot be borrowed between them.

First qualify a complete local/cloud work unit: actual tools/artifact transfer,
typed result, independent rejection of a wrong artifact, accepted writeback,
usage readback, cancellation and recovery. Then qualify two dependent work cycles
and worker-initiated delegation using the shared collaboration contract. A host
adapter transports Agent decisions; it must not contain the scenario's business
phase sequence. Waiting parents must not occupy every slot needed by children.

Native Goal qualification additionally needs publicly documented activation and
identity, duplicate-activation behavior, evaluation/terminal readback, bounded
resource use, defer/wake and restart handling. A successful message or an idle
session is insufficient. A provider API or slash wrapper stays in its adapter;
it is not a generic LoopX command. Provider claims require public versioned
sources and reproducible qualification; this proposal cites only LoopX-side
contracts and makes no new claim about an Ark deployment or API guarantee.

Report actual provider usage separately from LoopX's validated-work quota:
rejected work can still incur inference cost. Unknown usage is not zero. Promote
only the tested profile; keep unavailable capabilities explicit and preserve
rollback to the prior selected profile without creating a concurrent driver.

## Evidence Baseline

LoopX was inspected at `bf217e1e01bec79f357c9ecbd580cf2dfa73db8b`.
The implementation paths below are repository-relative:

- `packages/dsh-loopx-plugin/src/observer.ts`: pinned activation, session event
  compaction, first-append safety, bounded buffering and flush isolation.
- `loopx/capabilities/reliability_diagnostics/{receipt,projection}.py`: independent
  validation, integrity classification and authority-free diagnostic readback.
- `loopx/dsh_goal_mode/turn_host_adapter.py`: a bounded Turn connector, opaque
  session lineage, SDK calls and failure translation, not a desktop outer loop.
- `loopx/pi_goal_mode/{loopx-goal.ts,pi-goal-loop-runtime.mjs}`: a visible-host
  integration with bindings and continuation behavior; not a passive observer.
- `apps/desktop/loopx-control-plane/src-tauri/src/services.rs`: service process
  management must not be mistaken for the complete managed Agent lifecycle.

The dsh pin moved in two steps, and reading this document needs both states.
`main` today pins `deepseek-harness-sdk==0.1.2a3`. The managed stack moves that
pin to the newest released upstream channel rather than an unreleased tag:
`deepseek-harness-sdk==0.1.5rc1` / `deepseek-harness-runtime-bin==0.1.5rc1` on
PyPI (PR #4420), matching `latest` for `@deepseek-ai/dsh` on npm (checked
2026-09-15). Upstream `next` and `alpha` tags are newer than that channel and are
not adopted here.

Upstream references were inspected on 2026-09-06, pinned independently of the
versions validated by LoopX:

- [DSH README at d347e703](https://github.com/deepseek-ai/deepseek-harness/blob/d347e703908d0406b7a7ef80e3a0e594d86b2215/README.md):
  Cordis/plugin architecture and explicit developer-preview compatibility risk.
- [Pi SDK at 9767ba27](https://github.com/earendil-works/pi/blob/9767ba275f3e9a5ee0f5c5342249b629ab1b2282/packages/coding-agent/docs/sdk.md):
  event subscription, session operations and runtime replacement APIs.
- [Pi extensions at 9767ba27](https://github.com/earendil-works/pi/blob/9767ba275f3e9a5ee0f5c5342249b629ab1b2282/packages/coding-agent/docs/extensions.md):
  event hooks with context-injection, tool-blocking and result-modification power.

The historical Pi repository URL now redirects to `earendil-works/pi`; the
inspected SDK uses `@earendil-works/pi-coding-agent`. This is an upgrade-check
input, not permission to replace LoopX's installed package or assume API parity.

## Comparison by Product Requirement

| Requirement | DSH evidence | Pi evidence | Selection consequence |
| --- | --- | --- | --- |
| Passive observation | LoopX ships a separate observer entry, three session publication hooks and pre-append rejection | SDK offers `session.subscribe`; extensions also offer interception hooks | DSH has a qualified contract slice; a Pi adapter must choose subscription over intervention and prove isolation |
| Session identity / resume | Existing Turn connector derives session lineage; observer separately requires exact goal/session/run identity | SDK separates AgentSession from AgentSessionRuntime replacement/resume operations | Test identity after restart/fork for each adapter; method availability is not durable recovery proof |
| One bounded attempt | LoopX already has a DSH Turn host with timeout and failure mapping | Existing Pi goal mode includes continuation and pause behavior | Neither native loop may silently become the Desktop scheduler; avoid two outer loops |
| Packaging | Dedicated observer export/bundle and packed smokes exist | Extension discovery is part of SDK resource loading | Verify the actually loaded package/profile, not just source imports; neither boundary is OS isolation |
| Provider profiles | SDK connector/version constraints are explicit | SDK exposes runtime/model construction | Qualify the same route, model, tools and budget; harness choice does not establish provider compatibility |
| Public safety | Producer and Python consumer independently validate; shared counterfactuals exist | Tool/context hooks can expose or change raw content | A Pi observer needs first-append redaction and negative fixtures, not transcript copying |
| Performance | Buffer/count/flush accounting exists; no matched real overhead result established here | Subscription is available; no LoopX observer measurement established here | Reject numeric rankings until identical workloads and revisions are measured |
| Maintenance | DSH upstream explicitly warns of breaking changes; LoopX pins its validated connector surface | Current upstream package/runtime APIs differ from historical integration assumptions | Pin upgrades separately; do not compare an installed DSH against an unqualified latest Pi |

These are integration-cost and contract observations, not claims that Pi lacks
events or DSH cannot support other models. Both expose control-capable APIs;
passivity is a property of the selected adapter and its loaded dependencies.

The two LoopX surfaces that depend on dsh do not move together. The bounded Turn
host uses the Python SDK/runtime pin recorded above (`0.1.5rc1`, the released
channel). The dsh-side plugin (`packages/dsh-loopx-plugin`) now builds its
development, host, and client surfaces on the same released `0.1.5-rc.2` line
instead of the retired `0.1.1-rc.2` one, and its npm peer ranges admit only
`>=0.1.5-rc.1`. Three upstream moves forced that, so it is a new release line
rather than a patch: the 0.1.5 line no longer publishes
`@deepseek-ai/dsh-client-runtime` (last released 0.1.1-rc.2), which moves the
`slots` service seat to `@deepseek-ai/dsh-client-ui-renderer` — the package this
manifest now names in `dsh.client.inject`; `Session.events` became
`Session.snapshotEvents()` and `Inbox.hasPending` became the two pending queues;
and the shared `/api` bridge addresses Remote methods as `<namespace>/<method>`
with a single `args` payload field. One `dsh.client.inject` list cannot order
boot rows for both generations at once, so the plugin cannot claim both. The L1
observer contract above is unchanged: the observer still consumes only
`session/created`, `session/event`, and `session/disposed`, and now treats
token-level `assistant/chunk` rows as retired input replayed from older durable
logs instead of a live event type.

## Data and Authority Flow

The operator needs to distinguish missing evidence, unhealthy execution and
an invalid observation treatment before deciding what to do:

```text
native session publication
  -> isolated observer: compact, validate, count, append
  -> independent ledger validation
  -> integrity receipt + diagnostic projection
  -> operator presentation only

canonical eligibility -> Desktop supervisor -> bounded Turn -> validation/writeback
```

There is no arrow from diagnostics back to eligibility. `valid` means the
observation contract passed, not that a task succeeded. A stall signal is not
permission to retry. Observer errors must not become worker failures.

## Implemented Readback Increment

The existing CLI now supports an explicit combined read:

```bash
loopx reliability-diagnostics status --goal-id <goal-id> --with-receipt --format json --as-of "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

The POSIX-shell example evaluates age against the current UTC time. Other
clients must supply a timezone-aware current timestamp. Omit `--as-of` only
for historical replay: it defaults to the last event time, producing zero
last-event age, not a live liveness check. Display the observation and evaluation
times separately; advancing the evaluation clock does not change integrity.

The receipt and projection derive from the same in-memory ledger reading,
avoiding two CLI calls observing different append states. Omitting the flag
preserves the original response. This does **not** make concurrent file append
atomic: a partial last line remains an invalid-input signal rather than being
silently dropped. The command does not activate an observer, discover a binding,
write a ledger, call a model, or change a Goal/Todo/lease.

This is an executable readback seam, **not a shipped Mode B panel or supervisor**.
A future panel must bind exact goal/session/run identity, show observation age
and integrity independently of task status, and refuse to label a multi-run or
stale goal ledger as the current session's health. It must remain operator-only,
with no diagnostic input passed into prompts or scheduler decisions. Existing
CLI ledger reads are unbounded; do not put this command on an automatic polling
loop before adding an owner-reviewed read budget/snapshot strategy.

## Qualification Plan and Stop Conditions

1. **C0 adapter fidelity:** compare native execution with the managed adapter,
   observer disabled. Pin model, route, tool definitions, prompts, environment,
   budget, package/adapter revisions and initial session state. Account for all
   retries and interruptions. Reject comparisons with unequal treatments.
2. **C1 passive arm:** enable only the observer on that qualified adapter. Record
   eligible run identity, persisted/accepted/rejected/drop counts, receipt status,
   endpoint/worker-context/scheduler influence and all failed runs. Fixture success
   does not establish this gate; non-valid receipt is not eligible C1 evidence.
3. **Overhead:** measure baseline and observer wall time, process CPU, peak RSS,
   bytes written, event throughput and flush latency using paired repeated runs.
   Report sample count, distributions, uncertainty and warm/cold conditions.
   Declare acceptance thresholds before running; no threshold is invented here.
4. **Retention/deletion:** owner chooses maximum age/bytes, active-writer handling,
   export/support access, backup scope and delete verification. Dry-run inventory
   must precede deletion; never truncate an active ledger to meet a size cap.
5. **Mode B acceptance:** separately exercise start/resume/interrupt/close,
   process crash, stale session identity, duplicate completion, timeout and
   provider failure in a disposable runtime. Verify one Turn at a time and
   canonical validation/writeback before spending quota or requesting another.

Keep raw logs and credentials owner-local. Public evidence should contain only
generalized methodology, pinned revisions, aggregate results and safe references.
No live model execution or retention deletion is authorized by this document.

Milestone ownership stays with
[Agent Session Execution Modes](./agent-session-execution-modes-v0.md). This
document owns the C0, C1, overhead and retention evidence for the L1 observer
arm, and the Mode B acceptance above for a session-owning runtime; the M1-M4
integration milestones and the cross-frontend projection row remain that
document's, and nothing here defines mode inference or a second executor.

## Delivery Sequence

This comparison plus combined CLI readback can be reviewed now. A Mode B panel
requires the exact-session read contract and bounded refresh path first; it must
not be a second generic monitoring subsystem. Run C0/C1 and overhead experiments
as separately budgeted work, then submit only reusable fixes and safe evidence.
Implement deletion only after the owner selects the retention profile. Revisit
runtime preference if Pi satisfies the same isolation/lifecycle tests at lower
measured integration and operational cost, or DSH fails them. Do not introduce
L2 advice, retry control or a new scheduler to make an L1 experiment pass.

## Steward Channel Chat Transport (2026-09-15)

The governed Turn surface and the steward (manager) chat channel need different
host shapes, and they now resolve their defaults differently too: a bounded Turn
nobody selected still falls back to the host the operator credential resolves,
while the steward channel stays on the interactive CLI endpoint until an
operator selects the managed host. A Turn is one bounded work segment, which the
shipped DSH adapter serves today. The steward channel additionally needs a
transport that can hold an interactive session, and the shipped DSH surface
explicitly does not promise cross-turn DSH session continuity.

Option A shipped, so this section now records the transport rather than a plan.
The steward channel holds the managed host through
`loopx/chat_dsh.py`: each Chat turn starts **one bounded dsh segment** on the
resolved execution profile, hands it the channel's bounded visible history plus
the current message, and returns the final assistant message. The snapshot the
segment sees is composed by LoopX and the segment's sandbox is pinned read-only
through `DSH_PERMISSION_MODE`, so an answer cannot come from ambient write or
shell authority that the channel never granted.

What the transport deliberately does not claim, because the channel readback
could otherwise be read as offering it:

* **no streaming** — the answer arrives as one final message;
* **no cross-turn host session** — each segment is fresh, and the visible history
  is Chat-side context rather than a host session the channel resumed;
* **no tool authority** — the segment is refused by the dsh sandbox itself when
  it reaches for a write, and the channel reports `trust_scope: read_only`.

Because the segment cannot read anything for itself, every source it is expected
to speak about has to be supplied by LoopX in the same bounded prompt: the
declared evidence window and the registered-source read are composed by the Turn
owner, cached and budgeted so a wider reach cannot slow every turn. This is a
transport consequence, not a new authority: the segment still cannot widen its
own scope, and any source it did not receive is a named coverage gap rather than
evidence of no progress.

The earlier typed reason `managed_host_chat_transport_unsupported` is retired
with this change; it described a transport gap that no longer exists, and keeping
it would have made a working host unreachable. The reasons the channel can still
report are the managed host's own launchability facts
(`dsh_runtime_unavailable`, `operator_credential_unconfigured`,
`invalid_reasoning_effort`), and a session request for an unavailable host fails
as a typed host-tool gate instead of silently falling back to an individual CLI
login.

| Option | Shape | Cost and risk |
| --- | --- | --- |
| A. Turn-backed steward transport (**shipped**) | Each steward chat turn runs one bounded governed segment on the managed host through the same execution profile the governed Turn resolves, with bounded chat history as context | No duplex streaming and no cross-turn host session; each turn is a fresh segment. The tool/sandbox authority is pinned read-only by the channel and the per-turn bound is the channel's own hard timeout |
| B. ACP or stdio adapter | Reuse the ACP stdio adapter path (as the Kiro CLI chat endpoint does) when the managed host exposes such an interface | Lowest transport cost, but depends on an upstream interface that no shipped evidence covers yet |
| C. Codex endpoint bound to the operator provider | Start the Codex app-server itself against the operator provider so the existing transport and tool surface stay | Keeps streaming, but must prove the session no longer authenticates with an individual login; the provider config becomes host-state authority and needs its own gate |

Selection rule: prefer A, because it reuses the Turn authority, typed host
failure, journal and quota semantics LoopX already validates; keep B as the
cheaper replacement if the upstream interface appears; evaluate C only if
duplex streaming is required for the steward experience. Whichever option ships
must demonstrate that the governed work a steward drives never reaches an
individual subscription, and -- for a steward session the operator put on the
managed host -- that the model work lands on the operator credential. The
steward's own shipped default is the interactive CLI endpoint, which is billed
to one machine's login; that is a disclosed default rather than a hidden one,
because the channel reports the endpoint, its source and the shipped decision
behind it. This document authorizes no new scheduler, retry authority or second
monitoring subsystem to make that demonstration pass.

Option A is the one that shipped, and its demonstration is a repository smoke
rather than a live transcript: `examples/loopx-steward-managed-chat-smoke.py`
runs the real bundled dsh segment against a local mock model endpoint and asserts
the resolved binding, the model and effort that reach the wire, the persisted
answer, and that the read-only sandbox refuses a write. The persona and audience
of a real steward conversation stay out of this document.

## Steward Executor Machine Configuration (2026-09-16)

The steward executor used to be selectable only through the Chat service
environment, which made a machine-local decision live in a launch file rather
than in a product setting: no surface could show it, no surface could change it,
and a reader had to know which process variables were in effect. The executor,
the model, and the reasoning effort are now a typed machine-configuration
namespace, `steward_executor`
(`loopx/capabilities/steward_executor/machine_defaults.py`), so a machine's
steward choice is a first-class operator setting.

The current namespace keeps the primary endpoint, its model and effort, and a
closed selection policy. It stores no credential:

```json
{
  "schema_version": "steward_executor_machine_defaults_v1",
  "selection_policy": "preferred",
  "executor_endpoint": "codex",
  "eligible_endpoints": [],
  "executor_model": null,
  "executor_reasoning_effort": null
}
```

`executor_endpoint` is required and restricted to the endpoints LoopX ships as
channel executors; a blank model or reasoning effort means this machine decides
nothing about that field, so the channel keeps resolving it from the lower
layers. `preferred` keeps that endpoint as the default while honoring a user's
explicit executor pick. `pinned` rejects a different explicit pick. `flexible`
requires a non-empty `eligible_endpoints` pool that contains the primary and
allows availability fallback only inside that pool. Unknown fields, an unknown
schema version, an unlisted endpoint, an invalid pool, and an unsupported
reasoning effort all fail closed before any effect. Stored v0 documents retain
their former `preferred` behavior. An operator who needs an adapter the
namespace does not list still has `LOOPX_MANAGER_ENDPOINT`.

Precedence is stated once, in the channel owner
(`loopx/chat_manager.py`): machine configuration, then the service environment,
then the shipped default. The machine layer is the one a product surface owns,
so `loopx machine-config describe` publishes the template and the Dashboard
edits the same document through the existing revision-locked transaction; the
channel readback adds `executor_endpoint_source: machine_configuration` plus the
document's `status` and `configuration_revision`, so a machine decision can be
told from a service-environment value without reading the store. The resolved
endpoint, model, effort, policy, allocation reason, eligible pool and source
revision are also persisted on the manager Session. A live Session therefore
keeps the allocation under which it started instead of being reinterpreted
after a configuration edit or process restart. `loopx chat-endpoint
inspect-steward` reads the effective configuration and current Session binding
through the same public projection.

This stage implements the allocation boundary and availability fallback. It
does not infer semantic task fit from chat prose. A later Agent decision can
submit an explicit executor pick, but the same pinned or flexible boundary
still authorizes or rejects it.

What this increment does *not* change: the shipped default stays `codex` on
every machine, a credential still never selects an endpoint, the managed host
still requires its own credential and runtime, and the selection grants no
authority -- it names a provider-billed runtime, and `manager_runtime` remains a
separate machine decision. A malformed steward value or an unreadable store
falls back to the lower layers with a typed reason
(`configuration_invalid`, `unavailable`) instead of failing the surface a person
talks to, and a malformed *sibling* namespace cannot rewrite a valid steward
selection.

Validation: `tests/capabilities/test_steward_executor_machine_defaults.py`,
`tests/test_manager_channel_binding.py`, `tests/test_chat_machine_configuration_api.py`,
`tests/capabilities/test_capability_configuration_ui.py`, and
`apps/presentation/dashboard/src/features/personal-workspace/personal-workspace-contract.test.mjs`.

### Steward Answer Identity and Runtime Selection (2026-09-16)

Once a machine could declare its steward executor, the Dashboard was still
answering two different questions with one value: *who answered me* and *which
runtime served the turn*. The transcript titled a steward answer with whatever
the chat-runtime picker happened to hold, and the picker itself preferred a
`Codex` adapter whenever one was discovered on the machine -- so a channel
resolving to the managed host could still present itself as an individual CLI
login, and the header chip, the composer and the answer could each name a
different executor.

Two rules now hold on the manager channel:

* **Answer identity names the speaker.** The transcript labels a steward answer
  `LoopX Manager` / `LoopX 管家` on every path that can produce one -- return
  receipts, resumed history, recovery streaming, the streaming placeholder, the
  completion fallback, interruption and failure. The executor and its model
  stay in the machine-capability chip, which is the surface that reports them.
  Goal channels keep naming the Goal's own Agent.
* **Runtime selection resolves the way the channel resolves.** The chat-runtime
  picker follows the channel owner's precedence for the manager context: the
  declared steward executor first, the shipped default only when the machine
  declares nothing. A discovered adapter is never presented as the steward.

An explicit operator pick still wins for that context, and the client still
sends no endpoint when the operator made no pick, so the create-session contract
in `loopx/chat_server.py` -- "each channel resolves its own default through its
own owner" -- is unchanged. An explicit pick is also the only path that moves
this channel off the declared executor, which keeps a discovered CLI from
silently rewriting a machine decision.

Evidence: `examples/personal-workspace-browser-smoke.mjs` (`execution-chip`
scenario for the picker and composer resolution, `chat-recovery` scenario for
the answer identity), plus a live readback on the installed Dashboard where the
header chip resolved `dsh` while the picker previously reported `Codex`.

## Steward Team Intake (2026-09-16)

Audited at `43d362532`. Team intake has shipped a bounded preview, owner
confirmation and first-Todo materialization. It has not qualified a running,
budget-enforced or intent-aligned team. The [overall roadmap](loopx-overall-roadmap-v0.md)
owns cross-RFC priorities and F1–F7/R1–R7; this selection RFC owns runtime
selection and its qualification evidence, not a separate team architecture.

The shipped boundary is `steward_team_plan_preview_v0`, kind
`steward_team_plan_preview`, dispatched through
`loopx/control_plane/work_items/governed_transition_proposal.py` and Chat
`team.plan`. It names an exact Goal, 1–8 lanes, registered Agent identities,
first Todo text/priority/class/action, acceptance, quota envelope and stop
condition. Validation produces `applies: false`. Preview is not execution.

| Boundary | Shipped behavior | Remaining limitation |
| --- | --- | --- |
| Validation/admission (#4519/#4522/#4532/#4533) | Exact Goal, registered Agents, supported advancement kinds and bounded public-safe fields; channel-scoped Goal lookup; unavailable facts drop the proposal while preserving answer text | `ready` checks registration/action support, not executor health, tool eligibility or budget admission |
| Staffing gaps | Unknown Agent produces `agent_not_registered` and retains `declined_first_todo`; explicit `capability_not_granted` / `audience_not_authorized` gaps admit no work | These reason codes do not prove all capability/audience conditions are automatically detected |
| Materialization (#4524/#4528/#4535/#4538) | Revalidates the named Goal; calls canonical Todo owner per ready lane; records proposal digest and bounded `lane_todo_ids`; existing receipt shape remains readable; no monitor key | Confirmed priority is dropped; acceptance/quota/stop are not execution constraints on this path; no atomic team commit or automatic partial-recovery proof |
| Confirmation (#4547/#4548/#4552) | Existing frontend displays lanes/gaps and submits `team.plan`; bundle and browser fixture shipped; the manager conversation now lists the card its own channel stored, so an owner confirms where the sentence was typed while a Goal-scoped fetch stays in that Goal's workspace | Lark and real worker execution were not qualified by this fixture; the confirmation readback was repaired after this fixture (a confirmed lane keeps its declared priority, a partial application reports its gap count, and a plan that staffs no lane is a typed failure) |
| Freshness | Registry byte changes make the Chat preview stale; optional `intent_basis` reads alignment source facts before materialization | No exact Goal-intent/authorization/work precondition at commit; `intent_basis` is neither the full intent revision nor a CAS fence |

Unchanged-plan retries are covered by focused tests. Do not generalize those
tests to concurrent plans, interrupted multi-lane commits or intervening Todo
edits. R1 qualifies those cases through the actual action and recovery paths.

Latest integration checkpoint: #4569 (`f1166e81e`) keeps unsupported action kinds as lane-local gaps, distinguishes declared gaps from host verdicts, preserves admitted gaps on revalidation and projects admitted plans into local owner-channel cards. A blocked lane no longer rejects the whole plan. The Lark manager audience still has no corresponding card. These fixes do not establish plan execution or resolve the baseline F1–F4 commitment/recovery findings.

Subsequent #4572 (`0aa6179de`) appends a channel-authored confirmation-location pointer to manager answers, naming the Goal workspace. Local and remote manager audiences receive it; Goal channels are not annotated. A remote pointer does not create a Lark card or prove card persistence/team execution; model prose is preserved.

The manager conversation that produced a plan now also lists the card its channel stored, instead of only the Goal workspace the pointer names. This is a presentation change over the same validated proposal: it adds no Lark card, creates nothing before confirmation, and a proposal fetched for a selected Goal stays in that Goal's workspace because it belongs to that context.

**Reuse boundary for a Lark card.** LoopX already ships the Lark half of a card confirmation: `loopx/extensions/lark/goal_channel_operation.py` delivers a non-forwardable Card 2.0 with confirm/reject buttons for a typed `operation.execute` proposal, `event_collector_runtime` consumes `card.action.trigger`, and the transport owns operator membership and tenant verification, replay protection, card readback and result-card patching. The reusable part is that shell plus `presentation.action_review_plan.compile`; the operation-specific parts are the operation envelope identity, the claim/execute effect and the Goal-channel binding the delivery resolves. `compileReviewCardFrame` now also returns a provider-neutral `review_card_frame_v0` for a validated `team.plan` proposal -- identity is the proposal plus the state fingerprint the apply re-validates, fields are `{key, value}` pairs whose fixed labels stay keys -- so a plan card can use the same shell and callback consumer. What it still needs is a delivery route for the audience that asked (a manager group is not a Goal channel binding) and a callback effect that applies the proposal through the Chat action service instead of claiming an operation envelope. No plan card is posted yet, and confirming from Lark would also need an explicit answer for whether an external manager audience may perform this durable write.

### Relationship to multi-agent and shared authority

The [alignment RFC](shared-goal-alignment-and-governed-amendment-v0.md) owns
shared intent and governed amendments; [shared authority](shared-goal-authority-state-provider-v0.md)
owns persistence/promotion. A team plan proposes work inside accepted intent;
it cannot change permissions, shared acceptance or terminal conditions by
placing prose in its envelope. Stage 3 amendment commit remains unshipped.
The optional receipt `intent_basis` is the existing `source_basis_digest`,
not a version of that unimplemented full intent envelope. Historical receipts
must not acquire stronger semantics through a rename.

`peer_v1` permits a steward to organize, delegate and synthesize, without
unilateral write, claim, priority or preemption authority. Lanes refer to the
same canonical graph and per-Agent frontier. Creating `claimed_by` Todos does
not acquire task leases, launch workers or reserve distributed quota.

The [peer directory](../../reference/protocols/peer-agent-directory-and-observation-v0.md)
is shared by manager and peer callers. `loopx agent-directory --goal-id <goal>
[--agent-id <caller>]` reuses the management projection, caps local rows at 24,
reports omitted rows, has no pagination or presence provider and does not
project lease epochs. A supplied unregistered caller gets a scope gap. Local
CLI membership checks do not authenticate a remote caller; any future remote
entry must derive caller identity from a verified binding.

Per-Agent readback uses `loopx shared-goal-alignment --goal-id <goal> --agent-id
<agent>`, within its Stage 1/2 source-facts boundary. The plan receipt does not
project that entire state. The [three-layer contract](../../reference/protocols/multi-agent-three-layer-minimality-v0.md)
and [visible launcher](../../reference/protocols/multi-agent-visible-launcher-v0.md)
remain independent owners: user intent, preset procedure and kernel declarations
join by Goal/Agent/Todo identity, not by creating another runner, pane owner,
vision budget or evidence loop. Selecting an executor or storing credentials
grants none of these effects.

## Steward Channel Readiness by Milestone (2026-09-16)

The steward channel consumes both this document's host selection and the manager
milestones in
[capable-manager-semantic-handoff-v0](./capable-manager-semantic-handoff-v0.md).
This section records which steward-channel behaviours those milestones can rely
on today and which stay unverified. It states product contracts, not conversation
content: no live channel transcript, audience identity, dated incident or
operator-local path is recorded here.

| Milestone | Steward-channel contract in scope | Evidence state on 2026-09-16 |
| --- | --- | --- |
| Manager M1 — useful host agent | The channel resolves and reports its effective executor, model, reasoning effort and source, the executor selection does not follow a credential, and a host that cannot launch fails with a typed reason instead of a silent individual-login fallback | Shipped: selected endpoint with its source and default-rule reason, the executor's `execution_profile`, `executor_kind`, and the `channel_binding` readback (PR #4446 with the Turn-side readback in PR #4443; the unconditional default and the segment transport land with this change). The upstream **session identity** is still not projected to the channel, so a channel answer cannot yet prove which session served it |
| Manager M2 — semantic continuation | Receiver resolution across registered running lanes; typed per-source coverage and freshness; a goal-level milestone the report can lead with instead of coverage disclaimers | Partially implemented. Typed source failures and one real source read are recorded below; the local peer directory is shipped. Cross-directory receiver resolution, complete semantic requests/return and a synthesizable Goal-level milestone remain open. Source reading does not complete M2 |
| Manager M3 — automatic complete exchange | A persisted answer that exceeds or violates the channel's outbound text contract is split and re-sent under a stable answer identity; an ambiguous or failed send is reconciled instead of replaced by a local notice; the return path survives a transport restart; rich markdown renders as structured text | Partially mitigated. `loopx/extensions/lark/outbound.py` fails closed on an over-limit or malformed payload, and the channel reports that local failure without re-delivering the persisted answer; one answer carries no idempotency identity, so a retry can duplicate it; structured rendering is not guaranteed |
| Host modes M0-M1 | The channel's executor selection and its bounded one-segment execution | Selection is covered by PR #4446 and the Turn-side selection by PR #4443; bounded one-segment execution is covered by the Mode B acceptance above. The channel itself now reaches the managed host through the segment transport, so the managed host's own one-segment execution is reachable from the channel; what remains open is that the segment is not a session, so cross-turn host continuity is still not offered |
| Host modes M2-M3 | Attached-host parity, typed unavailability, and mode-aware projection with no mode inference and no second executor | Partly shipped: the channel's managed segment transport holds one executor per binding, refuses a second start with the typed `managed_host_chat_segment_in_flight`, and discards an interrupted segment's answer instead of letting it enter visible history. The channel readback also carries the mode-aware projection: it quotes the Session's own `session_mode` and `status`, reads a channel with no Session as `unbound`, and names a mode outside the closed set as `unrecognized` instead of deriving a mode from the executor it resolved. Still not implemented: attached-host parity, and an external audience still degrades to `restricted` |

### Remote-source coverage acceptance (2026-09-16)

Typed M2 source failures have shipped. The following preserves one historical
live-channel read and its acceptance boundary; this roadmap audit did not rerun it:

- a declared remote source that cannot be read reports a typed cause together
  with the repair that clears it (an authorization that lapsed, a remote client
  that is missing, a remote protocol that is unavailable, a host that cannot be
  reached) instead of an untyped unavailability;
- a live manager-channel question that required its declared remote source was
  accepted on 2026-09-16 at release `20260916T123949Z` (serving revision
  `55ebbc6b7`, executor `dsh`, profile `deepseek-v4-flash@high`). The answer
  named the one declared source it read and kept that read's freshness visible,
  stated its evidence window and the bounds it applied, listed the remote rows it
  included, and said that hosts it did not read are outside coverage instead of
  presenting them as having made no progress.

That is the typed per-source coverage and freshness property the M2 row asked
for. The other two halves of M2 - receiver resolution across registered running
lanes, and a goal-level milestone the report can lead with - stay open. The
acceptance is a live channel read: it needs a running channel, a real credential
and a declared source, so it is a recorded procedure rather than a CI job, and
the failure half needs an unreadable source to exercise.

Two boundaries stay fixed across all five rows. The channel remains an entry point
and projection of one manager Session: it owns no profile, no permission state, no
second executor and no work authority, so a richer answer contract must not widen
what the channel may read or change. And no row is promoted by this document; the
M1-M4 integration milestones and the cross-frontend projection row still belong to
[Agent Session Execution Modes](./agent-session-execution-modes-v0.md).
