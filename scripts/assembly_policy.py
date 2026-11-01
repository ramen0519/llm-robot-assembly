"""Provider-independent decision contract; current bundled policy is scripted, not an LLM."""
from typing import Protocol

from failure_report import FailureCode, FailureEvent


class DecisionPolicy(Protocol):
    kind: str
    def propose(self, context: dict) -> dict: ...


ROLE_INSTRUCTIONS = {
    'assembly': 'Choose the next permitted assembly skill from current observations. Do not invent success or coordinates.',
    'recovery': 'Use the measured failure and fresh observation. Choose only a permitted recovery or stop. Never blindly replay.',
    'single': 'Handle both assembly and measured failure recovery using the same tools, observations and total budget.',
}


def allowed_skills(completed, pending_failure, recoveries, recovery_budget):
    if pending_failure:
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
    if not isinstance(value, dict) or set(value) != fields:
        raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Policy proposal must match the exact action contract.')
    if (value['skill'] not in context['allowed_skills'] or value['part_id'] != context['part_id']
            or value['target_id'] != context['target_id']
            or type(value['observation_revision']) is not int
            or value['observation_revision'] != context['observation_revision']
            or not isinstance(value['reason'], str) or not 1 <= len(value['reason']) <= 512):
        raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Unknown skill/part/target, stale observation, or invalid rationale.')
    return value.copy()


class ScriptedPolicy:
    """Fixed baseline for offline plumbing; multiple instances are not autonomous LLM agents."""
    kind = 'scripted'
    def __init__(self, role='single'):
        if role not in ROLE_INSTRUCTIONS:
            raise ValueError('Unknown policy role.')
        self.role = role

    def propose(self, context):
        if self.role != 'single' and context['active_role'] != self.role:
            raise ValueError('Policy received the wrong role context.')
        return {'skill': context['allowed_skills'][0], 'part_id': context['part_id'],
                'target_id': context['target_id'], 'observation_revision': context['observation_revision'],
                'reason': 'Fixed offline baseline: take the first permitted skill.'}
