"""Real HTTP dispatch and shared BM25; no active Goal or provider credentials."""
from __future__ import annotations

import http.client
import json
import threading

import pytest

from loopx.capabilities.decision_context.profile import resolve_decision_context_activation
from loopx.capabilities.decision_context.runtime import recall_profile_decision_context
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler

GOAL = "evidence-demo"
AGENT = "researcher"
PATH = "/api/chat/decision-context"
CONTENT = "The migration preserves transaction authority and typed semantics."


@pytest.fixture
def api(tmp_path):
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps({
        "schema_version": "local_bm25_corpus_v0", "scope_ref": "corpus:demo",
        "documents": [{"id": "semantics", "content": CONTENT}],
    }))
    profile = {
        "schema_version": "decision_context_profile_v0", "goal_id": GOAL,
        "enabled": True, "enabled_agents": [AGENT],
        "source_provider_bindings": [], "sources": [],
        "context_provider": {"provider": "bm25", "namespace": "decision-context",
            "scope_ref": "corpus:demo", "max_results": 4, "timeout_seconds": 2,
            "config": {"corpus_path": str(corpus)}},
        "automation": {"automatic_capture": False, "fail_open": True},
    }
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({
        "common_runtime_root": str(tmp_path / "runtime"),
        "goals": [{"id": GOAL, "repo": str(tmp_path)}],
    }))
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path, server.runtime_root = registry, tmp_path / "runtime"
    server.selected_goal_id, server.verbose = GOAL, False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def call(*, action="inspect", override=None, origin=None, suffix=""):
        body = {"action": action, "goal_id": GOAL, "agent_id": AGENT, "profile": profile}
        if action == "recall":
            body.update(query="transaction authority", context_scope_ref="corpus:demo", max_results=4)
        body.update(override or {})
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        connection.request("POST", PATH + suffix, json.dumps(body), headers)
        response = connection.getresponse()
        result = json.loads(response.read())
        assert response.getheader("Cache-Control") == "no-store"
        connection.close()
        return response.status, result

    yield call, profile, corpus, server
    server.shutdown()
    server.server_close()
    thread.join(5)


def test_real_recall_matches_file_entry_without_persisting_profile_or_query(api, tmp_path):
    call, profile, corpus, _ = api
    original = tmp_path / "original-profile.json"
    original.write_text(json.dumps(profile))
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    code, activation = call()
    assert code == 200 and activation["available"]
    assert activation == resolve_decision_context_activation(
        goal_id=GOAL, agent_id=AGENT, profile_path=original,
    )[0]
    code, recall = call(action="recall")
    assert code == 200 and recall["ok"] and recall["results"][0]["content"] == CONTENT
    native = recall_profile_decision_context(
        goal_id=GOAL, agent_id=AGENT, profile_path=original,
        context_scope_ref="corpus:demo", query="transaction authority",
        query_summary=recall["query_summary"], observed_at=recall["observed_at"],
        max_results=4,
    )
    assert recall["results"] == native["results"]
    assert recall["provider"] == native["provider"] == "bm25"
    for field in ("profile_write_performed", "source_scan_performed", "cursor_state_read",
                  "cursor_state_mutated", "pending_settlement_written", "raw_content_persisted",
                  "execution_authorized", "external_writes_performed"):
        assert recall[field] is False
    assert recall["content_may_instruct"] is False
    receipt = json.dumps(recall["retrieval_receipt"])
    assert CONTENT not in receipt and str(corpus) not in receipt
    assert "transaction authority" not in receipt
    after = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before


