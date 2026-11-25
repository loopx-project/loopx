from __future__ import annotations

import json
from pathlib import Path

import pytest

import loopx.agent_registry as agent_registry
import loopx.control_plane.projects.registry_codec as registry_codec
from loopx.control_plane.capability_hooks import (
    capability_hook_request_scope,
    current_hook_request_scope,
    load_scoped_registry,
)


@pytest.mark.parametrize("use_symlink_alias", [False, True])
def test_transaction_create_update_remove_invalidates_request_scope(
    tmp_path: Path,
    use_symlink_alias: bool,
) -> None:
    real_reg = tmp_path / "real" / "registry.json"
    read_path = tmp_path / "alias" / "registry.json" if use_symlink_alias else real_reg
    if use_symlink_alias:
        read_path.parent.mkdir()
        read_path.symlink_to(real_reg)

    with capability_hook_request_scope():
        with registry_codec.project_registry_transaction(
            real_reg,
            operation="test_create",
            create=lambda: {"goals": [{"id": "g1", "title": "v1"}]},
        ) as tx:
            assert tx.commit({"goals": [{"id": "g1", "title": "v1"}]}) is True

        goal_v1 = agent_registry.load_goal_from_registry(read_path, "g1")
        assert goal_v1 is not None and goal_v1["title"] == "v1"
        if use_symlink_alias:
            direct_v1 = agent_registry.load_goal_from_registry(real_reg, "g1")
            assert direct_v1 is not None and direct_v1["title"] == "v1"

        with registry_codec.project_registry_transaction(
            real_reg,
            operation="test_update",
        ) as tx:
            payload = tx.payload_copy()
            payload["goals"][0]["title"] = "v2"
            assert tx.commit(payload) is True

        goal_v2 = agent_registry.load_goal_from_registry(read_path, "g1")
        assert goal_v2 is not None and goal_v2["title"] == "v2"
        if use_symlink_alias:
            direct_v2 = agent_registry.load_goal_from_registry(real_reg, "g1")
            assert direct_v2 is not None and direct_v2["title"] == "v2"

        with registry_codec.project_registry_transaction(
            real_reg,
            operation="test_remove",
        ) as tx:
            assert tx.remove() is True

        assert not real_reg.exists()
        assert agent_registry.load_goal_from_registry(read_path, "g1") is None
        assert load_scoped_registry(read_path, loader=registry_codec.load_registry) == {}
        if use_symlink_alias:
            assert agent_registry.load_goal_from_registry(real_reg, "g1") is None
            assert load_scoped_registry(real_reg, loader=registry_codec.load_registry) == {}

    assert current_hook_request_scope() is None
    assert agent_registry.load_goal_from_registry(read_path, "g1") is None
    assert agent_registry.load_goal_from_registry(real_reg, "g1") is None


def test_transaction_remove_missing_path_clears_stale_snapshot_before_returning_false(
    tmp_path: Path,
) -> None:
    missing_reg = tmp_path / "missing" / "registry.json"
    missing_reg.parent.mkdir()

    with capability_hook_request_scope() as scope:
        scope.registry_snapshots[missing_reg.absolute()] = json.dumps(
            {"goals": [{"id": "g-stale", "title": "stale"}]}
        ).encode("utf-8")
        stale_goal = agent_registry.load_goal_from_registry(missing_reg, "g-stale")
        assert stale_goal is not None and stale_goal["title"] == "stale"

        missing_reg.write_text("{}", encoding="utf-8")
        with registry_codec.project_registry_transaction(
            missing_reg,
            operation="test_remove_missing",
        ) as tx:
            missing_reg.unlink()
            assert tx.remove() is False

        assert agent_registry.load_goal_from_registry(missing_reg, "g-stale") is None
        assert load_scoped_registry(missing_reg, loader=registry_codec.load_registry) == {}


def test_transaction_restore_on_new_registry_invalidates_request_scope(
    tmp_path: Path,
) -> None:
    new_reg = tmp_path / "new" / "registry.json"
    alias = tmp_path / "alias" / "registry.json"
    alias.parent.mkdir()
    alias.symlink_to(new_reg)

    with capability_hook_request_scope():
        with registry_codec.project_registry_transaction(
            new_reg,
            operation="test_create_then_restore",
            create=lambda: {"goals": [{"id": "g-new", "title": "brand-new"}]},
        ) as tx:
            assert tx.commit({"goals": [{"id": "g-new", "title": "brand-new"}]}) is True
            created_direct = agent_registry.load_goal_from_registry(new_reg, "g-new")
            created_alias = agent_registry.load_goal_from_registry(alias, "g-new")
            assert created_direct is not None and created_direct["title"] == "brand-new"
            assert created_alias is not None and created_alias["title"] == "brand-new"

            tx.restore()

        assert not new_reg.exists()
        assert agent_registry.load_goal_from_registry(new_reg, "g-new") is None
        assert agent_registry.load_goal_from_registry(alias, "g-new") is None
        assert load_scoped_registry(new_reg, loader=registry_codec.load_registry) == {}
        assert load_scoped_registry(alias, loader=registry_codec.load_registry) == {}

    assert current_hook_request_scope() is None
    assert agent_registry.load_goal_from_registry(new_reg, "g-new") is None
    assert agent_registry.load_goal_from_registry(alias, "g-new") is None


