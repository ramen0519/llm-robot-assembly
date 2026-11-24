"""Check a cached preparation against its actual source contract, never just a flag."""
from pathlib import Path
import numpy as np
from assembly_v12.contracts import check_export,digest,read,rigid,sha,relative
from .completion import load_profile
from .preparation import source_geometry_check,decode_path,relations_and_operations
from .geometry import load_mesh,geometry_matches
from copy import deepcopy

def check_prepared(p,budget):
    if p.get('schema')!='prepared_assembly/v2.0':raise ValueError('Unsupported preparation schema.')
    contract,geometry=check_export(p['input_export']);source_geometry_check(Path(p['input_export']).parent,contract,geometry,budget)
    if p['base_id']!=contract['assembly_candidate']['initial_base'] or p['order']!=contract['assembly_candidate']['add_parts'] or set(p['parts'])!=set(contract['part_mapping']):
        raise ValueError('Preparation identity/order differs from the original chain.')
    profile=load_profile()
    if p['completion_profile']!=profile or p['completion_profile_hash']!=digest(profile):raise ValueError('Completion criteria altered.')
    world=rigid(p['common_world_from_assembly'])
    if p.get('profile_path') and (read(p['profile_path'])!=p['profile'] or p['profile_hash']!=digest(p['profile'])):raise ValueError('Common environment settings changed after preparation.')
    for pid,part in p['parts'].items():
        row=contract['part_mapping'][pid];goal=rigid(row['assembly_matrix_mm']).copy();goal[:3,3]*=.001
        if not np.allclose(part['original_goal'],goal,atol=1e-10) or not np.allclose(part['target'],world@goal,atol=1e-10):raise ValueError('Original CAD target/rotation was changed.')
        if sha(part['mesh'])!=part['mesh_sha256']:raise ValueError('Metric source changed.')
        native=load_mesh(relative(Path(p['input_export']).parent,row['native_mesh']),.01)
        if not geometry_matches(native,load_mesh(part['mesh'])) or part['source_mesh_sha256']!=row['source_sha256']:
            raise ValueError('Prepared metric mesh differs from original native/source surface.')
        disk=read(Path(part['asset']['folder'])/'asset.json')
        if {k:v for k,v in part['asset'].items() if k!='folder'}!=disk or disk['asset_hash']!=digest({k:v for k,v in disk.items() if k!='asset_hash'}):raise ValueError('Collision asset metadata changed.')
        if disk['quality']['status']!='passed':raise ValueError('Unvalidated collision asset.')
        for a in [disk['visual'],*disk['hulls']]:
            if sha(Path(part['asset']['folder'])/a['path'])!=a['sha256']:raise ValueError('Collision/visual asset changed.')
        budget.check('prepared_assets')
    restored=deepcopy(p['parts'])
    for step in contract['verified_transitions']:
        pid=step['part'];path=decode_path(Path(p['input_export']).parent,step,np.asarray(p['parts'][pid]['target']),world)
        if p['parts'][pid]['part_path']!=path:raise ValueError('Native part path mapping changed after preparation.')
    expected_pairs=relations_and_operations(restored,p['order'],budget)
    if expected_pairs!=p['allowed_goal_contact_pairs'] or any(restored[pid]['operation']!=p['parts'][pid]['operation'] for pid in p['order']):
        raise ValueError('Operation applicability/geometry conditions changed after preparation.')
    if p['asset_hash']!=digest({pid:part['asset']['asset_hash'] for pid,part in p['parts'].items()}):raise ValueError('Assembly asset identity differs.')
    return True
