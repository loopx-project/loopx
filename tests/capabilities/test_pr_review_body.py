import json
from pathlib import Path

import pytest

from loopx.capabilities.pr_review_queue.review_body import check_review_body

HEAD = "a" * 40
FIXTURES = Path(__file__).parents[2] / "examples/fixtures"


def review_body():
    return (FIXTURES / "pr-review.body.md").read_text().replace(
        "HEAD_OID", HEAD).replace("VERDICT", "APPROVE")


def test_standalone_body_contains_enough_explanation_but_does_not_certify_truth():
    result = check_review_body(review_body(), head_oid=HEAD, behavior_bearing=True)
    assert result["valid"]
    assert not result["evidence_truth_verified"]


@pytest.mark.parametrize("case", json.loads((FIXTURES / "pr-review-history/cases.json").read_text()),
                         ids=lambda case: case["case_family"])
def test_historical_reviews_pass_shape_checks_without_certifying_their_conclusions(case):
    body = (FIXTURES / case["review_file"]).read_text().replace(
        "HEAD_OID", case["head"]).replace("VERDICT", case["expected_verdict"])
    result = check_review_body(body, head_oid=case["head"], behavior_bearing=True)
    assert result["valid"], result["invalid_reasons"]
    assert result["verdict"] == case["expected_verdict"]
    assert not result["evidence_truth_verified"]


@pytest.mark.parametrize("padding", [
    "很好。\n" * 100,
    "[证据](https://example.com/" + "long-path" * 100 + ")",
    "```text\n" + "代码不会证明审阅了真实调用路径" * 100 + "\n```",
    "## 空标题\n" * 100,
])
def test_padding_cannot_replace_risk_explanation(padding):
    body = review_body()
    start, end = body.index("## 对主干的风险"), body.index("## 我的整体评价")
    body = body[:start] + "## 对主干的风险\n" + padding + "\n" + body[end:]
    result = check_review_body(body, head_oid=HEAD, behavior_bearing=True)
    assert not result["valid"]
    assert any(reason.startswith("section_too_short:对主干的风险") for reason in result["invalid_reasons"])


def test_headings_inside_code_do_not_count_as_review_sections():
    result = check_review_body("```\n" + review_body() + "\n```", head_oid=HEAD, behavior_bearing=True)
    assert "missing_section:具体改动" in result["invalid_reasons"]


@pytest.mark.parametrize("invalid_closer", ["```", "````still-code"])
def test_short_or_trailing_text_fence_cannot_expose_hidden_review(invalid_closer):
    body = "````\n" + invalid_closer + "\n" + review_body() + "\n````"
    result = check_review_body(body, head_oid=HEAD, behavior_bearing=True)
    assert "missing_section:具体改动" in result["invalid_reasons"]
    assert "missing_english_verdict" in result["invalid_reasons"]


def test_closed_fence_and_comment_before_visible_review_still_pass():
    body = "````\n<!-- ignored inside code -->\n````\n<!-- ignored -->\n" + review_body()
    assert check_review_body(body, head_oid=HEAD, behavior_bearing=True)["valid"]


def test_review_hidden_in_html_comment_cannot_claim_published_conclusion():
    result = check_review_body("<!--\n" + review_body() + "\n-->",
                               head_oid=HEAD, behavior_bearing=True)
    assert "missing_section:具体改动" in result["invalid_reasons"]
    assert "missing_exact_head" in result["invalid_reasons"]
    assert "missing_english_verdict" in result["invalid_reasons"]


def test_html_comments_do_not_supply_prose_or_change_visible_fence_state():
    body = review_body()
    start, end = body.index("## 对主干的风险"), body.index("## 我的整体评价")
    body = (body[:start] + "## 对主干的风险\n风险很小。\n"
            + "<!-- ```\n" + "隐藏的风险解释。" * 20 + "\n-->\n"
            + body[end:])
    result = check_review_body(body, head_oid=HEAD, behavior_bearing=True)
    assert any(reason.startswith("section_too_short:对主干的风险")
               for reason in result["invalid_reasons"])
    assert "missing_section:我的整体评价" not in result["invalid_reasons"]
    assert "missing_english_verdict" not in result["invalid_reasons"]


def test_unclosed_html_comment_hides_following_review_text():
    result = check_review_body("<!--\n" + review_body(),
                               head_oid=HEAD, behavior_bearing=True)
    assert "missing_section:具体改动" in result["invalid_reasons"]


