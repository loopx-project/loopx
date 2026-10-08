from __future__ import annotations

import asyncio
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sys
import copy

import httpx
import pytest
from arkruntime import AsyncArk

from loopx.control_plane.quota.turn_envelope import turn_envelope_action_signature_document
from loopx_ark_turn.config import AdapterError, Config
from loopx_ark_turn.host import run
from loopx_ark_turn.receipt import Receipt


def request() -> dict:
    envelope = {
        "schema_version": "loopx_turn_envelope_v0", "goal_id": "public-goal", "agent_id": "analyst",
        "action": {"primary_action": "Write the observation, then return a progress candidate.", "selected_todo": {"todo_id": "todo_fixture"}},
        "required_reads": [], "boundary": {"write_scope": ["observation.json"], "workspace_guard": {}},
    }
    signature = "sha256:" + sha256(json.dumps(turn_envelope_action_signature_document(envelope), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    envelope["action_signature"] = {"matches": True, "source_hash": signature, "envelope_hash": signature}
    return {
        "schema_version": "loopx_turn_host_request_v0", "turn_key": "sha256:" + "1" * 64,
        "session": {"context_policy": {"mode": "fresh"}}, "turn_envelope": envelope,
    }


def config(tmp_path: Path, *, tools: bool = True) -> Config:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return Config(
        model="public-model", environment_id="env-fixture", workspace=workspace,
        state_dir=tmp_path / "receipts", timeout_seconds=10, tool_timeout_seconds=5,
        poll_interval_seconds=0.01,
        mcp_command=(sys.executable, str(Path(__file__).with_name("fixture_server.py"))) if tools else (),
        tool_names=("write_observation",) if tools else (),
    )


def event(event_id: str, kind: str, **fields) -> dict:
    return {"id": event_id, "type": kind, **fields}


def tool_event(event_id: str = "e1", *, value: int = 42, name: str = "write_observation") -> dict:
    return event(event_id, "agent.custom_tool_use", session_thread_id="thread-root", name=name, input={"value": value})


class Provider:
    """Public wire fixtures exercised through the real SDK, not a fake SDK API."""
    def __init__(self, events: list[dict] | None = None) -> None:
        candidate = json.dumps({"result_kind": "validated_progress", "classification": "observation_written", "summary": "Observation recorded.", "next_action": "Independently validate the observation."})
        self.before = events if events is not None else [tool_event(), event("e2", "session.status_idle", stop_reason={"type": "requires_action"})]
        self.after = [event("e3", "agent.message", session_thread_id="thread-root", content=[{"type": "text", "text": candidate}]), event("e4", "session.status_idle", stop_reason={"type": "end_turn"})]
        self.calls: list[tuple[str, str, dict]] = []
        self.deleted: set[str] = set()
        self.results: list[dict] = []
        self.bad_binding = False
        self.bad_capabilities = False
        self.agent_snapshot = {}
        self.fail_delete = False
        self.fail_create = False
        self.empty_forever = False
        self.startup_events = []
        self.environment_snapshot = {"id": "env-fixture", "type": "environment", "config": {"type": "cloud"}}
        self.frozen_environment = None
        self.session_extras = {"resources": [], "vault_ids": []}

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path.removeprefix("/api/v3")
        body = json.loads(req.content) if req.content else {}
        self.calls.append((req.method, path, body))
        if req.method == "DELETE":
            if self.fail_delete:
                return httpx.Response(503, json={"error": {"message": "synthetic unavailable"}})
            self.deleted.add(path)
            return httpx.Response(200, json={"deleted": True, "id": path.split("/")[-1]})
        if req.method == "GET" and path in self.deleted:
            return httpx.Response(404, json={"error": {"message": "not found"}})
        if path == "/agents" and req.method == "POST":
            if self.fail_create:
                raise httpx.ReadTimeout("synthetic ambiguous create", request=req)
            assert body["tools"][0]["type"] == "agent_toolset_20260701"
            assert all(t["type"] == "custom" for t in body["tools"][1:])
            self.agent_snapshot = body
            return httpx.Response(200, json={"id": "agnt-fixture", "type": "agent", **body})
        if path == "/sessions" and req.method == "POST":
            if "environment" in body and self.frozen_environment is None:
                self.frozen_environment = {"id": "env-fixture", "config": body["environment"]["config"]}
            return httpx.Response(200, json={"id": "sesn-fixture", "type": "session", "status": "idle", "agent": {"id": "agnt-fixture"}, "environment_id": "env-fixture"})
        if path == "/environments/env-fixture" and req.method == "GET":
            return httpx.Response(200, json=self.environment_snapshot)
        if path == "/sessions/sesn-fixture" and req.method == "GET":
            if self.bad_capabilities:
                self.agent_snapshot["tools"] = [{"type": "agent_toolset_20260701"}]
            return httpx.Response(200, json={"id": "sesn-fixture", "type": "session", "status": "idle", "agent": {"id": "other" if self.bad_binding else "agnt-fixture", **self.agent_snapshot}, "environment_id": "env-fixture", "environment": self.frozen_environment, **self.session_extras, "usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 0}})
        if path.endswith("/events") and req.method == "POST":
            sent = body["events"][0]
            if sent["type"] == "user.custom_tool_result":
                self.results.append(sent)
                return httpx.Response(200, json={"data": [{"id": "result1", **sent}]})
            assert sent["type"] == "user.message"
            return httpx.Response(200, json={"data": [{"id": "e0", "type": "user.message", "session_thread_id": "thread-root"}]})
        if path.endswith("/events") and req.method == "GET":
            assert "after" not in req.url.params
            offset = int(req.url.params.get("page", "opaque-0").removeprefix("opaque-"))
            history = [*self.startup_events, event("startup", "session.status_idle", stop_reason={"type": "end_turn"}),
                       event("e0", "user.message", session_thread_id="thread-root")]
            if not self.empty_forever:
                history += self.before
                if self.results or not any(e["type"] == "agent.custom_tool_use" for e in self.before):
                    history += self.after
            page = history[offset:offset + 100]
            next_page = "opaque-" + str(offset + 100) if len(history) > offset + 100 else None
            return httpx.Response(200, json={"data": page, "next_page": next_page})
        raise AssertionError((req.method, path))


async def execute(provider: Provider, cfg: Config, req: dict | None = None) -> dict:
    async with AsyncArk(api_key="public-fixture", max_retries=0, http_client=httpx.AsyncClient(transport=httpx.MockTransport(provider))) as client:
        return await run(req or request(), cfg, client)


@pytest.mark.parametrize("checkpoint", ["running", "terminal"])
def test_original_cloud_checkpoint_resumes_without_new_input_or_tool_effect(tmp_path, monkeypatch, checkpoint):
    cfg = config(tmp_path)
    provider = Provider()
    captured = []
    save = Receipt.save

    def capture(receipt):
        save(receipt)
        if receipt.data["stage"] == checkpoint and receipt.data.get("tools", {}).get("e1", {}).get("stage") == "sent":
            captured.append(copy.deepcopy(receipt.data))

    monkeypatch.setattr(Receipt, "save", capture)
    asyncio.run(execute(provider, cfg))
    assert captured
    # Restore an actual persisted execution checkpoint. The fixture provider
    # retains the already accepted effect and original event history.
    state = next(row for row in captured if not row.get("cleanup") and not row.get("candidate"))
    path = Receipt(cfg.state_dir, request()["turn_key"]).path
    path.write_text(json.dumps(state))
    provider.deleted.clear()
    count = len(provider.calls)
    assert asyncio.run(execute(provider, cfg))["result_kind"] == "validated_progress"
    assert not [row for row in provider.calls[count:] if row[0] == "POST"]
    assert json.loads((cfg.workspace / "observation.json").read_text())["calls"] == 1


def test_uncertain_tool_checkpoint_preserves_resources_for_reconciliation(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    provider = Provider()
    captured = []
    save = Receipt.save

    def capture(receipt):
        save(receipt)
        if receipt.data.get("tools", {}).get("e1", {}).get("stage") == "executing":
            captured.append(copy.deepcopy(receipt.data))

    monkeypatch.setattr(Receipt, "save", capture)
    asyncio.run(execute(provider, cfg))
    Receipt(cfg.state_dir, request()["turn_key"]).path.write_text(json.dumps(captured[0]))
    provider.deleted.clear()
    count = len(provider.calls)
    with pytest.raises(AdapterError, match="requires_reconciliation"):
        asyncio.run(execute(provider, cfg))
    assert provider.calls[count:] == []
    assert json.loads((cfg.workspace / "observation.json").read_text())["calls"] == 1


def test_real_stdio_tools_are_bound_and_pending_tool_idle_is_not_completion(tmp_path, monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "must-not-reach-tool")
    cfg = config(tmp_path)
    provider = Provider()
    result = asyncio.run(execute(provider, cfg))
    assert result["result_kind"] == "validated_progress"
    assert result["turn_key"] == request()["turn_key"]
    observed = json.loads((cfg.workspace / "observation.json").read_text())
    assert observed == {"value": 42, "calls": 1, "agent": "analyst", "goal": "public-goal", "todo": "todo_fixture", "provider_credential_present": False}
    assert provider.results[0]["custom_tool_use_id"] == "e1"
    assert provider.results[0]["session_thread_id"] == "thread-root"
    assert provider.deleted == {"/sessions/sesn-fixture", "/agents/agnt-fixture"}
    receipt = json.loads(Receipt(cfg.state_dir, request()["turn_key"]).path.read_text())
    assert receipt["provider_usage"]["output_tokens"] == 20
    assert receipt["stage"] == "finished"
    before = len(provider.calls)
    assert asyncio.run(execute(provider, cfg)) == result
    assert len(provider.calls) == before  # Exact replay neither launches nor repeats a tool.


@pytest.mark.parametrize("mutation", ["signature", "context", "identity"])
def test_invalid_request_never_starts_provider(tmp_path, mutation):
    cfg = config(tmp_path, tools=False)
    req = request()
    if mutation == "signature":
        req["turn_envelope"]["action"]["primary_action"] = "Forged change"
    elif mutation == "context":
        req["session"]["context_policy"]["mode"] = "resume"
    else:
        req["turn_key"] = "not-a-turn-key"
    provider = Provider([])
    with pytest.raises(ValueError):
        asyncio.run(execute(provider, cfg, req))
    assert provider.calls == []


def test_duplicate_tool_identity_does_not_repeat_effect(tmp_path):
    cfg = config(tmp_path)
    provider = Provider([tool_event(), tool_event()])
    asyncio.run(execute(provider, cfg))
    assert len(provider.results) == 1
    assert json.loads((cfg.workspace / "observation.json").read_text())["calls"] == 1


def test_page_based_history_reaches_tools_beyond_first_page(tmp_path):
    provider = Provider([*[event("span" + str(i), "span.model_request_start") for i in range(205)], tool_event()])
    asyncio.run(execute(provider, config(tmp_path)))
    assert len(provider.results) == 1
    assert provider.results[0]["custom_tool_use_id"] == "e1"


@pytest.mark.parametrize("events,error", [
    ([tool_event(), tool_event(value=7)], "event_identity_conflict"),
    ([tool_event(name="unselected_tool")], "tool_not_in_bound_selection"),
    ([event("e1", "session.status_idle", stop_reason={"type": "end_turn"})], "terminal_without_candidate"),
    ([event("e1", "session.status_idle", stop_reason={"type": "user_interrupt"})], "unsupported_provider_stop_reason"),
    ([event("e1", "session.error")], "provider_execution_failed"),
])
def test_negative_event_paths_reject_and_retire_owned_resources(tmp_path, events, error):
    cfg = config(tmp_path)
    provider = Provider(events)
    with pytest.raises(Exception, match=error):
        asyncio.run(execute(provider, cfg))
    assert provider.deleted == {"/sessions/sesn-fixture", "/agents/agnt-fixture"}


def test_cleanup_failure_withholds_candidate_and_exact_retry_only_cleans_up(tmp_path):
    cfg = config(tmp_path, tools=False)
    provider = Provider([])
    provider.fail_delete = True
    with pytest.raises(AdapterError, match="cleanup"):
        asyncio.run(execute(provider, cfg))
    assert provider.deleted == set()
    provider.fail_delete = False
    result = asyncio.run(execute(provider, cfg))
    assert result["result_kind"] == "validated_progress"
    assert sum(method == "POST" and path == "/sessions" for method, path, _ in provider.calls) == 1


def test_changed_same_key_is_rejected_without_provider_calls(tmp_path):
    cfg = config(tmp_path, tools=False)
    provider = Provider([])
    asyncio.run(execute(provider, cfg))
    before = len(provider.calls)
    with pytest.raises(AdapterError, match="binding_mismatch"):
        asyncio.run(execute(provider, replace(cfg, model="another-model")))
    assert len(provider.calls) == before


def test_wrong_session_binding_is_rejected_before_input(tmp_path):
    cfg = config(tmp_path, tools=False)
    provider = Provider([])
    provider.bad_binding = True
    with pytest.raises(AdapterError, match="binding_mismatch"):
        asyncio.run(execute(provider, cfg))
    assert not any(path.endswith("/events") for _, path, _ in provider.calls)


def test_unexpected_provider_capabilities_are_rejected_before_input(tmp_path):
    provider = Provider([])
    provider.bad_capabilities = True
    with pytest.raises(AdapterError, match="capabilities_mismatch"):
        asyncio.run(execute(provider, config(tmp_path, tools=False)))
    assert not any(path.endswith("/events") for _, path, _ in provider.calls)


def test_ambiguous_create_is_not_automatically_retried(tmp_path):
    cfg = config(tmp_path, tools=False)
    provider = Provider([])
    provider.fail_create = True
    with pytest.raises(Exception):
        asyncio.run(execute(provider, cfg))
    with pytest.raises(AdapterError, match="reconciliation"):
        asyncio.run(execute(provider, cfg))
    assert len(provider.calls) == 1
    receipt = json.loads(Receipt(cfg.state_dir, request()["turn_key"]).path.read_text())
    assert receipt["cleanup"]["unknown_creation"] == "reconcile_required"


def test_tool_budget_prevents_second_effect(tmp_path):
    cfg = replace(config(tmp_path), max_tool_calls=1)
    provider = Provider([tool_event(), tool_event("e2")])
    with pytest.raises(Exception, match="tool_call_budget"):
        asyncio.run(execute(provider, cfg))
    assert json.loads((cfg.workspace / "observation.json").read_text())["calls"] == 1


def test_timeout_does_not_return_progress_and_cleans_resources(tmp_path):
    cfg = replace(config(tmp_path, tools=False), timeout_seconds=0.1)
    provider = Provider([])
    provider.empty_forever = True
    with pytest.raises(TimeoutError):
        asyncio.run(execute(provider, cfg))
    assert provider.deleted == {"/sessions/sesn-fixture", "/agents/agnt-fixture"}


def test_cancel_and_competing_start_do_not_launch_another_session(tmp_path):
    from loopx.file_lock import LockAcquireTimeoutError

    cfg = config(tmp_path, tools=False)
    provider = Provider([])
    provider.empty_forever = True

    async def exercise():
        active = asyncio.create_task(execute(provider, cfg))
        async with asyncio.timeout(5):
            while not any(path.endswith("/events") for _, path, _ in provider.calls):
                await asyncio.sleep(0.01)
        before = len(provider.calls)
        with pytest.raises(LockAcquireTimeoutError):
            await execute(provider, cfg)
        assert len(provider.calls) == before
        active.cancel()
        with pytest.raises(asyncio.CancelledError):
            await active

    asyncio.run(exercise())
    assert sum(method == "POST" and path == "/sessions" for method, path, _ in provider.calls) == 1
    assert provider.deleted == {"/sessions/sesn-fixture", "/agents/agnt-fixture"}


@pytest.mark.parametrize("override", [
    {"mcp_env": ("ARK_API_KEY",)}, {"mcp_env": ("LOOPX_TURN_AGENT_ID",)},
    {"timeout_seconds": float("nan")}, {"max_tool_calls": 0},
    {"base_url": "https://user:password@example.com/api/v3"},
])
def test_unsafe_configuration_is_rejected(tmp_path, override):
    with pytest.raises(AdapterError):
        replace(config(tmp_path, tools=False), **override)
