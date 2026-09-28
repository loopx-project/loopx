# T2 Agent-addressed read checkpoint

Moved without content change from [typescript-control-plane-migration-v0.md](../../typescript-control-plane-migration-v0.md) (former section "T2 Agent-addressed read checkpoint") on 2026-09-26; RFC baseline `3e443ad7c`. Checkpoint records live in this ledger, not in the RFC body.

Todo list selection now composes with the existing typed summary-lanes batch.
The Python role/status/id/Agent predicates and independent User scope rule are
removed; legacy and promoted consumers share `todos/agent_scope.ts` with quota
and decision scope. Explicit gate scope retains precedence over execution claim,
while retained User claims now correctly restrict scoped list visibility.
Full-source resume/succession stays evaluated before selection; original array
ordinals survive filters and display limits. No extra selection runtime crossing,
new capability/provider, or Python storage migration is introduced. Python keeps
input normalization and rendering until their actual host consumers migrate.
See [the read contract](../../../../reference/todo-work-counts.md); broader L5/D1 and
local-default qualifications remain open.
