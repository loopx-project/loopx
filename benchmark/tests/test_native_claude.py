"""Native provider transport invariants; no model calls or live task content."""
import hashlib
import json
import logging
import os
from pathlib import PurePosixPath
import shlex
import subprocess

import pytest

from sforge.harness.config import SForgeConfig
from benchmark.runtime.sforge_claude import NativeClaudeDockerBackend, NativeClaudeWorker


def worker(tmp_path):
    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\necho '2.1.308 (Claude Code)'\n")
    config = SForgeConfig(agent_model="synthetic-model", agent_effort="max",
                         agent_api_base_url="https://model.example.invalid",
                         agent_api_key="synthetic-fixture")
    return NativeClaudeWorker(config, binary=binary,
        sha256=hashlib.sha256(binary.read_bytes()).hexdigest(), version="2.1.308")


def test_explicit_model_includes_native_goal_evaluator(tmp_path):
    agent = worker(tmp_path)
    env = {"ANTHROPIC_DEFAULT_HAIKU_MODEL": "unrequested-model",
           "CLAUDE_CODE_EFFORT_LEVEL": "low"}
    agent.augment_env(env, "synthetic-selected-model")
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "synthetic-selected-model"
    assert env["CLAUDE_CODE_EFFORT_LEVEL"] == "max"
    for resume in (False, True):
        args = shlex.split(agent.format_run_cmd("/tmp/task", resume=resume, internet=False))
        assert args[args.index("--effort") + 1] == "max"
        assert args[args.index("--model") + 1] == "synthetic-model"
        assert "WebSearch,WebFetch" in args
        assert ("--continue" in args) is resume


def test_binary_drift_refused_before_install(tmp_path):
    agent = worker(tmp_path)
    agent.binary.write_text("changed")
    with pytest.raises(ValueError, match="digest"):
        agent.install_stop_hook(None, None, tmp_path, None)


def test_native_stop_hook_is_executable_under_private_umask(tmp_path):
    # Exercise the provider's actual script; inspect protocol independently.
    from sforge.harness.agent.claude_code import _generate_stop_hook
    old = os.umask(0o077)
    try:
        hook = tmp_path / "stop.sh"
        hook.write_text(_generate_stop_hook())
    finally:
        os.umask(old)
    hook.chmod(0o755)
    for active in (False, True):
        result = subprocess.run([str(hook)], input=json.dumps({"stop_hook_active": active}),
                                capture_output=True, text=True, check=True)
        decision = json.loads(result.stdout)
        assert decision["decision"] == "block" and decision["reason"]


@pytest.mark.skipif(os.environ.get("LOOPX_EDGEBENCH_DOCKER_SMOKE") != "1",
                    reason="Explicit isolated Docker qualification required")
def test_real_upload_checks_agent_permissions(tmp_path):
    import docker
    import uuid
    backend = NativeClaudeDockerBackend(docker.from_env())
    image = backend.client.images.get("python:3.12-slim")
    handle = backend.create_container(image.id, "claude-install-" + uuid.uuid4().hex[:10],
                                      cpu_limit=1, mem_limit="256m")
    backend.start_container(handle)
    old = os.umask(0o077)
    try:
        # A different UID exposes private-upload failures hidden by matching UIDs.
        agent_uid = 12001 if os.getuid() != 12001 else 12002
        assert backend.exec_run(handle, ["sh", "-ec",
            f"useradd -m -u {agent_uid} agent; mkdir -p /home/agent/.claude; "
            "printf '#!/bin/sh\\nexit 0\\n' > /usr/local/bin/sforge-submit; "
            "chmod 0600 /usr/local/bin/sforge-submit"], user="root").exit_code == 0
        agent = worker(tmp_path)
        agent.install_stop_hook(backend, handle, tmp_path, logging.getLogger(__name__))
        observed = backend.exec_run(handle, ["/bin/sh", "-ec",
            "/usr/local/bin/sforge-submit; printf '{}' | /tmp/sforge-stop-hook.sh"], user="agent")
        assert observed.exit_code == 0
        assert json.loads(observed.output)["decision"] == "block"
        prompt = tmp_path / "prompt.md"
        prompt.write_text("Synthetic task instruction")
        backend.copy_to_container(handle, prompt, PurePosixPath("/tmp/unrelated-private.md"))
        assert backend.exec_run(handle, ["cat", "/tmp/unrelated-private.md"],
                                user="agent").exit_code != 0
        backend.copy_to_container(handle, prompt, PurePosixPath("/tmp/agent_prompt.md"))
        readback = backend.exec_run(handle, ["cat", "/tmp/agent_prompt.md"], user="agent")
        assert readback.exit_code == 0 and readback.output == "Synthetic task instruction"
        assert backend.exec_run(handle, ["cat", "/tmp/agent_prompt.md"],
                                user="nobody").exit_code != 0
        assert json.loads((tmp_path / "claude-install.json").read_text())["installation_verified"]
    finally:
        os.umask(old)
        backend.cleanup_container(handle)
        backend.client.close()
