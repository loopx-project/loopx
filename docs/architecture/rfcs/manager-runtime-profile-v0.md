# Manager runtime profile v0

- **RFC status:** Accepted
  [capable-manager-semantic-handoff-v0](capable-manager-semantic-handoff-v0.md))
- **Supersedes / closes:** none

> Language note: the
> [Chinese version](./manager-runtime-profile-v0.zh-CN.md)
> and this English version are semantic mirrors. A difference between them is
> a defect.

### Problem

The original LoopX manager was a restricted planning conversation. It could read scoped,
structured Goal context but could not use the host's filesystem, shell, Git, web access, or
configured connectors. That is a safe installation default, but it also turns ordinary work
already authorized by the owner into delegation, waiting, or manual context transfer.

Removing a prompt sentence is insufficient. The host sandbox, prompt, managed workspace
instructions, persistent configuration, and Session readback must describe the same effective
mode; otherwise the UI can claim an enabled profile while an old upstream thread remains
read-only.

### Contract

`manager_runtime_profile_v0` is an explicit, persistent machine-level grant:

- `restricted` is the default. The manager keeps scoped LoopX reads and the Codex sandbox is
  `read-only`.
- `trusted_owner` lets the Codex manager use normal host filesystem, shell, Git, web, and
  configured-connector tools. Its Codex sandbox is `danger-full-access`.
  Ordinary authorized requests now execute in the current conversation using the relevant
  project's instructions and skills, with actual result verification. A similar registered
  responsibility alone no longer forces an inbox handoff. Explicitly assigned owners,
  established Goal work and tasks needing another Agent's context or execution grant still
  use delegation. An absent worker binding neither grants worker launch nor removes the
  manager's own host grant. Completed local work creates no replacement Goal or Todo.
- `trusted_owner` is not ambient authority. The current request and existing standing grants
  still bound the work. Protected merge, release, deploy, delete, and payment operations retain
  their typed contracts. Provider permission, audience, and durable LoopX state ownership do
  not change.
- Codex is currently the only endpoint that enforces `trusted_owner`. Other endpoints fail with
  an actionable typed error instead of pretending to provide the selected profile.
- `trusted_owner` applies to the local private owner-manager and a native private steward
  binding freshly verified by the existing Core conversation owner. That owner checks the
  App, owner principal, private source, current resource grant and frozen Session identity.
  The audience proof alone grants no tools: the same persistent machine grant is also required.
  Other external audiences, including Lark groups and workspace-only resources, remain
  `restricted`. Project assistants retain their separate workspace grant.

Configuration reuses the existing capability workbench and its
`preview -> apply -> readback` transaction. There is no second configuration source. Missing
configuration defaults to `restricted`; invalid configuration also falls back safely and
projects `configuration_invalid` plus a repair action.

### Session consistency

Each manager Session records the profile, sandbox, standing grant, tool classes, configuration
revision, and status used to start its upstream thread. A change to the effective manager
namespace closes the old upstream thread and starts a new one with visible history, preventing
stale sandbox or prompt state. Changes to unrelated machine capabilities do not rotate a healthy
manager. A healthy legacy Session already equivalent to the restricted default is backfilled
without an unnecessary restart.

Dashboard shows both machine configuration and current Session readback. CLI/managed Turn,
Dashboard, and Lark all use the same manager runtime controller. Lark remains an entry point and
projection of that Session; it does not own a separate profile or permission state. Native
owner-private bindings reuse Core's fresh audience/resource verification before starting or
reusing a host; no channel-name inference, persisted proof or manager-specific ACL replaces it.
A revoked binding or changed App/owner stops admission.

This slice implements only the private-owner M1 journey in
[capable-manager-semantic-handoff-v0](capable-manager-semantic-handoff-v0.md), targeting A1–A3/A12.
It does not implement the M2 collaboration request, the M3 outbox, or treat manager Session fields
as work, request, or delivery authority.

### Implementation and successor

`43d362532` introduced the machine profile. Native private steward bindings now carry a fresh
Core owner-audience proof into that existing profile at open, resume and authorization-scope
refresh. The installation default stays `restricted`; selecting DSH does not inherit the capable
profile. Source/RPC tests do not certify installed live-model work or desktop notification
delivery. Follow [roadmap](loopx-overall-roadmap-v0.md) R2 for those remaining qualifications.

### Acceptance

1. A default installation starts `restricted` with no implicit grant.
2. Machine configuration preview/apply/readback persists `trusted_owner`.
3. A new Codex manager thread sends `danger-full-access` to app-server, and its Turn prompt and
   managed `AGENTS.md` no longer contain the read-only restriction.
4. A profile change rotates the upstream thread while preserving the LoopX Session and visible
   history.
5. An unrelated machine-configuration change does not rotate the thread.
6. A non-Codex endpoint fails closed for `trusted_owner` and recommends selecting Codex or
   restoring `restricted`.
7. Desktop and mobile Dashboard show the effective profile; invalid configuration shows its
   fallback state.
8. An external audience without an existing scoped grant remains `read-only` and exposes the
   effective downgrade accurately.
9. A verified native private steward uses the explicit machine grant, preserves its Session
   and history across profile changes/restarts, and rechecks binding/provider identity before
   healthy-host reuse. Other Apps, groups and workspace-only resources do not inherit it.
