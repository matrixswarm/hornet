"""Check authors receive declarations, not implementation bodies or execution."""
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hornet.coding import interface_facts, contract_review, mission
from hornet.project import HornetError


class InterfaceFactsTests(unittest.TestCase):
    SOURCE = ('raise RuntimeError("must not execute")\nclass Calculator:\n'
              '    def double(self, value):\n        return "PRIVATE_BODY"\n'
              '    @staticmethod\n    def static(value=1):\n        pass\n'
              '    @classmethod\n    def create(cls, *, value):\n        pass\n'
              'def calculate(value, /, *, scale=2):\n    return Calculator().double(value)\n')

    def test_exact_declarations_without_bodies(self):
        facts = interface_facts.outline(self.SOURCE)
        by_name = {d['name']: d for d in facts['declarations']}
        self.assertEqual(by_name['calculate']['kind'], 'function')
        self.assertEqual(by_name['Calculator.double']['kind'], 'instance_method')
        self.assertIn('attribute access alone', by_name['Calculator.double']['access'])
        self.assertEqual(by_name['Calculator.static']['kind'], 'static_method')
        self.assertEqual(by_name['Calculator.create']['kind'], 'class_method')
        self.assertEqual(by_name['calculate']['parameters'], [
            {'name': 'value', 'kind': 'positional_only', 'required': True},
            {'name': 'scale', 'kind': 'keyword_only', 'required': False}])
        self.assertNotIn('PRIVATE_BODY', json.dumps(facts))
        self.assertNotIn('RuntimeError', json.dumps(facts))
        self.assertIn('instance.name()', interface_facts.GUIDANCE)
        self.assertIn('bound method', contract_review.review_prompt('submit_checks'))

    def test_bounded_declarations_and_custom_decorators(self):
        facts = interface_facts.outline(''.join('def f%d():\n    pass\n' % i for i in range(40)))
        self.assertEqual(len(facts['declarations']), 32)
        self.assertTrue(facts['truncated'])
        facts = interface_facts.outline('class C:\n    @custom\n    def f(self):\n        pass\n')
        self.assertEqual(facts['declarations'][0]['kind'], 'decorated_method')

    def test_capture_binds_source_and_reports_missing_files(self):
        source = self.SOURCE.encode()
        expected = hashlib.sha256(source).hexdigest()
        class Worker:
            def call(self, name, arguments):
                return {'ok': True, 'content_base64': base64.b64encode(source).decode()}
        snap = {'files': {'calc.py': expected}}
        facts = interface_facts.capture(Worker(), ['new.py', 'calc.py'], snap)
        self.assertEqual(facts['files'][0]['source_sha256'], expected)
        self.assertIn('unavailable', facts['files'][1])
        with self.assertRaisesRegex(HornetError, 'Source changed'):
            interface_facts.capture(Worker(), ['calc.py'], {'files': {'calc.py': '0'*64}})
        bounded = interface_facts.capture(Worker(), ['f%d.py' % i for i in range(12)], {'files': {}})
        self.assertEqual(len(bounded['files']), 8)
        self.assertEqual(bounded['omitted_files'], 4)

    def test_facts_reach_review_and_blind_oracle_without_proposed_answers(self):
        facts = {'version': 1, 'files': [{'path': 'calc.py', **interface_facts.outline(self.SOURCE)}]}
        state = {'roles': {'verifier-double': {'interface_facts': facts}},
                 'goal': 'Double integers', 'goal_sha256': 'goal'}
        candidate = {'checks': [{'id': 'double', 'python': 'from calc import calculate\nassert calculate(6) == 12'}]}
        packet = contract_review.packet(state, 'verifier-double', 'submit_checks', candidate, None, {})
        self.assertEqual(packet['interface_facts'], facts)
        blind = contract_review.oracle_packet(packet)
        self.assertEqual(blind['interface_facts'], facts)
        self.assertNotIn('candidate', blind)
        self.assertNotIn('proposed', blind['calls'][0])

    def test_saved_facts_require_matching_artifact_and_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory).resolve()
            policy = run / 'contract-policy.json'
            mission.immutable(policy, contract_review.POLICY)
            path = run / 'roles' / 'verifier-double' / 'interfaces.json'
            facts = {'version': 1, 'files': [], 'omitted_files': 0}
            mission.immutable(path, facts)
            record = {'interface_facts': facts, 'interface_facts_ref': mission.reference(run, path)}
            state = {'roles': {'verifier-double': record}, 'cells': {},
                     'contract_policy': mission.reference(run, policy)}
            contract_review.validate(run, state)
            for key in ('interface_facts', 'interface_facts_ref'):
                saved = record.pop(key)
                with self.assertRaisesRegex(HornetError, 'lost its binding'):
                    contract_review.validate(run, state)
                record[key] = saved
            record['interface_facts'] = dict(facts, omitted_files=1)
            with self.assertRaisesRegex(HornetError, 'evidence changed'):
                contract_review.validate(run, state)
            record['interface_facts'] = facts
            path.write_text(json.dumps(dict(facts, omitted_files=2)), encoding='utf-8')
            with self.assertRaisesRegex(HornetError, 'evidence changed'):
                contract_review.validate(run, state)

    def test_direct_method_scalar_checks_rejected_with_qualified_aliases(self):
        facts = {'files': [{'path': 'pkg/calc.py', **interface_facts.outline(self.SOURCE)}]}
        for source in (
            'from pkg.calc import Calculator\nc = Calculator()\nassert c.double == 12',
            'from pkg.calc import Calculator as C\nassert C().double == 12',
            'import pkg.calc as api\nc = api.Calculator()\nassert 12 == c.double',
            'import pkg.calc\nc = pkg.calc.Calculator()\nalias = c\nassert alias.double is True',
        ):
            with self.subTest(source=source), self.assertRaisesRegex(HornetError, 'ordinary method'):
                interface_facts.reject_method_value_checks({'checks': [{'id': 'behavior', 'python': source}]}, facts)

    def test_method_calls_and_uncertain_types_are_not_misclassified(self):
        facts = {'files': [{'path': 'calc.py', **interface_facts.outline(self.SOURCE)}]}
        for source in (
            'from calc import Calculator\nc = Calculator()\nassert c.double(6) == 12',
            'from calc import Calculator\nc = Calculator()\nassert callable(c.double)',
            'from calc import Calculator\nc = Calculator()\nc = factory()\nassert c.double == 12',
            'from calc import Calculator\nc = Calculator()\nc.double = 12\nassert c.double == 12',
            'from other import Calculator\nc = Calculator()\nassert c.double == 12',
            'from calc import Calculator\nc = Calculator()\nassert c.static == 12',
            'from calc import Calculator\nc = Calculator()\nif condition:\n    c = factory()\nassert c.double == 12',
            'from calc import Calculator\nc = Calculator()\nalias = c\nc.double = 12\nassert alias.double == 12',
            'from calc import Calculator\nc = Calculator()\nmutate(c)\nassert c.double == 12',
            'from calc import Calculator\nc = Calculator()\nc.mutate()\nassert c.double == 12',
            'from calc import Calculator\nc = Calculator()\nr = mutate(c)\nassert c.double == 12',
            'from calc import Calculator\nc = Calculator()\nassert mutate(c) == 1\nassert c.double == 12',
            'from calc import Calculator\nassert Calculator(mutate()).double == 12',
        ):
            with self.subTest(source=source):
                interface_facts.reject_method_value_checks({'checks': [{'id': 'behavior', 'python': source}]}, facts)

    def test_dynamic_classes_do_not_claim_a_runtime_method_object(self):
        for source in (
            'class C:\n    def __init__(self):\n        self.name = "v1"\n    def name(self):\n        return "v1"\n',
            'class C(Base):\n    def name(self):\n        return "v1"\n',
            'class C(metaclass=Meta):\n    def name(self):\n        return "v1"\n',
            '@decorate\nclass C:\n    def name(self):\n        return "v1"\n',
            'class C:\n    def __getattribute__(self, key):\n        return "v1"\n    def name(self):\n        return "v1"\n',
            'class C:\n    def name(self):\n        return "v1"\n    name = "v1"\n',
        ):
            with self.subTest(source=source):
                outlined = interface_facts.outline(source)
                declaration = next(d for d in outlined['declarations'] if d['name'] == 'C.name')
                self.assertEqual(declaration['attribute_lookup'], 'dynamic')
                facts = {'files': [{'path': 'api.py', **outlined}]}
                interface_facts.reject_method_value_checks({'checks': [{'id': 'name',
                    'python': 'from api import C\nobj = C()\nassert obj.name == "v1"'}]}, facts)

    def test_declared_method_calls_keep_only_an_ambiguity_hint(self):
        facts = {'files': [{'path': 'calc.py', **interface_facts.outline(self.SOURCE)}]}
        for source in (
            'from calc import Calculator\nc = Calculator()\nassert c.double(5) == 10\nassert c.double == 12',
            'from calc import Calculator\nc = Calculator()\nc.double(5)\nc.double(0)\nassert c.double == 12',
            'from calc import Calculator\nc = Calculator()\nr = c.double(5)\nassert c.double == 12',
        ):
            with self.subTest(source=source), self.assertRaisesRegex(HornetError, 'lookup is now ambiguous'):
                interface_facts.reject_method_value_checks({'checks': [{'id': 'behavior', 'python': source}]}, facts)
        for tail in ('assert c.double(6) == 12', 'c = factory()\nassert c.double == 12',
                     'mutate(c)\nassert c.double == 12', 'c.double = 12\nassert c.double == 12',
                     'if condition:\n    c = factory()\nassert c.double == 12'):
            source = 'from calc import Calculator\nc = Calculator()\nc.double(5)\n' + tail
            interface_facts.reject_method_value_checks({'checks': [{'id': 'behavior', 'python': source}]}, facts)

    def test_stateful_attribute_changes_are_not_claimed_impossible(self):
        source = 'class C:\n    def configure(self):\n        self.name = "v1"\n    def name(self):\n        pass\n'
        facts = {'files': [{'path': 'api.py', **interface_facts.outline(source)}]}
        prefix = 'from api import C\nc = C()\nc.configure()\n'
        with self.assertRaisesRegex(HornetError, 'lookup is now ambiguous'):
            interface_facts.reject_method_value_checks({'checks': [{'id': 'state',
                'python': prefix + 'assert c.name == "v1"'}]}, facts)
        # Explicit dynamic lookup is left to requirement review and execution.
        interface_facts.reject_method_value_checks({'checks': [{'id': 'state',
            'python': prefix + 'assert getattr(c, "name") == "v1"'}]}, facts)

    def test_legacy_facts_without_lookup_evidence_remain_uncertain(self):
        facts = {'files': [{'path': 'api.py', 'declarations': [{'name': 'C.name', 'kind': 'instance_method'}]}]}
        interface_facts.reject_method_value_checks({'checks': [{'id': 'name',
            'python': 'from api import C\nassert C().name == "v1"'}]}, facts)

    def test_bad_method_check_is_corrected_before_freezing_even_without_model_review(self):
        import copy
        import test_autonomous as fixtures
        from hornet.coding import autonomous
        fixture = fixtures.AutonomousTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.args.registry_only = True
        fixture.source.write_text('class Contract:\n    def name(self):\n        return "v1"\n\n' + fixture.source.read_text())
        real_call = fixture.worker.call
        def worker_call(name, arguments=None):
            if name == 'read_source':
                raw = fixture.source.read_bytes()
                return {'ok': True, 'content_base64': base64.b64encode(raw).decode()}
            return real_call(name, arguments)
        submissions = []
        def request(messages, tools, *rest):
            names = {t['function']['name'] for t in tools}
            if 'submit_checks' in names:
                proposal = copy.deepcopy(fixture.checks)
                code = ('\nfrom parity_cell import Contract\nobj = Contract()\nassert obj.name() == "v1"'
                        '\nassert obj.name{} == "v1"')
                proposal['checks'][0]['python'] += code.format('()' if submissions else '')
                submissions.append(proposal)
                return fixture.submit('submit_checks', proposal, 'check-' + str(len(submissions)))
            if names.intersection({'submit_intake', 'submit_plan', 'submit_goal_checks'}):
                return fixture.model(messages, tools, *rest)
            raise KeyboardInterrupt()
        with patch.object(fixture.worker, 'call', side_effect=worker_call), self.assertRaises(KeyboardInterrupt):
            autonomous.start(fixture.project, fixture.args, fixture.worker, request)
        self.assertEqual(len(submissions), 2)
        role = fixture.state()['roles']['verifier-check-even']
        self.assertTrue(any('lookup is now ambiguous' in h.get('result', {}).get('error', '') for h in role['history']))
        frozen = json.loads((fixture.run_dir() / 'cells/check-even/acceptance.json').read_text())
        self.assertIn('obj.name()', frozen['checks'][0]['python'])


if __name__ == '__main__':
    unittest.main()
