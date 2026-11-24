"""Bounded opportunistic retention tests for context-delivery directories."""
from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
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


def _make_dir(parent: Path, name: str, *, mtime: float) -> Path:
    path = parent / name
    path.mkdir(parents=True, exist_ok=True)
    payload = path / "payload.json"
    payload.write_text('{"ok":true}\n', encoding="utf-8")
    import os

    os.utime(payload, (mtime, mtime))
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

    # Real expired dir containing a nested symlink must delete only the ephemeral dir, not outside_dir.
    nested_link_dir = _make_dir(root, "recovery-with-nested-symlink", mtime=very_old)
    (nested_link_dir / "nested-link").symlink_to(outside_dir, target_is_directory=True)
    import os

    os.utime(nested_link_dir, (very_old, very_old))

    result = maybe_prune_context_delivery_root(
        root,
        min_age_seconds=60.0,
        keep_recent_per_prefix=0,
        now=now,
    )
    assert result.swept is True
    assert result.deleted == 1
    assert result.failed == 0
    assert not nested_link_dir.exists()
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
        path = root / f"{prefix}{index:04d}"
        path.mkdir()
        import os

        os.utime(path, (old_base + index, old_base + index))

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
    # 1. prepare_decision_capture prunes expired excess decision-* dirs cleanly.
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
    assert not old_d0.exists()
    assert old_d1.exists()

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
