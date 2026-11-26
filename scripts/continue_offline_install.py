"""API-free physical continuation from an authenticated lift checkpoint.

Restores the recorded integration state AND controller/slip reference. This is
a separate development diagnostic, never a new whole-assembly/Astra success.
"""
from pathlib import Path
import argparse,time,json,uuid
import numpy as np,mujoco
from assembly_v12.contracts import read,write,sha,digest
from assembly_v12.media import STATE,state
from assembly_v2.budget import Budget
from assembly_v2.media import Recorder
from assembly_v2.skills import MeshSkills
from failure_report import json_safe,FailureEvent,FailureCode
from assembly_v2.verification import observe_window
from assembly_v2.completion import valid_evidence

def validate_resume_request(c,prior,prepared,resume_stage):
    if resume_stage not in ('lifted','grasped','released','transporting','retreating'):raise ValueError('Unknown continuation stage.')
    if c.get('state_spec')!='mjSTATE_INTEGRATION':raise ValueError('Full integration checkpoint required.')
    if resume_stage=='released':
        failure=prior.get('failure') or {}
        if (c.get('phase')!='open_after_retreat' or failure.get('phase')!='open_after_retreat'
                or failure.get('failure_code')!='TimeoutError'
                or c['controller'].get('held_pose') is not None
                or c['controller'].get('slip_reference') is not None):
            raise ValueError('Released continuation requires a timeout after completed physical retreat, with an empty gripper.')
        if not any(r.get('phase')=='retreat_limited_open' for r in prior.get('path_checks',[])):
            raise ValueError('Missing checked physical retreat evidence.')
        if prepared['parts'][c['part_id']]['operation']['kind']=='linear_insertion' and not c.get('completion_context',{}).get('prealignment'):
            raise ValueError('Required insertion alignment evidence cannot be reconstructed from a final pose.')
    elif resume_stage=='retreating':
        failure=prior.get('failure') or {}
        if (c.get('source')!='live_simulation' or c.get('phase')!='retreat_limited_open'
                or failure.get('phase')!=c.get('phase') or failure.get('failure_code')!='TimeoutError'
                or c['controller'].get('held_pose') is not None
                or c['controller'].get('slip_reference') is not None):
            raise ValueError('Retreat continuation requires a live timeout with an empty gripper.')
        paths=prior.get('path_checks',[])
        if not (any(r.get('phase')=='confined_release' for r in paths)
                and any(r.get('phase')=='retreat_limited_open' and r.get('source')=='live_simulation'
                        and r.get('target_tcp') is not None for r in paths)):
            raise ValueError('Missing physical release and checked retreat target evidence.')
        if prepared['parts'][c['part_id']]['operation']['kind']=='linear_insertion' and not c.get('completion_context',{}).get('prealignment'):
            raise ValueError('Insertion alignment evidence is required before post-release continuation.')
    elif resume_stage=='transporting':
        failure=prior.get('failure') or {}
        if (c.get('source')!='live_simulation' or c.get('phase')!='transport_preinstall'
                or failure.get('phase')!=c.get('phase') or failure.get('failure_code')!='TimeoutError'
                or c['controller'].get('held_pose') is None
                or c['controller'].get('slip_reference') is None):
            raise ValueError('Transport continuation requires a live timeout with the original held/slip references.')
        if not any(r.get('phase')=='transport_preinstall' and r.get('source')=='live_simulation'
                   and r.get('target_tcp') is not None for r in prior.get('path_checks',[])):
            raise ValueError('Missing checked transport evidence.')
        # Eligibility is not proof of safety. resume() restores full state, then
        # re-observes contacts, original slip, lift clearance and the new path.
    elif c.get('phase')!='pick_lift':raise ValueError('A physical grasp/lift checkpoint is required.')
    if resume_stage=='lifted' and not c['state'].endswith('physically_lifted.npy'):
        raise ValueError('A phase label alone does not prove physical lift completion.')
    if resume_stage=='grasped':
        failure=prior.get('failure') or {}
        if failure.get('failure_code') not in ('IK_NOT_CONVERGED','PLANNED_CONTACT','MOTION_PATH_NOT_FOUND') or not failure.get('phase','').startswith('pick_lift'):
            raise ValueError('Grasped continuation requires a pure lift-planning failure; never replay a physical failure.')

