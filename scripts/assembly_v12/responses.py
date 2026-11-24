"""Budgeted SDK transport; v1.2 shared history supplies all policy instructions."""
import hashlib
import json
import time

from failure_report import FailureCode, FailureEvent
from .action_contract import validate_proposal

def compact_context(context):
    o = context['observation']
    if o.get('valid') is not True or o.get('source') != 'sim_ground_truth':
        raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Need a valid simulation observation for this policy.')
    w = o['workcell']
    keys = ('task_id', 'part_id', 'target_id', 'active_role', 'observation_revision', 'completed',
            'allowed_skills', 'recovery_budget_remaining', 'successful_recoveries', 'condition_fingerprint',
            'transport_replans', 'transport_replan_budget_remaining', 'transport_replan')
    packet = {key: context.get(key) for key in keys}
    packet['observation'] = {'source': o['source'], 'simulation_time_s': o['simulation_time_s'],
                             'tcp': o['tcp'], 'part': w['part'], 'target': w['target'],
                             'assembly_joint': w.get('assembly_joint'), 'all_parts': w.get('all_parts')}
    # No raw qpos/ctrl dump, model source, private paths or full repeated snapshots.
    packet['failure'] = ({key: context['failure'].get(key) for key in
                          ('failure_code', 'phase', 'simulation_time_s', 'violations')}
                         if context['failure'] else None)
    if 'alignment_observation' in context:
        packet['alignment_observation'] = context['alignment_observation']
    if context.get('grasp_task') is not None:
        packet['grasp_task'] = context['grasp_task']
    packet['failure_history'] = [{key: f.get(key) for key in ('failure_code', 'phase')} for f in context['failures']]
    packet['experiences'] = context.get('experiences', [])
    packet['last_verification'] = context.get('last_verification')
    encoded = json.dumps(packet, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    if len(encoded.encode('utf-8')) > 32768:
        raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Policy packet exceeds 32KiB; no silent truncation.')
    return packet

def build_request(config, context):
    properties = {'skill': {'type': 'string', 'enum': context['allowed_skills']},
                  'part_id': {'type': 'string', 'enum': [context['part_id']]},
                  'target_id': {'type': 'string', 'enum': [context['target_id']]},
                  'observation_revision': {'type': 'integer', 'enum': [context['observation_revision']]},
                  'reason': {'type': 'string', 'maxLength': 512}}
    if context.get('grasp_task') is not None:
        from post_grasp import GRASP_SCHEMA
        properties['grasp'] = {'anyOf': [GRASP_SCHEMA, {'type': 'null'}]}
    request = {'model': config.model,
               'tools': [{'type': 'function', 'name': 'propose_assembly_action',
                          'description': 'Propose one permitted action for host validation.',
                          'strict': True, 'parameters': {'type': 'object', 'properties': properties,
                                                        'required': list(properties), 'additionalProperties': False}}],
               'tool_choice': {'type': 'function', 'name': 'propose_assembly_action'},
               'parallel_tool_calls': False, 'truncation': 'disabled'}
    if config.reasoning_effort:
        request['reasoning'] = {'effort': config.reasoning_effort}
    return request


def _tool_arguments(response, name):
    if response.get('status') != 'completed' or response.get('error') is not None:
        raise ValueError('The model response did not complete.')
    output = response.get('output')
    if not isinstance(output, list):
        raise ValueError('Missing output list.')
    calls = [item for item in output if item.get('type') == 'function_call']
    if any(item.get('type') not in ('function_call', 'reasoning') for item in output) or len(calls) != 1:
        raise ValueError('Need exactly one function proposal; text/refusal/other tools are not executable.')
    call = calls[0]
    if call.get('name') != name or call.get('status') not in (None, 'completed'):
        raise ValueError('Unexpected/incomplete function call.')
    arguments = call.get('arguments')
    if not isinstance(arguments, str) or len(arguments) > 8192:
        raise ValueError('Invalid function arguments.')
    def no_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON field.')
            result[key] = value
        return result
    def invalid_constant(value):
        raise ValueError('Non-finite JSON constant.')
    return json.loads(arguments, object_pairs_hook=no_duplicate_keys, parse_constant=invalid_constant)

def parse_proposal(response, context):
    return validate_proposal(_tool_arguments(response, 'propose_assembly_action'), context)

class ResponsesTransport:
    """Shared accounting only: no independent policy, first-grasp search, or generated skill execution."""
    def __init__(self, config, client, ledger, *, role='single'):
        if role not in ('single', 'assembly', 'recovery'):
            raise ValueError('Unsupported model policy role.')
        self.role = role
        self.config, self.client, self.ledger = config.validate(live=True), client, ledger
    def _call_model(self, request, prepared_event):
        request_hash = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        ledger = self.ledger
        ledger.record(prepared_event, request_hash=request_hash, request=request)
        ledger.begin_count(request_hash)
        started = time.monotonic()
        try:
            counted = self.client.responses.input_tokens.count(**request)
            ledger.record('input_count_completed', input_tokens=counted.input_tokens,
                          request_id=getattr(counted, '_request_id', None))
        except (Exception, KeyboardInterrupt) as exc:
            ledger.halted = True
            ledger.record('input_count_failed', error_type=type(exc).__name__, http_status=getattr(exc, 'status_code', None))
            if isinstance(exc, KeyboardInterrupt):
                raise
            raise FailureEvent(FailureCode.POLICY_REQUEST_FAILED, 'Input count request failed; no generation or motion retried.', phase='policy/input_count') from None
        ledger.begin_generation(counted.input_tokens, request_hash)
        try:
            response = self.client.responses.create(**request, max_output_tokens=self.config.max_output_tokens,
                                                    store=False, service_tier='default',
                                                    **({'include': ['reasoning.encrypted_content']} if getattr(self, 'replay_encrypted_reasoning', False) else {}))
            body = response.model_dump(mode='json', by_alias=True, exclude_none=True)
            if getattr(self, 'replay_encrypted_reasoning', False):
                ledger.record('model_response_raw', response=body)
        except (Exception, KeyboardInterrupt) as exc:
            ledger.halted = True
            ledger.record('model_request_failed', error_type=type(exc).__name__, http_status=getattr(exc, 'status_code', None), usage_unknown=True)
            if isinstance(exc, KeyboardInterrupt):
                raise
            raise FailureEvent(FailureCode.POLICY_REQUEST_FAILED, 'Model request failed; generation usage is unknown and reserved. No automatic retry.', phase='policy/request') from None
        # Account for refusals, malformed actions and incomplete generations too.
        ledger.record('model_response_received', response_id=body.get('id'), model=body.get('model'),
                      request_id=getattr(response, '_request_id', None), response_status=body.get('status'),
                      usage=body.get('usage'), elapsed_s=time.monotonic() - started)
        try:
            ledger.settle_usage(body.get('usage'))
        except (ValueError, TypeError, AttributeError):
            ledger.halted = True
            ledger.record('usage_unresolved', summary=ledger.summary())
            raise FailureEvent(FailureCode.POLICY_USAGE_UNKNOWN, 'Missing, unsupported or inconsistent usage; stop for reconciliation.', phase='policy/usage') from None
        return body
