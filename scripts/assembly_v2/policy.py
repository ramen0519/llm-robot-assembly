"""One model/ledger/history/tool contract, optionally routed between two roles."""
from copy import deepcopy
import json
import time
from assembly_v12.context import History
from assembly_v12.responses import _tool_arguments
from assembly_v12.contracts import digest,read
from failure_report import FailureCode,FailureEvent
from .grasp import GRASP_SCHEMA
from .motion_plan import SCHEMA as MOTION_PLAN_SCHEMA,validate as validate_motion_plan
from .memory import guidance_schema,validate_guidance
from .packets import observation_packet,guidance_packet

def action_schema(context):
    # Stable wire schema permits reuse of an identical request prefix. Current
    # permissions/part/goal/revision are still enforced by validate_action before
    # any skill runs; the full current allowlist is included in each observation.
    props={'skill':{'type':'string','enum':['pick','lift','insert','recover_alignment','replan_insert','defer_part','finish_release','stop']},
      'part_id':{'type':'string'},'target_id':{'type':'string'},'observation_revision':{'type':'integer'},
      'reason':{'type':'string','maxLength':512},'grasp':GRASP_SCHEMA,'motion_plan':MOTION_PLAN_SCHEMA}
    return {'type':'object','properties':props,'required':list(props),'additionalProperties':False}

def validate_action(value,context):
    expected=set(action_schema(context)['properties'])
    legacy=expected-{'motion_plan'}
    if not isinstance(value,dict) or (set(value)!=expected and not ('motion_plan_contract' not in context and set(value)==legacy)):raise ValueError('Unexpected action fields.')
    if value['skill'] not in context['allowed_skills']:raise ValueError('Action is not permitted in the current state.')
    if any(value[k]!=context[k] for k in ('part_id','target_id','observation_revision')):raise ValueError('Stale part/goal/revision.')
    if not isinstance(value['reason'],str) or not 1<=len(value['reason'])<=512:raise ValueError('Invalid short reason.')
    if (value['skill']=='pick')!=(value['grasp'] is not None):raise ValueError('Only pick accepts a grasp.')
    if value['skill'] not in ('pick','lift','insert','replan_insert') and value.get('motion_plan') is not None:raise ValueError('This action cannot change a motion plan.')
    validate_motion_plan(value.get('motion_plan'),mode=context.get('motion_plan_contract',{}).get('mode','bounded'),operation=context.get('operation'))
    return value

