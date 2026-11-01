"""Chat composition and loopback read/recheck for the common background host."""
from __future__ import annotations

from ..extensions.background_services import BackgroundService, BackgroundServices, BackgroundTask, worker_observation

BACKGROUND_SERVICES_PATH = "/api/chat/background-services"


def initialize_chat_background_services(server) -> None:
    server.background_services = BackgroundServices()
    server.background_service_owners = set()


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


def close_chat_background_services(server) -> None:
    server.background_services.close()
    # Directly constructed HTTP fixtures/embedders can still supply old owner
    # attributes without registering. Close them once through the same adapter.
    for attribute in ("codex_account_service", "manager_return_service", "lark_goal_topic_runtime"):
        owner = getattr(server, attribute, None)
        if owner is not None and attribute not in server.background_service_owners:
            owner.close()


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
