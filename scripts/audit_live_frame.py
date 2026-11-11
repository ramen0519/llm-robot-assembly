"""Read-only audit of a fresh nine-installation live frame episode; no API or motion."""
import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path


def audit_search(folder, part, state, phase):
    from run_frame_assembly import verify_interference_recovery
    verify_interference_recovery(state, phase['executed_approach'])
    recovery = folder/'grasp_recovery'/part
    def read(relative):
        return json.loads((recovery/relative).read_text(encoding='utf-8'))
    receipt = read('decision_receipt.json')
    selector = read('selector/result.json')
    assert selector['transport'] == 'openai_responses' and selector['status'] == 'selected'
    assert receipt == state['grasp_recovery_receipt'] and receipt['selected_id'] == selector['selected_id']
    assert read('evaluation.json')['proposal']['code_sha256'] == receipt['code_sha256']
    assert selector['API_calls'] == receipt['API_calls'] == len(selector['events'])
    read_ids, hits = set(), set()
    for i, event in enumerate(selector['events'], 1):
        response = read(f'selector/response_{i}.json')
        calls = [item for item in response['output'] if item['type'] == 'function_call']
        assert response['status'] == 'completed' and len(calls) == 1
        assert calls[0]['name'] == event['tool'] and json.loads(calls[0]['arguments']) == event['arguments']
        if event['tool'] == 'search_library':
            hits.update(card['document_id'] for card in event['results'])
        elif event['tool'] == 'read_documents':
            ids = event['arguments']['document_ids']
            assert set(ids) <= hits
            read_ids.update(ids)
        else:
            assert i == len(selector['events']) and event['tool'] == 'select_skill'
            assert event['arguments']['document_id'] == receipt['selected_id'] in read_ids
    assert read('trial_01/source_before.json') == read('trial_01/source_after.json') == read('trial_02/source_before.json') == read('trial_02/source_after.json')
    assert read('trial_01/trial.json')['accepted'] is False and read('trial_02/trial.json')['accepted'] is True
    print(part, 'verified recovery', receipt['selected_id'], receipt['proposed_grasp'], 'calls', selector['API_calls'])
    return selector['API_calls']


