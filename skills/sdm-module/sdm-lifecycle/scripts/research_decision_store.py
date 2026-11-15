"""Single-writer durable decision commits; response files are derived views.

The shared research-control lock serializes commit, recovery and consumption.
One atomic record contains the approved payload, provenance and pending outbox
item. No existing event database or frozen v1 run is migrated.
"""
import hashlib
from pathlib import Path

from review_storage import atomic_json, read_json
from task_pipeline import digest

SCHEMA = 'sdm-research-decision-commit/v1'


def seal(value):
    content = {k: v for k, v in value.items() if k != 'integrity'}
    return {**content, 'integrity': digest(content)}


def check_root(config, root):
    root = Path(root).resolve()
    cfg = config['research_worker']
    if read_json(cfg['project_path']) != config:
        raise ValueError('persisted_worker_project_changed')
    if (root / 'research-ready.json').exists():
        raise ValueError('new_frozen_attempt_required_for_atomic_worker')
    for path in (root / 'research-decisions').glob('*/state.json'):
        if path.with_name('commit.json').exists():
            continue  # a derived cache can be rebuilt from its commit
        if read_json(path).get('schema') != 'sdm-research-decision/v' + str(cfg['version']):
            raise ValueError('new_frozen_attempt_required_for_atomic_worker')
    return root


