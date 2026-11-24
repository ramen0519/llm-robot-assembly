"""Fresh memory and supported failure boundaries; no seeded experience or paid calls."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from assembly_v12.action_contract import repairable_grasp
from assembly_v12.entry import main
from assembly_v12.memory import Library


class CleanStart(unittest.TestCase):
    def test_empty_memory_and_collection_snapshot_do_not_seed_examples(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);memory=root/'memory'
            out=io.StringIO()
            with redirect_stdout(out):main(['memory-status','--memory-root',str(memory)])
            status=json.loads(out.getvalue())
            self.assertEqual(status['registered_cases'],0)
            self.assertEqual(status['guidance_cards'],0)
            self.assertEqual(status['by_split'],dict.fromkeys(('development','collection','evaluation'),0))
            library=Library(memory)
            self.assertEqual(library.snapshot['cases'],[])
            main(['freeze-memory',str(memory),str(root/'frozen.json')])
            self.assertEqual(json.loads((root/'frozen.json').read_text())['cases'],[])
            self.assertEqual(list(memory.iterdir()),[])

    def test_current_executor_imports_without_legacy_graph_or_generated_code_stack(self):
        program="""import sys
sys.path.insert(0,'scripts')
import assembly_v12.execution
old={'assembly_graph','pick_place_graph','openai_policy','experience_memory',
     'grasp_skill_runtime','grasp_skill_vm','grasp_skill_selector','sleeve_memory_policy'}
assert not old.intersection(sys.modules), old.intersection(sys.modules)
"""
        subprocess.run([sys.executable,'-B','-c',program],cwd=ROOT,check=True,timeout=60,capture_output=True,text=True)

    def test_actual_drop_is_not_a_copy_rejection(self):
        issue={'phase':'pick_lift','failure_code':'PART_LOST','source':'live_simulation'}
        self.assertFalse(repairable_grasp(issue))
        self.assertTrue(repairable_grasp({**issue,'source':'mujoco_preflight_copy'}))
        self.assertFalse(repairable_grasp({**issue,'source':'mujoco_preflight_copy','phase':None}))


if __name__=='__main__':unittest.main()
