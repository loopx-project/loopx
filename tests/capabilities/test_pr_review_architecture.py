"""Architecture judgment must affect the verdict and reach the published body."""

import copy
import json

import pytest

from loopx.capabilities.pr_review_queue.result_check import check_review_result
from loopx.capabilities.pr_review_queue.review_contract import build_agent_response_contract
from loopx.cli import main
from tests.capabilities.test_pr_review_result_check import _review


KEY = "change_proportionality:architecture_assessment"


def _architecture_review(decision="retain"):
    packet, result = _review()
    assessment = {
        "decision": decision,
        "reason": "依据检查属于已有提交 owner；独立复核属于可选策略，两者不能靠一个开关捆绑。",
        "current_pr_boundary": "当前 PR 交付原 Turn 的结果保留和恢复；默认入口由已有宿主 owner 接入并验证，额外 reviewer 留给可选能力。",
        "validation_evidence": "Synthetic consistency fixture: actual reviewed evidence must name callers and real tests.",
        "mechanisms": [
            {"mechanism": "result basis check", "responsibility": "invariant",
             "owning_boundary": "Existing commit owner; keep its fence and receipt authority.",
             "activation_and_default": "Required within admitted commit scope; no blanket new trigger.",
             "failure_and_recovery": "Reject stale new writes; replay original committed receipt first.",
             "placement_basis": "Accepted commit contract, not the existence of a helper."},
            {"mechanism": "independent direction review", "responsibility": "policy",
             "owning_boundary": "Optional outcome capability; Host provider computes only.",
             "activation_and_default": "Explicit opt-in, separate from base integrity.",
             "failure_and_recovery": "When selected for a required checkpoint, retain result and recover original Turn.",
             "placement_basis": "Current maintainer direction; existing observer hook remains isolated."},
        ],
    }
    result["evidence"]["change_proportionality"]["architecture_assessment"] = assessment
    text = "\n\n".join([assessment["reason"], assessment["current_pr_boundary"]])
    result["review_body"] = result["review_body"].replace("## 改动思路\n", "## 改动思路\n\n" + text + "\n")
    return packet, result


def test_real_packet_exposes_architecture_in_the_existing_proportionality_owner():
    contract = build_agent_response_contract()["review_execution_contract"]
    requirement = next(r for r in contract["evidence_requirements"]
                       if r["evidence_id"] == "change_proportionality")
    assert "architecture_assessment" in requirement["fields"]
    assessment = requirement["architecture_assessment"]
    assert set(assessment["blocking_decisions"]) == {"simplify_now", "not_yet_proven"}
    assert assessment["publication"]["fields"] == ["reason", "current_pr_boundary"]
    assert "activation_and_default" in assessment["mechanism_fields"]
    assert "failure_and_recovery" in assessment["mechanism_fields"]


@pytest.mark.parametrize("decision", ["simplify_now", "not_yet_proven"])
def test_green_parent_approval_cannot_hide_required_architecture_work(decision):
    packet, result = _architecture_review(decision)
    # All other evidence stays verified/proportionate with no finding or red check.
    assert result["evidence"]["change_proportionality"]["verdict"] == "proportionate"
    checked = check_review_result(packet, result)
    assert f"{KEY}:blocking_decision" in checked["approval_blockers"]
    assert "approval_contradicts_evidence" in checked["errors"]
    result["verdict"] = "REQUEST_CHANGES"
    result["review_body"] = result["review_body"].replace("English verdict: APPROVE", "English verdict: REQUEST_CHANGES")
    assert check_review_result(packet, result)["ok"]


@pytest.mark.parametrize("decision", ["retain", "follow_up"])
def test_supported_design_or_nonblocking_future_work_can_still_approve(decision):
    packet, result = _architecture_review(decision)
    assert check_review_result(packet, result)["approval_consistent"]


@pytest.mark.parametrize("mutation", ["missing", "generic_prose", "empty_mechanisms",
    "unknown_role", "missing_failure_owner", "unknown_decision", "non_text_basis"])
def test_declared_verification_cannot_replace_mechanism_judgment(mutation):
    packet, result = _architecture_review()
    row = result["evidence"]["change_proportionality"]
    value = row["architecture_assessment"]
    if mutation == "missing":
        row.pop("architecture_assessment")
    elif mutation == "generic_prose":
        row["architecture_assessment"] = "Tests pass; architecture looks good."
    elif mutation == "empty_mechanisms":
        value["mechanisms"] = []
    elif mutation == "unknown_role":
        value["mechanisms"][0]["responsibility"] = "anything"
    elif mutation == "missing_failure_owner":
        value["mechanisms"][0].pop("failure_and_recovery")
    elif mutation == "unknown_decision":
        value["decision"] = ["retain"]
    else:
        value["mechanisms"][0]["placement_basis"] = True
    checked = check_review_result(packet, result)
    assert any(blocker.startswith(KEY) for blocker in checked["approval_blockers"])
    assert not checked["approval_consistent"]


