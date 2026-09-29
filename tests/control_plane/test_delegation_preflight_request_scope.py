"""Inspection reuses executable source, never binding or acceptance state."""

import json
from pathlib import Path

import pytest

from loopx.control_plane import effect_runtime
from test_local_delegation import service as delegation_service

service = delegation_service


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
