"""Development geometry fixtures. NOT native disassembly proof or Astra collection."""
import argparse
from copy import deepcopy
from pathlib import Path
import math
import traceback
import numpy as np
import trimesh
from scipy.spatial.transform import Rotation
from assembly_v12.contracts import read,write,digest,sha
from assembly_v2.geometry import prepare_asset
from assembly_v2.preparation import PROFILE,layout,relations_and_operations
from assembly_v2.completion import load_profile,valid_evidence
from assembly_v2.budget import Budget
from assembly_v2.scene import build_scene,body_name
from assembly_v2.skills import MeshSkills
from assembly_v2.media import Recorder
from assembly_v2.verification import observe_window
from assembly_v2.transforms import body_pose
from assembly_v2.grasp import candidates,resolve

def component_preparation(folder,shape,budget,count=1):
    profile=read(PROFILE);parts={};order=[]
    base=trimesh.creation.box(extents=[.08,.08,.006]);meshes={'fixture_base':base}
    for i in range(count):
        pid=f'new_instance_{i:02d}';order.append(pid)
        if shape=='cylinder':m=trimesh.creation.cylinder(radius=.011,height=.12,sections=32)
        elif shape=='oblique':
            m=trimesh.creation.box(extents=[.024,.022,.12]);m.vertices[:,0]+=.15*m.vertices[:,2]
        else:m=trimesh.creation.box(extents=[.012,.012,.025])
        meshes[pid]=m
    for i,(pid,m) in enumerate(meshes.items()):
        target=folder/'assets'/pid/'source_metric.obj';target.parent.mkdir(parents=True);m.export(target)
        asset=prepare_asset(target,folder/'assets'/pid/'collision',profile,budget)
        t=np.eye(4);t[:3,:3]=Rotation.from_euler('z',30,degrees=True).as_matrix()
        t[:2,3]=profile['assembly_center_xy_m']
        if i==0:t[2,3]=profile['tabletop_z_m']+.003
        else:
            t[2,3]=profile['tabletop_z_m']+.006+m.extents[2]/2
            if count>1:t[:2,3]+=[((i-1)%4-1.5)*.018,((i-1)//4-1)*.018]
        vertices=m.vertices@t[:3,:3].T+t[:3,3]
        parts[pid]={'part_id':pid,'mesh':str(target),'mesh_sha256':sha(target),'source_mesh_sha256':sha(target),
            'asset':asset,'mass_kg':max(.001,m.volume*800),'mass_source':'common_assumed_density',
            'original_goal':t.tolist(),'target':t.tolist(),'target_world_m':t[:3,3].tolist(),'size':m.extents.tolist(),
            'target_bounds_world_m':[vertices.min(0).tolist(),vertices.max(0).tolist()],
            'part_path':{'status':'not_provided','reason':'explicit component fixture; no native proof fabricated'}}
    placement=layout(parts,'fixture_base',order,profile,budget);pairs=relations_and_operations(parts,order,budget)
    completion=load_profile()
    p={'schema':'prepared_assembly/v2.0','validation_scope':'component_fixture_without_native_chain',
       'base_id':'fixture_base','order':order,'parts':parts,'profile':profile,'completion_profile':completion,
       'completion_profile_hash':digest(completion),'fixed_base_initial_pose':parts['fixture_base']['target'],
       'table_center_world_m':placement['table_center_world_m'],'table_size_xy_m':placement['table_size_xy_m'],
       'layout':placement,'allowed_goal_contact_pairs':pairs,'asset_hash':digest({pid:r['asset']['asset_hash'] for pid,r in parts.items()}),
       'source_run_hash':'component_fixture_no_native_result','source_geometry_hash':digest({pid:sha(r['mesh']) for pid,r in parts.items()}),
       'input_export':None,'manual_product_adjustments':False,'source':'generated_development_fixture'}
    write(folder/'prepared.json',p);return p

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',required=True)
    ap.add_argument('--shape',choices=['cylinder','oblique','capacity'],required=True);ap.add_argument('--seconds',type=float,default=300)
    ap.add_argument('--attempt',type=int,default=0);ap.add_argument('--prepare-only',action='store_true')
    a=ap.parse_args();folder=Path(a.output).absolute();folder.mkdir(parents=True,exist_ok=False);budget=Budget(a.seconds)
    result={'validation_scope':'scripted_component_not_native_integrated_not_Astra','actual_astra_calls':0,'physical_installation':'not_run'};record=None
    try:
        p=component_preparation(folder,a.shape,budget,count=12 if a.shape=='capacity' else 1)
        m,d=build_scene(p);result.update(preparation='passed',nonfixed_parts=len(p['order']),actual_general_mesh=True)
        if not a.prepare_only:
            rm,rd=build_scene(p,reference=True);ref=MeshSkills(rm,rd,p,p['order'][0],set(p['order']),{},budget)
            ref.verifier.reference_mode=True;ref.retreat_complete=True
            evidence=observe_window(ref,p['order'],check_id='reference',source='reference_scene',model_hash='component_reference',prealignment={},reference=True)
            write(folder/'reference_verification.json',evidence);result['reference']=evidence['status']
            if evidence['status']!='passed':raise ValueError('reference_invalid')
            record=Recorder(folder,m,d,p);record.part_id=p['order'][0];record.goal_images()
            skills=MeshSkills(m,d,p,p['order'][0],set(),{},budget,recorder=record);skills.settle()
            part=p['parts'][skills.active];current=body_pose(d,body_name(skills.active,p['base_id']))
            pool=candidates(part,current,1,budget,p['profile']);write(folder/'candidates.json',pool)
            family='side' if a.shape=='cylinder' else 'oblique';selected=[c for c in pool['presented'] if c['approach_family']==family]
            if a.attempt>=len(selected):raise ValueError('Requested approach family/candidate unavailable.')
            grasp=resolve({'mode':'candidate','candidate_id':selected[a.attempt]['candidate_id']},pool,part,current,1,'scripted')
            write(folder/'resolved_grasp.json',grasp);record.action_id='component_general_pick'
            result['pick']=skills.pick(grasp);record.action_id='component_general_install'
            evidence=skills.insert('component_general_install:completion');write(folder/'final_verification.json',evidence)
            result['physical_installation']=evidence['status'];result['approach_family']=family
        result['status']='component_checks_completed'
    except Exception as exc:
        result.update(status='failed',error=type(exc).__name__+': '+str(exc));write(folder/'exception.json',{'traceback':traceback.format_exc()})
        if 'skills' in locals():result['failure']=skills.failure(exc,skills)
    finally:
        if record:
            try:result['media']=record.close()
            except Exception as exc:result['media']={'status':'media_error','error':str(exc)}
        write(folder/'component_result.json',result)
    print(folder/'component_result.json',flush=True)

if __name__=='__main__':main()
