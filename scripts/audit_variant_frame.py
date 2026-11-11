"""Recheck the eight-part triangular assembly, its native plan, decisions and measured final state."""
import argparse
import csv
import hashlib
from itertools import groupby
import json
import math
from pathlib import Path
import tempfile

from audit_agent_handoffs import _graph_part, _json, _rows, _turns

LAYOUT = 'triangle_three_post'
POSTS = {f'post_{i}' for i in range(1, 4)}
SLEEVES = {f'sleeve_{i}' for i in range(1, 4)}
PARTS = POSTS | SLEEVES | {'cover'}


def require(ok, message):
    if not ok:
        raise ValueError('Triangle audit: ' + message)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cad_difference(path, registry):
    """Compare the original development meshes with the triangular native inputs."""
    from frame_product import export_product
    from multi_post_scene import MULTI_DIMENSIONS
    normalized = _json(path.parent / 'normalization.json')
    by_name = {part: index for index, part in normalized['binding']['part_map'].items()}
    require(len(by_name) == 8 and set(by_name) == PARTS | {'base'}, 'native part map does not describe eight parts')
    with tempfile.TemporaryDirectory(prefix='triangle-cad-comparison-') as tmp:
        old = export_product(Path(tmp), MULTI_DIMENSIONS)
        old_hash = {p['id']: _sha(Path(tmp) / p['obj']) for p in old['parts']}
    changed = {}
    for name in ('base', 'cover'):
        new_hash = _sha(path.parent / 'asap_input' / (by_name[name] + '.obj'))
        require(new_hash != old_hash[name], f'{name} mesh still matches the four-post control')
        changed[name] = {'four_post_obj_sha256': old_hash[name], 'triangle_obj_sha256': new_hash}
    cover = registry['cover']
    centers = cover['bore_centers_part_m']
    require(set(centers) == POSTS and set(cover['support_ids']) == SLEEVES, 'cover hole/support IDs differ')
    a, b, c = [centers[f'post_{i}'] for i in range(1, 4)]
    double_area = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    require(math.isfinite(double_area) and abs(double_area) > 1e-8, 'bore centers do not form a triangle')
    return changed


def _decisions(folder, config, result, order):
    require(config['policy_kind'] == result['policy_kind'] == 'openai_responses'
            and config['orchestration'] == result['orchestration'] == 'roles'
            and config['agent_roles'] == ['assembly', 'recovery'] and config['budget_scope'] == 'shared_episode'
            and config['scenario'] == 'nominal' and not config.get('fixed_first_grasp_trigger')
            and not config.get('grasp_skill_library'), 'expected nominal live policy provenance for the new product')
    ledger = _turns(folder, result, config, allow_idle_recovery=True)
    memory = config.get('sleeve_memory')
    targets = [] if memory is None else memory['targets']
    require(memory is None or (len(targets) == 2 and set(targets) == {'sleeve_2', 'sleeve_3'}),
            'unexpected sleeve search targets')
    turns = iter(ledger)
    rows_by_part = [(name, list(group)) for name, group in groupby(_rows(folder / 'graph_events.jsonl'),
                                                                  key=lambda row: row['active_part_id'])]
    require([name for name, _ in rows_by_part] == order, 'graph episodes out of native assembly order')
    results = result['results']
    require([item['part_id'] for item in results] == order, 'missing, duplicated or reordered insertion result')
    pick_times = {}
    handoffs = {}
    for (name, rows), item in zip(rows_by_part, results, strict=True):
        state = result['graph_states'][name]
        episode = result['part_episodes'][name]
        try:
            handoffs[name] = _graph_part(folder, name, state, episode, rows, turns, False,
                                         memory['experience_id'] if name in targets else None)
        except StopIteration as exc:
            raise ValueError('Missing provider decision for ' + name) from exc
        require(state['status'] == item['status'] == 'success' and state['completion_source'] == 'physics_verifier'
                and state['grasp_trials'][-1]['accepted'] is True
                and state['grasp_trials'][-1]['grasp'] == episode['executed_approach']['proposed_grasp']
                and state['verification']['checks'] == item['checks'] and item['checks']
                and all(ok is True for ok in item['checks'].values()), 'incomplete verified original installation for ' + name)
        decisions = sum(row['node'] == 'decide' for row in rows)
        count = episode['api_calls_at_end'] - episode['api_calls_at_start']
        require(count == decisions + int(name in targets), 'per-part provider turn count differs for ' + name)
        verified = [row for row in rows if row['node'] == 'verify' and row['action']['skill'] == 'pick']
        require(len(verified) == 1, 'missing or repeated verified pick for ' + name)
        pick_times[name] = verified[0]['simulation_time_s']
    require(next(turns, None) is None and result['api_calls'] == len(ledger)
            and result['policy_calls'] + len(targets) == len(ledger), 'unbound provider turn')
    return pick_times, handoffs, targets