class Astra:
    def __init__(self,config,client,ledger,history,prompts,budget):
        self.config,self.client,self.ledger,self.history,self.prompts,self.budget=config,client,ledger,history,prompts,budget
    def call(self,request,role):
        ledger=self.ledger;self.budget.check('api_count');request_hash=digest(request)
        with ledger.agent_turn(role):
            ledger.record('request_prepared',request=request,request_hash=request_hash)
            ledger.begin_count(request_hash);started=time.monotonic()
            try:
                client=self.client.with_options(timeout=self.budget.remaining(self.config.timeout_seconds))
                counted=client.responses.input_tokens.count(**request)
                ledger.record('input_count_completed',input_tokens=counted.input_tokens)
            except Exception as exc:
                body=getattr(exc,'body',None)
                ledger.halted=True;ledger.record('input_count_failed',error_type=type(exc).__name__,
                    http_status=getattr(exc,'status_code',None),provider_code=body.get('code') if isinstance(body,dict) else None,
                    parameter=body.get('param') if isinstance(body,dict) else None)
                raise FailureEvent(FailureCode.POLICY_REQUEST_FAILED,'Input count failed; no generation retried.') from None
            # Recompute the deadline after counting, rather than granting a second stale timeout.
            self.budget.check('api_generation');timeout=self.budget.remaining(self.config.timeout_seconds)
            ledger.begin_generation(counted.input_tokens,request_hash)
            try:
                response=self.client.with_options(timeout=timeout).responses.create(**request,
                    max_output_tokens=self.config.max_output_tokens,store=False,service_tier='default',include=['reasoning.encrypted_content'])
                # The next Responses request needs wire names (e.g. async),
                # not Python aliases (async_). Preserve reasoning/tool pairing.
                body=response.model_dump(mode='json',by_alias=True,exclude_none=True)
            except Exception as exc:
                ledger.halted=True;ledger.record('model_request_failed',error_type=type(exc).__name__,usage_unknown=True)
                raise FailureEvent(FailureCode.POLICY_REQUEST_FAILED,'Generation failed; unknown use remains reserved, no automatic retry.') from None
            ledger.record('model_response_raw',response=body,elapsed_s=time.monotonic()-started)
            try:ledger.settle_usage(body.get('usage'))
            except Exception:
                ledger.halted=True;ledger.record('usage_unresolved',summary=ledger.summary())
                raise FailureEvent(FailureCode.POLICY_USAGE_UNKNOWN,'Actual request usage could not be reconciled.') from None
            return body
    def request(self,name,schema,instructions,inputs):
        return {'model':self.config.model,'instructions':instructions,'input':inputs,
          'tools':[{'type':'function','name':name,'strict':True,'parameters':schema}],
          'tool_choice':{'type':'function','name':name},'parallel_tool_calls':False,'truncation':'disabled',
          'reasoning':{'effort':self.config.reasoning_effort}}
    def propose(self,context):
        if self.history.pending:raise ValueError('Previous tool call not completed.')
        packet=observation_packet(context)
        # Shared history is append-only. An unchanged common tool definition
        # refers to its full earlier message; raw observations remain on disk.
        if 'tool_configuration' in packet:
            tool_hash=digest(packet['tool_configuration'])
            if getattr(self,'_last_tool_hash',None)==tool_hash:
                packet['tool_configuration']={'source':'unchanged_definition_in_shared_history','sha256':tool_hash}
            else:
                packet['tool_configuration']['definition_sha256']=tool_hash
                self._last_tool_hash=tool_hash
        if 'motion_plan_contract' in packet:
            full=packet['motion_plan_contract']
            definition={k:v for k,v in full.items() if k not in ('current','current_sha256')}
            definition_hash=digest(definition)
            if getattr(self,'_last_plan_contract_hash',None)==definition_hash:
                packet['motion_plan_contract']={'source':'unchanged_definition_in_shared_history',
                    'definition_sha256':definition_hash,'mode':full['mode'],
                    'current':full['current'],'current_sha256':full['current_sha256']}
            else:packet['motion_plan_contract']['definition_sha256']=definition_hash
            self._last_plan_contract_hash=definition_hash
        from .packets import shared_candidate_packet
        if not hasattr(self,'_candidate_history_rows'):self._candidate_history_rows={}
        packet=shared_candidate_packet(packet,self._candidate_history_rows)
        self.history.add({'role':'user','content':json.dumps(packet,ensure_ascii=False,allow_nan=False,separators=(',',':'))})
        role=context['active_role']
        req=self.request('propose_assembly_action',action_schema(context),self.prompts['common']+'\n'+self.prompts[role],deepcopy(self.history.items))
        response=self.call(req,role);self.history.accept(response)
        try:return validate_action(_tool_arguments(response,'propose_assembly_action'),context)
        except ValueError as exc:
            if self.history.pending:
                self.history.complete({'status':'proposal_rejected','executed':False,'source':'host_no_motion',
                    'reason':str(exc)[:400],'fresh_observation_unchanged':True})
            else:raise
            if getattr(self,'_format_repair_used',False):raise
            self._format_repair_used=True
            self.ledger.record('proposal_contract_repair',error=str(exc)[:400],automatic_repair_limit=1)
            return self.propose(context)  # Same ledger/history/global deadline; never retry unknown usage.

    def guidance(self,incident):
        req=self.request('propose_guidance',guidance_schema(incident),self.prompts['guidance'],json.dumps(guidance_packet(incident),ensure_ascii=False,allow_nan=False,separators=(',',':')))
        response=self.call(req,'guidance');card=_tool_arguments(response,'propose_guidance');validate_guidance(card,incident);return card

def development_candidate_rank(candidate):
    """Use measured geometry flags before orientation in the API-free test policy.

    Astra is unaffected: it still receives and selects all presented candidates.
    No nominal flag replaces actual IK, motion, grasp or installation validation.
    """
    checks=candidate.get('checks',{})
    source=checks.get('nominal_source_tool',{}).get('status')
    goal=checks.get('nominal_full_tool_closure',{}).get('goal_with_first_contact',{}).get('status')
    blocked=(source in ('no_jointly_clear_sampled_opening','no_clear_sampled_opening') or goal=='blocked_sampled_nominal')
    clear=source==goal=='clear_sampled_nominal'
    hint=candidate.get('motion_hint',{})
    ik=candidate.get('checks',{}).get('nominal_goal_ik',{}).get('status')
    clearance=candidate.get('checks',{}).get('nominal_full_tool_closure',{}).get('goal_with_first_contact',{}).get('worst_clearance_mm',0.)
    span=hint.get('carried_joint_span_rad')
    winding=1 if span is None else 2 if span>3.14159265 else 0
    return (3 if ik=='not_found' else 2 if blocked else 0 if clear else 1,
            round(hint.get('goal_top_approach_deviation_rad',3.14159265),3),
            candidate.get('geometry_pose_adjustment_mm',0.),
            winding,
            -round(min(3.,clearance),2),
            round(hint.get('tcp_orientation_change_rad',3.14159265),3))


