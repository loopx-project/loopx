"""Fixtures for the CLI-visible Review Packet smoke."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.handoff_budget import PROJECT_AGENT_HANDOFF_BUDGET  # noqa: E402

STATUS_DATA_CONTRACT_PATH = REPO_ROOT / "docs" / "status-data-contract.md"
GOAL_ID = "planned-main-control"
APPROVED_COMMAND = f"loopx read-only-map --goal-id {GOAL_ID} --dry-run"
APPROVED_COMMAND_TAIL = f"read-only-map --goal-id {GOAL_ID} --dry-run"
FOCUS_WAIT_GOAL_ID = "focus-wait-owner-blocker"
LOCAL_ABSOLUTE_PATH_PATTERN = re.compile(
    r"(^|[\s`'\"=:(])(?:/[A-Za-z0-9._-]+(?:/[^\s`'\",)]+)+|[A-Za-z]:[\\/][^\s`'\",)]+)"
)
PROJECT_AGENT_HANDOFF_FORBIDDEN_MARKERS = (
    "【LoopX Review Packet】",
    "【人只需判断】",
    "【用户本地 Gate 记录草稿】",
    "问题：",
    "建议判断：",
    "回复：",
    "operator_gate_dry_run_command",
    "operator_gate_decision_commands",
    "latest_runs",
    "run_history",
)


def iter_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_strings(item)


def assert_no_local_paths(value: Any, label: str) -> None:
    for text in iter_strings(value):
        match = LOCAL_ABSOLUTE_PATH_PATTERN.search(text)
        assert match is None, f"{label} leaked local path {match.group(0)!r}: {text}"


def assert_project_agent_handoff_compact(text: str, label: str, *, goal_id: str) -> None:
    lines = text.splitlines()
    assert len(lines) <= PROJECT_AGENT_HANDOFF_BUDGET["max_lines"], (label, len(lines), text)
    assert len(text) <= PROJECT_AGENT_HANDOFF_BUDGET["max_chars"], (label, len(text), text)
    assert text.startswith(f"目标校验：本段只适用于 goal_id=`{goal_id}`"), (label, text)
    assert "上下文规则：本段只携带最小当前指令" in text, (label, text)
    assert "不要从旧聊天或旧 packet 拼当前状态" in text, (label, text)
    assert "项目资产来源：" in text, (label, text)
    assert "停止条件：" in text, (label, text)
    assert text.count("```bash") <= 1, (label, text)
    assert text.count("```") <= 2, (label, text)
    for marker in PROJECT_AGENT_HANDOFF_FORBIDDEN_MARKERS:
        assert marker not in text, (label, marker, text)


def assert_handoff_interface_budget(payload: dict[str, Any], label: str, *, text_key: str = "project_agent_handoff") -> None:
    text = str(payload.get(text_key) or "")
    budget = payload.get("handoff_interface_budget")
    assert isinstance(budget, dict), (label, payload)
    assert budget["mode"] == PROJECT_AGENT_HANDOFF_BUDGET["mode"], (label, budget)
    assert budget["max_lines"] == PROJECT_AGENT_HANDOFF_BUDGET["max_lines"], (label, budget)
    assert budget["max_chars"] == PROJECT_AGENT_HANDOFF_BUDGET["max_chars"], (label, budget)
    assert budget["line_count"] == len(text.splitlines()), (label, budget, text)
    assert budget["char_count"] == len(text), (label, budget, text)
    assert budget["within_line_budget"] is True, (label, budget)
    assert budget["within_char_budget"] is True, (label, budget)
    assert budget["within_budget"] is True, (label, budget)


def assert_handoff_only_top_level_budget(payload: dict[str, Any], label: str) -> None:
    budget = payload.get("handoff_interface_budget")
    assert isinstance(budget, dict), (label, payload)
    assert payload["line_count"] == budget["line_count"], (label, payload)
    assert payload["char_count"] == budget["char_count"], (label, payload)
    assert payload["within_budget"] == budget["within_budget"], (label, payload)


def assert_status_data_contract_documents_handoff_budget() -> None:
    contract = STATUS_DATA_CONTRACT_PATH.read_text(encoding="utf-8")
    compact_contract = " ".join(contract.split())
    assert "within 16 lines and 1800 characters" in compact_contract, contract
    assert "handoff_interface_budget" in compact_contract, contract
    assert "include at most one command block" in compact_contract, contract
    assert "optional delivery contract" in compact_contract, contract
    assert "mode=expand_after_repeated_small_delivery" in compact_contract, contract
    assert "handoff-only output must not carry the full Review Packet" in compact_contract, contract
    assert "raw `run_history`, or `latest_runs` cold-path evidence" in compact_contract, contract


def write_planned_registry(root: Path) -> Path:
    project = root / "project"
    runtime = root / "runtime"
    state_file = f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    registry_path = project / ".loopx" / "registry.json"
    (project / Path(state_file).parent).mkdir(parents=True, exist_ok=True)
    (project / state_file).write_text(
        "---\n"
        "status: planned-high-complexity\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Planned Main Control\n\n"
        "## User Todo\n\n"
        "- [ ] Read owner review worksheet first.\n\n"
        "## Agent Todo\n\n"
        "- [ ] Run the read-only map dry-run after owner todo resolution.\n",
        encoding="utf-8",
    )
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "complex-project",
                        "status": "planned-high-complexity",
                        "repo": str(project),
                        "state_file": state_file,
                        "authority_registry": {
                            "path": "docs/meta/DOC_REGISTRY.yaml",
                            "read_status": "read",
                            "default_entry_docs": [
                                "README.md",
                                "docs/GOAL.md",
                            ],
                            "topic_authority": {
                                "goal": "docs/GOAL.md",
                                "validation": "docs/VALIDATION.md",
                            },
                            "project_materials": {
                                "migration_design": {
                                    "role": "current_authority",
                                    "source_kind": "external_doc",
                                    "freshness": "owner_review_required",
                                },
                                "source_repo": {
                                    "role": "source_surface",
                                    "source_kind": "repository",
                                    "freshness": "read_only_status_ok",
                                },
                                "target_repo": {
                                    "role": "implementation_surface",
                                    "source_kind": "repository",
                                    "freshness": "read_only_status_ok",
                                },
                                "historical_note": {
                                    "role": "historical_reference",
                                    "source_kind": "external_doc",
                                    "freshness": "stale",
                                },
                            },
                            "deprecated_source_count": 0,
                            "conflict_risk": "low",
                        },
                        "adapter": {
                            "kind": "complex_project_read_only_map_v0",
                            "status": "planned",
                        },
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return registry_path


def approved_command_with_local_paths(root: Path) -> str:
    return (
        f"loopx --registry {root / 'project' / '.loopx' / 'registry.json'} "
        f"--runtime-root {root / 'runtime'} read-only-map --goal-id {GOAL_ID} --dry-run"
    )


def append_operator_gate_approval_fixture(root: Path, *, command: str | None = None) -> None:
    run_dir = root / "runtime" / "goals" / GOAL_ID / "runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    compact_time = generated_at.replace("-", "").replace(":", "")
    json_path = run_dir / f"{compact_time}-operator-gate.json"
    markdown_path = run_dir / f"{compact_time}-operator-gate.md"
    record = {
        "generated_at": generated_at,
        "goal_id": GOAL_ID,
        "classification": "operator_gate_approved",
        "recommended_action": "把已批准的 agent_command 发给目标项目 agent；这不是写权限授权",
        "health_check": "fixture operator_gate decision=approve; agent_command 1/1",
        "operator_gate": {
            "recorded_at": generated_at,
            "gate": "read_only_map_opt_in",
            "decision": "approve",
            "operator_question": f"是否同意 `{GOAL_ID}` 先执行 read-only map opt-in？",
            "reason_summary": f"同意 {GOAL_ID} 先做 read-only map dry-run，不授权写入或生产动作",
            "agent_command": command or approved_command_with_local_paths(root),
        },
    }
    json_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text("# Fixture operator gate approval\n", encoding="utf-8")
    with (run_dir / "index.jsonl").open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    **record,
                    "json_path": str(json_path),
                    "markdown_path": str(markdown_path),
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def mark_owner_review_todo_done(root: Path) -> None:
    state_path = root / "project" / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    text = state_path.read_text(encoding="utf-8")
    state_path.write_text(
        text.replace(
            "- [ ] Read owner review worksheet first.",
            "- [x] Read owner review worksheet first.",
        ),
        encoding="utf-8",
    )


def run_cli(root: Path, registry_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry_path),
            "--runtime-root",
            str(root / "runtime"),
            *args,
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )


def assert_order(text: str, labels: list[str]) -> None:
    positions = [text.index(label) for label in labels]
    assert positions == sorted(positions), (labels, positions, text)
