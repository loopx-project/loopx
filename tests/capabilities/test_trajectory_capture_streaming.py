"""Large private captures must not require a full text copy to observe costs."""
import hashlib
import json
import os
import tracemalloc

import pytest

from loopx.capabilities.benchmark_toolkit.trajectory_assessment_store import (
    _captured_view, _usage_rows, _usage_capture,
)


def test_hash_and_line_provenance_preserve_utf8_crlf_and_no_final_newline(tmp_path):
    path = tmp_path / 'capture.jsonl'
    raw = '{"text":"合成结果"}\r\n{"value":2}'.encode()
    path.write_bytes(raw)
    digest = hashlib.sha256()
    assert list(_usage_rows(path, digest)) == [(1, {'text': '合成结果'}), (2, {'value': 2})]
    assert digest.hexdigest() == hashlib.sha256(raw).hexdigest()
    path.write_bytes(b'{}\n{broken')
    with pytest.raises(ValueError):
        list(_usage_rows(path, hashlib.sha256()))


def test_current_readback_rehashes_same_size_same_timestamp_mutations(tmp_path):
    path = tmp_path / 'capture.bin'
    path.write_bytes(b'original')
    stamp = path.stat().st_mtime_ns
    captured = {'artifact': path.name, 'artifact_sha256': hashlib.sha256(b'original').hexdigest(),
                'projection': {'input_tokens': 123}}
    record = {'source_root': str(tmp_path), 'solver_usage': captured}
    assert _captured_view(record, 'solver_usage')['applicability'] == 'current'
    path.write_bytes(b'changed!')
    os.utime(path, ns=(stamp, stamp))
    historical = _captured_view(record, 'solver_usage')
    assert historical['reason_code'] == 'source_changed'
    assert historical['projection'] == captured['projection']
    path.unlink()
    assert _captured_view(record, 'solver_usage')['reason_code'] == 'source_unavailable'


def test_large_ignored_text_has_bounded_observation_and_verification_memory(tmp_path):
    path = tmp_path / 'large.jsonl'
    digest = hashlib.sha256()
    row = json.dumps({'type': 'response_item', 'payload': {
        'type': 'message', 'role': 'assistant', 'content': [{'text': 'x' * 65536}]}}).encode() + b'\n'
    with path.open('wb') as stream:
        for _ in range(512):
            stream.write(row)
            digest.update(row)
    # Start after fixture construction; measure the real parsing/hash path.
    tracemalloc.start()
    try:
        check = hashlib.sha256()
        assert sum(1 for _ in _usage_rows(path, check)) == 512
        captured = {'artifact': path.name, 'artifact_sha256': digest.hexdigest(), 'projection': {}}
        assert _captured_view({'source_root': str(tmp_path), 'solver_usage': captured}, 'solver_usage')['applicability'] == 'current'
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert check.hexdigest() == digest.hexdigest()
    assert peak < 4 * 1024 * 1024, f'32 MiB capture required {peak} bytes of Python allocation'


def test_real_counter_projection_retains_capture_digest_and_source_lines(tmp_path):
    usage = {'input_tokens': 100, 'cached_input_tokens': 60, 'output_tokens': 5}
    rows = [
        {'type': 'session_meta', 'payload': {'id': 'session'}},
        {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'turn'}},
        {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': [{'text': '合成输出'}]}},
        {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {'total_token_usage': usage, 'last_token_usage': usage}}},
        {'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': 'turn'}},
    ]
    for i, row in enumerate(rows):
        row['timestamp'] = f'2026-01-01T00:00:0{i}Z'
    raw = '\r\n'.join(json.dumps(r, ensure_ascii=False) for r in rows).encode()
    (tmp_path / 'usage.jsonl').write_bytes(raw)
    result = _usage_capture(tmp_path, 'usage.jsonl', 'session')
    assert result['artifact_sha256'] == hashlib.sha256(raw).hexdigest()
    assert result['projection']['input_tokens'] == 100
    assert result['projection']['turns'][0]['first_line'] == 2
    assert result['projection']['turns'][0]['last_line'] == 5
