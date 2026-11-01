"""Export our generated frame CAD into the existing ASAP mesh-loader input format."""
import argparse
import hashlib
from pathlib import Path
import shutil
import numpy as np
from failure_report import TrialRecorder, write_json
from frame_product import export_product
from frame_assembly_scene import build_frame_scene
from insertion_scene import BASE_XY_TABLE
from multi_post_scene import MULTI_DIMENSIONS
from workcell_scene import ROOT, table_to_world, yaw_rotation


def export_bundle(output_root=None):
    folder = TrialRecorder(output_root or ROOT / 'runs/planner_inputs').run_dir
    product = export_product(folder / 'cad_local', MULTI_DIMENSIONS)
    assembly = folder / 'asap_input'
    assembly.mkdir()
    _, _, registry = build_frame_scene(10)
    config = next(iter(registry.values()))['config']
    world_from_product = np.eye(4)
    world_from_product[:3, :3] = yaw_rotation(config['table']['yaw_deg'])
    world_from_product[:3, 3] = table_to_world(config, [*BASE_XY_TABLE, 0.])
    part_map, poses, frame_map, asap_config = {}, {}, {}, {}
    for index, part in enumerate(product['parts']):
        source_id, name = str(index), part['id']
        part_map[source_id] = name
        shutil.copyfile(folder / 'cad_local' / part['obj'], assembly / (source_id + '.obj'))
        # Official assets/load.py accepts final_state length 3 as a translation.
        # Preserve local mesh vertices and use the same metre unit for translation.
        asap_config[source_id] = {'final_state': part['position_product_m'], 'initial_state': None}
        world_from_cad = world_from_product @ np.asarray(part['assembled_pose_product'])
        cad_from_body = np.eye(4)
        if name != 'base':
            cad_from_body[:3, 3] = registry[name]['body_origin_in_cad_m']
        world_from_body = world_from_cad @ cad_from_body
        if name != 'base' and not np.allclose(world_from_body[:3, 3], registry[name]['joint']['target_part_center_world_m'], atol=1e-10):
            raise ValueError('CAD and simulator body frames disagree.')
        poses[name] = world_from_cad.tolist()
        frame_map[name] = {'world_from_cad_m': world_from_cad, 'cad_from_robot_body_m': cad_from_body,
                           'world_from_robot_body_m': world_from_body}
    write_json(assembly / 'config.json', asap_config)
    binding = {'schema_version': 1, 'source_report_sha256': None, 'source_length_scale_m': 1.,
               'source_frame': 'product_base_bottom_center', 'world_from_source_m': world_from_product,
               'part_map': part_map, 'assembled_part_poses_world_m': poses,
               'assembled_part_pose_reference': 'CAD_local_part_frame',
               'robot_body_frame_mapping': frame_map}
    write_json(folder / 'binding_template.json', binding)
    manifest = {'schema_version': 1, 'product_candidate': product['candidate_id'], 'dimensions_m': vars(MULTI_DIMENSIONS),
                'units': 'metre', 'source_length_scale_m': 1., 'mesh_frame': 'local_part_CAD_frame',
                'assembly_frame': 'product_base_bottom_center', 'fixed_base_source_id': '0', 'part_map': part_map,
                'input_format': 'ASAP assets/load.py OBJ + config.json final_state translation',
                'planner_execution_status': 'not_run', 'robot_execution_validated': False,
                'mesh_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(assembly.glob('*.obj'))},
                'config_sha256': hashlib.sha256((assembly / 'config.json').read_bytes()).hexdigest(),
                'requires_backend_validation': ['length-dependent physics settings', 'support assumptions', 'successful complete disassembly report'],
                'note': 'Input packaging and coordinate identities are not disassembly feasibility or robot execution approval.'}
    write_json(folder / 'planner_input_manifest.json', manifest)
    print(folder, flush=True)
    return folder


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root', type=Path)
    args = parser.parse_args()
    export_bundle(args.output_root)
