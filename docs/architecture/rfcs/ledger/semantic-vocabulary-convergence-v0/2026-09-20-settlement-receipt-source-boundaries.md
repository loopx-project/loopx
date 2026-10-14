# Executed settlement and receipt source boundaries

Current readback: [registered source treatment](2026-10-12-registered-source-treatment.md). The observations below retain their original baseline; they are not current PR status.


Measured from `36134771355f05c9bcc5657ce739bb86426383dc`, for #4447 Stage 2a.
The scope revision in #4789 remains a proposal. This entry changes neither RFC
acceptance nor the F1/F2 production domain: **7/26**, with these four entries
still outside it. Source treatment and scanner enrollment are separate claims.

- **Settlement envelopes (`settlement_step_kind`, `settlement_failure_kind`).**
  `turn_driver/settlement.ts::reduceTurnSettlementTransaction` calls the shared
  `effect_program.ts` builders; its `result.receipts[].step_kind` and
  `result.failure.{kind,step_kind}` cross `effect_runtime_result` into
  `effect_program.py::decode_settlement_result_payload`. The witness executes
  the real reducer directly and through the bridge: committed replay produces
  all four steps without dispatch/checkpoint; identity/prefix rejection,
  provider refusal, terminal refusal and unknown prepared outcome produce
  eight failure kinds. Real file readback adds `writeback_missing`.
- **External input is a separate obligation.** The live
  `settlement.bind_gate` handler runs `settlementResultInput` before its builder.
  Injected envelopes exercise admission of four step and eleven failure values,
  plus unknown/null/numeric rejection in each step/failure slot through both TS
  and Python decoders. These are input witnesses, not eleven producer witnesses.
  `permission_denied` has task-lease producers in `task_lease_acquire.ts` and
  `task_lease_lifecycle.ts`, but this batch does not execute them. `cancelled`
  remains decoder-admitted without an identified producing branch; it is not
  newly classified as compatibility-only. Both production obligations stay open.
- **Receipt phases (`receipt_bound_monitor_phase`, `receipt_bound_replay_phase`).**
  `quota/settlement_readback.ts::readQuotaSettlement` derives facts from isolated
  synthetic receipt files and calls `quota/settlement_phase.ts`; Python
  `read_heartbeat_settlement` decodes the results. Exact monitor commit, absent
  or wrong commit, completion/writeback/spend prefixes, repeated readback and
  conflicting identity exercise actual callers. Monitor emits `poll_due` or
  `settled` even without spend; `settlement_pending` remains accepted by the
  Python work-lane consumer only as compatibility evidence. The legacy monitor
  effect id and terminal-to-replay adapter are retained. Removing them requires
  separate historical-reader/caller migration evidence. Replay's three values
  are produced; autonomous-replan binding is additionally exercised through the
  phase bridge, not a full replan receipt transaction.

Run `uv run --extra test python -m pytest tests/architecture/test_settlement_receipt_source_boundaries.py`:
**62 passed** using Python 3.12.3 and qualified Node 22.22.3. With the existing
binding witness, settlement-driver and quota-settlement tests: **142 passed**.
The two native TS settlement/readback suites: **58 passed**, no skips.
Docs governance, focused lint/type checks and semantic drift smoke pass; the
latter still reports F1/F2 **7/26** and 19 unverified cross-runtime entries.
The thin `scripts/settlement_receipt_source_witness.mts` imports shipped owners;
it introduces no runtime rule, registry metadata or global vocabulary.

Limits: synthetic readback is not proof of durable writers, all call sites, live
CLI/backend qualification or F6 history compatibility. The existing budget-text
failure classifier is characterized, not repaired. No production refactor or
frontend/Lark/CLI change is included; the next owner action is evidence review
and the named missing producer witnesses, not automatic source closure.
