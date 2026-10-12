"""Retained shadow diagnostics must survive source-producer retirement."""
from __future__ import annotations

import json
import subprocess

import pytest

from canonical_authority_fixture import initialize_canonical_authority
from test_canonical_todo_writer_isolation import (
    isolated_todo_distribution, retirement_wheel,  # noqa: F401 -- distribution fixtures
)
from test_local_authority_shadow_outbox import (
    GOAL_ID, _capture, _fixture, _planned_todo, _record_change,
)
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.todos import list_goal_todos


def _remove_capture_producers(package):
    for name in ("runtime_shadow", "runtime_shadow_writer_adapter",
                 "local_authority_shadow_adapter", "local_authority_shadow_outbox"):
        (package / "control_plane/coordination" / f"{name}.py").unlink()
    (package / "control_plane/todos/legacy_mutation.py").unlink()


@pytest.mark.parametrize("configuration", [None, {"enabled": False}, {"enabled": True}])
def test_empty_status_without_capture_producers_preserves_default_off(
    tmp_path, configuration, isolated_todo_distribution,  # noqa: F811
):
    package, command = isolated_todo_distribution
    registry, state, runtime = _fixture(tmp_path, bootstrap=False)
    if configuration is not None:
        config = json.loads(registry.read_text())
        # Retired legacy observation configuration has no runtime schema.
        config["goals"][0]["coordination"]["runtime_shadow"] = configuration
        registry.write_text(json.dumps(config))
    original = registry.read_bytes(), state.read_bytes()

    def status():
        process = subprocess.run(
            [*command, "--registry", str(registry), "--runtime-root", str(runtime),
             "--format", "json", "authority-shadow", "status", "--goal-id", GOAL_ID],
            cwd=tmp_path, capture_output=True, text=True, timeout=60,
        )
        assert process.returncode == 0, process.stdout + process.stderr
        result = json.loads(process.stdout)
        assert result["runtime_config"]["enabled"] is False
        assert result["candidate"]["status"] == "missing"
        assert result["management"]["status"] == "missing"
        assert not runtime.exists()
        assert (registry.read_bytes(), state.read_bytes()) == original
        return result

    expected = status()
    _remove_capture_producers(package)
    assert status() == expected


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_retained_status_is_read_only_with_capture_producers_absent(
    tmp_path, provider, isolated_todo_distribution,  # noqa: F811
):
    package, command = isolated_todo_distribution
    registry, state, runtime = _fixture(tmp_path)
    committed = _record_change(registry, state, runtime, "Retained committed work")
    original = state.read_text()
    _, planned = _planned_todo(original, "Prepared work never written")
    prepared = _capture(registry, state, runtime, original_text=original)
    prepared.prepare(planned)
    assert prepared.outcome.failure is None
    todos = list_goal_todos(registry_path=registry, goal_id=GOAL_ID)["todos"]
    initialize_canonical_authority(
        runtime, GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=todos,
                                            handoff_mode="hard_lease"),
        state_path=state, provider=provider,
    )
    directory = runtime / "authority-shadow/outbox" / GOAL_ID / "todos"
    candidate = next((runtime / "authority-shadow/file-v0").glob("authority-store-*.json"))
    original_candidate = candidate.read_bytes()

    def files():
        return {str(p.relative_to(tmp_path)): p.read_bytes()
                for root in (runtime, state.parent) for p in root.rglob("*") if p.is_file()}

    def status():
        before = files()
        process = subprocess.run(
            [*command, "--registry", str(registry), "--runtime-root", str(runtime),
             "--format", "json", "authority-shadow", "status", "--goal-id", GOAL_ID],
            cwd=tmp_path, capture_output=True, text=True, timeout=60,
        )
        assert "Traceback" not in process.stderr, process.stderr
        payload = json.loads(process.stdout)
        assert files() == before, "a status read must not drain, repair or initialize anything"
        assert process.returncode == (0 if payload["ok"] else 1), payload
        return payload

    # Each oracle has independent invariant checks as well as complete
    # base/head parity; original bytes and identities are never rewritten.
    def scenarios():
        answers = {}
        answers["pending"] = status()
        assert answers["pending"]["ok"]
        assert answers["pending"]["outbox"]["todos"]["committed_pending"] == 1
        assert answers["pending"]["outbox"]["todos"]["prepared_only"] == 1
        assert answers["pending"]["candidate"]["codec_agreement"] is True
        assert answers["pending"]["management"]["status"] == "active"

        cursor = directory / "drain-cursor.json"
        cursor.write_text(json.dumps({
            "schema_version": "loopx_local_authority_shadow_drain_cursor_v0",
            "partition": "todos", "last_seq": committed.outcome.seq,
            "last_entry_id": committed.outcome.entry_id,
            "last_partition_digest": committed.outcome.partition_digest,
            "last_cursor": "retained:1", "last_provider_revision": "retained:1",
            "updated_at": "2026-09-03T00:00:00Z",
        }))
        answers["residue"] = status()
        assert answers["residue"]["ok"]
        assert answers["residue"]["outbox"]["todos"]["retired_residue"] == 2
        assert answers["residue"]["outbox"]["todos"]["committed_pending"] == 1
        cursor.write_text('{"invalid_original":true}')
        answers["bad_cursor"] = status()
        assert not answers["bad_cursor"]["ok"]
        assert answers["bad_cursor"]["outbox"]["todos"]["invalid"] == "outbox_file_invalid"
        cursor.unlink()

        extra = directory / "unclassified.json"
        extra.write_text('{"retain_original":true}')
        answers["bad_entry"] = status()
        assert not answers["bad_entry"]["ok"]
        assert answers["bad_entry"]["outbox"]["todos"]["invalid"] == "outbox_file_invalid"
        extra.unlink()

        candidate.write_text('{"damaged_candidate":true}')
        answers["bad_candidate"] = status()
        # The typed store reader returns a failed result for invalid bytes;
        # only transport exceptions become the adapter's unavailable status.
        assert answers["bad_candidate"]["candidate"]["status"] == "failed"
        assert answers["bad_candidate"]["candidate"]["reason_code"] is not None
        candidate.unlink()
        answers["missing_candidate"] = status()
        assert answers["missing_candidate"]["candidate"]["status"] == "missing"
        assert not candidate.exists(), "status must not mint a replacement store identity"
        candidate.write_bytes(original_candidate)
        return answers

    expected = scenarios()
    _remove_capture_producers(package)
    assert scenarios() == expected
    assert scenarios() == expected, "cold repeated CLI reads preserve the original history"
