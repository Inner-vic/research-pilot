"""R/W/V ownership contracts. Metadata only; never a second task state store."""
from copy import deepcopy

REVISION = 1
INTERFACES = {
    'resource': {
        'interface': 'sdm-resource-module/v1',
        'operations': {'26': 'investigate', '27': 'propose', '28': 'confirm',
                       '14': 'acquire', '30': 'release'},
        'implementation': ['resource_planning', 'bmad-resource-investigation', 'project-provider-adapter'],
        'state_owners': {
            'accepted_nodes': 'task_pipeline.events.jsonl',
            'proposal_confirmation': 'project-record-producer; task_pipeline accepts and pins',
            'request_resource_identity': 'project-provider-adapter',
            'cleanup_acceptance': 'task_pipeline.cleanup.json',
            'observed_platform_state': 'provider; adapter supplies same-identity readback'},
        'resume': 'Reconcile started request before retry; preserve original deadline; never reacquire after release.',
    },
    'workspace': {
        'interface': 'sdm-workspace-module/v1',
        'operations': {'23': 'isolate', '8': 'freeze'},
        'implementation': ['worktree_isolation', 'project-provider-adapter'],
        'state_owners': {
            'accepted_nodes': 'task_pipeline.events.jsonl',
            'worktree_ownership_snapshot': 'worktree_isolation locked private records and Git owner marker',
            'runtime_input_identity': 'project-provider-adapter'},
        'resume': 'Verify owned registration and frozen bytes; preserve edits; never adopt an unknown worktree.',
    },
    'review-collaboration': {
        'interface': 'sdm-review-module/v1',
        'operations': {'29': 'prepare-context', '31': 'exchange'},
        'implementation': ['review_collaboration', 'pro_review_session', 'pro_review_workflow'],
        'state_owners': {
            'accepted_nodes': 'task_pipeline.events.jsonl',
            'context_response': 'configured reviewer produces; task_pipeline accepts and pins',
            'pro_session_delivery': 'pro_review_session and pro_review_workflow existing CAS records',
            'scientific_acceptance': 'phase gates 25/34/19/32/20/33'},
        'resume': 'Restore purpose, request and context revision; reconcile uncertain send in same chat; no compute authority.',
    },
}


def describe(owner):
    return deepcopy(INTERFACES[owner])


def for_node(key):
    key = str(key)
    for owner, spec in INTERFACES.items():
        if key in spec['operations']:
            return {'module': owner, 'interface': spec['interface'],
                    'operation': spec['operations'][key],
                    'acceptance_owner': 'task_pipeline',
                    'authority': 'existing-task-scope-only'}
    return None


def call(root, key, driver, deadline):
    """Durable invocation identity; provider receipt remains the operation result."""
    from pathlib import Path
    from task_pipeline import digest
    contract = for_node('30' if key == 'cleanup' else key)
    if contract is None:
        return None
    return {'schema': 'sdm-capability-call/v1', **contract,
            'attempt': str(Path(root).resolve()), 'node': str(key),
            'driver_digest': digest(driver), 'deadline': deadline,
            'result_contract': 'task-node-status-and-receipt/v1',
            'execution_authority_created': False}
