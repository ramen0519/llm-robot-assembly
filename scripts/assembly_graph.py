"""Serial LangGraph executor with explicit assembly/recovery policy handoff."""
from copy import deepcopy
from typing import TypedDict
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from assembly_policy import ROLE_INSTRUCTIONS, ScriptedPolicy, allowed_skills, validate_proposal
from failure_report import FailureCode, FailureEvent, json_safe
from insertion_recovery import alignment_observation, recover_alignment
from pick_place_graph import _snapshot


class AssemblyState(TypedDict):
    status: str
    observation: dict | None
    observation_revision: int
    active_role: str
    action: dict | None
    completed: list[str]
    last_result: dict | None
    verification: dict | None
    failures: list[dict]
    pending_failure: dict | None
    recoveries: int
    transport_replans: int
    policy_calls: int
    policy_mode: str
    error: str | None
    experiences: list[dict]
    grasp_trials: list[dict]
    completion_source: str | None


def run_assembly_graph(skills, *, failure_handler, mode='roles', policies=None, on_event=None,
                       scenario='nominal', recovery_budget=1, memory_lookup=None,
                       task_id='four_post_frame_first_joint/v1', insert_result_skill='insert_post/v1', condition_fingerprint=None, transport_replan_budget=0, recipe_summaries=None, propose_grasp=False, retry_history=None, finish_on_verified_insert=False):
    from post_grasp import MAX_PROPOSALS
    prior = deepcopy(retry_history)
    if prior is not None and (mode != 'single' or not propose_grasp or scenario != 'nominal'
            or prior.get('status') != 'stopped' or prior.get('error') != 'Grasp proposal budget exhausted before pick.'
            or prior.get('completed') != [] or len(prior.get('grasp_trials', [])) != MAX_PROPOSALS
            or any(t.get('accepted') is not False for t in prior['grasp_trials'])
            or prior.get('policy_calls') != MAX_PROPOSALS):
        raise ValueError('Only one bounded retry of rejected, unexecuted grasp proposals is supported.')
    trial_limit = MAX_PROPOSALS + (len(prior['grasp_trials']) if prior else 0)
    if mode not in ('single', 'roles') or scenario not in ('nominal', 'alignment_offset'):
        raise ValueError('Unknown policy mode/scenario.')
    if type(recovery_budget) is not int or recovery_budget not in (0, 1):
        raise ValueError('This prototype permits at most one recovery.')
    if type(transport_replan_budget) is not int or transport_replan_budget not in (0, 1):
        raise ValueError('At most one policy-selected transport replan is supported.')
    if insert_result_skill not in ('insert_post/v1', 'insert_sleeve/v1', 'seat_cover/v1'):
        raise ValueError('Unknown insertion result contract.')
    keys = ['single'] if mode == 'single' else ['assembly', 'recovery']
    recipe_summaries = deepcopy(recipe_summaries or [])
    if not isinstance(recipe_summaries, list) or len(recipe_summaries) > 3:
        raise ValueError('At most three advisory validated recipe summaries are supported.')
    policies = policies or {key: ScriptedPolicy(key) for key in keys}
    if set(policies) != set(keys):
        raise ValueError('Policy mapping does not match selected mode.')
    initial = {'status': 'running', 'observation': None, 'observation_revision': 0,
               'active_role': 'assembly', 'action': None, 'completed': [], 'last_result': None,
               'verification': None, 'failures': [], 'pending_failure': None, 'recoveries': 0, 'transport_replans': 0,
               'policy_calls': 0, 'policy_mode': mode, 'error': None, 'experiences': [], 'grasp_trials': [], 'completion_source': None}
    fault_injected = False
    if prior:
        initial.update(grasp_trials=prior['grasp_trials'], policy_calls=prior['policy_calls'],
                       observation_revision=prior['observation_revision'])

    def settle(state):
        if prior is None:  # Runner has already restored and re-observed the stopped scene.
            skills.settle()
        return {}

    def observe(state):
        experiences = memory_lookup(state['pending_failure']) if memory_lookup else []
        if not isinstance(experiences, list) or len(experiences) > 3:
            raise ValueError('Memory lookup must return at most three experience summaries.')
        return {'observation': _snapshot(skills), 'observation_revision': state['observation_revision'] + 1, 'experiences': json_safe(experiences),
                'active_role': 'recovery' if state['pending_failure'] else 'assembly'}

    def decide(state):
        role = 'single' if mode == 'single' else state['active_role']
        context = {'task_id': task_id, 'insert_result_skill': insert_result_skill,
                   'condition_fingerprint': condition_fingerprint, 'successful_recoveries': state['recoveries'], 'part_id': skills.config['part']['id'],
                   'target_id': skills.config['target']['id'], 'active_role': state['active_role'],
                   'role_instruction': ROLE_INSTRUCTIONS[role], 'observation': state['observation'],
                   'observation_revision': state['observation_revision'], 'completed': state['completed'],
                   'failure': state['pending_failure'], 'failures': state['failures'],
                   'experiences': state['experiences'], 'recipes': recipe_summaries, 'last_verification': state['verification'],
                   'recovery_budget_remaining': recovery_budget - state['recoveries'],
                   'transport_replans': state['transport_replans'],
                   'transport_replan_budget_remaining': transport_replan_budget - state['transport_replans'],
                   'transport_replan': skills.insert_replan_context() if transport_replan_budget else None,
                   'allowed_skills': allowed_skills(state['completed'], state['pending_failure'], state['recoveries'], recovery_budget,
                       transport_replan_available=skills.insert_replan_available() if transport_replan_budget else False,
                       transport_replans=state['transport_replans'], transport_replan_budget=transport_replan_budget)}
        if 'recover_alignment' in context['allowed_skills']:
            context['alignment_observation'] = alignment_observation(skills)
        if propose_grasp and 'pick' in context['allowed_skills']:
            context['grasp_task'] = {'geometry': skills.grasp_geometry(), 'previous_trials': state['grasp_trials'],
                                     'attempts_remaining': trial_limit - len(state['grasp_trials'])}
        proposal = validate_proposal(policies[role].propose(deepcopy(context)), context)
        return {'action': proposal, 'policy_calls': state['policy_calls'] + 1,
                'last_result': None, 'verification': None}

    def check_grasp(state):
        action = state['action']
        if state['observation'] != _snapshot(skills) or action['observation_revision'] != state['observation_revision']:
            raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Observation changed before grasp preflight.')
        trial = skills.check_grasp(action['grasp'])
        trial['observation_revision'] = state['observation_revision']
        trials = [*state['grasp_trials'], trial]
        if not trial['accepted'] and len(trials) >= trial_limit:
            return {'grasp_trials': trials, 'status': 'stopped', 'error': 'Grasp proposal budget exhausted before pick.'}
        return {'grasp_trials': trials}

    def execute(state):
        nonlocal fault_injected
        a = state['action']
        if a['observation_revision'] != state['observation_revision'] or state['observation'] != _snapshot(skills):
            raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Policy action is stale; observe again.')
        name = a['skill']
        if name == 'pick':
            if propose_grasp:
                trial = state['grasp_trials'][-1]
                if (not trial['accepted'] or trial['grasp'] != a['grasp']
                        or trial['observation_revision'] != state['observation_revision']):
                    raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'No accepted current grasp.')
                result = skills.pick(a['part_id'], grasp=a['grasp'])
            else:
                result = skills.pick(a['part_id'])
        elif name == 'insert':
            offset = [.006, 0.] if scenario == 'alignment_offset' and not fault_injected else None
            fault_injected = True  # Scenario perturbation is explicit, separate from the policy.
            result = skills.insert(a['part_id'], a['target_id'], alignment_offset_m=offset)
        elif name == 'replan_insert':
            result = skills.replan_insert(a['part_id'], a['target_id'])
        elif name == 'recover_alignment':
            pending = state['pending_failure']
            failure = FailureEvent(FailureCode(pending['failure_code']), pending['message'])
            result = recover_alignment(skills, failure)
        elif name == 'stop':
            return {'status': 'stopped', 'error': a['reason']}
        elif name == 'finish':
            # Fresh current-state verification also required at terminal success.
            row = skills.verifier.sample(skills.data, skills.motion.reference, allow_support=True, allow_grasp=False)
            evidence = skills.insertion_evidence([row])
            if state['completed'] != ['pick', 'insert']:
                raise ValueError('Finish requires both verified actions.')
            return {'status': 'success', 'error': None, 'completion_source': 'policy_finish_then_physics_verifier',
                    'verification': {**evidence, 'measurements': row}}
        else:
            raise ValueError('Unsupported executable action.')
        return {'last_result': json_safe(result)}

    def verify(state):
        name, result = state['action']['skill'], state['last_result']
        expected = {'pick': 'pick/v1', 'insert': insert_result_skill, 'replan_insert': insert_result_skill, 'recover_alignment': 'recover_alignment/v1'}[name]
        if not isinstance(result, dict) or result.get('status') != 'success' or result.get('skill') != expected:
            raise ValueError('Skill returned an invalid result; success cannot be self-declared.')
        held = name not in ('insert', 'replan_insert')
        row = skills.verifier.sample(skills.data, skills.motion.reference, allow_support=True, allow_grasp=held)
        if held:
            if skills.motion.held_pose is None or row['supported'] or row['part_bottom_clearance_m'] < .05:
                raise FailureEvent(FailureCode.GRASP_NOT_CONFIRMED, 'Expected a physically held/lifted part.')
            skills.verifier.check_held(skills.data, row, skills.motion.held_pose)
            checks = skills.verifier.verify_alignment(skills.data) if name == 'recover_alignment' else {'held_and_lifted': True}
            evidence = {'checks': checks}
        else:
            evidence = skills.insertion_evidence([row])
        update = {'verification': {'source': 'sim_ground_truth', **evidence, 'measurements': json_safe(row)}}
        if name == 'recover_alignment':
            update.update(pending_failure=None, recoveries=state['recoveries'] + 1, error=None)
        else:
            update['completed'] = [*state['completed'], 'insert' if name == 'replan_insert' else name]
            if name == 'replan_insert':
                update.update(pending_failure=None, error=None)
        if finish_on_verified_insert and name in ('insert', 'replan_insert'):
            if update.get('completed') != ['pick', 'insert']:
                raise ValueError('Completion requires both physically verified actions.')
            # The current-state verifier, never an LLM declaration, is the authority.
            update.update(status='success', error=None, completion_source='physics_verifier')
        return update

    def guarded(name, function):
        def node(state):
            try:
                update = function(state)
            except (Exception, KeyboardInterrupt) as error:
                report = failure_handler(error)
                insertion_action = name == 'execute' and state['action']['skill'] in ('insert', 'replan_insert')
                recoverable = insertion_action and (
                    (isinstance(error, FailureEvent) and error.code == FailureCode.ALIGNMENT_OUT_OF_TOLERANCE
                     and report['failure_code'] == 'ALIGNMENT_OUT_OF_TOLERANCE' and state['recoveries'] < recovery_budget)
                    or (report['failure_code'] == 'PLANNED_CONTACT'
                        and state['transport_replans'] < transport_replan_budget
                        and skills.insert_replan_available(error)))
                update = {'status': 'running' if recoverable else report['status'],
                          'failures': [*state['failures'], report], 'pending_failure': report,
                          'last_result': None, 'verification': None, 'error': str(error)}
            if name == 'execute' and state['action']['skill'] == 'replan_insert':
                update['transport_replans'] = state['transport_replans'] + 1
            if name == 'decide' and 'policy_calls' not in update:
                update['policy_calls'] = state['policy_calls'] + 1  # Failed attempts also count.
            current = {**state, **update}
            if on_event:
                on_event(json_safe({'node': name, 'status': current['status'], 'active_role': current['active_role'],
                                   'observation_revision': current['observation_revision'], 'action': current['action'],
                                   **({'grasp_trial': current['grasp_trials'][-1]}
                                      if name == 'check_grasp' and update.get('grasp_trials') else {}),
                                   'completed': current['completed'], 'completion_source': current['completion_source'],
                                   'recoveries': current['recoveries'],
                                   'transport_replans': current['transport_replans'],
                                   'simulation_time_s': float(skills.data.time), 'failure_count': len(current['failures']),
                                   'experience_ids': [r['record_id'] for r in current['experiences']],
                                   'recipe_ids': [r['recipe_record_id'] for r in recipe_summaries]}))
            return update
        return node

    graph = StateGraph(AssemblyState)
    for name, function in [('settle', settle), ('observe', observe), ('decide', decide), ('execute', execute), ('verify', verify)]:
        graph.add_node(name, guarded(name, function))
    graph.add_node('check_grasp', guarded('check_grasp', check_grasp))
    graph.add_edge(START, 'settle')
    for source, target in [('settle', 'observe'), ('observe', 'decide'), ('verify', 'observe')]:
        graph.add_conditional_edges(source, lambda state, target=target: target if state['status'] == 'running' else END, [target, END])
    graph.add_conditional_edges('decide', lambda s: END if s['status'] != 'running' else (
        'check_grasp' if propose_grasp and s['action']['skill'] == 'pick' else 'execute'), ['check_grasp', 'execute', END])
    graph.add_conditional_edges('check_grasp', lambda s: END if s['status'] != 'running' else (
        'execute' if s['grasp_trials'][-1]['accepted'] else 'observe'), ['execute', 'observe', END])
    graph.add_conditional_edges('execute', lambda state: END if state['status'] != 'running' else (
        'observe' if state['last_result'] is None else 'verify'), ['observe', 'verify', END])
    with tracing_context(enabled=False):
        return json_safe(graph.compile().invoke(initial, {'recursion_limit': 36}))
