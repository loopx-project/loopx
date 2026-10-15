# Periodic report

`periodic-report` is LoopX's reusable reporting capability. It gives any
project a stable report-run envelope while leaving source semantics, cadence,
presentation, and destinations to profiles and adapters.

| Surface | Value |
| --- | --- |
| CLI | `loopx periodic-report inspect-profile --preset weekly`, `request`, `consume-pending`, custom `--profile-json <path>`, `evaluate-trigger`, `evaluate-runtime-trigger`, `compose-run`, and optional `archive-openviking` |
| Protocol | [`periodic_report_v0`](../../../docs/reference/protocols/periodic-report-v0.md) |
| Smokes | `python3 examples/periodic-report-smoke.py`, `periodic-report-profile-smoke.py`, `periodic-report-html-smoke.py`, `periodic-report-bindings-smoke.py`, and `openviking-periodic-report-extension-smoke.py` |

## Generate this week's report

In an active LoopX or Codex project session, ask the agent to **generate this
week's project report**. That explicit request opts the current session into
one provider-free generation. The agent resolves the built-in
`weekly-progress` profile (short aliases: `weekly` and `weekly-report`),
collects the current project's public-safe LoopX progress, and renders matching
Markdown and self-contained HTML artifacts. No project profile file,
Automation, provider, or external sink is required.

The agent can inspect the exact built-in profile with the effect-free command:

```bash
loopx periodic-report inspect-profile --preset weekly --format json
```

The receipt must report both `active: true` and `generation_allowed: true`.
Its `interaction_contract` also says that the explicit user request is
sufficient, no project profile file or Automation is required, and external
writes are not allowed for this mode. Agents should follow that packet instead
of expanding a generic heartbeat prompt.
The preset binds the built-in `project_progress_v0` source plus `markdown_v0`
and `html_artifact_v0`, declares no schedule, and contains no sink bindings.
The explicit request owns the report window using the active session's local
calendar context; it does not create a recurring job.

`project_progress_v0` organizes typed facts into a reusable hierarchy:
progress and outcomes, capability evolution, risks and blockers, next actions,
and collapsed supporting evidence. It permits at most eight primary items, so
delivery receipts and runtime validation stay supporting instead of crowding
the audience narrative. This hierarchy is inspired by the validated project
weekly-report presentation, but it contains no issue, pull-request, or
Issue Fix policy.

The source request may carry the profile-owned `language` as a BCP-47-like
tag. The built-in hierarchy currently provides English and Chinese section
vocabulary (other languages fall back to English), while item facts remain
owned by the caller. The orchestrator must pass the same language into the
document editorial input; renderers do not infer or rewrite business section
semantics. Omitting the field preserves the English default.

There is no issue-fix-specific report capability. Issue Fix, release notes,
research, operations, and other domains may supply peer source adapters when
their richer semantics are useful; none is required by the built-in weekly
profile.

## Request from a Goal Channel

After an Agent reads one addressed Goal Channel item and semantically decides
that the user is asking it for a report, it records that decision explicitly:

```bash
loopx periodic-report request \
  --goal-id <goal-id> \
  --agent-id <agent-id> \
  --source-ref <message-id> \
  --execute
```

When exactly one complete source adapter is active, the command selects it
automatically. With multiple active providers, the Agent selects the provider
explicitly with `--source-adapter-id <adapter-id>`. The journal retains that
adapter identity, and later settlement resolves only that owner regardless of
extension discovery order. A temporarily unavailable owner leaves the request
pending; LoopX never falls through to another provider. A declared adapter
that was not activated is reported by adapter id with a content-free code and
the `loopx extension doctor` next step instead of being silently dropped:
`extension_disabled` or `extension_doctor_not_ready` for a disabled or
doctor-stale extension, whose factory is never imported, and
`extension_hook_adapter_unavailable` (or an incomplete/ambiguous port code)
when a ready extension fails its permission check, factory import or port
contract. An unreadable extension state is reported as
`extension_state_unavailable`, with or without `--source-adapter-id`, rather
than as "no provider installed".

