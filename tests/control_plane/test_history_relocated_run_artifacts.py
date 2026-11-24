"""Copied goal runs stay observable without rewriting stored artifact paths.

A runtime copy leaves absolute ``json_path`` and ``markdown_path`` values that
name the old tree. History, attention, duplicate inspection, and doctor
promotion readiness must treat the same-named regular files in the current
runs directory as present. Symlinks, other goals, and non-regular names do not.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from loopx.control_plane.runtime.promotion_readiness import (
    PROMOTION_READINESS_CLASSIFICATION,
    PROMOTION_READINESS_RUNTIME_INDEX,
)
from loopx.doctor import collect_doctor, latest_promotion_readiness_event
from loopx.history import collect_history, inspect_index_duplicates, load_index
from loopx.status import goal_attention


GOAL_ID = "history-relocated-run"
GENERATED_AT = "2026-03-01T12:00:00+00:00"
REPAIR_ACTION = "repair or regenerate the latest run artifacts before trusting status"


def _goal() -> dict[str, Any]:
    return {
        "id": GOAL_ID,
        "adapter": {
            "kind": "read_only_project_map_v0",
            "status": "connected-read-only",
        },
    }


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _row(
    json_path: Path,
    markdown_path: Path,
    *,
    classification: str = "state_refreshed",
    generated_at: str = GENERATED_AT,
    **extra: Any,
) -> dict[str, Any]:
    row = {
        "generated_at": generated_at,
        "goal_id": GOAL_ID,
        "classification": classification,
        "recommended_action": "Inspect the current deliverable.",
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
    }
    row.update(extra)
    return row


def _write_index(runs_dir: Path, rows: list[dict[str, Any]]) -> Path:
    index_path = runs_dir / "index.jsonl"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    return index_path


def _copy_runs(runtime_a: Path, runtime_b: Path) -> tuple[Path, bytes]:
    source = runtime_a / "goals" / GOAL_ID / "runs"
    target = runtime_b / "goals" / GOAL_ID / "runs"
    shutil.copytree(source, target)
    return target, (target / "index.jsonl").read_bytes()


def _history(tmp_path: Path, runtime: Path) -> dict[str, Any]:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps({"goals": [_goal()]}), encoding="utf-8")
    return collect_history(
        registry_path=registry_path,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        limit=10,
    )


def test_copied_runtime_observes_artifacts_and_does_not_demand_repair(
    tmp_path: Path,
) -> None:
    runtime_a = tmp_path / "runtime-a"
    runtime_b = tmp_path / "runtime-b"
    runs_a = runtime_a / "goals" / GOAL_ID / "runs"
    json_path = runs_a / "2026-03-01T12-00-00Z-state.json"
    markdown_path = runs_a / "2026-03-01T12-00-00Z-state.md"
    _write(json_path, '{"classification":"state_refreshed"}\n')
    _write(markdown_path, "# State refreshed\n")
    row = _row(json_path, markdown_path)
    _write_index(runs_a, [row, row])
    runs_b, index_bytes = _copy_runs(runtime_a, runtime_b)
    shutil.rmtree(runtime_a)
    assert not json_path.exists()
    assert not markdown_path.exists()
    assert (runs_b / json_path.name).is_file()
    assert (runs_b / markdown_path.name).is_file()

    history = _history(tmp_path, runtime_b)
    run = history["goals"][0]["latest_status_run"]
    assert run["json_path"] == str(json_path)
    assert run["markdown_path"] == str(markdown_path)
    assert run["json_exists"] is True
    assert run["markdown_exists"] is True
    assert history["runs"][0]["json_exists"] is True
    assert history["runs"][0]["markdown_exists"] is True

    attention = goal_attention(history["goals"][0])
    assert attention is not None
    assert attention["status"] == "state_refreshed"
    assert attention["recommended_action"] == "Inspect the current deliverable."
    assert attention["recommended_action"] != REPAIR_ACTION

    loaded, _raw_count = load_index(runs_b / "index.jsonl")
    assert loaded[0]["json_exists"] is True
    assert loaded[0]["markdown_exists"] is True

    registry_path = tmp_path / "registry.json"
    duplicates = inspect_index_duplicates(
        registry_path=registry_path,
        runtime_root_override=str(runtime_b),
        goal_id=GOAL_ID,
        limit=10,
    )
    group = duplicates["groups"][0]
    assert group["json_path"] == str(json_path)
    assert group["markdown_path"] == str(markdown_path)
    assert group["json_exists"] is True
    assert group["markdown_exists"] is True
    assert (runs_b / "index.jsonl").read_bytes() == index_bytes


def test_doctor_legacy_goal_history_observes_copied_promotion_artifacts(
    tmp_path: Path,
) -> None:
    runtime_a = tmp_path / "runtime-a"
    runtime_b = tmp_path / "runtime-b"
    runs_a = runtime_a / "goals" / GOAL_ID / "runs"
    json_path = runs_a / "2026-03-01T12-00-00Z-readiness.json"
    markdown_path = runs_a / "2026-03-01T12-00-00Z-readiness.md"
    _write(json_path, '{"classification":"canary"}\n')
    _write(markdown_path, "# Readiness\n")
    _write_index(
        runs_a,
        [
            _row(
                json_path,
                markdown_path,
                classification=PROMOTION_READINESS_CLASSIFICATION,
                recommended_action="Readiness evidence is in the copied artifacts.",
            )
        ],
    )
    runs_b, index_bytes = _copy_runs(runtime_a, runtime_b)
    shutil.rmtree(runtime_a)

    event = latest_promotion_readiness_event(runtime_b, GOAL_ID)
    assert event["available"] is True
    assert event["source"] == "goal_run_history_legacy"
    assert event["goal_id"] == GOAL_ID
    assert event["json_exists"] is True
    assert event["markdown_exists"] is True
    assert (runs_b / "index.jsonl").read_bytes() == index_bytes
    assert not (runtime_b / PROMOTION_READINESS_RUNTIME_INDEX).exists()


def test_runtime_release_ledger_remaps_only_same_goal_run_files(
    tmp_path: Path,
) -> None:
    runtime_b = tmp_path / "runtime-b"
    old_runs = tmp_path / "runtime-a" / "goals" / GOAL_ID / "runs"
    goal_json = old_runs / "readiness.json"
    goal_markdown = old_runs / "readiness.md"
    release_dir = tmp_path / "runtime-a" / "release" / "promotion-readiness" / "runs"
    release_json = release_dir / "readiness.json"
    release_markdown = release_dir / "readiness.md"
    runs_b = runtime_b / "goals" / GOAL_ID / "runs"
    _write(runs_b / goal_json.name, "{}\n")
    _write(runs_b / goal_markdown.name, "# Goal\n")

    matched = _row(
        goal_json,
        goal_markdown,
        classification=PROMOTION_READINESS_CLASSIFICATION,
        generated_at="2026-03-01T12:00:00+00:00",
    )
    unrelated = _row(
        release_json,
        release_markdown,
        classification=PROMOTION_READINESS_CLASSIFICATION,
        generated_at="2026-03-01T11:00:00+00:00",
        goal_id=GOAL_ID,
    )
    index_path = runtime_b / PROMOTION_READINESS_RUNTIME_INDEX
    _write(
        index_path,
        "".join(json.dumps(row) + "\n" for row in (unrelated, matched)),
    )

    event = latest_promotion_readiness_event(runtime_b)
    assert event["source"] == "runtime_release_ledger"
    assert event["generated_at"] == "2026-03-01T12:00:00+00:00"
    assert event["json_exists"] is True
    assert event["markdown_exists"] is True
    assert (runs_b / goal_json.name).is_file()

    # The older release-ledger row is not a goal-run path. A same-named file
    # under goals/<id>/runs must not satisfy it. Drop the newer row and read it.
    index_path.write_text(json.dumps(unrelated) + "\n", encoding="utf-8")
    older = latest_promotion_readiness_event(runtime_b)
    assert older["generated_at"] == "2026-03-01T11:00:00+00:00"
    assert older["json_exists"] is False
    assert older["markdown_exists"] is False


def test_relocated_observation_rejects_unrelated_substitutes(tmp_path: Path) -> None:
    runtime_b = tmp_path / "runtime-b"
    runs_b = runtime_b / "goals" / GOAL_ID / "runs"
    runs_b.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    _write(outside, "{}\n")
    _write(outside.with_suffix(".md"), "# Outside\n")

    def observe(stored_json: Path, stored_markdown: Path) -> dict[str, Any]:
        _write_index(runs_b, [_row(stored_json, stored_markdown)])
        history = _history(tmp_path, runtime_b)
        return history["goals"][0]["latest_status_run"]

    missing_json = tmp_path / "runtime-a" / "goals" / GOAL_ID / "runs" / "missing.json"
    missing_markdown = missing_json.with_suffix(".md")
    missing = observe(missing_json, missing_markdown)
    assert missing["json_exists"] is False
    assert missing["markdown_exists"] is False
    attention = goal_attention(_history(tmp_path, runtime_b)["goals"][0])
    assert attention is not None
    assert attention["status"] == "run_artifact_missing"
    assert attention["recommended_action"] == REPAIR_ACTION

    other_json = tmp_path / "runtime-a" / "goals" / "other-goal" / "runs" / "foreign.json"
    _write(runs_b / "foreign.json", "{}\n")
    _write(runs_b / "foreign.md", "# Foreign\n")
    foreign = observe(other_json, other_json.with_suffix(".md"))
    assert foreign["json_exists"] is False
    assert foreign["markdown_exists"] is False

    traversed = (
        tmp_path / "runtime-a" / "goals" / GOAL_ID / "runs" / ".." / "secret.json"
    )
    _write(runs_b / "secret.json", "{}\n")
    _write(runs_b / "secret.md", "# Secret\n")
    escaped = observe(traversed, traversed.with_suffix(".md"))
    assert escaped["json_exists"] is False
    assert escaped["markdown_exists"] is False

    outside_target = tmp_path / "symlink-target.json"
    _write(outside_target, "{}\n")
    _write(outside_target.with_suffix(".md"), "# Linked\n")
    link_json = runs_b / "linked.json"
    link_markdown = runs_b / "linked.md"
    link_json.symlink_to(outside_target)
    link_markdown.symlink_to(outside_target.with_suffix(".md"))
    stored_link = tmp_path / "runtime-a" / "goals" / GOAL_ID / "runs" / "linked.json"
    linked = observe(stored_link, stored_link.with_suffix(".md"))
    assert linked["json_exists"] is False
    assert linked["markdown_exists"] is False
    assert link_json.is_symlink()

    directory = runs_b / "directory.json"
    directory.mkdir()
    (runs_b / "directory.md").mkdir()
    stored_dir = tmp_path / "runtime-a" / "goals" / GOAL_ID / "runs" / "directory.json"
    non_regular = observe(stored_dir, stored_dir.with_suffix(".md"))
    assert non_regular["json_exists"] is False
    assert non_regular["markdown_exists"] is False

    local_json = runs_b / "local.json"
    local_markdown = runs_b / "local.md"
    _write(local_json, "{}\n")
    _write(local_markdown, "# Local\n")
    local = observe(local_json, local_markdown)
    assert local["json_exists"] is True
    assert local["markdown_exists"] is True

    loose_index = tmp_path / "loose" / "index.jsonl"
    decoy_json = runs_b / "decoy.json"
    decoy_markdown = runs_b / "decoy.md"
    _write(decoy_json, "{}\n")
    _write(decoy_markdown, "# Decoy\n")
    stored_decoy = tmp_path / "runtime-a" / "goals" / GOAL_ID / "runs" / "decoy.json"
    _write_index(
        loose_index.parent,
        [_row(stored_decoy, stored_decoy.with_suffix(".md"))],
    )
    loose, _raw_count = load_index(loose_index)
    assert loose[0]["json_exists"] is False
    assert loose[0]["markdown_exists"] is False


def test_unexpandable_account_path_stays_missing_and_doctor_continues(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    runs = runtime / "goals" / GOAL_ID / "runs"
    copied_name = "copied-readiness"
    _write(runs / f"{copied_name}.json", "{}\n")
    _write(runs / f"{copied_name}.md", "# Copied\n")
    _write(runs / "old-readiness.json", "{}\n")
    _write(runs / "old-readiness.md", "# Old name only\n")
    stale_json = Path(
        f"~nosuchuser_loopx/goals/{GOAL_ID}/runs/old-readiness.json"
    )
    stale_markdown = Path(
        f"~nosuchuser_loopx/goals/{GOAL_ID}/runs/old-readiness.md"
    )
    stored_json = tmp_path / "runtime-a" / "goals" / GOAL_ID / "runs" / f"{copied_name}.json"
    stored_markdown = stored_json.with_suffix(".md")
    relative_json = Path("artifacts/missing.json")
    relative_markdown = Path("artifacts/missing.md")
    stale = _row(
        stale_json,
        stale_markdown,
        generated_at="2026-03-01T10:00:00+00:00",
    )
    copied = _row(
        stored_json,
        stored_markdown,
        generated_at="2026-03-01T12:00:00+00:00",
    )
    relative = _row(
        relative_json,
        relative_markdown,
        generated_at="2026-03-01T11:00:00+00:00",
    )
    index_path = _write_index(runs, [stale, relative, copied])
    index_bytes = index_path.read_bytes()

    history = _history(tmp_path, runtime)
    by_time = {run["generated_at"]: run for run in history["goals"][0]["latest_runs"]}
    assert by_time[stale["generated_at"]]["json_exists"] is False
    assert by_time[stale["generated_at"]]["markdown_exists"] is False
    assert by_time[relative["generated_at"]]["json_exists"] is False
    assert by_time[relative["generated_at"]]["markdown_exists"] is False
    assert by_time[copied["generated_at"]]["json_exists"] is True
    assert by_time[copied["generated_at"]]["markdown_exists"] is True
    attention = goal_attention(history["goals"][0])
    assert attention is not None
    assert attention["status"] == "state_refreshed"
    assert index_path.read_bytes() == index_bytes

    release_stale = {
        **stale,
        "classification": PROMOTION_READINESS_CLASSIFICATION,
    }
    release_copied = {
        **copied,
        "classification": PROMOTION_READINESS_CLASSIFICATION,
        "recommended_action": "Readiness evidence is in the copied artifacts.",
    }
    release_index = runtime / PROMOTION_READINESS_RUNTIME_INDEX
    _write(
        release_index,
        "".join(json.dumps(row) + "\n" for row in (release_stale, release_copied)),
    )
    release_bytes = release_index.read_bytes()
    event = latest_promotion_readiness_event(runtime)
    assert event["source"] == "runtime_release_ledger"
    assert event["generated_at"] == release_copied["generated_at"]
    assert event["json_exists"] is True
    assert event["markdown_exists"] is True

    release_index.write_text(json.dumps(release_stale) + "\n", encoding="utf-8")
    stale_only = latest_promotion_readiness_event(runtime)
    assert stale_only["generated_at"] == release_stale["generated_at"]
    assert stale_only["json_exists"] is False
    assert stale_only["markdown_exists"] is False
    assert index_path.read_bytes() == index_bytes

    release_index.write_bytes(release_bytes)
    payload = collect_doctor(
        registry_path=tmp_path / "registry.json",
        runtime_root_override=str(runtime),
    )
    readiness = payload["release_provenance"]["promotion_readiness"]
    assert readiness["json_exists"] is True
    assert readiness["markdown_exists"] is True
    assert release_index.read_bytes() == release_bytes


def _windows_cross_volume_relpath(old_root: Path, new_root: Path):
    """Raise the ``ntpath.relpath`` cross-drive ``ValueError`` for old vs new."""

    real = os.path.relpath
    old_text = str(old_root)
    new_text = str(new_root)

    def relpath(path: object, start: object | None = None) -> str:
        path_text = os.fspath(path)  # type: ignore[arg-type]
        start_text = os.fspath(start if start is not None else os.curdir)  # type: ignore[arg-type]
        if old_text in path_text and new_text in start_text:
            raise ValueError("path is on mount 'C:', start on mount 'D:'")
        return real(path, start)  # type: ignore[arg-type]

    return relpath


@pytest.mark.parametrize("copy_present", [True, False])
def test_windows_cross_volume_relocation_does_not_abort_history_or_doctor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    copy_present: bool,
) -> None:
    runtime_a = tmp_path / "runtime-a"
    runtime_b = tmp_path / "runtime-b"
    runs_a = runtime_a / "goals" / GOAL_ID / "runs"
    json_path = runs_a / "2026-03-01T12-00-00Z-state.json"
    markdown_path = runs_a / "2026-03-01T12-00-00Z-state.md"
    _write(json_path, '{"classification":"state_refreshed"}\n')
    _write(markdown_path, "# State refreshed\n")
    readiness_json = runs_a / "2026-03-01T12-00-00Z-readiness.json"
    readiness_markdown = runs_a / "2026-03-01T12-00-00Z-readiness.md"
    _write(readiness_json, "{}\n")
    _write(readiness_markdown, "# Readiness\n")
    _write_index(
        runs_a,
        [
            _row(json_path, markdown_path),
            _row(
                readiness_json,
                readiness_markdown,
                classification=PROMOTION_READINESS_CLASSIFICATION,
                recommended_action="Readiness evidence is in the copied artifacts.",
            ),
        ],
    )
    runs_b, index_bytes = _copy_runs(runtime_a, runtime_b)
    if not copy_present:
        (runs_b / json_path.name).unlink()
        (runs_b / markdown_path.name).unlink()
        (runs_b / readiness_json.name).unlink()
        (runs_b / readiness_markdown.name).unlink()
    shutil.rmtree(runtime_a)
    monkeypatch.setattr(
        "loopx.control_plane.quota.ledger_readback.os.path.relpath",
        _windows_cross_volume_relpath(runtime_a, runtime_b),
    )

    history = _history(tmp_path, runtime_b)
    run = history["goals"][0]["latest_status_run"]
    assert run["json_exists"] is copy_present
    assert run["markdown_exists"] is copy_present
    attention = goal_attention(history["goals"][0])
    assert attention is not None
    if copy_present:
        assert attention["status"] == "state_refreshed"
    else:
        assert attention["status"] == "run_artifact_missing"

    event = latest_promotion_readiness_event(runtime_b, GOAL_ID)
    assert event["source"] == "goal_run_history_legacy"
    assert event["json_exists"] is copy_present
    assert event["markdown_exists"] is copy_present
    assert (runs_b / "index.jsonl").read_bytes() == index_bytes
