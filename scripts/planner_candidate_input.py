"""Recheck native planner evidence before selecting the serial robot task order."""
import hashlib
from pathlib import Path
import tempfile
import numpy as np

from experience_memory import read_json
from frame_product import export_product
from multi_post_scene import MULTI_DIMENSIONS
from run_asap_native import assess_candidate, normalized_mesh, robot_prerequisites
from export_planner_input import LAYOUTS
from insertion_scene import BASE_XY_TABLE
from workcell_scene import ROOT, table_to_world, yaw_rotation


def same_pose(declared, expected):
    matrix = np.asarray(declared, dtype=float)
    return matrix.shape == (4, 4) and np.all(np.isfinite(matrix)) and np.allclose(matrix, expected, atol=1e-10, rtol=0)


def load_robot_candidate(path, registry, *, layout='four_post'):
    path = Path(path).resolve()
    folder = path.parent
    candidate = read_json(path)
    report_path = folder / 'native_report.json'
    report, manifest = read_json(report_path), read_json(folder / 'normalization.json')
    if (candidate.get('schema') != 'native_assembly_candidate/v1' or candidate.get('robot_execution_validated') is not False
            or candidate.get('source_report_sha256') != hashlib.sha256(report_path.read_bytes()).hexdigest()
            or manifest.get('count') != len(registry) + 1 or layout not in LAYOUTS
            or candidate.get('product_layout', 'four_post') != layout):
        raise ValueError('Planner candidate provenance/product scope mismatch.')
    assessed = assess_candidate(report, manifest, registry, layout=layout)
    if (assessed['status'] != 'candidate' or candidate.get('status') != 'candidate'
            or candidate.get('proposed_order') != assessed['proposed_order'] or candidate.get('errors') != []
            or set(assessed['proposed_order']) != set(registry)):
        raise ValueError('Native candidate is incomplete, modified or outside current robot prerequisites.')
    mapping = manifest['binding']['part_map']
    if set(manifest['input_sha256']) != {'config.json', *(index + '.obj' for index in mapping)}:
        raise ValueError('Native input hashes do not cover every selected mesh and pose.')
    constraints = manifest.get('candidate_constraints')
    if constraints is not None:
        declared = folder / 'robot_prerequisites.json'
        actual = robot_prerequisites(mapping, registry)
        if (constraints.get('source') != 'developer_declared_robot_prerequisites'
                or constraints.get('requires') != actual or read_json(declared) != actual
                or constraints.get('sha256') != hashlib.sha256(declared.read_bytes()).hexdigest()):
            raise ValueError('Native robot prerequisites differ from the selected geometry.')
    for name, expected in manifest['input_sha256'].items():
        if Path(name).name != name or (not name.endswith('.obj') and name != 'config.json'):
            raise ValueError('Unknown normalized input filename.')
        if hashlib.sha256((folder / 'asap_normalized' / name).read_bytes()).hexdigest() != expected:
            raise ValueError('Planner mesh/config changed after native validation.')
    # Regenerate only our small product CAD, not robot assets. Check the exact
    # source meshes instead of trusting an old product label or dimension claim.
    runtime = (ROOT / 'runs').resolve()
    runtime.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='candidate-cad-check-', dir=runtime) as temporary:
        expected_root = Path(temporary).resolve()
        if not expected_root.is_relative_to(runtime):
            raise ValueError('Temporary CAD check escaped the runtime folder.')
        product = export_product(expected_root, MULTI_DIMENSIONS, layout=layout)
        if manifest['original_manifest'].get('product_candidate') != product['candidate_id']:
            raise ValueError('Planner candidate product differs from the regenerated CAD.')
        scale = manifest['length_scale_native_per_m']
        if (type(scale) not in (int, float) or not np.isfinite(scale) or not 1 <= scale <= 1000
                or manifest['binding']['source_length_scale_m'] != 1. / scale
                or manifest['source_length_scale_m'] != 1. / scale):
            raise ValueError('Invalid native input length scale.')
        config = next(iter(registry.values()))['config']
        world = np.eye(4)
        world[:3, :3] = yaw_rotation(config['table']['yaw_deg'])
        world[:3, 3] = table_to_world(config, [*BASE_XY_TABLE, 0.])
        if not same_pose(manifest['binding']['world_from_source_m'], world):
            raise ValueError('Planner source and world frames disagree.')
        input_config = read_json(folder / 'asap_input/config.json')
        normalized_config = read_json(folder / 'asap_normalized/config.json')
        if set(normalized_config) != set(mapping):
            raise ValueError('Native pose set differs from the current CAD.')
        expected_mapping = {str(i): p['id'] for i, p in enumerate(product['parts'][:len(registry) + 1])}
        if mapping != expected_mapping:
            raise ValueError('Native part IDs differ from the current product CAD.')
        for index in mapping:
            expected = expected_root / product['parts'][int(index)]['obj']
            actual = folder / 'asap_input' / (index + '.obj')
            if hashlib.sha256(expected.read_bytes()).digest() != hashlib.sha256(actual.read_bytes()).digest():
                raise ValueError('Planner source CAD differs from the current product model.')
            part = product['parts'][int(index)]
            position = part['position_product_m']
            name = part['id']
            world_cad = world @ np.asarray(part['assembled_pose_product'])
            cad_body = np.eye(4)
            if name != 'base':
                cad_body[:3, 3] = registry[name]['body_origin_in_cad_m']
            frames = manifest['binding']['robot_body_frame_mapping'][name]
            if (not same_pose(frames['world_from_cad_m'], world_cad)
                    or not same_pose(frames['cad_from_robot_body_m'], cad_body)
                    or not same_pose(frames['world_from_robot_body_m'], world_cad @ cad_body)
                    or not same_pose(manifest['binding']['assembled_part_poses_world_m'][name], world_cad)):
                raise ValueError('Planner source CAD/body frames disagree with the regenerated part.')
            native_mesh = folder / 'asap_normalized' / (index + '.obj')
            if (input_config[index] != {'final_state': position, 'initial_state': None}
                    or normalized_config[index] != {'final_state': (np.asarray(position) * scale).tolist()}
                    or native_mesh.read_text(encoding='utf-8') != normalized_mesh(expected, scale)):
                raise ValueError('Native solver geometry or poses differ from the regenerated CAD.')
    frames = manifest['binding']['robot_body_frame_mapping']
    for name, entry in registry.items():
        pose = np.asarray(frames[name]['world_from_robot_body_m'], dtype=float)
        joint = entry['joint']
        if (pose.shape != (4, 4) or not np.all(np.isfinite(pose))
                or not np.allclose(pose[:3, 3], joint['target_part_center_world_m'], atol=1e-10, rtol=0)
                or not np.allclose(pose[:3, :3], joint['target_part_rotation_world'], atol=1e-10, rtol=0)):
            raise ValueError('Planner world/CAD/body frames differ from the current robot targets.')
    provenance = {'kind': 'native_disassembly_candidate', 'candidate_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                  'source_report_sha256': candidate['source_report_sha256'], 'source_commit': report['source_commit'],
                  'fixed_base': mapping['0'], 'disassembly_order_ids': list(report['stats']['sequence']),
                  'disassembly_order': [mapping[index] for index in report['stats']['sequence']],
                  'candidate_constraints': report.get('candidate_constraints'), 'order': assessed['proposed_order'],
                  'candidate_is_not_robot_success': True}
    if 'product_layout' in candidate:
        provenance['product_layout'] = layout
    return assessed['proposed_order'], provenance
