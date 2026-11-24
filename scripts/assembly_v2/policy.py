"""One model/ledger/history/tool contract, optionally routed between two roles."""
from copy import deepcopy
import json
import time
from assembly_v12.context import History
from assembly_v12.responses import _tool_arguments
from assembly_v12.contracts import digest,read
from failure_report import FailureCode,FailureEvent
from .grasp import GRASP_SCHEMA
from .memory import guidance_schema,validate_guidance

def action_schema(context):
    props={'skill':{'type':'string','enum':context['allowed_skills']},'part_id':{'type':'string','enum':[context['part_id']]},
      'target_id':{'type':'string','enum':[context['target_id']]},'observation_revision':{'type':'integer','enum':[context['observation_revision']]},
      'reason':{'type':'string','maxLength':512},'grasp':GRASP_SCHEMA}
    return {'type':'object','properties':props,'required':list(props),'additionalProperties':False}

def validate_action(value,context):
    if not isinstance(value,dict) or set(value)!=set(action_schema(context)['properties']):raise ValueError('Unexpected action fields.')
    if value['skill'] not in context['allowed_skills']:raise ValueError('Action is not permitted in the current state.')
    if any(value[k]!=context[k] for k in ('part_id','target_id','observation_revision')):raise ValueError('Stale part/goal/revision.')
    if not isinstance(value['reason'],str) or not 1<=len(value['reason'])<=512:raise ValueError('Invalid short reason.')
    if (value['skill']=='pick')!=(value['grasp'] is not None):raise ValueError('Only pick accepts a grasp.')
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
                ledger.halted=True;ledger.record('input_count_failed',error_type=type(exc).__name__)
                raise FailureEvent(FailureCode.POLICY_REQUEST_FAILED,'Input count failed; no generation retried.') from None
            # Recompute the deadline after counting, rather than granting a second stale timeout.
            self.budget.check('api_generation');timeout=self.budget.remaining(self.config.timeout_seconds)
            ledger.begin_generation(counted.input_tokens,request_hash)
            try:
                response=self.client.with_options(timeout=timeout).responses.create(**request,
                    max_output_tokens=self.config.max_output_tokens,store=False,service_tier='default',include=['reasoning.encrypted_content'])
                body=response.model_dump(mode='json')
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
        self.history.add({'role':'user','content':json.dumps(context,ensure_ascii=False,allow_nan=False)})
        role=context['active_role']
        req=self.request('propose_assembly_action',action_schema(context),self.prompts['common']+'\n'+self.prompts[role],deepcopy(self.history.items))
        response=self.call(req,role);self.history.accept(response)
        return validate_action(_tool_arguments(response,'propose_assembly_action'),context)
    def guidance(self,incident):
        req=self.request('propose_guidance',guidance_schema(),self.prompts['guidance'],json.dumps(incident,ensure_ascii=False,allow_nan=False))
        response=self.call(req,'guidance');card=_tool_arguments(response,'propose_guidance');validate_guidance(card,incident);return card

class Scripted:
    def __init__(self,first_grasp=None,approach=None):
        self.first_grasp=first_grasp;self.approach=approach;self.injection_used=False;self.last_provenance='scripted';self.last_guidance=None
    def propose(self,context):
        permitted=context['allowed_skills'];action=permitted[0];grasp=None
        if action=='pick':
            pool=context['grasp_candidates'];attempt=context['candidate_attempts']
            if attempt==0 and self.first_grasp is not None and not self.injection_used:
                grasp=deepcopy(self.first_grasp);self.injection_used=True;self.last_provenance='development_injection'
            elif context.get('experiences') and context['experiences'][0]['guidance']['action']=='pick':
                card=context['experiences'][0];values={p['name']:p['value'] for p in card['guidance']['parameters']}
                grasp={'mode':'pose','frame':'part_local','position_mm':values['position_mm'],
                       'quaternion_xyzw':values['quaternion_xyzw'],'opening_width_mm':values['opening_width_mm'][0],'parent_candidate_id':None}
                self.last_provenance='development_guidance_revalidation';self.last_guidance=card['record_id']
            else:
                self.last_provenance='scripted_candidate_selection'
                choices=[c for c in pool if c['approach_family']==(self.approach or 'top')]
                if not choices:action='stop'
                else:grasp={'mode':'candidate','candidate_id':choices[min(max(0,attempt-int(self.first_grasp is not None)),len(choices)-1)]['candidate_id']}
        return validate_action({'skill':action,'part_id':context['part_id'],'target_id':context['target_id'],
             'observation_revision':context['observation_revision'],'reason':'개발용 규칙 정책. 실제 Astra 판단으로 집계하지 않음.','grasp':grasp},context)
