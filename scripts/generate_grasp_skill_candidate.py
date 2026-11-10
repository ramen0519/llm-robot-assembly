"""Generate one static skill candidate from a saved, evidence-bound lesson.

No generated Python is executed, no scene is built, and no skill is registered.
Without an explicit LLM configuration this only previews the request.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time

from export_grasp_experience import ROOT, DEFAULT_OUTPUT, digest
from generate_grasp_lesson import (bounded_config, _write_json,
                                  _capture_accounting, _new_run_dir)
from grasp_skill_candidates import (DEFAULT_CANDIDATES, prepare_input, validate_input, build_request,
                                    parse_draft, build_candidate, save_candidate)

DEFAULT_RUNS = ROOT / 'runs/step22i_skill_candidates'


def _base_result(log_dir):
    return {'schema': 'post_grasp_skill_candidate_run/v1', 'status': 'failed',
        'run_dir': str(Path(log_dir).resolve()), 'transport': 'none',
        'API_calls': 0, 'http_requests': 0, 'new_physics_runs': 0,
        'generated_code_executions': 0, 'execution_authorized': False,
        'automatic_skill_registration': False, 'candidate_id': None,
        'candidate_path': None, 'candidate_source_path': None, 'code_sha256': None,
        'generation': None, 'error': None}


def _finish(log_dir, result):
    _write_json(Path(log_dir) / 'result.json', result)
    return result


def _failure(result, exc, stage, ledger=None):
    result['status'] = 'failed'
    result['error'] = {'type': type(exc).__name__, 'stage': stage}
    if ledger is not None:
        ledger.halted = True
        summary = _capture_accounting(result, ledger)
        try:
            ledger.record('candidate_run_failed', error_type=type(exc).__name__,
                          stage=stage, summary=summary)
        except (Exception, KeyboardInterrupt):
            pass
    return result


def generate_once(packet, config, *, log_dir, memory_dir=DEFAULT_OUTPUT,
                  output_dir=DEFAULT_CANDIDATES, client=None):
    """At most one generation, followed by static checking and immutable save.

    Injected clients are offline fixtures, never evidence of an actual API call.
    Unknown generation usage remains reserved; no exception triggers a retry.
    """
    from llm_runtime import ApiLedger, create_openai_client

    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    result, ledger, stage = _base_result(log_dir), None, 'configuration'
    injected = client is not None
    result['transport'] = 'injected_test_client' if injected else 'openai_responses'
    try:
        config = bounded_config(config, live=True)
        _write_json(log_dir / 'config_public.json', config.public())
        stage = 'request_preparation'
        validate_input(packet, memory_dir=memory_dir)
        request = build_request(config, packet)
        request_hash = digest(request)
        generation_request = {**request, 'max_output_tokens': config.max_output_tokens,
                              'store': False, 'service_tier': 'default'}
        generation_hash = digest(generation_request)
        _write_json(log_dir / 'input_packet.json', packet)
        _write_json(log_dir / 'request.json', request)
        _write_json(log_dir / 'generation_request.json', generation_request)
        result.update(packet_id=packet['packet_id'], request_hash=request_hash,
                      generation_request_hash=generation_hash)
        stage = 'budget_reservation'
        ledger = ApiLedger(config, log_dir / 'api_events.jsonl')
        ledger.ensure_room()
        ledger.record('run_transport', transport=result['transport'], actual_api_requests=not injected)
        ledger.record('request_prepared', request_hash=request_hash, request=request)
        ledger.record('generation_request_prepared', request_hash=generation_hash,
                      generation_request=generation_request)
        stage = 'client_initialization'
        if client is None:
            client = create_openai_client(config)
        stage = 'input_count'
        ledger.begin_count(request_hash)
        started = time.monotonic()
        counted = client.responses.input_tokens.count(**request)
        ledger.record('input_count_completed', input_tokens=counted.input_tokens,
                      request_id=getattr(counted, '_request_id', None))
        stage = 'generation_reservation'
        ledger.begin_generation(counted.input_tokens, generation_hash)
        stage = 'model_generation'
        response = client.responses.create(**generation_request)
        body = response.model_dump(mode='json')
        stage = 'usage_settlement'
        ledger.record('model_response_received', response_id=body.get('id'),
            model=body.get('model'), request_id=getattr(response, '_request_id', None),
            response_status=body.get('status'), usage=body.get('usage'),
            elapsed_s=time.monotonic() - started)
        trace_error = None
        try:
            _write_json(log_dir / 'response.json', body)
        except (Exception, KeyboardInterrupt) as exc:
            trace_error = exc
        ledger.settle_usage(body.get('usage'))
        if trace_error is not None:
            stage = 'response_trace'
            raise trace_error
        generation = {'method': 'offline_fixture' if injected else 'openai_responses',
            'transport': result['transport'], 'packet_id': packet['packet_id'],
            'requested_model': config.model, 'returned_model': body.get('model'),
            'response_id': body.get('id'), 'request_hash': request_hash,
            'generation_request_hash': generation_hash, 'response_hash': digest(body),
            'usage': body.get('usage'),
            'simulated_ledger' if injected else 'ledger': ledger.summary()}
        result['generation'] = generation
        _write_json(log_dir / 'generation.json', generation)
        stage = 'draft_validation'
        draft = parse_draft(body, packet)
        _write_json(log_dir / 'draft.json', draft)
        stage = 'candidate_static_validation'
        candidate = build_candidate(packet, draft, generation)
        if candidate is None:
            result['status'] = 'abstained'
            ledger.record('candidate_abstained', response_id=body.get('id'),
                          packet_id=packet['packet_id'], summary=ledger.summary())
        else:
            stage = 'candidate_publication'
            path, created = save_candidate(candidate, memory_dir=memory_dir, output_dir=output_dir)
            result.update(status='candidate_saved', candidate_id=candidate['candidate_id'],
                          candidate_path=str(path), candidate_created=created)
            stage = 'review_source_copy'
            source = candidate['source_code'].encode('utf-8')
            source_hash = hashlib.sha256(source).hexdigest()
            if source_hash != candidate['code_sha256']:
                raise ValueError('Candidate source digest mismatch.')
            source_path = log_dir / 'candidate.py'
            # Review copy only: canonical source stays in candidate JSON. Never import/run it.
            with source_path.open('xb') as stream:
                stream.write(source)
            result.update(candidate_source_path=str(source_path), code_sha256=source_hash)
            ledger.record('candidate_saved', response_id=body.get('id'),
                          candidate_id=candidate['candidate_id'], created=created,
                          code_sha256=source_hash, candidate_source_path=str(source_path),
                          summary=ledger.summary())
        _capture_accounting(result, ledger)
    except (Exception, KeyboardInterrupt) as exc:
        _failure(result, exc, stage, ledger)
    return _finish(log_dir, result)


def run_job(*, lesson, memory_dir=DEFAULT_OUTPUT, output_dir=DEFAULT_CANDIDATES,
            llm_config=None, preview=False, output_root=DEFAULT_RUNS):
    log_dir = _new_run_dir(output_root)
    result, stage = _base_result(log_dir), 'input_preparation'
    try:
        packet = prepare_input(lesson, memory_dir=memory_dir)
        stage = 'configuration'
        from llm_runtime import LlmConfig, load_llm_config
        config = load_llm_config(llm_config) if llm_config is not None else LlmConfig()
        if preview or llm_config is None:
            config = bounded_config(config)
            _write_json(log_dir / 'config_public.json', config.public())
            stage = 'preview_request'
            request = build_request(config, packet)
            _write_json(log_dir / 'input_packet.json', packet)
            _write_json(log_dir / 'request.json', request)
            result.update(status='preview', packet_id=packet['packet_id'],
                          request_hash=digest(request), explicitly_configured=llm_config is not None)
            return _finish(log_dir, result)
    except (Exception, KeyboardInterrupt) as exc:
        return _finish(log_dir, _failure(result, exc, stage))
    return generate_once(packet, config, log_dir=log_dir, memory_dir=memory_dir,
                         output_dir=output_dir)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lesson', type=Path, required=True)
    parser.add_argument('--memory-dir', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_CANDIDATES,
                        help='Unregistered static candidates, separate from executable skills.')
    parser.add_argument('--llm-config', type=Path,
                        help='Explicit budget configuration; omission always means preview.')
    parser.add_argument('--preview', action='store_true',
                        help='Prepare input/request only, without credential access or API calls.')
    parser.add_argument('--output-root', type=Path, default=DEFAULT_RUNS)
    args = parser.parse_args(argv)
    try:
        result = run_job(**vars(args))
    except (Exception, KeyboardInterrupt) as exc:
        result = {'status': 'failed', 'error': {'type': type(exc).__name__, 'stage': 'run_logging'}}
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 1 if result['status'] == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
