"""Real CLI and canonical FileAuthorityStore; no production registry or promotion."""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.todos.contract import format_todo_metadata_line
from loopx.todos import list_goal_todos, update_goal_todo


def fixture(tmp_path: Path, promoted: bool, provider: str = "file") -> tuple[Path, Path]:
    project = tmp_path / "project"
    state = project / ".codex/goals/goal-a/ACTIVE_GOAL_STATE.md"
    state.parent.mkdir(parents=True)
    rows = "\n".join(
        "- [ ] " + text + "\n" + format_todo_metadata_line(
            todo_id=todo_id, status="open", task_class="advancement_task",
            claimed_by=owner, note="Preserved note",
        ) for todo_id, text, owner in [
            ("todo_target", "Synthetic task", "agent-a"),
            ("todo_other", "Independent task", "agent-b"),
        ]
    )
    state.write_text("# Goal\n\n## User Todo / Owner Review Reading Queue\n\n"
                     "## Agent Todo\n\n" + rows + "\n\n## Completed Work Archive\n", encoding="utf-8")
    registry = tmp_path / "registry.json"
    runtime = tmp_path / "runtime"
    registry.write_text(json.dumps({"schema_version": 1, "common_runtime_root": str(runtime), "goals": [{
        "id": "goal-a", "status": "active", "repo": str(project),
        "state_file": ".codex/goals/goal-a/ACTIVE_GOAL_STATE.md",
        "coordination": {"registered_agents": ["agent-a", "agent-b"]},
    }]}), encoding="utf-8")
    if promoted:
        todos = list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"]
        projection = build_todo_runtime_shadow_projection(goal_id="goal-a", todos=todos, handoff_mode="soft_claim")
        initialize_canonical_authority(runtime, "goal-a", projection, state_path=state, provider=provider)
        state.unlink()  # The update must neither require nor import a Markdown authority source.
    return registry, state


def update(registry: Path, *args: str, ok: bool = True) -> dict:
    process = subprocess.run([
        sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(registry),
        "todo", "update", "--goal-id", "goal-a", "--todo-id", "todo_target", "--agent-id", "agent-a", *args,
    ], capture_output=True, text=True, timeout=45)
    result = json.loads(process.stdout)
    assert (process.returncode == 0) is ok, (result, process.stderr)
    return result


