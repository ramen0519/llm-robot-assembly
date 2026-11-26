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
from view_tcp import read_tcp_pose
from langsmith import tracing_context
from failure_report import FailureEvent,json_safe
from llm_runtime import load_llm_config,create_openai_client,ApiLedger
from assembly_v12.context import History
from assembly_v12.contracts import read,write,append,sha,digest
from .artifacts import inventory,counters,report,audit,seal_bundle,events
from .completion import valid_evidence
from .budget import Budget,ControlBudget,DevelopmentBudget
from .scene import build_scene,body_name
from .skills import MeshSkills
from .verification import observe_window
from .readiness import check_reference
from .transforms import body_pose,xyzw
from .grasp import candidates,resolve
from .media import Recorder
from .memory import Library,Encoder,DEFAULT_MEMORY,source_identity,scope_for,incidents_from,validate_guidance
from .policy import Astra,Scripted
from .packets import tool_result_packet
from .motion_plan import contract as motion_plan_contract

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
        encoder_path=None,seconds=300,budget=None,split='development',lineage_id=None,plan=None,first_grasp=None,approach=None,failure_probe=None,motion_plan_mode='bounded',development_learning=False,resume_parent=None,no_wall_time_limit=False):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);run_id=folder.name+'-'+uuid.uuid4().hex[:8]
    if any((folder/n).exists() for n in ('result.json','events.jsonl','model.mjb')):raise ValueError('Use a fresh run directory; no budget reset.')
    if motion_plan_mode not in ('fixed','bounded'):raise ValueError('Unknown motion plan mode.')
    if development_learning:
        if policy_kind!='scripted' or split!='development' or not memory_root or Path(memory_root).resolve()==Path(DEFAULT_MEMORY).resolve():
            raise ValueError('Scripted learning requires development mode and an explicit isolated memory directory.')
    dt=prepared.get('completion_profile',{}).get('physics_timestep_s',.002)
    if dt!=.002 and split!='development':raise ValueError('Experimental timestep runs are development only; freeze/qualify before collection or evaluation.')
    mode,memory=CONDITIONS[condition];lineage_id=lineage_id or run_id
    split_contract(split,memory,policy_kind,snapshot,plan,prepared,lineage_id)
    if split=='evaluation' and plan.get('motion_plan_mode','bounded')!=motion_plan_mode:raise ValueError('Motion-plan mode differs from frozen evaluation plan.')
    if (first_grasp is not None or approach is not None) and (policy_kind!='scripted' or split!='development'):
        raise ValueError('A development grasp/approach option cannot be silently applied to Astra/research.')
    if failure_probe is not None and (split!='development' or policy_kind!='astra' or first_grasp is not None or approach is not None):
        raise ValueError('An explicit copy-only development failure probe requires Astra development; never research collection/evaluation.')
    if (resume_parent is not None or no_wall_time_limit or isinstance(budget,DevelopmentBudget)) and (policy_kind!='scripted' or split!='development'):
        raise ValueError('Released continuation/no global deadline is API-free development only.')
    if no_wall_time_limit:
        if budget is not None and not isinstance(budget,DevelopmentBudget):raise ValueError('Conflicting continuation budget.')
        budget=budget or DevelopmentBudget();seconds=None
    else:budget=budget or Budget(seconds)
    control=ControlBudget(len(prepared['order']));started=time.monotonic()
    from .validation import check_prepared
    check_prepared(prepared,budget)
    result={'schema':'assembly_result/v2.0','run_id':run_id,'status':'initializing',
        'policy_source':'astra_with_explicit_development_probe' if failure_probe is not None else policy_kind,
        'development_failure_probe':failure_probe is not None,'main_action_policy_source':policy_kind,
        'development_learning':development_learning,'continuation_policy':'bounded_reobserve_replan_no_budget_reset',
        'condition':condition,'mode':mode,'memory':memory,'motion_plan_mode':motion_plan_mode,'split':split,'lineage_id':lineage_id,
        'source_identity':source_identity(prepared),'required':prepared['order'],'installed':[],'api_calls':0,
        'policy_decision_evaluated':False,'reference_status':'not_run','preparation_status':'geometry_ready',
        'manual_product_adjustments':False,'budget':{'control_limit':control.limit,'wall_time_limit_mode':'none_api_free_development' if no_wall_time_limit else 'finite','candidate_per_part':3,'alignment_per_run':1,'replan_per_run':1,'lift_recovery_per_part':1,'seconds':seconds},
        'metrics':counters([]),'error':None,'registrations':[],
        'physics_timestep_s':dt,'validation_regime':'baseline_2ms' if dt==.002 else 'experimental_'+str(round(dt*1000))+'ms',
        'baseline_501_sample_qualification':dt==.002}
    write(folder/'prepared.json',prepared);write(folder/'code_versions.json',inventory(prepared))
    prompts=read(ROOT/'config/policy_prompts.v2.json')
    write(folder/'resolved_config.json',{'prepared_sha256':digest(prepared),'condition':condition,'policy_source':policy_kind,
         'split':split,'lineage_id':lineage_id,'prompts_hash':digest(prompts),'budget':result['budget'],
         'first_grasp':first_grasp,'approach':approach,'development_failure_probe':failure_probe,'motion_plan_mode':motion_plan_mode,'development_learning':development_learning,
         'completion_profile_hash':prepared['completion_profile_hash'],'physics_timestep_s':dt,
         'validation_regime':result['validation_regime']})
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
        if resume_parent is not None:
            from .resume import load_parent,restore_controller
            model,data,checkpoint,resume_context=load_parent(resume_parent,prepared,folder,budget)
            installed.update(checkpoint['installed']);settled.update(checkpoint['settled'])
            result.update(resume_context=resume_context,reference_status='inherited_verified_reference',
                          continuation_policy='verified_released_boundary_api_free',fresh_empty_base_episode=False)
            record=Recorder(folder,model,data,prepared);record.deadline=budget.deadline
            if record.model_hash!=checkpoint['model_sha256']:raise ValueError('Restored model serialization differs.')
            record.part_id=checkpoint['part_id']
            skills=MeshSkills(model,data,prepared,record.part_id,installed,settled,budget,recorder=record)
            restore_controller(skills,checkpoint);prealignment=skills.prealignment
            record.sample(force=True)
            boundary=observe_window(skills,sorted(installed),check_id=run_id+':restored_boundary',
                source='live_simulation',model_hash=record.model_hash,prealignment=prealignment)
            write(folder/'restored_boundary_verification.json',boundary)
            if not valid_evidence(boundary,prepared['completion_profile_hash'],required_parts=installed):
                raise ValueError('Restored installed parts failed fresh continuous observation; no assembly motion started.')
            resume_context['physical_reobservation']='passed';write(folder/'resume_context.json',resume_context)
            images=record.goal_images();result['preparation_status']='restored_boundary_reverified'
        else:
            reference=check_reference(prepared,folder,budget,check_id=run_id+':reference')
            result['reference_status']=reference['status']
            if not valid_evidence(reference,prepared['completion_profile_hash'],source='reference_scene',required_parts=prepared['order']):
                failure=reference.get('execution_failure',{})
                result.update(status='reference_invalid',preparation_status='reference_failed',
                    stopped_phase='reference_goal_stability',
                    error={'code':failure.get('code','REFERENCE_NOT_VERIFIED'),
                        'message':failure.get('message') or reference.get('incomplete_reason') or 'Reference completion checks failed.',
                        'simulation_time_s':reference.get('stopped_at_simulation_time_s',reference.get('end_time_s')),
                        'diagnostics':'reference_diagnostics.json'},
                    initial_supply_status='not_run',robot_execution_status='not_run')
                return result
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
        queue=[pid for pid in prepared['order'] if pid not in installed]
        deferred_at={}
        advisory=bool(prepared.get('input_hint'))
        while queue:
            eligible=[pid for pid in queue if deferred_at.get(pid,-1)<len(installed)]
            if not eligible:
                result['error']='All remaining suggestions were deferred at the current assembly state.'
                break
            active=eligible[0]
            queue.remove(active)
            deferred=False
            moved_in_main=False
            # A new part wrapper must not erase the previous verified empty-hand
            # retreat. The next actual pick resets it; copied checks never do.
            released_boundary=bool(skills.retreat_complete and skills.motion.held_pose is None and skills.motion.gripper_command<1.)
            record.part_id=active;skills=MeshSkills(model,data,prepared,active,installed,settled,budget,recorder=record)
            skills.retreat_complete=released_boundary
            skills.prealignment=prealignment
            lift_retries=0;release_retries=0;attempts=0;pending=None;last_grasp=None;seen=set();part_done=False;main_changed_failure=False
            def turn(_state):
                nonlocal attempts,pending,revision,part_done,alignments,replans,last_grasp,main_changed_failure,probe_used
                nonlocal deferred,moved_in_main,lift_retries,release_retries
                budget.check('graph_turn');control.begin();revision+=1
                current=body_pose(data,body_name(active,prepared['base_id']))
                measured=skills.motion.sample(allow_support=True,allow_grasp=skills.motion.held_pose is not None or bool(skills.release_context and not skills.release_context['opening_complete']))
                allowed=['stop']
                if skills.motion.held_pose is not None:
                    if pending and pending['phase'].startswith('pick_lift') and pending['failure_code'] in ('IK_NOT_CONVERGED','PLANNED_CONTACT','MOTION_PATH_NOT_FOUND'):allowed=['lift','stop'] if lift_retries<1 else ['stop']
                    elif pending and pending['failure_code']=='ALIGNMENT_OUT_OF_TOLERANCE' and alignments<1:allowed=['recover_alignment','stop']
                    elif pending and replans<1:allowed=['replan_insert','stop']
                    elif not pending:allowed=['insert','stop']
                elif skills.release_context is not None:
                    allowed=['finish_release','stop'] if release_retries<1 else ['stop']
                else:
                    stable=measured['part_speed_m_s']<=.001 and measured['part_angular_speed_rad_s']<=.01
                    open_safe=measured['pad_gap_mm']>=80 and not measured['left_contact'] and not measured['right_contact']
                    if attempts<3 and (not main_changed_failure or stable and open_safe):allowed=['pick','stop']
                    if advisory and len(eligible)>1 and not moved_in_main and stable and open_safe:
                        allowed.insert(-1,'defer_part')
                if result.get('hard_execution_violation'):allowed=['stop']
                from .grasp_screen import GoalToolScreen
                screen=GoalToolScreen(skills) if 'pick' in allowed else None
                pool=candidates(prepared['parts'][active],current,revision,budget,prepared['profile'],
                    tcp_rotation=read_tcp_pose(data)[1],goal_screen=screen) if 'pick' in allowed else {'presented':[],'raw_generated':0}
                append(folder/'candidates.jsonl',{'part_id':active,'revision':revision,**pool})
                role='single' if mode=='single' else 'recovery' if pending else 'assembly'
                cards=[]
                if memory and pending:
                    query=scope_for(pending,last_grasp,pending.get('attempted_action',all_events[-1]['proposal']['skill'] if all_events else allowed[0]),prepared['completion_profile_hash'])
                    snap=pending.get('failure_snapshot',{});image=folder/snap['png'] if snap.get('png') else None
                    cards=library.retrieve(query,image=image,refs={'run_id':run_id,'part_id':active,'revision':revision,
                        'failure_check_id':snap.get('check_id')},log=folder/'retrieval.jsonl')
                goal=np.asarray(prepared['parts'][active]['target'])
                from .packets import candidate_packet
                compact=[candidate_packet(c) for c in pool['presented']]
                from .packets import tool_configuration_packet
                context={'part_id':active,'target_id':'cad_goal:'+active,'observation_revision':revision,'active_role':role,
                    'allowed_skills':allowed,'candidate_attempts':attempts,'grasp_candidates':compact,
                    'current_pose':{'position_mm':(current[:3,3]*1000).tolist(),'quaternion_xyzw':xyzw(current)},
                    'goal_pose':{'position_mm':(goal[:3,3]*1000).tolist(),'quaternion_xyzw':xyzw(goal)},
                    'measured':json_safe(measured),'holding_part':active if skills.motion.held_pose is not None else None,
                    'verified_installed_parts':sorted(installed),'failure':pending,'experiences':cards,
                    'workcell':{'tabletop_z_m':prepared['profile']['tabletop_z_m'],
                        'supply_fixture':next((f for f in prepared.get('layout',{}).get('supply_fixtures',[]) if f['part_id']==active),None)},
                    'tool_configuration':tool_configuration_packet(prepared['profile']),
                    'operation':prepared['parts'][active]['operation'],'motion_plan_contract':motion_plan_contract(motion_plan_mode,skills.motion_plan),'remaining':{'release_recovery':1-release_retries,'wall_seconds':budget.seconds_for_report(),'lift_recovery':1-lift_retries,'candidates':3-attempts,'alignment':1-alignments,'replan':1-replans,'control':control.limit-control.used}}
                if advisory:
                    context['order_hint']={'role':'suggestion','next_alternatives':eligible[1:],
                        'defer_part':'With open empty fingers, defer this part and consider the next suggestion. It can return after another installation.'}
                before=skills.snapshot();is_probe=failure_probe is not None and not probe_used
                if is_probe:
                    probe_used=True
                    proposal={'skill':'pick','part_id':active,'target_id':context['target_id'],'observation_revision':revision,
                        'reason':'Explicit development-only copy failure probe, not an Astra decision. Main execution is forbidden for this probe.',
                        'grasp':deepcopy(failure_probe)}
                else:
                    proposal=policy.propose(context);result['policy_decision_evaluated']=policy_kind=='astra'
                    persist()  # Show settled model usage before a long physical action.
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
                    previous_motion_plan=deepcopy(skills.motion_plan);previous_plan_source=skills.motion_plan_source
                    changed_plan=False
                    if action in ('pick','lift','insert','replan_insert'):
                        changed_plan=skills.configure_motion_plan(proposal.get('motion_plan'),reset=action=='pick',mode=motion_plan_mode,source=policy_kind)
                        event.update(resolved_motion_plan=deepcopy(skills.motion_plan),motion_plan_changed=changed_plan,motion_plan_source=skills.motion_plan_source)
                    if action=='stop':result['error']=proposal['reason'];done=True
                    elif action=='defer_part':
                        deferred_at[active]=len(installed)
                        queue.append(active)
                        deferred=True;done=True
                        response={'status':'part_deferred','part_id':active,'next_suggestion':eligible[1]}
                    elif action=='pick':
                        grasp=resolve(proposal['grasp'],pool,prepared['parts'][active],current,revision,policy_kind)
                        if is_probe:grasp['source']='explicit_development_failure_probe'
                        if policy_kind=='scripted' and policy.last_provenance=='development_injection':grasp['source']='development_injection'
                        if policy_kind=='scripted' and policy.last_provenance=='scripted_geometry_pose_alternative':grasp['source']='scripted_geometry_pose_alternative'
                        if policy_kind=='scripted' and policy.last_provenance=='development_guidance_revalidation':
                            grasp.update(source='development_guidance_revalidation',guidance_id=policy.last_guidance)
                        token=digest([grasp['tcp_in_part'],grasp['opening_width_mm'],skills.motion_plan,before])
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
                    elif action=='lift':
                        lift_retries+=1
                        response=skills.lift_part();pending=None;main_changed_failure=False
                    elif action in ('insert','replan_insert'):
                        if action=='replan_insert':replans+=1
                        if changed_plan:
                            record.check_id=action_id+':changed_plan_copy'
                            checked=skills.preflight_install(record.check_id);event['preflight']=checked
                            if not checked['accepted']:
                                event['failure']=checked['failure'];pending=checked['failure']
                                skills.motion_plan=previous_motion_plan;skills.motion_plan_source=previous_plan_source
                                response={'status':'changed_plan_preflight_rejected','preflight':checked,'rejected_plan_not_committed':True}
                                return {'done':False}
                            record.check_id=None;record.last_failure=None
                        verification=skills.insert(action_id+':completion',replan=action=='replan_insert' or changed_plan)
                        installed.add(active);part_done=True;done=True;pending=None
                        event.update(installation_verified=True,verification=verification,resolved_grasp=last_grasp)
                        event['released_checkpoint']=skills.checkpoint('verified_released_boundary')
                        response={'status':'installation_verified','verification':verification}
                    elif action=='finish_release':
                        release_retries+=1
                        verification=skills.finish_release(action_id+':completion')
                        installed.add(active);part_done=True;done=True;pending=None
                        event.update(installation_verified=True,verification=verification,resolved_grasp=last_grasp)
                        event['released_checkpoint']=skills.checkpoint('verified_released_boundary')
                        response={'status':'installation_verified','verification':verification}
                    elif action=='recover_alignment':
                        alignments+=1;response=skills.recover_alignment();pending=None
                except Exception as exc:
                    pending=skills.failure(exc,skills);event['failure']=pending;response={'status':'failed','failure':pending}
                    try:event['stopped_checkpoint']=skills.checkpoint('action_stopped')
                    except Exception as storage:result['checkpoint_error']=type(storage).__name__+': '+str(storage)[:300]
                    main_changed_failure=float(data.time)>start_time
                    if isinstance(exc,TimeoutError):result['cancelled' if str(exc).startswith('cancelled:') else 'deadline_reached']=True
                    if not isinstance(exc,FailureEvent):done=True
                finally:
                    event['continuation_events']=deepcopy(skills.continuation_events)
                    event['executed']=float(data.time)>start_time
                    moved_in_main=moved_in_main or event['executed']
                    if skills.verifier.hard_violation:
                        result['hard_execution_violation']=skills.verifier.hard_violation
                    if event['failure']:
                        # A copied physical check can return a saved timeout rather than raise it.
                        # Terminate this episode without requesting another model decision.
                        if event['failure'].get('failure_code')=='TimeoutError':
                            reason=event['failure'].get('message','deadline:preflight')
                            result['cancelled' if reason.startswith('cancelled:') else 'deadline_reached']=True
                            result['error']='TimeoutError: '+reason;done=True
                        event['failure'].update(attempted_action=action,executed_in_main=event['executed'],parent_action_id=action_id,observation_revision=revision)
                        event['scope']=scope_for(event['failure'],last_grasp,action,prepared['completion_profile_hash'])
                    response['executed']=event['executed'];response['source']='live_simulation' if event['executed'] else event['failure']['source'] if event['failure'] else 'host_no_motion'
                    event['source']=response['source']
                    event['tool_result']=json_safe(response);event['simulation_end_s']=float(data.time)
                    all_events.append(json_safe(event));append(folder/'events.jsonl',all_events[-1])
                    if is_probe and history:
                        history.add({'role':'user','content':__import__('json').dumps({'explicit_development_probe':True,
                            'not_an_Astra_decision':True,'proposal':proposal,'result':all_events[-1]['tool_result']},
                            ensure_ascii=False,separators=(',',':'))})
                    if history and history.pending:history.complete(tool_result_packet(all_events[-1]['tool_result'],event['id']))
                    persist()
                return {'done':done}
            graph=StateGraph(Turn);graph.add_node('decision_execute_verify',turn);graph.add_edge(START,'decision_execute_verify')
            graph.add_conditional_edges('decision_execute_verify',lambda s:END if s['done'] else 'decision_execute_verify')
            with tracing_context(enabled=False):graph.compile().invoke({'done':False},{'recursion_limit':control.limit+2})
            if not part_done and not deferred:break
        # Every installed part is measured in this one final window, also for
        # a locally solved incident in a later partial run.
        if installed and skills.motion.held_pose is None and skills.retreat_complete:
            final=observe_window(skills,sorted(installed),check_id=run_id+':final',source='live_simulation',
                   model_hash=record.model_hash,prealignment=prealignment)
            write(folder/'final_verification.json',final)
        success=not result.get('hard_execution_violation') and installed==set(prepared['order']) and valid_evidence(final,prepared['completion_profile_hash'],required_parts=prepared['order'])
        result['status']='physical_success' if success else 'cancelled' if result.get('cancelled') else 'deadline' if result.get('deadline_reached') else 'partial' if installed else 'failed'
    except Exception as exc:
        import traceback
        result.update(status=('cancelled' if str(exc).startswith('cancelled:') else 'deadline') if isinstance(exc,TimeoutError) else 'failed',error=type(exc).__name__+': '+str(exc)[:1000])
        write(folder/'exception.json',{'traceback':traceback.format_exc()})
        if history and history.pending:history.complete({'status':'stopped_on_error','executed':False,'error':type(exc).__name__})
    finally:
        from .learning import grounded_development_guidance,retrieval_outcomes
        if skills and record:
            try:result['terminal_checkpoint']=skills.checkpoint('terminal_state')
            except Exception as exc:result['checkpoint_error']=type(exc).__name__+': '+str(exc)[:300]
        try:
            incidents=incidents_from(all_events,run_id,prepared,split,lineage_id,result['policy_source'],final)
            if result.get('hard_execution_violation'):
                for incident in incidents:incident.update(registration='pending',registration_reason='hard_execution_violation_in_episode')
            write(folder/'incidents.json',incidents)
            for incident in incidents:
                if incident['registration']!='eligible' or (policy_kind!='astra' and not development_learning):
                    guidance.append({'incident_id':incident['id'],'status':'pending','reason':'requires_eligible_actual_astra_incident'});continue
                try:
                    card=policy.guidance(incident) if policy_kind=='astra' else grounded_development_guidance(incident)
                    guidance.append({'incident_id':incident['id'],'status':'generated' if validate_guidance(card,incident) else 'pending','card':card,'source':policy_kind if policy_kind=='astra' else 'scripted_evidence_extractor'})
                except Exception as exc:guidance.append({'incident_id':incident['id'],'status':'pending','reason':str(exc)[:400]})
            write(folder/'guidance_candidates.json',guidance)

            verified=set(final.get('part_ids',[])) if valid_evidence(final,prepared['completion_profile_hash']) else set()
            write(folder/'memory_use.json',retrieval_outcomes(all_events,verified))
        except Exception as exc:
            result['experience_error']=type(exc).__name__+': '+str(exc)[:400]
            write(folder/'incidents.json',incidents);write(folder/'guidance_candidates.json',guidance)
        if record:

            try:result['media']=record.close()
            except Exception as exc:result['media']={'status':'media_error','error':str(exc)[:500]}
        persist();result['audit']=audit(folder)
        try:
            result['replay_bundle']=seal_bundle(folder,prepared)
            for row in guidance:
                if row['status']=='generated':
                    incident=next(e for e in incidents if e['id']==row['incident_id'])
                    result['registrations'].append(library.register(incident,row['card'],folder,row.get('source','astra')))
        except Exception as exc:result['archive_error']=str(exc)[:800]
        write(folder/'registrations.json',result['registrations'])
        result['experience_summary']={'incidents':len(incidents),'resolved_locally':sum(i['state']=='solved_local' for i in incidents),
            'guidance_generated':sum(g['status']=='generated' for g in guidance),
            'registered':sum(g['status']=='registered' for g in result['registrations']),
            'source':'actual_astra' if policy_kind=='astra' else 'scripted_development',
            'available_from':'next_run_snapshot'}
        result['next_step']={'status':'complete' if result['status']=='physical_success' else 'stopped_with_evidence',
            'reason':None if result['status']=='physical_success' else result.get('error') or ('episode_budget_exhausted' if result.get('deadline_reached') else 'inspect_failure_summary'),
            'automatic_new_budget':False,'checkpoint_saved':bool(result.get('terminal_checkpoint'))}
        persist()
    return result
