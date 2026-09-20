#!/usr/bin/env python3
"""Validate the deterministic README star-history generation path."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "render-star-history.py"
IMAGE_URL = "https://loopx-project.github.io/loopx/site-assets/star-history.svg"


def _fixture_count(fixture: object) -> int:
    if not isinstance(fixture, list):
        return 0
    return sum(len(item) if isinstance(item, list) else 1 for item in fixture)


def _render(
    fixture: object,
    output: Path,
    *,
    expected_count: int | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    fixture_path = output.with_suffix(".json")
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo",
            "loopx-project/loopx",
            "--input-json",
            str(fixture_path),
            "--expected-count",
            str(_fixture_count(fixture) if expected_count is None else expected_count),
            "--as-of",
            "2026-08-06",
            "--output",
            str(output),
        ],
        check=check,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-star-history-") as tmp:
        temp = Path(tmp)
        output = temp / "history.svg"
        _render(
            [
                [
                    {"starred_at": "2026-05-01T00:00:00Z"},
                    {"starred_at": "2026-05-01T12:00:00Z"},
                ],
                [
                    {"starred_at": "2026-07-15T08:30:00Z"},
                    {"starred_at": "2026-08-01T09:45:00Z"},
                ],
            ],
            output,
        )
        svg = output.read_text(encoding="utf-8")
        root = ET.fromstring(svg)
        assert root.tag.endswith("svg")
        assert root.attrib["viewBox"] == "0 0 800 500"
        assert 'role="img" aria-labelledby="title desc"' in svg
        assert "@media (prefers-color-scheme: dark)" in svg
        assert "4 stars · updated Aug 6, 2026" in svg
        assert '<text x="25" y="18" class="legend">loopx-project/loopx</text>' in svg
        assert "GitHub stars" in svg
        assert "<polygon" not in svg
        assert "<script" not in svg.lower()
        assert "http://" not in svg.replace("http://www.w3.org/2000/svg", "")
        assert "https://" not in svg

        empty_output = temp / "empty.svg"
        _render([], empty_output)
        empty_svg = empty_output.read_text(encoding="utf-8")
        assert "0 stars · updated Aug 6, 2026" in empty_svg

        mismatch = _render(
            [{"starred_at": "2026-08-01T09:45:00Z"}],
            temp / "incomplete.svg",
            expected_count=2,
            check=False,
        )
        assert mismatch.returncode != 0
        assert "snapshot is incomplete: expected 2, got 1" in mismatch.stderr

    readme_sections = {
        ROOT / "README.md": ("## Current Status", "## Star History", "## License"),
        ROOT / "README.zh-CN.md": ("## 当前状态", "## Star 趋势", "## License"),
    }
    for readme, sections in readme_sections.items():
        text = readme.read_text(encoding="utf-8")
        positions = [text.index(section) for section in sections]
        assert text.count(IMAGE_URL) == 1, readme
        assert "https://github.com/loopx-project/loopx/stargazers" in text, readme
        assert 'width="800"' in text[positions[1] : positions[2]], readme
        assert "star-history.dera.page" not in text, readme
        assert positions == sorted(positions), (readme, sections)
        assert "\n## " not in text[positions[-1] + len(sections[-1]) :], readme

    print("readme star history smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
