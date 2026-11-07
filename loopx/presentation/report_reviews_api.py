"""Owner-local report reading; all review writes use existing Todo actions."""
from urllib.parse import parse_qs, urlparse

from ..capabilities.periodic_report.review import report_review_inventory, read_report_review
from ..control_plane.coordination.local_authority import CanonicalAuthorityRequired


class ReportReviewsRequestMixin:
    def _report_reviews(self, todo_id=None):
        query = parse_qs(urlparse(self.path).query)
        goal_id = query.get("goal_id", [""])[0]
        try:
            root = self._goal_result_scope(goal_id)
            if root is None:
                return
            if todo_id:
                result = read_report_review(registry_path=self.server.registry_path,
                    runtime_root=root, goal_id=goal_id, todo_id=todo_id)
            else:
                result = self.server.completed_todo_pages.page(scope=("report_reviews", goal_id),
                    cursor=query.get("cursor", [""])[0], load=lambda: report_review_inventory(
                        registry_path=self.server.registry_path, runtime_root=root, goal_id=goal_id))
            self._send_json(result)
        except CanonicalAuthorityRequired as exc:
            if todo_id:
                self._send_error(str(exc), status=409, error_code=exc.code)
            else:
                self._send_json({"ok": True, "available": False, "reason_code": exc.code,
                    "items": [], "total": 0, "next_cursor": None})
        except (ValueError, KeyError, TypeError):
            self._send_error("Report or review state changed. Refresh the original material.", status=409)
        except (OSError, RuntimeError):
            self._send_error("Report review is unavailable. Retry the original read.", status=503)
