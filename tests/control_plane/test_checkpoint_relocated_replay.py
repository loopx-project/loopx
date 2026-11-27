"""Public CLI replay returns the verified artifacts from the selected runtime."""
from __future__ import annotations

import errno
import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
import shutil

import pytest

from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.managed_effect_runtime_fixture import stop_disposable_effect_runtimes
from tests.control_plane.test_checkpoint_read_context import _missing
from tests.control_plane.test_quota_settlement_cli import GOAL_ID, _run_cli, _spend_run_count


@pytest.fixture(autouse=True)
def _retire_owned_runtime(tmp_path: Path):
    yield
    stop_disposable_effect_runtimes(tmp_path)


def _historical_records(runtime: Path) -> dict[str, bytes]:
    goal = runtime / "goals" / GOAL_ID
    paths = [
        *sorted((goal / "runs").glob("*.json")),
        *sorted((goal / "runs").glob("*.md")),
        *sorted((goal / "runs").glob("*.jsonl")),
        *sorted((goal / "checkpoint-contexts").glob("*.json")),
    ]
    events = goal / "rollout-event-log.jsonl"
    if events.exists():
        paths.append(events)
    return {str(path.relative_to(goal)): path.read_bytes() for path in paths}


def _relocated_checkpoint(tmp_path: Path, monkeypatch, provider: str):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry, binding, delivery, original = _missing(tmp_path, provider=provider)
    rc, context = _run_cli(registry, runtime, "checkpoint-context", *binding, cwd=project)
    assert rc == 0, context
    replay_args = (
        *delivery,
        "--vision-unchanged-reason", "The accepted scope and evidence remain applicable.",
        "--checkpoint-read-context", context["read_context_id"],
    )
    rc, committed = _run_cli(registry, runtime, *replay_args, cwd=project)
    assert rc == 0 and committed["appended"] is True, committed
    assert committed["vision_checkpoint"]["satisfied"] is True
    assert committed["vision_checkpoint"]["read_context"]["ok"] is True
    assert committed["settlement_identity"] == original["settlement_identity"]
    relocated = tmp_path / "copied-runtime"
    # Copy a completed disposable runtime without rewriting historical paths,
    # provider lineage, receipts, checkpoint proofs or index rows.
    shutil.copytree(runtime, relocated, symlinks=True)
    return project, runtime, relocated, registry, replay_args, committed


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_public_checkpoint_replay_returns_relocated_artifacts_without_rewriting_history(
    tmp_path: Path, monkeypatch, provider: str,
):
    project, runtime, relocated, registry, replay_args, committed = _relocated_checkpoint(
        tmp_path, monkeypatch, provider,
    )
    original_records = _historical_records(runtime)
    copied_records = _historical_records(relocated)
    assert copied_records == original_records
    for dry_run in (True, False, False):
        rc, replay = _run_cli(
            registry, relocated, *replay_args, *(("--dry-run",) if dry_run else ()), cwd=project,
        )
        assert rc == 0 and replay["ok"] is True, replay
        assert replay["dry_run"] is dry_run
        assert replay["appended"] is False and replay["idempotent_replay"] is True
        assert replay["settlement_identity"] == committed["settlement_identity"]
        assert replay["vision_checkpoint"] == committed["vision_checkpoint"]
        assert replay["generated_at"] == committed["generated_at"]
        for field in ("json_path", "markdown_path"):
            original_path = Path(committed[field])
            expected = (relocated / "goals" / GOAL_ID / "runs" / original_path.name).resolve()
            assert Path(replay[field]) == expected
            assert expected != original_path.resolve()
            assert expected.read_bytes() == original_path.read_bytes()
        # The selected index and artifact still record their historical basis.
        assert _historical_records(relocated) == copied_records
        assert _historical_records(runtime) == original_records
        assert _spend_run_count(relocated) == _spend_run_count(runtime) == 0
        assert json.loads(Path(replay["json_path"]).read_text())["vision_checkpoint"] == committed["vision_checkpoint"]


