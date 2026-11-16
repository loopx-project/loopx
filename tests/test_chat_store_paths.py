"""Atomic Chat publication keeps the original filesystem address on Windows."""
import json
from loopx.chat_store import _atomic_write_json
from loopx.control_plane.runtime.file_paths import windows_extended_path

def test_atomic_chat_temp_name_beyond_windows_limit(tmp_path):
    parent=tmp_path
    while len(str(parent))<220:
        parent=parent/'nested-chat-path'
    path=parent/'state.json'
    _atomic_write_json(path,{'identity':'original','version':1})
    _atomic_write_json(path,{'identity':'original','version':2},preserve_mode=True)
    assert json.loads(windows_extended_path(path).read_text())=={'identity':'original','version':2}
    assert not list(windows_extended_path(parent).glob('*.tmp'))