def resume(source,checkpoint,output,seconds,resume_stage="lifted"):
    started=time.monotonic();source=Path(source);out=Path(output);out.mkdir(parents=True,exist_ok=False)
    result={'schema':'offline_install_continuation/v1','run_id':'offline-install-'+uuid.uuid4().hex[:8],
        'policy_source':'scripted_development','api_calls':0,'api_cost_usd':0.,'whole_assembly_claim':False,
        'status':'initializing','source_run':str(source),'installation_feasible_with_grasp':'not_run'}
    recorder=skills=None
    try:
        c=read(source/checkpoint);p=read(source/'prepared.json')
        validate_resume_request(c,read(source/'result.json'),p,resume_stage)
        if sha(source/'model.mjb')!=c['model_sha256'] or sha(source/c['state'])!=c['state_sha256'] or digest(p)!=c['prepared_sha256']:
            raise ValueError('Checkpoint/model/input identity mismatch.')
        model=mujoco.MjModel.from_binary_path(str(source/'model.mjb'));data=mujoco.MjData(model)
        saved=np.load(source/c['state'],allow_pickle=False)
        mujoco.mj_setState(model,data,saved,STATE);mujoco.mj_forward(model,data)
        if not np.array_equal(saved,state(model,data)):raise ValueError('Integration state restoration differs.')
        write(out/'source_checkpoint.json',c);write(out/'prepared.json',p)
        write(out/'source_evidence.json',{'source_run':str(source),'checkpoint':checkpoint,'restored_state_exact':True,
            'controller_reference_and_slip_reference_restored':True,'no_goal_placement':True,'repeated_pick':False})
        budget=Budget(seconds,cancelled=lambda:(out/'cancel.request').exists())
        recorder=Recorder(out,model,data,p);recorder.deadline=budget.deadline
        recorder.part_id=c['part_id'];recorder.action_id=result['run_id']+':insert'
        skills=MeshSkills(model,data,p,c['part_id'],set(c['installed']),c['settled'],budget,recorder=recorder)
        if c.get('motion_plan'):
            skills.configure_motion_plan(c['motion_plan']['parameters'],source='restored_authenticated_plan')
        control=c['controller'];skills.motion.reference=np.asarray(control['reference'],float)
        skills.motion.gripper_command=control['gripper_command']
        skills.motion.held_pose=None if control['held_pose'] is None else tuple(np.asarray(x,float) for x in control['held_pose'])
        skills.motion.slip_reference=None if control['slip_reference'] is None else tuple(np.asarray(x,float) for x in control['slip_reference'])
        goal=control.get('goal');skills.motion.goal=None if goal is None else {**goal,
            'position_m':np.asarray(goal['position_m']),'rotation_matrix':np.asarray(goal['rotation_matrix'])}
        if resume_stage not in ('released','retreating'):skills.fresh_hold()
        result['resume_stage']=resume_stage
        if resume_stage in ('lifted','transporting'):
            row=skills.motion.sample(allow_support=True,allow_grasp=True)
            if row['supported'] or row['part_bottom_clearance_m']<.05:raise ValueError('Restored state was not physically lifted.')
            write(out/'resume_observation.json',json_safe({'resume_stage':resume_stage,'observed':row,
                'original_slip_reference_preserved':True,'replan_from_actual_state':True,
                'no_assembly_success_claim':True}))
        result.update(status='running',part_id=c['part_id'],inherited_installed=c['installed'])
        write(out/'result.json',result);print('restored authenticated '+resume_stage+' state; measured continuation starts, API=0',flush=True)
        if resume_stage=='grasped':
            picked=skills.lift_part();write(out/'continued_lift.json',picked)
        if resume_stage in ('released','retreating'):
            skills.verifier.mating=True
            skills.prealignment=c.get('completion_context',{}).get('prealignment',{})
            if resume_stage=='retreating':
                skills.stage('resume_retreat_observation')
                observed=skills.motion.sample(allow_support=True,allow_grasp=False)
                write(out/'resume_observation.json',json_safe({'resume_stage':resume_stage,'observed':observed,
                    'robot_product_noncontact_rechecked':True,'replan_from_actual_state':True}))
                prior=read(source/'result.json')
                target=next(r['target_tcp'] for r in reversed(prior['path_checks'])
                            if r.get('phase')=='retreat_limited_open' and r.get('target_tcp') is not None)
                skills.stage('retreat_limited_open')
                skills.move(np.asarray(target,float),grasp=False,rrt=True)
            skills.stage('finish_open_after_retreat')
            skills.motion.sample(allow_support=True,allow_grasp=False)
            skills.motion.hold(4.,allow_support=True,allow_grasp=False,gripper_target=0.)
            skills.retreat_complete=True
            verification=observe_window(skills,sorted(skills.installed|{skills.active}),
                check_id=result['run_id']+':completion',source=skills.source,
                model_hash=skills.model_hash,prealignment=skills.prealignment)
            if not valid_evidence(verification,p['completion_profile_hash'],source=skills.source,required_parts=skills.installed|{skills.active}):
                exc=FailureEvent(FailureCode.PLACEMENT_NOT_CONFIRMED,'Continued completion window failed.',violations=[verification])
                exc.completion_evidence=verification;raise exc
        else:verification=skills.insert(result['run_id']+':completion')
        write(out/'completion.json',verification)
        result.update(status='component_success',installation_feasible_with_grasp='passed',
            installed=sorted(skills.installed|{skills.active}),completion=verification)
    except Exception as exc:
        result.update(status='component_failed',installation_feasible_with_grasp='failed' if isinstance(exc,FailureEvent) else 'unknown',
            failure=skills.failure(exc,skills) if skills else {'message':str(exc)})
        if skills:result['stopped_checkpoint']=skills.checkpoint('stopped_diagnostic')
    finally:
        if skills:result['path_checks']=json_safe(skills.path_attempts)
        if recorder:
            try:result['media']=recorder.close()
            except Exception as exc:result['media']={'status':'media_error','message':str(exc)}
        result['elapsed_s']=time.monotonic()-started;write(out/'result.json',result)
    print(json.dumps({k:result.get(k) for k in ('run_id','status','api_calls','installation_feasible_with_grasp','elapsed_s')},ensure_ascii=False),flush=True)
    return result

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source-run',required=True);ap.add_argument('--checkpoint',required=True)
    ap.add_argument('--output',required=True);ap.add_argument('--seconds',type=float,default=180)
    ap.add_argument('--resume-stage',choices=['lifted','grasped','released','transporting','retreating'],default='lifted')
    a=ap.parse_args()
    if not 1<=a.seconds<=600:ap.error('Use a finite 1..600 second diagnostic.')
    result=resume(a.source_run,a.checkpoint,a.output,a.seconds,a.resume_stage)
    raise SystemExit(0 if result['status']=='component_success' else 2)
