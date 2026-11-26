"""Independent assembly input: CAD, original goal transforms and a suggested order."""
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import stat

from assembly_v12.contracts import InputError, digest, location, read, relative, rigid, sha

SCHEMA = 'assembly_order_hint/v1'
UNITS = {'mm': .001, 'cm': .01, 'm': 1., 'inch': .0254}


@dataclass
class AssemblyInput:
    path: Path
    base: str
    order: list
    parts: dict
    unit: str
    run_hash: str
    geometry_hash: str
    steps: list
    advisory: bool = False


def safe_file(path):
    # Inspect parents before the file; a Windows junction can point to another volume.
    path = Path(path).absolute()
    for parent in [*reversed(path.parents), path]:
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise InputError('input_invalid', 'Linked files or directories are not accepted.')
    if not path.is_file():
        raise InputError('input_invalid', 'Expected a regular input file.')
    return path


def read_hint(path, budget):
    path = safe_file(location(path))
    hint = read(path)
    if hint.get('schema') != SCHEMA or hint.get('order_semantics') != 'advisory_base_first':
        raise InputError('input_invalid', 'Expected a base-first suggested assembly order.')
    name = hint.get('cad_manifest')
    manifest_path = safe_file(relative(path.parent, name))
    if manifest_path.suffix.lower() != '.json' or sha(manifest_path) != hint.get('cad_manifest_sha256'):
        raise InputError('input_invalid', 'CAD manifest identity differs from the order hint.')
    cad = read(manifest_path)
    unit = cad.get('unit')
    if unit not in UNITS or not isinstance(cad.get('parts'), list) or not cad['parts']:
        raise InputError('input_invalid', 'Declare the CAD length unit and one row per instance.')
    hashes = hint.get('mesh_sha256')
    if not isinstance(hashes, dict):
        raise InputError('input_invalid', 'The order hint must identify every mesh.')
    parts = {}
    files = {name: manifest_path}
    for row in cad['parts']:
        budget.check('order_hint_geometry')
        pid = row.get('part_id')
        if not isinstance(pid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', pid) or pid in parts:
            raise InputError('input_invalid', 'Part IDs must be unique safe identifiers.')
        mesh = safe_file(relative(manifest_path.parent, row.get('mesh')))
        name_from_root = mesh.relative_to(path.parent).as_posix()
        frame = row.get('mesh_coordinate_frame')
        if mesh.suffix.lower() != '.obj' or frame not in ('assembly', 'part_local'):
            raise InputError('input_invalid', 'Each OBJ needs its original coordinate frame.')
        if sha(mesh) != hashes.get(pid):
            raise InputError('input_invalid', 'CAD mesh hash mismatch for ' + pid)
        parts[pid] = {'part_id': pid, 'source_part_id': pid, 'source_mesh': name_from_root,
                      'source_sha256': hashes[pid], 'mesh_coordinate_frame': frame,
                      'assembly_matrix_mm': rigid(row.get('assembly_matrix_mm')).tolist()}
        files[name_from_root] = mesh
    order = hint.get('suggested_order')
    if (not isinstance(order, list) or not all(isinstance(x, str) for x in order)
            or len(order) != len(parts) or set(order) != set(parts) or set(hashes) != set(parts)):
        raise InputError('input_invalid', 'The hint must cover all CAD IDs exactly once.')
    geometry = digest({'unit': unit, 'parts': parts})
    result = AssemblyInput(path, order[0], order[1:], parts, unit, sha(path), geometry, [], True)
    return result, files


def import_hint(path, destination, budget):
    source, files = read_hint(path, budget)
    if any(Path(name).as_posix().casefold() == 'assembly_hint.json' for name in files):
        raise InputError('input_invalid', 'Reserved order hint filename in the CAD manifest.')
    destination.mkdir(parents=True, exist_ok=False)
    for name, original in files.items():
        budget.check('order_hint_import')
        target = relative(destination, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
    target = destination/'assembly_hint.json'
    shutil.copyfile(source.path, target)
    return target
