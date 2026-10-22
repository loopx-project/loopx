"""Rich-post continuation through the existing Lark part-delivery journal.

The composite locator names private provider IO receipts, not a remote message
or a second Goal/work authority. Each concrete part keeps its own exact receipt.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ...file_lock import exclusive_file_lock
from .event_inbox import load_lark_event_inbox_config
from .manager_reply_delivery import write_delivery
from .presentation.markdown_parts import split_lark_markdown_posts

POST_PARTS_LOCATOR = re.compile(r"^lark-parts:([a-f0-9]{64})$")


def _path(project, config_path, intent_digest):
    config = load_lark_event_inbox_config(project=project, config_path=config_path)
    return (
        Path(config["inbox_path"])
        / "manager-delivery"
        / "parts"
        / f"{intent_digest.removeprefix('sha256:')}.json"
    )


def _batch_attempt(receipt: str, intent_digest: str) -> dict[str, Any]:
    return {
        "schema_version": "manager_return_delivery_attempt_v0",
        "provider": "lark",
        "message_ref": "lark-parts:" + intent_digest.removeprefix("sha256:"),
        "intent_digest": intent_digest,
        "provider_receipt": receipt,
    }


def _result(state, receipt, verified=False, blocker=None):
    return {
        "ok": verified,
        "status": "sent_verified" if verified else "sent_unverified",
        "idempotency_key": receipt,
        "content_format": "markdown",
        "format_degraded": False,
        "delivery_part_count": len(state["delivery_parts"]),
        "delivery_parts_sent": state.get("delivery_parts_sent", 0),
        "delivery_notice_sent": state.get("delivery_part_stall_notice") is True,
        "external_write_performed": bool(
            state.get("delivery_part_attempts") or state.get("delivery_write_attempted")
        ),
        "verification_performed": verified,
        "reply_verified": verified,
        **({"blocker": blocker} if blocker else {}),
    }


def deliver_post_parts(*, receipt, intent_digest, template, reply_text, arguments):
    from .manager_reply_parts import deliver_manager_reply_parts, deliver_stall_notice
    from .inbox_reply import _verified_reply_result

    arguments = {**arguments, "text": reply_text or arguments.get("text", "")}
    path = _path(arguments["project"], arguments["config_path"], intent_digest)
    with exclusive_file_lock(
        path.with_suffix(".lock"), agent_id="lark", operation="reply_parts"
    ):
        if path.is_file():
            state = json.loads(path.read_text(encoding="utf-8"))
            if (
                state.get("intent_digest") != intent_digest
                or state.get("provider_receipt") != receipt
            ):
                raise ValueError("rich reply delivery intent changed")
            expected = reply_text or arguments.get("text", "")
            if state["delivery_parts"] != split_lark_markdown_posts(
                expected, state["request_template"]
            ):
                raise ValueError("rich reply part plan changed")
        else:
            state = {
                "intent_digest": intent_digest,
                "provider_receipt": receipt,
                "delivery_parts": split_lark_markdown_posts(reply_text, template),
                "request_template": dict(template),
                "content_format": "markdown",
            }
            write_delivery(path, state)
        if state.get("delivery_parts_complete"):
            if arguments.get("delivery_attempt_recorder") is not None:
                arguments["delivery_attempt_recorder"](
                    _batch_attempt(receipt, intent_digest)
                )
            return verify_post_parts(
                receipt=receipt,
                intent_digest=intent_digest,
                attempt=_batch_attempt(receipt, intent_digest),
                arguments=arguments,
            )
        recorder = arguments.get("delivery_attempt_recorder")
        outer_hook = arguments.get("before_send")
        if outer_hook and (bind := getattr(outer_hook, "for_destination", None)):
            config = load_lark_event_inbox_config(
                project=arguments["project"], config_path=arguments["config_path"]
            )
            outer_hook = bind(str(config["reply"]["chat_id"]))

        def record(path, value):
            write_delivery(path, value)
            if (
                value.get("delivery_part_attempts")
                or value.get("delivery_write_attempted")
            ) and recorder is not None:
                recorder(_batch_attempt(receipt, intent_digest))

        def options(index):
            final = index == len(state["delivery_parts"]) - 1
            return {
                "source_membership_verifier": arguments.get(
                    "source_membership_verifier"
                ),
                "source_authority_verifier": arguments.get("source_authority_verifier"),
                "before_send": (lambda _part_intent: outer_hook(intent_digest))
                if outer_hook
                else None,
                "finalize_reactions": False,
                "attachment_keys": arguments.get("attachment_keys", ())
                if final
                else (),
                "attachment_names": arguments.get("attachment_names", ())
                if final
                else (),
            }

        result = deliver_manager_reply_parts(
            parts=state["delivery_parts"],
            delivery_state=state,
            delivery_path=path,
            write_delivery=record,
            reply_runner=arguments["runner"],
            root=Path(arguments["project"]),
            config_path=Path(arguments["config_path"]),
            message_id=arguments.get("message_id"),
            content_format="markdown",
            reply_options=options,
        )
        if result is None or result.get("reply_verified") is not True:
            state["delivery_part_stall_count"] = (
                int(state.get("delivery_part_stall_count") or 0) + 1
            )
            notice_options = options(0)
            notice_options.update(attachment_keys=(), attachment_names=())
            notice = deliver_stall_notice(
                delivery_state=state,
                delivery_path=path,
                write_delivery=record,
                reply_runner=arguments["runner"],
                root=Path(arguments["project"]),
                config_path=Path(arguments["config_path"]),
                message_id=arguments.get("message_id"),
                reply_options=notice_options,
            )
            if notice and notice.get("reply_verified") is True:
                state["delivery_part_stall_notice"] = True
            record(path, state)
            return _result(state, receipt, blocker="lark_inbox_reply_not_verified")
        complete = _verified_reply_result(
            project=arguments["project"],
            config_path=arguments["config_path"],
            message_id=arguments.get("message_id"),
            runner=arguments["runner"],
            finalize_reactions=bool(arguments.get("message_id"))
            and arguments.get("finalize_reactions", True),
        )
        return {**_result(state, receipt, True), **complete}


def verify_post_parts(*, receipt, intent_digest, attempt, arguments, resume=False):
    from .inbox_reply import verify_lark_inbox_reply, _verified_reply_result

    if dict(attempt) != _batch_attempt(receipt, intent_digest):
        return {
            "ok": False,
            "verification_performed": True,
            "reply_verified": False,
            "blocker": "provider_delivery_intent_conflict",
        }
    path = _path(arguments["project"], arguments["config_path"], intent_digest)
    if not path.is_file():
        return {
            "ok": False,
            "verification_performed": False,
            "reply_verified": False,
            "blocker": "provider_verification_unavailable",
        }
    state = json.loads(path.read_text(encoding="utf-8"))
    if (
        state.get("intent_digest") != intent_digest
        or state.get("provider_receipt") != receipt
    ):
        return {
            "ok": False,
            "verification_performed": True,
            "reply_verified": False,
            "blocker": "provider_delivery_intent_conflict",
        }
    if state["delivery_parts"] != split_lark_markdown_posts(
        arguments["text"], state["request_template"]
    ):
        return {
            "ok": False,
            "verification_performed": True,
            "reply_verified": False,
            "blocker": "provider_delivery_intent_conflict",
        }
    parts = state["delivery_parts"]
    for index, part in enumerate(parts):
        prior = state.get("delivery_part_attempts", {}).get(str(index))
        if prior is None:
            if state.get("delivery_part_started") == index:
                return {
                    "ok": False,
                    "verification_performed": True,
                    "reply_verified": False,
                    "blocker": "provider_message_missing",
                }
            if resume:
                return deliver_post_parts(
                    receipt=receipt,
                    intent_digest=intent_digest,
                    template={},
                    reply_text="",
                    arguments=arguments,
                )
            return _result(state, receipt, blocker="provider_verification_unavailable")
        final = index == len(parts) - 1
        result = verify_lark_inbox_reply(
            project=arguments["project"],
            config_path=arguments["config_path"],
            message_id=arguments.get("message_id"),
            text=part,
            attempt=prior,
            runner=arguments["runner"],
            source_membership_verifier=arguments.get("source_membership_verifier"),
            source_authority_verifier=arguments.get("source_authority_verifier"),
            finalize_reactions=False,
            attachment_keys=arguments.get("attachment_keys", ()) if final else (),
            attachment_names=arguments.get("attachment_names", ()) if final else (),
        )
        if not result.get("reply_verified"):
            return result
    if resume and state.get("delivery_parts_sent", 0) < len(parts):
        return deliver_post_parts(
            receipt=receipt,
            intent_digest=intent_digest,
            template={},
            reply_text="",
            arguments=arguments,
        )
    return {
        **_result(state, receipt, True),
        **_verified_reply_result(
            project=arguments["project"],
            config_path=arguments["config_path"],
            message_id=arguments.get("message_id"),
            runner=arguments["runner"],
            finalize_reactions=bool(arguments.get("message_id"))
            and arguments.get("finalize_reactions", True),
        ),
    }


def attachment_reply(*, project, config_path, attempt, text):
    """Files belong to the last concrete post, never to the composite locator."""
    match = POST_PARTS_LOCATOR.fullmatch(str(attempt.get("message_ref") or ""))
    if not match:
        return text, attempt
    state = json.loads(
        _path(project, config_path, "sha256:" + match[1]).read_text(encoding="utf-8")
    )
    if state.get("intent_digest") != attempt.get("intent_digest") or state.get(
        "provider_receipt"
    ) != attempt.get("provider_receipt"):
        raise ValueError("rich reply attachment intent changed")
    index = len(state["delivery_parts"]) - 1
    return state["delivery_parts"][index], state["delivery_part_attempts"][str(index)]