def validate(config, root, path, *, check_control=True):
    root = Path(root).resolve()
    path = Path(path).resolve()
    if path.parent.parent != root / 'research-decisions' or path.name != 'commit.json':
        raise ValueError('bound_decision_commit_required')
    value = read_json(path)
    if (value.get('schema') != SCHEMA or value.get('integrity') != seal(value)['integrity']
            or value.get('project_digest') != digest(config)
            or value.get('decision_id') != path.parent.name
            or type(value.get('consumed')) is not bool):
        raise ValueError('decision_commit_integrity_failed')
    record = value['record']
    response = Path(record['response_path']).resolve()
    request = Path(record['request_path']).resolve()
    if (record.get('schema') != 'sdm-research-decision/v' + str(config['research_worker']['version']) or record.get('status') != 'approved'
            or not response.is_relative_to(root) or request.parent != response.parent
            or request.name != 'analysis-request.json' or response.name != 'analysis-response.json'):
        raise ValueError('bound_committed_review_required')
    packet = read_json(request)
    if digest(packet) != record['request_digest']:
        raise ValueError('approved_request_changed')
    sources = record['sources']
    if str(request) not in sources or str(Path(config['research_worker']['project_path'])) not in sources:
        raise ValueError('approved_source_binding_required')
    for source, expected in sources.items():
        actual = digest(read_json(source)) if Path(source).resolve() == request else 'sha256:' + hashlib.sha256(Path(source).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError('review_source_changed')
    expected_id = digest({'config': config, 'request': packet, 'sources': sources,
                          'control_revision': record['control_revision']}).split(':')[-1]
    if value['decision_id'] != expected_id:
        raise ValueError('decision_commit_identity_changed')
    if config['research_worker']['version'] >= 3:
        from research_model_receipts import validate_committed
        validate_committed(config['research_worker'], path.parent, record.get('model_receipts'))
    payload_digest = digest(value['response'])
    if (payload_digest != record['response_digest']
            or value['critique'].get('verdict') != 'approve'
            or value['critique'].get('draft_digest') != payload_digest):
        raise ValueError('committed_approval_mismatch')
    expected_notice = digest({'decision_id': expected_id, 'kind': 'review-approved'})
    if (value.get('outbox', {}).get('id') != expected_notice
            or value['outbox'].get('kind') != 'review-approved'
            or value['outbox'].get('delivered') is not False):
        raise ValueError('committed_outbox_invalid')
    if check_control and not value['consumed']:
        control_path = root / 'research-control.json'
        steering = read_json(control_path) if control_path.exists() else {'revision': 0, 'paused': False}
        if type(steering.get('revision')) is not int or type(steering.get('paused')) is not bool:
            raise ValueError('invalid_research_control')
        if steering.get('paused') or steering.get('revision') != record['control_revision']:
            raise ValueError('stale_decision_requires_reconciliation')
    return value


def existing(config, root, job, response, review_recovery=None):
    """Resolve an owner before considering a new, potentially charged call."""
    root = check_root(config, root)
    target = Path(response).resolve()
    owner = None
    for path in (root / 'research-decisions').glob('*/commit.json'):
        value = validate(config, root, path)
        if Path(value['record']['response_path']).resolve() == target:
            if owner is not None:
                raise ValueError('decision_response_already_owned')
            owner = path
    if owner is not None:
        return owner
    released_claims=set()
    if review_recovery is not None:
        from result_review_recovery import validate as validate_recovery
        recovery=validate_recovery(config,root,review_recovery)
        released_claims={Path(row['path']).resolve().parent.parent for row in recovery['unlaunched_intents']}
    for path in (root / 'research-decisions').glob('*/state.json'):
        if path.parent.resolve() == Path(job).resolve() or path.with_name('commit.json').exists():
            continue
        value = read_json(path)
        if value.get('response_path') and Path(value['response_path']).resolve() == target:
            if path.parent.resolve() in released_claims:continue
            raise ValueError('unreconciled_decision_claim')
    return None


def materialize(config, root, path):
    """Rebuild missing views from a validated commit without model calls."""
    value = validate(config, root, path)
    record = value['record']
    response = Path(record['response_path'])
    if response.exists():
        if digest(read_json(response)) != record['response_digest']:
            raise ValueError('published_response_changed')
    else:
        atomic_json(response, value['response'])
    projected = {**record, 'consumed': value['consumed'], 'commit_path': str(Path(path).resolve()),
                 'commit_integrity': value['integrity']}
    state_path = Path(path).with_name('state.json')
    try:
        current = read_json(state_path) if state_path.exists() else None
    except ValueError:
        current = None  # derived cache only; the commit was checked above
    if current != projected:
        atomic_json(state_path, projected)
    return projected


def publish(config, root, job, record, response, critique):
    """Commit authority and its outbox before exporting either derived view."""
    root = check_root(config, root)
    job = Path(job).resolve()
    target = Path(record['response_path']).resolve()
    path = job / 'commit.json'
    if path.exists():
        previous = validate(config, root, path)
        if previous['record'] != record or previous['response'] != response or previous['critique'] != critique:
            raise ValueError('immutable_committed_decision_changed')
        return materialize(config, root, path)
    for other in (root / 'research-decisions').glob('*/commit.json'):
        old = validate(config, root, other)
        if Path(old['record']['response_path']).resolve() == target:
            raise ValueError('decision_response_already_owned')
    notice_id = digest({'decision_id': job.name, 'kind': 'review-approved'})
    value = seal({'schema': SCHEMA, 'decision_id': job.name, 'project_digest': digest(config),
        'record': record, 'response': response, 'critique': critique, 'consumed': False,
        'outbox': {'id': notice_id, 'kind': 'review-approved', 'delivered': False,
                   'summary': response['previous_experiment_assessment']}})
    atomic_json(path, value)  # the single authoritative publication point
    return materialize(config, root, path)


def before_advance(config, root):
    root = check_root(config, root)
    commits = []
    targets = set()
    for path in (root / 'research-decisions').glob('*/commit.json'):
        value = validate(config, root, path, check_control=config['research_worker']['version'] < 4)
        if config['research_worker']['version'] >= 4:
            from research_consumption import recover
            value = recover(config, root, path, value)
            validate(config, root, path)  # unaccepted stale decisions still block
        response = Path(value['record']['response_path']).resolve()
        if response in targets:
            raise ValueError('decision_response_already_owned')
        targets.add(response)
        commits.append((path, value))
    visible = set()
    for state_path in (root / 'research-decisions').glob('*/state.json'):
        if state_path.with_name('commit.json').exists():
            continue
        state = read_json(state_path)
        for source in state.get('sources', {}):
            request = Path(source).resolve()
            if request.name == 'analysis-request.json' and request.is_relative_to(root):
                possible = request.with_name('analysis-response.json')
                if possible.exists():
                    visible.add(possible)
    for pattern in ('review/analysis-response.json', 'review/prepared/*/analysis-response.json',
                    'round-*/review/prepared/*/analysis-response.json',
                    'round-*/attempt-*/review/prepared/*/analysis-response.json'):
        visible.update(path.resolve() for path in root.glob(pattern))
    if visible - targets:
        raise ValueError('uncommitted_research_response')
    for path, _ in commits:
        materialize(config, root, path)
    return commits


def consumed(config, root, commits, result):
    """Consume only the exact review accepted by the shared coordinator."""
    review = result.get('review', {})
    request_digest = review.get('decision', {}).get('request_digest')
    if config['research_worker']['version'] >= 4:
        from research_consumption import recover
        for path, _ in commits:
            current = validate(config, root, path, check_control=False)
            recovered = recover(config, root, path, current)
            if (not recovered['consumed'] and review.get('status') == 'completed'
                    and request_digest == current['record']['request_digest']):
                raise ValueError('durable_coordinator_acceptance_required')
        return
    if review.get('status') != 'completed' or not request_digest:
        return
    for path, value in commits:
        record = value['record']
        if record['request_digest'] != request_digest or value['consumed']:
            continue
        case_directory = review.get('case_directory')
        if not case_directory or Path(case_directory).resolve() != Path(record['response_path']).parent.resolve():
            continue
        current = validate(config, root, path)
        atomic_json(path, seal({**current, 'consumed': True}))
        materialize(config, root, path)
