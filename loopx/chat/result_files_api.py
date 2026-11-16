"""Read media from its original Chat message."""

import re
from base64 import b64decode
from hashlib import sha256
from urllib.parse import quote, unquote

from ..capabilities.manager_context.roundtrip import read_chat_return_file
from ..chat_attachments import CHAT_IMAGE_MAX_BYTES, CHAT_IMAGE_TYPES
from ..control_plane.content_digest import BARE_SHA256_PATTERN
from ..control_plane.effect_runtime import effect_runtime_request_scope


def is_bound_steward_history(session):
    context = session.get("steward_context") if session else None
    return isinstance(context, dict) and context.get("kind") == "bound_steward"


def _inline_image(attachment):
    if not isinstance(attachment, dict):
        return None
    mime, data, size = (attachment.get(k) for k in ("mime_type", "data_url", "size"))
    if (not isinstance(mime, str) or mime not in CHAT_IMAGE_TYPES or not isinstance(data, str)
            or type(size) is not int or not 0 < size <= CHAT_IMAGE_MAX_BYTES
            or not data.startswith(f"data:{mime};base64,")
            or len(data.partition(",")[2]) != ((size + 2) // 3) * 4):
        return None
    return mime, data, size


def link_chat_history_images(session_id, messages):
    """Project history images as scoped URLs without changing the stored rows."""
    result = []
    for message in messages:
        attachments = message.get("attachments")
        if not isinstance(attachments, list):
            result.append(message)
            continue
        projected = []
        for index, attachment in enumerate(attachments):
            image = _inline_image(attachment)
            if image is None:
                projected.append(attachment)
                continue
            _, data, _ = image
            # The URL changes if the image bytes change, so the existing visible
            # page digest and history resume proof still bind the original image.
            identity = sha256(data.encode()).hexdigest()
            route = (f"/api/chat/sessions/{quote(session_id, safe='')}/messages/"
                     f"{quote(message['message_id'], safe='')}/images/{index}/{identity}")
            projected.append({**attachment, "data_url": route})
        result.append({**message, "attachments": projected})
    return result


def read_chat_history_image(store, session_id, message_id, index, identity):
    session = store.load_session(session_id)
    if not is_bound_steward_history(session):
        raise KeyError("session unavailable")
    attachments = store.read_history_message(session_id, message_id).get("attachments")
    if not isinstance(attachments, list) or not 0 <= index < len(attachments):
        raise KeyError("image unavailable")
    image = _inline_image(attachments[index])
    if image is None:
        raise KeyError("image unavailable")
    mime, data, size = image
    if sha256(data.encode()).hexdigest() != identity:
        raise KeyError("image version unavailable")
    body = b64decode(data.partition(",")[2], validate=True)
    if len(body) != size:
        raise KeyError("image unavailable")
    return mime, body


class ChatResultFilesRequestMixin:
    def _link_history_images(self, session_id, session, selection, rows):
        if (self.headers.get("X-LoopX-History-Images") == "linked"
                and selection["message_limit"] is not None and selection["turn_id"] is None
                and is_bound_steward_history(session)):
            return link_chat_history_images(session_id, rows)
        return rows

    def _dispatch_result_file_read(self, path: str) -> bool:
        parts = path.strip("/").split("/")
        if (len(parts) == 9 and parts[:3] == ["api", "chat", "sessions"]
                and parts[4] == "messages" and parts[6] == "images"):
            return self._serve_chat_history_image(parts)
        if (len(parts) != 8 or parts[:3] != ["api", "chat", "sessions"]
                or parts[4] != "messages" or parts[6] != "files"):
            return False
        if not self._require_loopback_origin():
            return True
        try:
            with effect_runtime_request_scope():
                attachment, body = read_chat_return_file(
                    self.server.runtime_controller.coordination_runtime_root,
                    self.server.chat_store, parts[3], parts[5], parts[7], registry=self.server.registry_path)
        except KeyError:
            self._send_error("Returned file was not found in this conversation.", status=404)
        except (OSError, ValueError, RuntimeError):
            self._send_error("Returned file is temporarily unavailable.", status=503)
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(attachment["name"], safe=""))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
        return True

    def _serve_chat_history_image(self, parts) -> bool:
        if not self._require_loopback_origin():
            return True
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self._send_error("Conversation images are only available to the original App.", status=403)
            return True
        try:
            if not re.fullmatch(r"[0-9]{1,3}", parts[7]) or not BARE_SHA256_PATTERN.fullmatch(parts[8]):
                raise KeyError("image unavailable")
            mime, body = read_chat_history_image(
                self.server.chat_store, unquote(parts[3]), unquote(parts[5]), int(parts[7]), parts[8])
        except (KeyError, ValueError):
            self._send_error("Image was not found in this conversation.", status=404)
        except (OSError, RuntimeError):
            self._send_error("Conversation image is temporarily unavailable.", status=503)
        else:
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
        return True
