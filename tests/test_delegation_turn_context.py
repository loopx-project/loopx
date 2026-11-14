"""Real receipt/store journeys: a Turn agenda does not complete or replay work."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import sysconfig

import pytest

from loopx.capabilities.manager_context import acknowledge, deliver, turn_start_hook, _root, _write
from loopx.capabilities.manager_context.roundtrip import report, drain
from loopx.control_plane.capability_hooks import dispatch_turn_start_hooks
from loopx.control_plane.collaboration.peers import request, read_inbox, consume_return
from loopx.control_plane.collaboration.turn_context_observation import read_delegation_turn_context
import test_manager_context_roundtrip as roundtrip_tests


@pytest.fixture
def flow(tmp_path):
    return roundtrip_tests.flow.__wrapped__(tmp_path)


def agenda(root, registry, agent="worker"):
    return read_delegation_turn_context(root, registry, "research", agent)["unfinished_delegations"]


def test_two_turns_show_read_adopted_work_then_queued_return_without_mutating_receipts(flow):
    root, registry, store, create = flow
    session, turn, receipt = create()
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Existing scoped work")
    # Supplying the full inbox does not make the request disappear on the next Turn.
    read_inbox(root, registry, "research", "worker")
    before = {p.relative_to(root): p.read_bytes() for p in _root(root).rglob("*.json")}
    for _ in range(2):
        dispatch = dispatch_turn_start_hooks([turn_start_hook(root, registry, "research", "worker")])
        assert dispatch["failures"] == []
        supplied = dispatch["contexts"][0]["content"]
        assert supplied["read_complete"] is False
        row = supplied["unfinished_delegations"]["items"][0]
        assert row["request_id"] == rid
        assert row["decision"] == "adopt"
        assert row["step"] == "review_request_work"
        assert "Private deliberation" not in json.dumps(supplied)
    assert {p.relative_to(root): p.read_bytes() for p in _root(root).rglob("*.json")} == before
    report(root, "research", "worker", rid, "conclusion", "The artifact passed validation.")
    assert agenda(root, registry)["items"][0]["step"] == "await_return_delivery"
    drain(root, registry, store, lambda *_: pytest.fail("local return does not use an external sender"))
    assert agenda(root, registry)["items"] == []
    assert len([m for m in store.messages(session["session_id"]) if m.get("origin") == "manager_followup"]) == 1


def test_outgoing_request_and_return_require_explicit_consumption(tmp_path):
    root = tmp_path
    registry = root / "registry.json"
    registry.write_text(json.dumps({"goals": [{"id": "research", "repo": str(root),
        "coordination": {"registered_agents": ["worker", "reviewer"]}}]}))
    brief = {"schema_version": "collaboration_brief_v0", "purpose": "Independently inspect the revised artifact",
        "context": "Keep the receiver's execution authority", "constraints": [], "inputs": [],
        "acceptance": ["Return actual findings"], "return_requirement": "Return to the original requester"}
    receipt = request(root, registry, "research", "worker", "reviewer", "independent-review", brief)
    rid = receipt["request_id"]
    assert agenda(root, registry)["items"][0]["step"] == "await_peer_result"
    acknowledge(root, "research", "reviewer", rid, "adopt", "Inspect current source")
    report(root, "research", "reviewer", rid, "conclusion", "One boundary requires correction.")
    assert agenda(root, registry)["items"][0]["step"] == "consume_peer_result"
    read_inbox(root, registry, "research", "worker")
    assert agenda(root, registry)["items"][0]["step"] == "consume_peer_result"
    assert agenda(root, registry, "reviewer")["items"][0]["step"] == "await_peer_consumption"
    consume_return(root, "research", "worker", rid, registry=registry)
    assert agenda(root, registry)["items"] == []
    assert agenda(root, registry, "reviewer")["items"] == []


def test_invalid_receipt_and_invalid_entries_are_visible_as_unavailable(flow):
    root, registry, _store, create = flow
    _session, _turn, receipt = create()
    rid = receipt["request_id"]
    _write(_root(root) / "decisions" / (rid + ".json"), {"wrong": "identity"})
    assert agenda(root, registry)["items"][0]["step"] == "recover_evidence"
    entry = next((_root(root) / "entries").glob("*/*.json"))
    entry.write_text("{broken")
    result = agenda(root, registry)
    assert result["scan_complete"] is False
    assert result["unavailable_count"] == 1
    assert result["items"] == []


def test_separate_agent_has_no_access_to_another_receivers_agenda(flow):
    root, registry, _store, create = flow
    create()
    data = json.loads(registry.read_text())
    data["goals"][0]["coordination"]["registered_agents"].append("unrelated")
    registry.write_text(json.dumps(data))
    assert agenda(root, registry, "unrelated")["items"] == []


@pytest.mark.skipif(os.name != "posix", reason="native context delivery requires POSIX")
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_unbound_cli_first_read_leaves_inbox_rows_to_its_required_read(tmp_path, monkeypatch, provider):
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
    from tests.control_plane.test_context_first_read import read_selection
    from tests.control_plane.test_quota_settlement_cli import (
        AGENT_ID, GOAL_ID, TURN_ID, _heartbeat_receipt_count, _initialize_fixture_authority,
        _run_cli, _spend_run_count, _write_fixture,
    )
    from loopx.control_plane.turn_driver.decision_capture import read_context_read_reference

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    project, runtime, registry = _write_fixture(tmp_path)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    with state.open("a", encoding="utf-8") as stream:
        stream.write("\n- [ ] [P2] Verify a different source.\n"
            f"  <!-- loopx:todo todo_id=todo_other_source status=open claimed_by={AGENT_ID} "
            "task_class=advancement_task action_kind=validate -->\n")
    _initialize_fixture_authority(project, runtime, provider=provider)
    original_messages = {}
    for index in range(23):
        message = f"Original requirement {index}: " + "核对完整验收证据与后续条件。\n" * (500 if index == 0 else 180)
        result = deliver(runtime, registry, session={"session_id": "source", "channel_id": "manager"},
            turn={"client_turn_id": f"synthetic-review-{index}", "origin": "web", "message": message},
            request={"goal_id": GOAL_ID, "agent_id": AGENT_ID})
        original_messages[result["request_id"]] = message
    request_ids = set(original_messages)

    captures = tmp_path / "captures"
    captures.mkdir(mode=0o700)
    console = Path(sysconfig.get_path("scripts")) / "loopx"
    assert console.is_file(), "Install this checkout's console with uv sync --extra test."
    code, view = _run_cli(registry, runtime, "--cli-bin", str(console), "quota", "should-run", "--codex-app",
        "--context-delivery", "on", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--turn-instance-id", TURN_ID, "--decision-output-root", str(captures), cwd=project)
    assert code == 0 and view["mode"] == "full", view
    _, reference = read_context_read_reference(view["recovery"]["reference_file"])
    path = Path(reference["path"])
    original = path.read_bytes()
    channel = json.loads(original)["interaction_contract"]["agent_channel"]
    index, = (i for i, row in enumerate(channel["work_context"]["sources"]) if row.get("kind") == "operator_inbox")
    address = f"/interaction_contract/agent_channel/work_context/sources/{index}/content/unfinished_delegations"
    _, first = read_selection(view["first_read_command"])
    if "remaining_read_command" in view:
        _, remaining = read_selection(view["remaining_read_command"])
        first.update(remaining)
    cli = "/interaction_contract/cli_channel"
    assert first[cli + "/selection_required"] is True
    assert first[cli + "/spend_allowed_now"] is False
    assert first["/interaction_contract/agent_channel/delivery_allowed"] is False
    assert address not in first and address + "/items" not in first
    assert first[address + "/incoming_count"] == len(request_ids) == 23
    assert first[address + "/omitted_count"] > 0
    read, = (row for row in first["/interaction_contract/agent_channel/required_reads"] if row.get("kind") == "operator_inbox")
    command = shlex.split(read["command"])
    assert command[0] == str(console)
    def invoke(argv):
        result = subprocess.run(argv, cwd=project, capture_output=True, timeout=40)
        assert result.returncode == 0, result.stderr
        assert 0 < len(result.stdout) <= 12_000
        return result.stdout.decode()

    def read_receipts():
        root = _root(runtime)
        return {file.relative_to(root): file.read_bytes() for file in root.rglob("*.json")}

    cursor, seen, captured_pages, logical_pages = None, set(), 0, 0
    while True:
        stdout = invoke([*command, *(["--cursor", cursor] if cursor else [])])
        page = json.loads(stdout.split("\n```json\n", 1)[1].removesuffix("\n```\n"))
        logical_pages += 1
        if "command" in page:
            assert page["ok"] and not page["read_complete"] and "items" not in page
            reader = shlex.split(page["command"])
            assert reader[:3] == [str(console), "--cli-bin", str(console)] and "context-read" in reader
            assert "--capture-sha256" not in reader and "--capture-file" not in reader
            reference_path, descriptor = read_context_read_reference(reader[reader.index("--reference-file") + 1])
            saved = Path(descriptor["path"])
            pinned = page["sha256"]
            assert pinned == descriptor["sha256"] and reference_path.parent == saved.parent
            direct = shlex.split(page["recovery_command"])
            assert direct[:3] == reader[:3]
            assert direct[direct.index("--capture-sha256") + 1] == pinned
            before = read_receipts()
            raw = bytearray()
            while reader:
                part = json.loads(invoke(reader))
                assert part["ok"] and part["offset"] == len(raw) and part["sha256"] == pinned
                raw.extend(part["content"].encode("utf-8"))
                assert len(raw) > part["offset"]
                assert len(raw) == (part["total_bytes"] if part["eof"] else part["next_offset"])
                reader = shlex.split(part["next_command"]) if not part["eof"] else None
                if reader:
                    assert reader[:3] == direct[:3] and "--capture-sha256" not in reader
                    assert Path(reader[reader.index("--reference-file") + 1]) == reference_path
                captured_pages += 1
            assert raw == saved.read_bytes()
            if logical_pages == 1:
                reference_path.unlink()
                failed = subprocess.run(shlex.split(page["command"]), cwd=project, capture_output=True, timeout=40)
                assert failed.returncode == 1 and len(failed.stdout) <= 12_000
                assert "content" not in json.loads(failed.stdout)
                recovered = json.loads(invoke(direct))
                assert recovered["offset"] == 0 and recovered["sha256"] == pinned
                assert raw.startswith(recovered["content"].encode("utf-8"))
            assert read_receipts() == before
            page = json.loads(raw)
        page_ids = {item["request_id"] for item in page["items"]}
        assert page["ok"] and page_ids and page_ids <= request_ids - seen
        for item in page["items"]:
            assert item["message"] == original_messages[item["request_id"]]
        seen.update(page_ids)
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == request_ids and logical_pages == 2 and captured_pages > 1
    _, saved = read_selection(view["recovery"]["command"] + " --select " + shlex.quote(address))
    assert saved[address] == channel["work_context"]["sources"][index]["content"]["unfinished_delegations"]
    assert path.read_bytes() == original
    assert _heartbeat_receipt_count(runtime, TURN_ID) == 1 and _spend_run_count(runtime) == 0


@pytest.mark.skipif(os.name != "posix", reason="exercise the private transport's symlink refusal")
def test_large_inbox_does_not_fall_back_to_unbounded_output(flow):
    root, registry, _, create = flow
    _, _, receipt = create()
    entry, = (_root(root) / "entries").glob(f"*/{receipt['request_id']}.json")
    stored = json.loads(entry.read_bytes())
    message = "Long original request.\n" * 1000
    _write(entry, {**stored, "message": message})
    before = entry.read_bytes()
    destination = root / "untrusted"
    destination.mkdir()
    (root / "context-delivery").symlink_to(destination, target_is_directory=True)
    invocation = [sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry), "--runtime-root", str(root),
        "manager-inbox", "read", "--format", "markdown", "--goal-id", "research", "--agent-id", "worker"]
    result = subprocess.run(invocation, cwd=root, capture_output=True, timeout=40)
    assert result.returncode == 1 and len(result.stdout) <= 12_000
    output = result.stdout.decode()
    failure = json.loads(output.split("\n```json\n", 1)[1].removesuffix("\n```\n"))
    assert not failure["ok"] and not failure["read_complete"]
    assert "items" not in failure and "command" not in failure and "Long original request." not in output
    assert list(destination.iterdir()) == []
    assert entry.read_bytes() == before


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is not installed")
@pytest.mark.parametrize("directory", ["release", "owner's release"])
def test_large_inbox_reader_commands_run_verbatim_in_powershell(flow, directory):
    from loopx.command_invocation import loopx_cli_route

    root, registry, _, create = flow
    _, _, receipt = create()
    entry, = (_root(root) / "entries").glob(f"*/{receipt['request_id']}.json")
    stored = json.loads(entry.read_bytes())
    message = "Preserve the original constraint 核对原约束.\n" * 1100
    _write(entry, {**stored, "message": message})
    checkout = Path(__file__).resolve().parents[1]
    launcher = root / directory / "loopx.ps1"
    launcher.parent.mkdir()
    shutil.copyfile(checkout / "scripts/loopx.ps1", launcher)
    pointer = launcher.with_name("loopx-current-release.json")
    pointer.write_text(json.dumps({"release_root": str(checkout), "python": sys.executable}), encoding="utf-8")
    environment = dict(os.environ)
    environment.pop("LOOPX_CURRENT_RELEASE_FILE", None)
    environment["LOOPX_USAGE_PING"] = "0"
    powershell = [shutil.which("pwsh"), "-NoProfile", "-NonInteractive"]
    with loopx_cli_route(str(launcher)):
        hook = turn_start_hook(root, registry, "research", "worker", model_read=True)
        dispatch = dispatch_turn_start_hooks([hook])
    required_read, = dispatch["required_reads"]
    opening = subprocess.run([*powershell, "-Command", required_read["command"]], cwd=root, env=environment,
        capture_output=True, timeout=60)
    assert opening.returncode == 0, opening.stderr
    assert 0 < len(opening.stdout) <= 12_000
    markdown = opening.stdout.decode().replace("\r\n", "\n")
    view = json.loads(markdown.split("\n```json\n", 1)[1].removesuffix("\n```\n"))
    before = {p.relative_to(root): p.read_bytes() for p in _root(root).rglob("*.json")}
    original = bytearray()
    command = view["command"]
    pages = 0
    while command:
        output = subprocess.run([*powershell, "-Command", command], cwd=root, env=environment,
            capture_output=True, timeout=60)
        assert output.returncode == 0, output.stderr
        assert 0 < len(output.stdout) <= 12_000
        page = json.loads(output.stdout)
        assert page["ok"] and page["offset"] == len(original) and page["sha256"] == view["sha256"]
        original.extend(page["content"].encode())
        assert len(original) == (page["total_bytes"] if page["eof"] else page["next_offset"])
        command = None if page["eof"] else page["next_command"]
        pages += 1
    recovered, = json.loads(original)["items"]
    assert pages > 1 and recovered["request_id"] == receipt["request_id"] and recovered["message"] == message
    assert {p.relative_to(root): p.read_bytes() for p in _root(root).rglob("*.json")} == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("delivery", ["raw_envelope", "unified_inline", pytest.param("native_files",
    marks=pytest.mark.skipif(os.name != "posix", reason="native context delivery requires POSIX"))])
@pytest.mark.parametrize("partial", [False, True], ids=["unanswered", "partial"])
@pytest.mark.parametrize("parent_context", ["off", "on"])
def test_actual_cli_guards_and_host_envelopes_preserve_the_agenda(
    tmp_path, monkeypatch, provider, delivery, partial, parent_context,
):
    from control_plane.canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.testing.canary_harness import write_fixture_registry
    from loopx.control_plane.todos.active_state_todo_parser import parse_todo_source
    from loopx.control_plane.todos.goal_todo_projection import retained_todo_summary_fields
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    # A module launched from the checkout can mask a stale inherited package.
    # Exercise the installed console from the fixture project instead.
    stale_package = tmp_path / "stale-release" / "loopx"
    stale_package.mkdir(parents=True)
    (stale_package / "__init__.py").write_text('raise RuntimeError("wrong source checkout")\n')
    monkeypatch.setenv("PYTHONPATH", str(stale_package.parent))
    console = Path(sysconfig.get_path("scripts")) / ("loopx.exe" if os.name == "nt" else "loopx")
    assert console.is_file(), "Install this checkout's console with uv sync --extra test."
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY", parent_context)
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}

    def run(*arguments):
        result = subprocess.run(arguments, cwd=tmp_path, env=environment,
            capture_output=True, text=True, encoding="utf-8", timeout=90)
        assert result.stdout.strip(), result.stderr
        return result.returncode, json.loads(result.stdout)

    root, registry, state = tmp_path / "runtime", tmp_path / "registry.json", tmp_path / "state.md"
    prefix = [str(console), "--registry", str(registry), "--runtime-root", str(root), "--format", "json"]
    text = ("---\nstatus: active\n---\n# Goal\n## Objective\nQualify the report.\n## Agent Todo\n"
        "- [ ] [P1] Inspect the current report and return findings.\n"
        "  <!-- loopx:todo todo_id=todo_inspect status=open task_class=advancement_task claimed_by=worker action_kind=inspect_report -->\n"
        "- [x] Artifact implementation finished, but its request still needs a returned answer.\n"
        "  <!-- loopx:todo todo_id=todo_artifact status=done task_class=advancement_task claimed_by=worker action_kind=implement -->\n")
    state.write_text(text)
    write_fixture_registry(project=tmp_path, runtime_root=root, registry_path=registry,
        goal_id="research", domain="software", adapter_kind="generic_project_goal_v0",
        state_file=str(state), registered_agents=["worker", "reviewer"], quota_allowed_slots=None)
    goal = json.loads(registry.read_text())["goals"][0]
    fields, _, _ = parse_todo_source(text, goal=goal, state_path=state)
    todos = retained_todo_summary_fields(fields["agent"], rollout_events=[])["agent_todos"]["items"]
    initialize_canonical_authority(root, "research", build_todo_runtime_shadow_projection(
        goal_id="research", todos=todos, handoff_mode="soft_claim"), state_path=state, provider=provider)
    receipt = deliver(root, registry, session={"session_id": "source", "channel_id": "manager"},
        turn={"client_turn_id": "request", "origin": "web", "message": "Verify the artifact before adopting it."},
        request={"goal_id": "research", "agent_id": "worker"})
    acknowledge(root, "research", "worker", receipt["request_id"], "adopt", "Continue the linked work", registry=registry)
    from loopx.control_plane.collaboration.links import link
    link(root, registry, "research", "worker", receipt["request_id"], ["todo_artifact"], [])
    remaining_work = {"summary": "Validate the packaged repair", "resume_when": "The isolated package is available"}
    if partial:
        report(root, "research", "worker", receipt["request_id"], "conclusion", "The implementation is ready.",
            remaining_work=remaining_work, registry=registry)
    queued = deliver(root, registry, session={"session_id": "source", "channel_id": "manager"},
        turn={"client_turn_id": "queued", "origin": "web", "message": "Return the checked result to this conversation."},
        request={"goal_id": "research", "agent_id": "worker"})
    acknowledge(root, "research", "worker", queued["request_id"], "adopt", "Return the result", registry=registry)
    report(root, "research", "worker", queued["request_id"], "conclusion", "The isolated artifact passed.", registry=registry)
    peer = request(root, registry, "research", "worker", "reviewer", "review-artifact", {
        "schema_version": "collaboration_brief_v0", "purpose": "Independently verify the artifact",
        "context": "A returned review still requires requester consumption", "constraints": [], "inputs": [],
        "acceptance": ["Return checked findings"], "return_requirement": "Return to the original requester",
    })
    acknowledge(root, "research", "reviewer", peer["request_id"], "adopt", "Review the artifact", registry=registry)
    report(root, "research", "reviewer", peer["request_id"], "conclusion", "The artifact retains an explicit limit.", registry=registry)
    code, inbox = run(*prefix, "manager-inbox", "read", "--goal-id", "research", "--agent-id", "worker")
    assert code == 0 and inbox["ok"]
    assert inbox["peer_returns"]["items"][0]["request_id"] == peer["request_id"]
    code, tracking = run(*prefix, "manager-inbox", "status", "--goal-id", "research", "--agent-id", "worker",
        "--request-id", receipt["request_id"])
    assert code == 0 and tracking["rows"][0]["read"]["at"]
    before = {p.relative_to(root): p.read_bytes() for p in _root(root).rglob("*.json")}
    if delivery == "native_files":
        from loopx.control_plane.turn_driver.codex_context import codex_context_directory, codex_context_hook
        (tmp_path / "captures").mkdir()
        (tmp_path / "context").mkdir(mode=0o700)
        monkeypatch.setenv("CODEX_THREAD_ID", "agenda-session")
        directory = codex_context_directory(root=str(tmp_path / "context"), create=True)
        codex_context_hook(directory, {"session_id": "agenda-session", "cwd": str(tmp_path),
            "hook_event_name": "SessionStart", "source": "startup"})
    for turn_id in ("agenda-turn-one", "agenda-turn-two"):
        # Explicit legacy display and canonical machine JSON have different
        # read shapes. Native must exercise capture even under an inline parent.
        if delivery == "raw_envelope":
            presentation = ["--turn-envelope", "--context-delivery", "off"]
        elif delivery == "unified_inline":
            presentation = ["--context-delivery", "off"]
        else:
            presentation = ["--codex-app", "--context-delivery", "on",
                "--decision-output-root", str(tmp_path / "captures"),
                "--codex-context-root", str(tmp_path / "context")]
        code, packet = run(*prefix, "quota", "should-run", "--goal-id", "research",
            "--agent-id", "worker",  "--turn-instance-id", turn_id,
            *presentation)
        assert code == 0, (packet.get("error_code"), packet.get("reason"), packet.get("recovery"))
        if delivery == "native_files":
            if turn_id == "agenda-turn-one":
                assert packet["mode"] == "full" and "first_read_command" in packet
                command = packet["first_read_command"]
            else:
                # The native owner decides whether continuity permits a delta.
                # Read this Turn's current capture without seeding an old baseline.
                assert packet["mode"] in ("full", "delta")
                command = packet["recovery"]["command"] + " --initial-read"
            command += " --page-bytes 3000 --max-page-output-bytes 6000"
            content = bytearray()
            pages = 0
            while True:
                words = shlex.split(command)
                assert words[:3] == [sys.executable, "-m", "loopx.entrypoint"]
                code, page = run(*words)  # Preserve the producer's interpreter and argv.
                assert code == 0 and page["ok"] and page["offset"] == len(content), page
                content.extend(page["content"].encode("utf-8"))
                pages += 1
                if page["eof"]:
                    assert page["next_offset"] is None and page["total_bytes"] == len(content)
                    break
                assert page["next_offset"] == len(content) > page["offset"]
                command = page["next_command"]
            assert pages > 1, "Exercise complete selected-context pagination."
            from tests.control_plane.codex_checkpoint_fixture import selected_context_values

            values = selected_context_values(content.decode("utf-8"))
            pointer = next(key for key in values if key.endswith("/content/unfinished_delegations"))
            content = {"unfinished_delegations": values[pointer],
                "read_complete": values[pointer.removesuffix("unfinished_delegations") + "read_complete"]}
            required_reads = values["/interaction_contract/agent_channel/required_reads"]
        else:
            if delivery == "raw_envelope":
                assert packet["schema_version"] == "loopx_turn_envelope_v0"
                channel = packet
            else:
                channel = packet["interaction_contract"]["agent_channel"]
            work_context = channel["work_context"]
            source = next(row for row in work_context["sources"] if row.get("hook_id") == "manager.context_inbox")
            content, required_reads = source["content"], channel["required_reads"]
            assert work_context["complete"] is True
        assert content["read_complete"] is False
        rows = {row["request_id"]: row for row in content["unfinished_delegations"]["items"]}
        assert set(rows) == {receipt["request_id"], queued["request_id"], peer["request_id"]}
        row = rows[receipt["request_id"]]
        assert row["todo_ids"] == ["todo_artifact"] and row["step"] == "review_request_work"
        assert row.get("remaining_work") == (remaining_work if partial else None)
        assert rows[queued["request_id"]]["step"] == "await_return_delivery"
        assert rows[queued["request_id"]]["pending_return_count"] == 1
        assert rows[peer["request_id"]]["step"] == "consume_peer_result"
        manager_read = next(item for item in required_reads if item["hook_id"] == "manager.context_inbox")
        manager_argv = shlex.split(manager_read["command"])
        assert "manager-inbox" in manager_argv and "read" in manager_argv
        assert ("markdown" in manager_argv) == (delivery == "native_files")
        assert "Continue the linked work" not in json.dumps(content)
        assert os.environ["LOOPX_CONTEXT_DELIVERY"] == parent_context
    if delivery == "native_files":
        words = shlex.split(manager_read["command"])
        assert words[:3] == [sys.executable, "-m", "loopx.entrypoint"]
        result = subprocess.run(words, cwd=tmp_path, env=environment,
            capture_output=True, text=True, encoding="utf-8", timeout=90)
        assert result.returncode == 0, result.stderr
        body = result.stdout.split("\n```json\n", 1)[1].removesuffix("\n```\n")
        rendered = json.loads(body)
        common = rendered.pop("shared_item_guidance", {})
        rendered["items"] = [{**common, **row} for row in rendered["items"]]
        # Both commands perform the same scoped read. Each reports its own
        # observation clock; all supplied values and cursor facts must agree.
        for view in (inbox, rendered):
            view["unfinished_delegations"].pop("observed_at")
        assert rendered == inbox
    assert {p.relative_to(root): p.read_bytes() for p in _root(root).rglob("*.json")} == before


@pytest.mark.parametrize("variant", ["identical", "different", "missing"])
def test_inbox_markdown_keeps_different_or_missing_guidance_on_each_item(tmp_path, variant):
    from loopx.cli_commands.manager_inbox import _read_markdown

    items = [
        {"instruction": "Read the complete source.", "next_action": "Continue this request.", "message": "Original."},
        {"instruction": "Read the complete source.", "next_action": "Wait for the original return.", "revision": 2},
        {"instruction": "Read the complete source.", "unavailable": True},
    ]
    advice = {"step": "assess", "answer_owed": True, "future_details": {"attempt": 1}}
    for item in items:
        item["receiver_followthrough"] = advice
    if variant == "different":
        items[1]["receiver_followthrough"] = {**advice, "future_details": {"attempt": True}}
    elif variant == "missing":
        del items[1]["receiver_followthrough"]
    source = {"ok": True, "items": items, "has_more": True, "next_cursor": "scoped-next"}
    body, ok = _read_markdown(source, runtime_root=tmp_path)
    assert ok and not (tmp_path / "context-delivery").exists()
    body = body.split("\n```json\n", 1)[1].removesuffix("\n```")
    view = json.loads(body)
    common = view.pop("shared_item_guidance")
    expected = {"instruction": "Read the complete source."}
    if variant == "identical":
        expected["receiver_followthrough"] = advice
    assert common == expected
    assert all("instruction" not in item for item in view["items"])
    view["items"] = [{**common, **item} for item in view["items"]]
    assert json.dumps(view, sort_keys=True) == json.dumps(source, sort_keys=True)


def test_real_overflow_keeps_total_and_full_read_route(flow):
    root, registry, _store, create = flow
    for _ in range(26):
        create()
    result = agenda(root, registry)
    assert result["incoming_count"] == 26
    assert len(result["items"]) == 20
    assert result["omitted_count"] == 6
    assert result["scan_complete"] is True
    assert "manager-inbox read" in result["read_command"]