The adapter id is part of the request idempotency namespace together with the
Goal, Agent, and provider-local source reference. Two providers may therefore
use the same opaque source reference without collapsing distinct requests.
Replay without a selector remains valid when exactly one matching journal entry
exists; if the same source reference is already owned by multiple providers,
the Agent must select the intended adapter explicitly.

There is no keyword or regular-expression classifier. The provider adapter
binds only the exact source selected by the Agent and checks authorship,
addressing, Goal/Agent connection, target, and inbox identity. A manifest-
discovered `capability_action` hook supplies the content-free bind and settle
ports, so this capability and quota import no Lark implementation.

The command persists a replay-safe typed request journal. `consume-pending`
uses the normal manual trigger, editorial, frozen artifact, Workspace, and
delivery-Todo pipeline. It acknowledges the provider source only after
`delivery_ready` durability; failed ACKs become settlement-only retries and do
not duplicate delivery work.

## Todo authority and report retries

Stage-boundary frontier evaluation and project-progress selection share one
complete evaluated Todo snapshot. After Goal promotion, File/SQLite authority
owns those facts even when the Markdown display is stale, absent or malformed.
An empty canonical graph is empty; a failed canonical read remains a failed
observation and never falls back to Markdown. Reading does not repair the display.
Before promotion, the existing Markdown source remains in use.

The TS report selector consumes bounded decision fields, not report prose. It
keeps the reporter's outcomes before peer outcomes, excludes unowned work, and
uses the existing resume/acceptance evaluation for the next action. Report
consumption and all report-repair actions are excluded from next-action facts.
Publication-history filtering still runs before the six-outcome cap so already
published facts do not hide an unseen seventh outcome.

Completion and retry ordering compares offset-aware instants at microsecond
precision, rather than timestamp strings. Invalid/naive completion timestamps
are not reportable; an invalid stage timestamp is an error. A retained canonical
User rejection/cancellation can supersede an approval-pending receipt after
archival. It must match the exact decision scope and addressed Agent; this is
permission to reconsider that pending attempt, not approval to publish. Existing
retry-key bytes remain unchanged for the same selected decision.

Use the existing local command to consume pending work:

```bash
loopx periodic-report consume-pending --goal-id <goal-id> --agent-id <agent-id> --execute --format json
```

An explicit global `--runtime-root` routes both intent state and Todo reads.
When the result is `editorial_required`, inspect its local editorial-request
artifact: facts identify their source Todos. An identical retry reuses the
frozen editorial input; it does not rebuild it from newer Todo state. Provider
outages leave the intent for retry after authority recovery. Keep existing
editorial artifacts and receipts when rolling back; do not delete them to force
re-generation. No provider default, subscription, scheduler, external-delivery
permission or publication cursor is changed by this read-path refactor.

## Customize or schedule

The capability remains **inactive for background work and external writes by
default**. Create a project-owned `periodic_report_profile_v0` only when the
project needs custom sources, renderers, audience policy, timezone/RRULE, or
explicit sink bindings. Use Codex App Automation only for an unattended
recurring report: the host schedule should match that custom profile's RRULE.
Pausing the Automation or setting the profile to `enabled: false` stops that
scheduled path.

External delivery and archival are separate opt-ins. A normal in-session weekly
report has no sink and performs no external write. For the machine/Goal
`periodic_report` subscription, however, `enabled: true` together with an
explicit `route_ref` is standing authority to deliver reports produced at
validated stage boundaries. The selected extension, runtime capability,
configured route, sender identity, and exact readback must still pass their
own fail-closed gates.

An explicit Goal override is authoritative and must be complete; LoopX never
fills its missing fields from machine defaults. `plan-goal-delivery` reports an
invalid override with a typed field list, its configuration source, and the
choice to complete or clear the override. This diagnostic path is read-only.

The capability is intentionally effect-free. It first evaluates scheduled or
material progress facts into a deterministic trigger receipt, then composes a
run with stable run and sink idempotency, typed source snapshots, artifact and
sink receipts, explicit partial/unknown outcomes, and bounded retry guidance.
It performs no provider read or write.

