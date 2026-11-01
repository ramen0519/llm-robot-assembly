"""Bounded skill compositions extracted from experience and replayed before registration."""
import argparse
import hashlib
import json
from pathlib import Path
from assembly_policy import ScriptedPolicy
from experience_memory import digest, read_json, validate_record
from failure_report import write_json
from workcell_scene import ROOT

DEFAULT_RECIPES = ROOT / 'runtime/skills/simulation'
def implementation_fingerprint():
    # Fail closed after any execution-code, model asset, or dependency-lock change.
    # The deliberately conservative scope is suitable for this first prototype.
    paths = [*sorted((ROOT / 'scripts').glob('*.py')), *sorted((ROOT / 'models').rglob('*')),
             *sorted((ROOT / 'scenes').glob('*.xml')), *sorted((ROOT / 'third_party').rglob('*.py')), ROOT / 'uv.lock']
    return digest({str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in paths if p.is_file()})


def create_candidate(experience):
    validate_record(experience)
    sequence = experience.get('verified_skill_results', [])
    if (experience.get('outcome') != 'success'
            or experience.get('task_id') != 'four_post_frame_first_joint/v1'
            or 'ALIGNMENT_OUT_OF_TOLERANCE' not in experience.get('failure_codes', [])
            or 'recover_alignment/v1' not in experience.get('successful_recovery_skills', [])
            or sequence[-2:] != ['recover_alignment/v1', 'insert_post/v1']):
        raise ValueError('Need a recorded successful realignment followed by verified insertion.')
    payload = {'schema_version': 1, 'status': 'candidate', 'environment': 'simulation',
               'task_id': 'four_post_frame_first_joint/v1', 'condition_fingerprint': experience['condition_fingerprint'],
               'trigger_failure': 'ALIGNMENT_OUT_OF_TOLERANCE', 'steps': ['recover_alignment', 'insert'],
               'source_experience_id': experience['record_id'], 'generation_method': 'fixed_template_from_recorded_sequence',
               'contains_generated_python': False}
    return {'candidate_id': digest(payload), **payload}


def validate_candidate(candidate):
    fields = {'candidate_id', 'schema_version', 'status', 'environment', 'task_id', 'condition_fingerprint',
              'trigger_failure', 'steps', 'source_experience_id', 'generation_method', 'contains_generated_python'}
    if (not isinstance(candidate, dict) or set(candidate) != fields
            or candidate['candidate_id'] != digest({k: v for k, v in candidate.items() if k != 'candidate_id'})
            or candidate['schema_version'] != 1 or candidate['status'] != 'candidate'
            or candidate['environment'] != 'simulation' or candidate['task_id'] != 'four_post_frame_first_joint/v1'
            or candidate['trigger_failure'] != 'ALIGNMENT_OUT_OF_TOLERANCE'
            or candidate['steps'] != ['recover_alignment', 'insert']
            or candidate['generation_method'] != 'fixed_template_from_recorded_sequence'
            or candidate['contains_generated_python'] is not False):
        raise ValueError('Unsupported, modified, or executable-code recipe.')
    for field in ('condition_fingerprint', 'source_experience_id'):
        if not isinstance(candidate[field], str) or len(candidate[field]) != 64 or any(c not in '0123456789abcdef' for c in candidate[field]):
            raise ValueError('Invalid recipe provenance/fingerprint.')


class RecipeReplayPolicy(ScriptedPolicy):
    """Validation harness for a candidate; never bypasses allowed actions or observations."""
    kind = 'recipe_replay'
    def __init__(self, candidate, role='single'):
        super().__init__(role)
        validate_candidate(candidate)
        self.candidate = json.loads(json.dumps(candidate))

    def propose(self, context):
        if context.get('condition_fingerprint') != self.candidate['condition_fingerprint']:
            raise ValueError('Recipe is not compatible with current physical task conditions.')
        decision = super().propose(context)
        if context['failure']:
            if context['failure']['failure_code'] != self.candidate['trigger_failure']:
                decision['skill'] = 'stop'
                decision['reason'] = 'Recipe does not cover this observed failure.'
                return decision
            selected = self.candidate['steps'][0]
        elif context.get('successful_recoveries') == 1 and context['completed'] == ['pick']:
            selected = self.candidate['steps'][1]
        else:
            return decision
        if selected not in context['allowed_skills']:
            decision['skill'] = 'stop'
            decision['reason'] = 'Recipe step is not permitted under current observations/budget.'
        else:
            decision['skill'] = selected
            decision['reason'] = 'Replay recipe ' + self.candidate['candidate_id'] + ': ' + selected
        return decision


