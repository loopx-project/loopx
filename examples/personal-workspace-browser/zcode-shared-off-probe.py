"""Full shared-off observations; the caller compares immutable base/head output.

Only environment observations are fixed. No Core identity, quota, status or
process/installer decisions are replaced. JSON keys and complete user text stay.
"""

from __future__ import annotations
import dataclasses
import hashlib
import io
from contextlib import redirect_stdout
import ast
import json
from pathlib import Path
import sys
from datetime import datetime, timezone
import pytest
from loopx.control_plane.runtime import time as runtime_time
from loopx import doctor
from loopx.entrypoint import main
from loopx.status import collect_status
from loopx.presentation.renderers.status_markdown import render_status_markdown
from loopx.host_loop_activation import build_host_loop_activation_packet
from loopx.slash_command_install import (
    install_slash_commands,
    render_slash_command_install_markdown,
)
from loopx.extensions.process_runtime import run_capped_process

root, fixture_source = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
root.mkdir(parents=True, exist_ok=True)
# Reuse only the existing common environment fixture. Its ZCode-host probe
# stub is excluded: this comparison must not import or probe the optional host.
fixture_tree = ast.parse(
    (fixture_source / "tests/test_zcode_doctor_skill_delivery.py").read_text(
        encoding="utf-8"
    )
)
fixture = next(
    node
    for node in fixture_tree.body
    if isinstance(node, ast.FunctionDef) and node.name == "doctor_context"
)
fixture.decorator_list = []
fixture.body = [
    node
    for node in fixture.body
    if not any(
        isinstance(value, ast.Constant)
        and value.value
        == "loopx.zcode_goal_mode.diagnostics.collect_zcode_host_diagnostics"
        for value in ast.walk(node)
    )
]
namespace = {"Path": Path, "pytest": pytest, "doctor": doctor}
exec(
    compile(
        ast.Module(body=[fixture], type_ignores=[]), "shared_doctor_environment", "exec"
    ),
    namespace,
)
monkeypatch = pytest.MonkeyPatch()
namespace["doctor_context"](monkeypatch, root)


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return (
            cls(2026, 10, 12, 0, 0, tzinfo=timezone.utc).astimezone(tz)
            if tz
            else cls(2026, 10, 12)
        )


monkeypatch.setattr(doctor, "datetime", FixedDateTime)
# Freeze producer observation clocks, while retaining real process deadlines.

original_now = runtime_time.now_utc


def fixed_now():
    return FixedDateTime(2026, 10, 12, tzinfo=timezone.utc)


monkeypatch.setattr(runtime_time, "now_utc", fixed_now)
for loaded in list(sys.modules.values()):
    if getattr(loaded, "__name__", "").startswith("loopx."):
        if getattr(loaded, "now_utc", None) is original_now:
            monkeypatch.setattr(loaded, "now_utc", fixed_now)
        if getattr(loaded, "datetime", None) is datetime:
            monkeypatch.setattr(loaded, "datetime", FixedDateTime)
project = root / "project"
project.mkdir(exist_ok=True)
state = project / "GOAL.md"
state.write_text(
    "---\nstatus: active\n---\n# Shared off\n\n## Agent Todo\n", encoding="utf-8"
)
registry = root / "registry.json"
registry.write_text(
    json.dumps(
        {
            "schema_version": 1,
            "common_runtime_root": str(root / "runtime"),
            "goals": [
                {
                    "id": "shared-off",
                    "repo": str(project),
                    "state_file": str(state),
                    "status": "active",
                    "domain": "general",
                    "authority_sources": [],
                    "adapter": {
                        "kind": "generic_project_goal_v0",
                        "status": "connected",
                    },
                    "coordination": {
                        "registered_agents": ["codex-worker"],
                        "agent_profiles": {"codex-worker": {"agent_type": "codex-cli"}},
                    },
                }
            ],
        }
    ),
    encoding="utf-8",
)


def effects():
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for d in (project, root / "runtime", root / "codex-install")
        if d.exists()
        for p in d.rglob("*")
        if p.is_file()
    }


observations = {}


