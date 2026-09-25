"""Stage 2C parity-half rows (``s2c2.*``) of the shared-goal-authority ladder.

Every row drives the product through the real ``python -m loopx.cli`` against
one goal whose ``coordination.runtime_shadow`` capture is explicitly enabled
and bootstrapped, and asserts only through shipped operator interfaces:
``authority-shadow status|drain``, ``coordination-shadow bootstrap|inspect|
qualify|read-candidate|rollback`` and ``migrate-state``. Candidate history is
read back through the retained TypeScript store (the adapter's read RPC). The
rows add no product path and never read the candidate for a decision.

Two scheduling-only seams exist, both outside every product decision:

* holding the stable maintenance lock defers a writer's post-commit drain, so
  its committed outbox entry stays pending (``drain_lock_busy``);
* a POSIX crash worker pauses a real CLI process at one persistence window and
  the row SIGKILLs it there. It substitutes no result and edits no byte.
"""

from __future__ import annotations

import importlib
import json
import select
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from ...file_lock import exclusive_file_lock
from ..coordination import local_authority_shadow_outbox as shadow_outbox
from .authority_e2e_fixtures import (
    REPO_ROOT,
    GoalWorkspace,
    JsonObject,
    build_goal_workspace,
    cli_env,
    node_executable,
    parse_json_object,
    run_cli,
    unique_goal_id,
)
from .authority_e2e_row_support import (
    AGENT_A,
    AGENT_B,
    RowContext,
    RowOutcome,
    acquire_lease,
    add_todo,
    expect,
    lease_version,
    passed,
)

RUNTIME_SHADOW_CONFIG_SCHEMA = "loopx_coordination_runtime_shadow_config_v0"
RUNTIME_SHADOW_PROVIDER = "file_v0"
CRASH_BARRIER_TIMEOUT_SECONDS = 30.0
PRIMARY_CRASH_WINDOWS: tuple[tuple[str, bool], ...] = (
    ("before_replace", False),
    ("after_replace", True),
    ("before_marker", True),
)
DRAIN_CRASH_WINDOWS: tuple[str, ...] = ("before_commit", "after_commit", "after_cursor", "between_unlinks")
PARITY_CYCLES = 3
# ``todo archive-completed`` stays out of this shared cross-writer parity set
# because it is a Python-only lifecycle writer with no TypeScript counterpart to
# interleave; the archive-after-lease fold is pinned by its own deterministic row
# ``s2c2.archive_after_leased_completion_parity`` instead.
PARITY_REQUIRED_WRITE_CLASSES: tuple[str, ...] = (
    "todo_add",
    "todo_update",
    "todo_complete",
    "todo_supersede",
    "task_lease_acquire",
    "task_lease_renew",
    "task_lease_transfer",
    "task_lease_fence_close",
)
GROWTH_TRANSACTIONS = 10
# The archive-after-lease row delivers six transactions: the add, the lease
# acquire, the fenced complete (which also closes the lease fence), the anchor
# add, and the archive that retires the completed Todo from the graph.
ARCHIVE_PARITY_OPERATIONS = 6
# The row's own coverage: the Todo add, the lease acquire, the fenced complete
# with its lease fence close, and the archive-completed writer.
ARCHIVE_PARITY_REQUIRED_WRITE_CLASSES: tuple[str, ...] = (
    "todo_add",
    "todo_complete",
    "todo_archive_completed",
    "task_lease_acquire",
    "task_lease_fence_close",
)
GROWTH_TEXT_TEMPLATE = "Growth workload todo %02d " + "x" * 160
# Each file-v0 transaction retains the complete projection, so the per-transaction
# byte delta may grow by about one Todo record per transaction. A larger jump
# means something beyond the live projection is being re-published.
GROWTH_DELTA_ACCELERATION_ENVELOPE_BYTES = 2048
EVENT_ONLY_HOLD = "source_drift"
CONTINUITY_HOLD = "source_partition_continuity_unproved"
SHADOW_READ_MODULE = Path("loopx") / "control_plane" / "coordination" / "local_authority_shadow.ts"
SHADOW_READ_REQUEST_SCHEMA = "loopx_coordination_runtime_shadow_outbox_read_v0"
SHADOW_READ_SCAN_LIMIT = 10_000

# A real CLI process that pauses at exactly one persistence window. The parent
# waits for the BARRIER line and SIGKILLs the child there. Every hook forwards
# to the production function; nothing decides an outcome or substitutes bytes.
CRASH_WORKER = r"""
import json, pathlib, sys, time
from loopx.cli import main
from loopx.control_plane.coordination import local_authority_shadow_adapter as adapter
from loopx.control_plane.coordination import local_authority_shadow_outbox as outbox
from loopx.control_plane.todos import active_state_editing
window, state = sys.argv[1], pathlib.Path(sys.argv[2]).resolve()
def pause():
    print('BARRIER ' + json.dumps({'window': window}), flush=True)
    time.sleep(40)
    raise RuntimeError('parent failed to terminate at persistence barrier')
actual_rpc = adapter.effect_runtime_result
def rpc(method, request, **kwargs):
    if method == 'coordination.runtime_shadow.commit_entry' and window == 'before_commit':
        pause()
    result = actual_rpc(method, request, **kwargs)
    if method == 'coordination.runtime_shadow.commit_entry' and window == 'after_commit':
        pause()
    return result
adapter.effect_runtime_result = rpc
actual_cursor = outbox.write_cursor
def cursor(*args, **kwargs):
    result = actual_cursor(*args, **kwargs)
    if window == 'after_cursor':
        pause()
    return result
outbox.write_cursor = cursor
actual_json = outbox.durable_write_json
def write_json(path, value):
    if window == 'before_marker' and path.name.endswith('.committed.json'):
        pause()
    return actual_json(path, value)
outbox.durable_write_json = write_json
actual_replace = active_state_editing.os.replace
def replace(source, target):
    is_primary = pathlib.Path(target).resolve() == state
    if is_primary and window == 'before_replace':
        pause()
    result = actual_replace(source, target)
    if is_primary and window == 'after_replace':
        pause()
    return result
active_state_editing.os.replace = replace
actual_unlink = pathlib.Path.unlink
def unlink(path, *args, **kwargs):
    result = actual_unlink(path, *args, **kwargs)
    if window == 'between_unlinks' and path.name.endswith('.prepared.json'):
        pause()
    return result
pathlib.Path.unlink = unlink
raise SystemExit(main(sys.argv[3:]))
"""


# ---------------------------------------------------------------------------
# Workspace, CLI and read-back helpers
# ---------------------------------------------------------------------------


def _object(value: object, label: str) -> JsonObject:
    expect(isinstance(value, dict), f"{label} must be an object")
    assert isinstance(value, dict)
    return {str(key): item for key, item in value.items()}


def _list(value: object, label: str) -> list[object]:
    expect(isinstance(value, list), f"{label} must be a list")
    assert isinstance(value, list)
    return list(value)


def set_runtime_shadow(workspace: GoalWorkspace, *, enabled: bool) -> None:
    """The operator step: enable or disable transaction capture in the registry."""

    registry = parse_json_object(workspace.registry_path.read_text(encoding="utf-8"))
    goals = _list(registry.get("goals"), "registry goals")
    goal = _object(goals[0], "registry goal")
    coordination = _object(goal.get("coordination"), "goal coordination")
    coordination["runtime_shadow"] = {
        "schema_version": RUNTIME_SHADOW_CONFIG_SCHEMA,
        "enabled": enabled,
        "provider": RUNTIME_SHADOW_PROVIDER,
    }
    goal["coordination"] = coordination
    goals[0] = goal
    registry["goals"] = goals
    workspace.registry_path.write_text(json.dumps(registry), encoding="utf-8")


def goal_cli(workspace: GoalWorkspace, *args: str, check: bool = True) -> JsonObject:
    return run_cli(workspace, *args, "--goal-id", workspace.goal_id, check=check)


def bootstrap_capture(workspace: GoalWorkspace) -> JsonObject:
    """Bootstrap the capture lineage; returns the whole CLI payload."""

    payload = goal_cli(workspace, "coordination-shadow", "bootstrap", "--execute")
    bootstrap = _object(payload.get("bootstrap"), "bootstrap")
    expect(bootstrap.get("status") == "applied", "bootstrap must apply a fresh lineage")
    expect(bootstrap.get("cursor") == "1", "bootstrap must be the first transaction")
    expect(payload.get("decision_read_from_shadow") is False, "bootstrap must not read the candidate for a decision")
    return payload


