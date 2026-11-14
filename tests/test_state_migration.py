from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.state_migration import copy_active_state_files, migrate_legacy_state


def test_migration_rejects_target_goal_id_collisions_before_writing(
    tmp_path: Path,
) -> None:
    legacy_runtime = tmp_path / "legacy-runtime"
    target_runtime = tmp_path / "target-runtime"
    source_repos = {
        goal_id: tmp_path / f"{goal_id}-source" for goal_id in ("goal-a", "goal-b")
    }
    target_repos = {
        goal_id: tmp_path / f"{goal_id}-target" for goal_id in ("goal-a", "goal-b")
    }
    goals = []
    for goal_id, source_repo in source_repos.items():
        source_repo.mkdir()
        (source_repo / "ACTIVE_GOAL_STATE.md").write_text(
            f"---\ngoal_id: {goal_id}\n---\n",
            encoding="utf-8",
        )
        source_runtime = legacy_runtime / "goals" / goal_id
        source_runtime.mkdir(parents=True)
        (source_runtime / f"{goal_id}.json").write_text(
            json.dumps({"goal_id": goal_id}),
            encoding="utf-8",
        )
        goals.append(
            {
                "id": goal_id,
                "status": "active",
                "repo": str(source_repo),
                "state_file": "ACTIVE_GOAL_STATE.md",
            }
        )

    legacy_registry = tmp_path / "legacy-registry.json"
    legacy_registry.write_text(
        json.dumps({"schema_version": "0.1", "goals": goals}),
        encoding="utf-8",
    )
    target_registry = tmp_path / "target-registry.json"
    original_target_registry = b'{"schema_version":"0.1","goals":[]}\n'
    target_registry.write_bytes(original_target_registry)

    with pytest.raises(
        ValueError,
        match=r"goal-a and goal-b map to the same target goal id: goal-b",
    ):
        migrate_legacy_state(
            legacy_registry_path=legacy_registry,
            target_registry_path=target_registry,
            legacy_runtime_root=legacy_runtime,
            target_runtime_root=target_runtime,
            goal_ids=["goal-a", "goal-b"],
            goal_id_map={"goal-a": "goal-b"},
            path_map={
                str(source_repos[goal_id]): str(target_repos[goal_id])
                for goal_id in source_repos
            },
            copy_active_state=True,
            copy_runtime=True,
            execute=True,
        )

    assert target_registry.read_bytes() == original_target_registry
    assert not any(
        (target_repo / "ACTIVE_GOAL_STATE.md").exists()
        for target_repo in target_repos.values()
    )
    assert not (target_runtime / "goals" / "goal-b").exists()


