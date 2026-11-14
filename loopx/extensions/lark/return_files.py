"""Lark resource IO for snapshotted result files and canonical App images."""

from __future__ import annotations

import hashlib
import base64
import json
import re
import subprocess
from contextlib import ExitStack, contextmanager
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory

from .cli_resolution import run_lark_command_with_dispatch_guard
from .inbox_reply import _default_runner, _json_object, _message
from .outbound import normalize_lark_outbound_text
from .presentation.markdown_post import lark_markdown_readback_attachment_keys, lark_markdown_readback_matches
from ...chat_attachments import normalize_chat_image_attachments
from ...control_plane.collaboration.inbox import _hash, _read, _root, _write
from ...control_plane.collaboration.result_files import read_result_file
from ...file_lock import exclusive_file_lock


# An upload acknowledgement is the resource prefix plus 1-240 token characters.
# The prefix is a property of the resource kind, not of the caller, so both
# shapes are stated once here instead of being rebuilt at each use site.
RESULT_FILE_KEY_PATTERN = re.compile(r"file_[A-Za-z0-9_-]{1,240}")
CHAT_IMAGE_KEY_PATTERN = re.compile(r"img_[A-Za-z0-9_-]{1,240}")
LARK_APP_SCOPE_NOT_APPLIED = 99991672


def _image_upload_scope_rejected(result):
    """Recognize the CLI's explicit App rejection before resource creation."""
    if result.get("returncode") != 3 or str(result.get("stdout") or "").strip():
        return False
    rejected = _json_object(result.get("stderr"))
    error = rejected.get("error")
    return (
        rejected.get("ok") is False and rejected.get("identity") == "bot"
        and isinstance(error, dict) and error.get("identity") == "bot"
        and error.get("type") == "authorization"
        and error.get("subtype") == "app_scope_not_applied"
        and error.get("code") == LARK_APP_SCOPE_NOT_APPLIED
    )


def _call(args, cwd, *, runner, private_transport, dispatch_guard=None):
    try:
        if private_transport is not None:
            command = [private_transport.cli_bin, *args[1:]]
            if dispatch_guard is not None:
                return run_lark_command_with_dispatch_guard(private_transport.runner, command, cwd, 30, dispatch_guard)
            return private_transport.runner(command, cwd, 30)
        return runner(args) if runner is not None else _default_runner(args, cwd=cwd)
    except (OSError, subprocess.SubprocessError):
        return {"returncode": 1}


def uploaded_result_files(*, root, profile, provider_ref, attachments, runner=None,
                          private_transport=None, before_upload=lambda: True, verify_only=False):
    """Uploads are provider resources; the original message still has one attempt.

    Cached keys are bound to App identity, content and display name. Recovery
    never uploads replacement resources to reconstruct a sent-message intent.
    """
    return _uploaded_resources(root=root, profile=profile, provider_ref=provider_ref, attachments=attachments,
        cache_dir="lark-result-files", resource="files", key_field="file_key",
        key_pattern=RESULT_FILE_KEY_PATTERN,
        read_bytes=lambda attachment: read_result_file(root, attachment),
        upload_data=lambda attachment: {"file_type": "stream", "file_name": attachment["name"]},
        runner=runner, private_transport=private_transport, before_upload=before_upload, verify_only=verify_only)


def _chat_images(attachments):
    images = normalize_chat_image_attachments(attachments)
    for image in images:
        image["raw"] = base64.b64decode(image.pop("data_url").split(",", 1)[1], validate=True)
        image["sha256"] = hashlib.sha256(image["raw"]).hexdigest()
    return images


def uploaded_chat_images(*, root, profile, provider_ref, attachments, private_transport,
                         before_upload, verify_only=False, dispatch_guard=None):
    """Reuse resource IO; never regenerate an unknown upload or sent intent."""
    return _uploaded_resources(root=root, profile=profile, provider_ref=provider_ref,
        attachments=_chat_images(attachments), cache_dir="lark-chat-images", resource="images",
        key_field="image_key", key_pattern=CHAT_IMAGE_KEY_PATTERN,
        read_bytes=lambda image: image["raw"],
        upload_data=lambda _image: {"image_type": "message"}, runner=None,
        private_transport=private_transport, before_upload=before_upload, verify_only=verify_only,
        fence_upload=True, dispatch_guard=dispatch_guard)