def capture_workspace(
    context: RowContext,
    prefix: str,
    *,
    handoff_mode: str = "hard_lease",
    bootstrap: bool = True,
) -> GoalWorkspace:
    workspace = build_goal_workspace(
        context.root,
        goal_id=unique_goal_id(prefix),
        handoff_mode=handoff_mode,
        shadow_enabled=False,
        runtime_root_binding="cli_override",
    )
    set_runtime_shadow(workspace, enabled=True)
    if bootstrap:
        bootstrap_capture(workspace)
    return workspace


def capture_evidence(payload: Mapping[str, object], *, label: str) -> JsonObject:
    evidence = _object(payload.get("coordination_runtime_shadow"), f"{label} capture evidence")
    expect(evidence.get("primary_writeback_preserved") is True, f"{label} must preserve the primary writeback")
    expect(evidence.get("provider_to_local_writes") is False, f"{label} must never write from provider to local state")
    expect(evidence.get("candidate_read_for_decision") is False, f"{label} must never read the candidate for a decision")
    expect(evidence.get("parity_verdict") == "not_evaluated", f"{label} must not claim parity on the write path")
    return evidence


def delivered(payload: Mapping[str, object], *, label: str) -> JsonObject:
    """A primary write whose outbox entry was drained inline into the candidate."""

    evidence = capture_evidence(payload, label=label)
    expect(evidence.get("outcome") == "delivered", f"{label} must deliver its outbox entry")
    expect(
        evidence.get("source_transaction_correlated") is True and evidence.get("durable_source_outbox") is True,
        f"{label} must correlate a durable outbox entry",
    )
    drain = _object(evidence.get("drain"), f"{label} drain")
    expect(drain.get("outcome") == "drained" and drain.get("candidate_readback_verified") is True, f"{label} must verify its candidate read-back")
    return evidence


def no_transaction(payload: Mapping[str, object], *, label: str) -> JsonObject:
    evidence = capture_evidence(payload, label=label)
    expect(evidence.get("outcome") == "no_transaction", f"{label} must record no transaction")
    expect(_object(evidence.get("entry"), f"{label} entry").get("entry_id") is None, f"{label} must mint no outbox entry")
    return evidence


def deferred(payload: Mapping[str, object], *, label: str) -> JsonObject:
    evidence = capture_evidence(payload, label=label)
    expect(
        evidence.get("outcome") == "drain_deferred" and evidence.get("reason_code") == "drain_lock_busy",
        f"{label} must defer its drain behind the held maintenance lock",
    )
    expect(
        evidence.get("source_transaction_correlated") is True and evidence.get("durable_source_outbox") is True,
        f"{label} must still correlate a durable outbox entry",
    )
    return evidence


def shadow_status(workspace: GoalWorkspace) -> JsonObject:
    return goal_cli(workspace, "authority-shadow", "status", check=False)


def backlog(status: Mapping[str, object], partition: str) -> JsonObject:
    return _object(_object(status.get("outbox"), "status outbox").get(partition), f"{partition} backlog")


def candidate(status: Mapping[str, object]) -> JsonObject:
    return _object(status.get("candidate"), "status candidate")


def management_status(status: Mapping[str, object]) -> str:
    return str(_object(status.get("management"), "status management").get("status"))


def drain(workspace: GoalWorkspace, *flags: str) -> JsonObject:
    return goal_cli(workspace, "authority-shadow", "drain", *flags, check=False)


def inspect(workspace: GoalWorkspace) -> JsonObject:
    payload = goal_cli(workspace, "coordination-shadow", "inspect", check=False)
    expect(payload.get("decision_read_from_shadow") is False, "inspect must not read the candidate for a decision")
    return payload


def qualify(workspace: GoalWorkspace, *flags: str) -> JsonObject:
    payload = goal_cli(workspace, "coordination-shadow", "qualify", *flags, check=False)
    expect(payload.get("decision_read_from_shadow") is False, "qualify must not read the candidate for a decision")
    return payload


def read_candidate(workspace: GoalWorkspace, todo_id: str) -> JsonObject:
    payload = goal_cli(workspace, "coordination-shadow", "read-candidate", "--todo-id", todo_id, check=False)
    expect(payload.get("decision_read_from_shadow") is False, "read-candidate must not read the candidate for a decision")
    return payload


def qualified(payload: Mapping[str, object], *, label: str) -> JsonObject:
    qualification = _object(payload.get("qualification"), f"{label} qualification")
    expect(payload.get("ok") is True and qualification.get("status") == "qualified", f"{label} must qualify")
    expect(qualification.get("parity_matches") is True and qualification.get("scope") == "bounded", f"{label} must be a bounded parity match")
    expect(
        qualification.get("sustained_parity_verified") is False and qualification.get("sustained_parity_verdict") == "not_evaluated",
        f"{label} must not claim sustained parity",
    )
    return qualification


def rejected(payload: Mapping[str, object], key: str, *, label: str) -> JsonObject:
    result = _object(payload.get(key), f"{label} result")
    expect(payload.get("ok") is False and result.get("qualified") is False, f"{label} must be rejected")
    return result


