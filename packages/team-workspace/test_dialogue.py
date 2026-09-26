"""Focused tests for conversational Goal/member drafting without runtime side effects."""
import tempfile
import unittest

from model import validate
from service import Workspace


class ScriptedModel:
    def __init__(self, results):
        self.results = list(results)
        self.prompts = []

    def ask(self, workspace, prompt, schema, write=False):
        self.prompts.append(prompt)
        result = self.results.pop(0)
        validate(result, schema)
        return result

    def cancel(self):
        pass


class DialogueTest(unittest.TestCase):
    def test_goal_options_are_contextual_and_field_bounded(self):
        result = {'reply': '我理解了方向。接下来请确认成功标准。',
            'draft': {'title': '提升留存', 'objective': '持续改善新用户留存', 'acceptance': '',
                      'horizon': '三个月', 'boundaries': '外部操作需要授权'},
            'missing': ['成功标准'], 'options': [
                {'label': '按周留存', 'value': '连续四周跟踪次周留存并验证改善', 'field': 'acceptance'},
                {'label': '非法字段', 'value': '不会返回', 'field': 'unknown'}], 'ready': False}
        with tempfile.TemporaryDirectory(prefix='team-dialogue-test-') as root:
            model = ScriptedModel([result])
            app = Workspace(root, model, loop=object())
            actual = app.dialogue({'kind': 'goal', 'message': '希望三个月提升留存',
                                   'draft': {'horizon': '三个月'}, 'history': []})
            self.assertEqual(actual['draft']['objective'], '持续改善新用户留存')
            self.assertEqual([o['field'] for o in actual['options']], ['acceptance'])
            self.assertIn('不要把建议当成用户确认的事实', model.prompts[-1])
            app.store.close()

    def test_member_keeps_unstated_authority_empty(self):
        result = {'reply': '已整理能力，请确认判断范围。',
            'draft': {'name': '测试成员', 'role': '用户研究', 'description': '访谈并整理证据',
                      'skills': ['用户访谈'], 'decision_scopes': []},
            'missing': [], 'options': [], 'ready': True}
        with tempfile.TemporaryDirectory(prefix='team-dialogue-test-') as root:
            model = ScriptedModel([result])
            app = Workspace(root, model, loop=object())
            actual = app.dialogue({'kind': 'member', 'message': '我负责用户研究',
                                   'draft': {}, 'history': []})
            self.assertTrue(actual['ready'])
            self.assertEqual(actual['draft']['decision_scopes'], [])
            app.store.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
