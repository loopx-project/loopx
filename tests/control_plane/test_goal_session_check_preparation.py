"""Original managed Chat preparation over real File/SQLite acceptance owners.

The native model transport is synthetic. Preparation pins preexisting sources
and persists a proposal without configuring acceptance or running a checker.
"""

from copy import deepcopy
from contextlib import contextmanager, nullcontext
import hashlib
import json
import queue
import sys
from types import SimpleNamespace

import pytest

from loopx.chat.coordination import prepare_turn_context, check_preparation_tool
from loopx.chat_agent import CodexChatAgentSession
from loopx.chat_runtime import ChatRuntimeController, CodexAppServerAdapter
from loopx.chat_store import ChatSessionStore
from loopx.chat_action_store import ChatActionStore
from loopx.control_plane.collaboration import conversation_scope
from loopx.control_plane.goals.acceptance import refine_goal_outcome
from test_goal_acceptance_cli import acceptance_goal as acceptance_goal


@pytest.fixture
def original_check(acceptance_goal, monkeypatch):
    project, _, cli, _ = acceptance_goal
    root, registry = project.parent / "runtime", project.parent / "registry.json"
    refined = refine_goal_outcome(
        registry_path=registry,
        runtime_root=str(root),
        goal_id="goal-acceptance",
        agent_id="agent-a",
        operation_id="original-owner-outcome",
        expected_provider_revision=cli("inspect")[1]["provider_revision"],
        execute=True,
        refinement={
            "outcome_id": "goal-creation",
            "owner_agent_id": "agent-a",
            "objective": "Produce a usable export",
            "todo_ids": ["todo_export"],
            "requirements": [
                {
                    "id": "completion",
                    "description": "The exported artifact has the expected content",
                }
            ],
            "source": {
                "ref": "goal-create:fixture:completion",
                "summary": "Owner-approved completion criteria",
            },
        },
    )
    assert refined["status"] == "applied", refined
    checker = project / "check.py"
    checker.write_text(
        "from pathlib import Path\nassert Path('artifact.txt').read_text() == 'accepted'\n"
    )
    store = ChatSessionStore(root)
    runtime = ChatRuntimeController(
        store=store, codex_bin="unused", registry_path=registry
    )
    session = store.create_session(
        goal_id="goal-acceptance",
        managed_agent_id="agent-a",
        managed_workspace_path=str(project.resolve()),
        channel_id="agent.agent-a",
        agent_id="codex",
        executor_endpoint_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="original-thread",
        upstream_mode="chat",
    )
    turn, _ = store.create_turn(
        session["session_id"],
        client_turn_id="prepare-original-check",
        message="Prepare the independent delivery check for review.",
    )
    store.update_turn(session["session_id"], turn["turn_id"], status="running")
    store.update_session(session["session_id"], active_turn_id=turn["turn_id"])
    native = CodexChatAgentSession(
        process=SimpleNamespace(poll=lambda: None),
        messages=queue.Queue(),
        thread_id="original-thread",
        work_dir=project,
    )
    monkeypatch.setattr(native, "close", lambda: None)
    adapter = CodexAppServerAdapter(native)
    runtime.adapters[session["session_id"]] = adapter
    session = store.load_session(session["session_id"])
    prepared_adapter, context = prepare_turn_context(
        runtime,
        adapter,
        session,
        turn["turn_id"],
        lambda *_: None,
        scope=conversation_scope(session, origin="web"),
    )
    assert prepared_adapter is adapter and context["scope"] == "owner_goal"
    document = {
        "outcome_id": "goal-creation",
        "criteria": [
            {
                "id": "completion",
                "validation_argv": ["python3", "check.py"],
                "validation_timeout_seconds": 5,
                "oracle": {
                    "kind": "owner_pinned_oracle",
                    "source_id": "export-checker",
                    "input_refs": [],
                    "output_refs": ["artifact.txt"],
                },
            }
        ],
    }

    def call(**updates):
        return native.read_tool_handler(
            "loopx_collaboration",
            {
                "action": "prepare_check",
                "operation_id": "original-check",
                "document": document,
                **updates,
            },
        )

    try:
        yield SimpleNamespace(
            project=project,
            checker=checker,
            root=root,
            registry=registry,
            store=store,
            runtime=runtime,
            session=session,
            turn=turn,
            native=native,
            adapter=adapter,
            document=document,
            call=call,
            cli=cli,
        )
    finally:
        runtime.close()


