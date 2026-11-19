from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.skill_install_readback import PACKAGED_HOST_SKILL_IDS
from loopx.capabilities.project_skill_delivery import (
    classify_host_skill_sources,
    discover_project_scoped_skill_ids,
)

from loopx.cli import main


def _connected_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    registry = project / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text("{}\n", encoding="utf-8")
    return project


def test_project_skill_cli_installs_multiple_host_surfaces(
    tmp_path: Path,
    capsys,
) -> None:
    project = _connected_project(tmp_path)
    common = [
        "--project",
        str(project),
        "--skill",
        "loopx-material",
        "--surface",
        "codex",
        "--surface",
        "antigravity",
        "--surface",
        "claude-code",
        "--surface",
        "opencode",
        "--format",
        "json",
    ]

    assert main(["project-skill", "install", *common]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["mode"] == "preview"
    assert preview["changed"] is True
    assert all(item["status"] == "missing" for item in preview["surfaces"])

    assert main(["project-skill", "install", *common, "--execute"]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["status"] == "current"
    assert all(item["status"] == "current" for item in applied["surfaces"])

    assert main(["project-skill", "status", *common]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["managed"] is True

    assert main(["project-skill", "uninstall", *common, "--execute"]) == 0
    removed = json.loads(capsys.readouterr().out)
    assert removed["status"] == "missing"


@pytest.mark.parametrize("skill_id", PACKAGED_HOST_SKILL_IDS)
def test_global_workflow_supports_explicit_project_lifecycle(tmp_path, capsys, skill_id):
    project = _connected_project(tmp_path)
    args = ["--project", str(project), "--skill", skill_id, "--format", "json"]
    target = project / ".agents" / "skills" / skill_id

    assert main(["project-skill", "install", *args]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "missing"
    assert not target.exists()
    assert main(["project-skill", "install", *args, "--execute"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "current"
    assert (target / ".loopx-skill-scope").read_text().strip() == "global"
    assert main(["project-skill", "status", *args]) == 0
    assert json.loads(capsys.readouterr().out)["managed"] is True
    assert main(["project-skill", "uninstall", *args, "--execute"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "missing"
    assert not target.exists()
    # Discovery never registers a Goal or activates a capability.
    assert (project / ".loopx" / "registry.json").read_text() == "{}\n"


def test_only_capability_local_workflows_are_excluded_from_global_install():
    source = Path(__file__).resolve().parents[1] / "skills"
    assert set(discover_project_scoped_skill_ids(source)) == {
        "loopx-material", "loopx-change-quality",
    }


def test_fixed_host_install_delivers_only_declared_global_skills():
    """A marker-less source is repo-only, and the delivered set is the checked set.

    ``loopx-pr-merge`` is deliberately repo-kept: it has no scope marker so that
    no delivery path can copy a merge-decision workflow onto a host that never
    merges LoopX pull requests. The sources a host install may materialize must
    also stay identical to the packaged skill set the host checks verify, so
    neither list can widen without a conscious change.
    """
    source = Path(__file__).resolve().parents[1] / "skills"
    projection = classify_host_skill_sources(source)
    assert set(projection["deliverable_skill_ids"]) == set(PACKAGED_HOST_SKILL_IDS)
    assert len(projection["deliverable_skill_ids"]) == len(PACKAGED_HOST_SKILL_IDS)
    assert projection["project_skill_ids"] == ("loopx-change-quality", "loopx-material")
    assert projection["repo_only_skill_ids"] == ("loopx-pr-merge",)


def test_global_workflow_copy_still_requires_connected_project(tmp_path, capsys):
    assert main([
        "project-skill", "install", "--project", str(tmp_path),
        "--skill", "loopx-self-repair", "--execute", "--format", "json",
    ]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "blocked"
    assert not (tmp_path / ".agents").exists()
