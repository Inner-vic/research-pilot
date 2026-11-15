"""Recorded-shape fake CLI only; selectors here do not assert the live LPAI response schema."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from lpai_paired_adapter import LpaiAdapter
from paired_execution import request_for
from round_reporting import digest
from test_comparison_preflight import plan
from test_paired_execution import ref


def request():
    state = {'resource_deadline': 8200, 'monitoring': {'call_timeout_seconds': 2, 'health_max_age_seconds': 5}, 'contract_digest': 'fixture-contract',
             'nonce': 'fixture-nonce', 'arms': {}}
    return request_for(state, plan(), plan()['arms'][0], 1000)


def settings():
    task_fields = ('uid', 'name', 'namespace', 'owner', 'component_uid', 'request_marker', 'status', 'gpus', 'hourly_cost')
    arm = {'component_uid': 'fixture-component', 'component_name': 'fixture-component-name',
           'task_info': {'problem_statement': 'fixture problem', 'solution_approach': 'fixture method',
                         'expected_result': 'fixture result', 'expected_duration': 10, 'expected_duration_desc': 'fixture estimate'}}
    return {'schema': 'sdm-lpai-paired-adapter/v1', 'namespace': 'fixture-namespace', 'owner': 'fixture-owner',
            'project': 'fixture-project', 'cli': '/fixture/lpai-agent', 'page_size': 2, 'max_lookup_pages': 2,
            'phase_map': {'Running': 'running', 'Stopped': 'stopped', 'Succeeded': 'succeeded'},
            'fields': {'task': {k: [k] for k in task_fields}, 'component': {'uid': ['uid'], 'namespace': ['namespace']},
                       'start': {'uid': ['uid']}, 'list': {'items': ['items'], 'total': ['total']}},
            'arms': {'control': deepcopy(arm), 'treatment': deepcopy(arm)}}


class Hooks:
    def __init__(self, tmp_path): self.tmp = tmp_path; self.guard_scope = 'allocation-release'; self.released = False
    def preflight(self, r, component):
        return {'ready': True, 'project': r['project'], 'observed_at': 1000, 'external_allocations': [],
                'normal_preflight_ref': 'SYNTHETIC preflight',
                'lease_guard': {'deadline': r['deadline'], 'scope': self.guard_scope, 'independent': True,
                                'evidence_ref': 'SYNTHETIC native TTL'},
                'body_ref': ref(self.tmp / 'body.json', {'fixture': True}),
                'body_binding': {'request_key': r['request_key'], 'deadline': r['deadline'], 'arm_digest': r['declared_arm_digest']}}
    def observe(self, r, task):
        return {'uid': task['uid'], 'request_key': r['request_key'], 'released': self.released, 'observed_at': 1000,
                'release_evidence_ref': 'SYNTHETIC release' if self.released else None}


class Transport:
    def __init__(self, r): self.r = r; self.calls = []; self.jobs = []; self.total = None; self.pages = None
    def task(self, uid='fixture-task'):
        return {'uid': uid, 'name': self.r['job_name'], 'namespace': 'fixture-namespace', 'owner': 'fixture-owner',
                'component_uid': 'fixture-component', 'request_marker': 'bmad-paired-request:' + self.r['request_key'],
                'status': 'Running', 'gpus': 4, 'hourly_cost': 2.5}
    def __call__(self, argv, timeout):
        self.calls.append(argv); command = argv[1:3]
        if command == ['projects', 'get']: return {'uid': 'fixture-component', 'namespace': 'fixture-namespace'}
        if command == ['projects', 'start']:
            self.jobs = [self.task()]; return {'uid': 'fixture-task'}
        if command == ['tasks', 'get']: return deepcopy(self.jobs[0])
        if command == ['tasks', 'stop']: return {'ack': True}
        if command == ['tasks', 'list']:
            page = int(argv[argv.index('--page-num') + 1])
            return {'items': deepcopy(self.jobs if self.pages is None else self.pages[page-1]),
                    'total': len(self.jobs) if self.total is None else self.total}
        raise AssertionError(command)


def adapter(tmp_path):
    r = request(); t = Transport(r); h = Hooks(tmp_path)
    return LpaiAdapter(settings(), transport=t, hooks=h, clock=lambda: 1000), r, t, h


def test_native_detached_submission_has_five_required_info_fields_and_exact_uid_readback(tmp_path):
    a, r, t, h = adapter(tmp_path); a.preflight(r)
    assert a.lookup(r) == {'complete': True, 'matches': []}
    result = a.submit(r)
    argv = next(x for x in t.calls if x[1:3] == ['projects', 'start'])
    for flag in ('--problem-statement', '--solution-approach', '--expected-result', '--expected-duration', '--expected-duration-desc', '--body-file', '--describe'):
        assert flag in argv
    assert '--api-key' not in argv and '--body' not in argv
    assert result['uid'] == 'fixture-task' and result['observation']['phase'] == 'running'
    assert t.calls[-1][1:3] == ['tasks', 'get'] and '--uid' in t.calls[-1]


@pytest.mark.parametrize('field', ['namespace', 'owner', 'component_uid', 'request_marker', 'name', 'uid'])
def test_foreign_identity_cannot_be_stopped(tmp_path, field):
    a, r, t, h = adapter(tmp_path); t.jobs = [t.task()]; t.jobs[0][field] = 'foreign'
    with pytest.raises(ValueError, match='identity_mismatch'): a.stop('fixture-task', r, 'deadline')
    assert not any(x[1:3] == ['tasks', 'stop'] for x in t.calls)


def test_stop_ack_and_terminal_phase_are_not_allocation_release(tmp_path):
    a, r, t, h = adapter(tmp_path); t.jobs = [t.task()]
    assert a.stop('fixture-task', r, 'deadline')['release_verified'] is False
    t.jobs[0]['status'] = 'Stopped'
    assert a.observe('fixture-task', r)['released'] is False
    h.released = True
    assert a.observe('fixture-task', r)['released'] is True


def test_active_job_cannot_claim_released_resources(tmp_path):
    a, r, t, h = adapter(tmp_path); t.jobs = [t.task()]; h.released = True
    with pytest.raises(ValueError, match='release_evidence_required'): a.observe('fixture-task', r)


def test_process_guard_does_not_satisfy_allocation_release_guard(tmp_path):
    a, r, t, h = adapter(tmp_path); h.guard_scope = 'process-stop'
    with pytest.raises(ValueError, match='allocation_release_guard'): a.preflight(r)
    assert not any(x[1:3] == ['projects', 'start'] for x in t.calls)


@pytest.mark.parametrize('fault', ['missing-mapping', 'missing-field', 'unmapped-phase', 'non-total-gpus'])
def test_unknown_native_schema_never_guesses(tmp_path, fault):
    s = settings(); r = request(); t = Transport(r); h = Hooks(tmp_path); t.jobs = [t.task()]
    if fault == 'missing-mapping':
        del s['fields']['task']['uid']
        with pytest.raises(ValueError): LpaiAdapter(s, transport=t, hooks=h)
        return
    if fault == 'missing-field': del t.jobs[0]['gpus']
    if fault == 'unmapped-phase': t.jobs[0]['status'] = 'new-unrecognized-status'
    if fault == 'non-total-gpus': t.jobs[0]['gpus'] = '4'
    a = LpaiAdapter(s, transport=t, hooks=h)
    with pytest.raises(ValueError): a.observe('fixture-task', r)


def test_complete_paginated_lookup_and_bounded_partial_results(tmp_path):
    a, r, t, h = adapter(tmp_path)
    t.pages = [[t.task('one')], [t.task('two')]]; t.total = 2
    result = a.lookup(r); assert result['complete'] and len(result['matches']) == 2
    t.total = 3
    assert a.lookup(r)['complete'] is False


@pytest.mark.parametrize('fault', ['empty-before-total', 'duplicate-page', 'collision'])
def test_lookup_missing_or_foreign_collisions_never_prove_absence(tmp_path, fault):
    a, r, t, h = adapter(tmp_path)
    if fault == 'empty-before-total': t.pages = [[]]; t.total = 1
    if fault == 'duplicate-page': t.pages = [[t.task()], [t.task()]]; t.total = 2
    if fault == 'collision': t.jobs = [t.task()]; t.jobs[0]['request_marker'] = 'foreign-request'
    with pytest.raises(ValueError): a.lookup(r)


def test_native_prepared_payload_bytes_and_request_cannot_drift(tmp_path):
    a, r, t, h = adapter(tmp_path)
    with pytest.raises(ValueError): a.submit(r)
    a.preflight(r); (tmp_path / 'body.json').write_text('{"modified": true}')
    with pytest.raises(ValueError, match='evidence_bytes_changed'): a.submit(r)
    assert not any(x[1:3] == ['projects', 'start'] for x in t.calls)


def test_cli_timeout_is_bounded_and_does_not_print_credentials(tmp_path, monkeypatch):
    a, r, t, h = adapter(tmp_path)
    executable = tmp_path / 'lpai-agent'; executable.write_bytes(b'fixture executable')
    a.settings['cli'] = str(executable.resolve()); a.settings['cli_digest'] = 'sha256:' + hashlib.sha256(executable.read_bytes()).hexdigest()
    a.transport = None
    def timeout(argv, **kwargs):
        assert kwargs['shell'] is False and kwargs['timeout'] == 2
        raise subprocess.TimeoutExpired(argv, 2, output=b'SENSITIVE credential')
    monkeypatch.setattr(subprocess, 'run', timeout)
    with pytest.raises(RuntimeError) as error: a.query(r, ['tasks', 'get'], ['--uid', 'fixture-task'])
    assert 'SENSITIVE' not in str(error.value) and 'outcome_unknown' in str(error.value)


def test_native_cli_and_project_hook_binding_changed_do_not_run_changed_code(tmp_path):
    a, r, t, h = adapter(tmp_path)
    executable = tmp_path / 'lpai-agent'; executable.write_bytes(b'changed')
    a.settings.update(cli=str(executable.resolve()), cli_digest='sha256:wrong'); a.transport = None
    with pytest.raises(ValueError, match='cli_binding_changed'): a.query(r, ['tasks', 'get'], ['--uid', 'fixture-task'])


def test_native_missing_task_info_blocks_before_submit(tmp_path):
    a, r, t, h = adapter(tmp_path); a.preflight(r)
    del a.settings['arms']['control']['task_info']['expected_result']
    with pytest.raises(ValueError, match='task_info_required'): a.submit(r)
    assert not any(x[1:3] == ['projects', 'start'] for x in t.calls)
