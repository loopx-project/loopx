import pytest

from loopx.worker_bridge import _coerce_public_safe_worker_text


def test_coerce_public_safe_worker_text_accepts_clean_text():
    assert _coerce_public_safe_worker_text("clean worker update", field="msg", limit=100) == "clean worker update"


def test_coerce_public_safe_worker_text_rejects_case_insensitive_forbidden_markers():
    # Lowercase marker
    with pytest.raises(ValueError, match="contains a non-public marker"):
        _coerce_public_safe_worker_text("bearer secret-token-value", field="msg", limit=100)

    # Lowercase openai_api_key
    with pytest.raises(ValueError, match="contains a non-public marker"):
        _coerce_public_safe_worker_text("openai_api_key=mykey", field="msg", limit=100)

    # Uppercase Bearer
    with pytest.raises(ValueError, match="contains a non-public marker"):
        _coerce_public_safe_worker_text("Bearer secret-token-value", field="msg", limit=100)


def test_coerce_public_safe_worker_text_rejects_uppercase_sk_token_prefix():
    # Uppercase SK- token
    with pytest.raises(ValueError, match="contains a non-public marker"):
        _coerce_public_safe_worker_text("SK-abcdef1234567890", field="msg", limit=100)

    # Lowercase sk- token
    with pytest.raises(ValueError, match="contains a non-public marker"):
        _coerce_public_safe_worker_text("sk-abcdef1234567890", field="msg", limit=100)
