#!/usr/bin/env python3
"""Smoke-test the read-only goal channel frontstage fixture."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "examples" / "goal-channel-frontstage-fixture.py"


def load_fixture_module():
    spec = importlib.util.spec_from_file_location("goal_channel_frontstage_fixture", FIXTURE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fixture_projection_and_html_are_read_only() -> None:
    fixture = load_fixture_module()
    projection = fixture.build_sample_projection()
    projection["user_todos"][0]["bound_agent"] = "codex-main-control"
    html = fixture.render_goal_channel_projection_html(projection)

    assert projection["schema_version"] == "goal_channel_projection_v0", projection
    assert projection["mode"] == "read_only", projection
    assert projection["truth_contract"]["projection_is_writable"] is False, projection
    assert projection["truth_contract"]["event_ledger_is_source_of_truth"] is True, projection
    assert projection["decision_frame"]["user_action_required"] is True, projection
    assert projection["decision_frame"]["agent_action_required"] is True, projection
    assert projection["agent_todos"][1]["claimed_by"] == "codex-side-bypass", projection
    # The renderer fixture carries display metadata without a canonical provider.
    assert projection["active_leases"] == [], projection
    assert projection["coordination_observation"]["status"] == "unavailable", projection
    assert projection["coordination_authority"] == {"state": "unavailable"}, projection
    assert projection["truth_contract"]["write_authority"] == "none", projection

    assert 'data-schema="goal_channel_projection_v0"' in html, html
    assert 'data-mode="read_only"' in html, html
    for panel in (
        "decision-frame",
        "quota",
        "user-todos",
        "agent-todos",
        "open-gates",
        "active-leases",
        "artifacts",
        "recent-events",
        "source-warnings",
        "truth-contract",
    ):
        assert f'data-panel="{panel}"' in html, panel

    user_panel = html.split('data-panel="user-todos"', 1)[1].split("</section>", 1)[0]
    agent_panel = html.split('data-panel="agent-todos"', 1)[1].split("</section>", 1)[0]
    assert "Bound Agent" in user_panel, user_panel
    assert "Bound Agent" not in agent_panel, agent_panel

    forbidden_controls = ("<button", "<form", "method=", "onclick=", "javascript:")
    lowered = html.lower()
    assert not [token for token in forbidden_controls if token in lowered], html


def test_raw_private_values_are_omitted_from_fixture() -> None:
    fixture = load_fixture_module()
    projection = fixture.build_sample_projection()
    html = fixture.render_goal_channel_projection_html(projection)
    projection_text = json.dumps(projection, sort_keys=True)

    assert projection["source_warnings"], projection
    assert projection["source_warnings"][0]["kind"] == "raw_or_private_material_omitted", projection

    forbidden_values = (
        fixture.REDACTED_LOCAL_PATH,
        fixture.REDACTED_RAW_NOTE,
    )
    leaked = [
        value
        for value in forbidden_values
        if value in projection_text or value in html
    ]
    assert not leaked, leaked


def test_renderer_rejects_writeable_projection() -> None:
    fixture = load_fixture_module()
    projection = fixture.build_sample_projection()
    projection["mode"] = "writeable"
    try:
        fixture.render_goal_channel_projection_html(projection)
    except ValueError as exc:
        assert "read_only" in str(exc)
    else:
        raise AssertionError("renderer accepted a writeable projection")


def main() -> int:
    test_fixture_projection_and_html_are_read_only()
    test_raw_private_values_are_omitted_from_fixture()
    test_renderer_rejects_writeable_projection()
    print("goal-channel-frontstage-fixture-smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
