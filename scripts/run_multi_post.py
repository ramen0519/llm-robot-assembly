"""Sequential physical post installation; all parts exist from initialization."""
import argparse
import csv
from pathlib import Path
from failure_report import write_json
from multi_post_scene import build_multi_post_scene
from multi_post_skills import MultiPostSkills
from run_insertion import INSERTION_TRACE_FIELDS
from run_pick_place import AssemblyRecorder, save_snapshot
from workcell_scene import ROOT


def execute_trial(*, count=2, snapshots=False, output_root=None):
    recorder = AssemblyRecorder(output_root or ROOT / 'runs/step23', skill='sequential_posts/v1',
                                validation_profile='multiple_post_insertion/v1')
    skills, registry, installed, results = None, {}, set(), []
    status, error = 'failed', None
    try:
        model, data, registry = build_multi_post_scene(count)
        write_json(recorder.run_dir / 'resolved_config.json', {
            'registry': registry, 'product_parts_present': count + 1, 'robot_installations_goal': count,
            'environment': 'simulation', 'observation_source': 'sim_ground_truth',
            'orchestration': 'scripted', 'api_calls': 0})
        with recorder.trace_path.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=['active_part_id', *INSERTION_TRACE_FIELDS])
            writer.writeheader()
            def sample(row):
                writer.writerow({'active_part_id': skills.active, **row})
                recorder.remember(skills, row)
            def stage(name):
                print(f'[{data.time:7.3f} s] {skills.active}: {name}', flush=True)
            for active in registry:
                skills = MultiPostSkills(model, data, active, registry, installed, sample, stage)
                skills.settle()
                skills.pick(active)
                result = skills.insert(active, registry[active]['config']['target']['id'])
                installed.add(active)
                results.append(result)
                if snapshots:
                    save_snapshot(model, data, skills.config, recorder.run_dir / f'after_{active}.png')
            # Verify current active and every earlier assembly again at the final time.
            row = skills.verifier.sample(data, skills.motion.reference, allow_support=True, allow_grasp=False)
            skills.verifier.verify_insertion([row])
            skills.verifier.check_other_parts(data)
            status = 'success'
    except (Exception, KeyboardInterrupt) as exc:
        status = recorder.fail(exc, skills, 23)
        error = str(exc)
    result = {'status': status, 'error': error, 'run_dir': str(recorder.run_dir),
              'product_parts_present': count + 1, 'robot_installed_parts': len(installed),
              'robot_installations_goal': count, 'installed_part_ids': sorted(installed), 'results': results,
              'simulated_seconds': float(skills.data.time) if skills else None, 'samples': recorder.samples,
              'ten_part_assembly_complete': False, 'observation': skills.observe() if skills else None}
    write_json(recorder.run_dir / 'result.json', result)
    print(f'{status}: {recorder.run_dir}', flush=True)
    if error:
        print(error)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--posts', type=int, choices=[2, 3, 4], default=2)
    parser.add_argument('--snapshots', action='store_true')
    args = parser.parse_args()
    result = execute_trial(count=args.posts, snapshots=args.snapshots)
    raise SystemExit(0 if result['status'] == 'success' else 1)
