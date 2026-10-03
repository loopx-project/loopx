#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_cli(runtime_root: Path, *args: str) -> dict[str, object]:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--runtime-root",
            str(runtime_root),
            "--format",
            "json",
            *args,
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


with tempfile.TemporaryDirectory(prefix="loopx-extension-registry-") as raw_temp:
    runtime_root = Path(raw_temp) / "runtime"
    manifest = Path(raw_temp) / "extension.toml"
    manifest.write_text(
        """\
schema_version = "loopx_extension_manifest_v0"
id = "example-extension"
version = "1.0.0"
requires_loopx_api = ">=1,<2"
permissions = ["read_status"]

[[provides]]
id = "example-report"
kind = "projection_sink"
title = "Example report"
status = "active"
visibility = "public"
real_world_anchor = "public smoke fixture"
user_value = "Prove explicit extension composition."
entry_command = "example-extension report"
next_real_step = "Keep explicit enablement bounded."
""",
        encoding="utf-8",
    )

    baseline = run_cli(runtime_root, "capability", "list")
    builtin_capabilities = [
        item for item in baseline["capabilities"] if item["origin"] == "builtin"
    ]
    assert [item["id"] for item in builtin_capabilities] == [
        "benchmark-toolkit",
        "integration-branch-reconcile",
        "repository-change-window",
        "change-quality-qualification",
        "pull-request-review",
        "issue-fix",
        "decision-context",
        "project-skill-delivery",
        "material-lifecycle",
        "agent-turn-recall",
        "semantic-preference",
        "reward-memory",
        "periodic-report",
        "content-ops",
        "value-connectors",
        "explore",
        "deep-research",
        "public-safe-outbound",
        "connector-registry",
        "external-evidence-research",
        "performance-diagnosis",
        "reliability-diagnostics",
        "progress-review-sentinel",
        "goal-capability-organization",
    ]
    assert all(item["provider_id"] == "loopx-core" for item in builtin_capabilities)
    value_summary = next(
        item for item in baseline["capabilities"] if item["id"] == "value-connectors"
    )
    assert value_summary["status"] == "compatibility-facade", value_summary

    issue_fix = run_cli(runtime_root, "capability", "show", "issue-fix")["capability"]
    issue_fix_protocols = {
        item["schema_version"]: item
        for item in issue_fix["implemented_protocols"]
    }
    assert (
        issue_fix_protocols["github_public_channel_probe_packet_v0"]["module"]
        == "loopx.capabilities.issue_fix.github_public"
    ), issue_fix_protocols
    assert (
        issue_fix_protocols["github_public_reply_monitor_packet_v0"]["module"]
        == "loopx.capabilities.issue_fix.github_public"
    ), issue_fix_protocols

    value_connectors = run_cli(
        runtime_root, "capability", "show", "value-connectors"
    )["capability"]
    value_protocols = {
        item["schema_version"]: item
        for item in value_connectors["implemented_protocols"]
    }
    assert "github_public_channel_probe_packet_v0" not in value_protocols
    assert (
        value_protocols["value_connector_install_check_packet_v0"]["module"]
        == "loopx.capabilities.value_connectors.install_check"
    )
    github_commands = [
        item
        for item in value_connectors["commands"]
        if "github-" in item["command"]
    ]
    assert github_commands
    assert all(item["compatibility_for"] == "issue-fix" for item in github_commands)

    composed = run_cli(
        runtime_root,
        "capability",
        "list",
        "--extension-manifest",
        str(manifest),
    )
    assert composed["capabilities"][-1]["id"] == "example-report"
    assert composed["capabilities"][-1]["origin"] == "extension"
    assert composed["providers"][-1]["id"] == "example-extension"
    assert composed["providers"][-1]["declared"] is True
    assert composed["providers"][-1]["installed"] is False
    assert composed["providers"][-1]["enabled"] is False
    assert composed["providers"][-1]["ready"] is False

    detail = run_cli(
        runtime_root,
        "capability",
        "show",
        "example-report",
        "--extension-manifest",
        str(manifest),
    )
    assert detail["capability"]["capability_kind"] == "projection_sink"
    assert detail["capability"]["provider_id"] == "example-extension"
    assert detail["capability"]["provider_state"]["ready"] is False

    installed = run_cli(
        runtime_root,
        "extension",
        "install",
        "--bundled",
        "loopx-lark",
        "--execute",
    )
    assert installed["doctor"]["verified"] is True, installed
    lark = run_cli(
        runtime_root,
        "capability",
        "show",
        "lark-event-inbox",
    )
    assert lark["capability"]["origin"] == "extension", lark
    assert lark["capability"]["provider_id"] == "loopx-lark", lark
    assert lark["capability"]["provider_state"]["ready"] is True, lark

print("capability-extension-registry-smoke: ok")
