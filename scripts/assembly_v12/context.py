"""One append-only Responses conversation and one ledger shared by both roles."""
import base64
from copy import deepcopy
import json
from pathlib import Path

from assembly_policy import validate_proposal
from openai_policy import OpenAIAssemblyPolicy, build_request, parse_proposal

from .contracts import append, read, write


def world_pose(position_m, quaternion_xyzw):
    return {'frame':'workcell_world','position_mm':[1000*float(v) for v in position_m],
            'quaternion_xyzw':[float(v) for v in quaternion_xyzw]}


def measured_packet(context):
    from openai_policy import compact_context
    packet = compact_context(context)
    w = context['observation']['workcell']
    current = w['part']; joint = w['assembly_joint']
    from scipy.spatial.transform import Rotation
    packet['current_pose'] = world_pose(current['position_m'],current['quaternion_xyzw'])
    packet['goal_pose'] = world_pose(joint['target_part_center_world_m'],
        Rotation.from_matrix(joint['target_part_rotation_world']).as_quat())
    packet['current_observations']={'contacts':w.get('contacts',[]),'gripper':context.get('measured_gripper'),
                                    'holding_part':context.get('holding_part'),
                                    'verified_installed_parts':context.get('verified_installed_parts',[])}
    packet['goal_assembly_relations']=context.get('goal_assembly_relations',{})
    # Convert nested pose/length fields too, rather than supplying contradictory
    # world-mm and legacy world-m positions in the same model packet.
    def normalized(value):
        if isinstance(value,list): return [normalized(x) for x in value]
        if not isinstance(value,dict): return value
        result={}
        for k,v in value.items():
            if k=='quaternion_wxyz':
                result['quaternion_xyzw']=[v[1],v[2],v[3],v[0]]
            elif k.endswith('_m') and isinstance(v,(list,int,float)):
                import numpy as np
                result[k[:-2]+'_mm']=(np.asarray(v)*1000).tolist()
            else: result[k]=normalized(v)
        return result
    packet=normalized(packet)
    packet['pose_contract'] = {'frame':'workcell_world','length_unit':'mm','rotation':'xyzw',
                              'grasp':'PART-frame TCP x_mm/y_mm/z_mm; jaw azimuth degrees'}
    f=context.get('failure_event')
    packet['failure_event'] = normalized({k:f.get(k) for k in ('failure_code','phase','source','violations','message','check_id')}) if f else None
    packet['remaining_budgets'] = context.get('remaining_budgets')
    return packet


class History:
    def __init__(self, path, initial, images):
        if len(images) != 2:
            raise ValueError('Exactly two goal images required.')
        self.path, self.items, self.pending = Path(path), [], None
        content = [{'type':'input_text','text':json.dumps(initial,ensure_ascii=False,allow_nan=False)}]
        for path in images:
            content.append({'type':'input_image','image_url':'data:image/png;base64,'+base64.b64encode(Path(path).read_bytes()).decode()})
        self.add({'role':'user','content':content})

    def add(self, item):
        self.items.append(deepcopy(item))
        append(self.path,item)

    def accept(self, response):
        if self.pending is not None:
            raise ValueError('Previous tool call has no result.')
        for item in response['output']:
            self.add(item)
        calls = [i for i in response['output'] if i['type']=='function_call']
        if len(calls)!=1 or not calls[0].get('call_id'):
            raise ValueError('Exactly one identified tool call required.')
        self.pending = calls[0]['call_id']

    def complete(self, result):
        if self.pending is None:
            raise ValueError('Cannot attach a result to an unknown tool call.')
        self.add({'type':'function_call_output','call_id':self.pending,
                  'output':json.dumps(result,ensure_ascii=False,allow_nan=False,separators=(',',':'))})
        self.pending = None


class SharedPolicy(OpenAIAssemblyPolicy):
    def __init__(self, config, client, ledger, history, prompts, role):
        super().__init__(config,client,ledger,role=role)
        self.history, self.prompts = history, prompts
        self.replay_encrypted_reasoning = True
        self.deadline=float('inf')

    def propose(self,context):
        if context['active_role']!=self.role:
            raise ValueError('Inactive policy cannot generate an action.')
        with self.ledger.agent_turn(self.role):
            return self._propose(context)

    def _call_model(self,request,prepared_event):
        import time
        remaining=self.deadline-time.monotonic()
        if remaining<2:
            raise TimeoutError('No request fits the run wall-time budget.')
        if hasattr(self.client,'with_options'):
            self.client=self.client.with_options(timeout=min(self.config.timeout_seconds,remaining/2))
        return super()._call_model(request,prepared_event)

    def _propose(self, context):
        if self.history.pending:
            raise ValueError('Unpaired tool call in shared history.')
        request = build_request(self.config,context,role=self.role)
        packet = measured_packet(context)
        self.history.add({'role':'user','content':json.dumps(packet,ensure_ascii=False,allow_nan=False)})
        request['input'] = deepcopy(self.history.items)
        request['instructions'] = self.prompts['common']+'\n'+self.prompts[self.role]
        request['tools'][0]['parameters']['properties']['reason']['maxLength'] = 512
        # Full request token counting is still performed by the existing ledger.
        # No history slicing or automatic summarization on a context failure.
        response = self._call_model(request,'shared_history_request')
        try:
            self.history.accept(response)
            proposal = parse_proposal(response,context)
        except Exception:
            self.ledger.halted=True
            if self.history.pending:
                self.history.complete({'status':'policy_response_invalid','executed':False})
            raise
        self.ledger.record('proposal_validated',response_id=response.get('id'),proposal=proposal)
        return proposal


class Scripted:
    kind = 'scripted'
    def __init__(self, role='single', rejected_first=None):
        self.role, self.rejected_first = role, rejected_first

    def propose(self, context):
        action = context['allowed_skills'][0]
        value = {'skill':action,'part_id':context['part_id'],'target_id':context['target_id'],
                 'observation_revision':context['observation_revision'],
                 'reason':'개발용 규칙 정책: 현재 허용된 동작과 기하 기준값 사용. Astra 판단이 아님.'}
        if context.get('grasp_task') is not None:
            g = context['grasp_task']['geometry']
            previous = context['grasp_task']['previous_trials']
            xyz = [0.,0.,6.]
            if 'tab_center_xyz_mm' in g:
                xyz = [*g['tab_center_xyz_mm'][:2], g['tab_center_xyz_mm'][2]+6.]
            elif 'bore_width_mm' in g:
                xyz[2] = min(g['part_size_xyz_mm'][2]/2-2.,12.)
            if previous:
                xyz[2] = min(g['part_size_xyz_mm'][2]/2-2., max(xyz[2], 12.))
            value['grasp'] = dict(zip(('x_mm','y_mm','z_mm'),xyz),jaw_yaw_deg=0.) if action=='pick' else None
            if self.rejected_first is not None and context.get('first_part',False) and not previous:
                value['grasp'] = deepcopy(self.rejected_first)
                value['reason'] = '명시된 개발용 첫 파지 교란. 모델 제안이 아님.'
        return validate_proposal(value,context)
