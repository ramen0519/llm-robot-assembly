"""Local-only readiness checks. Never constructs a network client or prints secrets."""
import argparse
from decimal import Decimal
import importlib.metadata
import json
import os
from pathlib import Path
import tomllib

from failure_report import TrialRecorder, write_json
from llm_runtime import load_llm_config
from workcell_scene import ROOT


def diagnose(config_path=ROOT / 'config/llm.example.toml', *, output_root=None):
    checks = []
    def check(name, passed, detail):
        checks.append({'name': name, 'passed': bool(passed), 'detail': detail})
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    for spec in project['project']['dependencies']:
        name, expected = spec.split('==')
        try:
            version = importlib.metadata.version(name)
            check('dependency/' + name, version == expected, {'expected': expected, 'installed': version})
        except importlib.metadata.PackageNotFoundError:
            check('dependency/' + name, False, 'missing')
    from insertion_scene import build_insertion_scene
    from insertion_skills import InsertionSkills
    try:
        model, data, config, joint = build_insertion_scene()
        skills = InsertionSkills(model, data, config, joint)
        skills.settle()
        check('physics/scene_settle', data.time >= 2.999, {'simulated_seconds': float(data.time), 'installed_parts': 0})
    except Exception as error:
        check('physics/scene_settle', False, type(error).__name__)
    check('runtime/outside_OneDrive', not any(p.lower() == 'onedrive' for p in ROOT.parts), str(ROOT / 'runtime'))
    live_issues, live = [], None
    key_present = False
    try:
        config = load_llm_config(config_path)
        live = {'model': config.model or None, 'enabled': config.enabled, 'api_key_env': config.api_key_env,
                'max_model_requests': config.max_model_requests, 'max_total_tokens': config.max_total_tokens,
                'max_cost_usd': config.max_cost_usd}
        # Presence only: no key values, length, prefix, files or other environment entries are recorded.
        key_present = bool(os.environ.get(config.api_key_env, '').strip())
        try:
            config.validate(live=True)
        except Exception as error:
            live_issues.append(str(error))
        if not key_present:
            live_issues.append('Configured API key environment variable is absent.')
        reserve_tokens = config.max_input_tokens + config.max_output_tokens
        reserve_cost = (Decimal(config.max_input_tokens) * Decimal(config.input_usd_per_million)
                        + Decimal(config.max_output_tokens) * Decimal(config.output_usd_per_million)) / Decimal(1000000)
        if reserve_tokens > config.max_total_tokens or reserve_cost > Decimal(config.max_cost_usd):
            live_issues.append('A single worst-case request does not fit the configured budget.')
    except Exception:
        # A TOML parser error can quote its input; do not echo arbitrary local config contents.
        live_issues.append('Missing or invalid local LLM configuration; check the disabled example schema.')
    ready = all(c['passed'] for c in checks)
    result = {'status': 'offline_ready' if ready else 'offline_not_ready', 'checks': checks,
              'network_requests': 0, 'api_calls': 0, 'key_present': key_present, 'llm_configuration': live,
              'local_live_prerequisites_present': ready and not live_issues, 'live_issues': live_issues,
              'not_verified': ['key validity', 'account/model access', 'model tool support', 'current pricing', 'live model decisions']}
    folder = TrialRecorder(output_root or ROOT / 'runs/readiness').run_dir
    write_json(folder / 'readiness.json', result)
    result['run_dir'] = str(folder)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--llm-config', type=Path, default=ROOT / 'config/llm.example.toml')
    args = parser.parse_args()
    result = diagnose(args.llm_config)
    raise SystemExit(0 if result['status'] == 'offline_ready' else 1)
