"""Offline Lesson-to-Python candidate contracts; candidate Python is never executed."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
if not (PROJECT / 'scripts' / 'grasp_lessons.py').is_file():
    PROJECT = Path(r'C:\dev\llm-robot-assembly')  # Only the local staging layout uses this fallback.
for folder in (PROJECT / 'tests', PROJECT / 'scripts', HERE.parent / 'scripts', HERE):
    if folder.is_dir():
        sys.path.insert(0, str(folder))

import grasp_skill_candidates as candidates
from export_grasp_experience import digest
from grasp_lessons import save_lesson
import test_grasp_lessons as lesson_fixtures


FIXTURE_SOURCE = '''def propose(ctx):
    if ctx['holding_part'] or not ctx['observation_is_current'] or ctx['remaining_proposals'] <= 0:
        return None
    if ctx['failure_code'] != 'PLANNED_CONTACT' or ctx['failure_phase'] != 'insert_descent':
        return None
    size = ctx['part_size_xyz_mm']
    previous = ctx['previous_grasp']
    height = min(size[2] / 2, max(-size[2] / 2, previous['z_mm'] + 4))
    return {'x_mm': min(size[0] / 2, max(-size[0] / 2, previous['x_mm'])),
            'y_mm': min(size[1] / 2, max(-size[1] / 2, previous['y_mm'])),
            'z_mm': height,
            'jaw_yaw_deg': min(180, max(-180, previous['jaw_yaw_deg']))}
'''


def draft_for(packet, decision='propose_candidate'):
    """Shared with runner tests: this authored fixture makes no API/physics claim."""
    facts = packet['lesson']['evidence_packet']['facts']
    ids = [f['fact_id'] for f in facts]
    return {'decision': decision, 'title': '기둥 파지 높이 수정 후보',
            'rationale': '현재 치수와 직전 파지를 읽는 계약 검사 코드다. 4 mm 수정량은 미검증 휴리스틱이다.',
            'source_code': FIXTURE_SOURCE if decision == 'propose_candidate' else '',
            'lesson_ids': [packet['lesson']['lesson_id']],
            'supporting_fact_ids': ids if decision == 'propose_candidate' else [],
            'reviewed_fact_ids': ids,
            'applicability_notes': ['관측한 기둥·로봇·그리퍼·좌표계 조건에 한정한다.'],
            'exclusion_notes': ['실물·미관측 형상·파지 중 변경에는 적용하지 않는다.'],
            'limitations': ['정적 문법 검사 전용 fixture다. 생성 코드를 실행하지 않았고 새 물리 검증도 없다.']}


def reseal(value, key):
    value[key] = digest({k: v for k, v in value.items() if k != key})
    return value


class GraspSkillCandidateTests(unittest.TestCase):
    """Public setup fields are reused by the independent offline runner tests."""
    def setUp(self):
        self.lesson_fixture = lesson_fixtures.GraspLessonTests()
        self.lesson_fixture.setUp()
        self.root = self.lesson_fixture.root
        self.memory = self.lesson_fixture.memory
        self.lesson_path, _ = save_lesson(self.lesson_fixture.lesson(), memory_dir=self.memory,
                                          output_dir=self.root / 'lessons')
        self.packet = candidates.prepare_input(self.lesson_path, memory_dir=self.memory)
        self.output = self.root / 'candidates'

    def tearDown(self):
        self.lesson_fixture.tearDown()

    def candidate(self):
        return candidates.build_candidate(self.packet, draft_for(self.packet),
                                            generation={'method': 'offline_fixture'})

    def response(self, draft=None, **changes):
        draft = draft_for(self.packet) if draft is None else draft
        return {'status': 'completed', 'error': None, 'output': [
            {'type': 'function_call', 'name': candidates.TOOL_NAME, 'status': 'completed',
             'arguments': json.dumps(draft, ensure_ascii=False)}], **changes}

    def test_export_retrieval_saved_lesson_and_candidate_input_remain_connected(self):
        candidates.validate_input(self.packet, memory_dir=self.memory)
        self.assertEqual(self.packet['schema'], candidates.PACKET_SCHEMA)
        self.assertEqual(self.packet['contract'], candidates.CONTRACT)
        self.assertEqual(self.packet['lesson_source']['path'], str(self.lesson_path.resolve()))
        self.assertEqual(self.packet['lesson_source']['sha256'], hashlib.sha256(self.lesson_path.read_bytes()).hexdigest())
        facts = self.packet['lesson']['evidence_packet']['facts']
        self.assertEqual([f['fact_id'] for f in facts], ['F1', 'F2'])
        self.assertEqual(facts[1]['parent_fact_id'], 'F1')
        self.assertEqual(facts[0]['preflight_status'], 'rejected')
        self.assertTrue(facts[1]['recorded_measured_insertion'])
        self.assertFalse(self.packet['execution_authorized'])
        self.assertEqual(self.packet, candidates.prepare_input(self.lesson_path, memory_dir=self.memory))

    def test_modified_lesson_and_reformatted_source_bytes_are_detected(self):
        original = self.lesson_path.read_bytes()
        for mode in ('semantic', 'whitespace'):
            self.lesson_path.write_bytes(original)
            if mode == 'semantic':
                lesson = json.loads(original)
                lesson['interpretation']['hypothesis'] = 'Changed after sealing.'
                self.lesson_path.write_text(json.dumps(lesson), encoding='utf-8')
                with self.assertRaises(ValueError):
                    candidates.prepare_input(self.lesson_path, memory_dir=self.memory)
            else:
                self.lesson_path.write_bytes(original + b'\n')
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                candidates.validate_input(self.packet, memory_dir=self.memory)
            with self.subTest(save=mode), self.assertRaises(ValueError):
                candidates.save_candidate(self.candidate(), memory_dir=self.memory, output_dir=self.output)
        self.assertFalse(self.output.exists())

    def test_deleted_source_lesson_cannot_be_published(self):
        candidate = self.candidate()
        self.lesson_path.unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            candidates.save_candidate(candidate, memory_dir=self.memory, output_dir=self.output)
        self.assertFalse(self.output.exists())

    def test_modified_or_removed_source_experience_blocks_new_input_and_save(self):
        candidate = self.candidate()
        source = self.lesson_fixture.export_path
        original = source.read_bytes()
        for mode in ('tamper', 'delete'):
            source.write_bytes(original)
            if mode == 'tamper':
                bundle = json.loads(original)
                bundle['attempts'][0]['grasp']['z_mm'] = 999.
                source.write_text(json.dumps(bundle), encoding='utf-8')
            else:
                source.unlink()
            with self.subTest(prepare=mode), self.assertRaises(ValueError):
                candidates.prepare_input(self.lesson_path, memory_dir=self.memory)
            with self.subTest(save=mode), self.assertRaises(ValueError):
                candidates.save_candidate(candidate, memory_dir=self.memory, output_dir=self.output)
        self.assertFalse(self.output.exists())

    def test_packet_tampering_and_rehashed_interface_permission_changes_are_rejected(self):
        packet = deepcopy(self.packet)
        packet['lesson_source']['sha256'] = '0' * 64
        with self.assertRaises(ValueError):
            candidates.validate_input(packet)
        for mode in ('permission', 'contract'):
            packet = deepcopy(self.packet)
            if mode == 'permission':
                packet['execution_authorized'] = True
            else:
                packet['contract']['execution_adapter_implemented'] = True
            reseal(packet, 'packet_id')
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                candidates.validate_input(packet)

    def test_candidate_request_has_one_strict_tool_and_exact_reference_enums(self):
        request = candidates.build_request(SimpleNamespace(model='fixture-not-called', reasoning_effort='low'), self.packet)
        self.assertEqual(request['reasoning'], {'effort': 'low'})
        self.assertEqual(request['tool_choice'], {'type': 'function', 'name': candidates.TOOL_NAME})
        self.assertFalse(request['parallel_tool_calls'])
        self.assertEqual(request['truncation'], 'disabled')
        self.assertEqual(len(request['tools']), 1)
        tool = request['tools'][0]
        self.assertTrue(tool['strict'])
        params = tool['parameters']
        self.assertEqual(set(params['required']), candidates.FIELDS)
        self.assertFalse(params['additionalProperties'])
        self.assertEqual(params['properties']['lesson_ids']['items']['enum'], [self.packet['lesson']['lesson_id']])
        self.assertEqual(params['properties']['reviewed_fact_ids']['items']['enum'], ['F1', 'F2'])
        supplied = json.loads(request['input'])
        self.assertEqual(supplied['packet_id'], self.packet['packet_id'])
        self.assertEqual(supplied['evidence']['facts'][0]['preflight_status'], 'rejected')
        self.assertEqual(supplied['scope_policy'], 'observed_cases_only_no_inferred_ranges')
        other = candidates.build_request(SimpleNamespace(model='fixture-not-called', reasoning_effort=None), self.packet)
        self.assertNotIn('reasoning', other)
        self.assertFalse(self.output.exists())

    def test_draft_requires_exact_fields_lesson_and_known_unique_fact_references(self):
        for mode in ('extra', 'missing', 'wrong_lesson', 'two_lessons', 'no_support',
                     'foreign_fact', 'duplicate_fact', 'number_ref', 'wrong_decision'):
            draft = draft_for(self.packet)
            if mode == 'extra':
                draft['execute'] = True
            elif mode == 'missing':
                del draft['reviewed_fact_ids']
            elif mode == 'wrong_lesson':
                draft['lesson_ids'] = ['foreign-lesson']
            elif mode == 'two_lessons':
                draft['lesson_ids'] *= 2
            elif mode == 'no_support':
                draft['supporting_fact_ids'] = []
            elif mode == 'foreign_fact':
                draft['supporting_fact_ids'] = ['F999']
            elif mode == 'duplicate_fact':
                draft['supporting_fact_ids'] = ['F1', 'F1']
            elif mode == 'number_ref':
                draft['supporting_fact_ids'] = [1]
            else:
                draft['decision'] = 'register_skill'
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                candidates.validate_draft(draft, self.packet)

    def test_every_fact_must_be_reviewed_including_rejection(self):
        for missing in ('F1', 'F2'):
            draft = draft_for(self.packet)
            draft['reviewed_fact_ids'].remove(missing)
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                candidates.validate_draft(draft, self.packet)

    def test_text_notes_are_bounded_and_validated_draft_is_a_copy(self):
        original = draft_for(self.packet)
        validated = candidates.validate_draft(original, self.packet)
        validated['limitations'].append('Copy mutation.')
        self.assertEqual(original, draft_for(self.packet))
        for key, value in [('title', ' '), ('title', 'x' * 161), ('rationale', 12),
                           ('limitations', []), ('applicability_notes', [' ']),
                           ('exclusion_notes', ['x'] * 7), ('limitations', ['x' * 601])]:
            draft = deepcopy(original)
            draft[key] = value
            with self.subTest(field=key), self.assertRaises(ValueError):
                candidates.validate_draft(draft, self.packet)

    def test_source_inspection_hash_and_static_only_result(self):
        inspection = candidates.inspect_source(FIXTURE_SOURCE)
        self.assertEqual(inspection['status'], 'static_contract_passed')
        self.assertEqual(inspection['source_sha256'], hashlib.sha256(FIXTURE_SOURCE.encode('utf-8')).hexdigest())
        self.assertEqual(set(inspection['context_fields_read']), set(candidates.CONTEXT_FIELDS))
        self.assertEqual(inspection['return_forms'], ['abstain', 'grasp'])
        self.assertLessEqual(inspection['ast_nodes'], 600)
        for field in ('code_executed', 'runtime_behavior_verified', 'security_boundary_certified'):
            self.assertFalse(inspection[field])

    def test_ast_rejects_import_exec_mutation_loops_extra_arguments_and_dynamic_calls(self):
        prefixes = {
            'import': '    import os\n',
            'exec': "    exec('pass')\n",
            'mutation': "    ctx['holding_part'] = False\n",
            'alias_mutation': "    previous = ctx['previous_grasp']\n    previous['z_mm'] = 9\n",
            'for': '    for i in (1, 2):\n        return None\n',
            'while': '    while True:\n        return None\n',
            'dynamic_call': "    callback = ctx['previous_grasp']\n    value = callback()\n",
            'attribute': "    value = ctx.get('previous_grasp')\n",
            'unknown_name': '    value = outside_name\n',
            'nested_function': '    def helper():\n        return None\n',
            'dynamic_index': "    key = 'previous_grasp'\n    value = ctx[key]\n",
            'unknown_context': "    value = ctx['invented_sensor']\n",
            'reserved_local': '    max = 1\n',
            'underscore_local': '    _hidden = 1\n',
            'keyword_call': '    value = max(1, default=0)\n',
        }
        for mode, prefix in prefixes.items():
            source = FIXTURE_SOURCE.replace('def propose(ctx):\n', 'def propose(ctx):\n' + prefix)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                candidates.inspect_source(source)
        for header in ('def propose(ctx, extra):', 'def propose(ctx=None):',
                       'def propose(*ctx):', 'def propose(ctx, /):', 'def propose(ctx: dict):',
                       'def propose(ctx) -> dict:', 'async def propose(ctx):', 'def other(ctx):'):
            with self.subTest(header=header), self.assertRaises(ValueError):
                candidates.inspect_source(FIXTURE_SOURCE.replace('def propose(ctx):', header))
        with self.assertRaises(ValueError):
            candidates.inspect_source('@decorator\n' + FIXTURE_SOURCE)

    def test_ast_requires_current_geometry_previous_grasp_and_literal_four_field_output(self):
        for mode, source in (
                ('geometry_missing', FIXTURE_SOURCE.replace("ctx['part_size_xyz_mm']", "ctx['previous_grasp']")),
                ('previous_missing', FIXTURE_SOURCE.replace("ctx['previous_grasp']", "ctx['part_size_xyz_mm']")),
                ('not_literal_return', FIXTURE_SOURCE.replace('return {', 'result = {') + '    return result\n'),
                ('missing_grasp_key', FIXTURE_SOURCE.replace("'jaw_yaw_deg':", "'yaw':")),
                ('duplicate_grasp_key', FIXTURE_SOURCE.replace("'y_mm':", "'x_mm':")),
                ('only_abstain', "def propose(ctx):\n    a = ctx['part_size_xyz_mm']\n    b = ctx['previous_grasp']\n    return None\n")):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                candidates.inspect_source(source)

    def test_ast_rejects_syntax_excessive_constants_source_and_node_count(self):
        for source in ('', 'def propose(', FIXTURE_SOURCE + '\nprint(1)\n', 'x' * 16385,
                       FIXTURE_SOURCE.replace('+ 4)', '+ 1000001)'),
                       FIXTURE_SOURCE.replace('+ 4)', '+ 1e999)'),
                       FIXTURE_SOURCE.replace("'PLANNED_CONTACT'", repr('x' * 257))):
            with self.subTest(source=source[:50]), self.assertRaises(ValueError):
                candidates.inspect_source(source)
        many_nodes = FIXTURE_SOURCE.replace('    size =', '    ignored = ' + '+'.join(['1'] * 210) + '\n    size =')
        self.assertLess(len(many_nodes.encode('utf-8')), 16384)
        with self.assertRaises(ValueError):
            candidates.inspect_source(many_nodes)

    def test_static_pass_does_not_claim_safe_runtime_or_numerical_output(self):
        # Division by zero is intentionally NOT executed. The allowlist cannot prove behavior.
        syntactically_allowed_but_wrong = FIXTURE_SOURCE.replace("previous['z_mm'] + 4", "previous['z_mm'] / 0")
        inspection = candidates.inspect_source(syntactically_allowed_but_wrong)
        self.assertEqual(inspection['status'], 'static_contract_passed')
        self.assertFalse(inspection['code_executed'])
        self.assertFalse(inspection['runtime_behavior_verified'])

    def test_abstention_has_no_code_support_or_candidate_but_still_reviews_every_fact(self):
        draft = draft_for(self.packet, decision='abstain')
        self.assertEqual(candidates.parse_draft(self.response(draft), self.packet), draft)
        self.assertIsNone(candidates.build_candidate(self.packet, draft, generation={'method': 'offline_fixture'}))
        for field, value in [('source_code', FIXTURE_SOURCE), ('supporting_fact_ids', ['F1']),
                             ('reviewed_fact_ids', ['F2'])]:
            changed = deepcopy(draft)
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                candidates.validate_draft(changed, self.packet)
        self.assertFalse(self.output.exists())

    def test_parse_exact_one_call_with_optional_reasoning(self):
        response = self.response()
        response['output'].insert(0, {'type': 'reasoning', 'summary': []})
        self.assertEqual(candidates.parse_draft(response, self.packet), draft_for(self.packet))
        response['output'][1].pop('status')
        self.assertEqual(candidates.parse_draft(response, self.packet), draft_for(self.packet))

    def test_parse_rejects_refusal_multiple_calls_wrong_tool_and_incomplete(self):
        cases = [self.response(status='incomplete'), self.response(error={'message': 'fixture'}),
                 self.response(output=[]), self.response(output=None), self.response(output=[1]),
                 self.response(output=[{'type': 'message', 'content': [{'type': 'refusal'}]}])]
        duplicate = self.response()
        duplicate['output'] *= 2
        cases.append(duplicate)
        for key, value in [('name', 'execute_robot'), ('status', 'in_progress'), ('arguments', {})]:
            changed = self.response()
            changed['output'][0][key] = value
            cases.append(changed)
        for index, response in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(ValueError):
                candidates.parse_draft(response, self.packet)

    def test_parse_rejects_malformed_duplicate_nonfinite_and_oversized_arguments(self):
        for args in ('{', '{"decision":"abstain","decision":"propose_candidate"}',
                     '{"x":NaN}', '{"x":Infinity}', '[]', 'x' * 32769):
            response = self.response()
            response['output'][0]['arguments'] = args
            with self.subTest(args=args[:40]), self.assertRaises(ValueError):
                candidates.parse_draft(response, self.packet)

    def test_candidate_preserves_code_hash_scope_provenance_and_false_execution_permissions(self):
        candidate = self.candidate()
        candidates.validate_candidate(candidate, memory_dir=self.memory)
        self.assertEqual(candidate['schema'], candidates.CANDIDATE_SCHEMA)
        self.assertEqual(candidate['status'], 'candidate_static_checked')
        self.assertEqual(candidate['source_lesson_ids'], [self.packet['lesson']['lesson_id']])
        self.assertEqual(candidate['source_code'], FIXTURE_SOURCE)
        self.assertEqual(candidate['code_sha256'], hashlib.sha256(FIXTURE_SOURCE.encode('utf-8')).hexdigest())
        self.assertEqual(candidate['applicability'], self.packet['lesson']['applicability'])
        self.assertEqual(candidate['proposal_contract'], candidates.CONTRACT)
        self.assertEqual(candidate['generation'], {'method': 'offline_fixture'})
        self.assertEqual(candidate['training_eligibility'], 'unassigned_requires_dataset_manifest')
        self.assertTrue(candidate['contains_generated_python'])
        for field in ('execution_authorized', 'automatic_skill_registration', 'new_physics_validation', 'code_executed'):
            self.assertFalse(candidate[field])
        self.assertIn('bounded_VM_and_output_adapter', candidate['remaining_validation'])
        self.assertIn('physical_execution_and_independent_verification', candidate['remaining_validation'])
        self.packet['lesson']['interpretation']['title'] = 'Caller mutation'
        self.assertNotEqual(candidate['input_packet']['lesson']['interpretation']['title'], 'Caller mutation')

    def test_generation_requires_known_method_and_exact_live_request_metadata(self):
        draft = draft_for(self.packet)
        good = {'method': 'openai_responses', 'packet_id': self.packet['packet_id'],
                'requested_model': 'fixture-requested', 'returned_model': 'fixture-returned',
                'request_hash': 'fixture-request', 'generation_request_hash': 'fixture-generation-request',
                'response_id': 'fixture-response', 'response_hash': 'fixture-response-hash'}
        for generation in ({}, {'method': 'invented'}, {'method': 'openai_responses'},
                           {**good, 'packet_id': 'foreign-packet'}, {**good, 'returned_model': ''},
                           {**good, 'generation_request_hash': None}):
            with self.subTest(generation=generation), self.assertRaises(ValueError):
                candidates.build_candidate(self.packet, draft, generation=generation)
        # This fixture checks metadata only and does not claim that a model was called.
        candidate = candidates.build_candidate(self.packet, draft, generation=good)
        self.assertEqual(candidate['generation'], good)
        self.assertFalse(candidate['code_executed'])

    def test_rehashed_candidate_cannot_change_code_conditions_or_permissions(self):
        original = self.candidate()
        for mode in ('code', 'code_hash', 'scope', 'contract', 'static_pass', 'registered',
                     'executed', 'physical', 'permission', 'training', 'implementation'):
            value = deepcopy(original)
            if mode == 'code':
                value['source_code'] += '# Changed after generation.\n'
            elif mode == 'code_hash':
                value['code_sha256'] = '0' * 64
            elif mode == 'scope':
                value['applicability']['environment'] = 'real'
            elif mode == 'contract':
                value['proposal_contract']['execution_adapter_implemented'] = True
            elif mode == 'static_pass':
                value['static_validation']['runtime_behavior_verified'] = True
            elif mode == 'registered':
                value['automatic_skill_registration'] = True
            elif mode == 'executed':
                value['code_executed'] = True
            elif mode == 'physical':
                value['new_physics_validation'] = True
            elif mode == 'permission':
                value['execution_authorized'] = True
            elif mode == 'training':
                value['training_eligibility'] = 'approved'
            else:
                value['implementation_sha256'] = 'not-a-sha256'
            reseal(value, 'candidate_id')
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                candidates.validate_candidate(value)

    def test_historical_implementation_hash_is_preserved_without_claiming_authenticity(self):
        value = self.candidate()
        # A declared older implementation hash is provenance, not a signature or runtime result.
        value['implementation_sha256'] = 'a' * 64
        reseal(value, 'candidate_id')
        candidates.validate_candidate(value, memory_dir=self.memory)
        self.assertEqual(value['implementation_sha256'], 'a' * 64)
        self.assertFalse(value['code_executed'])
        for invalid in ('A' * 64, 'a' * 63, None):
            bad = deepcopy(value)
            bad['implementation_sha256'] = invalid
            reseal(bad, 'candidate_id')
            with self.subTest(hash=invalid), self.assertRaises(ValueError):
                candidates.validate_candidate(bad)

    def test_unhashed_candidate_tamper_is_rejected(self):
        value = self.candidate()
        value['draft']['title'] = 'Changed'
        with self.assertRaises(ValueError):
            candidates.validate_candidate(value)

    def test_save_is_idempotent_immutable_and_preserves_source_bytes(self):
        value = self.candidate()
        paths = [self.lesson_path, *self.memory.glob('*.json'), *self.lesson_fixture.run.iterdir()]
        before = {p: p.read_bytes() for p in paths if p.is_file()}
        path, created = candidates.save_candidate(value, memory_dir=self.memory, output_dir=self.output)
        self.assertTrue(created)
        self.assertEqual(path.name, value['candidate_id'] + '.json')
        payload = path.read_bytes()
        again, created_again = candidates.save_candidate(value, memory_dir=self.memory, output_dir=self.output)
        self.assertEqual(again, path)
        self.assertFalse(created_again)
        self.assertEqual(path.read_bytes(), payload)
        self.assertEqual(json.loads(payload), value)
        candidates.validate_candidate(json.loads(payload), memory_dir=self.memory)
        self.assertEqual(list(self.output.iterdir()), [path])
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_conflicting_existing_candidate_is_not_overwritten(self):
        value = self.candidate()
        path, _ = candidates.save_candidate(value, memory_dir=self.memory, output_dir=self.output)
        conflict = deepcopy(value)
        conflict['execution_authorized'] = True
        path.write_text(json.dumps(conflict), encoding='utf-8')
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            candidates.save_candidate(value, memory_dir=self.memory, output_dir=self.output)
        self.assertEqual(path.read_bytes(), before)

    def test_protected_source_memory_lesson_and_registered_stores_reject_output(self):
        value = self.candidate()
        simulated_project = self.root / 'simulated_project'
        protected = [self.memory, self.memory / 'nested', self.lesson_path.parent,
                     self.lesson_path.parent / 'nested', self.lesson_fixture.run / 'nested',
                     simulated_project / 'runs', simulated_project / 'runtime/memory',
                     simulated_project / 'runtime/lessons', simulated_project / 'runtime/skills',
                     simulated_project / 'runtime/knowledge']
        with patch.object(candidates, 'ROOT', simulated_project):
            for output in protected:
                with self.subTest(output=output), self.assertRaises(ValueError):
                    candidates.save_candidate(value, memory_dir=self.memory, output_dir=output)
        self.assertFalse(simulated_project.exists())
        self.assertFalse((self.memory / 'nested').exists())


if __name__ == '__main__':
    unittest.main()
