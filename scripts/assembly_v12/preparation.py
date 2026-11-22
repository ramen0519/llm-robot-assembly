"""Derive upright rectangular insertion contracts from exact mesh topology.

Only closed axis-aligned orthogonal solids are admitted. Cell occupancy is
derived by triangle ray intersections; boxes preserve the mesh solid exactly,
not its convex hull/bounding box. No product names or generated-product imports.
"""
from copy import deepcopy
import itertools
import math
from pathlib import Path

import numpy as np

from .contracts import InputError, digest, mesh, read, rigid, write


def unsupported(message):
    raise InputError('unsupported_execution_scope', message)


def solid_cells(vertices, faces, limit=12000):
    # Canonicalize coincident OBJ vertices before checking the closed surface.
    v, inv = np.unique(np.round(vertices, 9), axis=0, return_inverse=True)
    f = inv[faces]
    edges = {}
    for triangle in f:
        for a, b in zip(triangle, np.roll(triangle, -1)):
            key = tuple(sorted((int(a), int(b))))
            edges.setdefault(key, []).append(int(a) < int(b))
    if any(len(signs) != 2 or sum(signs) != 1 for signs in edges.values()):
        unsupported('A closed consistently oriented mesh is required.')
    triangles = v[f]
    cross = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
    norms = np.linalg.norm(cross, axis=1)
    if np.any(norms < 1e-14) or np.any(np.max(np.abs(cross/norms[:, None]), axis=1) < 1-1e-7):
        unsupported('Non-orthogonal/curved faces need another supported execution template.')
    axes = [np.unique(v[:, i]) for i in range(3)]
    sizes = [len(a)-1 for a in axes]
    if np.prod(sizes) > limit:
        unsupported('Orthogonal decomposition resource limit exceeded.')
    mid = [(a[:-1]+a[1:])/2 for a in axes]
    occ = np.zeros(sizes, bool)
    xt = triangles[np.abs(cross[:, 0])/norms > .999999]
    for j, y in enumerate(mid[1]):
        for k, z in enumerate(mid[2]):
            hits = []
            for tri in xt:
                a, b, c = tri[:, 1:]
                matrix = np.column_stack((b-a, c-a))
                bary = np.linalg.solve(matrix, np.array([y, z])-a)
                if min(bary) >= -1e-9 and sum(bary) <= 1+1e-9:
                    hits.append(tri[0, 0])
            hits = np.unique(np.round(hits, 9))
            if len(hits) % 2:
                unsupported('Ambiguous ray/surface intersection.')
            for lo, hi in zip(hits[::2], hits[1::2]):
                occ[:, j, k] |= (mid[0] > lo) & (mid[0] < hi)
    volume = sum(float(np.prod([axes[a][p[a]+1]-axes[a][p[a]] for a in range(3)]))
                 for p in zip(*np.nonzero(occ)))
    mesh_volume = abs(float(np.sum(np.einsum('ij,ij->i', triangles[:, 0], np.cross(triangles[:, 1], triangles[:, 2])))/6))
    if volume <= 0 or abs(volume-mesh_volume) > max(1e-12, volume*1e-5):
        unsupported('Collision decomposition does not preserve the original mesh volume.')
    boxes = []
    for j, k in itertools.product(range(sizes[1]), range(sizes[2])):
        start = None
        for i in range(sizes[0]+1):
            occupied = i < sizes[0] and occ[i, j, k]
            if occupied and start is None:
                start = i
            if not occupied and start is not None:
                low = np.array([axes[0][start], axes[1][j], axes[2][k]])
                high = np.array([axes[0][i], axes[1][j+1], axes[2][k+1]])
                boxes.append({'center': ((low+high)/2).tolist(), 'size': (high-low).tolist(), 'layer': k})
                start = None
    return dict(axes=[a.tolist() for a in axes], occupied=occ, boxes=boxes,
                volume_m3=volume, mesh_volume_m3=mesh_volume, low=v.min(0), high=v.max(0))


