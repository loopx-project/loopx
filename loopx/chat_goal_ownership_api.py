"""Path-free App transport for the existing TS-owned, backed-up policy migration.

Preview files survive server restarts. The browser receives an opaque handle and
must retry that original plan after a lost response, never recreate the intent.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from .control_plane.todos.provider_handoff_mode import (
    migrate_registered_handoff_mode,
    read_canonical_handoff_mode,
)

CHAT_GOAL_OWNERSHIP_PATH = "/api/chat/goal-ownership"


def _token(body: dict[str, Any], name: str, length: int) -> str:
    value = body.get(name)
    if not isinstance(value, str) or not re.fullmatch(rf"[a-f0-9]{{{length}}}", value):
        raise ValueError(f"invalid {name}")
    return value


class GoalOwnershipRequestMixin:
    server: Any
    path: str

    def _ownership_readback(self, goal_id: str) -> dict[str, Any]:
        self._registry_and_goal(goal_id)
        current = read_canonical_handoff_mode(
            runtime_root=self.server.runtime_root, goal_id=goal_id
        )
        return {
            "ok": True,
            "goal_id": goal_id,
            "canonical": current is not None,
            "current_mode": current["handoff_mode"] if current else None,
            "provider_revision": current["provider_revision"] if current else None,
        }

    def _ownership_inspect(self) -> None:
        try:
            query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            if set(query) != {"goal_id"} or len(query["goal_id"]) != 1:
                raise ValueError("goal_id is required exactly once")
            self._send_json(self._ownership_readback(query["goal_id"][0]))
        except (KeyError, TypeError, ValueError):
            self._send_error(
                "Choose a registered Goal.",
                status=400,
                error_code="invalid_goal_ownership_request",
            )
        except Exception:  # noqa: BLE001 - never expose local paths or provider data.
            self._send_error(
                "Ownership policy could not be read.",
                status=503,
                error_code="goal_ownership_unavailable",
            )

    def _ownership_update(self, *, execute: bool) -> None:
        try:
            body = self._read_json()
            allowed = (
                {"goal_id", "preview_id", "plan_sha256"}
                if execute
                else {"goal_id", "mode"}
            )
            if set(body) != allowed or not isinstance(body.get("goal_id"), str):
                raise ValueError("invalid ownership request")
            goal_id = body["goal_id"]
            self._registry_and_goal(goal_id)
            # Storage paths belong to the server. No caller paths or source overrides.
            preview_id = _token(body, "preview_id", 32) if execute else uuid4().hex
            digest = _token(body, "plan_sha256", 64) if execute else None
            directory = self.server.runtime_root / "chat" / "ownership-migrations"
            if not execute:
                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            result = migrate_registered_handoff_mode(
                registry_path=self.server.registry_path,
                runtime_root=self.server.runtime_root,
                goal_id=goal_id,
                action="migrate" if execute else "plan-migration",
                plan=directory / f"{preview_id}.json",
                mode=None if execute else body["mode"],
                plan_sha256=digest,
                execute=execute,
            )
            # Project facts, not a second decision rule; the CLI and App use the
            # same plan, backup, source witness, CAS and original receipt owner.
            payload = {
                key: result[key]
                for key in (
                    "ok",
                    "status",
                    "previous_mode",
                    "handoff_mode",
                    "changed",
                    "plan_sha256",
                    "preserved_claim_count",
                    "execution_authority_granted",
                    "reason_code",
                )
                if key in result
            }
            payload.update(goal_id=goal_id, preview_id=preview_id)
            payload["retained_lease_count"] = len(result.get("lease_dispositions", []))
            payload["conflict_count"] = len(result.get("conflicts", []))
            payload["conflicts"] = [
                {key: row[key] for key in ("todo_id", "reason_code") if key in row}
                for row in result.get("conflicts", [])[:20]
            ]
            payload["backup_verified"] = bool(
                result.get("ok") and result.get("backup_archive_sha256")
            )
            if execute and result.get("ok"):
                # A historical receipt cannot certify today's policy. Keep it
                # separate even when a later operation changed the mode again.
                try:
                    payload["current"] = self._ownership_readback(goal_id)
                except Exception:  # noqa: BLE001 - preserve committed/replayed facts.
                    payload["current"] = None
            self._send_json(payload, status=200 if result.get("ok") else 409)
        except (KeyError, TypeError, ValueError):
            self._send_error(
                "Invalid Goal policy request.",
                status=400,
                error_code="invalid_goal_ownership_request",
            )
        except Exception:  # noqa: BLE001 - response loss must retain the original preview.
            self._send_error(
                "Result unavailable. Retry the original preview to recover its receipt.",
                status=503,
                error_code="goal_ownership_unavailable",
            )
