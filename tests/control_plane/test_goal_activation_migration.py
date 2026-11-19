"""Retire old pause metadata without resuming work or losing registry content."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.control_plane.goals.activation_service import upgrade_goal_activation_registries
from loopx.control_plane.projects.registry_codec import (
    SOURCE_SESSION_WRITER_PROTOCOL,
    STRICT_SCHEMA_VERSION,
    CURRENT_WRITER_PROTOCOL,
    SOURCE_SESSION_SCHEMA_VERSION,
    decode_project_registry,
    load_registry,
    load_project_registry,
    project_registry_transaction,
    source_session_registry_transaction,
)


@pytest.mark.parametrize("wire", ["object", "v1", "v2"])
def test_upgrade_is_backup_first_lossless_and_idempotent(tmp_path, wire):
    path = tmp_path / "registry.json"
    goal = {"id": "example", "status": "active", "goal_instance_id": "original",
            "metadata": {"nested": [None, False, "原文"], "opaque": 12345678901234567890},
            "activation": {"schema_version": "loopx_goal_activation_v1", "state": "active",
                           "updated_at": "2026-01-01T00:00:00Z", "reason": "Original reason",
                           "metadata": {"large": 12345678901234567890, "fraction": 0.123456789}}}
    payload = {"goals": [goal, {"id": "unrelated", "status": "archived"}],
               "projects": [{"project_id": "project"}], "opaque": [1, {"keep": True}]}
    factory = project_registry_transaction
    if wire == "v2":
        payload["profile_id"] = "source_session_v1"
        factory = source_session_registry_transaction
    with factory(path, operation="fixture", create=lambda: payload) as transaction:
        transaction.commit(payload)
    # A historical file is authored outside the current writer on purpose.
    document = json.loads(path.read_bytes())
    old = document if wire == "object" else payload
    if wire != "v2":
        old["goals"][0]["status"] = "paused"
    old["goals"][0]["activation_state"] = "active"
    if wire != "object":
        from loopx.configuration_transaction import configuration_payload_revision
        protocol = SOURCE_SESSION_WRITER_PROTOCOL if wire == "v2" else CURRENT_WRITER_PROTOCOL
        schema = SOURCE_SESSION_SCHEMA_VERSION if wire == "v2" else STRICT_SCHEMA_VERSION
        document = [{"schema_version": schema, "minimum_writer_protocol": protocol,
                     "payload_sha256": configuration_payload_revision(old)}, old]
    path.write_text(json.dumps(document, ensure_ascii=False, indent=3) + "\n")
    original = path.read_bytes()
    with pytest.raises(ValueError, match="activation metadata requires upgrade"):
        load_project_registry(path)
    with pytest.raises(ValueError, match="activation metadata requires upgrade"):
        with factory(path, operation="ordinary writer"):
            pytest.fail("ordinary writer must reject before business effects")
    preview = upgrade_goal_activation_registries([path], execute=False)
    assert preview["results"][0]["status"] == "planned"
    assert path.read_bytes() == original and not (tmp_path / "backups").exists()
    result = upgrade_goal_activation_registries([path], execute=True)
    assert result["results"][0]["status"] == "upgraded"
    assert Path(result["results"][0]["backup"]).read_bytes() == original
    actual = load_project_registry(path)
    expected = decode_project_registry(original)
    expected["goals"][0]["status"] = "active"
    expected["goals"][0].pop("activation_state")
    expected["goals"][0]["activation"]["state"] = "active" if wire == "v2" else "stopped"
    assert actual == expected
    if wire != "object":
        assert json.loads(path.read_bytes())[0]["minimum_writer_protocol"] == protocol
    migrated = path.read_bytes()
    assert upgrade_goal_activation_registries([path], execute=True)["results"][0]["status"] == "current"
    assert path.read_bytes() == migrated


@pytest.mark.parametrize("old_side", ["source", "shared"])
def test_migration_preserves_pause_across_exact_source_projection(tmp_path, old_side):
    source, shared = tmp_path / "registry.json", tmp_path / "registry.global.json"
    for path in (source, shared):
        goal = {"id": "same", "goal_instance_id": "lifetime", "status": "active"}
        if path == shared:
            goal["source_registry"] = str(source)
        if path == (source if old_side == "source" else shared):
            goal["status"] = "paused"
        path.write_text(json.dumps({"goals": [goal]}))
    result = upgrade_goal_activation_registries([shared, source], execute=True)
    assert all(row["status"] == "upgraded" for row in result["results"])
    for path in (source, shared):
        goal = load_registry(path)["goals"][0]
        assert goal["activation"]["state"] == "stopped" and goal["status"] == "active"


def test_old_projection_cannot_stop_a_recreated_goal(tmp_path):
    source, shared = tmp_path / "registry.json", tmp_path / "registry.global.json"
    source.write_text(json.dumps({"goals": [{"id": "same", "goal_instance_id": "new", "status": "active"}]}))
    shared.write_text(json.dumps({"goals": [{"id": "same", "goal_instance_id": "old", "status": "paused",
                                          "source_registry": str(source)}]}))
    before = source.read_bytes()
    upgrade_goal_activation_registries([source, shared], execute=True)
    assert source.read_bytes() == before
    assert load_registry(shared)["goals"][0]["activation"]["state"] == "stopped"


def test_changed_source_route_requires_rediscovery_before_any_write(tmp_path):
    selected, changed, shared = tmp_path / "old.json", tmp_path / "new.json", tmp_path / "registry.global.json"
    for path in (selected, changed):
        path.write_text(json.dumps({"goals": [{"id": "example", "status": "paused"}]}))
    shared.write_text(json.dumps({"goals": [{"id": "example", "status": "paused", "source_registry": str(changed)}]}))
    before = {path: path.read_bytes() for path in (selected, changed, shared)}
    with pytest.raises(ValueError, match="rediscover and retry"):
        upgrade_goal_activation_registries([selected, shared], execute=True)
    assert {path: path.read_bytes() for path in before} == before and not (tmp_path / "backups").exists()


def test_lifecycle_only_profile_never_gains_execution_admission_from_migration(tmp_path):
    path = tmp_path / "registry.json"
    payload = {"profile_id": "source_session_v1", "goals": [{"id": "example", "status": "active"}]}
    with source_session_registry_transaction(path, operation="fixture", create=lambda: payload) as transaction:
        transaction.commit(payload)
    from loopx.configuration_transaction import configuration_payload_revision
    document = json.loads(path.read_bytes())
    document[1]["goals"][0]["status"] = "paused"
    document[0]["payload_sha256"] = configuration_payload_revision(document[1])
    path.write_text(json.dumps(document))
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="lifecycle owner supports Resume"):
        upgrade_goal_activation_registries([path], execute=False)
    assert path.read_bytes() == before and not (tmp_path / "backups").exists()


@pytest.mark.parametrize("alias", [None, "active", "stopped"])
def test_alias_retirement_preserves_current_stop(tmp_path, alias):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"goals": [{"id": "example", "activation_state": alias,
        "activation": {"schema_version": "loopx_goal_activation_v1", "state": "stopped"}}]}))
    upgrade_goal_activation_registries([path], execute=True)
    goal = load_registry(path)["goals"][0]
    assert "activation_state" not in goal and goal["activation"]["state"] == "stopped"


@pytest.mark.parametrize("stored_alias", [False, True])
def test_null_activation_retains_the_existing_active_default(tmp_path, stored_alias):
    path = tmp_path / "registry.json"
    goal = {"id": "example", "status": "active", "activation": None}
    if stored_alias:
        goal["activation_state"] = None
    path.write_text(json.dumps({"goals": [goal]}))
    before = path.read_bytes()
    upgraded = upgrade_goal_activation_registries([path], execute=True)
    actual = load_registry(path)["goals"][0]
    if stored_alias:
        assert "activation_state" not in actual and actual["activation"]["state"] == "active"
    else:
        assert upgraded["results"][0]["status"] == "current" and path.read_bytes() == before


def test_failed_shared_commit_can_resume_without_losing_the_source_pause(tmp_path, monkeypatch):
    from loopx.control_plane.projects.registry_codec import ProjectRegistryTransaction
    source, shared = tmp_path / "registry.json", tmp_path / "registry.global.json"
    source.write_text(json.dumps({"goals": [{"id": "example", "status": "active"}]}))
    shared.write_text(json.dumps({"goals": [{"id": "example", "status": "paused", "source_registry": str(source)}]}))
    commit = ProjectRegistryTransaction.commit

    def fail_shared(self, payload):
        if self.source_read_witness()["path"] == str(shared):
            raise OSError("Synthetic shared commit failure")
        return commit(self, payload)

    with monkeypatch.context() as patch:
        patch.setattr(ProjectRegistryTransaction, "commit", fail_shared)
        with pytest.raises(OSError, match="Synthetic shared commit failure"):
            upgrade_goal_activation_registries([source, shared], execute=True)
    first_source = source.read_bytes()
    assert load_registry(source)["goals"][0]["activation"]["state"] == "stopped"
    upgrade_goal_activation_registries([source, shared], execute=True)
    assert source.read_bytes() == first_source
    assert load_registry(shared)["goals"][0]["activation"]["state"] == "stopped"


def test_backup_failure_prevents_every_registry_commit(tmp_path, monkeypatch):
    from loopx.control_plane.projects.registry_codec import ProjectRegistryTransaction
    paths = [tmp_path / "a.json", tmp_path / "b.json"]
    for path in paths:
        path.write_text(json.dumps({"goals": [{"id": path.stem, "status": "paused"}]}))
    before = {path: path.read_bytes() for path in paths}
    backup = ProjectRegistryTransaction.backup_preimage

    def fail_second(self):
        if self.source_read_witness()["path"] == str(paths[1]):
            raise OSError("Synthetic backup failure")
        return backup(self)

    monkeypatch.setattr(ProjectRegistryTransaction, "backup_preimage", fail_second)
    with pytest.raises(OSError, match="Synthetic backup failure"):
        upgrade_goal_activation_registries(paths, execute=True)
    assert {path: path.read_bytes() for path in paths} == before


@pytest.mark.parametrize("field,value", [("status", "paused"), ("status", "stopped"), ("activation_state", "active")])
def test_normal_writer_cannot_reintroduce_retired_pause_metadata(tmp_path, field, value):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"goals": [{"id": "example", "status": "active"}]}))
    before = path.read_bytes()
    with project_registry_transaction(path, operation="fixture") as transaction:
        payload = transaction.payload_copy()
        payload["goals"][0][field] = value
        with pytest.raises(ValueError, match="activation metadata requires upgrade"):
            transaction.commit(payload)
    assert path.read_bytes() == before
