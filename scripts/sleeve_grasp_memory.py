"""A source-only, immutable example for advisory retrieval before an upright square-sleeve pick."""
import csv
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MEMORY = ROOT / 'runtime/memory/simulation/sleeve_grasp_v1'
SOURCE_PART = 'sleeve_1'
SOURCE_FILES = ('result.json', 'resolved_config.json', 'llm_events.jsonl')
GEOMETRY_KEYS = ('shape', 'part_size_xyz_mm', 'frame', 'position', 'orientation', 'gripper_max_open_mm',
                 'open_pad_spans_along_tcp_positive_z_mm', 'pad_axis_note', 'bore_width_mm', 'mating_post_width_mm',
                 'nominal_exposed_post_height_mm', 'seating_surface', 'tcp_note')
INSERT_CHECKS = ('released', 'target_position_under_1mm', 'target_orientation_under_1deg', 'insertion_depth_reached',
                 'linear_speed_under_1mm_s', 'angular_speed_under_0_01rad_s', 'penetration_under_1mm',
                 'declared_mating_support', 'post_through_both_sleeve_openings')
SEARCH_TERMS = frozenset('sleeve sleeves tube tubular collar square hollow bore hole closing closure close pick grasp '
                         'grasping gripper finger jaw pad pads table tabletop workbench collision contact clearance height '
                         'preflight approach tcp z 슬리브 파지 그리퍼 패드 접촉 작업대 충돌 높이 구멍'.split())


def _require(ok, detail):
    if not ok:
        raise ValueError('Sleeve experience: ' + detail)


