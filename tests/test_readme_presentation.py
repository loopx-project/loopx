from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TRENDING_BADGE = "https://trendshift.io/api/badge/repositories/102379"
TRENDING_SOURCE = "https://trendshift.io/repositories/102379"
USER_ATTACHMENT_RE = re.compile(r"^https://github\.com/user-attachments/assets/[0-9a-f-]+$")


class LinkedImages(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.link: str | None = None
        self.images: list[tuple[str | None, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "a":
            self.link = attributes.get("href")
        elif tag == "img":
            self.images.append((self.link, attributes))

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self.link = None


@pytest.mark.parametrize("readme", ["README.md", "README.zh-CN.md", "README.ja.md"])
def test_trending_achievement_is_linked_once_in_readme_hero(readme: str) -> None:
    text = (REPO_ROOT / readme).read_text(encoding="utf-8")
    document = LinkedImages()
    document.feed(text)
    badges = [image for image in document.images if image[1].get("src") == TRENDING_BADGE]
    assert len(badges) == 1, "Keep one achievement badge, without a duplicate in the footer"

    hero = LinkedImages()
    hero.feed(text.split("</div>", 1)[0])
    assert badges[0] in hero.images, "The achievement must be visible in the opening hero"

    link, attributes = badges[0]
    assert link == TRENDING_SOURCE, "The badge must link to this repository's ranking evidence"
    alt = attributes.get("alt") or ""
    assert "#1" in alt and "GitHub Trending" in alt, "Keep the achievement readable without the image"


@pytest.mark.parametrize(
    ("readme", "quick_start_anchor"),
    [
        ("README.md", '<a id="quick-start"></a>'),
        ("README.zh-CN.md", '<a id="快速开始"></a>'),
    ],
)
def test_readme_hero_embeds_standalone_user_attachment_video_outside_html_div(
    readme: str,
    quick_start_anchor: str,
) -> None:
    text = (REPO_ROOT / readme).read_text(encoding="utf-8")
    hero_end = text.index("</div>")
    quick_start_start = text.index(quick_start_anchor)
    opening_band = text[hero_end + len("</div>") : quick_start_start]
    paragraphs = [p.strip() for p in opening_band.split("\n\n") if p.strip()]
    attachment_paragraphs = [p for p in paragraphs if USER_ATTACHMENT_RE.match(p)]
    assert len(attachment_paragraphs) == 1, (
        "GitHub renders bare user-attachment URLs into inline <video> players only when placed "
        "as a standalone paragraph outside the opening <div> container"
    )
    assert "<video" not in opening_band.lower()
    assert not USER_ATTACHMENT_RE.search(text[:hero_end]), (
        "Hero video attachment URL must not sit inside the HTML <div> block"
    )
