"""API-free reference/supply diagnosis; neither check is robot assembly."""
from pathlib import Path
import math
import mujoco
import numpy as np
from assembly_v12.contracts import read,write,sha
from assembly_v12.media import png,state
from .scene import build_scene
from .numerics import signature
from .skills import MeshSkills
from .verification import observe_window,pose_diagnostics


def _image(model,data,prepared,path):
    bounds=np.asarray([p['target_bounds_world_m'] for p in prepared['parts'].values()])
    low,high=bounds[:,0].min(0),bounds[:,1].max(0)
    camera=mujoco.MjvCamera();camera.lookat[:]=(low+high)/2
    camera.distance=max(.18,float(np.linalg.norm(high-low))*2.1)
    camera.azimuth=125;camera.elevation=-22
    options=mujoco.MjvOption();options.geomgroup[3]=0
    with mujoco.Renderer(model,height=480,width=480) as renderer:
        renderer.update_scene(data,camera=camera,scene_option=options);png(path,renderer.render())


def check_reference(prepared,folder,budget,*,check_id):
    """Keep failure states and cause even if no main-scene recorder was started."""
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    model,data=build_scene(prepared,reference=True)
    path=folder/'reference_model.mjb';mujoco.mj_saveModel(model,str(path),None)
    skills=MeshSkills(model,data,prepared,prepared['order'][0],set(prepared['order']),{},budget)
    skills.verifier.reference_mode=True;skills.retreat_complete=True
    before=state(model,data);initial=pose_diagnostics(skills)
    media=folder/'reference_media';media.mkdir(exist_ok=True);media_errors=[]
    try:_image(model,data,prepared,media/'initial.png')
    except Exception as exc:media_errors.append({'image':'initial','error':str(exc)[:400]})
    evidence=observe_window(skills,prepared['order'],check_id=check_id,source='reference_scene',
                           model_hash=sha(path),prealignment={},reference=True)
    stopped=pose_diagnostics(skills)
    np.savez_compressed(folder/'reference_boundary_states.npz',states=np.array([before,state(model,data)]))
    try:_image(model,data,prepared,media/'stopped.png')
    except Exception as exc:media_errors.append({'image':'stopped','error':str(exc)[:400]})
    failure=evidence.get('execution_failure',{})
    displaced=[pid for pid,row in stopped.items() if not row['within_goal_pose_limits']]
    reasons=sorted({r['rejection_reason'] for r in failure.get('violations',[]) if 'rejection_reason' in r})
    diagnostic={'schema':'assembly_start_diagnosis/v1','stage':'reference_goal_stability',
        'source':'reference_scene','is_robot_assembly':False,'api_calls':0,
        'status':evidence['status'],'simulation_time_s':float(data.time),
        'numerical_solver':signature(model.opt),
        'fixed_base':prepared['base_id'],'fixture':prepared['profile']['fixture'],
        'assembly_rotation_xyzw':prepared['profile']['assembly_rotation_xyzw'],
        'initial_parts':initial,'stopped_parts':stopped,'outside_goal_pose_limits':displaced,
        'contact_rejection_reasons':reasons,'execution_failure':failure or None,
        'original_goal_unchanged':True,'main_supply_settling':'not_run','robot_actions':'not_run',
        'next_check':('Continue to independent supply settling.' if evidence['status']=='passed' else
            'Validate working orientation, physical support/fixture and required fastening; do not loosen completion thresholds.'),
        'state_recording':{'file':'reference_boundary_states.npz','model':'reference_model.mjb',
            'times_s':[0.,float(data.time)],'source':'reference_scene','scope':'two measured boundary states; not a motion video'},
        'media_errors':media_errors}
    write(folder/'reference_diagnostics.json',diagnostic)
    write(folder/'reference_verification.json',evidence)
    return evidence


def diagnose_start(prepared,folder,budget,*,rebuild_supply=False):
    """Run independent startup checks; no policy, API, attachment or motor path."""
    from copy import deepcopy
    from .validation import check_prepared
    from .preparation import layout
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=False)
    check_prepared(prepared,budget);p=deepcopy(prepared)
    if rebuild_supply:
        placement=layout(p['parts'],p['base_id'],p['order'],p['profile'],budget)
        p.update(layout=placement,table_center_world_m=placement['table_center_world_m'],
                 table_size_xy_m=placement['table_size_xy_m'])
    write(folder/'prepared.json',p)
    ref=check_reference(p,folder,budget,check_id=folder.name+':reference')
    model,data=build_scene(p);skills=MeshSkills(model,data,p,p['order'][0],set(),{},budget)
    try:
        settled=skills.settle();write(folder/'settled_initial_poses.json',settled)
        supply={'status':'passed','part_count':len(settled),'simulation_time_s':float(data.time)}
    except Exception as exc:
        supply={'status':'failed' if hasattr(exc,'code') else 'unknown','message':str(exc),
            'code':getattr(getattr(exc,'code',None),'value',None),'violations':getattr(exc,'violations',[]),
            'simulation_time_s':float(data.time)}
    write(folder/'supply_verification.json',supply)
    result={'schema':'assembly_readiness/v1','reference_status':ref['status'],'supply_status':supply['status'],
        'ready_for_robot_attempt':ref['status']=='passed' and supply['status']=='passed',
        'robot_assembly':'not_run','astra':'not_run','api_calls':0,'supply_layout_regenerated':bool(rebuild_supply),
        'reference_and_supply_checks_are_independent':True,'completion_thresholds_changed':False,
        'source_input':p.get('input_hint') or p.get('input_export')}
    write(folder/'readiness.json',result)
    return result
