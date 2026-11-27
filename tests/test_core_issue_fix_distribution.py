"""Installed Issue Fix selection and Core's existing PR recovery behavior."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from loopx import __version__
from loopx.control_plane.todos.mutation_api import add_goal_todo
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture
from tests.control_plane.host_process_fixture import write_python_host_fixture
from tests.test_core_distribution import REPOSITORY, _environment, _prepare_module, _run, core_python as core_python

DISTRIBUTION = "loopx-capability-issue-fix"
ISSUE_FIX_ENABLED = os.environ.get("LOOPX_ISSUE_FIX_TEST_ENABLED") == "1"


def test_issue_fix_stages_the_workflow_without_wait_or_ledger_owners(tmp_path):
    source = _prepare_module().prepare_capability_source(tmp_path / "issue-fix", DISTRIBUTION)
    owners = source / "loopx/capabilities/issue_fix"
    for name in ("cli.py", "candidate_preflight.py", "acceptance_loop.py", "workflow_plan.py"):
        assert (owners / name).read_bytes() == (REPOSITORY / owners.relative_to(source) / name).read_bytes()
    for name in ("catalog_entry.py", "__init__.py", "correction_settlement.py", "pr_wait_reconcile.py", "pr_review_ack.py"):
        assert not (owners / name).exists()
    assert not (source / "loopx/domain_packs/issue_fix.py").exists()
    assert (source / "requires-core.txt").read_text().strip() == f"loopx-core=={__version__}"


def _guided_args(project: Path, *, host_surface: str | None = "shell") -> list[str]:
    arguments = [
        "--runtime-root", str(project / "runtime"), "--format", "json",
        "start-goal", "--guided", "--project", str(project),
        "--goal-text", "Generate a checked inventory report.",
    ]
    if host_surface:
        arguments.extend(["--host-surface", host_surface])
    return arguments


def test_installed_issue_fix_is_quiet_until_selected(core_python, tmp_path):
    probe = '''
import json, sys
from importlib.metadata import distribution
from importlib.util import find_spec
from loopx.entrypoint import main

installed = sys.argv[2] == "True"
assert (find_spec("loopx.capabilities.issue_fix.cli") is not None) is installed
if installed:
    workflow = distribution("loopx-capability-issue-fix")
    core = distribution("loopx-core")
    assert workflow.version == core.version and f"loopx-core=={core.version}" in workflow.requires
    members = {entry.as_posix() for entry in workflow.files if entry.parts[0] == "loopx"}
    assert members.isdisjoint(entry.as_posix() for entry in core.files)
    assert "loopx/capabilities/issue_fix/cli.py" in members
assert main(json.loads(sys.argv[1])) == 0
for name in ("cli", "workflow_plan", "candidate_preflight", "acceptance_loop", "pr_lifecycle"):
    assert "loopx.capabilities.issue_fix." + name not in sys.modules, name
'''
    catalog_args = ["capability", "show", "issue-fix", "--format", "json"]
    discovered = subprocess.run(
        [core_python, "-I", "-c", probe, json.dumps(catalog_args), str(ISSUE_FIX_ENABLED)],
        cwd=tmp_path, env=_environment(core_python), capture_output=True, text=True, timeout=45,
    )
    assert discovered.returncode == 0, discovered.stderr
    capability = json.loads(discovered.stdout)["capability"]
    assert capability["provider_state"]["ready"] is ISSUE_FIX_ENABLED
    assert capability["commands"] and all(
        row["command"].startswith("loopx issue-fix ") for row in capability["commands"]
    )
    reference = json.loads(_run(core_python, ["commands", "--format", "json"], tmp_path).stdout)
    commands = [entry["command"] for group in reference["groups"] for entry in group["commands"]]
    assert ("loopx issue-fix" in commands) is ISSUE_FIX_ENABLED

    args = _guided_args(tmp_path)
    packet = json.loads(_run(core_python, args, tmp_path).stdout)
    assert packet["goal_text"] == "Generate a checked inventory report."
    assert packet["guided_transaction"]["writes_now"] is False
    assert packet["command_pack"]["goal_start_contract"]["domain_route_hints"] == {}
    assert "issue_fix_workflow_plan_template" not in packet["command_pack"]["commands"]
    assert "issue-fix workflow-plan" not in packet["message"]
    if not ISSUE_FIX_ENABLED:
        command = _run(core_python, ["issue-fix", "--help"], tmp_path, expected=2)
        assert DISTRIBUTION in command.stderr
        for start_args in (args, _guided_args(tmp_path, host_surface=None)):
            refusal = _run(core_python, [*start_args, "--capability-route", "issue-fix"], tmp_path, expected=2)
            assert DISTRIBUTION in json.loads(refusal.stdout)["error"]
            assert not refusal.stderr
        bootstrap = _run(core_python, [
            "--format", "json", "bootstrap-command-pack", "--project", str(tmp_path),
            "--capability-route", "issue-fix",
        ], tmp_path, expected=2)
        assert DISTRIBUTION in json.loads(bootstrap.stdout)["error"]
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(not ISSUE_FIX_ENABLED, reason="requires the matching Issue Fix wheel")
def test_installed_issue_fix_uses_original_admission_and_validated_git_branch(core_python, tmp_path):
    selected = json.loads(_run(
        core_python, [*_guided_args(tmp_path), "--capability-route", "issue-fix"], tmp_path,
    ).stdout)
    route = selected["guided_transaction"]["selected_capability_route"]
    assert route["capability_id"] == "issue-fix"
    assert route["candidate_preflight"]["required_before_implementation"] is True
    assert selected["guided_transaction"]["writes_now"] is False

    artifact = json.loads(_run(
        core_python, ["issue-fix", "repo-branch-fixture", "--format", "json"], tmp_path,
    ).stdout)
    assert artifact["ok"] is True
    verified = artifact["validated_fix_artifact"]
    assert verified["repro_before"]["passed"] is False
    assert verified["validation_after"]["passed"] is True
    assert verified["repo_branch"]["branch_created"] is True
    assert verified["repo_branch"]["external_remote_used"] is False


@pytest.mark.skipif(ISSUE_FIX_ENABLED, reason="qualifies Core without the workflow wheel")
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_core_retains_bound_pr_wait_and_reloads_only_due_observation(core_python, tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _ = promoted_create_fixture(tmp_path, provider=provider)
    config = json.loads(registry.read_text())
    config["goals"][0]["available_capabilities"] = ["external_evidence_poll"]
    config["goals"][0]["quota"] = {"compute": 0}
    registry.write_text(json.dumps(config), encoding="utf-8")
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]
    heartbeat = [*prefix, "heartbeat-prequota", "-g", "goal-a", "-a", "agent-a"]

    idle = subprocess.run(
        [core_python, "-I", "-c",
         "import json,sys;from loopx.entrypoint import main;"
         "assert main(json.loads(sys.argv[1]))==0;"
         "assert 'loopx.capabilities.issue_fix.pr_lifecycle' not in sys.modules",
         json.dumps(heartbeat)],
        cwd=tmp_path, env=_environment(core_python), capture_output=True, text=True, timeout=45,
    )
    assert idle.returncode == 0, (idle.stdout, idle.stderr)

    todo_id = add_goal_todo(
        registry_path=registry, goal_id="goal-a", role="agent", claimed_by="agent-a",
        text="Recheck the report after its source update merges.", resume_when="pr_merged:owner/repo#42",
    )["todo_id"]
    executable_dir = tmp_path / "gh-fixture"
    executable_dir.mkdir()
    calls = tmp_path / "observation-calls"
    write_python_host_fixture(executable_dir / "gh", f'''#!{core_python}
import json
from pathlib import Path
import sys
assert sys.argv[1:7] == ["pr", "view", "42", "--repo", "owner/repo", "--json"]
with Path({str(calls)!r}).open("a") as marker:
    marker.write("once\\n")
print(json.dumps({{"state": "MERGED", "mergedAt": "2026-10-01T00:00:00Z",
                   "url": "https://github.com/owner/repo/pull/42"}}))
''')
    # Isolate the child's gh process while preserving Node on PATH.
    environment = {"PATH": str(executable_dir) + os.pathsep + _environment(core_python)["PATH"]}
    first = json.loads(_run(core_python, heartbeat, tmp_path, environment=environment).stdout)
    assert first["checks"]["pr_dependencies"]["merged_count"] == 1
    assert first["quota_spend_required"] is False
    todo = json.loads(_run(
        core_python, [*prefix, "todo", "list", "--goal-id", "goal-a", "--todo-id", todo_id], tmp_path,
    ).stdout)["todo"]
    assert todo["resume_ready"] is True
    replay = json.loads(_run(core_python, heartbeat, tmp_path, environment=environment).stdout)
    assert replay["checks"]["pr_dependencies"]["external_read_count"] == 0
    assert calls.read_text().splitlines() == ["once"]
