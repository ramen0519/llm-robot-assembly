"""Private JSON subprocess for the metered AST interpreter, never Python exec/eval."""
import hashlib
import json
import math
from pathlib import Path
import sys

PROTOCOL = 'post_grasp_ast_vm/v1'
MAX_INPUT_BYTES = 65536
MAX_CONTEXT_BYTES = 16384
MAX_OUTPUT_BYTES = 32768
MAX_STEPS = 5000
VENDOR_SHA256 = 'e14a2b983d08622c23b07be8f6da3d059f0326a535b7e15ea0e78aa3b5e2497c'


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def clone_json(value, *, max_bytes=MAX_CONTEXT_BYTES):
    """Clone exact JSON types only, bounding cycles, depth, nodes and numeric size."""
    active, count = set(), [0]

    def visit(item, depth):
        count[0] += 1
        if depth > 12 or count[0] > 4096:
            raise ValueError('json_complexity_limit')
        kind = type(item)
        if item is None or kind is bool:
            return item
        if kind is int:
            if item.bit_length() > 256:
                raise ValueError('json_integer_limit')
            return item
        if kind is float:
            if not math.isfinite(item):
                raise ValueError('json_nonfinite')
            return item
        if kind is str:
            if len(item) > 16384 or len(item.encode('utf-8')) > 16384:
                raise ValueError('json_text_limit')
            return item
        if kind not in (dict, list) or len(item) > 4096:
            raise ValueError('json_type_or_container_limit')
        identity = id(item)
        if identity in active:
            raise ValueError('json_cycle')
        active.add(identity)
        try:
            if kind is list:
                return [visit(child, depth + 1) for child in item]
            result = {}
            for key, child in item.items():
                if type(key) is not str or len(key.encode('utf-8')) > 256:
                    raise ValueError('json_key_type_or_size')
                result[key] = visit(child, depth + 1)
            return result
        finally:
            active.remove(identity)

    cloned = visit(value, 0)
    if len(json_bytes(cloned)) > max_bytes:
        raise ValueError('json_byte_limit')
    return cloned


def decode_json(raw, *, max_bytes=MAX_INPUT_BYTES):
    if type(raw) is not bytes or len(raw) > max_bytes:
        raise ValueError('json_byte_limit')

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('json_duplicate_key')
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError('json_nonfinite')

    value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                       parse_constant=reject_constant)
    return clone_json(value, max_bytes=max_bytes)


def checked_request(value):
    required = {'schema', 'source', 'context', 'max_steps', 'code_sha256', 'input_sha256'}
    if type(value) is not dict or set(value) != required or value['schema'] != PROTOCOL:
        raise ValueError('request_schema')
    source = value['source']
    if type(source) is not str or not source or len(source.encode('utf-8')) > 16384:
        raise ValueError('source_size')
    if type(value['context']) is not dict:
        raise ValueError('context_type')
    context = clone_json(value['context'])
    if type(value['max_steps']) is not int or not 1 <= value['max_steps'] <= MAX_STEPS:
        raise ValueError('step_budget')
    if (value['code_sha256'] != sha256(source.encode('utf-8'))
            or value['input_sha256'] != sha256(json_bytes(context))):
        raise ValueError('request_digest')
    return source, context, value['max_steps']


def _error(code, exc=None):
    return {'code': code, 'type': None if exc is None else type(exc).__name__}


def run_payload(payload, validator_dir):
    """Trusted child entry. The source is parsed and interpreted, never imported."""
    result = {'schema': PROTOCOL, 'status': 'error', 'output': None,
        'error': None, 'steps': None, 'execution_attempted': False,
        'code_sha256': None, 'input_sha256': None, 'vm_sha256': None,
        'validator_sha256': None, 'max_steps': None}
    vm = None
    try:
        source, context, max_steps = checked_request(payload)
        result.update(code_sha256=payload['code_sha256'], input_sha256=payload['input_sha256'],
                      max_steps=max_steps)
        validator_dir = Path(validator_dir).resolve(strict=True)
        validator_path = validator_dir / 'grasp_skill_candidates.py'
        result['validator_sha256'] = sha256(validator_path.read_bytes())
        vendor_dir = Path(__file__).resolve().parent / 'vendor'
        result['vm_sha256'] = sha256((vendor_dir / 'python_skill_vm.py').read_bytes())
        if result['vm_sha256'] != VENDOR_SHA256:
            raise ValueError('vendor_digest_mismatch')
        # These are host-selected project files, never generated source or input paths.
        sys.path[:0] = [str(vendor_dir), str(validator_dir)]
        import grasp_skill_candidates as validator_module
        import python_skill_vm as vm_module
        if (Path(validator_module.__file__).resolve() != validator_path.resolve()
                or Path(vm_module.__file__).resolve() != (vendor_dir / 'python_skill_vm.py').resolve()):
            raise ValueError('trusted_module_path_mismatch')
        validator_module.inspect_source(source)

        def deny_eligible(value):
            raise vm_module.PolicyError('eligible_forbidden')

        vm = vm_module.VM(source, eligible=deny_eligible, max_steps=max_steps)
        context = clone_json(vm_module.bounded(context))
        result['execution_attempted'] = True
        value = vm.call('propose', [context])
        result['output'] = clone_json(value)
        result['status'] = 'returned'
    except (Exception, KeyboardInterrupt) as exc:
        diagnostic = getattr(exc, 'diagnostic', None)
        code = diagnostic.get('code') if type(diagnostic) is dict else None
        if type(code) is not str or len(code) > 160:
            code = 'runtime_error' if result['execution_attempted'] else 'child_validation_error'
        result['error'] = _error(code, exc)
        result['output'] = None
    finally:
        if vm is not None:
            result['steps'] = vm.steps
    return result


def main():
    # Only one bounded request and one bounded JSON response are accepted.
    try:
        if len(sys.argv) != 2:
            raise ValueError('child_arguments')
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        payload = decode_json(raw)
        result = run_payload(payload, sys.argv[1])
        encoded = json_bytes(result)
        if len(encoded) > MAX_OUTPUT_BYTES:
            result.update(status='error', output=None, error=_error('output_byte_limit'))
            encoded = json_bytes(result)
    except (Exception, KeyboardInterrupt) as exc:
        encoded = json_bytes({'schema': PROTOCOL, 'status': 'error', 'output': None,
                              'error': _error('child_protocol_error', exc)})
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
