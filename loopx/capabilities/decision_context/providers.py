"""Replaceable current-authority providers for Decision Context."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from ...materials import read_bounded_material_file

from .sources import (
    DecisionSourceExactRead,
    DecisionSourceItem,
    DecisionSourceProvider,
    DecisionSourceScan,
    DecisionSourceSpec,
)

LOCAL_FILE_SOURCE_ADAPTER = "local-file"
LOCAL_FILE_SOURCE_PROVIDER_VERSION = "builtin-v0"
DEFAULT_LOCAL_FILE_MAX_BYTES = 1_048_576
MAX_LOCAL_FILE_MAX_BYTES = 4_194_304

DecisionSourceProviderBuilder = Callable[
    [str, Mapping[str, Any]], DecisionSourceProvider
]
_SOURCE_PROVIDER_BUILDERS: dict[str, DecisionSourceProviderBuilder] = {}


def register_decision_source_provider(
    adapter: str,
    builder: DecisionSourceProviderBuilder,
) -> None:
    normalized = str(adapter or "").strip()
    if not normalized:
        raise ValueError("decision source adapter id is required")
    if normalized in _SOURCE_PROVIDER_BUILDERS:
        raise ValueError(f"decision source adapter already registered: {normalized}")
    _SOURCE_PROVIDER_BUILDERS[normalized] = builder


def decision_source_provider_registered(adapter: str) -> bool:
    return str(adapter or "").strip() in _SOURCE_PROVIDER_BUILDERS


def build_decision_source_provider(
    binding: Mapping[str, Any],
) -> DecisionSourceProvider:
    """Resolve one private provider binding without capability-specific branching."""

    provider_id = str(binding.get("provider_id") or "").strip()
    adapter = str(binding.get("adapter") or "").strip()
    config = binding.get("config")
    if not provider_id:
        raise ValueError("decision source provider_id is required")
    if not isinstance(config, Mapping):
        raise ValueError("decision source provider config must be an object")
    builder = _SOURCE_PROVIDER_BUILDERS.get(adapter)
    if builder is None:
        raise ValueError(
            f"unsupported decision source adapter: {adapter or '<missing>'}"
        )
    provider = builder(provider_id, config)
    if str(getattr(provider, "provider_id", "") or "") != provider_id:
        raise ValueError("decision source provider identity does not match its binding")
    return provider


def _parse_timestamp(value: str, *, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed


def _max_bytes(config: Mapping[str, Any]) -> int:
    value = config.get("max_bytes", DEFAULT_LOCAL_FILE_MAX_BYTES)
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_LOCAL_FILE_MAX_BYTES
    ):
        raise ValueError(
            f"local-file max_bytes must be between 1 and {MAX_LOCAL_FILE_MAX_BYTES}"
        )
    unexpected = sorted(set(config) - {"max_bytes"})
    if unexpected:
        raise ValueError(
            "local-file provider config contains unsupported fields: "
            + ", ".join(unexpected)
        )
    return int(value)


class LocalFileDecisionSourceProvider:
    """Read one explicitly configured local authority file without public capture."""

    def __init__(self, *, provider_id: str, max_bytes: int) -> None:
        self.provider_id = provider_id
        self.max_bytes = max_bytes

    def _read(
        self,
        source: DecisionSourceSpec,
    ) -> tuple[Path, bytes, str, str]:
        if source.provider_id != self.provider_id:
            raise ValueError("source provider identity does not match")
        path = Path(source.private_locator).expanduser()
        return read_bounded_material_file(path, self.max_bytes)

    def scan(
        self,
        *,
        source: DecisionSourceSpec,
        after_cursor: str | None,
        before: str,
        limit: int,
        timeout_seconds: float,
        observed_at: str,
    ) -> DecisionSourceScan:
        del observed_at
        started = time.monotonic()
        if limit < 1:
            raise ValueError("limit must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        path, _content, revision, item_observed_at = self._read(source)
        cursor = f"sha256:{revision}"
        if _parse_timestamp(
            item_observed_at,
            field_name="source observed_at",
        ) > _parse_timestamp(before, field_name="before"):
            return DecisionSourceScan(
                provider_id=self.provider_id,
                source_id=source.source_id,
                status="no_change",
                observed_at=before,
                requested_limit=min(limit, source.max_changes),
                cursor_before=after_cursor,
                cursor_after=after_cursor,
                provider_version=LOCAL_FILE_SOURCE_PROVIDER_VERSION,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        if after_cursor == cursor:
            return DecisionSourceScan(
                provider_id=self.provider_id,
                source_id=source.source_id,
                status="no_change",
                observed_at=item_observed_at,
                requested_limit=min(limit, source.max_changes),
                cursor_before=after_cursor,
                cursor_after=cursor,
                provider_version=LOCAL_FILE_SOURCE_PROVIDER_VERSION,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        return DecisionSourceScan(
            provider_id=self.provider_id,
            source_id=source.source_id,
            status="completed",
            observed_at=item_observed_at,
            requested_limit=min(limit, source.max_changes),
            cursor_before=after_cursor,
            cursor_after=cursor,
            items=(
                DecisionSourceItem(
                    resource_ref=str(path),
                    revision=revision,
                    observed_at=item_observed_at,
                ),
            ),
            provider_version=LOCAL_FILE_SOURCE_PROVIDER_VERSION,
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    def exact_read(
        self,
        *,
        source: DecisionSourceSpec,
        item: DecisionSourceItem,
        timeout_seconds: float,
        observed_at: str,
    ) -> DecisionSourceExactRead:
        del observed_at
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        path, content, revision, item_observed_at = self._read(source)
        if str(path) != item.resource_ref or revision != item.revision:
            raise ValueError("authority file changed after the bounded scan")
        text = content.decode("utf-8")
        if not text.strip():
            raise ValueError("configured authority file is empty")
        return DecisionSourceExactRead(
            resource_ref=item.resource_ref,
            revision=item.revision,
            observed_at=item_observed_at,
            content=text,
        )


def _build_local_file_provider(
    provider_id: str,
    config: Mapping[str, Any],
) -> DecisionSourceProvider:
    return LocalFileDecisionSourceProvider(
        provider_id=provider_id,
        max_bytes=_max_bytes(config),
    )


register_decision_source_provider(
    LOCAL_FILE_SOURCE_ADAPTER,
    _build_local_file_provider,
)
