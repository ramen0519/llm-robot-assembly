from copy import deepcopy
from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v2.completion import load_profile,profile_for_timestep,observation_samples,with_timestep,ObservationWindow,valid_evidence
from assembly_v12.contracts import digest
from assembly_v2.memory import compatible


class TimestepVariant(unittest.TestCase):
    def window(self,dt,count=None):
        p=profile_for_timestep(dt)
        op={'kind':'generic_goal_placement','not_applicable_reason':'No defined insertion.'}
        m={'position_error_base_xyz_m':[.0001]*3,'orientation_error_rad':0.,'linear_speed_m_s':0.,
            'angular_speed_rad_s':0.,'hard_penetration_m':.0001,'gripper_clearance_m':.01,
            'released':True,'retreat_complete':True,'robot_product_noncontact':True}
        w=ObservationWindow(['x'],p,check_id='test',source='live_simulation',model_sha256='m',asset_hash='a',operations={'x':op})
        for i in range(observation_samples(p) if count is None else count):w.add(i*dt,{'x':m})
        return p,w.finish()

    def test_baseline_remains_exact_original(self):
        self.assertEqual(profile_for_timestep(.002),load_profile())
        self.assertEqual(observation_samples(load_profile()),501)

    def test_variants_keep_all_physical_thresholds(self):
        base=load_profile()
        for dt in (.004,.01):
            p=profile_for_timestep(dt)
            for key,value in base.items():
                if key not in ('profile_id','physics_timestep_s'):self.assertEqual(p[key],value)
            self.assertNotEqual(digest(p),digest(base))

    def test_each_variant_needs_its_whole_one_second_window(self):
        for dt in (.002,.004,.01):
            p,e=self.window(dt);self.assertTrue(valid_evidence(e,digest(p)))
            _,partial=self.window(dt,observation_samples(p)-1);self.assertFalse(valid_evidence(partial,digest(p)))
            self.assertGreaterEqual(e['duration_s'],1.)

    def test_101_samples_cannot_pose_as_baseline_501(self):
        p,e=self.window(.01)
        self.assertFalse(valid_evidence(e,digest(load_profile())))
        e['physics_timestep_s']=.002
        self.assertFalse(valid_evidence(e))

    def test_removed_interval_cannot_hide_variant_profile(self):
        _,e=self.window(.004);e.pop('physics_timestep_s')
        self.assertFalse(valid_evidence(e))

    def test_changed_threshold_still_rejected(self):
        _,e=self.window(.01);e['parts']['x']['checks']['final_hard_penetration']['limit']=.01
        self.assertFalse(valid_evidence(e))

    def test_profile_derivation_preserves_input_and_source_object(self):
        p=load_profile();base={'completion_profile':p,'completion_profile_hash':digest(p),'parts':{'x':{'target':[[1]]}}}
        saved=deepcopy(base);variant=with_timestep(base,.01)
        self.assertEqual(base,saved);self.assertEqual(variant['parts'],base['parts'])
        self.assertTrue(variant['numerical_parent']['fresh_scene_required'])
        bad=deepcopy(base);bad['completion_profile']['global_completion']['final_hard_contact_penetration_max_m']=.01
        with self.assertRaises(ValueError):with_timestep(bad,.01)

    def test_experimental_collection_and_evaluation_are_blocked(self):
        import tempfile
        from assembly_v2.execution import run
        for split in ('collection','evaluation'):
            with tempfile.TemporaryDirectory() as temp:
                with self.assertRaisesRegex(ValueError,'Experimental timestep runs are development only'):
                    run({'completion_profile':profile_for_timestep(.004)},Path(temp)/'run',split=split)

    def test_ui_and_report_identify_experimental_interval(self):
        import tempfile
        from assembly_v2.ui import create_app,user_summary
        from assembly_v2.artifacts import report
        from assembly_v12.contracts import write
        r={'schema':'assembly_result/v2.0','run_id':'test-display-only','status':'physical_success',
            'policy_source':'scripted','split':'development','installed':['1'],'required':['1'],
            'physics_timestep_s':.004,'validation_regime':'experimental_4ms'}
        self.assertIn('개발 실험',user_summary({'result':r})['status'])
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'run';path.mkdir();write(path/'result.json',r);report(path,r)
            self.assertIn('개발 실험 조건',(path/'report.html').read_text(encoding='utf8'))
            client=create_app(Path(temp)/'ui',result_roots=[Path(temp)]).test_client()
            row=client.get('/api/recent').get_json()['runs'][0]
            self.assertEqual(row['physics_timestep_s'],.004)
            self.assertEqual(row['validation_regime'],'experimental_4ms')

    def test_unknown_interval_rejected(self):
        for dt in (.003,.02,float('nan')):
            with self.assertRaises(ValueError):profile_for_timestep(dt)

    def test_retrieval_does_not_mix_interval_profiles(self):
        a={'domain':'mesh_assembly','robot_gripper':'ur3e_2f85','profile_hash':digest(profile_for_timestep(.002)),
           'failure_code':'PLACEMENT_NOT_CONFIRMED','source':'live_simulation','phase':'release','action':'insert'}
        b={**a,'profile_hash':digest(profile_for_timestep(.01))}
        self.assertFalse(compatible(a,b)[0])


if __name__=='__main__':unittest.main()
