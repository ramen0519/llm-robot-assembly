from copy import deepcopy
from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v2.packets import shared_candidate_packet

class SharedCandidates(unittest.TestCase):
    def packet(self,rev):
        return {'part_id':'3','target_id':'cad_goal:3','observation_revision':rev,
            'grasp_candidates':{'schema':'factored_grasp_candidates/v1','columns':['candidate_id','checks'],
                'frame':'part_local','common_checks':{'physical_success':False},
                'rows':[[f'3:r{rev}:g1',{'status':'failed','distance':-.001}],
                        [f'3:r{rev}:g2',{'status':'unknown','distance':None}]]}}
    def test_current_ids_refer_to_original_full_rows_without_chains(self):
        cache={};first=self.packet(1);saved=deepcopy(first)
        a=shared_candidate_packet(first,cache);b=shared_candidate_packet(self.packet(2),cache)
        c=shared_candidate_packet(self.packet(3),cache)
        self.assertEqual(first,saved);self.assertEqual(a,first)
        for rev,p in ((2,b),(3,c)):
            row=p['grasp_candidates']['rows'][0]
            self.assertEqual(row['candidate_id'],f'3:r{rev}:g1')
            self.assertEqual(row['reuse_full_row_from_shared_history']['observation_revision'],1)
    def test_changed_failure_or_common_status_is_sent_fully(self):
        cache={};shared_candidate_packet(self.packet(1),cache)
        p=self.packet(2);p['grasp_candidates']['rows'][0][1]['status']='passed'
        value=shared_candidate_packet(p,cache)
        self.assertIsInstance(value['grasp_candidates']['rows'][0],list)
        p=self.packet(3);p['grasp_candidates']['common_checks']['physical_success']=True
        value=shared_candidate_packet(p,cache)
        self.assertTrue(all(isinstance(r,list) for r in value['grasp_candidates']['rows']))
    def test_different_part_or_frame_cannot_reference_prior_geometry(self):
        for field in ('part','frame'):
            cache={};shared_candidate_packet(self.packet(1),cache);p=self.packet(2)
            if field=='part':p.update(part_id='4',target_id='cad_goal:4')
            else:p['grasp_candidates']['frame']='world'
            self.assertEqual(shared_candidate_packet(p,cache),p)

if __name__=='__main__':unittest.main()
