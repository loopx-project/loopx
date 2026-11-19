"""Public retries preserve the committed pre-v2 configure/disable operation.

The default regression emits the frozen old adapter wire through the public CLI
and real authority. Set LOOPX_TEST_ACCEPTANCE_LEGACY_SOURCE to the pinned source
below to qualify an actual old CLI and stored-state upgrade instead. No download
or historical checkout is required by the ordinary regression.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import test_goal_acceptance_cli as acceptance_fixture


LEGACY_REVISION = "4b392c8328b7ba4451baefbd7812dcd3dcc580db"
acceptance_goal = acceptance_fixture.acceptance_goal
_OLD_ADAPTER = '''from loopx.control_plane.goals import acceptance as owner
original = owner._result
def old_wire(method, request, **kwargs):
    if method == "goal.acceptance.configure":
        request = {**request, "disable": request["document"] is None}
    return original(method, request, **kwargs)
owner._result = old_wire
from loopx.cli import main
raise SystemExit(main())'''


def _old_cli(project: Path, *arguments: str) -> tuple[int, dict]:
    historical = os.environ.get("LOOPX_TEST_ACCEPTANCE_LEGACY_SOURCE")
    source = Path(historical) if historical else Path(__file__).resolve().parents[2]
    if historical:
        revision = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert revision == LEGACY_REVISION
    command = ["-m", "loopx.cli"] if historical else ["-c", _OLD_ADAPTER]
    result = subprocess.run(
        [sys.executable, *command, "--registry", str(project.parent / "registry.json"),
         "--runtime-root", str(project.parent / "runtime"), "--format", "json",
         "goal-acceptance", *arguments, "--goal-id", "goal-acceptance"],
        cwd=source,
        env={**os.environ, "PYTHONPATH": str(source), "LOOPX_USAGE_PING": "0"},
        capture_output=True, text=True, encoding="utf-8", timeout=120, check=False,
    )
    assert result.stdout.strip(), result.stderr
    return result.returncode, json.loads(result.stdout)


@pytest.mark.parametrize("action", ["configure", "disable"])
def test_public_retry_recovers_old_configuration_without_restoring_it(acceptance_goal, action):
    project, document_path, cli, _ = acceptance_goal
    if action == "disable":
        initial = cli("inspect")[1]["provider_revision"]
        code, configured = _old_cli(
            project, "configure", "--document", str(document_path),
            "--expected-provider-revision", initial, "--operation-id", "old-initial", "--execute",
        )
        assert code == 0 and configured["status"] == "applied", configured
    before = cli("inspect")[1]["provider_revision"]
    arguments = [action, "--expected-provider-revision", before,
                 "--operation-id", "old-lost-reply", "--execute"]
    if action == "configure":
        arguments += ["--document", str(document_path)]
    code, historical = _old_cli(project, *arguments)
    assert code == 0 and historical["status"] == "applied", historical

    # The public caller retained its parameters but lost the response. A later
    # independent configuration must survive both that retry and its rejection.
    current = cli("inspect")[1]
    newer_path = project / "newer-checks.json"
    newer_document = json.loads(document_path.read_text(encoding="utf-8"))
    newer_document["objective"] = "Keep the current owner configuration"
    newer_path.write_text(json.dumps(newer_document), encoding="utf-8")
    code, newer = cli("configure", "--document", str(newer_path),
                      "--expected-provider-revision", current["provider_revision"],
                      "--operation-id", "current-independent-operation", "--execute")
    assert code == 0 and newer["status"] == "applied", newer
    retained = cli("inspect")[1]

    for _ in range(2):
        code, replay = cli(*arguments)
        assert code == 0 and replay["status"] == "replayed", replay
        assert replay["changed"] is False
        assert replay["operation_id"] == "old-lost-reply"
        assert replay["provider_revision"] == historical["provider_revision"]
        assert replay["goal_acceptance_contract"] == retained["goal_acceptance_contract"]
        assert cli("inspect")[1] == retained

    conflicting = arguments.copy()
    conflicting[conflicting.index("--expected-provider-revision") + 1] = retained["provider_revision"]
    code, conflict = cli(*conflicting)
    assert code == 1 and "coordination_operation_identity_mismatch" in conflict["error"], conflict
    if action == "configure":
        conflicting = arguments.copy()
        conflicting[conflicting.index("--document") + 1] = str(newer_path)
        code, conflict = cli(*conflicting)
        assert code == 1 and "coordination_operation_identity_mismatch" in conflict["error"], conflict
    assert cli("inspect")[1] == retained
