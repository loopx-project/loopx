"""Complete rich replies use provider bytes and survive partial delivery."""

import json
from pathlib import Path

import pytest

from loopx.extensions.lark.inbox_reply import (
    reply_lark_event_inbox,
    verify_lark_inbox_reply,
)
from loopx.extensions.lark.outbound import (
    LARK_POST_REQUEST_MAX_BYTES,
    LarkOutboundTextError,
)
from loopx.extensions.lark.presentation.markdown_parts import (
    split_lark_markdown_posts,
    post_request_bytes,
)
from loopx.extensions.lark.post_delivery import attachment_reply
from test_lark_inbox_reactions import ReplyRunner, _fixture


class RichProvider(ReplyRunner):
    def __init__(self):
        super().__init__()
        self.messages = {}
        self.sent = []
        self.unreadable = set()
        self.no_locator = False
        self.fail_after_write = False
        self.ascii_wire = False

    def __call__(self, args):
        if "+messages-send" in args or "+messages-reply" in args:
            self.calls.append(list(args))
            assert "--text" not in args
            content = json.loads(args[args.index("--content") + 1])
            keys = [args[i + 1] for i, arg in enumerate(args) if arg == "--attachment"]
            if keys:
                content["files"] = [{"key": key} for key in keys]
            body = {
                "msg_type": "post",
                "content": json.dumps(
                    content, ensure_ascii=self.ascii_wire, separators=(",", ":")
                ),
                "uuid": args[args.index("--idempotency-key") + 1],
                "receive_id": "oc_project_review",
            }
            if "--dry-run" in args:
                return {
                    "returncode": 0,
                    "stdout": json.dumps({"api": [{"body": body}]}),
                }
            assert (
                len(
                    json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
                )
                <= LARK_POST_REQUEST_MAX_BYTES
            )
            mid = f"om_part_{len(self.sent)}"
            self.sent.append(content)
            self.messages[mid] = {
                "message_id": mid,
                "msg_type": "post",
                "body": {"content": content},
            }
            return {
                "returncode": int(self.fail_after_write),
                "stdout": json.dumps({} if self.no_locator else {"message_id": mid}),
            }
        if "+messages-mget" in args:
            self.calls.append(list(args))
            mid = args[args.index("--message-ids") + 1]
            return {
                "returncode": int(mid in self.unreadable),
                "stdout": json.dumps({"items": [self.messages[mid]]}),
            }
        return super().__call__(args)


def send(tmp_path, provider, text, **options):
    config, _, project = _fixture(tmp_path, lifecycle=False)
    args = dict(
        project=project,
        config_path=config,
        message_id="om_reaction_fixture",
        text=text,
        content_format="markdown",
        execute=True,
        runner=provider,
    )
    return args, reply_lark_event_inbox(**args, **options)


def resume(args, attempt, **options):
    return verify_lark_inbox_reply(
        **{
            key: val
            for key, val in args.items()
            if key not in {"content_format", "execute"}
        },
        attempt=attempt,
        **options,
    )


@pytest.mark.parametrize(
    "body",
    ["测😀" * 9000, '"\\' * 12000, "x" * 310000],
    ids=["unicode", "escaping", "over-eight"],
)
def test_byte_packing_keeps_every_character_without_a_part_cap(body):
    template = {"msg_type": "post", "uuid": "a" * 50, "receive_id": "oc_fixture"}
    parts = split_lark_markdown_posts(body, template)
    assert "".join(part.split("\n\n", 1)[1] for part in parts) == body
    assert all(post_request_bytes(part, template) <= 30000 for part in parts)
    assert post_request_bytes(parts[0], template) >= 29990
    if len(body) > 300000:
        assert len(parts) > 8


def test_split_keeps_code_fences_tables_links_and_complete_data():
    code = "```python\n" + "    print('测')\n" * 5000 + "```"
    parts = split_lark_markdown_posts(code, {"msg_type": "post"})
    assert len(parts) > 1
    assert all(part.count("```") == 2 for part in parts)
    assert sum(part.count("print('测')") for part in parts) == 5000
    table = "| Case | Score |\n| --- | --- |\n" + "".join(
        f"| item-{i} | **0 / unknown** |\n" for i in range(4000)
    )
    parts = split_lark_markdown_posts(table, {"msg_type": "post"})
    assert all("| Case | Score |\n| --- | --- |" in part for part in parts)
    assert sum(part.count("| item-") for part in parts) == 4000
    link = "[source](https://example.org/" + "x" * 5000 + ")"
    parts = split_lark_markdown_posts(
        "a" * 29000 + link + "b" * 2000, {"msg_type": "post"}
    )
    assert sum(link in part for part in parts) == 1


