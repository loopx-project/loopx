"""Loopback Chat projection for the explicitly bound ZCode CLI provider."""
from __future__ import annotations

from typing import Any
from urllib.parse import unquote, urlparse

from ..chat import redact_local_paths
from ..status_server import is_loopback_host
from .bridge import ZCodeGoalBridgeError, zcode_goal_operation


def public_zcode_goal_readback(payload: dict[str, Any]) -> dict[str, Any]:
    result = {key: payload[key] for key in ("ok", "available", "goal_id", "goal_ref", "goal_creation_operation_id", "identity_scope", "agent_id", "actions") if key in payload}
    if "reason" in payload:
        result["reason"] = redact_local_paths(str(payload["reason"]))
    binding = payload.get("binding")
    result["binding"] = {key: binding[key] for key in ("mode", "connected", "cli_path", "protocol") if key in binding} if isinstance(binding, dict) else None
    native = payload.get("native")
    result["native"] = {key: native[key] for key in ("session_id", "target_id", "status", "raw_status", "session_status", "objective_sha256", "running", "usage", "selected_model", "available_models") if key in native} if isinstance(native, dict) else None
    quota = payload.get("quota")
    result["quota"] = {key: quota[key] for key in ("should_run", "checked_at") if key in quota} if isinstance(quota, dict) else None
    if isinstance(quota, dict) and "reason" in quota:
        result["quota"]["reason"] = redact_local_paths(str(quota["reason"]))
    return result


class ZCodeGoalRequestMixin:
    server: Any
    path: str

    def _read_json(self) -> dict[str, Any]:
        raise NotImplementedError

    def _require_loopback_origin(self) -> bool:
        raise NotImplementedError

    def _send_error(self, message: str, **kwargs: Any) -> None:
        raise NotImplementedError

    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        raise NotImplementedError

    def _dispatch_zcode_goal(self, path: str, *, apply: bool = False) -> bool:
        parts = path.strip("/").split("/")
        if len(parts) != 6 or parts[:2] != ["api", "goals"] or parts[3] != "agents" or parts[5] != "zcode-goal":
            return False
        if not is_loopback_host(str(self.server.server_address[0])):
            self._send_error("Managed ZCode operations require a loopback server.", status=403, error_code="zcode_goal_loopback_required")
            return True
        if not self._require_loopback_origin():
            return True
        try:
            if urlparse(self.path).query:
                raise ValueError("ZCode Goal routes do not accept query parameters.")
            goal_id, agent_id = unquote(parts[2]), unquote(parts[4])
            body = self._read_json() if apply else {}
            if body is None:
                return True
            if set(body) - {"action", "cli_path", "model_selection", "expected_binding"}:
                raise ValueError("ZCode Goal accepts action, expected_binding, optional cli_path and model_selection.")
            action = body.get("action") if apply else "status"
            if not isinstance(action, str):
                raise ValueError("ZCode Goal action must be a string.")
            if "cli_path" in body and (not isinstance(body["cli_path"], str) or not body["cli_path"].strip() or len(body["cli_path"]) > 4096):
                raise ValueError("cli_path must be a nonempty string of at most 4096 characters.")
            if "model_selection" in body and (action != "select_model" or not isinstance(body["model_selection"], dict)):
                raise ValueError("model_selection is a selection object accepted only by select_model.")
            if apply and not isinstance(body.get("expected_binding"), dict):
                raise ValueError("POST requires expected_binding from the current Goal readback.")
            payload = zcode_goal_operation(
                action=action, registry_path=self.server.registry_path, goal_id=goal_id, agent_id=agent_id,
                cli_path=body.get("cli_path"), runtime_root=self.server.runtime_root_override, model_selection=body.get("model_selection"), expected_binding=body.get("expected_binding"),
            )
        except (ValueError, OSError) as exc:
            self._send_error(redact_local_paths(str(exc)), status=exc.status if isinstance(exc, ZCodeGoalBridgeError) else 400, error_code=getattr(exc, "code", "invalid_zcode_goal_request"))
        else:
            self._send_json(public_zcode_goal_readback(payload))
        return True
