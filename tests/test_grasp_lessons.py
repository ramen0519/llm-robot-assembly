"""Offline evidence-to-lesson contracts; these fixtures are not physics results."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

HERE = Path(__file__).resolve().parent
for candidate in (HERE.parent / 'scripts', HERE.parent / 'grasp_experience',
                  HERE.parent / 'grasp_retrieval', HERE):
    if candidate.is_dir():
        sys.path.insert(0, str(candidate))

from export_grasp_experience import build_export, digest, save_export
from retrieve_grasp_experience import query_from_run, retrieve_experiences
from test_retrieve_grasp_experience import fixture
from grasp_lessons import (FIELDS, LESSON_SCHEMA, PACKET_SCHEMA, TOOL_NAME, NoEvidence,
                          build_lesson, build_request, model_input, parse_draft,
                          prepare_evidence, save_lesson, validate_draft,
                          validate_lesson, validate_packet)


def reseal(value, field):
    """Rehash authored test data to distinguish semantic guards from checksums."""
    value[field] = digest({k: v for k, v in value.items() if k != field})
    return value


def draft_for(packet, *, decision='propose_lesson'):
    ids = [fact['fact_id'] for fact in packet['facts']]
    return {'decision': decision,
            'title': '기둥 파지 높이 수정의 조건부 참고 사례',
            'hypothesis': '같은 조건에서 낮은 파지가 거절되면 높이를 수정한 후보를 새로 검사할 수 있다.',
            'rationale': '저장된 거절과 후속 시도 사이의 연관성만 설명하며 인과관계를 증명하지 않는다.',
            'supporting_fact_ids': ids if decision == 'propose_lesson' else [],
            'counterexample_fact_ids': [],
            'uncertain_fact_ids': [f['fact_id'] for f in packet['facts']
                                   if f['preflight_status'] != 'rejected'
                                   and not f['recorded_measured_insertion']],
            'reviewed_fact_ids': ids,
            'applicability_notes': ['관측한 기둥·로봇·그리퍼·좌표계 조건에만 한정한다.'],
            'exclusion_notes': ['실물 및 관측하지 않은 형상·접촉 조건에는 검증되지 않았다.'],
            'limitations': ['이 입력은 계약 검사 fixture이며 새 물리 검증이 아니다. 현재 장면에서 재검사가 필요하다.']}


class GraspLessonTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.memory = self.root / 'memory'
        self.memory.mkdir()
        self.output = self.root / 'lessons'
        self.current = self.write_run('current', *fixture())
        self.run, self.bundle, self.export_path = self.source()

    def tearDown(self):
        self.temporary.cleanup()

    def write_run(self, name, config, events, result=None):
        folder = self.root / name
        folder.mkdir(exist_ok=True)
        (folder / 'resolved_config.json').write_text(json.dumps(config, allow_nan=False), encoding='utf-8')
        (folder / 'graph_events.jsonl').write_text(
            ''.join(json.dumps(e, allow_nan=False) + '\n' for e in events), encoding='utf-8')
        if result is not None:
            (folder / 'result.json').write_text(json.dumps(result, allow_nan=False), encoding='utf-8')
        return folder

    def source(self, name='historical', *, with_result=True, events_limit=None):
        config, events, result = fixture()
        run = self.write_run(name, config, events if events_limit is None else events[:events_limit],
                             result if with_result else None)
        bundle = build_export(run)
        path, _ = save_export(bundle, self.memory)
        return run, bundle, path

    def search(self):
        query = query_from_run(self.current, 'post_1', failure_code='PLANNED_CONTACT',
                               failure_phase='insert_descent')
        return retrieve_experiences(query, memory_dir=self.memory)

    def packet(self):
        return prepare_evidence(self.search(), memory_dir=self.memory)

    def lesson(self, packet=None):
        packet = self.packet() if packet is None else packet
        return build_lesson(packet, draft_for(packet), generation={'method': 'offline_fixture'})

    def response(self, draft, **changes):
        return {'status': 'completed', 'error': None, 'output': [
            {'type': 'function_call', 'name': TOOL_NAME, 'status': 'completed',
             'arguments': json.dumps(draft, ensure_ascii=False)}], **changes}

    def test_export_search_packet_keeps_attempt_links_and_snapshot_hashes(self):
        packet = self.packet()
        validate_packet(packet, memory_dir=self.memory)
        self.assertEqual(packet['schema'], PACKET_SCHEMA)
        self.assertEqual([f['fact_id'] for f in packet['facts']], ['F1', 'F2'])
        first, second = packet['facts']
        self.assertEqual(first['preflight_status'], 'rejected')
        self.assertIsNone(first['parent_fact_id'])
        self.assertEqual(second['parent_fact_id'], 'F1')
        self.assertEqual(second['change_reference_fact_id'], 'F1')
        self.assertEqual(second['changed_parameters'], {'z_mm': 12.})
        self.assertTrue(second['recorded_measured_insertion'])
        self.assertTrue(all(f['evidence_kind'] == 'historical_log_projection' for f in packet['facts']))
        case = packet['cases'][0]
        self.assertEqual(case['source_episode_id'], self.bundle['source_episode_id'])
        for source in case['source_versions']:
            self.assertEqual(source['export_id'], self.bundle['export_id'])
            for filename, metadata in source['source_files'].items():
                self.assertEqual(metadata['sha256'], hashlib.sha256((self.run / filename).read_bytes()).hexdigest())
        self.assertEqual(packet['packet_id'], digest({k: v for k, v in packet.items() if k != 'packet_id'}))

    def test_multiple_cards_have_unique_aliases_and_case_local_parent_links(self):
        self.source('second_episode')
        packet = self.packet()
        self.assertEqual(len(packet['cases']), 2)
        self.assertEqual([f['fact_id'] for f in packet['facts']], ['F1', 'F2', 'F3', 'F4'])
        lookup = {f['fact_id']: f for f in packet['facts']}
        for fact in packet['facts']:
            for key in ('parent_fact_id', 'change_reference_fact_id'):
                if fact[key] is not None:
                    self.assertEqual(lookup[fact[key]]['case_id'], fact['case_id'])
                    self.assertEqual(lookup[fact[key]]['attempt_id'],
                                     fact['parent_attempt_id' if key == 'parent_fact_id' else 'change_reference_attempt_id'])

    def test_packet_is_deterministic_and_does_not_mutate_search(self):
        search = self.search()
        before = deepcopy(search)
        packet = prepare_evidence(search, memory_dir=self.memory)
        self.assertEqual(packet, prepare_evidence(search, memory_dir=self.memory))
        self.assertEqual(search, before)
        packet['search']['cards'][0]['attempts'][0]['grasp']['z_mm'] = 999.
        self.assertEqual(search, before)

    def test_modified_search_or_invented_card_is_not_frozen(self):
        original = self.search()
        for mode in ('card', 'fact', 'drop_attempt', 'query'):
            with self.subTest(mode=mode):
                changed = deepcopy(original)
                if mode == 'card':
                    changed['cards'][0]['card_id'] = 'invented-card'
                elif mode == 'fact':
                    changed['cards'][0]['attempts'][0]['grasp']['z_mm'] = 999.
                elif mode == 'drop_attempt':
                    changed['cards'][0]['attempts'].pop()
                else:
                    changed['query']['failure_phase'] = 'invented-phase'
                with self.assertRaises(ValueError):
                    prepare_evidence(changed, memory_dir=self.memory)

    def test_no_evidence_raises_before_generation_or_registration(self):
        self.export_path.unlink()
        search = self.search()
        self.assertEqual(search['cards'], [])
        with self.assertRaises(NoEvidence):
            prepare_evidence(search, memory_dir=self.memory)
        self.assertFalse(self.output.exists())

    def test_request_exposes_only_supplied_ids_and_one_strict_advisory_tool(self):
        packet = self.packet()
        config = SimpleNamespace(model='fixture-model-name-not-called', reasoning_effort='low')
        request = build_request(config, packet)
        self.assertEqual(request['model'], config.model)
        self.assertEqual(request['reasoning'], {'effort': 'low'})
        self.assertEqual(request['tool_choice'], {'type': 'function', 'name': TOOL_NAME})
        self.assertFalse(request['parallel_tool_calls'])
        self.assertEqual(request['truncation'], 'disabled')
        self.assertEqual(len(request['tools']), 1)
        tool = request['tools'][0]
        self.assertTrue(tool['strict'])
        parameters = tool['parameters']
        self.assertFalse(parameters['additionalProperties'])
        self.assertEqual(set(parameters['required']), FIELDS)
        for name in ('supporting_fact_ids', 'counterexample_fact_ids', 'uncertain_fact_ids', 'reviewed_fact_ids'):
            self.assertEqual(parameters['properties'][name]['items']['enum'], ['F1', 'F2'])
        supplied = json.loads(request['input'])
        self.assertEqual(supplied['packet_id'], packet['packet_id'])
        self.assertEqual(len(supplied['facts']), 2)
        self.assertIn('NOT a recorded measured live insertion', request['instructions'])
        self.assertFalse(self.output.exists())

    def test_optional_reasoning_is_omitted_and_compact_input_keeps_negative_facts(self):
        packet = self.packet()
        request = build_request(SimpleNamespace(model='fixture', reasoning_effort=None), packet)
        self.assertNotIn('reasoning', request)
        supplied = model_input(packet)
        self.assertEqual(supplied['facts'][0]['preflight_status'], 'rejected')
        self.assertTrue(supplied['facts'][1]['recorded_measured_insertion'])
        events = supplied['facts'][0]['events']
        self.assertTrue(any(e['failure_code'] == 'PLANNED_CONTACT' for e in events))
        self.assertFalse(any('source' in e or 'record_id' in e for e in events))
        self.assertIn('source_versions', packet['cases'][0])

    def test_draft_requires_every_field_and_disallows_code_fields(self):
        packet = self.packet()
        for mode in ('missing_refs', 'extra_python', 'unknown_decision'):
            draft = draft_for(packet)
            if mode == 'missing_refs':
                draft.pop('supporting_fact_ids')
            elif mode == 'extra_python':
                draft['python'] = 'raise AssertionError("must not execute")'
            else:
                draft['decision'] = 'execute_skill'
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                validate_draft(draft, packet)

    def test_draft_rejects_unknown_cross_packet_and_duplicate_references(self):
        packet = self.packet()
        for field in ('supporting_fact_ids', 'counterexample_fact_ids', 'uncertain_fact_ids', 'reviewed_fact_ids'):
            for bad in (['F999'], ['C1'], ['F1', 'F1'], [1], 'F1'):
                draft = draft_for(packet)
                draft[field] = bad
                with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                    validate_draft(draft, packet)
        self.source('other_card')
        larger_packet = self.packet()
        self.assertIn('F4', [f['fact_id'] for f in larger_packet['facts']])
        foreign = draft_for(packet)
        foreign['supporting_fact_ids'] = ['F4']
        with self.assertRaises(ValueError):
            validate_draft(foreign, packet)

    def test_review_cannot_drop_rejection_or_success_even_when_other_refs_keep_it(self):
        packet = self.packet()
        for omitted in ('F1', 'F2'):
            draft = draft_for(packet)
            draft['reviewed_fact_ids'].remove(omitted)
            with self.subTest(omitted=omitted), self.assertRaises(ValueError):
                validate_draft(draft, packet)

    def test_graph_only_success_must_remain_uncertain(self):
        self.export_path.unlink()
        self.source('no_saved_measurements', with_result=False)
        packet = self.packet()
        draft = draft_for(packet)
        self.assertEqual(draft['uncertain_fact_ids'], ['F2'])
        validate_draft(draft, packet)
        lesson = self.lesson(packet)
        self.assertEqual(lesson['host_assessment']['recorded_measured_pass_ids'], [])
        self.assertEqual(lesson['host_assessment']['unresolved_attempt_ids'], ['F2'])
        draft['uncertain_fact_ids'] = []
        with self.assertRaises(ValueError):
            validate_draft(draft, packet)

    def test_accepted_preflight_without_live_execution_must_remain_uncertain(self):
        self.export_path.unlink()
        self.source('accepted_only', with_result=False, events_limit=6)
        packet = self.packet()
        second = packet['facts'][1]
        self.assertEqual(second['preflight_status'], 'accepted')
        self.assertFalse(second['recorded_measured_insertion'])
        draft = draft_for(packet)
        self.assertEqual(draft['uncertain_fact_ids'], ['F2'])
        draft['uncertain_fact_ids'] = []
        with self.assertRaises(ValueError):
            validate_draft(draft, packet)

    def test_support_required_and_support_counterexample_overlap_rejected(self):
        packet = self.packet()
        for mode in ('empty', 'overlap'):
            draft = draft_for(packet)
            if mode == 'empty':
                draft['supporting_fact_ids'] = []
            else:
                draft['counterexample_fact_ids'] = ['F1']
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                validate_draft(draft, packet)

    def test_text_and_notes_are_nonempty_bounded_and_returned_as_copy(self):
        packet = self.packet()
        original = draft_for(packet)
        validated = validate_draft(original, packet)
        validated['limitations'].append('A mutation to the returned copy.')
        self.assertEqual(original, draft_for(packet))
        for field, bad in [('title', ' '), ('title', 'x' * 161), ('hypothesis', 1),
                           ('limitations', []), ('exclusion_notes', [' ']),
                           ('applicability_notes', ['x'] * 7), ('limitations', ['x' * 601])]:
            draft = deepcopy(original)
            draft[field] = bad
            with self.subTest(field=field, bad_type=type(bad).__name__), self.assertRaises(ValueError):
                validate_draft(draft, packet)

    def test_abstention_is_validated_but_creates_no_lesson(self):
        packet = self.packet()
        draft = draft_for(packet, decision='abstain')
        self.assertEqual(parse_draft(self.response(draft), packet), draft)
        self.assertIsNone(build_lesson(packet, draft, generation={'method': 'offline_fixture'}))
        draft['reviewed_fact_ids'] = []
        with self.assertRaises(ValueError):
            build_lesson(packet, draft, generation={'method': 'offline_fixture'})
        self.assertFalse(self.output.exists())

    def test_parse_one_completed_call_with_optional_reasoning(self):
        packet = self.packet()
        draft = draft_for(packet)
        response = self.response(draft)
        response['output'].insert(0, {'type': 'reasoning', 'summary': []})
        self.assertEqual(parse_draft(response, packet), draft)
        response['output'][1].pop('status')
        self.assertEqual(parse_draft(response, packet), draft)

    def test_parse_rejects_incomplete_errors_refusals_text_and_wrong_call_count(self):
        packet = self.packet()
        response = self.response(draft_for(packet))
        cases = []
        for changes in ({'status': 'incomplete'}, {'error': {'message': 'fixture failure'}},
                        {'output': []}, {'output': None}, {'output': [1]},
                        {'output': [{'type': 'message', 'content': [{'type': 'refusal'}]}]},
                        {'output': [{'type': 'output_text', 'text': 'not a tool call'}]}):
            cases.append(self.response(draft_for(packet), **changes))
        duplicate = deepcopy(response)
        duplicate['output'].append(deepcopy(duplicate['output'][0]))
        cases.append(duplicate)
        for field, value in [('name', 'execute_robot'), ('status', 'in_progress'), ('arguments', {})]:
            changed = deepcopy(response)
            changed['output'][0][field] = value
            cases.append(changed)
        for i, changed in enumerate(cases):
            with self.subTest(case=i), self.assertRaises(ValueError):
                parse_draft(changed, packet)

    def test_parse_rejects_malformed_duplicate_nonfinite_and_oversized_json(self):
        packet = self.packet()
        for arguments in ('{', '{"decision":"abstain","decision":"propose_lesson"}',
                          '{"value":NaN}', '{"value":Infinity}', '[]', 'x' * 16385):
            response = self.response(draft_for(packet))
            response['output'][0]['arguments'] = arguments
            with self.subTest(arguments=arguments[:40]), self.assertRaises(ValueError):
                parse_draft(response, packet)

    def test_built_lesson_is_advisory_and_offline_generation_is_not_live_validation(self):
        packet = self.packet()
        lesson = self.lesson(packet)
        validate_lesson(lesson, memory_dir=self.memory)
        self.assertEqual(lesson['schema'], LESSON_SCHEMA)
        self.assertEqual(lesson['status'], 'provisional_hypothesis')
        self.assertEqual(lesson['domain'], 'mujoco_robot_assembly')
        self.assertEqual(lesson['generation'], {'method': 'offline_fixture'})
        for field in ('contains_generated_python', 'execution_authorized',
                      'automatic_skill_registration', 'new_physics_validation'):
            self.assertIs(lesson[field], False)
        self.assertIsNone(lesson['candidate_code_hash'])
        self.assertEqual(lesson['training_eligibility'], 'unassigned_requires_dataset_manifest')
        self.assertEqual(lesson['host_assessment']['preflight_rejection_ids'], ['F1'])
        self.assertEqual(lesson['host_assessment']['recorded_measured_pass_ids'], ['F2'])
        self.assertFalse(lesson['host_assessment']['causal_validation'])
        self.assertFalse(lesson['host_assessment']['natural_language_claims_verified'])
        self.assertTrue(lesson['applicability']['requires_current_scene_validation'])
        self.assertIn('real_hardware', lesson['applicability']['not_validated_for'])
        packet['facts'][0]['grasp']['z_mm'] = 999.
        self.assertEqual(lesson['evidence_packet']['facts'][0]['grasp']['z_mm'], 0.)

    def test_live_generation_requires_provenance_but_metadata_is_not_a_model_run(self):
        packet = self.packet()
        draft = draft_for(packet)
        for generation in ({}, {'method': 'invented'}, {'method': 'openai_responses'},
                           {'method': 'openai_responses', 'requested_model': 'fixture-only'}):
            with self.subTest(generation=generation), self.assertRaises(ValueError):
                build_lesson(packet, draft, generation=generation)
        # Authored provenance verifies the schema only: no API function is imported or called.
        generation = {'method': 'openai_responses', 'requested_model': 'fixture-requested',
                      'returned_model': 'fixture-returned', 'request_hash': 'fixture-request-hash',
                      'response_id': 'fixture-response-id', 'response_hash': 'fixture-response-hash',
                      'packet_id': packet['packet_id']}
        for bad_id in (None, 'another-packet-id'):
            mismatched = {**generation, 'packet_id': bad_id}
            with self.subTest(packet_id=bad_id), self.assertRaises(ValueError):
                build_lesson(packet, draft, generation=mismatched)
        lesson = build_lesson(packet, draft, generation=generation)
        self.assertEqual(lesson['generation'], generation)
        self.assertFalse(lesson['new_physics_validation'])
        self.assertFalse(lesson['execution_authorized'])
        self.assertFalse(self.output.exists())

    def test_rehashed_lesson_cannot_expand_scope_or_change_host_assessment(self):
        original = self.lesson()
        for field, value in [('execution_authorized', True), ('automatic_skill_registration', True),
                             ('contains_generated_python', True), ('new_physics_validation', True),
                             ('candidate_code_hash', 'pretend-skill'), ('status', 'validated_skill')]:
            changed = deepcopy(original)
            changed[field] = value
            reseal(changed, 'lesson_id')
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_lesson(changed)
        for mode in ('real', 'ranges', 'fresh_check', 'contexts', 'pass_ids', 'causal'):
            changed = deepcopy(original)
            if mode == 'real':
                changed['applicability']['environment'] = 'real'
            elif mode == 'ranges':
                changed['applicability']['condition_policy'] = 'any_dimension'
            elif mode == 'fresh_check':
                changed['applicability']['requires_current_scene_validation'] = False
            elif mode == 'contexts':
                changed['applicability']['observed_cases'][0]['contexts'] = []
            elif mode == 'pass_ids':
                changed['host_assessment']['recorded_measured_pass_ids'] = ['F1', 'F2']
            else:
                changed['host_assessment']['causal_validation'] = True
            reseal(changed, 'lesson_id')
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                validate_lesson(changed)

    def test_packet_and_lesson_seals_detect_unhashed_fact_and_text_changes(self):
        packet = self.packet()
        changed = deepcopy(packet)
        changed['facts'][0]['grasp']['z_mm'] = 999.
        with self.assertRaises(ValueError):
            validate_packet(changed)
        changed = self.lesson(packet)
        changed['interpretation']['hypothesis'] = 'Changed after sealing.'
        with self.assertRaises(ValueError):
            validate_lesson(changed)

    def test_rehashed_packet_cannot_replace_a_fact_or_cross_card_parent_when_bound_to_memory(self):
        self.source('second_episode')
        original = self.packet()
        for mode in ('grasp', 'case_id', 'parent', 'snapshot', 'attempt_id'):
            packet = deepcopy(original)
            if mode == 'grasp':
                packet['facts'][0]['grasp']['z_mm'] = 999.
            elif mode == 'case_id':
                packet['facts'][0]['case_id'] = 'C2'
            elif mode == 'parent':
                packet['facts'][1]['parent_fact_id'] = 'F3'
            elif mode == 'snapshot':
                packet['cases'][0]['source_versions'][0]['source_files']['resolved_config.json']['sha256'] = '0' * 64
            else:
                packet['facts'][0]['attempt_id'] = packet['facts'][2]['attempt_id']
            reseal(packet, 'packet_id')
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                validate_packet(packet, memory_dir=self.memory)
            with self.subTest(save_mode=mode), self.assertRaises(ValueError):
                save_lesson(self.lesson(packet), memory_dir=self.memory, output_dir=self.output)
        self.assertFalse(self.output.exists())

    def test_packet_scope_stays_advisory_even_after_rehashing(self):
        for field, value in [('purpose', 'execution'), ('execution_authorized', True),
                             ('training_eligibility', 'approved_for_training')]:
            packet = self.packet()
            packet[field] = value
            reseal(packet, 'packet_id')
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_packet(packet)

    def test_source_export_corruption_or_removal_blocks_save_before_creating_output(self):
        lesson = self.lesson()
        original = self.export_path.read_bytes()
        for mode in ('corrupt', 'remove'):
            self.export_path.write_bytes(original)
            if mode == 'corrupt':
                changed = deepcopy(self.bundle)
                changed['attempts'][0]['grasp']['z_mm'] = 999.
                self.export_path.write_text(json.dumps(changed), encoding='utf-8')
            else:
                self.export_path.unlink()
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                save_lesson(lesson, memory_dir=self.memory, output_dir=self.output)
            self.assertFalse(self.output.exists())

    def test_later_measurements_change_snapshot_and_require_regeneration(self):
        self.export_path.unlink()
        run, _, _ = self.source('pending_result', with_result=False)
        packet = self.packet()
        lesson = self.lesson(packet)
        _, _, result = fixture()
        (run / 'result.json').write_text(json.dumps(result), encoding='utf-8')
        save_export(build_export(run), self.memory)
        with self.assertRaises(ValueError):
            save_lesson(lesson, memory_dir=self.memory, output_dir=self.output)
        refreshed = self.packet()
        self.assertNotEqual(refreshed['packet_id'], packet['packet_id'])
        self.assertEqual([f['attempt_id'] for f in refreshed['facts']],
                         [f['attempt_id'] for f in packet['facts']])
        self.assertTrue(refreshed['facts'][1]['recorded_measured_insertion'])

    def test_save_is_immutable_idempotent_and_does_not_change_memory_or_runs(self):
        lesson = self.lesson()
        preserved = {p: p.read_bytes() for folder in (self.memory, self.run, self.current)
                     for p in folder.iterdir() if p.is_file()}
        path, created = save_lesson(lesson, memory_dir=self.memory, output_dir=self.output)
        self.assertTrue(created)
        self.assertEqual(path.name, lesson['lesson_id'] + '.json')
        original_bytes = path.read_bytes()
        second_path, created_again = save_lesson(lesson, memory_dir=self.memory, output_dir=self.output)
        self.assertEqual(path, second_path)
        self.assertFalse(created_again)
        self.assertEqual(path.read_bytes(), original_bytes)
        self.assertEqual(json.loads(original_bytes), lesson)
        validate_lesson(json.loads(original_bytes), memory_dir=self.memory)
        self.assertEqual(list(self.output.iterdir()), [path])
        self.assertEqual({p: p.read_bytes() for p in preserved}, preserved)

    def test_existing_corrupted_lesson_is_not_overwritten(self):
        lesson = self.lesson()
        path, _ = save_lesson(lesson, memory_dir=self.memory, output_dir=self.output)
        damaged = deepcopy(lesson)
        damaged['execution_authorized'] = True
        path.write_text(json.dumps(damaged), encoding='utf-8')
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            save_lesson(lesson, memory_dir=self.memory, output_dir=self.output)
        self.assertEqual(path.read_bytes(), before)

    def test_lessons_cannot_be_saved_inside_source_or_memory(self):
        lesson = self.lesson()
        for output in (self.memory, self.memory / 'nested', self.run, self.run / 'nested'):
            with self.subTest(output=output), self.assertRaises(ValueError):
                save_lesson(lesson, memory_dir=self.memory, output_dir=output)
        self.assertFalse((self.memory / 'nested').exists())
        self.assertFalse((self.run / 'nested').exists())


if __name__ == '__main__':
    unittest.main()
