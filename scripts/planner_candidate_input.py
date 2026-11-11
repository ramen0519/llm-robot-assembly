"""Recheck native planner evidence before selecting the serial robot task order."""
import hashlib
from pathlib import Path
import tempfile
import numpy as np

from experience_memory import read_json
from frame_product import export_product
from multi_post_scene import MULTI_DIMENSIONS
from run_asap_native import assess_candidate
from workcell_scene import ROOT


def load_robot_candidate(path, registry):
    path = Path(path).resolve()
    folder = path.parent
    candidate = read_json(path)
    report_path = folder / 'native_report.json'
    report, manifest = read_json(report_path), read_json(folder / 'normalization.json')
    if (candidate.get('schema') != 'native_assembly_candidate/v1' or candidate.get('robot_execution_validated') is not False
            or candidate.get('source_report_sha256') != hashlib.sha256(report_path.read_bytes()).hexdigest()
            or manifest.get('count') != len(registry) + 1):
        raise ValueError('Planner candidate provenance/product scope mismatch.')
    assessed = assess_candidate(report, manifest, registry)
    if (assessed['status'] != 'candidate' or candidate.get('status') != 'candidate'
            or candidate.get('proposed_order') != assessed['proposed_order'] or candidate.get('errors') != []
            or set(assessed['proposed_order']) != set(registry)):
        raise ValueError('Native candidate is incomplete, modified or outside current robot prerequisites.')
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
        product = export_product(expected_root, MULTI_DIMENSIONS)
        mapping = manifest['binding']['part_map']
        expected_mapping = {str(i): p['id'] for i, p in enumerate(product['parts'][:len(registry) + 1])}
        if mapping != expected_mapping:
            raise ValueError('Native part IDs differ from the current product CAD.')
        for index in mapping:
            expected = expected_root / product['parts'][int(index)]['obj']
            actual = folder / 'asap_input' / (index + '.obj')
            if hashlib.sha256(expected.read_bytes()).digest() != hashlib.sha256(actual.read_bytes()).digest():
                raise ValueError('Planner source CAD differs from the current product model.')
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
    return assessed['proposed_order'], provenance
