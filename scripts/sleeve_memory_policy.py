"""First-grasp search by the assembly agent. Returned experiences are advisory."""
import json
from pathlib import Path

from failure_report import write_json
from sleeve_grasp_memory import build_query, retrieve_experience


def search_request(config, context):
    task = {field: context[field] for field in ('part_id', 'target_id', 'observation_revision', 'task_id')}
    task['geometry'] = context['grasp_task']['geometry']
    name = 'search_sleeve_experience'
    request = {'model': config.model,
        'instructions': ('You are the assembly agent preparing the first grasp of the current part. '
                         'Search local assembly experiences for relevant earlier failures and successful '
                         'corrections before proposing a grasp. Choose short English search terms based on '
                         'the current geometry and the contact risks you want to investigate.'),
        'input': json.dumps(task, ensure_ascii=False, separators=(',', ':'), allow_nan=False),
        'tools': [{'type': 'function', 'name': name, 'description': 'Look up a compatible earlier experience.',
                   'strict': True, 'parameters': {'type': 'object', 'properties': {'query': {'type': 'string'}},
                                                  'required': ['query'], 'additionalProperties': False}}],
        'tool_choice': {'type': 'function','name': name}, 'parallel_tool_calls': False, 'truncation': 'disabled'}
    if config.reasoning_effort:
        request['reasoning'] = {'effort': config.reasoning_effort}
    return request


class FirstGraspMemory:
    def __init__(self, record, log_dir, episode_id):
        self.record = record
        self.log_dir = Path(log_dir)
        self.episode_id = episode_id

    def before_proposal(self, skills, context, agent):
        options = {'episode_id': self.episode_id, 'observation_revision': context['observation_revision']}
        query = build_query(skills, **options)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        query_file = self.log_dir/'initial_query.json'
        with query_file.open('x', encoding='utf-8') as stream:
            json.dump(query, stream, ensure_ascii=False, indent=2, allow_nan=False)
        text, response_id = agent.search_before_grasp(context)
        if build_query(skills, **options) != query:
            raise ValueError('The observed scene changed during the experience search.')
        hit = retrieve_experience(self.record, query, search_text=text)
        cards = [hit] if hit is not None else []
        write_json(self.log_dir/'retrieval.json', {
            'schema': 'assembly_first_grasp_retrieval/v1', 'episode_id': self.episode_id,
            'part_id': context['part_id'], 'observation_revision': context['observation_revision'],
            'search_text': text, 'initial_query': query, 'response_id': response_id,
            'status': 'hit' if cards else 'miss', 'experience_ids': [c['record_id'] for c in cards],
            'cards': cards})
        return cards