def test_original_read_only_turn_prepares_one_host_pinned_proposal(original_check):
    fixture = original_check
    before = fixture.cli("inspect")[1]
    session_before = fixture.store.load_session(fixture.session["session_id"])
    source_before = fixture.checker.read_bytes()
    result = fixture.call()
    assert result["ok"] and result["prepared"] and result["executed"] is False, (
        result.get("next_action", result)
    )
    proposal = result["proposal"]
    parameters = proposal["normalized_parameters"]
    assert parameters["goal_id"] == fixture.session["goal_id"]
    assert parameters["prepared_by"] == "agent-a"
    criterion = parameters["document"]["criteria"][0]
    assert criterion["validation_argv"] == ["python3", "check.py"]
    assert criterion["validation_files"] == [
        {"path": "check.py", "sha256": hashlib.sha256(source_before).hexdigest()}
    ]
    assert criterion["oracle"]["output_refs"] == ["artifact.txt"]
    assert not (fixture.project / "artifact.txt").exists(), (
        "Preparation must allow unfinished delivery"
    )
    assert fixture.call()["proposal"] == proposal
    assert fixture.cli("inspect")[1] == before
    assert fixture.store.load_session(fixture.session["session_id"]) == session_before
    assert fixture.checker.read_bytes() == source_before
    assert len(ChatActionStore(fixture.root / "chat/actions").list()) == 1


@pytest.mark.parametrize(
    "updates",
    [
        {"goal_id": "another-goal"},
        {"agent_id": "other-agent"},
        {"prepared_by": "other-agent"},
        {"workspace": "another-workspace"},
        {"runtime_root": "another-runtime"},
        {"source_id": "remote"},
        {"action": "start"},
        {"action": "outcomes"},
        {"operation_id": ""},
    ],
)
def test_caller_cannot_replace_host_identity_or_action(original_check, updates):
    assert original_check.call(**updates)["error"] == "collaboration_request_rejected"
    assert ChatActionStore(original_check.root / "chat/actions").list() == []


@pytest.mark.parametrize(
    "change",
    [
        "agent",
        "workspace",
        "thread",
        "channel",
        "closed",
        "other-turn",
        "terminal",
        "external",
        "native",
        "legacy",
        "review",
        "lifetime",
        "registration",
        "registry-workspace",
        "goal-stopped",
    ],
)
def test_preparation_rechecks_original_session_turn_and_registration(
    original_check, change
):
    fixture = original_check
    sid, tid = fixture.session["session_id"], fixture.turn["turn_id"]
    fields = {
        "agent": {"managed_agent_id": "other-agent"},
        "workspace": {"managed_workspace_path": "other"},
        "thread": {"upstream_thread_id": "replacement"},
        "channel": {"channel_id": "goal.other"},
        "closed": {"status": "closed"},
        "other-turn": {"active_turn_id": "another-turn"},
        "legacy": {"loopx_mode": {"enabled": True}},
    }
    if change in fields:
        if change in {"agent", "workspace", "channel"}:
            # These fields are immutable through the public API. Corrupt only
            # this disposable input to qualify defensive source readback.
            from loopx.chat_store import _atomic_write_json

            path = fixture.store._session_path(sid)
            _atomic_write_json(
                path, {**fixture.store.load_session(sid), **fields[change]}
            )
        else:
            fixture.store.update_session(sid, **fields[change])
    elif change in {"terminal", "external", "native"}:
        if change == "external":
            from loopx.chat_store import _atomic_write_json

            _atomic_write_json(
                fixture.store._turn_path(sid, tid),
                {**fixture.store.load_turn(sid, tid), "origin": "lark"},
            )
        else:
            fixture.store.update_turn(
                sid,
                tid,
                **(
                    {"status": "completed"}
                    if change == "terminal"
                    else {"loopx_execution": True}
                ),
            )
    elif change == "review":
        fixture.native.read_only_review = True
    else:
        registry = json.loads(fixture.registry.read_text())
        goal = registry["goals"][0]
        if change == "lifetime":
            goal["goal_instance_id"] = "another-lifetime"
        elif change == "registration":
            goal["coordination"]["registered_agents"] = []
        elif change == "registry-workspace":
            goal["repo"] = str(fixture.project.parent)
        else:
            goal["status"] = "paused"
        fixture.registry.write_text(json.dumps(registry))
    result = fixture.call()
    assert result["error"] == "collaboration_request_rejected", result
    assert ChatActionStore(fixture.root / "chat/actions").list() == []


