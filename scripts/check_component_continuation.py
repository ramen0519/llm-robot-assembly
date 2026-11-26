"""Development-only physical follow-up from a verified, released main state.

This is a component experiment, never a fresh full-assembly success. No goal
placement, attachment, fixture modification or archived grasp is injected.
"""
from pathlib import Path
from copy import deepcopy
import json
import os
import shutil
import time
import uuid
import mujoco
import numpy as np

from assembly_v12.contracts import read,write,append,sha,digest
from assembly_v12.context import History
from assembly_v12.media import STATE,state
from assembly_v2.artifacts import verify_bundle,inventory,counters,report,audit,seal_bundle
from assembly_v2.budget import Budget
from assembly_v2.completion import valid_evidence
from assembly_v2.grasp import candidates,resolve
from assembly_v2.grasp_screen import GoalToolScreen
from assembly_v2.media import Recorder
from assembly_v2.packets import tool_configuration_packet,tool_result_packet
from assembly_v2.policy import Astra
from assembly_v2.scene import body_name
from assembly_v2.skills import MeshSkills
from assembly_v2.transforms import body_pose,xyzw
from assembly_v2.verification import observe_window
from failure_report import json_safe
from llm_runtime import load_llm_config,create_openai_client,ApiLedger
from view_tcp import read_tcp_pose

ROOT=Path(__file__).resolve().parents[1]

def allowed_search_overlay(original,updated):
    """A restored immutable MJB permits search metadata changes, not physics."""
    before=deepcopy(original);after=deepcopy(updated)
    for value in (before,after):
        value.pop('profile_path',None);value.pop('profile_hash',None)
        value['profile'].pop('grasp_pad_center_calibration',None)
        value['profile']['resources'].pop('max_raw_candidates',None)
    if before!=after:raise ValueError('Continuation changed CAD, physics, fixtures or a non-search setting.')
    if updated['profile_hash']!=digest(updated['profile']) or read(updated['profile_path'])!=updated['profile']:
        raise ValueError('Continuation profile identity mismatch.')
    for part in updated['parts'].values():
        if sha(part['mesh'])!=part['mesh_sha256']:raise ValueError('Candidate source mesh changed after the parent trial.')
    from assembly_v2.tooling import calibrated_pad_center
    calibrated_pad_center(updated['profile'],.01,[0,0,.006])
    return {'status':'passed','scope':'sealed original model/CAD and recorded released state; only search cap and empty-tool lookup changed',
            'fresh_geometry_adjacency_recomputation':'not_needed_for_unchanged_immutable_model'}

def checked_source(parent):
    parent=Path(parent);bundle=parent/'replay_bundle';manifest=verify_bundle(bundle)
    r=read(bundle/'result.json');p=read(bundle/'prepared.json')
    events=[json.loads(l) for l in (bundle/'events.jsonl').read_text(encoding='utf-8').splitlines()]
    if r.get('hard_execution_violation') or not r.get('installed'):
        raise ValueError('A continuation needs verified installations and no hard execution violation.')
    executed=[e for e in events if e.get('executed')]
    if not executed or not executed[-1].get('installation_verified'):
        raise ValueError('Last main action must have completed release and installation verification.')
    inherited=set(r['installed']);verified={e['part_id'] for e in events if e.get('installation_verified')}
    if inherited!=verified:raise ValueError('Installed IDs differ from measured installation events.')
    for e in events:
        if e.get('installation_verified'):
            v=e.get('verification') or {};parts=v.get('part_ids',[])
            if e['part_id'] not in parts or not set(parts)<=inherited or not valid_evidence(v,p['completion_profile_hash'],required_parts=parts):
                raise ValueError('Invalid inherited completion evidence.')
    meta=read(bundle/'replay_frames.json')
    if sha(bundle/'model.mjb')!=meta['model_sha256'] or sha(bundle/'replay_states.npz')!=meta['states_sha256']:
        raise ValueError('Parent model/state hashes do not match.')
    values=np.load(bundle/'replay_states.npz',allow_pickle=False)['states']
    if len(values)!=len(meta['frames']) or not np.isclose(meta['frames'][-1]['simulation_time_s'],executed[-1]['simulation_end_s'],atol=1e-9,rtol=0):
        raise ValueError('Main scene advanced after its last verified released state.')
    checkpoints=[read(f) for f in (bundle/'checkpoints').glob('*.json')]
    settled=next(c['settled'] for c in checkpoints if c.get('source')=='live_simulation')
    if any(digest(c['settled'])!=digest(settled) for c in checkpoints):raise ValueError('Supply baseline changed.')
    return bundle,manifest,r,p,events,values[-1].copy(),settled

