"""Offline installation of the native SForge Claude Code agent.

This Python transport keeps the provider's Stop hook, tool permissions and
outer resume/deadline lifecycle. It does not install LoopX goal control.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shlex

from sforge.harness.agent.claude_code import ClaudeCodeAgent
from sforge.harness.backend.docker_backend import DockerBackend

# Native CLI vocabulary, local to this provider transport.
CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")


class NativeClaudeDockerBackend(DockerBackend):
    """Assign the native prompt upload to its reader across host UID boundaries."""

    def copy_to_container(self, handle, src, dst):
        super().copy_to_container(handle, src, dst)
        if dst == PurePosixPath("/tmp/agent_prompt.md"):
            result = self.exec_run(handle, ["/bin/sh", "-ec",
                "chown agent:agent /tmp/agent_prompt.md; "
                "chmod 0600 /tmp/agent_prompt.md"], user="root")
            if result.exit_code:
                raise RuntimeError("Could not assign native prompt to agent")


class NativeClaudeWorker(ClaudeCodeAgent):
    """An explicitly pinned native binary, installed without a package download."""

    name = "native-claude-offline"
    install_cmds = []

    def __init__(self, config, *, binary: Path, sha256: str, version: str):
        super().__init__(config)
        if not config.agent_model or config.agent_effort not in CLAUDE_EFFORTS:
            raise ValueError("Explicit model and supported native Claude effort are required")
        if not config.agent_api_base_url or not config.agent_api_key:
            raise ValueError("An explicit Anthropic-compatible endpoint and credential are required")
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValueError("Expected binary SHA-256 is required")
        if not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise ValueError("Expected native Claude version is required")
        self.binary = Path(binary).resolve(strict=True)
        self.sha256, self.version = sha256, version
        self._verify_binary()

    def _verify_binary(self):
        with self.binary.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != self.sha256:
            raise ValueError("Native Claude binary differs from its declared digest")

    def augment_env(self, env, model):
        super().augment_env(env, model)
        selected = model or self._config.agent_model
        # Include the native /goal evaluator and other small-model calls in
        # the declared model budget; do not silently use another model family.
        for key in ("ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
                    "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_SMALL_FAST_MODEL",
                    "CLAUDE_CODE_SUBAGENT_MODEL"):
            env[key] = selected
        env["CLAUDE_CODE_EFFORT_LEVEL"] = self._config.agent_effort
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"

    def format_run_cmd(self, prompt_path, **kwargs):
        # The same native flag applies on initial execution and --continue.
        return (super().format_run_cmd(prompt_path, **kwargs)
                + " --effort " + shlex.quote(self._config.agent_effort))

    def install_stop_hook(self, backend, handle, log_dir, logger):
        self._verify_binary()
        backend.copy_to_container(handle, self.binary, PurePosixPath("/usr/local/bin/claude"))
        result = backend.exec_run(handle, ["chmod", "0755", "/usr/local/bin/claude"], user="root")
        if result.exit_code:
            raise RuntimeError("Could not install native Claude executable")
        digest = backend.exec_run(handle, ["sha256sum", "/usr/local/bin/claude"], user="agent")
        if digest.exit_code or digest.output.split()[:1] != [self.sha256]:
            raise RuntimeError("Installed native Claude binary digest differs")
        observed = backend.exec_run(handle, ["/usr/local/bin/claude", "--version"], user="agent")
        if observed.exit_code or observed.output.strip() != f"{self.version} (Claude Code)":
            raise RuntimeError("Installed native Claude version differs from the declared version")
        super().install_stop_hook(backend, handle, log_dir, logger)
        # Native uploads preserve host umask. An executable root-owned shell
        # script still needs agent read permission, including sforge-submit.
        result = backend.exec_run(handle, ["/bin/sh", "-ec",
            "chmod 0755 /tmp/sforge-stop-hook.sh /usr/local/bin/sforge-submit; "
            "chmod 0600 /home/agent/.claude/settings.json"], user="root")
        if result.exit_code:
            raise RuntimeError("Could not set native hook/submission permissions")
        result = backend.exec_run(handle, ["/bin/sh", "-ec",
            "test -r /tmp/sforge-stop-hook.sh; test -x /tmp/sforge-stop-hook.sh; "
            "test -r /usr/local/bin/sforge-submit; test -x /usr/local/bin/sforge-submit; "
            "test -r /home/agent/.claude/settings.json"], user="agent")
        if result.exit_code:
            raise RuntimeError("Native hooks or submission entrypoint are unavailable to agent")
        (Path(log_dir) / "claude-install.json").write_text(json.dumps({
            "version": self.version, "binary_sha256": self.sha256,
            "model": self._config.agent_model, "effort": self._config.agent_effort,
            "stop_hook": "native", "resume": "native", "installation_verified": True,
        }, indent=2) + "\n")
