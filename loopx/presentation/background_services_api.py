"""Chat composition and loopback read/recheck for the common background host."""
from __future__ import annotations

from ..extensions.background_services import BackgroundService, BackgroundTask, worker_observation

BACKGROUND_SERVICES_PATH = "/api/chat/background-services"


def register_chat_background_services(server) -> None:
    accounts = server.codex_account_service
    returns = server.manager_return_service
    lark = server.lark_goal_topic_runtime
    runtime = server.background_services
    runtime.register(BackgroundService("codex-accounts", (
        BackgroundTask("codex-observation", accounts.background_observation),
    ), accounts.reconcile_background, accounts.close))
    runtime.register(BackgroundService("manager-context", (
        BackgroundTask("manager-returns", lambda: worker_observation(returns._return_worker)),
        BackgroundTask("manager-executions", lambda: worker_observation(returns._execution_worker)),
    ), returns.start, returns.close))
    runtime.register(BackgroundService("lark", (
        BackgroundTask("lark-delivery", lambda: worker_observation(lark._delivery_worker,
            enabled=lark.private_conversations is not None)),
    ), lark.start, lark.close))
    server.background_service_owners = {"codex_account_service", "manager_return_service", "lark_goal_topic_runtime"}


class BackgroundServicesRequestMixin:
    def _background_services_read(self):
        self._send_json(self.server.background_services.snapshot())

    def _background_services_recheck(self):
        try:
            valid = self._read_json() == {"operation": "recheck"}
        except (ValueError, TypeError):
            valid = False
        if not valid:
            return self._send_json({"ok": False, "code": "invalid_request"}, status=400)
        self.server.background_services.reconcile()
        self._send_json(self.server.background_services.snapshot())