def _active_state_migration_fixture(tmp_path: Path) -> dict[str, Path]:
    repo = tmp_path / "repo"
    source_state = repo / ".codex" / "goals" / "goal-a" / "ACTIVE_GOAL_STATE.md"
    source_state.parent.mkdir(parents=True)
    source_state.write_text("## Agent Todo\n\n- [ ] legacy todo\n", encoding="utf-8")
    legacy_registry = tmp_path / "legacy-registry.json"
    legacy_registry.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "goals": [
                    {
                        "id": "goal-a",
                        "status": "active",
                        "repo": str(repo),
                        "state_file": ".codex/goals/goal-a/ACTIVE_GOAL_STATE.md",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return {
        "repo": repo,
        "legacy_registry": legacy_registry,
        "target_registry": repo / ".loopx" / "registry.json",
        "target_state": repo / ".loopx" / "goals" / "goal-a" / "ACTIVE_GOAL_STATE.md",
        "target_runtime": tmp_path / "target-runtime",
    }


def _migrate_active_state(paths: dict[str, Path], tmp_path: Path, *, execute: bool) -> dict:
    return migrate_legacy_state(
        legacy_registry_path=paths["legacy_registry"],
        target_registry_path=paths["target_registry"],
        legacy_runtime_root=tmp_path / "legacy-runtime",
        target_runtime_root=paths["target_runtime"],
        goal_ids=["goal-a"],
        goal_id_map={},
        path_map={".codex/goals": ".loopx/goals"},
        copy_active_state=True,
        copy_runtime=False,
        execute=execute,
    )


def test_repeated_migration_does_not_overwrite_diverged_target_active_state(
    tmp_path: Path,
) -> None:
    paths = _active_state_migration_fixture(tmp_path)
    first = _migrate_active_state(paths, tmp_path, execute=True)
    assert first["active_state"][0]["copied"] is True
    # An identical rerun is idempotent.
    assert _migrate_active_state(paths, tmp_path, execute=True)["ok"] is True

    progressed = "## Agent Todo\n\n- [x] legacy todo\n- [ ] work added after migration\n"
    paths["target_state"].write_text(progressed, encoding="utf-8")
    registry_before = paths["target_registry"].read_bytes()

    for execute in (False, True):
        with pytest.raises(ValueError, match="already exists with different content"):
            _migrate_active_state(paths, tmp_path, execute=execute)

    assert paths["target_state"].read_text(encoding="utf-8") == progressed
    assert paths["target_registry"].read_bytes() == registry_before


def _two_goal_active_state_fixture(
    tmp_path: Path, contents: tuple[str, str]
) -> tuple[dict[str, Path], dict]:
    legacy_goals = []
    for goal_id, content in zip(("goal-a", "goal-b"), contents):
        repo = tmp_path / f"{goal_id}-repo"
        state = repo / "ACTIVE_GOAL_STATE.md"
        state.parent.mkdir(parents=True)
        state.write_text(content, encoding="utf-8")
        legacy_goals.append(
            {
                "id": goal_id,
                "status": "active",
                "repo": str(repo),
                "state_file": "ACTIVE_GOAL_STATE.md",
            }
        )
    legacy_registry = tmp_path / "legacy-registry.json"
    legacy_registry.write_text(
        json.dumps({"schema_version": "0.1", "goals": legacy_goals}),
        encoding="utf-8",
    )
    shared_repo = tmp_path / "shared-repo"
    paths = {
        "legacy_registry": legacy_registry,
        "target_registry": tmp_path / "target-registry.json",
        "target_state": shared_repo / "ACTIVE_GOAL_STATE.md",
    }
    kwargs = {
        "legacy_registry_path": legacy_registry,
        "target_registry_path": paths["target_registry"],
        "legacy_runtime_root": tmp_path / "legacy-runtime",
        "target_runtime_root": tmp_path / "target-runtime",
        "goal_ids": ["goal-a", "goal-b"],
        "goal_id_map": {},
        "path_map": {
            str(tmp_path / "goal-a-repo"): str(shared_repo),
            str(tmp_path / "goal-b-repo"): str(shared_repo),
        },
        "copy_active_state": True,
        "copy_runtime": False,
    }
    return paths, kwargs


def test_batch_mapping_two_goals_to_one_state_file_is_rejected_before_writing(
    tmp_path: Path,
) -> None:
    paths, kwargs = _two_goal_active_state_fixture(
        tmp_path, ("- [ ] goal a todo\n", "- [ ] goal b todo\n")
    )
    paths["target_registry"].write_bytes(b'{"schema_version":"0.1","goals":[]}\n')
    registry_before = paths["target_registry"].read_bytes()

    for execute in (False, True):
        with pytest.raises(ValueError, match="same active state file"):
            migrate_legacy_state(**kwargs, execute=execute)

    assert not paths["target_state"].exists()
    assert paths["target_registry"].read_bytes() == registry_before


def test_batch_mapping_two_goals_with_identical_state_to_one_file_is_idempotent(
    tmp_path: Path,
) -> None:
    paths, kwargs = _two_goal_active_state_fixture(
        tmp_path, ("- [ ] shared todo\n", "- [ ] shared todo\n")
    )
    paths["target_registry"].write_bytes(b'{"schema_version":"0.1","goals":[]}\n')

    assert migrate_legacy_state(**kwargs, execute=True)["ok"] is True
    assert migrate_legacy_state(**kwargs, execute=True)["ok"] is True
    assert paths["target_state"].read_text(encoding="utf-8") == "- [ ] shared todo\n"


def test_batch_source_rewritten_in_place_is_judged_by_its_published_bytes(
    tmp_path: Path,
) -> None:
    path_map = {"/old": "/old/new"}
    source = tmp_path / "source" / "ACTIVE_GOAL_STATE.md"
    target = tmp_path / "target" / "ACTIVE_GOAL_STATE.md"
    source.parent.mkdir()
    target.parent.mkdir()
    original = "- [ ] see /old/file\n"
    source.write_text(original, encoding="utf-8")
    # The target already holds the single rewrite of the original source.
    target.write_text("- [ ] see /old/new/file\n", encoding="utf-8")
    in_place = {"repo": str(source.parent), "state_file": source.name}
    elsewhere = {"repo": str(target.parent), "state_file": target.name}
    pairs = [(in_place, in_place), (in_place, elsewhere)]

    for execute in (False, True):
        with pytest.raises(ValueError, match="already exists with different content"):
            copy_active_state_files(
                pairs, goal_id_map={}, path_map=path_map, execute=execute
            )

    assert source.read_text(encoding="utf-8") == original
    assert target.read_text(encoding="utf-8") == "- [ ] see /old/new/file\n"


def test_crlf_state_retry_is_idempotent_and_equivalent_newlines_share_a_target(
    tmp_path: Path,
) -> None:
    paths, kwargs = _two_goal_active_state_fixture(
        tmp_path, ("- [ ] shared todo\r\n", "- [ ] shared todo\n")
    )
    paths["target_registry"].write_bytes(b'{"schema_version":"0.1","goals":[]}\n')

    # The writer normalizes CRLF to LF, so the two sources publish the same
    # document and an identical retry must not conflict with its own output.
    for _ in range(2):
        assert migrate_legacy_state(**kwargs, execute=True)["ok"] is True
    assert paths["target_state"].read_bytes() == b"- [ ] shared todo\n"


def test_batch_generated_source_cannot_bypass_the_diverged_target_guard(
    tmp_path: Path,
) -> None:
    legacy_repo_a = tmp_path / "a-repo"
    target_a = tmp_path / "a-target"
    target_b = tmp_path / "b-target"
    legacy_repo_a.mkdir()
    (legacy_repo_a / "ACTIVE_GOAL_STATE.md").write_text("- [ ] a todo\n", encoding="utf-8")
    # Goal B has no legacy file; Goal A's target is where B's legacy source would be.
    target_b.mkdir()
    progressed = "- [x] b finished\n"
    (target_b / "ACTIVE_GOAL_STATE.md").write_text(progressed, encoding="utf-8")
    legacy_registry = tmp_path / "legacy-registry.json"
    legacy_registry.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "goals": [
                    {"id": "goal-a", "status": "active", "repo": str(legacy_repo_a),
                     "state_file": "ACTIVE_GOAL_STATE.md"},
                    {"id": "goal-b", "status": "active", "repo": str(target_a),
                     "state_file": "ACTIVE_GOAL_STATE.md"},
                ],
            }
        ),
        encoding="utf-8",
    )
    target_registry = tmp_path / "target-registry.json"
    target_registry.write_bytes(b'{"schema_version":"0.1","goals":[]}\n')
    registry_before = target_registry.read_bytes()
    kwargs = {
        "legacy_registry_path": legacy_registry,
        "target_registry_path": target_registry,
        "legacy_runtime_root": tmp_path / "legacy-runtime",
        "target_runtime_root": tmp_path / "target-runtime",
        "goal_ids": ["goal-a", "goal-b"],
        "goal_id_map": {},
        "path_map": {str(legacy_repo_a): str(target_a), str(target_a): str(target_b)},
        "copy_active_state": True,
        "copy_runtime": False,
    }

    for execute in (False, True):
        with pytest.raises(ValueError, match="already exists with different content"):
            migrate_legacy_state(**kwargs, execute=execute)

    assert (target_b / "ACTIVE_GOAL_STATE.md").read_text(encoding="utf-8") == progressed
    assert not (target_a / "ACTIVE_GOAL_STATE.md").exists()
    assert target_registry.read_bytes() == registry_before


