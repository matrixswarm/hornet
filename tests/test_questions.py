"""Question identity, interruption recovery and patient autonomous cells."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hornet.cli import main
from hornet.coding import session, questions, autonomous
from hornet.project import HornetError, Project
from test_resume import LocalWorker, call
import test_autonomous as auto_fixtures


class QuestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hornet-question-')
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root, self.guest = base / 'host', base / 'guest'
        self.root.mkdir()
        self.guest.mkdir()
        self.source = self.guest / 'sample.py'
        self.source.write_text('value = 1\n')
        self.worker = LocalWorker(self.guest)
        self.project = Project(self.root)
        self.args = argparse.Namespace(task='Update sample.py after asking which value to use.', task_file=None,
            worker_config='worker.json', model='fixture', base_url='http://localhost:1234/v1',
            protocol='lmstudio-json', max_tokens=1024, timeout=30, max_turns=6, max_tools=6,
            max_repairs=0, context_chars=16000, run_dir='runs', tools=['ask_user', 'write_file'])

    def ask(self, *unused):
        return call('ask_user', {'question': 'Which value should sample.py use?', 'choices': ['2', '3']}, 'ask-1'), {}

    def start(self):
        self.result = session.work(self.project, self.args, self.worker, self.ask)
        self.run = Path(self.result['run_dir'])
        self.ident = self.result['question_id']
        return self.result

    def state(self):
        return session.load(self.project, self.run)

    def resume(self, model=None):
        return session.resume(self.project, argparse.Namespace(run=str(self.run)), self.worker,
                              model or (lambda *args: self.fail('Waiting must not call the model')))

    def test_waits_with_no_deadline_no_worker_calls_and_no_budget_spend(self):
        result = self.start()
        self.assertTrue(result['ok'])
        self.assertFalse(result['completed'])
        self.assertEqual(result['status'], 'waiting_for_user')
        before = self.state()
        self.worker.calls.clear()
        with patch('hornet.coding.session.time.monotonic', return_value=999999999):
            for _ in range(3):
                self.assertEqual(self.resume()['question_id'], self.ident)
        self.assertEqual(self.worker.calls, [])
        self.assertEqual(self.state(), before)
        self.assertEqual(before['tools_used'], 1)
        self.assertEqual(before['turns_used'], 1)

    def test_answer_survives_restart_and_runs_once(self):
        self.start()
        first = questions.respond(self.project, self.run, self.ident, 'Use 2, please.')
        again = questions.respond(self.project, self.run, self.ident, 'Use 2, please.')
        self.assertFalse(first['already_recorded'])
        self.assertTrue(again['already_recorded'])
        count = [0]
        def model(messages, *unused):
            self.assertIn('Use 2, please.', json.dumps(messages))
            count[0] += 1
            if count[0] == 1:
                return call('write_file', {'path': 'sample.py', 'content': 'value = 2\n',
                    'expected_sha256': hashlib.sha256(self.source.read_bytes()).hexdigest()}, 'edit-1'), {}
            return {'role': 'assistant', 'content': 'Done'}, {}
        self.worker = LocalWorker(self.guest)
        result = self.resume(model)
        self.assertTrue(result['ok'])
        self.assertEqual(result['tool_calls'], 2)
        self.assertEqual(self.source.read_text(), 'value = 2\n')
        self.assertEqual(len(self.state()['answered_questions']), 1)
        self.assertFalse(questions.listing(self.project, self.run)['questions'][0]['active'])
        with self.assertRaisesRegex(HornetError, 'not waiting'):
            questions.respond(self.project, self.run, self.ident, 'Too late')

    def test_two_goals_with_same_call_id_are_distinct(self):
        self.start()
        first_run, first_id = self.run, self.ident
        self.start()
        self.assertNotEqual(first_id, self.ident)
        with self.assertRaisesRegex(HornetError, 'not waiting'):
            questions.respond(self.project, self.run, first_id, 'Wrong goal')
        questions.respond(self.project, first_run, first_id, '2')
        self.assertFalse(self.resume()['answer_recorded'])

    def test_cancel_never_becomes_permission(self):
        self.start()
        questions.respond(self.project, self.run, self.ident, 'Leave it alone.', cancel=True)
        self.worker.calls.clear()
        result = self.resume()
        self.assertEqual(result['status'], 'question_cancelled')
        self.assertFalse(result['ok'])
        self.assertEqual(self.worker.calls, [])
        self.assertEqual(self.source.read_text(), 'value = 1\n')
        with self.assertRaisesRegex(HornetError, 'cannot be overwritten'):
            questions.respond(self.project, self.run, self.ident, 'Actually 2')

    def test_answer_cannot_be_silently_replaced(self):
        self.start()
        questions.respond(self.project, self.run, self.ident, '2')
        with self.assertRaisesRegex(HornetError, 'cannot be overwritten'):
            questions.respond(self.project, self.run, self.ident, '3')

    def test_publication_gap_recovers_same_question(self):
        with patch.object(questions, 'publish', side_effect=OSError('injected publication failure')):
            result = session.work(self.project, self.args, self.worker, self.ask)
        self.run = Path(result['run_dir'])
        reference = self.state()['waiting_question']
        self.ident = reference['record']['id']
        result = self.resume()
        self.assertEqual(result['question_id'], self.ident)
        self.assertEqual(questions.listing(self.project, self.run)['questions'][0]['question_id'], self.ident)

    def test_answer_adoption_interrupt_does_not_duplicate_reply_or_tool_usage(self):
        self.start()
        questions.respond(self.project, self.run, self.ident, '2')
        def interrupted(*unused):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.resume(interrupted)
        self.assertEqual(len(self.state()['answered_questions']), 1)
        self.assertEqual(self.state()['tools_used'], 1)
        with self.assertRaises(KeyboardInterrupt):
            self.resume(interrupted)
        self.assertEqual(len(self.state()['answered_questions']), 1)

    def test_answer_keeps_source_drift_guard(self):
        self.start()
        questions.respond(self.project, self.run, self.ident, '2')
        self.source.write_text('outside = 9\n')
        with self.assertRaisesRegex(HornetError, 'workspace files changed'):
            self.resume()

    def test_corrupt_answer_rejected(self):
        self.start()
        questions.respond(self.project, self.run, self.ident, '2')
        path = self.run / 'questions' / self.ident / 'resolution.json'
        value = json.loads(path.read_text())
        value['cell_id'] = 'another-cell'
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(HornetError, 'binding'):
            self.resume()

    def test_corrupt_question_checkpoint_rejected(self):
        self.start()
        path = self.run / 'checkpoint.json'
        state = json.loads(path.read_text())
        state['waiting_question']['record']['question'] = 'Replaced'
        path.write_text(json.dumps(state))
        with self.assertRaises(HornetError):
            self.resume()

    def test_mixed_native_batch_executes_nothing(self):
        def mixed(*unused):
            message, stats = self.ask()
            message['tool_calls'] += call('write_file', {'path':'sample.py', 'content':'wrong',
                'expected_sha256':hashlib.sha256(self.source.read_bytes()).hexdigest()}, 'bad')['tool_calls']
            return message, stats
        result = session.work(self.project, self.args, self.worker, mixed)
        self.assertFalse(result['ok'])
        self.assertIn('only action', result['error'])
        self.assertNotIn('write_file', self.worker.calls)

    def test_free_text_cli_answer_and_listing(self):
        self.start()
        argv = ['--root',str(self.root),'--json']
        self.assertEqual(main(argv + ['questions',str(self.run)]), 0)
        answer = self.root / 'reply.txt'
        answer.write_text('Use 4 instead of the listed choices.', encoding='utf-8')
        self.assertEqual(main(argv + ['answer',str(self.run),self.ident,'--text-file','reply.txt']), 0)
        self.assertEqual(questions.resolution(self.state()['waiting_question'])['text'], answer.read_text())

    def test_invalid_question_and_answers_rejected(self):
        for value in ({}, {'question':''}, {'question':'q','choices':['one']},
                      {'question':'q','choices':['a','a']}, {'question':'q','extra':1}):
            with self.subTest(value=value), self.assertRaises(HornetError):
                questions.arguments(value)
        self.start()
        for text in ('', 'x' * 4001):
            with self.assertRaises(HornetError):
                questions.respond(self.project, self.run, self.ident, text)
        with self.assertRaises(HornetError):
            questions.respond(self.project, self.run, '../escape', 'yes')

    def test_answer_remains_visible_after_recent_exchange_compaction(self):
        self.worker.context_window = 2
        self.args.tools += ['read_file']
        self.args.max_tools = 10
        self.args.max_turns = 10
        self.start()
        questions.respond(self.project, self.run, self.ident, 'Use precisely 2 for this goal.')
        count = [0]
        def model(messages, *unused):
            self.assertIn('Use precisely 2 for this goal.', json.dumps(messages))
            count[0] += 1
            if count[0] <= 4:
                return call('read_file', {'path':'sample.py'}, 'read-' + str(count[0])), {}
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.resume(model)
        contexts = [item['context'] for item in self.state()['history'] if 'context' in item]
        self.assertTrue(any(item['dropped_turns'] > 0 for item in contexts))

    def test_second_question_is_independent_and_first_answer_is_retained(self):
        self.start()
        first = self.ident
        questions.respond(self.project, self.run, first, 'Use 2.')
        def another(messages, *unused):
            self.assertIn('Use 2.', json.dumps(messages))
            return call('ask_user', {'question':'Which comment should accompany it?'}, 'ask-2'), {}
        result = self.resume(another)
        self.assertNotEqual(result['question_id'], first)
        self.assertEqual(result['goal_id'], self.result['goal_id'])
        rows = questions.listing(self.project, self.run)['questions']
        self.assertEqual(sum(item['active'] for item in rows), 1)
        self.assertEqual(len(rows), 2)
        with self.assertRaisesRegex(HornetError, 'not waiting'):
            questions.respond(self.project, self.run, first, 'Old question')

    def test_reply_records_cannot_be_written_through_linked_directories(self):
        from hornet.project import is_link
        self.start()
        target = self.run / 'questions' / self.ident
        self.assertFalse(is_link(target))
        outside = self.root / 'outside'
        outside.mkdir()
        link = self.root / 'linked'
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('Symlink creation unavailable')
        with self.assertRaisesRegex(HornetError, 'linked'):
            questions.respond(self.project, link, self.ident, 'No')


class RecipeQuestionTests(unittest.TestCase):
    setUp = auto_fixtures.AutonomousTests.setUp
    submit = auto_fixtures.AutonomousTests.submit
    write = auto_fixtures.AutonomousTests.write
    run_dir = auto_fixtures.AutonomousTests.run_dir

    def test_intake_waits_and_resumes_with_answer_without_restarting_budget(self):
        asked = [False]
        def model(messages, offered, *options):
            names = {item['function']['name'] for item in offered}
            if 'submit_intake' in names:
                if not asked[0]:
                    asked[0] = True
                    return call('ask_user', {'question':'Should parity_cell.py be the target?'}, 'intake-question'), {}
                self.assertIn('Yes, parity_cell.py.', json.dumps(messages))
            return auto_fixtures.AutonomousTests.model(self, messages, offered, *options)
        result = autonomous.start(self.project, self.args, self.worker, model)
        self.assertEqual(result['status'], 'waiting_for_user')
        run = self.run_dir()
        before = (run/'recipe-checkpoint.json').read_bytes()
        again = autonomous.resume(self.project, argparse.Namespace(run=str(run)), self.worker,
                                  lambda *args:self.fail('No inference while waiting'))
        self.assertEqual(again['question_id'], result['question_id'])
        self.assertEqual((run/'recipe-checkpoint.json').read_bytes(), before)
        questions.respond(self.project, run, result['question_id'], 'Yes, parity_cell.py.')
        final = autonomous.resume(self.project, argparse.Namespace(run=str(run)), self.worker, model)
        self.assertTrue(final['ok'], final)
        state = autonomous.load(self.project, run)
        self.assertEqual(state['roles']['intake']['turns'], 2)
        self.assertEqual(state['roles']['intake']['attempt_turns'], 2)
        self.assertEqual(state['values']['check-even'], False)

    def test_coding_cell_question_preserves_goal_and_cell_identity(self):
        asked = [False]
        def model(messages, offered, *options):
            names = {item['function']['name'] for item in offered}
            if 'write_file' in names and not asked[0]:
                asked[0] = True
                return call('ask_user', {'question':'May I proceed with the requested parity correction?'}, 'cell-question'), {}
            if 'write_file' in names:
                self.assertIn('Yes, keep the frozen parity behavior.', json.dumps(messages))
            return auto_fixtures.AutonomousTests.model(self, messages, offered, *options)
        result = autonomous.start(self.project, self.args, self.worker, model)
        self.assertEqual(result['status'], 'waiting_for_user', result)
        self.assertEqual(result['cell_id'], 'check-even')
        run = self.run_dir()
        questions.respond(self.project, run, result['question_id'], 'Yes, keep the frozen parity behavior.')
        final = autonomous.resume(self.project, argparse.Namespace(run=str(run)), self.worker, model)
        self.assertTrue(final['ok'], final)
        self.assertEqual(final['values']['check-even'], False)
        historical = json.loads((run/'attempts/1/result.json').read_text())
        self.assertEqual(historical['historical_outcome'], 'waiting_for_user')

    def test_continue_command_does_not_answer_a_required_question(self):
        from hornet.coding.control import send
        result = autonomous.start(self.project, self.args, self.worker,
            lambda *args: (call('ask_user', {'question':'Which target?'}, 'required'), {}))
        run = self.run_dir()
        send(self.project, run, 'continue')
        final = autonomous.resume(self.project, argparse.Namespace(run=str(run)), self.worker,
                                  lambda *args:self.fail('Continue is not an answer'))
        self.assertEqual(final['question_id'], result['question_id'])
        self.assertEqual(final['status'], 'waiting_for_user')


class ReconQuestionTests(unittest.TestCase):
    import test_tree as tree_fixtures
    setUp = tree_fixtures.TreeTests.setUp
    submit = tree_fixtures.TreeTests.submit
    run_dir = tree_fixtures.TreeTests.run_dir
    state = tree_fixtures.TreeTests.state

    def test_recon_question_resumes_the_exact_parent_before_splitting(self):
        asked = [False]
        def model(messages, offered, *options):
            names = {item['function']['name'] for item in offered}
            if names & {'submit_goal_checks', 'submit_checks', 'submit_contract_oracle'}:
                self.assertNotIn('ask_user', names)
            if 'submit_route' in names:
                if not asked[0]:
                    asked[0] = True
                    return call('ask_user', {'question':'Should this parity task use helper and public cells?'}, 'recon-question'), {}
                self.assertIn('Use helper and public cells.', json.dumps(messages))
            return self.tree_fixtures.TreeTests.model(self, messages, offered, *options)
        result = autonomous.start(self.project, self.args, self.worker, model)
        self.assertEqual(result['status'], 'waiting_for_user', result)
        self.assertEqual(result['cell_id'], 'check-even')
        run = self.run_dir()
        questions.respond(self.project, run, result['question_id'], 'Use helper and public cells.')
        final = autonomous.resume(self.project, argparse.Namespace(run=str(run)), self.worker, model)
        self.assertTrue(final['ok'], final)
        self.assertIs(final['values']['check-even'], False)
        state = autonomous.load(self.project, run)
        self.assertEqual(state['roles']['recon-check-even']['turns'], 2)
        self.assertEqual(state['roles']['recon-check-even']['attempt_turns'], 2)


if __name__ == '__main__':
    unittest.main()
