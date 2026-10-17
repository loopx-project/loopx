"""Retire runtimes in a finished test's disposable namespace, via their owner."""
from __future__ import annotations

from pathlib import Path
import tempfile
from uuid import uuid4

from loopx.control_plane import effect_runtime


def capture_disposable_effect_runtimes(root: Path) -> list[tuple[Path, str]]:
    if root.is_symlink():
        return []
    owned_root = root.resolve()
    captured = []
    for locator in root.glob("**/loopx-effect-runtime-*/runtime-*.json"):
        # Never follow a fixture's borrowed namespace or locator outside the
        # test directory. Copied packages can legitimately own several revisions.
        if locator.is_symlink() or not locator.resolve().is_relative_to(owned_root):
            continue
        try:
            captured.append((locator, locator.read_text(encoding="utf-8")))
        except (OSError, UnicodeError):
            continue
    return captured


def stop_disposable_effect_runtimes(
    root: Path, captured: list[tuple[Path, str]] | None = None,
) -> None:
    records = dict.fromkeys([*(captured or []), *capture_disposable_effect_runtimes(root)])
    if not records:
        return
    # pytest may remove passed tmp_path directories during fixture teardown.
    # Validate the captured bytes with the existing reader after monkeypatches
    # are restored; the authenticated ping still verifies the exact live owner.
    with tempfile.TemporaryDirectory(prefix="loopx-test-runtime-cleanup-") as scratch:
        for locator, raw in records:
            validation_file = Path(scratch) / locator.name
            validation_file.write_text(raw, encoding="utf-8")
            info = effect_runtime._read_info(validation_file, fingerprint=None)
            if info is None or locator.name != f"runtime-{info['fingerprint'][:16]}.json":
                continue
            try:
                response = effect_runtime._request_with_info(
                    info, request_id=uuid4().hex, method="runtime.ping", params={}, timeout=1,
                )
            except (OSError, RuntimeError):
                # Corrupt, stale or rejected metadata cannot authorize shutdown.
                continue
            ping = response.get("result")
            if (not isinstance(ping, dict) or ping.get("ready") is not True or ping.get("pid") != info["pid"]
                    or ping.get("fingerprint") != info["fingerprint"]):
                continue
            result = effect_runtime._stop_effect_runtime(
                locator, info, timeout=5, wait_for_process_exit=True,
            )
            assert result["stopped"], f"Disposable runtime did not exit: {locator}"
