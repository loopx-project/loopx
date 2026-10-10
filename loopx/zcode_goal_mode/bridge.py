"""ZCode transport and exact Goal/Agent admission; provider decisions live in TS."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from ..agent_registry import agent_profile_for_goal, registered_agent_ids_for_goal, require_registered_agent_id
from ..control_plane.goals.activation import goal_is_stopped
from ..control_plane.goals.goal_ref_validation import exact_goal_ref, require_goal_id
from ..control_plane.runtime.goal_project_route import resolve_goal_project_route
from ..control_plane.projects.registry_codec import load_registry
from ..host_loop_activation import normalize_agent_type
from ..registry import registry_goals

# The bundled provider contract is the single action vocabulary for Python and TS.
ZCODE_GOAL_ACTIONS: tuple[str, ...] = tuple(
    json.loads(Path(__file__).with_name("contract.json").read_text(encoding="utf-8"))["actions"]
)
MAX_TRANSPORT_BYTES = 1_048_576
_UNSET = object()


class ZCodeGoalBridgeError(ValueError):
    def __init__(self, message: str, *, code: str = "invalid_zcode_goal_request", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def _zcode_host_compatible(profile: dict[str, Any] | None) -> bool:
    declared_host = (profile or {}).get("agent_type") or (profile or {}).get("host_surface")
    # An advisory profile need not declare a host. Binding remains an explicit choice.
    if declared_host is None:
        return True
    if not isinstance(declared_host, str):
        return False
    try:
        return normalize_agent_type(declared_host) == "zcode"
    except ValueError:
        return False


def zcode_goal_eligible_agent_ids(goal: dict[str, Any]) -> list[str]:
    """Project registered actors compatible with this host, without probing it."""
    return [agent_id for agent_id in registered_agent_ids_for_goal(goal)
            if _zcode_host_compatible(agent_profile_for_goal(goal, agent_id))]


def validate_zcode_binding(
    *, registry_path: Path, goal_id: str, agent_id: str,
    project: str | Path | None = None, goal_ref: dict[str, str] | None = None,
    require_active: bool = True, goal_creation_operation_id: object = _UNSET,
) -> dict[str, Any]:
    """Read existing authority only; never create an instance or register an agent."""
    require_goal_id(goal_id)
    _, canonical_project, route = resolve_goal_project_route(
        registry_path=registry_path, goal_id=goal_id, project_override=project,
    )
    source_registry = Path(route["source_registry"]).resolve()
    registry = load_registry(source_registry)
    matches = [item for item in registry_goals(registry) if item.get("id") == goal_id]
    if len(matches) != 1:
        raise ZCodeGoalBridgeError("Goal must be registered exactly once.", code="zcode_goal_authority_changed", status=409)
    goal = matches[0]
    if require_active and (goal_is_stopped(goal) or goal.get("status", "active") != "active"):
        raise ZCodeGoalBridgeError("The LoopX Goal is no longer active.", code="zcode_goal_authority_changed", status=409)
    # Generic Goal routing already rejects lifecycle-only source-session registries.
    # Existing exact identities remain exact; first-party legacy aliases stay compatible.
    instance_id = goal.get("goal_instance_id")
    if instance_id is not None:
        if not isinstance(instance_id, str):
            raise ZCodeGoalBridgeError("Goal instance identifier is invalid.")
        current_ref = exact_goal_ref(goal_id, instance_id)
        identity_scope = "exact_goal_instance"
    else:
        current_ref = {"goal_id": goal_id}
        identity_scope = "legacy_goal_alias"
    if goal_ref is not None and goal_ref != current_ref:
        raise ZCodeGoalBridgeError("Goal instance changed; inspect the current Goal before binding again.", code="zcode_goal_authority_changed", status=409)
    creation_id = goal.get("creation_operation_id")
    if creation_id is not None and (not isinstance(creation_id, str) or not creation_id):
        raise ZCodeGoalBridgeError("Goal creation witness is invalid.")
    if goal_creation_operation_id is not _UNSET and goal_creation_operation_id != creation_id:
        raise ZCodeGoalBridgeError("Goal creation witness changed; inspect the current Goal before binding again.", code="zcode_goal_authority_changed", status=409)
    normalized_agent = require_registered_agent_id(
        registry_path=source_registry, goal_id=goal_id, agent_id=agent_id, field="agent_id",
    )
    profile = agent_profile_for_goal(goal, normalized_agent)
    if not _zcode_host_compatible(profile):
        raise ZCodeGoalBridgeError("The registered Agent explicitly declares a different host.")
    if not canonical_project.is_dir():
        raise ZCodeGoalBridgeError("The canonical Goal project is unavailable.")
    return {
        "ok": True, "goal_id": goal_id, "goal_ref": current_ref, "agent_id": normalized_agent,
        "project": str(canonical_project), "registry": str(source_registry),
        "runtime_root": str(route["source_runtime_root"]),
        "identity_scope": identity_scope, "goal_creation_operation_id": creation_id,
    }


def _node_command() -> str:
    from ..control_plane.effect_runtime import _node_executable, EffectRuntimeStartupError
    try:
        return _node_executable()
    except EffectRuntimeStartupError as exc:
        raise ZCodeGoalBridgeError(str(exc), code="zcode_goal_runtime_unavailable", status=503) from exc


def _cli_command(cli_path: str | None, node: str) -> list[str]:
    requested = cli_path if cli_path is not None else "zcode"
    if not isinstance(requested, str) or not requested.strip() or len(requested) > 4096:
        raise ZCodeGoalBridgeError("ZCode CLI path must be a nonempty string of at most 4096 characters.")
    requested = requested.strip()
    resolved = shutil.which(requested) or str(Path(requested).expanduser().resolve())
    path = Path(resolved)
    if not path.is_file():
        raise ZCodeGoalBridgeError("ZCode CLI is unavailable. Supply its executable or JS bundle when binding.", code="zcode_cli_unavailable", status=503)
    if (path.parent / "resources/glm/zcode.cjs").is_file() or (path.parent / "resources/app.asar").is_file():
        raise ZCodeGoalBridgeError("Select ZCode CLI; a Desktop executable cannot be bound to the managed CLI provider.")
    if path.suffix.lower() in {".cmd", ".bat"}:
        raise ZCodeGoalBridgeError("Windows shell shims cannot be used by the managed stdio transport. Supply the installed ZCode JS bundle for bind.")
    if path.suffix.lower() in {".js", ".cjs", ".mjs"}:
        return [node, str(path.resolve()), "app-server"]
    return [str(path.resolve()), "app-server"]


def _run_json(command: list[str], *, project: str, request: dict[str, Any] | None = None) -> dict[str, Any]:
    environment = {**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        completed = subprocess.run(
            command, input=json.dumps(request, ensure_ascii=False) if request is not None else None,
            cwd=project, env=environment, capture_output=True, text=True,
            encoding="utf-8", errors="strict", timeout=45, check=False,
        )
    except (OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
        raise ZCodeGoalBridgeError("ZCode provider did not return a bounded response. Read status before retrying an operation.", code="zcode_goal_transport_unavailable", status=503) from exc
    if len(completed.stdout.encode("utf-8")) > MAX_TRANSPORT_BYTES:
        raise ZCodeGoalBridgeError("ZCode provider response exceeded the transport limit.", code="zcode_goal_invalid_readback", status=503)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ZCodeGoalBridgeError("ZCode provider did not return a JSON readback.", code="zcode_goal_invalid_readback", status=503) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
        raise ZCodeGoalBridgeError("ZCode provider returned an invalid readback.", code="zcode_goal_invalid_readback", status=503)
    if completed.returncode and payload.get("ok") is True:
        raise ZCodeGoalBridgeError("ZCode provider failed despite a successful readback.", code="zcode_goal_invalid_readback", status=503)
    return payload


def _heartbeat_task(binding: dict[str, Any], loopx_command: list[str]) -> str:
    payload = _run_json([
        *loopx_command, "--registry", binding["registry"], "--format", "json",
        "heartbeat-prompt", "--goal-id", binding["goal_id"], "--agent-id", binding["agent_id"],
        "--runtime-profile", "generic_cli", "--thin",
    ], project=binding["project"])
    body = payload.get("task_body")
    if payload.get("ok") is not True or not isinstance(body, str) or not body.strip():
        raise ZCodeGoalBridgeError("Canonical heartbeat instructions are unavailable. Repair the Goal state before starting ZCode.")
    return body


def zcode_goal_operation(
    *, action: str, registry_path: Path, goal_id: str, agent_id: str,
    project: str | Path | None = None, cli_path: str | None = None,
    runtime_root: str | Path | None = None, model_selection: dict[str, Any] | None = None,
    expected_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if action not in ZCODE_GOAL_ACTIONS:
        raise ZCodeGoalBridgeError("Unsupported ZCode Goal operation.")
    if model_selection is not None:
        if action != "select_model":
            raise ZCodeGoalBridgeError("Model selection is supported only by select_model.")
        if not isinstance(model_selection, dict) or set(model_selection) - {"providerId", "modelId", "options"}:
            raise ZCodeGoalBridgeError("Model selection accepts providerId, modelId and optional reasoning options.")
        if any(not isinstance(model_selection.get(key), str) or not model_selection[key].strip() or len(model_selection[key]) > 256 for key in ("providerId", "modelId")):
            raise ZCodeGoalBridgeError("Model selection requires bounded providerId and modelId strings.")
        options = model_selection.get("options", {})
        if not isinstance(options, dict) or set(options) - {"reasoningLevel"} or ("reasoningLevel" in options and (not isinstance(options["reasoningLevel"], str) or not options["reasoningLevel"].strip() or len(options["reasoningLevel"]) > 128)):
            raise ZCodeGoalBridgeError("Model selection options accept only a bounded reasoningLevel.")
    elif action == "select_model":
        raise ZCodeGoalBridgeError("select_model requires an explicit model_selection.")
    if cli_path is not None and action != "bind":
        raise ZCodeGoalBridgeError("ZCode CLI selection is supported only by an explicit bind operation.")
    expected_ref = None
    expected_creation: object = _UNSET
    if expected_binding is not None:
        if not isinstance(expected_binding, dict) or set(expected_binding) != {"goal_ref", "goal_creation_operation_id"}:
            raise ZCodeGoalBridgeError("expected_binding requires goal_ref and goal_creation_operation_id.")
        expected_ref = expected_binding["goal_ref"]
        if not isinstance(expected_ref, dict) or set(expected_ref) not in ({"goal_id"}, {"goal_id", "goal_instance_id"}):
            raise ZCodeGoalBridgeError("expected_binding requires an exact supported Goal reference shape.")
        if not isinstance(expected_ref.get("goal_id"), str):
            raise ZCodeGoalBridgeError("Expected Goal id must be a string.")
        require_goal_id(expected_ref["goal_id"])
        if "goal_instance_id" in expected_ref:
            if not isinstance(expected_ref["goal_instance_id"], str):
                raise ZCodeGoalBridgeError("Expected Goal instance must be a string.")
            exact_goal_ref(expected_ref["goal_id"], expected_ref["goal_instance_id"])
        expected_creation = expected_binding["goal_creation_operation_id"]
        if expected_creation is not None and (not isinstance(expected_creation, str) or not expected_creation or len(expected_creation) > 256):
            raise ZCodeGoalBridgeError("Expected creation witness must be a bounded string or null.")
    require_active = action not in {"status", "pause", "stop"}
    binding = validate_zcode_binding(
        registry_path=registry_path, goal_id=goal_id, agent_id=agent_id, project=project,
        require_active=require_active, goal_ref=expected_ref, goal_creation_operation_id=expected_creation,
    )
    node = _node_command()
    loopx_command = [sys.executable, "-m", "loopx.cli"]
    state_root = Path(runtime_root).expanduser().resolve() if runtime_root is not None else Path(binding["runtime_root"])
    state_key = hashlib.sha256(json.dumps(
        [binding["registry"], binding["goal_ref"], binding["goal_creation_operation_id"], binding["agent_id"]], sort_keys=True,
    ).encode("utf-8")).hexdigest()
    request: dict[str, Any] = {
        "action": action, **{key: binding[key] for key in ("project", "registry", "goal_id", "goal_ref", "goal_creation_operation_id", "identity_scope", "agent_id")},
        "state_path": str(state_root / "zcode-goal" / f"{state_key}.json"),
        "loopx_command": loopx_command,
        "validation_command": [sys.executable, "-m", "loopx.zcode_goal_mode.bridge", "--validate-binding"],
    }
    if model_selection is not None:
        request["model_selection"] = model_selection
    if action == "bind":
        request["cli_command"] = _cli_command(cli_path, node)
        request["cli_path"] = request["cli_command"][-2]
    if action in {"bind", "start"}:
        request["task_body"] = _heartbeat_task(binding, loopx_command)
    payload = _run_json([node, "--experimental-strip-types", str(Path(__file__).with_name("cli.ts"))], project=binding["project"], request=request)
    for key in ("goal_id", "goal_ref", "goal_creation_operation_id", "agent_id"):
        if key not in payload or payload[key] != binding[key]:
            raise ZCodeGoalBridgeError("ZCode readback did not match the exact Goal and Agent requested.", code="zcode_goal_invalid_readback", status=409)
    observed = validate_zcode_binding(
        registry_path=registry_path, goal_id=goal_id, agent_id=agent_id,
        project=binding["project"], goal_ref=binding["goal_ref"], require_active=require_active,
        goal_creation_operation_id=binding["goal_creation_operation_id"],
    )
    if observed["registry"] != binding["registry"]:
        raise ZCodeGoalBridgeError("Canonical Goal authority changed during the operation.", code="zcode_goal_authority_changed", status=409)
    return payload


def _validate_main() -> int:
    parser = argparse.ArgumentParser(description="Internal read-only ZCode binding admission.")
    parser.add_argument("--validate-binding", required=True, action="store_true")
    parser.parse_args()
    try:
        encoded = sys.stdin.buffer.read(MAX_TRANSPORT_BYTES + 1)
        if len(encoded) > MAX_TRANSPORT_BYTES:
            raise ValueError("binding request exceeded the transport limit")
        request = json.loads(encoded)
        if not isinstance(request, dict) or not isinstance(request.get("goal_ref"), dict):
            raise ValueError("an exact Goal reference is required")
        action = request.get("action")
        if not isinstance(action, str) or action not in ZCODE_GOAL_ACTIONS:
            raise ValueError("an explicit known ZCode operation is required for binding admission")
        required = ("registry", "project", "goal_id", "agent_id")
        if any(not isinstance(request.get(key), str) or not request[key] for key in required):
            raise ValueError("binding identity fields must be nonempty strings")
        result = validate_zcode_binding(
            registry_path=Path(request["registry"]), project=request["project"],
            goal_id=request["goal_id"], agent_id=request["agent_id"], goal_ref=request["goal_ref"],
            goal_creation_operation_id=request.get("goal_creation_operation_id", _UNSET),
            require_active=action not in {"status", "pause", "stop"},
        )
        payload = {key: result[key] for key in ("ok", "goal_id", "goal_ref", "goal_creation_operation_id", "identity_scope", "agent_id")}
    except (ValueError, OSError) as exc:
        payload = {"ok": False, "error": str(exc), "error_code": getattr(exc, "code", "invalid_zcode_goal_binding")}
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(_validate_main())
