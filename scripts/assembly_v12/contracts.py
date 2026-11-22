"""Portable input contracts. No imports or execution from a product bundle."""
import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path

import numpy as np


class InputError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def location(value):
    raw = str(value)
    if re.search(r'(^|[/\\])(?:e:|mnt[/\\]e)(?:[/\\]|$)', raw, re.I) or raw.lower().startswith(('e:', '\\\\', '//')):
        raise InputError('input_invalid', 'Disallowed source volume.')
    path = Path(value).absolute()
    if '..' in path.parts or any(p.is_symlink() for p in [path, *path.parents]):
        raise InputError('input_invalid', 'Linked/parent-traversing paths are not supported.')
    return path


def read(path):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError('Duplicate JSON key: '+k)
            result[k] = v
        return result
    return json.loads(location(path).read_text(encoding='utf-8-sig'), object_pairs_hook=pairs,
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError('Nonfinite JSON: '+x)))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                    separators=(',', ':')).encode()).hexdigest()


def sha(path):
    h = hashlib.sha256()
    with location(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def write(path, value):
    path = location(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf8')


def append(path, value):
    with location(path).open('a', encoding='utf8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False)+'\n')


def relative(root, name):
    if not isinstance(name, str) or not name or ':' in name or '\\' in name:
        raise InputError('input_invalid', 'Invalid relative artifact name.')
    p = Path(name)
    if p.is_absolute() or '..' in p.parts or not location(root/p).is_relative_to(location(root)):
        raise InputError('input_invalid', 'Artifact escapes bundle.')
    return root/p


def rigid(matrix):
    a = np.asarray(matrix, dtype=float)
    if (a.shape != (4, 4) or not np.isfinite(a).all() or not np.allclose(a[3], [0, 0, 0, 1], atol=1e-9)
            or not np.allclose(a[:3, :3].T@a[:3, :3], np.eye(3), atol=1e-8)
            or not np.isclose(np.linalg.det(a[:3, :3]), 1)):
        raise InputError('input_invalid', 'Expected a finite rigid transform, without scale.')
    return a


def mesh(path):
    vertices, faces = [], []
    for line in location(path).read_text(encoding='utf8').splitlines():
        if line.startswith('v '):
            vertices.append([float(x) for x in line.split()[1:4]])
        elif line.startswith('f '):
            face = [int(x.split('/')[0]) for x in line.split()[1:]]
            face = [x-1 if x > 0 else len(vertices)+x for x in face]
            for i in range(1, len(face)-1):
                faces.append([face[0], face[i], face[i+1]])
    v, f = np.asarray(vertices, float), np.asarray(faces, int)
    if v.ndim != 2 or v.shape[1] != 3 or not len(f) or not np.isfinite(v).all() or f.min() < 0 or f.max() >= len(v):
        raise InputError('input_invalid', 'Invalid OBJ geometry.')
    return v, f


def check_export(path):
    path = location(path)
    root, d = path.parent, read(path)
    if d.get('schema') != 'disassembly_export/v1' or d.get('robot_verified') is not False:
        raise InputError('input_invalid', 'Expected disassembly_export/v1 with robot_verified=false.')
    if d.get('evidence_scope')!='all_native_checks_and_media/v1':
        raise InputError('input_incomplete','A fully packaged native-check/media export is required by v1.2.')
    hashes = d['artifacts']
    if digest(hashes) != d['run_hash']:
        raise InputError('input_invalid', 'Export inventory hash mismatch.')
    for name, checksum in hashes.items():
        if sha(relative(root, name)) != checksum:
            raise InputError('input_invalid', 'Artifact hash mismatch: '+name)
    def evidence(name):
        if name not in hashes:
            raise InputError('input_invalid', 'Unpackaged evidence: '+str(name))
        return read(relative(root, name))
    geometry = evidence('input/geometry.json')
    if digest(geometry['source_files']) != d['source_hash']:
        raise InputError('input_invalid', 'Source inventory mismatch.')
    for name, checksum in geometry['source_files'].items():
        if hashes.get('input/source/'+name) != checksum:
            raise InputError('input_invalid', 'Original CAD hash mismatch.')
    ids = d['initial_assembled_parts']
    if len(ids) != len(set(ids)) or set(ids) != set(d['part_mapping']):
        raise InputError('input_invalid', 'Duplicate or inconsistent part IDs.')
    geom_parts = {p['part_id']: p for p in geometry['parts']}
    if set(geom_parts) != set(ids):
        raise InputError('input_invalid', 'Geometry and export IDs differ.')
    mapping_file = evidence('input/part_mapping.json')
    if geometry.get('source_unit') not in ('mm','cm','m','inch'):
        raise InputError('input_incomplete','The source CAD unit must be declared and verified upstream.')
    from scipy.spatial.transform import Rotation
    native_config=evidence('input/native/config.json')
    native_inventory=evidence('native_input.json')['sha256']
    for name,checksum in native_inventory.items():
        if hashes.get('input/native/'+name)!=checksum:
            raise InputError('input_invalid','Native physics input differs from packaged meshes/config.')
    for pid, p in d['part_mapping'].items():
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', pid) or pid != p['part_id']:
            raise InputError('unsupported_execution_scope', 'Part ID must be a stable safe identifier.')
        for key, checksum in [('native_mesh', 'native_mesh_sha256'), ('source_mesh', 'source_sha256')]:
            if hashes.get(p[key]) != p[checksum]:
                raise InputError('input_invalid', 'Mesh mapping mismatch.')
        original = mapping_file[pid]
        if any(original.get(k) != v for k, v in p.items() if k not in ('native_mesh', 'source_mesh')):
            raise InputError('input_invalid', 'Mapping metadata differs from packaged evidence.')
        if any('input/'+original[k] != p[k] for k in ('native_mesh', 'source_mesh')):
            raise InputError('input_invalid', 'Mapping paths differ.')
        transform=rigid(p['assembly_matrix_mm'])
        if (p.get('mesh_coordinate_frame') not in ('part_local','assembly') or p.get('native_mesh_frame')!='part_local'
                or p.get('assembly_pose_applied_to_source_mesh')!=(p['mesh_coordinate_frame']=='assembly')):
            raise InputError('input_invalid','Ambiguous source/native mesh placement contract.')
        pose=geom_parts[pid]['assembly_pose']
        if (not np.allclose(pose['translation_mm'],transform[:3,3],atol=1e-6,rtol=0) or
                not np.allclose(Rotation.from_quat(pose['rotation_xyzw']).as_matrix(),transform[:3,:3],atol=1e-8)):
            raise InputError('input_invalid','CAD goal pose differs from source mapping.')
        state=np.asarray(native_config[pid]['final_state'],float)
        if (state.shape!=(6,) or not np.allclose(state[:3]*10,transform[:3,3],atol=1e-6,rtol=0) or
                not np.allclose(Rotation.from_euler('xyz',state[3:]).as_matrix(),transform[:3,:3],atol=1e-7)):
            raise InputError('input_invalid','Native goal transform differs from CAD goal.')
        vertices,_=mesh(relative(root,p['native_mesh']))
        assembled_mm=vertices*10@transform[:3,:3].T+transform[:3,3]
        bounds=np.array([assembled_mm.min(0),assembled_mm.max(0)])
        if (not np.allclose(bounds[1]-bounds[0],geom_parts[pid]['bbox_size_mm'],atol=.001,rtol=0)
                or not np.allclose(bounds.mean(0),geom_parts[pid]['bbox_center_mm'],atol=.001,rtol=0)):
            raise InputError('input_invalid','Placed native mesh does not match measured CAD goal bounds.')
    if d['coordinate_system'] != geometry['coordinate_system']:
        raise InputError('input_invalid', 'Coordinate declarations differ.')
    coords = geometry['coordinate_system']
    if (coords.get('length_unit') != 'mm' or coords.get('rotation') != 'xyzw'
            or not np.allclose(coords['assembly_mm_to_native_cm'], np.diag([.1, .1, .1, 1]))):
        raise InputError('input_invalid', 'Unknown coordinate/normalization contract.')
    remaining, previous, sequence = set(ids), None, []
    for step in d['verified_transitions']:
        p = step['part']
        if (not step['accepted'] or not step['physics_pass'] or set(step['before']) != remaining
                or len(step['before']) != len(remaining) or p not in remaining
                or (previous is not None and step['parent_uid'] != previous)):
            raise InputError('input_invalid', 'Disconnected/unverified selected chain.')
        remaining.remove(p)
        if set(step['after']) != remaining or len(step['after']) != len(remaining):
            raise InputError('input_invalid', 'Invalid successor.')
        record = evidence('checks/'+step['path_check']+'.json')
        if (record['stage']!='path' or record['target'] != p or not record['passed'] or record['attempt'] != step['attempt']
                or record['trajectory'] != step['path'] or step['path'] not in hashes):
            raise InputError('input_invalid', 'Missing native path evidence.')
        if not step['stability_checks']:
            raise InputError('input_invalid', 'Missing stability verification.')
        for cid in step['stability_checks']:
            r = evidence('checks/'+cid+'.json')
            if r['stage'] != 'stability' or not r['passed'] or r['attempt'] != step['attempt']:
                raise InputError('input_invalid', 'Cross-attempt stability evidence.')
        sequence.append(p)
        previous = step['child_uid']
    if set(d['remaining_parts']) != remaining or d['reversed_local_removals'] != sequence[::-1]:
        raise InputError('input_invalid', 'Residual/reversed order mismatch.')
    if len(remaining) != 1 or len(sequence) != len(ids)-1 or d['completion'] != 'COMPLETE':
        raise InputError('input_incomplete', 'A complete selected chain is required for complete assembly.')
    if d['assembly_candidate'] != {'initial_base': next(iter(remaining)), 'add_parts': sequence[::-1]}:
        raise InputError('input_invalid', 'Assembly candidate does not reverse the selected chain.')
    if any(s.get('fixed_base')!=next(iter(remaining)) for s in d['verified_transitions']):
        raise InputError('unsupported_execution_scope','Selected transitions do not preserve the declared fixed base.')
    if d.get('evidence_scope') == 'all_native_checks_and_media/v1':
        evidence('input/surface_distances.json')
        for attempt in evidence('attempts.json'):
            for cid in attempt['checks']:
                r = evidence('checks/'+cid+'.json')
                if r.get('trajectory') and r['trajectory'] not in hashes:
                    raise InputError('input_invalid', 'Missing check trajectory.')
        for event in evidence('incidents.json'):
            rep = event['representative']
            if rep.get('png') and hashes.get(rep['png']) != rep['png_sha256']:
                raise InputError('input_invalid', 'Failure image mismatch.')
        media = evidence('media.json')
        for clip in [media]+[x['clip'] for x in media.get('incident_clips', []) if 'clip' in x]:
            if not clip.get('video'):
                continue
            name = clip['video']
            if hashes.get(name) != clip['sha256']:
                raise InputError('input_invalid', 'Video hash mismatch.')
            frames = evidence(str(Path(name).with_suffix(''))+'_frames.json')
            if len(frames) != clip['frames']:
                raise InputError('input_invalid', 'Video frame count mismatch.')
            for row in frames:
                evidence('checks/'+row['check']+'.json')
            if frames and any(str(Path(name).with_suffix(''))+f'_frame_{i:03d}.png' not in hashes for i in [0,len(frames)-1]):
                raise InputError('input_invalid', 'Missing representative video frames.')
    for name in ('input/views.json', 'input/top.png', 'input/oblique.png'):
        if name not in hashes:
            raise InputError('input_invalid', 'Missing goal image evidence: '+name)
    return d, geometry


def import_export(source, destination):
    source, destination = location(source), location(destination)
    if destination.exists():
        raise InputError('input_invalid', 'Use a new immutable input folder.')
    if source.is_dir():
        source = source/'export_v1.json'
    if source.suffix.lower() == '.zip':
        destination.mkdir(parents=True)
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
            if (len(members) > 20000 or sum(m.file_size for m in members) > 2_000_000_000
                    or len({m.filename for m in members}) != len(members)):
                raise InputError('input_invalid', 'Archive size/duplicate-entry limit.')
            for m in members:
                target = relative(destination, m.filename)
                if (m.external_attr >> 16) & 0o170000 == 0o120000:
                    raise InputError('input_invalid', 'Archive links are not allowed.')
                if m.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(m) as src, target.open('xb') as dst:
                        shutil.copyfileobj(src, dst)
        return check_export(destination/'export_v1.json')
    d, _ = check_export(source)
    destination.mkdir(parents=True)
    for name in ['export_v1.json', *d['artifacts']]:
        original = source if name == 'export_v1.json' else relative(source.parent, name)
        target = relative(destination, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
    return check_export(destination/'export_v1.json')
