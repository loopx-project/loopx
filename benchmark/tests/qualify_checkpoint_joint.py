"""Reproduce the existing synthetic native source and fresh-guest receivers.

Requires an explicitly owned Docker daemon and Linux binaries/dependencies.
No real model endpoint, benchmark task, active Goal or live bank is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


PROVIDER_REVISION = "071a884012754127f28f5b84be10f533a42db527"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider-source", type=Path, required=True)
    parser.add_argument("--native-binary", type=Path, required=True)
    parser.add_argument("--guest-node", type=Path, required=True)
    parser.add_argument("--guest-dependencies", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", default="python:3.12-slim")
    args = parser.parse_args()
    if sys.platform != "linux" or os.getuid() == 0:
        parser.error("Run on Linux as the ordinary owner of a disposable Docker daemon")
    source = Path(__file__).resolve().parents[2]
    source_revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"], text=True).strip():
        parser.error("Use a clean dedicated source checkout and keep it frozen during qualification")
    provider = args.provider_source.resolve(strict=True)
    if subprocess.check_output(["git", "-C", str(provider), "rev-parse", "HEAD"], text=True).strip() != PROVIDER_REVISION:
        parser.error("Provider must be the documented immutable checkpoint revision")
    if subprocess.check_output(["git", "-C", str(provider), "status", "--porcelain"], text=True).strip():
        parser.error("Provider source must be clean")
    binaries = {"native": args.native_binary.resolve(strict=True),
                "node": args.guest_node.resolve(strict=True)}
    dependencies = args.guest_dependencies.resolve(strict=True)
    output = args.output.absolute()
    output.parent.resolve(strict=True)
    if output.resolve() != output or output.exists():
        parser.error("Output must be a fresh physical directory; prior evidence is never overwritten")
    output.mkdir(mode=0o700)
    sys.path.insert(0, str(provider))
    import docker
    import pytest
    import sforge
    from benchmark.edgebench.checkpoint_supervisor import CheckpointSupervisor
    from benchmark.tests.test_checkpoint_controller import controllers, register, submit

    if Path(sforge.__file__).resolve().parent != provider / "sforge":
        parser.error("Imported provider does not match the declared source")
    base = {str(source): {"bind": "/source", "mode": "ro"},
            str(provider): {"bind": "/provider", "mode": "ro"},
            str(dependencies): {"bind": "/deps", "mode": "ro"},
            str(binaries["native"]): {"bind": "/native/codex", "mode": "ro"},
            str(binaries["node"]): {"bind": "/usr/local/bin/node", "mode": "ro"}}
    # Some development worktrees share node_modules through a physical symlink.
    # Preserve that existing dependency resolution in the isolated guest too.
    modules = source / "node_modules"
    if modules.is_symlink():
        physical = modules.resolve(strict=True)
        base[str(physical)] = {"bind": str(physical), "mode": "ro"}
    environment = {"PYTHONPATH": "/source:/provider:/deps", "LOOPX_USAGE_PING": "0",
                   "LOOPX_NATIVE_CHECKPOINT_BIN": "/native/codex"}
    client = docker.from_env(timeout=30)
    image = client.images.get(args.image).id  # Caller-prepared image, no implicit pull.

    def guest(command, volumes, env, log):
        container = client.containers.create(image, command, platform="linux/amd64",
            network_disabled=True, volumes={**base, **volumes}, working_dir="/source",
            environment={**environment, **env})
        try:
            container.start()
            status = container.wait(timeout=300)
            log.write_bytes(container.logs())
            assert status["StatusCode"] == 0, f"Qualification failed; inspect {log}"
        finally:
            container.remove(force=True)

    controller = output / "controller"
    controller.mkdir()
    source_files = output / "source-files"
    source_files.mkdir()
    patch = pytest.MonkeyPatch()
    fixture = controllers.__wrapped__(controller, patch)
    supervisor = container = None
    try:
        app, _, url, _, files, grades = next(fixture)
        token = register(app, url, submission_cooldown=3600, max_agent_submissions=18)
        assert submit(url, token, 7).status_code == 200
        script = ("from pathlib import Path; import time,pytest; "
                  "p=Path('/opt/loopx-benchmark/control'); p.mkdir(parents=True); "
                  "(p/'phase-deadline').write_text(str(int(time.time())+7200)); "
                  "raise SystemExit(pytest.main(["
                  "'/source/benchmark/tests/test_native_checkpoint_handoff.py::"
                  "test_actual_worker_captures_pending_replan_before_native_model[planning]',"
                  "'-q','-rs','-p','no:cacheprovider','--basetemp=/qualification/source-tmp']))")
        container = client.containers.create(image, ["python", "-c", script],
            platform="linux/amd64", network_disabled=True,
            volumes={**base, str(source_files): {"bind": "/qualification", "mode": "rw"}},
            working_dir="/source", environment={**environment, "NODE_OPTIONS": "--experimental-sqlite",
                                               "LOOPX_CHECKPOINT_EXTERNAL_CONTROLLER": "1"})
        supervisor = CheckpointSupervisor(files[0], output / "points")
        supervisor.bind({"SFORGE_TOKEN": token})
        container.start()
        supervisor.start(container)
        status = container.wait(timeout=300)
        (output / "source.log").write_bytes(container.logs())
        supervisor.close()
        assert status["StatusCode"] == 0, f"Inspect {output / 'source.log'}"
        points = [p for p in (output / "points").iterdir() if p.is_dir() and not p.name.startswith(".")]
        assert len(points) == 1 and len(grades) == 1
        point = points[0]
        packet = json.loads((point / "checkpoint.json").read_bytes())
        metadata = packet["point_metadata"]
        assert metadata["held_decision_component_id"] and metadata["decision_sha256"]
        git_object = point / "components/readonly_workspace/.git/objects/ab/retained-object"
        assert git_object.read_text() == "Readonly source evidence" and git_object.stat().st_mode & 0o777 == 0o444
        home = Path(metadata["native_session"]["guest_home"])
        config = source_files / home.relative_to("/qualification") / "config.toml"
        config.resolve(strict=True)
        for stride in (False, True):
            name = "stride" if stride else "ordinary"
            results = output / name
            results.mkdir()
            guest(["python", "-m", "pytest", "benchmark/tests/test_checkpoint_joint_receiver.py",
                   "-q", "-rs", "-p", "no:cacheprovider", "--basetemp=/results/test"],
                  {str(point): {"bind": "/point", "mode": "ro"},
                   str(config): {"bind": "/captured-config.toml", "mode": "ro"},
                   str(results): {"bind": "/results", "mode": "rw"}},
                  {"LOOPX_JOINT_RECEIVER_POINT": "/point", "LOOPX_JOINT_RECEIVER_CONFIG": "/captured-config.toml",
                   **({"LOOPX_JOINT_RECEIVER_STRIDE": "1"} if stride else {})}, output / f"{name}.log")
        assert subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip() == source_revision, "Source changed during qualification"
        assert not subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"], text=True).strip(), "Source changed during qualification"
        receipt = {"passed": True, "controller_uid": os.getuid(), "provider_revision": PROVIDER_REVISION,
                   "source_revision": source_revision, "source_tree_dirty": False,
                   "qualification_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                       [Path(__file__), source / "benchmark/tests/test_native_checkpoint_handoff.py",
                        source / "benchmark/tests/test_checkpoint_joint_receiver.py"]},
                   "image_id": image, "point_sha256": packet["sha256"],
                   "binary_sha256": {k: hashlib.sha256(p.read_bytes()).hexdigest() for k, p in binaries.items()},
                   "ordinary_and_stride": True, "research_point_qualified": False}
        (output / "result.json").write_text(json.dumps(receipt, indent=2))
        print(json.dumps(receipt))
    finally:
        try:
            if supervisor is not None:
                supervisor.close()
        finally:
            if container is not None:
                container.remove(force=True)
            client.close()
            fixture.close()
            patch.undo()


if __name__ == "__main__":
    main()
