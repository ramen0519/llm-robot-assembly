"""API-free development continuation and geometry-matched pose suggestions.

Every attempted action runs in its own recorded physical branch. Failed
branches are preserved, and only a released, verified branch can parent another
part. This is not a fresh Astra episode or research-memory collection.
"""
from pathlib import Path
from copy import deepcopy
import argparse,itertools,json,time,uuid
import numpy as np,mujoco
from scipy.spatial.transform import Rotation
from scipy.spatial import cKDTree
from assembly_v12.contracts import read,write,append,sha,digest
from assembly_v12.media import STATE,state
from assembly_v2.budget import Budget
from assembly_v2.completion import valid_evidence
from assembly_v2.geometry import load_mesh
from assembly_v2.grasp import resolve,candidates
from assembly_v2.grasp_screen import GoalToolScreen
from assembly_v2.media import Recorder
from assembly_v2.scene import body_name
from assembly_v2.skills import MeshSkills
from assembly_v2.transforms import body_pose,pose,xyzw
from assembly_v2.verification import observe_window
from failure_report import FailureEvent,json_safe
from view_tcp import read_tcp_pose


def source_state(folder):
    folder=Path(folder);p=read(folder/'prepared.json');r=read(folder/'result.json')
    e=read(folder/'completion.json');frames=read(folder/'replay_frames.json')
    installed=set(r['installed'])
    if r.get('hard_execution_violation') or not valid_evidence(e,p['completion_profile_hash'],required_parts=installed):
        raise ValueError('Parent must have a common released completion window for all installed parts.')
    if sha(folder/'model.mjb')!=frames['model_sha256'] or e['model_sha256']!=frames['model_sha256']:
        raise ValueError('Parent model identity mismatch.')
    if sha(folder/'replay_states.npz')!=frames['states_sha256']:raise ValueError('Parent state file changed.')
    if abs(frames['frames'][-1]['simulation_time_s']-e['end_time_s'])>1e-8:
        raise ValueError('Parent advanced after the verified released boundary.')
    states=np.load(folder/'replay_states.npz',allow_pickle=False)['states']
    if len(states)!=len(frames['frames']):raise ValueError('Frame/state count mismatch.')
    for row in p['parts'].values():
        if sha(row['mesh'])!=row['mesh_sha256']:raise ValueError('Original metric source mesh changed.')
    baseline=read(folder/'supply_baseline.json') if (folder/'supply_baseline.json').exists() else read(folder/'source_checkpoint.json')['settled']
    model=mujoco.MjModel.from_binary_path(str(folder/'model.mjb'));data=mujoco.MjData(model)
    saved=states[-1].copy();mujoco.mj_setState(model,data,saved,STATE);mujoco.mj_forward(model,data)
    if not np.array_equal(saved,state(model,data)):raise ValueError('Integration state restoration mismatch.')
    evidence={'parent_run':str(folder),'parent_run_id':r['run_id'],'model_sha256':frames['model_sha256'],
              'parent_result_sha256':sha(folder/'result.json'),'parent_completion_sha256':sha(folder/'completion.json'),
              'parent_states_sha256':frames['states_sha256'],'parent_final_frame':len(states)-1,
              'parent_final_time_s':float(data.time),'restored_exact':True,'inherited_installed':sorted(installed)}
    return model,data,p,installed,baseline,evidence


def triangle_signature(vertices,faces):
    # Canonical complete triangles, independent of vertex/face ordering.
    triangles=np.round(vertices[faces],7)
    triangles=np.sort(triangles.view([('x','f8'),('y','f8'),('z','f8')]),axis=1).view('f8').reshape(-1,9)
    return triangles[np.lexsort(triangles.T[::-1])]


def shape_transforms(source,target):
    """Rigid axis permutations matched against every triangle, no bbox-only reuse."""
    a,b=load_mesh(source['mesh']),load_mesh(target['mesh'])
    if len(a.faces)!=len(b.faces):return []
    target_sig=triangle_signature(b.vertices,b.faces);rows=[]
    ca,cb=a.bounds.mean(0),b.bounds.mean(0)
    for perm in itertools.permutations(range(3)):
        for signs in itertools.product((-1.,1.),repeat=3):
            rotation=np.eye(3)[:,perm]*signs
            if np.linalg.det(rotation)<0:continue
            shifted=(a.vertices-ca)@rotation.T+cb
            if np.max(abs(np.ptp(shifted,axis=0)-b.extents))>1e-7:continue
            sig=triangle_signature(shifted,a.faces)
            if np.array_equal(sig,target_sig):
                t=np.eye(4);t[:3,:3]=rotation;t[:3,3]=cb-rotation@ca;rows.append(t)
    rows.sort(key=lambda t:Rotation.from_matrix(t[:3,:3]).magnitude())
    return rows


