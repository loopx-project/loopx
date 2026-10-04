"""Real SQLite loss at either acceptance read must not expose a stale preview."""

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from loopx import collaboration_mcp as delegation
from test_local_delegation import service as delegation_service


@pytest.fixture
def sqlite_service(tmp_path, request, monkeypatch):
    return delegation_service.__wrapped__(
        tmp_path,
        SimpleNamespace(param="sqlite", addfinalizer=request.addfinalizer),
        monkeypatch
    )


@pytest.mark.parametrize("reuse_preview", [False, True])
@pytest.mark.parametrize("failed_capture", [1, 2])
def test_real_sqlite_unavailable_on_either_read_recovers_without_effects(
    sqlite_service, monkeypatch, reuse_preview, failed_capture
):
    root, original = sqlite_service
    runner = delegation.Delegations(
        original.root, original.registry, original.goal_id, original.agent_id,
        original.config, reuse_preview=reuse_preview,
    )
    database, = (runner.root / "authority" / "sqlite-v0").glob("*.sqlite")
    source = runner.registry.read_bytes(), runner.config.read_bytes()
    database_hash = hashlib.sha256(database.read_bytes()).hexdigest()
    capture = delegation.delegation_validation.capture
    calls = 0

    def lose_database_at_read(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls != failed_capture:
            return capture(*args, **kwargs)
        # Move only this owned synthetic database; the real TS provider must
        # fail closed without creating a replacement or using a File fallback.
        retained = database.with_suffix(".retained")
        database.rename(retained)
        try:
            return capture(*args, **kwargs)
        finally:
            retained.rename(database)

    try:
        before = runner.inspect("analysis")
        with monkeypatch.context() as patch:
            patch.setattr(delegation.delegation_validation, "capture", lose_database_at_read)
            result = runner.inspect("analysis")
        assert calls == failed_capture
        assert result["state"] == "authority_unavailable"
        assert result["authority_state"] == "unavailable"
        assert result["authority_next_action"] == "repair_canonical_authority"
        assert result["authority_reason"] == "local_authority_provider_missing"
        assert result["turn_eligible"] is False and result["turn_route"] is None
        assert result["authority_ready"] is False and result["acceptance_ready"] is False
        assert result["executor"] is None
        assert result["promotion_from_surface_allowed"] is False
        assert not any(result["effects"].values())
        assert runner.inspect("analysis") == before
        assert (runner.registry.read_bytes(), runner.config.read_bytes()) == source
        assert hashlib.sha256(database.read_bytes()).hexdigest() == database_hash
        assert not (root / "host-started").exists()
        assert not list((runner.root / "goals").glob("*/turns/*.json"))
        assert not list(runner.path("inventory").parent.glob("*.json"))
    finally:
        if runner._preview_transport is not None:
            runner._preview_transport.close()


@pytest.mark.parametrize("reuse_preview", [False, True])
@pytest.mark.parametrize("replacement", [False, True])
def test_workspace_fault_precedes_final_authority_error(
    sqlite_service, monkeypatch, reuse_preview, replacement
):
    root, original = sqlite_service
    runner = delegation.Delegations(
        original.root, original.registry, original.goal_id, original.agent_id,
        original.config, reuse_preview=reuse_preview,
    )
    workspace = Path(runner.binding("analysis", require_active=True)["workspace"])
    retained = root / "retained-worker"
    capture = delegation.delegation_validation.capture
    calls = 0

    def lose_workspace_and_authority(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return capture(*args, **kwargs)
        workspace.rename(retained)
        if replacement:
            workspace.mkdir()
        raise ValueError("local_authority_provider_open_failed")

    try:
        before = runner.inspect("analysis")
        with monkeypatch.context() as patch:
            patch.setattr(delegation.delegation_validation, "capture", lose_workspace_and_authority)
            result = runner.inspect("analysis")
        assert calls == 2
        assert result["state"] == "workspace_unavailable"
        assert result["workspace_state"] == ("unavailable" if replacement else "missing")
        assert result["authority_ready"] is None and result["executor"] is None
        assert result["turn_eligible"] is False
        assert not any(result["effects"].values())
        if replacement:
            workspace.rmdir()  # Only the empty synthetic replacement created above.
        retained.rename(workspace)
        assert runner.inspect("analysis") == before
    finally:
        if retained.exists():
            if replacement and workspace.exists():
                workspace.rmdir()
            retained.rename(workspace)
        if runner._preview_transport is not None:
            runner._preview_transport.close()