@pytest.mark.parametrize(
    "change",
    [
        "claimed-pin",
        "inline-python",
        "python-options",
        "extra-argv",
        "other-interpreter",
        "absolute",
        "parent",
        "symlink",
        "symlink-parent",
        "missing",
        "directory",
        "source-output",
        "missing-outputs",
        "output-symlink",
    ],
)
def test_preparation_rejects_unusable_or_unpinned_checker_sources(
    original_check, change
):
    fixture = original_check
    document = deepcopy(fixture.document)
    row = document["criteria"][0]
    if change == "claimed-pin":
        row["validation_files"] = [{"path": "check.py", "sha256": "0" * 64}]
    elif change == "inline-python":
        row["validation_argv"] = ["python3", "-c", "pass"]
    elif change == "python-options":
        row["validation_argv"] = ["python3", "-I", "check.py"]
    elif change == "extra-argv":
        row["validation_argv"].append("--ignore-failure")
    elif change == "other-interpreter":
        row["validation_argv"] = ["arbitrary-command", "check.py"]
    elif change == "absolute":
        row["validation_argv"][1] = str(fixture.checker)
    elif change == "parent":
        row["validation_argv"][1] = "../project/check.py"
    elif change in {"symlink", "missing", "directory"}:
        fixture.checker.unlink()
        if change == "symlink":
            fixture.checker.symlink_to(fixture.project.parent / "acceptance.json")
        elif change == "directory":
            fixture.checker.mkdir()
    elif change == "symlink-parent":
        (fixture.project / "linked").symlink_to(
            fixture.project, target_is_directory=True
        )
        row["validation_argv"][1] = "linked/check.py"
    elif change == "source-output":
        row["oracle"]["output_refs"] = ["check.py"]
    elif change == "missing-outputs":
        row["oracle"]["output_refs"] = []
    else:
        (fixture.project / "artifact.txt").symlink_to(fixture.checker)
    result = fixture.call(document=document)
    assert result["error"] == "collaboration_request_rejected", result
    assert ChatActionStore(fixture.root / "chat/actions").list() == []


def test_scope_change_during_source_read_is_refused_before_proposal(
    original_check, monkeypatch
):
    import loopx.chat.coordination as coordination

    observe = coordination._workspace_file

    def replace_scope(*args, **kwargs):
        result = observe(*args, **kwargs)
        original_check.store.update_session(
            original_check.session["session_id"], active_turn_id="later-turn"
        )
        return result

    monkeypatch.setattr(coordination, "_workspace_file", replace_scope)
    assert original_check.call()["error"] == "collaboration_request_rejected"
    assert ChatActionStore(original_check.root / "chat/actions").list() == []


def test_changed_source_retry_preserves_saved_intent_and_original_pin(original_check):
    fixture = original_check
    original = fixture.call()["proposal"]
    saved_pin = original["normalized_parameters"]["document"]["criteria"][0][
        "validation_files"
    ]
    source = fixture.checker.read_bytes()
    fixture.checker.write_bytes(source + b"# A different source version\n")
    refused = fixture.call()
    assert refused["error"] == "collaboration_request_rejected"
    proposals = ChatActionStore(fixture.root / "chat/actions").list()
    assert proposals == [original]
    assert (
        proposals[0]["normalized_parameters"]["document"]["criteria"][0][
            "validation_files"
        ]
        == saved_pin
    )
    fixture.checker.write_bytes(source)
    assert fixture.call()["proposal"] == original


@pytest.mark.parametrize("interpreter", [None, "/usr/bin/another-python"])
def test_unavailable_host_interpreter_refuses_preparation(
    original_check, monkeypatch, interpreter
):
    monkeypatch.setattr(
        "loopx.chat.coordination.shutil.which", lambda _name: interpreter
    )
    result = original_check.call()
    assert result["error"] == "collaboration_request_rejected"
    assert "host interpreter" in result["next_action"]
    assert ChatActionStore(original_check.root / "chat/actions").list() == []