def record(name, inputs, operation, render=None):
    before = effects()
    payload = operation()
    observations[name] = {
        "inputs": inputs,
        "payload": payload,
        "text": render(payload) if render else None,
        "state_before": before,
        "state_after": effects(),
    }


record(
    "doctor-default",
    {"agent_type": None},
    lambda: doctor.collect_doctor(),
    doctor.render_doctor_markdown,
)
record(
    "doctor-codex",
    {"agent_type": "codex-cli"},
    lambda: doctor.collect_doctor(agent_type="codex-cli"),
    doctor.render_doctor_markdown,
)
record(
    "status-default",
    {
        "registry": str(registry),
        "runtime": str(root / "runtime"),
        "goal_filter": "shared-off",
    },
    lambda: collect_status(
        registry_path=registry,
        runtime_root_override=str(root / "runtime"),
        limit=5,
        goal_id="shared-off",
    ),
    render_status_markdown,
)
for agent in (None, "codex-worker"):
    record(
        "activation-" + str(agent),
        {
            "agent_type": "codex-cli",
            "agent_id": agent,
            "registered_agents": ["codex-worker"],
        },
        lambda agent=agent: build_host_loop_activation_packet(
            agent_type="codex-cli",
            goal_id="shared-off",
            agent_id=agent,
            registered_agents=["codex-worker"],
        ),
    )
record(
    "installer-codex-dry",
    {"surfaces": ["codex"], "execute": False},
    lambda: install_slash_commands(
        execute=False, surfaces=["codex"], codex_home=str(root / "codex-install")
    ),
    render_slash_command_install_markdown,
)
record(
    "installer-codex-apply",
    {"surfaces": ["codex"], "execute": True},
    lambda: install_slash_commands(
        execute=True, surfaces=["codex"], codex_home=str(root / "codex-install")
    ),
    render_slash_command_install_markdown,
)
for output_format in ("json", "markdown"):
    arguments = [
        "--registry",
        str(registry),
        "--runtime-root",
        str(root / "runtime"),
        "--format",
        output_format,
        "turn",
        "plan",
        "--goal-id",
        "shared-off",
        "--agent-id",
        "codex-worker",
    ]

    def dispatch(arguments=arguments, output_format=output_format):
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = main(arguments)
        text = stream.getvalue()
        return {
            "exit_code": code,
            "body": json.loads(text) if output_format == "json" else text,
        }

    record("turn-plan-" + output_format, {"arguments": arguments}, dispatch)
for kind, command, limit in [
    ("normal", "print('same shared-off output')", 100),
    ("overflow", "print('x'*10000)", 32),
    ("timeout", "import time;time.sleep(10)", 100),
]:
    record(
        "process-" + kind,
        {"program": command, "input": "", "output_limit": limit, "timeout": 0.3},
        lambda command=command, limit=limit: dataclasses.asdict(
            run_capped_process(
                [sys.executable, "-c", command],
                stdin=b"",
                timeout_seconds=0.3,
                output_limit_bytes=limit,
            )
        ),
    )
# The JSON carrier retains keys/types; normalize only explicitly volatile
# observation time/cost coordinates and source/fixture installation locations.
volatile = {
    "observed_at",
    "generated_at",
    "checked_at",
    "read_at",
    "routes_read_at",
    "duration_ms",
    "elapsed_ms",
    "collected_at",
    "last_read_at",
    "served_at",
    "staleness_seconds",
}
source = str(Path(doctor.__file__).resolve().parents[1])


def stable(value, key=None):
    if isinstance(value, dict):
        return {k: stable(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [stable(v) for v in value]
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if key in volatile and value is not None:
        return (
            0
            if type(value) is int
            else 0.0
            if type(value) is float
            else "2026-10-12T00:00:00Z"
            if isinstance(value, str)
            else value
        )
    if isinstance(value, str):
        return (
            value.replace(sys.executable, "<interpreter>")
            .replace(str(root), "<fixture>")
            .replace(source, "<source>")
        )
    return value


print(json.dumps(stable(observations), ensure_ascii=False, sort_keys=True))
monkeypatch.undo()
