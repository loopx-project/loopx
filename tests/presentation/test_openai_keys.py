"""Key configuration through the shipped store, CLI and loopback HTTP owner."""
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="The private provider store uses POSIX locks")
KEY = "sk-" + "synthetic-key-" * 4


@pytest.fixture
def owner(monkeypatch, tmp_path):
    source = Path(__file__).resolve().parents[2] / "packages/loopx-codex-provider-routing/src"
    monkeypatch.syspath_prepend(str(source))
    native = importlib.import_module("loopx_codex_provider_routing.native")
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Key configuration cannot access native account IO")
    monkeypatch.setattr(native, "discover", forbidden)
    return native.NativeAccounts(tmp_path, transport=forbidden)


def test_configuration_is_private_revision_fenced_and_never_activates(owner):
    keys = owner.api_keys()
    assert keys.status()["keys"] == []
    assert not owner.root.exists()
    result = keys.add(expected_revision=0, label="Primary", api_key=KEY)
    assert KEY not in json.dumps(result)
    assert result["balance_available"] is False
    assert result["keys"][0]["check_status"] == "not_checked"
    assert keys.path.stat().st_mode & 0o777 == 0o600
    assert owner.root.stat().st_mode & 0o777 == 0o700
    assert not owner.path.exists(), "Native policy and account selection stay untouched"
    with pytest.raises(Exception, match="configuration_changed"):
        keys.remove(expected_revision=0, key_id=result["keys"][0]["id"])
    with pytest.raises(Exception, match="duplicate_api_key"):
        keys.add(expected_revision=1, label="Duplicate", api_key=KEY)
    assert owner.api_keys().status() == result, "A fresh instance reads the same saved config"
    keys.remove(expected_revision=1, key_id=result["keys"][0]["id"])
    assert keys.status()["keys"] == []


@pytest.mark.parametrize("label,key,code", [
    ("Name", "not-a-key", "invalid_api_key"),
    ("Name", "sk-admin-" + "a" * 32, "invalid_api_key"),
    (KEY, KEY, "invalid_key_label"),
    ("Name\nInjected", KEY, "invalid_key_label"),
])
def test_bad_inputs_never_create_a_store(owner, label, key, code):
    with pytest.raises(Exception, match=code):
        owner.api_keys().add(expected_revision=0, label=label, api_key=key)
    assert not owner.root.exists()


def test_symlink_and_public_permissions_are_refused(owner, tmp_path):
    keys = owner.api_keys()
    keys.add(expected_revision=0, label="Primary", api_key=KEY)
    keys.path.chmod(0o644)
    with pytest.raises(Exception, match="state_untrusted"):
        keys.status()
    keys.path.chmod(0o600)
    target = tmp_path / "target.json"
    keys.path.rename(target)
    keys.path.symlink_to(target)
    with pytest.raises(Exception, match="state_untrusted"):
        keys.status()


@pytest.mark.parametrize("http_status,expected", [(200, "models_readable"), (401, "authentication_failed"),
                                                  (403, "permission_denied"), (429, "rate_limited"), (503, "unavailable")])
def test_model_listing_does_not_claim_billing_quota(owner, monkeypatch, http_status, expected):
    module = importlib.import_module("loopx_codex_provider_routing.openai_keys")
    seen = []
    class Opener:
        def open(self, request, timeout):
            seen.append(request)
            assert timeout == 10 and request.get_method() == "GET"
            assert request.full_url == "https://api.openai.com/v1/models"
            if http_status != 200:
                raise urllib.error.HTTPError(request.full_url, http_status, "private provider text", {}, io.BytesIO(KEY.encode()))
            return io.BytesIO(b'{"data":[{"id":"synthetic-model"}]}')
    monkeypatch.setattr(module.urllib.request, "build_opener", lambda *_: Opener())
    keys = owner.api_keys()
    saved = keys.add(expected_revision=0, label="Primary", api_key=KEY)
    result = keys.check(expected_revision=1, key_id=saved["keys"][0]["id"])
    assert len(seen) == 1 and result["keys"][0]["check_status"] == expected
    assert result["balance_available"] is False
    assert KEY not in json.dumps(result) and "private provider text" not in json.dumps(result)


def test_transport_failure_and_redirect_keep_the_key_without_guessing_quota(owner, monkeypatch):
    module = importlib.import_module("loopx_codex_provider_routing.openai_keys")
    class Offline:
        def open(self, *_args, **_kwargs):
            raise OSError("private network detail")
    monkeypatch.setattr(module.urllib.request, "build_opener", lambda *_: Offline())
    assert module.check_models(KEY) == "unavailable"
    with pytest.raises(Exception, match="key_check_unavailable"):
        module.NoRedirect().redirect_request(None, None, None, None, None, None)


def test_http_owner_add_read_remove_and_bad_origin(owner):
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    from loopx.presentation.codex_accounts_api import CodexAccountService
    service = CodexAccountService(owner.root.parents[1])
    service.provider = owner
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.verbose = False
    server.codex_account_service = service
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/api/chat/codex-accounts/keys"
    def call(body=None, origin=None):
        data = json.dumps(body).encode() if body else None
        request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **({"Origin": origin} if origin else {})})
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)
    try:
        assert call()["keys"] == [] and not owner.root.exists()
        added = call({"operation": "keys-add", "parameters": {"expected_revision": 0, "label": "Primary", "api_key": KEY}})
        assert KEY not in json.dumps(added) and call() == added
        with pytest.raises(urllib.error.HTTPError) as error:
            call({"operation": "keys-add", "parameters": {}}, origin="https://outside.example")
        assert error.value.code == 403
        for operation, parameters in [("keys-check", {"expected_revision": 1, "key_id": added["keys"][0]["id"], "base_url": "https://outside.example"}),
                                      ("switch", {"manual_slot": "a"})]:
            with pytest.raises(urllib.error.HTTPError) as error:
                call({"operation": operation, "parameters": parameters})
            assert error.value.code == 409
        removed = call({"operation": "keys-remove", "parameters": {"expected_revision": 1, "key_id": added["keys"][0]["id"]}})
        assert removed["keys"] == [] and not owner.path.exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        service.close()


def test_cli_takes_secrets_only_on_stdin(owner, tmp_path):
    command = [sys.executable, "-m", "loopx_codex_provider_routing.native"]
    arguments = ["--runtime-root", str(tmp_path)]
    result = subprocess.run([*command, "keys-add", *arguments], input=json.dumps({"expected_revision": 0, "label": "Primary", "api_key": KEY}),
                            text=True, capture_output=True, check=True, timeout=10)
    assert KEY not in result.stdout + result.stderr
    assert len(json.loads(result.stdout)["keys"]) == 1
    read = subprocess.run([*command, "keys-status", *arguments], text=True, capture_output=True, check=True, timeout=10)
    assert json.loads(read.stdout)["keys"][0]["label"] == "Primary"
