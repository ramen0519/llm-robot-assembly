"""Physical settings and byte/source integrity at the verified-cache boundary."""
from copy import deepcopy
from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
import mujoco,numpy as np,trimesh
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v2.numerics import apply_numerics
from assembly_v2.geometry import asset_worker,geometry_matches,load_mesh
from assembly_v2.asset_cache import settings_for,register_asset,reuse,key
from assembly_v2.budget import Budget
from assembly_v12.contracts import read,write


class NumericsCacheTests(unittest.TestCase):
    def test_scripted_motion_hint_avoids_half_turn_but_keeps_attempt_limit(self):
        from assembly_v2.policy import Scripted
        context={'allowed_skills':['pick','stop'],'part_id':'p','target_id':'cad_goal:p',
                 'observation_revision':1,'candidate_attempts':0,
                 'grasp_candidates':[{'candidate_id':'long','approach_family':'top',
                    'motion_hint':{'tcp_orientation_change_rad':3.14}},
                    {'candidate_id':'short','approach_family':'top',
                    'motion_hint':{'tcp_orientation_change_rad':.001}}]}
        policy=Scripted()
        self.assertEqual(policy.propose(context)['grasp']['candidate_id'],'short')
        context['candidate_attempts']=1
        self.assertEqual(policy.propose(context)['grasp']['candidate_id'],'long')

    def test_precision_changes_solver_only_and_missing_settings_preserve_legacy(self):
        model=mujoco.MjModel.from_xml_string('<mujoco><worldbody><body><freejoint/><geom type="box" size=".01 .02 .03"/></body></worldbody></mujoco>')
        old=(model.opt.timestep,model.opt.gravity.copy(),model.geom_friction.copy(),model.body_mass.copy(),model.body_inertia.copy())
        count=model.opt.iterations;apply_numerics(model.opt,{})
        self.assertEqual(count,model.opt.iterations)
        apply_numerics(model.opt,{'numerical_solver':{'iterations':200,'tolerance':1e-12,'ls_iterations':100,'ls_tolerance':1e-6}})
        self.assertEqual(model.opt.tolerance,1e-12)
        for a,b in zip(old,(model.opt.timestep,model.opt.gravity,model.geom_friction,model.body_mass,model.body_inertia)):
            np.testing.assert_array_equal(a,b)
        with self.assertRaises(ValueError):apply_numerics(model.opt,{'numerical_solver':{'tolerance':1e-3}})
        with self.assertRaises(ValueError):apply_numerics(model.opt,{'numerical_solver':{'gravity':[0,0,0]}})

    def test_equivalent_indexed_surface_shortcuts_only_identical_geometry(self):
        a=trimesh.creation.box([.01,.02,.03]);b=a.copy();b.vertices+=1e-17
        with patch('assembly_v2.geometry.surface_distance',side_effect=AssertionError('unnecessary')):
            self.assertTrue(geometry_matches(a,b))
        b.apply_translation([.001,0,0]);self.assertFalse(geometry_matches(a,b))

    def test_verified_cache_matches_source_and_rejects_corrupt_piece(self):
        profile=read(Path(__file__).resolve().parents[1]/'config/assembly_workcell.v2.json')
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source=root/'part.obj';trimesh.creation.box([.01,.02,.03]).export(source)
            settings=settings_for(profile);asset_worker(source,root/'asset',settings)
            register_asset(source,root/'asset',profile,Budget(60),cache_root=root/'cache')
            copied=reuse(source,root/'copy',profile,Budget(60),cache_root=root/'cache')
            self.assertEqual(copied['source_mesh_sha256'],read(root/'asset/asset.json')['source_mesh_sha256'])
            self.assertFalse(read(root/'copy/cache_reuse.json')['physics_validation_reused'])
            changed=deepcopy(profile);changed['collision']['quality_samples']+=1
            self.assertIsNone(reuse(source,root/'miss',changed,Budget(60),cache_root=root/'cache'))
            entry=root/'cache'/key(source,settings)
            hull=entry/'collision'/copied['hulls'][0]['path'];hull.write_text('broken',encoding='utf8')
            with self.assertRaisesRegex(ValueError,'verified bytes'):
                reuse(source,root/'bad',profile,Budget(60),cache_root=root/'cache')


if __name__=='__main__':unittest.main()
