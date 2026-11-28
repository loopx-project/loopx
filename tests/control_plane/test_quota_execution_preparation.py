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
    guard_first_writeback_execution,
    prepared_lease_receipt_fields,
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
    guard_cwd: Path | None = None,
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
        registry_path=registry, runtime_root=runtime, cwd=guard_cwd,
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
    original = {**_todo(), "required_capabilities": ["filesystem_write"],
        "text": "Deliver the complete synthetic request: 保留原文" + "。不可截断的验收条件" * 100,
        "note": "Preserve the original stop and once-only settlement."}
    capture = tmp_path / "before-claim"
    before = _run(tmp_path, monkeypatch, provider, [], "--runtime-profile", scenario,
        "--available-capability", "filesystem_write", "--available-capability", "shell",
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
    runtime_capabilities = "/agent_identity/runtime_available_capabilities"
    pointers = ["/execution_preparation", "/selected_todo", "/heartbeat_receipt", "/interaction_contract/cli_channel",
        runtime_capabilities,
        "/interaction_contract/agent_channel/work_context", "/interaction_contract/agent_channel/required_reads"]
    for pointer in pointers:
        read.extend(("--select", pointer))
    completed = subprocess.run(read, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    page = json.loads(completed.stdout)
    assert page["eof"] is True and page["offset"] == 0
    selected = {item["pointer"]: item["value"] for item in json.loads(page["content"])["selections"]}
    assert selected["/execution_preparation"]["state"] == "ready"
    assert selected[runtime_capabilities] == before["agent_identity"]["runtime_available_capabilities"]
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
    for capability in ("filesystem_write", "shell"):
        assert f"--available-capability {capability}" in writeback["semantic_closeout_command_template"]
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
    proof = ready["execution_preparation"]["lease_proof"]
    refresh = ["refresh-state", *scope, "--classification", "fixture_manual_result",
        "--delivery-batch-scale", "single_surface", "--delivery-outcome", "outcome_progress",
        "--delivery-boundary", "in_flight_continuation", "--progress-result-class", "advanced",
        "--progress-work-item-id", TODO, "--progress-evidence-id", "fixture:manual-result",
        "--no-global-sync", "--suppress-external-sinks",
        "--task-lease-idempotency-key", proof["idempotency_key"],
        "--task-lease-expected-version", str(proof["expected_version"])]
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
            assert receipt["readiness_workdir"] == str(Path(__file__).resolve().parents[2])
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


@pytest.mark.parametrize(("provider", "workspace_state"), [
    ("file", "admitted"), ("sqlite", "admitted"), ("sqlite", "replaced"), ("file", "legacy-marker"),
])
def test_native_hook_prepares_repository_work_in_the_admitted_worktree(tmp_path, monkeypatch, provider, workspace_state):
    from loopx.control_plane.turn_driver.codex_context import codex_context_hook
    from test_quota_settlement_cli import _initialize_git_checkout, _spend_run_count

    project = tmp_path.resolve() / "project"
    project.mkdir()
    _initialize_git_checkout(project)
    subprocess.run(["git", "-c", "user.name=LoopX Test", "-c", "user.email=test@example.invalid",
                    "commit", "--allow-empty", "--quiet", "-m", "fixture"], cwd=project, check=True)
    worktree = tmp_path.resolve() / "delivery worktree"
    subprocess.run(["git", "worktree", "add", "--quiet", "--detach", str(worktree)], cwd=project, check=True)
    native = project / "native"
    native.mkdir(mode=0o700)
    capture = project / "capture"
    scenario, turn = "codex_app_heartbeat", "repository-preparation"
    session = "repository-preparation-session"
    monkeypatch.setenv("CODEX_THREAD_ID", session)
    event = {"session_id": session, "cwd": str(project)}
    assert codex_context_hook(str(native), {**event, "hook_event_name": "SessionStart", "source": "startup"}) == {}
    todo = {**_todo(), "task_repository": "git:github.com/example/read-only-settlement-fixture"}
    before = _run(project, monkeypatch, provider, [], "--runtime-profile", scenario,
                  "--turn-instance-id", turn, "--todo-id", TODO, "--defer-context-presentation",
                  "--codex-context-dir", str(native), "--decision-output-dir", str(capture),
                  todos=[todo], context_delivery="on", guard_cwd=worktree)
    assert before["execution_preparation"]["state"] == "claim_required"
    marker = native / "claim-source.json"
    if workspace_state == "legacy-marker":
        saved = json.loads(marker.read_bytes())
        saved.pop("workspace")
        marker.write_text(json.dumps(saved))
    elif workspace_state == "replaced":
        worktree.rename(tmp_path / "original-worktree")
        worktree.mkdir()
    digest = _present_saved_guard(project, native, capture, scenario, turn)
    hook = [sys.executable, "-m", "loopx.entrypoint", "--registry", str(project / "registry.json"),
            "--runtime-root", str(project / "runtime"), "--format", "json", "quota", "context-hook",
            "--runtime-profile", scenario, "--context-delivery", "on", "--codex-context-dir", str(native)]
    def post(response=None):
        payload = {**event, "hook_event_name": "PostToolUse", "tool_response": response}
        result = subprocess.run(hook, cwd=project, input=json.dumps(payload),
                                capture_output=True, text=True, check=True, timeout=30)
        return json.loads(result.stdout)

    facts = json.loads(post()["hookSpecificOutput"]["additionalContext"].rsplit("Receipt: ", 1)[1])
    assert facts["claim_operation_id"] == f"todo-claim:codex-capture:{digest}"
    assert facts["turn_instance_id"] == turn and facts["todo_id"] == TODO
    registry, runtime = project / "registry.json", project / "runtime"
    assert _spend_run_count(runtime, goal_id=GOAL) == 0
    if workspace_state != "admitted":
        assert facts["event"] == "host_execution_preparation_readback_required"
        assert "readiness_workdir" not in facts
        inspection = subprocess.run(shlex.split(facts["lease_command"]), cwd=project,
                                    capture_output=True, text=True, check=True, timeout=30)
        assert json.loads(inspection.stdout)["active"] is (workspace_state == "legacy-marker")
        assert ("readiness_command" in facts) is (workspace_state == "legacy-marker")
        return
    assert facts["event"] == "host_execution_prepared"
    assert facts["execution_preparation"]["state"] == "ready" and facts["context"]["ok"]
    # The checked reader, the subsequent writeback, and its replay keep
    # the selected Turn identity and the delivery from the linked checkout.
    reader = shlex.split(facts["context"]["recovery"]["command"])
    selected = subprocess.run([*reader, "--select", "/heartbeat_receipt/settlement_identity"], cwd=project,
                              capture_output=True, text=True, check=True, timeout=30)
    page = json.loads(selected.stdout)
    identity = json.loads(page["content"])["selections"][0]["value"]
    assert identity == before["heartbeat_receipt"]["settlement_identity"]
    assert post(page) == {} and post() == {}
    proof = facts["execution_preparation"]["lease_proof"]
    close = ["refresh-state", "--goal-id", GOAL, "--agent-id", AGENT, "--todo-id", TODO,
             "--turn-instance-id", turn, "--classification", "fixture_workspace_preparation",
             "--delivery-batch-scale", "single_surface", "--delivery-outcome", "outcome_progress",
             "--delivery-boundary", "in_flight_continuation", "--progress-result-class", "advanced",
             "--progress-work-item-id", TODO, "--progress-evidence-id", "fixture:workspace-preparation",
             "--task-lease-idempotency-key", proof["idempotency_key"],
             "--task-lease-expected-version", str(proof["expected_version"]),
             "--settle-turn", "--settlement-receipt", "--no-global-sync", "--suppress-external-sinks"]
    for _ in range(2):
        code, receipt = run_json_cli_result(*close, registry_path=registry, runtime_root=runtime, cwd=worktree)
        assert code == 0 and receipt["settlement_identity"] == identity
        assert receipt["settlement_progress"]["state"] == "settled"
    rows = [json.loads(line) for line in (runtime / "goals" / GOAL / "rollout-event-log.jsonl").read_text().splitlines()]
    assert [row["event_kind"] for row in rows if row["event_kind"] in {"refresh_state", "quota_spend"}] == [
        "refresh_state", "quota_spend"]


def _writeback_guard_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    todo_status: str = "open",
    leases: list[dict[str, object]] | None = None,
):
    from types import SimpleNamespace

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    state_path = project / "ACTIVE_GOAL_STATE.md"
    state_path.write_text("# Fixture\n", encoding="utf-8")
    runtime = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    write_fixture_registry(
        project=project,
        runtime_root=runtime,
        registry_path=registry,
        goal_id=GOAL,
        domain="fixture",
        adapter_kind="read_only_project_map_v0",
        state_file="ACTIVE_GOAL_STATE.md",
        registered_agents=(AGENT,),
    )
    todo = {**_todo(), "status": todo_status, "done": todo_status == "done"}
    initialize_canonical_authority(
        runtime,
        GOAL,
        build_todo_runtime_shadow_projection(
            goal_id=GOAL,
            handoff_mode="hard_lease",
            todos=[todo],
            leases=list(leases or ()),
        ),
        state_path=state_path,
        provider="sqlite",
    )

    def make_settlement(**overrides):
        defaults = {
            "prepared_lease": None,
            "prepared_lease_required": True,
            "completion_event": None,
            "native_child_admission": None,
            "semantic_replan_guard": None,
            "heartbeat_receipt": None,
            "supersede_event": None,
            "identity": SimpleNamespace(
                value=SimpleNamespace(turn_instance_id="turn-1"),
            ),
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def run_guard(**kwargs):
        with guard_first_writeback_execution(
            runtime_root=runtime,
            registry_path=registry,
            goal_id=GOAL,
            todo_id=TODO,
            agent_id=AGENT,
            locked_goal=None,
            **kwargs,
        ):
            return True

    return make_settlement, run_guard


def test_same_generation_lease_renewal_admits_open_todo_writeback_and_rejects_mismatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same-epoch lease renewals pass when explicitly enabled, never across epochs or keys."""
    current = {**_lease(), "idempotency_key": f"{AGENT}-execution", "version": 3, "lease_epoch": 1}
    make_settlement, run_guard = _writeback_guard_fixture(
        tmp_path, monkeypatch, todo_status="open", leases=[current],
    )
    prepared = prepared_lease_receipt_fields(
        {"idempotency_key": f"{AGENT}-execution", "expected_version": 1},
        lease_epoch=1,
    )
    settlement = make_settlement(prepared_lease=prepared, prepared_lease_required=True)
    stale_proof = {"idempotency_key": f"{AGENT}-execution", "expected_version": 1}
    renewed_proof = {"idempotency_key": f"{AGENT}-execution", "expected_version": 3}

    assert run_guard(
        requested_proof=stale_proof,
        settlement=settlement,
        allow_same_generation_lease_renewal=True,
    )
    assert run_guard(
        requested_proof=renewed_proof,
        settlement=settlement,
        allow_same_generation_lease_renewal=False,
    )
    with pytest.raises(RefreshTaskLeaseUnavailable):
        run_guard(
            requested_proof=stale_proof,
            settlement=settlement,
            allow_same_generation_lease_renewal=False,
        )

    for bad_settlement, bad_proof in (
        # Cross-epoch renewal must fail closed even for the same key.
        (
            make_settlement(
                prepared_lease={**prepared, "prepared_lease_epoch": 2},
                prepared_lease_required=True,
            ),
            renewed_proof,
        ),
        # Cross-key proof must fail closed.
        (
            make_settlement(
                prepared_lease={**prepared, "prepared_lease_idempotency_key": "other-key"},
                prepared_lease_required=True,
            ),
            renewed_proof,
        ),
        # Legacy prepared receipt without lease_epoch must not advance versions.
        (
            make_settlement(
                prepared_lease={"prepared_lease_idempotency_key": f"{AGENT}-execution", "prepared_lease_version": 1},
                prepared_lease_required=True,
            ),
            renewed_proof,
        ),
        # Version regression in requested_proof must fail closed.
        (
            settlement,
            {"idempotency_key": f"{AGENT}-execution", "expected_version": 4},
        ),
    ):
        with pytest.raises(RefreshTaskLeaseUnavailable):
            run_guard(
                requested_proof=bad_proof,
                settlement=bad_settlement,
                allow_same_generation_lease_renewal=True,
            )


def test_validation_recovery_turn_accepts_only_matching_retired_recovery_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resumed recovery matches exact `.validation-recovery.` or `.admission-retry` retired leases."""
    for index, suffix in enumerate((".validation-recovery.1", ".admission-retry", ".admission-retry.turn-2")):
        retired_recovery = {
            **_lease(status="released"),
            "idempotency_key": f"turn-1{suffix}",
            "version": 1,
            "lease_epoch": 2,
        }
        make_settlement, run_guard = _writeback_guard_fixture(
            tmp_path / f"recovery-{index}", monkeypatch, todo_status="done", leases=[retired_recovery],
        )
        prepared = prepared_lease_receipt_fields(
            {"idempotency_key": "turn-1", "expected_version": 1},
            lease_epoch=1,
        )
        completion = {
            "event_kind": "todo_complete",
            "todo_id": TODO,
            "agent_id": AGENT,
            "run_id": "turn-1",
        }
        valid_proof = {"idempotency_key": "turn-1", "expected_version": 1}
        assert run_guard(
            requested_proof=valid_proof,
            settlement=make_settlement(
                prepared_lease=prepared,
                prepared_lease_required=True,
                completion_event=completion,
            ),
            validation_recovery_turn_instance_id="turn-1",
        )
        for bad_completion, bad_recovery_turn in (
            (None, "turn-1"),
            ({**completion, "run_id": "other-turn"}, "turn-1"),
            ({**completion, "agent_id": "other-agent"}, "turn-1"),
            (completion, None),
            (completion, "other-turn"),
        ):
            with pytest.raises(RefreshTaskLeaseUnavailable):
                run_guard(
                    requested_proof=valid_proof,
                    settlement=make_settlement(
                        prepared_lease=prepared,
                        prepared_lease_required=True,
                        completion_event=bad_completion,
                    ),
                    validation_recovery_turn_instance_id=bad_recovery_turn,
                )


def test_validation_recovery_rejects_unrelated_retired_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unrelated retired lease without recovery/retry suffix never satisfies recovery."""
    for index, bad_key in enumerate(("unrelated-key", "turn-1.admission-retry2")):
        foreign_retired = {
            **_lease(status="released"),
            "idempotency_key": bad_key,
            "version": 1,
            "lease_epoch": 2,
        }
        make_settlement, run_guard = _writeback_guard_fixture(
            tmp_path / f"reject-{index}", monkeypatch, todo_status="done", leases=[foreign_retired],
        )
        prepared = prepared_lease_receipt_fields(
            {"idempotency_key": "turn-1", "expected_version": 1},
            lease_epoch=1,
        )
        with pytest.raises(RefreshTaskLeaseUnavailable):
            run_guard(
                requested_proof={"idempotency_key": "turn-1", "expected_version": 1},
                settlement=make_settlement(
                    prepared_lease=prepared,
                    prepared_lease_required=True,
                    completion_event={
                        "event_kind": "todo_complete",
                        "todo_id": TODO,
                        "agent_id": AGENT,
                        "run_id": "turn-1",
                    },
                ),
                validation_recovery_turn_instance_id="turn-1",
            )


def test_supersede_event_admits_done_todo_only_for_exact_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Superseded done Todos satisfy completion receipt checks only for their exact Turn."""
    retired = {**_lease(status="released"), "idempotency_key": "turn-1", "version": 1, "lease_epoch": 1}
    make_settlement, run_guard = _writeback_guard_fixture(
        tmp_path, monkeypatch, todo_status="done", leases=[retired],
    )
    prepared = prepared_lease_receipt_fields(
        {"idempotency_key": "turn-1", "expected_version": 1},
        lease_epoch=1,
    )
    supersede = {
        "event_kind": "todo_supersede",
        "status": "done",
        "todo_id": TODO,
        "agent_id": AGENT,
        "run_id": "turn-1",
    }
    assert run_guard(
        requested_proof={"idempotency_key": "turn-1", "expected_version": 1},
        settlement=make_settlement(
            prepared_lease=prepared,
            prepared_lease_required=True,
            supersede_event=supersede,
        ),
    )
    for bad_supersede in (
        None,
        {**supersede, "run_id": "foreign-turn"},
        {**supersede, "todo_id": "other-todo"},
        {**supersede, "status": "open"},
    ):
        with pytest.raises(RefreshTaskLeaseUnavailable):
            run_guard(
                requested_proof={"idempotency_key": "turn-1", "expected_version": 1},
                settlement=make_settlement(
                    prepared_lease=prepared,
                    prepared_lease_required=True,
                    supersede_event=bad_supersede,
                ),
            )


def test_todo_bound_autonomous_replan_admits_without_prepared_lease_only_on_replan_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Todo-bound autonomous replan Turns pass without prepared_lease but stay lease-fenced."""
    active = {**_lease(), "idempotency_key": f"{AGENT}-execution", "version": 1, "lease_epoch": 1}
    make_settlement, run_guard = _writeback_guard_fixture(
        tmp_path, monkeypatch, todo_status="open", leases=[active],
    )
    valid_proof = {"idempotency_key": f"{AGENT}-execution", "expected_version": 1}
    replan_settlement = make_settlement(
        prepared_lease=None,
        prepared_lease_required=False,
        semantic_replan_guard={"scope": "turn_guard", "selected_obligation_id": "replan-1"},
        heartbeat_receipt={"status": "autonomous_replan_required", "details": {"delivery_allowed": False}},
    )
    assert run_guard(requested_proof=valid_proof, settlement=replan_settlement)
    assert run_guard(requested_proof=None, settlement=replan_settlement)

    for bad_settlement, proof in (
        (replan_settlement, {"idempotency_key": f"{AGENT}-execution", "expected_version": 99}),
        (
            make_settlement(
                prepared_lease=None,
                prepared_lease_required=False,
                semantic_replan_guard={"scope": "turn_guard", "selected_obligation_id": "replan-1"},
                heartbeat_receipt={"status": "normal_run", "details": {"delivery_allowed": True}},
            ),
            valid_proof,
        ),
        (
            make_settlement(
                prepared_lease=None,
                prepared_lease_required=False,
                semantic_replan_guard={"scope": "turn_guard", "selected_obligation_id": ""},
                heartbeat_receipt={"status": "autonomous_replan_required", "details": {"delivery_allowed": False}},
            ),
            valid_proof,
        ),
    ):
        with pytest.raises(RefreshTaskLeaseUnavailable):
            run_guard(requested_proof=proof, settlement=bad_settlement)

    # Foreign active lease must reject even on a todo-bound replan guard.
    foreign_dir = tmp_path / "foreign"
    make_foreign, run_foreign = _writeback_guard_fixture(
        foreign_dir,
        monkeypatch,
        todo_status="open",
        leases=[{**_lease(owner="other-agent"), "idempotency_key": "other-exec", "version": 1}],
    )
    with pytest.raises(RefreshTaskLeaseUnavailable):
        run_foreign(
            requested_proof=None,
            settlement=make_foreign(
                prepared_lease=None,
                prepared_lease_required=False,
                semantic_replan_guard={"scope": "turn_guard", "selected_obligation_id": "replan-1"},
                heartbeat_receipt={"status": "autonomous_replan_required", "details": {"delivery_allowed": False}},
            ),
        )


def test_scoped_user_gate_fallback_execution_preparation_excludes_deferred_replan(monkeypatch):
    from loopx.control_plane.quota import execution_preparation

    replan_payload = {
        "should_run": True,
        "normal_delivery_allowed": False,
        "effective_action": "successor_replan_required",
        "selected_todo": {"todo_id": TODO, "status": "deferred", "resume_ready": True},
        "scoped_user_gate_fallback": {
            "selected_executable": {"todo_id": TODO, "fallback_kind": "deferred_successor_replan"}
        },
        "execution_obligation": {"must_attempt_work": True, "delivery_allowed": True},
    }

    def unexpected_read(**_):
        pytest.fail("deferred_successor_replan fallback must not consult execution-lease preparation")

    monkeypatch.setattr(execution_preparation, "project_selected_todo_execution_preparation", unexpected_read)
    apply_selected_todo_execution_preparation(
        replan_payload,
        runtime_root=None,
        goal_id=GOAL,
        agent_id=AGENT,
        obligation=replan_payload["execution_obligation"],
    )
    assert "execution_preparation" not in replan_payload
    assert replan_payload["should_run"] is True
    assert replan_payload["execution_obligation"]["delivery_allowed"] is True

    monkeypatch.setattr(
        execution_preparation,
        "project_selected_todo_execution_preparation",
        lambda **_: {
            "schema_version": "execution_preparation_v0",
            "todo_id": TODO,
            "agent_id": AGENT,
            "handoff_mode": "hard_lease",
            "state": "claim_required",
            "delivery_allowed": False,
            "reason": "Acquire an atomic lease before advancing the open fallback.",
            "claim": {"cli_args": ["todo", "claim", "--goal-id", GOAL, "--todo-id", TODO], "command_line": f"loopx todo claim --goal-id {GOAL} --todo-id {TODO} --thin"},
        },
    )
    open_fallback_payload = {
        "should_run": True,
        "normal_delivery_allowed": False,
        "effective_action": "scoped_user_gate_fallback",
        "selected_todo": {"todo_id": TODO, "status": "open"},
        "scoped_user_gate_fallback": {
            "selected_executable": {"todo_id": TODO}
        },
        "execution_obligation": {"must_attempt_work": True, "delivery_allowed": True},
    }
    apply_selected_todo_execution_preparation(
        open_fallback_payload,
        runtime_root=None,
        goal_id=GOAL,
        agent_id=AGENT,
        obligation=open_fallback_payload["execution_obligation"],
    )
    assert open_fallback_payload["execution_preparation"]["state"] == "claim_required"
    assert open_fallback_payload["should_run"] is True
    assert open_fallback_payload["execution_obligation"]["delivery_allowed"] is False


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_native_claim_refusal_is_reported_once_for_the_original_capture(tmp_path, monkeypatch, provider):
    from test_quota_settlement_cli import _spend_run_count

    tmp_path = tmp_path.resolve()
    monkeypatch.chdir(tmp_path)
    session, turn, scenario = "claim-race-session", "claim-race-turn", "codex_app_heartbeat"
    monkeypatch.setenv("CODEX_THREAD_ID", session)
    native, capture = tmp_path / "native", tmp_path / "before"
    native.mkdir(mode=0o700)
    registry, runtime = tmp_path / "registry.json", tmp_path / "runtime"

    def hook(name):
        event = {"session_id": session, "cwd": str(tmp_path), "hook_event_name": name}
        if name == "SessionStart":
            event["source"] = "startup"
        result = subprocess.run([sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
            "--runtime-root", str(runtime), "quota", "context-hook", "--context-delivery", "on",
            "--runtime-profile", scenario, "--codex-context-dir", str(native)], input=json.dumps(event),
            cwd=tmp_path, check=True, capture_output=True, text=True, timeout=30)
        return json.loads(result.stdout).get("hookSpecificOutput", {}).get("additionalContext", "")

    assert not hook("SessionStart")
    other_todo = "todo_independent"
    before = _run(tmp_path, monkeypatch, provider, [], "--runtime-profile", scenario,
        "--todo-id", TODO, "--turn-instance-id", turn, "--decision-output-dir", str(capture),
        "--defer-context-presentation", "--codex-context-dir", str(native), context_delivery="on",
        todos=[{**_todo(), "claimed_by": None}, {**_todo(), "todo_id": other_todo, "index": 2}])
    assert before["execution_preparation"]["state"] == "claim_required"
    _present_saved_guard(tmp_path, native, capture, scenario, turn)
    operation = json.loads((native / "session.json").read_text())["recovery"]["execution_preparation"]["claim_args"][-1]
    code, peer = run_json_cli_result("todo", "claim", "--goal-id", GOAL, "--agent-id", "agent-b",
        "--claimed-by", "agent-b", "--todo-id", TODO, "--claim-operation-id", "peer-claim",
        registry_path=registry, runtime_root=runtime)
    assert code == 0 and peer["ok"], peer

    def current_lease():
        code, inspected = run_json_cli_result("task-lease", "inspect", "--goal-id", GOAL,
            "--todo-id", TODO, registry_path=registry, runtime_root=runtime)
        assert code == 0, inspected
        return inspected["lease"]

    original = current_lease()
    assert original["owner"] == "agent-b"
    message = hook("PostToolUse")
    assert "claim_owner_mismatch" in message and "agent-b" in message, message
    assert "fresh eligible" in message and "claim-readback" not in message
    assert not hook("PostToolUse")
    _present_saved_guard(tmp_path, native, capture, scenario, turn)
    assert not hook("PostToolUse")
    assert current_lease() == original
    assert _spend_run_count(runtime, goal_id=GOAL) == 0

    code, receipt = run_json_cli_result("todo", "receipt", "--goal-id", GOAL, "--operation-id", operation,
        registry_path=registry, runtime_root=runtime)
    assert receipt["status"] == "missing", receipt

    # The refusal is local to the old attempt. Another eligible Todo in this
    # same session can still go from a live guard through its own normal claim.
    next_capture, next_turn = tmp_path / "independent", "independent-turn"
    code, next_guard = run_json_cli_result("quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT,
        "--todo-id", other_todo, "--turn-instance-id", next_turn, "--runtime-profile", scenario,
        "--context-delivery", "on", "--defer-context-presentation", "--codex-context-dir", str(native),
        "--decision-output-dir", str(next_capture), registry_path=registry, runtime_root=runtime)
    assert code == 0 and next_guard["execution_preparation"]["state"] == "claim_required", next_guard
    _present_saved_guard(tmp_path, native, next_capture, scenario, next_turn)
    prepared = json.loads(hook("PostToolUse").rsplit("Receipt: ", 1)[-1])
    assert prepared["event"] == "host_execution_prepared" and prepared["todo_id"] == other_todo
    assert current_lease() == original
    assert _spend_run_count(runtime, goal_id=GOAL) == 0


@pytest.mark.parametrize(("reason_code", "isolatable"), [
    ("write_scope_conflict", False), ("workspace_lease_conflict", True),
])
def test_lease_refusal_explains_when_a_fresh_guard_can_retry(tmp_path, reason_code, isolatable):
    from loopx.control_plane.turn_driver.codex_execution_preparation import NativeClaimRejected, _run_json

    identity = {"goal_id": GOAL, "todo_id": TODO, "actor_agent_id": AGENT, "claim_operation_id": "original"}
    decision = {**identity, "schema_version": "loopx_coordination_todo_claim_decision_v0",
        "status": "rejected", "reason_code": reason_code, "reason": "Conflicting active lease"}
    reply = subprocess.CompletedProcess([], 1, json.dumps({"ok": False, "source_authority": "file_v0",
        "decision_read_from_provider": True, "legacy_fallback_used": False, "claim_decision": decision}), "")
    with pytest.raises(NativeClaimRejected) as refusal:
        _run_json(["todo", "claim"], registry_path=tmp_path / "registry.json", runtime_root=None,
            cwd=tmp_path, timeout=1, runner=lambda *args, **kwargs: reply, claim_identity=identity)
    message = str(refusal.value)
    assert "lease is released or expires" in message and "fresh eligible guard" in message
    assert ("independent authorized worktree if the work can be isolated" in message) == isolatable


@pytest.mark.parametrize("case", ["wrong-operation", "unverified-provider", "prior-receipt"])
def test_inconclusive_claim_reply_remains_recoverable(tmp_path, case):
    from loopx.control_plane.turn_driver.codex_execution_preparation import _run_json

    identity = {"goal_id": GOAL, "todo_id": TODO, "actor_agent_id": AGENT, "claim_operation_id": "original"}
    decision = {**identity, "schema_version": "loopx_coordination_todo_claim_decision_v0",
        "status": "rejected", "reason_code": "claim_owner_mismatch", "reason": "Todo is already claimed"}
    payload = {"ok": False, "source_authority": "file_v0", "decision_read_from_provider": True,
        "legacy_fallback_used": False, "claim_decision": decision}
    if case == "wrong-operation":
        decision["claim_operation_id"] = "another"
    elif case == "unverified-provider":
        payload["decision_read_from_provider"] = False
    else:
        payload["original_receipt"] = {"operation_id": "original"}
    reply = subprocess.CompletedProcess([], 1, json.dumps(payload), "")
    with pytest.raises(RuntimeError):
        _run_json(["todo", "claim"], registry_path=tmp_path / "registry.json", runtime_root=None,
            cwd=tmp_path, timeout=1, runner=lambda *args, **kwargs: reply, claim_identity=identity)
