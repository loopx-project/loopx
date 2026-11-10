"""Controller-only CLI activation at an independently observed idle boundary.

The running Host retains its imported code. Only its owned CLI symlink changes;
work, native sessions, authority, model configuration and phase files stay put.
No controller mailbox or checkpoint capture identity is consumed by activation.
"""
from __future__ import annotations

from collections.abc import Callable
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import math
import os
import stat
import tarfile
import time

from loopx.control_plane.effect_runtime import effect_runtime_result
from loopx.file_lock import exclusive_file_lock, write_json_atomic
from .checkpoint_worker import _worker_lock
from .cli_source_probe import _archive_response, source_goal_control


class SourceActivationHeld(RuntimeError):
    """Reconcile this exact intent without replaying activation or dispatch."""


def _path(value: str) -> str:
    path = PurePosixPath(value)
    if not path.is_absolute() or str(path) != value or ".." in path.parts:
        raise ValueError("Activation paths must be normalized and absolute")
    return value


def _link_record(container, path: str) -> dict:
    """Read the symlink itself through the daemon, including while paused."""
    from docker.errors import NotFound

    blocks, length = [], 0
    try:
        with _archive_response(container, path) as (chunks, _):
            for block in chunks:
                length += len(block)
                if length > 128 * 1024:
                    raise ValueError("Owned CLI launcher archive exceeds the symlink bound")
                blocks.append(block)
    except NotFound:
        return {}
    with io.BytesIO(b"".join(blocks)) as data, tarfile.open(fileobj=data) as archive:
        members = archive.getmembers()
        if len(members) != 1 or not members[0].issym():
            return {}
        member = members[0]
        return {"launcher_target": _path(member.linkname), "launcher_uid": member.uid,
                "launcher_gid": member.gid, "launcher_mode": member.mode & 0o777}


def _link(container, path: str) -> str | None:
    return _link_record(container, path).get("launcher_target")


def _put_link(container, path: str, target: str, *, uid: int = 0, gid: int = 0) -> None:
    """Dispatch-atomic only under our physical pause, not an atomic rename.

    A partial stream or lost acknowledgement is resolved by _link before release.
    No code is executed inside the paused container.
    """
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w") as archive:
        entry = tarfile.TarInfo(PurePosixPath(path).name)
        entry.type, entry.linkname, entry.mode = tarfile.SYMTYPE, target, 0o777
        entry.uid, entry.gid = uid, gid
        archive.addfile(entry)
    payload.seek(0)
    if not container.put_archive(str(PurePosixPath(path).parent), payload):
        raise RuntimeError("CLI launcher transport did not confirm the write")


def _validate_plan(plan: dict) -> None:
    fields = {"activation_id", "container_id", "started_at", "goal_id", "agent_id", "todo_id", "turn_instance_id", "session_id",
              "registry_path", "launcher", "launcher_uid", "launcher_gid", "old_target", "new_target", "release_root", "target_revision",
              "target_archive_sha256", "worker_lock"}
    if not isinstance(plan, dict) or set(plan) != fields:
        raise ValueError("CLI activation needs its exact source/release declaration")
    for field, length in (("activation_id", 32), ("container_id", 64),
                          ("target_revision", 40), ("target_archive_sha256", 64)):
        if not isinstance(plan[field], str) or not re.fullmatch(f"[0-9a-f]{{{length}}}", plan[field]):
            raise ValueError("Invalid CLI activation identity or digest")
    for field in ("launcher_uid", "launcher_gid"):
        if type(plan[field]) is not int or plan[field] < 0:
            raise ValueError("CLI activation requires the original launcher ownership")
    for field in fields - {"activation_id", "container_id", "target_revision", "target_archive_sha256", "launcher_uid", "launcher_gid"}:
        if not isinstance(plan[field], str) or not plan[field]:
            raise ValueError("CLI activation source fields must be explicit")
    for field in ("registry_path", "launcher", "old_target", "new_target", "release_root", "worker_lock"):
        _path(plan[field])
    root = PurePosixPath(plan["release_root"])
    for field in ("old_target", "new_target"):
        relative = PurePosixPath(plan[field]).relative_to(root)
        if len(relative.parts) != 3 or relative.parts[1:] != ("scripts", "loopx"):
            raise ValueError("CLI activation needs an owned versioned launcher")
    if (plan["old_target"] == plan["new_target"] or
            root in PurePosixPath(plan["launcher"]).parents):
        raise ValueError("CLI activation must select a distinct owned versioned release")


def _state(container, plan):
    container.reload()
    state = container.attrs["State"]
    if container.id != plan["container_id"] or state["StartedAt"] != plan["started_at"]:
        raise SourceActivationHeld("Original source namespace identity changed")
    return state


def _held_state(container, plan):
    state = _state(container, plan)
    if not state["Running"] or not state["Paused"]:
        raise SourceActivationHeld("Original writer barrier changed; no launcher effect is authorized")
    return state