@pytest.mark.parametrize("element", [
    "[source](https://example.org/" + "x" * 5000 + ")",
    "`" + "x" * 5000 + "`",
], ids=["link", "inline-code"])
def test_real_entry_moves_protected_element_to_a_fresh_post(tmp_path, element):
    text = "P" * 28000 + "\n\n" + element + "z" * 35000
    provider = RichProvider()
    _, result = send(tmp_path, provider, text)
    assert result["ok"] and result["reply_verified"]
    bodies = [m["zh_cn"]["content"][0][0]["text"] for m in provider.sent]
    # The paragraph break becomes a message boundary; no authored data may go.
    assert bodies[0].split("\n\n", 1)[1] == "P" * 28000
    assert "".join(body.split("\n\n", 1)[1] for body in bodies[1:]) == element + "z" * 35000
    assert sum(element in body for body in bodies) == 1


@pytest.mark.parametrize("block", [
    "[source](https://example.org/" + "x" * 31000 + ")",
    "| Case | Value |\n| --- | --- |\n| row | " + "x" * 31000 + " |\n",
], ids=["link", "table-row"])
def test_unrepresentable_atomic_block_stays_pending_without_any_send(tmp_path, block):
    provider = RichProvider()
    with pytest.raises(LarkOutboundTextError, match="exceeds the provider"):
        send(tmp_path, provider, "P" * 28000 + "\n\n" + block)
    assert provider.sent == []


@pytest.mark.parametrize("escaped_unicode", [False, True])
def test_real_entry_splits_rich_posts_and_recovers_only_unsent_parts(
    tmp_path, escaped_unicode
):
    provider = RichProvider()
    provider.ascii_wire = escaped_unicode
    provider.unreadable.add("om_part_0")
    text = "## Findings\n\n" + "完整字段与来源。" * 6000
    attempts = []
    args, first = send(
        tmp_path, provider, text, delivery_attempt_recorder=attempts.append
    )
    assert first["reply_verified"] is False and len(provider.sent) == 1
    assert attempts and attempts[0]["message_ref"].startswith("lark-parts:")
    assert len({json.dumps(item, sort_keys=True) for item in attempts}) == 1
    # Verify-only recovery cannot send a tail, including after a restart.
    assert resume(args, attempts[0])["reply_verified"] is False
    assert len(provider.sent) == 1
    provider.unreadable.clear()
    complete = resume(args, attempts[0], resume_parts=True)
    assert complete["reply_verified"] and complete["ok"]
    assert complete["content_format"] == "markdown"
    sent = len(provider.sent)
    assert sent > 1
    assert resume(args, attempts[0], resume_parts=True)["reply_verified"]
    assert len(provider.sent) == sent
    assert all("--text" not in call for call in provider.calls)
    content = "".join(
        m["zh_cn"]["content"][0][0]["text"].split("\n\n", 1)[1] for m in provider.sent
    )
    assert content == text


@pytest.mark.parametrize("loss", ["locator", "response"])
def test_ambiguous_part_never_resends_or_reports_completion(tmp_path, loss):
    provider = RichProvider()
    provider.no_locator = loss == "locator"
    provider.fail_after_write = loss == "response"
    attempts = []
    args, result = send(
        tmp_path, provider, "测" * 20000, delivery_attempt_recorder=attempts.append
    )
    assert not result["reply_verified"] and len(provider.sent) == 1 and attempts
    assert not resume(args, attempts[0], resume_parts=True)["reply_verified"]
    assert len(provider.sent) == 1


def test_each_part_rechecks_source_and_keeps_the_original_review_hook(tmp_path):
    provider = RichProvider()
    observations = []
    reviews = []

    def authority(profile, chat, message):
        observations.append((profile, chat, message))
        return "Project Review Bot", len(observations) < 3

    _, result = send(
        tmp_path,
        provider,
        "测" * 24000,
        source_authority_verifier=authority,
        before_send=lambda intent: (
            reviews.append(intent) or {"continue_delivery": True}
        ),
    )
    assert not result["reply_verified"] and len(provider.sent) == 1
    assert len(set(observations)) == 1 and len(reviews) == 1


