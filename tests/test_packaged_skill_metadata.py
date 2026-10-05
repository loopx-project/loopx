import re
from pathlib import Path

import pytest

from loopx.capabilities.project_skill_delivery import classify_host_skill_sources
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
_SCOPED_SOURCES = classify_host_skill_sources(REPO_ROOT / "skills")
RELEASE_SKILL_IDS = (
    *_SCOPED_SOURCES["deliverable_skill_ids"],
    *_SCOPED_SOURCES["project_skill_ids"],
)


def test_packaged_workflow_skill_frontmatter_is_discoverable() -> None:
    for path in sorted((REPO_ROOT / "skills").glob("loopx-*/SKILL.md")):
        sections = path.read_text(encoding="utf-8").split("---", 2)
        assert len(sections) == 3 and not sections[0].strip(), path
        metadata = yaml.safe_load(sections[1])
        assert metadata["name"] == path.parent.name, path
        assert isinstance(metadata["description"], str) and metadata["description"].strip(), path


def test_packaged_loopx_skills_use_canonical_brand_display_names() -> None:
    skill_dirs = sorted((REPO_ROOT / "skills").glob("loopx-*"))
    assert skill_dirs

    for skill_dir in skill_dirs:
        metadata_path = skill_dir / "agents" / "openai.yaml"
        assert metadata_path.is_file(), f"{skill_dir.name} must declare an explicit display name"
        metadata = metadata_path.read_text(encoding="utf-8")
        match = re.search(r'^\s*display_name:\s*"([^"]+)"\s*$', metadata, re.MULTILINE)
        assert match, f"{metadata_path} must declare interface.display_name"
        display_name = match.group(1)
        assert display_name == "LoopX" or display_name.startswith("LoopX "), display_name
        assert not display_name.startswith("Loopx"), display_name


@pytest.mark.parametrize("skill_id", RELEASE_SKILL_IDS)
def test_release_scoped_skills_ship_source_and_scope_markers(skill_id):
    import tomllib

    package = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    data_files = package["tool"]["setuptools"]["data-files"]
    sources = data_files[f"share/loopx/skills/{skill_id}"]
    assert f"skills/{skill_id}/SKILL.md" in sources
    assert f"skills/{skill_id}/.loopx-skill-scope" in sources


@pytest.mark.parametrize("skill_id", RELEASE_SKILL_IDS)
def test_release_scoped_skill_display_metadata_is_in_distribution(skill_id):
    import tomllib

    package = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    data_files = package["tool"]["setuptools"]["data-files"]
    assert f"skills/{skill_id}/agents/openai.yaml" in data_files[
        f"share/loopx/skills/{skill_id}/agents"]
