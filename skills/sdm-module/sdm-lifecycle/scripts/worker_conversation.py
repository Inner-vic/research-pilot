"""Bound latest semantic changes as immutable shared Generator-Critic evidence, opt-in only."""
import json
from pathlib import Path

from project_driver import file_digest
from review_storage import atomic_json, read_json
from task_pipeline import digest


def snapshot(config, root, sources):
    if config['research_worker'].get('live_context_required') is not True:
        return None
    from research_observation import context, settings
    current = context(config, settings(config)['question_id'])
    value = {'schema': 'sdm-worker-conversation/v1', 'project_id': current['project_id'],
        'revision': current['revision'], 'nodes': current['nodes'][-8:],
        'window': 'latest-8-semantic-records; original history retained in research store',
        'has_more': current['has_more'] or current['total_nodes'] > 8,
        'execution_authority': False, 'scope': 'Conversation changes; resource authority remains with the original Guard'}
    if len(json.dumps(value, ensure_ascii=False).encode('utf-8')) > 65536:
        raise ValueError('worker_conversation_snapshot_exceeds_bound')
    path = Path(root) / 'worker-conversation' / (str(value['revision']) + '-' + digest(value).removeprefix('sha256:')[:24] + '.json')
    if path.exists():
        if read_json(path) != value:
            raise ValueError('immutable_worker_conversation_changed')
    else:
        atomic_json(path, value)
    sources[str(path)] = file_digest(path)
    return path
