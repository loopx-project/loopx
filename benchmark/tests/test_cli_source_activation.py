"""Real daemon transport and failure recovery, without models or work admission.

Native/settlement semantics retain their owners; these fixtures deliberately
isolate physical pause, immutable source installation and dispatch readback.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
import os
import subprocess
import tarfile
import time
import uuid

import pytest

from benchmark.edgebench import cli_source_activation as activation
from benchmark.edgebench.cli_source_probe import (
    archive_witness, _archive_response, _path_stat, _prepare_observer_native_home,
)
from benchmark.edgebench.cli_source_release import StagedCliRelease


pytestmark = pytest.mark.skipif(os.environ.get("LOOPX_CLI_ACTIVATION_DOCKER_SMOKE") != "1",
    reason="Requires explicit isolated Docker CLI-activation qualification")


def test_native_observer_reader_can_initialize_its_private_home():
    import docker

    client = docker.from_env(timeout=15)
    image = os.environ.get("LOOPX_CHECKPOINT_WRITER_IMAGE", "python:3.12-slim")
    client.images.get(image)
    box = client.containers.run(image, ["sleep", "180"], detach=True, network_disabled=True,
        mem_limit="64m", pids_limit=32, labels={"loopx.cli-activation-test": uuid.uuid4().hex})
    python, home, reader = "/usr/local/bin/python", "/profile/native", "65534:65534"
    try:
        assert box.exec_run(["mkdir", "-p", home], user="root").exit_code == 0
        command = [python, "-I", "-B", "-c", "from pathlib import Path; import sys; "
                   "home = Path(sys.argv[1]); assert not (home / 'auth.json').exists(); "
                   "(home / 'native-reader-state').write_bytes(b'initialized')", home]
        assert box.exec_run(command, user=reader).exit_code != 0
        assert _prepare_observer_native_home(box, python=python, home=home, user=reader)
        assert box.exec_run(command, user=reader).exit_code == 0
        assert box.exec_run(["cat", home + "/native-reader-state"], user=reader).output == b"initialized"
        other = box.exec_run(["ls", home], user="65533:65533")
        assert other.exit_code != 0
    finally:
        box.remove(force=True)
        client.close()


@pytest.fixture
def source(tmp_path):
    import docker

    client = docker.from_env(timeout=15)
    image = os.environ.get("LOOPX_CHECKPOINT_WRITER_IMAGE", "python:3.12-slim")
    client.images.get(image)  # No implicit pull or image build.
    box = client.containers.run(image, ["sleep", "180"], detach=True, network_disabled=True,
        mem_limit="64m", pids_limit=32, labels={"loopx.cli-activation-test": uuid.uuid4().hex})
    try:
        result = box.exec_run(["sh", "-c", "mkdir -p /profile/bin /profile/releases/old/scripts /work /native /control; "
            "printf '#!/bin/sh\\nprintf old' > /profile/releases/old/scripts/loopx; "
            "chmod 755 /profile/releases/old/scripts/loopx; "
            "ln -s /profile/releases/old/scripts/loopx /profile/bin/loopx; "
            "printf preserved >/work/incumbent; printf original-session >/native/session; "
            "printf original-finite-phase >/phase; chmod 400 /phase"])
        assert result.exit_code == 0, result.output
        with io.BytesIO() as stream:
            payload = json.dumps({"goals": [{"id": "fixture-goal"}]}).encode()
            with tarfile.open(fileobj=stream, mode="w") as archive:
                entry = tarfile.TarInfo("registry.json")
                entry.size = len(payload)
                archive.addfile(entry, io.BytesIO(payload))
            stream.seek(0)
            assert box.put_archive("/control", stream)
        repo = tmp_path / "product"
        (repo / "scripts").mkdir(parents=True)
        (repo / "scripts/loopx").write_text("#!/bin/sh\nprintf new\n")
        (repo / "scripts/loopx").chmod(0o755)
        for args in (["init", "-q"], ["add", "scripts/loopx"], ["-c", "user.name=Fixture", "-c",
                "user.email=fixture@example.test", "commit", "-qs", "-m", "Synthetic executable release"]):
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
        revision = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        from benchmark.runtime.source import archive_source

        archive = tmp_path / "source.tar"
        archive_source(repo, revision, archive)
        box.reload()
        plan = {"activation_id": uuid.uuid4().hex, "container_id": box.id,
            "started_at": box.attrs["State"]["StartedAt"], "goal_id": "fixture-goal", "agent_id": "fixture-agent",
            "todo_id": "todo_fixture0001", "turn_instance_id": "original-turn", "session_id": "original-native-session",
            "registry_path": "/control/registry.json", "launcher": "/profile/bin/loopx", "launcher_uid": 0, "launcher_gid": 0,
            "old_target": "/profile/releases/old/scripts/loopx",
            "new_target": "/profile/releases/new/scripts/loopx", "release_root": "/profile/releases",
            "target_revision": revision, "target_archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "worker_lock": str(activation._worker_lock(client, box.id))}
        release = StagedCliRelease(archive.resolve(), plan)
        assert release.stage(box)["staged"] is False
        assert release.stage(box, execute=True)["staged"] is True
        receipt_dir = tmp_path / "controller"
        receipt_dir.mkdir(mode=0o700)
        receipt = receipt_dir / "activation.json"
        components = [{"id": "work", "source_path": "/work"}, {"id": "native", "source_path": "/native"},
                      {"id": "phase", "source_path": "/phase"}]
        control = ["continue"]
        def witness(container, _):
            return archive_witness(container, components)
        original_witness = witness(box, plan)

        def facts(container, _):
            identity = {"schema_version": "quota_settlement_identity_v0",
                **{k: plan[k] for k in ("goal_id", "agent_id", "todo_id", "turn_instance_id")},
                "effect_id": ":".join(plan[k] for k in ("goal_id", "agent_id", "todo_id", "turn_instance_id"))}
            return {"ready_present": False, "unknown_process_ids": [], "probe_status": "ready",
                "scheduler": {"host_attempt": None, "host_pending": False, "last_wake_status": "wake_ok", "last_wake_rc": 0},
                "settlement": {"identity": {"result": {"value": identity, "failure": None}}, "progress": {"state": "settled"}},
                "native": {"session_id": plan["session_id"], "turn_ids": ["native-original"],
                    "completed_turn_id": "native-original", "visible_history_sha256": "e" * 64,
                    "model_calls": 0, "execution_authority_granted": False, "quiescent_readback": True},
                "source_witness_before": witness(container, plan), "source_witness_after": witness(container, plan)}

        params = {"client": client, "plan": plan, "receipt": receipt, "preflight": facts, "probe": facts,
                  "witness": witness, "verify_release": release.verify, "control": lambda: control[0]}
        yield box, params, control
        assert witness(box, plan) == original_witness
    finally:
        box.remove(force=True)
        assert not client.containers.list(all=True, filters={"id": box.id})
        client.close()


def test_preview_and_exact_activation_preserve_work_session_phase_and_do_not_replay(source, monkeypatch):
    box, params, _ = source
    assert activation.activate_cli_source(**params)["source_change_allowed"] is False
    assert not params["receipt"].exists()
    writes = []
    original = activation._put_link

    def write(*args, **kwargs):
        writes.append(args[2])
        return original(*args, **kwargs)

    monkeypatch.setattr(activation, "_put_link", write)
    result = activation.activate_cli_source(**params, execute=True)
    assert result["phase"] == "released" and result["applied"] is True
    assert result["model_receipt_adoption"] == "unobserved"
    assert result["work_or_clock_mutations"] == 0 and result["pause_wall_seconds"] > 0
    assert box.exec_run(["/profile/bin/loopx"]).output == b"new"
    assert params["receipt"].stat().st_mode & 0o777 == 0o600
    original_receipt = params["receipt"].read_bytes()
    historical = activation.activate_cli_source(**params, execute=True, reconcile=True)
    assert historical["historical_receipt"] is True and historical["replayed"] is True
    assert historical["current_source_verified"] is False and historical["applied"] is True
    assert historical["model_receipt_adoption"] == "unobserved"
    assert params["receipt"].read_bytes() == original_receipt
    assert writes == [params["plan"]["new_target"]]


@pytest.mark.parametrize("failure", ["lost-write-ack", "rejected-before-write", "partial-launcher", "lost-release-ack"])
def test_transport_ambiguity_is_read_back_before_release_and_never_repeats_dispatch(source, monkeypatch, failure):
    box, params, _ = source
    original = activation._put_link

    def faulty(container, path, target, **kwargs):
        if failure == "lost-write-ack":
            original(container, path, target, **kwargs)
        elif failure == "partial-launcher":
            with io.BytesIO() as stream:
                with tarfile.open(fileobj=stream, mode="w") as archive:
                    member = tarfile.TarInfo("loopx")
                    member.size = 7
                    archive.addfile(member, io.BytesIO(b"partial"))
                stream.seek(0)
                assert container.put_archive("/profile/bin", stream)
        raise OSError("Injected bounded transport failure")

    if failure == "lost-release-ack":
        from docker.models.containers import Container

        unpause = Container.unpause

        def lost_release(container):
            unpause(container)
            if container.id == box.id:
                raise OSError("Injected lost release acknowledgement")

        monkeypatch.setattr(Container, "unpause", lost_release)
    else:
        monkeypatch.setattr(activation, "_put_link", faulty)
    if failure in {"partial-launcher", "lost-release-ack"}:
        with pytest.raises((activation.SourceActivationHeld, OSError)):
            activation.activate_cli_source(**params, execute=True)
        box.reload()
        assert box.attrs["State"]["Paused"] is (failure == "partial-launcher")
        if failure == "partial-launcher":
            assert activation._link(box, params["plan"]["launcher"]) is None
            monkeypatch.setattr(activation, "_put_link", original)
            result = activation.activate_cli_source(**params, execute=True, reconcile=True, repair="new")
        else:
            result = activation.activate_cli_source(**params, execute=True, reconcile=True)
        assert result["phase"] == "released"
        assert box.exec_run(["/profile/bin/loopx"]).output == b"new"
    else:
        result = activation.activate_cli_source(**params, execute=True)
        assert result["phase"] == "released"
        expected = b"new" if failure == "lost-write-ack" else b"old"
        assert box.exec_run(["/profile/bin/loopx"]).output == expected
        assert result["applied"] is (failure == "lost-write-ack")


@pytest.mark.parametrize("case", ["busy-before-pause", "ready-after-pause", "Stop-before-pause", "Stop-after-pause", "unsettled-after-pause"])
def test_busy_unknown_and_stop_cannot_change_the_launcher(source, case):
    box, params, control = source
    if case == "Stop-before-pause":
        control[0] = "stop"
    elif case == "busy-before-pause":
        original = params["preflight"]
        params["preflight"] = lambda *args: {**original(*args), "unknown_process_ids": [123]}
    else:
        original = params["probe"]

        def changed(*args):
            value = deepcopy(original(*args))
            if case == "ready-after-pause":
                value["ready_present"] = True
            elif case == "Stop-after-pause":
                control[0] = "stop"
            else:
                value["settlement"]["progress"]["state"] = "spend_receipt_required"
            return value

        params["probe"] = changed
    if case == "Stop-after-pause":
        with pytest.raises(activation.SourceActivationHeld):
            activation.activate_cli_source(**params, execute=True)
        box.reload()
        assert box.attrs["State"]["Paused"] is True
        # Stop remains the release authority. A fresh explicit recovery after
        # revocation can restore only the unchanged, original source here.
        control[0] = "continue"
        activation.activate_cli_source(**params, execute=True, reconcile=True)
    else:
        result = activation.activate_cli_source(**params, execute=True)
        assert result["applied"] is False
    assert activation._link(box, params["plan"]["launcher"]) == params["plan"]["old_target"]
    assert box.exec_run(["/profile/bin/loopx"]).output == b"old"


def test_release_bytes_and_current_launcher_are_compared_not_overwritten(source):
    box, params, _ = source
    changed = box.exec_run(["sh", "-c", "printf changed >/profile/releases/new/scripts/loopx"])
    assert changed.exit_code == 0
    with pytest.raises(ValueError, match="differ"):
        activation.activate_cli_source(**params, execute=True)
    assert not params["receipt"].exists()
    assert activation._link(box, params["plan"]["launcher"]) == params["plan"]["old_target"]


def test_recovery_rejects_another_intent_and_public_receipt_permissions(source):
    _, params, _ = source
    activation.activate_cli_source(**params, execute=True)
    other = {**params, "plan": {**params["plan"], "activation_id": "f" * 32}}
    with pytest.raises(ValueError, match="original intent"):
        activation.activate_cli_source(**other, execute=True, reconcile=True)
    params["receipt"].chmod(0o644)
    with pytest.raises(ValueError, match="private physical receipt"):
        activation.activate_cli_source(**params, execute=True, reconcile=True)


def test_native_head_and_an_unstarted_get_close_release_the_daemon_container_lock(source):
    box, _, _ = source
    assert box.exec_run(["python", "-c", "from pathlib import Path; Path('/archive-probe').write_bytes(b'x' * 4194304)"]).exit_code == 0
    box.pause()
    started = time.monotonic()
    for _ in range(3):
        assert _path_stat(box, "/archive-probe")["size"] == 4194304
    # The caller can reject an archive before iterating a single body chunk.
    # Own Response.close rather than closing Docker SDK's unstarted generator.
    with _archive_response(box, "/archive-probe"):
        pass
    box.reload()
    assert box.attrs["State"]["Paused"] is True
    assert time.monotonic() - started < 5
    box.unpause()


@pytest.mark.parametrize("partial", [{"phase": phase} for phase in (
    "pause_requested", "pause_uncertain", "unknown-phase", None, 17)] + [
    {"phase": "paused", "pause_acknowledged": True},
    {"phase": "qualified", "pause_acknowledged": True, "paused_at_epoch": "unconfirmed"},
])
def test_unacknowledged_or_unknown_intent_cannot_acquire_another_actors_pause(source, monkeypatch, partial):
    box, params, _ = source
    from docker.models.containers import Container

    pause = Container.pause

    def controller_died_before_pause(container):
        if container.id == box.id:
            raise KeyboardInterrupt("Injected controller death before Docker pause")
        return pause(container)

    monkeypatch.setattr(Container, "pause", controller_died_before_pause)
    with pytest.raises(KeyboardInterrupt):
        activation.activate_cli_source(**params, execute=True)
    value = json.loads(params["receipt"].read_bytes())
    assert value["phase"] == "pause_requested" and "pause_acknowledged" not in value
    value.update(partial)
    params["receipt"].write_text(json.dumps(value))
    pause(box)  # A different actor establishes its own real physical barrier.
    with pytest.raises(activation.SourceActivationHeld, match="acknowledgement"):
        activation.activate_cli_source(**params, execute=True, reconcile=True)
    box.reload()
    assert box.attrs["State"]["Paused"] is True
    assert activation._link(box, params["plan"]["launcher"]) == params["plan"]["old_target"]
    assert json.loads(params["receipt"].read_bytes())["phase"] == partial["phase"]


def test_repair_rechecks_stop_after_target_verification(source, monkeypatch):
    box, params, control = source
    write = activation._put_link

    def interrupted(*_args, **_kwargs):
        raise KeyboardInterrupt("Injected controller death after confirmed pause")

    monkeypatch.setattr(activation, "_put_link", interrupted)
    with pytest.raises(KeyboardInterrupt):
        activation.activate_cli_source(**params, execute=True)
    value = json.loads(params["receipt"].read_bytes())
    assert value["pause_acknowledged"] is True and value["phase"] == "switch_requested"
    verify = params["verify_release"]

    def stop_after_verification(*args):
        verify(*args)
        control[0] = "stop"

    params["verify_release"] = stop_after_verification
    monkeypatch.setattr(activation, "_put_link", write)
    with pytest.raises(activation.SourceActivationHeld, match="stopped before"):
        activation.activate_cli_source(**params, execute=True, reconcile=True, repair="new")
    box.reload()
    assert box.attrs["State"]["Paused"] is True
    assert activation._link(box, params["plan"]["launcher"]) == params["plan"]["old_target"]
