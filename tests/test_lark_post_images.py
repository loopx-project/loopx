"""Exact post image content survives preview, readback and multipart sizing."""
import json

import pytest

from loopx.extensions.lark.presentation.markdown_post import (
    lark_markdown_post_content, lark_markdown_preview_matches, lark_markdown_readback_matches,
)
from loopx.extensions.lark.presentation.markdown_parts import post_request_bytes

TEXT = "**Image caption**\n\n```python\nprint('keep indentation')\n```"
KEYS = ("img_first", "img_second")


def test_exact_images_match_raw_post_and_real_cli_rendering():
    content = lark_markdown_post_content(TEXT, image_keys=KEYS)
    assert lark_markdown_preview_matches(text=TEXT, payload={"api": [{"body": {
        "msg_type": "post", "content": content}}]}, image_keys=KEYS)
    assert lark_markdown_readback_matches(text=TEXT, message={"msg_type": "post", "body": {"content": content}}, image_keys=KEYS)
    rendered = TEXT + "\n\n![Image](img_first)\n![Image](img_second)"
    assert lark_markdown_readback_matches(text=TEXT, message={"msg_type": "post", "content": rendered}, image_keys=KEYS)
    assert not lark_markdown_readback_matches(text=TEXT, message={"msg_type": "post", "content": rendered})


@pytest.mark.parametrize("changed", [(), ("img_first",), ("img_second", "img_first"),
    ("img_other", "img_second"), (*KEYS, "img_extra")])
def test_readback_rejects_missing_reordered_replaced_or_extra_images(changed):
    content = lark_markdown_post_content(TEXT, image_keys=changed)
    for actual in [{"body": {"content": content}},
                   {"content": TEXT + "\n" + "\n".join(f"![Image]({key})" for key in changed)}]:
        assert not lark_markdown_readback_matches(text=TEXT, message={"msg_type": "post", **actual}, image_keys=KEYS)


def test_hidden_image_node_fields_or_caption_lookalikes_cannot_verify():
    content = json.loads(lark_markdown_post_content(TEXT, image_keys=KEYS))
    content["zh_cn"]["content"][1][0]["extra"] = "changed"
    assert not lark_markdown_readback_matches(text=TEXT,
        message={"msg_type": "post", "body": {"content": content}}, image_keys=KEYS)
    assert not lark_markdown_readback_matches(text=TEXT,
        message={"msg_type": "text", "content": TEXT + "\n![Image](img_first)\n![Image](img_second)"}, image_keys=KEYS)


def test_multipart_envelope_budget_keeps_image_rows_for_mapping_and_string_templates():
    content = json.loads(lark_markdown_post_content(TEXT, image_keys=KEYS))
    plain = {"msg_type": "post", "content": lark_markdown_post_content(TEXT)}
    image = {"msg_type": "post", "content": json.dumps(content, ensure_ascii=False, separators=(",", ":"))}
    assert post_request_bytes(TEXT, image) > post_request_bytes(TEXT, plain)
    assert post_request_bytes(TEXT, {**image, "content": content}) == post_request_bytes(TEXT, image)
