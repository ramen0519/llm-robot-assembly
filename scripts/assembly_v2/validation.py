"""Check a cached preparation against its actual source contract, never just a flag."""
from pathlib import Path
import numpy as np
from assembly_v12.contracts import digest,read,rigid,sha
from .completion import profile_for_timestep
from .preparation import read_assembly_input,metric_part_mesh,decode_path,relations_and_operations
from .geometry import load_mesh,geometry_matches
from copy import deepcopy

def check_prepared(p,budget):
    if p.get('schema')!='prepared_assembly/v2.0':raise ValueError('Unsupported preparation schema.')
    inputs=[p[k] for k in ('input_hint','input_export') if p.get(k)]
    if len(inputs)!=1:
        raise ValueError('Prepared data must identify exactly one source input.')
    source=read_assembly_input(inputs[0],budget)
    if (source.advisory != bool(p.get('input_hint')) or source.base!=p['base_id'] or
            source.order!=p['order'] or set(p['parts'])!=set(source.parts) or
            p['source_geometry_hash']!=source.geometry_hash or p['source_run_hash']!=source.run_hash):
        raise ValueError('Preparation identity/order differs from its original input.')
    if source.advisory and (p.get('order_semantics')!='advisory_base_first' or any(
            'part_path' in row or 'support_conditions' in row for row in p['parts'].values())):
        raise ValueError('An order hint cannot certify a removal path or disassembly support.')
    profile=profile_for_timestep(p['completion_profile']['physics_timestep_s'])
    if p['completion_profile']!=profile or p['completion_profile_hash']!=digest(profile):raise ValueError('Completion criteria altered.')
    world=rigid(p['common_world_from_assembly'])
    if p.get('profile_path') and (read(p['profile_path'])!=p['profile'] or p['profile_hash']!=digest(p['profile'])):raise ValueError('Common environment settings changed after preparation.')
    for pid,part in p['parts'].items():
        row=source.parts[pid];goal=rigid(row['assembly_matrix_mm']).copy();goal[:3,3]*=.001
        if not np.allclose(part['original_goal'],goal,atol=1e-10) or not np.allclose(part['target'],world@goal,atol=1e-10):raise ValueError('Original CAD target/rotation was changed.')
        if sha(part['mesh'])!=part['mesh_sha256']:raise ValueError('Metric source changed.')
        native=metric_part_mesh(source,row,goal)
        if not geometry_matches(native,load_mesh(part['mesh'])) or part['source_mesh_sha256']!=row['source_sha256']:
            raise ValueError('Prepared metric mesh differs from original native/source surface.')
        disk=read(Path(part['asset']['folder'])/'asset.json')
        if {k:v for k,v in part['asset'].items() if k!='folder'}!=disk or disk['asset_hash']!=digest({k:v for k,v in disk.items() if k!='asset_hash'}):raise ValueError('Collision asset metadata changed.')
        if disk['quality']['status']!='passed':raise ValueError('Unvalidated collision asset.')
        for a in [disk['visual'],*disk['hulls']]:
            if sha(Path(part['asset']['folder'])/a['path'])!=a['sha256']:raise ValueError('Collision/visual asset changed.')
        budget.check('prepared_assets')
    if sum(len(part['asset']['hulls']) for part in p['parts'].values())+len(p.get('layout',{}).get('supply_fixtures',[]))>p['profile']['resources']['max_collision_geoms']:
        raise ValueError('Product plus supply fixture collision geometry budget exceeded.')
    if p['profile'].get('supply_pedestal',{}).get('enabled') or p.get('layout',{}).get('supply_fixtures'):
        # Fixtures are reproducible from source geometry and the common workcell
        # profile; a product bundle cannot smuggle in an arbitrary support/weld.
        from .preparation import layout
        layout_parts=deepcopy(p['parts'])
        expected=layout(layout_parts,p['base_id'],p['order'],p['profile'],budget)
        if (expected.get('supply_fixtures',[])!=p.get('layout',{}).get('supply_fixtures',[]) or
            any(layout_parts[pid]['initial']!=p['parts'][pid]['initial'] for pid in p['order']) or
            expected['table_center_world_m']!=p['table_center_world_m'] or expected['table_size_xy_m']!=p['table_size_xy_m']):
            raise ValueError('Supply fixture/placement differs from deterministic preparation.')
    restored=deepcopy(p['parts'])
    for step in source.steps:
        pid=step['part'];path=decode_path(source.path.parent,step,np.asarray(p['parts'][pid]['target']),world)
        if p['parts'][pid]['part_path']!=path:raise ValueError('Native part path mapping changed after preparation.')
    expected_pairs=relations_and_operations(restored,p['order'],budget)
    if expected_pairs!=p['allowed_goal_contact_pairs'] or any(restored[pid]['operation']!=p['parts'][pid]['operation'] for pid in p['order']):
        raise ValueError('Operation applicability/geometry conditions changed after preparation.')
    if p['asset_hash']!=digest({pid:part['asset']['asset_hash'] for pid,part in p['parts'].items()}):raise ValueError('Assembly asset identity differs.')
    return True
