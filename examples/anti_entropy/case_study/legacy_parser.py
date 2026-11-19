"""Legacy message parser slated for retirement."""

from typing import Any


class LegacyMessageParser:
    """Old-style frame decoder with delimiter separation."""

    def __init__(self, delimiter: str = "|") -> None:
        self.delimiter = delimiter

    def decode(self, raw_data: str) -> dict[str, Any]:
        parts = raw_data.strip().split(self.delimiter)
        if len(parts) < 3:
            raise ValueError("Malformed legacy frame")
        header, payload, checksum = parts[0], parts[1], parts[2]
        return {
            "version": "v1",
            "header": header,
            "payload": payload,
            "checksum": checksum,
            "size": len(payload),
        }


def parse_legacy_frame(raw_data: str) -> dict[str, Any]:
    """Standalone legacy helper."""
    return LegacyMessageParser().decode(raw_data)
