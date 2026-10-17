#!/usr/bin/env python3
"""Smoke-test the new-project handoff prompt quota guard."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.project_prompt import build_new_project_prompt  # noqa: E402


DOC = REPO_ROOT / "docs/operations/new-project-codex-prompt.md"
GOAL_ID = "new-project-main-control"
PROJECT = Path("/tmp/public-example-project")
GOAL_DOC = Path("/tmp/public-example-project/GOAL.md")
def assert_quota_guard(text: str) -> None:
    normalized = " ".join(text.split())
    for phrase in (
        "loopx doctor >/dev/null", "quota should-run", "--runtime-profile outer_controller",
        "interaction_contract", "execution_obligation", "goal_boundary",
        "user_channel.notify", "只控制通知", "must_attempt_work=true",
        "只有当前合同明确准入", "selection_command", "恢复前不执行 delivery、不记账",
        "guard 不扩大写权限", "精确 monitor settlement 不 refresh/spend",
        "settlement_plan.ordered_steps", "next_cli_actions", "精确 identity/effect id",
        "填写实际 classification、batch scale 和 outcome", "不从本接入提示拼接 refresh/spend",
        "不要盲目重试、重复记账", "验证、集成、负例和 readback", "明确的 host/Core stop",
        "--delivery-workspace-path", "user_gate --blocks-agent", "--role user --task-class user_action",
        "已有授权覆盖的动作不重复创建用户 gate", "--bootstrap", "--thin --codex-app",
        "不持久化本轮执行正文或 Turn identity", "当前 guard 已准入并选中 read-only map",
        "attention_queue.items", "project_asset", "--handoff-only",
        "当前合同未准入 delivery", "不能因没有 adapter run 推断免结算",
    ):
        assert phrase in normalized, phrase
    for stale in ("ranker/cross-domain", "如果不是 operator gate / blocker-push",
                  "quota spend-slot", "连续小步达到阈值"):
        assert stale not in text, stale
    assert normalized.index("quota should-run") < normalized.index("settlement_plan.ordered_steps")


def run_cli(*extra_args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", *extra_args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def cli_prompt_args() -> list[str]:
    return [
        "new-project-prompt",
        "--project",
        str(PROJECT),
        "--goal-doc",
        str(GOAL_DOC),
        "--goal-id",
        GOAL_ID,
        "--objective",
        "Public example objective",
        "--domain",
        "example",
    ]


def main() -> int:
    payload = build_new_project_prompt(
        project=PROJECT,
        goal_doc=GOAL_DOC,
        goal_id=GOAL_ID,
        objective="Public example objective",
        domain="example",
        adapter_kind="read_only_project_map_v0",
        adapter_status="connected-read-only",
        next_probe=None,
        spawn_allowed=False,
        allowed_domains=None,
        write_scope=None,
    )
    assert payload["quota_guard_command"] == (
        'loopx --format json --registry "$HOME/.loopx/registry.global.json" '
        "quota should-run --goal-id new-project-main-control "
        "--runtime-profile outer_controller"
    ), payload
    assert payload["quota_spend_command"] == (
        'loopx --format json --registry "$HOME/.loopx/registry.global.json" '
        "quota spend-slot --goal-id new-project-main-control --slots 1 --source adapter --execute"
    ), payload
    assert payload["progress_refresh_command"] == (
        "loopx refresh-state --goal-id new-project-main-control "
        "--classification <PUBLIC_SAFE_PROGRESS_CLASSIFICATION> "
        "--delivery-batch-scale <ACTUAL_DELIVERY_BATCH_SCALE> "
        "--delivery-outcome <ACTUAL_DELIVERY_OUTCOME>"
    ), payload
    prompt = str(payload["prompt"])
    progress_refresh = str(payload["progress_refresh_command"])
    quota_spend = str(payload["quota_spend_command"])
    state_only_refresh = str(payload["refresh_command"])
    assert progress_refresh not in prompt
    assert quota_spend not in prompt
    assert state_only_refresh in prompt
    assert_quota_guard(prompt)
    doc = DOC.read_text(encoding="utf-8")
    assert "loopx new-project-prompt" in doc
    assert "maintained prompt owner" in doc
    assert "settlement_plan.ordered_steps" in doc and "--bootstrap" in doc
    assert "quota spend-slot" not in doc

    cli_json = json.loads(run_cli("--format", "json", *cli_prompt_args()))
    assert cli_json["quota_guard_command"] == payload["quota_guard_command"], cli_json
    assert_quota_guard(cli_json["prompt"])

    cli_markdown = run_cli(*cli_prompt_args())
    assert "# New Project Codex Handoff Prompt" in cli_markdown, cli_markdown
    assert_quota_guard(cli_markdown)
    print("project-prompt-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
