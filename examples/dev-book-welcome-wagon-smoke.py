#!/usr/bin/env python3
"""Validate the bilingual Developer Book Welcome Wagon contract."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
BOOK = REPO_ROOT / "docs" / "book"
ZH_PAGE = BOOK / "welcome-wagon.md"
EN_PAGE = BOOK / "en" / "welcome-wagon.md"
ZH_CONFIG = BOOK / "mkdocs.zh.yaml"
EN_CONFIG = BOOK / "mkdocs.en.yaml"

SECTION_IDS = (
    "choose-finish-line",
    "inspect-first",
    "run-once",
    "share-feedback",
    "first-contribution",
    "review-path",
    "ask-for-help",
    "next-stop",
)

SEMANTIC_CHECKPOINTS = (
    "small-first-outcome",
    "four-routes",
    "inspect-before-write",
    "public-private-boundary",
    "run-first-goal",
    "verify-first-goal",
    "share-feedback",
    "route-community-channel",
    "find-current-work",
    "claim-bounded-slice",
    "deliver-clean-loop",
    "review-by-maturity",
    "ask-with-signal",
    "choose-next-depth",
)

SHARED_COMMANDS = (
    "loopx --version",
    "node --version",
    "loopx doctor",
    "git status --short --branch",
    "python3 -m pip install --upgrade loopx",
    "loopx workflow-skills --install",
    "loopx agent-onboard --list-agent-types",
    "loopx doctor --agent-type <agent-type>",
    "loopx connect --dry-run",
    "loopx connect",
    "loopx status",
    "loopx first-run-report",
)

SHARED_BOUNDARY_MARKERS = (
    "Python 3.11+",
    "Node.js 22.22.3+",
    ".loopx/",
    ".codex/goals/",
    ".local/",
    "Maintainer-owned",
    "DCO",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def markdown_link_targets(markdown: str) -> list[str]:
    return re.findall(r"(?<!!)\[[^]]+\]\(([^)]+)\)", markdown)


def explicit_section_ids(markdown: str) -> list[str]:
    return re.findall(
        r"^## .+ \{#([a-z0-9-]+)\}$",
        markdown,
        flags=re.MULTILINE,
    )


def semantic_checkpoints(markdown: str) -> list[str]:
    return re.findall(
        r"<!-- welcome-wagon:([a-z0-9-]+) -->",
        markdown,
    )


def rendered_route_for_absolute_link(site_root: Path, target: str) -> Path | None:
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/loopx/docs/"):
        return None
    relative = parsed.path.removeprefix("/loopx/docs/").strip("/")
    return site_root / relative / "index.html"


def validate_source() -> None:
    welcome_zh = read(ZH_PAGE)
    welcome_en = read(EN_PAGE)

    assert explicit_section_ids(welcome_zh) == list(SECTION_IDS)
    assert explicit_section_ids(welcome_en) == list(SECTION_IDS)
    assert semantic_checkpoints(welcome_zh) == list(SEMANTIC_CHECKPOINTS)
    assert semantic_checkpoints(welcome_en) == list(SEMANTIC_CHECKPOINTS)
    assert markdown_link_targets(welcome_zh) == markdown_link_targets(welcome_en)

    for command in SHARED_COMMANDS:
        assert command in welcome_zh, f"welcome-wagon.md: missing command {command}"
        assert command in welcome_en, f"en/welcome-wagon.md: missing command {command}"

    for marker in SHARED_BOUNDARY_MARKERS:
        assert marker in welcome_zh, f"welcome-wagon.md: missing semantic marker {marker}"
        assert marker in welcome_en, f"en/welcome-wagon.md: missing semantic marker {marker}"

    zh_config = read(ZH_CONFIG)
    en_config = read(EN_CONFIG)
    assert "Welcome Wagon: welcome-wagon.md" in zh_config
    assert "Welcome Wagon: welcome-wagon.md" in en_config


def validate_rendered_site(book_site_dir: Path) -> None:
    rendered_pages = {
        book_site_dir / "welcome-wagon" / "index.html": (
            "Welcome Wagon：30 分钟从读者到参与者",
            "跑通一次",
            "反馈一次",
            "贡献一次",
            "评审一次",
        ),
        book_site_dir / "en" / "welcome-wagon" / "index.html": (
            "Welcome Wagon: From Reader to Participant in 30 Minutes",
            "Run it once",
            "Share feedback",
            "Make a contribution",
            "Review something",
        ),
    }
    for path, markers in rendered_pages.items():
        assert path.is_file(), f"missing rendered Welcome Wagon page: {path}"
        html = read(path)
        for marker in markers:
            assert marker in html, f"{path}: missing rendered marker {marker}"
        for section_id in SECTION_IDS:
            assert f'id="{section_id}"' in html, (
                f"{path}: missing rendered semantic section id {section_id}"
            )

    docs_site_dir = book_site_dir.parent
    for source in (ZH_PAGE, EN_PAGE):
        for link in markdown_link_targets(read(source)):
            rendered_target = rendered_route_for_absolute_link(docs_site_dir, link)
            if rendered_target is not None:
                assert rendered_target.is_file(), (
                    f"{source.relative_to(BOOK)}: missing rendered cross-site route {link}"
                )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--site-dir",
        type=Path,
        help="Combined Pages artifact path ending in site/docs/book.",
    )
    args = parser.parse_args()

    validate_source()
    if args.site_dir is not None:
        validate_rendered_site(args.site_dir)

    print("dev-book-welcome-wagon-smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
