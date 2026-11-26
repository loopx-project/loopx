"""Exercise the installed PR review capability and its independent package boundary."""

import json
import os
import subprocess

import pytest

from loopx import __version__
from tests.test_core_distribution import REPOSITORY, _environment, _prepare_module, _run, core_python as core_python

DISTRIBUTION = "loopx-capability-pull-request-review"
REVIEW_ENABLED = os.environ.get("LOOPX_REVIEW_TEST_ENABLED") == "1"


def test_review_source_stages_the_existing_execution_owners(tmp_path):
    source = _prepare_module().prepare_capability_source(tmp_path / "review", DISTRIBUTION)
    review = source / "loopx/capabilities/pr_review_queue"
    assert {path.name for path in review.glob("*.py")}.isdisjoint({
        "__init__.py", "catalog_entry.py", "goal_configuration.py", "machine_defaults.py",
        "order.py", "review_body.py", "scheduling.py",
    })
    assert (review / "core.py").is_file()
    assert (review / "experiences/pr-5944-review-frame.md").is_file()
    assert (source / "loopx/cli_commands/pr_review.py").is_file()
    assert (source / "loopx/pr_review.py").is_file()
    assert (source / "loopx/pr_review_merge_readiness.py").is_file()
    for path in (source / "loopx").rglob("*.py"):
        assert path.read_bytes() == (REPOSITORY / path.relative_to(source)).read_bytes()
    assert (source / "requires-core.txt").read_text().strip() == f"loopx-core=={__version__}"


def test_installed_core_discovers_review_without_execution_imports(core_python, tmp_path):
    script = '''
from importlib.metadata import PackageNotFoundError, distribution
from importlib.util import find_spec
from loopx.entrypoint import main
import sys

installed = sys.argv[1] == "True"
if installed:
    review = distribution("loopx-capability-pull-request-review")
    core = distribution("loopx-core")
    recall_module = "loopx/capabilities/agent_turn_recall/core.py"
    assert review.version == core.version
    assert f"loopx-core=={core.version}" in review.requires
    members = {path.as_posix() for path in review.files if path.parts[0] == "loopx"}
    assert "loopx/cli_commands/pr_review.py" in members and "loopx/pr_review.py" in members
    assert recall_module not in members
    assert members.isdisjoint(path.as_posix() for path in core.files)
else:
    try:
        distribution("loopx-capability-pull-request-review")
    except PackageNotFoundError:
        pass
    else:
        raise AssertionError("unexpected review wheel")
assert (find_spec("loopx.cli_commands.pr_review") is not None) is installed
assert main(["capability", "show", "pull-request-review", "--format", "json"]) == 0
from loopx.capabilities.pr_review_queue.goal_configuration import configuration_summary
assert configuration_summary({}) is None
for module in ("loopx.cli_commands.pr_review", "loopx.pr_review", "loopx.capabilities.pr_review_queue.core",
               "loopx.capabilities.pr_review_queue.github_source"):
    assert module not in sys.modules
'''
    discovered = subprocess.run(
        [core_python, "-I", "-c", script, str(REVIEW_ENABLED)],
        cwd=tmp_path, env=_environment(core_python), capture_output=True, text=True, timeout=45,
    )
    assert discovered.returncode == 0, discovered.stderr
    capability = json.loads(discovered.stdout)["capability"]
    assert capability["provider_state"]["ready"] is REVIEW_ENABLED
    assert capability["provider_state"]["installed"] is REVIEW_ENABLED
    reference = json.loads(_run(core_python, ["commands", "--format", "json"], tmp_path).stdout)
    commands = [entry["command"] for group in reference["groups"] for entry in group["commands"]]
    assert ("loopx pr-review --help" in commands) is REVIEW_ENABLED
    if not REVIEW_ENABLED:
        result = _run(core_python, ["pr-review", "--help"], tmp_path, expected=2)
        assert DISTRIBUTION in result.stderr


@pytest.mark.skipif(not REVIEW_ENABLED, reason="requires the matching PR review wheel")
def test_installed_review_resumes_the_exact_head_queue(core_python, tmp_path):
    registry = tmp_path / "registry.json"
    registry.write_text('{"goals": []}', encoding="utf-8")
    checkpoint = tmp_path / "checkpoint.json"
    fixture = REPOSITORY / "examples/fixtures/pr-review.public.json"
    command = [
        "--registry", str(registry), "--runtime-root", str(tmp_path / "runtime"),
        "--format", "json", "pr-review", "--fixture", str(fixture), "--state", "open",
        "--autonomous-observation", "--observation-state-file", str(checkpoint),
    ]
    first = json.loads(_run(core_python, command, tmp_path).stdout)
    observation = first["autonomous_review"]
    assert observation["candidate_count"] == 1
    assert observation["candidate"]["number"] == 771
    assert observation["candidate"]["head_oid"] == "7710000000000000000000000000000000000000"
    assert observation["write_authority_granted"] is False

    replay = json.loads(_run(core_python, command, tmp_path).stdout)
    assert replay["request"]["previous_observation_supplied"] is True
    assert replay["autonomous_review"]["candidate"] == observation["candidate"]

    second = json.loads(_run(
        core_python,
        [*command, "--projected-exact-head", "771@7710000000000000000000000000000000000000"],
        tmp_path,
    ).stdout)
    assert second["autonomous_review"]["projected_candidate_count"] == 1
    assert second["autonomous_review"]["candidate"]["number"] == 773
    assert second["autonomous_review"]["write_authority_granted"] is False
