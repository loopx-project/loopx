import json
from pathlib import Path

import pytest

from canonical_authority_fixture import promoted_create_fixture
from loopx import paths
from loopx.cli import main
from loopx.control_plane.effect_runtime import restart_effect_runtime
from loopx.control_plane.runtime import runtime_projection_route
from loopx.control_plane.runtime.runtime_projection_route import (
    runtime_projection_candidate_roots,
)
from loopx.status import collect_status

DEFAULT_CONFLICT_GOAL = "goal-a"


def _conflicting_default_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Two default roots with machine state, exactly as a migration leaves them."""

    current, legacy = tmp_path / "current", tmp_path / "legacy"
    for root in (current, legacy):
        root.mkdir()
        (root / "machine.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(paths, "DEFAULT_RUNTIME_ROOT", current)
    monkeypatch.setattr(paths, "LEGACY_RUNTIME_ROOT", legacy)
    return current, legacy


def _source_registry(tmp_path: Path) -> Path:
    """A disposable project registry for one Goal with its own runtime root."""

    state = tmp_path / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "---\n"
        f"goal_id: {DEFAULT_CONFLICT_GOAL}\n"
        "handoff_mode: hard_lease\n"
        "---\n\n"
        "## Agent Todo\n\n",
        encoding="utf-8",
    )
    runtime = tmp_path / "project-runtime"
    runtime.mkdir()
    registry = tmp_path / "project" / "registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": DEFAULT_CONFLICT_GOAL,
                        "repo": str(tmp_path),
                        "state_file": state.name,
                        "coordination": {"agent_model": "peer_v1", "registered_agents": ["agent-a"]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return registry


def _declare_target(root: Path, source_registry: Path) -> Path:
    """Write the global registry that names this source registry as the target's author."""

    target = paths.global_registry_path(root)
    source_payload = json.loads(source_registry.read_text(encoding="utf-8"))
    goal = dict(source_payload["goals"][0])
    goal["source_registry"] = str(source_registry.resolve())
    target.write_text(
        json.dumps({"schema_version": 1, "registry_role": "global-local", "goals": [goal]}),
        encoding="utf-8",
    )
    return target


