"""Connect the local verified skill catalogue to a pre-pick rejection in LangGraph."""
from pathlib import Path

from generate_grasp_lesson import _write_json
from grasp_candidate_preflight import checked_trial, compare_trials, source_state
from grasp_preflight_trace import run_recorded_preflight
from grasp_skill_runtime import bind_rejection, capture_observation, evaluate_candidate, revision_geometry
from grasp_skill_selector import select_skill
from run_grasp_skill_reuse import DEVELOPMENT_GRASP, build_reuse_query


class DevelopmentFirstGrasp:
    """Declare one reproducible input for the interference scenario; delegate all subsequent decisions."""
    def __init__(self, policy):
        self.policy = policy
        self.used = False

    def propose(self, context):
        if (not self.used and context['part_id'] == 'post_1' and context['completed'] == []
                and 'pick' in context['allowed_skills'] and context.get('grasp_task', {}).get('previous_trials') == []):
            self.used = True
            return {'skill': 'pick', 'part_id': context['part_id'], 'target_id': context['target_id'],
                    'observation_revision': context['observation_revision'], 'grasp': dict(DEVELOPMENT_GRASP),
                    'reason': 'Declared first-post interference test input; contact is checked by MuJoCo.'}
        return self.policy.propose(context)


class GraphGraspRecovery:
    """Records checks and produces a candidate. The graph owns every original-scene robot command."""
    def __init__(self, library, config, client, ledger, log_dir, episode_id):
        self.library, self.config, self.client, self.ledger = library, config, client, ledger
        self.snapshot_id = library.snapshot_id
        self.folder = Path(log_dir)
        self.folder.mkdir(parents=True, exist_ok=False)
        self.episode_id = episode_id
        self.rejection = None
        self.expected = None
        self.check_count = 0

    def observation(self, skills, state):
        return capture_observation(skills, episode_id=self.episode_id, revision=state['observation_revision'])

    def check(self, skills, state, action):
        obs = self.observation(skills, state)
        selected = self.expected is not None
        if selected and self.expected != (obs, action['grasp']):
            raise ValueError('Scene or proposed grasp changed after skill selection.')
        self.check_count += 1
        folder = self.folder / ('trial_%02d' % self.check_count)
        folder.mkdir()
        _write_json(folder / 'observation.json', obs)
        before = source_state(skills)
        _write_json(folder / 'source_before.json', before)
        try:
            trial = run_recorded_preflight(skills, action['grasp'], observation_revision=obs['revision'],
                                           log_dir=folder / 'preflight')
            checked_trial(trial, action['grasp'], obs)
            if trial['trace_summary'].get('trace_complete') is not True:
                raise ValueError('Preflight trace incomplete.')
        finally:
            after = source_state(skills)
            _write_json(folder / 'source_after.json', after)
            if before != after:
                raise ValueError('Grasp preflight modified the original simulation.')
        _write_json(folder / 'trial.json', trial)
        if selected:
            _write_json(self.folder / 'comparison.json', compare_trials(self.rejection['trial'], trial))
        self.expected = None
        self.rejection = None if trial['accepted'] else bind_rejection(obs, trial)
        if self.rejection is not None:
            _write_json(folder / 'rejection.json', self.rejection)
        return trial

    def eligible(self, skills, state):
        if self.rejection is None:
            return False
        trial = self.rejection['trial']
        facts = revision_geometry(trial)
        return (skills.config['part']['id'] == 'post_1' and not skills.installed
                and skills.motion.held_pose is None and state['grasp_trials'][-1] == trial
                and trial['failure_code'] == 'PLANNED_CONTACT' and trial['phase'] == 'insert_lower/preflight'
                and facts['pad_contacts_socket_wall'] and facts['pick_completed_in_copy'])

    def recover(self, skills, state):
        if not self.eligible(skills, state):
            raise ValueError('No bound first-post insertion contact to recover.')
        def reader():
            return self.observation(skills, state)
        current = reader()
        before = source_state(skills)
        context = current['condition_context']['config']
        part = context['part']['id']
        query = build_reuse_query(f'Install {part} in {context["target"]["id"]}.', current, self.rejection,
                                  part_kind=skills.registry[part]['kind'])
        _write_json(self.folder / 'selection_query.json', query)
        decision = select_skill(query, self.library, config=self.config, client=self.client,
                                shared_ledger=self.ledger, log_dir=self.folder / 'selector')
        if (self.library.snapshot_id != self.snapshot_id or decision.get('snapshot_id') != self.snapshot_id
                or reader() != current or source_state(skills) != before):
            raise ValueError('Scene or library changed during search.')
        receipt = {'library_snapshot_id': self.snapshot_id, 'episode_id': self.episode_id,
                   'observation_id': current['observation_id'], 'observation_revision': current['revision'],
                   'rejection_id': self.rejection['rejection_id'], 'failure_phase': query['failure_phase'],
                   'selected_id': decision['selected_id'], 'selection_status': decision['status'],
                   'API_calls': decision['API_calls'], 'tools': [event['tool'] for event in decision['events']]}
        _write_json(self.folder / 'selection_receipt.json', receipt)
        if decision['status'] == 'abstained':
            return {'status': 'abstained', 'reason': decision['reason'][:512], 'receipt': receipt}
        if decision['status'] != 'selected':
            raise ValueError('Skill search failed: ' + str(decision.get('error', decision.get('reason'))))
        parent, revision = self.library.resolve(decision['selected_id'], current, self.rejection)
        evaluated = evaluate_candidate(parent, current, current_reader=reader, rejection=self.rejection,
                                       remaining_proposals=1, revision=revision)
        _write_json(self.folder / 'evaluation.json', evaluated)
        if reader() != current or source_state(skills) != before:
            raise ValueError('Original scene changed during skill evaluation.')
        receipt.update(revision_id=revision['revision_id'], code_sha256=revision['code_sha256'],
                       evaluation_status=evaluated['status'])
        if evaluated['status'] != 'proposed':
            _write_json(self.folder / 'decision_receipt.json', receipt)
            return {'status': 'abstained', 'reason': str(evaluated['reason'])[:512], 'receipt': receipt}
        proposal = evaluated['proposal']
        binding = {'kind': 'post_grasp_proposal', 'candidate_id': revision['revision_id'],
                   'code_sha256': revision['code_sha256'], 'part_id': part, 'target_id': context['target']['id'],
                   'episode_id': self.episode_id, 'observation_id': current['observation_id'],
                   'observation_revision': current['revision'], 'rejection_id': self.rejection['rejection_id'],
                   'requires_current_scene_preflight': True, 'execution_authorized': False}
        if any(type(proposal.get(k)) is not type(v) or proposal[k] != v for k, v in binding.items()):
            raise ValueError('Skill output is not bound to the current rejection and simulation state.')
        self.expected = (current, proposal['grasp'])
        receipt['proposed_grasp'] = proposal['grasp']
        _write_json(self.folder / 'decision_receipt.json', receipt)
        return {'status': 'proposed', 'grasp': proposal['grasp'], 'reason': decision['reason'][:512],
                'receipt': receipt}
