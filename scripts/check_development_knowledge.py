"""Check the 22T evaluation prerequisites on the two frozen development products."""
import hashlib
import json
from pathlib import Path

from development_post_packets import check_post_packets
from development_post_scope import check_post_scope
from development_sleeve_packets import check_sleeve_packets
from generate_grasp_lesson import _new_run_dir, _write_json
from grasp_skill_library import SkillLibrary
from sleeve_grasp_memory import load_experience


ROOT = Path(__file__).resolve().parents[1]
FOUR_POST_RUN = '20261111T134520_867280Z_047a16bf'
TRIANGLE_RUN = '20261111T160843_513944Z_29d9f165'


def load_frozen_inputs(repo):
    manifest = json.loads((repo / 'docs/SIM_PROTOTYPE_V1_FREEZE.json').read_text(encoding='utf-8'))
    root = repo / manifest['local_snapshot']
    files = {item['name']: item for item in manifest['artifacts']}
    if set(files) != {'sleeve_experience', 'registered_post_skill', 'model_configuration', 'dependency_lock'}:
        raise ValueError('Incomplete prototype freeze manifest.')
    for item in files.values():
        if hashlib.sha256((root / item['snapshot_file']).read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('Frozen file changed: ' + item['name'])
    memory = files['sleeve_experience']
    skill = files['registered_post_skill']
    record = load_experience(root / memory['snapshot_file'], expected_id=memory['experience_id'])
    library = SkillLibrary((root / skill['snapshot_file']).parent)
    if library.snapshot_id != skill['library_snapshot_id'] or library.skill_count != 1:
        raise ValueError('Frozen skill catalogue differs from the prototype snapshot.')
    return manifest, record, library


def run_checks(repo=ROOT):
    manifest, record, library = load_frozen_inputs(repo)
    runs = repo / 'runs/step23_frame'
    four, triangle = runs / FOUR_POST_RUN, runs / TRIANGLE_RUN
    folder = _new_run_dir(repo / 'runs/step22u_development_knowledge')
    inputs = {'prototype_commit': manifest['prototype_commit'], 'library_snapshot_id': library.snapshot_id,
              'experience_id': record['experience_id'], 'development_episodes': [four.name, triangle.name]}
    _write_json(folder / 'inputs.json', inputs)
    print('Development evidence:', folder, flush=True)
    sleeve_probes = []
    for episode, parts in ((four, ['sleeve_2', 'sleeve_4']), (triangle, ['sleeve_3', 'sleeve_2'])):
        for part in parts:
            probe = check_sleeve_packets(repo, episode, part, record)
            _write_json(folder / (episode.name + '_' + part + '.json'), probe)
            sleeve_probes.append(probe)
            print('Verified first sleeve request:', episode.name, part, flush=True)
    post_packets = check_post_packets(repo, four)
    _write_json(folder / 'post_requests.json', post_packets)
    print('Compared recovery requests for the same measured post rejection.', flush=True)
    post_scope = check_post_scope(repo, four, triangle, library)
    _write_json(folder / 'post_scope.json', post_scope)

    expected = {'A': [], 'B': [record['experience_id']], 'C': [record['experience_id']]}
    sleeve_supported = all(p['experience_ids'] == expected and p['identical_B_C_first_sleeve_requests']
                           and p['matches_archived_first_search_and_decision'] for p in sleeve_probes)
    same_post_cards = set(post_packets['b_document_ids']) == set(post_packets['c_read_document_ids'])
    if not sleeve_supported or not post_scope['registered_case_recomputed']:
        raise ValueError('Missing positive control for the frozen knowledge inputs.')
    checks = {'sleeve_first_request_parity': sleeve_supported, 'post_text_parity': same_post_cards,
              'registered_development_post_recomputed': post_scope['registered_case_recomputed'],
              'triangle_post_resolved': post_scope['triangle_resolved'],
              'triangle_denial_verified': post_scope['triangle_denial_verified']}
    # This is a diagnostic of the frozen prototype, not an automatic qualification
    # procedure for later implementations or other products.
    if same_post_cards or not post_scope['triangle_denial_verified'] or post_scope['triangle_resolved']:
        raise ValueError('Frozen post routing or applicability changed; review before choosing a study.')
    report = {'schema': 'development_knowledge_check/v1', 'status': 'completed',
              'tests': checks, 'sleeve_checkpoint_count': len(sleeve_probes),
              'protocol_route': 'reduced_sleeve_memory_A_B',
              'provider_calls_this_check': 0, 'held_out_episodes_started': 0, 'inputs': inputs}
    _write_json(folder / 'result.json', report)
    return report


if __name__ == '__main__':
    print(json.dumps(run_checks(), ensure_ascii=False, indent=2))
