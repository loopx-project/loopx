"""Catalog discovery follows the selected host without starting model Turns."""
import json
import sys
from types import SimpleNamespace

import pytest

from loopx.chat_model_catalog import chat_model_catalog, codex_models
from loopx.chat_model_catalog_api import ModelCatalogRequestMixin


def _codex(tmp_path, *, malformed=False, legacy=False):
    executable = tmp_path / "codex-fixture"
    trace = tmp_path / "requests.jsonl"
    executable.write_text(f"#!{sys.executable}\n" + f"trace={str(trace)!r}\nmalformed={malformed!r}\nlegacy={legacy!r}\n" + '''
import json,os,sys
if sys.argv[1:3] == ['debug','models']:
 print(json.dumps({'models':[{'slug':'compat-model','base_instructions':'synthetic host instructions'}]}));sys.exit(0)
for line in sys.stdin:
 d=json.loads(line)
 with open(trace,'a') as f:f.write(json.dumps({'method':d['method'],'home':os.environ.get('CODEX_HOME')})+'\\n')
 if 'id' not in d:continue
 if d['method']=='initialize':result={}
 elif d['method']=='model/list':
  if legacy and '-c' not in sys.argv:
   print(json.dumps({'id':d['id'],'error':{'message':'model_catalog_json missing field base_instructions'}}),flush=True);continue
  if malformed:result={'data':[],'nextCursor':'repeated'}
  elif not d['params'].get('cursor'):
   result={'data':[{'id':'host-one','model':'host-one','displayName':'Host One','base_instructions':'never project these'}],'nextCursor':'second'}
  else:result={'data':[{'id':'host-two','model':'host-two','displayName':'Host Two'}],'nextCursor':None}
 else:raise RuntimeError('Catalog must not start a thread or Turn')
 print(json.dumps({'id':d['id'],'result':result}),flush=True)
''')
    executable.chmod(0o700)
    return executable, trace


def test_codex_model_list_pages_use_actual_home_and_never_start_a_turn(tmp_path):
    executable, trace = _codex(tmp_path)
    home = tmp_path / "codex-home"
    result = codex_models(str(executable), home)
    assert [model["id"] for model in result] == ["host-one", "host-two"]
    assert "never project" not in json.dumps(result)
    requests = [json.loads(line) for line in trace.read_text().splitlines()]
    assert [r["method"] for r in requests] == ["initialize", "initialized", "model/list", "model/list"]
    assert all(r["home"] == str(home) for r in requests)


def test_invalid_pagination_is_unavailable_without_placeholder_models(tmp_path):
    executable, _ = _codex(tmp_path, malformed=True)
    controller = SimpleNamespace(codex_bin=str(executable), codex_home=tmp_path)
    result = chat_model_catalog(controller, "codex")
    assert result["available"] is False and result["models"] == []
    assert str(tmp_path) not in json.dumps(result)


def test_legacy_catalog_retry_remains_visible_in_discovery(tmp_path):
    executable, _ = _codex(tmp_path, legacy=True)
    result = chat_model_catalog(SimpleNamespace(codex_bin=str(executable), codex_home=tmp_path), "codex")
    assert result["available"] is True and result["compatibility_applied"] is True
    assert [row["id"] for row in result["models"]] == ["host-one", "host-two"]


def test_native_picker_returns_declared_models_without_env_or_codex_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "settings.json").write_text(json.dumps({"env": {"PRIVATE_FIELD": "not model metadata"},
        "modelPicker": {"options": [{"model": "native-one[1m]", "label": "Native One"},
                                    {"model": "native-two", "description": "Native Two"}]}}))
    result = chat_model_catalog(SimpleNamespace(), "claude-code")
    assert result["available"] and [row["id"] for row in result["models"]] == ["native-one[1m]", "native-two"]
    assert "not model metadata" not in json.dumps(result)
    assert str(tmp_path) not in json.dumps(result)
    (tmp_path / "settings.json").write_text("[]")
    assert chat_model_catalog(SimpleNamespace(), "claude-code")["available"] is False


@pytest.mark.parametrize("query", ["", "?endpoint_id=", "?endpoint_id=codex&endpoint_id=claude-code", "?endpoint_id=codex&command=arbitrary"])
def test_catalog_api_does_not_accept_commands_or_ambiguous_hosts(query):
    class Handler(ModelCatalogRequestMixin):
        path = "/api/chat/models" + query
        def _send_error(self, message, **kwargs):
            self.error = kwargs
    handler = Handler()
    handler._model_catalog()
    assert handler.error["status"] == 400
