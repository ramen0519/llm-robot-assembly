"""Regressions for startup diagnosis and supply pose selection."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import unittest
import numpy as np
from scipy.spatial.transform import Rotation
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v2.preparation import choose_initial
from assembly_v2.verification import observe_window
from assembly_v2.completion import load_profile,valid_evidence
from assembly_v2.artifacts import report
from assembly_v12.contracts import digest,write
from failure_report import FailureEvent,FailureCode


class ReadinessTests(unittest.TestCase):
    def test_stable_supply_over_goal_like_narrow_edge(self):
        side=np.eye(4);side[:3,:3]=Rotation.from_euler('x',90,degrees=True).as_matrix()
        asset={'stable_poses':[{'transform':np.eye(4).tolist(),'probability':.002354},
                               {'transform':side.tolist(),'probability':.394276}]}
        rotation,note=choose_initial(asset,np.eye(3))
        np.testing.assert_allclose(rotation,side[:3,:3])
        self.assertEqual(note['physical_settling'],'not_run')
        self.assertEqual(note['selected_index'],1)

    def test_symmetric_probability_tie_avoids_needless_turnover(self):
        side=np.eye(4);side[:3,:3]=Rotation.from_euler('x',180,degrees=True).as_matrix()
        a={'stable_poses':[{'transform':side.tolist(),'probability':.4+1e-16},
                          {'transform':np.eye(4).tolist(),'probability':.4}]}
        r,_=choose_initial(a,np.eye(3));np.testing.assert_allclose(r,np.eye(3))

    def interrupted(self,exception):
        profile=load_profile()
        def hold(*a,**kw):raise exception
        skills=SimpleNamespace(prepared={'completion_profile':profile,'asset_hash':'asset',
            'parts':{'p':{'operation':{'kind':'generic_goal_placement','not_applicable_reason':'fixture'}}}},
            data=SimpleNamespace(time=.032),motion=SimpleNamespace(hold=hold),verifier=SimpleNamespace())
        with patch('assembly_v2.verification.pose_diagnostics',return_value={'p':{'within_goal_pose_limits':False}}):
            return observe_window(skills,['p'],check_id='case',source='reference_scene',model_hash='model',prealignment={},reference=True)

    def test_measured_failure_before_first_sample_is_failed_and_traceable(self):
        e=self.interrupted(FailureEvent(FailureCode.UNEXPECTED_CONTACT,'Moved beyond goal.',violations=[]))
        self.assertEqual((e['status'],e['complete'],e['samples']),('failed',False,0))
        self.assertEqual(e['stopped_phase'],'reference_settle')
        self.assertEqual(e['stopped_at_simulation_time_s'],.032)
        self.assertIn('stopped_part_poses',e)
        self.assertFalse(valid_evidence(e,digest(load_profile()),source='reference_scene'))

    def test_timeout_is_unknown_not_fabricated_physical_failure(self):
        e=self.interrupted(TimeoutError('deadline'))
        self.assertEqual(e['status'],'unknown')
        self.assertNotIn('execution_failure',e)

    def test_reference_failure_report_distinguishes_unstarted_robot(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);(folder/'reference_media').mkdir()
            (folder/'reference_media/initial.png').touch();(folder/'reference_media/stopped.png').touch()
            write(folder/'reference_diagnostics.json',{'fixed_base':'b','simulation_time_s':.032,'outside_goal_pose_limits':['p']})
            report(folder,{'status':'reference_invalid','metrics':{},'policy_source':'scripted'})
            html=(folder/'report.html').read_text(encoding='utf8')
            self.assertIn('reference_media/stopped.png',html)
            self.assertIn('reference_scene',html)
            self.assertIn('아직 실행되지 않았습니다',html)


if __name__=='__main__':unittest.main()