def _goal_control(container, plan):
    try:
        return source_goal_control(container, plan, plan["registry_path"])
    except Exception:
        return "unknown"  # An unavailable owner cannot authorize a write/release.


def _historical(value):
    return {**value, "historical_receipt": True, "replayed": True,
            "current_source_verified": False, "model_receipt_adoption": "unobserved"}


def activate_cli_source(client, *, plan: dict, receipt: Path,
                        preflight: Callable, probe: Callable, witness: Callable, verify_release: Callable,
                        control: Callable[[], str], execute: bool = False,
                        reconcile: bool = False, repair: str | None = None,
                        timeout: float = 120) -> dict:
    """Change one launcher; callbacks are source/native owners, never model calls.

    ``probe`` observes a paused source using an isolated reader. ``witness``
    observes exactly that probe's declared source participants. ``verify_release``
    verifies the staged immutable bytes against the admitted archive. A controller
    supplies its current ordinary Stop observation before each effect/release.
    None of these observations grants Goal, account or solver admission authority.
    """
    _validate_plan(plan)
    if (type(execute) is not bool or type(reconcile) is not bool or
            not math.isfinite(timeout) or timeout <= 0 or
            repair not in (None, "old", "new") or (repair is not None and not reconcile)):
        raise ValueError("CLI activation requires explicit mode and finite timeout")
    if not execute:
        return {"planned": True, "source_change_allowed": False, "execution_authority_granted": False,
                "plan_sha256": hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()}
    if receipt.parent.resolve() != receipt.parent or receipt.is_symlink():
        raise ValueError("Activation receipt must be controller-owned and physical")
    parent = receipt.parent.stat()
    if parent.st_uid != os.getuid() or parent.st_mode & 0o077:
        raise ValueError("Activation receipt requires its private controller directory")
    box = client.containers.get(plan["container_id"])
    lock = _worker_lock(client, box.id)
    if str(lock) != plan["worker_lock"]:
        raise ValueError("Activation must share the original Host's physical worker-lock realm")
    with exclusive_file_lock(lock, timeout_seconds=timeout, operation="cli_source_activation"):
        if reconcile:
            info = receipt.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                    info.st_nlink != 1 or info.st_mode & 0o077):
                raise ValueError("Activation recovery requires its private physical receipt")
            value = json.loads(receipt.read_bytes())
            if value.get("schema") != "loopx_cli_source_activation_v0" or value.get("plan") != plan:
                raise ValueError("Activation recovery differs from its original intent")
            if value.get("phase") == "released":
                return _historical(value)  # Never pause or replay a released source.
        else:
            if receipt.exists():
                raise ValueError("CLI activation intent already exists; reconcile the original")
            state = _state(box, plan)
            if not state["Running"] or state["Paused"]:
                raise ValueError("CLI activation needs its exact running, unpaused source")
            expected_link = {"launcher_target": plan["old_target"], "launcher_uid": plan["launcher_uid"],
                             "launcher_gid": plan["launcher_gid"], "launcher_mode": 0o777}
            if control() != "continue" or _link_record(box, plan["launcher"]) != expected_link:
                return {"applied": False, "reason": "control_or_launcher_changed", "execution_authority_granted": False}
            initial = preflight(box, plan)
            initial.update(container_id=box.id, started_at=state["StartedAt"], running=True,
                           paused=False, control=control(), source_control=_goal_control(box, plan))
            initial_decision = effect_runtime_result("runtime.cli_source_activation", {
                "operation": "preflight", "scope": plan, "observation": initial})
            if not initial_decision.get("pause_allowed", False):
                return {"applied": False, "reason": initial_decision["reason"],
                        "execution_authority_granted": False}
            verify_release(box, plan)
            value = {"schema": "loopx_cli_source_activation_v0", "plan": plan,
                     "phase": "pause_requested", "prepared_at_epoch": time.time(),
                     "execution_authority_granted": False, "work_or_clock_mutations": 0}
            write_json_atomic(receipt, value, mode=0o600, fsync_directory=True)
            try:
                box.pause()
            except Exception:
                # A rejected/uncertain pause never authorizes release of somebody
                # else's barrier. Preserve the intent for explicit observation.
                value["phase"] = "pause_uncertain"
                write_json_atomic(receipt, value, mode=0o600, fsync_directory=True)
                raise SourceActivationHeld("Pause acknowledgement unavailable; inspect original source") from None
            value.update(phase="paused", paused_at_epoch=time.time())
            write_json_atomic(receipt, value, mode=0o600, fsync_directory=True)
        state = _state(box, plan)
        if value["phase"] == "release_requested" and state["Running"] and not state["Paused"]:
            target = plan["new_target"] if value["released_target"] == "new" else plan["old_target"]
            if _link_record(box, plan["launcher"]) != {"launcher_target": target, "launcher_uid": plan["launcher_uid"],
                    "launcher_gid": plan["launcher_gid"], "launcher_mode": 0o777}:
                raise SourceActivationHeld("Released source launcher changed; no effect was replayed")
            value.update(phase="released", release_observed_at_epoch=time.time(),
                         applied=value["released_target"] == "new", model_receipt_adoption="unobserved")
            write_json_atomic(receipt, value, mode=0o600, fsync_directory=True)
            return _historical(value)
        if not state["Running"] or not state["Paused"] or value["phase"] == "pause_uncertain":
            raise SourceActivationHeld("Original owned pause is not confirmed")
        if "qualification" not in value:
            facts = probe(box, plan)
            state = _held_state(box, plan)
            facts.update(container_id=box.id, started_at=state["StartedAt"], running=True,
                         paused=True, owned_barrier=True, control=control(),
                         source_control=_goal_control(box, plan),
                         **_link_record(box, plan["launcher"]))
            decision = effect_runtime_result("runtime.cli_source_activation", {
                "operation": "qualify", "scope": plan, "observation": facts})
            value.update(phase="qualified" if decision["qualified"] else "deferred", qualification=decision,
                         source_witness=facts["source_witness_after"],
                         source_stopped=facts.get("source_stopped", False))
            write_json_atomic(receipt, value, mode=0o600, fsync_directory=True)
            if decision["qualified"]:
                verify_release(box, plan)
                if control() != "continue" or _goal_control(box, plan) != "continue":
                    raise SourceActivationHeld("Current control stopped before the launcher write")
                _held_state(box, plan)
                value["phase"] = "switch_requested"
                write_json_atomic(receipt, value, mode=0o600, fsync_directory=True)
                try:
                    _put_link(box, plan["launcher"], plan["new_target"], uid=plan["launcher_uid"], gid=plan["launcher_gid"])
                except Exception:
                    value["write_acknowledgement_lost"] = True
                    write_json_atomic(receipt, value, mode=0o600, fsync_directory=True)
        if repair is not None:
            state = _held_state(box, plan)
            repair_decision = effect_runtime_result("runtime.cli_source_activation", {
                "operation": "repair", "scope": plan, "observation": {
                    "container_id": box.id, "started_at": state["StartedAt"],
                    "running": True, "paused": True, "owned_barrier": True, "control": control(),
                    "source_control": _goal_control(box, plan),
                    "source_stopped": value.get("source_stopped", False), "repair_target": repair,
                    "qualified_source": value.get("qualification", {}).get("qualified") is True,
                    "unchanged_source_witness": witness(box, plan) == value["source_witness"]}})
            if repair_decision.get("source_change_allowed") is not True:
                raise SourceActivationHeld("Explicit launcher repair lacks its unchanged qualified source")
            verify_release(box, plan)
            value.update(phase="repair_requested", repair_target=repair)
            write_json_atomic(receipt, value, mode=0o600, fsync_directory=True)
            _put_link(box, plan["launcher"], plan["old_target"] if repair == "old" else plan["new_target"],
                      uid=plan["launcher_uid"], gid=plan["launcher_gid"])
        if control() != "continue":
            raise SourceActivationHeld("Current control does not permit source release")
        state = _held_state(box, plan)
        decision = effect_runtime_result("runtime.cli_source_activation", {
            "operation": "readback", "scope": plan, "observation": {
                "container_id": box.id, "started_at": state["StartedAt"],
                "running": True, "paused": True, "owned_barrier": True, "control": control(),
                "source_control": _goal_control(box, plan),
                "source_stopped": value.get("source_stopped", False),
                "qualified_source": value.get("qualification", {}).get("qualified") is True,
                **_link_record(box, plan["launcher"]),
                "unchanged_source_witness": witness(box, plan) == value["source_witness"]}})
        value["readback"] = decision
        write_json_atomic(receipt, value, mode=0o600, fsync_directory=True)
        if not decision["resume_allowed"]:
            raise SourceActivationHeld("Launcher/source readback is uncertain; original source remains paused")
        value.update(phase="release_requested", released_target=decision["disposition"])
        write_json_atomic(receipt, value, mode=0o600, fsync_directory=True)
        _held_state(box, plan)
        if control() != "continue" or _goal_control(box, plan) != "continue":
            raise SourceActivationHeld("Current control stopped before releasing the original source")
        box.unpause()
        state = _state(box, plan)
        if state["Paused"] or not state["Running"]:
            raise SourceActivationHeld("Source release is not confirmed")
        value.update(phase="released", release_observed_at_epoch=time.time(),
                     applied=decision["disposition"] == "new",
                     pause_wall_seconds=time.time() - value["paused_at_epoch"],
                     model_receipt_adoption="unobserved")
        write_json_atomic(receipt, value, mode=0o600, fsync_directory=True)
        return value
