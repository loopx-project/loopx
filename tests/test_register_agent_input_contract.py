from __future__ import annotations

import json
from pathlib import Path

from loopx.cli_commands import registry_admin

from tests.source_cli import run_source_cli

GOAL_ID = "register-agent-input-contract"


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _fixture(tmp_path: Path, *, legacy: bool = False) -> tuple[Path, Path, Path]:
    runtime_root = tmp_path / "runtime"
    project = tmp_path / "project"
    source_registry = project / ".loopx" / "registry.json"
    state_file = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text("# Active Goal State\n\n## Agent Todo\n\n", encoding="utf-8")
    goal = {
        "id": GOAL_ID,
        "domain": "register-agent-input",
        "status": "active",
        "repo": str(project),
        "state_file": f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md",
        "adapter": {"kind": "fixture", "status": "connected-read-only"},
        "source_registry": str(source_registry),
    }
    if not legacy:
        goal["coordination"] = {
            "write_scope": [],
            "claim_ttl_minutes": 30,
            "requires_parent_approval": ["write", "publish", "production-action"],
            "registered_agents": ["codex-existing"],
            "agent_model": "peer_v1",
        }
    payload: dict[str, object] = {
        "schema_version": "0.1",
        "common_runtime_root": str(runtime_root),
        "goals": [goal],
    }
    _write_json(source_registry, payload)
    global_registry = runtime_root / "registry.global.json"
    _write_json(global_registry, {**payload, "registry_role": "global-local"})
    return runtime_root, source_registry, global_registry


def _registered_agents(path: Path) -> list[str]:
    return json.loads(path.read_text(encoding="utf-8"))["goals"][0]["coordination"][
        "registered_agents"
    ]


def test_repeated_and_comma_separated_ids_register_together(tmp_path: Path) -> None:
    runtime_root, source_registry, global_registry = _fixture(tmp_path)

    result = registry_admin.register_agent_via_source_registry(
        runtime_root_arg=str(runtime_root),
        goal_id=GOAL_ID,
        agent_ids=["peer-one, peer-two"],
        execute=True,
    )

    assert result["ok"] is True, result
    assert result["written"] is True
    assert result["changed"] is True
    assert result["requested_agents"] == ["peer-one", "peer-two"]
    assert result["registration_readback"]["verified"] is True
    assert _registered_agents(source_registry) == [
        "codex-existing",
        "peer-one",
        "peer-two",
    ]
    assert _registered_agents(global_registry) == _registered_agents(source_registry)


def test_one_rejected_id_fails_the_batch_before_any_write(tmp_path: Path) -> None:
    runtime_root, source_registry, global_registry = _fixture(tmp_path)

    result = registry_admin.register_agent_via_source_registry(
        runtime_root_arg=str(runtime_root),
        goal_id=GOAL_ID,
        agent_ids=["peer-one", "代理甲"],
        execute=True,
    )

    assert result["ok"] is False, result
    assert result["error_kind"] == "agent_identity_invalid"
    assert result["rejected_agents"] == ["代理甲"]
    assert "代理甲" in str(result["error"])
    assert result["changed"] is False
    assert result["written"] is False
    assert result["requested_agents"] == ["peer-one"]
    assert _registered_agents(source_registry) == ["codex-existing"]
    assert _registered_agents(global_registry) == ["codex-existing"]


def test_require_new_guard_cannot_pass_vacuously_on_a_rejected_id(
    tmp_path: Path,
) -> None:
    runtime_root, _, _ = _fixture(tmp_path)

    result = registry_admin.register_agent_via_source_registry(
        runtime_root_arg=str(runtime_root),
        goal_id=GOAL_ID,
        agent_ids=["代理甲"],
        require_new=True,
        execute=False,
    )

    assert result["ok"] is False, result
    assert result["error_kind"] == "agent_identity_invalid"
    assert result["written"] is False