def holes(mask, axes):
    remaining = set(zip(*np.nonzero(~mask)))
    result = []
    while remaining:
        group, todo = set(), [min(remaining)]
        while todo:
            p = todo.pop()
            if p not in remaining:
                continue
            remaining.remove(p); group.add(p)
            todo.extend((p[0]+dx, p[1]+dy) for dx, dy in [(1,0),(-1,0),(0,1),(0,-1)])
        low, high = np.min(list(group), 0), np.max(list(group), 0)
        if any(low == 0) or any(high == np.array(mask.shape)-1):
            unsupported('Open-sided channel is outside the square-bore template.')
        if len(group) != np.prod(high-low+1):
            unsupported('Bore must have an exact rectangular cross section.')
        lo = np.array([axes[a][low[a]] for a in range(2)])
        hi = np.array([axes[a][high[a]+1] for a in range(2)])
        if abs((hi-lo)[0]-(hi-lo)[1]) > 1e-7:
            unsupported('Current verifier requires square bores.')
        result.append({'center_xy': ((lo+hi)/2).tolist(), 'width': float(hi[0]-lo[0])})
    return result


def classify(cells, fixed=False):
    axes, occ = cells['axes'], cells['occupied']
    low, high = cells['low'], cells['high']
    if fixed:
        floors = [k for k in range(occ.shape[2]) if occ[:, :, k].all()]
        if not floors or floors != list(range(max(floors)+1)) or max(floors) == occ.shape[2]-1:
            unsupported('Fixed base needs a solid floor and upward-open square sockets.')
        start = max(floors)+1
        if any(not np.array_equal(occ[:, :, start], occ[:, :, k]) for k in range(start, occ.shape[2])):
            unsupported('Socket walls must be straight and vertical.')
        return {'kind': 'base', 'holes': holes(occ[:, :, start], axes), 'floor_z': axes[2][start], 'top_z': high[2]}
    if occ.all():
        if abs((high-low)[0]-(high-low)[1]) > 1e-7 or not .012 <= (high-low)[0] <= .060:
            unsupported('Solid column is outside the square-post grasp envelope.')
        return {'kind': 'post', 'holes': []}
    same = all(np.array_equal(occ[:, :, 0], occ[:, :, k]) for k in range(occ.shape[2]))
    if same:
        h = holes(occ[:, :, 0], axes)
        if len(h) != 1 or not np.allclose(h[0]['center_xy'], (low[:2]+high[:2])/2, atol=1e-7):
            unsupported('Sleeve must have one centered square through-hole.')
        return {'kind': 'sleeve', 'holes': h}
    h = holes(occ[:, :, 0], axes)
    if len(h) < 2:
        unsupported('Cover needs multiple through-bores and one raised grip tab.')
    split = next(k for k in range(1, occ.shape[2]) if not np.array_equal(occ[:, :, 0], occ[:, :, k]))
    if any(not np.array_equal(occ[:, :, split], occ[:, :, k]) for k in range(split, occ.shape[2])):
        unsupported('Cover tab must be a single rectangular prism.')
    positions = np.array(list(zip(*np.nonzero(occ[:, :, split]))))
    if not len(positions):
        unsupported('Missing integral tab.')
    a, b = positions.min(0), positions.max(0)
    if len(positions) != np.prod(b-a+1):
        unsupported('Raised geometry is not one rectangular tab.')
    if not occ[:, :, 0][tuple(positions.T)].all():
        unsupported('Tab must join a solid region of the plate.')
    tablo = np.array([axes[0][a[0]], axes[1][a[1]], axes[2][split]])
    tabhi = np.array([axes[0][b[0]+1], axes[1][b[1]+1], axes[2][-1]])
    return {'kind': 'cover', 'holes': h, 'plate_top_z': axes[2][split],
            'tab': {'center': ((tablo+tabhi)/2).tolist(), 'size': (tabhi-tablo).tolist()}}


