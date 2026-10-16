from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from loopx.state_migration import migrate_legacy_state, render_state_migration_markdown
from loopx.control_plane.coordination.legacy_writer_fence import LegacyCoordinationWriterFenced


OLD_GOAL_ID = "legacy-goal"
NEW_GOAL_ID = "migrated-goal"
OLD_STORE_IDENTITY = "file:11111111111111111111111111111111"


def _migration_fixture(tmp_path: Path) -> dict[str, Path]:
    legacy_runtime = tmp_path / "legacy-runtime"
    target_runtime = tmp_path / "target-runtime"
    source_repo = tmp_path / "legacy-repo"
    target_repo = tmp_path / "target-repo"
    source_repo.mkdir()
    target_repo.mkdir()

    source_state = source_repo / "ACTIVE_GOAL_STATE.md"
    source_state.write_text(
        "---\n"
        f"goal_id: {OLD_GOAL_ID}\n"
        "handoff_mode: soft_claim\n"
        "updated_at: 2026-09-02T00:00:00+10:00\n"
        "---\n\n"
        "## Agent Todo\n\n"
        "- [ ] Preserve the new local authority only.\n",
        encoding="utf-8",
    )

    legacy_registry = tmp_path / "legacy-registry.json"
    legacy_registry.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "common_runtime_root": str(legacy_runtime),
                "goals": [
                    {
                        "id": OLD_GOAL_ID,
                        "status": "active",
                        "repo": str(source_repo),
                        "state_file": source_state.name,
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": ["agent-a", "agent-b"],
                            "authority_shadow": {
                                "schema_version": (
                                    "loopx_local_authority_shadow_config_v0"
                                ),
                                "mode": "file_one_way",
                            },
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    source_goal_runtime = legacy_runtime / "goals" / OLD_GOAL_ID
    (source_goal_runtime / "task-leases").mkdir(parents=True)
    (source_goal_runtime / "task-leases" / "safe-local.json").write_text(
        json.dumps(
            {
                "goal_id": OLD_GOAL_ID,
                "todo_id": "safe-local",
                "owner": "agent-a",
                "version": 1,
                "lease_epoch": 1,
                "status": "released",
            }
        ),
        encoding="utf-8",
    )
    source_shadow_store = (
        legacy_runtime / "authority-shadow" / "file" / OLD_GOAL_ID
    )
    source_shadow_store.mkdir(parents=True)
    (source_shadow_store / "store-identity").write_text(
        OLD_STORE_IDENTITY,
        encoding="utf-8",
    )
    (source_shadow_store / "authority-store-legacy.json").write_text(
        json.dumps(
            {
                "goal_id": OLD_GOAL_ID,
                "store_identity": OLD_STORE_IDENTITY,
                "provider_revision": "file:99:legacy-lineage",
                "cursor": "99",
                "private_provider_byte": "must-never-migrate",
                "source_path": str(source_repo),
            }
        ),
        encoding="utf-8",
    )

    return {
        "legacy_registry": legacy_registry,
        "target_registry": tmp_path / "target-registry.json",
        "legacy_runtime": legacy_runtime,
        "target_runtime": target_runtime,
        "source_repo": source_repo,
        "target_repo": target_repo,
    }


def _migrate(paths: dict[str, Path], *, execute: bool) -> dict[str, object]:
    return migrate_legacy_state(
        legacy_registry_path=paths["legacy_registry"],
        target_registry_path=paths["target_registry"],
        legacy_runtime_root=paths["legacy_runtime"],
        target_runtime_root=paths["target_runtime"],
        goal_ids=[OLD_GOAL_ID],
        goal_id_map={OLD_GOAL_ID: NEW_GOAL_ID},
        path_map={str(paths["source_repo"]): str(paths["target_repo"])},
        copy_active_state=True,
        copy_runtime=True,
        execute=execute,
    )


@pytest.mark.parametrize("execute", [False, True])
def test_migration_preserves_old_store_without_reseeding_retired_lineage(tmp_path: Path, execute: bool) -> None:
    paths = _migration_fixture(tmp_path)
    old_dir = paths["legacy_runtime"] / "authority-shadow" / "file" / OLD_GOAL_ID
    before = {p.name: p.read_bytes() for p in old_dir.iterdir()}
    result = _migrate(paths, execute=execute)
    assert result["ok"] is True
    assert result["authority_shadow_seeds"] == [{
        "schema_version": "loopx_state_migration_shadow_seed_evidence_v0",
        "goal_id": NEW_GOAL_ID, "attempted": False, "outcome": "retired",
        "reason_code": "local_authority_shadow_retired"}]
    assert not (paths["target_runtime"] / "authority-shadow").exists()
    assert {p.name: p.read_bytes() for p in old_dir.iterdir()} == before
    assert "retired" in render_state_migration_markdown(result)
    if execute:
        target = json.loads(paths["target_registry"].read_text())["goals"][0]
        assert target["id"] == NEW_GOAL_ID
        assert "runtime_shadow" not in target["coordination"]
        lease = paths["target_runtime"] / "goals" / NEW_GOAL_ID / "task-leases" / "safe-local.json"
        assert json.loads(lease.read_text())["goal_id"] == NEW_GOAL_ID
    else:
        assert not paths["target_registry"].exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("endpoint", ["source", "target", "previous_runtime"])
def test_preview_refuses_canonical_endpoints_without_copying(
    tmp_path: Path, monkeypatch, provider: str, endpoint: str,
) -> None:
    from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    paths = _migration_fixture(tmp_path)
    runtime, goal_id, state = paths["legacy_runtime"], OLD_GOAL_ID, paths["source_repo"] / "ACTIVE_GOAL_STATE.md"
    if endpoint != "source":
        runtime = paths["target_runtime"] if endpoint == "target" else tmp_path / "previous-runtime"
        goal_id = NEW_GOAL_ID if endpoint == "target" else "retained-goal"
        state = paths["target_repo"] / "RETAINED_STATE.md"
        state.write_text("# Existing destination work\n")
        paths["target_registry"].write_text(json.dumps({
            "schema_version": 1, "common_runtime_root": str(runtime),
            "goals": [{"id": goal_id, "repo": str(paths["target_repo"]), "state_file": state.name}],
        }))
    initialize_canonical_authority(runtime, goal_id, {"goal_id": goal_id, "retained": True},
                                   state_path=state, provider=provider)
    # The preview's validity must come from the real selected authority/fence,
    # not from recognizing a particular registry spelling or a copied label.
    def retained_files() -> dict[str, bytes]:
        # The shared Effect transport may start a helper in the test's TMPDIR.
        # Compare every source/target file, not that unrelated helper locator.
        roots = [*paths.values(), tmp_path / "previous-runtime"]
        return {str(path.relative_to(tmp_path)): path.read_bytes()
                for root in roots for path in ([root] if root.is_file() else root.rglob("*"))
                if path.is_file()}

    before = retained_files()
    with pytest.raises(LegacyCoordinationWriterFenced):
        _migrate(paths, execute=False)
    assert retained_files() == before
    assert not (paths["target_repo"] / "ACTIVE_GOAL_STATE.md").exists()


def test_execute_rechecks_authority_after_an_eligible_preview(tmp_path: Path, monkeypatch) -> None:
    from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    paths = _migration_fixture(tmp_path)
    assert _migrate(paths, execute=False)["ok"] is True
    state = paths["source_repo"] / "ACTIVE_GOAL_STATE.md"
    initialize_canonical_authority(paths["legacy_runtime"], OLD_GOAL_ID,
        {"goal_id": OLD_GOAL_ID, "retained": True}, state_path=state)
    before = state.read_bytes()
    with pytest.raises(LegacyCoordinationWriterFenced):
        _migrate(paths, execute=True)
    assert state.read_bytes() == before
    assert not (paths["target_repo"] / state.name).exists()
    assert not paths["target_registry"].exists()
    assert not (paths["target_runtime"] / "authority").exists()


@pytest.mark.parametrize("provider", [None, "file", "sqlite"])
def test_public_migration_preview_reports_actual_source_eligibility(
    tmp_path: Path, monkeypatch, provider: str | None,
) -> None:
    from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    paths = _migration_fixture(tmp_path)
    if provider is not None:
        initialize_canonical_authority(paths["legacy_runtime"], OLD_GOAL_ID,
            {"goal_id": OLD_GOAL_ID, "retained": True},
            state_path=paths["source_repo"] / "ACTIVE_GOAL_STATE.md", provider=provider)
    command = [sys.executable, "-I", "-m", "loopx.cli", "--format", "json",
        "--registry", str(paths["target_registry"]), "migrate-state",
        "--legacy-registry", str(paths["legacy_registry"]),
        "--legacy-runtime-root", str(paths["legacy_runtime"]),
        "--target-runtime-root", str(paths["target_runtime"]), "--goal-id", OLD_GOAL_ID,
        "--goal-id-map", f"{OLD_GOAL_ID}={NEW_GOAL_ID}",
        "--path-map", f"{paths['source_repo']}={paths['target_repo']}",
        "--copy-active-state", "--copy-runtime"]
    child = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    result = json.loads(child.stdout)
    assert result["dry_run"] is True
    assert result["ok"] is (provider is None), result
    assert child.returncode == (0 if provider is None else 1)
    if provider is not None:
        assert result["write_check"]["status"] == "blocked"
        assert result["error_code"] == result["write_check"]["reason_code"]
    assert not paths["target_registry"].exists()
    assert not (paths["target_repo"] / "ACTIVE_GOAL_STATE.md").exists()
    assert not paths["target_runtime"].exists()
