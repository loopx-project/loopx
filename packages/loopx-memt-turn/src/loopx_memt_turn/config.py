from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
from urllib.parse import urlsplit


class AdapterError(ValueError):
    """A public reason code, without provider bodies or private paths."""


def digest(document: object) -> str:
    return sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def unique_fields(pairs: list) -> dict:
    document = {}
    for name, field in pairs:
        if name in document:
            raise AdapterError("duplicate_json_field")
        document[name] = field
    return document


def decode_document(encoded: bytes) -> dict:
    if len(encoded) > 256_000:
        raise AdapterError("document_exceeds_limit")
    document = json.loads(encoded.decode("utf-8"), object_pairs_hook=unique_fields)
    if not isinstance(document, dict):
        raise AdapterError("json_object_required")
    return document


@dataclass(frozen=True)
class Config:
    workspace: str
    state_dir: str
    tokenizer: str
    base_url: str = "http://127.0.0.1:30000"
    enabled: bool = False
    mcp_command: tuple[str, ...] = ()
    tool_names: tuple[str, ...] = ()
    timeout_seconds: float = 180
    max_new_tokens: int = 4096
    max_tool_calls: int = 16

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise AdapterError("enabled_must_be_boolean")
        for location in (self.workspace, self.state_dir, self.tokenizer):
            if not isinstance(location, str) or not Path(location).is_absolute():
                raise AdapterError("absolute_config_paths_required")
        if not Path(self.workspace).is_dir():
            raise AdapterError("workspace_unavailable")
        if Path(self.state_dir).resolve().is_relative_to(Path(self.workspace).resolve()):
            raise AdapterError("state_must_be_outside_workspace")
        endpoint = urlsplit(self.base_url)
        if (endpoint.scheme not in {"http", "https"} or not endpoint.hostname
                or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment):
            raise AdapterError("invalid_server_endpoint")
        if endpoint.scheme == "http" and endpoint.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise AdapterError("remote_endpoint_requires_https")
        if (isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, (int, float))
                or not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 3600):
            raise AdapterError("invalid_timeout")
        for count, maximum in ((self.max_new_tokens, 65_536), (self.max_tool_calls, 256)):
            if type(count) is not int or not 1 <= count <= maximum:
                raise AdapterError("invalid_execution_limit")
        for selection in (self.mcp_command, self.tool_names):
            if not isinstance(selection, (list, tuple)) or any(
                not isinstance(part, str) or not part or "\0" in part for part in selection
            ):
                raise AdapterError("invalid_tool_selection")
        if (bool(self.mcp_command) != bool(self.tool_names) or len(self.tool_names) > 8
                or len(set(self.tool_names)) != len(self.tool_names)):
            raise AdapterError("select_at_most_eight_unique_tools_with_command")

    @property
    def fingerprint(self) -> str:
        return digest(asdict(self))


def load_config(path: Path) -> Config:
    with path.open("rb") as stream:
        document = decode_document(stream.read(256_001))
    config = Config(**document)
    if path.resolve().is_relative_to(Path(config.workspace).resolve()):
        raise AdapterError("config_must_be_outside_workspace")
    return config
