"""Focused working history retains complete exchanges and leaves audit data intact."""
import copy
import json
import unittest

from hornet.coding.context import focus_messages, history_chars, short_result
from hornet.project import HornetError


class ContextTests(unittest.TestCase):
    def base(self):
        return [{'role': 'system', 'content': 'Keep the ticket focused.'},
                {'role': 'user', 'content': 'Fix exactly one bug.'}]

    def turn(self, number, size=2000):
        return [{'role': 'assistant', 'content': '', 'tool_calls': [
            {'id': str(number), 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{"path":"a.py"}'}}]},
            {'role': 'tool', 'name': 'read_file', 'tool_call_id': str(number), 'content': str(number) + 'x' * size}]

    def test_small_history_stays_complete_and_is_not_mutated(self):
        messages = self.base() + self.turn(1)
        before = copy.deepcopy(messages)
        active, info = focus_messages(messages, [], [], 8000)
        self.assertEqual(active, messages)
        self.assertEqual(messages, before)
        self.assertEqual(info['dropped_turns'], 0)

    def test_oldest_whole_turns_dropped_ticket_and_latest_pair_retained(self):
        messages = self.base()
        for number in range(6):
            messages += self.turn(number)
        before = copy.deepcopy(messages)
        history = [{'tool': 'edit_file', 'arguments': {'path': 'a.py'}, 'result': {'ok': True}}]
        active, info = focus_messages(messages, [], history, 8000)
        self.assertEqual(active[:2], messages[:2])
        self.assertGreater(info['dropped_turns'], 0)
        self.assertEqual(active[-2:], messages[-2:])
        self.assertLessEqual(history_chars(active, []), 8000)
        self.assertIn('Re-read source', active[2]['content'])
        self.assertEqual(messages, before)
        for index, item in enumerate(active):
            if item['role'] == 'tool':
                self.assertEqual(active[index - 1]['tool_calls'][0]['id'], item['tool_call_id'])

    def test_large_read_omits_source_with_hash_and_reread_hint(self):
        metadata = {'ok': True, 'path': 'a.py', 'sha256': 'a' * 64, 'read_ref': 'r_123456789012', 'total_lines': 200}
        message = {'role': 'tool', 'name': 'read_file', 'content': json.dumps(metadata) + '\n\nNumbered file content:\n' + 'SOURCE' * 3000}
        active = short_result(message, 2600)
        result = json.loads(active['content'])
        self.assertTrue(result['context_omitted'])
        self.assertEqual(result['sha256'], 'a' * 64)
        self.assertEqual(result['read_ref'], 'r_123456789012')
        self.assertNotIn('SOURCE', active['content'])
        self.assertIn('Read fewer', result['hint'])
        self.assertIn('SOURCE', message['content'])

    def test_command_keeps_failure_tail_with_explicit_omission_marker(self):
        message = {'role': 'tool', 'name': 'run_tests', 'content': '{"ok":false,"exit_code":1}\n\nCommand output:\n' + 'old\n' * 5000 + 'FAILED (errors=1)'}
        active = short_result(message, 2600)
        self.assertTrue(active['content'].endswith('FAILED (errors=1)'))
        self.assertIn('earlier output omitted', active['content'])
        self.assertLessEqual(len(active['content']), 2600)

    def test_oversized_ticket_and_latest_assistant_stop_before_request(self):
        with self.assertRaises(HornetError):
            focus_messages([{'role': 'system', 'content': 's'}, {'role': 'user', 'content': 'x' * 9000}], [], [], 8000)
        messages = self.base() + [{'role': 'assistant', 'content': 'x' * 9000}]
        with self.assertRaises(HornetError):
            focus_messages(messages, [], [], 8000)

    def test_tool_definitions_count_and_invalid_budget_rejected(self):
        with self.assertRaises(HornetError):
            focus_messages(self.base(), [{'schema': 'x' * 9000}], [], 8000)
        for limit in (True, 7999, 200001):
            with self.subTest(limit=limit), self.assertRaises(HornetError):
                focus_messages(self.base(), [], [], limit)

    def test_latest_observation_compacts_to_fit_beside_large_tool_schema(self):
        messages = self.base() + self.turn(1, size=2000)
        before = copy.deepcopy(messages)
        tools = [{'schema': 'x' * 6500}]
        active, info = focus_messages(messages, tools, [], 8000, 2)
        self.assertLessEqual(info['history_chars'], 8000)
        self.assertEqual(active[-2], messages[-2])
        self.assertTrue(json.loads(active[-1]['content'])['context_omitted'])
        self.assertEqual(messages, before)


if __name__ == '__main__':
    unittest.main()
