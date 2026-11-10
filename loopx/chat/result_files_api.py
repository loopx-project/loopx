"""Download an immutable returned file through its existing original Chat row."""

from urllib.parse import quote

from ..capabilities.manager_context.roundtrip import read_chat_return_file
from ..control_plane.effect_runtime import effect_runtime_request_scope


class ChatResultFilesRequestMixin:
    def _dispatch_result_file_read(self, path: str) -> bool:
        parts = path.strip("/").split("/")
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