def _hardlink_or_skip(existing: Path, path: Path) -> None:
    try:
        os.link(existing, path)
    except OSError as exc:
        if exc.errno in {errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV, errno.ENOSYS}:
            pytest.skip(f"hard links are unavailable on this platform: {exc}")
        raise


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("field", ["json_path", "markdown_path"])
@pytest.mark.parametrize("failure", ["missing", "symlink", "hardlink"])
def test_public_checkpoint_replay_rejects_missing_or_linked_relocated_artifacts(
    tmp_path: Path, monkeypatch, provider: str, field: str, failure: str,
):
    project, runtime, relocated, registry, replay_args, committed = _relocated_checkpoint(
        tmp_path, monkeypatch, provider,
    )
    original_records = _historical_records(runtime)
    original_path = Path(committed[field])
    original_bytes = original_path.read_bytes()
    candidate = relocated / "goals" / GOAL_ID / "runs" / original_path.name
    candidate.unlink()
    if failure == "symlink":
        candidate.symlink_to(original_path)
    elif failure == "hardlink":
        _hardlink_or_skip(original_path, candidate)
    copied_records = _historical_records(relocated)
    rc, rejected = _run_cli(registry, relocated, *replay_args, cwd=project)
    assert rc == 1 and rejected["ok"] is False, rejected
    assert rejected["error_code"] == "checkpoint_commit_unknown", rejected
    assert "checkpoint artifact path is invalid" in rejected["error"]
    assert rejected["checkpoint_read_context"]["reread_required"] is False
    assert _historical_records(relocated) == copied_records
    assert _historical_records(runtime) == original_records
    assert original_path.read_bytes() == original_bytes
    assert _spend_run_count(relocated) == _spend_run_count(runtime) == 0
    if failure == "symlink":
        assert candidate.is_symlink() and candidate.resolve() == original_path.resolve()
    elif failure == "hardlink":
        assert not candidate.is_symlink()
        assert candidate.stat().st_ino == original_path.stat().st_ino
        assert candidate.stat().st_nlink >= 2
    else:
        assert not candidate.exists()


def _run_cli_inprocess(registry: Path, runtime: Path, *args: str, cwd: Path) -> tuple[int, dict]:
    from loopx.cli import main as cli_main

    argv = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json", *args]
    buffer = io.StringIO()
    previous = Path.cwd()
    os.chdir(cwd)
    try:
        with redirect_stdout(buffer):
            rc = cli_main(argv)
    finally:
        os.chdir(previous)
    return rc, json.loads(buffer.getvalue())


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_public_checkpoint_commit_does_not_truncate_hard_linked_artifacts(
    tmp_path: Path, monkeypatch, provider: str,
):
    """Plant the commit targets as hard links so writeSynced would share an outside inode."""
    from loopx import state_refresh
    from loopx.history import reserve_unique_run_paths as reserve_real

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry, binding, delivery, _original = _missing(tmp_path, provider=provider)
    rc, context = _run_cli(registry, runtime, "checkpoint-context", *binding, cwd=project)
    assert rc == 0, context
    replay_args = (
        *delivery,
        "--vision-unchanged-reason", "The accepted scope and evidence remain applicable.",
        "--checkpoint-read-context", context["read_context_id"],
    )
    outside_json = tmp_path / "outside-checkpoint.json"
    outside_markdown = tmp_path / "outside-checkpoint.md"
    sentinel_json = b'{"planted":"outside-json"}\n'
    sentinel_markdown = b"planted outside markdown\n"
    outside_json.write_bytes(sentinel_json)
    outside_markdown.write_bytes(sentinel_markdown)
    index_path = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    index_before = index_path.read_bytes()
    planted: dict[str, Path] = {}

    def reserve_hardlinked(runs_dir: Path, generated_at: str) -> tuple[Path, Path]:
        json_path, markdown_path = reserve_real(runs_dir, generated_at)
        json_path.unlink()
        if markdown_path.exists():
            markdown_path.unlink()
        _hardlink_or_skip(outside_json, json_path)
        _hardlink_or_skip(outside_markdown, markdown_path)
        planted["json"] = json_path
        planted["markdown"] = markdown_path
        return json_path, markdown_path

    monkeypatch.setattr(state_refresh, "reserve_unique_run_paths", reserve_hardlinked)
    rc, rejected = _run_cli_inprocess(registry, runtime, *replay_args, cwd=project)
    assert outside_json.read_bytes() == sentinel_json
    assert outside_markdown.read_bytes() == sentinel_markdown
    assert index_path.read_bytes() == index_before
    assert planted["json"].stat().st_ino == outside_json.stat().st_ino
    assert planted["markdown"].stat().st_ino == outside_markdown.stat().st_ino
    assert planted["json"].stat().st_nlink >= 2
    assert planted["markdown"].stat().st_nlink >= 2
    assert rc == 1 and rejected["ok"] is False, rejected
    assert rejected["error_code"] == "checkpoint_commit_unknown", rejected
    assert "checkpoint artifact is not a regular file" in rejected["error"]
