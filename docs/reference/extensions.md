# Extensions And Capabilities

Capabilities and extensions are independent dimensions in LoopX:

- a **capability** describes what LoopX can do and the product contract exposed
  to callers;
- an **extension** is a delivery unit that can provide one or more capabilities
  and has its own installation, enablement, disablement, and upgrade lifecycle.

Built-in capabilities and extension-provided capabilities share one registry.
Implementation directories do not become capabilities merely because they live
under `loopx/capabilities/`; registration is explicit.

```text
LoopX Core
|-- capability contracts
|-- built-in capability registrations
`-- extension runtime
      |-- extension A -- provides a new capability
      |-- extension B -- implements a core capability
      `-- extension C -- remains disabled
```

## Runtime Responsibilities

Capability and extension are code and delivery boundaries. At runtime, keep
four responsibilities distinct:

| Responsibility | Contract |
| --- | --- |
| Agent | Plans and performs one bounded action through a host/runtime and an available capability. |
| Provider | Calls an external system and returns a bounded observation, effect result, or readback. |
| Capability | Normalizes provider output, applies domain policy and validation, and proposes a finite transition. |
| LoopX Kernel | Accepts or rejects that proposal and owns durable todo, gate, monitor, writeback, quota, recovery, and scheduling state. |

The normal flow is:

```text
Agent -> Capability -> Provider -> external system
Provider readback -> Capability transition proposal -> LoopX Kernel
```

### Agent-scoped external event connectors

`loopx.extensions.external_connector_runtime` defines the provider-neutral
binding shared by interactive group messages and document-comment streams. It
keeps source and cursor references owner-local while exposing a content-free
status projection with source kind, capture policy, ingress policy, response
policy, lifecycle, and declared operations.

The contract separates three independent decisions:

- capture: addressed events, all events from one configured source, or an
  incremental source stream;
- ingress: live steering, the same session's ordered queue, or an Agent-scoped
  asynchronous inbox; and
- response: no response, source-thread response, topic response, or a
  configured mirror.

Live steering and session queue bindings require one exact Agent session;
asynchronous inbox bindings require one owner-local inbox. History catch-up
requires a cursor reference. A response-capable provider must declare both
write and readback support.

Acknowledgement is a separate fail-closed decision. LoopX permits ACK and
cursor advancement only after a committed durable effect (including an
explicit no-follow-up effect) and, when a response is required, verified
provider readback. For an ordered working-session delivery, the completed and
persisted session Turn is the minimum effect receipt; an asynchronous inbox
requires its own accepted writeback or explicit no-follow-up receipt. Raw event
bodies, author identities, source references, cursor values, and provider
payloads do not enter the status projection.

The Agent-bound Lark Goal Topic path is the first caller. Legacy Goal-only
bindings remain readable, while new live, queued, and asynchronous Agent
bindings persist the generic Connector contract alongside their
provider-specific routing data. This runtime contract is not itself a new
capability registry entry; providers advertise stable caller outcomes through
their existing extension and capability surfaces.

Manager group bindings additionally apply the bilingual
[context-capture and Turn-authority contract](protocols/lark-manager-context-authority-v0.md):
an unaddressed message may be retained as bounded non-authoritative context,
but only typed provider addressing may enqueue or steer a Turn.

For asynchronous sources, the same module provides an owner-local incremental
inbox runtime. A provider translates a bounded page into
`agent_external_connector_event_v0` envelopes and calls the capture operation
with the exact previously committed cursor. Capture deduplicates stable event
ids, applies the declared addressed/all-source filter, preserves document
anchors and reply-chain references in private storage, and assigns a restart-
safe order. The page cursor remains pending until every accepted event from
that page is settled; a fully filtered page may checkpoint immediately because
it contains no accepted Agent input.

The bound Agent drains pending events in that order. Settlement rejects an
out-of-order event and calls the common ACK decision, so a missing durable
effect or required provider readback leaves both the event and cursor pending.
Only a successful settlement records the event as acknowledged, and only the
last accepted event from a captured page advances its cursor. After that state
is durably committed, the processed private event body is removed; its
content-free identity remains available for replay deduplication. Provider
failures are stored as content-free error codes. The public inbox projection
exposes only pending count, oldest age, failure count, and freshness; event ids,
bodies, anchors, reply chains, source references, and cursor values remain
owner-local.

`loopx.extensions.external_connector_provider` adds the fail-closed provider
call boundary for document comments. A document-comment registration must
reference material that was registered separately; the comment stream remains
`external_input_only` and cannot promote itself to project authority. Exact
provider identities, scopes, publication requirements, and official HTTPS
repair URLs stay in owner-local permission guidance. Status exposes only
content-free readiness and operation counts. Guidance must match the exact
requirements persisted with the Connector, and those requirements must cover
history capture plus response write and readback when the response policy needs
them.

The provider call sequence is permission evaluation, bounded page read,
durable inbox capture, Agent effect, provider response with readback, then ACK.
The runtime does not call the page reader until the registered permissions are
ready, does not call the response writer before a committed effect receipt, and
does not advance the cursor until an event-bound response receipt and provider
readback succeed. Concrete provider adapters supply the page reader and response
writer; LoopX does not own their credentials or raw payloads.

