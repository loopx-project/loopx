"""Canonical CLI qualification keeps authored input and isolated runtimes."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_source_fixture, isolate_sqlite_runtime
from loopx.control_plane.effect_runtime import _runtime_dir


@pytest.mark.parametrize("cached", [False, True])
def test_runtime_isolation_covers_process_and_cli_temp_directories(tmp_path, monkeypatch, cached):
    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(shared) if cached else None)

    isolate_sqlite_runtime(tmp_path, monkeypatch)

    assert tempfile.gettempdir() == str(tmp_path)
    assert _runtime_dir().parent == tmp_path
    child_root = subprocess.check_output(
        [sys.executable, "-c", "import tempfile; print(tempfile.gettempdir())"], text=True,
    ).strip()
    assert child_root == str(tmp_path)


def test_runtime_isolation_restores_the_process_cache(tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    monkeypatch.setattr(tempfile, "tempdir", str(shared))

    with monkeypatch.context() as isolated:
        isolate_sqlite_runtime(tmp_path, isolated)
        assert _runtime_dir().parent == tmp_path

    assert tempfile.tempdir == str(shared)
    assert _runtime_dir().parent == shared


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_authored_tail_survives_canonical_capture_host_and_settlement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    from tests.control_plane.test_turn_host_decision_capture import _fixture_module

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    fixture = _fixture_module()
    project, runtime, workspace, registry = fixture._write_fixture(tmp_path, turn_count=2)
    state = project / ".codex/goals" / fixture.GOAL_ID / "ACTIVE_GOAL_STATE.md"
    token = "complete-authored-tail"
    task = ("Write docs/source-artifact.txt from the final payload. "
        + "Keep the whole requirement and do not publish. " * 80
        + "Payload: " + token)
    lines = state.read_text().splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith("- [ ]"))
    lines[index] = "- [ ] [P0] " + task
    state.write_text("\n".join(lines) + "\n")
    initialize_canonical_source_fixture(runtime, fixture.GOAL_ID,
        state_path=state, provider=provider)

    # This real subprocess consumes the complete source, independently of the
    # historical effect adapter. Its artifact depends on the clause after the
    # display preview; returning a typed result alone cannot satisfy the test.
    host = """
import json
import pathlib
import sys
request = json.load(sys.stdin)
assert request['schema_version'] == 'loopx_turn_host_request_v1'
assert 'turn_envelope' not in request and 'turn_authority' not in request
source = request['work_contract']
work = source['interaction_contract']['agent_channel']['work_context']
body = next(row['content']['todo']['text'] for row in work['sources']
            if row.get('source') == 'selected_todo')
payload = body.rsplit('Payload: ', 1)[1]
pathlib.Path('docs/source-artifact.txt').write_text(payload)
pathlib.Path('docs/observed-source.txt').write_text(body)
calls = pathlib.Path('docs/source-host-calls.txt')
assert not calls.exists(), 'settlement replay relaunched the Host'
calls.write_text('1')
json.dump(dict(schema_version='loopx_turn_result_v0', turn_key=request['turn_key'],
    result_kind='validated_progress', completed_phases=['host_execute','typed_result'],
    classification='source_body_verified', recommended_action='Continue original work.',
    next_action='Validate the remaining original requirements.',
    delivery_batch_scale='implementation', delivery_outcome='outcome_progress',
    vision_unchanged_reason='The original artifact contract remains unchanged.',
    work_evidence=['artifact:docs/source-artifact.txt'],
    validation_evidence=['fixture:independent-file-validator'],
    summary='The complete authored payload reached the artifact.'), sys.stdout)
"""
    validator = ("import json,pathlib,sys; json.load(sys.stdin); "
        f"assert pathlib.Path('docs/source-artifact.txt').read_text() == {token!r}")
    host_path = tmp_path / "source_host.py"
    host_path.write_text(host)
    argv = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
        "turn", "run-once", "--goal-id", fixture.GOAL_ID, "--agent-id", fixture.AGENT_ID,
        "--host", "generic-cli", "--project", str(workspace),
        "--host-adapter-command-json", json.dumps([sys.executable, str(host_path)]),
        "--host-adapter-request-schema", "loopx_turn_host_request_v1",
        "--validation-command-json", json.dumps([sys.executable, "-c", validator]),
        "--no-global-sync", "--execute"]
    code, first = fixture._run_cli([*argv, "--turn-instance-id", "authored-source-body"])
    assert code == 0 and first["status"] == "committed", first
    assert (workspace / "docs/observed-source.txt").read_text() == "[P0] " + task
    assert (workspace / "docs/source-artifact.txt").read_text() == token
    assert fixture._quota_spend_count(runtime) == 1
    code, replay = fixture._run_cli([*argv, "--resume-turn-key", first["resume_turn_key"]])
    assert code == 0 and replay["status"] == "committed", replay
    assert (workspace / "docs/source-host-calls.txt").read_text() == "1"
    assert fixture._quota_spend_count(runtime) == 1
