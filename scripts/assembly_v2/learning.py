"""Evidence-only development cards and explicit retrieval outcomes.

The deterministic writer is an opt-in offline wiring check. Production research
still requires actual Astra guidance and actual preserved installation evidence.
"""
from .memory import validate_guidance


def grounded_development_guidance(incident):
    if incident.get('split')!='development' or incident.get('registration')!='eligible':
        raise ValueError('Only an eligible measured development incident can create a scripted card.')
    failures=[e for e in incident['events'] if e.get('failure')]
    successful=[e for e in incident['events'] if e.get('executed') and not e.get('failure')
        and e['proposal']['skill'] in ('pick','lift','insert','replan_insert','recover_alignment','finish_release')]
    # Prefer a concrete grasp example, otherwise the actual successful operation.
    event=next((e for e in successful if e['proposal']['skill']=='pick' and e.get('resolved_grasp')),successful[-1] if successful else None)
    if not failures or event is None:raise ValueError('Missing observed failure or successful action.')
    params=[]
    if event['proposal']['skill']=='pick':
        for key in ('position_mm','quaternion_xyzw','opening_width_mm'):
            value=event['resolved_grasp'][key]
            params.append({'name':key,'unit':'xyzw' if key=='quaternion_xyzw' else 'mm',
                           'value':value if isinstance(value,list) else [value],'event_id':event['id']})
    card={'summary':'개발용 자동 기록: '+failures[0]['failure']['failure_code']+
          ' 뒤 아래 실행값을 사용한 행동과 실제 설치 완료가 관측됨. 원인은 확정하지 않았으며 새 장면에서 다시 검사해야 함.',
          'action':event['proposal']['skill'],'parameters':params,
          'evidence_refs':list(dict.fromkeys([failures[0]['id'],event['id'],incident['installation_evidence']])),
          'checks':['current_geometry','grasp_copy','installation_copy','main_completion_window'],
          'motion_plan':{'event_id':event['id'],'parameters':event['resolved_motion_plan']} if event.get('resolved_motion_plan') else None}
    validate_guidance(card,incident);return card


def retrieval_outcomes(events,final_verified_parts):
    """Observed applicability, not a claim that a retrieved card caused success."""
    output=[]
    for event in events:
        for cid in event.get('retrieved',[]):
            checked=event.get('preflight',{})
            status=('revalidation_failed' if event.get('failure') else
                    'executed_and_installation_preserved' if event.get('executed') and event['part_id'] in final_verified_parts else
                    'executed_without_final_installation' if event.get('executed') else 'retrieved_not_executed')
            output.append({'case_id':cid,'event_id':event['id'],'part_id':event['part_id'],
                'status':status,'current_scene_preflight':checked.get('accepted'),
                'policy_source':event['policy_source'],'causal_benefit_proven':False})
    return output