The bundled Lark extension supplies the first concrete document-comment
adapter through `lark-cli`. Its owner-local target binds a safe Connector source
reference to a private document URL, profile, and bot or user identity. The
adapter probes exact comment read/create scopes, paginates comment cards and
nested replies with a restart-safe private cursor, and maps stable reply ids to
hashed Connector event ids. A completed scan restarts from the first comment
page so new replies on older cards remain discoverable; the generic inbox
deduplicates already captured or acknowledged events. Because Lark does not
expose provider idempotency for reply creation, the adapter requires an
owner-local receipt store: it records intent before the write, recovers a reply
by its opaque idempotency marker after a crash, records the returned reply id
before readback, and reuses that receipt on retry. The comment-list shortcut
requires `lark-cli` 1.0.69 or newer. Public status and provider receipts omit
document URLs, profiles, raw ids, cursor values, bodies, and subprocess output.

The Lark adapter intentionally rejects `addressed_only`. Correct mention
filtering needs an explicit provider identity contract, and treating every
comment on a configured document as an Agent mention would silently weaken the
generic capture policy. Configured-source and incremental bindings remain
supported. Source-thread response bindings also filter solved and whole-
document comment cards, which the Lark reply API does not allow replying to.

`Provider` is an implementation role. When it implements a LoopX capability,
it is registered under that capability; a standalone extension provider may
instead expose only its own bounded command. A provider may be built into LoopX
or delivered by an extension. `Extension` is not a fifth runtime role: it owns
provider packaging, installation, enablement, upgrade, and compatibility
lifecycle. It does not own domain transition policy or goal state. Domain state
and receipts are data crossing these boundaries, not independent actors.

## Repository Layout

The repository uses one path for each ownership boundary:

```text
loopx/capabilities/<capability>/   caller-facing contracts and core providers
loopx/extensions/                  extension lifecycle and bundled providers
packages/<package-id>/             independently installable distributions
```

`loopx/extensions/` is a Python package shipped in the LoopX wheel.
`packages/` is a monorepo package root; its children have their own packaging
metadata and do not become part of the LoopX wheel. There is intentionally no
repository-root `extensions/` directory. That duplicate name obscured whether a
path represented importable LoopX code or a separately installable artifact.

The layout does not merge capabilities and extensions. They remain separate
axes and compose through the capability/provider registry: a capability names
the stable outcome contract, while an extension owns provider delivery and
lifecycle.

## Registration Model

Every registered capability declares three provider-facing fields:

- `origin`: `builtin` or `extension`;
- `visibility`: `public` or `internal`;
- `provider_id`: `loopx-core` or the extension manifest id.

The built-in catalog remains the default source. Extension manifests declare
providers and contracts; the extension runtime state is the only source for
whether each provider is installed, enabled, and doctor-ready. Duplicate
capability or provider ids fail closed. Internal registrations remain available
to the registry but are omitted from the public catalog.

Catalog discovery does not scan arbitrary directories or import extension
Python code. A caller can add a declaration-only manifest to a catalog read:

```bash
loopx capability list \
  --extension-manifest /path/to/extension.toml \
  --format json

loopx capability show lark-kanban \
  --extension-manifest /path/to/extension.toml \
  --format json
```

The resulting provider reports `declared=true` and
`installed=enabled=ready=false`. The normal CLI read also composes installed
providers from `<runtime-root>/extensions/state.json`, so the catalog and
runtime dispatch see the same active manifest revision. `loopx extension`
registers an already-installed subprocess entrypoint only after the manifest,
API, permission, and doctor checks pass. It does not download packages or grant
new permissions.

## Starter And Scaffold

Create the next standalone extension through the same management surface. The
command previews by default and writes only with `--execute`:

```bash
loopx extension init loopx-example --format json
loopx extension init loopx-example --execute --format json
```

The default destination is `packages/<extension-id>`. Use `--destination`
when the provider is developed in another package or repository. The scaffold
creates an independently installable Python package, declarative manifest,
JSON stdin/stdout provider, versioned request and response JSON Schemas,
side-effect-free doctor, example request, and a short README. The generated
provider rejects missing, mismatched, or structurally invalid request contracts
before doing work. The init receipt explicitly identifies the starter as
`standalone` and names `loopx extension run` as its managed entrypoint.

`extension init` intentionally does not register a capability. It currently
generates only the complete standalone path. A `[[provides]]` extension needs a
real caller contract and command, while an `[[implements]]` extension needs an
existing capability-specific resolver, policy check, action/scope mapping, and
execution-envelope adapter. A generic scaffold cannot infer those authority
semantics safely. Add a capability integration profile first, then scaffold or
author the provider against that profile; do not add manifest tables that are
discoverable but not callable.

The command refuses every existing destination, including an empty directory;
there is no force or merge mode. It also does not build, install, register, or
enable the generated provider. Those remain explicit lifecycle steps so the
package manager and LoopX activation state cannot drift behind one command:

Run all three commands from the same activated Python environment. LoopX
verifies the provider through its installed console entrypoint, so installing
the package into a different environment correctly fails with
`entrypoint_missing`.

