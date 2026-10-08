import json

import pytest

from loopx.extensions.lark.outbound import (
    normalize_lark_outbound_text,
    safe_lark_plain_text_fallback,
)
from loopx.extensions.lark.presentation.markdown_post import (
    lark_markdown_post_content, lark_markdown_preview_matches,
    lark_markdown_readback_matches, normalize_lark_markdown_emphasis,
)

from loopx.extensions.lark.inbox_reply import (
    reply_lark_event_inbox,
    verify_lark_inbox_reply,
)
from test_lark_inbox_reactions import ReplyRunner, _fixture

TEXT = "进展\n\n- **结果**\n  - 证据\n\n```python\nif ok:\n    done()\n```"


def test_result_attachment_zone_matches_raw_post_and_flattened_cli_readback():
    keys = ("file_first", "file_second")
    content = json.loads(lark_markdown_post_content(TEXT))
    content["files"] = [{"key": key} for key in keys]
    assert lark_markdown_preview_matches(text=TEXT, payload={"api": [{"body": {
        "msg_type": "post", "content": content,
    }}]}, attachment_keys=keys)
    for actual in [{"body": {"content": content}}, {"content": TEXT + '\n\n<file key="file_first" name="first.pdf"/>\n<file key="file_second" name="second.csv"/>'}]:
        assert lark_markdown_readback_matches(text=TEXT, message={"msg_type": "post", **actual}, attachment_keys=keys)
    for tags in ['', '<file key="file_first"/>', '<file key="file_second"/>\n<file key="file_first"/>', '<image key="file_first"/>\n<file key="file_second"/>', '<file key="file_first"/>\n<file key="other"/>']:
        assert not lark_markdown_readback_matches(text=TEXT, message={"msg_type": "post", "content": TEXT + '\n' + tags}, attachment_keys=keys)
    assert not lark_markdown_readback_matches(text=TEXT, message={"msg_type": "post", "body": {"content": content}})


@pytest.mark.parametrize("flattened", [False, True])
def test_result_readback_allows_provider_resource_keys_only_for_exact_file_names(flattened):
    from loopx.extensions.lark.presentation.markdown_post import lark_markdown_readback_attachment_keys

    uploaded = ("file_upload_first", "file_upload_second")
    names = ("first.pdf", "second.csv")
    resources = ("file_message_first", "file_message_second")
    content = json.loads(lark_markdown_post_content(TEXT))
    content["files"] = [{"key": key, "name": name} for key, name in zip(resources, names)]
    message = {"msg_type": "post", **({"content": TEXT + '\n\n' + '\n'.join(
        f'<file key="{key}" name="{name}"/>' for key, name in zip(resources, names))}
        if flattened else {"body": {"content": content}})}
    assert lark_markdown_readback_attachment_keys(text=TEXT, message=message,
        attachment_keys=uploaded, attachment_names=names) == resources
    assert lark_markdown_readback_matches(text=TEXT, message=message,
        attachment_keys=uploaded, attachment_names=names)
    assert not lark_markdown_readback_matches(text=TEXT, message=message, attachment_keys=uploaded)
    for wrong_names in [("second.csv", "first.pdf"), ("first.pdf", "other.csv"), ("first.pdf",)]:
        assert not lark_markdown_readback_matches(text=TEXT, message=message,
            attachment_keys=uploaded, attachment_names=wrong_names)
    for replacement in ['<image key="file_message_first" name="first.pdf"/>',
                        '<file key="../outside" name="first.pdf"/>',
                        '<file key="file_message_first"/>',
                        '<file key="file_message_first" name="first.pdf"><text>extra</text></file>']:
        changed = {"msg_type": "post", "content": TEXT + '\n' + replacement +
                   '\n<file key="file_message_second" name="second.csv"/>'}
        assert not lark_markdown_readback_matches(text=TEXT, message=changed,
            attachment_keys=uploaded, attachment_names=names)


def test_safe_plain_text_fallback_repairs_presentation_without_forging_mentions():
    text = (
        r"结论：通过\n下一步：@LoopX 管家查看"
        "\n```text\nkeep \\n and @fixture\n```"
    )

    fallback = safe_lark_plain_text_fallback(text)

    assert fallback.startswith("结论：通过\n下一步：＠LoopX 管家查看")
    assert "keep \\n and @fixture" in fallback
    assert normalize_lark_outbound_text(fallback, limit=None, preserve_format=True) == fallback


def test_post_preview_preserves_structure_without_fetching_or_rewriting():
    text = normalize_lark_outbound_text(TEXT, preserve_format=True)
    assert text == TEXT
    content = lark_markdown_post_content(text)
    payload = {"api": [{"body": {"msg_type": "post", "content": content}}]}
    assert lark_markdown_preview_matches(text=text, payload=payload)
    payload["api"][0]["body"]["msg_type"] = "text"
    assert not lark_markdown_preview_matches(text=text, payload=payload)


