from copy import deepcopy
from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v2.packets import observation_packet,display_numbers


def merge(base,overrides):
    result=deepcopy(base)
    if overrides=='common_checks':return result
    for key,value in overrides.items():
        result[key]=merge(result[key],value) if isinstance(value,dict) and isinstance(result.get(key),dict) else deepcopy(value)
    return result


class CandidatePacketTests(unittest.TestCase):
    def candidate(self,idx,status):
        return {'candidate_id':'p:r1:g'+str(idx),'position_mm':[1.123456789,0,3],
          'quaternion_xyzw':[0,1,0,0],'opening_width_mm':30+idx,'approach_family':'top',
          'geometry':{'contact_points_part_m':[[.01,.02,.03],[-.01,.02,.03]],'contact_normals_part':[[1,0,0],[-1,0,0]],'contact_spacing_m':.02},
          'checks':{'robot_ik':'not_run','physical_grasp':'not_run','nominal_source_tool':{
              'source':'geometry_only','physical_success':False,'status':status,'reason':'case_'+str(idx),'clearance_m':idx*.000123456789}},
          'motion_hint':{'not_a_feasibility_check':True,'tcp_orientation_change_rad':idx*.123456789}}

    def test_each_candidate_check_and_hint_round_trips_without_loss(self):
        pool=[self.candidate(i,s) for i,s in enumerate(('not_run','blocked_nominal','clear_nominal'))]
        context={'grasp_candidates':pool,'failure':{'code':'UNEXPECTED_CONTACT','penetration':.000123456789}}
        original=deepcopy(context);packet=observation_packet(context);out=packet['grasp_candidates']
        self.assertEqual(context,original);self.assertEqual(packet['failure'],original['failure'])
        self.assertEqual(len(out['rows']),3)
        for row,c in zip(out['rows'],pool):
            self.assertEqual(row[0],c['candidate_id'])
            self.assertEqual(merge(out['common_checks'],row[8]),display_numbers(c['checks']))
            self.assertEqual(merge(out['common_motion_hint'],row[9]),display_numbers(c['motion_hint']))

    def test_optional_geometry_pose_alternatives_survive(self):
        pool=[self.candidate(0,'not_run'),self.candidate(1,'not_run')]
        pool[1]['geometry_pose_alternatives']=[{'source':'python_geometry','physical_success':False,
            'pose':{'position_mm':[0.,1.,2.],'quaternion_xyzw':[0,1,0,0]}}]
        out=observation_packet({'grasp_candidates':pool})['grasp_candidates']
        col=out['columns'].index('geometry_pose_alternatives')
        self.assertEqual(out['rows'][0][col],[])
        self.assertEqual(out['rows'][1][col],pool[1]['geometry_pose_alternatives'])

    def test_unknown_failure_is_not_inherited_from_a_success(self):
        a=self.candidate(0,'not_run');b=self.candidate(1,'blocked_nominal')
        b['checks']['new_failed_check']={'status':'failed','value':-.0009}
        out=observation_packet({'grasp_candidates':[a,b]})['grasp_candidates']
        self.assertNotIn('new_failed_check',merge(out['common_checks'],out['rows'][0][8]))
        self.assertEqual(merge(out['common_checks'],out['rows'][1][8])['new_failed_check']['status'],'failed')


if __name__=='__main__':unittest.main()
