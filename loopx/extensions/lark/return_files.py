"""Lark resource IO for explicit, snapshotted bound-owner result files."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from .inbox_reply import _default_runner, _json_object, _message
from .outbound import normalize_lark_outbound_text
from .presentation.markdown_post import lark_markdown_readback_attachment_keys
from ...control_plane.collaboration.inbox import _hash, _read, _root, _write
from ...control_plane.collaboration.result_files import read_result_file
from ...file_lock import exclusive_file_lock


def _call(args, cwd, *, runner, private_transport):
    try:
        if private_transport is not None:
            return private_transport.runner([private_transport.cli_bin, *args[1:]], cwd, 30)
        return runner(args) if runner is not None else _default_runner(args, cwd=cwd)
    except (OSError, subprocess.SubprocessError):
        return {"returncode": 1}


def uploaded_result_files(*, root, profile, provider_ref, attachments, runner=None,
                          private_transport=None, before_upload=lambda: True, verify_only=False):
    """Uploads are provider resources; the original message still has one attempt.

    Cached keys are bound to App identity, content and display name. Recovery
    never uploads replacement resources to reconstruct a sent-message intent.
    """
    keys = []
    for attachment in attachments:
        identity = {"profile": profile, "provider_ref": provider_ref,
                    "sha256": attachment["sha256"], "name": attachment["name"], "size": attachment["size"]}
        cache = _root(root) / "lark-result-files" / (_hash(identity) + ".json")
        with exclusive_file_lock(cache):
            saved = _read(cache) if cache.exists() else None
            if saved is not None:
                if saved.get("identity") != identity or not re.fullmatch(r"file_[A-Za-z0-9_-]{1,240}", str(saved.get("file_key", ""))):
                    raise ValueError("result file upload identity conflict")
                keys.append(saved["file_key"])
                continue
            if verify_only:
                raise ValueError("sent result file resource record unavailable")
            raw = read_result_file(root, attachment)
            if not before_upload():
                raise ValueError("result file return authority revoked")
            with TemporaryDirectory(prefix="loopx-lark-result-") as temporary:
                cwd = Path(temporary)
                (cwd / "resource").write_bytes(raw)
                args = ["lark-cli", "--profile", profile, "im", "files", "create",
                        "--data", json.dumps({"file_type": "stream", "file_name": attachment["name"]}),
                        "--file", "./resource", "--as", "bot", "--format", "json"]
                preview = _call([*args, "--dry-run"], cwd, runner=runner, private_transport=private_transport)
                if preview.get("returncode") != 0:
                    raise ValueError("result file upload preflight unavailable")
                if not before_upload():
                    raise ValueError("result file return authority revoked")
                uploaded = _call(args, cwd, runner=runner, private_transport=private_transport)
                payload = _json_object(uploaded.get("stdout"))
                key = (payload.get("data") or {}).get("file_key")
                if uploaded.get("returncode") != 0 or not re.fullmatch(r"file_[A-Za-z0-9_-]{1,240}", str(key or "")):
                    raise ValueError("result file upload unavailable")
            _write(cache, {"identity": identity, "file_key": key})
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
        for index, (attachment, key) in enumerate(zip(attachments, resources, strict=True)):
            # A suffix keeps lark-cli from inferring one from remote metadata.
            # The fixed local name still belongs to this temporary directory.
            output = f"./resource-{index}.bin"
            result = _call(["lark-cli", "--profile", profile, "im", "+messages-resources-download",
                           "--message-id", message_id, "--file-key", key, "--type", "file", "--output", output,
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
