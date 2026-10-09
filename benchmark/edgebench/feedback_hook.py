"""Installed, dependency-free Codex hook for the best-only feedback channel.

This reader has no judge credentials or grading access. It projects only the
host's public snapshot identity; it neither blocks tools nor starts model turns.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path('/opt/edgebench-feedback')
EVENTS = ('PostToolUse', 'SessionStart', 'UserPromptSubmit')


def deliver(event, root=ROOT, receipts=Path('/logs/agent/best-feedback-delivery'), *, output=None):
    output = output or sys.stdout
    name, session = event.get('hook_event_name'), event.get('session_id')
    if name not in EVENTS or not isinstance(session, str) or not session:
        raise ValueError('Expected a supported Codex hook with a session identity')
    packet = json.loads((root / 'latest.json').read_text())
    if packet.get('schema_version') != 'edgebench_best_feedback_v1':
        raise ValueError('Unknown best-only packet schema')
    notice = packet.get('latest')
    if notice is None:
        return
    snapshot, digest = notice.get('snapshot_id'), notice.get('source_sha256')
    if (notice.get('kind') != 'new_best' or not isinstance(snapshot, str)
            or not re.fullmatch(r'auto-[0-9]+', snapshot) or not isinstance(digest, str)
            or not re.fullmatch(r'[0-9a-f]{64}', digest)):
        raise ValueError('Invalid best-only snapshot identity')
    archive = root / f'{snapshot}-{digest}.tar.gz'
    if notice.get('source_archive') != str(archive) or not archive.is_file():
        raise ValueError('Best-only source checkpoint is unavailable')
    receipts.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(session.encode()).hexdigest()
    cursor = receipts / f'{key}.json'
    # Codex may finish multiple tools concurrently. Serialize their one delivery.
    with (receipts / f'{key}.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        identity = f'{snapshot}:{digest}'
        if cursor.exists() and json.loads(cursor.read_text()).get('identity') == identity:
            return
        text = (
            f'EdgeBench new-best feedback: evaluated snapshot {snapshot} strictly improved '
            "the task's native ranking among valid scored snapshots. "
            f'Your submitted source is {archive} (SHA-256 {digest}). '
            'Evaluation is asynchronous; this is not necessarily your current workspace. '
            'Preserve or compare this checkpoint without blindly overwriting newer work. '
            'Continue local validation; this signal does not establish task completion.'
        )
        # No decision/block/continue field: preserve the original tool output.
        print(json.dumps({'hookSpecificOutput': {
            'hookEventName': name, 'additionalContext': text}}), file=output, flush=True)
        pending = cursor.with_suffix('.pending')
        pending.write_text(json.dumps({'identity': identity, 'hook_event_name': name}))
        pending.replace(cursor)


def install(config=Path('/etc/codex'), script=ROOT / 'hook.py'):
    """Merge the provider hook with the official worker's existing Stop hook."""
    config.mkdir(parents=True, exist_ok=True)
    config.chmod(0o755)
    script.chmod(0o644)
    path = config / 'hooks.json'
    settings = json.loads(path.read_text()) if path.exists() else {}
    hooks = settings.setdefault('hooks', {})
    handler = {'hooks': [{'type': 'command', 'command': f'python3 {script}', 'timeout': 5}]}
    for name in EVENTS:
        groups = hooks.setdefault(name, [])
        if handler not in groups:
            groups.append(handler)
    path.write_text(json.dumps(settings))
    path.chmod(0o644)


if __name__ == '__main__':
    if sys.argv[1:] == ['--install']:
        install()
    else:
        try:
            deliver(json.load(sys.stdin))
        except (OSError, ValueError, TypeError, AttributeError):
            # No evaluator details, raw packet or paths in model-visible errors.
            print('Best-only notification transport unavailable', file=sys.stderr)
            sys.exit(1)
