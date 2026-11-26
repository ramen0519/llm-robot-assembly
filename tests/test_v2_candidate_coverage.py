import sys
from pathlib import Path
import unittest
from types import SimpleNamespace
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v2.budget import Budget
from assembly_v2.grasp import contact_seed_order,presented_families
from assembly_v2.policy import action_schema,validate_action

class Coverage(unittest.TestCase):
    def test_raw_cap_can_see_both_ends_of_a_long_part(self):
        z=np.r_[np.linspace(-.001,.001,100),-.035,.035]
        points=np.column_stack((np.full(len(z),-.009),np.zeros(len(z)),z))
        normals=np.tile([-1.,0.,0.],(len(z),1))
        mesh=SimpleNamespace(face_normals=np.array([[1.,0.,0.]]),center_mass=np.zeros(3),extents=np.array([.018,.018,.07]))
        rays={i:(.018,p+[.018,0,0],0) for i,p in enumerate(points)}
        from itertools import islice
        selected=list(islice(contact_seed_order(rays,points,normals,mesh,.001,Budget(2)),8))
        height=[points[i,2] for i,_ in selected]
        self.assertLessEqual(min(height),-.03);self.assertGreaterEqual(max(height),.03)

    def test_unmeasured_side_candidates_cannot_hide_all_other_families(self):
        rows=[{'approach_family':f,'candidate_id':f+str(i)} for f in ('side','top','oblique') for i in range(30)]
        presented=presented_families(rows,18)
        self.assertEqual({f:sum(r['approach_family']==f for r in presented) for f in ('top','side','oblique')},
                         {'top':6,'side':6,'oblique':6})

    def test_stable_wire_schema_keeps_current_state_permissions(self):
        a={'allowed_skills':['pick','stop'],'part_id':'1','target_id':'cad_goal:1','observation_revision':1}
        b={'allowed_skills':['insert','stop'],'part_id':'2','target_id':'cad_goal:2','observation_revision':20}
        self.assertEqual(action_schema(a),action_schema(b))
        valid={'skill':'stop','part_id':'2','target_id':'cad_goal:2','observation_revision':20,'reason':'bounded stop','grasp':None}
        self.assertEqual(validate_action(valid,b),valid)
        for change in ({'skill':'insert'},{'part_id':'2'},{'target_id':'cad_goal:2'},{'observation_revision':20}):
            stale={**valid,'part_id':'1','target_id':'cad_goal:1','observation_revision':1,**change}
            with self.assertRaises(ValueError):validate_action(stale,a)

if __name__=='__main__':unittest.main()
