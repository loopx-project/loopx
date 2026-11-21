from __future__ import annotations

from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.runtime.time import now_utc, utc_isoformat
from loopx.control_plane.testing.canary_harness import write_fixture_registry, run_json_cli_result
from loopx.control_plane.quota.execution_preparation import (
    RefreshTaskLeaseUnavailable,
    apply_selected_todo_execution_preparation,
)


GOAL = "goal-execution-preparation"
TODO = "todo_execution_preparation"
AGENT = "agent-a"


def test_default_runner_keeps_a_valid_receipt_when_stderr_is_undecodable(
    tmp_path: Path,
) -> None:
    from loopx.control_plane.turn_driver import codex_execution_preparation

    completed = codex_execution_preparation._default_runner(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('{\"ok\": true}'); "
            "sys.stderr.buffer.write(b'\\xff\\n')",
        ],
        cwd=tmp_path,
        timeout=30,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {"ok": True}
    assert "\ufffd" in completed.stderr


def test_default_runner_keeps_receipt_stdout_strict(tmp_path: Path) -> None:
    from loopx.control_plane.turn_driver import codex_execution_preparation

    with pytest.raises(UnicodeDecodeError):
        codex_execution_preparation._default_runner(
            [
                sys.executable,
                "-c",
                "import sys; sys.stdout.buffer.write(b'\\xff')",
            ],
            cwd=tmp_path,
            timeout=30,
        )


@pytest.mark.parametrize("status", ["open", "deferred"])
def test_explicit_non_delivery_obligation_does_not_prepare_selected_execution(monkeypatch, status):
    from loopx.control_plane.quota import execution_preparation

    def no_execution_read(**_):
        pytest.fail("A planning operation must not consult the selected delivery Todo's lease")

    monkeypatch.setattr(execution_preparation, "project_selected_todo_execution_preparation", no_execution_read)
    obligation = {"must_attempt_work": True, "delivery_allowed": False}
    payload = {"should_run": True, "selected_todo": {"todo_id": TODO, "status": status},
        "execution_obligation": obligation}
    apply_selected_todo_execution_preparation(payload, runtime_root=None, goal_id=GOAL,
        agent_id=AGENT, obligation=obligation)
    assert payload == {"should_run": True, "selected_todo": {"todo_id": TODO, "status": status},
        "execution_obligation": {"must_attempt_work": True, "delivery_allowed": False}}


def _todo() -> dict[str, object]:
    return {
        "schema_version": "todo_item_v0",
        "todo_id": TODO,
        "role": "agent",
        "source_section": "Agent Todo",
        "archive_state": "active",
        "text": "Deliver one validated synthetic change",
        "status": "open",
        "done": False,
        "task_class": "advancement_task",
        "priority": "P1",
        "claimed_by": AGENT,
        "index": 1,
    }


def _lease(*, owner: str = AGENT, status: str = "active", expired: bool = False) -> dict[str, object]:
    at = now_utc()
    return {
        "schema_version": "task_lease_v0",
        "goal_id": GOAL,
        "todo_id": TODO,
        "owner": owner,
        "idempotency_key": f"{owner}-execution",
        "write_scopes": [],
        "acquire_ttl_seconds": 600,
        "version": 2,
        "lease_epoch": 1,
        "status": status,
        "acquired_at": utc_isoformat(at - timedelta(seconds=120)),
        "updated_at": utc_isoformat(at - timedelta(seconds=120)),
        "expires_at": utc_isoformat(at - timedelta(seconds=60) if expired else at + timedelta(seconds=600)),
        **({"released_at": utc_isoformat(at)} if status == "released" else {}),
    }