Reportable transitions include a due cadence, a validated primary outcome, a
validated vision closure with either a successor or terminal goal, material
route decisions, primary-path blockers and recoveries, and authorized manual
runs. Ordinary todo completion, unchanged monitors, state refreshes,
surface-only events, and intermediate vision checkpoints are suppressed.
Profiles can enable trigger kinds and set a minimum interval; urgent outcome,
closure, blocker, and manual triggers may bypass that interval. Concurrent
material facts are coalesced into one report and previously covered trigger
ids are deduplicated.

Incremental reports advance from an exact, readback-verified publication
cursor rather than from generated prose. The cursor keeps cumulative trigger
ids and semantic fingerprints keyed by each fact's stable `source_ref`. A
later stage includes only new facts and facts whose fingerprint changed;
changed facts carry their prior status and kind so the editorial step can
render a transition instead of repeating the old item. If no supplied fact is
new or changed, the post-writeback producer emits no report intent. Local
generation and failed or partial delivery never advance this cursor. A
successful Goal Channel delivery records the predecessor
publication identity for the next report.

An enabled custom profile may also declare `trigger_policy.aggregation` with a
bounded `window_seconds` and `stage_completion_required=true`. Stage completion
reuses the existing goal-vision, outcome-checkpoint, and frontier-replan facts:
the current vision must close through an evidence-linked material checkpoint,
and the Goal must either become terminal or durably settle the existing
`vision_successor_required` transition into a successor vision and owned
frontier. The `evaluate-runtime-trigger` command promotes only that derived
success-path receipt into `bounded_segment_milestone`.

No Todo predeclaration or separate Stage lifecycle is required. Todo count,
elapsed time, ordinary completion, and blocker/stall/long-chain/monitor replans
remain context and never produce a report by themselves. The producer performs
no provider call or external write; an eligible receipt continues through the
existing `compose-run`, renderer, standing subscription authority, and sink
readback boundaries.
The CLI streams the append-only log and applies the 4,096-row capacity limit
only after goal, segment-window, and relevant-kind filtering. Malformed durable
rows and an oversized relevant window fail closed.

Every `--*-json` input, from a file or `-` for stdin, is refused before parsing
when it exceeds 16 MiB (16,777,216 bytes). Pass `--max-input-bytes N` on the
subcommand to set a different positive limit for a legitimately larger or
deliberately smaller request; there is no unlimited setting. The limit counts
input bytes, not total process memory. Request files stay strict UTF-8.

### Automatic post-writeback intent

Automatic milestone evaluation is default-off. A project may opt the built-in
weekly profile into the generic post-writeback hook at its local registry
composition boundary:

```json
{
  "control_plane": {
    "periodic_report": {
      "enabled": true,
      "profile_preset": "weekly"
    }
  }
}
```

After a committed `refresh-state` writeback with complete
Goal/Agent/Turn/effect identity, core dispatches the TypeScript-validated
`post_writeback` hook outside the primary transaction. The capability receives
only a bounded stage-completion projection and its public-safe progress
snapshot, both captured at the writeback boundary. It may propose one
idempotent `periodic_report.trigger_evaluation` intent. Core checkpoints that
proposal in a replay-safe sidecar. A transient failure is durably recorded as
`retryable_failure`; the next exact replay advances its attempt and may replace
it with `intent_recorded` or `not_applicable`, while terminal replay returns the
original receipt without invoking the provider again. Todo-bound settlements
carry a non-empty Todo id, while Todo-less autonomous replans carry an explicit
`null` Todo id. Disabled profiles, incomplete settlement identity, ordinary
Todo completion, and generic replan produce no trigger intent.

If the Python bridge cannot complete the TypeScript hook transaction, the
isolated failure includes only a typed runtime phase, error kind, and diagnostic
code. It does not expose raw provider output or private state, and it does not
change the committed primary writeback.

The intent is not a report and grants no generation, publication, connector,
network, credential, or sink authority. A separate governed executor may
evaluate it into the normal trigger decision. At consumption time LoopX reads
the current effective subscription again: a disabled subscription suppresses
the action, while an enabled subscription with an explicit route supplies the
standing delivery authority. Report composition, Miaoda HTML, content checks,
provider readiness, and group-message readback remain later independent gates.

