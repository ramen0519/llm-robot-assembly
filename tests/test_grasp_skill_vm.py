"""Real isolated AST-child calculations, never robot or MuJoCo validation."""
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'scripts'))
import grasp_skill_vm as host
import grasp_skill_child as child

# Exact source from the local 22I Luna candidate 96fb203d...003bee.
# This is a calculation regression fixture, not evidence of assembly success.
LUNA_SOURCE = '''def propose(ctx):
    if ctx["observation_is_current"] != True:
        return None
    if ctx["holding_part"] != False:
        return None
    if ctx["remaining_proposals"] <= 0:
        return None
    if ctx["failure_code"] != "PLANNED_CONTACT" or ctx["failure_phase"] != "insert_lower/preflight":
        return None
    size = ctx["part_size_xyz_mm"]
    prev = ctx["previous_grasp"]
    x = prev["x_mm"]
    y = prev["y_mm"]
    z = prev["z_mm"]
    yaw = prev["jaw_yaw_deg"]
    margin = 1
    limit = size[2] / 2
    if x < -size[0] / 2 or x > size[0] / 2:
        return None
    if y < -size[1] / 2 or y > size[1] / 2:
        return None
    if z < -limit or z > limit or z + margin > limit:
        return None
    return {"x_mm": x, "y_mm": y, "z_mm": z + margin, "jaw_yaw_deg": yaw}'''


def context():
    return {'part_size_xyz_mm': [20., 20., 60.],
        'previous_grasp': {'x_mm': 0., 'y_mm': 0., 'z_mm': 0., 'jaw_yaw_deg': 0.},
        'failure_code': 'PLANNED_CONTACT', 'failure_phase': 'insert_lower/preflight',
        'holding_part': False, 'observation_is_current': True, 'remaining_proposals': 1}


def source_with(expression, *, setup=''):
    return ('def propose(ctx):\n'
            '    size = ctx["part_size_xyz_mm"]\n'
            '    prev = ctx["previous_grasp"]\n' + setup +
            '    value = ' + expression + '\n'
            '    return {"x_mm": 0, "y_mm": 0, "z_mm": value, "jaw_yaw_deg": 0}\n')