@contextmanager
def _authorize_upload_dispatch(guard, cache, saved):
    with ExitStack() as authority:
        try:
            authority.enter_context(guard())
        except (KeyError, OSError, ValueError) as exc:
            # The CLI has yet to start. Only preserve receipts for an earlier
            # attempt, including a known scope denial.
            if saved is None:
                cache.unlink()
            else:
                _write(cache, saved)
            raise ValueError("original upload authority unavailable") from exc
        yield


def _uploaded_resources(*, root, profile, provider_ref, attachments, cache_dir, resource,
                        key_field, key_pattern, read_bytes, upload_data, runner, private_transport,
                        before_upload, verify_only, fence_upload=False, dispatch_guard=None):
    keys = []
    for attachment in attachments:
        identity = {"profile": profile, "provider_ref": provider_ref,
                    "sha256": attachment["sha256"], "name": attachment["name"], "size": attachment["size"]}
        cache = _root(root) / cache_dir / (_hash(identity) + ".json")
        with exclusive_file_lock(cache):
            saved = _read(cache) if cache.exists() else None
            if saved is not None:
                if saved.get("identity") != identity:
                    raise ValueError("resource upload identity conflict or acknowledgement unavailable")
                if key_pattern.fullmatch(str(saved.get(key_field, ""))):
                    keys.append(saved[key_field])
                    continue
                # Prior timeouts and missing acknowledgements remain fenced.
                if not (fence_upload and saved == {"identity": identity, "upload_rejected_scope": LARK_APP_SCOPE_NOT_APPLIED}):
                    raise ValueError("resource upload identity conflict or acknowledgement unavailable")
            if verify_only:
                raise ValueError("sent result file resource record unavailable")
            raw = read_bytes(attachment)
            if not before_upload():
                raise ValueError("result file return authority revoked")
            with TemporaryDirectory(prefix="loopx-lark-result-") as temporary:
                cwd = Path(temporary)
                (cwd / "resource").write_bytes(raw)
                args = ["lark-cli", "--profile", profile, "im", resource, "create",
                        "--data", json.dumps(upload_data(attachment)),
                        "--file", "./resource", "--as", "bot", "--format", "json"]
                preview = _call([*args, "--dry-run"], cwd, runner=runner, private_transport=private_transport)
                if preview.get("returncode") != 0:
                    raise ValueError("result file upload preflight unavailable")
                if not before_upload():
                    raise ValueError("result file return authority revoked")
                if fence_upload:
                    _write(cache, {"identity": identity, "upload_started": True})
                guard = partial(_authorize_upload_dispatch, dispatch_guard, cache, saved) if dispatch_guard else None
                uploaded = _call(args, cwd, runner=runner, private_transport=private_transport,
                                 dispatch_guard=guard)
                if fence_upload and _image_upload_scope_rejected(uploaded):
                    _write(cache, {"identity": identity, "upload_rejected_scope": LARK_APP_SCOPE_NOT_APPLIED})
                    raise ValueError("result file upload unavailable")
                payload = _json_object(uploaded.get("stdout"))
                data = payload.get("data")
                key = data.get(key_field) if isinstance(data, dict) else None
                if uploaded.get("returncode") != 0 or payload.get("ok") is False or not key_pattern.fullmatch(str(key or "")):
                    raise ValueError("result file upload unavailable")
            _write(cache, {"identity": identity, key_field: key})
            keys.append(key)
    return tuple(keys)