def test_genuinely_missing_source_is_still_skipped_in_preview_and_execute(
    tmp_path: Path,
) -> None:
    present = {"repo": str(tmp_path / "present"), "state_file": "S.md"}
    missing = {"repo": str(tmp_path / "missing"), "state_file": "S.md"}
    (tmp_path / "present").mkdir()
    (tmp_path / "present" / "S.md").write_text("- [ ] t\n", encoding="utf-8")
    target = {"repo": str(tmp_path / "out"), "state_file": "S.md"}
    other = {"repo": str(tmp_path / "out2"), "state_file": "S.md"}
    pairs = [(present, target), (missing, other)]

    for execute in (False, True):
        rows = copy_active_state_files(pairs, goal_id_map={}, path_map={}, execute=execute)
        assert rows[0].get("skipped_reason") is None
        assert rows[0]["copied"] is execute
        assert rows[1]["skipped_reason"] == "source_state_missing"
        assert rows[1]["copied"] is False


def test_symlinked_target_is_refused_before_any_write(tmp_path: Path) -> None:
    source_repo = tmp_path / "src"
    source_repo.mkdir()
    source = source_repo / "S.md"
    source.write_text("- [ ] see /old/file\n", encoding="utf-8")
    link = tmp_path / "link" / "S.md"
    link.parent.mkdir()
    link.symlink_to(source)
    completed_repo = tmp_path / "done"
    completed_repo.mkdir()
    completed = "- [x] finished\n"
    (completed_repo / "S.md").write_text(completed, encoding="utf-8")
    first = ({"repo": str(source_repo), "state_file": "S.md"}, {"repo": str(link.parent), "state_file": "S.md"})
    second = ({"repo": str(source_repo), "state_file": "S.md"}, {"repo": str(completed_repo), "state_file": "S.md"})

    for execute in (False, True):
        with pytest.raises(ValueError, match="symbolic link"):
            copy_active_state_files(
                [first, second], goal_id_map={}, path_map={"/old": "/old/new"}, execute=execute
            )

    assert link.is_symlink()
    assert source.read_text(encoding="utf-8") == "- [ ] see /old/file\n"
    assert (completed_repo / "S.md").read_text(encoding="utf-8") == completed


