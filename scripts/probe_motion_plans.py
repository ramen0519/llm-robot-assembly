"""Paired development probe: identical real held state, one lift decision each.

This is not a whole-assembly benchmark, collection run or learned experience.
Both conditions use identical physics, checkpoint, model, images and limits.
Only permission to propose a bounded recipe differs. No automatic API retry.
"""
from pathlib import Path
from dataclasses import replace
from copy import deepcopy
import argparse,getpass,json,logging,os,sys,time,uuid
import numpy as np,mujoco
sys.path.insert(0,str(Path(__file__).resolve().parent))
from assembly_v12.contracts import read,write,digest,sha
from assembly_v12.media import STATE,state
from assembly_v12.context import History
from assembly_v2.motion_plan import DEFAULT,contract
from assembly_v2.budget import Budget
from assembly_v2.media import Recorder
from assembly_v2.skills import MeshSkills
from assembly_v2.policy import Astra,validate_action
from assembly_v2.packets import tool_result_packet
from assembly_v2.transforms import body_pose,xyzw
from assembly_v2.scene import body_name
from failure_report import json_safe,FailureEvent
from llm_runtime import load_llm_config,create_openai_client,ApiLedger
from continue_offline_install import validate_resume_request
ROOT=Path(__file__).resolve().parents[1]

