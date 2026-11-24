# Archive: Retired Components & Frozen Rubric Snapshots

**Placement Invariant**: This directory stores *only* physically retired components / superseded prototype wrappers ([retired_components.json](retired_components.json)) and frozen rubric specifications ([rubric_v0.json](rubrics/rubric_v0.json)).

**Never move old release scorecards (`0.1.x`..`2.3.x`) into `archive/`.** All 15 `a.b` historical series live continuously in [`history/`](../history/v2.4.0.md) so `v0.1.3 -> v4.0.0` remains queryable on a single unbroken trajectory ([INDEX.md](../INDEX.md)).

## Retired Component Ledger

| ID | Window | Evidence | Retired Component | Single-Owner Replacement |
|---|---|---|---|---|
| `ARC-1X-MD-DUAL-WRITE` | `1.x -> 2.0` | #6396 | Legacy Markdown authority mutation dual-write callers | Single-owner AuthorityStore (File/SQLite + CAS + hard lease) in loopx/control_plane/ |
| `ARC-2X-TMUX-DEMOS` | `2.3.x` | #8190 | Legacy tmux agent and auto-research demo runners | Neutral CLI/ACP host adapters and Steward async manager_context inbox |
| `ARC-2X-LEGACY-PAUSE-METADATA` | `2.3.x -> 2.4.0` | #8431 | Legacy Goal pause metadata wrapper | Canonical authority-archive activation / sleep transitions with backed-up upgrades |
| `ARC-2X-EXPERIMENTAL-COMPACTION-MEM` | `2.4.0` | #8928 | Experimental compaction working memory prototype | Bitter-Lesson subtraction clearing path for FoldToReceipt / EpochReset (battleline 2.5) |
| `ARC-PLAN-B-SERVER-PG` | `PR #9223 (Open)` | #9223 | In-repo server-side PostgreSQL RLS storage & RemoteAuthority HTTP server | Extracted to private loopx-cloud; OSS keeps File/SQLite + openPostgresqlStore SPI + outbound RemoteAuthority client |
