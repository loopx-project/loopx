"""Loopback transport for Goal-owned private content compositions."""

from __future__ import annotations

import mimetypes
import hashlib
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..capabilities.content_ops.media import MediaWorkspace
from ..capabilities.content_ops.generation import GenerationWorkspace
from ..capabilities.content_ops.images import ImageWorkspace
from ..feedback import validate_goal_id
from ..control_plane.content_digest import BARE_SHA256_PATTERN
from .goal_source_routing import source_registry_for_goal

CONTENT_MEDIA_PATH = "/api/chat/content-media"


class ContentMediaRequestMixin:
    server: Any
    path: str

    def _media_workspace(self, goal_id: str) -> MediaWorkspace:
        validate_goal_id(goal_id)
        selected = getattr(self.server, "selected_goal_id", None)
        if selected and selected != goal_id:
            raise ValueError("Goal is outside this workspace")
        source = source_registry_for_goal(self.server.registry_path, goal_id)
        return MediaWorkspace(
            registry_path=Path(source),
            goal_id=goal_id,
            runtime_root=Path(self.server.runtime_root),
        )

    def _content_media_request(self) -> None:
        try:
            if urlparse(self.path).query:
                raise ValueError("Use the request body")
            body = self._read_json(max_bytes=4096)
            if set(body) - {"goal_id", "action", "composition_id", "generation_id", "image_id", "expected_revision"}:
                raise ValueError("Unsupported fields")
            workspace = self._media_workspace(body["goal_id"])
            action = body["action"]
            if action == "image_list" and set(body) == {"goal_id", "action"}:
                result = ImageWorkspace(workspace).list()
            elif action in {"image_submit", "image_collect"} and set(body) == {"goal_id", "action", "image_id", "expected_revision"}:
                revision = body["expected_revision"]
                if isinstance(revision, bool) or not isinstance(revision, int):
                    raise ValueError("Read the current image revision")
                result = ImageWorkspace(workspace).operate(body["image_id"], action.removeprefix("image_"), expected_revision=revision)
            elif action == "generation_list" and set(body) == {"goal_id", "action"}:
                result = GenerationWorkspace(workspace).list()
            elif action in {"generation_submit", "generation_check", "generation_cancel", "generation_collect"} and set(body) == {"goal_id", "action", "generation_id", "expected_revision"}:
                revision = body["expected_revision"]
                if isinstance(revision, bool) or not isinstance(revision, int):
                    raise ValueError("Read the current revision")
                result = GenerationWorkspace(workspace).operate(body["generation_id"], action.removeprefix("generation_"), expected_revision=revision)
            elif action == "list" and set(body) == {"goal_id", "action"}:
                result = workspace.list()
            elif action == "inspect" and set(body) == {
                "goal_id",
                "action",
                "composition_id",
            }:
                result = workspace.inspect(body["composition_id"])
            elif action in {"render", "cancel"} and set(body) == {
                "goal_id",
                "action",
                "composition_id",
                "expected_revision",
            }:
                revision = body["expected_revision"]
                if isinstance(revision, bool) or not isinstance(revision, int):
                    raise ValueError("Read the current revision")
                method = workspace.render if action == "render" else workspace.cancel
                result = method(
                    body["composition_id"],
                    expected_revision=revision,
                    **({"background": True} if action == "render" else {}),
                )
            else:
                raise ValueError("Unsupported operation")
            self._send_json(result)
        except (OSError, ValueError, TypeError, KeyError, RuntimeError):
            self._send_error(
                "Media request unavailable. Reopen the selected Goal and media item, then retry.",
                status=400,
            )

    def _content_media_file(self) -> None:
        try:
            query = parse_qs(urlparse(self.path).query, strict_parsing=True, keep_blank_values=True)
            # Existing links retain their verified current-file read. New artifact
            # links pin both revision and bytes; a stale link never reads a replacement.
            version_keys = {"revision", "sha256"}
            pinned = bool(set(query) & version_keys)
            fields = set(query) - version_keys
            if fields not in ({"goal_id", "composition_id", "file"}, {"goal_id", "generation_id", "file"}, {"goal_id", "image_id", "file"}) or any(
                len(v) != 1 for v in query.values()
            ):
                raise ValueError("Invalid file request")
            if pinned and (not version_keys <= set(query)
                           or not re.fullmatch(r"[1-9][0-9]*", query["revision"][0])
                           or not BARE_SHA256_PATTERN.fullmatch(query["sha256"][0])):
                raise ValueError("Invalid artifact version")
            workspace = self._media_workspace(query["goal_id"][0])
            name = query["file"][0]
            owner: MediaWorkspace | ImageWorkspace | GenerationWorkspace
            if "image_id" in query:
                owner, ident = ImageWorkspace(workspace), query["image_id"][0]
            elif "generation_id" in query:
                owner, ident = GenerationWorkspace(workspace), query["generation_id"][0]
            else:
                owner, ident = workspace, query["composition_id"][0]
            path = owner.file(ident, name, expected_revision=int(query["revision"][0]) if pinned else None)
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as source:
                if pinned:
                    if hashlib.file_digest(source, "sha256").hexdigest() != query["sha256"][0]:
                        raise ValueError("Artifact bytes changed")
                    source.seek(0)
                size = os.fstat(source.fileno()).st_size
                start, end, status = 0, size - 1, 200
                header = self.headers.get("Range")
                if header:
                    match = re.fullmatch(r"bytes=(\d+)-(\d*)", header)
                    if not match:
                        raise ValueError("Unsupported byte range")
                    start, end = (
                        int(match[1]),
                        min(int(match[2]) if match[2] else size - 1, size - 1),
                    )
                    if not 0 <= start <= end < size:
                        raise ValueError("Invalid byte range")
                    status = 206
                self.send_response(status)
                self.send_header(
                    "Content-Type",
                    "text/vtt; charset=utf-8"
                    if name.endswith(".vtt")
                    else mimetypes.guess_type(name)[0] or "application/octet-stream",
                )
                self.send_header("Content-Length", str(end - start + 1))
                self.send_header("Content-Disposition", f'inline; filename="{name}"')
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                self.send_header("X-Content-Type-Options", "nosniff")
                if status == 206:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                source.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = source.read(min(remaining, 65536))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return
        except (OSError, ValueError, TypeError, KeyError):
            self._send_error("Verified media file unavailable.", status=404)
