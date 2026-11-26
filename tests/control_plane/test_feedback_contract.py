"""Contract, zero-Any, MappingProxyType, and O(1) run-selection tests for loopx.feedback."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import MappingProxyType
import pytest

from loopx import feedback
from loopx import history
from loopx.control_plane.runtime import reward_rendering


def test_feedback_zero_any_budgets_and_exports() -> None:
    for rel, max_loc in (
        ("loopx/feedback.py", 550),
        ("loopx/control_plane/runtime/reward_rendering.py", 750),
        ("tests/control_plane/test_feedback_contract.py", 400),
    ):
        src = (Path(__file__).resolve().parents[2] / rel).read_text(encoding="utf-8")
        assert len(src.splitlines()) <= max_loc, rel
        tree = ast.parse(src)
        anys = [n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == "Any"]
        assert anys == [], rel
        assert ("type: " + "ignore") not in src, rel
        assert ("no" + "qa") not in src, rel

    reexported = (
        "REWARD_VALUES",
        "LESSON_KINDS",
        "now_utc",
        "now_local",
        "public_safe_text_guidance",
        "validate_public_safe_text",
        "compact_reward",
        "compact_reward_lesson",
        "build_reward_coordination",
        "build_active_state_entry",
        "update_frontmatter_timestamp",
        "insert_progress_ledger_entry",
        "describe_active_state_effect",
        "describe_run_overlay_effect",
        "render_reward_markdown",
    )
    listed_dir = set(dir(feedback))
    for name in reexported:
        assert name in feedback.__all__, name
        assert name in listed_dir, name
        assert getattr(feedback, name) is getattr(reward_rendering, name), name

    with pytest.raises(AttributeError):
        getattr(feedback, "nonexistent_feedback_symbol")


def test_mapping_proxy_and_text_validation_contracts(tmp_path: Path) -> None:
    feedback.validate_local_control_text("recommended_action", "Inspect task-123 locally")
    secret_header = "Author" + "ization: demo"
    with pytest.raises(ValueError, match="secret-looking value"):
        feedback.validate_local_control_text("recommended_action", secret_header)
    bearer_secret = "Bear" + "er " + "a" * 24
    with pytest.raises(ValueError, match="private-looking value"):
        feedback.validate_public_safe_text("recommended_action", bearer_secret)

    lesson = MappingProxyType(
        {
            "kind": "route",
            "summary": "Prefer narrow verification before broad runs.",
            "avoid": "unscoped retries",
            "prefer": ["exact selector", "exact selector", " "],
            "target_surface": ["status_server", "cli"],
            "constraint": MappingProxyType({"lane": "reward", "empty": ""}),
        }
    )
    compacted = feedback.compact_reward(
        recorded_at="2026-10-01T00:00:00Z",
        decision="continue",
        reward="positive",
        reason_summary="Verified targeted route",
        follow_up=None,
        lesson=lesson,
    )
    assert compacted["decision"] == "continue"
    compacted_lesson = compacted["lesson"]
    assert isinstance(compacted_lesson, dict)
    assert compacted_lesson["avoid"] == ["unscoped retries"]
    assert compacted_lesson["prefer"] == ["exact selector"]

    project = tmp_path / "project"
    project.mkdir()
    registry = MappingProxyType(
        {"goals": ({"id": "g-1", "repo": str(project), "state_file": "ACTIVE.md"},)}
    )
    registry_with_list = MappingProxyType(
        {"goals": [{"id": "g-1", "repo": str(project), "state_file": "ACTIVE.md"}]}
    )
    assert feedback.find_registry_goal(registry, "g-1") is None
    found = feedback.find_registry_goal(registry_with_list, "g-1")
    assert found is not None and found["id"] == "g-1"
    assert (
        feedback.resolve_reward_state_file(
            registry=registry_with_list, goal_id="g-1", state_file_override=None
        )
        == project / "ACTIVE.md"
    )

    runs = (
        MappingProxyType({"generated_at": "2026-10-01T00:00:00Z", "classification": "c1"}),
        MappingProxyType({"generated_at": "2026-10-02T00:00:00Z", "classification": "c2"}),
    )
    assert feedback.select_run(runs, None)["classification"] == "c2"

    coordination = feedback.build_reward_coordination(
        goal_id="g-1",
        selected_run=MappingProxyType({"generated_at": "2026-10-02T00:00:00Z"}),
        reward=MappingProxyType(
            {
                "reward": "positive",
                "decision": "continue",
                "reason_summary": "Looks good",
                "lesson": lesson,
            }
        ),
        dry_run=True,
    )
    rendered = feedback.render_reward_markdown(
        MappingProxyType(
            {
                "ok": True,
                "goal_id": "g-1",
                "index_path": "goals/g-1/runs/index.jsonl",
                "appended": False,
                "dry_run": True,
                "selected_run": {"generated_at": "2026-10-02T00:00:00Z", "classification": "c2"},
                "human_reward": {
                    "recorded_at": "2026-10-02T00:05:00Z",
                    "actor_kind": "owner",
                    "decision": "continue",
                    "reward": "positive",
                    "reason_summary": "Looks good",
                    "lesson": lesson,
                },
                "active_state_summary": coordination["active_state_summary"],
                "project_agent_visibility": coordination["project_agent_visibility"],
            }
        )
    )
    assert "lesson_kind: `route`" in rendered
    assert "lesson_prefer: exact selector" in rendered


def test_append_human_reward_observes_only_selected_run_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "runtime"
    project = tmp_path / "project"
    project.mkdir()
    registry_path = project / ".loopx" / "registry.json"
    registry_path.parent.mkdir()
    registry_path.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime),
                "goals": [{"id": "g-perf", "repo": str(project)}],
            }
        ),
        encoding="utf-8",
    )
    runs_dir = runtime / "goals" / "g-perf" / "runs"
    runs_dir.mkdir(parents=True)
    selected_json = runs_dir / "run-24.json"
    selected_md = runs_dir / "run-24.md"
    selected_json.write_text('{"ok": true}\n', encoding="utf-8")
    selected_md.write_text("# Run 24\n", encoding="utf-8")
    rows = [
        {
            "generated_at": f"2026-10-01T00:00:{i:02d}Z",
            "classification": f"run-{i:02d}",
            "json_path": str(runs_dir / f"run-{i:02d}.json"),
            "markdown_path": str(runs_dir / f"run-{i:02d}.md"),
        }
        for i in range(25)
    ]
    index_path = runs_dir / "index.jsonl"
    index_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    observed_batches: list[list[str]] = []
    original_observe = history._observe_run_artifacts

    def spy_observe(
        records: list[dict[str, object]],
        *,
        artifact_root: Path | None,
        runs_dir: Path | None = None,
        goal_id: str | None = None,
    ) -> None:
        observed_batches.append([str(row.get("classification")) for row in records])
        original_observe(
            records,
            artifact_root=artifact_root,
            runs_dir=runs_dir,
            goal_id=goal_id,
        )

    monkeypatch.setattr(history, "_observe_run_artifacts", spy_observe)

    result = feedback.append_human_reward(
        registry_path=registry_path,
        runtime_root_override=None,
        goal_id="g-perf",
        run_generated_at=None,
        reward={
            "recorded_at": "2026-10-02T00:00:00Z",
            "decision": "continue",
            "reward": "positive",
            "reason_summary": "Verified single-run artifact stat",
        },
        dry_run=True,
    )
    assert observed_batches == [["run-24"]]
    assert result["raw_index_records_before"] == 25
    assert result["selected_run"] == {
        "generated_at": "2026-10-01T00:00:24Z",
        "classification": "run-24",
        "recommended_action": None,
        "json_exists": True,
        "markdown_exists": True,
    }


def test_append_human_reward_error_boundaries_and_patched_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="reward must be one of"):
        feedback.compact_reward(
            recorded_at=None,
            decision="continue",
            reward="invalid",
            reason_summary="ok",
            follow_up=None,
        )
    with pytest.raises(ValueError, match="lesson kind"):
        feedback.compact_reward_lesson({"kind": "bad", "summary": "x"})
    with pytest.raises(ValueError, match="lesson summary"):
        feedback.compact_reward_lesson({"kind": "route", "summary": "   "})
    with pytest.raises(ValueError, match="must be iterable"):
        feedback.compact_reward_lesson({"kind": "route", "summary": "x", "avoid": 123})
    with pytest.raises(ValueError, match="no compact run records"):
        feedback.select_run([], None)
    with pytest.raises(ValueError, match="run not found"):
        feedback.select_run([{"generated_at": "2026-10-01T00:00:00Z"}], "missing")

    runtime = tmp_path / "runtime"
    project = tmp_path / "project"
    project.mkdir()
    state_file = project / "ACTIVE.md"
    state_file.write_text("## Progress Ledger\n", encoding="utf-8")
    registry_path = project / ".loopx" / "registry.json"
    registry_path.parent.mkdir()
    registry_path.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": "g-err",
                        "repo": str(project),
                        "state_file": "ACTIVE.md",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        feedback,
        "load_index",
        lambda _path: (
            [
                {
                    "generated_at": "2026-10-01T00:00:00Z",
                    "classification": "c1",
                    "json_path": "r.json",
                    "markdown_path": "r.md",
                }
            ],
            1,
        ),
    )

    def fail_write(_path: Path, _content: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(feedback, "atomic_write_state_text", fail_write)
    with pytest.raises(feedback.HumanRewardSummaryWriteError, match="disk full"):
        feedback.append_human_reward(
            registry_path=registry_path,
            runtime_root_override=None,
            goal_id="g-err",
            run_generated_at=None,
            reward={
                "recorded_at": "2026-10-01T01:00:00Z",
                "decision": "continue",
                "reward": "positive",
                "reason_summary": "Accept",
            },
            actor_kind="owner",
            write_active_state_summary=True,
        )
