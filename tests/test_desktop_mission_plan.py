"""Model-authored decomposition, explicit approval, and leaf-scoped execution."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from hornet.desktop import automation as api
from hornet.desktop.mission_plan import validate_goals, validate_goal_links
from hornet.desktop.mission_resume import approve_mission_plan, resume_mission
from hornet.desktop.workspace import Workspace, write_json


class MissionPlanTests(unittest.TestCase):
    def setUp(self):
        self.plan = {'existing_files': ['main.py'], 'new_files': ['helper.py'], 'explanation': 'Implement two small changes',
            'goals': [
                {'id': 'root', 'parent': None, 'goal': 'Update the small example application', 'files': [],
                 'success_criteria': 'Both source changes are present and reviewed', 'depends_on': []},
                {'id': 'helper', 'parent': 'root', 'goal': 'Create helper value module', 'files': ['helper.py'],
                 'success_criteria': 'helper.py exports the expected VALUE constant', 'depends_on': []},
                {'id': 'main', 'parent': 'root', 'goal': 'Update the main example value', 'files': ['main.py'],
                 'success_criteria': 'main.py contains the requested VALUE constant', 'depends_on': ['helper']}]
        }

    @staticmethod
    def describe(payload):
        return {'summaries': {name: 'Purpose of ' + name for name in payload['files']},
                'blueprint': {'purpose': 'Test application'}}

    def workspace(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        ws = Workspace(temp.name)
        base = ws.create('demo')
        (ws.revision('demo', base) / 'main.py').write_text('VALUE = 1\n')
        api.make_initialization_draft(ws, 'demo', base, self.describe)
        return ws, api.accept_initialization(ws, 'demo')

    def prepare(self, ws, base, confirm=True):
        return api.prepare_mission(ws, 'demo', base, 'Update the example', 'http://localhost', 'model',
                                   require_plan_confirmation=confirm, require_goal_decomposition=True)

    def test_valid_tree_has_stable_ids_scopes_and_dependencies(self):
        goals = validate_goals(self.plan, required=True)
        self.assertEqual(goals, self.plan['goals'])
        self.assertEqual(goals[-1]['depends_on'], ['helper'])
        self.assertEqual(self.plan['goals'][0]['parent'], None)

    def test_rejects_missing_duplicate_unknown_and_cyclic_ids(self):
        for change in ('missing_parent', 'duplicate', 'parent_cycle', 'dependency_cycle', 'missing_dependency'):
            with self.subTest(change=change):
                plan = copy.deepcopy(self.plan)
                if change == 'missing_parent':
                    plan['goals'][1]['parent'] = 'unknown'
                elif change == 'duplicate':
                    plan['goals'][2]['id'] = 'helper'
                elif change == 'parent_cycle':
                    plan['goals'][1]['parent'] = 'main'
                    plan['goals'][2]['parent'] = 'helper'
                elif change == 'dependency_cycle':
                    plan['goals'][1]['depends_on'] = ['main']
                else:
                    plan['goals'][2]['depends_on'] = ['unknown']
                with self.assertRaises(ValueError):
                    validate_goals(plan, required=True)

    def test_rejects_oversized_and_unscoped_leaf_goals(self):
        for change in ('count', 'depth', 'scope', 'empty_leaf', 'criteria', 'uncovered_file'):
            with self.subTest(change=change):
                plan = copy.deepcopy(self.plan)
                if change == 'count':
                    plan['goals'] = plan['goals'] * 14
                elif change == 'depth':
                    plan['goals'] = [dict(self.plan['goals'][1], id='g' + str(i),
                        parent=('g' + str(i - 1) if i else None),
                        files=(['main.py', 'helper.py'] if i == 5 else [])) for i in range(6)]
                elif change == 'scope':
                    plan['goals'][1]['files'] = ['outside.py']
                elif change == 'empty_leaf':
                    plan['goals'][1]['files'] = []
                elif change == 'criteria':
                    plan['goals'][1]['success_criteria'] = 'done'
                else:
                    plan['new_files'].append('uncovered.py')
                with self.assertRaises(ValueError):
                    validate_goals(plan, required=True)

    def test_leaf_links_are_required_and_cannot_expand_scope(self):
        goals = validate_goals(self.plan, required=True)
        proposal = {'files': {'helper.py': 'VALUE = 42\n'}, 'goal_links': {'helper.py': ['helper']}}
        validate_goal_links(proposal, goals)
        for links in ({}, {'helper.py': ['main']}, {'helper.py': ['root']}, {'helper.py': ['unknown']}):
            with self.subTest(links=links), self.assertRaises(ValueError):
                validate_goal_links(dict(proposal, goal_links=links), goals)

    def test_optional_confirmation_does_not_disable_required_decomposition(self):
        ws, base = self.workspace()
        old_plan = dict(self.plan)
        old_plan.pop('goals')
        with patch('hornet.desktop.automation.request_json', return_value=old_plan), \
             patch('hornet.desktop.registry_mission.propose_with_tools') as proposal:
            with self.assertRaisesRegex(ValueError, 'missing goals|1 to 40 bounded goals'):
                self.prepare(ws, base, confirm=False)
            proposal.assert_not_called()

    def test_plan_review_stops_before_edits_and_requires_bound_approval(self):
        ws, base = self.workspace()
        proposed = {'summary': 'Two changes', 'files': {'helper.py': 'VALUE = 42\n', 'main.py': 'VALUE = 42\n'},
                    'goal_links': {'helper.py': ['helper'], 'main.py': ['main']}}
        with patch('hornet.desktop.automation.request_json', return_value=self.plan) as planner, \
             patch('hornet.desktop.registry_mission.propose_with_tools', side_effect=[
                 ({'summary': 'Helper change', 'files': {'helper.py': 'VALUE = 42\n'}, 'goal_links': {'helper.py': ['helper']}}, []),
                 ({'summary': 'Main change', 'files': {'main.py': 'VALUE = 42\n'}, 'goal_links': {'main.py': ['main']}}, [])]) as proposal, \
             patch('hornet.desktop.automation.model_describer', return_value=self.describe):
            record = self.prepare(ws, base)
            self.assertEqual(record['state'], 'plan_review')
            self.assertTrue(record['resumable'])
            self.assertEqual(record['proposed_goals'], self.plan['goals'])
            proposal.assert_not_called()
            self.assertEqual(len(ws.revisions('demo')), 2)
            self.assertEqual(resume_mission(ws, 'demo', record['id'], 'http://localhost', 'model')['state'], 'plan_review')
            proposal.assert_not_called()
            approved = approve_mission_plan(ws, 'demo', record['id'])
            self.assertIn('plan_sha256', approved['plan_approval'])
            result = resume_mission(ws, 'demo', record['id'], 'http://localhost', 'model')
            self.assertEqual(result['state'], 'review')
            self.assertEqual(planner.call_count, 1)
            self.assertEqual(proposal.call_count, 2)
            self.assertEqual([call.args[1]['active_goal_id'] for call in proposal.call_args_list], ['helper', 'main'])
            self.assertEqual(proposal.call_args.args[1]['goal_decomposition'], self.plan['goals'])
            self.assertTrue(all(item['status'] == 'pending_human_review' for item in result['goal_evidence']))
            snapshot = json.loads((ws.revision('demo', result['final_revision']) / '.hornet-meta' / 'goals.json').read_text())
            self.assertEqual(snapshot['operation_snapshot']['proposed_goals'], self.plan['goals'])
            self.assertEqual(api.require_ready(ws, 'demo')['accepted_revision'], base)

    def test_approval_rejects_changed_source_or_reviewed_graph(self):
        ws, base = self.workspace()
        with patch('hornet.desktop.automation.request_json', return_value=self.plan):
            record = self.prepare(ws, base)
        path = ws.project('demo') / 'missions' / (record['id'] + '.json')
        changed = copy.deepcopy(record)
        changed['proposed_goals'][1]['goal'] = 'Different unreviewed objective'
        write_json(path, changed)
        with self.assertRaisesRegex(ValueError, 'decomposition changed'):
            approve_mission_plan(ws, 'demo', record['id'])
        write_json(path, record)
        (ws.revision('demo', base) / 'main.py').write_text('VALUE = 2\n')
        with self.assertRaisesRegex(ValueError, 'inputs changed'):
            approve_mission_plan(ws, 'demo', record['id'])

    def test_actual_serialized_planner_prompt_requests_recursive_bounded_goals(self):
        ws, base = self.workspace()
        response = MagicMock()
        response.read.return_value = json.dumps({'choices': [{'finish_reason': 'stop',
            'message': {'content': json.dumps(self.plan)}}]}).encode()
        with patch('urllib.request.build_opener') as build, \
             patch('hornet.desktop.registry_mission.propose_with_tools') as propose:
            build.return_value.open.return_value.__enter__.return_value = response
            result = self.prepare(ws, base)
            body = json.loads(build.return_value.open.call_args.args[0].data)
            system = body['messages'][0]['content']
            self.assertIn('Break oversized goals down recursively', system)
            self.assertIn('no more than 40 nodes, depth at most 5', system)
            self.assertIn('success_criteria', system)
            self.assertIn('HORNET OPERATING CONTRACT', system)
            self.assertIn('root/plan', system)
            self.assertEqual(result['state'], 'plan_review')
            propose.assert_not_called()

    def test_actual_atomic_editor_ties_every_edit_to_scoped_leaf(self):
        ws, base = self.workspace()
        edits = {'summary': 'Two scoped changes', 'edits': [
            {'uri': 'files.text.atomic_edit', 'goal_id': 'helper',
             'arguments': {'path': 'helper.py', 'action': 'create', 'new_text': 'VALUE = 42\n'}},
            {'uri': 'files.text.atomic_edit', 'goal_id': 'main',
             'arguments': {'path': 'main.py', 'action': 'replace', 'target_text': 'VALUE = 1', 'new_text': 'VALUE = 42'}}]}
        leaf_edits = [dict(edits, edits=[edit]) for edit in edits['edits']]
        with patch('hornet.desktop.automation.request_json', side_effect=[self.plan] + leaf_edits), \
             patch('hornet.desktop.automation.model_describer', return_value=self.describe):
            record = self.prepare(ws, base, confirm=False)
        self.assertEqual(record['goal_links'], {'helper.py': ['helper'], 'main.py': ['main']})
        evidence = {item['id']: item for item in record['goal_evidence']}
        self.assertEqual(set(evidence['main']['artifact_sha256']), {'main.py'})
        self.assertEqual(set(evidence['helper']['artifact_sha256']), {'helper.py'})
        self.assertTrue(all(item['status'] != 'complete' for item in evidence.values()))
        self.assertEqual(api.require_ready(ws, 'demo')['accepted_revision'], base)

    def test_interrupted_second_leaf_resumes_without_requesting_or_rewriting_first(self):
        ws, base = self.workspace()
        first = ({'summary': 'Helper', 'files': {'helper.py': 'VALUE = 42\n'},
                  'goal_links': {'helper.py': ['helper']}}, [])
        second = ({'summary': 'Main', 'files': {'main.py': 'VALUE = 42\n'},
                   'goal_links': {'main.py': ['main']}}, [])
        with patch('hornet.desktop.automation.request_json', return_value=self.plan) as planner, \
             patch('hornet.desktop.registry_mission.propose_with_tools', side_effect=[first, ValueError('leaf two offline')]) as propose, \
             patch('hornet.desktop.automation.model_describer', return_value=self.describe):
            with self.assertRaisesRegex(ValueError, 'leaf two offline'):
                self.prepare(ws, base, confirm=False)
            path = next((ws.project('demo') / 'missions').glob('*.json'))
            interrupted = json.loads(path.read_text())
            self.assertEqual([item['id'] for item in interrupted['leaf_results']], ['helper'])
            first_revision = interrupted['leaf_results'][0]['revision']
            before = ws.revisions('demo')
            propose.side_effect = [second]
            result = resume_mission(ws, 'demo', interrupted['id'], 'http://localhost', 'model')
            self.assertEqual(planner.call_count, 1)
            self.assertEqual(propose.call_count, 3)
            self.assertEqual(result['leaf_results'][0]['revision'], first_revision)
            self.assertEqual(len(ws.revisions('demo')), len(before) + 2)
            self.assertEqual([item['id'] for item in result['leaf_results']], ['helper', 'main'])
            calls = [call.args[1] for call in propose.call_args_list]
            self.assertEqual([call['active_goal_id'] for call in calls], ['helper', 'main', 'main'])
            self.assertEqual(set(calls[0]['new_files']), {'helper.py'})
            self.assertEqual(set(calls[1]['files']), {'main.py'})
            self.assertEqual(calls[1]['prior_goal_edits'], ['helper'])

    def test_interrupted_leaf_publication_is_reconciled_before_next_leaf(self):
        ws, base = self.workspace()
        first = ({'summary': 'Helper', 'files': {'helper.py': 'VALUE = 42\n'},
                  'goal_links': {'helper.py': ['helper']}}, [])
        second = ({'summary': 'Main', 'files': {'main.py': 'VALUE = 42\n'},
                   'goal_links': {'main.py': ['main']}}, [])
        original, published = ws.fork, []
        def interrupted(*args, **kwargs):
            revision = original(*args, **kwargs)
            published.append(revision)
            raise RuntimeError('lost first leaf publication')
        with patch('hornet.desktop.automation.request_json', return_value=self.plan), \
             patch('hornet.desktop.registry_mission.propose_with_tools', side_effect=[first, second]) as propose, \
             patch('hornet.desktop.automation.model_describer', return_value=self.describe):
            with patch.object(ws, 'fork', side_effect=interrupted):
                with self.assertRaisesRegex(RuntimeError, 'lost first leaf publication'):
                    self.prepare(ws, base, confirm=False)
            path = next((ws.project('demo') / 'missions').glob('*.json'))
            record = json.loads(path.read_text())
            result = resume_mission(ws, 'demo', record['id'], 'http://localhost', 'model')
            self.assertEqual(propose.call_count, 2)
            self.assertEqual(result['leaf_results'][0]['revision'], published[0])
            self.assertEqual(len(result['candidates']), 3)
            snapshot = json.loads((ws.revision('demo', published[0]) / '.hornet-meta' / 'goals.json').read_text())
            self.assertEqual(snapshot['operation']['checkpoint'], 'root/goals/helper/publish')


    def test_graph_validation_builds_context_per_leaf_not_for_all_files(self):
        ws, base = self.workspace()
        original = ws.mission_context
        def bounded(project, revision, selected, mission, new_files=None, **kwargs):
            self.assertLessEqual(len(selected) + len(new_files or []), 1)
            self.assertTrue(kwargs.get('scoped_shadows'))
            return original(project, revision, selected, mission, new_files, **kwargs)
        with patch.object(ws, 'mission_context', side_effect=bounded), \
             patch('hornet.desktop.automation.request_json', return_value=self.plan):
            self.assertEqual(self.prepare(ws, base)['state'], 'plan_review')




    def test_normalized_model_labels_survive_saved_review_resume_and_approval(self):
        from hornet.desktop.goal_ids import canonicalize_goal_ids
        ws,base=self.workspace()
        raw=copy.deepcopy(self.plan)
        labels={node['id']:node['id'].upper() + '_LONG_LABEL' * 8 for node in raw['goals']}
        for node in raw['goals']:
            node['id']=labels[node['id']]
            node['parent']=labels.get(node['parent'])
            node['depends_on']=[labels[dep] for dep in node['depends_on']]
        expected=canonicalize_goal_ids(raw)
        with patch('hornet.desktop.automation.request_json',return_value=raw) as planner, \
             patch('hornet.desktop.registry_mission.propose_with_tools') as proposal:
            record=self.prepare(ws,base)
            self.assertEqual(record['plan'],expected)
            self.assertEqual(record['proposed_goals'],expected['goals'])
            resumed=resume_mission(ws,'demo',record['id'],'http://localhost','model')
            self.assertEqual(resumed['plan'],expected)
            approved=approve_mission_plan(ws,'demo',record['id'])
            self.assertEqual(approved['plan'],expected)
            self.assertIn('plan_sha256',approved['plan_approval'])
            self.assertEqual(planner.call_count,1)
            proposal.assert_not_called()


    def test_legacy_verification_leaf_resume_never_promotes_empty_edit_to_success(self):
        ws, base = self.workspace()
        self.plan['goals'][1]['goal'] = 'Verify FileStrategyInterface definition'
        # Simulate a plan saved before the fresh-plan lint existed.
        with patch('hornet.desktop.automation.request_json', return_value=self.plan), \
             patch('hornet.desktop.planner_request.reject_read_only_leaves'):
            record = self.prepare(ws, base)
        approve_mission_plan(ws, 'demo', record['id'])
        unchanged_revisions = ws.revisions('demo')
        no_edit = {'summary': 'Verified, no edits needed', 'files': {}, 'goal_links': {}}
        with patch('hornet.desktop.automation.request_json') as planner, \
             patch('hornet.desktop.registry_mission.propose_with_tools', return_value=(no_edit, [])):
            with self.assertRaisesRegex(ValueError, 'no source changes for leaf goal helper'):
                resume_mission(ws, 'demo', record['id'], 'http://localhost', 'model')
            planner.assert_not_called()
        saved = json.loads((ws.project('demo') / 'missions' / (record['id'] + '.json')).read_text())
        self.assertEqual(saved['state'], 'failed')
        self.assertEqual(saved['plan'], self.plan)
        self.assertFalse(saved.get('leaf_results'))
        self.assertEqual(ws.revisions('demo'), unchanged_revisions)
        self.assertEqual(api.require_ready(ws, 'demo')['accepted_revision'], base)


    def test_read_only_context_survives_review_and_resume_without_edit_authority(self):
        ws, base = self.workspace()
        self.plan['existing_files'] = []
        self.plan['context_files'] = ['main.py']
        self.plan['goals'] = [self.plan['goals'][1]]
        self.plan['goals'][0]['parent'] = None
        def propose(root, context, *args, **kwargs):
            self.assertEqual(context['files'], {})
            self.assertEqual(context['read_only_files']['main.py'].splitlines(), ['VALUE = 1'])
            self.assertEqual(context['new_files'], ['helper.py'])
            return ({'summary': 'Create helper', 'files': {'helper.py': 'VALUE = 2\n'},
                     'goal_links': {'helper.py': ['helper']}}, [])
        with patch('hornet.desktop.automation.request_json', return_value=self.plan) as planner, \
             patch('hornet.desktop.registry_mission.propose_with_tools', side_effect=propose), \
             patch('hornet.desktop.automation.model_describer', return_value=self.describe):
            record = self.prepare(ws, base)
            self.assertEqual(record['plan']['context_files'], ['main.py'])
            approve_mission_plan(ws, 'demo', record['id'])
            result = resume_mission(ws, 'demo', record['id'], 'http://localhost', 'model')
            self.assertEqual(planner.call_count, 1)
            self.assertEqual(result['changed_files'], ['helper.py'])
            self.assertEqual((ws.revision('demo', result['final_revision']) / 'main.py').read_text(), 'VALUE = 1\n')

    def test_context_paths_are_validated_without_goal_assignment(self):
        plan = copy.deepcopy(self.plan)
        plan['context_files'] = ['reference.py']
        self.assertEqual(validate_goals(plan, required=True), self.plan['goals'])
        for names in (['main.py'], ['reference.py'] * 2, [None], 'reference.py',
                      ['ref' + str(i) for i in range(19)]):
            with self.subTest(names=names), self.assertRaises(ValueError):
                validate_goals(dict(plan, context_files=names), required=True)
        plan['existing_files'].append('missing.py')
        with self.assertRaisesRegex(ValueError, 'Unassigned editable paths: missing.py.*context_files'):
            validate_goals(plan, required=True)

    def test_unknown_context_path_fails_before_plan_review(self):
        ws, base = self.workspace()
        self.plan['context_files'] = ['missing.py']
        with patch('hornet.desktop.automation.request_json', return_value=self.plan), \
             self.assertRaisesRegex(ValueError, 'Read-only context file is not in the project: missing.py'):
            self.prepare(ws, base)


if __name__ == '__main__':
    unittest.main()