def _trace(folder, order, pick_times, result):
    count = 0
    last_time = None
    observed_order = []
    with (folder / 'skill_trace.csv').open(encoding='utf-8', newline='') as stream:
        for name, rows in groupby(csv.DictReader(stream), key=lambda row: row['active_part_id']):
            observed_order.append(name)
            require(name in order, 'unexpected part in original scene trace')
            stages = []
            lifted = False
            terminal = None
            for row in rows:
                count += 1
                now = float(row['simulation_time_s'])
                require(last_time is None or 0 <= now - last_time <= .00200001, 'simulation trace time gap')
                last_time = now
                if not stages or stages[-1] != row['stage']:
                    stages.append(row['stage'])
                if row['stage'] == 'pick_lift' and (pick_times is None or
                        math.isclose(now, pick_times[name], abs_tol=1e-8, rel_tol=0)):
                    lifted = (row['left_contact'] == row['right_contact'] == 'True'
                              and row['supported'] == 'False' and float(row['part_bottom_clearance_m']) >= .05)
                terminal = row
            required = ('pick_close', 'pick_lift', 'insert_lower', 'insert_release', 'insert_retreat', 'insert_verify')
            require(lifted and [s for s in stages if s in required] == list(required), 'incomplete pick/insert trace for ' + name)
            require(terminal['stage'] == 'insert_verify' and terminal['supported'] == 'True'
                    and terminal['left_contact'] == terminal['right_contact'] == 'False', 'missing release/support for ' + name)
    require(observed_order == order and count == result['samples'], 'trace does not account for all seven parts')
    return count


