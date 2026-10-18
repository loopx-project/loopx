"""Local operator input shares an existing private binding, never a provider event.

Real HTTP, Chat storage and File/SQLite authority; only the remote identity and
model subprocess are synthetic. This does not qualify installed UI or live Lark.
"""
import http.client
import json
from pathlib import Path
import runpy
import threading

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture
from loopx.capabilities.native_chat.conversation_bindings import ChatConversationBindings
from loopx.capabilities.native_chat.project_context import ChatProjectContexts
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.chat_store import ChatSessionStore
from loopx.control_plane.collaboration import conversation_scope
from loopx.control_plane.collaboration.source_grant_observation import external_source_policy
from loopx.control_plane.collaboration.source_grant_observation import source_context_target_authority


@pytest.fixture(params=["file", "sqlite"])
def bound_chat(tmp_path, monkeypatch, request):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, canonical, state = promoted_create_fixture(tmp_path, provider=request.param)
    workspace = state.parents[3]
    contexts = ChatProjectContexts([workspace], workspace_grant="workspace_read")
    store = ChatSessionStore(tmp_path / "chat")
    capture, fake = tmp_path / "requests.jsonl", tmp_path / "codex"
    source = runpy.run_path(str(Path(__file__).parents[2] / "examples/loopx-chat-runtime-smoke.py"))["FAKE_CODEX"]
    source = source.replace('    method = request.get("method")',
        f'    with open({str(capture)!r}, "a") as output:\n        output.write(json.dumps(request) + "\\n")\n'
        '    method = request.get("method")')
    # The isolated group host must acknowledge its requested permission profile;
    # keep the production profile check intact before testing HTTP source denial.
    source = source.replace('        result = {"thread": {"id": "durable-thread"}}',
        '        result = {"thread": {"id": "durable-thread"}, "activePermissionProfile": '
        '{"id": request["params"].get("permissions")}, "runtimeWorkspaceRoots": [request["params"]["cwd"]]}')
    fake.write_text(source)
    fake.chmod(0o700)
    runtime = ChatRuntimeController(store=store, codex_bin=str(fake), project_contexts=contexts, registry_path=registry)
    runtime.codex_home = tmp_path / "synthetic-codex-home"
    runtime.codex_home.mkdir()
    observed = {"transport_ref": "private-app", "provider_ref": "c" * 24,
                "operator_ref": "d" * 24, "verified": True}
    bindings = ChatConversationBindings(root=store.root, project_contexts=contexts, observe=lambda _: dict(observed))
    bindings.controller = runtime
    contexts.conversation_bindings = bindings
    runtime.manager_scope_resolver = bindings.steward_scope
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.chat_store, server.runtime_controller = store, runtime
    server.selected_goal_id, server.verbose = None, False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def post(session, body):
        connection = http.client.HTTPConnection(*server.server_address, timeout=15)
        try:
            connection.request("POST", f"/api/chat/sessions/{session['session_id']}/turns",
                               json.dumps(body), headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def open_bound(kind="project"):
        binding = bindings.configure(transport_ref="private-app", project_ref=contexts.available()[0]["project_ref"],
            executor_endpoint_id="codex", context_kind=kind, project_grant="workspace_read")
        session, _ = runtime.open_session(goal_id=None, agent_id="codex", work_dir=workspace, objective="",
            mode="resume_latest", conversation_binding_id=binding["binding_id"],
            source_context={"source_ref": "a" * 24, "sender_ref": observed["operator_ref"], "private_human_message": True})
        return binding, session

    post.server_address = server.server_address
    yield store, runtime, bindings, observed, post, open_bound, capture, canonical
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
    runtime.close()


def _bound_sessions(post, query):
    connection = http.client.HTTPConnection(*post.server_address, timeout=15)
    try:
        connection.request("GET", "/api/chat/sessions?" + query)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


@pytest.mark.parametrize("kind", ["project", "steward"])
def test_http_binding_discovery_selects_original_session_without_starting_work(bound_chat, kind):
    store, runtime, _, _, post, open_bound, capture, _ = bound_chat
    binding, original = open_bound(kind)
    other, _ = runtime.open_session(goal_id=None, agent_id="codex", objective="", mode="new",
        work_dir=Path(runtime.project_contexts.available()[0]["workspace_path"]),
        project_ref=binding["project_ref"])
    before = store.load_session(original["session_id"])
    requests_before = capture.read_bytes()
    status, listed = _bound_sessions(post, "conversation_binding_id=" + binding["binding_id"])
    assert status == 200, listed
    assert listed["conversation_binding_id"] == binding["binding_id"]
    assert [row["session_id"] for row in listed["sessions"]] == [original["session_id"]]
    assert listed["sessions"][0]["goal_id"] == ("loopx-manager" if kind == "steward" else None)
    assert listed["sessions"][0]["session_id"] != other["session_id"]
    assert store.load_session(original["session_id"]) == before
    assert capture.read_bytes() == requests_before
    status, ordinary = _bound_sessions(post, "")
    assert status == 200 and {row["session_id"] for row in ordinary["sessions"]} == {
        original["session_id"], other["session_id"]}
    assert "conversation_binding_id" not in ordinary
    # The discovery result is an existing identity, not a new execution grant.
    selected = listed["sessions"][0]
    status, accepted = post(selected, {"message": "Continue the discovered original conversation.",
                                     "client_turn_id": "discovered"})
    assert status == 202, accepted
    assert runtime.wait_for_turn(session_id=selected["session_id"], turn_id=accepted["turn_id"],
                                 timeout_sec=15)["status"] == "completed"
    assert store.load_session(selected["session_id"])["upstream_thread_id"] == original["upstream_thread_id"]


@pytest.mark.parametrize("query", ["conversation_binding_id=", "conversation_binding_id=x&conversation_binding_id=y",
                                  "conversation_binding_id=x&goal_id=goal-a"])
def test_http_binding_discovery_rejects_ambiguous_selector_without_unfiltered_fallback(bound_chat, query):
    _, _, _, _, post, open_bound, _, _ = bound_chat
    open_bound()
    status, denied = _bound_sessions(post, query)
    assert status == 400 and "sessions" not in denied


@pytest.mark.parametrize("mutation", ["operator_ref", "verified", "workspace", "revoked", "closed"])
def test_http_binding_discovery_rechecks_private_source_and_retains_closed_history(bound_chat, mutation):
    store, _, bindings, observed, post, open_bound, capture, canonical = bound_chat
    binding, session = open_bound()
    if mutation == "operator_ref":
        observed[mutation] = "f" * 24
    elif mutation == "verified":
        observed[mutation] = False
    elif mutation == "workspace":
        Path(session["project_context"]["workspace_path"]).rename(canonical.parent / "removed-workspace")
    elif mutation == "revoked":
        bindings.disconnect(binding["binding_id"], expected_revision=bindings.read()["revision"])
    else:
        store.update_session(session["session_id"], status="closed")
    before = capture.read_bytes()
    status, result = _bound_sessions(post, "conversation_binding_id=" + binding["binding_id"])
    if mutation == "closed":
        assert status == 200 and result["sessions"] == []
        assert store.load_session(session["session_id"])["status"] == "closed"
    else:
        assert status == 409 and "sessions" not in result
    assert capture.read_bytes() == before


def test_http_binding_discovery_does_not_hold_publication_fence_during_provider_io(bound_chat, monkeypatch):
    _, _, bindings, observed, post, open_bound, _, _ = bound_chat
    binding, _ = open_bound()
    verifying, release = threading.Event(), threading.Event()
    result = []

    def observe(_):
        snapshot = dict(observed)
        verifying.set()
        assert release.wait(5)
        return snapshot

    monkeypatch.setattr(bindings, "_observe_owner", observe)
    reader = threading.Thread(target=lambda: result.append(_bound_sessions(post,
        "conversation_binding_id=" + binding["binding_id"])))
    reader.start()
    try:
        assert verifying.wait(5)
        bindings.disconnect(binding["binding_id"], expected_revision=bindings.read()["revision"])
    finally:
        release.set()
        reader.join(timeout=10)
    assert not reader.is_alive() and result[0][0] == 409
    assert "sessions" not in result[0][1]


def test_http_binding_discovery_preserves_duplicate_observations_for_the_typed_owner(bound_chat, monkeypatch):
    store, _, _, _, post, open_bound, _, _ = bound_chat
    binding, _ = open_bound()
    observed = store.list_sessions()
    # Inject an ambiguous storage read, never duplicate/corrupt a live store.
    monkeypatch.setattr(store, "list_sessions", lambda **_: observed + observed)
    status, denied = _bound_sessions(post, "conversation_binding_id=" + binding["binding_id"])
    assert status == 409 and "sessions" not in denied


def test_http_local_owner_continues_private_project_thread_and_queue(bound_chat):
    store, runtime, _, _, post, open_bound, capture, _ = bound_chat
    _, session = open_bound()
    sid, original_thread = session["session_id"], session["upstream_thread_id"]
    message = "Keep this original workspace and full message.\n" + ("detail " * 2500).rstrip()
    status, accepted = post(session, {"message": message, "client_turn_id": "local-first"})
    assert status == 202, accepted
    assert runtime.wait_for_turn(session_id=sid, turn_id=accepted["turn_id"], timeout_sec=15)["status"] == "completed"
    status, replay = post(session, {"message": message, "client_turn_id": "local-first", "delivery": "queue"})
    assert status == 202 and replay["turn_id"] == accepted["turn_id"] and not replay["created"]
    status, next_turn = post(session, {"message": message, "client_turn_id": "local-second"})
    assert status == 202 and next_turn["turn_id"] != accepted["turn_id"]
    assert runtime.wait_for_turn(session_id=sid, turn_id=next_turn["turn_id"], timeout_sec=15)["status"] == "completed"
    turn = store.load_turn(sid, accepted["turn_id"])
    assert turn["origin"] == "web" and turn["message"] == message
    current = store.load_session(sid)
    assert current["goal_id"] is None and current["project_context"] == session["project_context"]
    assert current["upstream_thread_id"] == original_thread
    assert conversation_scope(current, origin="web") == {
        "kind": "project_workspace", "goal_ids": [], "private_conversation": False}
    requests = [json.loads(line) for line in capture.read_text().splitlines()]
    assert sum(row.get("method") == "thread/start" for row in requests) == 1
    assert sum(row.get("method") == "turn/start" for row in requests) == 2


def test_http_observes_bound_source_once_before_the_publication_fence(bound_chat, monkeypatch):
    _, runtime, bindings, observed, post, open_bound, _, _ = bound_chat
    _, session = open_bound()
    calls = []
    monkeypatch.setattr(bindings, "_observe_owner", lambda profile: calls.append(profile) or dict(observed))
    monkeypatch.setattr(runtime, "resume_session_queue", lambda **_: None)
    status, accepted = post(session, {"message": "one source observation", "client_turn_id": "single-observation"})
    assert status == 202, accepted
    assert calls == ["private-app"]


def test_http_local_input_cannot_inherit_a_group_topic(bound_chat):
    store, runtime, bindings, observed, post, _, _, _ = bound_chat
    project = runtime.project_contexts.available()[0]
    group, topic = "1" * 24, "2" * 24
    binding = bindings.configure(transport_ref="private-app", project_ref=project["project_ref"],
        executor_endpoint_id="codex", project_grant="workspace_read", audience="group",
        group_refs=[group], available_group_refs=[group])
    session, _ = runtime.open_session(goal_id=None, agent_id="codex", work_dir=Path(project["workspace_path"]),
        objective="", mode="resume_latest", conversation_binding_id=binding["binding_id"],
        source_context={"source_ref": topic, "sender_ref": observed["operator_ref"],
            "private_human_message": False, "group_ref": group, "topic_ref": topic, "group_human_message": True})
    status, denied = post(session, {"message": "not a verified group event", "client_turn_id": "group-web"})
    assert status == 400 and denied["delivery_state"] == "not_delivered"
    assert store.queued_turns(session["session_id"]) == []
    status, denied = _bound_sessions(post, "conversation_binding_id=" + binding["binding_id"])
    assert status == 409 and "sessions" not in denied


def test_http_bound_steward_keeps_web_provenance_at_coordination_root(bound_chat, monkeypatch):
    store, runtime, _, _, post, open_bound, _, canonical = bound_chat
    _, session = open_bound("steward")
    # Inspect the real canonical queue before dispatch; admission must persist
    # provenance first, without calling a model or creating a parallel inbox.
    monkeypatch.setattr(runtime, "resume_session_queue", lambda **_: None)
    message = "Ask the already registered agent-a about this exact request."
    status, accepted = post(session, {"message": message, "client_turn_id": "local-steward"})
    assert status == 202, accepted
    turn = store.load_turn(session["session_id"], accepted["turn_id"])
    assert turn["origin"] == "web" and turn["status"] == "queued"
    assert runtime.coordination_runtime_root == canonical != store.root.parent
    ingress, policy = external_source_policy(canonical, session, turn)
    assert ingress["source_message"] == message and ingress["origin"] == "web"
    assert ingress["source_id"].startswith("web:") and ingress["session_id"] == session["session_id"]
    assert ingress["client_turn_id"] == turn["client_turn_id"]
    assert policy["sender_ids"] == [session["steward_context"]["operator_ref"]]
    target = {"goal_id": "goal-a", "agent_id": "agent-a"}
    grant = source_context_target_authority(canonical, session, turn, target)
    assert grant["mode"] == "context_only" and grant["targets"] == [target]
    assert "execution_bindings" not in policy
    assert conversation_scope(session, origin="web") == {
        "kind": "external_audience", "goal_ids": [], "private_conversation": False, "bound_steward": True}
    with pytest.raises((OSError, ValueError)):
        external_source_policy(store.root.parent, session, turn)


@pytest.mark.parametrize("mutation", ["operator_ref", "provider_ref", "verified", "workspace", "revoked", "closed", "body_origin"])
def test_local_input_denies_lost_source_and_client_supplied_identity(bound_chat, mutation):
    store, runtime, bindings, observed, post, open_bound, _, canonical = bound_chat
    binding, session = open_bound()
    body = {"message": "must stay unadmitted", "client_turn_id": "denied"}
    if mutation in {"operator_ref", "provider_ref"}:
        observed[mutation] = "f" * 24
    elif mutation == "verified":
        observed[mutation] = False
    elif mutation == "workspace":
        Path(session["project_context"]["workspace_path"]).rename(canonical.parent / "removed-workspace")
    elif mutation == "revoked":
        bindings.disconnect(binding["binding_id"], expected_revision=bindings.read()["revision"])
    elif mutation == "closed":
        store.update_session(session["session_id"], status="closed")
    else:
        body["origin"] = "lark"
    status, denied = post(session, body)
    assert status in {400, 404}, denied
    assert denied["delivery_state"] == "not_delivered"
    assert store.queued_turns(session["session_id"]) == []
    assert store.load_session(session["session_id"])["active_turn_id"] is None


def test_revocation_can_commit_while_provider_verification_waits(bound_chat, monkeypatch):
    store, runtime, bindings, observed, post, open_bound, _, _ = bound_chat
    binding, session = open_bound("steward")
    monkeypatch.setattr(runtime, "resume_session_queue", lambda **_: None)
    verifying, release = threading.Event(), threading.Event()
    result = []

    def observe(_):
        snapshot = dict(observed)
        verifying.set()
        assert release.wait(5)
        return snapshot

    monkeypatch.setattr(bindings, "_observe_owner", observe)
    thread = threading.Thread(target=lambda: result.append(post(session,
        {"message": "source revoked while observation waits", "client_turn_id": "race"})))
    thread.start()
    try:
        assert verifying.wait(5)
        # This would deadlock/time out if admission held the publication lock
        # during provider IO. The stale observation cannot restore the grant.
        bindings.disconnect(binding["binding_id"], expected_revision=bindings.read()["revision"])
    finally:
        release.set()
        thread.join(timeout=10)
    assert not thread.is_alive() and result[0][0] == 400
    assert store.queued_turns(session["session_id"]) == []


def test_binding_publication_fence_covers_canonical_queue_commit(bound_chat, monkeypatch):
    store, runtime, bindings, _, post, open_bound, _, _ = bound_chat
    binding, session = open_bound()
    monkeypatch.setattr(runtime, "resume_session_queue", lambda **_: None)
    committing, release, revoked = threading.Event(), threading.Event(), threading.Event()
    create = store.create_queued_turn
    result, failures = [], []

    def gated_commit(*args, **kwargs):
        committing.set()
        assert release.wait(5)
        return create(*args, **kwargs)

    def revoke():
        try:
            bindings.disconnect(binding["binding_id"], expected_revision=bindings.read()["revision"])
        except Exception as exc:
            failures.append(exc)
        finally:
            revoked.set()

    monkeypatch.setattr(store, "create_queued_turn", gated_commit)
    sender = threading.Thread(target=lambda: result.append(post(session,
        {"message": "one accepted queue entry", "client_turn_id": "fenced"})))
    sender.start()
    revoker = threading.Thread(target=revoke)
    try:
        assert committing.wait(5)
        revoker.start()
        assert not revoked.wait(0.15), "revocation crossed the queue publication fence"
    finally:
        release.set()
        sender.join(timeout=10)
        if revoker.ident is not None:
            revoker.join(timeout=10)
    assert revoked.is_set() and not failures
    assert result[0][0] == 202, result
    turn = store.load_turn(session["session_id"], result[0][1]["turn_id"])
    assert turn["status"] in {"interrupted", "cancelled"}


def test_provider_and_local_followups_keep_busy_fifo_and_exact_replay(bound_chat):
    store, runtime, _, _, post, open_bound, _, _ = bound_chat
    _, session = open_bound()
    sid = session["session_id"]
    active, _ = runtime.enqueue_turn(session_id=sid, client_turn_id="provider-first",
        message="wait for interrupt", work_dir=Path(session["project_context"]["workspace_path"]), objective="", origin="lark")
    import time
    deadline = time.monotonic() + 5
    while not store.load_turn(sid, active["turn_id"]).get("upstream_turn_id") and time.monotonic() < deadline:
        time.sleep(0.01)
    assert store.load_session(sid)["active_turn_id"] == active["turn_id"]
    queued = []
    for client in ("local-next", "local-after"):
        status, accepted = post(session, {"message": "same text, different explicit requests", "client_turn_id": client})
        assert status == 202, accepted
        queued.append(accepted["turn_id"])
    status, replay = post(session, {"message": "same text, different explicit requests", "client_turn_id": "local-next"})
    assert status == 202 and not replay["created"] and replay["turn_id"] == queued[0]
    assert [turn["turn_id"] for turn in store.queued_turns(sid)] == queued
    runtime.interrupt_turn(session_id=sid, turn_id=active["turn_id"])
    for tid in queued:
        assert runtime.wait_for_turn(session_id=sid, turn_id=tid, timeout_sec=15)["status"] == "completed"
    assert store.load_turn(sid, active["turn_id"])["origin"] == "lark"
    assert all(store.load_turn(sid, tid)["origin"] == "web" for tid in queued)
