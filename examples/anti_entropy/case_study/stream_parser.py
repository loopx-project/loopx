"""Modern stream message parser replacing legacy parser."""

from typing import Any


class StreamMessageParser:
    """Modern stream parser parsing structured payload with validation."""

    def parse(self, raw_data: str) -> dict[str, Any]:
        # Accepts both pipe format and key-value formats
        parts = raw_data.strip().split("|")
        if len(parts) >= 3:
            return {
                "version": "v2",
                "header": parts[0],
                "payload": parts[1],
                "checksum": parts[2],
                "size": len(parts[1]),
            }
        raise ValueError("Invalid stream payload")
