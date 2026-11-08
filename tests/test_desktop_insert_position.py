import unittest
from tempfile import TemporaryDirectory
from hornet.desktop.insert_position import resolve_insert
from hornet.desktop.edit_plan_schema import edit_plan_schema
from hornet.registry.schema_validation import validate
from hornet.desktop.registry_mission import propose_with_tools
from hornet.desktop.workspace import Workspace


def edit(position=None, **extra):
    args = dict(path='main.py', action='insert', new_text='# inserted')
    if position is not None:
        args['position'] = position
    args.update(extra)
    return {'uri': 'files.text.atomic_edit', 'arguments': args}


class InsertPositionTests(unittest.TestCase):
    def test_exact_positions_empty_end_and_newlines(self):
        for text in ('', 'A', 'A\n', 'A\r\nB\r\n', 'A\rB'):
            for position, expected in [('start', 1), ('end', len(text.splitlines()) + 1)]:
                self.assertEqual(resolve_insert(edit(position)['arguments'], text)['line_number'], expected)
        text = 'A\r\nB\r\nC'
        for position, expected in [('before', 2), ('after', 4)]:
            self.assertEqual(resolve_insert(edit(position, anchor_text='B\nC')['arguments'], text)['line_number'], expected)

    def test_ambiguous_missing_partial_and_conflicting_positions_rejected(self):
        for args, text in [
            (edit('before', anchor_text='A')['arguments'], 'A\nA\n'),
            (edit('after', anchor_text='missing')['arguments'], 'A\n'),
            (edit('before', anchor_text='A')['arguments'], 'AA\n'),
            (edit('start', line_number=1000000000000000)['arguments'], 'A\n'),
            (edit('end', anchor_text='A')['arguments'], 'A\n'),
            (edit('before')['arguments'], 'A\n'),
            (edit(None, anchor_text='A')['arguments'], 'A\n')]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                resolve_insert(args, text)
        self.assertEqual(resolve_insert(edit(None, line_number=1000000000000000)['arguments'], 'A')['line_number'], 1000000000000000)

    def test_schema_accepts_explicit_intent_rejects_mixed_fields(self):
        for position in ('start', 'end', 'before', 'after'):
            item = edit(position, **({'anchor_text': 'A'} if position in ('before', 'after') else {}))
            plan = dict(summary='edit', edits=[item])
            validate(plan, edit_plan_schema())
            item['arguments']['line_number'] = 1
            with self.assertRaises(ValueError):
                validate(plan, edit_plan_schema())

    def test_controller_resolves_again_after_prior_edits_and_preserves_source(self):
        with TemporaryDirectory() as tmp:
            ws = Workspace(tmp); rev = ws.create('demo'); root = ws.revision('demo', rev)
            target = root/'main.py'; original = b'VALUE = 1\r\n'; target.write_bytes(original)
            context = ws.mission_context('demo', rev, ['main.py'], 'Insert comments')
            plan = {'summary': 'comments', 'edits': [
                edit('start', new_text='# File Managment'),
                edit('after', anchor_text='# File Managment', new_text='# second'),
                edit('end', new_text='# last')]}
            proposal, receipts = propose_with_tools(root, context, lambda *_: plan)
            self.assertEqual(proposal['files']['main.py'], '# File Managment\r\n# second\r\nVALUE = 1\r\n# last\r\n')
            self.assertEqual(target.read_bytes(), original)
            self.assertEqual([r['resolved_line_number'] for r in receipts if 'resolved_line_number' in r], [1, 2, 4])

    def test_rejected_semantic_or_huge_numeric_plan_preserves_base(self):
        cases = [edit('before', anchor_text='# duplicate'),
                 edit('start', new_text='value = "unterminated'),
                 edit(None, line_number=1000000000000000)]
        for item in cases:
            with self.subTest(item=item), TemporaryDirectory() as tmp:
                ws = Workspace(tmp); rev = ws.create('demo'); root = ws.revision('demo', rev)
                target = root/'main.py'; original = b'# duplicate\n# duplicate\nVALUE = 1\n'
                target.write_bytes(original)
                context = ws.mission_context('demo', rev, ['main.py'], 'Comment')
                with self.assertRaisesRegex(ValueError, 'corrected plan also failed'):
                    propose_with_tools(root, context, lambda *_: {'summary': 'bad', 'edits': [item]})
                self.assertEqual(target.read_bytes(), original)

    def test_created_file_can_be_anchored_by_later_insert(self):
        with TemporaryDirectory() as tmp:
            ws = Workspace(tmp); rev = ws.create('demo'); root = ws.revision('demo', rev)
            context = ws.mission_context('demo', rev, [], 'Create file', ['new.py'])
            first = {'uri': 'files.text.atomic_edit', 'arguments': {
                'path': 'new.py', 'action': 'create', 'new_text': 'VALUE = 1'}}
            second = edit('after', path='new.py', anchor_text='VALUE = 1', new_text='# end')
            proposal, _ = propose_with_tools(root, context, lambda *_: {'summary': 'new', 'edits': [first, second]})
            self.assertEqual(proposal['files']['new.py'], 'VALUE = 1\n# end\n')
            self.assertFalse((root/'new.py').exists())

    def test_invalid_anchor_is_repaired_but_never_applied_implicitly(self):
        with TemporaryDirectory() as tmp:
            ws = Workspace(tmp); rev = ws.create('demo'); root = ws.revision('demo', rev)
            target = root/'main.py'; target.write_text('VALUE = 1\n')
            context = ws.mission_context('demo', rev, ['main.py'], 'Top comment')
            calls = []
            def request(instruction, payload):
                calls.append(payload)
                return {'summary': 'comment', 'edits': [edit('before', anchor_text='missing') if len(calls) == 1 else edit('start')]}
            proposal, _ = propose_with_tools(root, context, request)
            self.assertIn('found 0', calls[1]['preflight_error'])
            self.assertTrue(proposal['files']['main.py'].splitlines()[0] == '# inserted')
            self.assertEqual(target.read_text(), 'VALUE = 1\n')

if __name__ == '__main__':
    unittest.main()