```bash
python3 -m pip install packages/loopx-example
loopx extension install \
  --manifest packages/loopx-example/extension.toml \
  --execute \
  --format json
loopx extension run loopx-example \
  --input-json packages/loopx-example/examples/request.json \
  --execute \
  --format json
```

Treat the generated response as executable documentation, not a permanent
domain contract. Before productizing the provider, replace the starter request,
response, permission, and doctor semantics with bounded domain-specific ones.

## Runtime Lifecycle

The lifecycle is local, explicit, and dry-run by default:

```bash
# Inspect the bundled OpenViking pilot, then activate it only if doctor passes.
loopx extension install \
  --bundled openviking-semantic-preference \
  --execute \
  --format json

loopx extension list --format json
loopx extension doctor openviking-semantic-preference --execute --format json
loopx extension disable openviking-semantic-preference --execute --format json
loopx extension enable openviking-semantic-preference --execute --format json

# Activate the bundled Lark lifecycle provider before using lark-inbox.
loopx extension install --bundled loopx-lark --execute --format json
```

After an install, update, or rollback changes the active LoopX release,
revalidate all enabled extension runtimes as one bounded read-only batch:

```bash
loopx extension doctor --all-enabled --execute --format json
```

The local installer runs this batch automatically. A passing doctor refreshes
only the local runtime-identity proof; it grants no new provider permission and
performs no connector write. Failed providers remain fail closed and the batch
names the exact repair command.

For a separately distributed provider, pass `--manifest <extension.toml>`.
`upgrade` validates and probes the new manifest before changing the active
revision. `rollback` probes the previous revision before switching back. A
failed probe leaves the current revision untouched. Activation state contains
validated manifest snapshots and revision ids in the private LoopX runtime
root; it does not contain provider output or credentials.

Standalone extensions use the same managed command shape as built-in
capabilities: LoopX accepts a bounded request, previews by default, executes
only with `--execute`, and returns a structured receipt. The v0 invocation
contract is:

```bash
loopx extension run <extension-id> --input-json <path-or-> [--execute]
```

The active manifest fixes the executable, arguments, protocol, permissions,
timeout, and revision. The caller supplies one JSON object over stdin and the
provider must return one JSON object over stdout; both payloads are UTF-8 bytes,
so a provider must pin its own stdio instead of falling back to the host locale
codec. LoopX does not accept an arbitrary executable path or argument
passthrough. `run` never installs a missing extension, and it rejects extensions
with `[[provides]]`, `[[implements]]`, or any declared permission; those
providers are invoked through their capability or domain command. Extension
lifecycle management is shared, but direct execution is reserved for
zero-permission, runtime-only standalone extensions.
Direct provider binaries are implementation and debugging surfaces; they are
not the supported management API.

### Goal-bound external capability providers

An extension that owns a new domain capability may attach one relative JSON
`integration_profile` to its `[[provides]]` record. LoopX reads, validates, and
snapshots this profile during manifest installation; later invocation resolves
only the enabled, doctor-ready active revision. The profile is data, not an
import or executable path.

```toml
[runtime]
protocol = "requirement_projection_provider_v0"
entrypoint = "example-requirement-provider"
required_permissions = ["requirement.read"]

[[provides]]
id = "requirement-delivery"
kind = "requirement_delivery"
title = "Requirement delivery"
visibility = "public"
integration_profile = "integration-profile.json"
```

```json
{
  "schema_version": "loopx_external_domain_capability_profile_v0",
  "capability_id": "requirement-delivery",
  "protocol": "requirement_projection_provider_v0",
  "operations": [
    {
      "id": "observe",
      "effect_class": "read_only",
      "required_permission": "requirement.read",
      "request_schema": "loopx_external_domain_capability_request_v0",
      "result_schema": "loopx_external_domain_capability_result_v0"
    }
  ]
}
```

A durable Goal binding enables bounded operations for one exact active provider
revision. It is Goal-scoped rather than Turn-scoped, so the same working Agent
session may reuse an enabled read-only capability without creating a governed
Turn for every observation. Preview the binding first, then persist it in the
Goal record of the project registry:

```bash
loopx --registry .loopx/registry.json capability bind requirement-delivery \
  --goal-id example-goal \
  --operation observe

loopx --registry .loopx/registry.json capability bind requirement-delivery \
  --goal-id example-goal \
  --operation observe \
  --execute
```

LoopX resolves the enabled, doctor-ready provider while creating the binding.
The persisted `goal.external_capability_bindings` entry has this typed shape:

```json
{
  "schema_version": "loopx_goal_external_capability_binding_v0",
  "goal_id": "example-goal",
  "capability_id": "requirement-delivery",
  "operations": ["observe"],
  "provider": {
    "extension_id": "example-requirement-provider",
    "revision": "sha256:active-revision",
    "profile_digest": "sha256:integration-profile"
  }
}
```

Preview or execute a read-only operation by resolving the durable Goal binding:

```bash
loopx --registry .loopx/registry.json capability invoke requirement-delivery \
  --operation observe \
  --goal-id example-goal \
  --input-json provider-input.json

loopx --registry .loopx/registry.json capability invoke requirement-delivery \
  --operation observe \
  --goal-id example-goal \
  --input-json provider-input.json \
  --execute
```