def _digest(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _scope(geometry, robot, representation, transport, empty):
    return {'environment': 'simulation', 'observation_source': 'sim_ground_truth', 'robot': robot['model'],
            'gripper': robot['gripper'], 'geometry_representation': representation, 'part_kind': 'sleeve',
            'geometry': {key: deepcopy(geometry[key]) for key in GEOMETRY_KEYS}, 'translation_unit': 'mm',
            'jaw_yaw_unit': 'deg', 'transport_strategy': transport, 'empty_approach_strategy': empty}


def _query(*, episode, revision, part, observation, geometry, scope, empty, pre_pick):
    mating = geometry['mating_post_id']
    active = observation['part']
    all_parts = observation['all_parts']
    return {'episode_id': episode, 'observation_revision': revision, 'part_id': part, 'scope': scope,
            'source': observation['source'], 'observed_part_id': active['id'], 'mating_post_id': mating,
            'mating_post_installed': all_parts.get(mating, {}).get('installed'),
            'part_installed': all_parts.get(part, {}).get('installed'), 'empty_gripper': empty, 'pre_pick': pre_pick,
            'quaternion_xyzw': deepcopy(active.get('quaternion_xyzw')),
            'linear_speed_m_s': active.get('linear_speed_m_s'), 'angular_speed_rad_s': active.get('angular_speed_rad_s')}


def build_query(skills, *, observation_revision, episode_id):
    """Read only the present part and its mating post; neither a future path nor an insertion is evaluated."""
    part = skills.active
    entry = skills.registry[part]
    if entry['kind'] != 'sleeve':
        return {'part_id': part, 'scope': None}
    observation, geometry = skills.observe(), skills.grasp_geometry()
    scope = _scope(geometry, skills.config['robot'], entry['geometry_representation'],
                   skills.transport_strategy, skills.empty_approach_strategy)
    return _query(episode=episode_id, revision=observation_revision, part=part, observation=observation,
                  geometry=geometry, scope=scope, empty=skills.motion.held_pose is None,
                  pre_pick=not skills.results and part not in skills.installed)


def _ready(q):
    if (not isinstance(q.get('episode_id'), str) or not q['episode_id'].strip()
            or type(q.get('observation_revision')) is not int or q['observation_revision'] <= 0
            or q.get('source') != 'sim_ground_truth' or not isinstance(q.get('part_id'), str)
            or not re.fullmatch(r'sleeve_[1-9][0-9]*', q['part_id']) or q.get('observed_part_id') != q['part_id']
            or q.get('mating_post_id') != 'post_' + q['part_id'].split('_')[1]
            or q.get('mating_post_installed') is not True or q.get('part_installed') is not False
            or q.get('pre_pick') is not True or q.get('empty_gripper') is not True):
        return False
    for key, limit in (('linear_speed_m_s', .001), ('angular_speed_rad_s', .01)):
        value = q.get(key)
        if value is not None and (not _finite(value) or not 0 <= value < limit):
            return False
    quat = q.get('quaternion_xyzw')
    return (isinstance(quat, (list, tuple)) and len(quat) == 4 and all(_finite(v) for v in quat)
            and abs(sum(v*v for v in quat) - 1) < 1e-5
            and 1 - 2*(quat[0]**2 + quat[1]**2) >= math.cos(math.radians(1)))


def _source_pick_row(folder, moment):
    # Stop at the source pick. Later sibling trials cannot supply evidence for this record.
    with (folder / 'skill_trace.csv').open(encoding='utf-8', newline='') as stream:
        for row in csv.DictReader(stream):
            time_s = float(row['simulation_time_s'])
            if time_s > moment + 1e-8:
                break
            if row['active_part_id'] == SOURCE_PART and row['stage'] == 'pick_lift' and abs(time_s - moment) <= 1e-8:
                _require(row['supported'] == 'False' and row['left_contact'] == row['right_contact'] == 'True',
                         'no measured free bilateral grasp at handback')
                gap, height = float(row['pad_gap_mm']), float(row['part_bottom_clearance_m'])
                _require(10 < gap < 70 and height >= .05 and math.isfinite(height), 'invalid pick clearance or jaw gap')
                return {'part_bottom_clearance_m': height, 'pad_gap_mm': gap, 'left_contact': True,
                        'right_contact': True, 'supported': False, 'simulation_time_s': moment}
    raise ValueError('Missing original-scene pick sample for source handback.')


def _admit(folder):
    files = {name: (folder / name).read_bytes() for name in SOURCE_FILES}
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    result, config = [json.loads(files[n]) for n in ('result.json', 'resolved_config.json')]
    part = SOURCE_PART
    state, phase, entry = result['graph_states'][part], result['part_episodes'][part], config['registry'][part]
    _require(config['environment'] == 'simulation' and config['observation_source'] == 'sim_ground_truth'
             and config['policy_kind'] == 'openai_responses' and config['orchestration'] == 'roles'
             and entry['kind'] == 'sleeve' and entry['requires'] == ['base', 'post_1']
             and result['status'] == state['status'] == 'success' and state['completion_source'] == 'physics_verifier'
             and state['completed'] == ['pick', 'insert'] and 'post_1' in phase['start']['installed']
             and part not in phase['start']['installed'] and set(phase['end']['installed']) == set(phase['start']['installed']) | {part},
             'source lacks original verified installation')
    proposals, initial, packet, response = [], None, None, None
    for line in files['llm_events.jsonl'].splitlines():
        e = json.loads(line)
        if e['event'] == 'request_prepared':
            packet = json.loads(e['request']['input'])
            response = None
            if initial is None and packet['part_id'] == part and packet['completed'] == [] and 'grasp_task' in packet:
                initial = packet
        elif e['event'] == 'model_response_received':
            response = e
        elif e['event'] == 'proposal_validated' and e['proposal']['part_id'] == part:
            action = e['proposal']
            _require(packet and response and packet['part_id'] == part and packet['target_id'] == action['target_id']
                     and packet['observation_revision'] == action['observation_revision']
                     and packet['active_role'] == e['agent_role'] == response['agent_role']
                     and response['response_status'] == 'completed' and response['response_id'] == e['response_id'],
                     'source proposal lacks its current observation and completed response')
            proposals.append(e)
            if action['skill'] == 'insert':
                break
    _require(initial is not None and [(p['agent_role'], p['proposal']['skill']) for p in proposals]
             == [('assembly', 'pick'), ('recovery', 'pick'), ('assembly', 'insert')]
             and len({p['response_id'] for p in proposals}) == 3, 'missing linked live proposals')
    geom = initial['grasp_task']['geometry']
    scope = _scope(geom, entry['config']['robot'], entry['geometry_representation'],
                   config['transport_strategy'], config['empty_approach_strategy'])
    q = _query(episode=folder.name, revision=initial['observation_revision'], part=part,
               observation=initial['observation'], geometry=geom, scope=scope, empty=True, pre_pick=True)
    _require(_ready(q), 'source observation is outside the pre-pick scope')
    rejected, repaired = state['grasp_trials']
    outgoing, returning = state['agent_handoffs']
    checked = returning['proof']
    _require(rejected['source'] == repaired['source'] == 'mujoco_preflight_copy' and rejected['accepted'] is False
             and rejected['phase'] == 'pick_close' and rejected['failure_code'] == 'UNEXPECTED_CONTACT'
             and repaired['accepted'] is True and rejected['live_start_s'] == repaired['live_start_s']
             and rejected['observation_revision'] < repaired['observation_revision']
             and all(t['grasp'] == p['proposal']['grasp'] and t['observation_revision'] == p['proposal']['observation_revision']
                     for t, p in zip((rejected, repaired), proposals[:2]))
             and initial['observation_revision'] == rejected['observation_revision']
             and (outgoing['from'], outgoing['to']) == ('assembly', 'recovery') and outgoing['part_id'] == returning['part_id'] == part
             and outgoing['observation_revision'] == repaired['observation_revision'] == checked['preflight_revision']
             and returning['observation_revision'] == proposals[-1]['proposal']['observation_revision']
             and outgoing['proof']['failure_code'] == rejected['failure_code'] and outgoing['proof']['phase'] == rejected['phase']
             and outgoing['proof']['rejected_grasp'] == rejected['grasp']
             and (returning['from'], returning['to']) == ('recovery', 'assembly')
             and checked['kind'] == 'verified_original_pick' and checked['held_and_lifted'] is True
             and checked['recovery_method'] == 'numeric_grasp_revision'
             and phase['executed_approach']['part_id'] == part and phase['executed_approach']['status'] == 'success'
             and checked['accepted_grasp'] == phase['executed_approach']['proposed_grasp'] == repaired['grasp'],
             'grasp proposals, copy checks and original pick are not a single recovery chain')
    contacts = [{'geoms': v['geoms'], 'signed_distance_m': v['signed_distance_m']} for v in rejected['violations']
                if len(v['geoms']) == 2 and 'workcell_tabletop' in v['geoms']
                and any(re.fullmatch(r'gripper_(right|left)_pad[12]', g) for g in v['geoms'])
                and _finite(v['signed_distance_m']) and v['signed_distance_m'] < 0]
    _require(contacts, 'missing signed pad/table contact')
    live_pick = _source_pick_row(folder, checked['source_state']['simulation_time_s'])
    _require(abs(live_pick['part_bottom_clearance_m'] - checked['part_bottom_clearance_m']) <= 1e-8,
             'handback clearance differs from original pick sample')
    insertion, = [r for r in result['results'] if r['part_id'] == part]
    fit = insertion['sleeve_fit']
    _require(insertion['status'] == 'success' and insertion['skill'] == 'insert_sleeve/v1'
             and insertion['target_id'] == proposals[-1]['proposal']['target_id']
             and all(insertion['checks'].get(key) is True and state['verification']['checks'].get(key) is True
                     for key in INSERT_CHECKS) and fit == state['verification']['sleeve_fit'] and fit['post_id'] == 'post_1'
             and all(_finite(fit[k]) and fit[k] > 0 for k in
                     ('minimum_bore_side_clearance_mm', 'minimum_post_extension_past_openings_mm'))
             and _finite(insertion['minimum_final_depth_m']) and insertion['minimum_final_depth_m'] > 0,
             'missing measured release, support or mating-post evidence')
    record = {'schema': 'sleeve_grasp_experience/v1', 'source_part': part, 'source_episode': folder.name,
              'source_file_sha256': hashes, 'scope': scope,
              'rejected': {'grasp': rejected['grasp'], 'phase': rejected['phase'], 'failure_code': rejected['failure_code'],
                           'source': rejected['source'], 'revision': rejected['observation_revision'],
                           'contacts': contacts},
              'repair': {'grasp': repaired['grasp'], 'method': checked['recovery_method'], 'preflight_accepted': True,
                         'revision': repaired['observation_revision']}, 'original_pick': live_pick,
              'original_insert': {'checks': {key: True for key in INSERT_CHECKS}, 'sleeve_fit': fit,
                                  'minimum_final_depth_m': insertion['minimum_final_depth_m']},
              'source_response_ids': [p['response_id'] for p in proposals]}
    return {**record, 'experience_id': _digest(record)}


def _validate(record):
    _require(isinstance(record, dict) and record.get('schema') == 'sleeve_grasp_experience/v1'
             and isinstance(record.get('source_episode'), str) and record['source_episode']
             and record.get('source_part') == SOURCE_PART and set(record.get('source_file_sha256', {})) == set(SOURCE_FILES)
             and all(isinstance(v, str) and re.fullmatch('[0-9a-f]{64}', v) for v in record['source_file_sha256'].values())
             and record.get('experience_id') == _digest({k: v for k, v in record.items() if k != 'experience_id'}),
             'invalid source or immutable record digest')


def load_experience(path, *, expected_id=None, source_run=None):
    path = Path(path)
    record = json.loads(path.read_text(encoding='utf-8'))
    _validate(record)
    _require(path.name == record['experience_id'] + '.json', 'filename and experience identity disagree')
    if expected_id is not None:
        _require(expected_id == record['experience_id'], 'record differs from pinned experiment ID')
    if source_run is not None:
        _require(record == _admit(Path(source_run).resolve()), 'record differs from current source evidence')
    return record


def save_source_experience(source_run, *, output_dir=DEFAULT_MEMORY):
    source, output = Path(source_run).resolve(), Path(output_dir).resolve()
    _require(not output.is_relative_to(source), 'output must be outside the source run')
    record = _admit(source)
    _validate(record)
    output.mkdir(parents=True, exist_ok=True)
    dest = output / (record['experience_id'] + '.json')
    if dest.exists():
        _require(record == load_experience(dest), 'immutable record conflict')
        return dest, False
    data = (json.dumps(record, ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode('utf-8')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output, prefix='.sleeve-', suffix='.tmp', delete=False) as f:
            temporary = Path(f.name)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(temporary, dest)
            return dest, True
        except FileExistsError:
            _require(record == load_experience(dest), 'concurrent immutable record conflict')
            return dest, False
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def retrieve_experience(record, query, *, search_text=None):
    _validate(record)
    if (not isinstance(query, dict) or query.get('scope') != record['scope'] or not _ready(query)
            or (query['episode_id'] == record['source_episode'] and query['part_id'] == record['source_part'])):
        return None
    if search_text is not None:
        if not isinstance(search_text, str) or len(search_text) > 512:
            return None
        tokens = set(re.findall(r'[a-z0-9]+|[가-힣]+', search_text.casefold()))
        if not tokens.intersection(SEARCH_TERMS):
            return None
    card = {k: deepcopy(record[k]) for k in ('source_part', 'scope', 'rejected', 'repair', 'original_pick', 'original_insert')}
    return {'record_id': record['experience_id'], **card, 'use': 'One prior simulation instance. Propose a new grasp for '
            'the current observation and verify it by current-scene preflight before original execution.'}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run', type=Path, required=True, help='Completed source episode containing the sleeve_1 recovery')
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_MEMORY)
    args = parser.parse_args()
    path, created = save_source_experience(args.source_run, output_dir=args.output_dir)
    print(json.dumps({'path': str(path), 'experience_id': path.stem, 'created': created}, ensure_ascii=False))
