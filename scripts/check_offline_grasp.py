"""API-free, one-component physical diagnostic from an immutable recorded scene.

This is a scripted development branch, never a fresh whole-assembly or Astra
result. Original artifacts remain read-only. No model clients are constructed.
"""
from pathlib import Path
import argparse,json,time,uuid
import mujoco,numpy as np
from assembly_v12.contracts import read,write,append,sha,digest
from assembly_v12.media import STATE,state
from assembly_v2.artifacts import verify_bundle
from assembly_v2.budget import Budget
from assembly_v2.completion import valid_evidence
from assembly_v2.grasp import resolve
from assembly_v2.media import Recorder
from assembly_v2.scene import body_name
from assembly_v2.skills import MeshSkills
from assembly_v2.transforms import body_pose
from assembly_v2.verification import observe_window
from failure_report import json_safe


def run(source,proposal_path,output,seconds):
    started=time.monotonic();source=Path(source);out=Path(output)
    out.mkdir(parents=True,exist_ok=False)
    budget=Budget(seconds,cancelled=lambda:(out/'cancel.request').exists())
    result={'schema':'offline_component_diagnostic/v1','run_id':'offline-'+uuid.uuid4().hex[:8],
        'policy_source':'scripted_development','api_calls':0,'api_cost_usd':0.,
        'whole_assembly_claim':False,'fresh_empty_base_episode':False,'status':'initializing',
        'grasp_feasible':'not_run','installation_feasible_with_grasp':'not_run'}
    record=skills=None;events=[]
    try:
        manifest=verify_bundle(source/'replay_bundle');p=read(source/'prepared.json')
        parent=read(source/'result.json');meta=read(source/'replay_frames.json')
        if sha(source/'model.mjb')!=meta['model_sha256'] or sha(source/'replay_states.npz')!=meta['states_sha256']:
            raise ValueError('Recorded scene identity mismatch.')
        if sha(source/'prepared.json')!=sha(source/'replay_bundle/prepared.json'):
            raise ValueError('Prepared input differs from sealed source.')
        proposal=read(proposal_path);part_id=proposal['part_id'];installed=set(parent['installed'])
        if part_id in installed or part_id not in p['order']:raise ValueError('Select an uninstalled part.')
        boundary=read(source/'restored_boundary_verification.json')
        if not valid_evidence(boundary,p['completion_profile_hash'],required_parts=installed):
            raise ValueError('Source must have a valid restored installed-part boundary.')
        source_events=[json.loads(l) for l in (source/'events.jsonl').read_text(encoding='utf-8').splitlines()]
        if any(e.get('executed') for e in source_events):raise ValueError('This loader supports only a verified released scene with copy-only subsequent attempts.')
        checkpoints=list((source/'checkpoints').glob('*.json'))
        if not checkpoints:raise ValueError('Missing supply baseline checkpoint.')
        settled=read(checkpoints[0])['settled']
        for f in checkpoints:
            if digest(read(f)['settled'])!=digest(settled):raise ValueError('Supply baseline changed.')
        model=mujoco.MjModel.from_binary_path(str(source/'model.mjb'));data=mujoco.MjData(model)
        saved=np.load(source/'replay_states.npz',allow_pickle=False)['states'][-1]
        mujoco.mj_setState(model,data,saved,STATE);mujoco.mj_forward(model,data)
        if not np.array_equal(saved,state(model,data)):raise ValueError('State restoration differs.')
        write(out/'prepared.json',p);write(out/'proposal.json',proposal)
        write(out/'source_evidence.json',{'source_run':str(source),'bundle_id':manifest['id'],
            'model_sha256':meta['model_sha256'],'source_states_sha256':meta['states_sha256'],
            'source_frame':len(meta['frames'])-1,'restored_exact':True,'supply_baseline':settled,
            'installed_inherited':sorted(installed),'no_target_placement':True})
        result.update(part_id=part_id,inherited_installed=sorted(installed),installed=sorted(installed),
                      source_run=str(source),status='running')
        record=Recorder(out,model,data,p);record.deadline=budget.deadline
        record.part_id=part_id;record.action_id=result['run_id']+':scripted'
        skills=MeshSkills(model,data,p,part_id,installed,settled,budget,recorder=record)
        skills.retreat_complete=True
        checked=observe_window(skills,sorted(installed),check_id=result['run_id']+':restore',
            source='live_simulation',model_hash=record.model_hash,prealignment=skills.prealignment)
        write(out/'restored_boundary_verification.json',checked)
        if not valid_evidence(checked,p['completion_profile_hash'],required_parts=installed):
            raise ValueError('Restored parts failed fresh completion check.')
        current=body_pose(data,body_name(part_id,p['base_id']))
        g=resolve(proposal['grasp'],{'presented':[]},p['parts'][part_id],current,1,'scripted')
        write(out/'resolved_grasp.json',g)
        result['grasp_feasible']='running';write(out/'result.json',result)
        print('physical pick starts, API=0',flush=True)
        picked=skills.pick(g);result['grasp_feasible']='passed'
        append(out/'events.jsonl',json_safe({'kind':'pick','policy_source':'scripted_development','result':picked}))
        write(out/'result.json',result);print('physical lift passed; installation starts',flush=True)
        completed=skills.insert(result['run_id']+':completion')
        write(out/'completion.json',completed)
        result['installation_feasible_with_grasp']='passed';installed.add(part_id)
        result.update(status='component_success',installed=sorted(installed),completion=completed)
        append(out/'events.jsonl',{'kind':'install','installation_verified':True,'policy_source':'scripted_development',
                                  'verification':completed,'part_id':part_id})
    except Exception as exc:
        from failure_report import FailureEvent
        failure=MeshSkills.failure(exc,skills) if skills else {'message':type(exc).__name__+': '+str(exc)}
        key='installation_feasible_with_grasp' if result['grasp_feasible']=='passed' else 'grasp_feasible'
        result.update(status='component_failed',failure=failure)
        result[key]='failed' if isinstance(exc,FailureEvent) else 'unknown'
        append(out/'events.jsonl',{'kind':'failure','policy_source':'scripted_development','failure':failure})
        print(json.dumps({'status':result['status'],'failure':failure.get('failure_code'),'phase':failure.get('phase'),'message':failure.get('message')},ensure_ascii=False),flush=True)
    finally:
        if skills:result['path_checks']=json_safe(skills.path_attempts)
        if record:
            try:result['media']=record.close()
            except Exception as exc:result['media']={'status':'media_error','message':str(exc)}
        result['elapsed_s']=time.monotonic()-started
        write(out/'result.json',result)
    print(json.dumps({k:result.get(k) for k in ('run_id','status','api_calls','grasp_feasible','installation_feasible_with_grasp','elapsed_s')},ensure_ascii=False),flush=True)
    return result

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source-run',required=True);ap.add_argument('--proposal',required=True)
    ap.add_argument('--output',required=True);ap.add_argument('--seconds',type=float,default=240)
    a=ap.parse_args()
    if not 1<=a.seconds<=600:ap.error('Use a bounded 1..600 second component diagnostic.')
    result=run(a.source_run,a.proposal,a.output,a.seconds)
    raise SystemExit(0 if result['status']=='component_success' else 2)
