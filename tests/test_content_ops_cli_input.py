from __future__ import annotations

import io

import pytest

from loopx.capabilities.content_ops import cli


def test_content_ops_stdin_json_loads_an_object(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.sys, "stdin", io.TextIOWrapper(io.BytesIO(b'{"item_id":"item-1"}')))

    assert cli._load_json_object("-") == {"item_id": "item-1"}


def test_content_ops_stdin_json_rejects_input_over_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "_MAX_STDIN_JSON_BYTES", 8)
    monkeypatch.setattr(cli.sys, "stdin", io.TextIOWrapper(io.BytesIO(b" " * 9)))

    with pytest.raises(ValueError, match="stdin JSON input exceeds the 16 MiB limit"):
        cli._load_json_object("-")