The input object contains `context_refs` plus a bounded domain `input` object.
LoopX checks that the requested capability and operation are enabled for the
Goal and that the provider id, active revision, and snapshotted profile digest
still match. The binding digest plus the bounded input derives a deterministic
invocation id. Managed runtime limits still apply. `--goal-binding-json`
remains available as a compatibility and debugging input, but normal execution
should resolve the binding from `--goal-id` so the LoopX registry remains the
task ground truth.

The direct `capability invoke` route admits read-only operations only: provider
results must not contain domain mutations, transition proposals, effect
receipts, raw payloads, credential-like fields, or private-looking strings. It
does not write LoopX state or spend quota.

An integration profile may also declare `effect_class: external_write`, but
that operation is deliberately unavailable through direct invocation. A host
adapter must call the governed material lifecycle in
`loopx.extensions.governed_capability_execution`:

An external-write operation also declares a typed `todo_contract` beside
`effect_class`, containing one or more lower-snake `action_kinds` and bounded
`target_key_prefixes`.

When a long-running provider needs LoopX to keep polling its external job, the
same operation may declare a `transition_contract`. This is an authority
allowlist, not a provider-owned Todo schema:

```json
{
  "proposal_kinds": [
    "continuous_monitor_upsert",
    "continuous_monitor_complete"
  ],
  "monitor_key_prefixes": ["example-provider:"],
  "monitor_action_kinds": ["poll_external_run"],
  "monitor_target_key_prefixes": ["external-run:"],
  "monitor_required_capabilities": ["network"]
}
```

The profile bounds every monitor identity, action, external target, and
required capability that the provider may propose. A proposal outside those
bounds fails before any LoopX state write. The provider receives no registry
path and never calls Todo APIs directly.

1. obtain `quota should-run` admission for one exact Goal, Agent, Todo, and
   `turn_instance_id`; the selected open Agent Todo's `action_kind` and
   `target_key` must match the operation profile's `todo_contract`, so an
   admitted Turn cannot borrow an unrelated Goal-bound write capability;
2. call `start_governed_external_capability(...)`, which journals intent before
   dispatch and gives the provider the settlement effect id as its stable
   idempotency key;
3. call `reconcile_governed_external_capability(...)` until the provider returns
   a terminal `loopx_external_effect_receipt_v0`;
4. let the LoopX Kernel materialize admitted monitor upserts through the normal
   Todo APIs. A `running` result may only create or retarget its bounded
   continuous monitor, so the recovery entry remains schedulable while the
   external operation or its settlement is incomplete;
5. supply typed writeback and spend callbacks. LoopX reuses the shared Turn
   settlement driver, requires the effect receipt digest in durable writeback,
   and never spends quota before that writeback commits;
6. only after the shared Turn settlement commits, let the Kernel materialize an
   admitted terminal monitor completion. A failed writeback or spend therefore
   leaves the monitor open for recovery instead of closing the only retry lane.

The provider may return `running`, so a service-side job can outlive the bounded
provider process. Start and reconcile are separately replayable from a mode-0600
journal. Exact provider revision, request digest, Goal binding, settlement
identity, transition proposal receipts, provider effect receipt, writeback
receipt, and quota receipt remain attached to the same invocation. Each
materialized proposal is checkpointed immediately. Monitor upserts belong to
the pre-settlement phase; monitor completion belongs to the post-settlement
phase. A crash between either Todo write and its checkpoint recovers by the
proposal's stable monitor key and completion identity instead of duplicating
work. A crash after an external
effect replays with the same idempotency key and reconciles the receipt instead
of starting an unrelated operation. One settlement effect id owns exactly one
material invocation: retrying the same request replays it, while attempting a
different operation or input under the same Turn receipt fails before provider
dispatch. A new invocation also requires `should_run=true`; an existing journal
may still be recovered with its exact typed receipt after the runnable decision
has changed.

Transition proposals are deliberately narrower than arbitrary Goal mutation.
The first version supports only continuous-monitor upsert and completion. It
cannot create a general advancement Todo, change a Goal, widen capability
authority, complete another Agent's work, or reopen a completed monitor. The
Kernel resolves one exact monitor by its admitted binding key, applies the
normal ownership and completion rules, and returns a content-bounded receipt.

Goal enablement alone never grants write authority. Creating or updating the
binding is an explicit Goal configuration change: it uses preview/apply, but it
does not create a Turn or spend Turn quota. Turn scope starts only when an
invocation may produce a material external or LoopX-state effect. The Goal
binding is a local typed projection, not a security token or proof of a remote
issuer; service authentication and authorization remain the provider's
responsibility.

The generic runner is deliberately non-effectful and grants no operation
effects. Both manifest `permissions` and runtime `required_permissions` must
be empty. Any operation needing read, write, send, publish, manage, or another
declared authority must enter through a capability or domain command that can
apply its domain policy before managed dispatch. Request files and stdin are capped while
being read. Provider stdout and stderr are drained concurrently and the provider
is started in a dedicated process group. Timeout or either output limit
terminates the entire group, so a descendant cannot continue effects after
LoopX reports that execution stopped.

