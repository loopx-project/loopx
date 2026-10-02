# Choose the right extension point

"Extend LoopX" does not automatically mean "create an Extension." Decide the caller outcome, product
contract, and lifecycle first. The implementation may belong in a Capability, Provider, Extension, or a
project-internal helper.

## Start from a bad ending

A team wants to add a second market-data source to LoopX, so it creates the package
`loopx-market-connector`, writes a manifest, declares one `[[provides]]`, and `loopx capability list`
immediately gains a `market-connector` row.

Three weeks later that row reads:

```text
market-connector: declared=true, installed=true, enabled=true, ready=true
                  callers=0, resolver=none, domain policy=none
```

It is discoverable, routable, and passes doctor, yet no caller consumes its result, no resolver
normalizes its output, and no domain policy governs its transitions. The control plane now carries a
contract with no real caller: every future Provider has to consider "should this also register as a
capability," and the one person who could answer has moved to another project.

The second week planted a second version. Someone noticed three internal modules repeating the same
state normalization, so the code became `loopx/capabilities/state-normalization/` on the grounds that
"several files are involved." Half a year on, that capability has one internal caller and still demands
manifest, doctor, version compatibility, and catalog registration upkeep.

Both endings share one cause: **the placement decision was made wrongly before any code existed, and
each mistake let an abstraction grow wider than any real caller needed.** One produced a public contract
with no caller; the other produced installation and lifecycle cost with no external consumer.

## Why "add the caller later" is not the fix

The natural reaction is to register first and wire it up later. Doing it in that order leaves concrete
damage on the control plane:

- **The catalog is an authoritative read model.** A declaration enters `loopx capability list`, where
  dashboards, agents, and review read it. A reader cannot tell a shipped contract from a placeholder
  name, so they either write calls against it or gradually stop trusting the catalog;
- **Registration decides routing.** Once a row exists it becomes a candidate when LoopX decides who
  should handle that result, and no domain policy exists to settle its transitions;
- **Installation cost starts immediately.** A capability means a version-compatibility window to
  maintain from day one, while the benefit it buys — being used by a caller — does not exist yet.

The opposite error is just as common: registering a helper that serves only the current project as a
capability in the hope that someone will reuse it. Whether anyone will is unverifiable, while the
installation cost is due now.

A placement decision exists to surface both errors before implementation. What it produces is a
rationale, not more design documentation.

## Capability and Extension are two dimensions

A **Capability** defines the outcome a caller can request and the contract that outcome must satisfy.

An **Extension** defines how an implementation is packaged and managed: install, enable, disable,
upgrade, rollback, and compatibility.

In the real repository each dimension has its own home:

```text
loopx/capabilities/<capability>/   caller-facing contracts and core providers
loopx/extensions/                  extension lifecycle and bundled providers
packages/<package-id>/             independently installable distributions
```

`loopx/extensions/` ships inside the LoopX wheel, while `packages/` children carry their own packaging
metadata and do not enter the wheel. There is deliberately no repository-root `extensions/` directory,
because that duplicate name obscured whether a path was importable LoopX code or a separately
installable artifact.

The two dimensions do not merge; they compose through the capability/provider registry:

```text
Capability: caller-facing outcome contract
      ^
      | implemented by
Provider
      ^
      | delivered by
Extension: package and lifecycle
```

Every registered capability declares three provider-facing fields: `origin` (`builtin` or `extension`),
`visibility` (`public` or `internal`), and `provider_id` (`loopx-core` or the extension manifest id).
Duplicate capability or provider ids fail closed.

An Extension can therefore:

- provide a new Capability;
- implement an existing Capability;
- expose only its own bounded standalone command.

A Capability can also use a built-in Provider shipped by LoopX core, without any Extension.

## Case: the Finance value-discovery Extension

The current `loopx-finance-value-discovery` package is a useful naming trap. It processes Finance
research packets, but its manifest declares neither `[[provides]]` nor `[[implements]]`, and it does not
register `finance-value-discovery` in the Capability catalog.

It does declare `[[presentation_surfaces]]` (`investment-research`, `visibility = "public-safe"`), which
belongs to the Extension's own presentation contract. A presentation surface is unrelated to a
capability: drawing the result as a dashboard does not create a routable domain outcome.

The official placement guide keeps Finance value discovery as a standalone extension: public-market,
filing, and news collection can stay inside that extension until a real cross-provider LoopX contract
exists, and the value connector protocol explicitly rules out inventing a `finance-value-discovery`
Capability. The analysis below follows the current manifest, catalog readback, and managed runtime.

Its placement rationale is:

```text
capability_id: none
provider_id: loopx-finance-value-discovery
origin: extension
placement: separately activated package
reason: deterministic reducer over caller-supplied frozen public-safe evidence;
        independent package and lifecycle; no provider-neutral caller contract yet
```

It is not currently a Capability because:

- the public call contract belongs to the Extension protocol `finance_value_discovery_extension_v0`;
- input is a frozen `finance_value_discovery_input_v0` supplied by the caller, not a broad request such
  as "find an investment";
