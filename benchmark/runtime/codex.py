"""Matched Codex settings and trial-local environment for Harbor runners."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path


MODES = ("plain", "native-goal", "heartbeat", "turn", "loopx-goal")
CONTEXTS = ("fresh", "resume")
TASK_ENTRIES = ("seeded-todo", "loopx-planned")
SANDBOXES = ("read-only", "workspace-write", "danger-full-access")


@dataclass(frozen=True)
class Execution:
    mode: str = "heartbeat"
    context: str = "fresh"
    sandbox: str = "danger-full-access"
    timeout_seconds: float = 4700
    validation_command: tuple[str, ...] = ()
    task_entry: str = "seeded-todo"

    def __post_init__(self) -> None:
        if self.mode not in MODES or self.context not in CONTEXTS:
            raise ValueError("unsupported execution mode or iteration context")
        if self.task_entry not in TASK_ENTRIES:
            raise ValueError("unsupported task entry")
        if self.task_entry == "loopx-planned" and not self.uses_loopx:
            raise ValueError("loopx-planned requires a LoopX execution mode")
        if self.context != "fresh" and self.mode not in {"turn", "heartbeat"}:
            raise ValueError("resume requires mode=turn or mode=heartbeat")
        if self.sandbox not in SANDBOXES:
            raise ValueError("unsupported Codex sandbox")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("execution timeout must be finite and positive")
        if not isinstance(self.validation_command, (list, tuple)):
            raise ValueError("validation_command must be an argv list")
        if any(not isinstance(arg, str) or not arg for arg in self.validation_command):
            raise ValueError("validation_command must contain non-empty argv strings")
        object.__setattr__(self, "validation_command", tuple(self.validation_command))
        if self.mode == "turn" and not self.validation_command:
            raise ValueError("mode=turn requires an independent validation_command")
        if self.mode != "turn" and self.validation_command:
            raise ValueError("validation_command is only used by mode=turn")

    @property
    def uses_loopx(self) -> bool:
        return self.mode in {"heartbeat", "turn", "loopx-goal"}

    @property
    def native_goal(self) -> bool:
        return self.mode in {"native-goal", "loopx-goal"}


def prepare_codex_home(
    home: Path,
    *,
    execution: Execution,
    workspace: Path,
    model: str,
    effort: str,
    base_url: str,
    api_key: str,
    wire_api: str,
    skills: Path | None,
) -> None:
    """Materialize fixed inputs once; never delete sessions between iterations.

    A different effective configuration requires a different trial. Secrets are
    kept out of the settings identity and may rotate without resetting history.
    """
    if not model or not effort:
        raise ValueError("model and effort are required")
    if base_url and not api_key:
        raise ValueError("custom provider requires OPENAI_API_KEY")
    if not base_url and not (home / "auth.json").is_file():
        raise ValueError("provide a gateway and API key, or stage CODEX_AUTH_JSON_PATH")
    if wire_api not in {"responses", "chat"}:
        raise ValueError("unsupported provider wire_api")
    home.mkdir(parents=True, exist_ok=True)
    settings = "\n".join(
        [
            f"model = {json.dumps(model)}",
            f"model_reasoning_effort = {json.dumps(effort)}",
            'approval_policy = "never"',
            f"sandbox_mode = {json.dumps(execution.sandbox)}",
            'web_search = "disabled"',
            f'model_provider = "{"harbor" if base_url else "openai"}"',
            "[features]",
            f"goals = {str(execution.native_goal).lower()}",
            "unified_exec = true",
            "[memories]",
            "generate_memories = false",
            "use_memories = false",
            f"[projects.{json.dumps(str(workspace))}]",
            'trust_level = "trusted"',
        ]
        + (
            [
                "[model_providers.harbor]",
                'name = "harbor"',
                f"base_url = {json.dumps(base_url)}",
                f"wire_api = {json.dumps(wire_api)}",
                'env_key = "OPENAI_API_KEY"',
                "request_max_retries = 8",
                "stream_max_retries = 8",
                "stream_idle_timeout_ms = 300000",
            ]
            if base_url
            else []
        )
        + [""]
    )
    config = home / "config.toml"
    if config.exists() and config.read_text(encoding="utf-8") != settings:
        raise ValueError("Codex settings changed within a trial; use a new trial")
    if not config.exists():
        config.write_text(settings, encoding="utf-8")
        config.chmod(0o444)
    skills_link = home / "skills"
    if execution.uses_loopx:
        if skills is None or not skills.is_dir():
            raise ValueError("LoopX execution requires formally installed skills")
        if not skills_link.exists():
            skills_link.symlink_to(skills.resolve(), target_is_directory=True)
        if skills_link.resolve() != skills.resolve():
            raise ValueError("Codex skills changed within a trial")
    elif skills_link.exists():
        raise ValueError("baseline Codex home must not contain LoopX skills")
    # The provider reads OPENAI_API_KEY. No credential file is copied to logs.


def process_environment(
    home: Path, *, base: dict[str, str] | None = None
) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    env["CODEX_HOME"] = str(home)
    return env
