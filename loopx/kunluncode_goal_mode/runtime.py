"""Recoverable LoopX controller for KunlunCode native Goal and Goal Pro."""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loopx.file_lock import exclusive_file_lock
from loopx.control_plane.goals.first_party_host_admission import (
    FirstPartyHostGoalAdmission,
    capture_first_party_host_goal_ref,
)
from loopx.kunluncode_goal_mode.app_server import (
    NATIVE_GOAL_MODES,
    KunlunAppServerClient,
    KunlunAppServerError,
    build_app_server_command,
    compact_goal,
    native_goal_success,
    stable_text_digest,
)
from loopx.kunluncode_goal_mode.control_plane import (
    VERIFIED_CLASSIFICATIONS,
    KunlunNativeGoalRuntimeError,
    LoopXControlPlane,
)
from loopx.kunluncode_goal_mode.guards import (
    NATIVE_CONTROLLER_AGENT_ENV,
    NATIVE_CONTROLLER_ENV,
    NATIVE_CONTROLLER_GOAL_ENV,
)
from loopx.registry import atomic_write_json


RUNTIME_STATE_SCHEMA_VERSION = "loopx_kunluncode_runtime_v0"
RUNTIME_STATE_FILENAME = "kunluncode-runtime.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def runtime_state_path(project: str | Path) -> Path:
    return Path(project) / ".loopx" / RUNTIME_STATE_FILENAME


