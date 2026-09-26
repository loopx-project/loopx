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
MUST_HAVE = (
    "registry 里的 `execution_profile`",
    "默认 cadence 是 `bounded_progress_segment`",
    "必须扩展到 `minimum_scale`",
    "包含 `must_include`",
    "如果返回 `state=operator_gate`",
    "把它当成人/控制器交互，而不是安静 skip",
    "`gate_prompt`、`operator_question`、`recommended_action`",
    "`next_handoff_condition`、`missing_gates`",
    "`user_todo_summary`",
    "不要执行任何 `agent_command`",
    "如果同一个未决 gate 最近已经问过，且返回 `safe_bypass_allowed=true`",
    "该 gate 只阻塞被 gate 覆盖的 delivery path",
    "选择一个不依赖该 gate 的 bounded 只读分析",
    "如果 payload 返回 `notify_user_on_open_todo=true`",
    "把开放 user todo 当作 blocker-push",
    "本轮不做 delivery、不 append quota spend",
    "无论 `should_run` 是 true 还是 false",
    "`execution_obligation`",
    "`effective_action`",
    "`recovery_delivery_allowed`、`safe_bypass_kind` 和 `heartbeat_recommendation`",
    "`heartbeat_recommendation.notify` 只是用户通知策略",
    "`execution_obligation.must_attempt_work=true`",
    "`must_attempt_work=false` 才能 quiet no-op",
    "如果是 `outcome_floor_recovery`",
    "ranker/cross-domain evidence recovery",
    "如果不是 operator gate / blocker-push / outcome-floor recovery / 明确 safe-bypass",
    "只有当返回 `should_run=true` 且 payload 里包含 `agent_command` 时",
    "才执行该命令。",
    "如果 `should_run=true` 但没有 `agent_command`",
    "`execution_obligation` / `recommended_action` / `goal_boundary`",
    "安全 bounded 动作",
    "只读目标保持只读",
    "delivery 目标按已授权 write scope 执行。",
    "如果你通过 read-only 分析、review doc、gate checklist 或 P0/P1 steering",
    "用户/owner 待办",
    "不要只写在 `Next Action`、外部 review 文档或聊天里。",
    "loopx todo add --goal-id",
    "--role user --task-class user_gate",
    "--role user --task-class user_action",
    "agent 自己的后续动作写成 `--role agent`",
    "docs/project-agent-todo-contract.md",
)
SPEND_MUST_HAVE = (
    "validation / writeback 完成后",
    "三个 placeholder",
    "`multi_surface` / `outcome_progress`",
    "accountable delivery",
    "普通 state-only refresh 不能替代它",
    "然后原样（不加管道或过滤）append 一次 quota spend",
    "不能重跑",
    'loopx --format json --registry "$HOME/.loopx/registry.global.json" quota spend-slot --goal-id',
    "--source adapter --execute",
    "在 spend 后仍需状态更新",
    "不要在 spend 后追加另一个 accountable progress refresh",
    "不要为 quiet `should_run=false` skip、preflight 失败、或纯 dry-run preview 记账",
    "实际完成了 `safe_bypass_allowed=true` 的 bounded safe-bypass 工作，要记一次账",
    "不要重复执行。",
)
HEARTBEAT_PROMPT_MUST_HAVE = (
    "如果要给这个项目设置 recurring Codex App heartbeat",
    "默认每 3 分钟一次",
    "loopx heartbeat-prompt",
    "--active-state .loopx/goals/",
    "再把输出复制进 automation",
)
HANDOFF_MUST_HAVE = (
    "如果需要把当前 packet 或已批准命令交给项目 agent",
    "优先生成最小 handoff",
    "不要从旧聊天",
    "旧 review packet",
    "`run_history.latest_runs`",
    "拼当前状态",
    "当前权威状态来自 `attention_queue.items` / `project_asset`",
    "如果缺少 `project_asset` 或标记为 `legacy/raw fallback`",
    "不要把 raw queue 字段当作 owner/gate/stop authority",
    "loopx review-packet --goal-id",
    "--handoff-only",
    "只把输出的 handoff 交给目标项目 agent",
    "完整 review packet 留给 operator view / evidence drill-down",
)


def assert_quota_guard(text: str) -> None:
    normalized = " ".join(text.split())
    assert 'export PATH="$HOME/.local/bin:$PATH"' in text, text
    assert 'install_script="$HOME/loopx/scripts/install-local.sh"' in text, text
    assert "loopx doctor >/dev/null" in text, text
    assert 'loopx --format json --registry "$HOME/.loopx/registry.global.json" quota should-run --goal-id' in text, text
    assert "--runtime-profile outer_controller" in normalized, text
    positions = []
    for phrase in MUST_HAVE:
        assert phrase in normalized, text
        positions.append(normalized.index(phrase))
    assert positions == sorted(positions), positions
    for phrase in SPEND_MUST_HAVE:
        assert phrase in normalized, text
    for phrase in HANDOFF_MUST_HAVE:
        assert phrase in normalized, text
    for phrase in HEARTBEAT_PROMPT_MUST_HAVE:
        assert phrase in normalized, text


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
    assert prompt.index(progress_refresh) < prompt.index(quota_spend), prompt
    assert prompt.index(quota_spend) < prompt.rindex(state_only_refresh), prompt
    assert "不要默认或拔高成 `multi_surface` / `outcome_progress`" in prompt, prompt
    assert_quota_guard(payload["prompt"])
    assert_quota_guard(DOC.read_text(encoding="utf-8"))

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
