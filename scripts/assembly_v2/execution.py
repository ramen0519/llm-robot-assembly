"""One serial LangGraph execution path for scripted checks and actual Astra runs."""
from copy import deepcopy
from pathlib import Path
from typing import TypedDict
import os
import time
import uuid
import mujoco
import numpy as np
from langgraph.graph import StateGraph,START,END
from langsmith import tracing_context
from failure_report import FailureEvent,json_safe
from llm_runtime import load_llm_config,create_openai_client,ApiLedger
from assembly_v12.context import History
from assembly_v12.contracts import read,write,append,sha,digest
from .artifacts import inventory,counters,report,audit,seal_bundle,events
from .completion import valid_evidence
from .budget import Budget,ControlBudget
from .scene import build_scene,body_name
from .skills import MeshSkills
from .verification import observe_window
from .transforms import body_pose,xyzw
from .grasp import candidates,resolve
from .media import Recorder
from .memory import Library,Encoder,DEFAULT_MEMORY,source_identity,scope_for,incidents_from,validate_guidance
from .policy import Astra,Scripted

ROOT=Path(__file__).resolve().parents[2]
CONDITIONS={'S0':('single',False),'S1':('single',True),'R0':('roles_shared_history',False),'R1':('roles_shared_history',True)}

def split_contract(split,memory,policy,snapshot,plan,prepared,lineage):
    if split not in ('development','collection','evaluation'):raise ValueError('Unknown split.')
    if split=='collection' and (memory or policy!='astra'):raise ValueError('Research collection requires actual Astra with retrieval OFF.')
    if split=='evaluation':
        if policy!='astra' or snapshot is None or plan is None:raise ValueError('Evaluation requires actual Astra, planned fixed snapshot (also for memory-off).')
        if plan.get('prepared_sha256')!=digest(prepared) or plan.get('lineage_id')!=lineage or plan.get('snapshot_id')!=snapshot['id']:
            raise ValueError('Evaluation case or snapshot differs from frozen plan.')

class Turn(TypedDict):
    done: bool

