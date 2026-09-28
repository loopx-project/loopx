# Canonical collection pagination checkpoint (2026-09-23)

Moved on 2026-09-26 from [typescript-control-plane-migration-v0.zh-CN.md](../../typescript-control-plane-migration-v0.zh-CN.md) (former section "canonical collection 分页检查点（2026-09-23）"); the English RFC never carried this section, so this file is the semantic mirror of the [Chinese entry](2026-09-23-canonical-collection-pagination.zh-CN.md). RFC baseline `3e443ad7c`. Checkpoint records live in this ledger, not in the RFC body.

Cross-language transport of the canonical collection moved to TS-consistent
pagination: the legacy direct list and the paginated path share the
`canonicalTodoCollection` rule owner, while Python validates and assembles the
complete pages and keeps the caller-facing shape. The 2 MiB RPC ceiling is not
raised and Todo/acceptance rules are not rebuilt in Python; a concurrent version
change fails the whole read, and File opens read-only without creating a missing
authority. Limits and overhead are in the
[pagination contract](../../../../reference/canonical-snapshot-pagination.md).
The current shared-authority checklist separates merged implementation, in-flight
PRs, new code boundaries and D1–D3 evidence instead of replacing the remaining PR
list with coarse package counts.
