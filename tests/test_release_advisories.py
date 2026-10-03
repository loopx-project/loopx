"""Release advisories must surface affected legacy runtimes in diagnosis."""

from __future__ import annotations

from loopx.doctor import render_doctor_markdown
from loopx.release_advisories import (
    build_release_advisories_section,
    release_advisories_for,
)


def test_affected_releases_receive_actionable_upgrade_guidance() -> None:
    for version in ("0.4.3", "0.4.5"):
        advisories = release_advisories_for(version)
        assert len(advisories) == 1, (version, advisories)
        advisory = advisories[0]
        assert advisory["id"] == "legacy_replan_prose_stall_matcher"
        assert advisory["last_affected_release"] == "0.4.5"
        assert advisory["fixed_in_release"] == "0.4.6"
        assert "installed" in advisory["summary"]
        assert "0.4.6" in advisory["guidance"]
        assert "loopx update check" in advisory["guidance"]
        assert "loopx update apply" in advisory["guidance"]


def test_fixed_releases_receive_no_advisory() -> None:
    for version in ("0.4.6", "1.0.3"):
        assert release_advisories_for(version) == ()


def test_doctor_section_reports_stale_runtime_for_affected_install() -> None:
    section = build_release_advisories_section("0.4.3")
    assert section["requires_upgrade"] is True
    rendered = render_doctor_markdown({"release_advisories": section})
    assert "## Release Advisory" in rendered
    assert "legacy_replan_prose_stall_matcher" in rendered
    assert "last_affected_release: `0.4.5`" in rendered
    assert "fixed_in_release: `0.4.6`" in rendered
    assert "loopx update apply" in rendered


def test_doctor_section_omits_advisory_for_current_runtime() -> None:
    section = build_release_advisories_section("1.0.3")
    assert section["requires_upgrade"] is False
    assert section["advisories"] == []
    rendered = render_doctor_markdown({"release_advisories": section})
    assert "## Release Advisory" not in rendered
