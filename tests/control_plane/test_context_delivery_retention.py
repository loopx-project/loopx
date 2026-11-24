"""Bounded opportunistic retention tests for context-delivery directories."""
from __future__ import annotations

import argparse
import ast
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from loopx.cli_commands.context_delivery_retention import (
    CONTEXT_DELIVERY_EPHEMERAL_PREFIXES,
    DEFAULT_KEEP_RECENT_PER_PREFIX,
    DEFAULT_MAX_DELETE_PER_SWEEP,
    DEFAULT_MIN_AGE_SECONDS,
    DEFAULT_MIN_SWEEP_INTERVAL_SECONDS,
    RETENTION_SWEEP_MARKER_NAME,
    maybe_prune_context_delivery_root,
)
from loopx.cli_commands.quota_capture import (
    budgeted_settlement_result,
    prepare_decision_capture,
    progressive_context_capture_view,
)
from loopx.cli_commands.quota_result_delivery import emit_quota_result

if TYPE_CHECKING:
    from loopx.cli_commands.quota_context import QuotaCommandContext

ROOT = Path(__file__).resolve().parents[2]


def _write_owned_json(
    path: Path, payload: dict[str, object], *, mtime: float
) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path.write_bytes(raw)
    os.utime(path, (mtime, mtime))
    return sha256(raw).hexdigest()


def _make_dir(parent: Path, name: str, *, mtime: float) -> Path:
    path = parent / name
    path.mkdir(parents=True, exist_ok=True)
    if name.startswith("decision-"):
        target = path / "decision.json"
        digest = _write_owned_json(target, {"ok": True, "decision": "wait"}, mtime=mtime)
        _write_owned_json(
            path / "decision.reader.json",
            {
                "schema_version": "context_capture_read_reference_v1",
                "path": str(target),
                "sha256": digest,
            },
            mtime=mtime,
        )
    elif name.startswith("recovery-"):
        _write_owned_json(
            path / "required-context.json",
            {"ok": True, "summary": "recovery"},
            mtime=mtime,
        )
    elif name.startswith("settlement-"):
        target = path / "result.json"
        digest = _write_owned_json(
            target, {"ok": True, "summary": "settled"}, mtime=mtime
        )
        _write_owned_json(
            path / "result.reader.json",
            {
                "schema_version": "context_capture_read_reference_v1",
                "path": str(target),
                "sha256": digest,
            },
            mtime=mtime,
        )
    else:
        target = path / "projection.json"
        digest = _write_owned_json(
            target, {"ok": True, "summary": "context"}, mtime=mtime
        )
        _write_owned_json(
            path / "projection.reader.json",
            {
                "schema_version": "context_capture_read_reference_v1",
                "path": str(target),
                "sha256": digest,
            },
            mtime=mtime,
        )
    os.utime(path, (mtime, mtime))
    return path


def test_retention_module_uses_zero_any_and_exact_defaults() -> None:
    source_path = ROOT / "loopx" / "cli_commands" / "context_delivery_retention.py"
    source = source_path.read_text(encoding="utf-8")
    assert "type: ignore" not in source
    tree = ast.parse(source)
    any_names = [
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == "Any"
    ]
    any_attrs = [
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "Any"
    ]
    assert any_names == []
    assert any_attrs == []
    assert CONTEXT_DELIVERY_EPHEMERAL_PREFIXES == (
        "decision-",
        "recovery-",
        "settlement-",
        "context-",
    )
    assert DEFAULT_MIN_AGE_SECONDS == 86400.0
    assert DEFAULT_KEEP_RECENT_PER_PREFIX == 64
    assert DEFAULT_MAX_DELETE_PER_SWEEP == 128
    assert DEFAULT_MIN_SWEEP_INTERVAL_SECONDS == 300.0


def test_age_floor_and_per_prefix_keep_floor_hold_together(tmp_path: Path) -> None:
    root = tmp_path / "context-delivery"
    root.mkdir(mode=0o700)
    now = 2_000_000.0
    old_base = now - 100_000.0
    young_base = now - 300.0

    # 70 old + 5 young decision-* -> newest 64 retained (5 young + 59 newest old), 11 oldest pruned.
    for index in range(70):
        _make_dir(root, f"decision-old-{index:03d}", mtime=old_base + index)
    for index in range(5):
        _make_dir(root, f"decision-young-{index:03d}", mtime=young_base + index)

    # 66 old recovery-* -> newest 64 retained, 2 oldest pruned.
    for index in range(66):
        _make_dir(root, f"recovery-old-{index:03d}", mtime=old_base + index)

    # 10 old settlement-* -> below keep_recent_per_prefix=64, so all 10 retained.
    for index in range(10):
        _make_dir(root, f"settlement-old-{index:03d}", mtime=old_base + index)

    # 80 young context-* -> above keep floor, but younger than min_age_seconds=86400, so all 80 retained.
    for index in range(80):
        _make_dir(root, f"context-young-{index:03d}", mtime=young_base + index)

    result = maybe_prune_context_delivery_root(root, now=now)
    assert result.swept is True
    assert result.skipped_reason is None
    assert result.scanned == 231
    assert result.deleted == 13
    assert result.failed == 0

    for index in range(11):
        assert not (root / f"decision-old-{index:03d}").exists()
    for index in range(11, 70):
        assert (root / f"decision-old-{index:03d}").exists()
    for index in range(5):
        assert (root / f"decision-young-{index:03d}").exists()
    for index in range(2):
        assert not (root / f"recovery-old-{index:03d}").exists()
    for index in range(2, 66):
        assert (root / f"recovery-old-{index:03d}").exists()
    for index in range(10):
        assert (root / f"settlement-old-{index:03d}").exists()
    for index in range(80):
        assert (root / f"context-young-{index:03d}").exists()


