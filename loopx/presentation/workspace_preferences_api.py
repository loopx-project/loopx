"""Private presentation preferences shared by App/Chat service origins.

This is bounded file/HTTP IO; goal-order.ts owns ordering semantics. It never
writes Goal priority, execution state or machine capability configuration.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..file_lock import exclusive_file_lock
from ..registry import atomic_write_json

SIDEBAR_GOAL_ORDER_PATH = '/api/chat/sidebar-goal-order'


def _source(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError('One status source is required')
    return value


def _order(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > 4096:
        raise ValueError('Goal order must be a bounded list')
    if any(not isinstance(item, str) or not item or len(item) > 512 for item in value):
        raise ValueError('Goal identifiers must be nonempty strings')
    if len(set(value)) != len(value):
        raise ValueError('Goal identifiers must be unique')
    return value


def _read(path: Path) -> dict[str, list[str]]:
    try:
        raw = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('Invalid sidebar preferences')
    return {_source(key): _order(order) for key, order in value.items()}


class WorkspacePreferencesRequestMixin:
    server: Any
    path: str

    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        raise NotImplementedError

    def _send_error(self, message: str, **kwargs: Any) -> None:
        raise NotImplementedError

    def _read_json(self, *, max_bytes: int = 256 * 1024) -> dict[str, Any]:
        raise NotImplementedError

    def _sidebar_order_file(self) -> Path:
        return Path(self.server.runtime_root) / 'presentation' / 'sidebar-goal-order-v1.json'

    def _sidebar_order_read(self) -> None:
        try:
            query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            if set(query) != {'source'} or len(query['source']) != 1:
                raise ValueError('Choose one status source')
            source = _source(query['source'][0])
        except ValueError as exc:
            self._send_error(str(exc), status=400)
            return
        try:
            order = _read(self._sidebar_order_file()).get(source)
            self._send_json({'ok': True, 'order': order})
        except (OSError, ValueError):
            self._send_error('Saved Goal order is unavailable. Retry after restoring the preference store.', status=503)

    def _sidebar_order_save(self) -> None:
        try:
            body = self._read_json(max_bytes=256 * 1024)
            if set(body) - {'only_if_absent'} != {'source', 'order'}:
                raise ValueError('Only source and Goal order are supported')
            source, order = _source(body['source']), _order(body['order'])
            only_if_absent = body.get('only_if_absent', False)
            if type(only_if_absent) is not bool:
                raise ValueError('only_if_absent must be a boolean')
        except (ValueError, TypeError) as exc:
            self._send_error(str(exc), status=400)
            return
        try:
            path = self._sidebar_order_file()
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with exclusive_file_lock(path):
                preferences = _read(path)
                if not only_if_absent or source not in preferences:
                    preferences[source] = order
                    atomic_write_json(path, preferences)
                saved = preferences[source]
            self._send_json({'ok': True, 'order': saved})
        except (OSError, ValueError, RuntimeError):
            self._send_error('Goal order could not be saved. Retry after restoring the preference store.', status=503)
