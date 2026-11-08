import copy
import unittest
from hornet.desktop.edit_plan_schema import edit_plan_schema
from hornet.registry.schema_validation import validate, check_schema


class EditPlanSchemaTests(unittest.TestCase):
    def setUp(self):
        self.context={'active_goal_id':'implement','goal_decomposition':[
            {'id':'root','parent':None},{'id':'implement','parent':'root'},
            {'id':'other','parent':'root'}]}
        self.schema=edit_plan_schema(self.context)
        self.plan={'summary':'Change value','edits':[{'goal_id':'implement','uri':'files.text.atomic_edit',
            'arguments':{'path':'main.py','action':'replace','target_text':'VALUE = 1','new_text':'VALUE = 2'}}]}

    def test_schema_requires_target_and_exact_active_goal(self):
        check_schema(self.schema);validate(self.plan,self.schema)
        for missing in ('target_text',):
            bad=copy.deepcopy(self.plan)
            if missing=='goal_id': del bad['edits'][0][missing]
            else: del bad['edits'][0]['arguments'][missing]
            with self.assertRaises(ValueError): validate(bad,self.schema)
        for leaf in ('root','other','invented'):
            bad=copy.deepcopy(self.plan);bad['edits'][0]['goal_id']=leaf
            with self.assertRaises(ValueError):validate(bad,self.schema)

    def test_active_goal_id_can_be_omitted_but_multi_leaf_requires_it(self):
        plan=copy.deepcopy(self.plan);del plan['edits'][0]['goal_id']
        validate(plan,self.schema)
        context=dict(self.context);context.pop('active_goal_id')
        with self.assertRaises(ValueError):validate(plan,edit_plan_schema(context))

    def test_action_fields_and_delete_empty_text(self):
        for action,extra in (('create',{}),('replace',{'target_text':'old'}),
                             ('delete',{'target_text':'old'}),('insert',{'line_number':1})):
            plan=copy.deepcopy(self.plan)
            args={'path':'main.py','action':action,'new_text':'' if action=='delete' else 'new'}
            args.update(extra);plan['edits'][0]['arguments']=args
            validate(plan,self.schema)
            if extra:
                del args[next(iter(extra))]
                with self.assertRaises(ValueError):validate(plan,self.schema)

    def test_legacy_schema_does_not_require_goal_but_retains_atomic_rules(self):
        plan=copy.deepcopy(self.plan);del plan['edits'][0]['goal_id']
        validate(plan,edit_plan_schema())
        del plan['edits'][0]['arguments']['target_text']
        with self.assertRaises(ValueError):validate(plan,edit_plan_schema())
