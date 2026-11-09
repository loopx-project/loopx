# Preserve the selected runtime during rollback

Status: implementation proposal.

[中文](self-update-explicit-runtime-route-v0.zh-CN.md)

## Problem and ownership

An explicitly routed `update --rollback` dropped its registry and runtime root
when planning and launching the target runtime. With two populated default
roots, the target's read-only authority check rejected the implicit route. The
parent reported a format incompatibility even though the selected store was
readable. A successful check could also be followed by diagnostics against a
different store.

The existing updater owns this defect. Rollback and forward update resolve the
same selected route through the existing registry/runtime resolver, then pass
that route to planning and every child check. The existing argument builder is
shared; callers that supply no route directly retain their implicit behavior.
This adds no state, provider, migration, permission, scheduler or executor.

## Failure and acceptance boundaries

Authority compatibility remains a read-only `--require-current` check before
the launcher changes. Its exit status and bounded output remain in the existing
execution readback, and failure is reported without guessing its cause. A
failed check also exposes its bounded stdout/stderr in the default CLI output. A
failed core doctor still restores the previous launcher; an extension failure
still reports the activated runtime with its provider unavailable.

Acceptance follows the real rollback CLI through planning and all three child
checks, including each failure boundary and paths containing spaces. Existing
archive, pip and pipx update route regressions remain applicable. Installed
acceptance must distinguish an isolated rollback from changing the user's
active launcher or restoring a production store. This repair does not explain
why a separate installer selected an older source revision.