def test_verdict_and_head_inside_code_do_not_count_as_published_conclusion():
    # Historical reviews also name the head in the motivation. Remove every
    # visible occurrence to isolate the fenced-conclusion regression.
    body = "\n".join(line for line in review_body().splitlines()
                     if not line.startswith("English verdict:")).replace(HEAD, "reviewed revision")
    body += f"\n```text\nEnglish verdict: APPROVE - {HEAD}\n```"
    result = check_review_body(body, head_oid=HEAD, behavior_bearing=True)
    assert "missing_english_verdict" in result["invalid_reasons"]
    assert "missing_exact_head" in result["invalid_reasons"]


def test_duplicate_sections_are_not_merged_into_a_passing_review():
    result = check_review_body(review_body() + "\n## 动机\n重复。", head_oid=HEAD, behavior_bearing=True)
    assert "duplicate_section:动机" in result["invalid_reasons"]


def test_bilingual_headings_keep_canonical_evidence_sections_and_prose_floors():
    from loopx.capabilities.pr_review_queue.review_body import review_section_text

    body = review_body()
    headings = [("动机", "Motivation"), ("改动思路", "Approach"), ("具体改动", "Changes"),
                ("对主干的风险", "Risks"), ("我的整体评价", "Verdict")]
    for chinese, english in headings:
        body = body.replace(f"## {chinese}\n", f"## {chinese} / {english}\n")
    original = check_review_body(review_body(), head_oid=HEAD, behavior_bearing=True)
    translated = check_review_body(body, head_oid=HEAD, behavior_bearing=True)
    assert translated == original
    for chinese, _ in headings:
        assert review_section_text(body, chinese) == review_section_text(review_body(), chinese)


@pytest.mark.parametrize("heading", ["动机 / Other", "动机 / Motivation / Extra", "Motivation"])
def test_bilingual_aliases_do_not_accept_arbitrary_or_english_only_headings(heading):
    body = review_body().replace("## 动机\n", f"## {heading}\n")
    result = check_review_body(body, head_oid=HEAD, behavior_bearing=True)
    assert "missing_section:动机" in result["invalid_reasons"]


def test_chinese_and_bilingual_alias_cannot_hide_duplicate_or_empty_sections():
    result = check_review_body(review_body() + "\n## 动机 / Motivation\n重复。",
                               head_oid=HEAD, behavior_bearing=True)
    assert "duplicate_section:动机" in result["invalid_reasons"]
    body = review_body()
    start, end = body.index("## 对主干的风险"), body.index("## 我的整体评价")
    body = body[:start] + "## 对主干的风险 / Risks\nFAIL\n" + body[end:]
    result = check_review_body(body, head_oid=HEAD, behavior_bearing=True)
    assert any(reason.startswith("section_too_short:对主干的风险")
               for reason in result["invalid_reasons"])


@pytest.mark.parametrize("wrapper", ["```\n{}\n```", "<!--\n{}\n-->"])
def test_bilingual_heading_remains_hidden_in_code_or_comment(wrapper):
    body = review_body().replace("## 动机\n", "## 动机 / Motivation\n")
    result = check_review_body(wrapper.format(body), head_oid=HEAD, behavior_bearing=True)
    assert "missing_section:动机" in result["invalid_reasons"]


def test_multiple_english_verdict_lines_cannot_hide_a_contradiction():
    result = check_review_body(review_body() + "\nEnglish verdict: REQUEST_CHANGES", head_oid=HEAD,
                               behavior_bearing=True)
    assert "ambiguous_english_verdict" in result["invalid_reasons"]


def test_docs_can_use_shorter_explanation_than_behavior_changes():
    body = "\n".join(f"## {label}\n{content}" for label, content in [
        ("动机", "文档中的命令参数已失效，用户照着操作无法读取当前配置。"),
        ("改动思路", "对照当前发布版本的帮助输出，修正原有示例参数，并删除同页与它冲突的旧说明。"),
        ("具体改动", "更新配置读回示例和错误提示说明，使参数名称与实际帮助一致。保留原有配置位置与操作顺序；通过发布包执行示例，检查输出内容能够找到用户刚写入的配置。"),
        ("对主干的风险", "此变更仅修改说明文字；主要风险是命令仍不能运行，因此使用实际发布包执行文档示例并验证结果。"),
        ("我的整体评价", "命令和读回均已验证，文档修复完成；无需引入新的配置选项。"),
    ]) + f"\nEnglish verdict: APPROVE - {HEAD}"
    assert check_review_body(body, head_oid=HEAD, behavior_bearing=False)["valid"]
    assert not check_review_body(body, head_oid=HEAD, behavior_bearing=True)["valid"]
