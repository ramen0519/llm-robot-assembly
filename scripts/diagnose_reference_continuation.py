"""Continue an archived reference state twice; no policy, API or assembly action."""
import argparse
import importlib.util
import json
import math
from pathlib import Path
import time
import mujoco
import numpy as np
from assembly_v12.contracts import read,write,sha,digest,location
from assembly_v12.media import STATE,state
from assembly_v2.budget import Budget
from assembly_v2.skills import MeshSkills
from assembly_v2.verification import pose_diagnostics
from assembly_v2.completion import ObservationWindow,load_profile
from assembly_v2.readiness import _image


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--seconds',type=float,default=900)
    args=parser.parse_args();started=time.monotonic();budget=Budget(args.seconds)
    source=location(args.source_run);out=location(args.output);out.mkdir(parents=True,exist_ok=False)
    files=['prepared.json','reference_boundary_states.npz','reference_model.mjb',
           'reference_verification.json','reference_diagnostics.json','code_versions.json','result.json']
    original_hashes={n:sha(source/n) for n in files}
    prepared=read(source/'prepared.json');old=read(source/'reference_verification.json')
    prior=read(source/'reference_diagnostics.json');versions=read(source/'code_versions.json')
    if prepared['completion_profile']!=load_profile():raise ValueError('Original completion profile differs.')
    if versions['packages']['mujoco']!=mujoco.__version__:raise ValueError('Use the original MuJoCo version.')
    if old['model_sha256']!=original_hashes['reference_model.mjb']:raise ValueError('Source model hash mismatch.')
    if old['asset_hash']!=prepared['asset_hash']:raise ValueError('Source asset identity mismatch.')
    repo=Path(__file__).resolve().parents[1]
    matching={n:sha(repo/n)==versions['files'][n] for n in
              ['scripts/workcell_motion.py','scripts/move_tcp.py','scripts/assembly_v12/media.py',
               'scripts/assembly_v2/skills.py','scripts/assembly_v2/completion.py']}
    if not all(matching.values()):raise ValueError('Controller, state or measurement implementation changed.')
    model=mujoco.MjModel.from_binary_path(str(source/'reference_model.mjb'))
    saved=np.load(source/'reference_boundary_states.npz',allow_pickle=False)['states']
    if saved.shape!=(2,mujoco.mj_stateSize(model,STATE)) or not np.isfinite(saved).all():
        raise ValueError('Incomplete integration state: replay from the initial source is required.')
    if model.opt.timestep!=.002 or not np.array_equal(model.opt.gravity,[0,0,-9.81]):raise ValueError('Physical settings changed.')
    first=mujoco.MjData(model);mujoco.mj_setState(model,first,saved[0],STATE);mujoco.mj_forward(model,first)
    if not np.array_equal(state(model,first),saved[0]):raise ValueError('Initial forward recomputation changed integration state.')
    reference=first.qpos[:6].copy()
    archived=source/'replay_bundle/implementation/scripts/assembly_v2/verification.py'
    if sha(archived)!=versions['files']['scripts/assembly_v2/verification.py']:raise ValueError('Archived verifier mismatch.')
    spec=importlib.util.spec_from_file_location('assembly_v2._archived_verifier_diagnostic',archived)
    original_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(original_module)
    restoration={'schema':'reference_state_restoration/v1','state_spec':'mjSTATE_INTEGRATION','state_mask':int(STATE),
        'state_size':mujoco.mj_stateSize(model,STATE),'fields':{},'source_hashes':original_hashes,
        'mujoco_version':mujoco.__version__,'original_controller_code_matches':matching,
        'controller_reference_source':'initial saved frame qpos[:6], as the original WorkcellMotion constructor',
        'controller_reference_rad':reference.tolist(),'gripper_command':0.,'held_pose':None,
        'no_integral_controller_state':True,'original_reference_result':old['status'],
        'prepared_sha256':original_hashes['prepared.json'],'branches':[]}
    for name,flag in mujoco.mjtState.__members__.items():
        value=int(flag)
        if value>0 and value&(value-1)==0:
            restoration['fields'][name]={'included':bool(int(STATE)&value),'size':mujoco.mj_stateSize(model,flag)}
    ids=prepared['order'];allids=sorted(prepared['parts'],key=lambda x:int(x) if x.isdigit() else x)
    limits=prepared['completion_profile']['global_completion'];benchmark=[];branch_states=[];branch_metrics=[]
    for label in ('continuation_a','continuation_b'):
        budget.check(label);folder=out/label;folder.mkdir()
        data=mujoco.MjData(model);mujoco.mj_setState(model,data,saved[1],STATE)
        exact=np.array_equal(state(model,data),saved[1]);mujoco.mj_forward(model,data)
        forwarded=np.array_equal(state(model,data),saved[1])
        if not exact or not forwarded:raise ValueError('Restore failed: must compare against initial replay.')
        if not math.isclose(data.time,2.,abs_tol=1e-8):raise ValueError('Source state is not at 2 s.')
        skills=MeshSkills(model,data,prepared,ids[0],set(ids),{},budget)
        drift=float(np.max(np.abs(skills.motion.reference-reference)))
        skills.motion.reference=reference.copy();skills.motion.gripper_command=0.
        skills.verifier.reference_mode=True;skills.retreat_complete=True
        skills.stage('followup_reference_2_to_3_seconds')
        initial_measured=pose_diagnostics(skills)
        delta={'linear_speed_m_s':0.,'angular_speed_rad_s':0.,'pose_element':0.}
        for pid,row in initial_measured.items():
            previous=prior['stopped_parts'][pid]
            for key in ('linear_speed_m_s','angular_speed_rad_s'):delta[key]=max(delta[key],abs(row[key]-previous[key]))
            delta['pose_element']=max(delta['pose_element'],float(abs(np.asarray(row['current_pose'])-previous['current_pose']).max()))
        restoration['branches'].append({'name':label,'state_roundtrip_exact':exact,'forward_preserves_state':forwarded,
            'difference_from_original_2s_measurements':delta,'naive_recreated_controller_reference_drift_rad':drift})
        window=ObservationWindow(ids,prepared['completion_profile'],check_id=out.name+':'+label,
            source='reference_scene',model_sha256=original_hashes['reference_model.mjb'],asset_hash=prepared['asset_hash'],
            operations={pid:prepared['parts'][pid]['operation'] for pid in ids})
        stats={pid:{'fixed_base':pid==prepared['base_id'],'samples':0,'maximum_linear_speed_m_s':0.,
            'maximum_angular_speed_rad_s':0.,'maximum_position_error_xyz_m':[0.,0.,0.],
            'maximum_orientation_error_rad':0.,'linear_violation_times_s':[],'angular_violation_times_s':[],
            'linear_peak_time_s':None,'angular_peak_time_s':None} for pid in allids}
        states=[];metric_trace=[]
        with (folder/'samples.jsonl').open('w',encoding='utf8') as trace:
            for index in range(501):
                if index:skills.motion.step(allow_support=True,allow_grasp=False)
                else:skills.motion.sample(allow_support=True,allow_grasp=False)
                measured=skills.verifier.measurements(data,ids,released=True,retreat_complete=True,prealignment={})
                window.add(float(data.time),measured)
                all_measured=pose_diagnostics(skills)
                entry={'index':index,'simulation_time_s':float(data.time),'parts':all_measured,
                       'hard_penetration_m':skills.verifier.last_contacts['hard_penetration_m']}
                trace.write(json.dumps(entry,ensure_ascii=True)+'\n');metric_trace.append(entry);states.append(state(model,data))
                for pid,row in all_measured.items():
                    stat=stats[pid];stat['samples']+=1;t=float(data.time)
                    for key,prefix in [('linear_speed_m_s','linear'),('angular_speed_rad_s','angular')]:
                        metric='maximum_'+key
                        if row[key]>=stat[metric]:stat[metric]=row[key];stat[prefix+'_peak_time_s']=t
                        if row[key]>limits[prefix+'_speed_max_'+('m_s' if prefix=='linear' else 'rad_s')]+1e-12:
                            stat[prefix+'_violation_times_s'].append(t)
                    stat['maximum_position_error_xyz_m']=np.maximum(stat['maximum_position_error_xyz_m'],row['position_error_base_xyz_m']).tolist()
                    stat['maximum_orientation_error_rad']=max(stat['maximum_orientation_error_rad'],row['orientation_error_rad'])
                if label=='continuation_a' and index in (0,250,500):
                    baseline=original_module.MeshVerifier(model,prepared,ids[0],set(ids),{})
                    baseline.reference_mode=True
                    cases=[];data_before=state(model,data)
                    for mode,verifier in [('archived',baseline),('same_call_cache',skills.verifier)]:
                        calls=[0];method=verifier.near_goal
                        def counted(d,p,_method=method):calls[0]+=1;return _method(d,p)
                        verifier.near_goal=counted;tick=time.perf_counter()
                        answer=verifier.contacts(data,allow_support=True,allow_grasp=False)
                        duration=time.perf_counter()-tick;verifier.near_goal=method
                        cases.append({'mode':mode,'wall_s':duration,'pose_calculations':calls[0],
                            'evidence_sha256':digest({'answer':answer,'contacts':verifier.last_contacts})})
                    same=cases[0]['evidence_sha256']==cases[1]['evidence_sha256']
                    benchmark.append({'time_s':float(data.time),'contacts':data.ncon,'cases':cases,
                        'identical_decisions_and_contact_evidence':same,'integration_state_unchanged':np.array_equal(data_before,state(model,data))})
                    if not same or not benchmark[-1]['integration_state_unchanged']:raise ValueError('Contact cache changed evidence/state.')
                if index%100==0:print(label,index,'time',round(data.time,3),'elapsed_s',round(time.monotonic()-started,1),flush=True)
        evidence=window.finish();evidence.update(diagnostic_only=True,original_result_unchanged=True,
            original_settling_profile_not_modified=True,followup_window_s=[2.,3.])
        write(folder/'verification.json',evidence)
        for stat in stats.values():stat['speed_limits_satisfied_entire_window']=not stat['linear_violation_times_s'] and not stat['angular_violation_times_s']
        write(folder/'part_statistics.json',stats)
        np.savez_compressed(folder/'integration_states.npz',states=np.array(states),state_spec=int(STATE))
        if label=='continuation_a':_image(model,data,prepared,folder/'at_3s.png')
        branch_states.append(np.array(states));branch_metrics.append(metric_trace)
    equal=np.array_equal(branch_states[0],branch_states[1]);metrics_equal=digest(branch_metrics[0])==digest(branch_metrics[1])
    restoration.update(reproduced_all_501_states_exactly=equal,reproduced_all_501_measurements_exactly=metrics_equal,
        maximum_absolute_state_difference=float(np.max(abs(branch_states[0]-branch_states[1]))),
        missing_integration_fields=[],initial_to_2s_replay='not_needed: full state present, exact roundtrip, original 2s measurements compared, identical two continuations')
    unchanged={n:sha(source/n)==original_hashes[n] for n in files}
    write(out/'restoration.json',restoration);write(out/'same_frame_cache_comparison.json',benchmark)
    summary={'schema':'reference_continuation_diagnostic/v1','source_run':str(source),'source':'reference_scene',
        'window_s':[2.,3.],'samples_per_part':501,'part_count_including_fixed_base':len(allids),
        'velocity_reference':'declared CAD part-frame origin, not COM','status':evidence['status'],
        'reproducible':equal and metrics_equal,'parts':stats,'source_files_unchanged':unchanged,
        'api_calls':0,'cost_usd':0,'robot_assembly':'not_run','memory_registered':0,
        'original_1_to_2s_status':old['status'],'completion_profile_unchanged':True,
        'elapsed_s':time.monotonic()-started}
    write(out/'summary.json',summary)
    print(json.dumps({k:v for k,v in summary.items() if k not in ('parts','source_files_unchanged')},ensure_ascii=True),flush=True)
    return 0 if equal and metrics_equal and all(unchanged.values()) else 2


if __name__=='__main__':raise SystemExit(main())
