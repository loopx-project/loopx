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
from xml.etree import ElementTree


PROVIDER_REVISION = "071a884012754127f28f5b84be10f533a42db527"


def require_one_pass(report):
    cases = ElementTree.parse(report).findall(".//testcase")
    if len(cases) != 1 or any(
        c.find(tag) is not None for c in cases for tag in ("skipped", "failure", "error")
    ):
        raise ValueError(f"Qualification requires one actual pass and no skip; inspect {report}")


def _stage_source_configuration(container, native, destination):
    from benchmark.edgebench.checkpoint_worker import _archive

    # The source guest can be root while its controller is an ordinary user.
    # Read only the bound config through the existing daemon archive seam;
    # source UID/mode and auth files remain untouched.
    config = _archive(container, str(Path(native["guest_home"]) / "config.toml"), destination)
    config.chmod(0o600)
    if hashlib.sha256(config.read_bytes()).hexdigest() != native["config_sha256"]:
        raise ValueError("Source native configuration differs from the sealed checkpoint")
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider-source", type=Path, required=True)
    parser.add_argument("--native-binary", type=Path, required=True)
    parser.add_argument("--guest-node", type=Path, required=True)
    parser.add_argument("--guest-dependencies", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", default="python:3.12-slim")
    parser.add_argument("--reuse-point", type=Path, help="Reuse an already sealed synthetic source; never count its source test as passed")
    parser.add_argument("--captured-config", type=Path, help="The reused point's matching native fixture configuration")
    args = parser.parse_args()
    if bool(args.reuse_point) != bool(args.captured_config):
        parser.error("--reuse-point and --captured-config must be supplied together")
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
    from benchmark.edgebench.checkpoint_controller import restore
    from loopx.control_plane.effect_runtime import effect_runtime_result
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

    def guest(command, volumes, env, log, *, capture_supervisor=None):
        container = client.containers.create(image, command, platform="linux/amd64",
            volumes={**base, **volumes}, working_dir="/source",
            user=f"{os.getuid()}:{os.getgid()}", environment={**environment, **env},
            **({"network_mode": "host"} if capture_supervisor else {"network_disabled": True}))
        try:
            container.start()
            if capture_supervisor:
                capture_supervisor.start(container)
            status = container.wait(timeout=300)
            log.write_bytes(container.logs())
            assert status["StatusCode"] == 0, f"Qualification failed; inspect {log}"
            require_one_pass(log.parent / log.stem / "junit.xml")
        finally:
            if capture_supervisor:
                capture_supervisor.close()
            log.write_bytes(container.logs())
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
        if args.reuse_point:
            point = args.reuse_point.absolute()
            config_source = args.captured_config.absolute()
            if point.resolve(strict=True) != point or config_source.resolve(strict=True) != config_source:
                raise ValueError("Reused point/configuration must be physical paths")
            packet = json.loads((point / "checkpoint.json").read_bytes())
            verified = effect_runtime_result("runtime.checkpoint_artifacts", {
                "action": "verify", "root": str(point), "expected_sha256": packet["sha256"]})
            assert verified["integrity_verified"]
            metadata = packet["point_metadata"]
            assert metadata["held_decision_component_id"] and metadata["worker_file_snapshot"]["prepared_source_verified"]
            config = output / "native-config"
            config.write_bytes(config_source.read_bytes())
            config.chmod(0o600)
            assert hashlib.sha256(config.read_bytes()).hexdigest() == metadata["native_session"]["config_sha256"]
        else:
            token = register(app, url, submission_cooldown=3600, max_agent_submissions=18)
            assert submit(url, token, 7).status_code == 200
            script = ("from pathlib import Path; import time,pytest; "
                      "p=Path('/opt/loopx-benchmark/control'); p.mkdir(parents=True); "
                      "(p/'phase-deadline').write_text(str(int(time.time())+7200)); "
                      "raise SystemExit(pytest.main(["
                      "'/source/benchmark/tests/test_native_checkpoint_handoff.py::"
                      "test_actual_worker_captures_pending_replan_before_native_model[planning]',"
                      "'-q','-rs','-p','no:cacheprovider','--basetemp=/qualification/source-tmp',"
                      "'--junitxml=/qualification/source.xml']))")
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
            require_one_pass(source_files / "source.xml")
            points = [p for p in (output / "points").iterdir() if p.is_dir() and not p.name.startswith(".")]
            assert len(points) == 1 and len(grades) == 1
            point = points[0]
            packet = json.loads((point / "checkpoint.json").read_bytes())
            metadata = packet["point_metadata"]
            assert metadata["held_decision_component_id"] and metadata["decision_sha256"]
            git_object = point / "components/task_workspace/.git/objects/ab/retained-object"
            assert git_object.read_text() == "Readonly source evidence" and git_object.stat().st_mode & 0o777 == 0o444
            config = _stage_source_configuration(container, metadata["native_session"], output / "native-config")
        for name in ("ordinary", "stride", "fresh"):
            stride = name != "ordinary"
            results = output / name
            results.mkdir()
            guest_workspace = results / "workspace-root"
            guest_workspace.mkdir()
            extra_env = {}
            extra_volumes = {}
            capture_supervisor = None
            if name == "fresh":
                operator = output / "receiver-controller"
                operator.mkdir(mode=0o700)
                phase = operator / "phase-deadline"
                capsule = point / "components" / metadata["worker_file_snapshot"]["native_capsule_component_id"]
                activation = restore(files[1], root=capsule,
                    expected_sha256=metadata["worker_file_snapshot"]["native_capsule_sha256"],
                    run_id="joint-full-receiver", phase_deadline_file=phase)
                public = operator / "worker-activation.json"
                public.write_text(json.dumps({"url": json.loads(files[1].read_bytes())["url"],
                    "activation": activation, "phase_deadline": phase.read_text()}))
                public.chmod(0o600)
                logs, publication, controls = results / "logs", output / "receiver-publication", results / "controls"
                for folder in (logs, publication, controls):
                    folder.mkdir(mode=0o700)
                capture_supervisor = CheckpointSupervisor(files[1], publication / "published")
                capture_supervisor.bind({"SFORGE_TOKEN": activation["token"]})
                extra_env = {"LOOPX_JOINT_RECEIVER_NATIVE": "/native-activation.json",
                    "LOOPX_JOINT_RECAPTURE_ROOT": "/publication/published"}
                extra_volumes = {str(public): {"bind": "/native-activation.json", "mode": "ro"},
                    str(logs): {"bind": "/logs/agent", "mode": "rw"},
                    str(publication): {"bind": "/publication", "mode": "ro"},
                    str(controls): {"bind": "/opt/loopx-benchmark/control", "mode": "rw"}}
            guest(["python", "-m", "pytest", "benchmark/tests/test_checkpoint_joint_receiver.py",
                   "-q", "-rs", "-p", "no:cacheprovider", "--basetemp=/results/test",
                   "--junitxml=/results/junit.xml"],
                  {str(point): {"bind": "/point", "mode": "ro"},
                   str(config): {"bind": "/captured-config.toml", "mode": "ro"},
                   str(guest_workspace): {"bind": "/qualification", "mode": "rw"},
                   str(results): {"bind": "/results", "mode": "rw"}, **extra_volumes},
                  {"LOOPX_JOINT_RECEIVER_POINT": "/point", "LOOPX_JOINT_RECEIVER_CONFIG": "/captured-config.toml",
                   "LOOPX_RUNTIME_ROOT": "/results/transport-runtime",
                   **({"LOOPX_JOINT_RECEIVER_STRIDE": "1"} if stride else {}),
                   **({"LOOPX_JOINT_RECEIVER_FRESH": "1"} if name == "fresh" else {}), **extra_env},
                  output / f"{name}.log", capture_supervisor=capture_supervisor)
            if name == "fresh":
                newer = [p for p in (publication / "published").iterdir() if p.is_dir() and (p / "checkpoint.json").exists()]
                assert len(newer) == 1
                recaptured = newer[0]
                next_packet = json.loads((recaptured / "checkpoint.json").read_bytes())
                check = effect_runtime_result("runtime.checkpoint_artifacts", {
                    "action": "verify", "root": str(recaptured), "expected_sha256": next_packet["sha256"]})
                assert check["integrity_verified"]
                third = output / "grandreceiver"
                third.mkdir()
                third_workspace = third / "workspace-root"
                third_workspace.mkdir()
                guest(["python", "-m", "pytest", "benchmark/tests/test_checkpoint_joint_receiver.py",
                       "-q", "-rs", "-p", "no:cacheprovider", "--basetemp=/results/test", "--junitxml=/results/junit.xml"],
                    {str(recaptured): {"bind": "/point", "mode": "ro"},
                     str(config): {"bind": "/captured-config.toml", "mode": "ro"},
                     str(third_workspace): {"bind": "/qualification", "mode": "rw"},
                     str(third): {"bind": "/results", "mode": "rw"}},
                    {"LOOPX_JOINT_RECEIVER_POINT": "/point", "LOOPX_JOINT_RECEIVER_CONFIG": "/captured-config.toml",
                     "LOOPX_JOINT_RECEIVER_GOAL": "joint-grandreceiver", "LOOPX_RUNTIME_ROOT": "/results/transport-runtime"},
                    output / "grandreceiver.log")
                assert not grades if args.reuse_point else len(grades) == 1
        # The same native provider must also complete the public Core journey
        # with every benchmark import forbidden by its test fixture.
        results = output / "core"
        results.mkdir()
        guest(["python", "-m", "pytest",
               "tests/control_plane/test_research_checkpoint_cli.py::test_explicit_ordinary_goal_capture_preserves_context_without_activation[True]",
               "-q", "-rs", "-p", "no:cacheprovider", "--basetemp=/results/test",
               "--junitxml=/results/junit.xml"],
              {str(results): {"bind": "/results", "mode": "rw"}},
              {"LOOPX_RUNTIME_ROOT": "/results/transport-runtime"}, output / "core.log")
        assert subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip() == source_revision, "Source changed during qualification"
        assert not subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"], text=True).strip(), "Source changed during qualification"
        receipt = {"passed": True, "controller_uid": os.getuid(), "provider_revision": PROVIDER_REVISION,
                   "source_revision": source_revision, "source_tree_dirty": False,
                   "qualification_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                       [Path(__file__), source / "benchmark/tests/test_native_checkpoint_handoff.py",
                        source / "benchmark/tests/test_checkpoint_joint_receiver.py",
                        source / "tests/control_plane/test_research_checkpoint_cli.py",
                        source / "tests/control_plane/codex_checkpoint_fixture.py"]},
                   "image_id": image, "point_sha256": packet["sha256"],
                   "binary_sha256": {k: hashlib.sha256(p.read_bytes()).hexdigest() for k, p in binaries.items()},
                   "ordinary_and_stride": True, "post_replan_fresh_context": True, "core_goal_without_benchmark": True,
                   "source_created_in_this_run": not bool(args.reuse_point),
                   "source_test_passed_in_this_run": not bool(args.reuse_point),
                   "automatic_full_recapture_sha256": next_packet["sha256"], "another_receiver_actual_work": True,
                   "research_point_qualified": False}
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