def one(source,checkpoint,folder,mode,policy_kind,config,client,seconds):
    folder.mkdir(parents=True,exist_ok=False);started=time.monotonic()
    c=read(source/checkpoint);p=read(source/'prepared.json');prior=read(source/'result.json')
    validate_resume_request(c,prior,p,'grasped')
    if sha(source/'model.mjb')!=c['model_sha256'] or sha(source/c['state'])!=c['state_sha256'] or digest(p)!=c['prepared_sha256']:
        raise ValueError('Checkpoint identity mismatch.')
    model=mujoco.MjModel.from_binary_path(str(source/'model.mjb'));data=mujoco.MjData(model)
    saved=np.load(source/c['state'],allow_pickle=False);mujoco.mj_setState(model,data,saved,STATE);mujoco.mj_forward(model,data)
    if not np.array_equal(saved,state(model,data)):raise ValueError('Restoration differs.')
    write(folder/'prepared.json',p);write(folder/'source_checkpoint.json',c)
    write(folder/'source_evidence.json',{'source_run':str(source),'checkpoint':checkpoint,'restored_state_exact':True,
        'state_sha256':c['state_sha256'],'model_sha256':c['model_sha256'],'existing_parts':c['installed'],
        'new_empty_scene':False,'same_state_in_both_modes':True})
    budget=Budget(seconds);recorder=Recorder(folder,model,data,p);recorder.deadline=budget.deadline
    run_id='motion-probe-'+uuid.uuid4().hex[:8];recorder.part_id=c['part_id'];recorder.action_id=run_id+':lift'
    skills=MeshSkills(model,data,p,c['part_id'],set(c['installed']),c['settled'],budget,recorder=recorder)
    ctrl=c['controller']
    for key in ('reference','gripper_command','held_pose','slip_reference','goal'):
        value=deepcopy(ctrl[key])
        if key=='reference':value=np.asarray(value,float)
        elif key in ('held_pose','slip_reference') and value is not None:value=tuple(np.asarray(v,float) for v in value)
        elif key=='goal' and value is not None:value={**value,'position_m':np.asarray(value['position_m']), 'rotation_matrix':np.asarray(value['rotation_matrix'])}
        setattr(skills.motion,key,value)
    result={'schema':'motion_plan_probe/v1','run_id':run_id,'mode':mode,'policy_source':policy_kind,
        'status':'initializing','part_id':c['part_id'],'api_calls':0,'installation_status':'not_run',
        'whole_assembly_claim':False,'experience_registered':False,'source_state_sha256':c['state_sha256']}
    ledger=history=None
    try:
        skills.stage('observe_held_part_before_lift');skills.fresh_hold()
        observed=skills.motion.sample(allow_support=True,allow_grasp=True)
        current=body_pose(data,body_name(c['part_id'],p['base_id']));goal=np.asarray(p['parts'][c['part_id']]['target'])
        context={'part_id':c['part_id'],'target_id':'cad_goal:'+c['part_id'],'observation_revision':1,'active_role':'recovery',
            'allowed_skills':['lift','stop'],'candidate_attempts':0,'grasp_candidates':[],
            'holding_part':c['part_id'],'verified_installed_parts':c['installed'],
            'current_pose':{'position_mm':(current[:3,3]*1000).tolist(),'quaternion_xyzw':xyzw(current)},
            'goal_pose':{'position_mm':(goal[:3,3]*1000).tolist(),'quaternion_xyzw':xyzw(goal)},
            'measured':json_safe(observed),'failure':prior['failure'],'experiences':[],
            'operation':p['parts'][c['part_id']]['operation'],'motion_plan_contract':contract(mode,skills.motion_plan),
            'remaining':{'control':1,'model_generations':1},
            'probe_scope':'Complete only the physical lift from this actual grasped state. Installation is a separate task. One decision, no retry.'}
        write(folder/'observation.json',context)
        if policy_kind=='astra':
            images=recorder.goal_images()
            history=History(folder/'conversation.jsonl',{'task':'Recover a measured lift-planning failure; execute only lift or stop.',
                'source':'real_MuJoCo_checkpoint','part_id':c['part_id'],'policy_comparison_mode':mode},images)
            ledger=ApiLedger(config,folder/'llm_events.jsonl')
            policy=Astra(config,client,ledger,history,read(ROOT/'config/policy_prompts.v2.json'),budget)
            proposal=policy.propose(context)
        else:
            proposal=validate_action({'skill':'lift','part_id':c['part_id'],'target_id':context['target_id'],
                'observation_revision':1,'reason':'API 없는 계약·실행 연결 검사','grasp':None,
                'motion_plan':None if mode=='fixed' else {**DEFAULT,'lift_height_mm':80.}},context)
        write(folder/'proposal.json',proposal);result['proposal']=proposal
        skills.configure_motion_plan(proposal['motion_plan'],mode=mode,source=policy_kind)
        result['resolved_motion_plan']=deepcopy(skills.motion_plan);result['status']='running'
        if ledger:result.update(api_calls=ledger.model_requests,usage=ledger.summary())
        write(folder/'result.json',result)
        if proposal['skill']=='stop':result.update(status='policy_stopped',reason=proposal['reason'])
        else:
            print(mode+' measured lift execution starts',flush=True)
            lifted=skills.lift_part();result.update(status='lift_verified',lift=lifted)
            # This is a grasp/lift check only. Never call it installation success.
        if history and history.pending:history.complete(tool_result_packet(result,run_id+':event'))
    except Exception as exc:
        result.update(status='failed',failure=skills.failure(exc,skills),stopped_checkpoint=skills.checkpoint('probe_stopped'))
        if history and history.pending:history.complete(tool_result_packet({'status':'failed','failure':result['failure']},run_id+':event'))
    finally:
        result['path_checks']=json_safe(skills.path_attempts)
        if ledger:result.update(api_calls=ledger.model_requests,usage=ledger.summary())
        try:result['media']=recorder.close()
        except Exception as exc:result['media']={'status':'error','error_type':type(exc).__name__}
        result['elapsed_s']=time.monotonic()-started;write(folder/'result.json',result)
    print(json.dumps({k:result.get(k) for k in ('run_id','mode','status','api_calls','usage','elapsed_s')},ensure_ascii=False),flush=True)
    return result

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source-run',required=True);ap.add_argument('--checkpoint',required=True);ap.add_argument('--output',required=True)
    ap.add_argument('--policy',choices=['scripted','astra'],default='scripted');ap.add_argument('--llm-config',default=str(ROOT/'config/llm.astra-high.toml'))
    ap.add_argument('--seconds-per-condition',type=float,default=300);ap.add_argument('--max-usd-per-condition',type=float,default=0.)
    a=ap.parse_args()
    if not 1<=a.seconds_per_condition<=600:ap.error('Use 1..600 seconds per condition.')
    if a.policy=='astra' and not 0<a.max_usd_per_condition<=.75:ap.error('Live probe requires explicit <=$0.75 per condition; two generations total.')
    out=Path(a.output);out.mkdir(parents=True,exist_ok=False);config=client=None
    for name in ('openai','httpx','httpcore'):logging.getLogger(name).setLevel(logging.WARNING)
    os.environ.pop('OPENAI_LOG',None)
    write(out/'scope.json',{'policy':a.policy,'conditions':['fixed','bounded'],'generations_per_condition':1,
        'max_cost_usd':2*a.max_usd_per_condition,'max_seconds_per_condition':a.seconds_per_condition,
        'same_source':a.source_run,'checkpoint':a.checkpoint,'independent_research_evaluation':False,
        'success_metric':'physical lift only; no assembly success claim','credentials':'masked stdin or environment; never files/arguments'})
    if a.policy=='astra':
        config=replace(load_llm_config(a.llm_config),max_model_requests=1,max_input_tokens=16000,
            max_output_tokens=4096,max_total_tokens=20096,max_cost_usd=f'{a.max_usd_per_condition:.2f}')
        if config.model!='gpt-6-astra' or config.reasoning_effort!='high':raise ValueError('Use the approved Astra high model.')
        config.validate(live=True);write(out/'llm_config.json',config.public())
        if not os.environ.get(config.api_key_env):
            key=getpass.getpass('TEMPORARY_KEY_INPUT (hidden): ')
            if not key.strip():raise ValueError('API credential unavailable; no request sent.')
            os.environ[config.api_key_env]=key.strip();del key
        client=create_openai_client(config)
    rows=[]
    for mode in ('fixed','bounded'):
        r=one(Path(a.source_run),a.checkpoint,out/mode,mode,a.policy,config,client,a.seconds_per_condition);rows.append(r)
        if r.get('usage',{}).get('unknown_usage_requests',0):break
        if r.get('failure',{}).get('failure_code') in ('POLICY_REQUEST_FAILED','POLICY_USAGE_UNKNOWN'):break
    summary={'schema':'motion_plan_comparison/v1','planned_sample_count_per_condition':1,'completed_conditions':len(rows),'policy':a.policy,
        'whole_assembly_claim':False,'generalization_claim':False,'same_source_state':len({r['source_state_sha256'] for r in rows})==1,
        'rows':[{k:r.get(k) for k in ('mode','run_id','status','api_calls','usage','elapsed_s','resolved_motion_plan')} for r in rows]}
    write(out/'comparison.json',summary);print(json.dumps(summary,ensure_ascii=False),flush=True)

if __name__=='__main__':
    try:main()
    finally:os.environ.pop('OPENAI_API_KEY',None)
