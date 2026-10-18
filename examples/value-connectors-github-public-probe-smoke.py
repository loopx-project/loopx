#!/usr/bin/env python3
"""Smoke-test the value connector starter CLI."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.capabilities.issue_fix.github_public import (  # noqa: E402
    GITHUB_PUBLIC_CHANNEL_PROBE_PACKET_SCHEMA_VERSION,
    GITHUB_PUBLIC_REPLY_MONITOR_PACKET_SCHEMA_VERSION,
    github_public_read_failure_row,
)
from loopx.capabilities.value_connectors.install_check import (  # noqa: E402
    VALUE_CONNECTOR_INSTALL_CHECK_PACKET_SCHEMA_VERSION,
)
from loopx.capabilities.value_connectors.planner import (  # noqa: E402
    VALUE_CONNECTOR_PLAN_PACKET_SCHEMA_VERSION,
    build_single_value_connector_plan,
    validate_value_connector_plan,
)
from loopx.capabilities.value_connectors.source_map import (  # noqa: E402
    VALUE_CONNECTOR_SOURCE_MAP_PACKET_SCHEMA_VERSION,
)
from tests.control_plane.host_process_fixture import write_python_host_fixture  # noqa: E402


PRIVATE_PATTERNS = [
    re.compile(r"/Users/[A-Za-z0-9._-]+/"),
    re.compile(r"/home/[A-Za-z0-9._-]+/"),
    re.compile(r"/private/"),
    re.compile(r"[A-Za-z]:\\\\Users\\\\"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]+"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
]

FORBIDDEN_VALUES = [
    "raw provider payload",
    "comment body text",
    "comment body that must stay gated",
    "issue body text",
    "restricted-value",
    "sensitive-value",
]


def assert_public_safe(payload: dict[str, Any] | str) -> None:
    text = (
        payload
        if isinstance(payload, str)
        else json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )
    for pattern in PRIVATE_PATTERNS:
        if pattern.search(text):
            raise AssertionError(f"payload matched private pattern {pattern.pattern!r}")
    leaked = [value for value in FORBIDDEN_VALUES if value in text]
    assert not leaked, leaked


def run_cli(
    args: list[str],
    *,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "loopx.cli", *args],
        cwd=REPO_ROOT,
        check=check,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def main() -> int:
    assert (
        importlib.util.find_spec("loopx.capabilities.value_connectors.github_public")
        is None
    )
    install = json.loads(
        run_cli(["--format", "json", "value-connectors", "install-check"]).stdout
    )
    assert install["ok"] is True, install
    assert (
        install["schema_version"] == VALUE_CONNECTOR_INSTALL_CHECK_PACKET_SCHEMA_VERSION
    )
    connector_ids = {item["connector_id"] for item in install["checks"]}
    assert "github_public_channel" in connector_ids, connector_ids
    assert "agent_reach_ops_source_map" in connector_ids, connector_ids
    assert "finance_market_snapshot" in connector_ids, connector_ids
    assert "botmail_identity" in connector_ids, connector_ids
    assert "social_browser_x" in connector_ids, connector_ids
    assert_public_safe(install)

    source_map = json.loads(
        run_cli(["--format", "json", "value-connectors", "source-map"]).stdout
    )
    assert source_map["ok"] is True, source_map
    assert (
        source_map["schema_version"] == VALUE_CONNECTOR_SOURCE_MAP_PACKET_SCHEMA_VERSION
    )
    assert source_map["external_reads_performed"] is False, source_map
    assert source_map["external_writes_performed"] is False, source_map
    assert source_map["projection"]["agent_can_start_without_docs"] is True, source_map
    profile_ids = {item["connector_id"] for item in source_map["source_profiles"]}
    assert "github_public_channel" in profile_ids, profile_ids
    assert "github_public_reply_monitor" in profile_ids, profile_ids
    assert "content_ops_public_handle" in profile_ids, profile_ids
    assert "social_browser_x" in profile_ids, profile_ids
    assert "agent_reach_ops_source_map" in profile_ids, profile_ids
    assert "finance_market_snapshot" in profile_ids, profile_ids
    profiles = {item["connector_id"]: item for item in source_map["source_profiles"]}
    assert profiles["github_public_channel"]["outcome_capability_id"] == "issue-fix"
    assert (
        profiles["github_public_reply_monitor"]["provider_binding_state"] == "migrated"
    )
    assert (
        profiles["github_public_reply_monitor"]["provider_module"]
        == "loopx.capabilities.issue_fix.github_public"
    )
    assert profiles["content_ops_public_handle"]["provider_binding_state"] == "native"
    assert profiles["social_browser_x"]["outcome_capability_id"] == "content-ops"
    assert profiles["social_browser_x"]["provider_binding_state"] == "migrated"
    assert (
        profiles["social_browser_x"]["provider_module"]
        == "loopx.capabilities.content_ops.social_browser_x"
    )
    assert (
        profiles["agent_reach_ops_source_map"]["outcome_capability_id"] == "content-ops"
    )
    finance_migration = profiles["finance_market_snapshot"]
    assert finance_migration["outcome_capability_id"] is None
    assert finance_migration["provider_binding_state"] == "migrated_to_extension"
    assert finance_migration["migration"]["replacement_extension_id"] == (
        "loopx-finance-value-discovery"
    )
    assert finance_migration["migration"]["replacement_capability_id"] is None
    action_ids = {item["connector_id"] for item in source_map["action_gated_profiles"]}
    assert "botmail_identity" in action_ids, action_ids
    assert "community_channel" in action_ids, action_ids
    actions = {
        item["connector_id"]: item for item in source_map["action_gated_profiles"]
    }
    assert actions["botmail_identity"]["outcome_capability_id"] == "content-ops"
    assert actions["community_channel"]["outcome_capability_id"] == "content-ops"
    assert source_map["projection"]["compatibility_facade"] is True
    assert source_map["projection"]["new_profile_ownership_allowed"] is False
    assert source_map["projection"]["mapped_profile_count"] == 8
    assert source_map["projection"]["migrated_profile_count"] == 4
    assert source_map["generic_evidence_card_schema"]["operation"] == "read", source_map
    assert "loopx value-connectors plan" in source_map["agent_prompt"], source_map
    assert_public_safe(source_map)

    agent_reach_map = json.loads(
        run_cli(
            [
                "--format",
                "json",
                "value-connectors",
                "source-map",
                "--connector",
                "agent_reach_ops_source_map",
            ]
        ).stdout
    )
    assert agent_reach_map["ok"] is True, agent_reach_map
    assert len(agent_reach_map["source_profiles"]) == 1, agent_reach_map
    agent_reach_profile = agent_reach_map["source_profiles"][0]
    assert agent_reach_profile["connector_id"] == "agent_reach_ops_source_map", (
        agent_reach_profile
    )
    assert agent_reach_profile["external_reads_allowed"] is True, agent_reach_profile
    assert agent_reach_profile["external_writes_allowed"] is False, agent_reach_profile
    assert not agent_reach_map["action_gated_profiles"], agent_reach_map
    assert_public_safe(agent_reach_map)

    x_install = json.loads(
        run_cli(
            [
                "--format",
                "json",
                "value-connectors",
                "install-check",
                "--connector",
                "social_browser_x",
            ]
        ).stdout
    )
    assert x_install["ok"] is True, x_install
    assert (
        x_install["schema_version"]
        == VALUE_CONNECTOR_INSTALL_CHECK_PACKET_SCHEMA_VERSION
    )
    assert x_install["truth_contract"]["external_reads_performed"] is False, x_install
    assert x_install["truth_contract"]["external_writes_performed"] is False, x_install
    x_check = x_install["checks"][0]
    assert x_check["connector_id"] == "social_browser_x", x_check
    assert x_check["external_write_capability"] is True, x_check
    assert "exact account identity" in x_check["write_gate"], x_check
    assert any("ego-browser" in item for item in x_check["install"]), x_check
    assert_public_safe(x_install)

    probe = json.loads(
        run_cli(
            [
                "--format",
                "json",
                "value-connectors",
                "github-public-probe",
                "--url",
                "https://github.com/huangruiteng/loopx/issues/670",
            ]
        ).stdout
    )
    assert probe["ok"] is True, probe
    assert probe["schema_version"] == GITHUB_PUBLIC_CHANNEL_PROBE_PACKET_SCHEMA_VERSION
    assert probe["external_reads_performed"] is False, probe
    assert probe["external_writes_performed"] is False, probe
    assert probe["raw_body_captured"] is False, probe
    assert probe["comment_bodies_captured"] is False, probe
    assert probe["metadata"] is None, probe
    assert probe["connector_call"]["money_metric"], probe
    assert_public_safe(probe)

    reply_provider = {
        "comments": [
            {
                "author": "loopx-operator",
                "author_association": "NONE",
                "created_at": "2026-06-25T06:54:50Z",
                "updated_at": "2026-06-25T06:54:50Z",
                "url": "https://github.com/huangruiteng/loopx/issues/670#issuecomment-1",
                "body": "comment body that must stay gated",
                "raw": "raw provider payload",
            },
            {
                "author": "maintainer-one",
                "author_association": "MEMBER",
                "created_at": "2026-06-25T07:10:00Z",
                "updated_at": "2026-06-25T07:10:00Z",
                "url": "https://github.com/huangruiteng/loopx/issues/670#issuecomment-2",
                "body": "comment body text",
            },
        ]
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        reply_path = Path(tmpdir) / "reply-comments.json"
        reply_path.write_text(json.dumps(reply_provider), encoding="utf-8")
        reply_monitor = json.loads(
            run_cli(
                [
                    "--format",
                    "json",
                    "value-connectors",
                    "github-reply-monitor",
                    "--issue-url",
                    "https://github.com/huangruiteng/loopx/issues/670",
                    "--after-comment-url",
                    "https://github.com/huangruiteng/loopx/issues/670#issuecomment-1",
                    "--metadata-json",
                    str(reply_path),
                ]
            ).stdout
        )
    assert reply_monitor["ok"] is True, reply_monitor
    assert (
        reply_monitor["schema_version"]
        == GITHUB_PUBLIC_REPLY_MONITOR_PACKET_SCHEMA_VERSION
    )
    assert reply_monitor["external_reads_performed"] is False, reply_monitor
    assert reply_monitor["external_writes_performed"] is False, reply_monitor
    assert reply_monitor["comment_bodies_captured"] is False, reply_monitor
    assert reply_monitor["raw_provider_payload_captured"] is False, reply_monitor
    assert reply_monitor["maintainer_reply_count"] == 1, reply_monitor
    assert reply_monitor["money_signal"] == "public_maintainer_interest", reply_monitor
    assert reply_monitor["recommended_action"] == "prepare_public_triage_note", (
        reply_monitor
    )
    assert reply_monitor["validation"]["gated_provider_field_count"] == 3, reply_monitor
    assert_public_safe(reply_monitor)

    with tempfile.TemporaryDirectory() as tmpdir:
        fake_bin = Path(tmpdir)
        marker = fake_bin / "fixture-called"
        gh = write_python_host_fixture(
            fake_bin / "gh",
            "\n".join(
                [
                    "#!/usr/bin/env python3",
                    "import json",
                    "import sys",
                    "from pathlib import Path",
                    f"Path({str(marker)!r}).write_text('called', encoding='utf-8')",
                    "argv = sys.argv[1:]",
                    "assert argv[:2] == ['api', '-H'], argv",
                    "assert 'repos/huangruiteng/loopx/issues/670/comments?per_page=100' in argv, argv",
                    "assert '--jq' in argv, argv",
                    "json.dump([",
                    "  {",
                    "    'author': 'loopx-operator',",
                    "    'author_association': 'NONE',",
                    "    'created_at': '2026-06-25T06:54:50Z',",
                    "    'updated_at': '2026-06-25T06:54:50Z',",
                    "    'url': 'https://github.com/huangruiteng/loopx/issues/670#issuecomment-1',",
                    "  }",
                    "], sys.stdout)",
                    "sys.stdout.write('\\n')",
                ]
            ),
        )
        live_env = {**os.environ, "PATH": os.pathsep.join((str(fake_bin), os.environ.get("PATH", "")))}
        assert Path(shutil.which("gh", path=live_env["PATH"]) or "").resolve() == gh.resolve(), (
            "the smoke must resolve its fixture, never an installed GitHub CLI"
        )
        live_reply_monitor = json.loads(
            run_cli(
                [
                    "--format",
                    "json",
                    "value-connectors",
                    "github-reply-monitor",
                    "--issue-url",
                    "https://github.com/huangruiteng/loopx/issues/670",
                    "--after-comment-url",
                    "https://github.com/huangruiteng/loopx/issues/670#issuecomment-1",
                    "--fetch-metadata",
                ],
                env=live_env,
            ).stdout
        )
        assert marker.read_text(encoding="utf-8") == "called"
    assert live_reply_monitor["ok"] is True, live_reply_monitor
    assert live_reply_monitor["external_reads_performed"] is True, live_reply_monitor
    assert live_reply_monitor["external_writes_performed"] is False, live_reply_monitor
    assert live_reply_monitor["maintainer_reply_count"] == 0, live_reply_monitor
    assert live_reply_monitor["recommended_action"] == "wait_no_bump", (
        live_reply_monitor
    )
    assert live_reply_monitor["comment_bodies_captured"] is False, live_reply_monitor
    assert live_reply_monitor["validation"]["anchor_found"] is True, live_reply_monitor
    assert_public_safe(live_reply_monitor)

    plan = json.loads(run_cli(["--format", "json", "value-connectors", "plan"]).stdout)
    assert plan["ok"] is True, plan
    assert plan["schema_version"] == VALUE_CONNECTOR_PLAN_PACKET_SCHEMA_VERSION
    assert plan["external_writes_performed"] is False, plan
    assert plan["validation"]["ok"] is True, plan["validation"]
    assert plan["projection"]["first_screen"]["gated_call_count"] == 4, plan
    assert plan["projection"]["first_screen"]["safe_prepare_call_count"] == 4, plan
    assert "github_issue_intake" in plan["projection"]["safe_prepare_calls"], plan
    assert "x_public_signal_scan" in plan["projection"]["safe_prepare_calls"], plan
    assert "x_launch_post_gate" in plan["projection"]["gated_calls"], plan
    calls = {item["call_id"]: item for item in plan["plan"]["connector_calls"]}
    x_launch = calls["x_launch_post_gate"]
    assert x_launch["connector_id"] == "social_browser_x", x_launch
    assert x_launch["connector_kind"] == "browser_social_channel", x_launch
    assert x_launch["external_write_requested"] is True, x_launch
    assert x_launch["external_writes_allowed"] is False, x_launch
    assert x_launch["requires_user_approval"] is True, x_launch
    assert x_launch["approval_gate_id"] == "gate_x_exact_publish", x_launch
    assert "spam" in x_launch["kill_condition"], x_launch
    assert_public_safe(plan)

    x_gated = json.loads(
        run_cli(
            [
                "--format",
                "json",
                "value-connectors",
                "plan",
                "--connector-id",
                "social_browser_x",
                "--connector-kind",
                "browser_social_channel",
                "--channel",
                "X public post via ego-browser",
                "--stage",
                "external_write_request",
                "--target-ref",
                "one approved LoopX post",
                "--target-url",
                "https://x.com/loopxops",
                "--external-write-requested",
                "--money-metric",
                "qualified workflow owner asks for LoopX setup help",
                "--success-metric",
                "one audit, demo, or setup request",
                "--kill-condition",
                "spam hiding, account-health degradation, or no workflow owner signal",
            ]
        ).stdout
    )
    assert x_gated["ok"] is True, x_gated
    x_call = x_gated["plan"]["connector_calls"][0]
    assert x_call["connector_id"] == "social_browser_x", x_call
    assert x_call["external_write_requested"] is True, x_call
    assert x_call["external_writes_allowed"] is False, x_call
    assert x_call["requires_user_approval"] is True, x_call
    assert x_gated["projection"]["first_screen"]["waiting_on"] == "user", x_gated
    assert_public_safe(x_gated)

    x_account_setup = json.loads(
        run_cli(
            [
                "--format",
                "json",
                "value-connectors",
                "plan",
                "--connector-id",
                "social_browser_x",
                "--connector-kind",
                "browser_social_channel",
                "--channel",
                "X account profile via ego-browser",
                "--stage",
                "account_setup",
                "--target-ref",
                "LoopX-only public account identity",
                "--target-url",
                "https://x.com/loopxops",
                "--money-metric",
                "public product channel ready to receive qualified workflow-owner replies",
                "--success-metric",
                "profile identity is public-safe and ready for approved posts",
                "--kill-condition",
                "requires payment, captcha bypass, non-LoopX brand claims, or private context",
            ]
        ).stdout
    )
    assert x_account_setup["ok"] is True, x_account_setup
    x_setup_call = x_account_setup["plan"]["connector_calls"][0]
    assert x_setup_call["stage"] == "account_setup", x_setup_call
    assert x_setup_call["access_mode"] == "agent_owned_identity", x_setup_call
    assert x_setup_call["requires_user_approval"] is True, x_setup_call
    assert x_account_setup["projection"]["first_screen"]["waiting_on"] == "user", (
        x_account_setup
    )
    assert_public_safe(x_account_setup)

    invalid_account_setup = build_single_value_connector_plan(
        connector_id="social_browser_x",
        connector_kind="browser_social_channel",
        channel="X account profile via ego-browser",
        stage="account_setup",
        target_ref="LoopX-only public account identity",
        target_url="https://x.com/loopxops",
        value_axis="revenue",
        money_metric="public product channel ready to receive qualified workflow-owner replies",
        success_metric="profile identity is public-safe and ready for approved posts",
        kill_condition="requires payment, captcha bypass, non-LoopX brand claims, or private context",
        generated_at="2026-06-25T00:00:00Z",
    )
    invalid_account_setup["connector_calls"][0]["access_mode"] = "public_metadata_only"
    invalid_validation = validate_value_connector_plan(invalid_account_setup)
    assert invalid_validation["ok"] is False, invalid_validation
    assert any(
        "account_setup" in error and "public_metadata_only" in error
        for error in invalid_validation["errors"]
    ), invalid_validation

    gated = json.loads(
        run_cli(
            [
                "--format",
                "json",
                "value-connectors",
                "plan",
                "--connector-id",
                "community_channel",
                "--connector-kind",
                "community_channel",
                "--channel",
                "public community thread",
                "--stage",
                "external_write_request",
                "--target-ref",
                "thread asking about agent workflow operations",
                "--external-write-requested",
                "--money-metric",
                "qualified workflow owner asks for a LoopX audit",
                "--success-metric",
                "one audit or demo request",
                "--kill-condition",
                "channel rules reject the reply or no workflow owner appears",
            ]
        ).stdout
    )
    assert gated["ok"] is True, gated
    call = gated["plan"]["connector_calls"][0]
    assert call["external_write_requested"] is True, call
    assert call["external_writes_allowed"] is False, call
    assert call["requires_user_approval"] is True, call
    assert gated["projection"]["first_screen"]["waiting_on"] == "user", gated
    assert_public_safe(gated)

    rejected = run_cli(
        [
            "--format",
            "json",
            "value-connectors",
            "github-public-probe",
            "--url",
            "https://github.com/owner/repo/issues/1?x=sensitive-value",
        ],
        check=False,
    )
    assert rejected.returncode == 1, rejected
    rejected_payload = json.loads(rejected.stdout)
    assert rejected_payload["ok"] is False, rejected_payload
    assert (
        rejected_payload["schema_version"] == "github_public_channel_probe_error_v0"
    ), rejected_payload
    assert "query or fragment" in rejected_payload["error"], rejected_payload

    rejected_reply = run_cli(
        [
            "--format",
            "json",
            "value-connectors",
            "github-reply-monitor",
            "--issue-url",
            "https://github.com/owner/repo/issues/1",
            "--after-comment-url",
            "https://github.com/owner/repo/issues/1#issuecomment-1?x=sensitive-value",
        ],
        check=False,
    )
    assert rejected_reply.returncode == 1, rejected_reply
    rejected_reply_payload = json.loads(rejected_reply.stdout)
    assert rejected_reply_payload["ok"] is False, rejected_reply_payload
    assert (
        rejected_reply_payload["schema_version"]
        == "github_public_reply_monitor_error_v0"
    )

    rejected_markdown = run_cli(
        [
            "value-connectors",
            "github-public-probe",
            "--url",
            "https://github.com/owner/repo/issues/1?x=sensitive-value",
        ],
        check=False,
    )
    assert rejected_markdown.returncode == 1, rejected_markdown
    assert "LoopX GitHub Public Channel Probe" in rejected_markdown.stdout, (
        rejected_markdown.stdout
    )
    assert "LoopX Value Connector Plan" not in rejected_markdown.stdout, (
        rejected_markdown.stdout
    )

    markdown = run_cli(
        [
            "value-connectors",
            "github-public-probe",
            "--url",
            "https://github.com/huangruiteng/loopx/issues/670",
        ]
    ).stdout
    assert "LoopX GitHub Public Channel Probe" in markdown, markdown
    assert "external_writes_performed: `False`" in markdown, markdown
    assert_public_safe(markdown)

    source_map_markdown = run_cli(["value-connectors", "source-map"]).stdout
    assert "LoopX Value Connector Source Map" in source_map_markdown, (
        source_map_markdown
    )
    assert "agent_can_start_without_docs: `True`" in source_map_markdown, (
        source_map_markdown
    )
    assert "`github_public_channel`" in source_map_markdown, source_map_markdown
    assert_public_safe(source_map_markdown)

    reply_markdown = run_cli(
        [
            "value-connectors",
            "github-reply-monitor",
            "--issue-url",
            "https://github.com/huangruiteng/loopx/issues/670",
            "--after-comment-url",
            "https://github.com/huangruiteng/loopx/issues/670#issuecomment-1",
        ]
    ).stdout
    assert "LoopX GitHub Public Reply Monitor" in reply_markdown, reply_markdown
    assert "recommended_action: `wait_no_bump`" in reply_markdown, reply_markdown
    assert_public_safe(reply_markdown)

    # A failed public read must surface as a typed row instead of the provider's
    # own text: steward answers used to quote "Cache miss" and
    # "invalid_arguments" instead of naming what was unread and how to repair
    # it. The provider message is replaced by its digest.
    failure = github_public_read_failure_row(RuntimeError("gh request failed: Cache miss"))
    assert failure["code"] == "provider_tool_unavailable", failure
    assert failure["source_id"] == "github_public_channel", failure
    assert "unread" in failure["coverage_effect"], failure
    assert failure["next_action"], failure
    assert failure["provider_message_digest"].startswith("sha256:"), failure
    assert "Cache miss" not in json.dumps(failure), failure
    assert_public_safe(failure)
    assert github_public_read_failure_row(subprocess.TimeoutExpired("gh", 5))[
        "code"
    ] == "provider_timeout"
    assert github_public_read_failure_row(
        URLError("nodename nor servname provided")
    )["code"] == "provider_network_unavailable"
    http_failure = github_public_read_failure_row(
        HTTPError("https://api.github.com/repos/o/r/issues/1", 403, "Forbidden", None, None)
    )
    assert http_failure["code"] == "provider_response_rejected", http_failure
    assert http_failure["provider_status"] == 403, http_failure

    print("value-connectors-github-public-probe-smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
