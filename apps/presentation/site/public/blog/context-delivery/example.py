"""Reproduce a synthetic context-delivery lesson with the existing owners.

Run with a LoopX source checkout's Python environment. This never registers a
Goal, admits/spends a Turn, contacts a model/provider or changes live state.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--loopx-root', type=Path, required=True)
p.add_argument('--output', type=Path, required=True, help='Empty directory for generated synthetic outputs.')
p.add_argument('--private-receipts', type=Path)
a = p.parse_args()
repo = a.loopx_root.resolve()
out = a.output.resolve()
if out.exists() and any(out.iterdir()):
    p.error('Use an empty output directory; existing material will not be overwritten.')
out.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(repo))
from loopx.control_plane.quota.turn_envelope import prepare_context_delivery, restore_context_delivery
import loopx
assert Path(loopx.__file__).resolve().parent == repo / 'loopx'

def raw(value):
    return (json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n').encode()

def save(name, value):
    content = raw(value)
    (out / name).write_bytes(content)
    return {'fixture': name, 'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)}

goal = {
    'objective': '让管家理解语音消息，并在原对话返回结果 / Understand a voice note and reply in its original chat',
    'constraints': {
        'provider': '优先已有语音服务 / Prefer the already configured speech service',
        'privacy': '音频不能公开；外部处理仍需已有授权 / Do not publish audio; external processing still needs authorization',
    },
    'acceptance': '转写正确，结果回到原对话；服务不可用时明确告知 / Correct transcript and same-chat reply; state service unavailability explicitly',
}
providers = {
    'configured': {'formats': ['m4a', 'mp3'], 'authorization': 'must_check_current_grant'},
    'alternative': {'formats': ['wav'], 'authorization': 'not_established'},
}
evidence = {
    'sample-1': {'conditions': {'reply_chat': 'original-chat', 'format': 'm4a'}, 'result': '正确回到原对话 / Replied in the original chat'},
    'sample-2': {'conditions': {'reply_chat': 'another-chat', 'format': 'm4a'}, 'result': '转写正确，但回复到了另一对话 / Correct transcript, wrong reply chat', 'reviewer': None},
}
history = [
    {'entry': i, 'topic': ['formats', 'privacy', 'failure-recovery', 'same-chat-return'][i % 4],
     'discussion': f'教学历史记录 {i}。接入方案要分别检查输入格式、当前服务授权、原对话身份和结果来源。'
       '转写文本正确并不足以通过验收，还需要返回同一条原始工作流。网络失败、响应丢失、格式不支持和用户撤回都保留为独立事实。'
       '比较候选服务时，原目标、隐私约束和接受条件继续有效；引用资料可以深入读取，不能用一段新摘要替换原要求。'
       '这是合成材料中的讨论，不记录任何真实用户、语音、账号、对话或服务调用。',
     'related_sample': 'sample-1' if i % 2 == 0 else 'sample-2'}
    for i in range(80)
]
lesson = {'goal': goal, 'providers': providers, 'evidence': evidence, 'history': history}
captures = {'capture': save('capture.json', lesson)}

env = {**os.environ, 'PYTHONPATH': str(repo), 'LOOPX_CONTEXT_DELIVERY': 'off'}
cli = [sys.executable, '-c', 'from loopx.cli import main; main()', '--format', 'json', 'quota', 'context-read']
all_receipts = {}

def read(label, capture, args):
    """Count complete actual CLI responses, following its verified byte cursors."""
    pages, bodies, offset, selection_digest = [], [], 0, None
    while True:
        cmd = cli + ['--capture-file', capture['fixture'], '--capture-sha256', capture['sha256'],
            '--offset', str(offset), '--page-bytes', '16000', '--max-page-output-bytes', '20000'] + args
        if selection_digest:
            cmd += ['--selection-sha256', selection_digest]
        r = subprocess.run(cmd, cwd=out, env=env, capture_output=True)
        page = json.loads(r.stdout)
        pages.append({'exit_code': r.returncode, 'stdout': r.stdout.decode(), 'stderr': r.stderr.decode()})
        if not page.get('ok'):
            assert 'content' not in page
            all_receipts[label] = pages
            return {'calls': len(pages), 'output_bytes': sum(len(x['stdout'].encode()) for x in pages), 'error': page}
        assert page['sha256'] == capture['sha256'] and page['offset'] == offset
        b = page['content'].encode()
        bodies.append(b)
        offset += len(b)
        if 'selection_sha256' in page:
            selection_digest = selection_digest or page['selection_sha256']
            assert selection_digest == page['selection_sha256']
        if page['eof']:
            assert page['next_offset'] is None and offset == page['total_bytes']
            break
        assert page['next_offset'] == offset and page.get('next_command')
    joined = b''.join(bodies)
    assert hashlib.sha256(joined).hexdigest() == (selection_digest or capture['sha256'])
    all_receipts[label] = pages
    return {'calls': len(pages), 'body_bytes': len(joined),
        'output_bytes': sum(len(x['stdout'].encode()) for x in pages), 'body': json.loads(joined)}

paths = ['/goal/objective', '/goal/constraints', '/goal/acceptance']
full = read('full', captures['capture'], [])
separate = [read('goal-' + str(i), captures['capture'], ['--select', path]) for i, path in enumerate(paths)]
batch = read('batch', captures['capture'], [arg for path in paths for arg in ['--select', path]])
assert {s['pointer']: s['value'] for r in separate for s in r['body']['selections']} == {s['pointer']: s['value'] for s in batch['body']['selections']}
assert full['body'] == lesson
indexes = [read('index-' + pointer, captures['capture'], ['--select', pointer, '--list'])
    for pointer in ['/providers', '/providers/configured', '/providers/alternative']]
deep_index = read('index-depth-2', captures['capture'], ['--select', '/providers', '--list', '--list-depth', '2'])
fields = lambda r: r['body']['selections'][0]['fields']
assert {f['pointer'] for r in indexes for f in fields(r)} == {f['pointer'] for f in fields(deep_index)}
sample = read('sample-result', captures['capture'], ['--select', '/evidence/sample-2/result'])
conditions = read('sample-conditions', captures['capture'], ['--select', '/evidence/sample-1/conditions', '--select', '/evidence/sample-2/conditions'])
missing = read('missing', captures['capture'], ['--select', '/goal/objective', '--select', '/providers/missing'])
assert missing['error']['diagnostic_code'] == 'context_selection_missing_pointer'
null = read('null', captures['capture'], ['--select', '/evidence/sample-2/reviewer'])
assert null['body']['selections'][0]['value'] is None

# Registered native acceptance shape: exercise the actual typed progressive owner.
clauses = [
    '优先复用已配置的语音服务；模型或工具配置不是一次外部发送授权。',
    '原始音频不可公开；外部处理、受众与保存边界沿当前授权核验。',
    '转写结果回到原对话，保留消息来源、顺序及原始音频引用。',
    '明确支持的格式；不支持或过大的输入返回可理解的失败结果。',
    '服务不可用和未获授权分别告知，不伪造已转写或已发送。',
    '响应丢失后核对原请求结果，同一次输入不重复计费或回传。',
    '取消与撤回保留真实边界，无法取消的外部操作不伪装已停止。',
    '识别不确定时说明来源和不确定性；原始证据继续可查。',
    '刷新和继续工作后仍保留全部原要求、当前状态及失败理由。',
    '验收区分组件自测、实际原会话采用与独立核验，不从配置推断完成。',
]
requirements = [{'id': 'voice-' + str(i), 'description': c, 'status': 'unverified', 'planning_satisfied': False}
    for i, c in enumerate(clauses)]
requirements[2]['agent_review'] = {'id': 'voice-2', 'conclusion': 'unmet', 'basis_current': False,
    'independently_verified': False, 'note': evidence['sample-2']['result'], 'evidence_refs': ['fixture:sample-2']}
outcome = {'id': 'voice-output', 'owner_agent_id': 'demo-agent', 'objective': goal['objective'], 'revision': 1,
    'requirements': requirements, 'todo_ids': ['todo_voice'],
    'sources': [{'ref': 'fixture:voice-brief', 'summary': json.dumps(history, ensure_ascii=False)}]}
row = {'schema_version': 'todo_item_v0', 'todo_id': 'todo_voice', 'text': '在原对话接入语音读取并核验完整返回。',
    'note': '\n'.join(clauses), 'content_revision': 'fixture:revision-1', 'claimed_by': 'demo-agent'}
native = {'ok': True, 'planning_horizon': {'core_goal': {'objective': goal['objective'],
    'acceptance_contract': {'outcome_requirements': {'schema_version': 'loopx_goal_outcomes_v0', 'outcomes': [outcome]}}}},
    'agent_todo_summary': {'schema_version': 'todo_summary_v0', 'first_open_items': [row], 'backlog_items': [copy.deepcopy(row)]},
    'work_state': {'phase': 'verify-original-chat', 'samples': ['sample-1', 'sample-2']}}
captures['original'] = save('projection.json', native)
scope = {'goal_id': 'voice-demo', 'agent_id': 'demo-agent', 'session_id': 'demo-session', 'context_epoch': 'epoch-1'}

def project(payload, capture, capture_id, **kwargs):
    return prepare_context_delivery(payload, scope=scope, capture_id=capture_id, surface='quota_packet',
        native_projection={'path': capture['fixture'], 'sha256': capture['sha256'],
            'command_template': 'loopx --format json quota context-read --capture-file ' + capture['fixture']
                + ' --capture-sha256 ' + capture['sha256'] + " --select '<pointer>'"}, **kwargs)

cold = project(native, captures['original'], 'fixture-capture-1')
assert cold['projected'] is True
required = cold['context']
captures['required'] = save('required-context.json', required)
current_outcome = required['planning_horizon']['core_goal']['acceptance_contract']['outcome_requirements']['outcomes'][0]
assert len(current_outcome['requirements']) == 10 and 'sources' not in current_outcome
assert all(x['description'] == y['description'] for x, y in zip(current_outcome['requirements'], requirements))
review = current_outcome['requirements'][2]['agent_review']
assert review == {'id': 'voice-2', 'conclusion': 'unmet', 'basis_current': False, 'independently_verified': False}
assert required['agent_todo_summary']['backlog_items'][0] == {'todo_id': 'todo_voice', 'context_ref': '/agent_todo_summary/first_open_items/0'}
parent = current_outcome['supporting_material_pointer']
detail = read('native-reason', captures['original'], ['--select', parent + '/requirements/2/agent_review/note'])
assert detail['body']['selections'][0]['value'] == evidence['sample-2']['result']

changed = copy.deepcopy(native)
changed['work_state']['samples'].append('sample-3')
changed['work_state']['phase'] = 'verify-repaired-return'
changed['planning_horizon']['core_goal']['acceptance_contract']['outcome_requirements']['outcomes'][0]['requirements'][2]['agent_review']['basis_current'] = True
captures['original_next'] = save('projection-next.json', changed)
current_full = project(changed, captures['original_next'], 'fixture-capture-2')
warm = project(changed, captures['original_next'], 'fixture-capture-2', baseline=cold['delivery'],
    continuity={'basis': 'captured_file', 'capture_bytes_verified': True, 'target': cold['delivery']['target']})
assert warm['delivery']['mode'] == 'delta'
restored = restore_context_delivery(warm['delivery'], baseline=cold['delivery'], current=current_full['delivery'])
assert restored['context'] == current_full['context']
save('cycle-base.json', cold['delivery']); save('cycle-delta.json', warm['delivery'])
new_epoch = prepare_context_delivery(current_full['context'], scope={**scope, 'context_epoch': 'epoch-2'},
    capture_id='fixture-capture-3', surface='quota_packet', baseline=cold['delivery'],
    continuity={'basis': 'captured_file', 'capture_bytes_verified': True, 'target': cold['delivery']['target']})
assert new_epoch['mode'] == 'full'
broken = copy.deepcopy(warm['delivery']); broken['changes'] = {'value': {'invented': True}}
recovery = restore_context_delivery(broken, baseline=cold['delivery'], current=current_full['delivery'])
assert recovery['mode'] == 'full' and recovery['context'] == current_full['context']
tiny = {'ok': True, 'state': 'a'}
tiny_base = prepare_context_delivery(tiny, scope=scope, capture_id='tiny-1', surface='quota_packet')
tiny_next = prepare_context_delivery({'ok': True, 'state': 'b'}, scope=scope, capture_id='tiny-2', surface='quota_packet',
    baseline=tiny_base, continuity={'basis':'captured_file', 'capture_bytes_verified': True, 'target': tiny_base['target']})
assert tiny_next['mode'] == 'full'

summary = lambda r: {k: r[k] for k in ['calls', 'body_bytes', 'output_bytes']}
rows = [
    {'case': 'complete_capture', **summary(full)},
    {'case': 'three_separate_selects', 'calls': sum(r['calls'] for r in separate), 'body_bytes': sum(r['body_bytes'] for r in separate), 'output_bytes': sum(r['output_bytes'] for r in separate)},
    {'case': 'one_batched_select', **summary(batch)},
    {'case': 'three_single_level_indexes', 'calls': sum(r['calls'] for r in indexes), 'body_bytes': sum(r['body_bytes'] for r in indexes), 'output_bytes': sum(r['output_bytes'] for r in indexes)},
    {'case': 'one_depth_two_index', **summary(deep_index)},
]
measurements = {'fixture':'synthetic voice-assistant task, 80 history entries; no model/provider call',
    'source_revision': subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip(),
    'reader_page_bytes':16000,'reader_output_limit':20000,'reader_rows':rows,
    'output_bytes_include_cli_page_metadata':True,'page_envelope_size_can_vary_with_local_path':True,
    'native_projection':{'original_bytes':captures['original']['bytes'],'required_bytes':captures['required']['bytes'],
        'requirements_retained':10,'original_unchanged':hashlib.sha256((out/'projection.json').read_bytes()).hexdigest()==captures['original']['sha256'],
        'shared_row_before_bytes':len(raw(row)),'shared_row_reference_bytes':len(raw(required['agent_todo_summary']['backlog_items'][0]))},
    'cycle_codec':{'current_full_bytes':len(raw(current_full['delivery'])),'delta_bytes':len(raw(warm['delivery'])),
        'same_epoch_mode':warm['delivery']['mode'],'new_epoch_mode':new_epoch['mode'],
        'damaged_delta_recovery':recovery['mode'],'tiny_mode':tiny_next['mode'],'reconstructed_exactly':True},
    'not_measured':['model tokens','prompt cache effects','paid cost','long-horizon effectiveness','native host integration'],
    'live_goal_or_authority_state_changed':False}
save('measurements.json', measurements)
save('manifest.json', {**captures['capture'], 'captures':captures, 'boundary':'Synthetic teaching fixture. Source-based owner and real CLI reader exercised; no native host or model qualification.'})
questions=[
 ('requirement',batch,{'zh':'先读齐目标、约束与验收','en':'Read the goal, constraints and acceptance together'},[arg for path in paths for arg in ['--select',path]]),
 ('index',deep_index,{'zh':'一次看清服务目录的两层结构','en':'Inspect two levels of the provider directory'},['--select','/providers','--list','--list-depth','2']),
 ('result',sample,{'zh':'转写正确，为什么仍没通过？','en':'The transcript is correct. Why did acceptance fail?'},['--select','/evidence/sample-2/result']),
 ('conditions',conditions,{'zh':'并排读两次回传条件','en':'Read both return conditions side by side'},['--select','/evidence/sample-1/conditions','--select','/evidence/sample-2/conditions']),
 ('missing',missing,{'zh':'路径不存在时，整个批次拒绝','en':'A missing path rejects the whole batch'},['--select','/goal/objective','--select','/providers/missing']),
]
save('display-cases.json',{'fixture':'capture.json','capture_sha256':captures['capture']['sha256'],'source':{'goal':goal,'providers':providers,'evidence':evidence,'history':'80 complete entries are retained in the downloadable capture.json'},
    'source_preview_is_complete':False,'cases':[{'id':i,'body':r.get('body',r.get('error')),'questions':q,'args':args} for i,r,q,args in questions]})
if a.private_receipts:
    a.private_receipts.mkdir(parents=True,exist_ok=True)
    (a.private_receipts/'reader-responses.json').write_text(json.dumps(all_receipts,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(measurements,ensure_ascii=False))