def test_warm_canonical_source_tracks_alias_invalidation_without_unrelated_eviction(
    tmp_path: Path,
) -> None:
    reg_b = tmp_path / "b" / "registry.json"
    reg_c = tmp_path / "c" / "registry.json"
    alias = tmp_path / "alias" / "registry.json"
    for path, gid, title in (
        (reg_b, "g1", "b-v1"),
        (reg_c, "g-c", "c-v1"),
    ):
        path.parent.mkdir()
        path.write_text(json.dumps({"goals": [{"id": gid, "title": title}]}), encoding="utf-8")
    alias.parent.mkdir()
    alias.symlink_to(reg_b)

    with capability_hook_request_scope() as scope:
        warm_b = agent_registry.load_goal_from_registry(reg_b, "g1")
        assert warm_b is not None and warm_b["title"] == "b-v1"
        alias_v1 = agent_registry.load_goal_from_registry(alias, "g1")
        assert alias_v1 is not None and alias_v1["title"] == "b-v1"

        # Committing an unrelated registry C must not evict alias -> B.
        with registry_codec.project_registry_transaction(reg_c, operation="test_unrelated_c") as tx:
            payload_c = tx.payload_copy()
            payload_c["goals"][0]["title"] = "c-v2"
            assert tx.commit(payload_c) is True
        assert alias.absolute() in scope.registry_snapshots
        assert (alias.absolute(), "registry.json") in scope._decoded_registries
        still_b = agent_registry.load_goal_from_registry(alias, "g1")
        assert still_b is not None and still_b["title"] == "b-v1"

        # Committing B after B was already warm must invalidate alias -> B immediately.
        with registry_codec.project_registry_transaction(reg_b, operation="test_commit_warm_b") as tx:
            payload_b = tx.payload_copy()
            payload_b["goals"][0]["title"] = "b-v2"
            assert tx.commit(payload_b) is True
        assert alias.absolute() not in scope.registry_snapshots
        alias_v2 = agent_registry.load_goal_from_registry(alias, "g1")
        assert alias_v2 is not None and alias_v2["title"] == "b-v2"
        loaded_v2 = load_scoped_registry(alias, loader=registry_codec.load_registry)
        goals_v2 = loaded_v2["goals"]
        assert isinstance(goals_v2, list) and isinstance(goals_v2[0], dict)
        assert goals_v2[0]["title"] == "b-v2"

    outside_v2 = agent_registry.load_goal_from_registry(alias, "g1")
    assert outside_v2 is not None and outside_v2["title"] == "b-v2"


def test_warm_canonical_source_captures_aba_race_invalidation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reg_a = tmp_path / "a" / "registry.json"
    reg_b = tmp_path / "b" / "registry.json"
    alias = tmp_path / "alias" / "registry.json"
    for path, title in ((reg_a, "a-v1"), (reg_b, "b-v1")):
        path.parent.mkdir()
        path.write_text(json.dumps({"goals": [{"id": "g1", "title": title}]}), encoding="utf-8")
    alias.parent.mkdir()
    alias.symlink_to(reg_a)

    orig_resolve = Path.resolve
    alias_resolve_calls = 0

    def _aba_race_resolve(self: Path, strict: bool = False) -> Path:
        nonlocal alias_resolve_calls
        target = orig_resolve(self, strict=strict)
        if self.parent.name == "alias" and self.name == "registry.json":
            alias_resolve_calls += 1
            if alias_resolve_calls == 1:
                alias.unlink()
                alias.symlink_to(reg_b)
            elif alias_resolve_calls == 2:
                alias.unlink()
                alias.symlink_to(reg_a)
        return target

    with capability_hook_request_scope() as scope:
        warm_b = agent_registry.load_goal_from_registry(reg_b, "g1")
        assert warm_b is not None and warm_b["title"] == "b-v1"

        monkeypatch.setattr(Path, "resolve", _aba_race_resolve)
        alias_aba = agent_registry.load_goal_from_registry(alias, "g1")
        assert alias_aba is not None and alias_aba["title"] == "b-v1"
        monkeypatch.undo()

        reg_a_direct = agent_registry.load_goal_from_registry(reg_a, "g1")
        assert reg_a_direct is not None and reg_a_direct["title"] == "a-v1"

        # Committing B must still evict the ABA-hit alias snapshot observed from warm B.
        with registry_codec.project_registry_transaction(reg_b, operation="test_commit_b_after_aba") as tx:
            payload_b = tx.payload_copy()
            payload_b["goals"][0]["title"] = "b-v2"
            assert tx.commit(payload_b) is True
        assert alias.absolute() not in scope.registry_snapshots
        refreshed_alias = agent_registry.load_goal_from_registry(alias, "g1")
        assert refreshed_alias is not None and refreshed_alias["title"] == "a-v1"
