"""Explicit authoring scope outranks actor-based defaults, never authority."""
from pathlib import Path

import pytest

from loopx.control_plane.testing.canary_harness import run_json_cli_result, write_fixture_registry
from loopx.control_plane.todos.active_state_editing import find_todo_block
from loopx.todos import add_goal_todo, add_todo_to_lines, update_goal_todo
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection

GOAL = "authoring-scope"


@pytest.fixture
def authored_goal(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    state = project / "ACTIVE_GOAL_STATE.md"
    state.write_text("# Goal\n\n## Agent Todo\n\n## User Todo\n", encoding="utf-8")
    registry = tmp_path / "registry.json"
    write_fixture_registry(project=project, runtime_root=tmp_path / "runtime",
        registry_path=registry, goal_id=GOAL, domain="scope-test",
        adapter_kind="generic_project_goal_v0", registered_agents=["agent-a", "agent-b"],
        quota_allowed_slots=None)
    return registry, state


def add(registry, state, **intent):
    return add_goal_todo(registry_path=registry, goal_id=GOAL, state_file=state,
        role="user", text="Decide the next step", **intent)


def stored(state: Path, todo_id):
    return find_todo_block(state.read_text(encoding="utf-8").splitlines(), todo_id=todo_id)[4]


@pytest.mark.parametrize("intent, bound, goal, blocks, global_", [
    ({"task_class": "user_action", "agent_id": "agent-a"}, "agent-a", False, None, False),
    ({"task_class": "user_action", "agent_id": "agent-a", "goal_bound": True}, None, True, None, False),
    ({"task_class": "user_gate", "agent_id": "agent-a"}, "agent-a", False, "agent-a", False),
    ({"task_class": "user_gate", "agent_id": "agent-a", "blocks_agent": "agent-b"}, "agent-b", False, "agent-b", False),
    ({"task_class": "user_gate", "agent_id": "agent-a", "global_gate": True}, None, True, None, True),
])
def test_add_persists_declared_scope_not_the_author_identity(authored_goal, intent, bound, goal, blocks, global_):
    registry, state = authored_goal
    result = add(registry, state, **intent)
    todo = stored(state, result["todo_id"])
    assert todo.get("bound_agent") == bound
    assert bool(todo.get("goal_bound")) == goal
    assert todo.get("blocks_agent") == blocks
    assert bool(todo.get("global_gate")) == global_


@pytest.mark.parametrize("intent", [
    {"global_gate": True, "bound_agent": "agent-a"},
    {"blocks_agent": "agent-a", "bound_agent": "agent-b"},
    {"blocks_agent": "agent-a", "goal_bound": True},
])
def test_update_rejects_explicit_scope_conflicts_without_writing(authored_goal, intent):
    registry, state = authored_goal
    result = add(registry, state, task_class="user_gate", global_gate=True, goal_bound=True)
    before = state.read_bytes()
    with pytest.raises(ValueError):
        update_goal_todo(registry_path=registry, goal_id=GOAL, state_file=state,
            todo_id=result["todo_id"], agent_id="agent-a", role="user",
            clear_global_gate=not intent.get("global_gate", False), **intent)
    assert state.read_bytes() == before


def test_real_cli_requires_explicit_global_flag_and_preserves_dry_run(authored_goal):
    registry, state = authored_goal
    common = ("todo", "add", "--goal-id", GOAL, "--role", "user", "--task-class", "user_gate",
        "--text", "Decide whole-goal policy", "--state-file", str(state))
    before = state.read_bytes()
    code, payload = run_json_cli_result(*common, "--goal-bound", registry_path=registry)
    assert code != 0
    assert state.read_bytes() == before
    code, preview = run_json_cli_result(*common, "--global-gate", "--agent-id", "agent-a", "--dry-run", registry_path=registry)
    assert code == 0, preview
    assert state.read_bytes() == before
    code, written = run_json_cli_result(*common, "--global-gate", "--agent-id", "agent-a", registry_path=registry)
    assert code == 0, written
    todo_id = written["todo_id"]
    assert stored(state, todo_id)["global_gate"] is True
    code, edited = run_json_cli_result("todo", "update", "--goal-id", GOAL, "--todo-id", todo_id,
        "--role", "user", "--agent-id", "agent-a", "--clear-global-gate", "--blocks-agent", "agent-b",
        "--state-file", str(state), registry_path=registry)
    assert code == 0, edited
    assert stored(state, todo_id)["bound_agent"] == "agent-b"
    assert not stored(state, todo_id).get("global_gate")


def test_missing_scope_never_creates_global_gate(authored_goal):
    registry, state = authored_goal
    before = state.read_bytes()
    for intent in ({}, {"goal_bound": True}):
        with pytest.raises(ValueError, match="scope|binding"):
            add(registry, state, task_class="user_gate", **intent)
        assert state.read_bytes() == before


@pytest.fixture(params=["unpromoted", "file", "sqlite"])
def execution_exclusion_goal(authored_goal, tmp_path, monkeypatch, request):
    registry, state = authored_goal
    if request.param != "unpromoted":
        if request.param == "sqlite":
            isolate_sqlite_runtime(tmp_path, monkeypatch)
        projection = build_todo_runtime_shadow_projection(
            goal_id=GOAL, handoff_mode="soft_claim", todos=[]
        )
        initialize_canonical_authority(
            tmp_path / "runtime", GOAL, projection,
            state_path=state, provider=request.param,
        )

    def cli(*args):
        return run_json_cli_result(
            "todo", *args, "--goal-id", GOAL, "--state-file", str(state),
            registry_path=registry,
        )

    return registry, state, cli


def test_execution_exclusion_registration_and_claim_conflict_preserve_source(execution_exclusion_goal):
    _, state, cli = execution_exclusion_goal
    create = ("add", "--role", "agent", "--text", "Review the current change",
              "--task-class", "advancement_task",
              "--claimed-by", "\u001cAGENT\u0085A\u001f")
    before = state.read_bytes()
    for excluded in ("unknown-agent", "agent-a", "invalid/token"):
        code, rejected = cli(*create, "--excluded-agent", excluded)
        assert code != 0, rejected
        assert state.read_bytes() == before
        assert cli("list")[1]["todo_count"] == 0

    code, created = cli(*create, "--excluded-agent", "\u001cAGENT\u0085B\u001f", "--excluded-agent", "agent-b")
    assert code == 0, created
    todo_id = created["todo_id"]
    code, listed = cli("list", "--todo-id", todo_id)
    assert code == 0, listed
    assert listed["todo"]["excluded_agents"] == ["agent-b"]
    assert listed["todo"]["claimed_by"] == "agent-a"

    before = state.read_bytes()
    for excluded in ("unknown-agent", "agent-a", "invalid/token"):
        code, rejected = cli("update", "--todo-id", todo_id, "--agent-id", "agent-a",
                             "--excluded-agent", excluded)
        assert code != 0, rejected
        assert state.read_bytes() == before
        assert cli("list", "--todo-id", todo_id)[1]["todo"] == listed["todo"]

    code, preview = cli("update", "--todo-id", todo_id, "--agent-id", "agent-a",
                        "--clear-excluded-agents", "--dry-run")
    assert code == 0, preview
    assert state.read_bytes() == before
    code, cleared = cli("update", "--todo-id", todo_id, "--agent-id", "agent-a",
                        "--clear-excluded-agents")
    assert code == 0, cleared
    row = cli("list", "--todo-id", todo_id)[1]["todo"]
    assert not row.get("excluded_agents")
    assert row["claimed_by"] == "agent-a"


def test_execution_exclusion_owner_unavailable_does_not_write(execution_exclusion_goal, monkeypatch):
    registry, state, cli = execution_exclusion_goal
    from loopx.control_plane.todos import authoring_scope
    from loopx.control_plane.effect_runtime import EffectRuntimeStartupError

    effect = authoring_scope.effect_runtime_result

    def unavailable(method, params, **kwargs):
        if method == "todo.creation_scope.plan":
            raise EffectRuntimeStartupError(
                "isolated authoring owner unavailable", diagnostic_code="node_unavailable"
            )
        return effect(method, params, **kwargs)

    monkeypatch.setattr(authoring_scope, "effect_runtime_result", unavailable)
    before = state.read_bytes()
    with pytest.raises(EffectRuntimeStartupError):
        add_goal_todo(registry_path=registry, goal_id=GOAL, state_file=state,
                      role="agent", text="Review the current change",
                      task_class="advancement_task",
                      claimed_by="agent-a", excluded_agents=["agent-b"])
    assert state.read_bytes() == before
    assert cli("list")[1]["todo_count"] == 0


@pytest.mark.parametrize("role, task_class, flags, field", [
    ("agent", "advancement_task", ("--global-gate",), "global_gate"),
    ("agent", "advancement_task", ("--blocks-agent", "agent-b"), "blocks_agent"),
    ("user", "user_action", ("--claimed-by", "agent-a"), "claimed_by"),
    ("user", "user_action", ("--task-repository", "git:github.com/example/project"), "task_repository"),
    ("user", "user_action", ("--task-domain", "scope-test"), "task_domain"),
    ("user", "user_action", ("--capability-binding-ref", "binding-a"), "capability_binding_ref"),
    ("agent", "advancement_task", ("--status", "done"), "todo add cannot create completed work"),
])
def test_create_role_restrictions_refuse_before_source_write(
    execution_exclusion_goal, role, task_class, flags, field,
):
    _, state, cli = execution_exclusion_goal
    before = state.read_bytes()
    scope = ("--bound-agent", "agent-a") if role == "user" else ()
    code, rejected = cli("add", "--role", role, "--task-class", task_class,
                         "--text", "Review the current change", *scope, *flags)
    assert code != 0, rejected
    assert field in rejected["error"], rejected
    assert state.read_bytes() == before
    code, listed = cli("list")
    assert code == 0, listed
    assert listed["todo_count"] == 0


@pytest.mark.parametrize("role, task_class, error", [
    ("user", None, "user todo requires explicit --task-class"),
    ("agent", "user_gate", "user_action and user_gate task_class are only valid for --role user"),
])
def test_standalone_markdown_codec_keeps_class_admission(role, task_class, error):
    lines = ["# Goal", "", "## Agent Todo", "", "## User Todo", ""]
    before = list(lines)
    with pytest.raises(ValueError, match=error):
        add_todo_to_lines(lines, role=role, task_class=task_class, text="Decide the next step")
    assert lines == before


@pytest.mark.parametrize("role, task_class, flags, error", [
    ("user", None, ("--bound-agent", "agent-a"), "user todo requires explicit --task-class"),
    ("user", "advancement_task", ("--bound-agent", "agent-a"), "user todo requires explicit --task-class"),
    ("user", "user_action", ("--blocks-agent", "agent-a"), "user_action is non-blocking"),
    ("user", "user_action", ("--global-gate",), "user_action is non-blocking"),
    ("agent", "user_gate", (), "user_action and user_gate task_class are only valid for --role user"),
])
def test_create_class_rules_reject_before_any_provider_write(
    execution_exclusion_goal, role, task_class, flags, error,
):
    _, state, cli = execution_exclusion_goal
    before = state.read_bytes()
    code, initial = cli("list")
    assert code == 0, initial
    declaration = ("--task-class", task_class) if task_class else ()
    code, rejected = cli("add", "--role", role, "--text", "Decide the next step",
                         *declaration, *flags)
    assert code != 0, rejected
    assert error in rejected["error"], rejected
    assert state.read_bytes() == before
    code, listed = cli("list")
    assert code == 0, listed
    assert listed["todo_count"] == 0
    assert listed.get("authority_read") == initial.get("authority_read")
