"""Write the first actual-observation policy request without credentials or network."""
import argparse
import json

from assembly_policy import allowed_skills
from failure_report import TrialRecorder, write_json
from insertion_scene import build_insertion_scene
from insertion_skills import InsertionSkills
from llm_runtime import load_llm_config
from openai_policy import build_request
from pick_place_graph import _snapshot
from workcell_scene import ROOT


def preview(config_path=ROOT / 'config/llm.example.toml', *, output_root=None):
    config = load_llm_config(config_path)
    model, data, scene, joint = build_insertion_scene()
    skills = InsertionSkills(model, data, scene, joint)
    skills.settle()
    context = {'task_id': 'four_post_frame_first_joint/v1', 'part_id': scene['part']['id'],
               'target_id': scene['target']['id'], 'active_role': 'assembly', 'observation_revision': 1,
               'completed': [], 'allowed_skills': allowed_skills([], None, 0, 1),
               'recovery_budget_remaining': 1, 'successful_recoveries': 0, 'condition_fingerprint': None,
               'observation': _snapshot(skills), 'failure': None, 'failures': [], 'experiences': [],
               'last_verification': None}
    request = build_request(config, context)
    folder = TrialRecorder(output_root or ROOT / 'runs/step21_llm_preview').run_dir
    write_json(folder / 'request.json', request)
    result = {'status': 'preview_only', 'api_calls': 0, 'http_requests': 0,
              'llm_response_received': False, 'robot_installed_parts': 0,
              'physical_execution': 'settle_only', 'simulation_time_s': float(data.time),
              'request_bytes': len(json.dumps(request, ensure_ascii=False).encode('utf-8')),
              'configured_model': config.model or None, 'run_dir': str(folder)}
    write_json(folder / 'result.json', result)
    print(json.dumps(result, ensure_ascii=False))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=ROOT / 'config/llm.example.toml')
    args = parser.parse_args()
    preview(args.config)