Effectful capability dispatch uses
`loopx_extension_execution_envelope_v0`. The capability command, not the caller
or provider, creates this minimal envelope after resolving one enabled,
doctor-ready implementation and checking the domain activation policy. It binds:

- the exact action;
- structured effect scope;
- extension id and active manifest revision;
- a digest of the exact provider request, excluding the attached envelope.

The provider repeats this validation before any effect. A caller-supplied
envelope, different request, wider scope, changed action, or mismatched active
revision fails closed. Capability id, protocol, and permission remain
authoritative in manifest resolution instead of being duplicated in the
envelope. The envelope is request binding, not proof of issuer identity, a
security token, or a replacement for service-side authentication and
authorization.

`disable` is reversible, but `enable` never trusts an earlier readiness result:
it reruns the configured doctor and changes the enabled bit only after that
probe succeeds. A successful doctor binds readiness to both the active manifest
revision and a content-addressed runtime identity. Moving an unchanged release
to a new install root, inode, or equivalent interpreter path preserves that
identity. The active revision retains up to 32 independently verified runtime
identities, so probing a source checkout does not displace an installed release.
Unverified executable, interpreter or module content still fails closed. A failed
executed probe revokes its observed identity only; `probed_entrypoint_identity`
is diagnostic evidence, never proof of readiness. An unavailable entrypoint
cannot activate. Upgrade and rollback qualify their selected runtime afresh;
proofs from another manifest revision do not carry over. Old single-identity
state stays read-only until a lifecycle write migrates it. Evicting the oldest
identity requires re-running doctor for that artifact, never implicit admission.

An enabled implementation is resolved by capability id and versioned protocol,
then checked against its declared permission, current revision, and current
doctor proof. Callers do not need to copy an extension id into normal config.
Disabled or stale implementations remain visible in the catalog but are not
dispatch candidates. When multiple enabled, doctor-ready extensions implement
the same capability/protocol pair, resolution fails closed until the caller
selects the intended provider during migration. Domain config may add bounded
provider arguments, but cannot replace the manifest entrypoint, timeout,
protocol, or permission contract.

Compatibility delegates use the same revision-bound readiness rule. Every
configured `loopx lark-inbox` operation resolves the enabled `loopx-lark`
provider, its current doctor proof, and the permission needed by that operation
before entering the in-process provider code. Disabling the extension therefore
blocks new collector starts, drain, ingest, reply, and acknowledge operations;
upgrade and rollback affect new invocations without changing project
configuration. Extension lifecycle commands do not terminate an already
running host-managed collector process; stop or restart that supervisor service
separately when changing the active provider revision.

Quota and Turn composition apply the same read gate. They inject the Lark
extension's urgency projector only after resolving `lark.inbox.read`; provider
profile/chat schema and private config reads stay in the extension. If the
extension is missing, disabled, or stale, urgency is unavailable and cannot
activate a Lark work lane. This adds no agent-facing CLI arguments.

## Presentation Surfaces

An independently delivered extension can declare an operator-facing
presentation surface without shipping browser code or making Core understand
the provider's domain. The provider owns source validation, the `view_schema`
contract, and the mapping into it. Core owns lifecycle resolution, revision
binding, persistence, and the public-safe surface catalog. Every declared
surface names its validator with a `module:callable` reference. The publisher
process loads that exact callable before accepting the provider view; missing or
unloadable validators fail closed. Core does not freeze any one domain's view
into core. Dashboard's generic status parser consumes only that compact
contract. A built-in renderer may separately own a provider view schema, as the
Finance renderer does for `decision_research_dashboard_v0`.

The finance value-discovery extension declares the first such view,
`decision_research_dashboard_v0`, a read-only decision-research view:

```toml
[[presentation_surfaces]]
id = "investment-research"
kind = "decision_research_dashboard"
title = "Investment Research"
view_schema = "decision_research_dashboard_v0"
view_validator = "loopx_finance_value_discovery.presentation_view:validate_decision_research_view"
visibility = "public-safe"
empty_state_title = "No validated research yet"
empty_state_detail = "Publish a validated projection."
```

Declarations are strict and bounded. Surface ids are stable kebab-case
identifiers. Titles and empty-state text are plain text without markup, URLs,
or local paths. A declaration does not make the surface visible by itself:
the extension must be installed, enabled, and doctor-ready at its active
manifest revision.

Publication uses the same managed, dry-run-by-default lifecycle gate as a
standalone extension invocation:

```bash
loopx extension publish-projection \
  <extension-id> \
  <surface-id> \
  --input-json <validated-owner-input.json> \
  --format json

loopx extension publish-projection \
  <extension-id> \
  <surface-id> \
  --input-json <validated-owner-input.json> \
  --execute \
  --format json
```

The preview resolves the active declaration but does not run the provider or
write a file. With `--execute`, LoopX runs the exact ready provider, validates
its `extension_presentation_projection_v0`, binds extension id, active revision,
surface kind, schema, and visibility from lifecycle state, then atomically
writes an `extension_projection_surface_v0` envelope. The receipt includes the
canonical payload SHA-256 and confirms exact readback. If lifecycle identity or
the declaration changes while publishing, the write fails closed.

