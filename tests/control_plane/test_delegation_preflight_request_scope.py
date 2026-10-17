"""Inspection reuses executable source, never binding or acceptance state."""

import json
from pathlib import Path

import pytest

from loopx.control_plane import effect_runtime
from test_local_delegation import brief, service as delegation_service

service = delegation_service


def test_each_inventory_page_pins_once_and_reads_current_records(service, monkeypatch):
    root, runner = service
    monkeypatch.setattr(runner, "_spawn", lambda _: None)
    for name in ["first", "second", "third"]:
        runner.start("analysis", name, brief())
    original = effect_runtime._runtime_fingerprint
    revisions = []

    def measured_fingerprint():
        revision = original()
        revisions.append(revision)
        return revision

    monkeypatch.setattr(effect_runtime, "_runtime_fingerprint", measured_fingerprint)
    first = runner.operations(limit=2)
    assert len(revisions) == 1
    assert first["page_readback_complete"] and first["has_more"]
    second = runner.operations(limit=2, cursor=first["next_cursor"])
    assert len(revisions) == 2
    assert second["page_readback_complete"] and len(second["items"]) == 1

    # A page pins executable source, never a previous operation observation.
    runner.path(first["items"][0]["operation_id"]).write_text("{")
    damaged = runner.operations(limit=2)
    assert len(revisions) == 3
    assert not damaged["page_readback_complete"]
    assert not damaged["projection_envelope"]["complete"]
    assert len(damaged["items"]) == 2
    assert not (root / "host-started").exists()


def test_each_inspection_pins_once_and_reads_current_binding(service, monkeypatch):
    root, runner = service
    original = effect_runtime._runtime_fingerprint
    revisions = []

    def measured_fingerprint():
        fingerprint = original()
        revisions.append(fingerprint)
        return fingerprint

    monkeypatch.setattr(effect_runtime, "_runtime_fingerprint", measured_fingerprint)
    first = runner.inspect("analysis")
    assert len(revisions) == 1
    assert first["state"] == "runtime_unverified" and first["turn_eligible"]
    assert not any(first["effects"].values())

    configuration = json.loads(runner.config.read_text())
    configuration["bindings"][0]["workspace"] = str(root / "missing-worker")
    runner.config.write_text(json.dumps(configuration))
    second = runner.inspect("analysis")
    assert len(revisions) == 2
    assert second["state"] == "workspace_unavailable"
    assert second["workspace_state"] == "missing"
    assert not any(second["effects"].values())
    assert not Path(configuration["bindings"][0]["workspace"]).exists()
    assert not list(runner.path("inventory").parent.glob("*.json"))


def test_inspection_still_rejects_source_drift_inside_one_request(service, monkeypatch):
    _, runner = service
    original = runner._cli

    def preview_then_retarget(binding, *args, **kwargs):
        result = original(binding, *args, **kwargs)
        configuration = json.loads(runner.config.read_text())
        configuration["bindings"][0]["workspace"] = str(runner.root / "retargeted")
        runner.config.write_text(json.dumps(configuration))
        return result

    monkeypatch.setattr(runner, "_cli", preview_then_retarget)
    with pytest.raises(ValueError, match="source changed"):
        runner.inspect("analysis")
    assert not list(runner.path("inventory").parent.glob("*.json"))
