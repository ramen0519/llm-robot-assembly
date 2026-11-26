"""Evidence routing, not an LLM explanation or authorization to retry a motion.

Inspired by per-primitive trace diagnosis in ASPIRE and hierarchical failure
localization in REFLECT. Independently implemented; no external code copied.
"""
from copy import deepcopy


def completion_interruption(evidence):
    """Read current or historical incomplete-window causes without changing facts."""
    if not isinstance(evidence,dict) or evidence.get('complete') is not False:
        return None
    if evidence.get('interruption'):
        return deepcopy(evidence['interruption'])
    reason=evidence.get('incomplete_reason','')
    kind,separator,message=reason.partition(': ')
    if separator and kind not in ('FailureEvent',):
        return {'exception_type':kind,'message':message}
    return None


def measured_completion_failure(evidence):
    return bool(evidence and (evidence.get('execution_failure') or evidence.get('violations') or
        any(check.get('status')=='failed' for part in evidence.get('parts',{}).values()
            for check in part.get('checks',{}).values())))


def completion_error(evidence):
    """A deadline remains a deadline, even after a partial observation was saved."""
    from failure_report import FailureCode,FailureEvent
    interrupted=completion_interruption(evidence)
    if interrupted:
        error=(TimeoutError(interrupted['message']) if interrupted['exception_type']=='TimeoutError' else
               RuntimeError('completion_observation_interrupted: '+interrupted['exception_type']+': '+interrupted['message']))
    elif evidence.get('complete') is not True and not measured_completion_failure(evidence):
        error=RuntimeError('completion_observation_incomplete: '+evidence.get('incomplete_reason','missing measurements'))
    else:
        error=FailureEvent(FailureCode.PLACEMENT_NOT_CONFIRMED,'Common completion window failed.',violations=[evidence])
    error.completion_evidence=evidence
    return error


def diagnose_failure(failure):
    """Keep the detector's facts distinct from causes that need another check."""
    code = failure.get('failure_code', 'unknown')
    phase = failure.get('phase', 'unknown')
    source = failure.get('source', 'unknown')
    completion=failure.get('completion_evidence')
    if completion is None:
        completion=next((v for v in failure.get('violations',[]) if isinstance(v,dict) and
                         v.get('schema')=='completion_evidence/v1.2'),None)
    interrupted=completion_interruption(completion)
    measured=measured_completion_failure(completion)
    layer, checks, outcome = 'unclassified', [], 'unknown'
    if code in ('TimeoutError', 'CANCELLED', 'POLICY_BUDGET_EXCEEDED') or (interrupted and interrupted['exception_type']=='TimeoutError'):
        layer = 'resource_or_cancellation'
        checks = ['Inspect last completed primitive and authenticated checkpoint.',
                  'Re-observe before a bounded continuation; do not repeat pick automatically.']
        if measured:outcome='measured_violation'  # Preserve any violation observed before interruption.
    elif completion and completion.get('complete') is False and not measured:
        layer='observation_incomplete'
        checks=['Inspect the saved interruption and incomplete observation window.',
                'A missing full window is neither a physical pass nor measured infeasibility.']
    elif code in ('IK_NOT_CONVERGED', 'MOTION_PATH_NOT_FOUND', 'PLANNED_CONTACT'):
        layer = 'geometric_planning'
        checks = ['Inspect target pose, starting posture, sampled path and rejected geometry.',
                  'Test a changed pose/path with the same limits before physical execution.']
        outcome = 'bounded_plan_not_found'
    elif code == 'UNEXPECTED_CONTACT':
        layer = 'execution_contact'
        checks = ['Inspect the exact detector state and contact pair.',
                  'Distinguish intended mating from finger backing, table or neighbor contact.',
                  'Re-observe the hold before selecting a checked recovery.']
        outcome = 'measured_violation'
    elif code in ('MODEL_OR_CONTROL_INVALID', 'GRIPPER_STATE_INVALID',
                  'SIMULATION_INVALID', 'MOTION_LIMIT_EXCEEDED', 'TCP_NOT_REACHED'):
        layer = 'model_or_control'
        checks = ['Compare commanded and measured aperture/joint/TCP states.',
                  'Check calibration, actuator response and tracking before blaming the grasp proposal.']
        outcome = 'measured_violation'
    elif code in ('GRASP_NOT_CONFIRMED', 'PART_LOST'):
        layer = 'grasp_retention'
        checks = ['Inspect bilateral pad contact, measured aperture and support clearance.',
                  'Compare held transform with the original slip reference.']
        outcome = 'measured_violation'
    elif code in ('ALIGNMENT_OUT_OF_TOLERANCE', 'INSERTION_DEPTH_NOT_REACHED',
                  'INSERTION_CONTACT_LIMIT', 'PLACEMENT_NOT_CONFIRMED', 'NOT_SETTLED'):
        layer = 'mating_or_completion'
        checks = ['Inspect the operation contract and failed completion checks.',
                  'Distinguish pose error, force, support and post-release motion; keep original thresholds.']
        outcome = 'measured_violation'
    elif code in ('POLICY_RESPONSE_INVALID', 'POLICY_REQUEST_FAILED', 'POLICY_USAGE_UNKNOWN'):
        layer = 'policy_interface'
        checks = ['Inspect the response schema or provider event; physical success is unknown.']
    elif code == 'INPUT_OR_PRECONDITION_INVALID':
        layer = 'precondition'
        checks = ['Inspect the unmet precondition and current observation before changing a proposal.']
    else:
        checks = ['Inspect the exception and original trace; the failing subsystem is not established.']
    return {'schema': 'primitive_failure_diagnosis/v1',
            'detector_fact': {'failure_code': code, 'phase': phase, 'source': source},
            'failure_layer': layer, 'physical_outcome_interpretation': outcome,
            'completion_interruption': interrupted, 'measured_completion_violation': measured,
            'root_cause_confirmed': False, 'global_impossibility_proven': False,
            'next_evidence_checks': checks, 'automatic_motion_retry_authorized': False,
            'evidence': {'snapshot': deepcopy(failure.get('failure_snapshot')),
                         'complete_contacts_location': 'parent failure.violations',
                         'completion_location': 'parent failure.completion_evidence'},
            'meaning': 'Deterministic evidence routing only; not an LLM diagnosis, learned skill or success decision.'}
