# File Lock Acquisition v0

LoopX uses sibling kernel-lock files to serialize local read-modify-write
operations: POSIX uses `flock`, while Windows uses an `msvcrt` byte-range lock.
The kernel lock, not the file's existence, determines ownership. Operators and
automation must never delete a lock file to recover a waiter.

## Acquisition Policies

| Policy | Deadline | Timeout behavior |
| --- | ---: | --- |
| `mutation` | 5 seconds | Stop the command and require holder inspection before a manual retry. |
| `monitor` | 1 second | Stop the poll; do not tight-loop. Retry only on a later scheduled poll after inspection. |
| `single_flight` | no wait | Return an ordinary duplicate/no-op result without recording an incident. |

`exclusive_file_lock` uses `LOCK_EX | LOCK_NB`, a monotonic deadline, and a
bounded sleep between attempts. A deadline raises
`LockAcquireTimeoutError` with `error_code=lock_acquire_timeout`. The former
unbounded `LOCK_EX` wait is not part of this contract.

## Holder And Incident Records

After acquisition, the holder writes public-safe JSON to the POSIX `*.lock`
file or atomically overwrites the Windows `*.lock.holder` sidecar:

- stable hashed `lock_id` (never an absolute target path);
- PID, agent id, operation, policy, and acquisition time;
- release time after a normal exit.

Windows metadata is separate because a byte-range lock prevents another file
handle from reading the locked byte. POSIX retains the existing single-file
contract, where advisory metadata and `flock` share `*.lock`. In both cases the
kernel lock, not the metadata file's existence, is authoritative.

The Windows sidecar persists after release and sits beside the locked state
file, so its name must not end in `.json`: directory scans that collect
`*.json` state records would otherwise read lock metadata as state. Earlier
releases wrote `*.lock.holder.json`. Readers still accept that name, preferring
the most recently acquired record, and each new holder removes it while it
owns the kernel lock.

A timeout appends one `file_lock_incident_v0` row to the sibling
`*.lock.incidents.jsonl` channel. That append uses `O_APPEND` directly and does
not acquire the blocked lock. The row contains holder and waiter identities,
wait duration, policy, and an `operator_action`. Failure to append an incident
does not hide or delay the typed timeout.

## Operator Recovery

1. Inspect the recorded holder PID, agent, operation, and acquisition time.
2. Confirm that the process is still present and actually stalled.
3. Terminate the process only after that confirmation and within the operator's
   existing authority.
4. Retry according to the policy after the process exits. Do not delete the
   lock file; a later owner will overwrite the holder sidecar.

An absent PID or stale metadata is evidence to investigate, not permission to
remove a lock file. The kernel releases `flock` or `msvcrt` ownership when its
process or file descriptor exits.
