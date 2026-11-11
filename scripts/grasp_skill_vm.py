"""Bounded JSON host for a disposable AST interpreter, not a CPython sandbox."""
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

import grasp_skill_candidates
from grasp_skill_child import (PROTOCOL, MAX_INPUT_BYTES, MAX_OUTPUT_BYTES, MAX_STEPS,
    VENDOR_SHA256, checked_request, clone_json, decode_json, json_bytes, sha256)

HERE = Path(__file__).resolve().parent


def _minimal_environment():
    # Do not inherit API keys, Python configuration, user profiles, or PATH.
    return {key: os.environ[key] for key in ('SystemRoot', 'WINDIR') if key in os.environ}


def _launch_child(raw, validator_dir, timeout_s, *, on_started=None):
    """Drain bounded pipes concurrently, including a bounded stdin writer."""
    command = [sys.executable, '-I', '-S', '-B', '-X', 'utf8',
               str(HERE / 'grasp_skill_child.py'), str(validator_dir)]
    captures = {'stdout': bytearray(), 'stderr': bytearray()}
    overflow, write_failed = threading.Event(), threading.Event()
    with tempfile.TemporaryDirectory(prefix='assembly_skill_vm_') as cwd:
        options = {'stdin': subprocess.PIPE, 'stdout': subprocess.PIPE, 'stderr': subprocess.PIPE,
                   'cwd': cwd, 'env': _minimal_environment(), 'close_fds': True}
        if os.name == 'nt':
            options['creationflags'] = subprocess.CREATE_NO_WINDOW
        process = subprocess.Popen(command, **options)
        if on_started is not None:
            on_started()

        def kill():
            try:
                process.kill()
            except OSError:
                pass

        def drain(name, stream, maximum):
            try:
                while True:
                    chunk = stream.read(min(4096, maximum + 1 - len(captures[name])))
                    if not chunk:
                        break
                    captures[name].extend(chunk)
                    if len(captures[name]) > maximum:
                        overflow.set()
                        kill()
                        break
            except (OSError, ValueError):
                write_failed.set()
            finally:
                stream.close()

        def write():
            try:
                process.stdin.write(raw)
            except (OSError, ValueError):
                write_failed.set()
            finally:
                try:
                    process.stdin.close()
                except (OSError, ValueError):
                    pass

        threads = [threading.Thread(target=write, daemon=True),
                   threading.Thread(target=drain, args=('stdout', process.stdout, MAX_OUTPUT_BYTES), daemon=True),
                   threading.Thread(target=drain, args=('stderr', process.stderr, 4096), daemon=True)]
        timed_out = False
        try:
            for thread in threads:
                thread.start()
            try:
                process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                kill()
                process.wait(timeout=1.0)
        except BaseException:
            kill()
            process.wait(timeout=1.0)
            raise
        finally:
            for thread in threads:
                if thread.ident is not None:
                    thread.join(timeout=0.5)
        if any(thread.is_alive() for thread in threads):
            kill()
            raise RuntimeError('child_io_not_closed')
        return {'returncode': process.returncode, 'stdout': bytes(captures['stdout']),
                'stderr': bytes(captures['stderr']), 'timed_out': timed_out,
                'overflow': overflow.is_set(), 'io_error': write_failed.is_set()}


