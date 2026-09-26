"""Semantic integration checks against disposable real LoopX state."""
import json
import tempfile
import time
import unittest
from pathlib import Path

from service import Workspace, cadence, iso
from model import PLAN, EXECUTE, VERIFY, validate


HUMAN = {'kind': 'supplement', 'title': '补充目标用户', 'reason': '需要确定受众',
         'expected': '提供具体受众及一个需求', 'skill': 'product', 'member_id': ''}


class ScriptedModel:
    def __init__(self):
        self.results = []
        self.calls = []

    def ask(self, workspace, prompt, schema, write=False):
        self.calls.append({'prompt': prompt, 'write': write})
        if not self.results:
            raise AssertionError('unexpected model call')
        result = self.results.pop(0)
        if callable(result):
            result = result(workspace)
        validate(result, schema)
        return result

    def cancel(self):
        pass


class WorkspaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='team-workspace-test-')
        cls.model = ScriptedModel()
        cls.app = Workspace(cls.tmp.name, cls.model)
        cls.goal = cls.app.create_goal({'title': 'Test goal', 'objective': 'Verify a real local collaboration loop',
                                      'acceptance': 'Verified local artifact and human contribution'})['id']

    @classmethod
    def tearDownClass(cls):
        cls.app.store.close()
        cls.tmp.cleanup()

    def test_01_execute_verify_and_replay(self):
        app, goal = self.app, self.goal
        todo = app.task(goal, {'title': 'Write result artifact', 'acceptance': 'result.txt contains verified result', 'skill': 'writing'})
        def execute(workspace):
            (workspace / 'result.txt').write_text('verified result')
            return {'outcome': 'completed', 'summary': 'Result written', 'artifacts': ['result.txt'], 'human': HUMAN}
        self.model.results += [execute, {'sufficient': True, 'reason': 'Read result.txt and verified its contents',
                                         'evidence': ['result.txt: verified result'], 'gap': ''}]
        app.execute(goal, next(t for t in app.todos(goal, True) if t['todo_id'] == todo))
        self.assertTrue(next(t for t in app.todos(goal, True) if t['todo_id'] == todo)['done'])
        run = [r for r in app.rows('runs', goal) if r.get('todo_id') == todo][-1]
        self.assertEqual(run['phase'], 'committed')
        # A result replay must not run the model or spend a second slot.
        before = len(self.model.calls)
        ledger = app.loop.runtime / 'goals' / goal / 'runs/index.jsonl'
        spent_before = sum(json.loads(line).get('classification') == 'quota_slot_spent' for line in ledger.read_text().splitlines())
        app.apply_execution(run)
        self.assertEqual(len(self.model.calls), before)
        spent_after = sum(json.loads(line).get('classification') == 'quota_slot_spent' for line in ledger.read_text().splitlines())
        self.assertEqual(spent_after, spent_before)
        self.assertGreater(spent_before, 0)

    def test_02_human_gap_then_sufficient(self):
        app, goal = self.app, self.goal
        parent = app.task(goal, {'title': 'Create audience brief', 'acceptance': 'Use supplied audience', 'skill': 'product'})
        req = app.request_human(goal, HUMAN, parent)
        app.respond(req['id'], {'text': '还没想好'})
        self.model.results.append({'sufficient': False, 'reason': 'Audience remains unknown', 'evidence': [], 'gap': '请给出具体目标用户'})
        app.verify_request(app.store.get('requests', req['id']))
        self.assertEqual(app.store.get('requests', req['id'])['phase'], 'gap')
        self.assertFalse(next(t for t in app.todos(goal, True) if t['todo_id'] == req['todo_id'])['done'])
        app.respond(req['id'], {'kind': 'correction', 'text': '以后先看现有客户资料；本次受众是独立开发者，需要自动跟踪用户反馈。'})
        self.model.results.append({'sufficient': True, 'reason': '明确提供受众与需求', 'evidence': ['最新人类回应'], 'gap': ''})
        app.verify_request(app.store.get('requests', req['id']))
        self.assertEqual(app.store.get('requests', req['id'])['phase'], 'verified')
        self.assertEqual(next(t for t in app.todos(goal, True) if t['todo_id'] == parent)['status'], 'open')
        self.assertEqual(app.rows('lessons', goal)[0]['skill'], 'product')
        self.assertIn('以后先看现有客户资料', json.dumps(app.context(goal), ensure_ascii=False))

    def test_03_observation_cannot_decide(self):
        req = self.app.request_human(self.goal, {**HUMAN, 'kind': 'judge', 'title': '判断方案'})
        self.app.store.patch('requests', req['id'], phase='submitted', evidence=[{'id': 'event', 'text': '打开方案文档'}])
        before = len(self.model.calls)
        self.app.verify_request(self.app.store.get('requests', req['id']))
        self.assertEqual(len(self.model.calls), before)
        self.assertEqual(self.app.store.get('requests', req['id'])['phase'], 'gap')

    def test_04_monitor_baseline_unchanged_changed(self):
        app, goal = self.app, self.goal
        path = app.loop.workspace(goal) / 'metrics.json'
        path.write_text('{"users": 10}')
        monitor = app.add_monitor(goal, {'title': 'Watch metric', 'path': 'metrics.json', 'cadence': '1m'})['id']
        row = next(t for t in app.todos(goal, True) if t['todo_id'] == monitor)
        app.monitor(goal, row)
        before = len(self.model.calls)
        app.loop.update(goal, monitor, next_due_at=iso(time.time() - 1))
        app.monitor(goal, next(t for t in app.todos(goal, True) if t['todo_id'] == monitor))
        self.assertEqual(len(self.model.calls), before)
        path.write_text('{"users": 3}')
        app.loop.update(goal, monitor, next_due_at=iso(time.time() - 1))
        self.model.results.append({'relevant': True, 'reason': 'Metric dropped from 10 to 3',
                                   'task_title': 'Investigate metric decline', 'acceptance': 'Identify cause with evidence'})
        app.monitor(goal, next(t for t in app.todos(goal, True) if t['todo_id'] == monitor))
        self.assertTrue(any(t['text'] == 'Investigate metric decline' for t in app.todos(goal, True)))

    def test_05_restart_preserves_requests_and_run_journal(self):
        self.app.store.put('runs', {'id': 'interrupted-test', 'goal_id': self.goal, 'phase': 'running', 'kind': 'execute'})
        restarted = Workspace(self.tmp.name, ScriptedModel())
        self.assertEqual(restarted.store.get('runs', 'interrupted-test')['phase'], 'interrupted')
        self.assertTrue(restarted.rows('requests', self.goal))
        self.assertTrue(restarted.rows('lessons', self.goal))
        restarted.store.close()

    def test_06_input_boundaries(self):
        for path in ('../GOAL.json', '/etc/passwd'):
            with self.assertRaises(ValueError):
                self.app.artifact(self.goal, path)
        with self.assertRaises(ValueError):
            self.app.add_monitor(self.goal, {'title': 'bad', 'path': '../secret', 'cadence': '1m'})
        with self.assertRaises(ValueError):
            cadence('0m')

    def test_07_continuous_ticks_and_canonical_close(self):
        app = self.app
        goal = app.create_goal({'title': 'Continuous tick test', 'objective': 'Create a verified marker',
                                'acceptance': 'marker.txt contains done'})['id']
        self.model.results.append({'summary': 'Create and verify the marker', 'tasks': [
            {'title': 'Create marker', 'acceptance': 'marker.txt contains done', 'skill': 'writing'}],
            'human': [], 'monitors': [], 'waiting_reason': '', 'goal_satisfied': False, 'evidence': []})
        app.tick(goal)
        self.assertEqual(len(app.todos(goal, True)), 1)
        def execute(workspace):
            (workspace / 'marker.txt').write_text('done')
            return {'outcome': 'completed', 'summary': 'Marker created', 'artifacts': ['marker.txt'], 'human': HUMAN}
        verdict = {'sufficient': True, 'reason': 'Read marker.txt: done', 'evidence': ['marker.txt'], 'gap': ''}
        self.model.results.extend([execute, verdict])
        app.tick(goal)
        self.assertTrue(app.todos(goal, True)[0]['done'])
        self.model.results.extend([{'summary': 'All criteria met', 'tasks': [], 'human': [], 'monitors': [],
                                   'waiting_reason': '', 'goal_satisfied': True, 'evidence': ['marker.txt']}, verdict])
        app.tick(goal)
        self.assertFalse(app.store.get('goals', goal)['enabled'])
        self.assertEqual(app.loop.guard(goal)['pause_cause'], 'goal_stopped')

    def test_08_restart_verifies_existing_work_before_retry(self):
        app = self.app
        goal = app.create_goal({'title': 'Interrupted execution test', 'objective': 'Write a marker once',
                                'acceptance': 'resume.txt contains existing result'})['id']
        todo = app.task(goal, {'title': 'Write resume marker', 'acceptance': 'resume.txt contains existing result', 'skill': 'writing'})
        turn = 'turn_restart_qualification'
        app.loop.claim(goal, todo)
        self.assertTrue(app.loop.guard(goal, turn, todo)['should_run'])
        app.store.put('runs', {'id': turn, 'goal_id': goal, 'todo_id': todo, 'phase': 'running', 'kind': 'execute'})
        (app.loop.workspace(goal) / 'resume.txt').write_text('existing result')
        # Simulate a process disappearing after the artifact write, before result delivery.
        model = ScriptedModel()
        restarted = Workspace(self.tmp.name, model)
        model.results.append({'sufficient': True, 'reason': 'Read existing resume.txt and verified contents',
                              'evidence': ['resume.txt'], 'gap': ''})
        restarted.tick(goal)
        self.assertTrue(restarted.todos(goal, True)[0]['done'])
        self.assertEqual(len(model.calls), 1)
        self.assertFalse(model.calls[0]['write'])
        self.assertEqual(restarted.store.get('runs', turn)['phase'], 'committed')
        restarted.store.close()

    def test_09_conversational_goal_and_member_drafts(self):
        goal_result = {'reply': '我理解了方向。接下来请确认成功标准。',
            'draft': {'title': '提升留存', 'objective': '持续改善新用户留存', 'acceptance': '',
                      'horizon': '三个月', 'boundaries': '外部操作需要授权'},
            'missing': ['成功标准'], 'options': [
                {'label': '按周留存', 'value': '连续四周跟踪次周留存并验证改善', 'field': 'acceptance'},
                {'label': '非法字段', 'value': '不会返回', 'field': 'unknown'}], 'ready': False}
        self.model.results.append(goal_result)
        result = self.app.dialogue({'kind': 'goal', 'message': '希望三个月提升留存',
                                    'draft': {'horizon': '三个月'}, 'history': []})
        self.assertEqual(result['draft']['objective'], '持续改善新用户留存')
        self.assertEqual([o['field'] for o in result['options']], ['acceptance'])
        self.assertIn('不要把建议当成用户确认的事实', self.model.calls[-1]['prompt'])

        member_result = {'reply': '已整理能力，请确认判断范围。',
            'draft': {'name': '测试成员', 'role': '用户研究', 'description': '访谈并整理证据',
                      'skills': ['用户访谈'], 'decision_scopes': []},
            'missing': [], 'options': [], 'ready': True}
        self.model.results.append(member_result)
        result = self.app.dialogue({'kind': 'member', 'message': '我负责用户研究', 'draft': {}, 'history': []})
        self.assertTrue(result['ready'])
        self.assertEqual(result['draft']['decision_scopes'], [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