@pytest.mark.parametrize("patch, expected", [
    (None, "disabled"),
    ({"enabled": False}, "disabled"),
    ({"enabled_agents": ["another-agent"]}, "agent_not_enabled"),
    ({"goal_id": "another-goal"}, "profile_invalid"),
    ({"schema_version": "invalid"}, "profile_invalid"),
])
def test_inactive_profiles_never_build_or_call_provider(api, monkeypatch, patch, expected):
    from loopx.capabilities.decision_context import runtime
    call, profile, _, _ = api

    def forbidden(*args, **kwargs):
        pytest.fail("inactive profiles must not reach provider construction")

    monkeypatch.setattr(runtime, "_build_advisory_context_provider", forbidden)
    value = None if patch is None else profile | patch
    assert call(override={"profile": value})[1]["status"] == expected
    code, result = call(action="recall", override={"profile": value})
    assert code == 200 and not result["ok"] and result["status"] == expected
    assert result["results"] == []


def test_wrong_scope_provider_failure_and_real_file_recovery(api):
    call, _, corpus, _ = api
    code, mismatch = call(action="recall", override={"context_scope_ref": "corpus:foreign"})
    assert code == 200 and not mismatch["ok"] and mismatch["reason_code"] == "scope_mismatch"
    content = corpus.read_bytes()
    corpus.unlink()
    unavailable = call(action="recall")[1]
    assert not unavailable["ok"] and unavailable["reason_code"] == "corpus_unavailable"
    corpus.write_bytes(content)
    recovered = call(action="recall")[1]
    assert recovered["ok"] and recovered["results"][0]["content"] == CONTENT
    assert call(action="recall", override={"query": "unmatchedterm"})[1]["results"] == []


def test_source_profile_without_recall_provider_is_not_recall_readiness(api):
    call, profile, corpus, _ = api
    override = {"profile": profile | {
        "context_provider": None,
        "source_provider_bindings": [{"provider_id": "authority", "adapter": "local-file", "config": {}}],
        "sources": [{"source_id": "source:authority", "provider_id": "authority", "source_kind": "artifact",
            "priority": "p0", "evidence_level": "s3", "objectives": ["architecture"],
            "scan_mode": "incremental", "exact_read_policy": "material_change", "freshness_seconds": 3600,
            "private_locator": str(corpus), "max_changes": 4, "enabled": True}],
    }}
    activation = call(override=override)[1]
    assert activation["available"] is True and activation["context_provider_configured"] is False
    result = call(action="recall", override=override)[1]
    assert not result["ok"] and result["reason_code"] == "context_provider_not_configured"
    assert result["results"] == []


@pytest.mark.parametrize("override", [
    {"action": "capture"}, {"profile_path": "private-profile.json"},
    {"goal_id": "other-goal"}, {"agent_id": ""}, {"profile": []},
    {"max_results": True}, {"max_results": 9}, {"query": "x" * 1001},
    {"context_scope_ref": ""},
])
def test_request_bounds_and_private_field_errors_do_not_echo_input(api, tmp_path, override):
    call, _, _, _ = api
    code, refused = call(action="recall", override=override)
    assert code == 400 and not refused["ok"]
    assert str(tmp_path) not in json.dumps(refused)
    assert "private-profile" not in json.dumps(refused)


def test_origin_query_and_registered_goal_are_enforced_before_recall(api):
    call, _, _, server = api
    assert call(action="recall", origin="https://foreign.example")[0] == 403
    assert call(suffix="?query=must-not-log-private-queries")[0] == 400
    assert call(override={"query": "inspection-must-not-recall"})[0] == 400
    registry = json.loads(server.registry_path.read_text())
    registry["goals"] = []
    server.registry_path.write_text(json.dumps(registry))
    assert call()[0] == 400


@pytest.mark.parametrize("action", ["inspect", "recall"])
def test_lifecycle_only_registry_is_refused_before_provider_construction(api, monkeypatch, action):
    from loopx.capabilities.decision_context import runtime
    call, _, _, server = api

    def forbidden(*args, **kwargs):
        pytest.fail("lifecycle-only registries must not reach provider construction")

    monkeypatch.setattr(runtime, "_build_advisory_context_provider", forbidden)
    server.registry_path.write_text(json.dumps({
        "profile_id": "source_session_v1", "goals": [{"id": GOAL}],
    }))
    code, refused = call(action=action)
    assert code == 400 and not refused["ok"]
    assert "Decision Context request unavailable" in json.dumps(refused)
