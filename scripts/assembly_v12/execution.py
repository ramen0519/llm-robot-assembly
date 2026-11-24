"""A serial LangGraph loop sharing physics, policy context, memory and budgets."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import time
import uuid
from typing import TypedDict

import mujoco
import numpy as np
from langgraph.graph import START, END, StateGraph
from langsmith import tracing_context

from .action_contract import repairable_grasp, allowed_skills, validate_proposal, snapshot
from failure_report import FailureCode, FailureEvent, json_safe
from insertion_recovery import alignment_observation, recover_alignment
from llm_runtime import ApiLedger, create_openai_client, load_llm_config
from .responses import _tool_arguments

from .contracts import append, digest, read, write
from .context import History, SharedPolicy, Scripted, world_pose
from .media import Recorder, state
from .memory import Incidents, Library, Encoder, scope_for, validate_guidance, DEFAULT_MEMORY
from .scene import build_scene, ObservedSkills
from .metrics import write_metrics
from .artifacts import versions,report


class Turn(TypedDict):
    status: str
    turns: int


def error_record(error, phase, source, media=None):
    return {'failure_code':error.code.value if isinstance(error,FailureEvent) else type(error).__name__,
            'phase':getattr(error,'phase',None) or phase,'message':str(error)[:1600], 'source':source,
            'violations':json_safe(getattr(error,'violations',[])),
            'failure_snapshot':media or {'status':'unavailable'},'candidate':json_safe(getattr(error,'candidate',None))}


def generate_guidance(policy, incident, prompts):
    schema={'type':'object','properties':{k:{'type':['string','null']} for k in ('applies_when','strategy','checks')},
            'required':['applies_when','strategy','checks','evidence_refs'],'additionalProperties':False}
    schema['properties']['evidence_refs']={'type':'array','items':{'type':'string'}}
    request={'model':policy.config.model,'instructions':prompts['guidance'],'input':__import__('json').dumps(incident,ensure_ascii=False),
             'tools':[{'type':'function','name':'propose_guidance','strict':True,'parameters':schema}],
             'tool_choice':{'type':'function','name':'propose_guidance'},'parallel_tool_calls':False,'truncation':'disabled',
             'reasoning':{'effort':policy.config.reasoning_effort}}
    with policy.ledger.agent_turn('guidance'):
        response=policy._call_model(request,'post_run_guidance')
    card=_tool_arguments(response,'propose_guidance')
    validate_guidance(card,incident)
    return card


def run(prepared, folder, *, mode='single', memory=False, policy_kind='scripted', llm_config=None,
        memory_root=None, frozen=None, encoder_path=None, seconds=300, split='development', family='unspecified',
        scenario='nominal', first_grasp=None, scripted_guidance=False, prompts_path=None, lineage_id=None):
    if mode=='roles': mode='roles_shared_history'
    if mode not in ('single','roles_shared_history') or scenario not in ('nominal','alignment_offset'):
        raise ValueError('Unsupported decision mode/scenario.')
    if scripted_guidance and (policy_kind!='scripted' or split!='development'):
        raise ValueError('Scripted guidance is a development-only plumbing fixture.')
    folder=Path(folder); folder.mkdir(parents=True,exist_ok=True)
    if any((folder/name).exists() for name in ('result.json','events.jsonl','llm_events.jsonl','model.mjb')):
        raise ValueError('Use a new run folder; restarting must not replenish an earlier run budget.')
    versions(folder)
    if not (folder/'prepared.json').exists(): write(folder/'prepared.json',prepared)
    elif digest(read(folder/'prepared.json'))!=digest(prepared): raise ValueError('Prepared scene changed before run.')
    started=time.monotonic(); run_id=folder.name+'-'+uuid.uuid4().hex[:12]
    timings=dict.fromkeys(('scene_setup','goal_render','stabilization','policy','preflight','main_tool','guidance'),0.)
    def timed(key, function, *args, **kwargs):
        before=time.monotonic()
        try:return function(*args,**kwargs)
        finally:timings[key]+=time.monotonic()-before
    budget={'grasp_per_part':3,'alignment_per_run':1,'replan_per_run':1,
            'control_generations':4*len(prepared['order'])+4,'seconds':seconds}
    result={'schema':'assembly_result/v1.2','run_id':run_id,'mode':mode,'memory':memory,'policy_source':policy_kind,
            'split':split,'family':family,'status':'initializing','input_valid':True,'task_supported':True,
            'preparation_ready':False,'installed':[],'initial_installed':[], 'required':prepared['order'],
            'api_calls':0,'budget':budget,'manual_product_adjustments':False,
            'scenario':scenario,'forced_first_grasp':first_grasp,'error':None,'registrations':[],
            'policy_decision_evaluated':False,'input_source_run_hash':prepared['source_run_hash']}
    incidents=Incidents(run_id,split,family,lineage_id)
    encoder=Encoder(encoder_path) if encoder_path else None
    library=Library(memory_root or DEFAULT_MEMORY,snapshot=frozen,encoder=encoder,
                    allowed_splits=('collection',) if split=='evaluation' else ('collection','development'),
                    exclude_runs=(run_id,))
    write(folder/'memory_snapshot.json',library.snapshot)
    record=history=ledger=model=data=client=None
    alignment_used=replan_used=control_calls=revision=0
    policies={}; installed=set(); last_stage='prepare'; active=None
    prompts=read(prompts_path)
    write(folder/'resolved_config.json',{'prepared_sha256':digest(prepared),'mode':mode,'memory':memory,
          'policy_source':policy_kind,'budget':budget,'prompt_sha256':digest(prompts),'split':split,
          'scenario':scenario,'forced_first_grasp':first_grasp})
    try:
        model,data,registry=timed('scene_setup',build_scene,prepared)
        record=Recorder(folder,model,data,prepared)
        result['model_sha256']=record.model_hash
        result['initial_state_sha256']=digest(state(model,data).tolist())
        write(folder/'initial_state.json',{'state_sha256':result['initial_state_sha256'],'model_sha256':record.model_hash,
                                         'prepared_sha256':digest(prepared),'source':'live_simulation_before_any_step'})
        record.deadline=started+seconds
        images=timed('goal_render',record.goal_images)
        record.sample(force=True)
        if policy_kind=='astra':
            config=load_llm_config(llm_config)
            if config.reasoning_effort!='high' or 'astra' not in config.model:
                raise ValueError('This comparison requires the approved exact Astra model and high reasoning.')
            config=replace(config,max_model_requests=min(config.max_model_requests,budget['control_generations']))
            client=create_openai_client(config)
            ledger=ApiLedger(config,folder/'llm_events.jsonl')
            initial_parts={pid:{'kind':p['kind'],'size_mm':(np.array(p['size'])*1000).tolist(),
                                'goal_pose':world_pose(p['target_world_m'],[0,0,0,1]),
                                'requires':p.get('requires',[]),'support_ids':p.get('support_ids',[])}
                           for pid,p in prepared['parts'].items()}
            history=History(folder/'conversation.jsonl',{'source':'configured_goal','goal_parts':initial_parts,
                    'order':prepared['order'],'base':prepared['base_id'],'world_pose_unit':'mm','rotation':'xyzw'},images)
            for role in (['single'] if mode=='single' else ['assembly','recovery']):
                policies[role]=SharedPolicy(config,client,ledger,history,prompts,role)
                policies[role].deadline=started+seconds
            write(folder/'llm_config.json',config.public())
        elif policy_kind=='scripted':
            policies={r:Scripted(r,first_grasp) for r in ('single','assembly','recovery')}
        else: raise ValueError('Use astra or explicitly labeled scripted physics.')
        result['status']='running'
        for index,active in enumerate(prepared['order']):
            record.part_id=active
            def on_stage(name):
                nonlocal last_stage
                last_stage=name
                record.sample(force=True)
            skills=ObservedSkills(model,data,active,registry,installed,record.sample,on_stage,observer=record,
                                  transport_strategy='direct',empty_approach_strategy='direct_then_rrt')
            if index==0:
                timed('stabilization',skills.settle)
                prepared['preparation_checks']['stability']=True
                result['stable_layout_ready']=True
                prepared['preparation_checks']['robot_access']={pid:'not_checked' for pid in prepared['order']}
                # Access remains a separate measured check as each grasp is proposed.
                write(folder/'prepared_after_settle.json',prepared)
            completed=[]; trials=[]; pending=None; latest_failure=None; recover_focus=False
            local_replans=0; role='single' if mode=='single' else 'assembly'
            seen=set(); injection_used=False; stopped_reason=None
            def turn(graph_state):
                nonlocal revision,control_calls,alignment_used,replan_used,local_replans,pending,latest_failure
                nonlocal role,recover_focus,injection_used,stopped_reason
                if time.monotonic()>started+seconds:
                    raise TimeoutError('run_wall_time_budget')
                if control_calls>=budget['control_generations']:
                    stopped_reason='control_generation_budget'
                    return {'status':'stopped','turns':graph_state['turns']}
                revision+=1
                observation=snapshot(skills)
                measured=skills.verifier.sample(data,skills.motion.reference,allow_support=True,
                                                allow_grasp=skills.motion.held_pose is not None)
                if skills.motion.held_pose is not None:
                    skills.verifier.check_held(data,measured,skills.motion.held_pose)
                permitted=allowed_skills(completed,pending,alignment_used,1,
                    transport_replan_available=skills.insert_replan_available() if pending and local_replans<1 else False,
                    transport_replans=replan_used,transport_replan_budget=1)
                if not completed and len(trials)>=3: permitted=['stop']
                if pending and pending['failure_code']=='ALIGNMENT_OUT_OF_TOLERANCE':
                    if pending['source']!='live_simulation' or pending['phase']!='insert_alignment_check' or skills.motion.held_pose is None:
                        permitted=['stop']
                    else:
                        try:
                            aligned=alignment_observation(skills,allow_support=False)
                            if (aligned['lateral_error_m']>.020 or aligned['rim_clearance_m']<.020
                                    or aligned['orientation_error_rad']>np.deg2rad(1) or aligned['part_speed_m_s']>.001 or aligned['supported']):
                                permitted=['stop']
                        except FailureEvent: permitted=['stop']
                role='single' if mode=='single' else ('recovery' if recover_focus or pending else 'assembly')
                geometry=skills.grasp_geometry()
                cards=[]
                if memory and latest_failure and permitted!=['stop']:
                    query=scope_for(latest_failure,geometry,permitted,'UR3e/2F85/FrameSkills/vertical-v1')
                    snap=latest_failure.get('failure_snapshot',{})
                    image=folder/snap['png'] if snap.get('png') and snap.get('status')=='exact_detector_snapshot' else None
                    cards,_=library.retrieve(query,image=image,image_camera=snap.get('camera_version'),log=folder/'retrieval.jsonl',
                        refs={'failure_check_id':latest_failure.get('check_id') or latest_failure.get('failure_snapshot',{}).get('check_id'),
                              'detector_check_id':latest_failure.get('failure_snapshot',{}).get('check_id'),
                              'parent_action_id':latest_failure.get('parent_action_id') or latest_failure.get('failure_snapshot',{}).get('action_id'),
                              'observation_revision':revision,'part_id':active})
                context={'task_id':prepared['source_run_hash'],'part_id':active,'target_id':registry[active]['config']['target']['id'],
                    'first_part':index==0,
                    'holding_part':active if skills.motion.held_pose is not None else None,
                    'measured_gripper':{k:measured[k] for k in ('pad_gap_mm','left_contact','right_contact')},
                    'verified_installed_parts':sorted(installed),
                    'goal_assembly_relations':{k:registry[active][k] for k in ('requires','mating_ids','support_ids')},
                    'active_role':role,'observation_revision':revision,'observation':observation,'completed':completed,
                    'failure':pending,'failures':[latest_failure] if latest_failure else [],'failure_event':latest_failure,
                    'experiences':cards,'last_verification':None,'recovery_budget_remaining':1-alignment_used,
                    'transport_replan_budget_remaining':1-replan_used,'transport_replans':replan_used,
                    'transport_replan':skills.insert_replan_context() if pending and 'replan_insert' in permitted else None,
                    'allowed_skills':permitted,'remaining_budgets':{'grasps':3-len(trials),'alignment':1-alignment_used,
                        'replans':1-replan_used,'control_generations':budget['control_generations']-control_calls},
                    'insert_result_skill':{'post':'insert_post/v1','sleeve':'insert_sleeve/v1','cover':'seat_cover/v1'}[registry[active]['kind']]}
                if not completed:
                    context['grasp_task']={'geometry':geometry,'previous_trials':trials,'attempts_remaining':3-len(trials)}
                if 'recover_alignment' in permitted: context['alignment_observation']=alignment_observation(skills)
                action_id=f'{run_id}:action:{uuid.uuid4().hex[:12]}'
                record.action_id=action_id; record.check_id=None; record.last_failure=None
                append(folder/'observations.jsonl',{'id':f'{run_id}:observation:{revision}','action_id':action_id,**context})
                proposal=validate_proposal(timed('policy',policies[role].propose,deepcopy(context)),context)
                control_calls+=1
                event={'action_id':action_id,'observation_revision':revision,'part_id':active,'target_id':context['target_id'],
                       'active_role':role,'proposal':proposal,'policy_source':policy_kind,'source':'live_simulation',
                       'memory_snapshot':library.snapshot['id'],'retrieved':[c['record_id'] for c in cards],
                       'failure':None,'installation_verified':False,'executed':False}
                action_start=float(data.time)
                print(f'{active} {role} {proposal["skill"]} revision={revision}',flush=True)
                response={'status':'stopped','executed':False}
                try:
                    if observation!=snapshot(skills): raise ValueError('Stale observation before action.')
                    action=proposal['skill']
                    if action=='stop':
                        stopped_reason=proposal['reason']; return {'status':'stopped','turns':graph_state['turns']+1}
                    if action=='pick':
                        record.check_id=action_id+':preflight'
                        token=digest({'grasp':proposal['grasp'],'state':state(model,data).tolist()})
                        if token in seen:
                            raise ValueError('unchanged_rejected_candidate_at_same_state')
                        seen.add(token)
                        trial=timed('preflight',skills.check_grasp,proposal['grasp'])
                        trial.update(observation_revision=revision,check_id=record.check_id,parent_action_id=action_id)
                        trials.append(trial)
                        event['preflight']=trial
                        if not trial['accepted']:
                            latest_failure=trial
                            event['failure']=trial; event['source']='mujoco_preflight_copy'
                            event['scope']=scope_for(trial,geometry,['pick','stop'],'UR3e/2F85/FrameSkills/vertical-v1')
                            response={'status':'preflight_rejected','executed':False,'failure':trial}
                            recover_focus=True
                            if not repairable_grasp(trial) or len(trials)>=3:
                                stopped_reason='preflight_not_repairable_or_grasp_budget'
                                return {'status':'stopped','turns':graph_state['turns']+1}
                            return {'status':'running','turns':graph_state['turns']+1}
                        record.check_id=None
                        if observation!=snapshot(skills): raise ValueError('Main scene changed during copy check.')
                        prepared['preparation_checks']['robot_access'][active]='full_pick_insert_copy_pass'
                        result['preparation_ready']=all(v=='full_pick_insert_copy_pass' for v in prepared['preparation_checks']['robot_access'].values())
                        event['executed']=True
                        response=timed('main_tool',skills.pick,active,grasp=proposal['grasp'])
                        row=skills.verifier.sample(data,skills.motion.reference,allow_support=False,allow_grasp=True)
                        skills.verifier.check_held(data,row,skills.motion.held_pose)
                        if row['part_bottom_clearance_m']<.05: raise ValueError('Actual lift not verified.')
                        completed.append('pick'); recover_focus=False; latest_failure=None
                    elif action in ('insert','replan_insert'):
                        event['executed']=True
                        if action=='replan_insert':
                            replan_used+=1; local_replans+=1
                            response=timed('main_tool',skills.replan_insert,active,context['target_id'])
                        else:
                            offset=[.006,0.] if scenario=='alignment_offset' and index==0 and not injection_used else None
                            injection_used=True
                            response=timed('main_tool',skills.insert,active,context['target_id'],alignment_offset_m=offset)
                        row=skills.verifier.sample(data,skills.motion.reference,allow_support=True,allow_grasp=False)
                        verification=skills.insertion_evidence([row])
                        completed.append('insert'); installed.add(active)
                        event.update(installation_verified=True,verification=json_safe(verification))
                        pending=latest_failure=None; recover_focus=False
                    elif action=='recover_alignment':
                        event['executed']=True
                        alignment_used+=1
                        failure=FailureEvent(FailureCode(pending['failure_code']),pending['message'])
                        response=timed('main_tool',recover_alignment,skills,failure)
                        skills.verifier.verify_alignment(data)
                        pending=latest_failure=None; recover_focus=False
                    else: raise ValueError('Unsupported action in integrated executor.')
                    event['executed']=True
                    event['result']=json_safe(response)
                    response={'status':'verified','executed':True,'action':action,'installation_verified':event['installation_verified'],
                              'checks':event.get('verification',{}).get('checks',{})}
                    return {'status':'part_complete' if event['installation_verified'] else 'running','turns':graph_state['turns']+1}
                except FailureEvent as exc:
                    failure_source=('ik_candidate' if (exc.phase or '').endswith('/ik') else
                                    'planner_candidate' if (exc.phase or '').endswith('/preflight') else 'live_simulation')
                    latest_failure=error_record(exc,last_stage,failure_source,record.materialize(getattr(exc,'v12snapshot',None)))
                    pending=latest_failure; recover_focus=True
                    event['failure']=latest_failure
                    event['source']=failure_source
                    permitted_after=allowed_skills(completed,pending,alignment_used,1,
                        transport_replan_available=skills.insert_replan_available(exc) if local_replans<1 else False,
                        transport_replans=replan_used,transport_replan_budget=1)
                    event['scope']=scope_for(latest_failure,geometry,permitted_after,'UR3e/2F85/FrameSkills/vertical-v1')
                    response={'status':'physical_failure','executed':True,'failure':latest_failure}
                    if permitted_after==['stop']:
                        stopped_reason=latest_failure['failure_code']
                        return {'status':'failed','turns':graph_state['turns']+1}
                    return {'status':'running','turns':graph_state['turns']+1}
                finally:
                    event['executed']=float(data.time)>action_start+1e-9
                    event['simulation_time_s']=float(data.time)
                    event['execution_started_s']=action_start
                    event['source_run_hash']=prepared['source_run_hash']
                    facts=incidents.event(active,context['target_id'],json_safe(event))
                    append(folder/'events.jsonl',facts)
                    if history and history.pending: history.complete(response)
                    record.sample(force=True)
            graph=StateGraph(Turn)
            graph.add_node('observe_decide_execute_verify',turn)
            graph.add_edge(START,'observe_decide_execute_verify')
            graph.add_conditional_edges('observe_decide_execute_verify',
                lambda s:'observe_decide_execute_verify' if s['status']=='running' else END)
            with tracing_context(enabled=False):
                terminal=graph.compile().invoke({'status':'running','turns':0},{'recursion_limit':24})
            result['installed']=list(x for x in prepared['order'] if x in installed)
            if terminal['status']!='part_complete':
                result.update(status=terminal['status'],error=stopped_reason); break
        else:
            # Read-only final all-part preservation, then independent installation evidence for every part.
            final=[]
            for pid in prepared['order']:
                checker=ObservedSkills(model,data,pid,registry,installed)
                checker.verifier.mating_enabled=True
                checker.verifier.verify_other_parts(data)
                row=checker.verifier.sample(data,checker.motion.reference,allow_support=True,allow_grasp=False)
                final.append({'part_id':pid,**json_safe(checker.insertion_evidence([row]))})
            write(folder/'final_verification.json',final)
            result['status']='success'
    except Exception as exc:
        result.update(status='blocked' if model is None or (policy_kind=='astra' and ledger is None) else 'failed',
                      error=error_record(exc,last_stage,'system'))
        import traceback
        write(folder/'exception.json',{'type':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc()})
    finally:
        result['installed']=[x for x in prepared['order'] if x in installed]
        if data is not None:
            result['final_state_sha256']=digest(state(model,data).tolist())
        write(folder/'prepared_after_execution.json',prepared)
        facts=incidents.finalize(result['status']=='success')
        write(folder/'incidents.json',facts)
        for incident in facts:
            if incident['registration']!='eligible': continue
            try:
                if policy_kind=='astra' and ledger and not ledger.halted and time.monotonic()<started+seconds:
                    card=timed('guidance',generate_guidance,next(iter(policies.values())),incident,prompts)
                    source='astra_post_run'
                elif scripted_guidance:
                    card={'applies_when':'같은 수직 조립 형식과 같은 단계의 검사 거부가 확인된 경우.',
                          'strategy':'현재 파지 기하와 이전 거부 근거를 확인하여 파지 후보를 수정한다.',
                          'checks':'복사 검사 이후 주 실행의 집기와 원래 설치 완료를 다시 확인한다.',
                          'evidence_refs':[e['id'] for e in incident['events'] if e.get('failure') or e.get('installation_verified')]}
                    source='scripted_development_fixture'
                else:
                    result['registrations'].append({'incident':incident['id'],'status':'pending','reason':'guidance_generation_not_run'})
                    continue
                problem=next(e for e in incident['events'] if e.get('failure'))
                registration=library.register(incident,card,problem['scope'],folder,guidance_source=source)
                result['registrations'].append(registration)
            except Exception as exc:
                result['registrations'].append({'incident':incident['id'],'status':'pending','reason':str(exc)[:300]})
        result.update(control_decisions=control_calls,alignment_attempts=alignment_used,replan_attempts=replan_used,
                      policy_decision_evaluated=policy_kind=='astra' and control_calls>0,
                      physical_wall_seconds=time.monotonic()-started,simulation_seconds=float(data.time) if data else 0.,
                      incidents=len(facts),solved_local=sum(i['state']=='solved_local' for i in facts))
        result['execution_wall_seconds']=result['physical_wall_seconds']
        result['timings_seconds']=timings
        result['timing_scope']='physical_wall_seconds is a legacy alias for execution_wall_seconds, including policy and checks. main_tool includes planning and verification inside the existing executor. preflight includes copy simulation and detector rendering. Categories are measured, not inferred by subtraction.'
        if ledger: result.update(api_calls=ledger.model_requests,usage=ledger.summary(),agent_usage=ledger.agent_totals())
        if client is not None: client.close()
        if record:
            record.deadline=float('inf')
            try: result['media']=record.close()
            except Exception as exc: result['media']={'status':'media_error','error':str(exc)}
        else: result['media']={'status':'not_run'}
        result['elapsed_seconds']=time.monotonic()-started
        write(folder/'result.json',result)
        write_metrics(result,folder)
        report(result,folder)
    return result
