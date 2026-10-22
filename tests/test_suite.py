import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

from hornet.coding.suite import MARKER, SCRIPT, run_suite


DIGEST = 'a' * 64


def reply(value, code=0):
    return {'ok': code == 0, 'exit_code': code,
            'output': MARKER + json.dumps(value) + '\n', 'timed_out': False}


def inventory(counts):
    return reply({'sha256': DIGEST, 'counts': counts})


def batch(index, count, ok=True):
    return reply({'sha256': DIGEST, 'index': index, 'expected': count,
                  'ran': count, 'ok': ok}, 0 if ok else 1)


class SuiteTests(unittest.TestCase):
    def test_all_batches_must_complete_against_one_inventory(self):
        rpc = Mock(side_effect=[inventory([3, 2]), batch(0, 3), batch(1, 2)])
        result = run_suite(rpc)
        self.assertTrue(result['ok'])
        self.assertTrue(result['complete'])
        self.assertEqual((result['tests_run'], result['expected_tests']), (5, 5))
        self.assertEqual(len(result['batches']), 3)
        self.assertIn('Ran 5 tests across 2 batches', result['output'])
        for call in rpc.call_args_list:
            self.assertEqual(call.args[0], 'run_command')
            self.assertLessEqual(call.args[1]['timeout'], 120)
            self.assertLess(len(call.args[1]['argv'][3]), 4000)

    def test_failed_batch_is_retained_and_later_batch_still_runs(self):
        rpc = Mock(side_effect=[inventory([2, 1]), batch(0, 2, False), batch(1, 1)])
        result = run_suite(rpc)
        self.assertFalse(result['ok'])
        self.assertTrue(result['complete'])
        self.assertEqual(result['exit_code'], 1)
        self.assertEqual(result['tests_run'], 3)
        self.assertFalse(result['batches'][1]['result']['ok'])

    def test_missing_receipt_and_timeout_cannot_pass_or_start_next_batch(self):
        for timed_out in (False, True):
            with self.subTest(timed_out=timed_out):
                rpc = Mock(side_effect=[inventory([2, 1]), {
                    'ok': not timed_out, 'exit_code': 124 if timed_out else 0,
                    'output': 'Looks good', 'timed_out': timed_out}])
                result = run_suite(rpc)
                self.assertFalse(result['ok'])
                self.assertFalse(result['complete'])
                self.assertEqual(rpc.call_count, 2)
                self.assertEqual(result['timed_out'], timed_out)

    def test_changed_inventory_or_incomplete_count_is_rejected(self):
        for field, value in [('sha256', 'b' * 64), ('index', 1), ('expected', 4), ('ran', 2), ('ran', True)]:
            with self.subTest(field=field):
                result_value = {'sha256': DIGEST, 'index': 0, 'expected': 3, 'ran': 3, 'ok': True}
                result_value[field] = value
                rpc = Mock(side_effect=[inventory([3]), reply(result_value)])
                self.assertFalse(run_suite(rpc)['ok'])

    def test_whole_suite_budget_does_not_reset_between_batches(self):
        rpc = Mock(side_effect=[inventory([3]), batch(0, 3)])
        clock = Mock(side_effect=[0, 0, 301, 301])
        result = run_suite(rpc, clock=clock)
        self.assertEqual(rpc.call_count, 1)
        self.assertEqual(result['exit_code'], 124)
        self.assertFalse(result['complete'])

    def test_invalid_discovery_stops_before_any_batch(self):
        for counts in ([0, 1], [-1], [True], [1] * 65, 'bad'):
            with self.subTest(counts=counts):
                rpc = Mock(return_value=inventory(counts))
                self.assertFalse(run_suite(rpc)['ok'])
                self.assertEqual(rpc.call_count, 1)

    def local_rpc(self, root):
        def dispatch(name, args):
            self.assertEqual(name, 'run_command')
            process = subprocess.run([sys.executable, *args['argv'][1:]], cwd=root,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                timeout=args['timeout'], env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
            return {'ok': process.returncode == 0, 'exit_code': process.returncode,
                    'output': process.stdout, 'timed_out': False}
        return dispatch

    def test_real_discovery_keeps_module_fixtures_and_covers_every_test(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'tests').mkdir()
            source = ('import unittest\nfrom pathlib import Path\n'
                      'def setUpModule():\n'
                      '    p=Path("fixture_runs.txt")\n'
                      '    assert not p.exists(), "module fixture repeated"\n'
                      '    p.write_text("once")\nclass Cases(unittest.TestCase):\n')
            source += ''.join('    def test_{}(self): self.assertTrue(True)\n'.format(i) for i in range(51))
            (root / 'tests/test_a.py').write_text(source)
            (root / 'tests/test_b.py').write_text('import unittest\nclass Other(unittest.TestCase):\n    def test_one(self): self.assertEqual(1,1)\n')
            result = run_suite(self.local_rpc(root))
            self.assertTrue(result['ok'], result['output'])
            self.assertEqual(result['tests_run'], 52)
            self.assertEqual(len(result['batches']), 3)
            self.assertEqual((root/'fixture_runs.txt').read_text(), 'once')

    def test_real_early_exit_without_receipt_is_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'tests').mkdir()
            (root / 'tests/test_exit.py').write_text('import os, unittest\nclass Cases(unittest.TestCase):\n    def test_exit(self): os._exit(0)\n')
            result = run_suite(self.local_rpc(root))
            self.assertFalse(result['ok'])
            self.assertFalse(result['complete'])
            self.assertIn('Missing suite completion receipt', result['output'])


if __name__ == '__main__':
    unittest.main()
