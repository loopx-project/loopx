"""Budget rules for the shared public-safety text surface.

``compact_text`` is the bound every projection spends before it hands a value to
a host, a dashboard or another tool, and 202 production call sites pass it an
explicit ``limit``. These cases pin what a caller reads back when a value does
not fit: how much survives, where the ellipsis goes, and that the refusal rules
still fire inside the kept prefix.
"""

import pytest

from loopx.control_plane.runtime.public_safety import (
    compact_text,
    public_safe_compact_text,
)

# A synthetic credential shape, never a real one: enough of the pattern to be
# refused and nothing else.
SYNTHETIC_CREDENTIAL = "tok" + "en=" + "abcdefghijklmn"


@pytest.mark.parametrize("limit", [2, 3, 5, 12, 40])
def test_a_value_over_budget_keeps_the_head_and_exactly_one_ellipsis(
    limit: int,
) -> None:
    text = "y" * 80

    compacted = compact_text(text, limit=limit)

    assert compacted == text[: limit - 1] + "…"
    assert len(compacted) == limit
    assert compacted.endswith("…")


def test_whitespace_is_collapsed_before_the_budget_is_spent() -> None:
    raw = "  padded   value  "

    assert len(raw) > 14
    assert compact_text(raw, limit=14) == "padded value"
    assert "…" not in compact_text(raw, limit=14)


def test_a_value_that_exactly_fits_is_returned_without_an_ellipsis() -> None:
    text = "abcdefghij"

    assert compact_text(text, limit=len(text)) == text
    assert len(compact_text(text, limit=len(text) - 1)) == len(text) - 1


def test_the_ellipsis_replaces_a_character_rather_than_adding_one() -> None:
    compacted = compact_text("alpha  beta\tgamma " + "x" * 40, limit=12)

    assert compacted == "alpha beta…"
    assert len(compacted) <= 12


def test_values_are_coerced_before_the_budget_and_the_empty_stays_empty() -> None:
    assert compact_text(123456, limit=3) == "12…"
    assert compact_text(None, limit=5) == ""
    assert public_safe_compact_text(None, limit=20) is None


def test_default_budget_is_two_hundred_twenty_characters() -> None:
    compacted = public_safe_compact_text("w" * 400)

    assert compacted is not None
    assert len(compacted) == 220
    assert compacted.endswith("…")


def test_a_credential_inside_the_kept_prefix_is_still_refused() -> None:
    assert (
        public_safe_compact_text(
            f"note {SYNTHETIC_CREDENTIAL}",
            limit=len(SYNTHETIC_CREDENTIAL) + 8,
        )
        is None
    )
    assert public_safe_compact_text("note ordinary prose", limit=40) == (
        "note ordinary prose"
    )


def test_http_link_with_path_shaped_parts_survives_compaction() -> None:
    value = "https://[2001:db8::1]/home/overview?next=/tmp/a"
    assert public_safe_compact_text(value, limit=200) == value
