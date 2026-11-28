"""Direct provider adapters keep healthy defaults while honoring overrides."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from loopx.chat.providers import DirectModelAdapter, direct_model_from_environment


PROVIDERS = (
    ("anthropic", "ANTHROPIC_API_KEY", "LOOPX_ANTHROPIC_MODEL", "claude-sonnet-5-5"),
    ("openai", "OPENAI_API_KEY", "LOOPX_OPENAI_MODEL", "gpt-6.1-sol"),
)


@pytest.mark.parametrize(("provider", "key_name", "model_name", "expected"), PROVIDERS)
def test_direct_model_uses_current_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    provider: str,
    key_name: str,
    model_name: str,
    expected: str,
) -> None:
    monkeypatch.setenv(key_name, "fake-key")
    monkeypatch.delenv(model_name, raising=False)

    adapter = direct_model_from_environment(provider=provider, work_dir=tmp_path)

    assert adapter.model == expected


@pytest.mark.parametrize(("provider", "key_name", "model_name", "_expected"), PROVIDERS)
def test_direct_model_honors_environment_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    provider: str,
    key_name: str,
    model_name: str,
    _expected: str,
) -> None:
    monkeypatch.setenv(key_name, "fake-key")
    monkeypatch.setenv(model_name, "operator-selected-model")

    adapter = direct_model_from_environment(provider=provider, work_dir=tmp_path)

    assert adapter.model == "operator-selected-model"


@pytest.mark.parametrize(("provider", "key_name", "model_name", "expected"), PROVIDERS)
def test_direct_model_sends_current_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    provider: str,
    key_name: str,
    model_name: str,
    expected: str,
) -> None:
    monkeypatch.setenv(key_name, "fake-key")
    monkeypatch.delenv(model_name, raising=False)
    bodies: list[dict[str, Any]] = []

    def fake_post(
        _adapter: DirectModelAdapter,
        _url: str,
        body: dict[str, Any],
        _headers: dict[str, str],
    ) -> dict[str, Any]:
        bodies.append(body)
        if provider == "anthropic":
            return {"content": [{"type": "text", "text": "ok"}]}
        return {"output_text": "ok"}

    monkeypatch.setattr(DirectModelAdapter, "_post", fake_post)
    adapter = direct_model_from_environment(provider=provider, work_dir=tmp_path)

    adapter.start_turn("hello", lambda _kind, _payload: None)

    assert bodies[0]["model"] == expected
    if provider == "anthropic":
        assert bodies[0]["max_tokens"] == 8192
