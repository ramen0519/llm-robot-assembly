from pathlib import Path
from tempfile import TemporaryDirectory
import sys,unittest
import numpy as np
import mujoco,trimesh
from scipy.spatial.transform import Rotation
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v2.tooling import validate_fingertips
from assembly_v2.grasp import diverse_poses,candidates
from assembly_v2.budget import Budget
from ur3e_with_gripper import build_robot_spec
from workcell_scene import compile_workcell
from move_gripper import pad_gaps_mm

class ToolingTests(unittest.TestCase):
    def test_fingertip_changes_keep_housing_linkages_and_source_default(self):
        baseline,initial=compile_workcell(build_robot_spec(),free_part_count=0)
        for ext in (.02,.075):
            model,data=compile_workcell(build_robot_spec(fingertip_profile={'kind':'rigid_extension_concept','extension_m':ext,'width_m':.008}),free_part_count=0,tcp_extension_m=ext)
            self.assertEqual((model.nq,model.nv,model.nu,model.neq),(14,14,7,3))
            np.testing.assert_array_equal(model.eq_type,baseline.eq_type)
            for name in ('gripper_base','gripper_left_follower','gripper_right_follower'):
                np.testing.assert_allclose(model.body(name).mass,baseline.body(name).mass)
                np.testing.assert_allclose(model.body(name).pos,baseline.body(name).pos)
            self.assertAlmostEqual(model.site('gripper_pinch').pos[2],.145+ext)
            self.assertTrue(np.all(model.body('gripper_left_pad').inertia>0))
            self.assertAlmostEqual(min(pad_gaps_mm(model,data)),85.4,places=7)
        again,_=compile_workcell(build_robot_spec(),free_part_count=0)
        np.testing.assert_array_equal(again.geom_size,baseline.geom_size)
        np.testing.assert_array_equal(again.body_mass,baseline.body_mass)
        np.testing.assert_array_equal(again.site_pos,baseline.site_pos)

    def test_end_contact_is_explicit_and_stays_within_100mm(self):
        tool={'kind':'rigid_extension_concept','extension_m':.075,'width_m':.008,'contact_style':'end_contact'}
        model,_=compile_workcell(build_robot_spec(fingertip_profile=tool),free_part_count=0,tcp_extension_m=.075)
        self.assertAlmostEqual(model.geom('gripper_left_backing').size[2]*2,.100)
        self.assertAlmostEqual(model.geom('gripper_left_pad1').size[2]*2,.002)
        self.assertLess(model.body('gripper_left_pad').mass[0],.01)
        self.assertEqual(model.neq,3)

    def test_contact_calibration_matches_tool_and_rejects_stale_builder(self):
        from assembly_v2.tooling import calibrated_pad_center
        import assembly_v2.tooling as tooling
        from assembly_v12.contracts import digest,sha
        tool={'kind':'rigid_extension_concept','extension_m':.075,'width_m':.008,'contact_style':'end_contact'}
        cal={'source':'empty_gripper_simulation','tool_definition_hash':digest(tool),
             'tool_builder_sha256':sha(Path(tooling.__file__)),
             'samples':[{'gap_mm':2.,'pad_center_tcp_m':[0.,0.,.003]}, {'gap_mm':8.,'pad_center_tcp_m':[0.,0.,.0028]}]}
        profile={'fingertip_concept':tool,'grasp_pad_center_calibration':cal}
        np.testing.assert_allclose(calibrated_pad_center(profile,.0025,[0,0,.006]),[0,0,.003])
        np.testing.assert_allclose(calibrated_pad_center({},.0025,[0,0,.006]),[0,0,.006])
        changed={**profile,'fingertip_concept':{**tool,'width_m':.006}}
        with self.assertRaises(ValueError):calibrated_pad_center(changed,.0025,[0,0,.006])
        cal['tool_builder_sha256']='stale'
        with self.assertRaises(ValueError):calibrated_pad_center(profile,.0025,[0,0,.006])

    def test_uncalibrated_custom_tool_cannot_generate_assumed_stock_contacts(self):
        profile={'fingertip_concept':{'kind':'rigid_extension_concept','extension_m':.075,'width_m':.008}}
        with self.assertRaisesRegex(ValueError,'contact calibration'):
            candidates({},np.eye(4),1,Budget(5),profile)

    def test_astra_receives_measured_tool_metadata_without_product_answers(self):
        from assembly_v2.packets import tool_configuration_packet
        from assembly_v12.contracts import digest,sha
        import assembly_v2.tooling as tooling
        tool={'kind':'rigid_extension_concept','extension_m':.075,'width_m':.008,'contact_style':'end_contact'}
        cal={'source':'empty_gripper_simulation','tool_definition_hash':digest(tool),
             'tool_builder_sha256':sha(Path(tooling.__file__)),
             'samples':[{'gap_mm':2.,'pad_center_tcp_m':[0.,0.,.003],'qpos':[999]*8}]}
        profile={'gripper':'robotiq_2f85','fingertip_concept':tool,'grasp_pad_center_calibration':cal,
                 'release_strategy':'limited_open_then_retreat'}
        packet=tool_configuration_packet(profile)
        self.assertEqual(packet['backing_dimensions_mm'],[8.,3,100.])
        self.assertEqual(packet['contact_calibration']['rows'],[[2.,[0.,0.,3.]]])
        self.assertNotIn('qpos',str(packet))
        self.assertNotIn('candidate_id',str(packet))
        self.assertNotIn('part_id',str(packet))
        profile['grasp_pad_center_calibration']['tool_builder_sha256']='stale'
        with self.assertRaises(ValueError):tool_configuration_packet(profile)

    def test_unbounded_or_unlabelled_finger_profiles_rejected(self):
        for p in ({'kind':'stock','extension_m':.05,'width_m':.008},
                  {'kind':'rigid_extension_concept','extension_m':.1,'width_m':.008},
                  {'kind':'rigid_extension_concept','extension_m':.05,'width_m':float('nan')}):
            with self.assertRaises(ValueError):validate_fingertips(p)

    def test_incremental_diversity_matches_original_rule(self):
        rng=np.random.default_rng(27);quats=Rotation.random(100,random_state=rng).as_quat();pos=rng.normal(size=(100,3))*20
        rows=[{'id':i,'quaternion_xyzw':q.tolist(),'position_mm':p.tolist()} for i,(q,p) in enumerate(zip(quats,pos))]
        pending=rows[1:].copy();chosen=[rows[0]]
        def dist(a,b):
            return 2*np.arccos(np.clip(abs(np.asarray(a['quaternion_xyzw'])@np.asarray(b['quaternion_xyzw'])),0,1))+min(np.linalg.norm(np.asarray(a['position_mm'])-b['position_mm'])/100,1)
        while len(chosen)<18:
            i=max(range(len(pending)),key=lambda i:min(dist(pending[i],s) for s in chosen));chosen.append(pending.pop(i))
        actual=diverse_poses(rows,18,Budget(5))
        self.assertEqual([x['id'] for x in actual],[x['id'] for x in chosen])
        class Stop:
            def check(self,phase):raise TimeoutError(phase)
        with self.assertRaises(TimeoutError):diverse_poses(rows,18,Stop())

    def test_thin_geometry_requires_explicit_candidate_setting(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'thin.obj';trimesh.creation.box(extents=[.0015,.020,.040]).export(path)
            part={'part_id':'thin','mesh':str(path),'mesh_sha256':'fixture','asset':{'asset_hash':'fixture'}}
            profile={'seed':0,'resources':{'surface_samples':512,'max_raw_candidates':256,'max_presented_candidates':18}}
            standard=candidates(part,np.eye(4),1,Budget(5),profile)
            explicit=candidates(part,np.eye(4),1,Budget(5),{**profile,'minimum_contact_spacing_m':.001})
            self.assertFalse(any(c['geometry']['contact_spacing_m']<.006 for c in standard['presented']))
            self.assertTrue(any(c['geometry']['contact_spacing_m']<.006 for c in explicit['presented']))

if __name__=='__main__':unittest.main()
