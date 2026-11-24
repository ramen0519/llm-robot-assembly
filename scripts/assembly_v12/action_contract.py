"""Current action validation and recovery eligibility; no legacy graph or memory imports."""
from failure_report import FailureCode, FailureEvent, capture_observation

def allowed_skills(completed, pending_failure, recoveries, recovery_budget, *, transport_replan_available=False,
                   transport_replans=0, transport_replan_budget=0):
    if pending_failure:
        if (pending_failure['failure_code'] == 'PLANNED_CONTACT' and completed == ['pick']
                and transport_replan_available and transport_replans < transport_replan_budget):
            return ['replan_insert', 'stop']
        return (['recover_alignment', 'stop']
                if pending_failure['failure_code'] == 'ALIGNMENT_OUT_OF_TOLERANCE' and recoveries < recovery_budget
                else ['stop'])
    if completed == []:
        return ['pick', 'stop']
    if completed == ['pick']:
        return ['insert', 'stop']
    if completed == ['pick', 'insert']:
        return ['finish']
    raise ValueError('Invalid verified action history.')

def validate_proposal(value, context):
    fields = {'skill', 'part_id', 'target_id', 'observation_revision', 'reason'}
    if context.get('grasp_task') is not None:
        fields.add('grasp')
    if not isinstance(value, dict) or set(value) != fields:
        raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Policy proposal must match the exact action contract.')
    if (value['skill'] not in context['allowed_skills'] or value['part_id'] != context['part_id']
            or value['target_id'] != context['target_id']
            or type(value['observation_revision']) is not int
            or value['observation_revision'] != context['observation_revision']
            or not isinstance(value['reason'], str) or not 1 <= len(value['reason']) <= 512):
        raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Unknown skill/part/target, stale observation, or invalid rationale.')
    if 'grasp' in fields:
        from post_grasp import numeric_grasp
        if ((value['skill'] == 'pick' and not numeric_grasp(value['grasp']))
                or (value['skill'] != 'pick' and value['grasp'] is not None)):
            raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Pick needs a numeric grasp; stop needs null.')
    return value.copy()

def repairable_grasp(trial):
    if trial.get('source') != 'mujoco_preflight_copy' or not trial.get('phase'):
        return False
    if trial['phase'] == 'grasp_bounds':
        return trial.get('failure_code') == 'INPUT_OR_PRECONDITION_INVALID'
    return trial.get('failure_code') in {
        'IK_NOT_CONVERGED', 'MOTION_PATH_NOT_FOUND', 'PLANNED_CONTACT', 'UNEXPECTED_CONTACT',
        'MOTION_LIMIT_EXCEEDED', 'TCP_NOT_REACHED', 'GRIPPER_STATE_INVALID', 'GRASP_NOT_CONFIRMED',
        'PART_LOST', 'ALIGNMENT_OUT_OF_TOLERANCE', 'INSERTION_DEPTH_NOT_REACHED',
        'INSERTION_CONTACT_LIMIT', 'PLACEMENT_NOT_CONFIRMED',
    }

def snapshot(skills):
    observation = capture_observation(skills.model, skills.data)
    if not observation["valid"]:
        raise FailureEvent(FailureCode.SIMULATION_INVALID, "Graph observation is invalid.")
    observation["workcell"] = skills.observe()
    return observation
