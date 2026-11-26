from pathlib import Path
from copy import deepcopy
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v2.completion import load_profile,ObservationWindow
from assembly_v2.packets import tool_result_packet,observation_packet
from assembly_v12.contracts import digest

class CompletionViewTests(unittest.TestCase):
    def evidence(self):
        profile=load_profile();op={'kind':'generic_goal_placement','not_applicable_reason':'No defined insertion axis.'}
        w=ObservationWindow(['p'],profile,check_id='component-test',source='live_simulation',model_sha256='model',asset_hash='assets',operations={'p':op})
        m={'goal_pose':None,'current_pose':None,'position_error_base_xyz_m':[.0001234567890123456,0.,0.],
           'orientation_error_rad':0.,'linear_speed_m_s':0.,'angular_speed_rad_s':0.,'hard_penetration_m':0.,
           'gripper_clearance_m':.01,'released':True,'retreat_complete':True,'robot_product_noncontact':True}
        for i in range(501):w.add(i*.002,{'p':m})
        return w.finish()

    def test_passed_window_view_keeps_exact_worst_measurements_and_provenance(self):
        e=self.evidence();raw={'status':'installation_verified','verification':e};original=deepcopy(raw)
        out=tool_result_packet(raw,'event:test');v=out['verification']
        self.assertEqual(raw,original)
        self.assertEqual(v['schema'],'verified_completion_model_view/v1')
        self.assertEqual(v['full_completion_sha256'],digest(e))
        self.assertEqual(v['source'],'live_simulation')
        self.assertEqual(v['rows'][0][2][0],e['parts']['p']['worst']['position_base_axis5mm'])
        self.assertEqual(v['evidence_location']['event_id'],'event:test')
        self.assertEqual(out['model_evidence_view']['full_tool_result_sha256'],digest(raw))

    def test_unknown_or_failed_window_is_never_tabulated(self):
        for status in ('failed','unknown'):
            e=self.evidence();e['status']=status;e['complete']=False
            raw={'verification':e};self.assertEqual(tool_result_packet(raw,'event:test'),raw)

if __name__=='__main__':unittest.main()