def _configure(registry: Path, capsys) -> tuple[int, dict]:
    code = main(
        [
            "--registry",
            str(registry),
            "--format",
            "json",
            "configure-goal",
            "--goal-id",
            DEFAULT_CONFLICT_GOAL,
            "--quota-window-hours",
            "12",
            "--execute",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out) if captured.out.strip().startswith("{") else {}
    return code, payload


def test_explicit_status_survives_conflicting_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    current, legacy = tmp_path / "current", tmp_path / "legacy"
    for root in (current, legacy):
        root.mkdir()
        (root / "machine.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(paths, "DEFAULT_RUNTIME_ROOT", current)
    monkeypatch.setattr(paths, "LEGACY_RUNTIME_ROOT", legacy)
    registry, runtime, _state = promoted_create_fixture(tmp_path / "fixture")
    try:
        projection = collect_status(
            registry_path=registry,
            runtime_root_override=str(runtime),
            scan_roots=[],
            limit=10,
            goal_id="goal-a",
            include_public_boundary_scan=False,
        )
        assert any(goal["id"] == "goal-a" for goal in projection["run_history"]["goals"])
        assert set(runtime_projection_candidate_roots(source_runtime_root=runtime)) == {
            current, legacy, runtime,
        }
        # Diagnostic discovery never authorizes implicit execution or migration.
        with pytest.raises(ValueError, match="Both default LoopX runtime roots"):
            paths.select_default_runtime_root()
        assert (current / "machine.json").read_text() == "{}"
        assert (legacy / "machine.json").read_text() == "{}"
    finally:
        restart_effect_runtime()


def test_explicit_candidates_do_not_discover_other_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def unexpected_read():
        pytest.fail("Explicit candidate roots must remain isolated")

    monkeypatch.setattr(runtime_projection_route, "default_runtime_route", unexpected_read, raising=False)
    root = tmp_path / "explicit"
    assert runtime_projection_candidate_roots(
        source_runtime_root=root, candidate_roots=[root, root],
    ) == [root]


@pytest.mark.parametrize("missing_path_key", ["json_path", "markdown_path"])
def test_route_projection_with_missing_artifact_is_not_current(
    tmp_path: Path,
    missing_path_key: str,
) -> None:
    runtime = tmp_path / "runtime"
    runs = runtime / "goals" / DEFAULT_CONFLICT_GOAL / "runs"
    runs.mkdir(parents=True)
    json_path = runs / "projection.json"
    markdown_path = runs / "projection.md"
    json_path.write_text("{}\n", encoding="utf-8")
    markdown_path.write_text("# projection\n", encoding="utf-8")
    (runs / "index.jsonl").write_text(
        json.dumps(
            {
                "json_path": str(json_path),
                "markdown_path": str(markdown_path),
                "shared_runtime_projection": {
                    "runtime_projection_route_id": "route-a",
                    "source_generated_at": "2026-10-06T00:00:00Z",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert runtime_projection_route._route_projection_is_current(
        target_runtime_root=runtime,
        goal_id=DEFAULT_CONFLICT_GOAL,
        route_id="route-a",
        source_generated_at="2026-10-06T00:00:00Z",
        marker_field="shared_runtime_projection",
    )

    Path(
        {"json_path": json_path, "markdown_path": markdown_path}[missing_path_key]
    ).unlink()

    assert not runtime_projection_route._route_projection_is_current(
        target_runtime_root=runtime,
        goal_id=DEFAULT_CONFLICT_GOAL,
        route_id="route-a",
        source_generated_at="2026-10-06T00:00:00Z",
        marker_field="shared_runtime_projection",
    )


def test_invalid_default_root_is_not_treated_as_a_conflict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    current = tmp_path / "invalid"
    current.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(paths, "DEFAULT_RUNTIME_ROOT", current)
    monkeypatch.setattr(paths, "LEGACY_RUNTIME_ROOT", tmp_path / "absent")
    explicit = tmp_path / "explicit"
    # Discovery stays a candidate read: an invalid default root is inspected, not
    # selected. Implicit selection keeps refusing it, so the read-only path cannot
    # hide a broken route from the commands that must not guess.
    assert runtime_projection_candidate_roots(source_runtime_root=explicit) == [
        current.resolve(),
        (tmp_path / "absent").resolve(),
        explicit.resolve(),
    ]
    with pytest.raises(ValueError, match="linked or not a directory"):
        paths.select_default_runtime_root()
    assert current.read_text(encoding="utf-8") == "not a directory"


def test_configure_goal_execute_syncs_the_single_declared_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
):
    current, legacy = _conflicting_default_roots(tmp_path, monkeypatch)
    registry = _source_registry(tmp_path)
    target = _declare_target(legacy, registry)

    code, payload = _configure(registry, capsys)

    assert code == 0, json.dumps(payload, indent=1)
    assert payload.get("ok") is True
    sync = payload.get("global_sync") or {}
    resolution = sync.get("target_resolution") or {}
    assert resolution.get("target_global_registry") == str(target), json.dumps(sync, indent=1)
    assert resolution.get("declaration_source") == "target_registry.goal.source_registry"
    assert (resolution.get("route") or {}).get("status") == "resolved"
    configured = json.loads(registry.read_text(encoding="utf-8"))["goals"][0]
    assert configured["quota"]["window_hours"] == 12
    # The conflicting root stays an untouched candidate, not a second writer.
    assert not paths.global_registry_path(current).exists()
    assert (current / "machine.json").read_text(encoding="utf-8") == "{}"


def test_configure_goal_execute_refuses_ambiguous_declared_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
):
    current, legacy = _conflicting_default_roots(tmp_path, monkeypatch)
    registry = _source_registry(tmp_path)
    _declare_target(legacy, registry)
    _declare_target(current, registry)
    before = registry.read_bytes()

    code, payload = _configure(registry, capsys)

    assert code != 0
    assert "ambiguous" in json.dumps(payload)
    assert registry.read_bytes() == before
