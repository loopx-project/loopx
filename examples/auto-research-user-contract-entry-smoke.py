#!/usr/bin/env python3
"""Smoke-test the one-question auto-research user contract entrypoint."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from argparse import Namespace
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from demo.auto_research.user_contract import (  # noqa: E402
    AUTO_RESEARCH_USER_CONTRACT_SCHEMA_VERSION,
    build_auto_research_user_contract,
    infer_auto_research_output_language,
)
from demo.auto_research.cli import (  # noqa: E402
    _default_auto_research_start_workspace,
)
from demo.auto_research.bootstrap_contract import (  # noqa: E402
    auto_research_start_command_text,
    build_auto_research_contract_acceptance,
)
from demo.multi_agent.visible_launch_policy import (  # noqa: E402
    resolve_codex_trust_workspace,
    resolve_visible_launch_policy,
)


QUESTION = "How should LoopX make visible multi-agent auto research useful?"


def assert_public_boundary(payload: dict[str, Any]) -> None:
    text = json.dumps(payload, sort_keys=True)
    forbidden = [
        "/" + "Users/",
        "/" + "private/",
        "/" + "tmp/",
        "lark" + "office",
        "byte" + "dance",
        "http" + "://",
        "https" + "://",
        "api" + "_key",
        "pass" + "word",
        "sec" + "ret",
    ]
    leaked = [needle for needle in forbidden if needle.lower() in text.lower()]
    assert not leaked, leaked


def assert_contract(payload: dict[str, Any]) -> None:
    assert payload["ok"] is True, payload
    assert payload["schema_version"] == AUTO_RESEARCH_USER_CONTRACT_SCHEMA_VERSION, payload
    assert payload["mode"] == "user_contract", payload
    assert payload["product_id"] == "auto-research", payload
    assert payload["open_question"] == QUESTION, payload
    assert payload["output_language"]["resolved"] in {"en", "zh"}, payload

    layering = payload["layering"]
    assert layering["user_layer"] == "one open question", layering
    assert layering["auto_research_layer"] == "fixed output contract only", layering
    assert "multi-agent runner" in layering["kernel_layer"], layering
    assert "pane-local tick" in layering["kernel_layer"], layering

    assert "minimal_a2a_recipe" not in payload, payload
    assert "thin_surface" not in payload, payload

    command_contract = payload["command_contract"]
    assert command_contract["canonical_invocation"] == 'loopx auto-research "<open question>"'
    assert (
        command_contract["one_click_start_invocation"]
        == 'loopx auto-research start "<open question>" --execute'
    )
    assert command_contract["user_required_inputs"] == ["open_question"], command_contract
    assert command_contract["user_optional_inputs"] == ["output_language", "preset"], command_contract
    assert command_contract["max_action_plan_todos"] == 5, command_contract
    assert command_contract["auto_research_required_outputs"] == [
        "research_brief",
        "action_plan",
        "evidence_refs",
        "next_executable_step",
        "gate",
    ], command_contract

    one_click_start = payload["one_click_start"]
    assert (
        one_click_start["command_template"]
        == 'loopx auto-research start "<open question>" --execute'
    ), one_click_start
    assert one_click_start["command"].startswith("loopx auto-research start "), one_click_start
    assert one_click_start["command"].endswith(" --execute"), one_click_start
    assert (
        one_click_start["operator_takeover_command_template"]
        == 'loopx auto-research start "<open question>" --execute --attach'
    ), one_click_start
    assert one_click_start["operator_takeover_command"].endswith(" --execute --attach")
    assert "operator takeover first" in one_click_start["attach_semantics"], one_click_start
    assert one_click_start["preview_command"].startswith("loopx auto-research start "), one_click_start
    assert one_click_start["starts"] == "visible_codex_tui_lanes", one_click_start
    assert one_click_start["uses_generic_kernel"] is True, one_click_start
    assert one_click_start["coordination_model"] == "decentralized_state_a2a", one_click_start
    assert "agent_ids" in one_click_start["user_does_not_choose"], one_click_start

    brief = payload["research_brief"]
    assert set(brief) == {"read", "not_read", "claim_boundary"}, brief
    assert brief["read"] == [], brief
    assert "source code" in brief["not_read"], brief
    assert "evidence ref" in brief["claim_boundary"], brief

    action_plan = payload["action_plan"]
    assert 1 <= len(action_plan) <= 5, action_plan
    assert [item["priority"] for item in action_plan][:2] == ["P0", "P0"], action_plan
    assert all(item["owner_layer"] != "user_layer" for item in action_plan), action_plan

    evidence = payload["evidence_refs"]
    assert set(evidence) == {"code", "docs", "benchmarks", "issues", "pull_requests"}, evidence
    assert all(value == [] for value in evidence.values()), evidence

    step = payload["next_executable_step"]
    assert step["can_run_automatically"] is True, step
    assert "read-only evidence discovery" in step["step"], step

    gate = payload["gate"]
    assert gate["user_judgment_needed"], gate
    assert "public/read-only discovery" in gate["default_without_user_gate"], gate

    boundary = payload["public_boundary"]
    assert boundary == {
        "raw_logs": False,
        "private_material": False,
        "credentials": False,
        "destructive_git": False,
        "production_actions": False,
    }, boundary
    assert_public_boundary(payload)


def run_cli(temp_dir: Path, *args: str) -> str:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(temp_dir / "registry.json"),
            *args,
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout


def assert_start_wake_contract() -> None:
    cli_source = (REPO_ROOT / "demo/auto_research/cli.py").read_text(
        encoding="utf-8"
    )
    assert "wake_visible_after_launch=bool(args.wake_visible_after_launch)" not in cli_source
    assert "def _start_wake_visible_after_launch" not in cli_source
    assert "def _start_attach_visible" not in cli_source
    assert "def _start_codex_trust_workspace" not in cli_source
    assert "wake_visible_multi_agent_panes" not in cli_source
    assert "resolve_visible_launch_policy" in cli_source
    assert "make_visible_launcher_callback" in cli_source
    assert "--auto-wake" in cli_source
    assert "auto_wake=bool(visible_policy.launch_visible and args.auto_wake)" in cli_source
    start_source = cli_source.split('elif args.auto_research_command == "start":', 1)[1].split(
        'elif args.auto_research_command == "demo-supervisor":',
        1,
    )[0]
    assert "if args.codex_trust_workspace is None" not in start_source
    demo_e2e_source = cli_source.split('elif args.auto_research_command == "demo-e2e":', 1)[1]
    assert "default=True" in cli_source.split('"--auto-wake"', 1)[1].split(
        '"--auto-wake-interval-seconds"',
        1,
    )[0]
    assert "auto_wake=bool(visible_policy.launch_visible and args.auto_wake)" in demo_e2e_source
    assert "auto_wake_interval_seconds=args.auto_wake_interval_seconds" in demo_e2e_source

    def start_policy(args: Namespace):
        return resolve_visible_launch_policy(
            args,
            launch_visible=bool(args.execute and not args.headless),
            default_wake_allowed=False,
            default_attach_allowed=bool(args.execute and not args.headless),
        )

    def trust(args: Namespace) -> bool:
        return resolve_codex_trust_workspace(
            args,
            launch_visible=bool(args.execute and not args.headless),
            default=True,
        )

    default_visible = Namespace(
        execute=True,
        headless=False,
        wake_visible_after_launch=None,
        attach=False,
        no_attach=False,
        codex_trust_workspace=None,
        auto_wake=True,
    )
    default_policy = start_policy(default_visible)
    assert default_policy.wake_visible_after_launch is False
    assert trust(default_visible) is True
    assert default_policy.attach is True

    attach_takeover = Namespace(
        execute=True,
        headless=False,
        wake_visible_after_launch=None,
        attach=True,
        no_attach=False,
        codex_trust_workspace=None,
        auto_wake=True,
    )
    takeover_policy = start_policy(attach_takeover)
    assert takeover_policy.wake_visible_after_launch is False
    assert trust(attach_takeover) is True
    assert takeover_policy.attach is True

    explicit_wake = Namespace(
        execute=True,
        headless=False,
        wake_visible_after_launch=True,
        attach=False,
        no_attach=False,
        codex_trust_workspace=None,
        auto_wake=True,
    )
    explicit_wake_policy = start_policy(explicit_wake)
    assert explicit_wake_policy.wake_visible_after_launch is True
    assert trust(explicit_wake) is True
    assert explicit_wake_policy.attach is False

    manual_takeover = Namespace(
        execute=True,
        headless=False,
        wake_visible_after_launch=False,
        attach=False,
        no_attach=False,
        codex_trust_workspace=None,
        auto_wake=True,
    )
    manual_policy = start_policy(manual_takeover)
    assert manual_policy.wake_visible_after_launch is False
    assert trust(manual_takeover) is True
    assert manual_policy.attach is True

    background_manual = Namespace(
        execute=True,
        headless=False,
        wake_visible_after_launch=False,
        attach=False,
        no_attach=True,
        codex_trust_workspace=None,
        auto_wake=False,
    )
    background_policy = start_policy(background_manual)
    assert background_policy.wake_visible_after_launch is False
    assert trust(background_manual) is True
    assert background_policy.attach is False

    headless = Namespace(
        execute=True,
        headless=True,
        wake_visible_after_launch=True,
        attach=False,
        no_attach=False,
        codex_trust_workspace=None,
        auto_wake=True,
    )
    headless_policy = start_policy(headless)
    assert headless_policy.wake_visible_after_launch is False
    assert trust(headless) is False
    assert headless_policy.attach is False

    explicit_no_trust = Namespace(
        execute=True,
        headless=False,
        wake_visible_after_launch=None,
        attach=False,
        no_attach=False,
        codex_trust_workspace=False,
        auto_wake=True,
    )
    assert trust(explicit_no_trust) is False

    explicit_trust = Namespace(
        execute=True,
        headless=False,
        wake_visible_after_launch=None,
        attach=False,
        no_attach=False,
        codex_trust_workspace=True,
        auto_wake=True,
    )
    assert trust(explicit_trust) is True


def main() -> None:
    payload = build_auto_research_user_contract(QUESTION)
    assert_contract(payload)
    assert infer_auto_research_output_language("如何评估 multi-agent auto research 是否有收益?") == "zh"
    zh_payload = build_auto_research_user_contract(
        "如何评估 multi-agent auto research 是否有收益?"
    )
    assert zh_payload["output_language"]["resolved"] == "zh", zh_payload
    assert " --language zh --execute" in zh_payload["one_click_start"]["command"], zh_payload
    assert (
        auto_research_start_command_text(
            cli_bin="loopx",
            objective="如何评估 multi-agent auto research 是否有收益?",
            execute=True,
            output_language="zh",
        )
        == "loopx auto-research start '如何评估 multi-agent auto research 是否有收益?' --language zh --execute"
    )
    assert build_auto_research_contract_acceptance(zh_payload)["accepted"] is True
    assert_start_wake_contract()
    default_workspace = Path(
        _default_auto_research_start_workspace("loopx-auto-research-demo-smoke")
    )
    assert default_workspace.parts[-3:] == (
        "loopx-auto-research",
        "loopx-auto-research-demo-smoke",
        "visible-workspace",
    ), default_workspace
    assert default_workspace.is_absolute(), default_workspace
    assert default_workspace == (
        Path.home() / "loopx-auto-research" / "loopx-auto-research-demo-smoke" / "visible-workspace"
    ), default_workspace

    with tempfile.TemporaryDirectory() as raw_temp_dir:
        temp_dir = Path(raw_temp_dir)
        shorthand_json = run_cli(temp_dir, "--format", "json", "auto-research", QUESTION)
        assert_contract(json.loads(shorthand_json))

        group_format_json = run_cli(temp_dir, "auto-research", "--format", "json", QUESTION)
        assert_contract(json.loads(group_format_json))

        explicit_json = run_cli(
            temp_dir,
            "--format",
            "json",
            "auto-research",
            "contract",
            QUESTION,
            "--max-todos",
            "3",
        )
        explicit_payload = json.loads(explicit_json)
        assert len(explicit_payload["action_plan"]) == 3, explicit_payload

        start_json = run_cli(
            temp_dir,
            "--format",
            "json",
            "auto-research",
            "start",
            QUESTION,
        )
        start_payload = json.loads(start_json)
        assert start_payload["ok"] is True, start_payload
        assert start_payload["mode"] == "dry_run", start_payload
        assert start_payload["execution_kind"] == "worker_loop_preview", start_payload
        assert start_payload["contract_acceptance"]["accepted"] is True, start_payload
        assert_contract(start_payload["user_contract"])
        assert (
            start_payload["commands"]["one_question_contract"]
            == f"loopx auto-research '{QUESTION}'"
        ), start_payload
        assert (
            start_payload["commands"]["one_question_start"]
            == f"loopx auto-research start '{QUESTION}' --execute"
        ), start_payload
        assert_public_boundary(start_payload)

        start_headless_json = run_cli(
            temp_dir,
            "--format",
            "json",
            "auto-research",
            "start",
            QUESTION,
            "--execute",
            "--headless",
        )
        start_headless_payload = json.loads(start_headless_json)
        assert start_headless_payload["ok"] is True, start_headless_payload
        assert start_headless_payload["execution_kind"] == "loopx_worker_loop", start_headless_payload
        assert start_headless_payload["contract_acceptance"]["accepted"] is True, start_headless_payload
        worker_loop = start_headless_payload["worker_loop"]
        assert worker_loop["turn_count"] >= 4, start_headless_payload
        assert worker_loop["executed_turn_count"] == 0, start_headless_payload
        assert worker_loop["completed_turn_count"] == 0, start_headless_payload
        assert any(
            turn.get("mode") == "manual_research_required" for turn in worker_loop["turns"]
        ), start_headless_payload
        assert start_headless_payload["tonight_experience"]["positive_result"] is False
        assert start_headless_payload["tonight_experience"]["ready"] is False
        assert start_headless_payload["tonight_experience"]["positive_result_basis"] == (
            "requires_visible_lane_authored_evidence"
        )
        assert start_headless_payload["tonight_experience"]["dev_metric"] is None
        assert start_headless_payload["tonight_experience"]["holdout_metric"] is None
        assert "auto-research start" in start_headless_payload["commands"]["one_question_start"], start_headless_payload
        assert_public_boundary(start_headless_payload)

        markdown = run_cli(temp_dir, "auto-research", QUESTION)
        assert "# LoopX Auto Research" in markdown, markdown
        for required in [
            "## Research Brief",
            "## Action Plan",
            "## Evidence Refs",
            "## One-Click Start",
            "## Next Executable Step",
            "## Gate",
        ]:
            assert required in markdown, markdown

        supervisor_json = run_cli(
            temp_dir,
            "--format",
            "json",
            "auto-research",
            "demo-supervisor",
            "--goal-id",
            "loopx-auto-research-contract-entry-smoke",
        )
        supervisor_payload = json.loads(supervisor_json)
        assert supervisor_payload["schema_version"] == "auto_research_demo_supervisor_plan_v0"

    print("auto-research-user-contract-entry-smoke ok")


if __name__ == "__main__":
    main()