def audit(run_dir, *, planner_candidate, physics_only=False):
    from failure_report import json_safe
    from frame_assembly_scene import build_frame_scene
    from frame_assembly_skills import FrameSkills
    from planner_candidate_input import load_robot_candidate
    from run_frame_assembly import episode_boundary, restore_episode_data

    folder, native = Path(run_dir).resolve(), Path(planner_candidate).resolve()
    config, result = _json(folder / 'resolved_config.json'), _json(folder / 'result.json')
    require(config['product_layout'] == result['product_layout'] == LAYOUT and result['status'] == 'success'
            and result['complete_product_verified'] is True
            and result['product_parts_present'] == 8 and result['robot_installed_parts'] == 7
            and result['robot_installations_goal'] == 7 and result['resume'] is None
            and result['checkpoint_error'] is None and config.get('initial_installed', []) == [], 'missing fresh triangular episode')
    model, initial, registry = build_frame_scene(8, layout=LAYOUT)
    require(set(registry) == PARTS and json_safe(registry) == config['registry'], 'registry differs from the triangular physics model')
    order, provenance = load_robot_candidate(native, registry, layout=LAYOUT)
    require(provenance == config['planner_provenance'] == result['planner_provenance']
            and provenance['candidate_sha256'] == _sha(native) and provenance['fixed_base'] == 'base'
            and order == list(reversed(provenance['disassembly_order']))
            and (config.get('sequence') == order or (physics_only and 'sequence' not in config))
            and order == result['requested_sequence'] == result['execution_order']
            and order[-1] == 'cover' and len(order) == 7 and set(order) == PARTS, 'native inverse order or hashes differ')
    changed = _cad_difference(native, registry)
    episodes = result['part_episodes']
    require(set(episodes) == set(result['completed_installations']) == PARTS and len(result['completed_installations']) == 7,
            'missing or duplicated installation history')
    boundary = episode_boundary(model, initial, registry, set())
    billed = 0
    installed = set()
    for name in order:
        episode = episodes[name]
        require(episode['start'] == boundary and episode['api_calls_at_start'] == billed
                and boundary['installed'] == sorted(installed), 'different source state entering ' + name)
        installed.add(name)
        billed = episode['api_calls_at_end']
        end = episode['end']
        require(end['installed'] == sorted(installed) and end['time_s'] > boundary['time_s']
                and end['model_sha256'] == boundary['model_sha256'], 'bad installation boundary for ' + name)
        boundary = end
    final = result['final_verification']
    require(billed == result['api_calls'] and boundary == result['terminal_checkpoint'] == final['boundary']
            and final['all_installed_parts_preserved'] is True and set(final['parts']) == PARTS,
            'terminal state or provider total differs')
    if physics_only:
        require(config['policy_kind'] == result['policy_kind'] == 'scripted'
                and config['orchestration'] == result['orchestration'] == 'scripted'
                and config['completion_protocol'] == 'scripted_skill_verification'
                and not config.get('sleeve_memory') and result['api_calls'] == 0
                and not result['graph_states'] and result['llm_usage'] is None
                and not (folder / 'llm_events.jsonl').exists(),
                'physics-only audit requires a zero-call scripted run')
        require([r['part_id'] for r in result['results']] == order and all(
                r['status'] == 'success' and r['checks'] and all(ok is True for ok in r['checks'].values())
                for r in result['results']), 'scripted insertion lacks successful measurements')
        pick_times, handoffs, targets = None, {}, []
        policy_provenance = 'scripted_zero_api'
    else:
        require(config['completion_protocol'] == 'physics_verifier' and set(result['graph_states']) == PARTS,
                'missing live graph evidence')
        pick_times, handoffs, targets = _decisions(folder, config, result, order)
        policy_provenance = 'completed_live_provider_turns'
    samples = _trace(folder, order, pick_times, result)
    data = restore_episode_data(model, boundary)
    require(episode_boundary(model, data, registry, installed) == boundary, 'restored integration state differs')
    cover = FrameSkills(model, data, 'cover', registry, installed)
    cover.verifier.mating_enabled = True
    row = cover.verifier.sample(data, cover.motion.reference, allow_support=True, allow_grasp=False)
    terminal = cover.insertion_evidence([row])
    cover.verifier.verify_other_parts(data)
    fit = terminal['cover_fit']
    require(terminal['checks'] == final['terminal']['checks'] and all(ok is True for ok in terminal['checks'].values())
            and fit == final['terminal']['cover_fit']
            and set(fit['bores']) == POSTS and set(fit['vertical_normal_load_n']) == SLEEVES
            and all(math.isfinite(n) and n > .01 for n in fit['vertical_normal_load_n'].values()),
            'three independent cover supports or bore measurements missing')
    require(set(final['sleeve_fits']) == SLEEVES, 'missing terminal sleeve fit')
    for name in sorted(SLEEVES):
        sleeve_fit = FrameSkills(model, data, name, registry, installed).verify_sleeve_fit()
        post = 'post_' + name.split('_')[1]
        require(sleeve_fit == final['sleeve_fits'][name] and sleeve_fit['post_id'] == post,
                'restored sleeve does not engage its matching post: ' + name)
        for measured in (sleeve_fit, fit['bores'][post]):
            require(measured['post_id'] == post and measured['minimum_bore_side_clearance_mm'] > 0
                    and measured['minimum_post_extension_past_openings_mm'] > 0, 'bore clearance or overlap missing for ' + post)
    require(episode_boundary(model, data, registry, installed) == boundary, 'terminal inspection changed simulation state')
    return {'schema': 'triangle_frame_run_audit/v1', 'episode': folder.name, 'product_layout': LAYOUT,
            'policy_provenance': policy_provenance, 'installed': len(installed),
            'native_disassembly_order': provenance['disassembly_order'], 'verified_assembly_order': order,
            'candidate_sha256': provenance['candidate_sha256'], 'source_report_sha256': provenance['source_report_sha256'],
            'changed_meshes': changed, 'original_trace_samples': samples, 'handoffs_by_part': handoffs,
            'audited_sleeve_search_targets': targets, 'provider_calls': result['api_calls'],
            'cover_support_load_n': fit['vertical_normal_load_n'], 'cover_bores': fit['bores']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path)
    parser.add_argument('--planner-candidate', type=Path, required=True)
    parser.add_argument('--physics-only', action='store_true', help='Audit an explicitly scripted zero-API execution.')
    args = parser.parse_args()
    print(json.dumps(audit(args.run_dir, planner_candidate=args.planner_candidate, physics_only=args.physics_only), ensure_ascii=False, indent=2, allow_nan=False))