def records(registry: Path) -> dict[str, dict]:
    return {item["todo_id"]: item for item in list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"]}


@pytest.mark.parametrize("promoted", [False, True])
def test_work_requirements_update_preserves_authority_and_supports_explicit_empty(tmp_path: Path, promoted: bool) -> None:
    registry, _state = fixture(tmp_path, promoted)
    before = records(registry)
    result = update_goal_todo(
        registry_path=registry, goal_id="goal-a", todo_id="todo_target", agent_id="agent-a",
        action_kind="IMPLEMENT", task_domain="Code.Review",
        task_repository="git@github.com:example/project.git",
        required_capabilities=["Code-Review", "code_review"],
        target_capabilities=["Delivery"], required_write_scopes=["src/**", "tests/**"],
        explore_result_node_refs=["Node:alpha"],
    )
    assert result["ok"] is True
    todo = records(registry)["todo_target"]
    assert todo["action_kind"] == "implement"
    assert todo["task_domain"] == "code.review"
    assert todo["task_repository"] == "git:github.com/example/project"
    assert todo["required_capabilities"] == ["code_review"]
    assert todo["target_capabilities"] == ["delivery"]
    assert todo["required_write_scopes"] == ["src/**", "tests/**"]
    assert todo["explore_result_node_refs"] == ["Node:alpha"]
    assert todo["claimed_by"] == "agent-a"
    assert records(registry)["todo_other"] == before["todo_other"]
    update_goal_todo(registry_path=registry, goal_id="goal-a", todo_id="todo_target",
                     agent_id="agent-a", required_capabilities=[], required_write_scopes=[],
                     target_capabilities=[], explore_result_node_refs=[])
    cleared = records(registry)["todo_target"]
    for field in ("required_capabilities", "target_capabilities", "required_write_scopes", "explore_result_node_refs"):
        assert not cleared.get(field)
    assert cleared["task_repository"] == todo["task_repository"]


@pytest.mark.parametrize("promoted", [False, True])
@pytest.mark.parametrize("intent", [
    {"required_capabilities": ["code_review", "bad/token"]},
    {"required_write_scopes": ["src/**", "../escape"]},
    {"task_repository": "https://user:password@example.com/project"},
    {"task_repository": "user:password@example.com:project"},
    {"explore_result_node_refs": ["Node:alpha", "bad/ref"]},
])
def test_invalid_work_requirement_is_not_silently_dropped(tmp_path: Path, promoted: bool, intent: dict) -> None:
    registry, state = fixture(tmp_path, promoted)
    before = records(registry)
    with pytest.raises((ValueError, RuntimeError)):
        update_goal_todo(registry_path=registry, goal_id="goal-a", todo_id="todo_target",
                         agent_id="agent-a", text="Must not partially commit", **intent)
    assert records(registry) == before
    if promoted:
        assert not state.exists()


@pytest.mark.parametrize("promoted", [False, True])
def test_cli_routes_work_requirements_without_display_dependency(tmp_path: Path, promoted: bool) -> None:
    registry, state = fixture(tmp_path, promoted)
    args = ["--action-kind", "IMPLEMENT", "--task-domain", "code",
            "--task-repository", "https://github.com/example/project",
            "--required-capability", "Code-Review", "--target-capability", "Delivery",
            "--required-write-scope", "src/**", "--explore-result-node-ref", "Node:alpha"]
    if promoted:
        args += ["--update-operation-id", "requirements-cli"]
    update(registry, *args, "--dry-run")
    if promoted:
        assert not state.exists()
    update(registry, *args)
    assert records(registry)["todo_target"]["required_capabilities"] == ["code_review"]
    if promoted:
        assert update(registry, *args)["status"] == "replayed"
    update(registry, "--clear-explore-result-node-refs")
    assert not records(registry)["todo_target"].get("explore_result_node_refs")
    before = records(registry)
    update(registry, "--required-capability", "valid", "--required-capability", "bad/token", ok=False)
    assert records(registry) == before


@pytest.mark.parametrize("promoted", [False, True])
@pytest.mark.parametrize(("remote", "expected"), [
    ("https://github.com:443/example/project", "git:github.com/example/project"),
    ("ssh://git@github.com:22/example/project", "git:github.com/example/project"),
    ("git://github.com:9418/example/project", "git:github.com/example/project"),
    ("https://github.com:22/example/project", "git:github.com:22/example/project"),
    ("ssh://git@github.com:443/example/project", "git:github.com:443/example/project"),
    ("git://github.com:80/example/project", "git:github.com:80/example/project"),
    ("git://github.com:0/example/project", "git:github.com:0/example/project"),
])
def test_cli_repository_ports_match_before_and_after_promotion(
    tmp_path: Path, promoted: bool, remote: str, expected: str,
) -> None:
    registry, _state = fixture(tmp_path, promoted)
    before = records(registry)
    args = ["--task-repository", remote]
    if promoted:
        args += ["--update-operation-id", "repository-port-cli"]
    update(registry, *args)
    after = records(registry)
    assert after["todo_target"]["task_repository"] == expected
    assert after["todo_other"] == before["todo_other"]
    if promoted:
        assert update(registry, *args)["status"] == "replayed"
        assert records(registry) == after


@pytest.mark.parametrize("promoted", [False, True])
@pytest.mark.parametrize("surface", ["cli", "python_api"])
@pytest.mark.parametrize(("label", "note", "expected_note"), [
    ("omitted", None, "Preserved note"),
    ("empty", "", "Preserved note"),
    ("unicode_whitespace", " \t\u2003\n", "Preserved note"),
    ("nonempty", "  Updated\u2003note  ", "Updated note"),
])
def test_note_input_semantics_match_before_and_after_promotion(
    tmp_path: Path, promoted: bool, surface: str, label: str,
    note: str | None, expected_note: str,
) -> None:
    registry, _state = fixture(tmp_path, promoted)
    operation_id = f"note-{surface}-{label}" if promoted else None
    if surface == "cli":
        args = ["--text", "Corrected task"]
        if note is not None:
            args += ["--note", note]
        if operation_id is not None:
            args += ["--update-operation-id", operation_id]
        update(registry, *args)
    else:
        result = update_goal_todo(
            registry_path=registry, goal_id="goal-a", todo_id="todo_target",
            role="agent", agent_id="agent-a", text="Corrected task", note=note,
            update_operation_id=operation_id,
        )
        assert result["ok"] is True
    persisted = records(registry)["todo_target"]
    assert persisted["text"] == "Corrected task"
    assert persisted["note"] == expected_note


def test_promoted_v0_replay_uses_normalized_empty_note_identity(tmp_path: Path) -> None:
    registry, _state = fixture(tmp_path, True)
    base = ["--text", "Corrected task", "--update-operation-id", "note-v0-replay"]
    assert update(registry, *base)["status"] == "applied"
    assert update(registry, *base, "--note", "")["status"] == "replayed"
    assert update(registry, *base, "--note", " \t\u2003\n")["status"] == "replayed"
    update(registry, *base, "--note", "Different note", ok=False)
    assert records(registry)["todo_target"]["note"] == "Preserved note"


@pytest.mark.parametrize("promoted", [False, True])
def test_public_cli_nonterminal_wait_update_and_clear(tmp_path: Path, promoted: bool) -> None:
    registry, state = fixture(tmp_path, promoted)
    before = records(registry)
    args = ["--status", "deferred", "--resume-when", "pr_merged:#123", "--reason", "Await upstream",
            "--evidence", "Synthetic evidence", "--text", "  Corrected\u2003task  "]
    if promoted:
        args += ["--update-operation-id", "planning-attempt"]
    update(registry, *args, "--dry-run")
    assert records(registry) == before
    if promoted:
        assert not state.exists()
    update(registry, *args)
    waiting = records(registry)
    todo = waiting["todo_target"]
    assert todo["status"] == "deferred" and todo["done"] is True
    assert todo["resume_when"] == "pr_merged:#123"
    assert todo["reason"] == "Await upstream"
    assert todo["text"] == "Corrected task"
    assert todo["note"] == "Preserved note"
    assert todo["claimed_by"] == "agent-a"
    assert waiting["todo_other"] == before["todo_other"]
    if promoted:
        assert update(registry, *args)["status"] == "replayed"
        update(registry, *args, "--note", "Changed retry", ok=False)
        assert records(registry) == waiting
        assert state.exists(), "accepted commit should drain its independent display projection"
    update(registry, "--status", "open", "--clear-resume-when")
    resumed = records(registry)["todo_target"]
    assert resumed["status"] == "open" and resumed["done"] is False
    assert not resumed.get("resume_when")
    assert not resumed.get("resume_monitor_generation")


def deferred_hard_lease_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> tuple[Path, Path]:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, state = fixture(tmp_path, False)
    todos = list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"]
    target = next(todo for todo in todos if todo["todo_id"] == "todo_target")
    target.update(status="deferred", done=True, resume_when="resume_at:2020-01-01T00:00:00Z")
    projection = build_todo_runtime_shadow_projection(
        goal_id="goal-a", todos=todos, handoff_mode="hard_lease",
    )
    initialize_canonical_authority(tmp_path / "runtime", "goal-a", projection,
                                   state_path=state, provider=provider)
    state.unlink()
    return registry, state


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_dependency_replan_uses_existing_inactive_lifecycle_then_fresh_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, state = fixture(tmp_path, False)
    todos = list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"]
    target = next(todo for todo in todos if todo["todo_id"] == "todo_target")
    target.update(resume_when="todo_done:todo_other", successor_todo_ids=["todo_other"])
    projection = build_todo_runtime_shadow_projection(
        goal_id="goal-a", todos=todos, handoff_mode="hard_lease",
    )
    retained = {
        "todo_id": "todo_target", "owner": "agent-a", "status": "released",
        "idempotency_key": "retired-execution", "version": 4, "lease_epoch": 2,
        "expires_at": "2020-01-01T00:00:00Z", "write_scopes": [],
    }
    projection["leases"] = [retained]
    initialize_canonical_authority(tmp_path / "runtime", "goal-a", projection,
                                   state_path=state, provider=provider)
    state.unlink()

    def lease(action: str, *args: str) -> tuple[int, dict]:
        process = subprocess.run([
            sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(registry),
            "task-lease", action, "--goal-id", "goal-a", "--todo-id", "todo_target", *args,
        ], capture_output=True, text=True, timeout=45)
        return process.returncode, json.loads(process.stdout)

    before = records(registry)
    code, rejection = lease("acquire", "--owner", "agent-a", "--idempotency-key", "next-execution")
    assert code == 1 and rejection["error_code"] == "todo_dependency_pending"
    failed = update(registry, "--clear-resume-when", "--reason", "Reviewed replan",
                    "--update-operation-id", "direct-clear", ok=False)
    assert failed["error_code"] == "handoff_mode_requires_lease"
    assert failed["recovery"]["execution_authority_granted"] is False
    # The candidate makes the existing two-step route discoverable; it does
    # not add an admission exception for an old execution proof.
    assert failed["recovery"]["action"] == "resolve_lifecycle_edit"
    assert len(failed["recovery"]["lifecycle_replan"]["steps"]) == 2
    assert records(registry) == before

    guide = failed["recovery"]["lifecycle_replan"]
    assert (guide["goal_id"], guide["todo_id"], guide["agent_id"]) == ("goal-a", "todo_target", "agent-a")
    assert guide["execution_proof"] == "omit" and guide["next_execution"] == "acquire_fresh_lease"
    basis = list_goal_todos(registry_path=registry, goal_id="goal-a")["authority_read"]["provider_revision"]
    pause = [*shlex.split(guide["steps"][0]["command"])[3:],
             "--update-operation-id", "pause-obsolete-wait", "--update-expected-provider-revision", basis]
    assert update(registry, *pause, "--dry-run")["status"] == "planned"
    assert records(registry) == before
    assert update(registry, *pause)["status"] == "applied"
    assert update(registry, *pause)["status"] == "replayed"
    blocked = records(registry)
    assert blocked["todo_target"]["status"] == "blocked"
    assert not blocked["todo_target"].get("resume_when")
    assert lease("inspect")[1]["lease"]["status"] == "released"
    code, rejected = lease("acquire", "--owner", "agent-a", "--idempotency-key", "paused-execution")
    assert code == 1 and rejected["error_code"] == "todo_not_open"
    update(registry, "--status", "open", "--clear-resume-when", "--reason", "Reviewed new route",
           "--evidence", "Bundled evidence is a separate leased edit", ok=False)
    assert records(registry) == blocked
    reopen = shlex.split(guide["steps"][1]["command"])[3:]
    update(registry, *reopen, "--update-operation-id", "stale-reopen",
           "--update-expected-provider-revision", basis, ok=False)
    assert records(registry) == blocked
    current_basis = list_goal_todos(registry_path=registry, goal_id="goal-a")["authority_read"]["provider_revision"]
    resumed = update(registry, *reopen, "--update-operation-id", "resume-new-route",
                     "--update-expected-provider-revision", current_basis)
    assert resumed["status"] == "applied"
    after = records(registry)
    assert after["todo_target"]["status"] == "open"
    assert after["todo_target"]["text"] == before["todo_target"]["text"]
    assert after["todo_target"]["claimed_by"] == before["todo_target"]["claimed_by"]
    assert after["todo_target"]["successor_todo_ids"] == before["todo_target"]["successor_todo_ids"]
    assert after["todo_other"] == before["todo_other"]
    # Reopening has not changed the retained execution identity or granted one.
    assert lease("inspect")[1]["lease"]["status"] == "released"
    code, acquired = lease("acquire", "--owner", "agent-a", "--idempotency-key", "fresh-execution")
    assert code == 0 and acquired["acquired"]
    assert acquired["lease"]["version"] == 5 and acquired["lease"]["lease_epoch"] == 3
    assert acquired["source_authority"] == f"{provider}_v0"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_bound_user_action_metadata_through_real_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, state = fixture(tmp_path, False)
    todos = list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"]
    target = next(todo for todo in todos if todo["todo_id"] == "todo_target")
    target.pop("claimed_by")
    target.update(role="user", task_class="user_action", source_section="User Todo",
                  created_by="agent-a", bound_agent="agent-a")
    projection = build_todo_runtime_shadow_projection(
        goal_id="goal-a", todos=todos, handoff_mode="hard_lease",
    )
    initialized = initialize_canonical_authority(
        tmp_path / "runtime", "goal-a", projection, state_path=state, provider=provider,
    )
    state.unlink()
    before = records(registry)
    args = ["--role", "user", "--text", "Reminder: disposition awaits user execution",
            "--note", "No execution authorization", "--evidence", "Synthetic receipt",
            "--update-operation-id", "bound-action-copy",
            "--update-expected-provider-revision", initialized["provider_revision"]]
    assert update(registry, *args, "--dry-run")["status"] == "planned"
    assert not state.exists()
    assert records(registry) == before
    applied = update(registry, *args)
    assert applied["status"] == "applied"
    assert applied["source_authority"] == f"{provider}_v0"
    after = records(registry)
    assert after["todo_other"] == before["todo_other"]
    assert after["todo_target"]["text"] == "Reminder: disposition awaits user execution"
    assert after["todo_target"]["note"] == "No execution authorization"
    assert after["todo_target"]["evidence"] == "Synthetic receipt"
    for field in ("role", "task_class", "status", "done", "created_by", "bound_agent", "claimed_by"):
        assert after["todo_target"].get(field) == before["todo_target"].get(field)
    assert update(registry, *args)["status"] == "replayed"
    update(registry, *args, "--note", "Conflicting retry", ok=False)
    stale = update(registry, "--role", "user", "--note", "Stale writer",
                   "--update-operation-id", "stale-action-copy",
                   "--update-expected-provider-revision", initialized["provider_revision"], ok=False)
    assert stale["reason_code"] == "provider_revision_mismatch"
    for attempt in (["--agent-id", "agent-b", "--text", "Foreign writer"],
                    ["--status", "blocked"], ["--bound-agent", "agent-b"],
                    ["--required-capability", "shell"]):
        update(registry, "--role", "user", *attempt, ok=False)
    assert records(registry) == after
    # Authoring an updated reminder must not make an execution claim possible.
    claimed = subprocess.run([
        sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(registry),
        "todo", "claim", "--goal-id", "goal-a", "--todo-id", "todo_target",
        "--agent-id", "agent-a", "--claimed-by", "agent-a",
    ], capture_output=True, text=True, timeout=45)
    assert claimed.returncode == 1
    assert json.loads(claimed.stdout)["error_code"] == "todo_not_agent"
    inspected = subprocess.run([
        sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(registry),
        "task-lease", "inspect", "--goal-id", "goal-a", "--todo-id", "todo_target",
    ], capture_output=True, text=True, timeout=45)
    assert inspected.returncode == 0
    assert json.loads(inspected.stdout)["lease"] is None
    # A fresh CAS permits recovery after the rejected stale attempt.
    recovered = update(registry, "--role", "user", "--note", "Recovered copy",
                       "--update-operation-id", "recovered-action-copy",
                       "--update-expected-provider-revision", applied["provider_revision"])
    assert recovered["status"] == "applied"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_bound_user_action_leased_metadata_through_real_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, state = fixture(tmp_path, False)
    todos = list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"]
    target = next(todo for todo in todos if todo["todo_id"] == "todo_target")
    target.pop("claimed_by")
    target.update(role="user", task_class="user_action", source_section="User Todo",
                  created_by="agent-a", bound_agent="agent-a")
    projection = build_todo_runtime_shadow_projection(
        goal_id="goal-a", todos=todos, handoff_mode="hard_lease",
    )
    initialize_canonical_authority(tmp_path / "runtime", "goal-a", projection,
                                   state_path=state, provider=provider)
    state.unlink()

    def lease(action: str, *args: str) -> dict:
        process = subprocess.run([
            sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(registry),
            "task-lease", action, "--goal-id", "goal-a", "--todo-id", "todo_target", *args,
        ], capture_output=True, text=True, timeout=45)
        assert process.returncode == 0, process.stderr
        return json.loads(process.stdout)

    acquired = lease("acquire", "--owner", "agent-a", "--idempotency-key", "copy-execution")
    retained = acquired["lease"]
    proof = ["--task-lease-idempotency-key", "copy-execution",
             "--task-lease-expected-version", str(retained["version"])]
    before = records(registry)
    args = ["--role", "user", "--text", "Reminder: disposition awaits user execution",
            "--note", "Delivery receipt only", "--evidence", "Synthetic delivery receipt",
            "--update-operation-id", "leased-action-copy",
            "--update-expected-provider-revision", acquired["provider_revision"], *proof]
    assert update(registry, *args, "--dry-run")["status"] == "planned"
    assert records(registry) == before
    applied = update(registry, *args)
    assert applied["status"] == "applied" and applied["source_authority"] == f"{provider}_v0"
    after = records(registry)
    assert after["todo_other"] == before["todo_other"]
    assert after["todo_target"]["note"] == "Delivery receipt only"
    assert after["todo_target"]["evidence"] == "Synthetic delivery receipt"
    for field in ("role", "task_class", "status", "done", "created_by", "bound_agent", "claimed_by"):
        assert after["todo_target"].get(field) == before["todo_target"].get(field)
    assert lease("inspect")["lease"] == retained
    assert update(registry, *args)["status"] == "replayed"
    assert update(registry, *args, "--note", "Conflicting retry", ok=False)["reason_code"] == "coordination_operation_identity_mismatch"
    assert update(registry, "--role", "user", "--note", "Stale writer", *proof,
                  "--update-operation-id", "stale-copy",
                  "--update-expected-provider-revision", acquired["provider_revision"], ok=False)["reason_code"] == "provider_revision_mismatch"
    for attempt in (["--agent-id", "agent-b"], ["--status", "blocked"],
                    ["--required-capability", "shell"], ["--bound-agent", "agent-b"],
                    ["--task-lease-idempotency-key", "wrong-key"],
                    ["--task-lease-expected-version", "0"]):
        update(registry, "--role", "user", "--note", "Refused edit", *proof, *attempt, ok=False)
    assert records(registry) == after
    assert lease("inspect")["lease"] == retained
    released = lease("release", "--owner", "agent-a", "--idempotency-key", "copy-execution",
                     "--expected-version", str(retained["version"]))
    rejected = update(registry, "--role", "user", "--note", "Old execution", *proof, ok=False)
    assert rejected["recovery"]["action"] == "acquire_fresh_lease"
    assert rejected["recovery"]["execution_authority_granted"] is False
    assert records(registry) == after
    # Historical success replays but grants no authority for a new operation.
    assert update(registry, *args)["status"] == "replayed"
    fresh = lease("acquire", "--owner", "agent-a", "--idempotency-key", "next-copy-execution",
                  "--expected-version", str(released["lease"]["version"]))
    assert fresh["lease"]["lease_epoch"] == retained["lease_epoch"] + 1
    recovered = update(registry, "--role", "user", "--note", "Recovered metadata",
                       "--update-operation-id", "fresh-action-copy",
                       "--update-expected-provider-revision", fresh["provider_revision"],
                       "--task-lease-idempotency-key", "next-copy-execution",
                       "--task-lease-expected-version", str(fresh["lease"]["version"]))
    assert recovered["status"] == "applied"
    assert lease("inspect")["lease"] == fresh["lease"]
    assert not records(registry)["todo_target"].get("claimed_by")


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_promoted_hard_lease_deferred_todo_resumes_through_real_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    registry, state = deferred_hard_lease_fixture(tmp_path, monkeypatch, provider)

    def acquire(operation_id: str) -> tuple[int, dict]:
        process = subprocess.run([
            sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(registry),
            "task-lease", "acquire", "--goal-id", "goal-a", "--todo-id", "todo_target",
            "--owner", "agent-a", "--idempotency-key", operation_id, "--ttl-seconds", "120",
        ], capture_output=True, text=True, timeout=45)
        return process.returncode, json.loads(process.stdout)

    blocked_code, blocked = acquire(f"before-resume-{provider}")
    assert blocked_code == 1 and blocked["error_code"] == "todo_not_open", blocked

    args = ["--status", "open", "--clear-resume-when",
            "--update-operation-id", f"resume-{provider}"]
    assert update(registry, *args, "--dry-run")["status"] == "planned"
    assert not state.exists()
    accepted = update(registry, *args)
    assert accepted["status"] == "applied"
    assert accepted["source_authority"] == f"{provider}_v0"
    assert records(registry)["todo_target"]["status"] == "open"
    assert records(registry)["todo_target"]["claimed_by"] == "agent-a"
    assert not records(registry)["todo_target"].get("resume_when")
    assert update(registry, *args)["status"] == "replayed"
    acquired_code, acquired = acquire(f"after-resume-{provider}")
    assert acquired_code == 0 and acquired["acquired"] is True, acquired
    assert acquired["source_authority"] == f"{provider}_v0"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_promoted_hard_lease_deferred_todo_supersedes_through_real_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    registry, state = deferred_hard_lease_fixture(tmp_path, monkeypatch, provider)

    def supersede(*args: str) -> tuple[int, dict]:
        process = subprocess.run([
            sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(registry),
            "todo", "supersede", "--goal-id", "goal-a", "--todo-id", "todo_target",
            "--agent-id", "agent-a", "--reason", "Retire a synthetic wait", *args,
        ], capture_output=True, text=True, timeout=45)
        return process.returncode, json.loads(process.stdout)

    planned_code, planned = supersede("--dry-run")
    assert planned_code == 0 and planned["status"] == "planned", planned
    assert not state.exists()
    applied_code, applied = supersede()
    assert applied_code == 0 and applied["status"] == "done", applied
    assert applied["superseded"] is True
    assert applied["source_authority"] == f"{provider}_v0"
    assert records(registry)["todo_target"]["status"] == "done"


@pytest.mark.parametrize("args", [
    ["--status", "done"], ["--status", "deferred"], ["--claimed-by", "unknown-agent"],
    ["--task-class", "continuous_monitor"], ["--status", "blocked", "--agent-id", "agent-b"],
])
def test_promoted_unsupported_or_unauthorized_update_never_falls_back(tmp_path: Path, args: list[str]) -> None:
    registry, state = fixture(tmp_path, True)
    before = records(registry)
    update(registry, *args, ok=False)
    assert records(registry) == before
    assert not state.exists()


@pytest.mark.parametrize("todo_id,role,reason", [
    ("todo_missing", "agent", "Todo is missing from canonical authority"),
    ("todo_target", "user", "Todo does not have the requested role"),
])
def test_native_target_lookup_keeps_public_value_error(tmp_path: Path, todo_id: str, role: str, reason: str) -> None:
    registry, state = fixture(tmp_path, True)
    before = records(registry)
    with pytest.raises(ValueError, match=reason):
        update_goal_todo(registry_path=registry, goal_id="goal-a", todo_id=todo_id,
                         role=role, agent_id="agent-a", text="Correction")
    assert records(registry) == before
    assert not state.exists()