def test_max_delete_per_sweep_bounds_backlog_oldest_first(tmp_path: Path) -> None:
    root = tmp_path / "context-delivery"
    root.mkdir(mode=0o700)
    now = 3_000_000.0
    old_base = now - 200_000.0

    # 54 dirs per prefix * 4 prefixes = 216 dirs; keep_recent=4 leaves 200 eligible oldest-first.
    ordered_eligible: list[str] = []
    tick = 0
    for round_index in range(54):
        for prefix in CONTEXT_DELIVERY_EPHEMERAL_PREFIXES:
            name = f"{prefix}{round_index:03d}"
            _make_dir(root, name, mtime=old_base + tick)
            if round_index < 50:
                ordered_eligible.append(name)
            tick += 1

    first = maybe_prune_context_delivery_root(
        root,
        keep_recent_per_prefix=4,
        max_delete_per_sweep=128,
        min_sweep_interval_seconds=300.0,
        now=now,
    )
    assert first.swept is True
    assert first.deleted == 128
    assert first.failed == 0

    for name in ordered_eligible[:128]:
        assert not (root / name).exists()
    for name in ordered_eligible[128:]:
        assert (root / name).exists()

    # Second sweep after interval clears remaining 72 oldest while retaining the 16 recent dirs.
    second = maybe_prune_context_delivery_root(
        root,
        keep_recent_per_prefix=4,
        max_delete_per_sweep=128,
        min_sweep_interval_seconds=300.0,
        now=now + 301.0,
    )
    assert second.swept is True
    assert second.deleted == 72
    assert second.failed == 0

    remaining_prefixes = sorted(
        entry.name
        for entry in root.iterdir()
        if entry.name != RETENTION_SWEEP_MARKER_NAME
    )
    assert len(remaining_prefixes) == 16
    for prefix in CONTEXT_DELIVERY_EPHEMERAL_PREFIXES:
        for round_index in range(50, 54):
            assert (root / f"{prefix}{round_index:03d}").exists()


def test_symlinks_hash_dirs_and_unexpected_files_are_untouched(tmp_path: Path) -> None:
    outside_dir = tmp_path / "outside-target"
    outside_dir.mkdir(mode=0o700)
    outside_keep = outside_dir / "keep.txt"
    outside_keep.write_text("preserved\n", encoding="utf-8")

    root = tmp_path / "context-delivery"
    root.mkdir(mode=0o700)
    now = 4_000_000.0
    very_old = now - 500_000.0

    # Protected non-ephemeral or non-directory entries inside root.
    hash_dir = _make_dir(root, "a1b2c3d4e5f60718293a4b5c6d7e8f90", mtime=very_old)
    other_dir = _make_dir(root, "custom-preserved", mtime=very_old)
    prefix_file = root / "decision-regular-file.json"
    prefix_file.write_text("keep-file\n", encoding="utf-8")

    dir_symlink = root / "decision-symlink-dir"
    dir_symlink.symlink_to(outside_dir, target_is_directory=True)
    file_symlink = root / "context-symlink-file"
    file_symlink.symlink_to(outside_keep)

    # Unowned directories with prefix names (unexpected files, empty dir, foreign reader ref, nested symlink)
    # must fail closed and stay untouched; only valid owned LoopX captures are deleted.
    unowned_notes = root / "context-notes"
    unowned_notes.mkdir(mode=0o700)
    (unowned_notes / "notes.md").write_text("personal notes\n", encoding="utf-8")
    os.utime(unowned_notes, (very_old, very_old))

    unowned_payload = root / "decision-unowned-payload"
    unowned_payload.mkdir(mode=0o700)
    (unowned_payload / "payload.json").write_text('{"ok":true}\n', encoding="utf-8")
    os.utime(unowned_payload, (very_old, very_old))

    empty_context = root / "context-empty"
    empty_context.mkdir(mode=0o700)
    os.utime(empty_context, (very_old, very_old))

    foreign_reader = root / "settlement-foreign-reader"
    foreign_reader.mkdir(mode=0o700)
    _write_owned_json(foreign_reader / "result.json", {"ok": True}, mtime=very_old)
    _write_owned_json(
        foreign_reader / "result.reader.json",
        {
            "schema_version": "context_capture_read_reference_v1",
            "path": str(outside_keep),
            "sha256": "0" * 64,
        },
        mtime=very_old,
    )
    os.utime(foreign_reader, (very_old, very_old))

    nested_link_dir = _make_dir(root, "recovery-with-nested-symlink", mtime=very_old)
    (nested_link_dir / "nested-link").symlink_to(outside_dir, target_is_directory=True)
    os.utime(nested_link_dir, (very_old, very_old))

    valid_expired = _make_dir(root, "recovery-expired-owned", mtime=very_old)

    result = maybe_prune_context_delivery_root(
        root,
        min_age_seconds=60.0,
        keep_recent_per_prefix=0,
        now=now,
    )
    assert result.swept is True
    assert result.scanned == 1
    assert result.deleted == 1
    assert result.failed == 0
    assert not valid_expired.exists()
    assert nested_link_dir.exists()
    assert unowned_notes.exists() and (unowned_notes / "notes.md").exists()
    assert unowned_payload.exists()
    assert empty_context.exists()
    assert foreign_reader.exists()
    assert outside_keep.read_text(encoding="utf-8") == "preserved\n"
    assert hash_dir.is_dir()
    assert other_dir.is_dir()
    assert prefix_file.is_file()
    assert dir_symlink.is_symlink()
    assert file_symlink.is_symlink()

    # Symlinked root and symlinked marker both fail closed without touching target.
    symlink_root = tmp_path / "context-delivery-link"
    symlink_root.symlink_to(outside_dir, target_is_directory=True)
    root_res = maybe_prune_context_delivery_root(
        symlink_root, min_age_seconds=0.0, keep_recent_per_prefix=0, now=now
    )
    assert root_res.swept is False
    assert root_res.skipped_reason == "invalid_root"

    unsafe_root = tmp_path / "unsafe-marker-root"
    unsafe_root.mkdir(mode=0o700)
    (unsafe_root / RETENTION_SWEEP_MARKER_NAME).symlink_to(outside_keep)
    _make_dir(unsafe_root, "decision-expired", mtime=very_old)
    marker_res = maybe_prune_context_delivery_root(
        unsafe_root, min_age_seconds=0.0, keep_recent_per_prefix=0, now=now
    )
    assert marker_res.swept is False
    assert marker_res.skipped_reason == "unsafe_marker"
    assert (unsafe_root / "decision-expired").exists()
    assert outside_keep.read_text(encoding="utf-8") == "preserved\n"


