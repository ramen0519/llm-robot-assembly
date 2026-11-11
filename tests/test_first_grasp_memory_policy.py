"""Exercise the assembly decision boundary without using the network."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import assembly_graph
from llm_runtime import ApiLedger, load_llm_config
from openai_policy import OpenAIAssemblyPolicy
from graph_grasp_recovery import DevelopmentFirstGrasp
from sleeve_memory_policy import FirstGraspMemory
from test_graph_grasp_recovery import FixturePolicy, FixtureSkills
from test_openai_policy import context


class FirstGraspPolicyTests(unittest.TestCase):
    def test_actual_search_request_and_grasp_are_distinct_operations(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            config = load_llm_config(Path(__file__).resolve().parents[1]/'config/llm.astra-high-sleeve-memory.toml')
            ledger = ApiLedger(config,folder/'ledger.jsonl')
            client = Mock()
            client.responses.input_tokens.count.return_value = SimpleNamespace(input_tokens=80)
            def reply(**kw):
                name = kw['tool_choice']['name']
                body={'status':'completed','id':name,'model':config.model,
                      'usage':{'input_tokens':80,'output_tokens':40,'total_tokens':120}}
                args = {'query':'sleeve grasp tabletop'}
                if name == 'propose_assembly_action':
                    args = {'skill':'pick','part_id':'sleeve_2','target_id':'test','observation_revision':1,
                            'grasp':{'x_mm':0,'y_mm':0,'z_mm':9,'jaw_yaw_deg':0},'reason':'Reviewed sleeve_1.'}
                body['output']=[{'type':'function_call','name':name,'arguments':json.dumps(args)}]
                return SimpleNamespace(model_dump=lambda **_: body)
            client.responses.create.side_effect=reply
            policy=OpenAIAssemblyPolicy(config,client,ledger,role='assembly')
            c=context()
            c.update(part_id='sleeve_2',target_id='test',grasp_task={'geometry': {'shape':'sleeve'},'previous_trials':[]})
            card={'record_id':'example','source_part':'sleeve_1','repair':{'grasp':{'z_mm':12}}}
            hook=FirstGraspMemory({},folder/'memory','current')
            with patch('sleeve_memory_policy.build_query',return_value={'part_id':'sleeve_2'}), \
                 patch('sleeve_memory_policy.retrieve_experience',return_value=card) as lookup:
                c['experiences']=hook.before_proposal(object(),c,DevelopmentFirstGrasp(policy))
            self.assertEqual(lookup.call_args.kwargs['search_text'],'sleeve grasp tabletop')
            self.assertEqual(policy.propose(c)['grasp']['z_mm'],9)
            requests=[call.kwargs for call in client.responses.create.call_args_list]
            self.assertNotIn('example',requests[0]['input'])
            self.assertEqual(json.loads(requests[1]['input'])['experiences'],[card])
            self.assertEqual(ledger.agent_totals()['assembly']['api_calls'],2)

    def test_first_grasp_only_ends_before_original_pick_even_for_an_accepted_trial(self):
        skills, assembly, recovery = FixtureSkills(), FixturePolicy(), FixturePolicy()
        hook=Mock()
        hook.before_proposal.return_value=[{'record_id':'example'}]
        with patch.object(assembly_graph,'_snapshot',lambda s: {'test':True}), \
                patch.object(skills,'check_grasp',lambda g:{'grasp':g,'accepted':True}):
            state=assembly_graph.run_assembly_graph(skills,failure_handler=lambda exc: self.fail(str(exc)),
                mode='roles',propose_grasp=True,first_grasp_only=True,first_grasp_memory=hook,
                policies={'assembly':assembly,'recovery':recovery},finish_on_verified_insert=True)
        self.assertEqual(state['status'],'first_grasp_checked')
        self.assertEqual(assembly.contexts[0]['experiences'],[{'record_id':'example'}])
        self.assertEqual(len(state['grasp_trials']),1)
        self.assertEqual(skills.commands,[])
        self.assertEqual(recovery.contexts,[])
