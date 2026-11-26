"""Workspace reply-image verification, attachment encoding, and capture plans."""
from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote


_REPLY_IMAGE_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
_REPLY_IMAGE_SIGNATURES: dict[str, bytes] = {
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
    "image/gif": b"GIF8",
}
REPLY_IMAGE_NAME_PATTERN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,119}")


def reply_image_directory(turn_id: str) -> Path:
    """Return the sole workspace output directory for this exact Core Turn."""
    if not re.fullmatch(r"[a-f0-9]{32}", turn_id):
        raise ValueError("invalid reply image Turn")
    return Path(".local/loopx-reply-images") / turn_id


def reply_image_workspace(session: Mapping[str, object], turn: Mapping[str, object]) -> Path | None:
    """Local image output belongs to an ordinary, bound-owner Lark project Turn."""
    context_raw = session.get("project_context")
    context: Mapping[str, object] = context_raw if isinstance(context_raw, Mapping) else {}
    workspace_path = context.get("workspace_path")
    if (
        turn.get("origin") != "lark"
        or session.get("session_mode") != "managed_runtime"
        or session.get("agent_id") != "codex"
        or context.get("audience") != "bound_owner"
        or context.get("grant") != "workspace_write"
        or not isinstance(workspace_path, str)
        or not workspace_path
        or session.get("steward_context")
    ):
        return None
    return Path(workspace_path).resolve()


def _check_reply_image(data: bytes, mime: str) -> None:
    from ...chat.attachments import CHAT_IMAGE_MAX_BYTES

    if not data or len(data) > CHAT_IMAGE_MAX_BYTES:
        raise ValueError("reply image is empty or exceeds its size bound")
    sig = _REPLY_IMAGE_SIGNATURES.get(mime)
    valid_sig = (sig is not None and data.startswith(sig)) or (
        mime == "image/webp" and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    )
    if not valid_sig:
        raise ValueError("unsupported reply image bytes")


def reply_image_attachment(data: bytes, mime: str, index: int) -> dict[str, object]:
    """Validate and encode one captured reply image as a bounded Chat attachment."""
    _check_reply_image(data, mime)
    return {
        "id": f"reply-image-{index}",
        "name": f"original-{index}.{mime.split('/')[1]}",
        "mime_type": mime,
        "size": len(data),
        "data_url": f"data:{mime};base64," + base64.b64encode(data).decode("ascii"),
    }


def read_reply_image(workspace: Path, turn_id: str, name: str) -> tuple[str, bytes]:
    """Read and validate one workspace reply image inside the Turn directory."""
    from ...control_plane.collaboration import result_files

    if not REPLY_IMAGE_NAME_PATTERN.fullmatch(name):
        raise ValueError("invalid reply image name")
    mime = _REPLY_IMAGE_TYPES.get(Path(name).suffix.lower())
    if mime is None:
        raise ValueError("unsupported reply image type")
    ref = reply_image_directory(turn_id) / name
    workspace_file = getattr(result_files, "_workspace_file")
    data = workspace_file(workspace, ref)
    if not isinstance(data, bytes):
        raise ValueError("reply image payload must be bytes")
    _check_reply_image(data, mime)
    return mime, data


def capture_reply_image_plan(
    workspace: Path, turn_id: str, targets: Sequence[str],
) -> list[str | dict[str, object] | None]:
    """Pin exact local bytes without keeping host paths in Chat responses."""
    from ...chat.attachments import CHAT_IMAGE_MAX_COUNT, CHAT_IMAGE_MAX_TOTAL_BYTES

    if all(target.startswith("https://") for target in targets):
        return []  # The original remote-image path remains authoritative.
    if len(targets) > CHAT_IMAGE_MAX_COUNT:
        return [None]
    output_dir = workspace / reply_image_directory(turn_id)
    result: list[str | dict[str, object] | None] = []
    total = 0
    for target in targets:
        if target.startswith("https://"):
            result.append(target)
            continue
        try:
            path = Path(unquote(target))
            if path.parent != output_dir or ".." in path.parts:
                raise ValueError("reply image is outside the current Turn's output directory")
            _, data = read_reply_image(workspace, turn_id, path.name)
            total += len(data)
            if total > CHAT_IMAGE_MAX_TOTAL_BYTES:
                raise ValueError("reply images exceed their aggregate size bound")
            result.append({"name": path.name, "sha256": hashlib.sha256(data).hexdigest()})
        except (OSError, ValueError):
            result.append(None)
    return result


def _preparation_image_identity(value: object) -> dict[str, object] | None:
    """Fingerprint already normalized Core images without exporting their bytes."""
    from ...chat.attachments import normalize_chat_image_attachments

    if not isinstance(value, list) or not value:
        return None
    try:
        images = normalize_chat_image_attachments(value)
        if images != value:
            return None
        encoded = json.dumps(images, sort_keys=True, separators=(",", ":")).encode()
        return {"count": len(images), "sha256": hashlib.sha256(encoded).hexdigest()}
    except (ValueError, TypeError, OverflowError):
        return None
