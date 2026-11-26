"""API-free test-policy retries through the existing bounded action contract."""
from copy import deepcopy
from .motion_plan import resolve


def remember_pick(context, grasp, plan):
    if grasp['mode']=='candidate':
        candidate=next((c for c in context['grasp_candidates'] if c['candidate_id']==grasp['candidate_id']),None)
        if candidate is None or not all(k in candidate for k in ('position_mm','quaternion_xyzw','opening_width_mm')):
            return None
        value={k:deepcopy(candidate[k]) for k in ('position_mm','quaternion_xyzw','opening_width_mm')}
        value.update(mode='pose',frame='part_local',parent_candidate_id=None)
    else:
        value=deepcopy(grasp)
        # An earlier revision's candidate ID is not a current candidate ID.
        value['parent_candidate_id']=None
    return {'part_id':context['part_id'],'target_id':context['target_id'],
            'revision':context['observation_revision'],'current_pose':deepcopy(context.get('current_pose')),
            'grasp':value,'motion_plan':resolve(plan)}


def shorter_approach(context, previous):
    """No replay after real motion, no stale-part retry, no range expansion."""
    failure=context.get('failure') or {}
    if (previous is None or context.get('candidate_attempts',0)<1 or
        context.get('holding_part') is not None or
        context.get('motion_plan_contract',{}).get('mode','bounded')!='bounded' or
        failure.get('source')!='mujoco_preflight_copy' or
        failure.get('failure_code')!='IK_NOT_CONVERGED' or failure.get('phase')!='approach_pregrasp'):
        return None
    if (any(previous[k]!=context[k] for k in ('part_id','target_id')) or
        previous['revision']>=context['observation_revision'] or
        previous['current_pose'] is None or previous['current_pose']!=context.get('current_pose')):
        return None
    distance=previous['motion_plan']['approach_distance_mm']
    if distance<=40:return None
    plan=deepcopy(previous['motion_plan']);plan['approach_distance_mm']=max(40.,distance-20.)
    return deepcopy(previous['grasp']),plan
