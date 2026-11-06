"""Real canonical Todos and HTTP reads; no live Goal, model or external sink."""
from copy import deepcopy
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from tests.capabilities.test_periodic_report_bindings import _document
from loopx.capabilities.periodic_report.bindings import build_periodic_report_generation_bundle
from loopx.capabilities.periodic_report.review import request_report_review, read_report_review, report_review_inventory
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.todos.mutation_api import update_goal_todo
from loopx.presentation.renderers.periodic_report_html import render_periodic_report_html
from loopx.presentation.renderers.periodic_report_markdown import render_periodic_report_markdown


def review_bundle(title="研究材料：结论、变化与待判断事项"):
    document = _document()
    document["title"] = title
    return build_periodic_report_generation_bundle(document=document,
        artifacts=[render_periodic_report_markdown(document), render_periodic_report_html(document)])


def test_review_configuration_is_opt_in_and_only_on_original_report_conversation():
    from loopx.capabilities.periodic_report.goal_configuration import normalize_configuration
    from loopx.capabilities.configuration_ui import build_capability_configuration_catalog
    from loopx.capabilities.machine_configuration.builtins import build_builtin_machine_configuration_registry
    from loopx.chat.machine_configuration_api import _public_payload
    base = {"enabled": True, "profile_preset": "weekly-progress", "route_ref": "managed-session:" + "a" * 32}
    assert "review_required" not in normalize_configuration(base)
    assert normalize_configuration({**base, "review_required": False})["review_required"] is False
    assert normalize_configuration({**base, "review_required": True})["review_required"] is True
    for change in [{"route_ref": "another-channel"}, {"profile_preset": "session-task"}, {"review_required": "true"}]:
        with pytest.raises((ValueError, TypeError)):
            normalize_configuration({**base, "review_required": True, **change})
    registry = build_builtin_machine_configuration_registry()
    machine = _public_payload({}, registry=registry)["capability_catalog"]
    report = next(row for row in machine["capabilities"] if row["capability_id"] == "periodic_report")
    assert "review_required" not in {field["key"] for field in report["configuration_editor"]["fields"]}
    goal = build_capability_configuration_catalog(goal_features=[{"feature_id": "periodic_report"}])
    assert "review_required" in {field["key"] for field in goal["capabilities"][0]["configuration_editor"]["fields"]}


def test_unconfigured_legacy_goal_stays_quiet_without_creating_material(tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, registry, state = tmp_path / "runtime", tmp_path / "registry.json", tmp_path / "state.md"
    state.write_text("# Legacy Goal\n\n## User Todo\n\n## Agent Todo\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{"id": "legacy", "repo": str(tmp_path),
        "state_file": state.name, "coordination": {"registered_agents": ["writer"]}}]}))
    scope = dict(registry_path=registry, runtime_root=runtime, goal_id="legacy")
    before = state.read_bytes()
    from loopx.control_plane.coordination.local_authority import CanonicalAuthorityRequired
    with pytest.raises(CanonicalAuthorityRequired):
        report_review_inventory(**scope)
    with pytest.raises(CanonicalAuthorityRequired):
        request_report_review(**scope, agent_id="writer", bundle=review_bundle(), execute=True)
    assert state.read_bytes() == before and not (runtime / "goals/legacy/periodic_reports/review-bundles").exists()
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path, server.runtime_root_override, server.verbose = registry, str(runtime), False
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/chat/report-reviews?goal_id=legacy") as response:
            unavailable = json.load(response)
        assert unavailable["available"] is False and unavailable["reason_code"] == "canonical_authority_required"
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


@pytest.fixture(params=["file", "sqlite"])
def review_goal(tmp_path, monkeypatch, request):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, state, registry = tmp_path / "runtime", tmp_path / "state.md", tmp_path / "registry.json"
    state.write_text("# Research\n\n## User Todo\n\n## Agent Todo\n", encoding="utf-8")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{"id": "research",
        "status": "active", "goal_instance_id": "review-instance", "domain": "research", "adapter": {"kind": "generic"},
        "repo": str(tmp_path), "state_file": state.name, "coordination": {"registered_agents": ["researcher", "peer"]}}]}))
    initialize_canonical_authority(runtime, "research", build_todo_runtime_shadow_projection(
        goal_id="research", handoff_mode="soft_claim", todos=[]), state_path=state, provider=request.param)
    return {"registry_path": registry, "runtime_root": runtime, "goal_id": "research"}