def test_throttled_fast_skip_under_2ms_on_1000_dir_root_and_env_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "context-delivery"
    root.mkdir(mode=0o700)
    now = 5_000_000.0
    old_base = now - 200_000.0

    for index in range(1000):
        prefix = CONTEXT_DELIVERY_EPHEMERAL_PREFIXES[index % 4]
        _make_dir(root, f"{prefix}{index:04d}", mtime=old_base + index)

    initial = maybe_prune_context_delivery_root(root, now=now)
    assert initial.swept is True
    assert initial.scanned == 1000
    assert initial.deleted == 128
    assert initial.failed == 0

    timings: list[float] = []
    for offset in range(1, 21):
        started = time.perf_counter()
        skipped = maybe_prune_context_delivery_root(root, now=now + offset)
        elapsed = time.perf_counter() - started
        assert skipped.swept is False
        assert skipped.skipped_reason == "throttled"
        assert skipped.scanned == 0
        timings.append(elapsed)

    # Marker lstat throttle must avoid directory scans and skip in <2ms per call.
    avg_seconds = sum(timings) / len(timings)
    assert avg_seconds < 0.002, f"throttled fast-skip too slow: {avg_seconds:.6f}s"

    # Explicit invalid parameters reject fast; malformed env vars fall back safely.
    with pytest.raises(ValueError):
        maybe_prune_context_delivery_root(root, min_age_seconds=-1.0)
    with pytest.raises(ValueError):
        maybe_prune_context_delivery_root(root, keep_recent_per_prefix=-1)
    with pytest.raises(ValueError):
        maybe_prune_context_delivery_root(root, keep_recent_per_prefix=True)
    with pytest.raises(ValueError):
        maybe_prune_context_delivery_root(root, min_sweep_interval_seconds=float("nan"))

    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY_MIN_AGE_SECONDS", "not-a-number")
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY_KEEP_RECENT", "-9")
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY_MAX_DELETE", "4")
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY_SWEEP_INTERVAL_SECONDS", "10")
    env_res = maybe_prune_context_delivery_root(root, now=now + 12.0)
    assert env_res.swept is True
    assert env_res.deleted == 4


