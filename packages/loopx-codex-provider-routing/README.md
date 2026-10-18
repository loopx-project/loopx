# LoopX Codex Provider Routing

This optional extension integrates **native Codex account slots** with LoopX.
It observes allowance, selects an authorized available account after exhaustion,
and asks the installed native slot owner to switch at an idle boundary. Codex
keeps its official OpenAI login, model settings and history. CPA is no longer
the recommended provider path.

Install this package in the LoopX runtime, then open **Settings → Model provider
→ Native Codex accounts**. The page discovers slots, enables automatic handoff,
checks allowance, switches explicitly and reads back recovery. The same owner
is available through `loopx-codex-accounts`. See the [native guide](NATIVE.md)
for prerequisites, migration and the safe-boundary limitation. Installation
alone switches nothing.

## Placement

This package is an **extension** that supplies a standalone integration
provider. It is not a built-in capability:

- Codex App owns task settings, turn snapshots and the session store;
- the installed Personal Apps slot manager owns credential transactions,
  idle admission, login-cache preservation and rollback;
- the optional provider owns allowance observation and its default-off policy;
  its TypeScript selector is shared by CLI and App;
- LoopX Core supplies optional lifecycle and owner-local presentation transport,
  without becoming a credential or model proxy.

The **managed qualification protocol** declares no permissions and performs no writes. It does not read
`HOME`, `CODEX_HOME`, auth files, process tables or network endpoints. Callers
must convert local observations into the content-free snapshot schema before
qualification. The separately invoked native host reads local credentials,
queries native usage and performs explicitly enabled account effects. It is
never invoked by the permissionless protocol or its doctor.

## Legacy compatibility

Existing CPA contracts, fixtures and `loopx-cpa-operator` remain for explicit
legacy use and rollback. They are not started, migrated or selected automatically.
The following operations describe that compatibility protocol; they do not
certify native-slot availability or authorize a native switch.

## Operations

`compile_catalog`
: Compiles secret-free logical profiles and routes into
  `codex_provider_routing_catalog_v1`. One bounded account ring can back Auto,
  Prefer A, Prefer B and Luna without presenting separate rings to the user.
  Route affinity is only a hint and must be revalidated against required input
  modalities and service tier on every attempt. A terminal text-only fallback
  can serve compatible Sol requests but cannot receive image history; Luna has
  no heterogeneous fallback tail. A route may declare one `fast_selector`;
  the compiler then emits a `fast/<route>` sibling row whose candidates are
  limited to Fast-capable providers and whose default tier is `fast`.

`normalize_selector_request`
: Resolves the original App model selector before provider alias mapping. A
  `fast/` selector is stripped to its underlying route and forces the wire
  request tier to `priority`; an ordinary selector preserves the caller's
  service tier. If that preserved tier is `priority`, candidate admission still
  switches to Fast-capable-only, so the native Fast entry cannot reach Ark.
  The operation accepts no prompt, auth or request body. A caller may require
  `custom_tool_call`; providers that only preserve ordinary `function_call`
  items are then removed before traversal, so Code Mode fails closed instead
  of reaching an incompatible fallback. Missing capability metadata is treated
  as function-only, including for catalogs generated before version 0.7.0;
  custom transport support must be declared explicitly after qualification.

`qualify_desktop_patch`
: Checks the public-safe post-build evidence for a patched desktop runtime:
  one versioned patch anchor, every changed file's ASAR integrity, the ASAR
  header digest stored in bundle metadata, code signature, launch and heartbeat
  readback. It diagnoses upgrade drift but never edits or signs an App bundle.

`qualify_snapshot`
: Checks the content-free App/CPA readback: visible and hidden routes, input
  modalities, explicit Fast sibling rows, per-row default tiers, active request
  normalization including effective-priority admission, loopback binding,
  modality-aware affinity, typed route traversal, durable settings revision and
  commit barrier.