def test_read_is_passive_replay_does_not_reopen_and_new_editions_require_review(review_goal):
    bundle = review_bundle()
    dry = request_report_review(**review_goal, agent_id="researcher", bundle=bundle)
    assert dry["dry_run"] and report_review_inventory(**review_goal) == []
    first = request_report_review(**review_goal, agent_id="researcher", bundle=bundle, execute=True)
    before = report_review_inventory(**review_goal)
    for _ in range(2):
        read = read_report_review(**review_goal, todo_id=first["todo_id"])
        assert read["review"]["status"] == "open"
        assert read["todo"]["task_class"] == "user_action" and not read["todo"].get("blocks_agent")
        assert read["generation_id"] == first["generation_id"]
    assert report_review_inventory(**review_goal) == before
    completed = update_goal_todo(registry_path=review_goal["registry_path"], runtime_root_arg=str(review_goal["runtime_root"]),
        goal_id="research", agent_id="researcher", todo_id=first["todo_id"], role="user", status="done", reason="Owner accepted this edition")
    assert completed["ok"], completed
    replay = request_report_review(**review_goal, agent_id="researcher", bundle=bundle, execute=True)
    assert replay["todo_id"] == first["todo_id"]
    assert read_report_review(**review_goal, todo_id=first["todo_id"])["review"]["status"] == "done"
    next_edition = request_report_review(**review_goal, agent_id="researcher", bundle=review_bundle("修订后的研究材料"), execute=True)
    peer = request_report_review(**review_goal, agent_id="peer", bundle=bundle, execute=True)
    assert len({first["todo_id"], next_edition["todo_id"], peer["todo_id"]}) == 3
    assert read_report_review(**review_goal, todo_id=next_edition["todo_id"])["review"]["status"] == "open"


def test_public_cli_can_prepare_then_retry_one_review_request(review_goal, capsys):
    from loopx.entrypoint import main
    path = review_goal["runtime_root"].parent / "generation-bundle.json"
    path.write_text(json.dumps(review_bundle()))
    args = ["--format", "json", "--registry", str(review_goal["registry_path"]),
        "--runtime-root", str(review_goal["runtime_root"]), "periodic-report", "request-review",
        "--goal-id", "research", "--agent-id", "researcher", "--bundle-json", str(path)]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    assert report_review_inventory(**review_goal) == []
    ids = []
    for _ in range(2):
        assert main([*args, "--execute"]) == 0
        ids.append(json.loads(capsys.readouterr().out)["todo_id"])
    assert ids[0] == ids[1] and len(report_review_inventory(**review_goal)) == 1


def test_queued_edition_survives_renderer_upgrade_without_reopening(review_goal, monkeypatch):
    from loopx.presentation.renderers import periodic_report_html
    old, revised = review_bundle(), review_bundle("A new edition")
    queued = request_report_review(**review_goal, agent_id="researcher", bundle=old, execute=True)
    update_goal_todo(registry_path=review_goal["registry_path"], runtime_root_arg=str(review_goal["runtime_root"]),
        goal_id="research", agent_id="researcher", todo_id=queued["todo_id"], status="done", reason="Owner handled the original")
    def changed_renderer(_):
        raise ValueError("Synthetic later renderer cannot reproduce old formatting")
    monkeypatch.setattr(periodic_report_html, "render_periodic_report_html", changed_renderer)
    replay = request_report_review(**review_goal, agent_id="researcher", bundle=old, execute=True)
    assert replay["todo_id"] == queued["todo_id"]
    assert read_report_review(**review_goal, todo_id=queued["todo_id"])["review"]["status"] == "done"
    for bundle, agent in [(revised, "researcher"), (old, "peer")]:
        with pytest.raises(ValueError, match="later renderer"):
            request_report_review(**review_goal, agent_id=agent, bundle=bundle, execute=True)
    assert len(report_review_inventory(**review_goal)) == 1


def test_arbitrary_html_and_corrupt_or_foreign_material_fail_without_accepting(review_goal):
    bundle = review_bundle()
    forged = deepcopy(bundle)
    html = next(a for a in forged["artifacts"] if a["renderer_id"] == "html_artifact_v0")
    html["content"] = '<script>fetch("https://example.org/leak")</script>'
    # Rehashing arbitrary HTML still cannot impersonate the escaped renderer.
    import hashlib
    html["content_digest"] = "sha256:" + hashlib.sha256(html["content"].encode()).hexdigest()
    with pytest.raises(ValueError):
        request_report_review(**review_goal, agent_id="researcher", bundle=forged, execute=True)
    assert report_review_inventory(**review_goal) == []
    first = request_report_review(**review_goal, agent_id="researcher", bundle=bundle, execute=True)
    path = review_goal["runtime_root"] / "goals/research/periodic_reports/review-bundles" / (first["bundle_sha256"] + ".json")
    path.write_text("{}")
    with pytest.raises((KeyError, ValueError)):
        read_report_review(**review_goal, todo_id=first["todo_id"])
    assert report_review_inventory(**review_goal)[0]["status"] == "open"
    with pytest.raises((KeyError, ValueError, RuntimeError)):
        read_report_review(**{**review_goal, "goal_id": "foreign"}, todo_id=first["todo_id"])