def _run(
    tmp_path,
    monkeypatch,
    provider: str,
    leases: list[dict[str, object]],
    *guard_args: str,
    todos: list[dict[str, object]] | None = None,
    context_delivery: str = "off",
    goal_suffix: str = "",
    write_scopes: list[str] | None = None,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    state = tmp_path / "state.md"
    state.write_text(
        "---\nstatus: active\n---\n# Goal\n\n## Objective\n"
        "Deliver one validated synthetic change.\n\n## Next Action\n"
        "Run the selected Todo.\n" + goal_suffix,
        encoding="utf-8",
    )
    write_fixture_registry(
        project=tmp_path,
        runtime_root=runtime,
        registry_path=registry,
        goal_id=GOAL,
        domain="execution-preparation",
        adapter_kind="generic_project_goal_v0",
        state_file=str(state),
        registered_agents=[AGENT, "agent-b"],
        quota_allowed_slots=None,
        extra_goal_fields={"coordination": {
            "registered_agents": [AGENT, "agent-b"],
            "handoff_mode": "hard_lease",
            **({"write_scope": write_scopes, "requires_parent_approval": ["publish"]} if write_scopes else {}),
        }},
    )
    projection = build_todo_runtime_shadow_projection(
        goal_id=GOAL,
        todos=[_todo()] if todos is None else todos,
        leases=leases,
        handoff_mode="hard_lease",
    )
    initialize_canonical_authority(
        runtime, GOAL, projection, state_path=state, provider=provider,
    )
    code, packet = run_json_cli_result(
        "quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT,
        "--context-delivery", context_delivery,
        *guard_args,
        registry_path=registry, runtime_root=runtime,
    )
    assert code == 0, json.dumps(packet, ensure_ascii=False, indent=2)
    return packet


def _present_saved_guard(tmp_path, native, capture, scenario, turn):
    source = capture / "decision.json"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    code, view = run_json_cli_result(
        "quota", "context-project", "--runtime-profile", scenario,
        "--context-delivery", "on", "--codex-context-dir", str(native),
        "--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", turn,
        "--capture-file", str(source), "--capture-sha256", digest,
        registry_path=tmp_path / "registry.json", runtime_root=tmp_path / "runtime",
    )
    assert code == 0 and view["ok"] is True, view
    return digest


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize(
    "leases,reason_code",
    [
        ([], "task_lease_missing"),
        ([_lease(status="released")], "task_lease_released"),
        ([_lease(expired=True)], "task_lease_expired"),
    ],
)
def test_should_run_requires_one_atomic_claim_before_delivery(
    tmp_path, monkeypatch, provider, leases, reason_code,
):
    packet = _run(tmp_path, monkeypatch, provider, leases)
    preparation = packet["execution_preparation"]
    assert preparation["state"] == "claim_required"
    assert preparation["reason_code"] == reason_code
    assert preparation["claim"]["host_execution_allowed"] is True
    assert preparation["claim"]["command_line"].startswith("loopx todo claim ")
    assert packet["should_run"] is True
    assert packet["normal_delivery_allowed"] is False
    assert packet["selected_todo"]["claim_required_before_work"] is True
    contract = packet["interaction_contract"]
    assert contract["agent_channel"]["must_attempt"] is True
    assert contract["agent_channel"]["delivery_allowed"] is False
    assert contract["cli_channel"]["next_cli_actions"][0] == preparation["claim"]["command_line"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_matching_active_lease_preserves_delivery(tmp_path, monkeypatch, provider):
    packet = _run(tmp_path, monkeypatch, provider, [_lease()])
    assert packet["execution_preparation"]["state"] == "ready"
    assert packet["execution_preparation"]["lease_proof"] == {
        "idempotency_key": _lease()["idempotency_key"], "expected_version": _lease()["version"],
    }
    assert packet["normal_delivery_allowed"] is True
    assert packet["interaction_contract"]["agent_channel"]["delivery_allowed"] is True


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_execution_preparation_transports_only_the_selected_provider_facts(
    tmp_path, monkeypatch, provider,
):
    from loopx.control_plane.todos.goal_todo_projection import project_todo_read

    other = {**_todo(), "todo_id": "todo_unrelated", "index": 2, "claimed_by": "agent-b",
             "text": "[P1] Unrelated work", "note": "Unrelated durable evidence " * 200}
    own_lease = _lease()
    foreign_lease = {**_lease(owner="agent-b"), "todo_id": other["todo_id"]}
    packet = _run(tmp_path, monkeypatch, provider, [own_lease, foreign_lease],
                  todos=[_todo(), other])
    assert packet["execution_preparation"]["state"] == "ready"
    runtime = tmp_path / "runtime"
    provider_head = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL, include_leases=True)

    def read(todo_id, source_root=runtime):
        return project_todo_read({
            "kind": "execution_preparation_source",
            "source": {"runtime_root": str(source_root), "goal_id": GOAL}, "todo_id": todo_id,
        })["execution_preparation_source"]

    observed = read(TODO)
    assert observed["goal_id"] == GOAL and observed["todo_id"] == TODO
    assert observed["provider_revision"] == provider_head["provider_revision"]
    assert observed["source_authority"] == provider_head["source_authority"]
    assert observed["decision_read_from_provider"] and not observed["legacy_fallback_used"]
    assert observed["todo"] == {"todo_id": TODO, "status": "open", "claimed_by": AGENT}
    assert [row["todo_id"] for row in observed["leases"]] == [TODO]
    assert observed["leases"][0]["expires_at"] == own_lease["expires_at"]
    assert other["todo_id"] not in json.dumps(observed) and len(json.dumps(observed)) < 2048
    assert read("todo_missing") is None
    absent = tmp_path / "unbound-runtime"
    assert read(TODO, absent) is None
    assert not absent.exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_projected_claim_prepares_the_same_todo_for_a_fresh_guard(
    tmp_path, monkeypatch, provider,
):
    before = _run(tmp_path, monkeypatch, provider, [])
    claim_args = before["execution_preparation"]["claim"]["cli_args"]

    claim_code, claim = run_json_cli_result(
        *claim_args,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
    )
    assert claim_code == 0, json.dumps(claim, ensure_ascii=False, indent=2)

    guard_code, after = run_json_cli_result(
        "quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT,
        "--context-delivery", "off",
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
    )
    assert guard_code == 0, json.dumps(after, ensure_ascii=False, indent=2)
    assert after["selected_todo"]["todo_id"] == TODO
    assert after["execution_preparation"]["state"] == "ready"
    assert after["normal_delivery_allowed"] is True


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("projection_available", [True, False])
def test_model_claim_keeps_receipt_without_unrelated_projection_details(
    tmp_path, monkeypatch, provider, projection_available,
):
    other_todos = [
        {**_todo(), "todo_id": f"todo_other_{index:03d}", "index": index + 2,
         "claimed_by": "agent-b", "text": f"[P1] Unrelated work {index}",
         "note": ("Supporting details for unrelated work. " * 30).strip()}
        for index in range(72)
    ]
    selected = {**_todo(), "text": "[P1] Deliver one validated synthetic change"}
    before = _run(tmp_path, monkeypatch, provider, [], todos=[selected, *other_todos],
                  goal_suffix="\n## Agent Todo\n")
    preparation = before["execution_preparation"]
    assert preparation["state"] == "claim_required"
    action = shlex.split(before["interaction_contract"]["cli_channel"]["next_cli_actions"][0])
    assert action[0] == "loopx"
    assert "--thin" not in preparation["claim"]["cli_args"]  # Exact Host action.
    operation = ["--claim-operation-id", "synthetic-thin-claim"]

    def invoke(*args):
        rc, result = run_json_cli_result(
            *args, registry_path=tmp_path / "registry.json", runtime_root=tmp_path / "runtime",
        )
        assert rc == 0 and result["ok"] is True, result
        return result

    state = tmp_path / "state.md"
    if not projection_available:
        state.unlink()
        state.mkdir()  # The authority is available; only the display write fails.
    claim = invoke(*action[1:], *operation)
    assert claim["status"] == "applied" and claim["claim_operation_id"] == operation[1]
    assert claim["todo_id"] == TODO and claim["claimed_by"] == AGENT
    initial = invoke("task-lease", "inspect", "--goal-id", GOAL, "--todo-id", TODO)
    assert initial["active"] is True and initial["lease"]["owner"] == AGENT
    outbox = claim["projection_outbox"]
    if not projection_available:
        assert claim["projection_delivery"] == outbox["status"] == "pending"
        assert outbox["reason_code"] and outbox["recommended_action"]
        assert outbox["retryable"] is True and outbox["retry_business_mutation"] is False
        assert "detail_command" not in outbox
        return

    assert claim["projection_delivery"] == outbox["status"] == "delivered", outbox
    assert claim["next_cli_actions"] and outbox["next_action"] == "finish"
    if provider == "sqlite":
        assert outbox["active_fields_omitted"] == len(other_todos)
        assert "omitted_active_records" not in outbox
        before_read = state.read_bytes()
        diagnostic_args = shlex.split(outbox["detail_command"])
        detail = invoke(*diagnostic_args[diagnostic_args.index("todo"):])
        assert detail["dry_run"] is True and detail["provider_revision"] == outbox["provider_revision"]
        assert state.read_bytes() == before_read
        rows = detail["omitted_active_records"]
        assert {row["todo_id"] for row in rows} == {row["todo_id"] for row in other_todos}
    else:
        rows = []
        assert outbox["active_fields_omitted"] == 0 and outbox["omitted_active_records"] == rows
        assert "detail_command" not in outbox
    assert "--thin" in action
    replay = invoke(*preparation["claim"]["cli_args"], *operation)
    assert replay["claim_operation_id"] == claim["claim_operation_id"]
    assert replay["projection_outbox"]["omitted_active_records"] == rows
    current = invoke("task-lease", "inspect", "--goal-id", GOAL, "--todo-id", TODO)
    assert current["lease"] == initial["lease"]


@pytest.mark.parametrize("provider,scenario,location,ambient", [
    (provider, scenario, "directory", "preparation-session")
    for provider in ("file", "sqlite")
    for scenario in ("codex_app_heartbeat", "codex_app_ssh_goal")
] + [
    ("sqlite", "codex_app_heartbeat", "root", None),
    ("sqlite", "codex_app_heartbeat", "root", "other-session"),
])
def test_native_hook_returns_readable_ready_source_and_original_claim(
    tmp_path, monkeypatch, provider, scenario, location, ambient,
):
    from loopx.control_plane.turn_driver import codex_context

    tmp_path = tmp_path.resolve()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEX_THREAD_ID", "preparation-session")
    root = tmp_path / "native"
    root.mkdir(mode=0o700)
    native = root
    if location == "root":
        native = Path(codex_context.codex_context_directory(directory=None, root=str(root),
            session="preparation-session", create=True))
    event = {"session_id": "preparation-session", "cwd": str(tmp_path),
        "hook_event_name": "SessionStart", "source": "startup"}
    assert codex_context.codex_context_hook(str(native), event) == {}
    original = {**_todo(), "text": "Deliver the complete synthetic request: 保留原文" + "。不可截断的验收条件" * 100,
        "note": "Preserve the original stop and once-only settlement."}
    capture = tmp_path / "before-claim"
    before = _run(tmp_path, monkeypatch, provider, [], "--runtime-profile", scenario,
        "--turn-instance-id", "preparation-turn", "--todo-id", TODO,
        "--defer-context-presentation", "--codex-context-dir", str(native), "--decision-output-dir", str(capture),
        todos=[original], context_delivery="on",
        goal_suffix="\n\n## Constraints\n\n" + "Preserve the complete Goal and its stop. 保留停止条件。\n" * 100)
    assert before["execution_preparation"]["state"] == "claim_required"
    goal_text = (tmp_path / "state.md").read_text()
    digest = _present_saved_guard(tmp_path, native, capture, scenario, "preparation-turn")
    hook = [sys.executable, "-m", "loopx.entrypoint", "--registry", str(tmp_path / "registry.json"),
        "--runtime-root", str(tmp_path / "runtime"), "--format", "json", "quota", "context-hook",
        "--context-delivery", "on", "--runtime-profile", scenario,
        "--codex-context-root" if location == "root" else "--codex-context-dir", str(root)]
    hook_env = os.environ.copy()
    if ambient is None:
        hook_env.pop("CODEX_THREAD_ID", None)
    else:
        hook_env["CODEX_THREAD_ID"] = ambient
    hook_event = {**event, "hook_event_name": "PostToolUse"}
    log = tmp_path / "runtime" / "goals" / GOAL / "rollout-event-log.jsonl"
    unchanged = log.read_bytes()
    disabled = subprocess.run(["off" if item == "on" else item for item in hook],
        input=json.dumps(hook_event), env=hook_env, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert disabled.returncode == 0 and json.loads(disabled.stdout) == {}
    assert log.read_bytes() == unchanged
    completed = subprocess.run(hook, input=json.dumps(hook_event), env=hook_env, cwd=tmp_path,
        capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    additional = json.loads(completed.stdout)["hookSpecificOutput"]["additionalContext"]
    receipt = json.loads(additional.rsplit("Receipt: ", 1)[1])
    assert receipt["event"] == "host_execution_prepared"
    assert receipt["todo_id"] == TODO and receipt["turn_instance_id"] == "preparation-turn"
    assert receipt["execution_preparation"]["state"] == "ready"
    claim_reference = receipt["claim_operation_id"]
    assert claim_reference == f"todo-claim:codex-capture:{digest}"
    ready = receipt["context"]
    assert ready["ok"], ready.get("reason")
    if location == "root":
        assert list(root.iterdir()) == [native]
    read = shlex.split(ready["recovery"]["command"])
    pointers = ["/execution_preparation", "/selected_todo", "/heartbeat_receipt", "/interaction_contract/cli_channel",
        "/interaction_contract/agent_channel/work_context", "/interaction_contract/agent_channel/required_reads"]
    for pointer in pointers:
        read.extend(("--select", pointer))
    completed = subprocess.run(read, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    page = json.loads(completed.stdout)
    assert page["eof"] is True and page["offset"] == 0
    selected = {item["pointer"]: item["value"] for item in json.loads(page["content"])["selections"]}
    assert selected["/execution_preparation"]["state"] == "ready"
    assert selected["/heartbeat_receipt"]["turn_instance_id"] == "preparation-turn"
    plan = selected["/interaction_contract/cli_channel"]["settlement_plan"]
    assert plan["identity"]["todo_id"] == TODO and plan["identity"]["turn_instance_id"] == "preparation-turn"
    assert plan["identity"] == selected["/heartbeat_receipt"]["settlement_identity"]
    assert plan["schema_version"] == "quota_settlement_plan_v2"
    channel = selected["/interaction_contract/cli_channel"]
    assert channel["quota_spend_source"] == ("heartbeat" if scenario == "codex_app_heartbeat" else "visible-goal")
    writeback, spend = [step for step in plan["ordered_steps"]
                        if step["kind"] in {"durable_writeback", "quota_spend"}]
    assert "--settle-turn --settlement-receipt" in writeback["command_template"]
    assert spend["owner"] == "runtime" and "command_template" not in spend
    sources = selected["/interaction_contract/agent_channel/work_context"]["sources"]
    task = next(item["content"]["todo"] for item in sources if item["source"] == "selected_todo")
    # The v0 owner may deduplicate short text already in selected_todo.
    # The complete request must survive either original placement, without
    # replacing it with the selected summary when that summary is shorter.
    assert task.get("text", selected["/selected_todo"]["text"]) == original["text"]
    assert task["note"] == original["note"]
    goal_reads = [item for item in selected["/interaction_contract/agent_channel/required_reads"]
        if item["source"] == "goal_state"]
    if goal_reads:
        goal = subprocess.run(shlex.split(goal_reads[0]["command"]), capture_output=True, text=True, timeout=30)
        assert goal.returncode == 0 and goal.stdout == goal_text
    else:
        goal = next(item["content"] for item in sources if item["source"] == "goal_state")
        assert goal["text"] == goal_text
    code, current = run_json_cli_result("todo", "list", "--goal-id", GOAL, "--todo-id", TODO,
        registry_path=tmp_path / "registry.json", runtime_root=tmp_path / "runtime", cwd=tmp_path)
    assert code == 0
    code, progress = run_json_cli_result("todo", "update", "--goal-id", GOAL, "--todo-id", TODO,
        "--agent-id", AGENT, "--claim-operation-id", claim_reference,
        "--update-operation-id", "prepared-progress",
        "--update-expected-provider-revision", current["authority_read"]["provider_revision"],
        "--note", original["note"] + " Checkpoint verified.",
        registry_path=tmp_path / "registry.json", runtime_root=tmp_path / "runtime", cwd=tmp_path)
    assert code == 0 and progress["ok"], progress
    events = log.read_bytes()
    repeat = subprocess.run(hook, input=json.dumps(hook_event), env=hook_env, cwd=tmp_path,
        capture_output=True, text=True, timeout=30)
    assert repeat.returncode == 0 and "host_execution_prepared" not in repeat.stdout
    assert log.read_bytes() == events
    guards = [json.loads(line) for line in events.splitlines()
        if json.loads(line)["event_kind"] == "quota_should_run"]
    assert len(guards) == 2
    assert [item["details"]["delivery_allowed"] for item in guards] == [False, True]
    source, _ = codex_context.read_codex_context_reference(str(native), goal_id=GOAL, agent_id=AGENT)
    # Simulate a Host losing the successful Hook output and presenting its
    # original pre-claim capture again in the same session.
    assert _present_saved_guard(tmp_path, native, capture, scenario, "preparation-turn") == digest
    lost_output = subprocess.run(hook, input=json.dumps(hook_event), env=hook_env, cwd=tmp_path,
        capture_output=True, text=True, timeout=30)
    assert lost_output.returncode == 0, lost_output.stderr
    lost_response = json.loads(lost_output.stdout)
    replay_context = lost_response.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert "host_execution_preparation_readback_required" in replay_context, lost_response
    readback = json.loads(replay_context.rsplit("Receipt: ", 1)[1])
    assert readback["claim_operation_id"] == claim_reference
    assert (readback["todo_id"], readback["turn_instance_id"]) == (TODO, "preparation-turn")
    readback_command = shlex.split(readback["context"]["recovery_command"])
    observed = subprocess.run(readback_command, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert observed.returncode == 0, observed.stderr
    projection = json.loads(observed.stdout)
    assert projection["ok"] and projection["sha256"] == readback["capture_sha256"]
    # C1 verifies the saved contract before offering its original reader.
    # Successful projection alone does not show any source bytes were read.
    reader = shlex.split(projection["recovery"]["command"])
    observed = subprocess.run(reader, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert observed.returncode == 0, observed.stderr
    ready_page = json.loads(observed.stdout)
    assert ready_page["ok"] and ready_page["sha256"] == readback["capture_sha256"]
    forged = {**ready_page, "content": "x" + ready_page["content"][1:]}
    for observation, should_repeat in ((projection, True), (forged, True), (ready_page, False)):
        shown = subprocess.run(hook, input=json.dumps({**hook_event, "tool_response": observation}),
            env=hook_env, cwd=tmp_path, capture_output=True, text=True, timeout=30)
        assert shown.returncode == 0, shown.stderr
        assert ("host_execution_preparation_readback_required" in shown.stdout) is should_repeat, shown.stdout
    assert log.read_bytes() == events

    source = Path(source)
    source.write_bytes(source.read_bytes() + b" ")
    corrupted = subprocess.run(read, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert corrupted.returncode != 0 and json.loads(corrupted.stdout)["ok"] is False
    assert log.read_bytes() == events


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("spend_committed", [False, True])
def test_restored_native_capture_does_not_claim_after_manual_writeback(
    tmp_path, monkeypatch, provider, spend_committed,
):
    from test_quota_settlement_cli import _initialize_git_checkout, _spend_run_count

    project = tmp_path / "project"
    project.mkdir()
    _initialize_git_checkout(project)
    (project / ".gitignore").write_text(".loopx/\n.codex/\n", encoding="utf-8")
    (project / "README.md").write_text("Synthetic native recovery fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore", "README.md"], cwd=project, check=True)
    subprocess.run([
        "git", "-c", "user.name=LoopX Test",
        "-c", "user.email=loopx-test@example.invalid",
        "commit", "--quiet", "-m", "fixture",
    ], cwd=project, check=True)
    delivery_worktree = tmp_path / "delivery-worktree"
    subprocess.run(
        ["git", "worktree", "add", "--quiet", "--detach", str(delivery_worktree)],
        cwd=project,
        check=True,
    )
    monkeypatch.setenv("CODEX_THREAD_ID", "manual-recovery-session")
    native = project / "native"
    native.mkdir(mode=0o700)
    capture = project / "original"
    registry, runtime = project / "registry.json", project / "runtime"
    turn, scenario = "manual-recovery-turn", "codex_app_heartbeat"
    host = ["--runtime-profile", scenario, "--context-delivery", "on", "--codex-context-dir", str(native)]
    scope = ["--goal-id", GOAL, "--agent-id", AGENT, "--todo-id", TODO, "--turn-instance-id", turn]

    def invoke(*args, cwd=project):
        code, payload = run_json_cli_result(*args, registry_path=registry, runtime_root=runtime, cwd=cwd)
        assert code == 0 and payload["ok"], payload
        return payload

    def hook(name):
        event = {"session_id": "manual-recovery-session", "cwd": str(project), "hook_event_name": name}
        if name == "SessionStart":
            event["source"] = "resume"
        result = subprocess.run([sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
            "--runtime-root", str(runtime), "quota", "context-hook", *host],
            input=json.dumps(event), cwd=project, check=True, capture_output=True, text=True, timeout=20)
        return json.loads(result.stdout)

    first = _run(project, monkeypatch, provider, [], "--runtime-profile", scenario,
        "--todo-id", TODO, "--turn-instance-id", turn, "--decision-output-dir", str(capture),
        "--defer-context-presentation", "--codex-context-dir", str(native), context_delivery="on")
    source = capture / "decision.json"
    original = source.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    assert first["execution_preparation"]["state"] == "claim_required"
    assert hook("PostToolUse") == {}  # Missing native journal uses ordinary preparation.
    claim = invoke(*first["execution_preparation"]["claim"]["cli_args"], "--thin")
    ready = invoke("quota", "should-run", *scope, "--runtime-profile", scenario, "--context-delivery", "off")
    assert ready["execution_preparation"]["state"] == "ready"
    proof = ready["execution_preparation"]["lease_proof"]
    identity = ready["heartbeat_receipt"]["settlement_identity"]
    assert identity == first["heartbeat_receipt"]["settlement_identity"]
    refresh = ["refresh-state", *scope,
        "--task-lease-idempotency-key", proof["idempotency_key"],
        "--task-lease-expected-version", str(proof["expected_version"]),
        "--classification", "fixture_manual_result",
        "--delivery-batch-scale", "single_surface", "--delivery-outcome", "outcome_progress",
        "--delivery-boundary", "in_flight_continuation", "--progress-result-class", "advanced",
        "--progress-work-item-id", TODO, "--progress-evidence-id", "fixture:manual-result",
        "--no-global-sync", "--suppress-external-sinks"]
    if spend_committed:
        refresh += ["--settle-turn", "--settlement-receipt"]
    assert invoke(*refresh, cwd=delivery_worktree)["settlement_identity"] == identity
    assert _spend_run_count(runtime, goal_id=GOAL) == int(spend_committed)
    inspect = ["task-lease", "inspect", "--goal-id", GOAL, "--todo-id", TODO]
    lease = invoke(*inspect)["lease"]
    released = invoke("task-lease", "release", "--goal-id", GOAL, "--todo-id", TODO, "--owner", AGENT,
        "--expected-version", str(lease["version"]), "--idempotency-key", lease["idempotency_key"])["lease"]
    receipt_args = ["todo", "receipt", "--goal-id", GOAL, "--operation-id", claim["claim_operation_id"]]
    manual_receipts = invoke(*receipt_args)["receipts"]
    events = runtime / "goals" / GOAL / "rollout-event-log.jsonl"
    old_events = events.read_bytes()
    assert hook("SessionStart") == {}
    assert _present_saved_guard(project, native, capture, scenario, turn) == digest
    refused = hook("PostToolUse")["hookSpecificOutput"]["additionalContext"]
    assert "already settling or settled" in refused
    assert hook("PostToolUse") == {}
    assert invoke(*inspect)["lease"] == released
    assert invoke(*receipt_args)["receipts"] == manual_receipts
    assert source.read_bytes() == original and events.read_bytes() == old_events
    spend = ["quota", "spend-slot", *scope, "--source", "heartbeat", "--execute"]
    assert invoke(*spend, cwd=delivery_worktree).get("idempotent_replay", False) is spend_committed
    assert invoke(*spend, cwd=delivery_worktree)["idempotent_replay"]
    assert _spend_run_count(runtime, goal_id=GOAL) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("replace_stage", ["guard", "record", "version"])
def test_prepared_hook_refuses_a_replaced_ready_capture_without_reclaim(
    tmp_path, monkeypatch, capsys, provider, replace_stage,
):
    import io
    from loopx.cli import main
    from loopx.control_plane.turn_driver import codex_context

    tmp_path = tmp_path.resolve()
    monkeypatch.chdir(tmp_path)
    session = "bound-preparation"
    monkeypatch.setenv("CODEX_THREAD_ID", session)
    native = tmp_path / "native"
    native.mkdir(mode=0o700)
    event = {"session_id": session, "cwd": str(tmp_path), "hook_event_name": "SessionStart", "source": "startup"}
    assert codex_context.codex_context_hook(str(native), event) == {}
    original = tmp_path / "preparation"
    _run(tmp_path, monkeypatch, provider, [], "--codex-app",
        "--turn-instance-id", "prepared-turn", "--todo-id", TODO, "--codex-context-dir", str(native),
        "--decision-output-dir", str(original), context_delivery="on")
    from loopx.control_plane.turn_driver import codex_execution_preparation
    record = codex_context.record_native_execution_preparation
    run = codex_execution_preparation._default_runner
    verified = {}

    def replace_capture():
        source, = native.glob("prepared-*/ready/decision.json")
        verified["path"], verified["bytes"] = source, source.read_bytes()
        source.write_bytes(verified["bytes"] + b" ")

    def observe_guard(command, **kwargs):
        prepared = "--prepared-capture-reference" in command
        if prepared and replace_stage == "version":
            command = [*command, "--complete-turn"]
        result = run(command, **kwargs)
        if prepared:
            verified["writer"] = json.loads(result.stdout)
            if replace_stage == "guard":
                replace_capture()
            elif replace_stage == "version":
                source, = native.glob("prepared-*/ready/decision.json")
                payload = json.loads(source.read_bytes())
                assert payload["schema_version"] == "loopx_turn_envelope_v1"
                payload["schema_version"] = "loopx_turn_envelope_v999"
                source.write_text(json.dumps(payload))
                verified["writer"]["capture_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
                result.stdout = json.dumps(verified["writer"])
        return result

    monkeypatch.setattr(codex_execution_preparation, "_default_runner", observe_guard)

    def replace_after_ready(*args, **kwargs):
        record(*args, **kwargs)
        if replace_stage == "record":
            replace_capture()

    monkeypatch.setattr(codex_context, "record_native_execution_preparation", replace_after_ready)
    command = ["--registry", str(tmp_path / "registry.json"), "--runtime-root", str(tmp_path / "runtime"),
               "--format", "json", "quota", "context-hook", "--codex-app", "--context-delivery", "on",
               "--codex-context-dir", str(native)]
    def hook(tool_response=None):
        data = json.dumps({**event, "hook_event_name": "PostToolUse",
                           "tool_response": tool_response}).encode()
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(data)))
        capsys.readouterr()
        assert main(command) == 0
        return json.loads(capsys.readouterr().out)

    result = hook()["hookSpecificOutput"]["additionalContext"]
    receipt = json.loads(result.rsplit("Receipt: ", 1)[1])
    assert receipt["claim_operation_id"].startswith("todo-claim:")
    context = receipt["context"]
    assert context["ok"] is False
    recovery = shlex.split(context["recovery_command"])
    if replace_stage == "version":
        assert "Unsupported" in context["reason"]
        assert context["recovery_command"] == context["recovery"]["command"]
        assert [recovery[index + 1] for index, word in enumerate(recovery) if word == "--select"] == ["/heartbeat_receipt"]
        log = tmp_path / "runtime" / "goals" / GOAL / "rollout-event-log.jsonl"
        after_claim = log.read_bytes()
        diagnostic = subprocess.run(recovery, cwd=tmp_path, check=True, text=True, capture_output=True)
        diagnostic_page = json.loads(diagnostic.stdout)
        assert json.loads(diagnostic_page["content"])["selections"] == [
            {"pointer": "/heartbeat_receipt", "value": context["heartbeat_receipt"]},
        ]
        # Even reading the rejected file explicitly does not observe ready work context.
        select_at = recovery.index("--select")
        raw_reader = recovery[:select_at] + recovery[select_at + 2:]
        raw = subprocess.run(raw_reader, cwd=tmp_path, check=True, text=True, capture_output=True)
        raw_page = json.loads(raw.stdout)
        assert raw_page["sha256"] == context["capture_sha256"]
        for page in (diagnostic_page, raw_page):
            response = hook(page)
            readback = json.loads(response["hookSpecificOutput"]["additionalContext"].rsplit("Receipt: ", 1)[1])
            assert readback["event"] == "host_execution_preparation_readback_required"
            assert readback["claim_operation_id"] == receipt["claim_operation_id"]
        implicit = subprocess.run([*raw_reader[:raw_reader.index("quota")], "quota", "context-read",
            "--codex-context-dir", str(native), "--goal-id", GOAL, "--agent-id", AGENT],
            cwd=tmp_path, text=True, capture_output=True)
        assert implicit.returncode == 1 and json.loads(implicit.stdout)["ok"] is False
        retry = shlex.split(readback["context"]["recovery_command"])
        assert "context-project" in retry and "--turn-instance-id" in retry
        refusal = subprocess.run(retry, cwd=tmp_path, text=True, capture_output=True)
        error = json.loads(refusal.stdout)
        assert refusal.returncode == 1 and error["heartbeat_receipt"] == context["heartbeat_receipt"]
        assert "content" not in error and log.read_bytes() == after_claim
        return
    assert "digest mismatch" in context["reason"]
    digest = hashlib.sha256(verified["bytes"]).hexdigest()
    writer = verified["writer"]
    assert writer["capture_sha256"] == digest and writer["execution_preparation"] == receipt["execution_preparation"]
    assert writer["selected_todo"] == {"todo_id": TODO}
    assert writer["heartbeat_receipt"] == {"turn_instance_id": "prepared-turn"}
    assert "interaction_contract" not in writer and "work_context" not in writer
    assert recovery[recovery.index("--capture-sha256") + 1] == digest
    log = tmp_path / "runtime" / "goals" / GOAL / "rollout-event-log.jsonl"
    after_claim = log.read_bytes()
    retry = json.loads(hook()["hookSpecificOutput"]["additionalContext"].rsplit("Receipt: ", 1)[1])
    assert retry["claim_operation_id"] == receipt["claim_operation_id"]
    assert retry["event"] == "host_execution_preparation_readback_required"
    assert "context" not in retry
    verified["path"].write_bytes(verified["bytes"])
    restored = json.loads(hook()["hookSpecificOutput"]["additionalContext"].rsplit("Receipt: ", 1)[1])
    assert restored["capture_sha256"] == digest
    assert restored["claim_operation_id"] == receipt["claim_operation_id"]
    read = subprocess.run([*recovery, "--select", "/execution_preparation/state"], cwd=tmp_path,
                          check=True, text=True, capture_output=True)
    assert json.loads(json.loads(read.stdout)["content"])["selections"][0]["value"] == "ready"
    observed = subprocess.run(recovery, cwd=tmp_path, check=True, text=True, capture_output=True)
    assert hook(json.loads(observed.stdout)) == {}
    assert log.read_bytes() == after_claim


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("case", [
    "same-execution", "released-execution", "after-ready-same-execution",
    "after-ready-released-execution", "legacy-same-execution", "legacy-released-execution",
    "reencoded-released-execution", "legacy-marked-released-execution",
    "ssh-same-execution", "delta-only-same-execution",
    "released-at-delivery", "ssh-released-at-delivery", "delta-released-at-delivery", "replaced-at-delivery",
    "released-during-staging", "ssh-released-during-staging", "delta-released-during-staging",
    "replaced-during-staging",
])
def test_native_hook_replays_the_original_claim_after_guard_timeout(
    tmp_path, monkeypatch, capsys, provider, case,
):
    import io
    from loopx.cli import main
    from loopx.control_plane.turn_driver import codex_context, codex_execution_preparation
    from tests.control_plane.codex_checkpoint_fixture import selected_context_values
    from test_quota_settlement_cli import _spend_run_count

    tmp_path = tmp_path.resolve()
    monkeypatch.chdir(tmp_path)
    session, turn = "claim-replay-session", "claim-replay-turn"
    monkeypatch.setenv("CODEX_THREAD_ID", session)
    native = tmp_path / "native"
    native.mkdir(mode=0o700)
    event = {"session_id": session, "cwd": str(tmp_path),
             "hook_event_name": "SessionStart", "source": "startup"}
    assert codex_context.codex_context_hook(str(native), event) == {}
    capture = tmp_path / "before-claim"
    legacy = case.startswith("legacy-")
    after_ready = case.startswith("after-ready-")
    retire_during_staging = case.endswith("-during-staging")
    retire_at_delivery = case.endswith("-at-delivery")
    refuse_delivery = retire_at_delivery or retire_during_staging
    released = not case.endswith("same-execution") and not refuse_delivery
    scenario = "codex_app_ssh_goal" if case.startswith("ssh-") else "codex_app_heartbeat"
    host = ("--runtime-profile", scenario)
    delivery = ("--context-delivery", "off", "--delta-delivery", "auto") if case.startswith("delta-") else (
        "--context-delivery", "on",
    )
    native_args = () if legacy else ("--defer-context-presentation", "--codex-context-dir", str(native))
    before = _run(tmp_path, monkeypatch, provider, [], *host,
        "--turn-instance-id", turn, "--todo-id", TODO, "--decision-output-dir", str(capture),
        *native_args, *delivery[2:], context_delivery="off" if legacy else delivery[1])
    assert before["execution_preparation"]["state"] == "claim_required"
    source = capture / "decision.json"
    original_bytes = source.read_bytes()
    digest = _present_saved_guard(tmp_path, native, capture, scenario, turn)

    run = codex_execution_preparation._default_runner
    record_ready = codex_context.record_native_execution_preparation
    claims, guards = [], []

    def interrupt_first_guard(command, **kwargs):
        if "--prepared-capture-reference" in command:
            guards.append(command[command.index("--turn-instance-id") + 1])
            if not after_ready and len(guards) == 1:
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        # Reach the deliberate post-ready fault rather than accidentally
        # injecting the separate pre-commit timeout under test contention.
        result = run(command, **{**kwargs, "timeout": 30} if after_ready else kwargs)
        if "claim" in command:
            claims.append(json.loads(result.stdout))
        return result

    def interrupt_after_ready(*args, **kwargs):
        record_ready(*args, **kwargs)
        if after_ready:
            raise RuntimeError("interrupted after ready receipt")

    monkeypatch.setattr(codex_execution_preparation, "_default_runner", interrupt_first_guard)
    monkeypatch.setattr(codex_context, "record_native_execution_preparation", interrupt_after_ready)
    registry, runtime = tmp_path / "registry.json", tmp_path / "runtime"
    command = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
               "quota", "context-hook", *host, *delivery,
               "--codex-context-dir", str(native)]

    def hook(name="PostToolUse", tool_response=None):
        data = json.dumps({**event, "hook_event_name": name, "tool_response": tool_response}).encode()
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(data)))
        capsys.readouterr()
        assert main(command) == 0
        return json.loads(capsys.readouterr().out)

    def read(*args):
        code, payload = run_json_cli_result(*args, registry_path=registry, runtime_root=runtime, cwd=tmp_path)
        assert code == 0 and payload["ok"], payload
        return payload

    if legacy:
        # Before the upgrade the Host issued the ordinary claim without a
        # persisted operation ID, then could lose the ready result.
        legacy_claim = read(*before["execution_preparation"]["claim"]["cli_args"])
        original_id = legacy_claim["claim_operation_id"]
    else:
        first_context = hook()["hookSpecificOutput"]["additionalContext"]
        first_request = json.loads(first_context.rsplit("Receipt: ", 1)[1])
        assert first_request["event"] == "host_execution_preparation_readback_required"
        original_id = claims[0]["claim_operation_id"]
        assert original_id == f"todo-claim:codex-capture:{digest}"
        assert first_request["claim_operation_id"] == original_id
    original = read("todo", "receipt", "--goal-id", GOAL, "--operation-id", original_id)
    lease = original["receipts"][0]["result"]["lease"]
    if released:
        read("task-lease", "release", "--goal-id", GOAL, "--todo-id", TODO, "--owner", AGENT,
            "--expected-version", str(lease["version"]), "--idempotency-key", lease["idempotency_key"])

    unverified_copy = case in ("reencoded-released-execution", "legacy-marked-released-execution")
    if legacy or unverified_copy:
        presented_capture = capture
        if unverified_copy:
            presented_capture = tmp_path / "unverified"
            presented_capture.mkdir(mode=0o700)
            copy = presented_capture / "decision.json"
            if legacy:
                # This fixture tests the former unsigned carrier. Editing the
                # current signed source would stop at its integrity check,
                # before the legacy marker's lack of a live guard is tested.
                historical = json.loads(original_bytes)
                assert historical.pop("schema_version") == "loopx_turn_envelope_v1"
                historical.pop("contract_binding")
                historical["native_claim_replay_bound"] = True
                copy.write_text(json.dumps(historical))
            else:
                copy.write_bytes(original_bytes + b" ")
            copy.chmod(0o600)
        presented_digest = _present_saved_guard(tmp_path, native, presented_capture, scenario, turn)
        assert (presented_digest != digest) == unverified_copy
    result = hook()
    resume = hook("SessionStart")
    current = read("task-lease", "inspect", "--goal-id", GOAL, "--todo-id", TODO)
    assert source.read_bytes() == original_bytes
    if legacy or unverified_copy:
        guidance = result["hookSpecificOutput"]["additionalContext"]
        assert "no verified live guard binding" in guidance and "fresh capture" in guidance
        assert "host_execution_prepared" not in guidance
        assert len(claims) == len(guards) == (0 if legacy else 1)
        assert current["active"] is (not released)
        assert current["lease"]["lease_epoch"] == lease["lease_epoch"]
        if not released:
            assert current["lease"] == lease
    else:
        assert len(claims) == 1 and guards == [turn]
        for observed in (result, resume):
            context = observed["hookSpecificOutput"]["additionalContext"]
            receipt = json.loads(context.rsplit("Receipt: ", 1)[1])
            assert receipt["event"] == "host_execution_preparation_readback_required"
            assert receipt["claim_operation_id"] == original_id
            assert (receipt["goal_id"], receipt["agent_id"], receipt["todo_id"], receipt["turn_instance_id"]) == (
                GOAL, AGENT, TODO, turn,
            )
            if after_ready:
                assert receipt["context"]["recovery_command"]
                assert "readiness_command" not in receipt
        for command_key in ("receipt_command", "lease_command"):
            proc = subprocess.run(
                shlex.split(receipt[command_key]), cwd=tmp_path, capture_output=True, text=True, check=True,
            )
            assert json.loads(proc.stdout)["ok"]
        if released:
            assert not current["active"] and current["lease"]["lease_epoch"] == lease["lease_epoch"]
        else:
            assert current["active"] and current["lease"] == lease
        if after_ready:
            resume_command = shlex.split(receipt["context"]["recovery_command"])
            assert resume_command[resume_command.index("--prepared-claim-operation-id") + 1] == original_id
            replay = subprocess.run(resume_command, cwd=tmp_path, capture_output=True, text=True, timeout=30)
            recovered = json.loads(replay.stdout)
            assert recovered["ok"] is (not released)
            assert replay.returncode == (1 if released else 0)
            if released:
                assert "current" in recovered["reason"] and "content" not in recovered
                history = subprocess.run(shlex.split(recovered["recovery"]["command"]),
                    cwd=tmp_path, capture_output=True, text=True, check=True, timeout=30)
                assert selected_context_values(json.loads(history.stdout)["content"])[
                    "/heartbeat_receipt"]["settlement_identity"] == before["heartbeat_receipt"]["settlement_identity"]
        else:
            source_marker = native / "claim-source.json"
            marker_before = source_marker.read_bytes()
            guard = shlex.split(receipt["readiness_command"])
            assert "should-run" in guard and "--begin-turn" not in guard
            assert guard[guard.index("--registry") + 1] == str(registry)
            assert guard[guard.index("--runtime-root") + 1] == str(runtime)
            assert guard[guard.index("--context-delivery") + 1] == delivery[1]
            prepared = subprocess.run(guard, cwd=tmp_path, capture_output=True, text=True, timeout=30)
            ready = json.loads(prepared.stdout)
            assert source_marker.read_bytes() == marker_before
            if released:
                assert ready.get("normal_delivery_allowed") is not True
                assert ready.get("execution_preparation", {}).get("state") != "ready"
                lease_check = read("task-lease", "inspect", "--goal-id", GOAL, "--todo-id", TODO)
                assert lease_check["lease"] == current["lease"] and not lease_check["active"]
                hook(tool_response=ready)
                assert len(claims) == len(guards) == 1
            else:
                assert prepared.returncode == 0 and ready["normal_delivery_allowed"] is True, ready
                assert ready["selected_todo"] == {"todo_id": TODO}
                assert ready["heartbeat_receipt"] == {"turn_instance_id": turn}
                assert ready["execution_preparation"]["state"] == "ready"
                assert ready["execution_preparation"]["lease_proof"] == {
                    "idempotency_key": lease["idempotency_key"], "expected_version": lease["version"],
                }
                projection = shlex.split(receipt["ready_context_command_template"].replace(
                    "<capture_sha256>", ready["capture_sha256"]))
                assert projection[projection.index("--prepared-claim-operation-id") + 1] == original_id
                release = ["task-lease", "release", "--goal-id", GOAL, "--todo-id", TODO, "--owner", AGENT,
                    "--expected-version", str(lease["version"]), "--idempotency-key", lease["idempotency_key"]]
                replacement_args = ["task-lease", "acquire", "--goal-id", GOAL, "--todo-id", TODO,
                    "--owner", AGENT, "--idempotency-key", "replacement-lease", "--ttl-seconds", "3600"]
                if retire_at_delivery:
                    retired = read(*release)
                    if case == "replaced-at-delivery":
                        replacement = read(*replacement_args, "--expected-version", str(retired["lease"]["version"]))
                        assert replacement["lease"]["lease_epoch"] != lease["lease_epoch"]
                if retire_during_staging:
                    # Stage through the real publisher and let a separate canonical writer
                    # finish before the publication transaction can validate its lease.
                    run_projection = """
import json
import subprocess
import sys
from loopx.control_plane.quota import turn_envelope
from loopx.entrypoint import main
projection, release, replace = json.loads(sys.argv[1])
prefix = projection[:projection.index("quota")]
prepare = turn_envelope.prepare_context_delivery
def stage_then_release(*args, **kwargs):
    staged = prepare(*args, **kwargs)
    result = subprocess.run([*prefix, *release], capture_output=True, check=True, timeout=20)
    retired = json.loads(result.stdout)["lease"]
    assert retired["status"] == "released"
    if replace:
        replaced = subprocess.run([*prefix, *replace, "--expected-version", str(retired["version"])],
                                   capture_output=True, check=True, timeout=20)
        assert json.loads(replaced.stdout)["lease"]["lease_epoch"] != retired["lease_epoch"]
    return staged
turn_envelope.prepare_context_delivery = stage_then_release
raise SystemExit(main(projection[projection.index("--registry"):]))
"""
                    replace = replacement_args if case == "replaced-during-staging" else []
                    projection = [sys.executable, "-c", run_projection, json.dumps([projection, release, replace])]
                projected = subprocess.run(projection, cwd=tmp_path, capture_output=True, text=True, timeout=30)
                view = json.loads(projected.stdout)
                assert view["ok"] is (not refuse_delivery) and view["recovery"]["command"], projected.stderr
                assert projected.returncode == (1 if refuse_delivery else 0)
                if refuse_delivery:
                    assert "current" in view["reason"] and "content" not in view
                    assert source_marker.read_bytes() == marker_before
                    assert view["heartbeat_receipt"]["settlement_identity"] == before["heartbeat_receipt"]["settlement_identity"]
                    if retire_during_staging:
                        assert "recovery" not in codex_context._state(native)
                reader = shlex.split(view["recovery"]["command"])
                if not refuse_delivery:
                    reader += ["--select", "/interaction_contract/cli_channel/settlement_plan/identity",
                               "--select", "/heartbeat_receipt/settlement_identity"]
                selected = subprocess.run(reader, cwd=tmp_path, capture_output=True, text=True, check=True, timeout=30)
                page = json.loads(selected.stdout)
                assert page["eof"]
                facts = selected_context_values(page["content"])
                identity = json.loads(original_bytes)["heartbeat_receipt"]["settlement_identity"]
                if refuse_delivery:
                    assert facts["/heartbeat_receipt"]["settlement_identity"] == identity
                else:
                    assert facts["/interaction_contract/cli_channel/settlement_plan/identity"] == identity
                    assert facts["/heartbeat_receipt/settlement_identity"] == identity
                    assert hook(tool_response=page) == {}
                assert len(claims) == len(guards) == 1
    readback = read("todo", "receipt", "--goal-id", GOAL, "--operation-id", original_id)
    assert readback["cursor"] == original["cursor"] and readback["receipts"] == original["receipts"]
    assert _spend_run_count(runtime, goal_id=GOAL) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("multiple_candidates", [False, True])
def test_projected_claim_prepares_the_same_receipt_bound_turn(
    tmp_path, monkeypatch, provider, multiple_candidates,
):
    scopes = [f"src/synthetic-area-{index:03d}/**" for index in range(220)]
    unbound = _run(
        tmp_path,
        monkeypatch,
        provider,
        [],
        "--runtime-profile",
        "codex_app_ssh_goal",
        "--begin-turn",
        todos=[_todo(), {**_todo(), "todo_id": "todo_other_candidate", "index": 2}]
        if multiple_candidates else None,
        write_scopes=scopes,
    )
    assert "heartbeat_receipt" in unbound, json.dumps(
        unbound, ensure_ascii=False, indent=2,
    )
    turn = unbound["heartbeat_receipt"]["turn_instance_id"]
    if multiple_candidates:
        assert unbound["interaction_contract"]["cli_channel"]["selection_required"] is True
    guard_code, before = run_json_cli_result(
        "quota", "should-run",
        "--runtime-profile", "codex_app_ssh_goal",
        "--context-delivery", "off",
        "--goal-id", GOAL,
        "--agent-id", AGENT,
        "--turn-instance-id", turn,
        "--todo-id", TODO,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
    )
    assert guard_code == 0, json.dumps(before, ensure_ascii=False, indent=2)
    assert before["execution_preparation"]["state"] == "claim_required"
    assert before["heartbeat_receipt"]["turn_instance_id"] == turn
    assert before["selected_todo"]["todo_id"] == TODO
    assert before["selected_todo"]["selection_binding"] == "heartbeat_receipt"
    assert before["normal_delivery_allowed"] is False
    contract = before["interaction_contract"]
    assert contract["agent_channel"]["delivery_allowed"] is False
    assert contract["cli_channel"]["next_cli_actions"] == [
        before["execution_preparation"]["claim"]["command_line"],
    ]
    assert "settlement_plan" not in contract["cli_channel"]
    assert not read_canonical_todos_if_promoted(
        runtime_root=tmp_path / "runtime", goal_id=GOAL, include_leases=True,
    )["leases"]

    def read_model_view(packet, stage, *, deferred):
        source = tmp_path / f"{stage}.json"
        raw = json.dumps(packet, ensure_ascii=False, separators=(",", ":")).encode()
        source.write_bytes(raw)
        registry, runtime = tmp_path / "absent-registry.json", tmp_path / "reader-runtime"
        args = ["quota", "context-project", "--capture-file", str(source),
            "--capture-sha256", hashlib.sha256(raw).hexdigest(), "--context-delivery", "on",
            "--delta-delivery", "off", "--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", turn]

        def read(*argv):
            code, result = run_json_cli_result(*argv, registry_path=registry, runtime_root=runtime)
            assert code == 0 and result["ok"], result
            return result

        navigation = read(*args)
        command = shlex.split(navigation["first_read_command"])
        selected = bytearray()
        while True:
            page = read(*command[command.index("quota"):])
            assert page["offset"] == len(selected)
            selected.extend(page["content"].encode())
            if page["eof"]:
                assert len(selected) == page["total_bytes"]
                break
            command = shlex.split(page["next_command"])
        values = {row["pointer"]: row["value"] for row in json.loads(selected)["selections"]}
        boundary = packet["goal_boundary"]
        assert boundary["write_scope"] == scopes and boundary["requires_parent_approval"] == ["publish"]
        assert values["/heartbeat_receipt"] == packet["heartbeat_receipt"]
        if deferred:
            marker = "/goal_boundary/write_scope"
            assert values["/goal_boundary"] == {
                **{key: value for key, value in boundary.items() if key != "write_scope"},
                "write_scope_pointer": marker,
            }
            detail = values["/context_details/unread_context_index/source"]
            restored = read("quota", "context-read", "--capture-file", detail["path"],
                "--capture-sha256", detail["sha256"], "--select", marker)
            assert restored["eof"]
            assert json.loads(restored["content"])["selections"] == [{"pointer": marker, "value": scopes}]
            if stage == "claim":
                args[args.index("--context-delivery") + 1] = "off"
                plain = read(*args)
                assert plain["mode"] == "full" and plain["recovery"]["sha256"] == hashlib.sha256(raw).hexdigest()
                assert plain["recovery"]["path"] == str(source)
        else:
            assert values["/goal_boundary"] == boundary
        assert source.read_bytes() == raw and not registry.exists()

    if multiple_candidates:
        read_model_view(unbound, "selection", deferred=True)
        read_model_view(before, "claim", deferred=True)

    claim_code, claim = run_json_cli_result(
        *shlex.split(contract["cli_channel"]["next_cli_actions"][0])[1:],
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
    )
    assert claim_code == 0, json.dumps(claim, ensure_ascii=False, indent=2)

    guard_code, after = run_json_cli_result(
        "quota", "should-run",
        "--runtime-profile", "codex_app_ssh_goal",
        "--context-delivery", "off",
        "--goal-id", GOAL,
        "--agent-id", AGENT,
        "--turn-instance-id", turn,
        "--todo-id", TODO,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
    )
    assert guard_code == 0, json.dumps(after, ensure_ascii=False, indent=2)
    assert after["heartbeat_receipt"]["turn_instance_id"] == turn
    assert after["selected_todo"]["todo_id"] == TODO
    assert after["execution_preparation"]["state"] == "ready"
    assert after["normal_delivery_allowed"] is True
    if multiple_candidates:
        read_model_view(after, "ready", deferred=False)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_foreign_active_lease_blocks_without_claim_action(tmp_path, monkeypatch, provider):
    packet = _run(tmp_path, monkeypatch, provider, [_lease(owner="agent-b")])
    preparation = packet["execution_preparation"]
    assert preparation["state"] == "blocked_by_conflict"
    assert preparation["reason_code"] == "active_lease_owned_by_other_agent"
    assert "claim" not in preparation
    assert packet["should_run"] is False
    assert packet["normal_delivery_allowed"] is False


def test_preparation_does_not_replace_explicit_action_selection(monkeypatch):
    from loopx.control_plane.quota import execution_preparation as module

    monkeypatch.setattr(
        module,
        "project_selected_todo_execution_preparation",
        lambda **_kwargs: pytest.fail("preparation must wait for the current binding"),
    )
    payload = {
        "should_run": True,
        "selected_todo": {"todo_id": TODO},
    }
    payload["action_portfolio"] = {
        "selection_policy": {"requires_explicit_turn_binding": True},
    }
    apply_selected_todo_execution_preparation(
        payload,
        runtime_root="/synthetic/runtime",
        goal_id=GOAL,
        agent_id=AGENT,
    )
    assert "execution_preparation" not in payload


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("lease_state", ["missing", "expired"])
@pytest.mark.parametrize("startup", ["before-guard", "after-guard"])
def test_codex_lifecycle_hook_uses_captured_authority_from_another_project(
    tmp_path, monkeypatch, provider, lease_state, startup,
):
    from loopx.control_plane.turn_driver.codex_context import (
        codex_context_hook,
    )

    tmp_path = tmp_path.resolve()
    context_root = tmp_path / "context"
    capture_root = tmp_path / "capture"
    context_root.mkdir(mode=0o700)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    foreign_workspace = tmp_path / "project-b"
    foreign_workspace.mkdir()
    event = {
        "session_id": "codex-hook-session",
        "cwd": str(foreign_workspace.resolve()),
        "hook_event_name": "SessionStart",
        "source": "startup",
    }
    if startup == "before-guard":
        assert codex_context_hook(str(context_root), event) == {}
    before = _run(
        tmp_path,
        monkeypatch,
        provider,
        [] if lease_state == "missing" else [_lease(expired=True)],
        "--runtime-profile", "codex_app_ssh_goal",
        "--begin-turn", "--defer-context-presentation", "--codex-context-dir", str(context_root),
        "--decision-output-dir", str(capture_root), context_delivery="on",
    )
    turn = before["heartbeat_receipt"]["turn_instance_id"]
    monkeypatch.setenv("CODEX_THREAD_ID", "codex-hook-session")
    monkeypatch.chdir(foreign_workspace)
    if startup == "after-guard":
        assert codex_context_hook(str(context_root), event) == {}
    _present_saved_guard(tmp_path, context_root, capture_root, "codex_app_ssh_goal", turn)

    command = [
        sys.executable, "-m", "loopx.entrypoint",
        # The lifecycle process may be rooted in another project.  These
        # deliberately wrong invocation routes must not replace the route
        # retained with the captured preparation action.
        "--registry", str(foreign_workspace / ".loopx" / "registry.json"),
        "--runtime-root", str(foreign_workspace / ".loopx" / "runtime"),
        "--format", "json",
        "quota", "context-hook",
        "--context-delivery", "on",
        "--runtime-profile", "codex_app_ssh_goal",
        "--codex-context-dir", str(context_root),
    ]
    post_tool = {**event, "hook_event_name": "PostToolUse", "tool_response": "{}"}
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])

    # A subagent may share its parent's native session and cwd, but it must not
    # execute or receive the parent's claim action.
    child = subprocess.run(
        command,
        input=json.dumps({**post_tool, "agent_id": "child-agent"}),
        cwd=foreign_workspace,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout) == {}
    child_guard_code, child_guard = run_json_cli_result(
        "quota", "should-run",
        "--runtime-profile", "codex_app_ssh_goal",
        "--context-delivery", "off",
        "--goal-id", GOAL,
        "--agent-id", AGENT,
        "--turn-instance-id", turn,
        "--todo-id", TODO,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
    )
    assert child_guard_code == 0
    assert child_guard["execution_preparation"]["state"] == "claim_required"

    prepared = subprocess.run(
        command,
        input=json.dumps(post_tool),
        cwd=foreign_workspace,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert prepared.returncode == 0, prepared.stderr
    hook = json.loads(prepared.stdout)
    additional = hook["hookSpecificOutput"]["additionalContext"]
    assert "automatically restored" in additional
    assert '"state":"ready"' in additional
    assert turn in additional

    guard_code, after = run_json_cli_result(
        "quota", "should-run",
        "--runtime-profile", "codex_app_ssh_goal",
        "--context-delivery", "off",
        "--goal-id", GOAL,
        "--agent-id", AGENT,
        "--turn-instance-id", turn,
        "--todo-id", TODO,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
    )
    assert guard_code == 0
    assert after["execution_preparation"]["state"] == "ready"

    repeated = subprocess.run(
        command,
        input=json.dumps(post_tool),
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert repeated.returncode == 0 and json.loads(repeated.stdout) == {}


def _writeback_guard_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    provider: str,
    turn_instance_id: str,
    lease: dict[str, object],
    legacy_no_epoch_receipt: bool = False,
):
    from loopx.control_plane.quota.settlement import read_heartbeat_settlement

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    state_path = project / "ACTIVE_GOAL_STATE.md"
    state_path.write_text("# Fixture\n", encoding="utf-8")
    runtime = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    fixture_todo = {
        **_todo(),
        "continuation_policy": "same_agent_non_delivery",
    }
    write_fixture_registry(
        project=project,
        runtime_root=runtime,
        registry_path=registry,
        goal_id=GOAL,
        domain="fixture",
        adapter_kind="read_only_project_map_v0",
        state_file="ACTIVE_GOAL_STATE.md",
        registered_agents=(AGENT, "agent-b"),
        extra_goal_fields={"coordination": {
            "registered_agents": [AGENT, "agent-b"],
            "handoff_mode": "hard_lease",
        }},
    )
    initialize_canonical_authority(
        runtime,
        GOAL,
        build_todo_runtime_shadow_projection(
            goal_id=GOAL,
            handoff_mode="hard_lease",
            todos=[fixture_todo],
            leases=[lease],
        ),
        state_path=state_path,
        provider=provider,
    )
    proof = {
        "idempotency_key": lease["idempotency_key"],
        "expected_version": lease["version"],
    }
    if legacy_no_epoch_receipt:
        from loopx.control_plane.quota.effect_program import SettlementIdentity
        from loopx.control_plane.quota.heartbeat_receipt import ensure_turn_heartbeat_settlement_receipt

        ensure_turn_heartbeat_settlement_receipt(
            runtime,
            SettlementIdentity(
                goal_id=GOAL, agent_id=AGENT, todo_id=TODO,
                turn_instance_id=turn_instance_id,
            ),
            semantic_replan_guard_scoped=True,
            semantic_replan_obligation_id=None,
            task_lease_proof=proof,
            task_lease_epoch=None,
            prepared_lease_required=True,
        )
    else:
        code, packet = run_json_cli_result(
            "quota", "should-run", "--codex-app", "--context-delivery", "off",
            "--goal-id", GOAL, "--agent-id", AGENT, "--todo-id", TODO,
            "--turn-instance-id", turn_instance_id,
            registry_path=registry, runtime_root=runtime, cwd=project,
        )
        assert code == 0 and packet["execution_preparation"]["state"] == "ready", json.dumps(
            packet, ensure_ascii=False, indent=2,
        )
    settlement = read_heartbeat_settlement(
        runtime, goal_id=GOAL, agent_id=AGENT, todo_id=TODO,
        turn_instance_id=turn_instance_id, registry_path=registry,
    )
    assert settlement is not None
    assert settlement.identity.value is not None
    assert settlement.identity.value.goal_id == GOAL
    assert settlement.identity.value.agent_id == AGENT
    assert settlement.identity.value.todo_id == TODO
    assert settlement.identity.value.turn_instance_id == turn_instance_id
    if not legacy_no_epoch_receipt:
        assert settlement.prepared_lease == {
            "prepared_lease_key_sha256": hashlib.sha256(
                str(lease["idempotency_key"]).encode("utf-8")
            ).hexdigest(),
            "prepared_lease_version": lease["version"],
            "prepared_lease_epoch": lease["lease_epoch"],
        }
    return {
        "project": project, "runtime": runtime, "registry": registry,
        "state": state_path, "turn": turn_instance_id, "proof": proof,
        "settlement": settlement,
    }


def _run_real_semantic_writeback(
    context: dict[str, object], *, proof: dict[str, object] | None,
    allow_same_generation_lease_renewal: bool = False,
    validation_recovery_turn_instance_id: str | None = None,
):
    from loopx.state_refresh import refresh_state_run

    return refresh_state_run(
        registry_path=context["registry"],
        runtime_root_override=str(context["runtime"]),
        goal_id=str(context.get("goal_id") or GOAL),
        project=context["project"], state_file=None,
        classification="receipt_bound_semantic_writeback",
        recommended_action="Keep the synthetic Todo open while recording this Turn.",
        delivery_batch_scale="single_surface", delivery_outcome="outcome_gap",
        delivery_boundary="semantic_closeout",
        todo_id=str(context.get("todo") or TODO),
        turn_instance_id=str(context["turn"]),
        agent_id=str(context.get("agent_id") or AGENT),
        progress_observation={
            "schema_version": "typed_progress_observation_v0",
            "result_class": "blocked",
            "work_item_id": str(context.get("todo") or TODO),
            "blocker_id": "synthetic-writeback-fence",
            "evidence_ids": ["evidence:synthetic-writeback-fence"],
        },
        vision_unchanged_reason="The synthetic Todo remains open after this scoped writeback.",
        task_lease_proof=proof,
        goal_ref=context.get("goal_ref"),
        allow_same_generation_lease_renewal=allow_same_generation_lease_renewal,
        validation_recovery_turn_instance_id=validation_recovery_turn_instance_id,
        dry_run=False, sync_global=False,
    )


def _writeback_side_effect_snapshot(context: dict[str, object]) -> dict[str, object]:
    from loopx.file_lock import lock_holder_path
    from loopx.control_plane.quota.settlement import read_heartbeat_settlement
    from loopx.rollout_event_log import rollout_event_log_path

    goal_id = str(context.get("goal_id") or GOAL)
    agent_id = str(context.get("agent_id") or AGENT)
    todo_id = str(context.get("todo") or TODO)
    settlement = read_heartbeat_settlement(
        context["runtime"], goal_id=goal_id, agent_id=agent_id,
        todo_id=todo_id, turn_instance_id=str(context["turn"]),
        registry_path=context["registry"], goal_ref=context.get("goal_ref"),
    )
    assert settlement is not None
    runs = context["runtime"] / "goals" / goal_id / "runs"
    index_holder = lock_holder_path(runs / "index.jsonl")
    receipt_log = rollout_event_log_path(context["runtime"], goal_id)
    run_files: dict[str, bytes] = {}
    if runs.exists():
        for path in runs.rglob("*"):
            if not path.is_file():
                continue
            if path == index_holder:
                released_holder = json.loads(path.read_text(encoding="utf-8"))
                assert released_holder["schema_version"] == "file_lock_holder_v0"
                assert isinstance(released_holder.get("released_at"), str)
                assert released_holder["released_at"]
                continue
            run_files[str(path.relative_to(runs))] = path.read_bytes()
    return {
        "authority": read_canonical_todos_if_promoted(
            runtime_root=context["runtime"], goal_id=goal_id, include_leases=True,
        ),
        "original_prepared": settlement.original_prepared_receipt,
        "original_event_id": settlement.original_prepared_event_id,
        "original_sha256": settlement.original_prepared_receipt_sha256,
        "goal_file": context["state"].read_bytes(),
        "receipt_log": receipt_log.read_bytes(),
        "run_files": run_files,
    }


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_writeback_requires_current_same_generation_cas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    from loopx.control_plane.work_items.task_lease import renew_task_lease

    for case, expected_version in (("current", 3), ("stale", 1)):
        original = {**_lease(), "idempotency_key": "turn-p1", "version": 1, "lease_epoch": 1}
        context = _writeback_guard_fixture(
            tmp_path / case, monkeypatch, provider=provider,
            turn_instance_id=f"turn-p1-{case}", lease=original,
        )
        renewed = None
        for version in (1, 2):
            renewed = renew_task_lease(
                registry_path=context["registry"], runtime_root=context["runtime"],
                goal_id=GOAL, todo_id=TODO, owner=AGENT,
                idempotency_key="turn-p1", expected_version=version,
            )
            assert renewed["lease"]["version"] == version + 1
            assert renewed["lease"]["lease_epoch"] == 1
        proof = {"idempotency_key": "turn-p1", "expected_version": expected_version}
        if case == "current":
            result = _run_real_semantic_writeback(
                context, proof=proof, allow_same_generation_lease_renewal=True,
            )
            assert result["ok"] is True
            run = json.loads(Path(result["json_path"]).read_text(encoding="utf-8"))
            assert run["settlement_identity"]["turn_instance_id"] == context["turn"]
            assert run["refresh_recovery"]["decision"] == "append"
        else:
            before = _writeback_side_effect_snapshot(context)
            with pytest.raises(RefreshTaskLeaseUnavailable):
                _run_real_semantic_writeback(
                    context, proof=proof, allow_same_generation_lease_renewal=True,
                )
            assert _writeback_side_effect_snapshot(context) == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_validation_recovery_cannot_execute_replaced_epoch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, provider: str,
) -> None:
    from tests.control_plane.test_execution_loss_closeout_cli import (
        OWNER, TURN, _fixture as execution_loss_fixture,
    )

    context = execution_loss_fixture(
        tmp_path, monkeypatch, capsys, provider,
        source_profile=True, replace=True, replacement_owner=OWNER,
    )
    original = context["original_lease"]
    current = context["current_lease"]
    assert current["lease_epoch"] > original["lease_epoch"]
    assert current["idempotency_key"] != original["idempotency_key"]
    writeback = {
        **context, "turn": TURN, "agent_id": OWNER,
        "proof": {"idempotency_key": original["idempotency_key"],
                  "expected_version": original["version"]},
    }
    before = _writeback_side_effect_snapshot(writeback)
    with pytest.raises(RefreshTaskLeaseUnavailable):
        _run_real_semantic_writeback(
            writeback, proof=writeback["proof"],
            validation_recovery_turn_instance_id=TURN,
        )
    assert _writeback_side_effect_snapshot(writeback) == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_legacy_prepared_receipt_is_exact_version_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    from loopx.control_plane.work_items.task_lease import renew_task_lease

    legacy = {**_lease(), "idempotency_key": "legacy-turn", "version": 1}
    legacy.pop("lease_epoch")
    exact = _writeback_guard_fixture(
        tmp_path / "exact", monkeypatch, provider=provider,
        turn_instance_id="turn-legacy-exact", lease=legacy,
        legacy_no_epoch_receipt=True,
    )
    expected_legacy_receipt = {
        "prepared_lease_key_sha256": hashlib.sha256(b"legacy-turn").hexdigest(),
        "prepared_lease_version": 1,
    }
    assert exact["settlement"].prepared_lease == expected_legacy_receipt
    assert "prepared_lease_epoch" not in exact["settlement"].prepared_lease
    result = _run_real_semantic_writeback(exact, proof=exact["proof"])
    assert result["ok"] is True

    advanced = _writeback_guard_fixture(
        tmp_path / "advanced", monkeypatch, provider=provider,
        turn_instance_id="turn-legacy-advanced", lease=legacy,
        legacy_no_epoch_receipt=True,
    )
    renewed = renew_task_lease(
        registry_path=advanced["registry"], runtime_root=advanced["runtime"],
        goal_id=GOAL, todo_id=TODO, owner=AGENT,
        idempotency_key="legacy-turn", expected_version=1,
    )["lease"]
    assert renewed["version"] == 2
    before = _writeback_side_effect_snapshot(advanced)
    with pytest.raises(RefreshTaskLeaseUnavailable):
        _run_real_semantic_writeback(
            advanced,
            proof={"idempotency_key": renewed["idempotency_key"],
                   "expected_version": renewed["version"]},
            allow_same_generation_lease_renewal=True,
        )
    assert _writeback_side_effect_snapshot(advanced) == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_exact_source_supersede_admits_only_its_original_turn_writeback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, provider: str,
) -> None:
    from tests.control_plane.test_execution_loss_closeout_cli import (
        OWNER, TURN, _binding, _fixture as execution_loss_fixture, _proof, _readback,
    )

    context = execution_loss_fixture(
        tmp_path, monkeypatch, capsys, provider,
        source_profile=True, replace=False,
    )
    expected_todo = "todo_" + hashlib.sha256(b"execution-loss-task").hexdigest()[:24]
    expected_effect = (
        f"execution-loss-closeout:original-worker:{expected_todo}:original-prepared-turn"
    )
    admission_identity = _readback(context).identity.value.as_dict()
    assert admission_identity["goal_id"] == "execution-loss-closeout"
    assert admission_identity["agent_id"] == "original-worker"
    assert admission_identity["todo_id"] == expected_todo
    assert admission_identity["turn_instance_id"] == "original-prepared-turn"
    assert admission_identity["effect_id"] == expected_effect
    lease = context["original_lease"]
    code, retired = context["cli"](
        "todo", "supersede", "--reason", "Retire only the original synthetic Turn.",
        *_binding(context), *_proof(lease),
    )
    assert code == 0 and retired["superseded"] is True, retired
    assert retired["settlement_identity"]["turn_instance_id"] == TURN
    writeback = {
        **context, "turn": TURN, "agent_id": OWNER,
        "proof": {"idempotency_key": lease["idempotency_key"],
                  "expected_version": lease["version"]},
    }
    assert retired["settlement_identity"] == admission_identity
    before_wrong_turn = _writeback_side_effect_snapshot(writeback)
    with pytest.raises(RuntimeError, match="exact settlement readback unexpectedly returned not-found"):
        _run_real_semantic_writeback(
            {**writeback, "turn": "foreign-prepared-turn"}, proof=writeback["proof"],
        )
    assert _writeback_side_effect_snapshot(writeback) == before_wrong_turn
    result = _run_real_semantic_writeback(writeback, proof=writeback["proof"])
    assert result["ok"] is True
    run = json.loads(Path(result["json_path"]).read_text(encoding="utf-8"))
    assert run["settlement_identity"] == admission_identity
    assert run["todo_id"] == expected_todo
    index = context["runtime"] / "goals" / "execution-loss-closeout" / "runs" / "index.jsonl"
    [index_row] = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()]
    assert index_row["settlement_identity"] == admission_identity


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_writeback_holds_shared_marker_until_run_is_durable_before_lease_renewal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    """Composition proof: the actual writer retains M through its Run append.

    The zero-wait process probe observes the shared marker, not a native waiter.
    Canonical-writer coverage separately proves public lease acquire uses M.
    """
    from threading import Event, Thread
    from loopx.control_plane.coordination.shadow_management import shadow_maintenance_lock_target
    from loopx.control_plane.quota import execution_preparation
    from loopx.control_plane.quota.settlement import read_heartbeat_settlement
    from loopx.control_plane.work_items.task_lease import renew_task_lease
    from loopx.rollout_event_log import rollout_event_log_path
    from loopx import state_refresh

    original = {**_lease(), "idempotency_key": "same-generation-writeback", "version": 1,
                "lease_epoch": 1}
    context = _writeback_guard_fixture(
        tmp_path, monkeypatch, provider=provider,
        turn_instance_id="turn-same-generation-writeback", lease=original,
    )
    original_settlement = context["settlement"]
    original_receipt = original_settlement.original_prepared_receipt
    original_event_id = original_settlement.original_prepared_event_id
    original_receipt_sha256 = original_settlement.original_prepared_receipt_sha256
    receipt_log = rollout_event_log_path(context["runtime"], GOAL)
    original_receipt_bytes = receipt_log.read_bytes()
    current = None
    for version in (1, 2):
        current = renew_task_lease(
            registry_path=context["registry"], runtime_root=context["runtime"],
            goal_id=GOAL, todo_id=TODO, owner=AGENT,
            idempotency_key="same-generation-writeback", expected_version=version,
        )["lease"]
    assert current is not None
    assert current["version"] == 3 and current["lease_epoch"] == 1

    boundary_names = ("qualification", "json", "markdown", "index")
    reached = {name: Event() for name in boundary_names}
    release = {name: Event() for name in boundary_names}
    writer_result: list[dict[str, object]] = []
    writer_error: list[BaseException] = []
    qualification_scopes: list[dict[str, object]] = []
    original_write_text = Path.write_text
    original_append_index = state_refresh.append_run_index_record
    original_qualification = execution_preparation.read_turn_writeback_qualification

    def wait_at_boundary(name: str) -> None:
        reached[name].set()
        if not release[name].wait(timeout=20):
            raise AssertionError(f"writeback {name} barrier was not released")

    def pause_at_run_write(path: Path, *args, **kwargs):
        if path.parent.name == "runs" and path.suffix in {".json", ".md"}:
            wait_at_boundary("json" if path.suffix == ".json" else "markdown")
        return original_write_text(path, *args, **kwargs)

    def pause_at_index_append(*args, **kwargs):
        wait_at_boundary("index")
        return original_append_index(*args, **kwargs)

    def observe_fresh_qualification(*args, **kwargs):
        scope = original_qualification(*args, **kwargs)
        qualification_scopes.append(scope)
        wait_at_boundary("qualification")
        return scope

    monkeypatch.setattr(Path, "write_text", pause_at_run_write)
    monkeypatch.setattr(state_refresh, "append_run_index_record", pause_at_index_append)
    monkeypatch.setattr(
        execution_preparation, "read_turn_writeback_qualification", observe_fresh_qualification,
    )
    writeback = {
        **context,
        "proof": {"idempotency_key": current["idempotency_key"],
                  "expected_version": current["version"]},
    }

    def write_run() -> None:
        try:
            writer_result.append(_run_real_semantic_writeback(writeback, proof=writeback["proof"]))
        except BaseException as error:  # preserve the writer failure for the parent test thread
            writer_error.append(error)

    writer = Thread(target=write_run, name="synthetic-semantic-writeback")

    repo = Path(__file__).resolve().parents[2]
    maintenance_target = shadow_maintenance_lock_target(context["runtime"], GOAL).resolve()
    probe_source = """
from pathlib import Path
import sys
from loopx.control_plane.coordination.shadow_management import shadow_maintenance_lock_target
from loopx.file_lock import LockAcquireTimeoutError, exclusive_mutation_file_lock
target = shadow_maintenance_lock_target(Path(sys.argv[1]), sys.argv[2])
assert target.resolve() == Path(sys.argv[3]).resolve()
try:
    with exclusive_mutation_file_lock(target, timeout_seconds=0, operation="writeback-test-probe"):
        pass
except LockAcquireTimeoutError:
    raise SystemExit(23)
"""

    def probe_marker() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", probe_source, str(context["runtime"]), GOAL,
             str(maintenance_target)],
            cwd=repo, env={**os.environ, "PYTHONPATH": str(repo)},
            capture_output=True, text=True, timeout=15, check=False,
        )

    writer_started = False
    try:
        writer.start()
        writer_started = True
        for name in boundary_names:
            assert reached[name].wait(timeout=20), f"writer never reached the {name} durability boundary"
            held = probe_marker()
            assert held.returncode == 23, f"{name}: {held.stdout}{held.stderr}"
            release[name].set()
    finally:
        for event in release.values():
            event.set()
        if writer_started:
            writer.join(timeout=20)
    assert not writer.is_alive(), "writeback writer did not leave the shared marker"
    assert writer_error == []
    assert len(writer_result) == 1 and writer_result[0]["ok"] is True
    assert qualification_scopes == [{
        "step_kind": "durable_writeback",
        "effect_ref": (
            "goal-execution-preparation:agent-a:todo_execution_preparation:"
            "turn-same-generation-writeback#durable_writeback"
        ),
        "delivery_boundary": "semantic_closeout",
        "original_prepared": original_receipt,
        "selected_obligation_id": None,
    }]
    released = probe_marker()
    assert released.returncode == 0, released.stdout + released.stderr

    # The supported public writer runs only after the semantic writer returned.
    code, renewed = run_json_cli_result(
        "task-lease", "renew", "--goal-id", GOAL, "--todo-id", TODO,
        "--owner", AGENT, "--idempotency-key", current["idempotency_key"],
        "--expected-version", str(current["version"]), "--ttl-seconds", "600",
        registry_path=context["registry"], runtime_root=context["runtime"],
        cwd=context["project"],
    )
    assert code == 0, renewed
    assert renewed["lease"]["version"] == 4
    assert renewed["lease"]["lease_epoch"] == current["lease_epoch"]
    assert renewed["lease"]["idempotency_key"] == current["idempotency_key"]
    assert receipt_log.read_bytes() == original_receipt_bytes
    final = read_heartbeat_settlement(
        context["runtime"], goal_id=GOAL, agent_id=AGENT, todo_id=TODO,
        turn_instance_id=context["turn"], registry_path=context["registry"],
    )
    assert final is not None
    assert final.original_prepared_receipt == original_receipt
    assert final.original_prepared_event_id == original_event_id
    assert final.original_prepared_receipt_sha256 == original_receipt_sha256
    runs = list((context["runtime"] / "goals" / GOAL / "runs").glob("*.json"))
    assert len(runs) == 1
    run = json.loads(runs[0].read_text(encoding="utf-8"))
    assert final.writeback_run == run
    assert run["settlement_identity"]["turn_instance_id"] == context["turn"]
    markdown = runs[0].with_suffix(".md")
    assert markdown.exists() and context["turn"] in markdown.read_text(encoding="utf-8")
    index = context["runtime"] / "goals" / GOAL / "runs" / "index.jsonl"
    index_rows = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()]
    assert len(index_rows) == 1
    assert index_rows[0]["settlement_identity"] == run["settlement_identity"]
    assert Path(index_rows[0]["json_path"]) == runs[0]
    assert Path(index_rows[0]["markdown_path"]) == markdown


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_todo_semantic_writeback_is_operation_scoped_without_business_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    from tests.control_plane.test_quota_settlement_cli import (
        AGENT_ID, GOAL_ID, SELECTED_REPLAN_TODO_ID,
        _bind_selected_replan_guard, _configure_selected_todo_replan_fixture,
        _initialize_fixture_authority, _projected_cli_args, _run_cli, _write_fixture,
    )
    from loopx.control_plane.quota.settlement import read_heartbeat_settlement

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selected_todo_replan_fixture(project, registry, stride_strategy="off")
    _initialize_fixture_authority(
        project, runtime, provider=provider, handoff_mode="hard_lease", leases=[],
    )
    turn = "turn-operation-scoped-semantic-writeback"
    admitted = _bind_selected_replan_guard(registry, runtime, project, turn)
    assert admitted["normal_delivery_allowed"] is False
    assert admitted["heartbeat_receipt"]["settlement_identity"]["todo_id"] == SELECTED_REPLAN_TODO_ID
    assert "prepared_lease" not in admitted["heartbeat_receipt"]

    refresh = next(
        command for command in admitted["interaction_contract"]["cli_channel"]["next_cli_actions"]
        if "refresh-state" in command
    )
    vision = tmp_path / "semantic-writeback-vision.json"
    vision.write_text(json.dumps({
        "schema_version": "goal_vision_replan_contract_v0",
        "state": "vision_patch_proposed",
        "vision_patch": {
            "vision_summary": "Validate the selected bounded slice.",
            "acceptance_summary": "The slice retains independent evidence.",
            "advancement_policy": "as_needed",
        },
        "path_delta": {
            "schema_version": "goal_path_delta_v0", "outcome": "replan",
            "prior_assumption": "The long chain needed one review.",
            "observed_reality": "The selected slice now has a verified route.",
            "retained": ["Existing acceptance boundaries"],
            "changed": ["Proceed through the selected validation slice"],
            "evidence_refs": ["evidence:semantic-writeback"],
        },
    }) + "\n", encoding="utf-8")
    refresh = refresh.replace(
        "<path-to-evidence-linked-goal-vision-replan-contract-v0.json>", str(vision),
    )
    for key, value in {
        "<advanced|blocked|exploration_exhausted|no_followup>": "advanced",
        "<surface-id>": "semantic-writeback-surface",
        "<hypothesis-id>": "semantic-writeback-hypothesis",
        "<probe-kind>": "semantic-writeback-probe",
        "<evidence-id>": "evidence:semantic-writeback",
    }.items():
        refresh = refresh.replace(key, value)
    code, result = _run_cli(
        registry, runtime, *_projected_cli_args(refresh, turn_instance_id=turn), cwd=project,
    )
    assert code == 0 and result["settlement_result"]["ok"] is True, result
    run = json.loads(Path(result["json_path"]).read_text(encoding="utf-8"))
    semantic = run["autonomous_replan_ack"]["semantic_delta"]
    assert semantic["accepted"] is True
    assert semantic["observation_fingerprint"] == run["progress_observation"]["fingerprint"]
    readback = read_heartbeat_settlement(
        runtime, goal_id=GOAL_ID, agent_id=AGENT_ID,
        todo_id=SELECTED_REPLAN_TODO_ID, turn_instance_id=turn,
        registry_path=registry,
    )
    assert readback is not None
    assert readback.prepared_lease is None
    assert readback.completion_event is None
    assert readback.writeback_run == run
    with pytest.raises(RefreshTaskLeaseUnavailable):
        read_heartbeat_settlement(
            runtime, goal_id=GOAL_ID, agent_id=AGENT_ID,
            todo_id=SELECTED_REPLAN_TODO_ID, turn_instance_id=turn,
            registry_path=registry,
            resume_lease_candidate={"requested_proof": None},
        )
