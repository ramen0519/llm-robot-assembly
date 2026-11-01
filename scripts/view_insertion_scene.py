"""Build and settle the first joint scene; this does not execute insertion."""
import argparse
from pathlib import Path
import mujoco
from assembly_skills import AssemblySkills
from failure_report import TrialRecorder, write_json
from insertion_scene import build_insertion_scene
from run_pick_place import save_snapshot
from workcell_scene import ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root', type=Path, default=ROOT / 'runs/step17')
    args = parser.parse_args()
    recorder = TrialRecorder(args.output_root)
    model, data, config, joint = build_insertion_scene()
    skills = AssemblySkills(model, data, config)
    skills.settle()
    save_snapshot(model, data, config, recorder.run_dir / 'scene.png')
    write_json(recorder.run_dir / 'scene_report.json', {
        'status': 'scene_ready', 'assembly_execution_validated': False, 'joint': joint,
        'config': config, 'observation': skills.observe(), 'simulator': mujoco.__version__,
        'seconds_simulated': data.time})
    print(recorder.run_dir)


if __name__ == '__main__':
    main()