def overlap(a, b, margin=0.):
    return bool(np.all(np.asarray(a[0])-margin < np.asarray(b[1])) and np.all(np.asarray(b[0])-margin < np.asarray(a[1])))


def native_priority(folder, profile, fixed_base=None):
    """Geometric priority only; the external native planner must still verify it.

    A post can be pulled out through a sleeve in disassembly, yet our sleeve
    insertion executor requires the post first. Rank actual supported features
    before native search, never reorder an already verified exported chain.
    """
    folder=Path(folder); geometry=read(folder/'geometry.json')
    mappings=read(folder/'part_mapping.json'); p=read(profile)
    aliases={row['source_part_id']:row['part_id'] for row in geometry['parts']}
    fixed_base=aliases.get(fixed_base,fixed_base)
    cells={}
    for pid,row in mappings.items():
        if row['native_mesh_frame']!='part_local':
            raise InputError('input_invalid','Unknown native mesh frame.')
        v,f=mesh(folder/row['native_mesh']); t=rigid(row['assembly_matrix_mm'])
        cells[pid]=solid_cells((v*.01)@t[:3,:3].T+t[:3,3]*.001,f,p['max_geometry_cells'])
    if fixed_base is None:
        candidates=[]
        for pid,value in cells.items():
            try:
                if classify(value,True)['kind']=='base': candidates.append(pid)
            except InputError:
                pass
        if len(candidates)!=1:
            raise InputError('input_incomplete','A unique supported fixed socket base cannot be identified; specify the intended base.')
        fixed_base=candidates[0]
    if fixed_base not in cells:
        raise InputError('input_invalid','Fixed base is absent from CAD.')
    kinds={pid:classify(value,pid==fixed_base)['kind'] for pid,value in cells.items()}
    tiers={'cover':0,'sleeve':1,'post':2}
    ranked=sorted((pid for pid in cells if pid!=fixed_base),key=lambda pid:(tiers[kinds[pid]],pid))
    return {'source':'orthogonal_template_priority/v1','fixed_base':fixed_base,'ranking':ranked,
            'detected_kinds':kinds,'physics_verified':False,
            'meaning':'Priority for the real native planner, never a replacement for its selected successful chain.'}