`quota should-run` reads eligible `intent_recorded` sidecars for the exact Goal
and Agent. A pending intent takes precedence over monitor-quiet and terminal
no-follow-up projection and returns one TypeScript-validated governed command.
That command may render provider-free local HTML and Markdown, run content
checks, persist the normalized generation bundle, and create one runnable
delivery successor bound to the frozen generation digest and current effective
subscription. Exact replay does not rerender or duplicate the Todo. The delivery
request carries that subscription's Goal, source, effective revision, and route;
the Lark provider revalidates it immediately before each message write. Normal
Todo/quota selection can therefore continue into Miaoda publication, Lark
document creation, and Goal Channel delivery without another per-report owner
gate. Disabling the subscription revokes pending automatic delivery; route,
provider, sender-identity, or readback drift still fails closed.

Project-specific scheduled reports should be layered as profiles and adapters.
For example, a maintenance profile may choose a local timezone and weekly
cadence, collect repository and discussion signals, render a team card, archive
the artifact, and deliver it to a configured channel. None of those choices
becomes an invariant of the shared core or the in-session preset.

## Audience relevance and Lark announcements

A custom profile may declare a `periodic_report_audience_policy_v0`. Recipients
use stable symbolic ids and must declare at least one owned domain or typed
routing rule. Report items may carry normalized `domains`; routing rules may
also select explicit source ids, section ids, content kinds, tags, or domains.
Every declared selector in one rule must match the same normalized item.

`build_periodic_report_announcement_plan` compiles those facts into a
provider-neutral `periodic_report_announcement_plan_v0`. A recipient is
mentioned only when at least one eligible report item intersects an owned
domain or matches an explicit rule. Primary items are eligible by default;
supporting evidence is excluded unless the profile opts it in. The default for
an unrelated recipient is omission. Titles, summaries, authored mention text,
provider identities, and external lookups never participate in selection.

The Lark delivery adapter may consume the exact normalized document and policy
alongside the rendered artifact. Preview returns the announcement plan without
resolving an identity or sending a message. On execute, the extension resolves
only the selected symbolic ids through an injected provider adapter and places
those verified `<at>` elements before the report. A match without a renderer,
a mismatched document/artifact digest, or provider mention markup embedded in
the artifact, title, or footer fails before send. The core therefore owns
relevance while the Lark extension owns provider identity and wire rendering.

The bundled execution command is `periodic-report deliver-goal-channel`. It
derives the destination and sender exclusively from the current Goal's enabled
Goal Channel binding. Only a verified `project_bot` profile is valid; the
request cannot provide a chat id, profile, Bot App id, display name, or sender
identity. The intent instead supplies exactly two ordered HTTPS entries—the
hosted report and the Lark document. Execution sends two independent messages,
verifies the bound Bot and chat before send, then requires exact
interactive-card, chat, and Bot-identity readback for both. Missing or drifted
identity or subscription authority, either missing message, or a partial readback
fails closed without a user/default-Bot fallback. Retries first scan the complete
Goal Channel history from the frozen generation time and reuse only an exact card,
chat, and Bot-sender match. Incomplete history fails closed instead of risking a
duplicate; the stable provider idempotency key closes the concurrent-send race.
When the normalized report contains a primary typed `next_action`, the hosted
report announcement also carries its public-safe summary as a compact `下一步`
prompt. The hosted artifact remains authoritative, while the Channel card gives
an immediate direction and the same exact-card readback verifies that prompt.

This is a built-in capability, not an extension: callers need the trigger,
idempotency, retry, and receipt contract even when no provider is installed.
Optional or independently versioned collectors, renderers, archive stores, and
message transports remain extension providers (or built-in adapters) that
implement the capability's ports without owning its lifecycle.

