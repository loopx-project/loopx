#!/usr/bin/env python3
"""Guard adaptive steward answers and the installed instruction refresh."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.capabilities.manager_context.answer_contract import (  # noqa: E402
    MANAGER_ANSWER_CONTRACT_SCHEMA_VERSION,
    classify_manager_answer_shape,
    manager_answer_contract_instruction,
)
from loopx.chat_agent import _turn_prompt  # noqa: E402
from loopx.chat_manager import (  # noqa: E402
    MANAGED_SKILL_CURRENT_MARKER,
    MANAGED_SKILL_MARKER_V2,
    MANAGER_AGENT_OBJECTIVE,
    MANAGER_CONTEXT_VERSION,
    manager_answer_readback,
    manager_skill_text,
    manager_workspace,
)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def repeated_segments(text: str) -> list[str]:
    segments = [
        segment.strip() for segment in re.split(r"(?<=[.;])\s+", text) if segment.strip()
    ]
    counts = Counter(segments)
    return sorted(segment for segment, count in counts.items() if count > 1)


def main() -> int:
    instruction = manager_answer_contract_instruction()
    skill = manager_skill_text()
    check("A simple question needs no report template." in instruction
          and "Preserve requested substantive detail" in instruction,
          "the shared instruction must distinguish short and substantive answers")
    check("depth proportionate to the work" in instruction
          and "do not replace a complete answer" in instruction,
          "the new instruction must not recreate the four-section obligation")
    check(instruction in MANAGER_AGENT_OBJECTIVE,
          "the steward objective must use the contract owner")
    check("concise Chinese" not in MANAGER_AGENT_OBJECTIVE,
          "the old brevity instruction must not suppress a substantive answer")
    check("A short factual question" in skill and "An investigation or decision" in skill,
          "the installed skill must carry the same adaptive policy")
    check(MANAGED_SKILL_CURRENT_MARKER in skill,
          "the installed skill must carry the current marker")
    check("safe Markdown text" in _turn_prompt("question")
          and "complete answer must stay in this conversation" in _turn_prompt("question"),
          "the shared Codex/DSH/direct-model Turn prompt must preserve a full Markdown answer")
    for surface, text in (("objective", MANAGER_AGENT_OBJECTIVE), ("skill", skill)):
        check(not repeated_segments(text), f"{surface} must not duplicate whole sentences")

    with TemporaryDirectory() as root:
        workspace = manager_workspace(Path(root))
        skill_path = workspace / ".agents/skills/loopx-manager/SKILL.md"
        check(skill_path.exists(), "the manager workspace must install its skill")
        skill_path.write_text(f"{MANAGED_SKILL_MARKER_V2}\n\n# stale manager skill\n", encoding="utf-8")
        manager_workspace(Path(root))
        check(skill_path.read_text(encoding="utf-8") == skill,
              "a workspace holding the previous managed skill must refresh")
        instructions = (workspace / "AGENTS.md").read_text(encoding="utf-8")
        check(instruction in instructions,
              "an installed manager workspace must carry the adaptive instruction")
        check(MANAGER_CONTEXT_VERSION >= 16,
              "the changed answer contract must invalidate existing manager context")

    short = "可以，见[来源](https://example.org/source)。"
    detailed = (
        "建议先验证方案 A，再决定是否采用。\n\n"
        "## 比较\n\n| 方案 | 证据 |\n|---|---|\n| A | 已验收 |\n"
        "\n- 当前证据：[记录](https://example.org/report)\n"
        "- 待确认：外部部署状态尚未读回。"
    )
    short_readback = manager_answer_readback({"message": short}, channel="manager")
    long_readback = manager_answer_readback({"message": detailed}, channel="manager")
    check(short_readback["message"] == short and short_readback["answer_shape"]["state"] == "present",
          "a direct answer must be retained and never called incomplete for lacking headings")
    check(long_readback["message"] == detailed
          and long_readback["answer_shape"]["schema_version"] == MANAGER_ANSWER_CONTRACT_SCHEMA_VERSION
          and long_readback["answer_shape"]["state"] == "present"
          and long_readback["answer_shape"]["character_count"] == len(detailed),
          "a multi-block Markdown answer must remain complete with format and length readback")
    check("required_order" not in classify_manager_answer_shape(detailed),
          "presentation readback must not call a missing ceremonial section a failure")
    check("answer_shape" not in manager_answer_readback({"message": detailed}, channel="manager.external.fixture"),
          "an external audience keeps its own transcript and is not measured by owner readback")
    check("answer_shape" not in manager_answer_readback({"message": ""}, channel="manager"),
          "an empty answer has no presentation readback")

    print("manager-answer-contract-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