@pytest.mark.parametrize("direct", [False, True])
def test_source_changed_during_read_is_refused_before_proposal(
    original_check, monkeypatch, direct
):
    import loopx.chat.coordination as coordination

    observe = coordination._workspace_file
    document = deepcopy(original_check.document)
    selected_source = original_check.checker
    if direct:
        selected_source = original_check.project / "checks/verify"
        selected_source.parent.mkdir()
        selected_source.write_bytes(original_check.checker.read_bytes())
        selected_source.chmod(0o755)
        document["criteria"][0]["validation_argv"] = ["checks/verify"]
    changed = False

    def change_after_read(*args, **kwargs):
        nonlocal changed
        result = observe(*args, **kwargs)
        if kwargs.get("required") and not changed:
            changed = True
            selected_source.write_text("raise SystemExit(0)\n")
        return result

    monkeypatch.setattr(coordination, "_workspace_file", change_after_read)
    result = original_check.call(document=document)
    assert result["error"] == "collaboration_request_rejected"
    assert "source changed during preparation" in result["next_action"]
    assert ChatActionStore(original_check.root / "chat/actions").list() == []


def test_first_of_two_checker_sources_is_reobserved_before_proposal(
    original_check, monkeypatch
):
    import loopx.chat.coordination as coordination

    fixture = original_check
    refine_goal_outcome(
        registry_path=fixture.registry,
        runtime_root=str(fixture.root),
        goal_id=fixture.session["goal_id"],
        agent_id="agent-a",
        operation_id="additional-owner-requirement",
        expected_provider_revision=fixture.cli("inspect")[1]["provider_revision"],
        execute=True,
        refinement={
            "outcome_id": "goal-creation",
            "requirements": [
                {
                    "id": "additional",
                    "description": "The additional independent check succeeds",
                }
            ],
            "source": {
                "ref": "message:additional-owner-direction",
                "summary": "The owner added another requirement",
            },
        },
    )
    (fixture.project / "additional.py").write_text(
        "from pathlib import Path\nassert Path('artifact.txt').read_text() == 'accepted'\n"
    )
    document = deepcopy(fixture.document)
    additional = deepcopy(document["criteria"][0])
    additional.update(id="additional", validation_argv=["python3", "additional.py"])
    additional["oracle"]["source_id"] = "additional-checker"
    document["criteria"].append(additional)
    observe = coordination._workspace_file

    def replace_first_after_second(workspace, ref, **kwargs):
        digest = observe(workspace, ref, **kwargs)
        if ref == "additional.py" and kwargs["required"]:
            fixture.checker.write_text("raise SystemExit(0)\n")
        return digest

    monkeypatch.setattr(coordination, "_workspace_file", replace_first_after_second)
    result = fixture.call(document=document)
    assert result["error"] == "collaboration_request_rejected"
    assert "source changed during preparation" in result["next_action"]
    assert ChatActionStore(fixture.root / "chat/actions").list() == []


def test_unavailable_safe_file_observation_is_explicitly_refused(
    original_check, monkeypatch
):
    monkeypatch.setattr("loopx.chat.coordination.os.supports_dir_fd", set())
    result = original_check.call()
    assert result["error"] == "collaboration_request_rejected"
    assert "cannot safely inspect workspace checker sources" in result["next_action"]
    assert ChatActionStore(original_check.root / "chat/actions").list() == []