`inspect-profile` returns a deterministic `periodic_report_activation_v0`
receipt and never starts a scheduler or invokes a provider. `--preset weekly`
resolves the built-in session profile; `--profile-json` validates a custom
project profile. In a custom profile, an omitted `enabled` field is treated as
`false`, and enabled profiles require at least one source and one renderer.
An optional archive extension can therefore add durable history without making
local report generation or another configured delivery sink depend on it.

The public profiles fixture covers the built-in portable weekly profile, a
default-disabled project, a cadence-based release report with an optional
archive extension, and a milestone-only research report with an
extension-provided source. These are peer product uses; none changes the
capability identity or core schema.

## Generation and formal delivery

The reusable lifecycle has two independently truthful phases:

1. `build_periodic_report_generation_bundle` freezes one normalized document,
   one or more rendered artifacts, and a provider-free generation receipt. A
   missing chat or archive provider cannot invalidate these local artifacts.
2. A project profile declares sink bindings. LoopX checks the pinned extension
   version, protocol, capability version, and provider readback before a caller
   attempts formal delivery. Provider results are then normalized into one
   delivery receipt with retryable sink ids and exact-readback evidence.

Each sink dependency is profile-owned and explicit:

- `required` fails closed when its provider is missing, incompatible, or not
  verified;
- `optional` preserves the generated report and records a degraded or partial
  formal-delivery outcome;
- `disabled` performs no provider lookup or write.

This yields three portable operating modes. `portable` generates artifacts
without providers, `enhanced` adds optional sinks, and `durable` requires one
or more formal delivery or archive sinks. The public fixture at
`examples/fixtures/periodic-report-extension-modes.public.json` exercises all
three without naming a project or relying on a live provider.

`compose-run` remains the compatibility envelope for callers that already have
both archive and delivery receipts. New profile integrations can use the split
generation/readiness/delivery receipts so provider-specific policy does not
leak into the capability core.

## Optional OpenViking archive extension

`openviking-periodic-report` is a bundled **LoopX extension** that implements
the capability's `periodic_report_sink_v0` archive port. It is not an
OpenViking extension and it adds no OpenViking runtime ABI. The dependency
direction is:

```text
periodic-report capability
  -> openviking-periodic-report LoopX extension
     -> OpenViking public SDK read/write
```

Install it through the normal LoopX lifecycle:

```bash
loopx extension install --bundled openviking-periodic-report --execute --format json
```

Activation fails closed unless all three facts hold:

1. a normalized `periodic_report_activation_v0` says the project profile is
   enabled;
2. that profile binds `report.archive.write/v0` to
   `openviking-periodic-report@1.0.0` with `periodic_report_sink_v0` and a
   non-disabled dependency policy;
3. the current run has observed and declared
   `--available-capability openviking_context_write`.

The extension manifest permission does not grant write authority. It only
lets the runtime prove that the selected, enabled, doctor-verified revision is
compatible with authority already observed by the caller. A profile may keep
the sink `optional` for an enhanced experience or mark it `required` for a
durable report contract.

The v0 provider accepts only the Markdown artifact. It writes `report.md`
first and `manifest.json` last; the exact-read-back manifest is the commit
marker and contains the stable bundle digest and result id. A byte-identical
retry performs no write. A stable URI containing different bytes fails closed.
It does not archive HTML, copy the full report into Agent Memory, scan report
history, or implement `/reports`.

```bash
loopx periodic-report archive-openviking \
  --request-json periodic-report-openviking-request.json \
  --available-capability openviking_context_write \
  --openviking-url http://localhost:1933 \
  --execute \
  --format json
```

Use `--openviking-api-key-env` to name an environment variable; never put a
credential in the report profile or request. Without `--openviking-url`, the
provider uses the public embedded `OpenViking` client and optional
`--openviking-path`; `--openviking-config` selects its `ov.conf` explicitly so
an isolated run need not inherit the user's default configuration. The
OpenViking SDK is an optional environment dependency and the extension doctor
stays unavailable until it can import that public client surface. Project
profiles own whether the archive is enabled and
the Resource root/tags supplied in the request context. The generic capability
continues to own trigger, generation, delivery truth, and retry semantics.

## Built-in renderers

- `markdown_v0` produces a compact linear artifact for documents and message
  adapters.
