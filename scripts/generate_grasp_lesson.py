"""Preview historical evidence or make one budgeted advisory-lesson request.

No scene is built and no motion is executed. No configuration means preview mode;
an explicit LLM configuration is required before any credential is consulted.
"""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import time
from uuid import uuid4

from export_grasp_experience import ROOT, DEFAULT_OUTPUT, digest
from grasp_lessons import (DEFAULT_LESSONS, NoEvidence, prepare_evidence,
                          build_request, parse_draft, build_lesson, save_lesson)
from retrieve_grasp_experience import query_from_run, retrieve_experiences

DEFAULT_RUNS = ROOT / 'runs/step22h_lessons'


def bounded_config(config, *, live=False):
    """Only tighten the supplied authorization; never raise an existing cap."""
    config.validate()
    inputs = min(config.max_input_tokens, 12000)
    outputs = min(config.max_output_tokens, 4096)
    return replace(config, max_model_requests=min(config.max_model_requests, 1),
        max_input_tokens=inputs, max_output_tokens=outputs,
        max_total_tokens=min(config.max_total_tokens, inputs + outputs),
        max_cost_usd=str(min(Decimal(config.max_cost_usd), Decimal('0.01')))).validate(live=live)


def _write_json(path, value):
    # Exclusive creation preserves evidence if a directory is accidentally reused.
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    with Path(path).open('x', encoding='utf-8') as stream:
        stream.write(text)


def _base_result(log_dir):
    return {'schema': 'post_grasp_lesson_run/v1', 'status': 'failed',
        'run_dir': str(Path(log_dir).resolve()), 'transport': 'none',
        'API_calls': 0, 'http_requests': 0,
        'new_physics_runs': 0, 'execution_authorized': False,
        'automatic_skill_registration': False, 'lesson_id': None,
        'lesson_path': None, 'generation': None, 'error': None}


def _finish(log_dir, result):
    _write_json(Path(log_dir) / 'result.json', result)
    return result


def _capture_accounting(result, ledger):
    summary = ledger.summary()
    if result['transport'] == 'injected_test_client':
        result['simulated_ledger'] = summary
        result['simulated_model_requests'] = summary['api_calls']
        result['simulated_http_requests'] = summary['http_requests']
        result['API_calls'] = result['http_requests'] = 0
    else:
        result['ledger'] = summary
        result['API_calls'] = summary['api_calls']
        result['http_requests'] = summary['http_requests']
    return summary


def _failure(result, exc, stage, ledger=None):
    # Exception messages and SDK response bodies can contain sensitive input.
    result['status'] = 'failed'
    result['error'] = {'type': type(exc).__name__, 'stage': stage}
    if ledger is not None:
        ledger.halted = True
        summary = _capture_accounting(result, ledger)
        try:
            ledger.record('lesson_run_failed', error_type=type(exc).__name__,
                          stage=stage, summary=summary)
        except (Exception, KeyboardInterrupt):
            # Preserve the original error and the in-memory usage/reservation.
            pass
    return result


