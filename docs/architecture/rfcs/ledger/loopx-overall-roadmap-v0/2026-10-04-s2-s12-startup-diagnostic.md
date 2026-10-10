# S2/S12 startup diagnostic checkpoint (2026-10-04)

Recorded for [#5551](https://github.com/loopx-project/loopx/pull/5551); the
[roadmap](../../loopx-overall-roadmap-v0.md) body keeps a one-line pointer here.

When the managed Effect runtime cannot publish its startup locator, the failure
now travels through the existing typed startup envelope and reuses the shared
filesystem/lock codes (`io_is_directory`, `mutation_lock_timeout`, Windows
`io_permission_denied` for an occupied locator directory) instead of surfacing
only as `runtime_exited_before_ready` from an unhandled listen-callback
rejection. The bounded message carries no raw Node error, locator path, token or
stack trace.

Real fixtures occupy the locator with a directory and hold a live mutation lock.
Both fail exactly once with the exact code, leave the foreign occupant and its
lock untouched, remove the runtime's own start lock, and resume normal
ping/shutdown once the injected fault is gone. An exit without a typed envelope
still reports `runtime_exited_before_ready`.

This closes the reproduced missing diagnostic only. It does not attribute the
unrelated Linux unexpected-exit CI failure, qualify Linux/Windows CI as passing,
or claim installation, release or strict publication readiness. The
operator-facing boundary is in the
[installation guide](../../../../guides/installing-loopx.md).
