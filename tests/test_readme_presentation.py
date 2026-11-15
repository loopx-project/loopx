from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TRENDING_BADGE = "https://trendshift.io/api/badge/repositories/102379"
TRENDING_SOURCE = "https://trendshift.io/repositories/102379"


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
