"""Runner retry settings must bind the actual host gateway or fail closed."""
from dataclasses import dataclass

import pytest

pytest.importorskip('sforge')
pytest.importorskip('harbor')

from benchmark.edgebench.run import _configure_provider_retries
from sforge.harness.config import SForgeConfig
from sforge.harness.run_agent import _prepare_credential_proxy
from sforge.harness.agent.codex import CodexAgent


def test_runner_setting_reaches_real_gateway():
    if not hasattr(SForgeConfig, 'agent_rate_limit_retry_seconds'):
        pytest.skip('real gateway qualification requires the companion SForge retry revision')
    config = SForgeConfig(agent_api_base_url='http://127.0.0.1:12345', agent_api_key='synthetic')
    configured = _configure_provider_retries(config, 900)
    backend = type('Backend', (), {'backend_name': 'docker'})()
    proxy, route = _prepare_credential_proxy(CodexAgent(configured), configured, backend)
    try:
        assert proxy.rate_limit_retry_seconds == 900
        assert route == f'http://host.docker.internal:{proxy.port}'
        assert config.agent_rate_limit_retry_seconds == 0
    finally:
        proxy.stop()


def test_zero_overrides_ambient_retry_setting():
    @dataclass
    class Config(SForgeConfig):
        agent_rate_limit_retry_seconds: int = 900
    assert _configure_provider_retries(Config(), 0).agent_rate_limit_retry_seconds == 0


@pytest.mark.parametrize('seconds', [-1, True, 1.5, '900'])
def test_invalid_setting_is_rejected(seconds):
    with pytest.raises(ValueError, match='nonnegative'):
        _configure_provider_retries(SForgeConfig(), seconds)


def test_oauth_does_not_silently_accept_inapplicable_setting():
    with pytest.raises(ValueError, match='API key'):
        _configure_provider_retries(SForgeConfig(), 900)


def test_old_sforge_keeps_default_but_rejects_unavailable_retry():
    @dataclass
    class Legacy:
        agent_api_base_url: str = 'http://127.0.0.1:12345'
        agent_api_key: str = 'synthetic'
    old = Legacy()
    assert _configure_provider_retries(old, 0) is old
    with pytest.raises(ValueError, match='does not support'):
        _configure_provider_retries(old, 900)
