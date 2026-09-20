#!/usr/bin/env python3
"""Guard the steward answer contract: one order, one owner, one readback.

The contract is data (`manager_context.answer_contract`). This smoke proves the
instruction the manager receives is derived from that data rather than written
twice, that an installed manager workspace is refreshed when the contract
changes, and that a rendered answer is classified structurally instead of by
guessing at wording.
"""

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
    MANAGER_ANSWER_FOOTER_SECTION_ID,
    classify_manager_answer_shape,
    manager_answer_contract_instruction,
    manager_answer_labels,
    manager_answer_section_ids,
)
from loopx.chat_manager import (  # noqa: E402
    MANAGED_SKILL_CURRENT_MARKER,
    MANAGED_SKILL_MARKER_V1,
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
    """Segments one instruction surface carries verbatim more than once.

    A repeated sentence is read by the manager on every turn, so it costs
    tokens each time and makes the next edit ambiguous about which copy the
    contract owns. The check is structural: it compares whole sentence-sized
    segments rather than words that legitimately recur.
    """

    segments = [
        segment.strip() for segment in re.split(r"(?<=[.;])\s+", text) if segment.strip()
    ]
    counts = Counter(segments)
    return sorted(segment for segment, count in counts.items() if count > 1)


def assert_labels_in_order(text: str, where: str) -> None:
    labels = manager_answer_labels()
    positions = [text.find(label) for label in labels]
    check(
        all(position >= 0 for position in positions),
        f"{where} must carry every contract label ({dict(zip(labels, positions))})",
    )
    check(
        positions == sorted(positions),
        f"{where} must carry the contract labels in order ({dict(zip(labels, positions))})",
    )


def main() -> int:
    check(
        manager_answer_section_ids() == ("conclusion", "milestones", "evidence", "gaps"),
        f"the contract order is the answer shape ({manager_answer_section_ids()})",
    )
    check(
        manager_answer_labels() == ("结论", "里程碑/基线", "依据", "缺口"),
        f"the contract labels are the owner-facing parts ({manager_answer_labels()})",
    )
    check(
        MANAGER_ANSWER_FOOTER_SECTION_ID == "gaps",
        "coverage disclaimers belong to the last section, not the answer body",
    )
    instruction = manager_answer_contract_instruction()
    check(
        instruction.startswith("Lead every answer with this contract order"),
        f"the rendered instruction states the order it enforces ({instruction[:60]!r})",
    )

    # The objective the manager receives is the contract's own text, so the
    # instruction cannot drift away from the type that owns it.
    check(
        instruction in MANAGER_AGENT_OBJECTIVE,
        "MANAGER_AGENT_OBJECTIVE must carry the rendered contract instruction",
    )
    assert_labels_in_order(MANAGER_AGENT_OBJECTIVE, "MANAGER_AGENT_OBJECTIVE")

    skill = manager_skill_text()
    check(
        MANAGED_SKILL_CURRENT_MARKER in skill,
        f"the managed skill must carry its current marker ({MANAGED_SKILL_CURRENT_MARKER})",
    )
    assert_labels_in_order(skill, "the managed loopx-manager skill")

    # Both instruction surfaces reach the manager on every turn, so neither may
    # carry a sentence twice: the copy costs tokens and the next edit would not
    # know which one the contract owns. The objective shipped with one repeated
    # sentence until this guard was added.
    for surface, text in (
        ("MANAGER_AGENT_OBJECTIVE", MANAGER_AGENT_OBJECTIVE),
        ("the managed loopx-manager skill", skill),
    ):
        repeated = repeated_segments(text)
        check(
            not repeated,
            f"{surface} must not repeat a sentence ({[item[:60] for item in repeated]})",
        )

    # A workspace installed before this change carries the v1 marker; the writer
    # must refresh it rather than leaving the old contract in place.
    with TemporaryDirectory() as root:
        workspace = manager_workspace(Path(root))
        skill_path = workspace / ".agents/skills/loopx-manager/SKILL.md"
        check(skill_path.exists(), "the manager workspace must install the manager skill")
        assert_labels_in_order(
            skill_path.read_text(encoding="utf-8"), "the installed manager skill"
        )
        skill_path.write_text(
            f"{MANAGED_SKILL_MARKER_V1}\n\n# stale manager skill\n", encoding="utf-8"
        )
        manager_workspace(Path(root))
        check(
            MANAGED_SKILL_CURRENT_MARKER in skill_path.read_text(encoding="utf-8"),
            "a workspace holding the previous marker must be refreshed to the current skill",
        )
        instructions = (workspace / "AGENTS.md").read_text(encoding="utf-8")
        check(
            instruction in instructions,
            "the installed manager instructions must carry the contract instruction",
        )
        check(
            MANAGER_CONTEXT_VERSION >= 14,
            "an answer-contract change must invalidate existing manager context",
        )

    ordered = "结论：本轮已完成 X。\n\n里程碑/基线：M1 验收。\n\n依据：交付记录与 CI。\n\n缺口：远端来源本次未读。"
    verdict = classify_manager_answer_shape(ordered)
    check(
        verdict["schema_version"] == MANAGER_ANSWER_CONTRACT_SCHEMA_VERSION,
        f"the verdict names its contract ({verdict['schema_version']})",
    )
    check(verdict["state"] == "ordered", f"a contract-shaped answer is ordered ({verdict})")
    check(
        verdict["footer_section"] == "缺口",
        f"the verdict names the footer section ({verdict['footer_section']})",
    )

    reordered = "缺口：远端来源本次未读。\n\n依据：交付记录与 CI。\n\n里程碑/基线：M1。\n\n结论：已完成 X。"
    verdict = classify_manager_answer_shape(reordered)
    check(
        verdict["state"] == "out_of_order",
        f"an answer that opens with its disclaimers is out_of_order ({verdict})",
    )
    check(
        verdict["observed_order"][0] == "缺口",
        f"the verdict reports what the answer actually led with ({verdict['observed_order']})",
    )

    incomplete = "结论：已完成 X。\n\n依据：交付记录。"
    verdict = classify_manager_answer_shape(incomplete)
    check(verdict["state"] == "incomplete", f"a missing part is incomplete ({verdict})")
    check(
        verdict["missing_sections"] == ["里程碑/基线", "缺口"],
        f"the verdict names the missing parts ({verdict['missing_sections']})",
    )

    # The readback is the owner channel's, and it never rewrites the answer.
    readback = manager_answer_readback({"message": ordered}, channel="manager")
    check(
        readback["message"] == ordered and readback["answer_shape"]["state"] == "ordered",
        f"the owner readback attaches the shape and keeps the answer ({readback})",
    )
    external = manager_answer_readback({"message": ordered}, channel="manager.external.lark:team")
    check(
        "answer_shape" not in external,
        "an external audience keeps its own transcript and is not measured by this contract",
    )
    check(
        manager_answer_readback({"message": ""}, channel="manager").get("answer_shape") is None,
        "an empty answer is not classified",
    )

    print("manager-answer-contract-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
