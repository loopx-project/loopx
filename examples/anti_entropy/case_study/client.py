"""Consumer client currently bound to legacy parser."""

from typing import Any
from .legacy_parser import LegacyMessageParser


class DataConsumer:
    """High-level data consumer."""

    def __init__(self) -> None:
        self._parser = LegacyMessageParser()

    def process_message(self, raw_input: str) -> dict[str, Any]:
        result = self._parser.decode(raw_input)
        return {
            "status": "ok",
            "content": result["payload"],
            "length": result["size"],
        }
