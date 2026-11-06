"""A stopped source proof observes existing bytes, and grants no execution."""
from __future__ import annotations

from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import shutil
import tarfile
import time
from types import SimpleNamespace

import pytest

from benchmark.edgebench import stopped_recovery as subject
from benchmark.runtime.interruption_recovery import _stopped_source


def _source(root):
    paths = dict(goal_id="fixture-goal", agent_id="fixture-agent", workspace=str(root / "workspace"),
        runtime_root=str(root / "runtime"), native_home=str(root / "native"),
        registry=str(root / "control/registry.json"), scheduler_state=str(root / "control/state.json"),
        wake_log_dir=str(root / "wakes"), phase_clock=str(root / "control/deadline"),
        wake_turn_id="wake-100000000000-deadbeefabcd")
    for path in [Path(paths[k]) for k in ("workspace", "runtime_root", "native_home", "wake_log_dir")]:
        path.mkdir(parents=True)
    (root / "control").mkdir()
    for file in ("workspace/answer", "runtime/goal", "native/state_5.sqlite",
                 "native/state_5.sqlite-wal", "native/thread_history_1.sqlite", "native/config.toml"):
        (root / file).write_bytes(b"synthetic retained bytes")
    (root / "native/sessions").mkdir()
    (root / "native/sessions/session.jsonl").write_text("{}\n")
    (root / "control/registry.json").write_text("{}")
    (root / "control/deadline").write_text("105")
    state = Path(paths["scheduler_state"])
    state.write_text(json.dumps(dict(host_attempt=dict(status="started", scope="a" * 64, attempt=1))))
    state.with_name(state.name + '.host-attempt').write_text('{"pending":true}')
    os.utime(state, (99, 99))
    receipt = Path(paths["wake_log_dir"]) / paths["wake_turn_id"] / "receipt.json"
    receipt.parent.mkdir()
    receipt.write_text(json.dumps(dict(turn_id=paths["wake_turn_id"], host_invoked=True, budget_exhausted=True,
        mode="heartbeat", stage="execute", ok=False, session=dict(binding_scope="agent", session_id="original"))))
    os.utime(receipt, (110, 110))
    return paths


def _fake_container(monkeypatch):
    box = SimpleNamespace(id="a" * 64, attrs={"State": dict(Status="exited", Running=False, Pid=0,
        FinishedAt="1970-01-01T00:02:00Z"), "Config": dict(Cmd=["tail", "-f", "/dev/null"]), "Mounts": []})
    box.reload = lambda: None
    def archive(container, remote, destination, **kwargs):
        destination.mkdir()
        source = Path(remote)
        if source.is_dir():
            return Path(shutil.copytree(source, destination / source.name))
        copied = Path(shutil.copy2(source, destination / source.name))
        for suffix in ("-wal", "-journal"):
            if Path(remote + suffix).exists():
                shutil.copy2(remote + suffix, str(copied) + suffix)
        return copied
    monkeypatch.setattr(subject, "_archive_component", archive)
    return box, SimpleNamespace(containers=SimpleNamespace(get=lambda identity: box))


@pytest.mark.parametrize("fault", [None, "live", "restart_command", "mount", "ambiguous_wake"])
def test_prepare_stopped_source_keeps_original_state_and_requires_unique_return(tmp_path, monkeypatch, fault):
    declaration = _source(tmp_path)
    box, client = _fake_container(monkeypatch)
    original = Path(declaration["scheduler_state"]).read_bytes()
    if fault == "live": box.attrs["State"].update(Status="running", Running=True, Pid=42)
    elif fault == "restart_command": box.attrs["Config"]["Cmd"] = ["run-original-job"]
    elif fault == "mount": box.attrs["Mounts"] = [dict(Source="/writable")]
    elif fault == "ambiguous_wake":
        second = tmp_path / "wakes/second/receipt.json"
        second.parent.mkdir()
        second.write_text("{}")
    output = tmp_path / "source-proof.json"
    if fault:
        with pytest.raises(ValueError):
            subject.prepare_stopped_recovery(client, container_id=box.id, declaration=declaration, output=output)
        assert not output.exists()
    else:
        result = subject.prepare_stopped_recovery(client, container_id=box.id, declaration=declaration, output=output)
        assert result["execution_authority_granted"] is False
        assert any(p["source_path"].endswith("state_5.sqlite-wal") for p in result["components"])
    assert Path(declaration["scheduler_state"]).read_bytes() == original


@pytest.mark.parametrize("fault", ["workspace", "wal_removed", "new_journal", "session_removed", "state", "wrong_lane"])
def test_consumer_rejects_stale_or_incomplete_latest_source(tmp_path, monkeypatch, fault):
    declaration = _source(tmp_path)
    box, client = _fake_container(monkeypatch)
    proof = tmp_path / "source-proof.json"
    subject.prepare_stopped_recovery(client, container_id=box.id, declaration=declaration, output=proof)
    env = dict(LOOPX_INTERRUPTED_SOURCE=str(proof), LOOPX_GOAL_ID=declaration["goal_id"],
        LOOPX_AGENT_ID=declaration["agent_id"], LOOPX_PROJECT=declaration["workspace"],
        LOOPX_RUNTIME_ROOT=declaration["runtime_root"], CODEX_HOME=declaration["native_home"],
        LOOPX_REGISTRY=declaration["registry"])
    if fault == "workspace": (tmp_path / "workspace/answer").write_text("different")
    elif fault == "wal_removed": (tmp_path / "native/state_5.sqlite-wal").unlink()
    elif fault == "new_journal": (tmp_path / "native/state_5.sqlite-journal").write_text("different")
    elif fault == "session_removed": (tmp_path / "native/sessions/session.jsonl").unlink()
    elif fault == "state": (tmp_path / "control/state.json").write_text("{}")
    elif fault == "wrong_lane": env["LOOPX_AGENT_ID"] = "other"
    with pytest.raises(ValueError):
        _stopped_source(Path(declaration["scheduler_state"]), {}, env, declaration["wake_log_dir"])


@pytest.mark.skipif(os.environ.get("LOOPX_EDGEBENCH_DOCKER_SMOKE") != "1", reason="Explicit disposable Docker qualification")
def test_real_stopped_docker_proof_installation_is_inert_and_read_back(tmp_path):
    docker = pytest.importorskip("docker")
    client = docker.from_env(timeout=30)
    box = client.containers.create("python:3.12-slim", ["tail", "-f", "/dev/null"], network_disabled=True)
    local = tmp_path / "fixture"
    local.mkdir()
    declaration = _source(local)
    declaration = {key: value.replace(str(local), "/fixture") for key, value in declaration.items()}
    try:
        package = io.BytesIO()
        with tarfile.open(fileobj=package, mode="w") as archive:
            archive.add(local, arcname="fixture")
        assert box.put_archive("/", package.getvalue())
        box.start()
        box.stop(timeout=2)
        box.reload()
        original = dict(box.attrs["State"])
        proof = subject.prepare_stopped_recovery(client, container_id=box.id, declaration=declaration,
            output=tmp_path / "proof.json", install=True)
        assert proof["installed_proof_path"].startswith("/fixture/control/interruption-source-")
        assert proof["execution_authority_granted"] is False
        box.reload()
        assert box.attrs["State"] == original
    finally:
        box.remove(force=True)