Status collection never executes the provider, reads its owner input, or grows
the Dashboard status hot path with extension rows. The loopback status server
advertises a cold-path surface-catalog endpoint that reads only the active
manifest snapshot and persisted, bounded envelope. A `ready` or `review_due`
catalog item carries a content-addressed `detail_ref` (extension id, surface id,
revision, and payload SHA-256) rather than inlining the full provider view.
Consumers that need the view use the separately advertised projection endpoint.
That read revalidates the active extension revision, declared surface, persisted
envelope, and payload hash before returning a `public-safe` projection. Because
the endpoints have no authenticated audience contract, projection reads reject
`owner-only` surfaces rather than treating loopback access as owner
authentication.
Visibility follows this matrix:

| Lifecycle or projection state | Cold-path surface catalog | Dashboard |
| --- | --- | --- |
| Not installed, disabled, or doctor-stale | No item | Tab and home summary hidden |
| Ready declaration, no matching active-revision file | `empty` | Declarative empty state |
| Valid active-revision envelope | `ready` with `detail_ref` | Read-only view and summaries |
| Valid envelope past `review_due_at` | `review_due` with `detail_ref` | View retained with review warning |
| Corrupt active-revision envelope | `invalid` without `detail_ref` | Safe diagnostic, no partial content |
| File belongs to another revision | `empty` | No fallback to old content |

Disable hides the surface but does not delete its projection. Re-enabling and
successfully re-running doctor restores a matching-revision projection.
Upgrade keeps the old file for audit continuity, but the new revision sees
`empty` until it publishes its own envelope. Rollback applies the same exact
revision rule.

Presentation projections are display sinks, not authority. They cannot change
goal state, promote a method, submit a trade, or grant provider permissions.
The finance research view rejects credentials, account or order fields, raw
provider/request/response bodies, private relative or absolute paths, sensitive
URL parameters, non-finite numbers, and unbounded text. Canonical persistence
uses standard JSON only; `NaN` and infinity fail closed before publication.
Providers must emit compact references and conclusions, not private evidence
bodies. Dashboard routing identifies a surface by both extension id and surface
id so independently versioned providers may reuse a local surface id without
colliding. A `public-safe` surface still passes the public/private scan; an
`owner-only` surface describes an operator boundary and is never permission to
persist secrets or bypass that scan.

Run the public synthetic lifecycle proof after changing this contract:

```bash
uv run --extra test python examples/extension-presentation-surface-smoke.py
```

## Placement Decision For Agents

Before creating a directory, LoopX or an executing agent must answer these
questions in order:

1. **What user outcome and caller-visible contract is being added or changed?**
   Capability ids describe outcomes, not transports. Names such as
   `connector`, `provider`, `adapter`, or `sink` usually describe an extension
   or internal mechanism unless callers use and validate that mechanism as an
   independent product contract. If an existing
   capability already owns that contract, add the implementation to
   `loopx/capabilities/<existing-capability>/` instead of creating a sibling.
2. **Must LoopX core always ship and maintain the implementation?** If yes, it
   may be a built-in capability. A new built-in needs a stable id, a real
   entrypoint or protocol call site, focused validation, and catalog
   registration.
3. **Does the implementation need independent installation, enablement,
   disablement, upgrade, dependencies, credentials, or provider ownership?**
   If yes, it is an extension provider. The capability remains the contract;
   the extension manifest declares that it provides the contract.
4. **Is this only registration or lifecycle machinery shared by all
   extensions?** Put that mechanism in `loopx/extensions/`, not in a provider
   package.
5. **Is this only an internal helper?** Put it in the nearest module that owns
   its change reason. Do not register a capability or create an extension.

Use this placement map after answering the questions:

| Change | Placement |
| --- | --- |
| Existing built-in capability behavior | `loopx/capabilities/<capability-id>/` |
| Built-in catalog and registration contract | `loopx/capabilities/catalog.py` or `registry.py` |
| Generic extension runtime | `loopx/extensions/` |
| Co-located optional extension distribution | `packages/<package-id>/` |
| Separately distributed extension/provider | owner package or repository |
| Internal implementation helper | nearest owning module |

Some work belongs on both axes, but an optional workflow does not need a
capability merely because it is user-visible. Create a capability only when
LoopX callers need a provider-neutral contract, catalog identity, and routing
surface. An extension-owned command and packet contract may remain a
standalone extension runtime. Finance value discovery uses this standalone
shape; public-market, filing, and news collection can stay inside that
extension until a real cross-provider LoopX contract exists.

`value-connectors` is an existing compatibility CLI and protocol surface. Do
not use it as the public capability owner for new work. Migrate each profile
to an existing outcome capability such as `issue-fix` or `content-ops`, or to
a standalone extension such as `loopx-finance-value-discovery`, before
retiring the compatibility surface. This keeps the migration
behavior-preserving instead of replacing one broad bucket with another broad
bucket.

Before editing, record a compact rationale in the active todo or plan:

```text
capability_id: <existing-or-new-contract>
provider_id: loopx-core | <extension-id>
origin: builtin | extension
placement: <target-directory-or-package>
reason: <why the nearest existing owner is or is not sufficient>
```

