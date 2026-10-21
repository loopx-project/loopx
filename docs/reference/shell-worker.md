# Shell worker Host failures

`scripts/external_scheduler_worker.py` remains observe-only unless `--wake-cmd`
is configured. Ordinary opaque shell commands retain their existing behavior.
The optional `--wake-host-failure` adapter consumes the configured Host's JSON
stdout receipt and uses the existing Codex Host failure policy. Native benchmark
heartbeat workers enable this adapter; plain/native-Goal and governed Turn
workers retain their existing routes. This changes failed heartbeat dispatch,
not task scoring, submission allowances, model selection or account selection.

The cooperating Host returns `ok`, an agent-scoped `session.session_id`, and on
failure the existing `loopx_turn_host_failure_v0` record in `host_failure`.
One returned process observation has attempt 1; the supervisor owns the durable
attempt count for its configuration. No provider prose, tool output or credential
belongs in that record. Invalid/truncated output, process timeout, a changed
session or an unrecognized failure stays unknown.

Before invoking the Host, the worker atomically persists a started attempt and
holds a nonblocking single-consumer lock for the configured state path. An
interrupted or unreadable original attempt prevents new dispatch. Hard quota,
authentication and unknown failures require the original Host's recovery;
restarting, changing a Todo/reset token or removing the opt-in flag does not
erase that hold. A configuration mismatch also stays held. Core Stop still
wins, without declaring that the original effect was settled.
The same lock also protects a content-free `<state-file>.host-attempt` witness.
It is marked pending before the main started record and Host invocation, and
resolved only after confirmed success is committed to the main state. A pending
or unreadable witness keeps the guarded route even when the main JSON is damaged
or missing and the flag is removed. Conflicting or interrupted writes hold
without another Host call; Core Stop remains readable and preserves the evidence.
Retain both files during recovery, upgrade and rollback. This is local process
evidence, not a new Core state owner or a generic unlock. Once success is confirmed,
disabling the flag retains ordinary command behavior; never-guarded paths are
unchanged, including their existing treatment of damaged backoff state.
For a predecessor without the witness, the matching Host-adapter lock record
also retains the guarded route when its primary state is unreadable or missing.
An unrelated ordinary lock does not enable the adapter. This compatibility
readback never treats PID liveness as permission to dispatch or bypass the lock.
An explicit resolved witness takes precedence over that older lock record;
flag-off commands retain ordinary validation of damaged primary state without
reopening a completed Host attempt. State and witness are rechecked after
acquiring the lock, so a contender cannot revive an earlier observation after
the original Host has committed success.
The retained adapter lock also fences its initial Core read before intent is
written; after confirmed success, flag-off commands keep their ordinary result
handling under that fence.
An explicitly configured `--wake-stop-exit-code` also remains a worker stop
after restart, without another dispatch or a Goal-completion claim. It cannot
clear a prior failed/unknown attempt, which is checked before invoking the Host.

Only an explicit retryable Host result with a retained session can use the
existing bounded same-configuration retry policy. Retry deadlines and attempts
survive worker restart. Each retry rechecks Core admission and passes its exact
session to the native worker, which refuses a missing/replaced binding before
model invocation. A retry reads that original binding even when normal wakes
use fresh context. A confirmed successful response ends the failure streak;
an exhausted policy remains held. Provider hold/backoff is checked before
recording a new semantic replan attempt; a rejected dispatch cannot consume one.
An unresolved semantic replan still requires its original settlement recovery,
even when the provider would otherwise permit a transport retry. Host state is
local transport evidence, not Goal/Todo/quota authority or proof of completion.

Inspect the existing state file, the original wake's `receipt.json`, and the
content-free `host_failure_hold` / `host_failure_backoff` liveness output. Use
the original Host's verified recovery path; deleting state or selecting a new
state-file path is not recovery. This adapter provides no generic unlock,
credential rotation, quota purchase or automatic account handoff. Separate state
paths/uncooperating writers are outside its single-consumer guarantee. Retain
the state during upgrade/rollback; disabling observation does not settle an
already-started call. Stop the worker before rolling back to a version without
this adapter: older workers cannot enforce its held-attempt marker.
Installation alone does not modify a frozen experiment.

Qualification uses synthetic provider envelopes and real local child processes,
including bounded retries, crash/restart, missing session, corrupt state,
concurrency and Stop. It proves the adapter's dispatch rules, not real provider
allowance, model outcome quality, complete billing or long-horizon benefit.