@pytest.mark.parametrize("hiding", ["omit", "comment", "code", "wrong_section"])
def test_decisive_architecture_cannot_stay_in_private_result_or_hidden_prose(hiding):
    packet, result = _architecture_review()
    text = result["evidence"]["change_proportionality"]["architecture_assessment"]["reason"]
    result["review_body"] = result["review_body"].replace(text, "")
    if hiding == "comment":
        result["review_body"] += f"\n<!-- {text} -->\n"
    elif hiding == "code":
        result["review_body"] += f"\n```text\n{text}\n```\n"
    elif hiding == "wrong_section":
        result["review_body"] = result["review_body"].replace("## 动机\n", f"## 动机\n\n{text}\n")
    assert "review_body:architecture_not_published:reason" in check_review_result(packet, result)["errors"]


def test_saved_packet_cannot_weaken_the_live_architecture_gate():
    packet, result = _architecture_review("simplify_now")
    packet["agent_response_contract"] = {"review_execution_contract": {"evidence_requirements": []}}
    assert f"{KEY}:blocking_decision" in check_review_result(packet, result)["approval_blockers"]


@pytest.mark.parametrize("field", ["reason", "current_pr_boundary"])
@pytest.mark.parametrize("text", ["[](https://example.com/empty)", "![](https://example.com/empty)", "` _ `", "**__**", " \n\t "])
def test_empty_visible_architecture_text_cannot_count_as_publication(field, text):
    packet, result = _architecture_review()
    assessment = result["evidence"]["change_proportionality"]["architecture_assessment"]
    result["review_body"] = result["review_body"].replace(assessment[field], "")
    assessment[field] = text
    checked = check_review_result(packet, result)
    assert f"review_body:architecture_not_text:{field}" in checked["errors"]
    assert not checked["approval_consistent"]


@pytest.mark.parametrize("field", ["reason", "current_pr_boundary"])
def test_visible_formatted_architecture_text_remains_accepted(field):
    packet, result = _architecture_review()
    assessment = result["evidence"]["change_proportionality"]["architecture_assessment"]
    original = assessment[field]
    assessment[field] = f"**{original}**"
    result["review_body"] = result["review_body"].replace(original, f"[{original}](https://example.com/basis)")
    assert check_review_result(packet, result)["approval_consistent"]


def test_real_cli_checks_the_same_gate_and_preserves_publication(tmp_path, capsys):
    packet, result = _architecture_review("simplify_now")
    packet_path, result_path = tmp_path / "packet.json", tmp_path / "result.json"
    packet_path.write_text(json.dumps(packet))
    result_path.write_text(json.dumps(result))
    args = ["--format", "json", "pr-review", "--check-result", str(result_path), "--packet", str(packet_path)]
    main(args)
    checked = json.loads(capsys.readouterr().out)
    assert f"{KEY}:blocking_decision" in checked["approval_blockers"]
    result = copy.deepcopy(result)
    result["verdict"] = "REQUEST_CHANGES"
    result["review_body"] = result["review_body"].replace("English verdict: APPROVE", "English verdict: REQUEST_CHANGES")
    result_path.write_text(json.dumps(result))
    main(args)
    assert json.loads(capsys.readouterr().out)["ok"]


@pytest.mark.parametrize("field,text", [
    ("reason", "[](https://example.com/empty)"),
    ("current_pr_boundary", "` _ `"),
])
def test_real_cli_rejects_empty_publication_and_recovers_with_visible_text(tmp_path, capsys, field, text):
    packet, result = _architecture_review()
    assessment = result["evidence"]["change_proportionality"]["architecture_assessment"]
    original = assessment[field]
    assessment[field] = text
    packet_path, result_path = tmp_path / "packet.json", tmp_path / "result.json"
    packet_path.write_text(json.dumps(packet))
    result_path.write_text(json.dumps(result))
    args = ["--format", "json", "pr-review", "--check-result", str(result_path), "--packet", str(packet_path)]
    main(args)
    checked = json.loads(capsys.readouterr().out)
    assert f"review_body:architecture_not_text:{field}" in checked["errors"]
    assert not checked["approval_consistent"]
    assessment[field] = f"**{original}**"
    result_path.write_text(json.dumps(result))
    main(args)
    assert json.loads(capsys.readouterr().out)["ok"]
