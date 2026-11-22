"""Audit an existing recorded run. This never executes a robot or a model."""
import argparse
from pathlib import Path
import numpy as np
from assembly_v12.contracts import read,sha,digest,write,check_export
from assembly_v12.memory import verified_installation
from assembly_v12.metrics import records


def audit(folder):
    folder=Path(folder);r=read(folder/'result.json');checks={}
    if (folder/'input/export_v1.json').is_file():
        check_export(folder/'input/export_v1.json');checks['input_selected_chain']=True
    events=records(folder/'events.jsonl')
    checks['unique_actions']=len({e['action_id'] for e in events})==len(events)
    revisions=[e['observation_revision'] for e in events]
    checks['monotone_revisions']=all(a<b for a,b in zip(revisions,revisions[1:]))
    checks['physical_installation_evidence']=all(verified_installation(e) for e in events if e.get('installation_verified'))
    installed=[e['part_id'] for e in events if verified_installation(e)]
    checks['installed_list_matches_evidence']=installed==r['installed']
    if r['status']=='success':checks['all_required_installed']=installed==r['required']
    if (folder/'replay_frames.json').is_file():
        index=read(folder/'replay_frames.json');data=np.load(folder/'replay_states.npz',allow_pickle=False)['states']
        checks['replay_model_hash']=sha(folder/'model.mjb')==index['model_sha256']
        if index.get('states_sha256'):checks['replay_states_hash']=sha(folder/'replay_states.npz')==index['states_sha256']
        checks['replay_state_count']=len(data)==len(index['frames'])
        checks['replay_source']=all(e['source']=='live_simulation' for e in index['frames'])
        times=[e['simulation_time_s'] for e in index['frames']]
        checks['no_time_reset']=all(a<=b for a,b in zip(times,times[1:]))
        checks['finite_states']=bool(np.isfinite(data).all())
        if r.get('initial_state_sha256'):checks['initial_state_hash']=digest(data[0].tolist())==r['initial_state_sha256']
        if r.get('final_state_sha256'):checks['final_state_hash']=digest(data[-1].tolist())==r['final_state_sha256']
    for path in (folder/'failures').glob('*.json'):
        f=read(path)
        if f.get('png'):checks['image_'+path.stem]=sha(folder/f['png'])==f['png_sha256']
        if f.get('state_sha256'):checks['state_'+path.stem]=sha(folder/f['state'])==f['state_sha256']
    if r.get('media',{}).get('status')=='recorded_states_rendered':
        checks['video_hash']=sha(folder/r['media']['video'])==r['media']['sha256']
    result={'status':'pass' if all(checks.values()) else 'fail','checks':checks,'run':r['run_id'],
            'audit_not_new_physics':True,'policy_source':r['policy_source'],'actual_api_calls':r['api_calls']}
    write(folder/'artifact_audit.json',result)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('run');a=p.parse_args()
    result=audit(a.run);print(__import__('json').dumps(result,ensure_ascii=False))
    raise SystemExit(0 if result['status']=='pass' else 1)
