"""Admit independently verified simulation revisions and search a pinned local catalogue."""
import argparse
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import re
import tempfile

from export_grasp_experience import ROOT, DEFAULT_OUTPUT, CHECKS, _decode, canonical, digest
from grasp_candidate_preflight import checked_trial
from grasp_revision_generation import validate_revision
from grasp_skill_candidates import validate_candidate
from grasp_skill_runtime import bind_rejection, revision_geometry, validate_observation

DEFAULT_LIBRARY = ROOT / 'runtime/skills/simulation/post_recovery_v1'
SCHEMA = 'verified_post_recovery_entry/v1'


def _read(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError('Oversized skill evidence.')
    return _decode(raw.decode('utf-8'))


def _scope(obs):
    return {'condition_id': obs['condition_context']['active_part_condition_id'],
            'part': obs['condition_context']['config']['part']['id'],
            'target': obs['condition_context']['config']['target']['id'],
            'simulator_version': obs['simulator_version'], 'installed_parts': obs['installed_parts']}


def _guard(obs, rejection):
    validate_observation(obs)
    old = rejection['source_observation']
    if (bind_rejection(old, rejection['trial']) != rejection or old['revision'] > obs['revision']
            or any(old[k] != v for k, v in obs.items() if k not in ('revision','observation_id'))
            or obs['provenance'] != 'mujoco_live_snapshot'):
        raise ValueError('Expected the current bound simulation rejection.')
    trial = rejection['trial']
    checked_trial(trial, trial['grasp'], old)
    if (trial.get('trace_summary') or {}).get('trace_complete') is not True:
        raise ValueError('Incomplete failure trace cannot ground admission or reuse.')
    facts = revision_geometry(trial)
    return trial, facts


def _audit_original_measurements(final, insertion, target_depth):
    def number(value):
        return type(value) in (float, int) and math.isfinite(value)
    row = final['row']
    limits = {'target_position_error_m': .001, 'target_orientation_error_rad': math.radians(1.),
              'part_speed_m_s': .001, 'part_angular_speed_rad_s': .01, 'penetration_m': .001}
    depth = final['insertion_window_measured_depth_m']
    error = final['insertion_window_max_position_error_m']
    if (not all(number(row[k]) and 0 <= row[k] < limit for k, limit in limits.items())
            or row['fixture_supported'] is not True or row['left_contact'] is not False
            or row['right_contact'] is not False or not number(row['pad_gap_mm']) or row['pad_gap_mm'] <= 80
            or not number(row['insertion_depth_m']) or abs(row['insertion_depth_m']-target_depth) >= .001
            or not number(depth) or abs(depth-target_depth) >= .001 or not number(error) or not 0 <= error < .001
            or not number(insertion['minimum_final_depth_m']) or insertion['minimum_final_depth_m'] != depth
            or not number(insertion['maximum_final_position_error_m']) or insertion['maximum_final_position_error_m'] != error):
        raise ValueError('Verified insertion needs matching measured depth, support, release and settled pose.')


def admit_verified_run(run_dir, directory=DEFAULT_LIBRARY):
    """One local publication per proof. Search and reuse never publish new entries."""
    folder = Path(run_dir)
    result = _read(folder / 'result.json')
    final = _read(folder / 'original_final_verification.json')
    parent = _read(folder / 'parent_candidate_snapshot.json')
    revision = _read(folder / 'revision_snapshot.json')
    obs = _read(folder / 'revision_observation.json')
    rejection = _read(folder / 'reproduced_rejection_binding.json')
    evaluated = _read(folder / 'revision/result.json')
    intent = _read(folder / 'original_pick_intent.json')
    insert = _read(folder / 'original_insert_result.json')
    validate_candidate(parent, memory_dir=DEFAULT_OUTPUT)
    validate_revision(revision, parent)
    failed, facts = _guard(obs, rejection)
    proposal = evaluated['evaluation']['proposal']
    trial = checked_trial(evaluated['trial'], proposal['grasp'], obs)
    scope = _scope(obs)
    checks = final['checks']
    if (result.get('status') != 'original_simulation_verified'
            or result.get('environment') != 'simulation' or result['episode_id'] != obs['episode_id']
            or result['original_execution_started'] is not True or result['original_stage'] != 'verified'
            or result['original_scene_assembly_success'] is not True or result['input_artifacts_unchanged'] is not True
            or result['original_state_unchanged_through_preflights'] is not True
            or result['revision_id'] != revision['revision_id'] or result['code_sha256'] != revision['code_sha256']
            or result['original_installed_parts'] != sorted(obs['installed_parts'] + [scope['part']])
            or result['original_final_verification'] != final or final['measurement_domain'] != 'original_simulation'
            or final['read_only_measurements'] is not True or final['other_parts_preserved'] is not True
            or final['installed_part'] != scope['part'] or final['target_id'] != scope['target']
            or insert['skill'] != 'insert_post/v1' or insert['status'] != 'success'
            or insert['part_id'] != scope['part'] or insert['target_id'] != scope['target']
            or insert['checks'] != checks or set(checks) != CHECKS | {'declared_mating_support'}
            or any(v is not True for v in checks.values())
            or evaluated['status'] != 'preflight_accepted' or evaluated['original_state_unchanged'] is not True
            or trial['accepted'] is not True
            or trial['trace_summary']['trace_complete'] is not True or intent['grasp'] != proposal['grasp']
            or intent['part_id'] != scope['part'] or intent['target_id'] != scope['target']
            or proposal['part_id'] != scope['part'] or proposal['target_id'] != scope['target']
            or intent['observation_revision'] != obs['revision'] or proposal['observation_revision'] != obs['revision']
            or proposal['episode_id'] != obs['episode_id'] or proposal['execution_authorized'] is not False
            or proposal['requires_current_scene_preflight'] is not True
            or intent['observation_id'] != obs['observation_id'] or proposal['observation_id'] != obs['observation_id']
            or proposal['candidate_id'] != revision['revision_id'] or proposal['code_sha256'] != revision['code_sha256']
            or proposal['rejection_id'] != rejection['rejection_id']
            or not facts['pick_completed_in_copy'] or not facts['pad_contacts_socket_wall']
            or facts['predicted_pad_to_rim_clearance_mm'] is None
            or failed['failure_code'] != 'PLANNED_CONTACT' or failed['phase'] != 'insert_lower/preflight'):
        raise ValueError('No matching verified original-scene post installation.')
    target_depth = obs['condition_context']['joint']['nominal_insertion_depth_m']
    _audit_original_measurements(final, insert, target_depth)
    measured_depth = final['insertion_window_measured_depth_m']
    measured_error = final['insertion_window_max_position_error_m']
    body = {'schema': SCHEMA, 'scope': scope, 'origin_episode': obs['episode_id'],
            'proof_sha256': digest(result), 'parent':parent, 'revision':revision,
            'failure':{'code':failed['failure_code'], 'phase':failed['phase'], 'grasp':failed['grasp'], **facts},
            'observed':{'depth_mm':measured_depth * 1000, 'position_error_mm':measured_error * 1000,
                        'completed_checks': sorted(checks)}}
    key = digest(body)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (key + '.json')
    with tempfile.NamedTemporaryFile(dir=directory, suffix='.tmp', delete=False) as tmp:
        tmp.write(canonical(body).encode('utf-8'))
        temp_path = Path(tmp.name)
    try:
        try:
            os.link(temp_path, path)
        except FileExistsError:
            if _read(path) != body:
                raise ValueError('Existing catalogue entry conflicts with its identity.')
    finally:
        temp_path.unlink(missing_ok=True)
    return 'skill:' + key


class SkillLibrary:
    def __init__(self, directory=DEFAULT_LIBRARY):
        self._entries, self._cards = {}, {}
        for path in sorted(Path(directory).glob('*.json')):
            entry = _read(path)
            key = digest(entry)
            if path.stem != key or entry.get('schema') != SCHEMA:
                raise ValueError('Unrecognized or changed catalogue entry.')
            skill, memory = 'skill:' + key, 'memory:' + key
            self._entries[skill] = entry
            scope, failure = entry['scope'], entry['failure']
            tags = ('기둥 파지 집기 삽입 간섭 손가락 패드 벽 충돌 여유 복구 post pillar peg grasp insertion '
                    'socket pad wall collision interference recovery clearance ' +
                    ' '.join((scope['part'], scope['target'], failure['code'], failure['phase'])))
            self._cards[skill] = {'document_id':skill, 'kind':'skill', 'title':'Verified post grasp clearance recovery',
                'terms':tags, 'scope':scope, 'preconditions':{'code':failure['code'],'phase':failure['phase'],
                    'pick_completed_in_copy':True,'pad_contacts_socket_wall':True,
                    'requires_current_predicted_clearance':True,'holding_part':False},
                'evidence':entry['observed'], 'use':'Recalculate a part-frame grasp from the CURRENT projected clearance; fresh preflight required.',
                'limits':'Verified in one original simulation episode within this scope.'}
            self._cards[memory] = {'document_id':memory, 'kind':'memory','title':'Post insertion path rejection after a successful pick',
                'terms':tags+' failure history 경험 실패', 'scope':scope, 'failed_trial':failure,
                'use':'Historical failure observation. No executable procedure.'}
        self.snapshot_id = digest(sorted(self._cards))
        self.skill_count, self.document_count = len(self._entries), len(self._cards)

    def search(self, query):
        if not isinstance(query,str) or not 0 < len(query.strip()) <= 300:
            raise ValueError('Expected a short search query.')
        words = set(re.findall(r'[^\W_]+',query.casefold()))
        ranked = []
        for key, card in self._cards.items():
            terms = set(re.findall(r'[^\W_]+', (card['kind']+' '+card['title']+' '+card['terms']).casefold()))
            score = len(words & terms)
            if score:
                ranked.append((-score,key,{k:card[k] for k in ('document_id','kind','title')}))
        return [hit for _,_,hit in sorted(ranked)[:6]]

    def read(self, document_id):
        return deepcopy({k:v for k,v in self._cards[document_id].items() if k != 'terms'})

    def resolve(self, document_id, observation, rejection):
        if document_id not in self._entries:
            raise ValueError('Only an admitted executable skill can be resolved.')
        entry = self._entries[document_id]
        trial, facts = _guard(observation, rejection)
        if (entry['scope'] != _scope(observation) or entry['origin_episode'] == observation['episode_id']
                or trial['failure_code'] != entry['failure']['code'] or trial['phase'] != entry['failure']['phase']
                or not facts['pick_completed_in_copy'] or not facts['pad_contacts_socket_wall']
                or facts['predicted_pad_to_rim_clearance_mm'] is None or observation['measurements']['holding_part']):
            raise ValueError('Registered skill outside verified conditions in this episode.')
        return deepcopy(entry['parent']), deepcopy(entry['revision'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admit-run', type=Path, required=True)
    parser.add_argument('--library-dir', type=Path, default=DEFAULT_LIBRARY)
    args = parser.parse_args()
    key = admit_verified_run(args.admit_run, args.library_dir)
    print(json.dumps({'registered_id':key,'snapshot':SkillLibrary(args.library_dir).snapshot_id}))