@pytest.mark.parametrize("change", ["type", "indent", "missing", "mention", "extra_node"])
def test_readback_rejects_delivery_or_content_changes(change):
    content = json.loads(lark_markdown_post_content(TEXT))
    message = {"msg_type": "post", "body": {"content": content}}
    assert lark_markdown_readback_matches(text=TEXT, message=message)
    if change == "type":
        message["msg_type"] = "text"
    elif change == "indent":
        content["zh_cn"]["content"][0][0]["text"] = TEXT.replace("    done", "done")
    elif change == "missing":
        content["zh_cn"]["content"][0][0]["text"] = "进展"
    elif change == "mention":
        message["mentions"] = [{"id": "unexpected"}]
    else:
        content["zh_cn"]["content"][0].append({"tag": "text", "text": "extra"})
    assert not lark_markdown_readback_matches(text=TEXT, message=message)


@pytest.mark.parametrize("placement", ["source_context", "source_thread"])
def test_reply_transport_verifies_post_and_reuses_exact_idempotent_request(tmp_path, placement):
    config, _, project = _fixture(tmp_path, lifecycle=False)
    value = json.loads(config.read_text())
    value["reply"]["placement_policy"] = placement
    config.write_text(json.dumps(value))
    fallback = ReplyRunner()
    calls = []
    def runner(args):
        calls.append(args)
        if "+messages-send" in args or "+messages-reply" in args:
            assert "--markdown" not in args and "--text" not in args
            assert args[args.index("--msg-type") + 1] == "post"
            content = args[args.index("--content") + 1]
            assert json.loads(content)["zh_cn"]["content"][0][0]["text"] == TEXT
            result = {"api": [{"body": {"msg_type": "post", "content": content}}]} if "--dry-run" in args else {"message_id": "om_reply_fixture"}
            return {"returncode": 0, "stdout": json.dumps(result)}
        if "+messages-mget" in args:
            return {"returncode": 0, "stdout": json.dumps({"items": [{"message_id": "om_reply_fixture", "msg_type": "post", "content": TEXT}]})}
        return fallback(args)
    receipts = [reply_lark_event_inbox(project=project, config_path=config,
        message_id="om_reaction_fixture", text=TEXT, content_format="markdown",
        execute=True, runner=runner) for _ in range(2)]
    assert all(result["reply_verified"] for result in receipts)
    assert receipts[0]["idempotency_key"] == receipts[1]["idempotency_key"]
    sent = [args for args in calls if "--content" in args and "--dry-run" not in args]
    assert sent[0] == sent[1]
    assert ("--reply-in-thread" in sent[0]) == (placement == "source_thread")


def test_large_post_falls_back_before_send_without_truncating(tmp_path):
    config, _, project = _fixture(tmp_path, lifecycle=False)
    text = "- 完整内容\n" * 2000
    fallback = ReplyRunner(readback_text=text.strip())
    calls = []
    def runner(args):
        calls.append(args)
        if "--content" in args:
            assert "--dry-run" in args  # oversized post must never be sent
            return {"returncode": 0, "stdout": json.dumps({"api": [{"body": {
                "msg_type": "post", "content": args[args.index("--content") + 1],
            }}]})}
        return fallback(args)
    result = reply_lark_event_inbox(project=project, config_path=config,
        message_id="om_reaction_fixture", text=text, content_format="markdown",
        execute=True, runner=runner)
    assert result["reply_verified"]
    assert result["content_format"] == "text"
    assert result["format_fallback"] == "post_size_limit"
    sent = [args for args in calls if "--text" in args and "--dry-run" not in args]
    assert len(sent) == 1
    assert sent[0][sent[0].index("--text") + 1] == text.strip()


def test_large_post_fallback_can_be_verified_without_resending(tmp_path):
    config, _, project = _fixture(tmp_path, lifecycle=False)
    text = "- 完整内容\n" * 2000
    fallback = ReplyRunner(readback_text="different text")
    attempts = []

    def runner(args):
        if "--content" in args:
            return {
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "api": [
                            {
                                "body": {
                                    "msg_type": "post",
                                    "content": args[args.index("--content") + 1],
                                }
                            }
                        ]
                    }
                ),
            }
        return fallback(args)

    first = reply_lark_event_inbox(
        project=project,
        config_path=config,
        message_id="om_reaction_fixture",
        text=text,
        content_format="markdown",
        execute=True,
        runner=runner,
        delivery_attempt_recorder=attempts.append,
    )
    assert first["status"] == "sent_unverified"
    assert first["content_format"] == "text"

    recovery = ReplyRunner(readback_text=text.strip())
    verified = verify_lark_inbox_reply(
        project=project,
        config_path=config,
        message_id="om_reaction_fixture",
        text=text,
        attempt=attempts[0],
        runner=recovery,
    )
    assert verified["reply_verified"] is True
    assert not any(
        "+messages-send" in call or "+messages-reply" in call
        for call in recovery.calls
    )