def verify_result_files(*, profile, message_id, text, attachments, keys, runner=None, private_transport=None):
    """Read back actual bytes; matching a filename or text tag is insufficient."""
    with TemporaryDirectory(prefix="loopx-lark-result-readback-") as temporary:
        cwd = Path(temporary).resolve()
        result = _call(["lark-cli", "--profile", profile, "im", "+messages-mget",
                        "--message-ids", message_id, "--as", "bot", "--no-reactions", "--format", "json"],
                       cwd, runner=runner, private_transport=private_transport)
        if result.get("returncode") != 0:
            return {"reply_verified": False, "verification_performed": False, "blocker": "provider_verification_unavailable"}
        message = _message(_json_object(result.get("stdout")), message_id)
        resources = lark_markdown_readback_attachment_keys(
            text=normalize_lark_outbound_text(text, limit=None, preserve_format=True), message=message or {},
            attachment_keys=keys, attachment_names=tuple(attachment["name"] for attachment in attachments))
        if resources is None:
            return {"reply_verified": False, "verification_performed": True, "blocker": "provider_delivery_mismatch"}
        return _verify_resource_bytes(cwd=cwd, profile=profile, message_id=message_id, attachments=attachments,
            keys=resources, resource_type="file", runner=runner, private_transport=private_transport)


def verify_chat_images(*, profile, message_id, text, attachments, keys, private_transport, before_read):
    """Require the exact ordered post and independently downloaded image bytes."""
    with TemporaryDirectory(prefix="loopx-lark-image-readback-") as temporary:
        cwd = Path(temporary).resolve()
        if not before_read():
            return {"reply_verified": False, "verification_performed": False, "blocker": "provider_verification_unavailable"}
        result = _call(["lark-cli", "--profile", profile, "im", "+messages-mget",
                        "--message-ids", message_id, "--as", "bot", "--no-reactions", "--format", "json"],
                       cwd, runner=None, private_transport=private_transport)
        if result.get("returncode") != 0:
            return {"reply_verified": False, "verification_performed": False, "blocker": "provider_verification_unavailable"}
        message = _message(_json_object(result.get("stdout")), message_id)
        if not lark_markdown_readback_matches(text=normalize_lark_outbound_text(text, limit=None, preserve_format=True),
                                             message=message or {}, image_keys=keys):
            return {"reply_verified": False, "verification_performed": True, "blocker": "provider_delivery_mismatch"}
        return _verify_resource_bytes(cwd=cwd, profile=profile, message_id=message_id,
            attachments=_chat_images(attachments), keys=keys, resource_type="image", runner=None,
            private_transport=private_transport, before_read=before_read)


def _verify_resource_bytes(*, cwd, profile, message_id, attachments, keys, resource_type, runner,
                           private_transport, before_read=lambda: True):
    for index, (attachment, key) in enumerate(zip(attachments, keys, strict=True)):
        # A suffix keeps lark-cli from inferring one from remote metadata.
        # The fixed local name still belongs to this temporary directory.
        output = f"./resource-{index}.bin"
        if not before_read():
            return {"reply_verified": False, "verification_performed": False, "blocker": "provider_verification_unavailable"}
        result = _call(["lark-cli", "--profile", profile, "im", "+messages-resources-download",
                       "--message-id", message_id, "--file-key", key, "--type", resource_type, "--output", output,
                       "--as", "bot", "--format", "json"], cwd, runner=runner, private_transport=private_transport)
        if result.get("returncode") != 0:
            return {"reply_verified": False, "verification_performed": False, "blocker": "provider_verification_unavailable"}
        path = cwd / output
        if path.is_symlink() or not path.is_file() or path.stat().st_size != attachment["size"]:
            return {"reply_verified": False, "verification_performed": True, "blocker": "provider_delivery_mismatch"}
        # Reuse the host's bounded, regular-file reader. No remote filename
        # or provider-returned destination can choose a filesystem address.
        from ...control_plane.collaboration.result_files import _read_regular

        if hashlib.sha256(_read_regular(path)).hexdigest() != attachment["sha256"]:
            return {"reply_verified": False, "verification_performed": True, "blocker": "provider_delivery_mismatch"}
    return {"reply_verified": True, "verification_performed": True}