def test_source_symlinked_to_its_own_target_is_still_rewritten_in_place(
    tmp_path: Path,
) -> None:
    real_repo = tmp_path / "real"
    real_repo.mkdir()
    real = real_repo / "S.md"
    real.write_text("- [ ] see /old/file\n", encoding="utf-8")
    link_repo = tmp_path / "linked"
    link_repo.mkdir()
    (link_repo / "S.md").symlink_to(real)
    pair = (
        {"repo": str(link_repo), "state_file": "S.md"},
        {"repo": str(real_repo), "state_file": "S.md"},
    )

    rows = copy_active_state_files(
        [pair], goal_id_map={}, path_map={"/old": "/old/new"}, execute=True
    )

    assert rows[0]["copied"] is True
    assert real.read_text(encoding="utf-8") == "- [ ] see /old/new/file\n"


def test_two_level_symlink_chain_cannot_bypass_the_completed_target_guard(
    tmp_path: Path,
) -> None:
    real_repo = tmp_path / "real"
    real_repo.mkdir()
    real = real_repo / "S.md"
    original = "- [ ] see /old/file\n"
    real.write_text(original, encoding="utf-8")
    chain_a = tmp_path / "chain-a"
    chain_a.mkdir()
    (chain_a / "S.md").symlink_to(real)
    chain_b = tmp_path / "chain-b"
    chain_b.mkdir()
    (chain_b / "S.md").symlink_to(chain_a / "S.md")
    done_repo = tmp_path / "done"
    done_repo.mkdir()
    completed = "- [x] see /old/new/file\n"
    (done_repo / "S.md").write_text(completed, encoding="utf-8")
    # A's source and target are the same link; B reads through A's link.
    first = (
        {"repo": str(chain_a), "state_file": "S.md"},
        {"repo": str(chain_a), "state_file": "S.md"},
    )
    second = (
        {"repo": str(chain_b), "state_file": "S.md"},
        {"repo": str(done_repo), "state_file": "S.md"},
    )

    for execute in (False, True):
        with pytest.raises(ValueError, match="symbolic link"):
            copy_active_state_files(
                [first, second], goal_id_map={}, path_map={"/old": "/old/new"}, execute=execute
            )

    assert (chain_a / "S.md").is_symlink()
    assert real.read_text(encoding="utf-8") == original
    assert (done_repo / "S.md").read_text(encoding="utf-8") == completed


@pytest.mark.parametrize("in_place_first", [False, True])
def test_in_place_pair_sharing_a_target_with_another_pair_is_checked_in_either_order(
    tmp_path: Path, in_place_first: bool
) -> None:
    path_map = {"/old": "/old/new"}
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    # Rewriting the legacy text once gives exactly the completed target text.
    (legacy / "S.md").write_text("- [x] see /old/file\n", encoding="utf-8")
    shared_repo = tmp_path / "shared"
    shared_repo.mkdir()
    completed = "- [x] see /old/new/file\n"
    (shared_repo / "S.md").write_text(completed, encoding="utf-8")
    copy = ({"repo": str(legacy), "state_file": "S.md"}, {"repo": str(shared_repo), "state_file": "S.md"})
    in_place = ({"repo": str(shared_repo), "state_file": "S.md"}, {"repo": str(shared_repo), "state_file": "S.md"})
    pairs = [in_place, copy] if in_place_first else [copy, in_place]

    for execute in (False, True):
        with pytest.raises(ValueError, match="same active state file"):
            copy_active_state_files(pairs, goal_id_map={}, path_map=path_map, execute=execute)

    assert (shared_repo / "S.md").read_text(encoding="utf-8") == completed
    assert (legacy / "S.md").read_text(encoding="utf-8") == "- [x] see /old/file\n"