def test_changed_intent_or_part_plan_is_rejected_without_a_write(tmp_path):
    provider = RichProvider()
    provider.unreadable.add("om_part_0")
    attempts = []
    args, _ = send(
        tmp_path, provider, "测" * 20000, delivery_attempt_recorder=attempts.append
    )
    changed = {**args, "text": args["text"] + "changed"}
    assert (
        resume(changed, attempts[0], resume_parts=True)["blocker"]
        == "provider_delivery_intent_conflict"
    )
    journal = next(
        Path(args["project"]).glob(
            ".loopx/inbox/feedback/manager-delivery/parts/*.json"
        )
    )
    state = json.loads(journal.read_text())
    state["delivery_parts"][1] = "other content"
    journal.write_text(json.dumps(state))
    assert (
        resume(args, attempts[0], resume_parts=True)["blocker"]
        == "provider_delivery_intent_conflict"
    )
    assert len(provider.sent) == 1


def test_attachments_stay_on_last_concrete_post(tmp_path):
    provider = RichProvider()
    attempts = []
    args, result = send(
        tmp_path,
        provider,
        "测" * 20000,
        delivery_attempt_recorder=attempts.append,
        attachment_keys=("file_fixture",),
    )
    assert result["reply_verified"]
    assert all("files" not in content for content in provider.sent[:-1])
    assert provider.sent[-1]["files"] == [{"key": "file_fixture"}]
    part, last = attachment_reply(
        project=args["project"],
        config_path=args["config_path"],
        attempt=attempts[-1],
        text=args["text"],
    )
    assert last["message_ref"] == f"om_part_{len(provider.sent) - 1}"
    assert part.endswith("测")


def test_preview_and_denied_review_do_not_write_or_record_an_attempt(tmp_path):
    provider = RichProvider()
    config, _, project = _fixture(tmp_path, lifecycle=False)
    args = dict(
        project=project,
        config_path=config,
        message_id="om_reaction_fixture",
        text="测" * 20000,
        content_format="markdown",
        runner=provider,
    )
    preview = reply_lark_event_inbox(**args, provider_preflight=True)
    assert (
        preview["content_format"] == "markdown" and preview["delivery_part_count"] > 1
    )
    assert not provider.sent
    assert not list(Path(project).glob("**/manager-delivery/parts/*.json"))
    attempts, reviewed = [], []

    class Review:
        def __call__(self, intent):
            pytest.fail("review must bind the actual destination")

        def for_destination(self, chat):
            reviewed.append(chat)
            return lambda intent: {"continue_delivery": False}

    result = reply_lark_event_inbox(
        **args,
        execute=True,
        before_send=Review(),
        delivery_attempt_recorder=attempts.append,
    )
    assert not result["reply_verified"] and not provider.sent and not attempts
    assert reviewed == ["oc_project_review"]


@pytest.mark.parametrize("fault", ["source", "destination", "missing-journal"])
def test_recovery_fences_unavailable_source_destination_or_journal(tmp_path, fault):
    provider = RichProvider()
    provider.unreadable.add("om_part_0")
    attempts = []
    args, _ = send(
        tmp_path, provider, "测" * 20000, delivery_attempt_recorder=attempts.append
    )
    provider.unreadable.clear()
    options = {}
    if fault == "source":
        options["source_authority_verifier"] = lambda *_: ("Project Review Bot", False)
    elif fault == "destination":
        config = json.loads(Path(args["config_path"]).read_text())
        config["reply"]["chat_id"] = "oc_other"
        Path(args["config_path"]).write_text(json.dumps(config))
    else:
        next(Path(args["project"]).glob("**/manager-delivery/parts/*.json")).unlink()
    assert not resume(args, attempts[0], resume_parts=True, **options)["reply_verified"]
    assert len(provider.sent) == 1


def test_unexpected_preview_encoding_fails_without_recursing_or_text_fallback(tmp_path):
    provider = RichProvider()

    def html_escaped_preview(args):
        result = provider(args)
        if "--dry-run" in args:
            value = json.loads(result["stdout"])
            body = value["api"][0]["body"]
            body["content"] = body["content"].replace("<", "\\u003c")
            return {**result, "stdout": json.dumps(value)}
        return result

    with pytest.raises(LarkOutboundTextError, match="serialization"):
        send(tmp_path, html_escaped_preview, "<" * 6000)
    assert provider.sent == []
