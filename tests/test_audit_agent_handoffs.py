"""Correlate synthetic evidence and make sure selected mutations cannot pass the audit."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import audit_agent_handoffs as audit
import test_role_transfer as transfer_fixtures


class AgentAuditTests(unittest.TestCase):
    def graph_case(self):
        harness = transfer_fixtures.RoleTransferTests()
        state = harness.run_case(part='sleeve_1')
        self.assertEqual(state['status'], 'success', state['error'])

        def stamps(handoffs):
            for h in handoffs:
                stamp = h['proof']['source_state']
                for k in ('model_sha256', 'integration_sha256', 'host_sha256'):
                    stamp[k] = hashlib.sha256(str(stamp[k]).encode()).hexdigest()

        stamps(state['agent_handoffs'])
        rows = deepcopy(harness.events)
        stamps([r['handoff'] for r in rows if 'handoff' in r])
        contexts = sorted(harness.assembly.contexts + harness.recovery.contexts, key=lambda c: c['observation_revision'])
        turns = []
        for ctx, row in zip(contexts, [r for r in rows if r['node'] == 'decide'], strict=True):
            role = ctx['active_role']
            if 'handoff' in ctx:
                stamps([ctx['handoff']])
            turns.append([{'event': 'request_prepared', 'agent_role': role, 'request': {
                'instructions': f'You are the {role} agent.', 'input': json.dumps(ctx)}}, {'proposal': row['action']}])
        phase = {'executed_approach': harness.skills.results[0]}
        return state, phase, rows, turns

    def test_numeric_transfer_requires_same_graph_owner_bound_prompt_and_authorized_original_commands(self):
        state, phase, original, calls = self.graph_case()
        self.assertEqual(audit._graph_part(None, 'sleeve_1', state, phase, original, iter(calls), False), 2)
        for corrupt in ('owner', 'prompt', 'extra_pick', 'other_target'):
            with self.subTest(corrupt=corrupt):
                rows, turns = deepcopy(original), deepcopy(calls)
                pos = next(i for i, r in enumerate(rows) if r['node'] == 'execute')
                if corrupt == 'owner':
                    rows[pos]['active_role'] = 'assembly'
                elif corrupt == 'extra_pick':
                    rows.insert(pos, deepcopy(rows[pos]))
                elif corrupt == 'other_target':
                    rows[pos]['action'] = {**rows[pos]['action'], 'target_id': 'unapproved'}
                else:
                    request = turns[-1][0]['request']
                    ctx = json.loads(request['input'])
                    del ctx['handoff']
                    request['input'] = json.dumps(ctx)
                with self.assertRaises(ValueError):
                    audit._graph_part(None, 'sleeve_1', state, phase, rows, iter(turns), False)

    def test_ledger_recomputes_role_costs_and_rejects_reused_completion_ids(self):
        rows = []
        for role in ('assembly', 'recovery'):
            request = {'model': 'offline', 'input': json.dumps({'active_role': role})}
            key = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
            response = 'fixture_' + role
            usage = {'input_tokens': 10, 'output_tokens': 4, 'total_tokens': 14, 'input_tokens_details': {
                'cached_tokens': 4, 'cache_write_tokens': 2}, 'output_tokens_details': {'reasoning_tokens': 3}}
            for event, data in [
                    ('request_prepared', {'request_hash': key, 'request': request}), ('input_count_started', {'request_hash': key}),
                    ('input_count_completed', {'input_tokens': 10}), ('model_request_started', {'model': 'offline', 'request_hash': key}),
                    ('model_response_received', {'response_id': response, 'usage': usage, 'response_status': 'completed'}),
                    ('proposal_validated', {'response_id': response})]:
                rows.append({'event': event, 'agent_role': role, **data})
        cost = .000038  # (4*2 + 4*1 + 2*3 + 4*5) per million.
        per_role = {'api_calls': 1, 'input_tokens': 10, 'output_tokens': 4, 'reasoning_tokens': 3,
                    'known_api_cost_estimate_usd': cost, 'unknown_usage_requests': 0}
        result = {'api_calls': 2, 'agent_usage': {r: dict(per_role) for r in ('assembly', 'recovery')},
                  'llm_usage': {k: v*2 for k, v in per_role.items()}}
        config = {'llm_config': {'model': 'offline', 'input_usd_per_million': '2', 'cached_input_usd_per_million': '1',
                                 'cache_write_30m_usd_per_million': '3', 'output_usd_per_million': '5'}}
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            file = folder / 'llm_events.jsonl'
            file.write_text('\n'.join(map(json.dumps, rows)), encoding='utf-8')
            self.assertEqual(len(audit._turns(folder, result, config)), 2)
            wrong = deepcopy(result)
            wrong['agent_usage']['recovery']['known_api_cost_estimate_usd'] = 0
            with self.assertRaisesRegex(ValueError, 'cost'):
                audit._turns(folder, wrong, config)
            for row in rows[-2:]:
                row['response_id'] = 'fixture_assembly'
            file.write_text('\n'.join(map(json.dumps, rows)), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'reused'):
                audit._turns(folder, result, config)


if __name__ == '__main__':
    unittest.main()
