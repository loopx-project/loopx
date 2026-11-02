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
configured connectors. That former installation default also turns ordinary work
already authorized by the owner into delegation, waiting, or manual context transfer.

Removing a prompt sentence is insufficient. The host sandbox, prompt, managed workspace
instructions, persistent configuration, and Session readback must describe the same effective
mode; otherwise the UI can claim an enabled profile while an old upstream thread remains
read-only.

### Contract

`manager_runtime_profile_v0` is the machine-level host-tool policy. The private owner manager
ships with host tools; an explicit machine choice overrides that default. The Codex steward
ships with `gpt-6.1-sol` and `xhigh`; machine and service overrides retain their precedence.
These defaults do not change ordinary Goal Chat models or grant other Agents tools:

- An explicit `restricted` choice keeps scoped LoopX reads and the Codex sandbox is
  `read-only`.
- The default `trusted_owner` lets the Codex manager use normal host filesystem, shell, Git, web, and
  configured-connector tools. Its Codex sandbox is `danger-full-access`.
  Ordinary authorized requests now execute in the current conversation using the relevant
  project's instructions and skills, verifying file edits by readback and durable LoopX
  state changes by their existing typed receipts. A similar registered
  responsibility alone no longer forces an inbox handoff. Explicitly assigned owners,
  established Goal work and tasks needing another Agent's context or execution grant still
  use delegation. An absent worker binding neither grants worker launch nor removes the
  manager's own host grant. Completed local work creates no replacement Goal or Todo.
- `trusted_owner` is not ambient authority. The current request and existing standing grants
  still bound the work. Protected merge, release, deploy, delete, and payment operations retain
  their typed contracts. Provider permission, audience, and durable LoopX state ownership do
  not change.
- Codex and the local Antigravity Language Server support `trusted_owner`. Antigravity uses
  its signed-in host's existing tool permissions and native approvals; LoopX never automatically
  accepts those approvals. A waiting interaction returns an actionable gate. Other endpoints
  fail with an actionable typed error instead of pretending to provide the selected profile.
- Antigravity's `restricted` Chat is text-only: each invocation explicitly disables native tool
  calling and uses the read-only planner mode rather than inheriting a previous invocation's
  tool configuration. This is a provider tool restriction, not a Codex or operating-system
  sandbox. Trusted-owner remains limited to the existing verified owner audience. Governed
  Task execution, workspace filesystem grants and native managed steward identity preparation
  retain their existing Codex owner; selecting Antigravity does not widen those contracts.
  Argon tiers must be offered by the local account and match the requested reasoning effort.
  A missing original cascade fails resume without silently starting a replacement. Stop waits
  for the native cascade to become idle; timeout or partial output is not successful completion.
  Read-only polling transport loss does not resend the input or reset its idle/hard deadline.
  Native retry/error steps remain owned by the running cascade; only a settled terminal error
  fails the adapter, with its structured native cause retained. Host heartbeat and bounded
  Stop-hook recovery use the separate [Antigravity host procedure](../../../skills/loopx-project/references/heartbeat-hosts.md#antigravity-app).
  These source boundaries do not establish upstream network repair or sustained long-run reliability.
- `trusted_owner` applies to the local private owner-manager and a native private steward
  binding freshly verified by the existing Core conversation owner. That owner checks the
  App, owner principal, private source, current resource grant and frozen Session identity.
  The audience proof alone grants no tools: the effective machine policy still applies,
  including an explicit restricted choice.
  Other external audiences, including Lark groups and workspace-only resources, remain
  `restricted`. Project assistants retain their separate workspace grant.

Configuration reuses the existing capability workbench and its
`preview -> apply -> readback` transaction. There is no second configuration source. Missing
configuration uses `trusted_owner` for the private owner; invalid configuration falls back to
`restricted` and projects `configuration_invalid` plus a repair action.

### Session consistency

Each manager Session records the profile, sandbox, standing grant, tool classes, configuration
revision, and status used to start its upstream thread. A change to the effective manager
namespace closes the old upstream thread and starts a new one with visible history, preventing
stale sandbox or prompt state. Changes to unrelated machine capabilities do not rotate a healthy
manager. A healthy legacy Session already equivalent to an explicit restricted policy is backfilled
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
refresh. The private owner installation default is `trusted_owner`. Selecting DSH does not make
that endpoint support the capable profile; its existing actionable refusal remains. Source/RPC
tests do not certify installed live-model work or desktop notification
delivery. Follow [roadmap](loopx-overall-roadmap-v0.md) R2 for those remaining qualifications.

### Acceptance

1. A default private owner installation starts `trusted_owner`, `gpt-6.1-sol` and `xhigh`.
   Explicit `restricted` and invalid-configuration fallback remain read-only; other audiences
   do not inherit the default tools.
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
9. A verified native private steward uses the effective machine policy, preserves its Session
   and history across profile changes/restarts, and rechecks binding/provider identity before
   healthy-host reuse. Other Apps, groups and workspace-only resources do not inherit it.