def history(workspace: GoalWorkspace) -> list[JsonObject]:
    """Complete candidate history through the TypeScript shadow read over the retained store.

    The read runs in an independent node process so the row never imports the
    Python adapter; it is the same ``readLocalAuthorityShadow`` the product
    uses for status and drain proof.
    """

    node = node_executable()
    expect(node is not None, "node is required to read the candidate history")
    assert node is not None
    request = {
        "schema_version": SHADOW_READ_REQUEST_SCHEMA,
        "runtime_root": str(workspace.runtime_root),
        "goal_id": workspace.goal_id,
        "scan_limit": SHADOW_READ_SCAN_LIMIT,
    }
    request_path = workspace.home / "shadow-read-request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    script = (
        f"import {{ readLocalAuthorityShadow }} from {json.dumps((REPO_ROOT / SHADOW_READ_MODULE).as_uri())};"
        "import { readFile } from 'node:fs/promises';"
        "process.stdout.write(JSON.stringify(await readLocalAuthorityShadow(JSON.parse(await readFile(process.argv[1], 'utf8')))));"
    )
    completed = subprocess.run(
        [node, "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e", script, str(request_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        timeout=60,
        check=False,
    )
    expect(completed.returncode == 0, f"the TypeScript shadow read exited {completed.returncode}")
    view = parse_json_object(completed.stdout)
    expect(view.get("status") == "loaded", "candidate history must load through the TypeScript store")
    proof = _object(view.get("proof"), "history proof")
    return [_object(item, "transaction") for item in _list(proof.get("transactions"), "history transactions")]


def single_receipt(transaction: Mapping[str, object], *, label: str) -> JsonObject:
    receipts = _list(transaction.get("receipts"), f"{label} receipts")
    expect(len(receipts) == 1, f"{label} must carry exactly one receipt")
    return _object(receipts[0], f"{label} receipt")


def committed_receipt(transaction: Mapping[str, object], *, label: str) -> JsonObject:
    receipt = single_receipt(transaction, label=label)
    expect(receipt.get("resolution") == "committed" and receipt.get("no_op") is False, f"{label} must be a committed, effective receipt")
    return receipt


def projected_todo_count(transaction: Mapping[str, object]) -> int:
    projection = _object(transaction.get("projection"), "transaction projection")
    todos = projection.get("todos")
    return len(todos) if isinstance(todos, list) else 0


def partition_files(workspace: GoalWorkspace, partition: str) -> list[str]:
    directory = shadow_outbox.partition_directory(workspace.runtime_root, workspace.goal_id, partition)
    return sorted(path.name for path in directory.iterdir()) if directory.is_dir() else []


def phase_count(names: Sequence[str], phase: str) -> int:
    return sum(1 for name in names if name.endswith(f".{phase}.json"))


def outbox_present(workspace: GoalWorkspace) -> bool:
    return shadow_outbox.outbox_root(workspace.runtime_root, workspace.goal_id).exists()


def store_documents(workspace: GoalWorkspace) -> list[Path]:
    return sorted((workspace.runtime_root / "authority-shadow" / "file-v0").glob("authority-store-*.json"))


def todo_count(workspace: GoalWorkspace) -> int:
    listed = goal_cli(workspace, "todo", "list")
    return len(_list(listed.get("todos"), "todo list"))


@contextmanager
def hold_drain_lock(workspace: GoalWorkspace) -> Iterator[None]:
    """Hold the stable maintenance lock so writers defer their post-commit drain."""

    with exclusive_file_lock(
        shadow_outbox.drain_lock_target(workspace.runtime_root, workspace.goal_id),
        operation="e2e_window",
    ):
        yield


def crash_cli(workspace: GoalWorkspace, window: str, *args: str) -> None:
    """Run one real CLI command and SIGKILL it at ``window``."""

    command = [sys.executable, "-c", CRASH_WORKER, window, str(workspace.state_path), *workspace.cli_prefix(), *args, "--goal-id", workspace.goal_id]
    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        env=cli_env(workspace),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    line = ""
    try:
        assert process.stdout is not None
        readable, _, _ = select.select([process.stdout], [], [], CRASH_BARRIER_TIMEOUT_SECONDS)
        if readable:
            line = process.stdout.readline()
    finally:
        process.kill()
        _, stderr = process.communicate(timeout=10)
    expect(line.startswith("BARRIER "), f"{window}: the CLI did not reach its persistence window: {stderr[-200:]}")
    expect(process.returncode == -9, f"{window}: the crash worker must die by SIGKILL")


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------


def row_outbox_prepared_then_committed_entries(context: RowContext) -> RowOutcome:
    """A Python Todo writer and a TypeScript lease writer each leave one prepared record and committed marker; drain delivers both once."""

    workspace = capture_workspace(context, "ladder-outbox")
    with hold_drain_lock(workspace):
        added = add_todo(workspace, "Prepared and committed while the drain lock is held.")
        expect(added.get("added") is True, "the todo must commit while its drain is deferred")
        todo_id = str(added["todo_id"])
        acquired = acquire_lease(workspace, todo_id=todo_id, owner=AGENT_A, idempotency_key="ladder-outbox-lease")
        expect(acquired.get("acquired") is True, "the lease must commit while its drain is deferred")
        entries = [
            _object(deferred(added, label="todo add").get("entry"), "todo entry"),
            _object(deferred(acquired, label="task-lease acquire").get("entry"), "lease entry"),
        ]
        expect([entry.get("partition") for entry in entries] == ["todos", "leases"], "the writers must target their own partitions")
        expect(all(entry.get("seq") == 1 for entry in entries), "each partition must start at sequence 1")
        status = shadow_status(workspace)
        for partition in ("todos", "leases"):
            pending = backlog(status, partition)
            expect(
                pending.get("committed_pending") == 1 and pending.get("prepared_only") == 0 and pending.get("cursor_last_seq") is None,
                f"{partition} must hold exactly one committed, undrained entry",
            )
            names = partition_files(workspace, partition)
            expect(
                phase_count(names, "prepared") == 1 and phase_count(names, "committed") == 1,
                f"{partition} must retain its prepared record and committed marker",
            )
        expect(todo_count(workspace) == 1, "the primary must show the committed todo while its entry is pending")
    drained = drain(workspace)
    expect(
        drained.get("ok") is True and drained.get("outcome") == "drained" and drained.get("delivered") == 2 and drained.get("replayed") == 0,
        "one drain must deliver both pending entries exactly once",
    )
    settled = [_object(item, "drained entry") for item in _list(drained.get("entries"), "drain entries")]
    expect(
        all(item.get("outcome") == "delivered" and item.get("resolution") == "committed" for item in settled),
        "every drained entry must be a committed delivery",
    )
    status = shadow_status(workspace)
    for partition in ("todos", "leases"):
        pending = backlog(status, partition)
        expect(pending.get("committed_pending") == 0 and pending.get("prepared_only") == 0 and pending.get("cursor_last_seq") == 1, f"{partition} must be drained to sequence 1")
        expect(partition_files(workspace, partition) == ["drain-cursor.json"], f"{partition} must retain only its cursor after delivery")
    transactions = history(workspace)
    expect(len(transactions) == 3 and transactions[0].get("receipts") == [] and transactions[0].get("cursor") == "1", "history must be the bootstrap plus two deliveries")
    receipts = [committed_receipt(transaction, label=f"transaction {index}") for index, transaction in enumerate(transactions[1:], start=2)]
    runtimes = sorted({str(receipt.get("writer_runtime")) for receipt in receipts})
    classes = sorted({str(receipt.get("write_class")) for receipt in receipts})
    expect(runtimes == ["python", "typescript"], "the deliveries must come from both writer runtimes")
    expect(classes == ["task_lease_acquire", "todo_add"], "the deliveries must carry their write classes")
    inline = delivered(add_todo(workspace, "Delivered inline once the lock is free."), label="todo add (inline)")
    expect(_object(inline.get("drain"), "inline drain").get("last_cursor") == "4", "an inline delivery must continue the same lineage")
    return passed(
        deferred_entries=2,
        partitions=["leases", "todos"],
        drained=2,
        writer_runtimes=runtimes,
        write_classes=classes,
        inline_cursor="4",
    )


def row_drain_idempotent(context: RowContext) -> RowOutcome:
    """Bounded drains are cumulative, an idle drain changes nothing, and a writer replay mints no entry."""

    workspace = capture_workspace(context, "ladder-drain")
    with hold_drain_lock(workspace):
        for index in range(3):
            deferred(add_todo(workspace, f"Deferred todo {index}."), label=f"todo add {index}")
    pending = backlog(shadow_status(workspace), "todos")
    expect(pending.get("committed_pending") == 3 and pending.get("next_seq") == 3, "three committed entries must be pending")
    first = drain(workspace, "--max-entries", "1")
    expect(
        first.get("ok") is True and first.get("delivered") == 1 and first.get("replayed") == 0 and first.get("pending_after") == 2 and first.get("budget_exhausted") is True,
        "a bounded drain must deliver one entry and preserve the rest",
    )
    expect(first.get("cursor_after") == "2", "the bounded drain must advance the candidate by one transaction")
    rest = drain(workspace)
    expect(rest.get("ok") is True and rest.get("delivered") == 2 and rest.get("replayed") == 0 and rest.get("pending_after") == 0, "the next drain must deliver the remaining entries")
    expect(rest.get("cursor_after") == "4", "the second drain must finish at cursor 4")
    idle = drain(workspace)
    expect(
        idle.get("ok") is True and idle.get("outcome") == "nothing_pending" and idle.get("delivered") == 0 and idle.get("replayed") == 0,
        "an idle drain must deliver and replay nothing",
    )
    expect(
        idle.get("cursor_before") == "4" and idle.get("cursor_after") == "4"
        and idle.get("head_digest") == rest.get("head_digest") and idle.get("provider_revision") == rest.get("provider_revision"),
        "an idle drain must leave the candidate head unchanged",
    )
    after = backlog(shadow_status(workspace), "todos")
    expect(after.get("committed_pending") == 0 and after.get("cursor_last_seq") == 3, "the backlog must be empty at sequence 3")
    expect(partition_files(workspace, "todos") == ["drain-cursor.json"], "only the cursor may remain")
    transactions = history(workspace)
    expect(len(transactions) == 4, "history must hold the bootstrap plus three deliveries")
    sequences = [committed_receipt(transaction, label=f"transaction {index}").get("seq") for index, transaction in enumerate(transactions[1:], start=2)]
    expect(sequences == [1, 2, 3], "receipts must settle the outbox sequences in order")
    listed = _list(goal_cli(workspace, "todo", "list").get("todos"), "todo list")
    todo_id = str(_object(listed[0], "listed todo")["todo_id"])
    acquired = delivered(
        acquire_lease(workspace, todo_id=todo_id, owner=AGENT_A, idempotency_key="ladder-drain-lease"),
        label="task-lease acquire",
    )
    replayed = acquire_lease(workspace, todo_id=todo_id, owner=AGENT_A, idempotency_key="ladder-drain-lease")
    expect(replayed.get("idempotent") is True, "a same-key re-acquire must be idempotent")
    expect("coordination_runtime_shadow" not in replayed, "an idempotent replay must not capture")
    expect(len(history(workspace)) == 5, "a writer replay must not add a candidate transaction")
    expect(backlog(shadow_status(workspace), "leases").get("committed_pending") == 0, "a writer replay must leave no pending entry")
    return passed(
        bounded_first_pass=1,
        second_pass=2,
        idle_pass_delivered=0,
        head_digest_stable=True,
        lease_partition_cursor=str(_object(acquired.get("drain"), "lease drain").get("last_cursor")),
        transactions=5,
    )


def row_sigkill_between_primary_write_and_drain(context: RowContext) -> RowOutcome:
    """A writer killed around its primary replace leaves a prepared-only entry that drain settles from the primary bytes."""

    workspace = capture_workspace(context, "ladder-primary-crash")
    resolutions: dict[str, str] = {}
    visible = 0
    for index, (window, replaced) in enumerate(PRIMARY_CRASH_WINDOWS, start=1):
        crash_cli(workspace, window, "todo", "add", "--role", "agent", "--text", f"Primary write killed at {window}.", "--task-class", "advancement_task")
        visible += 1 if replaced else 0
        expect(todo_count(workspace) == visible, f"{window}: the primary must show exactly the replaced writes")
        pending = backlog(shadow_status(workspace), "todos")
        expect(pending.get("committed_pending") == 0 and pending.get("prepared_only") == 1, f"{window}: one prepared-only entry must remain")
        expect(phase_count(partition_files(workspace, "todos"), "committed") == 0, f"{window}: no committed marker may exist")
        drained = drain(workspace)
        expect(
            drained.get("ok") is True and drained.get("delivered") == 1 and drained.get("replayed") == 0 and drained.get("no_op") == (0 if replaced else 1),
            f"{window}: drain must settle the entry from the primary bytes",
        )
        transactions = history(workspace)
        expect(len(transactions) == index + 1, f"{window}: every settled entry must be one transaction")
        receipt = single_receipt(transactions[-1], label=window)
        resolution = str(receipt.get("resolution"))
        expect(
            resolution == ("committed_proven_by_readback" if replaced else "abandoned") and receipt.get("no_op") is (not replaced),
            f"{window}: the receipt must prove the primary outcome",
        )
        expect(projected_todo_count(transactions[-1]) == visible, f"{window}: the candidate must equal the primary after recovery")
        after = backlog(shadow_status(workspace), "todos")
        expect(after.get("prepared_only") == 0 and after.get("cursor_last_seq") == index, f"{window}: the cursor must settle sequence {index}")
        resolutions[window] = resolution
    inspection = _object(inspect(workspace).get("inspection"), "inspection")
    expect(inspection.get("status") == "matched" and inspection.get("parity_matches") is True, "recovered history must match the primary")
    return passed(windows=resolutions, primary_todos=visible, transactions=len(PRIMARY_CRASH_WINDOWS) + 1, parity="matched")


def row_sigkill_mid_drain(context: RowContext) -> RowOutcome:
    """A writer killed inside its inline drain is recovered by the next drain from exact receipts, never delivered twice."""

    outcomes: dict[str, JsonObject] = {}
    for window in DRAIN_CRASH_WINDOWS:
        workspace = capture_workspace(context, "ladder-drain-crash")
        crash_cli(workspace, window, "todo", "add", "--role", "agent", "--text", f"Drain killed at {window}.", "--task-class", "advancement_task")
        expect(todo_count(workspace) == 1, f"{window}: the primary write must be committed")
        before = backlog(shadow_status(workspace), "todos")
        drained = drain(workspace)
        expected_delivered = 1 if window == "before_commit" else 0
        expect(
            drained.get("ok") is True and drained.get("outcome") == "drained"
            and drained.get("delivered") == expected_delivered and drained.get("replayed") == 1 - expected_delivered,
            f"{window}: recovery must deliver an uncommitted entry once and replay a committed one",
        )
        transactions = history(workspace)
        expect(len(transactions) == 2, f"{window}: the candidate must hold exactly one delivery")
        committed_receipt(transactions[1], label=window)
        expect(projected_todo_count(transactions[1]) == 1, f"{window}: the delivery must project the committed todo")
        after = backlog(shadow_status(workspace), "todos")
        expect(after.get("committed_pending") == 0 and after.get("prepared_only") == 0 and after.get("cursor_last_seq") == 1, f"{window}: the backlog must settle at sequence 1")
        expect(partition_files(workspace, "todos") == ["drain-cursor.json"], f"{window}: only the cursor may remain")
        idle = drain(workspace)
        expect(idle.get("outcome") == "nothing_pending" and idle.get("delivered") == 0 and idle.get("replayed") == 0, f"{window}: a further drain must be idle")
        outcomes[window] = {
            "delivered": drained.get("delivered"),
            "replayed": drained.get("replayed"),
            "committed_pending_before": before.get("committed_pending"),
            "backlog_invalid_before": before.get("invalid"),
        }
    return passed(windows=outcomes)


def row_rollback_with_pending_entries(context: RowContext) -> RowOutcome:
    """Rollback archives the outbox with its pending entries, holds capture until an explicit rebootstrap, and replays."""

    workspace = capture_workspace(context, "ladder-rollback", bootstrap=False)
    first = _object(bootstrap_capture(workspace).get("bootstrap"), "first bootstrap")
    delivered(add_todo(workspace, "Delivered before rollback."), label="todo add")
    with hold_drain_lock(workspace):
        deferred(add_todo(workspace, "Committed but not yet drained."), label="todo add (deferred)")
    crash_cli(workspace, "before_replace", "todo", "add", "--role", "agent", "--text", "Prepared only.", "--task-class", "advancement_task")
    status = shadow_status(workspace)
    pending = backlog(status, "todos")
    expect(pending.get("committed_pending") == 1 and pending.get("prepared_only") == 1 and pending.get("cursor_last_seq") == 1, "one committed and one prepared-only entry must be pending")
    current = candidate(status)
    revision = str(current.get("provider_revision"))
    expect(current.get("status") == "loaded" and current.get("cursor") == "2", "the candidate must be at cursor 2")
    inspection = _object(inspect(workspace).get("inspection"), "inspection")
    expect(
        inspection.get("qualified") is False and inspection.get("reason_code") == "outbox_pending" and inspection.get("provider_revision") == revision,
        "pending entries must block qualification and name the exact revision",
    )
    preview = goal_cli(workspace, "coordination-shadow", "rollback", "--provider-revision", revision)
    expect(preview.get("ok") is True and preview.get("executed") is False and "rollback" not in preview, "a rollback preview must not execute")
    expect(outbox_present(workspace), "a preview must leave the outbox in place")
    executed = goal_cli(workspace, "coordination-shadow", "rollback", "--provider-revision", revision, "--execute")
    rollback = _object(executed.get("rollback"), "rollback")
    expect(
        rollback.get("status") == "applied" and rollback.get("active_shadow_removed") is True and rollback.get("archive_retained") is True,
        "rollback must retire the active candidate into a retained archive",
    )
    expect(rollback.get("archived_cursor") == "2" and rollback.get("archived_provider_revision") == revision, "the archive must bind the exact revision")
    archive = rollback.get("outbox_archive_path")
    expect(isinstance(archive, str) and Path(archive).is_dir(), "rollback must archive the goal outbox")
    assert isinstance(archive, str)
    archived = sorted(path.name for path in Path(archive).rglob("*") if path.is_file())
    expect(
        phase_count(archived, "prepared") == 2 and phase_count(archived, "committed") == 1
        and "drain-cursor.json" in archived and "manifest.json" in archived,
        "the archive must preserve pending entries, the marker, the cursor and the manifest",
    )
    expect(not outbox_present(workspace), "the active outbox must be moved, not copied")
    status = shadow_status(workspace)
    expect(management_status(status) == "inactive" and candidate(status).get("status") == "missing", "management must be inactive with no candidate")
    expect(backlog(status, "todos").get("committed_pending") == 0 and backlog(status, "todos").get("prepared_only") == 0, "no backlog may remain")
    after = add_todo(workspace, "Primary write after rollback.")
    expect(after.get("added") is True, "primary writes must resume after rollback")
    held = capture_evidence(after, label="todo add (after rollback)")
    expect(held.get("outcome") == "no_transaction" and held.get("reason_code") == "bootstrap_required", "capture must report bootstrap_required, not a transaction")
    rebootstrap = bootstrap_capture(workspace)
    second = _object(rebootstrap.get("bootstrap"), "second bootstrap")
    expect(second.get("capture_lineage_id") != first.get("capture_lineage_id"), "a rebootstrap must start a new lineage")
    summary = _object(rebootstrap.get("projection_summary"), "projection summary")
    expect(summary.get("todo_count") == 3 and summary.get("lease_count") == 0, "the new baseline must import the current primary")
    replay = _object(goal_cli(workspace, "coordination-shadow", "rollback", "--provider-revision", revision, "--execute").get("rollback"), "rollback replay")
    expect(
        replay.get("status") == "replayed" and replay.get("current_capture_lineage_id") == second.get("capture_lineage_id"),
        "a replayed rollback must return its historical result against the new lineage",
    )
    resumed = delivered(add_todo(workspace, "Captured in the new lineage."), label="todo add (new lineage)")
    expect(_object(resumed.get("drain"), "drain").get("last_cursor") == "2", "the new lineage must capture from cursor 2")
    transactions = history(workspace)
    expect(len(transactions) == 2 and transactions[0].get("receipts") == [] and projected_todo_count(transactions[0]) == 3, "the new history must start from the imported baseline")
    return passed(
        pending_before_rollback={"committed": 1, "prepared_only": 1},
        archived_files=len(archived),
        archived_prepared=2,
        archived_committed=1,
        capture_after_rollback="bootstrap_required",
        rebootstrap_baseline_todos=3,
        new_lineage=True,
        replayed_rollback=True,
    )


@dataclass
class _MixedWriterLedger:
    workspace: GoalWorkspace
    mutations: list[tuple[str, JsonObject]] = field(default_factory=list)
    deliveries: int = 0
    no_change_writes: int = 0

    def cli(self, *args: str) -> JsonObject:
        return goal_cli(self.workspace, *args)

    def mutate(self, label: str, payload: JsonObject, *, flag: str) -> JsonObject:
        expect(payload.get(flag) is True, f"{label} must report {flag}=true")
        evidence = delivered(payload, label=label)
        # A leased complete or supersede also closes its lease fence, so one
        # CLI call may deliver two transactions; count what the drain reports.
        count = _object(evidence.get("drain"), f"{label} drain").get("delivered")
        expect(isinstance(count, int) and count >= 1, f"{label} must deliver at least one transaction")
        assert isinstance(count, int)
        self.deliveries += count
        self.mutations.append((label, evidence))
        return payload

    def unchanged(self, label: str, payload: JsonObject) -> None:
        expect(payload.get("changed") is False, f"{label} must report changed=false")
        no_transaction(payload, label=label)
        self.no_change_writes += 1


def _mixed_writer_cycle(ledger: _MixedWriterLedger, cycle: int) -> None:
    """One cycle of interleaved Python Markdown writers and TypeScript lease writers."""

    workspace = ledger.workspace
    key_a, key_b, key_c = (f"ladder-parity-{cycle}-{suffix}" for suffix in ("a", "b", "c"))
    first = ledger.mutate("todo add", add_todo(workspace, f"Cycle {cycle}: deliver one bounded change."), flag="added")
    todo_id = str(first["todo_id"])
    acquired = ledger.mutate("task-lease acquire", acquire_lease(workspace, todo_id=todo_id, owner=AGENT_A, idempotency_key=key_a), flag="acquired")
    note = ["todo", "update", "--todo-id", todo_id, "--note", f"Cycle {cycle} note.", "--agent-id", AGENT_A]
    ledger.mutate("todo update (note)", ledger.cli(*note), flag="changed")
    ledger.unchanged("todo update (same note)", ledger.cli(*note))
    renewed = ledger.mutate(
        "task-lease renew",
        ledger.cli(
            "task-lease", "renew", "--todo-id", todo_id, "--owner", AGENT_A, "--idempotency-key", key_a,
            "--expected-version", lease_version(acquired, label="acquire"), "--ttl-seconds", "120",
        ),
        flag="renewed",
    )
    transferred = ledger.mutate(
        "task-lease transfer",
        ledger.cli(
            "task-lease", "transfer", "--todo-id", todo_id, "--owner", AGENT_A, "--idempotency-key", key_a,
            "--new-owner", AGENT_B, "--new-idempotency-key", key_b,
            "--expected-version", lease_version(renewed, label="renew"), "--ttl-seconds", "120",
        ),
        flag="transferred",
    )
    ledger.mutate(
        "todo complete",
        ledger.cli(
            "todo", "complete", "--todo-id", todo_id, "--agent-id", AGENT_B,
            "--task-lease-idempotency-key", key_b, "--task-lease-expected-version", lease_version(transferred, label="transfer"),
            "--evidence", "validation://ladder-parity", "--no-follow-up",
        ),
        flag="completed",
    )
    second = ledger.mutate("todo add (second)", add_todo(workspace, f"Cycle {cycle}: replace this work with a successor."), flag="added")
    second_id = str(second["todo_id"])
    ledger.mutate(
        "todo update (explicit exclusion)",
        ledger.cli("todo", "update", "--todo-id", second_id, "--excluded-agent", AGENT_B, "--agent-id", AGENT_A),
        flag="changed",
    )
    clear = ["todo", "update", "--todo-id", second_id, "--clear-excluded-agents", "--agent-id", AGENT_A]
    ledger.mutate("todo update (explicit clear)", ledger.cli(*clear), flag="changed")
    ledger.unchanged("todo update (clear again)", ledger.cli(*clear))
    held = ledger.mutate("task-lease acquire (second)", acquire_lease(workspace, todo_id=second_id, owner=AGENT_A, idempotency_key=key_c), flag="acquired")
    ledger.mutate(
        "todo supersede",
        ledger.cli(
            "todo", "supersede", "--todo-id", second_id, "--agent-id", AGENT_A, "--reason", "Replace obsolete work.",
            "--next-agent-todo", f"Cycle {cycle}: carry the bounded work forward.",
            "--task-lease-idempotency-key", key_c, "--task-lease-expected-version", lease_version(held, label="acquire (second)"),
        ),
        flag="superseded",
    )
    ledger.mutate(
        "todo add (verification)",
        add_todo(workspace, f"Cycle {cycle}: verify the captured projection."),
        flag="added",
    )


def _assert_bounded_parity(ledger: _MixedWriterLedger, cycle: int, anchor_todo_id: str) -> JsonObject:
    workspace = ledger.workspace
    inspection = _object(inspect(workspace).get("inspection"), f"cycle {cycle} inspection")
    expect(
        inspection.get("status") == "matched" and inspection.get("parity_matches") is True,
        f"cycle {cycle}: the candidate head must match the primary "
        f"(status={inspection.get('status')!r}, reason_code={inspection.get('reason_code')!r})",
    )
    flags = ["--minimum-operations", str(ledger.deliveries)]
    for write_class in PARITY_REQUIRED_WRITE_CLASSES:
        flags.extend(["--require-event-kind", write_class])
    qualification = qualified(qualify(workspace, *flags), label=f"cycle {cycle}")
    evidence = _object(qualification.get("evidence"), f"cycle {cycle} evidence")
    expect(evidence.get("operation_count") == ledger.deliveries, f"cycle {cycle}: every delivered transaction must count as one verified operation")
    expect(evidence.get("missing_required_event_kinds") == [] and evidence.get("pending_outbox") is False, f"cycle {cycle}: coverage must be complete with nothing pending")
    read = read_candidate(workspace, anchor_todo_id)
    result = _object(read.get("read_candidate"), f"cycle {cycle} read-candidate")
    expect(
        read.get("ok") is True and result.get("status") == "matched" and result.get("read_candidate_qualified") is True,
        f"cycle {cycle}: a qualified read must return the anchor todo from the verified head",
    )
    return qualification


def row_parity_equal(context: RowContext) -> RowOutcome:
    """Sustained interleaving of Python and TypeScript writers keeps every bounded qualification matched."""

    workspace = capture_workspace(context, "ladder-parity")
    ledger = _MixedWriterLedger(workspace)
    anchor = ledger.mutate("todo add (anchor)", add_todo(workspace, "Anchor todo that stays open for qualified reads."), flag="added")
    anchor_todo_id = str(anchor["todo_id"])
    qualification: JsonObject = {}
    for cycle in range(1, PARITY_CYCLES + 1):
        _mixed_writer_cycle(ledger, cycle)
        qualification = _assert_bounded_parity(ledger, cycle, anchor_todo_id)
    transactions = history(workspace)
    expect(len(transactions) == ledger.deliveries + 1, "history must hold the bootstrap plus one transaction per delivery")
    receipts = [committed_receipt(transaction, label=f"transaction {index}") for index, transaction in enumerate(transactions[1:], start=2)]
    runtimes = sorted({str(receipt.get("writer_runtime")) for receipt in receipts})
    classes = sorted({str(receipt.get("write_class")) for receipt in receipts})
    expect(runtimes == ["python", "typescript"], "both writer runtimes must appear in one lineage")
    expect(set(PARITY_REQUIRED_WRITE_CLASSES) <= set(classes), "every required write class must be captured")
    expect(len({str(receipt.get("entry_id")) for receipt in receipts}) == len(receipts), "every receipt must carry a distinct entry id")
    return passed(
        cycles=PARITY_CYCLES,
        mutations=len(ledger.mutations),
        deliveries=ledger.deliveries,
        no_change_writes=ledger.no_change_writes,
        writer_runtimes=runtimes,
        write_classes=classes,
        final_cursor=str(qualification.get("cursor")),
        operation_count=_object(qualification.get("evidence"), "evidence").get("operation_count"),
        sustained_parity_verdict=str(qualification.get("sustained_parity_verdict")),
    )


def _recover_by_rollback_and_rebootstrap(workspace: GoalWorkspace, *, label: str) -> JsonObject:
    """The documented recovery for a held lineage: exact-revision rollback, then a fresh bootstrap."""

    revision = str(candidate(shadow_status(workspace)).get("provider_revision"))
    rollback = _object(goal_cli(workspace, "coordination-shadow", "rollback", "--provider-revision", revision, "--execute").get("rollback"), f"{label} rollback")
    expect(rollback.get("status") == "applied" and rollback.get("archive_retained") is True, f"{label}: rollback must retire the held lineage into an archive")
    rebootstrap = bootstrap_capture(workspace)
    for index in range(3):
        delivered(add_todo(workspace, f"{label} recovery write {index}."), label=f"{label} recovery write {index}")
    qualified(qualify(workspace), label=f"{label} after recovery")
    return _object(rebootstrap.get("projection_summary"), f"{label} rebootstrap summary")


def row_parity_divergent_detects_foreign_edit(context: RowContext) -> RowOutcome:
    """A direct edit of the primary is detected as drift, holds later captures, and is recovered only by rollback and rebootstrap."""

    workspace = capture_workspace(context, "ladder-drift")
    todo_ids: list[str] = []
    for index in range(3):
        added = add_todo(workspace, f"Drift baseline {index}.")
        delivered(added, label=f"todo add {index}")
        todo_ids.append(str(added["todo_id"]))
    qualified(qualify(workspace), label="baseline")
    original = workspace.state_path.read_text(encoding="utf-8")
    foreign = original.replace("handoff_mode: hard_lease", "handoff_mode: soft_claim")
    expect(foreign != original, "the foreign edit must change the primary")
    workspace.state_path.write_text(foreign, encoding="utf-8")
    inspection = _object(inspect(workspace).get("inspection"), "inspection")
    expect(
        inspection.get("status") == "drifted" and inspection.get("parity_matches") is False
        and inspection.get("reason_code") == "shadow_projection_drift" and inspection.get("qualified") is False,
        "inspect must report the drift",
    )
    rejected(qualify(workspace), "qualification", label="qualify after drift")
    read = read_candidate(workspace, todo_ids[0])
    expect(read.get("ok") is False and rejected(read, "read_candidate", label="read after drift").get("read_candidate_qualified") is False, "no candidate read may qualify after drift")
    after = add_todo(workspace, "Public write after the foreign edit.")
    expect(after.get("added") is True, "the primary write must still commit")
    held = capture_evidence(after, label="todo add (after drift)")
    expect(held.get("outcome") == "pending" and held.get("reason_code") == CONTINUITY_HOLD, "the capture must hold on unproven continuity")
    expect(_object(held.get("drain"), "held drain").get("outcome") == "stopped", "the inline drain must stop, not deliver")
    expect(backlog(shadow_status(workspace), "todos").get("committed_pending") == 1, "the held entry must stay pending")
    workspace.state_path.write_text(workspace.state_path.read_text(encoding="utf-8").replace("handoff_mode: soft_claim", "handoff_mode: hard_lease"), encoding="utf-8")
    restored = rejected(qualify(workspace), "qualification", label="qualify after restore")
    expect(restored.get("reason_code") == "outbox_pending", "restoring the bytes must not requalify a held lineage")
    stopped = drain(workspace)
    expect(
        stopped.get("ok") is False and stopped.get("outcome") == "stopped" and stopped.get("delivered") == 0 and stopped.get("reason_code") == CONTINUITY_HOLD,
        "drain must keep holding the entry rather than guess continuity",
    )
    summary = _recover_by_rollback_and_rebootstrap(workspace, label="drift")
    return passed(
        drift_status="drifted",
        drift_reason="shadow_projection_drift",
        held_write=CONTINUITY_HOLD,
        restore_requalifies=False,
        recovered_by="rollback_then_bootstrap",
        rebootstrap_baseline_todos=summary.get("todo_count"),
        recovered_qualification="qualified",
    )


def row_event_only_todo_source_holds(context: RowContext) -> RowOutcome:
    """An event-only Todo source holds qualification and candidate reads fail-closed; recovery needs rollback and rebootstrap."""

    workspace = capture_workspace(context, "ladder-event")
    todo_ids: list[str] = []
    for index in range(3):
        added = add_todo(workspace, f"Markdown baseline {index}.")
        delivered(added, label=f"todo add {index}")
        todo_ids.append(str(added["todo_id"]))
    qualified(qualify(workspace), label="baseline")
    log = workspace.state_path.with_name("events.jsonl")
    # The product's own state-event store writes the event-only source. It is
    # loaded lazily so this strictly typed ladder module does not follow the
    # untyped state-event module at type-check time.
    state_events = importlib.import_module("loopx.event_sourced_state")
    state_events.AppendOnlyStateEventStore(log).append(
        state_events.make_state_event(
            event_id="ladder-event-only-todo",
            goal_id=workspace.goal_id,
            event_type=state_events.TODO_ADDED,
            refs={"todo_id": "todo_event_only"},
            payload={"role": "agent", "title": "An event-only todo without a Markdown writer.", "task_class": "advancement_task"},
            recorded_at="2026-09-06T00:00:00+00:00",
        )
    )
    log_bytes = log.read_bytes()
    surfaces = {"inspect": inspect(workspace), "qualify": qualify(workspace), "read-candidate": read_candidate(workspace, todo_ids[0])}
    for label, payload in surfaces.items():
        surface = payload.get({"inspect": "inspection", "qualify": "qualification", "read-candidate": "read_candidate"}[label])
        expect(isinstance(surface, dict) and surface.get("parity_matches") is False, f"{label} must reject uncaptured event source drift")
    status = shadow_status(workspace)
    expect(status.get("ok") is True and management_status(status) == "active", "status must stay readable while the lineage is held")
    during = add_todo(workspace, "Markdown write during the event-only hold.")
    expect(during.get("added") is True, "the primary write must still commit")
    held = capture_evidence(during, label="todo add (during hold)")
    expect(held.get("outcome") == "pending" and held.get("reason_code") == CONTINUITY_HOLD, "the capture must hold on unproven continuity")
    expect(log.read_bytes() == log_bytes, "the hold must not touch the event log")
    expect(backlog(shadow_status(workspace), "todos").get("committed_pending") == 1, "the held entry must stay pending")
    log.unlink()
    removed = rejected(qualify(workspace), "qualification", label="qualify after removal")
    expect(removed.get("reason_code") == "outbox_pending", "removing the event source must not requalify the held lineage")
    stopped = drain(workspace)
    expect(stopped.get("outcome") == "stopped" and stopped.get("reason_code") == CONTINUITY_HOLD, "drain must keep holding the entry")
    summary = _recover_by_rollback_and_rebootstrap(workspace, label="event-only")
    return passed(
        hold=EVENT_ONLY_HOLD,
        held_surfaces=sorted(surfaces),
        primary_write_during_hold=CONTINUITY_HOLD,
        event_log_untouched=True,
        removal_requalifies=False,
        recovered_by="rollback_then_bootstrap",
        rebootstrap_baseline_todos=summary.get("todo_count"),
    )


@dataclass(frozen=True)
class _MigrationTarget:
    """The target registry, repository and runtime root of one migration."""

    registry_path: Path
    repo: Path
    runtime_root: Path
    home: Path
    goal_id: str

    def cli_prefix(self) -> list[str]:
        return ["--registry", str(self.registry_path), "--runtime-root", str(self.runtime_root), "--format", "json"]


MIGRATION_SENTINEL = b'{"schema_version":"existing","goals":[]}\n'


def _migration_target(context: RowContext, legacy: GoalWorkspace) -> _MigrationTarget:
    root = context.root / "migration-target"
    repo = root / "repo"
    repo.mkdir(parents=True)
    registry_path = root / "registry.json"
    registry_path.write_bytes(MIGRATION_SENTINEL)
    return _MigrationTarget(registry_path=registry_path, repo=repo, runtime_root=root / "runtime", home=legacy.home, goal_id=unique_goal_id("migrated"))


def _migration_arguments(legacy: GoalWorkspace, target: _MigrationTarget) -> list[str]:
    return [
        "migrate-state",
        "--legacy-registry", str(legacy.registry_path),
        "--legacy-runtime-root", str(legacy.runtime_root),
        "--target-runtime-root", str(target.runtime_root),
        "--goal-id", legacy.goal_id,
        "--goal-id-map", f"{legacy.goal_id}={target.goal_id}",
        "--path-map", f"{legacy.repo}={target.repo}",
        "--copy-active-state",
        "--copy-runtime",
        "--no-global-sync",
    ]


def _set_target_runtime_shadow(target: _MigrationTarget, *, enabled: bool) -> JsonObject:
    registry = parse_json_object(target.registry_path.read_text(encoding="utf-8"))
    goals = [_object(goal, "target goal") for goal in _list(registry.get("goals"), "target goals")]
    expect(len(goals) == 1 and goals[0].get("id") == target.goal_id, "the target registry must carry exactly the migrated goal")
    coordination = _object(goals[0].get("coordination"), "target coordination")
    carried = _object(coordination.get("runtime_shadow"), "carried runtime_shadow")
    coordination["runtime_shadow"] = {**carried, "enabled": enabled}
    goals[0]["coordination"] = coordination
    registry["goals"] = goals
    target.registry_path.write_text(json.dumps(registry), encoding="utf-8")
    return carried


def row_migration_seeds_and_drains(context: RowContext) -> RowOutcome:
    """migrate-state refuses an active capture source; after rollback the migrated goal bootstraps a fresh lineage that drains."""

    legacy = capture_workspace(context, "ladder-legacy", bootstrap=False)
    first = _object(bootstrap_capture(legacy).get("bootstrap"), "legacy bootstrap")
    delivered(add_todo(legacy, "Captured before migration."), label="legacy todo add")
    documents = store_documents(legacy)
    expect(len(documents) == 1, "the legacy runtime must hold one candidate document")
    legacy_bytes = documents[0].read_bytes()
    target = _migration_target(context, legacy)
    arguments = _migration_arguments(legacy, target)
    preview = run_cli(target, *arguments, check=False)
    expect(preview.get("ok") is True and preview.get("dry_run") is True and target.registry_path.read_bytes() == MIGRATION_SENTINEL, "a dry run must plan without writing")
    refused = run_cli(target, *arguments, "--execute", check=False)
    expect(
        refused.get("ok") is False and refused.get("error_code") == "shadow_source_replacement_requires_rebootstrap",
        "an active capture source must refuse a generic state rebuild",
    )
    expect(target.registry_path.read_bytes() == MIGRATION_SENTINEL and documents[0].read_bytes() == legacy_bytes, "a refused migration must write nothing")
    set_runtime_shadow(legacy, enabled=False)
    disabled_only = run_cli(target, *arguments, "--execute", check=False)
    expect(disabled_only.get("error_code") == "shadow_source_replacement_requires_rebootstrap", "disabling capture alone must not release an active lineage")
    set_runtime_shadow(legacy, enabled=True)
    revision = str(candidate(shadow_status(legacy)).get("provider_revision"))
    rollback = _object(goal_cli(legacy, "coordination-shadow", "rollback", "--provider-revision", revision, "--execute").get("rollback"), "legacy rollback")
    expect(rollback.get("status") == "applied" and rollback.get("archive_retained") is True, "the legacy lineage must be retired into an archive first")
    set_runtime_shadow(legacy, enabled=False)
    executed = run_cli(target, *arguments, "--execute")
    expect(executed.get("ok") is True and executed.get("wrote_project_registry") is True, "the migration must execute after rollback")
    runtime_goal = _object(_list(executed.get("runtime_goals"), "runtime goals")[0], "runtime goal")
    expect(runtime_goal.get("copied") is True, "the runtime goal directory must be copied")
    expect(executed.get("authority_shadow_seeds") == [], "a capture-only goal plans no observation seed")
    expect((target.repo / legacy.state_path.name).exists(), "the active state must be copied to the mapped repository")
    carried = _set_target_runtime_shadow(target, enabled=True)
    expect(carried.get("enabled") is False and carried.get("provider") == RUNTIME_SHADOW_PROVIDER, "the migrated goal must carry its disabled capture configuration")
    inspection = _object(run_cli(target, "coordination-shadow", "inspect", "--goal-id", target.goal_id, check=False).get("inspection"), "target inspection")
    expect(inspection.get("status") == "missing" and inspection.get("bootstrap_required") is True, "the migrated goal must require its own bootstrap")
    bootstrap_payload = run_cli(target, "coordination-shadow", "bootstrap", "--goal-id", target.goal_id, "--execute")
    bootstrap = _object(bootstrap_payload.get("bootstrap"), "target bootstrap")
    expect(bootstrap.get("status") == "applied" and bootstrap.get("capture_lineage_id") != first.get("capture_lineage_id"), "the target must seed a fresh lineage")
    summary = _object(bootstrap_payload.get("projection_summary"), "target projection summary")
    expect(summary.get("todo_count") == 1 and summary.get("lease_count") == 0, "the fresh lineage must import the migrated baseline")
    added = run_cli(target, "todo", "add", "--goal-id", target.goal_id, "--role", "agent", "--text", "Captured in the migrated runtime root.", "--task-class", "advancement_task")
    drained = _object(delivered(added, label="target todo add").get("drain"), "target drain")
    expect(drained.get("last_cursor") == "2", "the first migrated write must drain to cursor 2")
    qualification = _object(run_cli(target, "coordination-shadow", "qualify", "--goal-id", target.goal_id, "--minimum-operations", "1", check=False).get("qualification"), "target qualification")
    expect(qualification.get("status") == "qualified" and _object(qualification.get("evidence"), "evidence").get("operation_count") == 1, "the migrated lineage must qualify on its own write")
    archive = legacy.runtime_root / "authority-shadow" / "file-v0" / "rollback"
    expect(archive.is_dir() and any(archive.iterdir()), "the legacy archive must be retained")
    return passed(
        preview_dry_run=True,
        active_capture_refused="shadow_source_replacement_requires_rebootstrap",
        disabled_config_still_refused=True,
        executed_after_rollback=True,
        observation_seeds=0,
        migrated_baseline_todos=1,
        new_lineage=True,
        drained_cursor="2",
        legacy_archive_retained=True,
    )


def row_growth_measurement_gate(context: RowContext) -> RowOutcome:
    """Measure file-v0 history growth per transaction and gate its integrity; no capacity horizon is claimed."""

    workspace = capture_workspace(context, "ladder-growth")
    status = shadow_status(workspace)
    sizes = [int(str(status.get("store_bytes")))]
    cursors = [str(candidate(status).get("cursor"))]
    for index in range(GROWTH_TRANSACTIONS):
        delivered(add_todo(workspace, GROWTH_TEXT_TEMPLATE % index), label=f"growth write {index}")
        status = shadow_status(workspace)
        expect(status.get("retention_pressure") is False, "the bounded workload must not trip retention pressure")
        sizes.append(int(str(status.get("store_bytes"))))
        cursors.append(str(candidate(status).get("cursor")))
    expect(cursors == [str(index) for index in range(1, GROWTH_TRANSACTIONS + 2)], "every write must advance the cursor by exactly one")
    deltas = [after - before for before, after in zip(sizes, sizes[1:])]
    expect(all(delta > 0 for delta in deltas), "every transaction must grow the retained history")
    accelerations = [later - earlier for earlier, later in zip(deltas, deltas[1:])]
    expect(
        max(abs(value) for value in accelerations) <= GROWTH_DELTA_ACCELERATION_ENVELOPE_BYTES,
        "per-transaction growth may increase by at most one live record per transaction",
    )
    transactions = history(workspace)
    expect(len(transactions) == GROWTH_TRANSACTIONS + 1, "history must retain every transaction")
    expect(
        [projected_todo_count(transaction) for transaction in transactions] == list(range(GROWTH_TRANSACTIONS + 1)),
        "every transaction must retain its complete projection",
    )
    final = sizes[-1]
    cumulative = sum(sizes)
    return passed(
        transactions=GROWTH_TRANSACTIONS + 1,
        final_history_bytes=final,
        cumulative_publication_bytes=cumulative,
        publication_rewrite_ratio=round(cumulative / final, 2),
        first_delta_bytes=deltas[0],
        last_delta_bytes=deltas[-1],
        delta_acceleration_max_bytes=max(abs(value) for value in accelerations),
        delta_acceleration_envelope_bytes=GROWTH_DELTA_ACCELERATION_ENVELOPE_BYTES,
        retention_pressure=False,
        capacity_verdict="not_evaluated",
        qualification_horizon="not_claimed",
    )


def row_archive_after_leased_completion_parity(context: RowContext) -> RowOutcome:
    """Archiving a Todo whose released lease stays on disk keeps the candidate head matched and qualifiable."""

    workspace = capture_workspace(context, "ladder-archive-leased")
    leased = add_todo(workspace, "Leased todo archived after its completion is captured.")
    leased_todo_id = str(leased["todo_id"])
    delivered(leased, label="todo add (leased)")
    acquired = acquire_lease(workspace, todo_id=leased_todo_id, owner=AGENT_A, idempotency_key="ladder-archive-leased-a")
    delivered(acquired, label="task-lease acquire")
    completed = goal_cli(
        workspace, "todo", "complete", "--todo-id", leased_todo_id, "--agent-id", AGENT_A,
        "--task-lease-idempotency-key", "ladder-archive-leased-a",
        "--task-lease-expected-version", lease_version(acquired, label="acquire"),
        "--evidence", "validation://ladder-archive-leased", "--no-follow-up",
    )
    delivered(completed, label="todo complete")
    anchor = add_todo(workspace, "Anchor todo that stays open across the archive write.")
    anchor_todo_id = str(anchor["todo_id"])
    delivered(anchor, label="todo add (anchor)")
    qualified(qualify(workspace), label="baseline")
    archived = goal_cli(workspace, "todo", "archive-completed", "--role", "agent", "--max-active-done", "0", "--execute")
    expect(archived.get("changed") is True and archived.get("moved_count") == 1, "archive-completed must move exactly the completed todo")
    inspection = _object(inspect(workspace).get("inspection"), "post-archive inspection")
    expect(
        inspection.get("status") == "matched" and inspection.get("parity_matches") is True,
        "archiving a Todo whose released lease remains on disk must not orphan that lease in the candidate head",
    )
    expect(inspection.get("reason_code") is None, "a matched archive must report no drift reason")
    # Require exactly the event kinds this row delivers, including the archive
    # that retires the Todo from the graph while its released lease file stays
    # on disk as audit history. Reusing the mixed-writer parity set here would
    # demand writers this row never drives.
    flags = ["--minimum-operations", str(ARCHIVE_PARITY_OPERATIONS)]
    for write_class in ARCHIVE_PARITY_REQUIRED_WRITE_CLASSES:
        flags.extend(["--require-event-kind", write_class])
    qualification = qualified(qualify(workspace, *flags), label="post-archive")
    read = read_candidate(workspace, anchor_todo_id)
    expect(read.get("ok") is True, "a qualified read must remain available after the archive write")
    lease_dir = workspace.runtime_root / "goals" / workspace.goal_id / "task-leases"
    lease_names = sorted(path.name for path in lease_dir.glob("*.json"))
    expect(f"{leased_todo_id}.json" in lease_names, "the released lease file must stay on disk as audit history")

    # The second boundary the same rule covers: a later lease write must not
    # re-read the retained lease of the archived Todo into its own partition.
    successor = add_todo(workspace, "Successor todo that takes a fresh lease after the archive.")
    successor_todo_id = str(successor["todo_id"])
    delivered(successor, label="todo add (successor)")
    successor_lease = acquire_lease(
        workspace, todo_id=successor_todo_id, owner=AGENT_A, idempotency_key="ladder-archive-leased-b",
    )
    delivered(successor_lease, label="task-lease acquire (successor)")
    after_successor = _object(inspect(workspace).get("inspection"), "post-successor inspection")
    expect(
        after_successor.get("status") == "matched" and after_successor.get("parity_matches") is True,
        "a lease write after the archive must not inherit the retained lease of the archived Todo",
    )
    # Completion closes the lease fence in a separate native request. It must
    # retain the same current-graph rule as acquire, not reintroduce audit leases.
    successor_completed = goal_cli(
        workspace, "todo", "complete", "--todo-id", successor_todo_id, "--agent-id", AGENT_A,
        "--task-lease-idempotency-key", "ladder-archive-leased-b",
        "--task-lease-expected-version", lease_version(successor_lease, label="successor acquire"),
        "--evidence", "validation://ladder-successor-complete", "--no-follow-up",
    )
    delivered(successor_completed, label="todo complete (successor)")
    drained = drain(workspace)
    expect(drained.get("ok") is True and drained.get("pending_after") == 0,
           "successor fence-close must leave no unprovable lease partition")
    qualified(qualify(workspace), label="post-successor completion")
    expect(read_candidate(workspace, anchor_todo_id).get("ok") is True,
           "candidate reads must survive successor fence-close")
    final_lease_names = sorted(path.name for path in lease_dir.glob("*.json"))
    expect(
        {f"{leased_todo_id}.json", f"{successor_todo_id}.json"} <= set(final_lease_names),
        "both the archived Todo's audit lease and the successor's live lease must remain on disk",
    )
    return passed(
        archived_todo=leased_todo_id,
        retained_lease_files=len(lease_names),
        parity_status="matched",
        parity_reason=None,
        qualification_cursor=str(qualification.get("cursor")),
        anchor_read_qualified=True,
        successor_todo=successor_todo_id,
        post_successor_parity="matched",
        final_lease_files=len(final_lease_names),
    )

__all__ = [
    "CRASH_WORKER",
    "DRAIN_CRASH_WINDOWS",
    "GROWTH_DELTA_ACCELERATION_ENVELOPE_BYTES",
    "GROWTH_TRANSACTIONS",
    "PARITY_CYCLES",
    "PARITY_REQUIRED_WRITE_CLASSES",
    "PRIMARY_CRASH_WINDOWS",
    "row_archive_after_leased_completion_parity",
    "row_drain_idempotent",
    "row_event_only_todo_source_holds",
    "row_growth_measurement_gate",
    "row_migration_seeds_and_drains",
    "row_outbox_prepared_then_committed_entries",
    "row_parity_divergent_detects_foreign_edit",
    "row_parity_equal",
    "row_rollback_with_pending_entries",
    "row_sigkill_between_primary_write_and_drain",
    "row_sigkill_mid_drain",
]