def test_failsafe_on_missing_root_permission_error_and_concurrent_deletion_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = maybe_prune_context_delivery_root(tmp_path / "does-not-exist")
    assert missing.swept is False
    assert missing.skipped_reason == "missing_or_unreadable"

    root = tmp_path / "context-delivery"
    root.mkdir(mode=0o700)
    now = 6_000_000.0
    very_old = now - 200_000.0
    race_dir = _make_dir(root, "decision-race-000", mtime=very_old)
    perm_dir = _make_dir(root, "decision-perm-001", mtime=very_old + 1)
    good_dir = _make_dir(root, "decision-good-002", mtime=very_old + 2)

    real_rmtree = shutil.rmtree

    def flaky_rmtree(path: Path) -> None:
        if path == race_dir:
            real_rmtree(path)
            raise FileNotFoundError(str(path))
        if path == perm_dir:
            raise PermissionError(str(path))
        real_rmtree(path)

    monkeypatch.setattr(shutil, "rmtree", flaky_rmtree)
    res = maybe_prune_context_delivery_root(
        root, min_age_seconds=60.0, keep_recent_per_prefix=0, now=now
    )
    assert res.swept is True
    assert res.deleted == 1
    assert res.failed == 1
    assert not race_dir.exists()
    assert perm_dir.exists()
    assert not good_dir.exists()
    monkeypatch.undo()

    # Concurrent multi-thread sweep on overlapping candidates completes without raising.
    race_root = tmp_path / "concurrent-root"
    race_root.mkdir(mode=0o700)
    for index in range(32):
        _make_dir(race_root, f"context-race-{index:03d}", mtime=very_old + index)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(
                maybe_prune_context_delivery_root,
                race_root,
                min_age_seconds=60.0,
                keep_recent_per_prefix=2,
                min_sweep_interval_seconds=0.0,
                now=now,
            )
            for _ in range(4)
        ]
        results = [fut.result() for fut in futures]
    assert any(item.swept for item in results)
    assert all(
        item.swept or item.skipped_reason == "sweep_in_progress" for item in results
    )
    assert all(item.failed == 0 for item in results)
    remaining_dirs = sorted(
        p.name for p in race_root.iterdir() if p.name.startswith("context-race-")
    )
    assert remaining_dirs == ["context-race-030", "context-race-031"]

    if sys.platform != "win32":
        import fcntl
        import os

        locked_root = tmp_path / "locked-root"
        locked_root.mkdir(mode=0o700)
        stale_locked = _make_dir(locked_root, "decision-locked", mtime=very_old)
        marker_fd = os.open(
            locked_root / RETENTION_SWEEP_MARKER_NAME,
            os.O_RDWR | os.O_CREAT,
            0o600,
        )
        try:
            fcntl.flock(marker_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            contended = maybe_prune_context_delivery_root(
                locked_root,
                min_age_seconds=0.0,
                keep_recent_per_prefix=0,
                min_sweep_interval_seconds=0.0,
                now=now,
            )
            assert contended.swept is False
            assert contended.skipped_reason == "sweep_in_progress"
            assert stale_locked.exists()
        finally:
            fcntl.flock(marker_fd, fcntl.LOCK_UN)
            os.close(marker_fd)


def test_callsite_integration_and_dry_run_preservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY_MIN_AGE_SECONDS", "60")
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY_KEEP_RECENT", "1")
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY_SWEEP_INTERVAL_SECONDS", "0")

    old_mtime = time.time() - 3600.0
    # 1. prepare_decision_capture does NOT prune caller-supplied --decision-output-root.
    capture_root = tmp_path / "decision-root"
    capture_root.mkdir(mode=0o700)
    old_d0 = _make_dir(capture_root, "decision-old-0", mtime=old_mtime - 10)
    old_d1 = _make_dir(capture_root, "decision-old-1", mtime=old_mtime)
    prep_args = argparse.Namespace(
        quota_command="should-run",
        format="json",
        turn_instance_id="turn-1",
        begin_turn=False,
        decision_output_dir=None,
        decision_output_root=str(capture_root),
        decision_handoff_socket=None,
        codex_context_dir=None,
        codex_context_root=None,
        context_delivery="off",
    )
    created = prepare_decision_capture(prep_args)
    assert created is not None and created.is_dir()
    assert old_d0.exists()
    assert old_d1.exists()
    assert not (capture_root / RETENTION_SWEEP_MARKER_NAME).exists()

    # 2. progressive_context_capture_view prunes expired context-* under runtime/context-delivery.
    runtime_a = tmp_path / "runtime-a"
    cd_a = runtime_a / "context-delivery"
    cd_a.mkdir(parents=True, mode=0o700)
    old_c0 = _make_dir(cd_a, "context-old-0", mtime=old_mtime - 10)
    old_c1 = _make_dir(cd_a, "context-old-1", mtime=old_mtime)
    view = progressive_context_capture_view(
        {"ok": True, "goal_id": "g", "summary": "hello"},
        argparse.Namespace(
            quota_command="should-run",
            format="json",
            context_delivery="auto",
            delta_delivery="auto",
        ),
        runtime_root=runtime_a,
        directory=None,
        reader_argv=["loopx"],
    )
    assert view["ok"] is True
    assert not old_c0.exists()
    assert old_c1.exists()

    # 3. budgeted_settlement_result prunes expired settlement-* without breaking bounded delivery.
    runtime_b = tmp_path / "runtime-b"
    cd_b = runtime_b / "context-delivery"
    cd_b.mkdir(parents=True, mode=0o700)
    old_s0 = _make_dir(cd_b, "settlement-old-0", mtime=old_mtime - 10)
    old_s1 = _make_dir(cd_b, "settlement-old-1", mtime=old_mtime)
    bounded = budgeted_settlement_result(
        {"ok": True, "before": {"blob": "x" * 2500}, "after": {"blob": "y" * 2500}},
        runtime_root=runtime_b,
        output_bytes=2000,
        reader_argv=["loopx"],
    )
    assert bounded["ok"] is True
    assert not old_s0.exists()
    assert old_s1.exists()

    # 4. emit_quota_result: dry_run leaves context-delivery untouched; normal run protects active dir.
    runtime_c = tmp_path / "runtime-c"
    cd_c = runtime_c / "context-delivery"
    cd_c.mkdir(parents=True, mode=0o700)
    active_dir = _make_dir(cd_c, "decision-active", mtime=old_mtime)
    stale_dir = _make_dir(cd_c, "recovery-stale", mtime=old_mtime - 50)
    fake_quota = SimpleNamespace(
        observe_quota_result=lambda *a, **k: None,
        _effective_spend_turn_instance_id=lambda *_a, **_k: "turn-live",
        _prepare_quota_result_context=lambda p, *a, **k: (
            p,
            {"enabled": False, "progressive_enabled": False},
        ),
        _export=lambda _name: (lambda *a, **k: None),
    )
    ctx_c = cast(
        "QuotaCommandContext",
        SimpleNamespace(
            runtime_root=runtime_c,
            status_payload={},
            scheduler_context=None,
        ),
    )
    printed: list[dict[str, object]] = []
    rc_dry = emit_quota_result(
        {"ok": True, "action": "wait"},
        argparse.Namespace(
            quota_command="should-run",
            format="json",
            goal_id="g",
            agent_id="a",
            dry_run=True,
            scheduler_observation=False,
        ),
        quota_module=fake_quota,
        context=ctx_c,
        registry_path=tmp_path / "registry.json",
        heartbeat_turn_id="turn-dry",
        usage_quota_started=0,
        capture_directory=active_dir,
        detail_sections=frozenset(),
        print_payload=lambda p, _f, _r: printed.append(p),
    )
    assert rc_dry == 0
    assert stale_dir.exists()
    assert not (cd_c / RETENTION_SWEEP_MARKER_NAME).exists()

    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY_MIN_AGE_SECONDS", "0")
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY_KEEP_RECENT", "0")
    rc_live = emit_quota_result(
        {"ok": True, "action": "wait"},
        argparse.Namespace(
            quota_command="should-run",
            format="json",
            goal_id="g",
            agent_id="a",
            dry_run=False,
            scheduler_observation=False,
        ),
        quota_module=fake_quota,
        context=ctx_c,
        registry_path=tmp_path / "registry.json",
        heartbeat_turn_id="turn-live",
        usage_quota_started=0,
        capture_directory=active_dir,
        detail_sections=frozenset(),
        print_payload=lambda p, _f, _r: printed.append(p),
    )
    assert rc_live == 0
    assert not stale_dir.exists()
    assert active_dir.exists()


def _extract_capture_dir_from_command(command: str) -> Path:
    tokens = shlex.split(command)
    for flag in ("--capture-file", "--reference-file"):
        if flag in tokens:
            return Path(tokens[tokens.index(flag) + 1]).parent
    raise AssertionError(f"missing capture path flag in command: {command}")


def test_shared_decision_output_root_preserves_unrelated_notes_and_both_goals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.control_plane.test_quota_settlement_cli import (
        AGENT_ID,
        GOAL_ID,
        TODO_ID,
        _run_cli,
        _write_fixture,
    )

    _, runtime1, registry1 = _write_fixture(tmp_path / "goal1", provider="file")
    _, runtime2, registry2 = _write_fixture(tmp_path / "goal2", provider="file")
    shared_root = tmp_path / "shared-output-root"
    shared_root.mkdir(mode=0o700)
    notes_file = shared_root / "context-notes" / "notes.md"
    notes_file.parent.mkdir(mode=0o700)
    notes_file.write_text("owner project notes\n", encoding="utf-8")

    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY_MIN_AGE_SECONDS", "0")
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY_KEEP_RECENT", "0")
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY_MAX_DELETE", "128")
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY_SWEEP_INTERVAL_SECONDS", "0")

    code1, guard1 = _run_cli(
        registry1,
        runtime1,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--begin-turn",
        "--decision-output-root",
        str(shared_root),
    )
    assert code1 == 0 and guard1["ok"] is True, guard1
    capture1_dir = _extract_capture_dir_from_command(
        guard1["detail_ref"]["full_decision"]
    )
    assert capture1_dir.parent.resolve() == shared_root.resolve()
    saved1 = capture1_dir / "decision.json"
    digest1 = sha256(saved1.read_bytes()).hexdigest()
    ptr = "/heartbeat_receipt/settlement_identity"
    rc_before, read_before = _run_cli(
        registry1,
        runtime1,
        "quota",
        "context-read",
        "--capture-file",
        str(saved1),
        "--capture-sha256",
        digest1,
        "--select",
        ptr,
    )
    assert rc_before == 0 and read_before["ok"] is True, read_before

    code2, guard2 = _run_cli(
        registry2,
        runtime2,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--begin-turn",
        "--decision-output-root",
        str(shared_root),
    )
    assert code2 == 0 and guard2["ok"] is True, guard2
    capture2_dir = _extract_capture_dir_from_command(
        guard2["detail_ref"]["full_decision"]
    )

    assert notes_file.read_text(encoding="utf-8") == "owner project notes\n"
    assert not (shared_root / RETENTION_SWEEP_MARKER_NAME).exists()
    assert capture1_dir.is_dir()
    assert capture2_dir.is_dir()
    rc_after, read_after = _run_cli(
        registry1,
        runtime1,
        "quota",
        "context-read",
        "--capture-file",
        str(saved1),
        "--capture-sha256",
        digest1,
        "--select",
        ptr,
    )
    assert rc_after == 0 and read_after["content"] == read_before["content"]


def test_managed_root_mixed_non_loopx_dirs_and_unowned_capture_shapes(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    managed_root = runtime / "context-delivery"
    managed_root.mkdir(parents=True, mode=0o700)
    now = 8_000_000.0
    very_old = now - 500_000.0

    mixed_notes = managed_root / "context-notes" / "readme.txt"
    mixed_notes.parent.mkdir(mode=0o700)
    mixed_notes.write_text("mixed manual note\n", encoding="utf-8")
    os.utime(mixed_notes.parent, (very_old, very_old))

    # Mismatched or absent runtime_root admission receipts must fail closed and stay protected.
    missing_rr = managed_root / "decision-missing-runtime-root"
    missing_rr.mkdir(mode=0o700)
    _write_owned_json(
        missing_rr / "decision.json",
        {
            "ok": True,
            "decision": "run",
            "goal_id": "g-1",
            "heartbeat_receipt": {"turn_instance_id": "t-1", "event_id": "evt-1"},
        },
        mtime=very_old,
    )
    os.utime(missing_rr, (very_old, very_old))

    wrong_rr = managed_root / "decision-wrong-runtime-root"
    wrong_rr.mkdir(mode=0o700)
    _write_owned_json(
        wrong_rr / "decision.json",
        {
            "ok": True,
            "decision": "run",
            "goal_id": "g-1",
            "runtime_root": str(tmp_path / "other-runtime"),
            "heartbeat_receipt": {"turn_instance_id": "t-2", "event_id": "evt-2"},
        },
        mtime=very_old,
    )
    os.utime(wrong_rr, (very_old, very_old))

    disposable = _make_dir(managed_root, "recovery-disposable", mtime=very_old)

    res = maybe_prune_context_delivery_root(
        managed_root,
        runtime_root=runtime,
        min_age_seconds=0.0,
        keep_recent_per_prefix=0,
        min_sweep_interval_seconds=0.0,
        now=now,
    )
    assert res.swept is True
    assert res.deleted == 1
    assert not disposable.exists()
    assert mixed_notes.read_text(encoding="utf-8") == "mixed manual note\n"
    assert missing_rr.exists()
    assert wrong_rr.exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_unsettled_turn_capture_protected_until_settled_across_file_and_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
    from tests.control_plane.test_quota_settlement_cli import (
        AGENT_ID,
        GOAL_ID,
        TODO_ID,
        _run_cli,
        _write_fixture,
    )

    cast("Callable[[Path, pytest.MonkeyPatch], None]", isolate_sqlite_runtime)(
        tmp_path, monkeypatch
    )
    project, runtime, registry = _write_fixture(tmp_path, provider=provider)
    managed_root = runtime.expanduser().resolve() / "context-delivery"
    managed_root.mkdir(parents=True, mode=0o700)
    mixed_notes = managed_root / "context-notes" / "readme.txt"
    mixed_notes.parent.mkdir(mode=0o700)
    mixed_notes.write_text("mixed runtime note\n", encoding="utf-8")

    # Turn 1 admission capture inside <runtime_root>/context-delivery.
    code1, guard1 = _run_cli(
        registry,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        "turn-retention-1",
        "--decision-output-root",
        str(managed_root),
        cwd=project,
    )
    assert code1 == 0 and guard1["ok"] is True, guard1
    turn1_dir = _extract_capture_dir_from_command(
        guard1["detail_ref"]["full_decision"]
    )
    turn1_file = turn1_dir / "decision.json"
    turn1_sha = sha256(turn1_file.read_bytes()).hexdigest()
    ptr = "/heartbeat_receipt/settlement_identity"
    rc_before, read_before = _run_cli(
        registry,
        runtime,
        "quota",
        "context-read",
        "--capture-file",
        str(turn1_file),
        "--capture-sha256",
        turn1_sha,
        "--select",
        ptr,
    )
    assert rc_before == 0 and read_before["ok"] is True, read_before

    # Turn 2 runs with aggressive retention settings: Turn 1's unsettled capture must survive.
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY_MIN_AGE_SECONDS", "0")
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY_KEEP_RECENT", "0")
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY_MAX_DELETE", "128")
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY_SWEEP_INTERVAL_SECONDS", "0")

    code2, guard2 = _run_cli(
        registry,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        "turn-retention-2",
        "--decision-output-root",
        str(managed_root),
        cwd=project,
    )
    turn2_dir = _extract_capture_dir_from_command(
        guard2["detail_ref"]["full_decision"]
    )
    assert code2 == 0 and guard2["ok"] is True, guard2
    assert turn1_dir.is_dir()
    assert mixed_notes.read_text(encoding="utf-8") == "mixed runtime note\n"

    # Explicit aggressive sweep while Turn 1 is still unsettled must still keep Turn 1 intact.
    unsettled_sweep = maybe_prune_context_delivery_root(
        managed_root,
        runtime_root=runtime,
        min_age_seconds=0.0,
        keep_recent_per_prefix=0,
        min_sweep_interval_seconds=0.0,
    )
    assert unsettled_sweep.swept is True
    assert unsettled_sweep.deleted == 0
    assert turn1_dir.is_dir()

    rc_still, read_still = _run_cli(
        registry,
        runtime,
        "quota",
        "context-read",
        "--capture-file",
        str(turn1_file),
        "--capture-sha256",
        turn1_sha,
        "--select",
        ptr,
    )
    assert rc_still == 0 and read_still["content"] == read_before["content"]

    # Settle Turn 1 via real CLI refresh-state --settle-turn --settlement-receipt.
    code_settle, settled = _run_cli(
        registry,
        runtime,
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--classification",
        "validated_progress",
        "--delivery-batch-scale",
        "implementation",
        "--delivery-outcome",
        "outcome_progress",
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        "turn-retention-1",
        "--no-global-sync",
        "--suppress-external-sinks",
        "--settle-turn",
        "--settlement-receipt",
        cwd=project,
    )
    assert code_settle == 0 and settled["settlement_progress"]["state"] == "settled", settled

    # Subsequent sweep prunes the now-settled Turn 1 capture while leaving unsettled Turn 2 and mixed notes intact.
    settled_sweep = maybe_prune_context_delivery_root(
        managed_root,
        runtime_root=runtime,
        min_age_seconds=0.0,
        keep_recent_per_prefix=0,
        min_sweep_interval_seconds=0.0,
    )
    assert settled_sweep.swept is True
    assert settled_sweep.deleted >= 1
    assert not turn1_dir.exists()
    assert turn2_dir.is_dir()
    assert mixed_notes.read_text(encoding="utf-8") == "mixed runtime note\n"


def test_active_session_references_and_transitive_dependencies_preserved(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    root = runtime / "context-delivery"
    root.mkdir(parents=True, mode=0o700)
    now = 9_000_000.0
    very_old = now - 500_000.0

    # Transitive dependency referenced by an active session capture must survive even when older.
    transitive_dep = _make_dir(root, "context-transitive-dep", mtime=very_old - 100)
    session_capture = root / "decision-session-ref"
    session_capture.mkdir(mode=0o700)
    session_decision = session_capture / "decision.json"
    _write_owned_json(
        session_decision,
        {
            "ok": True,
            "decision": "wait",
            "detail_ref": {
                "full_decision": (
                    f"loopx quota context-read --capture-file {transitive_dep / 'projection.json'}"
                )
            },
        },
        mtime=very_old,
    )
    os.utime(session_capture, (very_old, very_old))

    # Digest-only claim-source.json reference protects its owned capture directory.
    claim_capture = root / "decision-claim-ref"
    claim_capture.mkdir(mode=0o700)
    claim_digest = _write_owned_json(
        claim_capture / "decision.json",
        {"ok": True, "decision": "proceed"},
        mtime=very_old,
    )
    os.utime(claim_capture, (very_old, very_old))

    # Unsettled admission capture with a transitive dependency inside root must protect both.
    unsettled_dep = _make_dir(root, "context-unsettled-dep", mtime=very_old - 200)
    unsettled_capture = root / "decision-unsettled"
    unsettled_capture.mkdir(mode=0o700)
    _write_owned_json(
        unsettled_capture / "decision.json",
        {
            "ok": True,
            "decision": "run",
            "goal_id": "g-unsettled",
            "heartbeat_receipt": {"turn_instance_id": "t-unsettled", "event_id": "evt-u"},
            "decision_capture_path": str(unsettled_dep / "projection.json"),
        },
        mtime=very_old,
    )
    os.utime(unsettled_capture, (very_old, very_old))

    continuity_dir = root / "a1b2c3d4e5f60718293a4b5c6d7e8f90"
    continuity_dir.mkdir(mode=0o700)
    _write_owned_json(
        continuity_dir / "session.json",
        {
            "session_id": "sess-1",
            "recovery": {"path": str(session_decision), "sha256": "a" * 64},
        },
        mtime=very_old,
    )
    _write_owned_json(
        continuity_dir / "claim-source.json",
        {"session_id": "sess-1", "sha256": claim_digest},
        mtime=very_old,
    )

    disposable = _make_dir(root, "recovery-disposable", mtime=very_old)

    res = maybe_prune_context_delivery_root(
        root,
        runtime_root=runtime,
        min_age_seconds=0.0,
        keep_recent_per_prefix=0,
        min_sweep_interval_seconds=0.0,
        now=now,
    )
    assert res.swept is True
    assert res.deleted == 1
    assert not disposable.exists()
    assert session_capture.exists()
    assert transitive_dep.exists()
    assert claim_capture.exists()
    assert unsettled_capture.exists()
    assert unsettled_dep.exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_default_cli_unsettled_capture_protected_then_pruned_on_turn3_across_file_and_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
    from tests.control_plane.test_quota_settlement_cli import (
        AGENT_ID,
        GOAL_ID,
        TODO_ID,
        _run_cli,
        _write_fixture,
    )

    cast("Callable[[Path, pytest.MonkeyPatch], None]", isolate_sqlite_runtime)(
        tmp_path, monkeypatch
    )
    project, runtime, registry = _write_fixture(tmp_path, provider=provider)
    managed_root = runtime.expanduser().resolve() / "context-delivery"
    managed_root.mkdir(parents=True, mode=0o700)

    # Pre-seed progressive context-* (with delivery.json + pinned .reader.json.<16hex>),
    # decision-* with delivery-request.json, owned failure-only decision-*, unowned
    # failure.json-only decision-*, and unrelated context-notes/.
    old_mtime = time.time() - 10_000.0
    progressive_ctx = managed_root / "context-progressive-delta"
    progressive_ctx.mkdir(mode=0o700)
    delta_target = progressive_ctx / "delivery.json"
    delta_sha = _write_owned_json(
        delta_target,
        {"schema_version": "context_delivery_v1", "mode": "delta", "delta": {"open_count": 1}},
        mtime=old_mtime,
    )
    _write_owned_json(
        progressive_ctx / "delivery.0123456789abcdef.reader.json",
        {
            "schema_version": "context_capture_read_reference_v1",
            "path": str(delta_target),
            "sha256": delta_sha,
        },
        mtime=old_mtime,
    )
    os.utime(progressive_ctx, (old_mtime, old_mtime))

    req_decision = managed_root / "decision-with-delivery-request"
    req_decision.mkdir(mode=0o700)
    req_dec_sha = _write_owned_json(
        req_decision / "decision.json",
        {"ok": True, "decision": "wait"},
        mtime=old_mtime,
    )
    _write_owned_json(
        req_decision / "delivery-request.json",
        {"mode": "full", "requested": True},
        mtime=old_mtime,
    )
    _write_owned_json(
        req_decision / "decision.reader.json.abcdef0123456789",
        {
            "schema_version": "context_capture_read_reference_v1",
            "path": str(req_decision / "decision.json"),
            "sha256": req_dec_sha,
        },
        mtime=old_mtime,
    )
    os.utime(req_decision, (old_mtime, old_mtime))

    failure_owned = managed_root / "decision-failure-owned"
    failure_owned.mkdir(mode=0o700)
    failure_target = failure_owned / "failure.json"
    failure_sha = _write_owned_json(
        failure_target,
        {"ok": False, "error_code": "QUOTA_OUTPUT_BOUND"},
        mtime=old_mtime,
    )
    _write_owned_json(
        failure_owned / "failure.reader.json",
        {
            "schema_version": "context_capture_read_reference_v1",
            "path": str(failure_target),
            "sha256": failure_sha,
        },
        mtime=old_mtime,
    )
    os.utime(failure_owned, (old_mtime, old_mtime))

    failure_unowned = managed_root / "decision-failure-unowned"
    failure_unowned.mkdir(mode=0o700)
    _write_owned_json(
        failure_unowned / "failure.json",
        {"ok": False, "custom": "no-reader"},
        mtime=old_mtime,
    )
    os.utime(failure_unowned, (old_mtime, old_mtime))

    mixed_notes = managed_root / "context-notes" / "readme.txt"
    mixed_notes.parent.mkdir(mode=0o700)
    mixed_notes.write_text("default-cli mixed note\n", encoding="utf-8")

    # Turn 1 uses default CLI flags (raw_guard=False, omitting --context-delivery off),
    # writing delivery.json + delivery.<16hex>.reader.json + decision.json inside <runtime_root>/context-delivery.
    code1, guard1 = _run_cli(
        registry,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        "turn-default-1",
        cwd=project,
        raw_guard=False,
    )
    assert code1 == 0 and guard1["ok"] is True, guard1
    turn1_ref = Path(str(guard1["reference_file"]))
    turn1_dir = turn1_ref.parent
    assert turn1_dir.parent == managed_root
    assert (turn1_dir / "delivery.json").is_file()
    assert (turn1_dir / "decision.json").is_file()

    rc_read1, read1 = _run_cli(
        registry,
        runtime,
        "quota",
        "context-read",
        "--reference-file",
        str(turn1_ref),
    )
    assert rc_read1 == 0 and read1["ok"] is True, read1

    # Enable aggressive sweep for Turn 2 and Turn 3.
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY_MIN_AGE_SECONDS", "0")
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY_KEEP_RECENT", "0")
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY_MAX_DELETE", "128")
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY_SWEEP_INTERVAL_SECONDS", "0")

    # Turn 2 triggers opportunistic CLI pruning:
    # - unsettled Turn 1 capture stays protected & readable;
    # - settled/no-receipt LoopX dirs (progressive context-* delta, delivery-request, owned failure) are pruned;
    # - unowned failure.json-only dir and context-notes/ survive untouched.
    code2, guard2 = _run_cli(
        registry,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        "turn-default-2",
        cwd=project,
        raw_guard=False,
    )
    assert code2 == 0 and guard2["ok"] is True, guard2
    turn2_dir = Path(str(guard2["reference_file"])).parent
    assert turn1_dir.is_dir()
    assert turn2_dir.is_dir()
    assert not progressive_ctx.exists()
    assert not req_decision.exists()
    assert not failure_owned.exists()
    assert failure_unowned.is_dir()
    assert mixed_notes.read_text(encoding="utf-8") == "default-cli mixed note\n"

    rc_still1, read_still1 = _run_cli(
        registry,
        runtime,
        "quota",
        "context-read",
        "--reference-file",
        str(turn1_ref),
    )
    assert rc_still1 == 0 and read_still1["ok"] is True, read_still1

    # Settle Turn 1 via real CLI refresh-state --settle-turn.
    code_settle, settled = _run_cli(
        registry,
        runtime,
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--classification",
        "validated_progress",
        "--delivery-batch-scale",
        "implementation",
        "--delivery-outcome",
        "outcome_progress",
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        "turn-default-1",
        "--no-global-sync",
        "--suppress-external-sinks",
        "--settle-turn",
        "--settlement-receipt",
        cwd=project,
    )
    assert code_settle == 0 and settled["settlement_progress"]["state"] == "settled", settled

    # Turn 3 runs with default CLI flags: opportunistic pruning now reclaims settled Turn 1
    # (even though turn1_dir contains delivery.json + *.reader.json* + decision.json + projection.json)
    # while still protecting unsettled Turn 2 and Turn 3.
    code3, guard3 = _run_cli(
        registry,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        "turn-default-3",
        cwd=project,
        raw_guard=False,
    )
    assert code3 == 0 and guard3["ok"] is True, guard3
    turn3_dir = Path(str(guard3["reference_file"])).parent
    assert not turn1_dir.exists()
    assert turn2_dir.is_dir()
    assert turn3_dir.is_dir()
    assert failure_unowned.is_dir()
    assert mixed_notes.read_text(encoding="utf-8") == "default-cli mixed note\n"
