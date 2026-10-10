"""Protocol, configuration, installed-off and one-shot failure contracts."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from loopx_jev.config import load_config, strict_json
from loopx_jev.progress import DOMAINS, decode_assessment
from loopx_jev.protocol import validate_choice
from loopx_jev.transport import send, TransportFailure


@pytest.mark.parametrize("code", ["unsupported_method", "invalid_request", "io_permission_denied"])
def test_manual_basis_preserves_only_the_typed_legacy_method_boundary(tmp_path, monkeypatch, code):
    from loopx.control_plane.effect_runtime import EffectRuntimeRemoteError, EffectRuntimeRejected, EffectRuntimePermanentIOError
    from loopx_jev.runner import read_basis

    path = tmp_path / "basis.json"
    manifest = {"goal_id": "study", "objective": "Study a declared file",
                "acceptance": ["Observe its net change"], "evidence": [{"ref": "code.py"}]}
    path.write_text(json.dumps(manifest))
    evidence = tmp_path / "code.py"
    evidence.write_text("before")
    calls = []
    def unsupported(method, params):
        calls.append(method)
        if code == "io_permission_denied":
            raise EffectRuntimePermanentIOError("Fixture receipt store denied access", diagnostic_code=code)
        raise EffectRuntimeRejected("Fixture core rejected the method", diagnostic_code=code)
    monkeypatch.setattr("loopx.control_plane.effect_runtime.effect_runtime_result", unsupported)
    if code != "unsupported_method":
        with pytest.raises(EffectRuntimeRemoteError):
            read_basis(path, tmp_path)
    else:
        basis, current = read_basis(path, tmp_path)
        assert basis["acceptance"] == manifest["acceptance"]
        assert "criterion_binding" not in basis
        assert current()
        evidence.write_text("after")
        assert not current()
    assert calls == ["progress_review.criterion_basis"]


@pytest.mark.parametrize("raw", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'])
def test_ambiguous_json_rejected(raw):
    with pytest.raises(ValueError):
        strict_json(raw)


@pytest.mark.parametrize(
    "patch",
    [
        {"mode": "assist"},
        {"ranking_policy": "pairwise"},
        {"allow_egress": "yes"},
        {"mode": "shadow", "model": "latest"},
        {"scenarios": ["todo_order"]},
        {"limits": {"max_requests_per_run": True}},
        {"limits": {"deadline_ms": 0}},
        {"minimum_label_probability": float("inf")},
        {"schema_version": "loopx_jev_branch_config_v0"},
    ],
)
def test_invalid_or_old_pilot_configuration_is_not_promoted(tmp_path, patch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"schema_version": "loopx_jev_drift_config_v0", **patch}))
    with pytest.raises(ValueError):
        load_config(p)


@pytest.mark.parametrize(
    "probabilities",
    [
        {"on_goal": True},
        {"on_goal": float("nan")},
        {"on_goal": 1.1},
        {"on_goal": 0.5},
    ],
)
def test_invalid_probability_never_reaches_a_judgment(probabilities):
    with pytest.raises(ValueError):
        validate_choice(
            {"type": "choice", "choice": "on_goal", "probabilities": probabilities},
            ("on_goal",),
        )


def test_high_confidence_does_not_replace_selected_label_probability():
    answer = {
        "type": "choice",
        "choice": "on_goal",
        "confidence": 1,
        "probabilities": {
            "on_goal": 0.4,
            "necessary_prerequisite": 0.2,
            "off_goal": 0.2,
            "unknown": 0.2,
        },
    }
    increment = {
        "type": "choice",
        "choice": "new_evidence",
        "probabilities": {"new_evidence": 1.0, "no_new_evidence": 0.0, "unknown": 0.0},
    }
    nouls = {
        name: {"type": "noul", "noul": 0.5}
        for name in ("behavior_change", "serves_acceptance", "evidence_increment")
    }
    result = decode_assessment(
        {"model": "fixture", "answers": {"relation": answer, "increment": increment, **nouls}},
        {"facts": {"history_available": False}},
        "fixture",
        0.6,
    )
    assert result["judgments"] == {"relation": "unknown", "increment": "unknown"}
    # Missing history withholds both increment judgments; the two other Noul
    # probabilities are present but sit in the undecided band.
    assert result["noul"]["evidence_increment"] is None
    assert result["coverage"] == {"decided": 0, "total": 5}
    assert result["drift_signal"] == {"noul": None, "choice": None}
    assert set(DOMAINS) == {"relation", "increment"}


def test_source_only_off_command_loads_no_transport(tmp_path):
    root = Path(__file__).resolve().parents[3]
    code = """import sys
from loopx_jev.cli import main
assert main(['drift','drain','--state-dir','absent']) == 0
assert 'loopx_jev.transport' not in sys.modules
assert 'loopx_jev.runner' not in sys.modules
"""
    process = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join(
                [str(root / "packages/loopx-jev/src"), str(root)]
            ),
        },
        timeout=10,
    )
    assert process.returncode == 0, process.stderr
    assert not (tmp_path / "absent").exists()


def test_transport_does_not_normalize_duplicate_remote_keys(monkeypatch):
    from loopx_jev.config import Config

    class Child:
        returncode = 0

        def communicate(self, *args, **kwargs):
            return b'{"response":{"model":"a","model":"b"}}', b""

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: Child())
    with pytest.raises(TransportFailure, match="invalid_transport_response"):
        send({}, Config(), "fixture")


@pytest.mark.parametrize(
    "args,expected",
    [
        (["refresh-state", "--goal-id", "example"], True),
        (["--registry", "registry.json", "--format=json", "refresh-state"], True),
        (["status", "--goal-id", "refresh-state"], False),
        (["--registry", "refresh-state", "status"], False),
        (["--registry=", "refresh-state"], False),
    ],
)
def test_only_actual_refresh_command_is_observed(args, expected):
    from loopx_jev.drift_cli import _refresh_command

    assert _refresh_command(args) is expected


def test_unavailable_platform_file_primitives_are_explicit(tmp_path, monkeypatch):
    from loopx_jev.drift_capture import capture

    monkeypatch.delattr(os, "O_NOFOLLOW")
    with pytest.raises(ValueError, match="unsupported_capture_platform"):
        capture(tmp_path, ["file.txt"])
