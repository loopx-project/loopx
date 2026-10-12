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


@pytest.mark.parametrize(
    "archive_suffix",
    [
        Path("."),
        Path("archive"),
        Path("archive/deeper"),
    ],
    ids=["source", "direct-child", "nested-child"],
)
@pytest.mark.parametrize("execute", [False, True], ids=["dry-run", "execute"])
def test_archive_runtime_rejects_destination_inside_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    archive_suffix: Path,
    execute: bool,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    source = tmp_path / ".loopx" / "runtime" / "goals" / "obsolete-goal"
    source.mkdir(parents=True)
    marker = source / "expected.txt"
    marker.write_text("preserved", encoding="utf-8")
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

    def fail_move(_source: str, _destination: str) -> None:
        pytest.fail("invalid archive destination reached shutil.move")

    monkeypatch.setattr("loopx.runtime.shutil.move", fail_move)

    with pytest.raises(
        ValueError,
        match="archive destination must be outside the runtime goal directory",
    ):
        archive_runtime_goal(
            registry_path=registry_path,
            runtime_root_override=None,
            goal_id="obsolete-goal",
            archive_root=source / archive_suffix,
            allow_registered=False,
            execute=execute,
        )

    assert marker.read_text(encoding="utf-8") == "preserved"
    assert [path.relative_to(source) for path in source.rglob("*")] == [
        Path("expected.txt")
    ]


@pytest.mark.parametrize("source_alias", [False, True], ids=["direct", "alias"])
@pytest.mark.parametrize("execute", [False, True], ids=["dry-run", "execute"])
def test_archive_runtime_rejects_source_child_symlink_to_external_archive(
    tmp_path: Path,
    source_alias: bool,
    execute: bool,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    source = tmp_path / ".loopx" / "runtime" / "goals" / "obsolete-goal"
    source.mkdir(parents=True)
    marker = source / "expected.txt"
    marker.write_text("preserved", encoding="utf-8")
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
    external_archive = tmp_path / "external-archive"
    external_archive.mkdir()
    source_route = source
    if source_alias:
        source_route = tmp_path / "source-alias"
        source_route.symlink_to(source, target_is_directory=True)
    archive_root = source_route / "archive-link"
    archive_root.symlink_to(external_archive, target_is_directory=True)

    with pytest.raises(
        ValueError,
        match="archive destination must be outside the runtime goal directory",
    ):
        archive_runtime_goal(
            registry_path=registry_path,
            runtime_root_override=None,
            goal_id="obsolete-goal",
            archive_root=archive_root,
            allow_registered=False,
            execute=execute,
        )

    assert marker.read_text(encoding="utf-8") == "preserved"
    assert archive_root.resolve() == external_archive.resolve()
    assert list(external_archive.iterdir()) == []
