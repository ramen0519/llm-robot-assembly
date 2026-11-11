"""Only measured first candidates enter the contact and acceptance denominators."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from run_sleeve_memory_trial import counts, table_contact


class FirstGraspResultsTests(unittest.TestCase):
    def test_count_requires_the_same_phase_and_contact_pair(self):
        rejected={'accepted':False, 'failure_code':'UNEXPECTED_CONTACT', 'phase':'pick_close',
                  'violations':[{'geoms':['workcell_tabletop','gripper_left_pad2']}]}
        self.assertTrue(table_contact(rejected))
        for changes in ({'phase':'insert_lower'}, {'failure_code':'PLANNED_CONTACT'},
                        {'violations':[{'geoms':['other_part','gripper_left_pad2']}]}, {'accepted':True}):
            self.assertFalse(table_contact({**rejected,**changes}))

    def test_abstention_other_failure_and_missing_evidence_stay_distinct(self):
        measured={'status':'first_grasp_checked','trial':{'accepted':False},'target_contact':False,
                  'original_state_unchanged':True,'experience_ids':['record'],'searched':True}
        stopped={**measured,'status':'stopped','trial':None,'experience_ids':[]}
        result=counts([measured,stopped,{**stopped,'status':'failed'}])
        self.assertEqual(result['first_candidates_checked'],1)
        self.assertEqual(result['preflight_accepted'],0)
        self.assertEqual(result['other_preflight_rejections'],1)
        self.assertEqual((result['stopped'],result['failed_or_incomplete']),(1,1))
