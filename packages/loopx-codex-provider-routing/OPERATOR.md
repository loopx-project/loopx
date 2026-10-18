# Local CPA operator

Legacy opt-in compatibility. New native account installations use
[Native accounts](NATIVE.md); this operator is never selected automatically.

The package now includes `loopx-cpa-operator`, a separately invoked local CLI.
The managed extension protocol remains read-only and permission-free. Installing
or running that protocol does not enable the operator, discover credentials,
start CPA, change an App, or write a task store.

## Routing preset

The operator's `abc-sol-astra` preset uses the existing `compile_catalog` ring
contract. Its credentials, App catalog, validation and observations share the
same selector definitions.

| Visible selector | Candidate order | Terminal behavior |
| --- | --- | --- |
| `auto/MODEL` | A → B → C | Report exhaustion; no model substitution |
| `fast/auto/MODEL` | A → B → C | Request native Fast; no model substitution |
| `auto-with-ds/MODEL` | A → B → C → DeepSeek | Explicit Standard text fallback |
| `gpt-5.6-luna` | A → B → C | Report exhaustion; no model substitution |

Sol and Astra each have two Standard Auto options and a three-account Fast Auto
option. Luna remains visible as a three-account route: seven picker rows total.
The catalog retains 18 hidden compatibility rows (bare Sol/Astra model ids,
Prefer A/B/C and their Fast variants, and manual Ark identifiers), so existing
tasks keep their metadata and route ids.
Legacy Standard, bare Sol/Astra and Prefer aliases now stay within A/B/C.
Auto OAuth aliases use `fork: true` to retain the original model ids in CPA;
a self-alias alone does not retain them. Only
`auto-with-ds/` aliases admit Ark. Existing tasks are not silently opted into it.
The four-route picker does not advertise Fast; the three-account picker retains
native speed tiers. Image-bearing history excludes the text-only fallback.

A preferred legacy account remains an entrypoint, not an exclusive pin. Auto
keeps an eligible subscription affinity for session stability; a recovered
higher-priority subscription does not forcibly steal an eligible binding.
Removing Ark aliases from the three-account routes prevents an old degraded
binding from keeping those routes on DeepSeek.

