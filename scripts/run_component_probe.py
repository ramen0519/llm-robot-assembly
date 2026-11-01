"""One robot-installed component on a declared preassembled, still-free fixture."""
import argparse
import csv
from pathlib import Path
import mujoco
from failure_report import write_json
from frame_assembly_scene import build_frame_scene
from frame_assembly_skills import FrameSkills
from run_insertion import INSERTION_TRACE_FIELDS
from run_pick_place import AssemblyRecorder, save_snapshot
from workcell_scene import ROOT, part_name


def execute_trial(component='sleeve_1', *, cover_grip='center', snapshots=False):
    if component not in ('sleeve_1', 'sleeve_2', 'sleeve_3', 'sleeve_4', 'cover'):
        raise ValueError('This probe installs one sleeve or the cover.')
    recorder = AssemblyRecorder(ROOT / 'runs/component_probes', skill='component_probe/v1',
                                validation_profile='compound_component_on_preassembled_fixture/v1')
    skills, result, initial_installed = None, None, set()
    status, error = 'failed', None
    try:
        model, data, registry = build_frame_scene(10 if component == 'cover' else 5 + int(component[-1]), cover_grip=cover_grip)
        initial_installed = set(registry) - {component}
        # Initial fixture configuration only, before any physical episode begins.
        # These components remain free bodies and are not robot installation successes.
        for name in initial_installed:
            entry = registry[name]
            data.joint(part_name(entry['config']) + '_free').qpos[:3] = entry['joint']['target_part_center_world_m']
        mujoco.mj_forward(model, data)
        write_json(recorder.run_dir / 'resolved_config.json', {
            'environment': 'simulation', 'component': component, 'cover_grip': cover_grip,
            'initial_preassembled_parts': sorted(initial_installed | {'base'}),
            'robot_installations_goal': 1, 'registry': registry, 'simulator_version': mujoco.__version__})
        with recorder.trace_path.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=INSERTION_TRACE_FIELDS)
            writer.writeheader()
            def sample(row):
                writer.writerow(row)
                recorder.remember(skills, row)
            def stage(name):
                stream.flush()
                print(f'[{data.time:8.3f} s] {component}: {name}', flush=True)
            skills = FrameSkills(model, data, component, registry, initial_installed, sample, stage)
            skills.settle()
            skills.pick(component)
            result = skills.insert(component, registry[component]['config']['target']['id'])
            status = 'success'
    except (Exception, KeyboardInterrupt) as exc:
        status = recorder.fail(exc, skills, 23)
        error = str(exc)
    if skills and snapshots:
        save_snapshot(skills.model, skills.data, skills.config, recorder.run_dir / 'final.png')
    report = {'status': status, 'error': error, 'run_dir': str(recorder.run_dir), 'component': component,
              'initial_preassembled_parts': sorted(initial_installed | {'base'}),
              'robot_installed_parts': int(status == 'success'), 'whole_frame_robot_assembly': False,
              'result': result, 'samples': recorder.samples,
              'simulated_seconds': float(skills.data.time) if skills else None}
    write_json(recorder.run_dir / 'result.json', report)
    print(f'{status}: {recorder.run_dir}', flush=True)
    if error:
        print(error)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--component', choices=['sleeve_1', 'sleeve_2', 'sleeve_3', 'sleeve_4', 'cover'], default='sleeve_1')
    parser.add_argument('--cover-grip', choices=['center', 'edge'], default='center')
    parser.add_argument('--snapshots', action='store_true')
    args = parser.parse_args()
    result = execute_trial(args.component, cover_grip=args.cover_grip, snapshots=args.snapshots)
    raise SystemExit(0 if result['status'] == 'success' else 1)
