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
  `read-only`. Claude Code disables built-in tools and offers only the same scoped Core
  readers through an invocation-scoped MCP bridge. This is a tool boundary, not an OS sandbox.
- `trusted_owner` lets the Codex or Claude Code manager use normal host filesystem, shell,
  Git, web, and configured-connector tools. Its Codex sandbox is `danger-full-access`;
  Claude Code uses its native `bypassPermissions` mode under the same existing owner grant.
- `trusted_owner` is not ambient authority. The current request and existing standing grants
  still bound the work. Protected merge, release, deploy, delete, and payment operations retain
  their typed contracts. Provider permission, audience, and durable LoopX state ownership do
  not change.
- Codex and Claude Code enforce `trusted_owner`. Other endpoints fail with
  an actionable typed error instead of pretending to provide the selected profile.
- `trusted_owner` currently applies only to the private owner-manager conversation. An external
  audience, including a Lark group, is a separate trust boundary and resolves the same machine
  choice to `restricted` until an existing audience/resource grant can be verified.

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
projection of that Session; it does not own a separate profile or permission state, but an external
audience currently degrades visibly to `restricted`. Future Lark host-tool access must reuse an
existing audience/resource authority instead of adding a manager-specific ACL here.

This slice implements only the private-owner M1 journey in
[capable-manager-semantic-handoff-v0](capable-manager-semantic-handoff-v0.md), targeting A1–A3/A12.
It does not implement the M2 collaboration request, the M3 outbox, or treat manager Session fields
as work, request, or delivery authority.

### Implementation and successor (2026-09-16)

`43d362532` contains the original machine profile, controller integration and focused tests;
that checkpoint qualified only Codex. Claude Code now reuses the existing scoped Core reader,
per-Turn audience check, machine grant and Session allocation. MCP read-only annotations allow
the reader in native plan mode; tokens and bridge addresses are transient. Its vendor model
default is the native `sonnet` alias, while explicit machine or environment models retain
priority. This transport does not establish deployment or full M1 qualification. The
`restricted` default and external-audience downgrade remain. Selecting DSH does not inherit
the capable profile. Follow [roadmap](loopx-overall-roadmap-v0.md) R2 for actual
tool/session/continued-execution and settings readback, without a second machine configuration.

### Acceptance

1. A default installation starts `restricted` with no implicit grant.
2. Machine configuration preview/apply/readback persists `trusted_owner`.
3. A new Codex manager thread sends `danger-full-access` to app-server, and its Turn prompt and
   managed `AGENTS.md` no longer contain the read-only restriction.
4. A profile change rotates the upstream thread while preserving the LoopX Session and visible
   history.
5. An unrelated machine-configuration change does not rotate the thread.
6. An endpoint other than Codex or Claude Code fails closed for `trusted_owner` and recommends
   selecting a supported endpoint or restoring `restricted`. Native restricted reads reject
   another Goal, revoked audience access, and a preceding Turn's private reader.
7. Desktop and mobile Dashboard show the effective profile; invalid configuration shows its
   fallback state.
8. An external audience without an existing scoped grant remains `read-only` and exposes the
   effective downgrade accurately.

### Model discovery and native cancellation

The Chat model picker reads the selected host through `GET /api/chat/models`.
Codex discovery uses its configured CLI and captured home for paginated
`model/list`, without creating a model Turn. Native Claude discovery projects
only the host's declared model picker metadata. Catalog availability is not
proof of inference or model access; empty and unavailable catalogs retain an
explicit model input, rather than falling back to another host's suggestions.
Legacy Codex catalog compatibility remains visible in the response and UI.
Native Chat interruption terminates the invocation's owned process group,
including wrapper children. It does not target unrelated host sessions.
