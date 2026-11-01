from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from assembly_graph import run_assembly_graph
from assembly_policy import ScriptedPolicy
from insertion_scene import build_insertion_scene
from insertion_skills import InsertionSkills


class MemoryContextTests(unittest.TestCase):
    def test_retrieved_evidence_passed_as_data_and_recorded_without_authorizing_motion(self):
        m, d, c, j = build_insertion_scene()
        skills = InsertionSkills(m, d, c, j)
        contexts, events = [], []
        class StopPolicy(ScriptedPolicy):
            def propose(self, context):
                contexts.append(context)
                return super().propose(context) | {'skill': 'stop', 'reason': 'Context contract test complete.'}
        record = {'record_id': 'test_fixture', 'outcome': 'failed', 'failure_codes': ['ALIGNMENT_OUT_OF_TOLERANCE']}
        state = run_assembly_graph(skills, mode='single', policies={'single': StopPolicy()},
                                   failure_handler=lambda exc: self.fail(str(exc)), on_event=events.append,
                                   memory_lookup=lambda failure: [record])
        self.assertEqual(state['status'], 'stopped')
        self.assertEqual(contexts[0]['experiences'], [record])
        self.assertEqual(contexts[0]['allowed_skills'], ['pick', 'stop'])
        self.assertAlmostEqual(d.time, 3.)
        self.assertEqual(next(e for e in events if e['node'] == 'decide')['experience_ids'], ['test_fixture'])


if __name__ == '__main__':
    unittest.main()