def suggest(parent,part_id,experience_file,out,seconds=100):
    started=time.monotonic();out=Path(out);out.mkdir(parents=True,exist_ok=False)
    model,data,p,installed,settled,identity=source_state(parent)
    if part_id in installed:raise ValueError('Part already installed.')
    skills=MeshSkills(model,data,p,part_id,installed,settled,Budget(seconds))
    current=body_pose(data,body_name(part_id,p['base_id']));proposals=[];checks=[];seen=set()
    def evaluate(local,opening,origin):
        if len(checks)>=48:return
        token=tuple(np.round(np.r_[local[:3,3]*1000,local[:3,:3].ravel()*1000],5))
        if token in seen:return
        seen.add(token)
        candidate={'candidate_id':'offline:'+str(len(checks)),'position_mm':(local[:3,3]*1000).tolist(),
            'quaternion_xyzw':xyzw(local),'opening_width_mm':float(opening),'geometry':{'contact_spacing_m':float(opening)/1000,
                'spacing_role':'opening_proxy_only_for_diagnostic_metadata_not_a_surface_pair'}}
        # Surface width here is only a historical seed; actual whole-tool closing
        # is recomputed on this target CAD. No scalar-width success inference.
        screen=GoalToolScreen(skills)
        closing=screen.closure_preview(candidate)
        row={'origin':origin,'candidate':candidate,'closure':closing}
        checks.append(row)
        if closing.get('status')!='first_contact_bracketed' or closing.get('contact_kind')!='pad':return
        g=closing.get('goal_with_first_contact',{})
        if g.get('status')!='clear_sampled_nominal':return
        approach=screen.opening_and_closure(candidate);row['source_approach']=approach
        if approach.get('status')!='clear_sampled_nominal':return
        opening=approach['recommended_opening_mm'];goal_clear=g['worst_clearance_mm']
        change=Rotation.from_matrix(read_tcp_pose(data)[1].T@current[:3,:3]@local[:3,:3]).magnitude()
        proposals.append({'part_id':part_id,'grasp':{'mode':'pose','frame':'part_local',
            'position_mm':candidate['position_mm'],'quaternion_xyzw':candidate['quaternion_xyzw'],
            'opening_width_mm':opening,'parent_candidate_id':None},'origin':origin,
            'geometry_evidence':row,'rank_goal_clearance_mm':goal_clear,'rank_rotation_rad':float(change),
            'physical_success':False,'source':'scripted_geometry_matched_development'})
    for entry in read(experience_file):
        source_part=p['parts'][entry['part_id']]
        transforms=shape_transforms(source_part,p['parts'][part_id])
        for index,t in enumerate(transforms):
            local=t@pose(np.asarray(entry['grasp']['position_mm'])/1000,entry['grasp']['quaternion_xyzw'])
            # Use the successful measured parameter first, followed by in-plane
            # rotations; each alternative rechecks actual target geometry.
            for yaw in (0,90,-90,180,45,-45):
                trial=local.copy();trial[:3,:3]=local[:3,:3]@Rotation.from_euler('z',yaw,degrees=True).as_matrix()
                evaluate(trial,entry['grasp']['opening_width_mm'],{'kind':'rigid_triangle_matched_prior_grasp',
                    'source_part':entry['part_id'],'source_run':entry['source_run'],
                    'source_to_target':t.tolist(),'yaw_revision_deg':yaw,'all_triangles_match_quantized_0_1um':True})
            if len(proposals)>=6:break
    if not proposals:
        pool=candidates(p['parts'][part_id],current,1,skills.budget,p['profile'],
            tcp_rotation=read_tcp_pose(data)[1],goal_screen=GoalToolScreen(skills))
        write(out/'generated_pool.json',pool)
        for c in pool['presented']:
            pose_rows=[c]+c.get('geometry_pose_alternatives',{}).get('alternatives',[])
            for row in pose_rows:
                evaluate(pose(np.asarray(row['position_mm'])/1000,row['quaternion_xyzw']),row['opening_width_mm'],
                    {'kind':'fresh_surface_candidate','candidate_id':c['candidate_id'],'retraction_mm':row.get('retraction_mm')})
    proposals.sort(key=lambda r:(-r['rank_goal_clearance_mm'],r['rank_rotation_rad']))
    for i,row in enumerate(proposals):write(out/f'proposal_{i:02d}.json',row)
    result={'api_calls':0,'part_id':part_id,'source':identity,'proposals':len(proposals),'checks':checks,
            'elapsed_s':time.monotonic()-started,'physical_success':False}
    write(out/'search.json',result)
    print(json.dumps({'part':part_id,'proposals':len(proposals),'checks':len(checks),'elapsed_s':result['elapsed_s']}),flush=True)
    return result


