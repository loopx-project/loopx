from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.runtime import archive_runtime_goal


def test_archive_runtime_resolves_relative_root_from_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    registry_path = project / ".loopx" / "registry.json"
    runtime_root = project / ".loopx" / "runtime"
    source = runtime_root / "goals" / "obsolete-goal"
    source.mkdir(parents=True)
    (source / "expected.txt").write_text("target", encoding="utf-8")
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": ".loopx/runtime",
                "goals": [],
            }
        ),
        encoding="utf-8",
    )

    unrelated = tmp_path / "unrelated"
    decoy = unrelated / ".loopx" / "runtime" / "goals" / "obsolete-goal"
    decoy.mkdir(parents=True)
    (decoy / "decoy.txt").write_text("unrelated", encoding="utf-8")
    monkeypatch.chdir(unrelated)

    result = archive_runtime_goal(
        registry_path=registry_path,
        runtime_root_override=None,
        goal_id="obsolete-goal",
        archive_root=None,
        allow_registered=False,
        execute=True,
    )

    archive_path = Path(result["archive_path"])
    assert Path(result["runtime_root"]) == runtime_root
    assert not source.exists()
    assert (archive_path / "expected.txt").read_text(encoding="utf-8") == "target"
    assert (decoy / "decoy.txt").read_text(encoding="utf-8") == "unrelated"