def execute_source(source, context, *, max_steps=1000, timeout_s=3.0, context_version=1):
    """Return JSON or an explicit error; domain grasp validation belongs to the host.

    A timeout marks execution as potentially attempted, never successful. No
    retry is performed. Source/context are exact bounded JSON data, not objects.
    """
    result = {'schema': PROTOCOL, 'status': 'error', 'output': None, 'error': None,
        'steps': None, 'execution_attempted': False, 'execution_confirmed': False,
        'child_launch_attempted': False, 'child_launched': False,
        'code_sha256': None, 'input_sha256': None,
        'vm_sha256': None, 'validator_sha256': None,
        'limits': {'max_steps': None, 'timeout_s': None, 'max_input_bytes': MAX_INPUT_BYTES,
                   'max_output_bytes': MAX_OUTPUT_BYTES},
        'new_physics_runs': 0, 'execution_authorized': False,
        'security_boundary_certified': False}
    stage = 'input_validation'
    try:
        if (type(max_steps) is not int or not 1 <= max_steps <= MAX_STEPS
                or type(timeout_s) not in (int, float) or not math.isfinite(timeout_s)
                or not 0 < timeout_s <= 5.0):
            raise ValueError('invalid_limits')
        if type(source) is not str or type(context) is not dict:
            raise ValueError('source_or_context_type')
        source_bytes = source.encode('utf-8')
        if not source_bytes or len(source_bytes) > 16384:
            raise ValueError('source_size')
        cloned = clone_json(context)
        code_sha, input_sha = sha256(source_bytes), sha256(json_bytes(cloned))
        request = {'schema': PROTOCOL, 'source': source, 'context': cloned,
                   'max_steps': max_steps, 'code_sha256': code_sha, 'input_sha256': input_sha}
        if type(context_version) is not int or context_version not in (1, 2):
            raise ValueError('Invalid grasp context version.')
        if context_version == 2:
            request['context_version'] = context_version
            result['context_version'] = context_version
        checked_request(request)
        raw = json_bytes(request)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError('input_size')
        stage = 'static_validation'
        grasp_skill_candidates.inspect_source(source, context_version=context_version)
        validator_path = Path(grasp_skill_candidates.__file__).resolve()
        vm_sha = sha256((HERE / 'vendor/python_skill_vm.py').read_bytes())
        if vm_sha != VENDOR_SHA256:
            raise ValueError('vendor_digest_mismatch')
        result.update(code_sha256=code_sha, input_sha256=input_sha, vm_sha256=vm_sha,
                      validator_sha256=sha256(validator_path.read_bytes()))
        result['limits'].update(max_steps=max_steps, timeout_s=float(timeout_s))
        stage = 'child_process'
        result['child_launch_attempted'] = True

        def started():
            result['child_launched'] = True
            result['execution_attempted'] = True

        completed = _launch_child(raw, validator_path.parent, float(timeout_s), on_started=started)
        if completed['timed_out']:
            result['error'] = {'code': 'timeout', 'type': None}
            return result
        if completed['overflow']:
            result['error'] = {'code': 'child_output_limit', 'type': None}
            return result
        if completed['returncode'] != 0 or completed['io_error'] or completed['stderr']:
            result['error'] = {'code': 'child_process_error', 'type': None}
            return result
        stage = 'child_response'
        body = decode_json(completed['stdout'], max_bytes=MAX_OUTPUT_BYTES)
        fields = {'schema', 'status', 'output', 'error', 'steps', 'execution_attempted',
                  'code_sha256', 'input_sha256', 'vm_sha256', 'validator_sha256', 'max_steps'}
        if (type(body) is not dict or set(body) != fields or body['schema'] != PROTOCOL
                or body['status'] not in ('returned', 'error')
                or type(body['execution_attempted']) is not bool
                or type(body['max_steps']) is not int or body['max_steps'] != max_steps
                or any(body[key] != result[key] for key in
                       ('code_sha256', 'input_sha256', 'vm_sha256', 'validator_sha256'))):
            raise ValueError('child_provenance_or_schema')
        steps = body['steps']
        if steps is not None and (type(steps) is not int or not 0 <= steps <= max_steps + 1):
            raise ValueError('child_step_count')
        if body['execution_attempted'] and steps is None:
            raise ValueError('child_execution_count_missing')
        if body['status'] == 'returned':
            if body['error'] is not None or not body['execution_attempted'] or steps is None:
                raise ValueError('child_return_contract')
        elif (body['output'] is not None or type(body['error']) is not dict
              or set(body['error']) != {'code', 'type'}
              or type(body['error']['code']) is not str or not 1 <= len(body['error']['code']) <= 160
              or (body['error']['type'] is not None and
                  (type(body['error']['type']) is not str or not 1 <= len(body['error']['type']) <= 80))):
            raise ValueError('child_error_contract')
        result.update(status=body['status'], output=clone_json(body['output']), error=body['error'],
                      steps=steps, execution_attempted=body['execution_attempted'],
                      execution_confirmed=body['execution_attempted'])
    except (Exception, KeyboardInterrupt) as exc:
        result['error'] = {'code': stage, 'type': type(exc).__name__}
    return result