def attempt(parent,proposal_path,out,seconds):
    started=time.monotonic();out=Path(out);out.mkdir(parents=True,exist_ok=False)
    result={'schema':'offline_sequence_branch/v1','run_id':'offline-seq-'+uuid.uuid4().hex[:8],
        'api_calls':0,'api_cost_usd':0.,'policy_source':'scripted_development','fresh_empty_base_episode':False,
        'whole_assembly_claim':False,'status':'initializing','grasp_feasible':'not_run','installation_feasible_with_grasp':'not_run'}
    recorder=skills=None
    try:
        model,data,p,installed,settled,identity=source_state(parent)
        proposal=read(proposal_path);part_id=proposal['part_id']
        if part_id not in p['order'] or part_id in installed:raise ValueError('Not an uninstalled target part.')
        result.update(part_id=part_id,installed=sorted(installed),inherited_installed=sorted(installed),parent=identity)
        write(out/'prepared.json',p);write(out/'proposal.json',proposal);write(out/'supply_baseline.json',settled)
        write(out/'parent_boundary.json',identity)
        budget=Budget(seconds,cancelled=lambda:(out/'cancel.request').exists())
        recorder=Recorder(out,model,data,p);recorder.deadline=budget.deadline
        recorder.part_id=part_id;recorder.action_id=result['run_id']+':scripted'
        skills=MeshSkills(model,data,p,part_id,installed,settled,budget,recorder=recorder)
        skills.retreat_complete=True
        boundary=observe_window(skills,sorted(installed),check_id=result['run_id']+':restored',
            source='live_simulation',model_hash=recorder.model_hash,prealignment=skills.prealignment)
        write(out/'restored_boundary_verification.json',boundary)
        if not valid_evidence(boundary,p['completion_profile_hash'],required_parts=installed):raise ValueError('Restored boundary failed.')
        current=body_pose(data,body_name(part_id,p['base_id']))
        grasp=resolve(proposal['grasp'],{'presented':[]},p['parts'][part_id],current,1,'scripted')
        write(out/'resolved_grasp.json',grasp)
        # The proposal may come from an older scene. Re-observe current part and
        # all installed obstacles; never trust the prior geometric score alone.
        c={**grasp,'candidate_id':'fresh-diagnostic','geometry':{'contact_spacing_m':grasp['opening_width_mm']/1000}}
        screen=GoalToolScreen(skills);closure=screen.closure_preview(c);approach=screen.opening_and_closure(c)
        write(out/'fresh_geometry_check.json',{'closure':closure,'approach':approach,'source':'current_restored_scene','physical_success':False})
        if closure.get('goal_with_first_contact',{}).get('status')!='clear_sampled_nominal' or approach.get('status')!='clear_sampled_nominal':
            raise ValueError('Current-scene full-tool geometric check rejected this proposal before pick.')
        result.update(status='running',grasp_feasible='running');write(out/'result.json',result)
        print('part',part_id,'physical pick, API=0',flush=True)
        picked=skills.pick(grasp);result['grasp_feasible']='passed'
        append(out/'events.jsonl',{'kind':'pick','result':picked,'policy_source':'scripted_development'})
        write(out/'result.json',result);print('part',part_id,'lift passed, install',flush=True)
        completion=skills.insert(result['run_id']+':completion')
        installed.add(part_id);write(out/'completion.json',completion)
        result.update(status='component_success',installed=sorted(installed),installation_feasible_with_grasp='passed',completion=completion,
                      all_required_installed=set(p['order'])==installed)
        append(out/'events.jsonl',{'kind':'install','installation_verified':True,'part_id':part_id,'verification':completion})
        result['released_checkpoint']=skills.checkpoint('verified_released_boundary')
    except Exception as exc:
        result.update(status='component_failed',failure=skills.failure(exc,skills) if skills else {'message':str(exc)})
        key='installation_feasible_with_grasp' if result['grasp_feasible']=='passed' else 'grasp_feasible'
        result[key]='failed' if isinstance(exc,FailureEvent) else 'unknown'
        if skills:result['stopped_checkpoint']=skills.checkpoint('stopped_development_branch')
        print(json.dumps({'part':result.get('part_id'),'failure':result['failure'].get('failure_code'),
                          'phase':result['failure'].get('phase'),'message':result['failure'].get('message')},ensure_ascii=False),flush=True)
    finally:
        if skills:result['path_checks']=json_safe(skills.path_attempts)
        if recorder:
            try:result['media']=recorder.close()
            except Exception as exc:result['media']={'status':'media_error','error':str(exc)}
        result['elapsed_s']=time.monotonic()-started;write(out/'result.json',result)
    print(json.dumps({k:result.get(k) for k in ('run_id','part_id','status','installed','elapsed_s','api_calls')},ensure_ascii=False),flush=True)
    return result

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('mode',choices=['suggest','attempt'])
    ap.add_argument('--parent',required=True);ap.add_argument('--output',required=True)
    ap.add_argument('--seconds',type=float,default=360);ap.add_argument('--part')
    ap.add_argument('--experiences');ap.add_argument('--proposal')
    a=ap.parse_args()
    if not 1<=a.seconds<=600:ap.error('Finite 1..600 seconds required per operation.')
    if a.mode=='suggest':suggest(a.parent,a.part,a.experiences,a.output,a.seconds)
    else:
        r=attempt(a.parent,a.proposal,a.output,a.seconds)
        raise SystemExit(0 if r['status']=='component_success' else 2)