def audit(run_dir, *, planner_candidate=None):
    if not __debug__:
        raise RuntimeError('Run the audit without -O or PYTHONOPTIMIZE; checks must stay enabled.')
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root/'scripts'))
    folder = Path(run_dir)
    r = json.loads((folder/'result.json').read_text(encoding='utf-8'))
    c = json.loads((folder/'resolved_config.json').read_text(encoding='utf-8'))
    print('status',r['status'],r['error'],'calls',r['api_calls'])
    if r['status'] != 'success':
        print('completed',r['completed_installations'],r.get('llm_usage'))
        for name,state in r['graph_states'].items():
            print(name, state['status'], [(x['grasp'],x['accepted'],x.get('phase'),x.get('failure_code')) for x in state['grasp_trials']])
        sys.exit(1)
    sleeve_names = [f'sleeve_{i}' for i in range(1,5)]
    default_order = [*(f'post_{i}' for i in range(1,5)),*sleeve_names,'cover']
    expected = default_order
    if c.get('planner_provenance') is not None or planner_candidate is not None:
        from planner_candidate_input import load_robot_candidate
        if planner_candidate is None:
            raise ValueError('Supply the original --planner-candidate to audit this external-plan run.')
        expected, provenance = load_robot_candidate(planner_candidate, c['registry'])
        assert c['planner_provenance'] == r['planner_provenance'] == provenance
        assert expected == list(reversed(provenance['disassembly_order'])) == r['requested_sequence']
        assert provenance['fixed_base'] == 'base' and set(expected) == set(default_order)
    assert r['execution_order']==expected and r['robot_installed_parts']==9 and r['ten_part_assembly_complete']
    assert c['sequence']==expected and c['policy_kind']=='openai_responses'
    assert r.get('resume') is None, 'This audit requires a fresh episode without checkpoint resume.'
    assert c['initial_installed']==[]
    assert c.get('completion_protocol')=='physics_verifier'
    assert (folder/'before_assembly.png').stat().st_size>1000
    validated=[e for line in (folder/'llm_events.jsonl').read_text(encoding='utf-8').splitlines() if (e:=json.loads(line))['event']=='proposal_validated']
    fixed_trigger = c.get('fixed_first_grasp_trigger') is True
    search_calls = (audit_search(folder, 'post_1', r['graph_states']['post_1'], r['part_episodes']['post_1'])
                    if fixed_trigger else 0)
    assert len(validated)+search_calls==r['api_calls']
    assert all(e['response_id'] and e['summary']['requested_model']==c['llm_config']['model'] for e in validated)
    assert r['llm_usage']['unknown_usage_requests']==0
    assert len(validated)+int(fixed_trigger)==r['policy_calls']
    assert r['api_calls']<=c['llm_config']['max_model_requests']
    assert r['llm_usage']['known_api_cost_estimate_usd']<=float(c['llm_config']['max_cost_usd'])
    assert r['llm_usage']['input_tokens']+r['llm_usage']['output_tokens']<=c['llm_config']['max_total_tokens']
    previous=None
    for name in expected:
        phase=r['part_episodes'][name]
        if previous is not None:
            assert phase['start']==previous['end']
            assert phase['api_calls_at_start']==previous['api_calls_at_end']
        else:
            assert phase['start']['time_s']==0 and phase['start']['installed']==[]
            for part,pose in phase['start']['parts'].items():
                assert pose['position_world_m']!=c['registry'][part]['joint']['target_part_center_world_m']
        previous=phase
        state=r['graph_states'][name]
        approved=state['grasp_trials'][-1]
        assert state['status']=='success' and approved['accepted']
        assert approved['grasp']==phase['executed_approach']['proposed_grasp']
        responses=[e['proposal'] for e in validated if e['proposal']['part_id']==name]
        picks=[p for p in responses if p['skill']=='pick']
        recovered = fixed_trigger and name == 'post_1'
        assert len(responses)+(search_calls if recovered else 0)==phase['api_calls_at_end']-phase['api_calls_at_start']
        if recovered:
            assert approved['grasp']==state['grasp_recovery_receipt']['proposed_grasp']
        else:
            assert picks[-1]['grasp']==approved['grasp']
        if c.get('completion_protocol')=='physics_verifier':
            assert responses[-1]['skill']=='insert'
            assert state['completion_source']=='physics_verifier'
            assert state['completed']==['pick','insert']
            assert not any(p['skill']=='finish' for p in responses)
        else:
            assert responses[-1]['skill']=='finish'
        assert all(state['verification']['checks'].values())
        result=next(item for item in r['results'] if item['part_id']==name)
        assert all(result['checks'].values())
        print(name,'grasp',approved['grasp'],'trials',len(state['grasp_trials']),
              'max_final_error_mm',result['maximum_final_position_error_m']*1000,
              'API',phase['api_calls_at_end']-phase['api_calls_at_start'],
              'time',phase['start']['time_s'],phase['end']['time_s'])
        assert (folder/f'after_{name}.png').stat().st_size>1000
    final=r['final_verification']
    assert final['boundary']==previous['end'] and final['all_installed_parts_preserved']
    if c.get('completion_protocol')=='physics_verifier':
        assert r['terminal_checkpoint']==final['boundary'] and r['checkpoint_error'] is None
    from frame_assembly_scene import build_frame_scene
    from run_frame_assembly import restore_episode_data, episode_boundary
    model,_,registry=build_frame_scene(10)
    restored=restore_episode_data(model,final['boundary'])
    assert episode_boundary(model,restored,registry,set(expected))==final['boundary']
    print('Final physical checkpoint restored exactly (audit only, not a new assembly).')
    from frame_assembly_skills import FrameSkills
    check=FrameSkills(model,restored,'cover',registry,set(expected))
    # Restore the terminal inspection mode: declared mating contacts are expected.
    check.verifier.mating_enabled=True
    row=check.verifier.sample(restored,check.motion.reference,allow_support=True,allow_grasp=False)
    fresh=check.insertion_evidence([row])
    assert all(fresh['checks'].values()) and fresh['cover_fit']==final['terminal']['cover_fit']
    for name in sleeve_names:
        sleeve_check=FrameSkills(model,restored,name,registry,set(expected))
        assert sleeve_check.verify_sleeve_fit()==final['sleeve_fits'][name]
    assert episode_boundary(model,restored,registry,set(expected))==final['boundary']
    print('Restored-state independent physical cover and sleeve checks passed without motion.')
    assert set(final['sleeve_fits'])==set(sleeve_names)
    assert len(final['terminal']['cover_fit']['bores'])==4
    for fit in [*final['sleeve_fits'].values(),*final['terminal']['cover_fit']['bores'].values()]:
        assert fit['minimum_bore_side_clearance_mm']>0 and fit['minimum_post_extension_past_openings_mm']>0
    assert all(v>.01 for v in final['terminal']['cover_fit']['vertical_normal_load_n'].values())
    print('final maximum error mm',max(v['position_error_m'] for v in final['parts'].values())*1000)
    print('sleeve minimum clearance mm',min(v['minimum_bore_side_clearance_mm'] for v in final['sleeve_fits'].values()))
    print('cover minimum clearance mm',min(v['minimum_bore_side_clearance_mm'] for v in final['terminal']['cover_fit']['bores'].values()))
    print('cover support loads N',final['terminal']['cover_fit']['vertical_normal_load_n'])
    print('wall elapsed seconds',r['wall_elapsed_s'])
    print('cost:',r['llm_usage'])
    counts=defaultdict(int)
    stages=defaultdict(list)
    previous_time=None
    row_count=0
    with (folder/'skill_trace.csv').open(newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            moment=float(row['simulation_time_s'])
            if previous_time is not None:
                assert 0<=moment-previous_time<=.00200001
            previous_time=moment
            name=row['active_part_id']
            counts[name]+=1
            if not stages[name] or stages[name][-1]!=row['stage']:
                stages[name].append(row['stage'])
            row_count+=1
    assert row_count==r['samples']
    for name in expected:
        assert counts[name]>100
        for stage in ('pick_close','pick_lift','insert_lower','insert_release','insert_retreat'):
            assert stages[name].count(stage)==1,(name,stage,stages[name])
    print('trace rows',row_count,'by part',dict(counts),'simulation seconds',r['simulated_seconds'])
    assert all(hashlib.sha256((root/'scripts'/f).read_bytes()).hexdigest()==h for f,h in c['implementation_sha256'].items())
    print('source hashes',len(c['implementation_sha256']))
    if c.get('orchestration') == 'roles' and fixed_trigger:
        from audit_agent_handoffs import audit_agent_handoffs
        print('verified agent handoffs and usage', audit_agent_handoffs(folder,r,c))
    print('audit_source_sha256',hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    print('independent_audit_passed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path, help='A completed fresh live frame run with snapshots')
    parser.add_argument('--planner-candidate', type=Path, help='Native candidate and adjacent source artifacts to recheck')
    args = parser.parse_args()
    audit(args.run_dir, planner_candidate=args.planner_candidate)