Use `capability_id: none` for a standalone extension. Do not create a new
capability directory merely because no current directory has the feature name
or because the manifest needs a lifecycle anchor. Do not create an extension
merely because an external service is involved: a built-in connector can still
belong to an existing capability when it shares the core release and
lifecycle.

## Manifest Contract

An extension manifest is declarative TOML. An executable `[runtime]` is enough
for a standalone extension. `[[provides]]` records add new capability contracts
to the catalog. `[[implements]]` binds a provider runtime to an existing
core-owned capability without duplicating that capability id. Do not add either
table solely to make a runtime installable.
The v0 runtime exposes integer extension API version `1` and accepts bounded
integer constraints such as `>=1,<2`; incompatible manifests fail closed.

`[[hook_adapters]]` lets an extension contribute provider ports to an existing
capability-owned hook point without adding provider branches to the capability
composition root or control-plane kernel. Discovery reads only installed
manifest declarations and admits a factory only when the extension is enabled,
doctor-ready, and authorized for every declared adapter permission. The
factory must return exactly the declared callable ports. Import, activation,
and factory failures become content-free optional-adapter failures; they do not
execute or replace kernel logic.

`phase = "capability_action"` is for an explicit typed action whose semantics
have already been decided by the Agent or caller. The adapter may bind and
settle provider evidence, but it must not infer the action from provider text.

```toml
schema_version = "loopx_extension_manifest_v0"
id = "loopx-lark"
version = "1.0.0"
requires_loopx_api = ">=1,<2"
permissions = ["read_status", "read_todos", "external_write"]

[runtime]
protocol = "lark_kanban_provider_v0"
python_module = "loopx.extensions.lark.provider"
doctor_args = ["--doctor"]
required_permissions = ["read_status", "read_todos"]
timeout_seconds = 30

[[provides]]
id = "lark-kanban"
kind = "projection_sink"
title = "Lark Kanban projection"
status = "active"
visibility = "public"
real_world_anchor = "operator-facing Lark Base projection"
user_value = "Project public-safe LoopX status and todo rows into Lark."
entry_command = "loopx lark-kanban sync"
next_real_step = "Validate one explicitly enabled owner-approved sink."

[[hook_adapters]]
id = "lark-periodic-report-source"
capability_id = "periodic-report"
target_hook_id = "periodic_report.request"
phase = "capability_action"
factory = "loopx.extensions.lark.periodic_report_request:build_lark_periodic_report_hook_adapter"
required_permissions = ["lark.inbox.read", "lark.inbox.write"]
ports = [
  "periodic_report.request.bind_source",
  "periodic_report.request.settle_source",
]
```

The bundled OpenViking pilot uses `[[implements]]` instead:

```toml
[runtime]
protocol = "semantic_preference_provider_v0"
entrypoint = "loopx-openviking-semantic-preference"
doctor_args = ["--doctor"]
required_permissions = ["semantic_preference.read"]

[[implements]]
capability_id = "semantic-preference"
protocol = "semantic_preference_provider_v0"
```

The optional `packages/loopx-obelisk` package follows the same placement rule.
It implements the existing `decision-context` capability's advisory
`ContextProvider` port; it does not register a second session-context
capability. LoopX Core parses a copied Codex deep link into the normalized
`host-session:codex:<thread-id>` scope, and the extension maps that scope to
Obelisk's public read-only query CLI. See
[`decision_context_advisory_provider_v0`](protocols/decision-context-advisory-provider-v0.md)
and the package README for activation, validation, and removal.

The bundled periodic-report archive uses the same ownership direction. It
implements one existing capability port rather than registering a second
"OpenViking report" product capability:

```toml
[runtime]
protocol = "periodic_report_sink_v0"
python_module = "loopx.extensions.openviking_periodic_report.provider"
required_permissions = ["openviking_context_write"]

[[implements]]
capability_id = "periodic-report"
protocol = "periodic_report_sink_v0"
```

Its capability-specific activation wrapper additionally requires an enabled
`periodic_report_activation_v0`, a matching non-disabled sink binding, and the
observed `openviking_context_write` runtime capability. Those project and turn
facts do not belong in the generic extension manifest or lifecycle state.

### Finance value-discovery sample

`packages/loopx-finance-value-discovery/` is a co-located, independently
packaged standalone workflow. Its manifest registers only the
`finance_value_discovery_extension_v0` runtime; it does not create a capability
catalog entry or a `value-connectors` route. After an explicit install and
successful doctor probe, invoke it through the managed extension command:

```bash
loopx extension install \
  --manifest packages/loopx-finance-value-discovery/extension.toml \
  --execute
loopx extension run loopx-finance-value-discovery \
  --input-json packages/loopx-finance-value-discovery/examples/paypal-debeta-discovery.json \
  --execute \
  --format json
```

The included PayPal packet preserves a reusable de-beta research method, not
an investment conclusion: start from a frozen cross-sectional screen, retain
same-group controls, separate structural growth from profit-pool capture,
require dilution and terminal-risk evidence, then falsify the candidate before
selecting at most one successor. The reducer performs no live reads, gives no
price target or advice, and cannot trade or start a continuous watch.

