from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from loopx.control_plane.effect_runtime import MINIMUM_NODE_VERSION_TEXT


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
CONTRIBUTING = REPO_ROOT / "CONTRIBUTING.md"

# Every surface a user or contributor reads before the runtime check can fire.
NODE_REQUIREMENT_SURFACES = (
    "README.md",
    "README.zh-CN.md",
    "CONTRIBUTING.md",
    "pyproject.toml",
    "docs/guides/installing-loopx.md",
    "loopx/entrypoint.py",
)


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    following = re.search(r"^#{2,3} ", text[start + len(heading):], re.MULTILINE)
    end = start + len(heading) + following.start() if following else len(text)
    return text[start:end]


def test_node_minimum_is_stated_once_everywhere() -> None:
    for relative in NODE_REQUIREMENT_SURFACES:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        versions = set(re.findall(r"Node\.js (\d+\.\d+\.\d+)", text))
        assert versions == {MINIMUM_NODE_VERSION_TEXT}, (
            f"{relative} states Node.js {sorted(versions)}; "
            f"the runtime minimum is {MINIMUM_NODE_VERSION_TEXT}"
        )

    package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    assert package["engines"]["node"] == f">={MINIMUM_NODE_VERSION_TEXT}"

    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        pinned = set(
            re.findall(r'node-version: "(\d+\.\d+\.\d+)"', workflow.read_text(encoding="utf-8"))
        )
        assert pinned <= {MINIMUM_NODE_VERSION_TEXT}, (
            f"{workflow.name} pins Node.js {sorted(pinned)}; exact pins qualify the "
            f"minimum {MINIMUM_NODE_VERSION_TEXT}, other lanes use a major version"
        )


def test_contributing_ci_table_lists_every_workflow() -> None:
    section = _section(
        CONTRIBUTING.read_text(encoding="utf-8"), "### What CI runs on a pull request"
    )
    listed = set(re.findall(r"^\| `([^`]+\.yml)` \|", section, re.MULTILINE))
    present = {path.name for path in WORKFLOWS.glob("*.yml")}
    assert listed == present, (
        f"CONTRIBUTING.md CI table is missing {sorted(present - listed)} "
        f"and lists removed {sorted(listed - present)}"
    )


def test_contributing_names_every_merge_gate_dependency() -> None:
    section = _section(
        CONTRIBUTING.read_text(encoding="utf-8"), "### What CI runs on a pull request"
    )
    workflow = yaml.safe_load((WORKFLOWS / "python-tests.yml").read_text(encoding="utf-8"))
    needs = set(workflow["jobs"]["merge-gate"]["needs"]) - {"changes"}
    missing = sorted(job for job in needs if f"`{job}`" not in section)
    assert not missing, f"CONTRIBUTING.md does not name merge-gate jobs {missing}"