def _validate_runtime_state_payload(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != RUNTIME_STATE_SCHEMA_VERSION:
        raise KunlunNativeGoalRuntimeError(
            "KunlunCode runtime journal has an unsupported schema"
        )
    binding = payload.get("binding")
    native = payload.get("native")
    writeback = payload.get("writeback")
    if not isinstance(binding, dict):
        raise KunlunNativeGoalRuntimeError(
            "KunlunCode runtime journal is missing a binding object"
        )
    if not isinstance(native, dict):
        raise KunlunNativeGoalRuntimeError(
            "KunlunCode runtime journal is missing a native state object"
        )
    if not isinstance(writeback, dict):
        raise KunlunNativeGoalRuntimeError(
            "KunlunCode runtime journal is missing a writeback state object"
        )
    if not all(str(binding.get(key) or "").strip() for key in ("goal_id", "agent_id")):
        raise KunlunNativeGoalRuntimeError(
            "KunlunCode runtime journal has an incomplete goal or agent binding"
        )
    if not str(payload.get("todo_id") or "").strip():
        raise KunlunNativeGoalRuntimeError(
            "KunlunCode runtime journal is missing its todo identity"
        )
    if native.get("mode") not in NATIVE_GOAL_MODES:
        raise KunlunNativeGoalRuntimeError(
            "KunlunCode runtime journal has an unsupported native goal mode"
        )
    objective_digest = str(native.get("objective_sha256") or "")
    if len(objective_digest) != 64 or any(
        character not in "0123456789abcdef" for character in objective_digest
    ):
        raise KunlunNativeGoalRuntimeError(
            "KunlunCode runtime journal has an invalid objective digest"
        )
    for key in ("verification_passed", "verified", "resumed"):
        if not isinstance(native.get(key), bool):
            raise KunlunNativeGoalRuntimeError(
                f"KunlunCode runtime journal native field {key!r} must be boolean"
            )
    for key in ("delivery_recorded", "todo_completed", "quota_spent"):
        if not isinstance(writeback.get(key), bool):
            raise KunlunNativeGoalRuntimeError(
                f"KunlunCode runtime journal writeback field {key!r} must be boolean"
            )
    if writeback["todo_completed"] and not writeback["delivery_recorded"]:
        raise KunlunNativeGoalRuntimeError(
            "KunlunCode runtime journal records todo completion before delivery"
        )
    if writeback["quota_spent"] and not writeback["todo_completed"]:
        raise KunlunNativeGoalRuntimeError(
            "KunlunCode runtime journal records quota spend before todo completion"
        )
    if any(writeback.values()) and native["verified"] is not True:
        raise KunlunNativeGoalRuntimeError(
            "KunlunCode runtime journal records writeback before native verification"
        )
    if native["verified"] and native.get("status") != "complete":
        raise KunlunNativeGoalRuntimeError(
            "KunlunCode runtime journal marks a non-complete native goal verified"
        )
    if (
        native["verified"]
        and native.get("mode") == "goal-pro"
        and native["verification_passed"] is not True
    ):
        raise KunlunNativeGoalRuntimeError(
            "KunlunCode Goal Pro runtime journal is verified without verifier proof"
        )


def read_runtime_state(project: str | Path) -> dict[str, Any] | None:
    path = runtime_state_path(project)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KunlunNativeGoalRuntimeError(
            f"KunlunCode runtime journal is unreadable: {path}"
        ) from exc
    if not isinstance(payload, dict):
        raise KunlunNativeGoalRuntimeError(
            "KunlunCode runtime journal is not an object"
        )
    _validate_runtime_state_payload(payload)
    return payload


def write_runtime_state(project: str | Path, state: dict[str, Any]) -> Path:
    state["updated_at"] = _now()
    _validate_runtime_state_payload(state)
    path = runtime_state_path(project)
    atomic_write_json(path, state)
    return path


def _writeback_state(state: Mapping[str, Any]) -> dict[str, Any]:
    value = state.get("writeback")
    return dict(value) if isinstance(value, Mapping) else {}


def runtime_phase(state: Mapping[str, Any]) -> str:
    native = state.get("native") if isinstance(state.get("native"), Mapping) else {}
    writeback = _writeback_state(state)
    if writeback.get("quota_spent") is True:
        return "committed"
    if writeback.get("todo_completed") is True:
        return "todo_completed"
    if writeback.get("delivery_recorded") is True:
        return "delivery_recorded"
    if native.get("verified") is True:
        return "native_verified"
    if native.get("thread_id"):
        return "native_active"
    return "planned"


def compact_runtime_state(state: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(state, Mapping):
        return None
    binding = state.get("binding") if isinstance(state.get("binding"), Mapping) else {}
    native = state.get("native") if isinstance(state.get("native"), Mapping) else {}
    writeback = _writeback_state(state)
    return {
        "schema_version": state.get("schema_version"),
        "phase": runtime_phase(state),
        "goal_id": binding.get("goal_id"),
        "agent_id": binding.get("agent_id"),
        "todo_id": state.get("todo_id"),
        "mode": native.get("mode"),
        "thread_id": native.get("thread_id"),
        "native_goal_id": native.get("goal_id"),
        "native_status": native.get("status"),
        "verification_passed": native.get("verification_passed") is True,
        "resumed": native.get("resumed") is True,
        "delivery_recorded": writeback.get("delivery_recorded") is True,
        "todo_completed": writeback.get("todo_completed") is True,
        "quota_spent": writeback.get("quota_spent") is True,
        "updated_at": state.get("updated_at"),
    }


def build_native_goal_objective(todo: Mapping[str, Any], *, agent_id: str) -> str:
    todo_id = str(todo.get("todo_id") or "").strip()
    text = str(todo.get("text") or "").strip()
    if not todo_id or not text:
        raise KunlunNativeGoalRuntimeError(
            "LoopX selected todo must include a stable todo_id and non-empty text"
        )
    scopes = [
        str(item).strip()
        for item in todo.get("required_write_scopes") or []
        if str(item).strip()
    ]
    scope_text = ", ".join(scopes) if scopes else "the active LoopX goal boundary"
    return (
        f"Complete LoopX todo {todo_id} as bound agent {agent_id}: {text}\n\n"
        f"Write boundary: {scope_text}. Preserve all existing repository and user authority gates. "
        "Run focused validation for the actual artifact. Continue under this native Goal until the "
        "objective is genuinely complete. The LoopX outer controller exclusively owns claim, todo "
        "completion, state refresh, and quota accounting; do not call LoopX MCP claim_task or "
        "complete_task and do not run LoopX lifecycle write commands. Use KunlunCode's native "
        "update_goal lifecycle only after the work and its validation are complete."
    )


def native_goal_turn_prompt(mode: str) -> str:
    label = "/goal-pro" if mode == "goal-pro" else "/goal"
    return (
        f"Work autonomously toward the already-active native KunlunCode {label} objective. "
        "Inspect current repository state before editing, stay inside the stated write boundary, "
        "and run real focused validation. Do not create a second goal. Do not call LoopX lifecycle "
        "write tools; the outer controller will reconcile LoopX only after this native goal reaches "
        "an accepted terminal state."
    )


def _new_state(
    context: Mapping[str, Any],
    *,
    todo_id: str,
    mode: str,
    objective_sha256: str,
    goal_ref: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    timestamp = _now()
    binding = {
        "goal_id": str(context.get("goal_id") or ""),
        "agent_id": str(context.get("agent_id") or ""),
    }
    if goal_ref is not None:
        binding["goal_instance_id"] = goal_ref["goal_instance_id"]
    return {
        "schema_version": RUNTIME_STATE_SCHEMA_VERSION,
        "created_at": timestamp,
        "updated_at": timestamp,
        "binding": binding,
        "todo_id": todo_id,
        "native": {
            "mode": mode,
            "objective_sha256": objective_sha256,
            "thread_id": None,
            "goal_id": None,
            "revision": None,
            "status": "planned",
            "verification_kind": None,
            "verification_passed": False,
            "verified": False,
            "verified_at": None,
            "resumed": False,
            "event_counts": {},
        },
        "writeback": {
            "delivery_recorded": False,
            "todo_completed": False,
            "quota_spent": False,
        },
    }


def _validate_binding(state: Mapping[str, Any], context: Mapping[str, Any]) -> None:
    binding = state.get("binding") if isinstance(state.get("binding"), Mapping) else {}
    expected = {
        "goal_id": str(context.get("goal_id") or ""),
        "agent_id": str(context.get("agent_id") or ""),
    }
    actual = {key: str(binding.get(key) or "") for key in expected}
    if actual != expected:
        raise KunlunNativeGoalRuntimeError(
            "KunlunCode runtime journal belongs to another LoopX goal or agent"
        )


def _runtime_goal_ref(state: Mapping[str, Any]) -> object:
    binding = state.get("binding")
    if not isinstance(binding, Mapping):
        return {"malformed": True}
    goal_ref = {"goal_id": binding.get("goal_id")}
    if "goal_instance_id" in binding:
        goal_ref["goal_instance_id"] = binding.get("goal_instance_id")
    return goal_ref


def _commit_runtime_state(
    project: Path,
    state: dict[str, Any],
    goal_admission: FirstPartyHostGoalAdmission,
) -> Path:
    return goal_admission.accept_result(lambda: write_runtime_state(project, state))


def _compact_receipt(
    payload: Mapping[str, Any], fields: tuple[str, ...]
) -> dict[str, Any]:
    return {key: payload.get(key) for key in fields if key in payload}


def _reconcile_writeback_evidence(
    project: Path,
    state: dict[str, Any],
    control: LoopXControlPlane,
    goal_admission: FirstPartyHostGoalAdmission,
) -> None:
    native = state["native"]
    since = str(native.get("verified_at") or "")
    if not since:
        return
    todo_id = str(state.get("todo_id") or "")
    goal_admission.require_current()
    payload = control.evidence_since(since, todo_id=todo_id)
    ledger = payload.get("ledger") if isinstance(payload.get("ledger"), list) else []
    writeback = state["writeback"]
    classification = VERIFIED_CLASSIFICATIONS[str(native.get("mode") or "goal-pro")]
    for event in ledger:
        if not isinstance(event, Mapping):
            continue
        event_kind = str(event.get("event_kind") or "")
        if str(event.get("todo_id") or "") != todo_id:
            continue
        if (
            event_kind == "refresh_state"
            and event.get("classification") == classification
        ):
            writeback["delivery_recorded"] = True
        if event_kind == "todo_complete" and str(event.get("todo_id") or "") == todo_id:
            writeback["todo_completed"] = True
        if event_kind in {"quota_spend", "quota_slot_spent"}:
            writeback["quota_spent"] = True
    _commit_runtime_state(project, state, goal_admission)


def _verified_evidence(state: Mapping[str, Any]) -> str:
    native = state.get("native") if isinstance(state.get("native"), Mapping) else {}
    mode = str(native.get("mode") or "goal-pro")
    verification = "passed" if native.get("verification_passed") else "not-required"
    digest = str(native.get("objective_sha256") or "")[:16]
    native_goal_id = str(native.get("goal_id") or "")
    return (
        f"KunlunCode native {mode} terminal accepted; status=complete; "
        f"verification={verification}; objective_sha256={digest}; "
        f"native_goal_id={native_goal_id}"
    )


def _closeout_writeback(
    project: Path,
    state: dict[str, Any],
    control: LoopXControlPlane,
    goal_admission: FirstPartyHostGoalAdmission,
) -> None:
    goal_admission.require_current()
    _reconcile_writeback_evidence(project, state, control, goal_admission)
    writeback = state["writeback"]
    mode = str(state["native"]["mode"])
    if writeback.get("delivery_recorded") is not True:
        goal_admission.require_current()
        payload = control.record_verified_delivery(
            mode=mode,
            todo_id=str(state["todo_id"]),
        )
        if payload.get("appended") is not True:
            raise KunlunNativeGoalRuntimeError(
                "LoopX did not append the verified KunlunCode delivery record"
            )
        writeback["delivery_recorded"] = True
        writeback["delivery_receipt"] = _compact_receipt(
            payload, ("classification", "generated_at", "appended")
        )
        _commit_runtime_state(project, state, goal_admission)
    if writeback.get("todo_completed") is not True:
        goal_admission.require_current()
        payload = control.complete(
            str(state["todo_id"]), evidence=_verified_evidence(state)
        )
        if payload.get("completed") is not True or payload.get("status") != "done":
            raise KunlunNativeGoalRuntimeError(
                "LoopX did not accept the verified KunlunCode todo completion"
            )
        writeback["todo_completed"] = True
        writeback["todo_receipt"] = _compact_receipt(
            payload, ("todo_id", "status", "status_changed", "changed")
        )
        _commit_runtime_state(project, state, goal_admission)
    if writeback.get("quota_spent") is not True:
        goal_admission.require_current()
        payload = control.spend(todo_id=str(state["todo_id"]))
        if payload.get("appended") is not True:
            raise KunlunNativeGoalRuntimeError(
                "LoopX did not append quota accounting for the verified KunlunCode delivery: "
                + str(payload.get("reason") or "unknown reason")
            )
        writeback["quota_spent"] = True
        writeback["quota_receipt"] = _compact_receipt(
            payload, ("classification", "generated_at", "appended", "slots")
        )
        _commit_runtime_state(project, state, goal_admission)


def _update_native_from_goal(state: dict[str, Any], goal: Mapping[str, Any]) -> None:
    compact = compact_goal(goal)
    native = state["native"]
    native["goal_id"] = compact.get("goal_id")
    native["revision"] = compact.get("revision")
    native["status"] = compact.get("status")
    native["verification_kind"] = compact.get("verification_kind")
    native["verification_passed"] = compact.get("verification_passed") is True


def _validate_native_identity(
    state: Mapping[str, Any],
    goal: Mapping[str, Any],
) -> None:
    native = state["native"]
    compact = compact_goal(goal)
    expected_goal_id = str(native.get("goal_id") or "")
    observed_goal_id = str(compact.get("goal_id") or "")
    if expected_goal_id and observed_goal_id != expected_goal_id:
        raise KunlunNativeGoalRuntimeError(
            "resumed KunlunCode thread contains a different native goal"
        )
    objective_sha256 = str(compact.get("objective_sha256") or "")
    if objective_sha256 and objective_sha256 != native.get("objective_sha256"):
        raise KunlunNativeGoalRuntimeError(
            "resumed KunlunCode native goal objective no longer matches the LoopX todo"
        )
    expected_mode = NATIVE_GOAL_MODES[str(native.get("mode") or "")]
    observed_mode = str(compact.get("mode") or "")
    if observed_mode and observed_mode != expected_mode:
        raise KunlunNativeGoalRuntimeError(
            "resumed KunlunCode native goal mode does not match the runtime journal"
        )


def _run_native_host(
    project: Path,
    state: dict[str, Any],
    *,
    objective: str,
    kunluncode_bin: str,
    permission_mode: str,
    max_duration_secs: int,
    controller_timeout_secs: int,
    token_budget: int | None,
    scenario: str | None,
    client_factory: Callable[..., Any],
    goal_admission: FirstPartyHostGoalAdmission,
) -> None:
    command = build_app_server_command(
        kunluncode_bin,
        project=project,
        permission_mode=permission_mode,
        max_duration_secs=max_duration_secs,
        scenario=scenario,
    )
    environment = dict(os.environ)
    environment[NATIVE_CONTROLLER_ENV] = "1"
    environment[NATIVE_CONTROLLER_GOAL_ENV] = str(state["binding"]["goal_id"])
    environment[NATIVE_CONTROLLER_AGENT_ENV] = str(state["binding"]["agent_id"])
    adapter_bin = str(Path(sys.executable).absolute().parent)
    environment["PATH"] = adapter_bin + os.pathsep + environment.get("PATH", "")
    event_counts: dict[str, int] = {}

    def record_event(message: Mapping[str, Any]) -> None:
        method = str(message.get("method") or "")
        if method:
            event_counts[method] = event_counts.get(method, 0) + 1

    native = state["native"]
    goal_admission.require_current()
    with client_factory(
        command,
        cwd=project,
        env=environment,
        event_handler=record_event,
    ) as client:
        client.initialize()
        thread_id = str(native.get("thread_id") or "")
        resumed = False
        goal: dict[str, Any] = {}
        if thread_id:
            client.resume_thread(thread_id)
            resumed = True
        if not thread_id:
            thread_id = client.start_thread(project)
            native["thread_id"] = thread_id
            native["status"] = "thread_started"
            _commit_runtime_state(project, state, goal_admission)
        elif native.get("goal_id"):
            goal = client.get_goal(thread_id)
            _validate_native_identity(state, goal)
        else:
            try:
                goal = client.get_goal(thread_id)
                _validate_native_identity(state, goal)
            except KunlunAppServerError:
                goal = {}
        native["resumed"] = resumed
        if not goal:
            goal = client.set_goal(
                thread_id,
                objective=objective,
                mode=str(native["mode"]),
                token_budget=token_budget,
            )
            _validate_native_identity(state, goal)
            _update_native_from_goal(state, goal)
            _commit_runtime_state(project, state, goal_admission)
        else:
            _update_native_from_goal(state, goal)
            _commit_runtime_state(project, state, goal_admission)

        if not native_goal_success(goal, mode=str(native["mode"])):
            status = str(goal.get("status") or "")
            if status != "active":
                raise KunlunNativeGoalRuntimeError(
                    "KunlunCode native goal stopped without an accepted completion: "
                    + json.dumps(compact_goal(goal), ensure_ascii=False, sort_keys=True)
                )
            client.start_turn(
                thread_id,
                native_goal_turn_prompt(str(native["mode"])),
            )
            goal = client.wait_for_goal_terminal(
                thread_id,
                timeout_seconds=controller_timeout_secs,
            )
            _validate_native_identity(state, goal)
            _update_native_from_goal(state, goal)
        native["event_counts"] = dict(sorted(event_counts.items()))
        if not native_goal_success(goal, mode=str(native["mode"])):
            _commit_runtime_state(project, state, goal_admission)
            raise KunlunNativeGoalRuntimeError(
                "KunlunCode native goal did not reach an accepted terminal state: "
                + json.dumps(compact_goal(goal), ensure_ascii=False, sort_keys=True)
            )
        native["verified"] = True
        native["verified_at"] = native.get("verified_at") or _now()
        _commit_runtime_state(project, state, goal_admission)


def run_native_goal(
    project: Path,
    context: Mapping[str, Any],
    *,
    mode: str,
    permission_mode: str,
    max_duration_secs: int,
    controller_timeout_secs: int,
    token_budget: int | None = None,
    scenario: str | None = None,
    kunluncode_bin: str | None = None,
    control_plane: LoopXControlPlane | None = None,
    client_factory: Callable[..., Any] = KunlunAppServerClient,
) -> dict[str, Any]:
    if mode not in NATIVE_GOAL_MODES:
        raise KunlunNativeGoalRuntimeError(
            f"unsupported native KunlunCode goal mode: {mode}"
        )
    if max_duration_secs <= 0 or controller_timeout_secs <= 0:
        raise KunlunNativeGoalRuntimeError(
            "KunlunCode turn and controller timeouts must be positive"
        )
    if token_budget is not None and token_budget <= 0:
        raise KunlunNativeGoalRuntimeError(
            "KunlunCode native goal token budget must be positive"
        )
    if permission_mode == "ask":
        raise KunlunNativeGoalRuntimeError(
            "native app-server runs cannot service interactive approvals; use --permission-mode auto "
            "or another explicitly non-interactive KunlunCode mode"
        )
    project = project.resolve()
    binary = kunluncode_bin or shutil.which("kunluncode")
    if not binary:
        raise KunlunNativeGoalRuntimeError("kunluncode is not on PATH")
    control = control_plane or LoopXControlPlane(project, context)
    goal_id = str(context.get("goal_id") or "")
    registry_path = Path(
        str(context.get("registry") or project / ".loopx" / "registry.json")
    )
    goal_ref = capture_first_party_host_goal_ref(
        registry_path=registry_path,
        goal_id=goal_id,
    )
    goal_admission = FirstPartyHostGoalAdmission.for_plan(
        registry_path=registry_path,
        goal_id=goal_id,
        planned_goal_ref=goal_ref,
    )
    journal_path = runtime_state_path(project)
    with exclusive_file_lock(
        journal_path,
        agent_id=str(context.get("agent_id") or ""),
        operation="kunluncode_native_goal",
    ):
        state = goal_admission.select_state(
            read_state=lambda: read_runtime_state(project),
            goal_ref_of=_runtime_goal_ref,
        )
        if state is not None:
            _validate_binding(state, context)
            if (
                state["native"].get("verified") is True
                and runtime_phase(state) != "committed"
            ):
                _closeout_writeback(
                    project,
                    state,
                    control,
                    goal_admission,
                )
                return {
                    "ok": True,
                    "status": "committed",
                    "recovered": True,
                    "runtime": compact_runtime_state(state),
                }

        decision = control.should_run()
        if decision.get("should_run") is not True:
            return {
                "ok": True,
                "status": "skipped",
                "reason": decision.get("reason"),
                "runtime": compact_runtime_state(state),
            }
        todo = decision.get("selected_todo")
        if not isinstance(todo, Mapping):
            raise KunlunNativeGoalRuntimeError(
                "LoopX allowed a KunlunCode turn without selecting an agent todo"
            )
        todo_id = str(todo.get("todo_id") or "")
        claimed_by = str(todo.get("claimed_by") or "")
        agent_id = str(context.get("agent_id") or "")
        if claimed_by and claimed_by != agent_id:
            raise KunlunNativeGoalRuntimeError(
                "LoopX selected todo is claimed by another agent"
            )
        objective = build_native_goal_objective(todo, agent_id=agent_id)
        objective_sha256 = stable_text_digest(objective)
        if state is not None and str(state.get("todo_id") or "") == todo_id:
            native = state["native"]
            if native.get("mode") != mode:
                raise KunlunNativeGoalRuntimeError(
                    "unfinished KunlunCode runtime journal uses a different native goal mode"
                )
            if native.get("objective_sha256") != objective_sha256:
                raise KunlunNativeGoalRuntimeError(
                    "LoopX selected todo changed after the KunlunCode native goal started"
                )
        elif state is not None and runtime_phase(state) != "committed":
            raise KunlunNativeGoalRuntimeError(
                "unfinished KunlunCode runtime journal belongs to another todo"
            )
        else:
            state = _new_state(
                context,
                todo_id=todo_id,
                mode=mode,
                objective_sha256=objective_sha256,
                goal_ref=goal_ref,
            )
            _commit_runtime_state(project, state, goal_admission)
        if not claimed_by:
            goal_admission.require_current()
            control.claim(todo_id)
        _run_native_host(
            project,
            state,
            objective=objective,
            kunluncode_bin=binary,
            permission_mode=permission_mode,
            max_duration_secs=max_duration_secs,
            controller_timeout_secs=controller_timeout_secs,
            token_budget=token_budget,
            scenario=scenario,
            client_factory=client_factory,
            goal_admission=goal_admission,
        )
        goal_admission.require_current()
        _closeout_writeback(
            project,
            state,
            control,
            goal_admission,
        )
        return {
            "ok": True,
            "status": "committed",
            "recovered": state["native"].get("resumed") is True,
            "runtime": compact_runtime_state(state),
        }
