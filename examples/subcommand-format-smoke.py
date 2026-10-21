#!/usr/bin/env python3
"""Smoke-test documented subcommand-local --format JSON aliases."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
GOAL_ID = "subcommand-format-goal"


def package_version() -> str:
    namespace: dict[str, object] = {}
    exec((REPO_ROOT / "loopx" / "__init__.py").read_text(encoding="utf-8"), namespace)
    version = namespace.get("__version__")
    assert isinstance(version, str), version
    return version


def write_fixture(root: Path) -> Path:
    project = root / "project"
    runtime = root / "runtime"
    state_file = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "## Agent Todo\n\n"
        "- [ ] [P1] Keep the fixture CLI handoff compact.\n\n"
        "## Next Action\n\n"
        "- Verify documented CLI command ordering remains runnable.\n",
        encoding="utf-8",
    )
    registry_path = project / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "fixture",
                        "status": "active",
                        "repo": str(project),
                        "state_file": f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md",
                        "adapter": {
                            "kind": "harness_self_improvement",
                            "status": "connected-read-only",
                        },
                        "quota": {"compute": 1.0, "window_hours": 24},
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return registry_path


def cli_json(registry_path: Path, *args: str) -> dict:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry_path),
            *args,
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, (args, result.returncode, result.stdout, result.stderr)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError((args, result.stdout, result.stderr)) from exc
    assert isinstance(payload, dict), (args, payload)
    return payload


def main() -> int:
    expected_version = package_version()
    version_text = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--version"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert version_text.returncode == 0, (
        version_text.returncode,
        version_text.stdout,
        version_text.stderr,
    )
    assert version_text.stdout.strip() == f"loopx {expected_version}", version_text.stdout

    version_json = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--format", "json", "version"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert version_json.returncode == 0, (
        version_json.returncode,
        version_json.stdout,
        version_json.stderr,
    )
    version_payload = json.loads(version_json.stdout)
    assert version_payload["ok"] is True, version_payload
    assert version_payload["version"] == expected_version, version_payload

    with tempfile.TemporaryDirectory(prefix="loopx-subcommand-format-smoke-") as raw_tmp:
        registry_path = write_fixture(Path(raw_tmp))

        heartbeat = cli_json(
            registry_path,
            "heartbeat-prompt",
            "--goal-id",
            GOAL_ID,
            "--thin",
            "--format",
            "json",
        )
        assert heartbeat["ok"] is True, heartbeat
        assert heartbeat["schema_version"] == "heartbeat_agent_input_v1", heartbeat
        assert heartbeat["interface_budget"]["mode"] == "thin", heartbeat

        upgrade = cli_json(registry_path, "upgrade-plan", "--format", "json")
        assert upgrade["ok"] is True, upgrade
        assert upgrade["mode"] == "upgrade-plan", upgrade

        update = cli_json(registry_path, "update", "--format", "json", "--check")
        assert update["ok"] is True, update
        assert update["mode"] == "update", update
        assert update["check_only"] is True, update

        promotion = cli_json(registry_path, "promotion-gate", "--format", "json")
        assert promotion["ok"] is True, promotion
        assert promotion["gate"] == "promotion_readiness", promotion

        status = cli_json(
            registry_path,
            "status",
            "--format",
            "json",
            "--limit",
            "1",
        )
        assert status["ok"] is True, status
        assert "attention_queue" in status, status

        handoff = cli_json(
            registry_path,
            "review-packet",
            "--goal-id",
            GOAL_ID,
            "--handoff-only",
            "--format",
            "json",
        )
        assert handoff["ok"] is True, handoff
        assert handoff["handoff_only"] is True, handoff
        assert "packet" not in handoff, handoff

    print("subcommand-format-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
