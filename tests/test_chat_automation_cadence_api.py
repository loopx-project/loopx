"""Real loopback settings path through the quota-owned cadence store."""

from __future__ import annotations

import http.client
import json
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest

from loopx import chat_automation_cadence_api
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.control_plane.effect_runtime import (
    EffectRuntimeConflict,
    effect_runtime_result,
)


def _exchange(
    port: int, method: str, path: str, body: dict | None = None
) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    connection.request(
        method,
        path,
        body=json.dumps(body).encode() if body is not None else None,
        headers={"Origin": "http://127.0.0.1:5320", "Content-Type": "application/json"},
    )
    response = connection.getresponse()
    result = response.status, json.loads(response.read())
    connection.close()
    return result


def test_chat_cadence_preview_apply_inheritance_and_stale_rejection(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps({"schema_version": "0.1", "goals": [{"id": "goal-one"}]})
    )
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path = registry
    server.runtime_root = tmp_path / "runtime"
    server.verbose = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    path = "/api/chat/automation-cadence"
    try:
        status, initial = _exchange(port, "GET", f"{path}?goal_id=goal-one")
        assert status == 200
        assert initial["min_interval_minutes"] == 0
        assert initial["sources"] == []
        assert initial["pre_model_admission"] == "not_qualified"
        assert initial["eligibility"] == {
            "state": "unconfigured",
            "reason": "unconfigured",
            "eligible_now": None,
            "next_eligible_at_ms": None,
        }

        change = {
            "goal_id": "goal-one",
            "agent_id": None,
            "automation_id": None,
            "min_interval_minutes": 60,
            "expected_revision": initial["configuration_revision"],
            "owner_reference": "Owner requested hourly automatic runs",
            "approve_reduction": False,
        }
        status, preview = _exchange(port, "POST", f"{path}/preview", change)
        assert status == 200 and preview["written"] is False
        assert preview["min_interval_minutes"] == 60
        assert "Owner requested" not in json.dumps(preview)
        assert (
            _exchange(port, "GET", f"{path}?goal_id=goal-one")[1][
                "min_interval_minutes"
            ]
            == 0
        )

        status, applied = _exchange(
            port,
            "POST",
            f"{path}/apply",
            {
                **change,
                "preview_revision": preview["preview_revision"],
            },
        )
        assert status == 200 and applied["readback_verified"] is True
        assert applied["min_interval_minutes"] == 60
        assert (
            _exchange(
                port,
                "POST",
                f"{path}/apply",
                {
                    **change,
                    "preview_revision": preview["preview_revision"],
                },
            )[0]
            == 409
        )
        # An apply whose preview no longer matches its own fields is the same
        # refresh-and-retry conflict, not an invalid request.
        stale_preview_status, stale_preview = _exchange(
            port,
            "POST",
            f"{path}/apply",
            {**change, "preview_revision": "0" * 64},
        )
        assert stale_preview_status == 409
        assert stale_preview["error_code"] == "automation_cadence_conflict"

        query = urlencode(
            {"goal_id": "goal-one", "agent_id": "agent-a", "automation_id": "daily"}
        )
        status, inherited = _exchange(port, "GET", f"{path}?{query}")
        assert status == 200 and inherited["min_interval_minutes"] == 60
        assert inherited["sources"] == [
            {
                "agent_id": None,
                "automation_id": None,
                "min_interval_minutes": 60,
                "revision": 1,
            }
        ]
        assert inherited["eligibility"] == {
            "state": "eligible",
            "reason": "owner_minimum_interval",
            "eligible_now": True,
            "next_eligible_at_ms": None,
        }
        now_ms = int(time.time() * 1_000)
        admitted = effect_runtime_result(
            "quota.automation_cadence.admit",
            {
                "runtime_root": str(server.runtime_root),
                "goal_id": "goal-one",
                "agent_id": "agent-a",
                "automation_id": "daily",
                "request_id": "api-fixture-start",
                "now_ms": now_ms,
                "trigger_at_ms": now_ms,
            },
            retry_safe=False,
        )
        assert admitted["admitted"] is True
        status, waiting = _exchange(port, "GET", f"{path}?{query}")
        assert status == 200
        assert waiting["eligibility"] == {
            "state": "waiting",
            "reason": "minimum_interval_wait",
            "eligible_now": False,
            "next_eligible_at_ms": now_ms + 60 * 60 * 1_000,
        }
        assert "owner_reference" not in json.dumps(waiting)

        lower = {
            **change,
            "min_interval_minutes": 30,
            "expected_revision": applied["configuration_revision"],
        }
        assert _exchange(port, "POST", f"{path}/preview", lower)[0] == 400
        status, approved = _exchange(
            port, "POST", f"{path}/preview", {**lower, "approve_reduction": True}
        )
        assert status == 200 and approved["min_interval_minutes"] == 30

        # A per-Agent setting must not become a Goal default or affect a peer.
        agent_change = {
            **change,
            "agent_id": "agent-a",
            "min_interval_minutes": 120,
            "expected_revision": applied["configuration_revision"],
        }
        status, agent_preview = _exchange(port, "POST", f"{path}/preview", agent_change)
        assert status == 200 and agent_preview["min_interval_minutes"] == 120
        status, agent_applied = _exchange(
            port,
            "POST",
            f"{path}/apply",
            {
                **agent_change,
                "preview_revision": agent_preview["preview_revision"],
            },
        )
        assert status == 200 and agent_applied["readback_verified"] is True
        for agent_id, expected in (("agent-a", 120), ("agent-b", 60)):
            agent_query = urlencode({"goal_id": "goal-one", "agent_id": agent_id})
            status, observed = _exchange(port, "GET", f"{path}?{agent_query}")
            assert status == 200 and observed["min_interval_minutes"] == expected
        assert (
            _exchange(port, "GET", f"{path}?goal_id=goal-one")[1][
                "min_interval_minutes"
            ]
            == 60
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_chat_cadence_conflict_status_ignores_message_wording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The conflict contract comes from the failure type, not from its text."""

    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps({"schema_version": "0.1", "goals": [{"id": "goal-one"}]})
    )
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path = registry
    server.runtime_root = tmp_path / "runtime"
    server.verbose = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    path = "/api/chat/automation-cadence"

    def advanced(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise EffectRuntimeConflict(
            "policy advanced", diagnostic_code="automation_cadence_revision_conflict"
        )

    monkeypatch.setattr(chat_automation_cadence_api, "effect_runtime_result", advanced)
    try:
        status, payload = _exchange(
            port,
            "POST",
            f"{path}/preview",
            {
                "goal_id": "goal-one",
                "agent_id": None,
                "automation_id": None,
                "min_interval_minutes": 60,
                "expected_revision": 0,
                "owner_reference": "Owner requested hourly automatic runs",
                "approve_reduction": False,
            },
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
    assert status == 409
    assert payload["error_code"] == "automation_cadence_conflict"


def test_invalid_owner_readback_is_never_reported_as_a_client_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps({"schema_version": "0.1", "goals": [{"id": "goal-one"}]})
    )
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path = registry
    server.runtime_root = tmp_path / "runtime"
    server.verbose = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    path = "/api/chat/automation-cadence"

    def malformed(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "schema_version": "automation_cadence_result_v1",
            "ok": True,
            "goal_id": "goal-one",
            "agent_id": None,
            "automation_id": None,
            "configuration_revision": 0,
            "min_interval_minutes": 60,
            "eligibility": {
                "state": "waiting",
                "reason": "minimum_interval_wait",
                "eligible_now": False,
                "next_eligible_at_ms": None,
            },
            "enabled": True,
            "enforcement": "managed_turn_atomic_admission_and_schedule_recommendation",
            "pre_model_admission": "managed_turn_only",
            "sources": [],
        }

    monkeypatch.setattr(chat_automation_cadence_api, "effect_runtime_result", malformed)
    change = {
        "goal_id": "goal-one",
        "agent_id": None,
        "automation_id": None,
        "min_interval_minutes": 60,
        "expected_revision": 0,
        "owner_reference": "Owner requested hourly automatic runs",
        "approve_reduction": False,
    }
    preview_revision = chat_automation_cadence_api._request(
        change,
        execute=False,
        runtime_root=str(server.runtime_root),
    )["preview_revision"]
    try:
        read_status, read_payload = _exchange(
            port, "GET", f"{path}?goal_id=goal-one"
        )
        write_status, write_payload = _exchange(
            port,
            "POST",
            f"{path}/apply",
            {**change, "preview_revision": preview_revision},
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert read_status == 500
    assert read_payload["error_code"] == "automation_cadence_read_failed"
    assert write_status == 500
    assert write_payload["error_code"] == "automation_cadence_write_unknown"
    assert "next_eligible_at_ms" not in json.dumps(
        [read_payload, write_payload]
    )