class GraspSkillVmTests(unittest.TestCase):
    def test_exact_luna_source_runs_in_real_child_and_does_not_mutate_context(self):
        before = context()
        data = deepcopy(before)
        result = host.execute_source(LUNA_SOURCE, data)
        self.assertEqual(hashlib.sha256(LUNA_SOURCE.encode()).hexdigest(),
                         '92418c389b6d2a178cbfffe978a84babd93b835ccb1cc3b29d4e551ca371b2a8')
        self.assertEqual(result['status'], 'returned', result)
        self.assertEqual(result['output'], {'x_mm': 0., 'y_mm': 0., 'z_mm': 1., 'jaw_yaw_deg': 0.})
        self.assertEqual(data, before)
        self.assertTrue(result['child_launched'])
        self.assertTrue(result['execution_confirmed'])
        self.assertEqual(result['vm_sha256'], child.VENDOR_SHA256)
        self.assertEqual(result['input_sha256'], child.sha256(child.json_bytes(before)))
        self.assertGreater(result['steps'], 0)
        self.assertEqual(result['new_physics_runs'], 0)
        self.assertFalse(result['execution_authorized'])

    def test_real_child_abstention_is_returned_none_not_failure(self):
        data = context()
        data['holding_part'] = True
        result = host.execute_source(LUNA_SOURCE, data)
        self.assertEqual(result['status'], 'returned', result)
        self.assertIsNone(result['output'])
        self.assertIsNone(result['error'])
        self.assertTrue(result['execution_attempted'])

    def test_runtime_arithmetic_undefined_local_and_step_limits_are_explicit(self):
        cases = [(source_with('1 / 0'), 1000, 'runtime_error'),
                 (source_with('missing', setup='    if False:\n        missing = 1\n'),
                  1000, 'python_undefined_local'),
                 (LUNA_SOURCE, 1, 'python_step_limit')]
        for source, budget, code in cases:
            with self.subTest(code=code):
                result = host.execute_source(source, context(), max_steps=budget)
                self.assertEqual(result['status'], 'error', result)
                self.assertEqual(result['error']['code'], code)
                self.assertIsNone(result['output'])
                self.assertTrue(result['execution_confirmed'])

    def test_legal_ast_cannot_allocate_unbounded_text_or_lists(self):
        for expression in ('"a" * 1000000', 'size * 1000000'):
            with self.subTest(expression=expression):
                source = source_with(expression)
                host.grasp_skill_candidates.inspect_source(source)
                result = host.execute_source(source, context())
                self.assertEqual(result['status'], 'error', result)
                self.assertEqual(result['error']['code'], 'python_allocation_limit')

    def test_import_file_access_and_callbacks_are_refused_before_spawn(self):
        sources = ['import os\n' + LUNA_SOURCE, source_with('open("forbidden")'),
                   source_with('eligible(prev)'), source_with('ctx.__class__')]
        with patch.object(host, '_launch_child') as launch:
            for source in sources:
                result = host.execute_source(source, context())
                self.assertEqual(result['status'], 'error')
                self.assertFalse(result['child_launched'])
                self.assertFalse(result['execution_attempted'])
            launch.assert_not_called()

    def test_child_rechecks_static_contract_even_when_parent_guard_is_bypassed(self):
        with patch.object(host.grasp_skill_candidates, 'inspect_source', return_value={}):
            result = host.execute_source(source_with('open("forbidden")'), context())
        self.assertEqual(result['status'], 'error', result)
        self.assertEqual(result['error']['code'], 'child_validation_error')
        self.assertTrue(result['child_launched'])
        self.assertFalse(result['execution_attempted'])

    def test_context_types_cycles_nonfinite_and_limits_rejected_before_spawn(self):
        circular = context()
        circular['previous_grasp'] = circular
        cases = [circular, {'bad': object()}, {'bad': float('nan')}, {'bad': (1, 2)},
                 {1: 'bad'}, {'bad': 2 ** 300}, {'bad': 'x' * 16385}]
        with patch.object(host, '_launch_child') as launch:
            for value in cases:
                with self.subTest(type=type(value)):
                    result = host.execute_source(LUNA_SOURCE, value)
                    self.assertEqual(result['status'], 'error')
                    self.assertFalse(result['execution_attempted'])
            for kwargs in ({'max_steps': 5001}, {'max_steps': True}, {'timeout_s': 5.01},
                           {'timeout_s': float('inf')}, {'timeout_s': 0}):
                result = host.execute_source(LUNA_SOURCE, context(), **kwargs)
                self.assertEqual(result['status'], 'error')
            launch.assert_not_called()

    def test_timeout_is_not_returned_or_retried(self):
        def timeout(*args, **kwargs):
            kwargs['on_started']()
            return {'timed_out': True}
        with patch.object(host, '_launch_child', side_effect=timeout) as launch:
            result = host.execute_source(LUNA_SOURCE, context(), timeout_s=0.1)
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(result['error']['code'], 'timeout')
        self.assertEqual(result['status'], 'error')
        self.assertIsNone(result['output'])
        self.assertTrue(result['execution_attempted'])
        self.assertFalse(result['execution_confirmed'])

    def test_spawn_failure_does_not_claim_child_launched(self):
        with patch.object(host.subprocess, 'Popen', side_effect=OSError('PRIVATE_EXCEPTION')):
            result = host.execute_source(LUNA_SOURCE, context())
        self.assertTrue(result['child_launch_attempted'])
        self.assertFalse(result['child_launched'])
        self.assertFalse(result['execution_attempted'])
        self.assertNotIn('PRIVATE_EXCEPTION', json.dumps(result))

    def test_process_is_isolated_and_environment_excludes_credentials(self):
        original = host.subprocess.Popen
        calls = []
        def observe(command, **kwargs):
            calls.append((command, kwargs))
            return original(command, **kwargs)
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'synthetic-test-only',
                                     'PYTHONPATH': 'synthetic-untrusted-path'}), \
                patch.object(host.subprocess, 'Popen', side_effect=observe):
            result = host.execute_source(LUNA_SOURCE, context())
        self.assertEqual(result['status'], 'returned', result)
        command, options = calls[0]
        self.assertEqual(command[:6], [sys.executable, '-I', '-S', '-B', '-X', 'utf8'])
        self.assertLessEqual(set(options['env']), {'SystemRoot', 'WINDIR'})
        self.assertNotIn('OPENAI_API_KEY', options['env'])
        self.assertNotIn('PYTHONPATH', options['env'])
        self.assertNotEqual(Path(options['cwd']), Path.cwd())
        self.assertFalse(Path(options['cwd']).exists())
        self.assertTrue(options['close_fds'])

    def test_duplicate_keys_and_forged_child_provenance_are_rejected(self):
        with self.assertRaises(ValueError):
            child.decode_json(b'{"x":1,"x":2}')
        completed = {'timed_out': False, 'overflow': False, 'returncode': 0,
                     'io_error': False, 'stderr': b'', 'stdout': b'{"status":"returned"}'}
        with patch.object(host, '_launch_child', return_value=completed):
            result = host.execute_source(LUNA_SOURCE, context())
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['error']['code'], 'child_response')
        self.assertIsNone(result['output'])

    def test_pipe_reader_stops_at_the_output_limit(self):
        class Process:
            def __init__(self):
                self.stdin, self.stdout, self.stderr = io.BytesIO(), io.BytesIO(b'x' * 100000), io.BytesIO()
                self.returncode, self.killed = 0, False
            def wait(self, timeout):
                return 0
            def kill(self):
                self.killed = True
        process = Process()
        with patch.object(host.subprocess, 'Popen', return_value=process):
            result = host._launch_child(b'{}', HERE, 1.)
        self.assertTrue(result['overflow'])
        self.assertTrue(process.killed)
        self.assertLessEqual(len(result['stdout']), child.MAX_OUTPUT_BYTES + 1)


if __name__ == '__main__':
    unittest.main()
