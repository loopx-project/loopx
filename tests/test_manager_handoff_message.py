"""An inbox receipt is not receiver execution; explain the delivered brief."""

import pytest

from loopx.capabilities.manager_context.execution import handoff_message


RECEIPT = {"goal_id": "research", "agent_id": "worker", "status": "delivered"}
BRIEF = {
    "schema_version": "collaboration_brief_v0",
    "purpose": "Check the reported failure and return a verified fix.",
    "context": "This worker owns the affected module. Keep the owner's latest correction.",
    "constraints": ["Preserve existing permissions."],
    "inputs": [],
    "acceptance": ["Reproduce the failure, then verify the fix."],
    "return_requirement": "Return the result to this conversation.",
}


@pytest.mark.parametrize(
    "execution",
    [
        {"submitted": False},
        {"submitted": False, "reason": "execution_not_launchable"},
    ],
)
def test_receipt_does_not_establish_receiver_execution(execution):
    message = handoff_message(RECEIPT, execution)
    assert "`worker`" in message
    assert "research / worker" not in message
    assert "尚未启动" not in message
    assert "是否已开始处理尚未核实" in message
    assert "处理结论" in message and "本次对话" in message


def test_ack_explains_existing_brief_without_changing_it():
    original = {**BRIEF, "constraints": list(BRIEF["constraints"])}
    message = handoff_message(RECEIPT, {"submitted": False}, brief=BRIEF)
    for field in ("purpose", "return_requirement"):
        assert BRIEF[field] in message
    assert BRIEF["constraints"][0] in message
    assert BRIEF["acceptance"][0] in message
    assert BRIEF["context"] not in message
    assert BRIEF == original


def test_full_brief_is_not_dumped_into_ack():
    long_brief = {**BRIEF, "context": "c" * 6000, "constraints": ["x" * 1000] * 12}
    message = handoff_message(RECEIPT, {"submitted": False}, brief=long_brief)
    assert len(message) < 1000
    assert "完整简报已投递" in message
    assert long_brief["context"] == "c" * 6000
    assert len(long_brief["constraints"]) == 12


def test_submission_is_separate_from_completion():
    message = handoff_message(
        RECEIPT, {"submitted": True, "status": "prepared"}, brief=BRIEF
    )
    assert "已提交执行" in message
    assert "尚未确认完成" in message
    assert "是否已开始处理尚未核实" not in message


def test_display_is_scannable_and_keeps_detail_out_of_the_preview():
    detailed = {
        **BRIEF,
        "purpose": "Inspect the changed behavior.\nReturn checked findings.",
        "context": "Internal selection record: request-" + "a" * 64,
        "acceptance": ["First result", "Second result", "Third result", "Fourth result"],
        "constraints": ["Keep scope", "Keep audience", "Preserve stop"],
    }
    message = handoff_message(RECEIPT, {"submitted": False}, brief=detailed)
    assert "Inspect the changed behavior. Return checked findings." in message
    assert "**期待的结果**\n\n- First result\n- Second result\n- Third result" in message
    assert "**执行边界**\n\n- Keep scope\n- Keep audience" in message
    assert "另有 1 项" in message
    assert detailed["context"] not in message
    assert detailed["acceptance"][-1] == "Fourth result"
    assert detailed["constraints"][-1] == "Preserve stop"
    assert "是否已开始处理尚未核实" in message


def test_each_preview_item_has_a_visible_bound():
    huge = {
        **BRIEF, "purpose": "p" * 2000, "context": "context" * 1000,
        "acceptance": ["a" * 1000] * 6, "constraints": ["k" * 1000] * 6,
        "return_requirement": "r" * 1000,
    }
    message = handoff_message(RECEIPT, {"submitted": False}, brief=huge)
    assert len(message) < 1000
    assert "p" * 161 not in message and "a" * 101 not in message
    assert "k" * 91 not in message and "r" * 121 not in message
    assert "…" in message and "另有 3 项" in message and "另有 4 项" in message
    assert huge["return_requirement"] == "r" * 1000