def test_direct_existing_checker_and_matching_claimed_pin_are_supported(original_check):
    fixture = original_check
    checks = fixture.project / "checks"
    checks.mkdir()
    executable = checks / "verify"
    executable.write_text(
        "#!/usr/bin/env python3\nfrom pathlib import Path\nassert Path('artifact.txt').read_text() == 'accepted'\n"
    )
    executable.chmod(0o755)
    document = deepcopy(fixture.document)
    row = document["criteria"][0]
    row["validation_argv"] = ["checks/verify"]
    row["validation_files"] = [
        {
            "path": "checks/verify",
            "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        }
    ]
    result = fixture.call(document=document)
    assert result["prepared"] and result["executed"] is False, result
    assert result["proposal"]["normalized_parameters"]["document"] == document


@pytest.mark.parametrize("token", ["python", sys.executable])
def test_supported_python_aliases_resolve_to_public_safe_host_token(
    original_check, token
):
    document = deepcopy(original_check.document)
    document["criteria"][0]["validation_argv"][0] = token
    result = original_check.call(document=document)
    assert result["prepared"] and result["executed"] is False, result
    row = result["proposal"]["normalized_parameters"]["document"]["criteria"][0]
    assert row["validation_argv"] == ["python3", "check.py"]


def test_outer_dispatcher_preserves_read_owner_and_rejects_actor_override_in_document(
    original_check,
):
    fixture = original_check
    handler = fixture.native.read_tool_handler
    arguments = {"view": "goals", "source_id": "remote"}
    assert handler("loopx_context_read", arguments) == handler.read(
        "loopx_context_read", arguments
    )
    document = {**fixture.document, "goal_id": "another-goal"}
    assert fixture.call(document=document)["error"] == "collaboration_request_rejected"
    assert ChatActionStore(fixture.root / "chat/actions").list() == []


@pytest.mark.parametrize(
    "managed,resume,legacy,execution,review,manager,expected",
    [
        (
            True,
            None,
            False,
            False,
            False,
            False,
            ["loopx_context_read", "loopx_collaboration"],
        ),
        (False, None, False, False, False, False, ["loopx_context_read"]),
        (True, "existing-thread", False, False, False, False, ["loopx_context_read"]),
        (
            False,
            None,
            True,
            False,
            False,
            False,
            ["loopx_context_read", "loopx_collaboration"],
        ),
        (True, None, False, True, False, False, []),
        (True, None, False, False, True, False, []),
        (True, None, False, False, False, True, ["loopx_manager_read"]),
    ],
)
def test_prepare_projection_is_only_for_new_default_managed_threads(
    tmp_path, monkeypatch, managed, resume, legacy, execution, review, manager, expected
):
    from loopx.capabilities.native_chat.codex_adapter import start_runtime_adapter

    captured = []
    monkeypatch.setattr(
        CodexAppServerAdapter, "start", lambda **kwargs: captured.append(kwargs)
    )
    controller = SimpleNamespace(
        codex_bin="unused",
        coordination_runtime_root=tmp_path,
        codex_home=None,
        startup_timeout_sec=2,
        idle_timeout_sec=2,
        hard_timeout_sec=2,
    )
    start_runtime_adapter(
        controller,
        work_dir=tmp_path,
        goal_id="loopx-manager" if manager else "goal-fixture",
        native_context={"objective": "Observe", "runtime_profile": "restricted"},
        manager_profile=None,
        model_config={},
        history=None,
        resume_thread_id=resume,
        execution_mode=execution,
        project_context=None,
        project_coordination=not manager,
        loopx_tools=legacy,
        check_preparation=managed,
        codex_home_override=None,
        read_only_review=review,
        metadata_review_context=None,
        answer_review_context=None,
    )
    tools = captured[0]["dynamic_tools"] or []
    assert [tool["name"] for tool in tools] == expected
    if (
        managed
        and not resume
        and not legacy
        and not execution
        and not review
        and not manager
    ):
        assert tools[-1]["inputSchema"]["properties"]["action"]["enum"] == [
            "prepare_check"
        ]
        assert set(tools[-1]["inputSchema"]["properties"]) == {
            "action",
            "operation_id",
            "document",
        }
    if legacy:
        assert "start" in tools[-1]["inputSchema"]["properties"]["action"]["enum"]


def test_prepare_projection_does_not_mutate_existing_collaboration_tool():
    from loopx.chat_loopx_mode import TOOL

    before = deepcopy(TOOL)
    check_preparation_tool()
    assert TOOL == before


def _capture_native_dispatch(fixture, monkeypatch):
    """Run the real send/request event loop with a synthetic guarded wire."""
    captured = []

    def wire(payload, *, write_guard=None, deadline=None):
        with write_guard() if write_guard is not None else nullcontext():
            captured.append(deepcopy(payload))
            fixture.native.messages.put(
                {"id": payload["id"], "result": {"turn": {"id": "transport-turn"}}}
            )
            fixture.native.messages.put(
                {"method": "item/agentMessage/delta", "params": {"delta": "Ready."}}
            )
            fixture.native.messages.put(
                {
                    "method": "turn/completed",
                    "params": {"turn": {"status": "completed"}},
                }
            )

    monkeypatch.setattr(fixture.native, "_write", wire)
    return captured


def _native_task_policy(fixture):
    return {
        "type": "workspaceWrite",
        "writableRoots": [str(fixture.project.resolve())],
        "networkAccess": False,
        "excludeTmpdirEnvVar": True,
        "excludeSlashTmp": True,
    }


@pytest.mark.parametrize("writable", [False, True])
def test_fenced_native_task_dispatch_then_ordinary_read_reset_preserves_session_defaults(
    original_check, monkeypatch, writable
):
    fixture = original_check
    captured = _capture_native_dispatch(fixture, monkeypatch)
    guarded = []

    @contextmanager
    def admitted_host_write():
        # This synthetic guard checks original transport identity only. The
        # actual Core Task/lease/authority path is qualified by the HTTP suite.
        fixture.native.read_tool_handler._current()
        guarded.append("entered")
        yield

    fixture.native._launch_guard = admitted_host_write
    before = (fixture.native.execution_mode, fixture.native.sandbox)
    policy = (
        _native_task_policy(fixture)
        if writable
        else {"type": "readOnly", "networkAccess": False}
    )
    fixture.adapter.start_native_task_turn(
        "Perform the confirmed Task.", lambda *_: None, policy
    )
    native = captured[-1]["params"]
    assert native["sandboxPolicy"] == policy
    assert "permissions" not in native
    assert (
        "You are the execution agent for a confirmed LoopX Task"
        in native["input"][0]["text"]
    )
    assert "Do not edit files, mutate LoopX state" not in native["input"][0]["text"]
    assert guarded == ["entered"]
    fixture.adapter.start_turn("Prepare the check for review.", lambda *_: None)
    ordinary = captured[-1]["params"]
    assert ordinary["sandboxPolicy"] == {"type": "readOnly", "networkAccess": False}
    assert "Do not edit files, mutate LoopX state" in ordinary["input"][0]["text"]
    fixture.adapter.start_turn_with_attachments(
        "Read the original attachment.", lambda *_: None, []
    )
    assert captured[-1]["params"]["sandboxPolicy"] == {
        "type": "readOnly",
        "networkAccess": False,
    }
    assert (fixture.native.execution_mode, fixture.native.sandbox) == before
    fixture.native.send("Read from the original direct source.")
    assert captured[-1]["params"]["sandboxPolicy"] == {
        "type": "readOnly",
        "networkAccess": False,
    }
    assert all(frame["params"]["threadId"] == "original-thread" for frame in captured)
    assert fixture.native._launch_guard is admitted_host_write


@pytest.mark.parametrize(
    "invalid", ["no-guard", "wrong-workspace", "network", "named-profile", "review"]
)
def test_native_task_transport_refuses_unbound_policy_or_missing_host_fence(
    original_check, monkeypatch, invalid
):
    fixture = original_check
    captured = _capture_native_dispatch(fixture, monkeypatch)
    policy = _native_task_policy(fixture)

    @contextmanager
    def admitted_host_write():
        yield

    fixture.native._launch_guard = admitted_host_write
    if invalid == "no-guard":
        fixture.native._launch_guard = None
    elif invalid == "wrong-workspace":
        policy["writableRoots"] = [str(fixture.project.parent.resolve())]
    elif invalid == "network":
        policy["networkAccess"] = True
    elif invalid == "named-profile":
        fixture.native.permissions_profile = "existing-owner-profile"
    else:
        fixture.native.read_only_review = True
    with pytest.raises(ValueError, match="current host launch guard"):
        fixture.adapter.start_native_task_turn(
            "Perform the Task.", lambda *_: None, policy
        )
    assert captured == []
    assert (
        fixture.native.execution_mode is False and fixture.native.sandbox == "read-only"
    )
    assert fixture.native._native_task_policy_used is False


def test_revoked_host_launch_guard_refuses_native_frame_and_ordinary_turn_cannot_widen_policy(
    original_check, monkeypatch
):
    fixture = original_check
    captured = _capture_native_dispatch(fixture, monkeypatch)

    @contextmanager
    def revoked_host_write():
        raise ValueError("the original Task authority was revoked")
        yield

    fixture.native._launch_guard = revoked_host_write
    with pytest.raises(ValueError, match="authority was revoked"):
        fixture.adapter.start_native_task_turn(
            "Perform the Task.", lambda *_: None, _native_task_policy(fixture)
        )
    assert captured == []
    with pytest.raises(ValueError, match="ordinary Chat Turn"):
        fixture.native.send(
            "Ordinary request.", sandbox_policy=_native_task_policy(fixture)
        )
    assert captured == []


def test_uncertain_native_publication_retains_ordinary_policy_reset_requirement(
    original_check, monkeypatch
):
    fixture = original_check
    captured = _capture_native_dispatch(fixture, monkeypatch)
    request = fixture.native._request

    @contextmanager
    def admitted_host_write():
        yield

    fixture.native._launch_guard = admitted_host_write

    def uncertain_publication(*args, **kwargs):
        raise OSError("native publication is unconfirmed")

    monkeypatch.setattr(fixture.native, "_request", uncertain_publication)
    with pytest.raises(OSError, match="publication is unconfirmed"):
        fixture.adapter.start_native_task_turn(
            "Perform the Task.", lambda *_: None, _native_task_policy(fixture)
        )
    assert fixture.native._native_task_policy_used is True and captured == []
    monkeypatch.setattr(fixture.native, "_request", request)
    fixture.native.send("Read the original evidence after recovery.")
    assert captured[-1]["params"]["sandboxPolicy"] == {
        "type": "readOnly",
        "networkAccess": False,
    }
    assert captured[-1]["params"]["threadId"] == "original-thread"
    assert (
        fixture.native.execution_mode is False and fixture.native.sandbox == "read-only"
    )


@pytest.mark.parametrize("cold", [False, True], ids=["new", "cold-resume"])
def test_verified_managed_session_construction_binds_direct_read_default_without_native_history(
    original_check, monkeypatch, cold
):
    from loopx.chat.coordination import PROJECT_CONTEXT_VERSION

    fixture = original_check
    starts = []

    def start(**kwargs):
        starts.append(kwargs)
        native = CodexChatAgentSession(
            process=SimpleNamespace(poll=lambda: None),
            messages=queue.Queue(),
            thread_id=kwargs.get("resume_thread_id") or "new-managed-thread",
            work_dir=kwargs["work_dir"],
        )
        monkeypatch.setattr(native, "close", lambda: None)
        return CodexAppServerAdapter(native)

    monkeypatch.setattr(CodexAppServerAdapter, "start", start)
    monkeypatch.setattr(
        fixture.runtime,
        "capabilities",
        lambda: [
            {"agent_id": "codex", "available": True, "adapter_kind": "codex_app_server"}
        ],
    )
    if cold:
        fixture.store.update_turn(
            fixture.session["session_id"], fixture.turn["turn_id"], status="completed"
        )
        fixture.store.update_session(
            fixture.session["session_id"],
            active_turn_id=None,
            coordination_context_version=PROJECT_CONTEXT_VERSION,
        )
        fixture.runtime.adapters.pop(fixture.session["session_id"])
        session = fixture.store.load_session(fixture.session["session_id"])
        adapter = fixture.runtime._ensure_adapter(
            session, work_dir=fixture.project, objective="Produce a usable export"
        )
        assert starts[-1]["resume_thread_id"] == "original-thread"
        assert adapter.upstream_thread_id == "original-thread"
        assert [tool["name"] for tool in starts[-1]["dynamic_tools"]] == [
            "loopx_context_read"
        ]
    else:
        session, reused = fixture.runtime.open_session(
            goal_id=fixture.session["goal_id"],
            agent_id="codex",
            managed_agent_id="agent-a",
            work_dir=fixture.project,
            objective="Produce a usable export",
            mode="new",
        )
        assert not reused
        adapter = fixture.runtime.adapters[session["session_id"]]
    assert adapter.session._managed_goal_read_default is True
    assert adapter.session._native_task_policy_used is False
    wire = _capture_native_dispatch(
        SimpleNamespace(native=adapter.session), monkeypatch
    )
    adapter.session.send("Read the original source.")
    assert wire[-1]["params"]["sandboxPolicy"] == {
        "type": "readOnly",
        "networkAccess": False,
    }
    assert wire[-1]["params"]["threadId"] == adapter.upstream_thread_id
    assert "managed_read_default" not in fixture.store.load_session(
        session["session_id"]
    )
    assert "_managed_goal_read_default" not in fixture.store.load_session(
        session["session_id"]
    )
    assert (
        adapter.session.execution_mode is False
        and adapter.session.sandbox == "read-only"
    )


def test_unbound_legacy_session_direct_send_preserves_default_native_payload(
    original_check, monkeypatch
):
    fixture = original_check
    assert fixture.native._managed_goal_read_default is False
    assert fixture.native._native_task_policy_used is False
    wire = _capture_native_dispatch(fixture, monkeypatch)
    fixture.native.send("Read the source in this legacy transport.")
    assert "sandboxPolicy" not in wire[-1]["params"]
    assert (
        fixture.native.execution_mode is False and fixture.native.sandbox == "read-only"
    )
