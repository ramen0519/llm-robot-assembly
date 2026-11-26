"""Isolated native timestep sensitivity probe; never assembly-success evidence.

Uses the same ArmPositionController and MeshVerifier as production while holding
the authenticated controller references fixed. Production timestep guards and
completion profiles remain untouched. No policy/API or physical hardware.
"""
from pathlib import Path
from copy import deepcopy
import argparse, json, math, sys, time
import mujoco
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from assembly_v12.contracts import read, write, digest, sha
from assembly_v12.media import STATE, state
from assembly_v2.budget import Budget
from assembly_v2.verification import MeshVerifier
from assembly_v2.scene import body_name
from assembly_v2.transforms import body_pose
from move_tcp import ArmPositionController
from simulation_warnings import checked_physics
from failure_report import FailureEvent, json_safe


def case(source, checkpoint, output, timestep, seconds, repeat, deadline):
    out=output/f'dt_{timestep:.4f}_r{repeat}';out.mkdir()
    p=read(source/'prepared.json');c=read(source/checkpoint)
    if c.get('source')!='live_simulation':raise ValueError('Use an authenticated actual simulation checkpoint.')
    if sha(source/'model.mjb')!=c['model_sha256'] or sha(source/c['state'])!=c['state_sha256'] or digest(p)!=c['prepared_sha256']:
        raise ValueError('Checkpoint identity mismatch.')
    m=mujoco.MjModel.from_binary_path(str(source/'model.mjb'));d=mujoco.MjData(m)
    saved=np.load(source/c['state'],allow_pickle=False)
    mujoco.mj_setState(m,d,saved,STATE);mujoco.mj_forward(m,d)
    if not np.array_equal(saved,state(m,d)):raise ValueError('Original integration state did not restore exactly.')
    original_dt=float(m.opt.timestep);m.opt.timestep=timestep
    with checked_physics('diagnostic_forward_after_timestep_change'):mujoco.mj_forward(m,d)
    original_integration_preserved=bool(np.array_equal(saved,state(m,d)))
    controller=ArmPositionController(m)
    verifier=MeshVerifier(m,p,c['part_id'],set(c['installed']),deepcopy(c['settled']))
    held=c['controller']['held_pose'] is not None
    # Released checkpoint is from the known installation/retreat phase.
    verifier.mating=not held;verifier.stage='isolated_timestep_hold'
    reference=np.asarray(c['controller']['reference']);gripper=c['controller']['gripper_command']
    slip=c['controller'].get('slip_reference') or c['controller'].get('held_pose')
    slip=None if slip is None else tuple(np.asarray(v) for v in slip)
    ids=list(p['parts']);times=[];poses=[];q=[];samples=[];violations=[]
    initial_time=float(d.time)
    result={'schema':'timestep_sensitivity/v1','source':'isolated_physics_diagnostic',
        'source_run':str(source),'checkpoint':checkpoint,'source_model_sha256':c['model_sha256'],
        'source_state_sha256':c['state_sha256'],'source_prepared_sha256':c['prepared_sha256'],
        'checkpoint_restored_exact':True,'state_unchanged_after_dt_forward':original_integration_preserved,
        'original_timestep_s':original_dt,'experimental_timestep_s':timestep,'repeat':repeat,
        'requested_simulation_seconds':seconds,'held':held,'part_id':c['part_id'],'part_ids':ids,
        'controller_reference':'original fixed arm reference and gripper command; updated each physics step',
        'production_guard_changed':False,'assembly_success_claim':False,'completion_profile_evaluated':False,
        'api_calls':0,'source_files_or_motion_policy_modified':False,
        'experimental_model_overrides':{'opt.timestep':timestep},'status':'running'}
    write(out/'result.json',result)
    def observe():
        row=verifier.sample(d,reference,allow_support=True,allow_grasp=held)
        if held:verifier.check_held(d,row,slip)
        times.append(float(d.time)-initial_time)
        poses.append([body_pose(d,body_name(pid,p['base_id'])) for pid in ids]);q.append(d.qpos[:6].copy())
        samples.append(row)
    start=time.perf_counter();completed=0
    try:
        observe()
        for _ in range(round(seconds/timestep)):
            deadline.check('timestep_probe')
            before=float(d.time)
            with checked_physics('gravity_feedforward'):controller.apply(d,reference,gripper)
            with checked_physics('mj_step'):mujoco.mj_step(m,d)
            with checked_physics('mj_forward_measurement'):mujoco.mj_forward(m,d)
            completed+=1
            if not math.isclose(float(d.time),before+timestep,abs_tol=1e-8):raise ValueError('Simulation time changed unexpectedly.')
            observe()
        result['status']='short_hold_checks_passed'
    except Exception as exc:
        result['status']='stopped'
        result['failure']={'type':type(exc).__name__,'code':exc.code.value if isinstance(exc,FailureEvent) else None,
            'message':str(exc),'simulation_elapsed_s':float(d.time)-initial_time,
            'violations':json_safe(getattr(exc,'violations',[]))}
    elapsed=time.perf_counter()-start
    np.save(out/'end_state.npy',state(m,d),allow_pickle=False)
    np.savez_compressed(out/'trace.npz',elapsed_s=times,part_poses=poses,arm_qpos=q)
    write(out/'samples.json',json_safe(samples))
    result.update(wall_seconds=elapsed,physics_steps=completed,checked_samples=len(samples),
        actual_simulation_seconds=float(d.time)-initial_time,end_state_sha256=sha(out/'end_state.npy'))
    fields=('hard_penetration_m','penetration_m','relative_slip_m','relative_rotation_rad',
            'part_speed_m_s','part_angular_speed_rad_s','tracking_error_rad')
    result['observed_maxima']={key:max(float(s[key]) for s in samples if key in s) for key in fields if any(key in s for s in samples)}
    result['all_observed_samples_bilateral_contact']=all(s.get('left_contact') and s.get('right_contact') for s in samples) if held else None
    write(out/'result.json',result)
    print(json.dumps({k:result[k] for k in ('experimental_timestep_s','repeat','status','wall_seconds','physics_steps')},ensure_ascii=False),flush=True)
    return result,out


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source-run',type=Path,required=True);ap.add_argument('--checkpoint',required=True)
    ap.add_argument('--output',type=Path,required=True);ap.add_argument('--simulation-seconds',type=float,default=1.)
    ap.add_argument('--timesteps',type=float,nargs='+',default=[.002,.004]);ap.add_argument('--repeats',type=int,default=2)
    ap.add_argument('--wall-seconds',type=float,default=120.)
    a=ap.parse_args()
    if not (0<a.simulation_seconds<=2 and 1<=a.repeats<=3 and 1<=a.wall_seconds<=180):ap.error('Bounded diagnostic only: <=2 simulated seconds, 1..3 repeats, <=180 wall seconds.')
    if a.timesteps[0]!=.002 or len(set(a.timesteps))!=len(a.timesteps) or any(dt not in (.001,.002,.004,.010) for dt in a.timesteps):ap.error('Baseline .002 first; isolated diagnostic values .001/.002/.004/.010 only; not production qualification.')
    if any(not math.isclose(a.simulation_seconds/dt,round(a.simulation_seconds/dt),abs_tol=1e-9) for dt in a.timesteps):ap.error('Duration must be divisible by every timestep.')
    a.output.mkdir(parents=True,exist_ok=False);deadline=Budget(a.wall_seconds);rows=[]
    for repeat in range(1,a.repeats+1):
        for dt in a.timesteps if repeat%2 else list(reversed(a.timesteps)):
            result,out=case(a.source_run,a.checkpoint,a.output,dt,a.simulation_seconds,repeat,deadline)
            rows.append({**result,'folder':out.name})
    paired=[]
    for repeat in range(1,a.repeats+1):
        base=next(r for r in rows if r['repeat']==repeat and r['experimental_timestep_s']==.002)
        for r in rows:
            if r['repeat']!=repeat or r['experimental_timestep_s']==.002:continue
            item={'repeat':repeat,'baseline_dt':.002,'candidate_dt':r['experimental_timestep_s']}
            if base['status']==r['status']=='short_hold_checks_passed':
                b=np.load(a.output/base['folder']/'trace.npz');v=np.load(a.output/r['folder']/'trace.npz')
                # Compare only observed equal physical times, no invented interpolation.
                common=[]
                for j,t in enumerate(v['elapsed_s']):
                    i=int(round(t/.002))
                    if 0<=i<len(b['elapsed_s']) and abs(b['elapsed_s'][i]-t)<1e-8:common.append((i,j))
                ix,iy=zip(*common);pb=b['part_poses'][list(ix)];pv=v['part_poses'][list(iy)]
                xyz=np.max(np.abs(pb[...,:3,3]-pv[...,:3,3]),axis=0)
                rel=np.einsum('...ji,...jk->...ik',pb[...,:3,:3],pv[...,:3,:3])
                angles=np.arccos(np.clip((np.trace(rel,axis1=-2,axis2=-1)-1)/2,-1,1))
                item.update(comparable_samples=len(common),wall_speed_ratio=base['wall_seconds']/r['wall_seconds'],
                    max_arm_difference_rad=float(np.max(np.abs(b['arm_qpos'][list(ix)]-v['arm_qpos'][list(iy)]))),
                    part_max_difference={pid:{'position_xyz_mm':(xyz[k]*1000).tolist(),'orientation_deg':float(np.rad2deg(angles[:,k].max()))} for k,pid in enumerate(base['part_ids'])})
            else:item['comparison_status']='not_comparable_full_duration; retain actual failure'
            paired.append(item)
    summary={'schema':'timestep_sensitivity_comparison/v1','scope':'fixed controller reference only; no approach, closing, insertion or completion qualification',
       'assembly_success_claim':False,'api_calls':0,'production_changed':False,'rows':rows,'paired_comparisons':paired}
    write(a.output/'comparison.json',summary)

if __name__=='__main__':main()