def validate_and_register(candidate, *, registry_dir=DEFAULT_RECIPES, output_root=None):
    validate_candidate(candidate)
    from run_assembly_graph import execute_trial
    implementation_before = implementation_fingerprint()
    # A new physical episode must actually consume the candidate policy.
    result = execute_trial(mode='single', scenario='alignment_offset', recovery_budget=1,
                           recipe=candidate, output_root=output_root or ROOT / 'runs/step22_recipes')
    if (result['status'] != 'success' or result.get('recipe_candidate_id') != candidate['candidate_id']
            or result['graph_state']['recoveries'] != 1
            or [r['skill'] for r in result['results']][-2:] != ['recover_alignment/v1', 'insert_post/v1']):
        raise ValueError('Candidate did not pass the independent physical replay; it remains unregistered: ' + result['run_dir'])
    if implementation_fingerprint() != implementation_before:
        raise ValueError('Execution code/models changed during validation. Keep the candidate unregistered and rerun.')
    run = Path(result['run_dir'])
    payload = {'schema_version': 1, 'status': 'sim_validated_for_recorded_case', 'candidate': candidate,
               'condition_fingerprint': candidate['condition_fingerprint'], 'implementation_fingerprint': implementation_before,
               'validation_profile': 'first_joint_alignment_offset_6mm/v1', 'validation_episodes': 1,
               'validation_run_dir': str(run), 'result_sha256': hashlib.sha256((run / 'result.json').read_bytes()).hexdigest(),
               'not_validated': ['real_hardware', 'ten_part_frame', 'new_geometry', 'other_failure_types'],
               'automatic_generalization': False}
    record = {'recipe_record_id': digest(payload), **payload}
    folder = Path(registry_dir) / 'validated'
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder / (record['recipe_record_id'] + '.json'), record)
    return record


def compatible_recipes(fingerprint, *, registry_dir=DEFAULT_RECIPES):
    result = []
    current_implementation = implementation_fingerprint()
    for path in sorted((Path(registry_dir) / 'validated').glob('*.json')):
        record = read_json(path)
        if (record.get('recipe_record_id') != digest({k: v for k, v in record.items() if k != 'recipe_record_id'})
                or record.get('status') != 'sim_validated_for_recorded_case' or record.get('automatic_generalization') is not False):
            raise ValueError('Invalid registered recipe.')
        validate_candidate(record['candidate'])
        if record['condition_fingerprint'] != record['candidate']['condition_fingerprint']:
            raise ValueError('Registered scope differs from candidate scope.')
        if record['condition_fingerprint'] == fingerprint and record['implementation_fingerprint'] == current_implementation:
            result.append(record)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    create = sub.add_parser('create')
    create.add_argument('--experience', type=Path, required=True)
    validate = sub.add_parser('validate')
    validate.add_argument('--candidate', type=Path, required=True)
    args = parser.parse_args()
    if args.operation == 'create':
        candidate = create_candidate(read_json(args.experience))
        destination = DEFAULT_RECIPES / 'candidates' / (candidate['candidate_id'] + '.json')
        destination.parent.mkdir(parents=True, exist_ok=True)
        write_json(destination, candidate)
        print(destination)
    else:
        record = validate_and_register(read_json(args.candidate))
        print(json.dumps({'recipe_record_id': record['recipe_record_id'], 'validation_run_dir': record['validation_run_dir'],
                          'status': record['status']}, ensure_ascii=False))
