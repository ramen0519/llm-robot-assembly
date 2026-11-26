"""API-free component skill test from an authenticated, empty-gripper state.

This is a separate development branch, not a new Astra episode. A saved goal
pose alone is not a restoration checkpoint and cannot be used as success.
"""
from pathlib import Path
import argparse,json,time,uuid
import mujoco,numpy as np
from assembly_v12.contracts import read,write,sha,digest
from assembly_v12.media import STATE,state
from assembly_v2.artifacts import verify_bundle,inventory
from assembly_v2.budget import Budget
from assembly_v2.completion import valid_evidence
from assembly_v2.grasp import resolve
from assembly_v2.media import Recorder
from assembly_v2.scene import body_name
from assembly_v2.skills import MeshSkills
from assembly_v2.transforms import body_pose
from failure_report import json_safe,FailureEvent


def probe(source,checkpoint,proposal_path,output,seconds):
    source=Path(source);out=Path(output);out.mkdir(parents=True,exist_ok=False)
    started=time.monotonic();budget=Budget(seconds,cancelled=lambda:(out/'cancel.request').exists())
    result={'schema':'component_primitive_probe/v1','run_id':'primitive-'+uuid.uuid4().hex[:8],
        'policy_source':'scripted_development','api_calls':0,'api_cost_usd':0.,
        'fresh_input_pipeline':False,'whole_assembly_claim':False,'status':'initializing',
        'grasp_feasible':'not_run','installation_feasible_with_grasp':'not_run'}
    skills=recorder=None
    try:
        bundle=verify_bundle(source/'replay_bundle');c=read(source/checkpoint);p=read(source/'prepared.json')
        if c['state_spec']!='mjSTATE_INTEGRATION' or c['controller']['held_pose'] is not None or c['installed']:
            raise ValueError('This probe requires an authenticated empty-gripper, empty-base scene.')
        if sha(source/'model.mjb')!=c['model_sha256'] or sha(source/c['state'])!=c['state_sha256'] or digest(p)!=c['prepared_sha256']:
            raise ValueError('Source checkpoint/model/prepared mismatch.')
        proposal=read(proposal_path);pid=proposal['part_id']
        if pid not in p['order']:raise ValueError('Unknown installable part.')
        model=mujoco.MjModel.from_binary_path(str(source/'model.mjb'));data=mujoco.MjData(model)
        saved=np.load(source/c['state'],allow_pickle=False)
        mujoco.mj_setState(model,data,saved,STATE);mujoco.mj_forward(model,data)
        if not np.array_equal(saved,state(model,data)):raise ValueError('Restored integration state differs.')
        write(out/'prepared.json',p);write(out/'proposal.json',proposal);write(out/'source_checkpoint.json',c)
        write(out/'source_evidence.json',{'source_run':str(source),'bundle_id':bundle['id'],
            'state_restored_exactly':True,'original_controller_restored':True,'source_checkpoint_source':c['source'],
            'initial_simulation_time_s':c['simulation_time_s'],'no_goal_placement':True,'no_api':True})
        write(out/'code_versions.json',inventory(p))
        recorder=Recorder(out,model,data,p);recorder.deadline=budget.deadline;recorder.part_id=pid
        recorder.action_id=result['run_id']+':component';recorder.check_id=result['run_id']+':actual'
        skills=MeshSkills(model,data,p,pid,set(),c['settled'],budget,recorder=recorder)
        ctrl=c['controller'];skills.motion.reference=np.asarray(ctrl['reference'],float)
        skills.motion.gripper_command=float(ctrl['gripper_command']);skills.motion.goal=None
        skills.stage('restored_initial_observation');skills.motion.sample(allow_support=True,allow_grasp=False)
        current=body_pose(data,body_name(pid,p['base_id']))
        grasp=resolve(proposal['grasp'],{'presented':[]},p['parts'][pid],current,1,'scripted')
        skills.configure_motion_plan(proposal.get('motion_plan'),reset=True,source='scripted_development')
        write(out/'resolved_grasp.json',grasp)
        result.update(status='running',part_id=pid);write(out/'result.json',result)
        picked=skills.pick(grasp);write(out/'pick.json',picked)
        result['grasp_feasible']='passed';write(out/'result.json',result);print('physical pick/lift passed',flush=True)
        verification=skills.insert(result['run_id']+':completion')
        assert valid_evidence(verification,p['completion_profile_hash'],source='live_simulation',required_parts={pid})
        write(out/'completion.json',verification)
        result.update(status='component_success',installation_feasible_with_grasp='passed',installed=[pid],
            required=list(p['order']),completion=verification)
    except Exception as exc:
        field='installation_feasible_with_grasp' if result['grasp_feasible']=='passed' else 'grasp_feasible'
        result[field]='failed' if isinstance(exc,FailureEvent) else 'unknown'
        result.update(status='component_failed',failure=skills.failure(exc,skills) if skills else {'message':type(exc).__name__+': '+str(exc)})
        if skills:result['stopped_checkpoint']=skills.checkpoint('stopped')
    finally:
        if skills:result['path_checks']=json_safe(skills.path_attempts)
        if recorder:
            try:result['media']=recorder.close()
            except Exception as exc:result['media_error']=type(exc).__name__+': '+str(exc)
        result['elapsed_s']=time.monotonic()-started;write(out/'result.json',result)
    print(json.dumps({k:result.get(k) for k in ('run_id','status','grasp_feasible','installation_feasible_with_grasp','elapsed_s')},ensure_ascii=True),flush=True)
    return result


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    for flag in ('source-run','checkpoint','proposal','output'):ap.add_argument('--'+flag,required=True)
    ap.add_argument('--seconds',type=float,default=360);a=ap.parse_args()
    if not 1<=a.seconds<=600:ap.error('Use a finite 1..600 second probe.')
    r=probe(a.source_run,a.checkpoint,a.proposal,a.output,a.seconds)
    raise SystemExit(0 if r['status']=='component_success' else 2)
