"""Pure feedback callers do not require retained-state mutation adapters."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _isolated(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT,
        capture_output=True, text=True, check=False,
    )


def test_text_validation_remains_available_without_mutation_adapters() -> None:
    result = _isolated('''
import importlib.abc
import sys

class UnavailableWriters(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname in {
            'loopx.control_plane.coordination.legacy_writer_fence',
            'loopx.control_plane.runtime.document_io',
        }:
            raise ImportError('retained-state mutation adapters unavailable')

sys.meta_path.insert(0, UnavailableWriters())
from loopx.feedback import validate_local_control_text, validate_public_safe_text
validate_local_control_text('next_action', 'Read the current task and continue.')
validate_public_safe_text('summary', 'The artifact passed independent validation.')
try:
    validate_local_control_text('next_action', 'Bearer ' + 'x' * 32)
except ValueError:
    pass
else:
    raise AssertionError('the existing credential refusal must remain')
''')
    assert result.returncode == 0, result.stderr


def test_feedback_adapter_exports_retain_owner_identity_and_star_import() -> None:
    result = _isolated('''
from loopx import feedback
from loopx.control_plane.coordination import legacy_writer_fence
from loopx.control_plane.runtime import document_io

namespace = {}
exec('from loopx.feedback import *', namespace)
assert namespace['append_human_reward'] is feedback.append_human_reward
assert namespace['render_reward_markdown'] is feedback.render_reward_markdown
assert namespace['require_prose_state_write_allowed'] is legacy_writer_fence.require_prose_state_write_allowed
assert namespace['atomic_write_state_text'] is document_io.atomic_write_state_text
assert feedback.require_prose_state_write_allowed is namespace['require_prose_state_write_allowed']
assert feedback.atomic_write_state_text is namespace['atomic_write_state_text']
assert 'require_prose_state_write_allowed' in dir(feedback)
assert 'atomic_write_state_text' in dir(feedback)
''')
    assert result.returncode == 0, result.stderr


def test_missing_writer_dependency_fails_before_reward_mutation() -> None:
    result = _isolated('''
import importlib.abc
import sys
from pathlib import Path

class UnavailableWriters(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == 'loopx.control_plane.runtime.document_io':
            raise ImportError('mutation writer unavailable')

sys.meta_path.insert(0, UnavailableWriters())
from loopx.feedback import append_human_reward
try:
    append_human_reward(
        registry_path=Path('registry-does-not-exist.json'),
        runtime_root_override=None, goal_id='synthetic-feedback',
        run_generated_at=None, reward={}, actor_kind='owner',
    )
except ImportError as error:
    assert str(error) == 'mutation writer unavailable'
else:
    raise AssertionError('an unavailable writer must refuse before opening the registry')
''')
    assert result.returncode == 0, result.stderr
