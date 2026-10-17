"""Loopback room-work transport; the existing Todo and delivery owners decide effects."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..control_plane.runtime.runtime_projection_route import resolve_goal_source_runtime_route
from ..extensions.lark.goal_channel_contracts import binding_for_goal, read_goal_channel_binding
from ..extensions.lark.goal_channel_delivery_contract import goal_channel_binding_digest
from ..extensions.lark.goal_channel_work import _resolve_binding, run_goal_channel_work
from ..extensions.lark import LARK_EXTENSION_ID, LARK_GOAL_CHANNEL_PERMISSION, goal_channel_work
from ..extensions.runtime import default_extension_state_file

ROOM_WORK_PATH = "/api/chat/lark/room-work"


class RoomWorkChatRequestMixin:
    def _room_work(self, *, claim: bool = False) -> None:
        try:
            if claim:
                body = self._read_json()
                allowed = {"goal_id", "connection_id", "scope_revision", "todo_id", "expected_revision", "idempotency_key"}
                if not isinstance(body, dict) or set(body) != allowed:
                    raise ValueError("invalid room claim fields")
            else:
                query = parse_qs(urlparse(self.path).query)
                if not {"goal_id", "connection_id"} <= set(query) or set(query) - {"goal_id", "connection_id", "todo_id"} or any(len(v) != 1 for v in query.values()):
                    raise ValueError("invalid room work target")
                body = {k: v[0] for k, v in query.items()}
            if any(not isinstance(v, str) or not v.strip() or len(v) > 512 for v in body.values()):
                raise ValueError("invalid room work input")
            goal_id, connection_id = body["goal_id"], body["connection_id"]
            _, binding_path = self._goal_channel_context(goal_id)
            route = resolve_goal_source_runtime_route(registry_path=self.server.registry_path, goal_id=goal_id)
            selected = binding_for_goal(read_goal_channel_binding(binding_path), goal_id, connection_id=connection_id)
            actor = (selected or {}).get("agent_id")
            routing = (selected or {}).get("routing") or {}
            if not isinstance(routing, dict) or routing.get("conversation_kind") not in (None, "goal"):
                raise ValueError("a Goal connection is required")
            if not isinstance(actor, str) or not actor or selected.get("enabled") is not True:
                raise ValueError("an enabled Agent connection is required")
            target_path = self._goal_channel_target_path()
            binding = _resolve_binding(binding_path, target_path, goal_id, actor)
            if binding.get("connection_id") != connection_id:
                raise ValueError("connection does not match the Agent route")
            digest = goal_channel_binding_digest(binding)
            # An opaque freshness witness, never a claim grant or persisted state.
            scope = "sha256:" + hashlib.sha256(json.dumps([
                digest, str(Path(route["source_registry"]).resolve()),
                str(Path(route["source_runtime_root"]).resolve()),
            ], separators=(",", ":")).encode()).hexdigest()
            if claim and body["scope_revision"] != scope:
                return self._send_error("The connection changed. Refresh room work before claiming.",
                    status=409, error_code="room_work_scope_changed")
            packet = run_goal_channel_work(
                registry_path=Path(route["source_registry"]), runtime_root=Path(route["source_runtime_root"]),
                binding_path=binding_path, target_path=target_path, goal_id=goal_id, actor_id=actor,
                command="claim" if claim else "project", execute=claim, runner=self._lark_runner(),
                todo_id=body.get("todo_id"), expected_revision=body.get("expected_revision"),
                idempotency_key=body.get("idempotency_key"), expected_connection_id=connection_id,
                expected_binding_digest=digest, invoked_registry_path=self.server.registry_path,
            )
            activation = {}
            if not claim:
                try:
                    activation = goal_channel_work.resolve_extension_activation(LARK_EXTENSION_ID,
                        state_file=default_extension_state_file(Path(route["source_runtime_root"])),
                        required_permissions=(LARK_GOAL_CHANNEL_PERMISSION,))
                except ValueError:
                    # Inactive/incompatible extensions leave an explicitly read-only view.
                    pass
            envelope = packet.get("projection_envelope") or {}
            self._send_json({**packet, "connection_id": connection_id, "scope_revision": scope,
                "claim_available": packet.get("ok") is True and envelope.get("alert") is False and (activation.get("enabled") is True or
                    (claim and packet.get("canonical_claim_accepted") is True and packet.get("readback_verified") is True))})
        except (ValueError, KeyError):
            self._send_error("Room work is unavailable for this connection. Check its Agent and authority, then refresh.",
                status=400, error_code="room_work_unavailable")
