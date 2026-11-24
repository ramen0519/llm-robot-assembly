"""Bounded, explicitly scripted component checks against the new real mesh scene."""
import argparse
from pathlib import Path
import traceback
from assembly_v12.contracts import read,write,import_export
from assembly_v2.budget import Budget
from assembly_v2.preparation import prepare,PROFILE
from assembly_v2.scene import build_scene,body_name
from assembly_v2.skills import MeshSkills
from assembly_v2.media import Recorder
from assembly_v2.verification import observe_window
from assembly_v2.transforms import body_pose
from assembly_v2.grasp import candidates,resolve

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--export',required=True);ap.add_argument('--output',required=True)
    ap.add_argument('--seconds',type=float,default=300);ap.add_argument('--pick',action='store_true');ap.add_argument('--grasp')
    a=ap.parse_args();out=Path(a.output).absolute();out.mkdir(parents=True,exist_ok=False);budget=Budget(a.seconds)
    result={'source':'scripted_component_validation','astra_calls':0,'physical_installation':'not_run'};record=None
    try:
        import_export(a.export,out/'input');p=prepare(out/'input',out,PROFILE,budget)
        m,d=build_scene(p,reference=True);reference=MeshSkills(m,d,p,p['order'][0],set(p['order']),{},budget)
        reference.verifier.reference_mode=True;reference.retreat_complete=True
        reference_result=observe_window(reference,p['order'],check_id='reference',source='reference_scene',model_hash='component_reference',prealignment={},reference=True)
        write(out/'reference_verification.json',reference_result);result['reference']=reference_result['status']
        if reference_result['status']!='passed':raise ValueError('reference_invalid')
        m,d=build_scene(p);record=Recorder(out,m,d,p);record.goal_images();record.part_id=p['order'][0]
        skills=MeshSkills(m,d,p,p['order'][0],set(),{},budget,recorder=record);skills.settle()
        pool=candidates(p['parts'][skills.active],body_pose(d,body_name(skills.active,p['base_id'])),1,budget,p['profile'])
        write(out/'candidate_probe.json',pool);result['candidates']=len(pool['presented']);result['settling']='passed'
        if a.pick:
            submitted=read(a.grasp) if a.grasp else {'mode':'candidate','candidate_id':pool['presented'][0]['candidate_id']}
            grasp=resolve(submitted,pool,p['parts'][skills.active],body_pose(d,body_name(skills.active,p['base_id'])),1,'scripted')
            write(out/'resolved_grasp.json',grasp);record.action_id='component_pick'
            result['pick']=skills.pick(grasp);record.action_id='component_install'
            result['completion']=skills.insert('component_install:completion');result['physical_installation']=result['completion']['status']
        result['status']='component_checks_completed'
    except Exception as exc:
        result.update(status='failed',error=type(exc).__name__+': '+str(exc));write(out/'exception.json',{'traceback':traceback.format_exc()})
        if 'skills' in locals():result['failure']=skills.failure(exc,skills)
    finally:
        if record:result['media']=record.close()
        write(out/'component_result.json',result)
    print(out/'component_result.json',flush=True)

if __name__=='__main__':main()