def generate_once(packet, config, *, log_dir, memory_dir=DEFAULT_OUTPUT,
                  output_dir=DEFAULT_LESSONS, client=None):
    """Generate at most once; an injected client allows offline lifecycle tests.

    Use a fresh log directory. Usage is settled before interpreting a refusal,
    incomplete response or invalid lesson. Unknown usage remains reserved and
    no failure triggers a second request. This is a per-run, not global, budget.
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
        request = build_request(config, packet)
        request_hash = digest(request)
        generation_request = {**request, 'max_output_tokens': config.max_output_tokens,
                              'store': False, 'service_tier': 'default'}
        generation_request_hash = digest(generation_request)
        _write_json(log_dir / 'evidence_packet.json', packet)
        _write_json(log_dir / 'request.json', request)
        _write_json(log_dir / 'generation_request.json', generation_request)
        result['packet_id'] = packet['packet_id']
        result['request_hash'] = request_hash
        result['generation_request_hash'] = generation_request_hash
        stage = 'budget_reservation'
        ledger = ApiLedger(config, log_dir / 'api_events.jsonl')
        ledger.ensure_room()
        ledger.record('run_transport', transport=result['transport'],
                      actual_api_requests=not injected)
        ledger.record('request_prepared', request_hash=request_hash, request=request)
        ledger.record('generation_request_prepared', request_hash=generation_request_hash,
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
        ledger.begin_generation(counted.input_tokens, generation_request_hash)

        stage = 'model_generation'
        response = client.responses.create(**generation_request)
        body = response.model_dump(mode='json')
        stage = 'usage_settlement'
        ledger.record('model_response_received', response_id=body.get('id'),
            model=body.get('model'), request_id=getattr(response, '_request_id', None),
            response_status=body.get('status'), usage=body.get('usage'),
            elapsed_s=time.monotonic() - started)
        # Keep the raw response even when its usage or interpretation is invalid.
        # A trace-write failure still attempts accounting before it can stop us.
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
            'requested_model': config.model,
            'returned_model': body.get('model'), 'response_id': body.get('id'),
            'request_hash': request_hash, 'response_hash': digest(body),
            'generation_request_hash': generation_request_hash,
            'usage': body.get('usage'),
            'simulated_ledger' if injected else 'ledger': ledger.summary()}
        result['generation'] = generation
        _write_json(log_dir / 'generation.json', generation)
        stage = 'draft_validation'
        draft = parse_draft(body, packet)
        _write_json(log_dir / 'draft.json', draft)
        stage = 'lesson_construction'
        lesson = build_lesson(packet, draft, generation)
        if lesson is None:
            result['status'] = 'abstained'
            ledger.record('lesson_abstained', response_id=body.get('id'),
                          packet_id=packet['packet_id'], summary=ledger.summary())
        else:
            stage = 'lesson_publication'
            path, created = save_lesson(lesson, memory_dir=memory_dir, output_dir=output_dir)
            result.update(status='lesson_saved', lesson_id=lesson['lesson_id'],
                          lesson_path=str(path), lesson_created=created)
            ledger.record('lesson_saved', response_id=body.get('id'),
                          lesson_id=lesson['lesson_id'], created=created,
                          summary=ledger.summary())
        _capture_accounting(result, ledger)
    except (Exception, KeyboardInterrupt) as exc:
        _failure(result, exc, stage, ledger)
    return _finish(log_dir, result)


def _new_run_dir(output_root):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    path = Path(output_root) / (stamp + '_' + uuid4().hex[:12])
    path.mkdir(parents=True, exist_ok=False)
    return path


def run_job(*, run_dir, part, failure_code=None, failure_phase=None,
            memory_dir=DEFAULT_OUTPUT, output_dir=DEFAULT_LESSONS,
            llm_config=None, preview=False, output_root=DEFAULT_RUNS):
    """Freeze retrieval evidence, preview by default, or explicitly generate."""
    log_dir = _new_run_dir(output_root)
    result, stage = _base_result(log_dir), 'query_preparation'
    try:
        query = query_from_run(run_dir, part, failure_code=failure_code,
                               failure_phase=failure_phase)
        _write_json(log_dir / 'query.json', query)
        stage = 'retrieval'
        search = retrieve_experiences(query, memory_dir=memory_dir)
        _write_json(log_dir / 'search.json', search)
        if not search['cards']:
            result['status'] = 'no_evidence'
            return _finish(log_dir, result)
        stage = 'evidence_preparation'
        packet = prepare_evidence(search, memory_dir=memory_dir)
        stage = 'configuration'
        # No OpenAI SDK or credential access occurs on the preview path.
        from llm_runtime import LlmConfig, load_llm_config
        config = load_llm_config(llm_config) if llm_config is not None else LlmConfig()
        if preview or llm_config is None:
            config = bounded_config(config)
            _write_json(log_dir / 'config_public.json', config.public())
            stage = 'preview_request'
            request = build_request(config, packet)
            _write_json(log_dir / 'evidence_packet.json', packet)
            _write_json(log_dir / 'request.json', request)
            result.update(status='preview', packet_id=packet['packet_id'],
                request_hash=digest(request), explicitly_configured=llm_config is not None)
            return _finish(log_dir, result)
    except NoEvidence:
        result['status'] = 'no_evidence'
        return _finish(log_dir, result)
    except (Exception, KeyboardInterrupt) as exc:
        return _finish(log_dir, _failure(result, exc, stage))
    return generate_once(packet, config, log_dir=log_dir, memory_dir=memory_dir,
                         output_dir=output_dir)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--part', required=True)
    parser.add_argument('--failure-code')
    parser.add_argument('--failure-phase')
    parser.add_argument('--memory-dir', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_LESSONS,
                        help='Immutable advisory lesson store, separate from runs/memory/skills.')
    parser.add_argument('--llm-config', type=Path,
                        help='Explicit budget configuration; omitting it always previews.')
    parser.add_argument('--preview', action='store_true',
                        help='Save evidence and request without reading credentials or calling the API.')
    parser.add_argument('--output-root', type=Path, default=DEFAULT_RUNS,
                        help='Parent for a new unique run log directory.')
    args = parser.parse_args(argv)
    try:
        result = run_job(**vars(args))
    except (Exception, KeyboardInterrupt) as exc:
        # Includes inaccessible output directories; never emit an SDK traceback.
        result = {'status': 'failed', 'error': {'type': type(exc).__name__,
                                              'stage': 'run_logging'}}
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 1 if result['status'] == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