def prepare(bundle, output, profile):
    from .contracts import check_export
    bundle, output = Path(bundle), Path(output)
    d, geometry = check_export(bundle/'export_v1.json')
    p = read(profile)
    if p['robot'] != 'ur3e' or p['gripper'] != 'robotiq_2f85' or p['fixture'] != 'fixed_base_bolted_to_table':
        unsupported('Unsupported shared workcell profile.')
    base, order = d['assembly_candidate']['initial_base'], d['assembly_candidate']['add_parts']
    if len(d['part_mapping']) > p['max_parts']:
        raise InputError('unsupported_quantity', 'Product exceeds the declared free-body/resource limit.')
    if any(s.get('additional_support') for s in d['verified_transitions']):
        unsupported('Additional disassembly support needs an explicitly modeled fixture.')
    parts = {}
    for pid, mapping in d['part_mapping'].items():
        if mapping['native_mesh_frame'] != 'part_local':
            raise InputError('input_invalid', 'Native cm mesh must be part-local.')
        v, f = mesh(bundle/mapping['native_mesh'])
        transform = rigid(mapping['assembly_matrix_mm'])
        v = (v*.01)@transform[:3, :3].T+transform[:3, 3]*.001
        cells = solid_cells(v, f, p['max_geometry_cells'])
        feature = classify(cells, pid == base)
        center = (cells['low']+cells['high'])/2
        boxes = deepcopy(cells['boxes'])
        if pid == base:
            boxes = [b for b in boxes if b['center'][2] > feature['floor_z']]
            size = cells['high']-cells['low']; size[2] = feature['floor_z']-cells['low'][2]
            boxes.insert(0, {'name': 'floor', 'center': [*center[:2], (feature['floor_z']+cells['low'][2])/2], 'size': size.tolist()})
        elif feature['kind'] == 'cover':
            boxes = [b for b in boxes if b['center'][2] < feature['plate_top_z']]
            boxes.append({'name': 'grip_tab', **feature['tab']})
        for i, box in enumerate(boxes):
            box['name'] = box.get('name', 'solid_'+str(i))
            box['center'] = (np.asarray(box['center'])-center).tolist()
            box.pop('layer', None)
        parts[pid] = {'id': pid, 'feature': feature, 'kind': feature['kind'], 'center': center.tolist(),
                      'low': cells['low'].tolist(), 'high': cells['high'].tolist(), 'size': (cells['high']-cells['low']).tolist(),
                      'boxes': boxes, 'volume_m3': cells['volume_m3'], 'mesh_volume_m3': cells['mesh_volume_m3'],
                      'mesh_to_body': 'native cm to m, assembly matrix once, subtract assembled bbox center',
                      'collision_method': 'exact orthogonal surface cell decomposition, volume checked',
                      'source_mesh_sha256': mapping['native_mesh_sha256']}
    fixture = parts[base]
    shift = np.array([*p['assembly_center_xy_m'], p['tabletop_z_m']])-np.array([*fixture['center'][:2], fixture['low'][2]])
    for pid, part in parts.items():
        part['target_world_m'] = (np.array(part['center'])+shift).tolist()
        part['rotation_world'] = np.eye(3).tolist()
        if pid == base:
            continue
        kind, feature = part['kind'], part['feature']
        required, mating, supports = [], [], []
        if kind == 'post':
            matches = [h for h in fixture['feature']['holes'] if np.allclose(h['center_xy'], part['center'][:2], atol=1e-7)
                       and h['width'] > part['size'][0]]
            if len(matches) != 1 or abs(part['low'][2]-fixture['feature']['floor_z']) > 1e-7:
                unsupported('Column does not seat on one verified socket floor: '+pid)
            width, rim = matches[0]['width'], fixture['high'][2]
            required, mating, supports = [base], [base], ['base_floor']
            post_width = part['size'][0]
        else:
            matched = []
            for h in feature['holes']:
                candidates = [q for q, t in parts.items() if t['kind'] == 'post'
                              and np.allclose(t['center'][:2], h['center_xy'], atol=1e-7)
                              and t['size'][0] < h['width'] and t['high'][2] > (feature.get('plate_top_z', part['high'][2]))]
                if len(candidates) != 1:
                    unsupported('Through-bore does not have one matching upright column: '+pid)
                matched.append(candidates[0])
            widths = [h['width'] for h in feature['holes']]
            if max(widths)-min(widths) > 1e-7:
                unsupported('Current multi-bore verifier requires equal square bore widths.')
            width = widths[0]
            post_width = max(parts[q]['size'][0] for q in matched)
            rim = max(parts[q]['high'][2] for q in matched)
            if kind == 'sleeve':
                if abs(part['low'][2]-fixture['high'][2]) > 1e-7:
                    unsupported('Sleeve must seat on the fixed base top.')
                required, mating, supports = [base, *matched], [base, *matched], ['base_top']
            else:
                for q in matched:
                    candidates = [sid for sid, t in parts.items() if t['kind'] == 'sleeve'
                                  and np.allclose(t['center'][:2], parts[q]['center'][:2], atol=1e-7)
                                  and abs(t['high'][2]-part['low'][2]) < 1e-7]
                    if len(candidates) != 1:
                        unsupported('Cover needs a verified sleeve top under every bore.')
                    supports.extend(candidates)
                required, mating = matched+supports, matched+supports
                part['bore_centers_part_m'] = {q: (np.array([*h['center_xy'], (part['low'][2]+feature['plate_top_z'])/2])-part['center']).tolist()
                                                for h, q in zip(feature['holes'], matched)}
        part.update(requires=required, mating_ids=mating, support_ids=supports, fixed_base_id=base,
                    hole_width=width, post_width=post_width, rim_world_m=float(rim+shift[2]),
                    floor_world_m=float(part['low'][2]+shift[2]), nominal_depth_m=float(rim-part['low'][2]),
                    installation_method='upright_square_insertion/v1')
    ready = {base}
    for pid in order:
        if not set(parts[pid]['requires']) <= ready:
            unsupported('Reversed disassembly order violates verified robotic support prerequisites: '+pid)
        ready.add(pid)
    # Stable input IDs, not planner order, determine grid cells.
    free = sorted(order)
    cell = np.max([parts[x]['size'][:2] for x in free], axis=0)+2*p['grid_margin_m']
    cols = math.ceil(math.sqrt(len(free)))
    robot = np.array(p['robot_base_world_m'][:2])
    base_xy = np.array(fixture['target_world_m'][:2]); half = np.array(fixture['size'][:2])/2
    reserved = [(robot-.07, robot+.07), (base_xy-half, base_xy+half)]
    bounds = list(reserved)
    pending = iter(free); pid = next(pending, None); used = []
    for k in range(p['max_grid_cells']):
        if pid is None:
            break
        row, col = divmod(k, cols)
        xy = np.array(p['grid_first_center_xy_m'])+np.array(p['grid_direction_xy'])*cell*[col, row]
        h = np.array(parts[pid]['size'][:2])/2
        box = (xy-h, xy+h)
        if any(overlap(box, r, p['grid_margin_m']) for r in reserved):
            continue
        part = parts[pid]
        local_min_z = part['low'][2]-part['center'][2]
        part['initial_world_m'] = [*xy, p['tabletop_z_m']-local_min_z+p['spawn_clearance_m']]
        part['settled_reference_world_m'] = [*xy, p['tabletop_z_m']-local_min_z]
        bounds.append(box); used.append({'id': pid, 'row': row, 'column': col})
        pid = next(pending, None)
    if pid is not None:
        raise InputError('preparation_failed', 'Grid budget exhausted; this does not prove every placement impossible.')
    low = np.min([b[0] for b in bounds], 0)-p['table_margin_m']
    high = np.max([b[1] for b in bounds], 0)+p['table_margin_m']
    size = np.maximum(high-low, p['table_min_size_xy_m'])
    result = {'schema': 'prepared_assembly/v1.2', 'input_valid': True, 'task_supported': True,
              'preparation_ready': False, 'robot_verified': False, 'source_run_hash': d['run_hash'],
              'base_id': base, 'order': order, 'parts': parts, 'profile': p, 'profile_sha256': digest(p),
              'table_center_world_m': [*((high+low)/2), p['tabletop_z_m']], 'table_size_xy_m': size.tolist(),
              'grid': {'cell_size_m': cell.tolist(), 'columns': cols, 'rows': max(x['row'] for x in used)+1, 'cells': used},
              'manual_product_adjustments': False, 'target_mapping': {'translation_m': shift.tolist(), 'rotation': np.eye(3).tolist()},
              'source_bundle':str(bundle.absolute()),'source_unit':geometry['source_unit'],
              'disassembly_evidence':[{'part_id':s['part'],'pose':s['pose'],'direction':s['direction'],
                                      'additional_support':s['additional_support'],'fixed_base':s['fixed_base'],
                                      'path_check':s['path_check'],'stability_checks':s['stability_checks']}
                                     for s in d['verified_transitions']],
              'disassembly_directions_are_tcp_commands':False,
              'preparation_checks': {'geometry_contract': True, 'layout_generated': True, 'stability': 'not_run', 'robot_access': 'not_run'}}
    write(output/'prepared.json', result)
    return result