def run_component(parent,output,part_id,config_path,seconds,calibration=None,failure_from=None):
    started=time.monotonic();out=Path(output);out.mkdir(parents=True,exist_ok=False)
    budget=Budget(seconds,cancelled=lambda:(out/'cancel.request').exists())
    run_id='component-'+uuid.uuid4().hex[:8];record=ledger=history=None;events=[];installed=set();prepared=None
    result={'schema':'assembly_result/v2.0','run_id':run_id,'status':'initializing','policy_source':'astra',
        'main_action_policy_source':'astra','condition':'R0','mode':'roles_shared_history','split':'development',
        'memory':False,'registrations':[],'component_target':part_id,'whole_assembly_claim':False,
        'manual_component_selection':True,'fresh_empty_base_episode':False,'api_calls':0,'error':None}
    try:
        source,manifest,parent_result,prepared,parent_events,saved,settled=checked_source(parent)
        installed=set(parent_result['installed'])
        if part_id not in prepared['order'] or part_id in installed:raise ValueError('Select an uninstalled original part.')
        original_prepared=prepared
        prepared=deepcopy(prepared);prepared['profile']['resources']['max_raw_candidates']=8192
        if calibration:
            prepared['profile']['grasp_pad_center_calibration']=read(calibration)['calibration']
        prepared['profile_path']=str(out/'workcell.json');prepared['profile_hash']=digest(prepared['profile'])
        write(out/'workcell.json',prepared['profile'])
        overlay=allowed_search_overlay(original_prepared,prepared)
        write(out/'continuation_contract.json',overlay)
        result.update(required=prepared['order'],installed=sorted(installed),inherited_installed=sorted(installed))
        write(out/'prepared.json',prepared);write(out/'code_versions.json',inventory(prepared))
        model=mujoco.MjModel.from_binary_path(str(source/'model.mjb'));data=mujoco.MjData(model)
        mujoco.mj_setState(model,data,saved,STATE);mujoco.mj_forward(model,data)
        if not np.array_equal(saved,state(model,data)):
            raise ValueError('Integration state did not restore exactly; no model call or assembly action permitted.')
        evidence=out/'continuation_source';evidence.mkdir()
        np.save(evidence/'parent_last_main_state.npy',saved,allow_pickle=False)
        shutil.copy2(source/'events.jsonl',evidence/'parent_events.jsonl')
        write(evidence/'parent_evidence.json',{'source_bundle':str(source),'source_bundle_id':manifest['id'],
            'model_sha256':sha(source/'model.mjb'),'states_sha256':sha(source/'replay_states.npz'),
            'selected_frame':len(read(source/'replay_frames.json')['frames'])-1,'restored_state_exact':True,
            'parent_installed':sorted(installed),'supply_baseline':settled,
            'controller':'new empty-hand skill starts from measured arm qpos, as at every normal part boundary',
            'no_target_placement':True,'not_a_fresh_full_episode':True})
        extra_attempts=[]
        if failure_from:
            failed=Path(failure_from)/'replay_bundle';failed_manifest=verify_bundle(failed)
            if sha(failed/'model.mjb')!=sha(source/'model.mjb'):raise ValueError('Prior failure uses a different physical model.')
            failed_prepared=read(failed/'prepared.json')
            if any(failed_prepared['parts'][pid]['target']!=prepared['parts'][pid]['target'] for pid in prepared['parts']):
                raise ValueError('Prior failure has different CAD goals.')
            failed_events=[json.loads(l) for l in (failed/'events.jsonl').read_text(encoding='utf-8').splitlines()]
            for e in failed_events:
                if e['part_id']==part_id and e.get('failure') and not e.get('executed'):
                    g=e.get('resolved_grasp') or {}
                    extra_attempts.append({'event_id':e['id'],'source_run':str(failed),'source_bundle_id':failed_manifest['id'],
                        'grasp':{k:g.get(k) for k in ('position_mm','quaternion_xyzw','opening_width_mm')},
                        'failure':e['failure'],'main_executed':False})
            write(evidence/'prior_failed_component_attempts.json',extra_attempts)
        record=Recorder(out,model,data,prepared);record.deadline=budget.deadline
        if record.model_hash!=sha(source/'model.mjb'):raise ValueError('Restored physical model changed.')
        record.part_id=part_id;record.action_id=run_id+':restore_check'
        skills=MeshSkills(model,data,prepared,part_id,installed,settled,budget,recorder=record)
        skills.retreat_complete=True
        boundary=observe_window(skills,sorted(installed),check_id=run_id+':restored_boundary',
            source='live_simulation',model_hash=record.model_hash,prealignment=skills.prealignment)
        write(out/'restored_boundary_verification.json',boundary)
        if not valid_evidence(boundary,prepared['completion_profile_hash'],required_parts=sorted(installed)):
            raise ValueError('Restored scene failed fresh physical verification.')
        images=record.goal_images();cfg=load_llm_config(config_path).validate(live=True)
        if not os.environ.get(cfg.api_key_env):raise ValueError('API key is not available.')
        ledger=ApiLedger(cfg,out/'llm_events.jsonl')
        history=History(out/'conversation.jsonl',{'task':'Development component continuation in the same original CAD/model',
            'target_part':part_id,'parent_run':parent_result['run_id'],'already_physically_installed':sorted(installed),
            'scope':'Select a current grasp, then physically install this component. This is not a full empty-base episode.',
            'candidate_generator_change':'Central and whole-surface coverage are both retained; top, side and oblique candidates are presented.',
            'pose_actions':'part-local mm/quaternion_xyzw','original_goals_m':{pid:p['target'] for pid,p in prepared['parts'].items()}},images)
        if extra_attempts:
            history.add({'role':'user','content':json.dumps({'prior_failed_component_attempts':extra_attempts,
                'note':'Factual failed checks from the same model/CAD; no successful grasp is supplied. Current candidates and opening checks are newly computed.'},ensure_ascii=False)})
        policy=Astra(cfg,create_openai_client(cfg),ledger,history,read(ROOT/'config/policy_prompts.v2.json'),budget)
        write(out/'llm_config.json',cfg.public());pending=None;attempts=0;last_grasp=None
        failures=[e['failure'] for e in parent_events if e['part_id']==part_id and e.get('failure')]
        if failures:pending=failures[-1]
        if extra_attempts:pending=extra_attempts[-1]['failure']
        for revision in range(1,5):
            budget.check('component_turn')
            held=skills.motion.held_pose is not None
            allowed=['insert','stop'] if held else ['pick','stop'] if attempts<3 else ['stop']
            if allowed==['stop']:break
            current=body_pose(data,body_name(part_id,prepared['base_id']))
            pool=candidates(prepared['parts'][part_id],current,revision,budget,prepared['profile'],
                tcp_rotation=read_tcp_pose(data)[1],goal_screen=GoalToolScreen(skills)) if not held else {'presented':[],'raw_generated':0}
            append(out/'candidates.jsonl',{'part_id':part_id,'revision':revision,**pool})
            goal=np.asarray(prepared['parts'][part_id]['target']);role='recovery' if pending else 'assembly'
            context={'part_id':part_id,'target_id':'cad_goal:'+part_id,'observation_revision':revision,'active_role':role,
                'allowed_skills':allowed,'candidate_attempts':attempts,'grasp_candidates':pool['presented'],
                'current_pose':{'position_mm':(current[:3,3]*1000).tolist(),'quaternion_xyzw':xyzw(current)},
                'goal_pose':{'position_mm':(goal[:3,3]*1000).tolist(),'quaternion_xyzw':xyzw(goal)},
                'measured':json_safe(skills.motion.sample(allow_support=True,allow_grasp=held)),
                'holding_part':part_id if held else None,'verified_installed_parts':sorted(installed),
                'failure':pending,'experiences':[],'tool_configuration':tool_configuration_packet(prepared['profile']),
                'workcell':{'tabletop_z_m':prepared['profile']['tabletop_z_m']},
                'operation':prepared['parts'][part_id]['operation'],'remaining':{'candidates':3-attempts,'control':4-revision}}
            before=skills.snapshot();proposal=policy.propose(context);result['policy_decision_evaluated']=True
            action_id=run_id+':action:'+str(revision);record.action_id=action_id;record.check_id=None
            append(out/'observations.jsonl',{'action_id':action_id,'state_hash':before,'context':context})
            event={'id':run_id+':event:'+str(revision),'action_id':action_id,'observation_revision':revision,
                'part_id':part_id,'active_role':role,'policy_source':'astra','proposal':proposal,
                'geometry_candidates_generated':pool['raw_generated'],'candidate_started':False,
                'executed':False,'installation_verified':False,'failure':None,'source':'live_simulation'}
            start_time=float(data.time);response={'status':'stopped'};done=False
            print(part_id,role,proposal['skill'],'revision',revision,flush=True)
            try:
                if before!=skills.snapshot():raise ValueError('Observation changed before action.')
                if proposal['skill']=='stop':result['error']=proposal['reason'];done=True
                elif proposal['skill']=='pick':
                    last_grasp=resolve(proposal['grasp'],pool,prepared['parts'][part_id],current,revision,'astra')
                    event.update(candidate_started=True,resolved_grasp=last_grasp);attempts+=1
                    record.check_id=action_id+':copy';pf=skills.preflight(last_grasp,record.check_id);event['preflight']=pf
                    if not pf['accepted']:
                        pending=pf['failure'];event['failure']=pending;response={'status':'preflight_rejected','preflight':pf}
                    else:
                        record.check_id=None;record.last_failure=None
                        response=skills.pick(last_grasp);pending=None
                elif proposal['skill']=='insert':
                    verification=skills.insert(action_id+':completion');installed.add(part_id)
                    event.update(installation_verified=True,verification=verification,resolved_grasp=last_grasp)
                    response={'status':'installation_verified','verification':verification};done=True
            except Exception as exc:
                pending=skills.failure(exc,skills);event['failure']=pending;response={'status':'failed','failure':pending}
                done=True
            event['executed']=float(data.time)>start_time
            response.update(executed=event['executed'],source='live_simulation' if event['executed'] else event['failure']['source'] if event['failure'] else 'host_no_motion')
            event.update(source=response['source'],tool_result=json_safe(response),simulation_end_s=float(data.time))
            events.append(json_safe(event));append(out/'events.jsonl',events[-1]);history.complete(tool_result_packet(events[-1]['tool_result'],event['id']))
            result.update(status='running',installed=sorted(installed),metrics=counters(events),api_calls=ledger.model_requests,usage=ledger.summary())
            write(out/'result.json',result)
            if done:break
        if part_id in installed:
            final=observe_window(skills,sorted(installed),check_id=run_id+':final',source='live_simulation',model_hash=record.model_hash,prealignment=skills.prealignment)
            write(out/'final_verification.json',final)
            result['status']='component_success' if valid_evidence(final,prepared['completion_profile_hash'],required_parts=sorted(installed)) else 'component_failed'
        else:result['status']='component_failed'
    except Exception as exc:
        import traceback
        result.update(status='component_failed',error=type(exc).__name__+': '+str(exc)[:800])
        write(out/'exception.json',{'traceback':traceback.format_exc()})
        if history and history.pending:history.complete({'status':'stopped_on_error','executed':False})
    finally:
        if record:
            try:result['media']=record.close()
            except Exception as exc:result['media']={'status':'media_error','error':str(exc)[:300]}
        result.update(installed=sorted(installed),metrics=counters(events),elapsed_s=time.monotonic()-started)
        if ledger:result.update(api_calls=ledger.model_requests,usage=ledger.summary(),agent_usage=ledger.agent_totals())
        write(out/'result.json',result)
        if prepared and record:
            result['audit']=audit(out)
            try:result['replay_bundle']=seal_bundle(out,prepared)
            except Exception as exc:result['archive_error']=str(exc)[:500]
            write(out/'result.json',result);report(out,result)
    return result

if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--parent',required=True);ap.add_argument('--output',required=True);ap.add_argument('--part',required=True)
    ap.add_argument('--llm-config',required=True);ap.add_argument('--seconds',type=float,required=True)
    ap.add_argument('--calibration');ap.add_argument('--failure-from')
    a=ap.parse_args()
    if not 1<=a.seconds<=3600:ap.error('Use a finite 1–3600 second budget.')
    r=run_component(a.parent,a.output,a.part,a.llm_config,a.seconds,a.calibration,a.failure_from)
    print(r['status'],r.get('installed'),flush=True)
    raise SystemExit(0 if r['status']=='component_success' else 2)