The four-route operator requires a CPA build with
[custom-tool namespace recovery](https://github.com/router-for-me/CLIProxyAPI/pull/5558). Chat Completions encodes custom tools as functions
with an `input` string. Upstream adoption is tracked in
[the maintainer issue](https://github.com/router-for-me/CLIProxyAPI/issues/5560)
because the repository restricts direct translator PRs. CPA must restore `custom_tool_call`, the original input,
namespace and call id in streamed and non-streamed responses. Historical custom
calls must replay under the same qualified name as the current declaration.
An omitted namespace can be recovered only from a unique current declaration;
exact names take precedence and ambiguous names must never be guessed.
This fixes transport, not model behavior: DeepSeek can still choose poorer code
or miss Code Mode's output instructions. Keep the three-subscription route as
the default for coding tasks. Explicit Ark access remains available by id.

CPA owns online retry and cooling. The operator retains 10 CPA retries and up
to a 65-second advised wait. App retries are separate; a single ring traversal
is not a bound on total turn time. Do not disable cooling globally to recover
one account: it would repeatedly hit known exhausted subscriptions.

If an upstream account recovers earlier than its previously reported reset,
CPA may still cache the old cooldown. After confirming recovery, permit a new
attempt with `reset-cooldown --slot b --execute` (see the runnable command
below). This uses CPA's targeted management API, does not rotate OAuth tokens,
restart other accounts, modify task stores, or claim that quota was replenished.
It refuses disabled slots. Verify with a bounded live request and actual account
selection; a successful cache reset alone is not a health check. This explicit
operator action does not install a background polling loop.

Fast rows set a picker default and the checksum-pinned request plugin forces
`service_tier=priority` before alias mapping. Verify the provider-bound request,
not just the App's thread default: a custom-provider App may report `default`.
The upstream response may also report `default`; a priority request is not a
promise that the upstream supplied an accelerated tier.

## Configuration and activation

Install in a dedicated Python 3.11+ environment:

```sh
python3 -m pip install packages/loopx-codex-provider-routing
loopx-cpa-operator --config /absolute/private/operator.json validate
loopx-cpa-operator --config /absolute/private/operator.json --execute validate
```

The first command produces a credential-free plan and performs no writes or
network/process operations. `--execute` authorizes only that invocation. Supply
a mode-0600 JSON file with `schema_version: "loopx_cpa_local_operator_v1"` and:

- `paths`: explicit absolute references for `runtime_root`, `temporary_root`,
  `binary`, `plugin_directory`, `codex_binary`, `gpt_cache`, `astra_cache`,
  `ark_catalog`, `ark_profile_catalog`, `ark_env_file`; optionally `login_source`;
- `binary_sha256`, `plugin_sha256`, `source_commit`: exact reviewed artifact pins;
- `port`: an unprivileged local TCP port; `launchd_label`: the owned service id;
- `ark_base_url`: a credential-free HTTPS endpoint;
- `ark_model`, `ark_pro_model`: upstream model identifiers.

Keep that file outside Git. Supply references, never inline keys or tokens.
Writable roots must be dedicated directories outside Git worktrees. Slot files
are basenames under the configured auth directory; traversal, duplicates and
symlink targets are rejected. Every receipt stays symbolic and credential-free.
The API key is read only by the local operator and placed in a private temporary
runtime config. Credentials, caches, binaries, plugin artifacts, logs, snapshots
and service-manager files are never package resources or LoopX state.

The App cache for each model is its metadata source; Astra does not inherit
Sol's prompt, context window or model capabilities. Auto retains the existing
low-through-max effort policy, while preferred routes preserve native levels.
CPA must refresh its model definitions: an old `-local-model` catalog can omit a
new model even when the App picker advertises it. The operator enables CPA's
model-definition refresh while keeping the executable/plugin hashes pinned.
Model-definition refresh and binary upgrade are separate operations.

## Reuse outside this preset

The package's reusable routing boundary is `compile_catalog(source)` and the
managed [routing contract](CONTRACT.md): callers supply symbolic profiles,
rings, routes and capability declarations. The A/B/C and Sol/Astra names in
this operator are a concrete preset, not requirements of that contract.
Changing a contract does not install a runtime or grant credential access.

Tool identity recovery belongs to CPA's Responses translator and depends only
on the current request's tool declarations, not account slots, model names or
a particular compatibility provider. Early cooldown recovery uses CPA's
existing management API; this operator resolves the configured symbolic slot
locally and emits a credential-free receipt. Credential acquisition, service
supervision and live availability verification remain explicit host actions.

## Commands

```sh
# Import the explicitly configured current login into a vacant symbolic slot.
loopx-cpa-operator --config /absolute/private/operator.json enroll --slot c
loopx-cpa-operator --config /absolute/private/operator.json --execute enroll --slot c

# Validate all three identities before updating any routing metadata.
loopx-cpa-operator --config /absolute/private/operator.json --execute reconcile
loopx-cpa-operator --config /absolute/private/operator.json --execute write-catalog

# Process supervision: configure the owned service manager to invoke this.
loopx-cpa-operator --config /absolute/private/operator.json --execute serve

# Content-free live and isolated App Server readback.
loopx-cpa-operator --config /absolute/private/operator.json --execute status
loopx-cpa-operator --config /absolute/private/operator.json --execute validate
loopx-cpa-operator --config /absolute/private/operator.json --execute probe
loopx-cpa-operator --config /absolute/private/operator.json --execute route-status
```

Enrollment checks account identity, token expiry, vacant slots and duplicate
accounts. It leaves the source login untouched. CPA owns subsequent refreshes.
Use a separately authorized OAuth login if another client concurrently rotates
that login and causes refresh conflicts. Never copy task databases or rollouts.

`serve` replaces its process with the pinned CPA executable; the service manager
owns restart supervision. `start`/`stop` are for an unmanaged process. `stop`
refuses to signal a launchd-managed or unrelated process. For launchd, unload the
specific configured service before changing its program, then load it again.
The operator does not modify LaunchAgents, App bundles or App processes itself.
Restart only the affected App after changing its model catalog, and verify the
seven-visible/18-hidden row readback. Model registration is not proof of account entitlement or a
successful model call; perform a bounded live request separately.

## Early quota recovery

```sh
# Plan first; the executed command clears only the named slot's CPA cooldown.
loopx-cpa-operator --config /absolute/private/operator.json reset-cooldown --slot b
loopx-cpa-operator --config /absolute/private/operator.json --execute reset-cooldown --slot b
loopx-cpa-operator --config /absolute/private/operator.json --execute route-status
```

Run a bounded model request after the reset, then inspect route-status again.
Its success must identify the recovered subscription; another account succeeding
is not evidence for the named slot. A fresh limit response reinstates cooling.

## Rollback and disable

`reconcile`, `write-catalog` and `enroll` emit a `rollback_snapshot` identifier.
Snapshots stay in the configured private state directory. To restore one:

```sh
loopx-cpa-operator --config /absolute/private/operator.json rollback --snapshot-id SNAPSHOT_ID
loopx-cpa-operator --config /absolute/private/operator.json --execute rollback --snapshot-id SNAPSHOT_ID
```

Integrity and target checks precede writes. Restore routing metadata onto the
latest credential instead of restoring stale OAuth refresh tokens. Credentials
newly enrolled after the snapshot are retained but disabled during rollback.
Restart the owned service/App when restoring process configuration or a catalog.
The operator never deletes a credential or changes a task store.

To disable, unload the operator-owned service or stop its unmanaged process.
Restore the previous service program and private config from the installation
backup, or uninstall the dedicated environment. Keep auth/state for recovery.
Uninstalling the managed read-only extension does not stop an independently
installed operator service.

## Verification

```sh
python3 packages/loopx-codex-provider-routing/smoke/operator_smoke.py
python3 packages/loopx-codex-provider-routing/smoke/codex_provider_routing_smoke.py
```

Offline tests use only synthetic credentials and temporary directories. They
cover default-off isolation, slot/target boundaries, Sol/Astra parity, ring
order, rollback integrity and token rotation, and legacy A/B qualification.
`qualify_snapshot` accepts `routing_preset: "abc-sol-astra"`; omitting it keeps
the original A/B/Sol contract for existing consumers.
