"""Planner-geometry contract fixtures; the synthesized report does not run physics."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from export_planner_input import export_bundle
from failure_report import write_json
from frame_assembly_scene import build_frame_scene
from planner_candidate_input import load_robot_candidate
from run_asap_native import EXPECTED_COMMIT, assess_candidate, prepare_normalized, robot_prerequisites

LAYOUT = 'triangle_three_post'


class TrianglePlannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        t = tempfile.TemporaryDirectory()
        cls.addClassCleanup(t.cleanup)
        cls.root = Path(t.name)
        cls.bundle = export_bundle(cls.root/'export', layout=LAYOUT)
        _, cls.manifest = prepare_normalized(cls.bundle, 8)
        _, _, cls.registry = build_frame_scene(8, layout=LAYOUT)
        cls.path = cls.bundle/'assembly_candidate.json'
        cls.mapping = cls.manifest['binding']['part_map']
        inverse = {v:k for k,v in cls.mapping.items()}
        cls.order = ['post_1','sleeve_1','post_2','sleeve_2','post_3','sleeve_3','cover']
        cls.sequence = [inverse[p] for p in reversed(cls.order)]
        edges = []
        remaining = list(cls.mapping)
        for part in cls.sequence:
            after = [p for p in remaining if p != part]
            edges.append({'before':remaining,'after':after,'sim_info':{'part_move':part,'base_part':'0',
                'parts_fix':[],'feasible':True,'action':[0,0,1],'pose':None}})
            remaining = after
        requires = robot_prerequisites(cls.mapping, cls.registry)
        write_json(cls.bundle/'robot_prerequisites.json', requires)
        cls.manifest['candidate_constraints'] = {'source':'developer_declared_robot_prerequisites','requires':requires,
                'sha256':hashlib.sha256((cls.bundle/'robot_prerequisites.json').read_bytes()).hexdigest()}
        cls.report = {'schema':'asap_native_dfs/v1','source_commit':EXPECTED_COMMIT,
                'input_sha256':cls.manifest['input_sha256'],'remaining':['0'],'stats':{'success':True,'sequence':cls.sequence},
                'candidate_constraints':cls.manifest['candidate_constraints'],'selected_edges':edges}
        cls.persist(cls.manifest,cls.report)

    @classmethod
    def persist(cls, manifest, report):
        write_json(cls.bundle/'normalization.json', manifest)
        report_path = cls.bundle/'native_report.json'
        write_json(report_path, report)
        candidate = assess_candidate(report, manifest, cls.registry, layout=LAYOUT)
        candidate['source_report_sha256'] = hashlib.sha256(report_path.read_bytes()).hexdigest()
        write_json(cls.path, candidate)

    def check(self, layout=LAYOUT):
        with patch('planner_candidate_input.ROOT', self.root):
            return load_robot_candidate(self.path, self.registry, layout=layout)

    def test_every_part_and_triangle_cad_pose_are_admitted(self):
        order, provenance = self.check()
        self.assertEqual(order,self.order)
        self.assertEqual(provenance['product_layout'], LAYOUT)
        self.assertEqual(set(self.mapping.values()), {'base', *self.order})
        self.assertEqual(self.manifest['original_manifest']['product_candidate'],'triangle_three_post_v1')
        with self.assertRaises(ValueError):
            self.check('four_post')

    def test_rehashed_normalized_pose_and_prerequisite_change_are_rejected(self):
        config_path = self.bundle/'asap_normalized/config.json'
        before = config_path.read_bytes()
        try:
            bad = json.loads(before)
            bad['1']['final_state'][0] += 1
            write_json(config_path,bad)
            manifest, report = deepcopy(self.manifest), deepcopy(self.report)
            changed = hashlib.sha256(config_path.read_bytes()).hexdigest()
            manifest['input_sha256']['config.json'] = report['input_sha256']['config.json'] = changed
            self.persist(manifest,report)
            with self.assertRaisesRegex(ValueError,'poses'):
                self.check()
        finally:
            config_path.write_bytes(before)
            self.persist(self.manifest,self.report)
        manifest,report = deepcopy(self.manifest),deepcopy(self.report)
        manifest['candidate_constraints']['requires']['7']=[]
        report['candidate_constraints']=manifest['candidate_constraints']
        try:
            self.persist(manifest,report)
            with self.assertRaisesRegex(ValueError,'prerequisites'):
                self.check()
        finally:
            self.persist(self.manifest,self.report)


if __name__=='__main__':
    unittest.main()
