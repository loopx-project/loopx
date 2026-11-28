"""Actual SDK transport shapes for deletion while execution is still running."""
import asyncio
from dataclasses import replace
import json

import httpx
import pytest

from loopx_ark_turn.config import AdapterError
from loopx_ark_turn.receipt import Receipt
from test_host import Provider, config, execute, request


class RunningProvider(Provider):
    def __init__(self, *, stop=True, interrupt_timeout=False, wrong_owner=False, status="running"):
        super().__init__([])
        self.running = status
        self.stop = stop
        self.interrupt_timeout = interrupt_timeout
        self.wrong_owner = wrong_owner
        self.interrupts = 0

    def __call__(self, req):
        path = req.url.path.removeprefix("/api/v3")
        body = json.loads(req.content) if req.content else {}
        if req.method == "DELETE" and path == "/sessions/sesn-fixture" and path not in self.deleted:
            if self.running not in {"idle", "terminated"}:
                self.calls.append((req.method, path, body))
                return httpx.Response(400, json={"error": {"code": "InvalidAction", "message": "synthetic running session"}})
        if req.method == "POST" and path.endswith("/events") and body["events"][0]["type"] == "user.interrupt":
            self.calls.append((req.method, path, body))
            self.interrupts += 1
            if self.interrupt_timeout:
                raise httpx.ReadTimeout("synthetic lost interrupt ACK", request=req)
            if self.stop:
                self.running = "idle"
            return httpx.Response(200, json={"data": [{"id": "interrupt-fixture", "type": "user.interrupt"}]})
        response = super().__call__(req)
        value = response.json()
        if req.method == "POST" and path.endswith("/events"):
            value["data"][0].pop("session_thread_id", None)
        if req.method == "GET" and path == "/sessions/sesn-fixture" and response.status_code == 200:
            value["status"] = self.running
            if self.wrong_owner and any(m == "DELETE" for m, _, _ in self.calls):
                value["agent"]["id"] = "another-agent"
        return httpx.Response(response.status_code, json=value)


def state(cfg):
    return json.loads(Receipt(cfg.state_dir, request()["turn_key"]).path.read_text())


@pytest.mark.parametrize("enabled", [False, True])
def test_missing_thread_ack_stops_owned_execution_preserves_failure_and_input(tmp_path, enabled):
    cfg = replace(config(tmp_path, tools=False), sandbox_builtins=enabled)
    p = RunningProvider()
    with pytest.raises(AdapterError, match="message_receipt_thread_missing"):
        asyncio.run(execute(p, cfg))
    r = state(cfg)
    assert r["stage"] == "sending_input" and r["input_cursor"] == "e0"
    assert r["error"] == "message_receipt_thread_missing" and not r.get("candidate")
    assert r["cleanup"] == {"session": "absent", "agent": "absent"}
    assert r["cleanup_interrupt_attempted"] is True
    assert p.interrupts == 1
    assert p.deleted == {"/sessions/sesn-fixture", "/agents/agnt-fixture"}
    assert sum(m == "POST" and b.get("events", [{}])[0].get("type") == "user.message" for m, _, b in p.calls) == 1
    count = len(p.calls)
    with pytest.raises(AdapterError, match="previous_attempt_requires_reconciliation"):
        asyncio.run(execute(p, cfg))
    assert p.calls[count:] == []


@pytest.mark.parametrize("kwargs", [{"stop": False}, {"interrupt_timeout": True}, {"wrong_owner": True}, {"status": "unknown"}])
@pytest.mark.parametrize("enabled", [False, True])
def test_unverified_stop_retains_parent_and_never_repeats_interrupt(tmp_path, kwargs, enabled):
    cfg = replace(config(tmp_path, tools=False), sandbox_builtins=enabled)
    p = RunningProvider(**kwargs)
    with pytest.raises(AdapterError, match="message_receipt_thread_missing"):
        asyncio.run(execute(p, cfg))
    r = state(cfg)
    assert r["cleanup"] == {"session": "pending"} and not p.deleted
    interrupts = p.interrupts
    assert interrupts <= 1
    before = len(p.calls)
    with pytest.raises(AdapterError, match="previous_attempt_requires_reconciliation"):
        asyncio.run(execute(p, cfg))
    assert p.interrupts == interrupts
    assert not any(m == "POST" for m, _, _ in p.calls[before:])
    assert len([1 for m, path, _ in p.calls if m == "GET" and path == "/sessions/sesn-fixture"]) <= 6


def test_lost_interrupt_ack_can_later_retire_idle_session_without_resend(tmp_path):
    cfg = config(tmp_path, tools=False)
    p = RunningProvider(interrupt_timeout=True)
    with pytest.raises(AdapterError, match="message_receipt_thread_missing"):
        asyncio.run(execute(p, cfg))
    assert state(cfg)["cleanup_interrupt_attempted"]
    p.running = "idle"
    count = len(p.calls)
    with pytest.raises(AdapterError, match="previous_attempt_requires_reconciliation"):
        asyncio.run(execute(p, cfg))
    assert state(cfg)["cleanup"] == {"session": "absent", "agent": "absent"}
    assert not any(m == "POST" for m, _, _ in p.calls[count:])


def test_normal_idle_cleanup_has_no_interrupt_or_additional_predelete_read(tmp_path):
    cfg = config(tmp_path, tools=False)
    p = Provider([])
    asyncio.run(execute(p, cfg))
    assert not any(b.get("events", [{}])[0].get("type") == "user.interrupt" for _, _, b in p.calls)
    first_delete = next(i for i, row in enumerate(p.calls) if row[0] == "DELETE")
    assert [m for m, _, _ in p.calls[first_delete:]] == ["DELETE", "GET", "DELETE", "GET"]


def test_interrupt_attempt_is_visible_only_when_present_in_receipt_projection(tmp_path):
    r = Receipt(tmp_path, request()["turn_key"])
    r.data = {}
    assert "cleanup_interrupt_attempted" not in r.projection()
    r.data["cleanup_interrupt_attempted"] = True
    assert r.projection()["cleanup_interrupt_attempted"] is True
