# Delegated operation stop: the canonical lease is the only fence

- Baseline: `e55489c77`, measured October 3, 2026.
- Outcome: overall roadmap S4 ("restart/cancel/drain/stop retain work and
  fence old executors") and the R2 bounded single-operation stop; the
  revocation half of delivery 2 in the
  [September 27 host-supervision plan](2026-09-27-host-supervision.md)
  ("cancellation on expiry/reclaim/revocation"). No provider, capability,
  configuration surface or lease vocabulary is introduced.
- This entry is the specification a follow-up implementation PR is built
  against. It records a design decision and the measurements behind it; it
  does not claim the stop surface has shipped.
- [中文](2026-10-03-delegation-stop-lease-fence.zh-CN.md).

## What was measured

The closed [#5308](https://github.com/loopx-project/loopx/pull/5308) tried to
ship the same user outcome and received sixteen maintainer reviews in four
days, fifteen of them `REQUEST_CHANGES`. Nine of its blocking findings were
instances of one structural property: the receipt's terminal `settled` was a
read-side conjunction of facts written by six independent writers (stop
sidecar acknowledgement, operation lock probe, Turn lane holder record, inner
Host process-group record, outer CLI process-group record, canonical lease),
and each pair of writers has an interleaving window. Each repair added another
fact or another lock; each review found another pair. The branch merged
`main` fifteen times while the same lease owner changed twelve times on
`main`. Its final head passed 147 selected real-process tests and was closed
under an unresolvable historical-attribution hold.

`main` already carries the fence that PR was emulating with file locks:

- `Delegations._complete_delegated_todo` refuses to commit without the
  execution's acquired lease and completes through the canonical lease CAS
  ([#5466](https://github.com/loopx-project/loopx/pull/5466)).
- `runLeasedHostProcess` re-proves the original owner/key/epoch at
  `min(30 s, remaining/2)` and cancels the delegated CLI, including its nested
  Host, when current proof is lost; the forced group termination follows a
  six-second grace ([#5436](https://github.com/loopx-project/loopx/pull/5436)).
- `tests/test_delegation_lease_lifetime.py::test_real_revocation_or_new_execution_stops_nested_host_without_acceptance`
  proves on real File and SQLite authority that releasing that lease stops the
  nested Host and its descendants before the worker returns, leaves the Todo
  open, and that retrying the operation neither reacquires the old execution
  nor launches the Host again.

A replay of the retired acquire receipt is the only way the old execution key
can reach the authority again. The acquire receipt identity is deterministic
in `(goal_id, todo_id, owner, idempotency_key)`, and replay requires current
proof, so a released lease retires its key permanently without a new status.

## Contract

1. **Scope.** One authorized, bound delegated operation on the local host
   authority. Not a team or Goal stop, not coordinator pause, not cross-host
   signalling, not a frontend control beyond a recorded-state label.
2. **Intent.** `stop` is the explicit intent of the binding's requester for
   one operation. It is persisted beside the operation record before any
   fence write, carries the requester identity and one stable `stop_id`, and
   cannot be inferred from a signal, a timeout or progress. Repeated stops
   return the same receipt.
3. **Fence.** The execution's own canonical hard lease
   (`owner`, `idempotency_key`, `lease_epoch`) is the only fence. The
   Delegations host releases it through the existing canonical lifecycle with
   a CAS on the current version, retrying only a version-mismatch race. This
   is the same trust the host already exercises when it claims, renews and
   completes on the member's behalf. After release the authority rejects
   every renewal, completion CAS and acquire replay from that execution; late
   Todo completion and result acceptance are impossible by construction. No
   file lock, worker acknowledgement, lane probe or process-group record is
   part of the guarantee.
4. **Receipt.** The typed TypeScript owner derives one phase from current
   facts on every read; no phase is persisted.
   - `requested`: intent persisted, the execution has not yet exposed a lease
     to release. Read again; the worker observes the intent before it
     launches a Host.
   - `revoked`: the release committed, or the execution is already fenced by
     another epoch or by expiry. Safe to continue the Todo with a new
     operation; the old execution cannot commit any canonical effect.
   - `drained`: additionally, the operation recorded its `stopped`
     observation with `host_supervision` of `returned` (the leased supervisor
     returned after proof loss) or `not_launched` (no Host was launched).
   - `noop`: the operation was `accepted` or `rejected` before the fence took
     effect. Its prior conclusion stands and nothing is written.
   Drain is an observation, never a settlement condition. A dead worker
   leaves `revoked` with `host_supervision: unobserved`; later green reads do
   not upgrade it.
5. **Worker observation.** The worker checks the intent before acquiring a
   lease and again before launching a Host, releases its own lease on either
   checkpoint, and records `stopped` after any supervised execution returns
   while the intent exists. Those checkpoints avoid wasted work; the fence,
   not the checkpoint, is the guarantee.
6. **Authority mode.** Stop requires the Goal's canonical `hard_lease` mode.
   On `legacy` or `soft_claim` authority there is no execution lease and
   therefore no fence; `stop --execute` is refused before any write with a
   reason naming the mode. Promoting the Goal is the enabling step.
7. **Lifecycle.** A stopped operation refuses `resume`; continuing requires a
   new operation, which acquires a new lease epoch. Stop never completes the
   Todo, settles the Goal or changes an accepted result.
8. **Drain latency.** Bounded by the supervisor's renewal cadence plus its
   grace: at most about thirty-six seconds after the release commits, under
   the existing supervisor. No signal accelerator is added in this slice.
   Nested Host cleanup after a forced group kill remains the existing
   supervisor boundary from #5436 and is not re-proven here.
9. **Surfaces.** CLI `delegation stop --execute` and MCP `stop_delegation`
   share `Delegations.stop`; `read`, `wait` and the inventory expose the
   receipt and the `stopped` observation. The dashboard shows a recorded
   stop, not a claim that execution resources were released.

## Decisions taken here

- **D1, hard-lease only.** The alternative is a second linearization
  mechanism for unleased routes, which is the design that failed above.
- **D2, release instead of a new `revoked` lease status.** A new status would
  extend a vocabulary consumed by lifecycle, proof, retirement, migration and
  recovery owners; release already retires the key, as measured.
- **D3, drain reported, not required.** Requiring it recreated every
  process-attribution window in #5308.

## What this entry does not establish

The stop surface is not implemented by this entry. Windows native drain,
PostgreSQL re-qualification, cross-host stop, Lark controls, whole-team stop
and installed-product acceptance are outside the slice. Lease records are
opaque JSON to the File, SQLite and PostgreSQL providers, so no provider
change is expected, but that is a reviewed claim of the implementation PR.