- the Provider emits one bounded research packet;
- no set of interchangeable Providers shares a caller outcome, resolver, and domain policy;
- the current Capability catalog makes no `finance-value-discovery` promise.

It fits the Extension dimension because:

- package, version, doctor, enablement, and upgrade have an independent lifecycle;
- manifest and runtime permissions are empty;
- the reducer does not fetch market data, read accounts or portfolios, submit trades, or start
  continuous monitoring;
- the same frozen public-safe evidence produces a deterministic result;
- generic `extension run` crosses no external-effect authority boundary.

The current boundary is:

```text
public evidence collector or human review
  -> frozen finance_value_discovery_input_v0
  -> loopx-finance-value-discovery Extension
  -> bounded finance_value_discovery_packet_v0
  -> human / Goal decides whether a successor is justified
```

Package installation, Extension activation, and invocation prove different facts:

```bash
# Put the Provider entrypoint in the current Python environment
python3 -m pip install ./packages/loopx-finance-value-discovery

# Record and activate a doctor-validated manifest revision
loopx extension install \
  --manifest packages/loopx-finance-value-discovery/extension.toml \
  --execute \
  --format json

# Reduce one frozen public-evidence input through managed runtime
loopx extension run loopx-finance-value-discovery \
  --input-json packages/loopx-finance-value-discovery/examples/paypal-debeta-discovery.json \
  --execute \
  --format json
```

These commands require the provider source package. The Extension is not bundled, and LoopX does not
download the package for the user. `extension list` proves activation state, an executed doctor proves
readiness for the current revision, and the example run proves the request/response contract. None
substitutes for another.

Install the package into the same Python environment that runs `loopx`, with
`loopx-finance-value-discovery` visible on the current `PATH`. Calling the absolute path of `loopx`
inside a virtual environment without making that environment's Provider entrypoint resolvable causes
doctor to return `entrypoint_missing`. That is the correct fail-closed result.

### When it should become a Capability plus Provider

If LoopX later needs a stable provider-neutral result across several Finance data or research Providers,
define the Capability contract first: common input, evidence freshness, authority, failure, readback,
and successor policy. This package could then declare `[[implements]]` and become one Extension
Provider.

Do not register a speculative Capability merely because the package name contains "value discovery."
Collection must not leak into this zero-permission reducer either. Public-market, filing, and news
collection needs its own Provider boundary plus freshness, licensing, and credential Gates.

## Four candidate locations

### 1. Project-internal helper

If code serves only the current project and has no independent caller contract, installation need, or
lifecycle, keep it in the nearest owning module.

Suppose the current project needs to convert two internal states into one dict, with no external caller
and no independent version-compatibility requirement. Turning it into a Capability or Extension would
add manifest, doctor, and upgrade cost. Shared code may justify a helper; it does not prove a need for
independent installation and lifecycle.

### 2. Provider for an existing Capability

If an existing Capability already defines the caller outcome, implement that contract rather than
creating a synonym.

The Provider can be:

- built-in: shipped with LoopX core;
- extension-delivered: supplied by an independent Extension.

Accessing an external system is not the only test for an Extension. A connector that shares the core
release lifecycle and is always maintained by core can still be a built-in Provider.

### 3. New Capability

Create a Capability only when LoopX callers need a stable provider-neutral outcome, catalog identity,
and routing surface.

At minimum, it needs:

- a clear caller outcome;
- stable id and versioned protocol;
- a real entrypoint or call site;
- domain validation and transition policy;
- focused validation;
- catalog registration.

"We may have multiple Providers later" is not enough to ship a speculative abstraction.

### 4. Standalone Extension

A standalone Extension is a good starting point when the ability:

- has its own package and version;
- needs independent install, activation, upgrade, or rollback;
- exposes one bounded request/response command;
- does not belong to an existing Capability;
- requires no permissions for direct invocation.

The `loopx-text-stats` example fits this shape. It computes statistics from text in the request. It does
not read files, use the network, modify external systems, or define a cross-Provider product contract.

## Make the placement decision in order

The official guide requires five questions in order; this book expands them to six. The order matters:
settle the outcome, then the owner, and only then the delivery shape.

1. **What outcome does the user need?** Name the result, not a mechanism such as connector, adapter, or sink.

2. **Can the nearest existing owner provide it?** Extend an existing Capability when it owns the same outcome.

3. **Must LoopX core always ship this implementation?** If yes, consider built-in; otherwise consider an Extension.

4. **Does it need an independent lifecycle?** Independent dependencies, versions, credentials, or provider ownership point toward an Extension.

5. **Is it only an internal helper?** No independent caller contract means it should remain local.

6. **Does it perform an effect?** A generic standalone runner must not bypass Capability or domain authority.

### Where platform-level machinery belongs

One further branch confuses people. If the addition is only the registration and lifecycle machinery
shared by every Extension, it belongs in `loopx/extensions/`, not in any provider package. It is not a
capability and needs no manifest of its own. Putting that machinery inside one provider forces the next
provider to depend on the first one's private structure.