def run(prepared,folder,*,condition='R0',policy_kind='scripted',llm_config=None,memory_root=None,snapshot=None,
        encoder_path=None,seconds=300,budget=None,split='development',lineage_id=None,plan=None,first_grasp=None,approach=None,failure_probe=None):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);run_id=folder.name+'-'+uuid.uuid4().hex[:8]
    if any((folder/n).exists() for n in ('result.json','events.jsonl','model.mjb')):raise ValueError('Use a fresh run directory; no budget reset.')
    mode,memory=CONDITIONS[condition];lineage_id=lineage_id or run_id
    split_contract(split,memory,policy_kind,snapshot,plan,prepared,lineage_id)
    if (first_grasp is not None or approach is not None) and (policy_kind!='scripted' or split!='development'):
        raise ValueError('A development grasp/approach option cannot be silently applied to Astra/research.')
    if failure_probe is not None and (split!='development' or policy_kind!='astra' or first_grasp is not None or approach is not None):
        raise ValueError('An explicit copy-only development failure probe requires Astra development; never research collection/evaluation.')
    budget=budget or Budget(seconds);control=ControlBudget(len(prepared['order']));started=time.monotonic()
    from .validation import check_prepared
    check_prepared(prepared,budget)
    result={'schema':'assembly_result/v2.0','run_id':run_id,'status':'initializing',
        'policy_source':'astra_with_explicit_development_probe' if failure_probe is not None else policy_kind,
        'development_failure_probe':failure_probe is not None,'main_action_policy_source':policy_kind,
        'condition':condition,'mode':mode,'memory':memory,'split':split,'lineage_id':lineage_id,
        'source_identity':source_identity(prepared),'required':prepared['order'],'installed':[],'api_calls':0,
        'policy_decision_evaluated':False,'reference_status':'not_run','preparation_status':'geometry_ready',
        'manual_product_adjustments':False,'budget':{'control_limit':control.limit,'candidate_per_part':3,'alignment_per_run':1,'replan_per_run':1,'seconds':seconds},
        'metrics':counters([]),'error':None,'registrations':[]}
    write(folder/'prepared.json',prepared);write(folder/'code_versions.json',inventory(prepared))
    prompts=read(ROOT/'config/policy_prompts.v2.json')
    write(folder/'resolved_config.json',{'prepared_sha256':digest(prepared),'condition':condition,'policy_source':policy_kind,
         'split':split,'lineage_id':lineage_id,'prompts_hash':digest(prompts),'budget':result['budget'],
         'first_grasp':first_grasp,'approach':approach,'development_failure_probe':failure_probe,
         'completion_profile_hash':prepared['completion_profile_hash']})
    record=ledger=history=policy=skills=library=None;installed=set();settled={};prealignment={};all_events=[]
    alignments=replans=revision=0;final=None;incidents=[];guidance=[];model=None;data=None;probe_used=False
    def persist():
        result.update(installed=sorted(installed),metrics=counters(all_events),elapsed_s=time.monotonic()-started,control_generations=control.used)
        if ledger:
            result.update(api_calls=ledger.model_requests,usage=ledger.summary(),agent_usage=ledger.agent_totals())
        write(folder/'result.json',result);report(folder,result)
    try:
        if not prepared['order']:result['status']='no_work';return result
        encoder=Encoder(encoder_path,prepared['profile']['resources']['encoder_seconds']) if encoder_path else None
        library=Library(memory_root or DEFAULT_MEMORY,snapshot=snapshot,encoder=encoder,split=split,
                       exclude_sources=source_identity(prepared) if split=='evaluation' else None)
        write(folder/'memory_snapshot.json',library.snapshot)
        reference_model,reference_data=build_scene(prepared,reference=True)
        mujoco.mj_saveModel(reference_model,str(folder/'reference_model.mjb'),None)
        ref=MeshSkills(reference_model,reference_data,prepared,prepared['order'][0],set(prepared['order']),{},budget)
        ref.verifier.reference_mode=True;ref.retreat_complete=True
        reference=observe_window(ref,prepared['order'],check_id=run_id+':reference',source='reference_scene',
                     model_hash=sha(folder/'reference_model.mjb'),prealignment={},reference=True)
        write(folder/'reference_verification.json',reference);result['reference_status']=reference['status']
        if not valid_evidence(reference,prepared['completion_profile_hash'],source='reference_scene',required_parts=prepared['order']):
            result['status']='reference_invalid';return result
        model,data=build_scene(prepared);record=Recorder(folder,model,data,prepared);record.deadline=budget.deadline
        images=record.goal_images();record.part_id=prepared['order'][0]
        skills=MeshSkills(model,data,prepared,record.part_id,installed,settled,budget,recorder=record);skills.settle()
        write(folder/'settled_initial_poses.json',settled);result['preparation_status']='reference_and_initial_stability_passed'
        if policy_kind=='astra':
            config=load_llm_config(llm_config)
            if config.model!='gpt-6-astra' or config.reasoning_effort!='high':raise ValueError('Use the approved Astra high configuration.')
            if not os.environ.get(config.api_key_env):
                result.update(status='not_run',error='API_key_not_available; no model request or main assembly action started');return result
            client=create_openai_client(config);ledger=ApiLedger(config,folder/'llm_events.jsonl')
            history=History(folder/'conversation.jsonl',{'task':'Assemble original CAD goal with one UR3e/2F85',
                   'order':prepared['order'],'base':prepared['base_id'],'goals':{pid:p['target'] for pid,p in prepared['parts'].items()},
                   'goal_matrix_translation_unit':'m','pose_actions':'part_local mm/quaternion_xyzw'},images)
            policy=Astra(config,client,ledger,history,prompts,budget);write(folder/'llm_config.json',config.public())
        elif policy_kind=='scripted':policy=Scripted(first_grasp,approach)
        else:raise ValueError('Unsupported policy kind.')
        result['status']='running';persist()
        for part_index,active in enumerate(prepared['order']):
            record.part_id=active;skills=MeshSkills(model,data,prepared,active,installed,settled,budget,recorder=record)
            skills.prealignment=prealignment
            attempts=0;pending=None;last_grasp=None;seen=set();part_done=False;main_changed_failure=False
            def turn(_state):
                nonlocal attempts,pending,revision,part_done,alignments,replans,last_grasp,main_changed_failure,probe_used
                budget.check('graph_turn');control.begin();revision+=1
                current=body_pose(data,body_name(active,prepared['base_id']))
                measured=skills.motion.sample(allow_support=True,allow_grasp=skills.motion.held_pose is not None)
                allowed=['stop']
                if skills.motion.held_pose is not None:
                    if pending and pending['failure_code']=='ALIGNMENT_OUT_OF_TOLERANCE' and alignments<1:allowed=['recover_alignment','stop']
                    elif pending and replans<1:allowed=['replan_insert','stop']
                    elif not pending:allowed=['insert','stop']
                elif attempts<3:
                    stable=measured['part_speed_m_s']<=.001 and measured['part_angular_speed_rad_s']<=.01
                    open_safe=measured['pad_gap_mm']>=80 and not measured['left_contact'] and not measured['right_contact']
                    if not main_changed_failure or stable and open_safe:allowed=['pick','stop']
                if result.get('hard_execution_violation'):allowed=['stop']
                pool=candidates(prepared['parts'][active],current,revision,budget,prepared['profile']) if 'pick' in allowed else {'presented':[],'raw_generated':0}
                append(folder/'candidates.jsonl',{'part_id':active,'revision':revision,**pool})
                role='single' if mode=='single' else 'recovery' if pending else 'assembly'
                cards=[]
                if memory and pending:
                    query=scope_for(pending,last_grasp,'pick' if 'pick' in allowed else allowed[0],prepared['completion_profile_hash'])
                    snap=pending.get('failure_snapshot',{});image=folder/snap['png'] if snap.get('png') else None
                    cards=library.retrieve(query,image=image,refs={'run_id':run_id,'part_id':active,'revision':revision,
                        'failure_check_id':snap.get('check_id')},log=folder/'retrieval.jsonl')
                goal=np.asarray(prepared['parts'][active]['target'])
                compact=[{k:c[k] for k in ('candidate_id','position_mm','quaternion_xyzw','opening_width_mm','approach_family','geometry','checks')} for c in pool['presented']]
                context={'part_id':active,'target_id':'cad_goal:'+active,'observation_revision':revision,'active_role':role,
                    'allowed_skills':allowed,'candidate_attempts':attempts,'grasp_candidates':compact,
                    'current_pose':{'position_mm':(current[:3,3]*1000).tolist(),'quaternion_xyzw':xyzw(current)},
                    'goal_pose':{'position_mm':(goal[:3,3]*1000).tolist(),'quaternion_xyzw':xyzw(goal)},
                    'measured':json_safe(measured),'holding_part':active if skills.motion.held_pose is not None else None,
                    'verified_installed_parts':sorted(installed),'failure':pending,'experiences':cards,
                    'operation':prepared['parts'][active]['operation'],'remaining':{'candidates':3-attempts,'alignment':1-alignments,'replan':1-replans,'control':control.limit-control.used}}
                before=skills.snapshot();is_probe=failure_probe is not None and not probe_used
                if is_probe:
                    probe_used=True
                    proposal={'skill':'pick','part_id':active,'target_id':context['target_id'],'observation_revision':revision,
                        'reason':'Explicit development-only copy failure probe, not an Astra decision. Main execution is forbidden for this probe.',
                        'grasp':deepcopy(failure_probe)}
                else:
                    proposal=policy.propose(context);result['policy_decision_evaluated']=policy_kind=='astra'
                action_id=run_id+':action:'+str(revision);record.action_id=action_id;record.check_id=None
                append(folder/'observations.jsonl',{'action_id':action_id,'state_hash':before,'context':context})
                event={'id':run_id+':event:'+str(revision),'action_id':action_id,'observation_revision':revision,'part_id':active,
                    'active_role':role,'proposal':proposal,'policy_source':'development_failure_probe' if is_probe else policy_kind,'source':'live_simulation',
                    'geometry_candidates_generated':pool['raw_generated'],'candidate_started':False,'executed':False,
                    'installation_verified':False,'failure':None,'retrieved':[c['record_id'] for c in cards],
                    'memory_snapshot':library.snapshot['id']}
                response={'status':'stopped'};start_time=float(data.time);action=proposal['skill'];done=False
                print(active,role,action,'revision',revision,flush=True)
                try:
                    if before!=skills.snapshot():raise ValueError('Observation changed before action.')
                    if action=='stop':result['error']=proposal['reason'];done=True
                    elif action=='pick':
                        grasp=resolve(proposal['grasp'],pool,prepared['parts'][active],current,revision,policy_kind)
                        if is_probe:grasp['source']='explicit_development_failure_probe'
                        if policy_kind=='scripted' and policy.last_provenance=='development_injection':grasp['source']='development_injection'
                        if policy_kind=='scripted' and policy.last_provenance=='development_guidance_revalidation':
                            grasp.update(source='development_guidance_revalidation',guidance_id=policy.last_guidance)
                        token=digest([grasp['tcp_in_part'],grasp['opening_width_mm'],before])
                        if token in seen:raise ValueError('unchanged_rejected_candidate_at_same_state')
                        seen.add(token);attempts+=1;event['candidate_started']=True;event['resolved_grasp']=grasp;last_grasp=grasp
                        record.check_id=action_id+':copy'
                        preflight=skills.preflight(grasp,record.check_id);event['preflight']=preflight
                        if not preflight['accepted']:
                            event['failure']=preflight['failure'];pending=event['failure'];response={'status':'preflight_rejected','preflight':preflight}
                        else:
                            if is_probe:raise ValueError('development_probe_did_not_reproduce_a_failure; injected grasp cannot move the main scene')
                            record.check_id=None;record.last_failure=None
                            response=skills.pick(grasp);pending=None;main_changed_failure=False
                    elif action in ('insert','replan_insert'):
                        if action=='replan_insert':replans+=1
                        verification=skills.insert(action_id+':completion',replan=action=='replan_insert')
                        installed.add(active);part_done=True;done=True;pending=None
                        event.update(installation_verified=True,verification=verification,resolved_grasp=last_grasp)
                        response={'status':'installation_verified','verification':verification}
                    elif action=='recover_alignment':
                        alignments+=1;response=skills.recover_alignment();pending=None
                except Exception as exc:
                    pending=skills.failure(exc,skills);event['failure']=pending;response={'status':'failed','failure':pending}
                    main_changed_failure=float(data.time)>start_time
                    if isinstance(exc,TimeoutError):result['deadline_reached']=True
                    if not isinstance(exc,FailureEvent):done=True
                finally:
                    event['executed']=float(data.time)>start_time
                    if skills.verifier.hard_violation:
                        result['hard_execution_violation']=skills.verifier.hard_violation
                    if event['failure']:
                        event['failure'].update(executed_in_main=event['executed'],parent_action_id=action_id,observation_revision=revision)
                        event['scope']=scope_for(event['failure'],last_grasp,action,prepared['completion_profile_hash'])
                    response['executed']=event['executed'];response['source']='live_simulation' if event['executed'] else event['failure']['source'] if event['failure'] else 'host_no_motion'
                    event['source']=response['source']
                    event['tool_result']=json_safe(response);event['simulation_end_s']=float(data.time)
                    all_events.append(json_safe(event));append(folder/'events.jsonl',all_events[-1])
                    if is_probe and history:
                        history.add({'role':'user','content':__import__('json').dumps({'explicit_development_probe':True,
                            'not_an_Astra_decision':True,'proposal':proposal,'result':all_events[-1]['tool_result']},
                            ensure_ascii=False,separators=(',',':'))})
                    if history and history.pending:history.complete(all_events[-1]['tool_result'])
                    persist()
                return {'done':done}
            graph=StateGraph(Turn);graph.add_node('decision_execute_verify',turn);graph.add_edge(START,'decision_execute_verify')
            graph.add_conditional_edges('decision_execute_verify',lambda s:END if s['done'] else 'decision_execute_verify')
            with tracing_context(enabled=False):graph.compile().invoke({'done':False},{'recursion_limit':control.limit+2})
            if not part_done:break
        # Every installed part is measured in this one final window, also for
        # a locally solved incident in a later partial run.
        if installed and skills.motion.held_pose is None and skills.retreat_complete:
            final=observe_window(skills,sorted(installed),check_id=run_id+':final',source='live_simulation',
                   model_hash=record.model_hash,prealignment=prealignment)
            write(folder/'final_verification.json',final)
        success=not result.get('hard_execution_violation') and installed==set(prepared['order']) and valid_evidence(final,prepared['completion_profile_hash'],required_parts=prepared['order'])
        result['status']='physical_success' if success else 'deadline' if result.get('deadline_reached') else 'partial' if installed else 'failed'
        incidents=incidents_from(all_events,run_id,prepared,split,lineage_id,result['policy_source'],final)
        if result.get('hard_execution_violation'):
            for incident in incidents:incident.update(registration='pending',registration_reason='hard_execution_violation_in_episode')
        write(folder/'incidents.json',incidents)
        for incident in incidents:
            if incident['registration']!='eligible' or policy_kind!='astra':
                guidance.append({'incident_id':incident['id'],'status':'pending','reason':'requires_eligible_actual_astra_incident'});continue
            try:
                card=policy.guidance(incident)
                guidance.append({'incident_id':incident['id'],'status':'generated' if validate_guidance(card,incident) else 'pending','card':card})
            except Exception as exc:guidance.append({'incident_id':incident['id'],'status':'pending','reason':str(exc)[:400]})
        write(folder/'guidance_candidates.json',guidance)
    except Exception as exc:
        import traceback
        result.update(status='deadline' if isinstance(exc,TimeoutError) else 'failed',error=type(exc).__name__+': '+str(exc)[:1000])
        write(folder/'exception.json',{'traceback':traceback.format_exc()})
        if history and history.pending:history.complete({'status':'stopped_on_error','executed':False,'error':type(exc).__name__})
    finally:
        if record:
            try:result['media']=record.close()
            except Exception as exc:result['media']={'status':'media_error','error':str(exc)[:500]}
        persist();result['audit']=audit(folder)
        try:
            result['replay_bundle']=seal_bundle(folder,prepared)
            for row in guidance:
                if row['status']=='generated':
                    incident=next(e for e in incidents if e['id']==row['incident_id'])
                    result['registrations'].append(library.register(incident,row['card'],folder,'astra'))
        except Exception as exc:result['archive_error']=str(exc)[:800]
        write(folder/'registrations.json',result['registrations']);persist()
    return result