- `html_artifact_v0` produces a self-contained, zero-build editorial report
  with dense outcome rows, responsive deep-linked section navigation, text
  search, Markdown copy, and print/PDF controls. Its default
  `editorial_dense_v2` profile accepts profile-owned language, period labels,
  and at most four first-screen highlights. The document builder compiles the
  hero summary from typed primary outcomes, risks, and next actions; authored
  summaries are rejected so process narration cannot bypass the content
  hierarchy. Normalized items may declare
  `visibility=primary|supporting` and a `content_kind`; `runtime` and
  `delivery_receipt` items are rejected unless they are supporting context.
  Generation metadata, source status, digests, and supporting items live in a
  collapsed appendix instead of interrupting the report. The Markdown renderer
  retains supporting items in a labeled appendix so copied and delivered text
  remains complete.
  The renderer has no external runtime dependency and escapes all source
  content before rendering.

Both built-in renderers consume the exact same normalized document. The HTML
artifact records the companion Markdown digest, so a caller can prove that the
shareable page and linear message/document version carry the same primary
content. The public fixture at
`examples/fixtures/periodic-report-editorial-dense.public.json` demonstrates a
reusable, project-neutral report.

## Bundled Miaoda HTML delivery

The bundled `loopx-lark` extension provides an opt-in `miaoda_html` delivery
sink for the self-contained `html_artifact_v0` output. The sink publishes the
already-rendered artifact to a project-owned existing Miaoda HTML app selected
by the delivery request; it does not
rebuild the document or choose an audience. Before any external effect it
checks the single HTML, compressed archive, and uncompressed payload limits.
After publication it preserves the provider's exact release id and requires
readback of that release in `finished` state as well as the same app id,
published URL, and published state. The sink receipt keeps three evidence
layers separate:

- `release_readback` proves the exact provider release reached its terminal
  published state. The capability recomputes this from the requested release id
  and provider status; a provider-supplied `verified` flag cannot override a
  mismatch;
- `access_scope_readback` records the observed scope and login requirement, or
  a typed `unsupported_by_app_type` result when the provider does not expose
  that query for creative HTML apps. Other provider-query failures remain a
  retryable `unavailable` evidence state without erasing a verified release;
- `content_readback` states whether the remote content digest was verified.
  The bundled provider currently records `unavailable` because the provider
  API does not expose the published bytes or their digest; no speculative
  `verified` state is accepted until a provider can supply real digest evidence.

An app id, online URL, or finished release therefore proves delivery lifecycle,
not byte-for-byte equality with the local artifact.

Projects bind the sink explicitly so report generation remains portable and
external writes remain disabled by default:

```json
{
  "sink_id": "miaoda_html_delivery",
  "sink_kind": "miaoda_html",
  "sink_role": "delivery",
  "dependency_policy": "optional",
  "capability": {
    "capability_id": "report.miaoda_html.publish",
    "capability_version": "v0"
  },
  "extension": {
    "extension_id": "loopx-lark",
    "extension_version": "1.6.0",
    "protocol": "periodic_report_sink_v0"
  }
}
```

The project or host still owns the app id, authentication, execution decision,
and access policy. A preview delivery performs validation only and never calls
the injected publish or readback effects. Repeated publication should reuse the
same app id and delivery idempotency key instead of creating a new app for each
report.

The public CLI makes that boundary executable. A
`periodic_report_miaoda_delivery_request_v0` contains the full normalized
profile, its `periodic_report_generation_bundle_v0`, and one typed
`periodic_report_delivery_intent_v0`:

```json
{
  "schema_version": "periodic_report_miaoda_delivery_request_v0",
  "profile": { "schema_version": "periodic_report_profile_v0" },
  "generation_bundle": {
    "schema_version": "periodic_report_generation_bundle_v0"
  },
  "delivery_intent": {
    "schema_version": "periodic_report_delivery_intent_v0",
    "kind": "hosted",
    "sink_id": "miaoda_html_delivery",
    "sink_kind": "miaoda_html",
    "app_id": "app_example123",
    "idempotency_key": "weekly-report-2026-29"
  }
}
```

