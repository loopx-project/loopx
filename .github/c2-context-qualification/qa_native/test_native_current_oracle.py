"""Keep current main's native assertions; reuse #7430's saved-byte oracle only."""
import hashlib
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent
ORIGINAL = Path.cwd() / 'tests/control_plane/test_codex_context_capture.py'
source = ORIGINAL.read_text()
old = '''            # Keep the independent full-wire oracle separate from its page view.
            wire_file = Path(descriptor["path"]).parent / "delivery.json"
            wires[name] = json.loads(wire_file.read_text())'''
new = '''            # Keep saved-byte/state oracles separate from actual page input.
            # Full views page their existing capture; only deltas need a wire.
            context_root = home / "loopx-context" if host == "benchmark" else lifecycle
            if shared_root:
                context_root /= hashlib.sha256(descriptor["target"]["session_id"].encode()).hexdigest()
            recovery = json.loads((context_root / "session.json").read_bytes())["recovery"]
            wires[name] = native_capture_wire(descriptor, recovery=recovery)'''
old_reader = 'read_paths[name] = Path(wires[name]["recovery"]["path"]) if wires[name]["mode"] == "full" else wire_file'
assert source.count(old) == source.count(old_reader) == 1
adapted = source.replace(old, new).replace(old_reader, 'read_paths[name] = Path(descriptor["path"])')
namespace = {'__file__': str(ORIGINAL), '__name__': 'native_current_oracle',
             '__package__': 'tests.control_plane'}
exec(compile(adapted, str(ORIGINAL), 'exec'), namespace)
helper = (ROOT / 'native_capture_wire.py').read_text()
exec(compile(helper, str(ROOT / 'native_capture_wire.py'), 'exec'), namespace)
assert hashlib.sha256(ORIGINAL.read_bytes()).hexdigest() == hashlib.sha256(source.encode()).hexdigest()


@pytest.mark.parametrize('surface', ['quota_packet', 'turn_envelope'])
def test_native_ssh_retains_original_recovery(tmp_path, monkeypatch, surface):
    namespace['test_native_ssh_goal_keeps_identity_delta_and_full_recovery'](tmp_path, monkeypatch, surface)
