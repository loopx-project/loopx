"""Loopback settings API for the quota-owned automatic execution floor."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import parse_qs, urlparse

from .control_plane.effect_runtime import (
    EffectRuntimeConflict,
    EffectRuntimeRejected,
    effect_runtime_result,
)
from .control_plane.quota.automation_cadence_readback import (
    automation_cadence_readback,
)


CHAT_AUTOMATION_CADENCE_PATH = "/api/chat/automation-cadence"
CHAT_AUTOMATION_CADENCE_PREVIEW_PATH = f"{CHAT_AUTOMATION_CADENCE_PATH}/preview"
CHAT_AUTOMATION_CADENCE_APPLY_PATH = f"{CHAT_AUTOMATION_CADENCE_PATH}/apply"


class SupersededCadencePreview(Exception):
    """An apply carried a preview that no longer matches its own request.

    Typed on purpose: the HTTP status and error code must come from the failure
    kind, never from whether the message happens to contain a keyword.
    """


class InvalidAutomationCadenceOwnerResult(RuntimeError):
    """The owner returned data that cannot be exposed as verified readback."""


def _cadence_failure_status(exc: Exception) -> tuple[int, str]:
    """Map a cadence failure to its stable HTTP contract by type, not wording."""

    if isinstance(exc, (EffectRuntimeConflict, SupersededCadencePreview)):
        return 409, "automation_cadence_conflict"
    return 400, "invalid_automation_cadence_request"


def _scope(body: dict[str, Any]) -> tuple[str, str | None, str | None]:
    goal_id = body.get("goal_id")
    agent_id = body.get("agent_id")
    automation_id = body.get("automation_id")
    for name, value in (
        ("goal_id", goal_id),
        ("agent_id", agent_id),
        ("automation_id", automation_id),
    ):
        if value is not None and (
            not isinstance(value, str) or not value.strip() or len(value) > 256
        ):
            raise ValueError(
                f"{name} must be a nonempty string of at most 256 characters"
            )
    if not goal_id:
        raise ValueError("goal_id is required")
    if automation_id and not agent_id:
        raise ValueError("automation_id requires agent_id")
    return (
        goal_id.strip(),
        agent_id.strip() if agent_id else None,
        automation_id.strip() if automation_id else None,
    )


def _public(
    result: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str | None,
    automation_id: str | None,
) -> dict[str, Any]:
    """Policy provenance stays local; the browser receives only scoped values."""

    try:
        readback = automation_cadence_readback(
            result,
            expected_goal_id=goal_id,
            expected_agent_id=agent_id,
            expected_automation_id=automation_id,
        )
        return {
            "ok": True,
            "schema_version": "chat_automation_cadence_v0",
            **readback,
            "enabled": result["enabled"],
            "enforcement": result["enforcement"],
            "pre_model_admission": result["pre_model_admission"],
            "sources": [
                {
                    "agent_id": rule["agent_id"],
                    "automation_id": rule["automation_id"],
                    "min_interval_minutes": rule["min_interval_minutes"],
                    "revision": rule["revision"],
                }
                for rule in result["sources"]
            ],
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidAutomationCadenceOwnerResult(
            "automation cadence owner returned invalid readback"
        ) from exc


def _request(
    body: dict[str, Any], *, execute: bool, runtime_root: str
) -> dict[str, Any]:
    allowed = {
        "goal_id",
        "agent_id",
        "automation_id",
        "min_interval_minutes",
        "expected_revision",
        "owner_reference",
        "approve_reduction",
    }
    if execute:
        allowed.add("preview_revision")
    unknown = set(body) - allowed
    if unknown:
        raise ValueError("unknown cadence field(s): " + ", ".join(sorted(unknown)))
    goal_id, agent_id, automation_id = _scope(body)
    minutes = body.get("min_interval_minutes")
    revision = body.get("expected_revision")
    reference = body.get("owner_reference")
    if (
        isinstance(minutes, bool)
        or not isinstance(minutes, int)
        or not 0 <= minutes <= 525600
    ):
        raise ValueError("min_interval_minutes must be an integer from 0 to 525600")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("expected_revision must be a non-negative integer")
    if not isinstance(reference, str) or not reference.strip() or len(reference) > 256:
        raise ValueError(
            "owner_reference is required and must be at most 256 characters"
        )
    if not isinstance(body.get("approve_reduction", False), bool):
        raise ValueError("approve_reduction must be a boolean")
    values = {
        "goal_id": goal_id,
        "agent_id": agent_id,
        "automation_id": automation_id,
        "min_interval_minutes": minutes,
        "expected_revision": revision,
        "owner_reference": reference.strip(),
        "approve_reduction": body.get("approve_reduction", False),
    }
    digest = hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if execute and body.get("preview_revision") != digest:
        raise SupersededCadencePreview(
            "preview is stale; inspect and preview the current policy again"
        )
    return {
        "runtime_root": runtime_root,
        "operation": "configure",
        "execute": execute,
        **values,
        "preview_revision": digest,
    }


class AutomationCadenceRequestMixin:
    server: Any
    path: str

    def _cadence_read(self) -> None:
        try:
            query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            if set(query) - {"goal_id", "agent_id", "automation_id"} or any(
                len(v) != 1 for v in query.values()
            ):
                raise ValueError("invalid cadence query")
            goal_id, agent_id, automation_id = _scope(
                {key: values[0] for key, values in query.items()}
            )
            self._registry_and_goal(goal_id)
            result = effect_runtime_result(
                "quota.automation_cadence.manage",
                {
                    "runtime_root": str(self.server.runtime_root),
                    "operation": "read",
                    "goal_id": goal_id,
                    "agent_id": agent_id,
                    "automation_id": automation_id,
                },
                retry_safe=True,
            )
            self._send_json(
                _public(
                    result,
                    goal_id=goal_id,
                    agent_id=agent_id,
                    automation_id=automation_id,
                )
            )
        except (EffectRuntimeRejected, TypeError, ValueError) as exc:
            self._send_error(
                str(exc), status=400, error_code="invalid_automation_cadence_request"
            )
        except Exception:  # noqa: BLE001 - never expose local policy paths to a browser.
            self._send_error(
                "Automatic execution policy could not be read.",
                status=500,
                error_code="automation_cadence_read_failed",
            )

    def _cadence_update(self, *, execute: bool) -> None:
        try:
            body = self._read_json()
            request = _request(
                body, execute=execute, runtime_root=str(self.server.runtime_root)
            )
            self._registry_and_goal(request["goal_id"])
            preview_revision = request.pop("preview_revision")
            result = effect_runtime_result(
                "quota.automation_cadence.manage", request, retry_safe=False
            )
            payload = _public(
                result,
                goal_id=request["goal_id"],
                agent_id=request["agent_id"],
                automation_id=request["automation_id"],
            )
            payload.update(
                {"preview_revision": preview_revision, "written": result["written"]}
            )
            if execute:
                try:
                    readback = effect_runtime_result(
                        "quota.automation_cadence.manage",
                        {
                            "runtime_root": str(self.server.runtime_root),
                            "operation": "read",
                            "goal_id": request["goal_id"],
                            "agent_id": request["agent_id"],
                            "automation_id": request["automation_id"],
                        },
                        retry_safe=True,
                    )
                    payload = _public(
                        readback,
                        goal_id=request["goal_id"],
                        agent_id=request["agent_id"],
                        automation_id=request["automation_id"],
                    )
                    payload.update(
                        {
                            "written": True,
                            "readback_verified": readback["configuration_revision"]
                            == request["expected_revision"] + 1
                            and any(
                                rule["agent_id"] == request["agent_id"]
                                and rule["automation_id"] == request["automation_id"]
                                and rule["min_interval_minutes"]
                                == request["min_interval_minutes"]
                                for rule in readback["sources"]
                            ),
                        }
                    )
                except Exception:  # noqa: BLE001 - preserve the known write fact.
                    payload.update({"written": True, "readback_verified": False})
            self._send_json(payload)
        except InvalidAutomationCadenceOwnerResult:
            self._send_error(
                (
                    "Automatic execution change could not be verified. "
                    "Refresh policy before retrying."
                    if execute
                    else "Automatic execution policy could not be read."
                ),
                status=500,
                error_code=(
                    "automation_cadence_write_unknown"
                    if execute
                    else "automation_cadence_read_failed"
                ),
            )
        except (
            EffectRuntimeConflict,
            EffectRuntimeRejected,
            SupersededCadencePreview,
            TypeError,
            ValueError,
        ) as exc:
            status, error_code = _cadence_failure_status(exc)
            self._send_error(
                str(exc),
                status=status,
                error_code=error_code,
            )
        except Exception:  # noqa: BLE001 - write may have reached the store; force readback.
            self._send_error(
                "Automatic execution change could not be verified. Refresh policy before retrying.",
                status=500,
                error_code="automation_cadence_write_unknown",
            )
