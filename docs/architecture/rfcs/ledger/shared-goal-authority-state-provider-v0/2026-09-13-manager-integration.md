# Manager integration checkpoint (2026-09-13)

Moved without content change from [shared-goal-authority-state-provider-v0.md](../../shared-goal-authority-state-provider-v0.md) (former section "Manager integration checkpoint (2026-09-13)") on 2026-09-26; RFC baseline `3e443ad7c`. Checkpoint records live in this ledger, not in the RFC body.

Source audit at `7eb4b7bb1661bd5eff63a8725a33169792d5964b` confirms the
`AuthorityStore` seam and the transaction/presentation/journal consolidations
in #4280, #4283 and #4287. This updates the integration baseline, not the
qualification evidence or historical provider baselines above. Candidate
SQLite/PostgreSQL paths, provider-specific holds and the D1–D3 plan remain;
neither a default source switch nor a shared service is declared shipped.

The [capable manager and semantic handoff RFC](../../capable-manager-semantic-handoff-v0.md)
consumes this authority. Its M1 host-tool work and M2 request-ledger refactor
can proceed without provider promotion. Section 1.4 defines their boundary;
the [TS execution cards](../../typescript-control-plane-migration-v0.md#execution-cards-after-the-current-stack)
still own business-rule consolidation and legacy-caller deletion.