For upgrade compatibility, the retired
`value-connectors` Finance selectors, including the legacy
`plan --connector-id finance_market_snapshot` form, remain as migration
tombstones. They return `value_connector_extension_migration_v0` with ordered
extension startup prerequisites; they do not execute Finance or restore a
Finance capability. Source checkouts can install the co-located provider package
before registration. Packaged LoopX users still need a separately distributed
provider artifact, so agents must stop rather than claiming automatic
installation when that artifact is unavailable.

Runtime-required permissions must be a subset of the provider's declared
permissions. Declaring either does not grant authority: existing LoopX goal
boundaries, user gates, and external-write authorization still decide whether
an operation may execute. Extension packages are trusted executable code rather
than an operating-system sandbox; the manifest records and constrains managed
routing, but cannot make an untrusted provider safe.

Every executable runtime declares exactly one launch target. Use `entrypoint`
for a separately installed executable such as the OpenViking provider. Use
`python_module` for a provider shipped in the LoopX Python package. Module
providers run as `<current-loopx-python> -m <module>` and their doctor proof is
bound to both that interpreter and the resolved module source. This lets a
clean source checkout and a local LoopX release activate bundled providers
without separately installing a console script; catalog discovery remains
declarative and does not import the module.

Automatic Goal Channel notifications require extension activation immediately
before provider transport, after existing selection, suppression and receipt
checks. An unconfigured, disabled, suppressed, unselected or already-delivered
notification does not require doctor readiness and cannot block primary Turn
settlement for that reason. A selected new delivery still requires the enabled,
current verified revision, declared permissions, provider identity, channel
membership and readback. There is no automatic doctor execution or new external
write authority. Notification selection remains owned by the quota contract;
the Python extension only orders its existing local checks and transport.

Because discovery is declarative, a declared launch target is only shape-checked
until activation. The public smoke
`examples/extension-entrypoint-surface-smoke.py` is a repository-scoped coverage
guard for that gap rather than an activation check: without importing provider
code it resolves the declared `python_module`, the `entrypoint` console script,
each hook adapter `factory`, and each presentation `view_validator` for every
bundled and co-located manifest in this repository against the source tree, so a
renamed or removed entrypoint fails in the same change that removes it instead
of at the user's first activation. It does not cover an edited or third-party
manifest, and detection is structural: an attribute the scanner cannot see, for
example one installed through `getattr`, is reported as unresolved.

### Local executable locations

Successful executable install/upgrade and doctor operations save the selected
absolute launcher path in the host-local revision state, separately from the
portable manifest. When that launcher is a symlink, identity checks still hash
the final executable artifact while the launcher directory remains the child
process PATH prefix. Enable, rollback, update-time revalidation and invocation
use that revision's saved location instead of rediscovering a same-named
executable on the current shell PATH. File-identity checks remain mandatory; a
missing saved executable does not fall back to another PATH entry. Package
upgrades resolve and verify the new revision's executable through the explicit
upgrade workflow. Bundled `python_module` providers continue using the current
LoopX interpreter and retain their existing identity checks.

For executable providers, only the child process prepends the executable's
directory to PATH, so tools installed in the same environment remain available.
No complete environment, credentials, package contents, or local path is added
to the public manifest or doctor result. An explicitly supplied execution
environment remains the base environment; only this PATH prefix is added.

Legacy installations without a saved location require one successful
`loopx extension doctor <extension-id> --execute` with the provider environment
available on PATH. Read-only doctor calls do not migrate state. Subsequent
revalidation can run from the desktop's minimal PATH. If an executable is moved,
restore its registered location or explicitly upgrade its local manifest to
point to the new executable; do not hide the failure by disabling the extension.

## Scope Boundaries

The executable v0 runtime intentionally does not:

- rename or move existing capability implementation directories;
- infer capabilities from Python packages;
- download, build, or install extension packages;
- start services, create credentials, or edit provider configuration;
- import an extension entrypoint during catalog discovery;
- let manifest permissions bypass LoopX control-plane authority.

These boundaries keep activation reversible and auditable while leaving package
distribution and service setup to explicit operator-owned workflows.

Provider migration follows the same direction. Core routing consumes compact
provider-neutral read models, while provider packages own collection, transport,
credentials, and external effects. For example, quota reads
`operator_inbox_urgency_v0` through an injected projector. The generic parser
and read-model contract stay in the control plane; Lark schema, identity,
destination, collection, reply transport, and provider-owned configuration live
under `loopx/extensions/lark/`. The existing
`loopx lark-inbox` command remains a direct compatibility delegate, but it now
requires an installed, enabled, doctor-verified `loopx-lark` revision with the
operation's declared permission. The provider subprocess currently implements
doctor only; command execution remains in-process until the transport protocol
is migrated.
The former `loopx.capabilities.lark` provider imports are intentionally removed
instead of kept as wrappers. Lark Kanban and Explore presentation sinks live
under `loopx.extensions.lark.presentation`; their compatibility CLI delegates
require the installed, enabled, doctor-verified revision to declare
`lark.projection_sink.use`. No additional agent-facing CLI arguments are
required.
