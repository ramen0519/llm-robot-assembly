"""Read-only audit of a fresh nine-installation live frame episode; no API or motion."""
import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path


def audit(run_dir):
    if not __debug__:
        raise RuntimeError('Run the audit without -O or PYTHONOPTIMIZE; checks must stay enabled.')
    root = Path(__file__).resolve().parents[1]
    folder = Path(run_dir)
    r = json.loads((folder/'result.json').read_text(encoding='utf-8'))
    c = json.loads((folder/'resolved_config.json').read_text(encoding='utf-8'))
    print('status',r['status'],r['error'],'calls',r['api_calls'])
    if r['status'] != 'success':
        print('completed',r['completed_installations'],r.get('llm_usage'))
        for name,state in r['graph_states'].items():
            print(name, state['status'], [(x['grasp'],x['accepted'],x.get('phase'),x.get('failure_code')) for x in state['grasp_trials']])
        sys.exit(1)
    expected=[*(f'post_{i}' for i in range(1,5)),*(f'sleeve_{i}' for i in range(1,5)),'cover']
    assert r['execution_order']==expected and r['robot_installed_parts']==9 and r['ten_part_assembly_complete']
    assert c['sequence']==expected and c['policy_kind']=='openai_responses'
    assert r.get('resume') is None, 'This audit requires a fresh episode without checkpoint resume.'
    assert c['initial_installed']==[]
    assert c.get('completion_protocol')=='physics_verifier'
    assert (folder/'before_assembly.png').stat().st_size>1000
    validated=[e for line in (folder/'llm_events.jsonl').read_text(encoding='utf-8').splitlines() if (e:=json.loads(line))['event']=='proposal_validated']
    assert len(validated)==r['api_calls']
    assert all(e['response_id'] and e['summary']['requested_model']==c['llm_config']['model'] for e in validated)
    assert r['llm_usage']['unknown_usage_requests']==0
    assert r['api_calls']==r['policy_calls']<=c['llm_config']['max_model_requests']
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
        assert len(responses)==phase['api_calls_at_end']-phase['api_calls_at_start']
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
    sys.path.insert(0,str(root/'scripts'))
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
    for name in expected[4:8]:
        sleeve_check=FrameSkills(model,restored,name,registry,set(expected))
        assert sleeve_check.verify_sleeve_fit()==final['sleeve_fits'][name]
    assert episode_boundary(model,restored,registry,set(expected))==final['boundary']
    print('Restored-state independent physical cover and sleeve checks passed without motion.')
    assert set(final['sleeve_fits'])==set(expected[4:8])
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
    print('audit_source_sha256',hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    print('independent_audit_passed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path, help='A completed fresh live frame run with snapshots')
    audit(parser.parse_args().run_dir)
