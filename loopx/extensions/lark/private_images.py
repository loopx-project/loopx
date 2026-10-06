"""Bounded image IO for a message already verified under its receiving App.

Resource keys come from lark-cli's canonical message rendering; the provider
download endpoint checks that each key belongs to this exact message. Core's
existing image normalization, Session and Turn remain the only model boundary.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ...chat_attachments import (
    CHAT_IMAGE_MAX_BYTES, CHAT_IMAGE_MAX_COUNT, CHAT_IMAGE_MAX_TOTAL_BYTES, normalize_chat_image_attachments,
)
from .goal_channel_transport import call, json_payload, lark_args

_IMAGE = re.compile(r"(?:!\[[^\]]*\]\(|\[Image: *)(img_[A-Za-z0-9_-]+)[)\]]")
_OTHER_RESOURCE = re.compile(r"<(?:file|folder|audio|video|media)\b")


def private_message_caption(content: str) -> str:
    """Control parsing reads the caption; model input keeps image placeholders."""
    return _IMAGE.sub("", content).strip()


def private_message_images(*, content: str, message_type: str, message_id: str,
                           profile: str, cli_bin: str, runner: Any) -> tuple[str, list[dict[str, Any]]]:
    """Read all images or reject the whole message; never execute a partial post."""
    if _OTHER_RESOURCE.search(content):
        raise ValueError("这条消息包含暂不支持的文件或音视频，尚未提交执行。请将文字与图片单独发送。")
    keys = list(dict.fromkeys(_IMAGE.findall(content)))
    if message_type == "image" and not keys:
        raise ValueError("未能读取这张图片的资源信息，尚未提交执行。请重新发送图片。")
    if len(keys) > CHAT_IMAGE_MAX_COUNT:
        raise ValueError("一次最多支持 4 张图片，尚未提交执行。请分开发送。")
    attachments = []
    total = 0
    with TemporaryDirectory(prefix="loopx-lark-images-") as temporary:
        root = Path(temporary).resolve()
        for index, key in enumerate(keys, 1):
            result = call(lambda args, _cwd, timeout: runner(args, root, timeout),
                lark_args(cli_bin=cli_bin, profile=profile, tail=["im", "+messages-resources-download",
                    "--message-id", message_id, "--file-key", key, "--type", "image", "--as", "bot",
                    "--output", f"./image-{index}", "--format", "json"]))
            payload = json_payload(result)
            if result.get("returncode") != 0 or payload.get("ok") is not True:
                raise ValueError("图片下载失败，尚未提交执行。请重发；若仍失败，请检查此 App 的消息读取权限。")
            data = payload.get("data") or {}
            if not isinstance(data, dict):
                raise ValueError("图片下载结果不可用，尚未提交执行。请重新发送。")
            path = Path(str(data.get("saved_path") or ""))
            path = path if path.is_absolute() else root / path
            if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
                raise ValueError("图片下载结果不可用，尚未提交执行。请重新发送。")
            try:
                if path.stat().st_size > CHAT_IMAGE_MAX_BYTES:
                    raise ValueError("单张图片最多支持 5 MB，尚未提交执行。请压缩后重发。")
                with path.open("rb") as stream:
                    raw = stream.read(CHAT_IMAGE_MAX_BYTES + 1)
            except OSError as exc:
                raise ValueError("图片下载结果不可读取，尚未提交执行。请重新发送。") from exc
            if len(raw) > CHAT_IMAGE_MAX_BYTES:
                raise ValueError("单张图片最多支持 5 MB，尚未提交执行。请压缩后重发。")
            total += len(raw)
            if total > CHAT_IMAGE_MAX_TOTAL_BYTES:
                raise ValueError("图片总量超过限制（最多 12 MB），尚未提交执行。请分开发送。")
            if raw.startswith(b"\x89PNG\r\n\x1a\n"):
                mime = "image/png"
            elif raw.startswith(b"\xff\xd8\xff"):
                mime = "image/jpeg"
            elif raw.startswith((b"GIF87a", b"GIF89a")):
                mime = "image/gif"
            elif raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
                mime = "image/webp"
            else:
                raise ValueError("支持 PNG、JPEG、GIF 和 WebP 图片；这份资源尚未提交执行。")
            attachments.append({"id": f"lark-image-{index}", "name": f"image-{index}", "mime_type": mime,
                "data_url": f"data:{mime};base64," + base64.b64encode(raw).decode("ascii"), "size": len(raw)})
    try:
        attachments = normalize_chat_image_attachments(attachments)
    except ValueError as exc:
        raise ValueError("图片总量超过限制（最多 12 MB），尚未提交执行。请分开发送。") from exc
    text = _IMAGE.sub(lambda match: f"[图片 {keys.index(match[1]) + 1}]", content).strip()
    return text or "请查看这张图片。", attachments
