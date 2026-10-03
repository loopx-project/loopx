import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


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


def test_packaged_scope_markers_ship_with_workflow_sources():
    import tomllib
    from loopx.skill_install_readback import PACKAGED_HOST_SKILL_IDS

    package = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    data_files = package["tool"]["setuptools"]["data-files"]
    for skill_id in PACKAGED_HOST_SKILL_IDS:
        assert f"skills/{skill_id}/.loopx-skill-scope" in data_files[
            f"share/loopx/skills/{skill_id}"
        ]


def test_packaged_skill_display_metadata_is_in_distribution():
    import tomllib
    from loopx.skill_install_readback import PACKAGED_HOST_SKILL_IDS

    package = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    data_files = package["tool"]["setuptools"]["data-files"]
    for skill_id in PACKAGED_HOST_SKILL_IDS:
        assert f"skills/{skill_id}/agents/openai.yaml" in data_files[
            f"share/loopx/skills/{skill_id}/agents"]
