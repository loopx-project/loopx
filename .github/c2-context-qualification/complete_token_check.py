"""Count complete captured-material read/recovery traces, never fake billing."""
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

import tiktoken
from evaluate import CONFIG, ROOT, environment

ENCODERS = {name: tiktoken.get_encoding(name) for name in ('o200k_base', 'cl100k_base')}


def pages(command, arm, project):
    outputs, content = [], []
    while command:
        result = subprocess.run(shlex.split(command), cwd=project,
                                env=environment(arm, 'token-' + arm), capture_output=True, timeout=120)
        assert result.returncode == 0, result.stderr
        page = json.loads(result.stdout)
        assert page['ok'] and 'content' in page, page
        outputs.append(result.stdout.decode())
        content.append(page['content'])
        command = page.get('next_command')
        if page['eof']:
            assert command is None
    return outputs, ''.join(content)


def read_reference(reference, arm, project):
    if 'content' in reference:
        if reference.get('eof'):
            return [], reference['content']
        outputs, rest = pages(reference['next_command'], arm, project)
        return outputs, reference['content'] + rest
    return pages(reference['command'], arm, project)


def supporting_pointers(value):
    if isinstance(value, dict):
        pointer = value.get('supporting_material_pointer')
        if isinstance(pointer, str):
            yield pointer + '/sources'
        for child in value.values():
            yield from supporting_pointers(child)
    elif isinstance(value, list):
        for child in value:
            yield from supporting_pointers(child)


rows = []
for provider in CONFIG['providers']:
    for history in CONFIG['history_counts']:
        case = f'{provider}-{history}'
        manifest = json.loads((ROOT / 'evaluation-fixtures' / case / 'manifest.json').read_text())
        for mode in CONFIG['modes']:
            traces = {}
            for arm, index in [('base', 1), ('head', 2)]:
                raw = (ROOT / f'preflight-{case}-{mode}-{index}.json').read_text()
                reference = json.loads(raw)
                trace = []
                if mode == 'warm':
                    initial_raw = (Path(manifest['project']) / 'setup.stdout.json').read_text()
                    initial = json.loads(initial_raw)
                    trace.append(initial_raw)
                    consumed, _ = read_reference(initial, arm, manifest['project'])
                    trace.extend(consumed)
                trace.append(raw)
                consumed, _ = read_reference(reference, arm, manifest['project'])
                trace.extend(consumed)
                # The scenario loses context, then recovers this same capture
                # and its supporting sources without another admission.
                trace.append(None)
                consumed, complete = pages(reference['recovery']['command'], arm, manifest['project'])
                trace.extend(consumed)
                required = json.loads(complete)
                details = required.get('context_details')
                assert details, 'qualification must exercise supporting material'
                pointers = sorted(set(supporting_pointers(required)))
                assert pointers
                command = shlex.split(details['command_template'])
                command = command[:command.index('--select')]
                for pointer in pointers:
                    command.extend(['--select', pointer])
                consumed, selected = pages(shlex.join(command), arm, manifest['project'])
                trace.extend(consumed)
                assert len(json.loads(selected)['selections']) == len(pointers)
                traces[arm] = trace
            assert traces['base'] == traces['head'], (case, mode, 'model-visible material differs')
            counts = {}
            for name, encoder in ENCODERS.items():
                totals = {}
                for arm, trace in traces.items():
                    delivered = 0
                    prefix = 'Read current captured constraints; recover current facts after context loss.\n'
                    replayed = 0
                    for output in trace:
                        if output is None:
                            prefix = 'Context lost; recover the same current capture before dependent work.\n'
                            continue
                        delivered += len(encoder.encode(output))
                        prefix += output
                        replayed += len(encoder.encode(prefix))
                    totals[arm] = {'delivered_tokens': delivered, 'retained_history_replay_tokens': replayed}
                assert totals['base'] == totals['head'], (case, mode, name, totals)
                counts[name] = totals
            trace_data = json.dumps(traces['base'], ensure_ascii=False).encode()
            (ROOT / f'token-trace-{case}-{mode}.json').write_bytes(trace_data)
            rows.append({'case': case, 'mode': mode, 'tool_outputs': sum(x is not None for x in traces['base']),
                         'trace_sha256': hashlib.sha256(trace_data).hexdigest(), 'tokens': counts})

report = {'baseline': CONFIG['baseline'], 'candidate': CONFIG['candidate_head'], 'passed': True,
          'scope': 'Actual CLI stdout over full, warm, supporting-source and same-capture recovery reads. '
                   'Repeated input-history replay is counted and resets at the declared context-loss boundary. '
                   'SDK/system framing, cache/provider billing and generative model usage are not measured.',
          'rows': rows}
(ROOT / 'token-parity.json').write_text(json.dumps(report, indent=2))
print(json.dumps({'passed': True, 'cells': len(rows), 'complete_consumption_trace': True}))
