"""Diagnostic sampling must not skip physics verification or alter its outcome."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import mujoco
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_pick_place
from frame_assembly_scene import build_frame_scene
from frame_assembly_skills import FrameSkills


class DiagnosticSamplingTests(unittest.TestCase):
    def test_same_physics_and_every_sample_with_fewer_diagnostic_snapshots(self):
        model, initial, registry = build_frame_scene(10)
        outputs=[]
        for interval in (0., .02):
            from copy import copy
            data=copy(initial)
            with tempfile.TemporaryDirectory() as tmp:
                recorder=run_pick_place.AssemblyRecorder(Path(tmp),diagnostic_interval_s=interval)
                rows=[]
                def sample(row):
                    rows.append((row['stage'],row['simulation_time_s']))
                    recorder.remember(skills,row)
                skills=FrameSkills(model,data,'post_1',registry,set(),sample)
                with patch('run_pick_place.capture_observation',wraps=run_pick_place.capture_observation) as capture:
                    skills.settle()
                    calls=capture.call_count
                state=np.empty(mujoco.mj_stateSize(model,mujoco.mjtState.mjSTATE_INTEGRATION))
                mujoco.mj_getState(model,data,state,mujoco.mjtState.mjSTATE_INTEGRATION)
                self.assertEqual(recorder.samples,len(rows))
                self.assertLessEqual(data.time-recorder.last_diagnostic_time,.02000001)
                self.assertTrue(recorder.last_valid['valid'])
                outputs.append((state,rows,calls))
        np.testing.assert_array_equal(outputs[0][0],outputs[1][0])
        self.assertEqual(outputs[0][1],outputs[1][1])
        self.assertGreater(len(outputs[1][1]),1000)
        self.assertLess(outputs[1][2],outputs[0][2]/5)

    def test_rejects_invalid_interval(self):
        for value in (-1, .1, float('nan'), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                run_pick_place.AssemblyRecorder(Path('unused'),diagnostic_interval_s=value)


if __name__=='__main__':
    unittest.main()
