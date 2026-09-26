"""Bounded Codex calls with strict result contracts and process-group cleanup."""
import json
import os
import signal
import subprocess
import tempfile
from pathlib import Path


def obj(**properties):
    return dict(type='object', properties=properties, required=list(properties), additionalProperties=False)


S = {'type': 'string'}
B = {'type': 'boolean'}
STRINGS = {'type': 'array', 'items': S}
HUMAN = obj(kind={'type': 'string', 'enum': ['supplement', 'execute', 'judge']},
            title=S, reason=S, expected=S, skill=S, member_id=S)
TASK = obj(title=S, acceptance=S, skill=S)
MONITOR = obj(title=S, path=S, cadence=S, expires_at=S)
PLAN = obj(summary=S, tasks={'type': 'array', 'items': TASK},
           human={'type': 'array', 'items': HUMAN}, monitors={'type': 'array', 'items': MONITOR}, waiting_reason=S,
           goal_satisfied=B, evidence=STRINGS)
EXECUTE = obj(outcome={'type': 'string', 'enum': ['completed', 'needs_human', 'blocked']},
              summary=S, artifacts=STRINGS, human=HUMAN)
VERIFY = obj(sufficient=B, reason=S, evidence=STRINGS, gap=S)
OBSERVE = obj(relevant=B, reason=S, task_title=S, acceptance=S)
CAPABILITY = obj(summary=S, evidence=STRINGS)
OPTION = obj(label=S, value=S, field=S)
GOAL_DRAFT = obj(title=S, objective=S, acceptance=S, horizon=S, boundaries=S)
MEMBER_DRAFT = obj(name=S, role=S, description=S, skills=STRINGS, decision_scopes=STRINGS)
GOAL_DIALOGUE = obj(reply=S, draft=GOAL_DRAFT, missing=STRINGS,
                    options={'type': 'array', 'items': OPTION}, ready=B)
MEMBER_DIALOGUE = obj(reply=S, draft=MEMBER_DRAFT, missing=STRINGS,
                      options={'type': 'array', 'items': OPTION}, ready=B)


def validate(value, schema):
    kind = schema['type']
    if kind == 'object':
        if not isinstance(value, dict) or set(value) != set(schema['required']):
            raise ValueError('AI 返回的字段不符合约定')
        for key, child in schema['properties'].items():
            validate(value[key], child)
    elif kind == 'array':
        if not isinstance(value, list) or len(value) > 30:
            raise ValueError('AI 返回的列表无效或过长')
        for child in value:
            validate(child, schema['items'])
    elif kind == 'string':
        if not isinstance(value, str) or len(value) > 16000:
            raise ValueError('AI 返回的文本无效或过长')
    elif kind == 'boolean' and not isinstance(value, bool):
        raise ValueError('AI 返回的判断无效')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('AI 返回了未知的动作')


class Codex:
    def __init__(self, binary, timeout=300):
        self.binary = binary
        self.timeout = timeout
        self.process = None

    def check(self):
        result = subprocess.run([self.binary, 'login', 'status'], capture_output=True, text=True, timeout=15)
        return {'available': result.returncode == 0, 'detail': '已登录' if result.returncode == 0 else '请先登录本机 Codex'}

    def cancel(self):
        proc = self.process
        if proc and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def ask(self, workspace, prompt, schema, write=False):
        with tempfile.TemporaryDirectory(prefix='team-codex-') as tmp:
            path = Path(tmp)
            (path / 'schema.json').write_text(json.dumps(schema))
            args = [self.binary, 'exec', '--skip-git-repo-check', '--ephemeral',
                    '--sandbox', 'workspace-write' if write else 'read-only',
                    '-c', 'approval_policy="never"', '-C', str(workspace),
                    '--output-schema', str(path / 'schema.json'),
                    '--output-last-message', str(path / 'result.json'), '--json', '-']
            env = dict(os.environ)
            env.pop('CODEX_THREAD_ID', None)
            with (path / 'events.jsonl').open('w') as out, (path / 'stderr.log').open('w') as err:
                proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=out, stderr=err,
                                        text=True, env=env, start_new_session=True)
                self.process = proc
                try:
                    proc.communicate(prompt, timeout=self.timeout)
                except subprocess.TimeoutExpired:
                    self.cancel()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
                    raise RuntimeError('本轮 Codex 超时；保留任务，下轮先检查已有结果') from None
                finally:
                    self.process = None
            if proc.returncode or not (path / 'result.json').exists():
                raise RuntimeError('Codex 本轮未完成。请检查登录、额度或网络；任务结果未标记完成。')
            result = json.loads((path / 'result.json').read_text())
            validate(result, schema)
            return result
