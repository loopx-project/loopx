from __future__ import annotations

import re
from pathlib import Path


CAPABILITIES = Path(__file__).resolve().parents[1] / "loopx" / "capabilities"
HEADINGS = {"README.md": "## Code Map", "README.zh-CN.md": "## 代码地图"}


def _mapped_modules(readme: Path) -> set[str] | None:
    text = readme.read_text(encoding="utf-8")
    heading = HEADINGS[readme.name]
    if heading not in text:
        return None
    section = text.split(heading, 1)[1].split("\n## ", 1)[0]
    return set(re.findall(r"^\| `([^`]+)` \|", section, re.MULTILINE))


def _package_modules(package: Path) -> set[str]:
    modules = {
        path.name
        for path in package.iterdir()
        if path.suffix in {".py", ".ts"} and path.name != "__init__.py"
    }
    modules |= {
        f"{path.name}/"
        for path in package.iterdir()
        if path.is_dir() and (path / "__init__.py").is_file()
    }
    return modules


def test_code_maps_list_every_module_and_mirror_the_same_set() -> None:
    mapped_packages = []
    for readme in sorted(CAPABILITIES.glob("*/README.md")):
        mapped = _mapped_modules(readme)
        if mapped is None:
            continue
        package = readme.parent
        mapped_packages.append(package.name)
        actual = _package_modules(package)
        assert mapped == actual, (
            f"{package.name} code map is missing {sorted(actual - mapped)} "
            f"and lists absent {sorted(mapped - actual)}"
        )
        mirror = readme.with_name("README.zh-CN.md")
        if mirror.is_file():
            assert _mapped_modules(mirror) == mapped, (
                f"{package.name} Chinese code map differs from the English one"
            )
    assert "decision_context" in mapped_packages