The abbreviated profile and generation objects above must be replaced by their
complete normalized receipts. Preview the exact request first:

```bash
loopx periodic-report publish-miaoda \
  --request-json periodic-report-miaoda-request.json \
  --format json
```

Preview performs size, profile, binding, extension, and artifact checks but
returns `status=pending_execution` and `intent_satisfied=false`. Local HTML
generation is therefore useful output, not proof that a hosted-report request
was delivered. Publish only after the operator authorizes the external write:

```bash
loopx periodic-report publish-miaoda \
  --request-json periodic-report-miaoda-request.json \
  --execute \
  --format json
```

The command resolves the installed, enabled, doctor-verified `loopx-lark`
revision and its `lark.miaoda_html.publish` permission. Authentication remains
inside `lark-cli`; the request accepts no token or credential. The provider
publishes a temporary `index.html`, reads the exact app back, and sets
`intent_satisfied=true` only when the same app id and online URL agree and the
exact release returned by publication reads back as `finished`. Access-scope
readback is evidence only: an app-type-specific unsupported response is kept as
a typed boundary instead of being treated as a guessed scope. The command does
not claim remote content-digest verification, create an app, change the app's
audience, or change its access scope. Disable or roll back the bundled extension
to remove the provider without affecting already-generated local artifacts.

## Default editorial contract

The reusable renderer deliberately separates audience content from operational
receipts:

- visible body: outcomes, evidence, impact or risk expressed in the summary,
  status, and a concrete next action when one exists;
- collapsed supporting context: profile identity, generation time, source
  health, snapshot digests, renderer lineage, runtime notes, and delivery
  receipts;
- omitted by default: narration about how the report was generated, tool-use
  commentary, local paths, raw logs, or policy explanations that do not change
  an audience decision.

Profiles may provide `editorial.kicker`, `period_label`, `language`, and up to
four highlights. They do not author `editorial.summary`. The builder selects
the highest-ranked typed `outcome` or `decision`, `risk`, and `next_action`
titles, falling back to a typed item's `next_action` field when no dedicated
next-action item exists. It records exact item/field lineage in
`periodic_report_editorial_orchestration_v0`. Both renderers recompute that
summary and reject a stale or hand-edited value. A localized profile language
also selects the built-in report controls, compiler labels, and appendix
labels. Items may use up to four ordered `details` rows to split dense evidence
into named facts, plus `tag_labels` to display localized labels without
changing canonical tags. Primary summaries are limited to 360 characters;
primary `capability_change` items require at least two named details so a long
mechanism narrative cannot return as one wall of text. Artifact parity,
archive-provider canaries,
idempotency, digests, renderer versions, and delivery readback belong in
supporting items or sink receipts. A `source_ref` is only a navigation source;
frozen claims should be backed by the source snapshot digest/ref rather than an
open-ended live query.

Source adapters still decide what each fact means by assigning its
`content_kind` and `value_rank`; profiles decide the report sections and
audience. The orchestration layer composes only those typed facts and never promotes `runtime` or
`delivery_receipt` items. The renderer does not guess business semantics,
delete supporting facts, or silently rewrite a weak report; it verifies the
compiled contract and gives all projects one readable default once their facts
have been normalized.

HTML generation is separate from publication. A static-site, Lark HTML, or
other hosting adapter may publish the artifact and return an exact readback
receipt, but the renderer neither chooses a destination nor performs a write.
An HTML host should publish the generated artifact without re-rendering its
normalized document. A host that applies another presentation must preserve the
artifact's primary/supporting visibility policy and validate direct section
hashes after dynamic content is mounted.
Project profiles still own language, layout policy, audience, cadence, and
selection rules.

## Personal Workspace readback

After the Goal Channel sink has verified delivery and committed the publication cursor, the
local status server can expose the latest report as a typed, content-addressed
milestone projection. Its compact index deliberately omits report prose; the
full projection is fetched over the loopback-only cold path and is accepted
only when its generation id and digest match the current publication cursor.
Pending delivery work and generation-only artifacts remain invisible. The view
is informational and has no browser write authority.