def development_choices(pool,approach=None):
    """Include already generated geometric revisions in API-free trials.

    Source approach and physical closure are still unknown for an alternative.
    Nominal closure/goal evidence only ranks a proposal; execution runs the
    unchanged full copy test before any main-scene motion.
    """
    choices=[]
    for candidate in pool:
        if approach is not None and candidate['approach_family']!=approach:continue
        choices.append(candidate)
        for alternative in candidate.get('geometry_pose_alternatives',{}).get('alternatives',[]):
            geometry=alternative.get('geometry',{})
            if geometry.get('goal_clearance_mm',-1)<0:continue
            value={k:deepcopy(alternative[k]) for k in ('mode','frame','position_mm','quaternion_xyzw','opening_width_mm','parent_candidate_id')}
            choices.append({'candidate_id':candidate['candidate_id'],'approach_family':candidate['approach_family'],
                'motion_hint':{**deepcopy(candidate.get('motion_hint',{})),**deepcopy(alternative.get('motion_hint',{}))},
                'checks':{'nominal_source_tool':{'status':'not_run'},'nominal_goal_ik':deepcopy(geometry.get('goal_ik',{})),
                    'nominal_full_tool_closure':{'goal_with_first_contact':{'status':'clear_sampled_nominal','worst_clearance_mm':geometry['goal_clearance_mm']}}},
                'development_pose_proposal':value,'geometry_pose_adjustment_mm':alternative.get('retraction_mm',0.),'proposal_evidence':deepcopy(alternative)})
    choices.sort(key=development_candidate_rank)
    return choices


class Scripted:
    def __init__(self,first_grasp=None,approach=None):
        self.first_grasp=first_grasp;self.approach=approach;self.injection_used=False;self.injected_part=None;self.last_provenance='scripted';self.last_guidance=None;self.last_pick=None
    def propose(self,context):
        permitted=context['allowed_skills'];action=permitted[0];grasp=None;motion_plan=None
        if action=='pick':
            pool=context['grasp_candidates'];attempt=context['candidate_attempts']
            from .scripted_feedback import shorter_approach
            retry=shorter_approach(context,self.last_pick)
            if retry is not None:
                grasp,motion_plan=retry;self.last_provenance='scripted_approach_feedback_revalidation'
            elif attempt==0 and self.first_grasp is not None and not self.injection_used:
                grasp=deepcopy(self.first_grasp);self.injection_used=True;self.injected_part=context['part_id'];self.last_provenance='development_injection'
            elif context.get('experiences') and context['experiences'][0]['guidance']['action']=='pick':
                card=context['experiences'][0];values={p['name']:p['value'] for p in card['guidance']['parameters']}
                grasp={'mode':'pose','frame':'part_local','position_mm':values['position_mm'],
                       'quaternion_xyzw':values['quaternion_xyzw'],'opening_width_mm':values['opening_width_mm'][0],'parent_candidate_id':None}
                self.last_provenance='development_guidance_revalidation';self.last_guidance=card['record_id']
                motion_plan=deepcopy((card['guidance'].get('motion_plan') or {}).get('parameters'))
            else:
                self.last_provenance='scripted_candidate_selection'
                choices=development_choices(pool,self.approach)
                if not choices:action='stop'
                else:
                    failure=context.get('failure') or {}
                    release_contact=(failure.get('source')=='mujoco_preflight_copy' and
                        failure.get('failure_code')=='UNEXPECTED_CONTACT' and failure.get('phase','').split('/')[0]=='release' and
                        context.get('motion_plan_contract',{}).get('mode','bounded')=='bounded')
                    selected=0 if release_contact else min(max(0,attempt-int(context['part_id']==self.injected_part)),len(choices)-1)
                    chosen=choices[selected]
                    if chosen.get('development_pose_proposal'):
                        grasp=deepcopy(chosen['development_pose_proposal']);self.last_provenance='scripted_geometry_pose_alternative'
                    else:grasp={'mode':'candidate','candidate_id':chosen['candidate_id']}
                    if release_contact:
                        from .motion_plan import DEFAULT
                        motion_plan={key:None for key in DEFAULT};motion_plan['release_mode']='limited_open_then_retreat'
                        self.last_provenance='scripted_release_feedback_revalidation'

        proposal=validate_action({'skill':action,'part_id':context['part_id'],'target_id':context['target_id'],
             'observation_revision':context['observation_revision'],'reason':'개발용 규칙 정책. 실제 Astra 판단으로 집계하지 않음.','grasp':grasp,'motion_plan':motion_plan},context)
        if action=='pick':
            from .scripted_feedback import remember_pick
            self.last_pick=remember_pick(context,grasp,motion_plan)
        return proposal