## Record the minimum rationale

Write a short record before implementing:

```text
capability_id: none
provider_id: loopx-text-stats
origin: extension
placement: standalone package
reason: bounded deterministic command with an independent lifecycle;
        no provider-neutral LoopX capability is needed
```

For an Extension implementing an existing Capability:

```text
capability_id: <existing-capability>
provider_id: <extension-id>
origin: extension
placement: independently packaged provider
reason: reuses the caller contract but needs independent dependencies
        and activation lifecycle
```

This rationale can live in a Todo, PR description, or commit history. Its job is to expose a misplaced
abstraction before implementation.

## Cost and boundary

A placement decision is not free. It buys alignment between the abstraction boundary and real caller
needs, and the costs deserve stating.

**Cost one: the delivery shape is hard to change later.** Once a package has its own version and
installation step, collapsing it back into core means handling the upgrade path of existing users.

**Cost two: Extension users must act explicitly.** An independent lifecycle means the user installs,
enables, and runs doctor themselves. A core built-in works as soon as LoopX is installed. A manifest
declared into a catalog read reports `declared=true, installed=false, enabled=false, ready=false`; it
does not become usable merely by existing.

**Cost three: a public contract needs maintenance.** A new Capability needs a stable caller outcome, a real entrypoint, and validation. One Provider can satisfy these conditions. Independent installation alone is insufficient, and provider count is not a gate.

**Boundary one: this decision governs ownership inside the LoopX product surface, not someone else's
repository layout.** An independently distributed provider lives in its own package or repository;
LoopX only constrains its manifest and lifecycle contract.

**Boundary two: `value-connectors` is a compatibility surface.** It is an existing CLI and protocol
surface and should not be the public capability owner for new work. Migrate each profile to an outcome
capability such as `issue-fix` or `content-ops`, or to a standalone extension such as
`loopx-finance-value-discovery`, before retiring it.

**Boundary three: this decision does not judge whether the work is worth doing.** It answers "where does
it go." Whether an outcome has value is answered by a Goal and its acceptance.

## Common placement mistakes

### "It calls an external API, so create a connector Capability"

Transport is not the caller outcome. Find who needs the result and whether an existing Capability already
owns it.

### "Declare `[[provides]]` now and add the caller later"

A discoverable but uncallable manifest creates a false product surface. Establish the real caller contract,
resolver, policy, and validation first.

### "The standalone runner starts a process, so it can send messages"

The generic runner requires both manifest and runtime permissions to be empty. Sending, writing, publishing,
or managing resources is an effect and must go through a Capability or domain command.

### "Several files share code, so make an Extension"

Shared code may justify a helper. It does not prove a need for independent installation and lifecycle.
Abstraction follows the change reason, not the file count.

## Placement used by this book

| Field | Decision |
| --- | --- |
| `capability_id` | `none` |
| `provider_id` | `loopx-text-stats` |
| origin | `extension` |
| kind | standalone |
| permissions | `[]` |
| managed entrypoint | `loopx extension run` |

The executable evidence for this decision is `examples/capability-extension-placement-doc-smoke.py`,
`tests/capabilities/test_capability_extension_registry.py`, and
`tests/capabilities/test_capability_extension_registry.py::test_duplicate_capability_fails_closed`. The
first checks the placement document against directory ownership. The second checks built-in catalog
origin and that an extension-declared Capability composes into the registry as `declared=true,
installed=false`. The third checks that the registry fails closed when one id is declared twice. (The
`examples/capability-extension-registry-smoke.py` script is currently stale on main — its hardcoded
built-in capability list no longer matches — so do not cite it as a working evidence entrypoint.)

If you are designing more than a standalone package — such as Explore, Domain State, a Capability Pack,
multi-agent preset, Provider, or presentation composition — continue to
[Control-Plane Course Lesson 11](/loopx/docs/development/control-plane-course/11-extension-layer/). It
explains how these extension surfaces reuse the Kernel instead of creating a second Goal, Todo, quota, or
scheduler.

## Invariants

Six claims you can check yourself.

1. **A capability exists for its caller outcome, not for its directory name.** With no caller, resolver,
   and domain policy to point at, it does not belong in the catalog.
2. **Capability and extension are two dimensions, and a change may touch only one.** Inferring a new
   capability from a new package is the most common placement error.
3. **Shared code is not grounds for a capability.** The test is an independent caller contract and
   lifecycle, not the file count.
4. **A discoverable manifest does not make a contract callable.** Write `[[provides]]` or
   `[[implements]]` only once the real entrypoint, resolver, and validation exist.
5. **An Extension's independent lifecycle is a user-visible cost.** Before declaring one, confirm the
   cost buys a version or credential boundary some caller actually needs.
6. **An effect must not reach around authority through the standalone runner.** Any ability to write
   files, send messages, publish, or manage resources goes through a Capability or domain command.

The next chapter creates this structure from the official scaffold and changes only the domain contract.
