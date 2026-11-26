from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v2.packets import tool_result_packet
from assembly_v12.contracts import digest

class ToolEvidenceViewTests(unittest.TestCase):
    def test_checkpoint_restart_payload_removed_without_changing_failure(self):
        raw={'status':'preflight_rejected','executed':False,'source':'planner_candidate',
             'preflight':{'accepted':False,'grasp_feasible':'passed',
             'installation_feasible_with_grasp':'failed','main_state_unchanged':True,
             'grasp_checkpoint':{'schema':'physical_action_checkpoint/v1',
                 'source':'mujoco_preflight_copy','state':'checkpoints/a.npy',
                 'state_sha256':'state-hash','model_sha256':'model-hash',
                 'settled':{'other_part':[[1,0,0,0]]},
                 'controller':{'reference':[1,2,3],'held_pose':[[.001,.002,.003],[1,0,0]]}},
             'failure':{'failure_code':'PLANNED_CONTACT','phase':'install_endpoint_check',
                 'source':'planner_candidate','violations':[{'signed_distance_m':-.011858640942328845,
                   'geoms':['body:gripper_base/geom:33','assembly_base_collision_0']}],
                 'failure_snapshot':{'state_sha256':'detector-hash','png':'failure.png'}}}}
        original=deepcopy(raw);out=tool_result_packet(raw,'event:1')
        self.assertEqual(raw,original)
        self.assertEqual(out['preflight']['failure'],raw['preflight']['failure'])
        self.assertEqual(out['preflight']['main_state_unchanged'],True)
        checkpoint=out['preflight']['grasp_checkpoint']
        self.assertNotIn('controller',checkpoint);self.assertNotIn('settled',checkpoint)
        self.assertEqual(checkpoint['held_pose'],raw['preflight']['grasp_checkpoint']['controller']['held_pose'])
        self.assertEqual(checkpoint['checkpoint_payload_sha256'],digest(raw['preflight']['grasp_checkpoint']))
        self.assertEqual(out['model_evidence_view']['full_tool_result_sha256'],digest(raw))
        self.assertEqual(out['model_evidence_view']['event_id'],'event:1')

    def test_no_checkpoint_keeps_completion_evidence_exactly(self):
        raw={'status':'installation_verified','verification':{'status':'passed','observations':501,
             'limits':{'linear_speed_m_s':.001},'source':'live_simulation'}}
        self.assertEqual(tool_result_packet(raw,'event:2'),raw)

if __name__=='__main__':unittest.main()
