"""Matched Codex settings and trial-local environment for Harbor runners."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path


# New benchmark runs and the product both default to six settled work Turns.
DEFAULT_REPLAN_AFTER_TURNS = 6


MODES = ("plain", "native-goal", "heartbeat", "turn", "loopx-goal")
CONTEXTS = ("fresh", "resume")
TASK_ENTRIES = ("seeded-todo", "loopx-planned")
SANDBOXES = ("read-only", "workspace-write", "danger-full-access")


@dataclass(frozen=True)
class Execution:
    mode: str = "heartbeat"
    context: str = "fresh"
    sandbox: str = "danger-full-access"
    timeout_seconds: float | None = None
    validation_command: tuple[str, ...] = ()
    task_entry: str | None = None
    turn_envelope: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.turn_envelope, bool) or (self.turn_envelope and self.mode != "heartbeat"):
            raise ValueError("turn_envelope requires heartbeat execution and a boolean opt-in")
        if self.mode not in MODES or self.context not in CONTEXTS:
            raise ValueError("unsupported execution mode or iteration context")
        if self.task_entry is None:
            object.__setattr__(self, "task_entry", "loopx-planned" if self.uses_loopx else "seeded-todo")
        if self.task_entry not in TASK_ENTRIES:
            raise ValueError("unsupported task entry")
        if self.task_entry == "loopx-planned" and not self.uses_loopx:
            raise ValueError("loopx-planned requires a LoopX execution mode")
        if self.context != "fresh" and self.mode not in {"turn", "heartbeat"}:
            raise ValueError("resume requires mode=turn or mode=heartbeat")
        if self.sandbox not in SANDBOXES:
            raise ValueError("unsupported Codex sandbox")
        if self.timeout_seconds is not None and (
            not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0
        ):
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


def turn_envelope_receipt(*, explicit: bool) -> dict:
    """Record only the explicit display opt-in; checkpoint capture is independent."""
    if explicit:
        return {"turn_envelope": True, "turn_envelope_source": "explicit"}
    return {}



def validate_model_catalog(model_catalog: Path, model: str) -> dict:
    """Admit only catalog entries that keep Codex's function-tool surface.

    Real catalog entries are not capability-only. ``tool_mode`` selects how
    tools are delivered: with a valid ``code_mode_only`` entry the real client
    sends ``tools: []`` on its first request, silently removing every task
    tool. ``model_messages`` injects prompt content. A file that merely exists
    therefore proves nothing; reject surfaces this before a trial instead of
    recording an empty-toolface run as model capability.
    """
    if not model_catalog.is_file():
        raise ValueError(f"model catalog missing: {model_catalog}")
    try:
        catalog = json.loads(model_catalog.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"model catalog is not valid JSON: {model_catalog}") from error
    models = catalog.get("models") if isinstance(catalog, dict) else None
    if not isinstance(models, list):
        raise ValueError('model catalog must be an object with a "models" list')
    entries = [e for e in models if isinstance(e, dict) and e.get("slug") == model]
    if not entries:
        raise ValueError(
            f"model catalog has no entry for {model!r}; staging it would leave "
            "the model on unknown-slug behavior while appearing configured"
        )
    entry = entries[0]
    tool_mode = entry.get("tool_mode")
    if tool_mode is not None:
        raise ValueError(
            f"unsupported catalog tool_mode {tool_mode!r} for {model!r}: only the "
            "default function-tool mode is qualified; a code-mode entry makes the "
            "client send an empty tool list"
        )
    return entry

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
    web_search: str = "disabled",
    model_catalog: Path | None = None,
    model_catalog_destination: Path | None = None,
) -> None:
    """Materialize fixed inputs once; never delete sessions between iterations.

    A different effective configuration requires a different trial. Secrets are
    kept out of the settings identity and may rotate without resetting history.

    ``model_catalog`` is required for slugs Codex does not ship built-in
    (e.g. gateway aliases such as ``gpt-5.6-sol``). Without a catalog entry
    Codex treats the model as unknown and offers a reduced tool surface, and
    the ``web_search`` setting has no effect. A catalog entry is not
    capability-only: it can select a tool mode and carry ``model_messages``
    that become prompt content. Entries whose tool mode would strip the
    function-tool surface (for example ``code_mode_only``, which makes the
    real client send ``tools: []``) are rejected before a trial; qualify a
    pinned binary/catalog pair at the first-request boundary with
    ``benchmark.runtime.toolface`` before relying on the rendered tools.
    Host-side installers may set ``model_catalog_destination`` to the absolute
    guest path where those validated catalog bytes are staged. It affects only
    rendering; the host source must still pass the same validation.
    """
    if not model or not effort:
        raise ValueError("model and effort are required")
    if base_url and not api_key:
        raise ValueError("custom provider requires OPENAI_API_KEY")
    if not base_url and not (home / "auth.json").is_file():
        raise ValueError("provide a gateway and API key, or stage CODEX_AUTH_JSON_PATH")
    if wire_api not in {"responses", "chat"}:
        raise ValueError("unsupported provider wire_api")
    if web_search not in {"disabled", "cached", "live"}:
        raise ValueError("unsupported web_search setting")
    if model_catalog is not None:
        validate_model_catalog(model_catalog, model)
    if model_catalog_destination is not None and (
        model_catalog is None or not model_catalog_destination.is_absolute()
    ):
        raise ValueError("catalog destination requires a validated catalog and an absolute guest path")
    home.mkdir(parents=True, exist_ok=True)
    settings = "\n".join(
        [
            f"model = {json.dumps(model)}",
            f"model_reasoning_effort = {json.dumps(effort)}",
        ]
        + (
            # Codex resolves this path from its own working directory, not the
            # caller's; a relative path validates here yet silently fails there.
            [f"model_catalog_json = {json.dumps(str(model_catalog_destination or model_catalog.resolve()))}"]
            if model_catalog is not None
            else []
        )
        + [
            'approval_policy = "never"',
            f"sandbox_mode = {json.dumps(execution.sandbox)}",
            f"web_search = {json.dumps(web_search)}",
            f'model_provider = "{"harbor" if base_url else "openai"}"',
            # Codex filters its inherited shell environment. Set the trial
            # policy explicitly so model tools cannot silently lose it.
            "[shell_environment_policy.set]",
            'LOOPX_USAGE_PING = "0"',
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
