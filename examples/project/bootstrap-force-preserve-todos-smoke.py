#!/usr/bin/env python3
"""Smoke-test canonical reconnect preservation and refusal of retired writes."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
GOAL_ID = "bootstrap-preserve-fixture"


def run_cli(*args: str, check: bool = True) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--format", "json", *args],
        cwd=REPO_ROOT,
        check=check,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


def goal_from_registry(project: Path) -> dict:
    registry = project / ".loopx" / "registry.json"
    payload = json.loads(registry.read_text(encoding="utf-8"))
    return payload["goals"][0]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-bootstrap-preserve-") as tmp:
        canonical_project = Path(tmp) / "canonical-project"
        canonical_project.mkdir()
        canonical_runtime = Path(tmp) / "canonical-runtime"
        canonical = run_cli(
            "--runtime-root", str(canonical_runtime), "bootstrap",
            "--project", str(canonical_project), "--goal-id", GOAL_ID,
            "--objective", "Preserve canonical authority during reconnect.", "--no-global-sync",
        )
        assert canonical["storage_target"]["provider"] == "sqlite", canonical
        canonical_state = Path(canonical["state_file"])
        state_before = canonical_state.read_bytes()
        registry_before = (canonical_project / ".loopx/registry.json").read_bytes()
        refused = run_cli(
            "--runtime-root", str(canonical_runtime), "bootstrap",
            "--project", str(canonical_project), "--goal-id", GOAL_ID,
            "--objective", "Do not rebuild canonical state.",
            "--force", "--preserve-todos", "--no-global-sync", check=False,
        )
        assert refused["ok"] is False, refused
        assert "Canonical creation cannot rebuild existing state" in json.dumps(refused), refused
        assert canonical_state.read_bytes() == state_before
        assert (canonical_project / ".loopx/registry.json").read_bytes() == registry_before

        for provider in ("file", "sqlite"):
            assert_canonical_reconnect(Path(tmp) / provider, provider)

        # The old opt-out no longer authorizes new Markdown Goal writers.
        runtime = Path(tmp) / "unpromoted-runtime"
        configuration = runtime / "machine/configuration.json"
        configuration.parent.mkdir(parents=True)
        configuration.write_text(json.dumps({
            "schema_version": "loopx_machine_configuration_v0", "namespaces": {
                "goal_storage": {"schema_version": "loopx_goal_storage_defaults_v1",
                                 "new_goal_provider": "file", "canonical_creation": False,
                                 "new_goal_handoff_mode": "soft_claim"},
            },
        }), encoding="utf-8")
        project = Path(tmp) / "project"
        project.mkdir()

        refused = run_cli(
            "--runtime-root", str(runtime), "bootstrap",
            "--project",
            str(project),
            "--goal-id",
            GOAL_ID,
            "--objective",
            "Do not revive the retired Markdown writer.",
            "--no-global-sync",
            check=False,
        )
        assert refused["ok"] is False, refused
        assert refused["error_code"] == "canonical_authority_required", refused
        assert not (project / ".loopx/registry.json").exists()
        assert not (project / f".loopx/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md").exists()

    print("bootstrap-force-preserve-todos-smoke ok")
    return 0


def assert_canonical_reconnect(root: Path, provider: str) -> None:
    runtime = root / "runtime"
    configuration = runtime / "machine/configuration.json"
    configuration.parent.mkdir(parents=True)
    configuration.write_text(json.dumps({
        "schema_version": "loopx_machine_configuration_v0", "namespaces": {
            "goal_storage": {"schema_version": "loopx_goal_storage_defaults_v1",
                             "new_goal_provider": provider, "canonical_creation": True,
                             "new_goal_handoff_mode": "soft_claim"},
        },
    }), encoding="utf-8")
    project = root / "project"
    project.mkdir()
    bootstrap = (
        "--runtime-root", str(runtime), "bootstrap", "--project", str(project),
        "--goal-id", GOAL_ID, "--objective", "Preserve canonical Todos on reconnect.",
        "--no-global-sync",
    )
    initial = run_cli(*bootstrap)
    assert initial["ok"] and initial["state_action"] == "created", initial
    assert initial["storage_target"]["provider"] == provider, initial
    state = Path(initial["state_file"])
    registry = project / ".loopx/registry.json"
    prefix = ("--runtime-root", str(runtime), "--registry", str(registry))
    text = "[P1] Preserve canonical work — 保留任务与原始归属。"
    added = run_cli(*prefix, "todo", "add", "--goal-id", GOAL_ID,
                    "--role", "agent", "--task-class", "advancement_task",
                    "--action-kind", "smoke", "--text", text)
    assert added["added"] is True, added
    read = ("todo", "list", "--goal-id", GOAL_ID)
    before = run_cli(*prefix, *read)
    assert before["authority_read"]["source_authority"] == f"{provider}_v0", before
    assert before["authority_read"]["decision_read_from_provider"] is True, before
    assert before["authority_read"]["legacy_fallback_used"] is False, before
    stored = run_cli(*prefix, *read, "--todo-id", added["todo_id"])["todo"]
    assert stored["text"] == text and stored["status"] == "open", stored
    for force_args in (("--force",), ("--force", "--preserve-todos")):
        state_before, registry_before = state.read_bytes(), registry.read_bytes()
        refused = run_cli(*bootstrap, *force_args, check=False)
        assert refused["ok"] is False, refused
        assert "cannot rebuild existing state" in json.dumps(refused), refused
        assert state.read_bytes() == state_before
        assert registry.read_bytes() == registry_before
        assert run_cli(*prefix, *read) == before
    reconnected = run_cli(*bootstrap)
    assert reconnected["ok"] is True, reconnected
    assert reconnected["state_action"] == "kept-existing", reconnected
    assert run_cli(*prefix, *read) == before
    assert text in state.read_text(encoding="utf-8")
    assert goal_from_registry(project)["coordination"]["storage_target"]["provider"] == provider


if __name__ == "__main__":
    raise SystemExit(main())