def test_markdown_request_with_mentions_keeps_verified_text_transport(tmp_path):
    config, _, project = _fixture(tmp_path, lifecycle=False)
    text = '<at open_id="ou_public_reviewer">Reviewer</at> please review'
    runner = ReplyRunner(readback_text="@_user_1 please review", readback_mentions=[{
        "key": "@_user_1", "id": "ou_public_reviewer", "name": "Reviewer",
    }])
    result = reply_lark_event_inbox(project=project, config_path=config,
        message_id="om_reaction_fixture", text=text, content_format="markdown",
        execute=True, runner=runner)
    assert result["reply_verified"]
    assert result["content_format"] == "text"
    assert all("--content" not in args for args in runner.calls)


@pytest.mark.parametrize(("source", "expected"), [
    ("**委派已完成。**回执可查", "**委派已完成**。回执可查"),
    ("**交给了 `research-agent`。**回执 `example-01` 确认", "**交给了 `research-agent`**。回执 `example-01` 确认"),
    ("**Done.**Next", "**Done**.Next"),
    ("- **设计回顾（草稿）**｜状态：待审", "- **设计回顾（草稿**）｜状态：待审"),
    ("**Release notes (draft)**｜Ready", "**Release notes (draft**)｜Ready"),
    ("**检查完成！**✅", "**检查完成**！✅"),
    ("**一。**后面 **二！**还有", "**一**。后面 **二**！还有"),
])
def test_provider_strong_boundary_preserves_visible_text(source, expected):
    assert normalize_lark_markdown_emphasis(source) == expected
    assert expected.replace("**", "") == source.replace("**", "")
    assert normalize_lark_markdown_emphasis(expected) == expected
    post = json.loads(lark_markdown_post_content(source))
    assert post["zh_cn"]["content"][0][0]["text"] == expected
    assert lark_markdown_preview_matches(text=source, payload={"api": [{"body": {
        "msg_type": "post", "content": post,
    }}]})
    assert lark_markdown_readback_matches(text=source, message={
        "msg_type": "post", "content": expected,
    })
    assert not lark_markdown_readback_matches(text=source, message={
        "msg_type": "post", "content": source,
    })


@pytest.mark.parametrize("source", [
    "**已完成。** 回执确认", "**已完成**。回执确认",
    "**已完成。**！确认", "**Ready.**|Next", "**Price $**$10",
    "**标题**｜下一项", "`**标题（草稿）**｜下一项`",
    "[link](https://example.org/**draft）**｜next)",
    "`**literal。**Next`", "``**literal。**Next``",
    "**带 `**literal。**Next` 的代码。** 已确认",
    r"\*\*literal。\*\*Next", "**unmatched。Next",
    "***nested。***Next", "[link](https://example.org/**path。**Next)",
    "[link](https://example.org/(nested)/**path。**Next)",
    "**[link](https://example.org)**Next", "**`code`**Next",
    "```python\n**literal。**Next\n```", "````md\n**literal。**Next\n````",
    "~~~md\n**literal。**Next\n~~~", "```md\n**literal。**Next",
])
def test_provider_repair_keeps_valid_or_opaque_markdown(source):
    assert normalize_lark_markdown_emphasis(source) == source


@pytest.mark.parametrize(("source", "expected"), [
    ("**交给了 `research-agent`。**回执已确认", "**交给了 `research-agent`**。回执已确认"),
    ("**设计回顾（草稿）**｜待审", "**设计回顾（草稿**）｜待审"),
])
def test_reply_transport_uses_the_provider_repair_and_checks_normalized_readback(tmp_path, source, expected):
    config, _, project = _fixture(tmp_path, lifecycle=False)
    fallback = ReplyRunner()
    def runner(args):
        if "+messages-send" in args or "+messages-reply" in args:
            content = args[args.index("--content") + 1]
            assert json.loads(content)["zh_cn"]["content"][0][0]["text"] == expected
            result = {"api": [{"body": {"msg_type": "post", "content": content}}]} if "--dry-run" in args else {"message_id": "om_reply_fixture"}
            return {"returncode": 0, "stdout": json.dumps(result)}
        if "+messages-mget" in args:
            return {"returncode": 0, "stdout": json.dumps({"items": [{
                "message_id": "om_reply_fixture", "msg_type": "post", "content": expected,
            }]})}
        return fallback(args)
    result = reply_lark_event_inbox(project=project, config_path=config,
        message_id="om_reaction_fixture", text=source, content_format="markdown",
        execute=True, runner=runner)
    assert result["reply_verified"] is True
    assert result["content_format"] == "markdown"
