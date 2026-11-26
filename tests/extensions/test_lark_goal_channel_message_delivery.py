"""Provider-rendered confirmation text must preserve its visible terms."""

from copy import deepcopy

from loopx.extensions.lark.card_callback import callback_card_content_matches
from loopx.extensions.lark.goal_channel_message_delivery import (
    message_card_matches,
    normalized_card_text,
)
from loopx.extensions.lark.presentation.message_card import (
    build_lark_markdown_reply_card,
)


def confirmation_card():
    return {
        "schema": "2.0",
        "header": {"title": {"tag": "plain_text", "content": "Review request"}},
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": "Result: not&#95;executed &amp; retained",
                },
                {
                    "tag": "column_set",
                    "columns": [
                        {
                            "tag": "column",
                            "elements": [
                                {
                                    "tag": "button",
                                    "text": {
                                        "tag": "plain_text",
                                        "content": "Continue",
                                    },
                                    "confirm": {
                                        "title": {
                                            "tag": "plain_text",
                                            "content": "Confirm request?",
                                        },
                                        "text": {
                                            "tag": "plain_text",
                                            "content": "Terms cannot change.",
                                        },
                                    },
                                    "behaviors": [
                                        {
                                            "type": "callback",
                                            "value": {"decision": "confirm"},
                                        }
                                    ],
                                }
                            ],
                        },
                        {
                            "tag": "column",
                            "elements": [
                                {
                                    "tag": "button",
                                    "text": {"tag": "plain_text", "content": "Reject"},
                                }
                            ],
                        },
                    ],
                },
            ],
        },
    }


PROVIDER_CONTENT = (
    '<card title="Review request">\n'
    "Result: not_executed & retained\n"
    '[Continue](confirm:"Confirm request?: Terms cannot change.") [Reject]\n'
    "</card>"
)


def test_native_user_dsl_preserves_confirmation_and_rendered_entities():
    card = confirmation_card()
    assert normalized_card_text(card) == PROVIDER_CONTENT
    assert message_card_matches({"content": PROVIDER_CONTENT}, card)
    assert callback_card_content_matches(PROVIDER_CONTENT, card)
    # Visible equality still cannot authenticate hidden callback terms in history.
    assert not message_card_matches(
        {"content": PROVIDER_CONTENT}, card, allow_normalized=False
    )


def test_changed_visible_terms_or_confirmation_are_rejected():
    for changed in (
        PROVIDER_CONTENT.replace("not_executed", "filled"),
        PROVIDER_CONTENT.replace("Terms cannot change.", "Terms may change."),
        PROVIDER_CONTENT.replace(
            '(confirm:"Confirm request?: Terms cannot change.")', ""
        ),
    ):
        assert not message_card_matches({"content": changed}, confirmation_card())
        assert not callback_card_content_matches(changed, confirmation_card())


def test_unsupported_confirmation_and_plain_text_are_not_silently_normalized():
    card = deepcopy(confirmation_card())
    card["body"]["elements"][1]["columns"][0]["elements"][0]["confirm"] = {}
    assert normalized_card_text(card) is None
    plain = {
        "schema": "2.0",
        "header": {"title": {"content": "Title"}},
        "body": {"elements": [{"tag": "plain_text", "content": "a &amp; b"}]},
    }
    assert normalized_card_text(plain) == '<card title="Title">\na &amp; b\n</card>'


def test_legacy_report_readback_matches_all_visible_paragraphs_in_order():
    card = build_lark_markdown_reply_card("First", title="Report", footer="Details")
    card["elements"][1:1] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": text}}
        for text in ("Second", "Third")
    ]
    rendered = '<card title="Report">\nFirst\nSecond\nThird\n---\n📝 Details\n</card>'
    assert message_card_matches({"content": rendered}, card)
    assert not message_card_matches({"content": rendered.replace("Second\n", "")}, card)
    assert not message_card_matches(
        {"content": rendered.replace("Second\nThird", "Third\nSecond")}, card
    )
    assert not message_card_matches({"content": rendered}, card, allow_normalized=False)

    del card["elements"][-2:]
    assert (
        normalized_card_text(card)
        == '<card title="Report">\nFirst\nSecond\nThird\n</card>'
    )
    card["elements"].append({"tag": "img", "img_key": "example"})
    assert normalized_card_text(card) is None
