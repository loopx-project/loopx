from __future__ import annotations

import importlib.util
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest


def _load_runner() -> object:
    path = Path(__file__).resolve().parents[1] / "run_widesearch_case.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("run_widesearch_case", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_app_server_command_disables_multi_agent_for_responses_compat() -> None:
    mod = _load_runner()
    command = mod._app_server_command(
        codex_bin="codex",
        enable_web_search=True,
        gateway_base_url="http://127.0.0.1:8765",
        shell_policy_args=("-c", "shell-policy"),
    )
    assert "features.multi_agent=false" in command
    assert "tools.web_search=true" in command
    assert command[0] == "codex"
    assert "shell-policy" in command
    assert 'model_provider="loopx_runner_gateway"' in command
    assert any("http://127.0.0.1:8765" in value for value in command)
    assert any("LOOPX_MODEL_PROVIDER_SENTINEL" in value for value in command)


def test_app_server_command_can_disable_web_search() -> None:
    mod = _load_runner()
    command = mod._app_server_command(
        codex_bin="codex",
        enable_web_search=False,
        gateway_base_url="http://127.0.0.1:8765",
        shell_policy_args=(),
    )
    assert "tools.web_search=false" in command
    assert "tools.web_search=true" not in command


def test_app_server_environment_keeps_profile_temp_scope_and_nonsecret_sentinel(
    tmp_path: Path,
) -> None:
    mod = _load_runner()
    profile = SimpleNamespace(
        home=tmp_path / "home",
        codex_home=tmp_path / "codex-home",
        bin_dir=tmp_path / "bin",
    )
    environment = mod._app_server_environment(
        profile,
        {
            "PATH": "/bin",
            "TMPDIR": "/external-temporary-scope",
            "TMP": "/external-temporary-scope",
            "TEMP": "/external-temporary-scope",
            "UNRELATED_PRIVATE_TOKEN": "must-not-propagate",
            "ARK_OPENAI_API_KEY": "must-not-propagate",
        },
    )
    assert environment == {
        "PATH": f"{tmp_path / 'bin'}:/bin",
        "HOME": str(tmp_path / "home"),
        "TMPDIR": str(tmp_path / "home"),
        "TMP": str(tmp_path / "home"),
        "TEMP": str(tmp_path / "home"),
        "CODEX_HOME": str(tmp_path / "codex-home"),
        # Synthetic profiles always opt out of usage collection (#5272), even
        # when the supervisor's own opt-out flags are not inherited.
        "LOOPX_USAGE_PING": "0",
        "LOOPX_MODEL_PROVIDER_SENTINEL": "runner-owned-gateway-no-upstream-secret",
    }


@pytest.mark.parametrize("forbidden_key", ("ARK_OPENAI_BASE_URL", "ARK_OPENAI_API_KEY"))
def test_app_server_environment_rejects_upstream_provider_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    forbidden_key: str,
) -> None:
    mod = _load_runner()
    profile = SimpleNamespace(
        home=tmp_path / "home",
        codex_home=tmp_path / "codex-home",
        bin_dir=tmp_path / "bin",
    )
    monkeypatch.setattr(
        mod,
        "native_codex_profile_environment",
        lambda *_args, **_kwargs: {"PATH": "/bin", forbidden_key: "fixture"},
    )
    with pytest.raises(ValueError, match="outside app-server"):
        mod._app_server_environment(profile, {"PATH": "/bin"})


def test_runner_provider_authority_requires_both_values() -> None:
    mod = _load_runner()
    assert mod._runner_provider_authority(
        {
            "ARK_OPENAI_BASE_URL": "https://provider.invalid/v1",
            "ARK_OPENAI_API_KEY": "fixture-provider-value",
        }
    ) == ("https://provider.invalid/v1", "fixture-provider-value")
    with pytest.raises(RuntimeError, match="provider_authority_missing"):
        mod._runner_provider_authority(
            {"ARK_OPENAI_BASE_URL": "https://provider.invalid/v1"}
        )


def test_native_runner_refuses_ambient_nonlinux_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _load_runner()
    monkeypatch.setattr(mod.sys, "platform", "darwin")
    with pytest.raises(RuntimeError, match="native_isolation_unavailable_use_pier"):
        mod.run_case(
            case_id="fixture",
            arm="baseline",
            data_root=tmp_path,
            timeout_sec=1,
        )
    assert list(tmp_path.iterdir()) == []


def test_pier_agent_uses_gateway_sentinel_instead_of_provider_secret() -> None:
    template = (
        Path(__file__).resolve().parents[1] / "pier" / "job.yaml.template"
    ).read_text(encoding="utf-8")
    agent_section = template.partition("datasets:")[0]
    assert "<RUNNER_LOCAL_GATEWAY_BASE_URL>" in agent_section
    assert "LOOPX_MODEL_PROVIDER_SENTINEL" in agent_section
    assert "OPENAI_API_KEY" not in agent_section
    assert "<INJECT_VIA_RUNTIME_ENV>" not in agent_section