def test_foreign_feedback_session_cannot_be_bound_to_report(review_goal):
    from loopx.chat_store import ChatSessionStore
    foreign = ChatSessionStore(review_goal["runtime_root"]).create_session(goal_id="research", agent_id="codex",
        managed_agent_id="peer", managed_workspace_path=str(review_goal["runtime_root"].parent),
        channel_id="agent.peer", adapter_kind="codex", upstream_thread_id="synthetic-peer")
    with pytest.raises(ValueError, match="original Goal and Agent"):
        request_report_review(**review_goal, agent_id="researcher", bundle=review_bundle(), session_id=foreign["session_id"], execute=True)
    assert report_review_inventory(**review_goal) == []


def test_original_chat_store_can_be_separate_from_goal_authority(review_goal):
    from loopx.chat_store import ChatSessionStore
    store = ChatSessionStore(review_goal["runtime_root"].parent / "separate-history")
    session = store.create_session(goal_id="research", goal_instance_id="review-instance", agent_id="codex",
        managed_agent_id="researcher", managed_workspace_path=str(review_goal["runtime_root"].parent),
        channel_id="agent.researcher", adapter_kind="codex_app_server", upstream_thread_id="synthetic-original")
    with pytest.raises(ValueError):
        request_report_review(**review_goal, agent_id="researcher", bundle=review_bundle(), session_id=session["session_id"])
    assert report_review_inventory(**review_goal) == []
    queued = request_report_review(**review_goal, agent_id="researcher", bundle=review_bundle(),
        session_id=session["session_id"], session_store=store, execute=True)
    assert read_report_review(**review_goal, todo_id=queued["todo_id"])["source_session_id"] == session["session_id"]


def test_real_http_material_reader_is_same_origin_and_keeps_canonical_state(review_goal, monkeypatch):
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    first = request_report_review(**review_goal, agent_id="researcher", bundle=review_bundle(), execute=True)
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path = review_goal["registry_path"]
    server.runtime_root_override = str(review_goal["runtime_root"])
    server.selected_goal_id, server.scan_roots, server.limit = None, [], 10
    server.verbose = False
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    url = f"http://127.0.0.1:{server.server_port}/api/chat/report-reviews"
    try:
        with urlopen(url + "?goal_id=research") as response:
            page = json.load(response)
        assert page["total"] == 1 and page["items"][0]["todo_id"] == first["todo_id"]
        with urlopen(url + f'/{first["todo_id"]}?goal_id=research') as response:
            document = json.load(response)
        assert document["review"]["status"] == "open" and len(document["artifacts"]) == 2
        # Home consumes the compact project-asset projection, not the rich row.
        from loopx.status import collect_status
        status = collect_status(registry_path=review_goal["registry_path"], runtime_root_override=str(review_goal["runtime_root"]),
            goal_id="research", limit=10)
        goal = next(row for row in status["attention_queue"]["items"] if row["goal_id"] == "research")
        summary = next(row for row in goal["project_asset"]["user_todos"]["items"] if row["todo_id"] == first["todo_id"])
        assert summary["capability_binding_ref"] == document["todo"]["capability_binding_ref"]
        assert "resolved_path" not in json.dumps(document)
        with pytest.raises(HTTPError) as denied:
            urlopen(Request(url + f'/{first["todo_id"]}?goal_id=research', headers={"Origin": "https://unrelated.example"}))
        assert denied.value.code == 403
        from loopx.control_plane.coordination import local_authority
        def unavailable(**_):
            raise RuntimeError("Synthetic authority unavailable")
        with monkeypatch.context() as failure:
            failure.setattr(local_authority, "read_canonical_todos_if_promoted", unavailable)
            with pytest.raises(HTTPError) as stopped:
                urlopen(url + "?goal_id=research")
            assert stopped.value.code == 503
        assert report_review_inventory(**review_goal)[0]["status"] == "open"
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
