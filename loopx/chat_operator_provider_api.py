"""The browser surface for this machine's operator model credential.

The Dashboard owns the machine settings a person edits, and the credential the
steward channel and the managed host authenticate with is one of them. This
module exposes exactly two operations: read the redacted status, and write one
update.

The value is write-only. ``GET`` returns the fingerprint, the endpoint and the
source of each field -- never the key -- and the write path accepts a key
without ever echoing it back, so a browser session can configure a credential
it cannot read. The endpoint is deliberately outside the machine-configuration
route: that document is projected, inspected and copied into transaction
backups, and a secret must not travel with it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .control_plane.operator_provider import (
    PROVIDER_KEY_FIELD,
    BASE_URL_FIELD,
    operator_provider_projection,
    write_operator_provider,
)

CHAT_OPERATOR_PROVIDER_PATH = "/api/chat/operator-credential"


class _OperatorProviderServer(Protocol):
    runtime_root: Path


class OperatorProviderRequestMixin:
    """Serve this machine's operator credential to the personal workspace."""

    server: _OperatorProviderServer

    def _send_json(self, payload: dict, *, status: int = 200) -> None: ...

    def _send_error(
        self, message: str, *, status: int, error_code: str
    ) -> None: ...

    def _read_json(self) -> dict: ...

    def _operator_provider_status(self) -> None:
        # The projection already carries the version for this payload, and it is
        # the same payload `loopx machine-config credential status` prints, so
        # the browser contract and the CLI contract cannot drift into two
        # spellings of one readback.
        self._send_json(
            {
                "ok": True,
                **operator_provider_projection(self.server.runtime_root),
            }
        )

    def _operator_provider_update(self) -> None:
        try:
            body = self._read_json()
            allowed = {
                PROVIDER_KEY_FIELD,
                BASE_URL_FIELD,
                "clear_provider_key",
                "clear_base_url",
            }
            if set(body) - allowed:
                raise ValueError(
                    "operator credential request contains unknown fields"
                )
            for flag in ("clear_provider_key", "clear_base_url"):
                if flag in body and not isinstance(body[flag], bool):
                    raise TypeError(f"{flag} must be a boolean")
            projection = write_operator_provider(
                runtime_root=self.server.runtime_root,
                api_key=body.get(PROVIDER_KEY_FIELD),
                base_url=body.get(BASE_URL_FIELD),
                clear_api_key=bool(body.get("clear_provider_key")),
                clear_base_url=bool(body.get("clear_base_url")),
            )
        except (TypeError, ValueError) as exc:
            self._send_error(
                str(exc),
                status=400,
                error_code="invalid_operator_credential",
            )
            return
        except OSError:
            self._send_error(
                "The operator credential could not be stored; the prior value "
                "was preserved.",
                status=500,
                error_code="operator_credential_write_failed",
            )
            return
        self._send_json(
            {
                "ok": True,
                "action": "stored",
                **projection,
            }
        )


__all__ = [
    "CHAT_OPERATOR_PROVIDER_PATH",
    "OperatorProviderRequestMixin",
]