def test_empty_delimiters_are_skipped_but_never_registered(tmp_path: Path) -> None:
    runtime_root, source_registry, _ = _fixture(tmp_path)

    result = registry_admin.register_agent_via_source_registry(
        runtime_root_arg=str(runtime_root),
        goal_id=GOAL_ID,
        agent_ids=["peer-one,"],
        execute=True,
    )

    assert result["ok"] is True, result
    assert result["requested_agents"] == ["peer-one"]
    assert _registered_agents(source_registry) == ["codex-existing", "peer-one"]


def test_empty_request_is_rejected_before_any_write(tmp_path: Path) -> None:
    runtime_root, source_registry, global_registry = _fixture(tmp_path)
    source_before = source_registry.read_bytes()
    global_before = global_registry.read_bytes()

    for execute in (False, True):
        result = registry_admin.register_agent_via_source_registry(
            runtime_root_arg=str(runtime_root),
            goal_id=GOAL_ID,
            agent_ids=[",", "   "],
            require_new=True,
            execute=execute,
        )

        assert result["ok"] is False, (execute, result)
        assert result["error_kind"] == "agent_identity_empty"
        assert result["requested_agents"] == []
        assert result["rejected_agents"] == []
        assert result["changed"] is False
        assert result["written"] is False

    assert _registered_agents(source_registry) == ["codex-existing"]
    assert _registered_agents(global_registry) == ["codex-existing"]
    assert source_registry.read_bytes() == source_before
    assert global_registry.read_bytes() == global_before


def test_empty_request_on_a_legacy_goal_writes_nothing(tmp_path: Path) -> None:
    runtime_root, source_registry, global_registry = _fixture(tmp_path, legacy=True)
    source_before = source_registry.read_bytes()
    global_before = global_registry.read_bytes()

    for execute in (False, True):
        result = registry_admin.register_agent_via_source_registry(
            runtime_root_arg=str(runtime_root),
            goal_id=GOAL_ID,
            agent_ids=[","],
            require_new=True,
            execute=execute,
        )

        assert result["ok"] is False, (execute, result)
        assert result["error_kind"] == "agent_identity_empty"
        assert result["changed"] is False
        assert result["written"] is False

    for path in (source_registry, global_registry):
        goal = json.loads(path.read_text(encoding="utf-8"))["goals"][0]
        assert "coordination" not in goal
    assert source_registry.read_bytes() == source_before
    assert global_registry.read_bytes() == global_before


def test_cli_rejects_a_delimiter_only_request_and_exits_nonzero(
    tmp_path: Path,
) -> None:
    runtime_root, source_registry, global_registry = _fixture(tmp_path, legacy=True)

    result = run_source_cli(
        [
            "--runtime-root", str(runtime_root),
            "--registry", str(source_registry),
            "--format", "json",
            "register-agent",
            "--goal-id", GOAL_ID,
            "--agent-id", ",",
            "--require-new",
            "--execute",
        ],
        check=False,
    )

    assert result.returncode == 1, (result.stdout, result.stderr)
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error_kind"] == "agent_identity_empty"
    assert payload["requested_agents"] == []
    assert payload["changed"] is False
    assert payload["written"] is False
    for path in (source_registry, global_registry):
        goal = json.loads(path.read_text(encoding="utf-8"))["goals"][0]
        assert "coordination" not in goal


def test_cli_reports_the_rejected_id_and_exits_nonzero(tmp_path: Path) -> None:
    runtime_root, source_registry, _ = _fixture(tmp_path)

    result = run_source_cli(
        [
            "--runtime-root", str(runtime_root),
            "--registry", str(source_registry),
            "--format", "json",
            "register-agent",
            "--goal-id", GOAL_ID,
            "--agent-id", "peer-one",
            "--agent-id", "代理甲",
            "--execute",
        ],
        check=False,
    )

    assert result.returncode == 1, (result.stdout, result.stderr)
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error_kind"] == "agent_identity_invalid"
    assert payload["rejected_agents"] == ["代理甲"]
    assert payload["written"] is False
    assert _registered_agents(source_registry) == ["codex-existing"]
