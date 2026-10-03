# Goal-scoped capability improvement (M1 backend preview)

Language: [中文](README.zh-CN.md).

This capability owns **improvement intent**, not capability enablement. It adds
bounded advisory guidance to the existing coordinator `before_plan` entry.
The quota path discloses a pending replan through that same entry. There is no
new phase, scheduler, memory/adoption store, installer or execution driver.
Applicable enabled capabilities remain directly usable without membership in a
portfolio and without opting into this policy.

## Original owner and configuration

The existing Goal registry owns `control_plane.capability_improvement`.
`configure-goal` and the Goal settings preview/apply/CAS API share the same
writer, configuration catalog, source revision and readback. Python adapts IO;
normalization, bounds and advisory decisions have one TypeScript owner in
`loopx/control_plane/capabilities/goal_capability_organization.ts`, inside the
packaged runtime fingerprint boundary. No machine-level override is introduced.

```sh
loopx configure-goal --goal-id example --capability-improvement-mode bounded \
  --capability-discovery-budget-minutes 5 --capability-max-trials 1
# Inspect preview, then explicitly repeat with --execute to apply.
loopx capability inspect --goal-id example --format json
loopx configure-goal --goal-id example --clear-capability-improvement-configuration
# Explicit --execute clears the override and restores default off.
```

Absent configuration means off. `mode` is `off` or `bounded`;
`discovery_budget_minutes` is 1–30 (default 5); `max_trials` is 0–2 (default 1).
These bound the proposed discovery work, not a reservation or proof of quota
consumption. Existing Todo, lease, quota, capability/provider readiness and
protected-effect admission still apply. Changing this intent enables no other
capability. Invalid authoring fails before persistence; a corrupt stored policy
is visible as invalid, fails open in advice, and can be explicitly cleared.

## Progressive disclosure

An ordinary wake with no explicit Goal gap returns `no_goal_gap` and recommends
continuing current work. It does not scan a catalog, query a provider or create
work. A replan tag discloses context; it is not itself proof of a capability gap.
An explicit cold-path read can pass a public-safe stable gap reference and at
most eight candidate observations from their original owners:

```sh
loopx agent-context --goal-id example --agent-id coordinator --phase before_plan \
  --capability-planning-trigger replan --capability-gap-ref example/gap \
  --capability-candidate-json '{"capability_id":"example-source","applicable":true,"enabled":false,"configuration_ref":"owner/config-v1","effect_ref":"experiment/baseline","rollback_ref":"owner/rollback"}'
```

The policy recommends inspecting an applicable already-enabled capability first.
If none exists, a trial proposal requires explicit applicability, disabled state,
configuration-owner reference, effect-baseline reference and rollback reference.
Missing/unknown observations or zero trial budget recommend continuing current
work. No candidate list permits bounded discovery within the configured budget.
References and caller observations are **not authenticated execution grants**;
re-read the original owners before acting. Advice never authorizes a provider
call, installation, configuration mutation, order, signing or transfer.

Retain the use/result/effect and retirement decision through existing Todo,
outcome and capability owners. A useful result must improve a declared outcome
or cost against its baseline; invocation, a PR or a successful test is not utility.
Unknown effect remains unknown. Roll back via the original capability owner,
without deleting historical evidence. Empty, invalid or over-budget advice stays
isolated and must not block useful work or weaken existing admission.

## Delivery and qualification boundary

CLI configuration, readback, shared Goal settings API and live quota injection
are implemented. The existing schema-driven App editor receives the new fields;
**packaged App interaction/localization and Lark control/guidance are companion
work, so this is partial product delivery**. The same projection must be reused,
not copied into a new UI/chat source. No real-domain utility, default adoption,
autonomous evolution or complete portfolio milestone is claimed.

This is the improvement-intent M1 backend slice, not the similarly numbered
external-evidence/connector milestone in the
[portfolio RFC](../../../docs/architecture/rfcs/goal-scoped-capability-portfolio-v0.md).
It supports that RFC's direct-first path without a new composition DSL.
Validate deterministic policy and real CLI/configuration behavior with:

```sh
node --experimental-strip-types --test tests/control_plane_ts/goal_capability_organization.test.ts
LOOPX_USAGE_PING=0 uv run --extra test python -m pytest tests/capabilities/test_capability_improvement.py -q
```
