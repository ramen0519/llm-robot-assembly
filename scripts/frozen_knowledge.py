"""Content-addressed development knowledge snapshot; no writes during evaluation."""
import argparse
from copy import deepcopy
import hashlib
from pathlib import Path

from experience_memory import DEFAULT_MEMORY, condition_fingerprint, digest, read_json, validate_record
from failure_report import FailureCode, write_json
from skill_recipes import DEFAULT_RECIPES, compatible_recipes, implementation_fingerprint, validate_candidate
from workcell_scene import ROOT

SUMMARY_FIELDS = ('record_id', 'outcome', 'failure_codes', 'verified_skill_results', 'successful_recovery_skills')


def validate_bundle(bundle, fingerprint, implementation):
    if (not isinstance(bundle, dict) or bundle.get('schema') != 'frozen_development_knowledge/v1'
            or bundle.get('bundle_id') != digest({k: v for k, v in bundle.items() if k != 'bundle_id'})
            or bundle.get('condition_fingerprint') != fingerprint
            or bundle.get('implementation_fingerprint') != implementation
            or bundle.get('source_split') != 'development' or bundle.get('update_during_episode') is not False):
        raise ValueError('Knowledge bundle changed or has incompatible code/conditions/split.')
    experiences, recipes = bundle.get('experiences'), bundle.get('recipes')
    if not isinstance(experiences, list) or len(experiences) > 100 or not isinstance(recipes, list) or len(recipes) > 10:
        raise ValueError('Knowledge snapshot is not bounded.')
    ids = set()
    for record in experiences:
        validate_record(record)
        if record['condition_fingerprint'] != fingerprint or record['record_id'] in ids:
            raise ValueError('Duplicate or incompatible experience in bundle.')
        ids.add(record['record_id'])
    recipe_ids = set()
    for record in recipes:
        validate_candidate(record['candidate'])
        if (record.get('recipe_record_id') != digest({k: v for k, v in record.items() if k != 'recipe_record_id'})
                or record.get('recipe_record_id') in recipe_ids
                or record.get('status') != 'sim_validated_for_recorded_case'
                or record.get('automatic_generalization') is not False
                or record.get('implementation_fingerprint') != implementation
                or record.get('condition_fingerprint') != fingerprint
                or record['candidate']['condition_fingerprint'] != fingerprint):
            raise ValueError('Unvalidated, stale, duplicate or incompatible recipe in bundle.')
        recipe_ids.add(record['recipe_record_id'])


def freeze_knowledge(fingerprint, *, memory_dir=DEFAULT_MEMORY, registry_dir=DEFAULT_RECIPES, output_dir=None):
    records = []
    for path in sorted(Path(memory_dir).glob('*.json')):
        record = read_json(path)
        validate_record(record)
        if record['condition_fingerprint'] == fingerprint:
            records.append(record)
    recipes = compatible_recipes(fingerprint, registry_dir=registry_dir)
    for record in recipes:
        source = Path(record['validation_run_dir']) / 'result.json'
        if hashlib.sha256(source.read_bytes()).hexdigest() != record['result_sha256']:
            raise ValueError('Recipe physical validation evidence changed; cannot freeze it.')
    payload = {'schema': 'frozen_development_knowledge/v1', 'source_split': 'development',
               'condition_fingerprint': fingerprint, 'implementation_fingerprint': implementation_fingerprint(),
               'update_during_episode': False, 'experiences': records, 'recipes': recipes}
    bundle = {'bundle_id': digest(payload), **payload}
    validate_bundle(bundle, fingerprint, payload['implementation_fingerprint'])
    destination = Path(output_dir or ROOT / 'runtime/knowledge/simulation') / (bundle['bundle_id'] + '.json')
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and read_json(destination) != bundle:
        raise ValueError('Immutable knowledge snapshot conflict.')
    if not destination.exists():
        write_json(destination, bundle)
    return destination, bundle


class FrozenKnowledge:
    def __init__(self, bundle, fingerprint, mode='both'):
        if mode not in ('memory', 'recipes', 'both'):
            raise ValueError('Knowledge mode must be memory, recipes or both.')
        validate_bundle(bundle, fingerprint, implementation_fingerprint())
        self.bundle = deepcopy(bundle)
        self.mode = mode
        if mode in ('recipes', 'both') and not self.bundle['recipes']:
            raise ValueError('No compatible validated recipe in this bundle; validate one or use memory mode.')

    def lookup(self, failure):
        if self.mode == 'recipes':
            return []
        code = FailureCode(failure['failure_code']).value if failure else None
        records = [r for r in self.bundle['experiences'] if code is None or code in r['failure_codes']]
        records.sort(key=lambda r: (not bool(r['successful_recovery_skills']), r['outcome'] != 'success', r['record_id']))
        return deepcopy([{key: r[key] for key in SUMMARY_FIELDS} for r in records[:3]])

    def recipe_summaries(self):
        if self.mode == 'memory':
            return []
        return [{'recipe_record_id': r['recipe_record_id'], 'candidate_id': r['candidate']['candidate_id'],
                 'trigger_failure': r['candidate']['trigger_failure'], 'steps': list(r['candidate']['steps']),
                 'validation_profile': r['validation_profile'], 'validation_episodes': r['validation_episodes'],
                 'not_validated': list(r['not_validated']), 'advisory_only': True}
                for r in self.bundle['recipes'][:3]]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True, help='Completed first-joint run defining the physical conditions')
    args = parser.parse_args()
    fingerprint = condition_fingerprint(read_json(args.run_dir / 'resolved_config.json'))
    path, bundle = freeze_knowledge(fingerprint)
    print(path)
    print(f"Experiences: {len(bundle['experiences'])}; currently validated recipes: {len(bundle['recipes'])}")
