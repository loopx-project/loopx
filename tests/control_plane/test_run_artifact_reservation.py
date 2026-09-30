from __future__ import annotations

import multiprocessing
from pathlib import Path

from loopx.control_plane.runtime.run_artifacts import reserve_run_artifact_paths


def _reserve_same_timestamp(args: tuple[str, str, str]) -> tuple[str, str]:
    runs_dir, stem, suffix = args
    json_path, markdown_path = reserve_run_artifact_paths(
        Path(runs_dir),
        stem,
        suffix,
    )
    return json_path.name, markdown_path.name


def test_run_artifact_reservation_is_atomic_across_processes(tmp_path: Path) -> None:
    worker_count = 8
    args = (str(tmp_path), "2026-07-17T07-49-27-08-00", "quota-monitor-poll")

    with multiprocessing.get_context("spawn").Pool(worker_count) as pool:
        paths = pool.map(_reserve_same_timestamp, [args] * worker_count)

    json_names = {json_name for json_name, _ in paths}
    markdown_names = {markdown_name for _, markdown_name in paths}
    assert len(json_names) == worker_count
    assert len(markdown_names) == worker_count
    assert {path.name for path in tmp_path.glob("*.json")} == json_names
    assert all((tmp_path / name).stat().st_size == 0 for name in json_names)


def test_run_filename_facades_reuse_the_runtime_codec() -> None:
    from loopx import operator_gate, state_refresh
    from loopx.control_plane.runtime.run_artifacts import run_file_stem

    assert operator_gate.run_file_stem is run_file_stem
    assert state_refresh.run_file_stem is run_file_stem
    assert run_file_stem("2026-09-29T14:00:00.123+08:00") == "2026-09-29T14-00-00-123-08-00"
    assert run_file_stem("../🧪/2026-09-29/") == "2026-09-29"