`qualify_heartbeat_transport`
: Checks a content-free host observation for one scheduler-injected heartbeat.
  A heartbeat envelope must enter the turn as user input with `role=user`.
  Encoding it as a tool result, especially as an `automation_update` result,
  fails with a stable code and assigns remediation to the Codex App heartbeat
  transport rather than to the LoopX prompt or model provider. This operation
  diagnoses the boundary; it does not patch, restart or modify Codex App.

`qualify_host_control_recovery`
: Checks a content-free observation of CPA recovery for an orphan host control
  output. Only confirmed host names with no `call_id`, no matching model call
  and non-empty semantic output may be retyped as a `role=user` message. The
  qualification also requires proof that the next observed action followed the
  preserved instruction; HTTP success alone cannot pass. Unknown, paired or
  empty outputs must remain a typed `409` failure.

`qualify_quota_recovery`
: Orders a successful account quota reset against the cooldown observation that
  preceded it. A newer reset must invalidate the old cooldown and trigger a
  bounded account probe before fallback is admitted; only a fresh quota-limited
  probe may create a replacement cooldown.

`qualify_stream_recovery`
: Qualifies an observed SSE idle-timeout repair using content-free transport
  and original-session readback. The effective idle deadline must exceed the
  observed silent gap, retries must not increase, small events must be forwarded
  incrementally, and completion must come from the upstream. Both text and a
  tool round trip must finish in the same session and home with history preserved.
  A larger retry count, HTTP 200, a synthetic terminal event or a fresh test
  session cannot qualify recovery. This does not establish why the upstream
  became silent and does not change App configuration. See
  [the recovery procedure](CONTRACT.md#stream-and-session-recovery).

`qualify_outage_recovery`
: Orders the end of a provider-wide incident against the stale native cooldown
  and degraded fallback affinity it created. A recovery signal newer than the
  cooldown source must invalidate the cooldown, run a bounded probe and clear
  or revalidate the fallback binding before a native-capability request is
  admitted. Text-only traffic may remain on the fallback only while the probe
  still reports an outage.

`qualify_tool_transport`
: Checks that a requested tool item type survives provider adaptation and that
  host dispatch completes. In particular, Code Mode `custom_tool_call` must not
  be downgraded to `function_call {"input": ...}`.

`project_runtime_status`
: Joins a stable, route-independent host ChatGPT identity state with a
  content-free CPA execution observation and symbolic A/B quota/activity.
  It validates the actual attempt chain against the compiled route, derives
  remaining quota and reports fallback only when more than one provider was
  attempted. It never accepts account identity, auth-file names or tokens.

`reconcile_integration_candidate`
: Validates one ordered multi-source CPA candidate against its last sync
  receipt. It proves exact base/source heads, required runtime-seam coverage and
  whether reconciliation is needed, then returns inputs for LoopX core
  `integration-branch`. It never fetches, merges, pushes, builds or deploys.

`upgrade_plan`
: Produces a bounded upgrade/rollback checklist from public current and target
  refs plus the changed seams. It never installs, switches or deletes a
  runtime.

## Managed Usage

Run these commands from the same activated Python environment:

```bash
python3 -m pip install packages/loopx-codex-provider-routing
loopx extension install \
  --manifest packages/loopx-codex-provider-routing/extension.toml \
  --execute \
  --format json
loopx extension doctor loopx-codex-provider-routing --execute --format json
loopx extension run loopx-codex-provider-routing \
  --input-json packages/loopx-codex-provider-routing/examples/request.json \
  --execute \
  --format json
loopx extension run loopx-codex-provider-routing \
  --input-json packages/loopx-codex-provider-routing/examples/normalize-request.json \
  --execute \
  --format json
loopx extension run loopx-codex-provider-routing \
  --input-json packages/loopx-codex-provider-routing/examples/heartbeat-transport.json \
  --execute \
  --format json
loopx extension run loopx-codex-provider-routing \
  --input-json packages/loopx-codex-provider-routing/examples/host-control-recovery.json \
  --execute \
  --format json
loopx extension run loopx-codex-provider-routing \
  --input-json packages/loopx-codex-provider-routing/examples/desktop-patch.json \
  --execute \
  --format json
loopx extension run loopx-codex-provider-routing \
  --input-json packages/loopx-codex-provider-routing/examples/quota-recovery.json \
  --execute \
  --format json
loopx extension run loopx-codex-provider-routing \
  --input-json packages/loopx-codex-provider-routing/examples/outage-recovery.json \
  --execute \
  --format json
loopx extension run loopx-codex-provider-routing \
  --input-json packages/loopx-codex-provider-routing/examples/tool-transport.json \
  --execute \
  --format json
loopx extension run loopx-codex-provider-routing \
  --input-json packages/loopx-codex-provider-routing/examples/integration-candidate.json \
  --execute \
  --format json
```

The extension runtime owns install/enable/disable/doctor registration. Package
installation remains an explicit package-manager action; neither operation
downloads CPA or grants credentials.

## Script Migration Boundary

The earlier operator scripts split into three classes:

| Script responsibility | Extension ownership |
| --- | --- |
| Secret-free profile/catalog compiler | Migrated into `compile_catalog` and strengthened with modality/service-tier eligibility |
| Fast selector catalog generation and request-tier normalization | Migrated into `compile_catalog` plus `normalize_selector_request`; the CPA adapter remains the online enforcement point |
| Code Mode tool-shape admission and readback | Migrated into profile `tool_transports`, `normalize_selector_request` and `qualify_tool_transport`; an incompatible fallback fails before first output |
| App/CPA model readback assertions | Migrated as the content-free `qualify_snapshot` contract |
| Patched desktop ASAR/signature/launch checks | Migrated as `qualify_desktop_patch`; the effectful bundle patcher stays operator-owned |
| Quota reset versus cached cooldown reconciliation | Migrated as `qualify_quota_recovery`; live cooldown invalidation and probing stay CPA-owned |
| Provider incident end versus stale native cooldown and degraded fallback affinity | Migrated as `qualify_outage_recovery`; live invalidation, bounded probe and binding revalidation stay CPA-owned |
| Ordered PR/patch candidate inventory and drift detection | Migrated as `reconcile_integration_candidate`; Git composition remains owned by LoopX core `integration-branch` |
| Upgrade matrix, snapshot order and rollback triggers | Migrated as `upgrade_plan`; effect execution remains operator-owned |
| CPA process launcher, OAuth login/reconcile, Ark key loading | Excluded; these are provider runtime and credential lifecycle, not LoopX state |
| Direct App config writes, private snapshots and rollback copies | Excluded from v0; require an explicit permissioned execution envelope before productization |
| Raw log/evidence collection | Excluded; an operator adapter may submit only symbolic, content-free observations to `project_runtime_status` |

The complete responsibility-by-responsibility mapping and credential-free
configuration references are in [`REFERENCES.md`](REFERENCES.md).

The canonical architecture, qualification matrix and public PR lineage are in
[`RUNBOOK.md`](RUNBOOK.md). Reliable SSH egress remains a shared LoopX
integration because it is useful beyond CPA.

## Validation

```bash
python3 packages/loopx-codex-provider-routing/smoke/codex_provider_routing_smoke.py
python3 packages/loopx-codex-provider-routing/smoke/recovery_contracts_smoke.py
python3 -m pytest -q tests/extensions/test_colocated_extension_layout.py
loopx check --scan-path packages/loopx-codex-provider-routing
```

## Optional local operator

Version 0.9 adds a separately invoked `loopx-cpa-operator` CLI for explicit local
configuration, account enrollment, catalog generation, process supervision and
readback. It ships the A/B/C Sol/Astra preset while preserving the managed
extension's read-only protocol and the original A/B qualification default.
See [Local CPA operator](OPERATOR.md) for activation, exact target boundaries,
validation, rollback and disable commands.
