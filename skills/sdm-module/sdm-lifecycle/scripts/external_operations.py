"""Provider-neutral operation routing and write/readback transactions.

Only project-bound official/UI adapters know provider commands. This module does
not discover credentials, grant authority, poll, or own lifecycle node acceptance.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

TIERS = {'official-sdk': 0, 'official-cli': 0, 'browser': 1, 'computer-use': 1, 'custom': 2}
TERMINAL = {'stopped', 'failed', 'succeeded', 'completed', 'cancelled'}


def sha(value):
    return 'sha256:' + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def save(path, value):
    from review_storage import atomic_json
    atomic_json(path,value)


def envelope(value, exit_code=0):
    """A zero process exit never overrides an error/approval/unknown response."""
    if not isinstance(value, dict):
        raise ValueError('structured_provider_response_required')
    if value.get('status') in {'pending_approval', 'approval-pending'}:
        from submission_guard import PendingApproval
        raise PendingApproval(value)
    if exit_code != 0 or value.get('status') != 'ok' or value.get('error'):
        raise ValueError('provider_response_failed')
    if value.get('code') not in (None, 0, 200) or value.get('http_status', 200) not in (200, 201, 202):
        raise ValueError('provider_response_failed')
    return value.get('data')


def attribution(evidence):
    """Classifier hints and echoed source text cannot prove runtime CUDA OOM."""
    if not isinstance(evidence, dict):
        raise ValueError('failure_evidence_required')
    lines = evidence.get('lines', [])
    if not isinstance(lines, list) or any(not isinstance(v, dict) for v in lines):
        raise ValueError('typed_log_lines_required')
    runtime = [v.get('text', '') for v in lines if v.get('kind') == 'runtime-exception']
    if evidence.get('training_started') is False and evidence.get('exception_type') == 'AssertionError':
        return {'category': 'preflight-assertion', 'parameter_change_supported': False}
    if evidence.get('training_started') is True and any(
            'torch.OutOfMemoryError' in line or 'CUDA out of memory' in line for line in runtime):
        return {'category': 'runtime-cuda-oom', 'parameter_change_supported': True}
    return {'category': 'unresolved', 'parameter_change_supported': False}


class Operation:
    def __init__(self, spec, state_path, timeout):
        self.spec, self.path = spec, Path(state_path)
        self.until = time.monotonic() + timeout
        if spec.get('schema') != 'sdm-external-operation/v1':
            raise ValueError('external_operation_schema_required')
        if spec.get('action') not in {'read', 'diagnose', 'submit', 'stop', 'switch'}:
            raise ValueError('external_operation_action_required')
        for key in ('provider', 'namespace', 'owner', 'request_key'):
            if not isinstance(spec.get(key), str) or not spec[key]:
                raise ValueError('external_operation_identity_required:' + key)
        self.state = json.loads(self.path.read_bytes()) if self.path.exists() else {
            'spec_digest': sha(spec), 'responses': {}, 'intents': []}
        if self.state.get('spec_digest') != sha(spec):
            raise ValueError('external_operation_changed')

    def command(self, name, uid=None):
        route = next(v for v in self.spec['routes'] if v['id'] == self.state['route'])
        refs=route.get('adapter_refs',[])
        if not isinstance(refs,list) or not refs:
            raise ValueError('pinned_project_adapters_required')
        for ref in refs:
            path=Path(ref['path'])
            if not path.is_absolute() or not path.is_file() or path.is_symlink() or 'sha256:'+hashlib.sha256(path.read_bytes()).hexdigest()!=ref['digest']:
                raise ValueError('external_adapter_changed')
        argv = route['commands'].get(name)
        if not isinstance(argv, list) or not argv or any(not isinstance(v, str) for v in argv):
            raise ValueError('operation_adapter_argv_required:' + name)
        argv = [v.replace('{uid}', uid or '').replace('{request_key}', self.spec['request_key']) for v in argv]
        remaining = self.until - time.monotonic()
        if remaining <= 0:
            raise ValueError('external_operation_deadline')
        import os
        result = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8', errors='strict',
            env={**os.environ, 'PYTHONIOENCODING':'utf-8'}, shell=False, timeout=remaining,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        # Raw provider output stays in the project adapter; do not duplicate
        # potentially sensitive CLI diagnostics or credentials in core receipts.
        try:
            value = json.loads(result.stdout)
        except ValueError:
            raise ValueError('structured_provider_response_required') from None
        return envelope(value, result.returncode)

    def route(self):
        if 'route' in self.state:
            return
        routes = self.spec.get('routes')
        if not isinstance(routes, list) or not routes or len({v['id'] for v in routes}) != len(routes):
            raise ValueError('unique_external_routes_required')
        if any(v.get('kind') not in TIERS for v in routes):
            raise ValueError('external_route_kind_required')
        required = {'switch': {'prepare', 'stop', 'read-old', 'submit', 'lookup', 'read-new'},
                    'submit': {'submit', 'lookup', 'read-new'}, 'stop': {'stop', 'read-old'},
                    'read': {'read'}, 'diagnose': {'diagnose'}}[self.spec['action']]
        probes = []
        for route in sorted(routes, key=lambda v: TIERS[v['kind']]):
            self.state['route'] = route['id']
            probe = self.command('probe')
            if (not isinstance(probe, dict) or type(probe.get('available')) is not bool
                or not isinstance(probe.get('operations'), list) or any(not isinstance(v,str) for v in probe['operations'])
                or not probe.get('source_ref') or not probe.get('version')):
                raise ValueError('capability_probe_contract_invalid')
            probes.append({'route': route['id'], 'checked_at':time.time(), **probe})
            if probe['available'] and required <= set(probe['operations']) and required <= set(route['commands']):
                if route['kind'] == 'custom' and not route.get('documentation_ref'):
                    raise ValueError('custom_adapter_documentation_required')
                self.state['probes'] = probes
                save(self.path, self.state)
                return
        self.state.pop('route', None)
        raise ValueError('external_capability_unavailable')

    def authorization(self):
        ref = self.spec.get('authorization_ref', {})
        path = Path(ref.get('path', ''))
        if not path.is_file() or 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest() != ref.get('digest'):
            raise ValueError('bound_external_authorization_required')
        value = json.loads(path.read_bytes())
        for key in ('provider', 'namespace', 'owner', 'request_key', 'action','component_uid','configuration_digest'):
            if value.get(key) != self.spec[key]:
                raise ValueError('external_authorization_scope_mismatch')
        if value.get('direct_user_confirmation') is not True or not value.get('source_ref'):
            raise ValueError('direct_user_authorization_required')
        if value.get('old_uid') != self.spec.get('old_uid'):
            raise ValueError('external_authorization_old_uid_mismatch')

    def identity(self, value, uid=None, request=False):
        if not isinstance(value, dict):
            raise ValueError('object_readback_required')
        for key in ('provider', 'namespace', 'owner'):
            if value.get(key) != self.spec[key]:
                raise ValueError('readback_identity_mismatch:' + key)
        if not isinstance(value.get('uid'), str) or not value['uid'] or (uid is not None and value['uid'] != uid):
            raise ValueError('readback_uid_mismatch')
        if request and value.get('request_key') != self.spec['request_key']:
            raise ValueError('readback_request_mismatch')
        observed=value.get('observed_at')
        if type(observed) not in (int,float) or not 0<=time.time()-observed<=self.spec.get('readback_max_age_seconds',120):
            raise ValueError('fresh_object_readback_required')
        if request and value.get('component_uid')!=self.spec.get('component_uid'):
            raise ValueError('readback_component_mismatch')
        return value

    def write(self, name, uid=None):
        self.authorization()
        if name in self.state['responses']:
            return self.state['responses'][name]
        if name in self.state['intents']:
            raise ValueError('uncertain_effect_requires_reconciliation:' + name)
        self.state['intents'].append(name)
        save(self.path, self.state)  # Durable intent before any external mutation.
        if name=='submit' and getattr(self,'guard',None):self.guard.before_submit(self.path)
        try:response = self.command(name, uid)
        except ValueError as error:
            from submission_guard import PendingApproval
            if isinstance(error,PendingApproval):
                pending={'command':name,'route':self.state['route'],**error.approval}
                if name=='submit' and getattr(self,'guard',None):self.guard.pending(error.approval,self.state['route'])
                self.state['pending_approval']=pending;save(self.path,self.state)
            raise
        self.state['responses'][name] = response
        save(self.path, self.state)
        return response

    def released(self):
        old = self.identity(self.command('read-old', self.spec['old_uid']), self.spec['old_uid'])
        if old.get('status') not in TERMINAL or not isinstance(old.get('pods'), list) or any(
                v.get('status') not in TERMINAL | {'Succeeded', 'Failed'} for v in old['pods']):
            raise ValueError('old_resource_release_not_verified')
        if old.get('released') is not True:
            raise ValueError('old_resource_release_not_verified')
        return old

    def run(self):
        from review_storage import locked
        with locked(self.path.parent,'.'+self.path.name+'.lock','external_operation_busy'):
            if self.path.exists():
                self.state=json.loads(self.path.read_bytes())
                if self.state.get('spec_digest')!=sha(self.spec):raise ValueError('external_operation_changed')
            if self.spec['action'] in {'submit','switch'}:
                from submission_guard import Guard
                self.guard=Guard(self.spec,self.path)
                with self.guard.lock():
                    self.guard.load()
                    if self.guard.state['phase']=='pending-approval':
                        self.pending_approval={'command':'submit','route':self.guard.state['route'],**self.guard.state['approval']}
                        raise ValueError('provider_approval_pending_do_not_fallback')
                    if self.guard.state['phase']=='intent' and self.guard.state.get('state_path')!=str(self.path.resolve()):
                        raise ValueError('uncertain_submission_do_not_resubmit')
                    if self.state.get('pending_approval',{}).get('command')=='submit':raise ValueError('approval_resolution_requires_new_attempt')
                    return self._run()
            return self._run()

    def _run(self):
        self.route()
        action = self.spec['action']
        if action == 'read':
            return self.identity(self.command('read'))
        if action == 'diagnose':
            return attribution(self.command('diagnose'))
        self.authorization()
        adopted=getattr(self,'guard',None) and self.guard.state['phase']=='completed'
        if action == 'switch' and not adopted:
            prepared = self.write('prepare')
            if (not isinstance(prepared, dict) or prepared.get('configuration_verified') is not True
                or prepared.get('component_uid')!=self.spec.get('component_uid')
                or not self.spec.get('configuration_digest')
                or prepared.get('configuration_digest')!=self.spec['configuration_digest']):
                raise ValueError('replacement_configuration_not_verified')
        if action in {'stop', 'switch'} and not adopted:
            if 'stop' not in self.state['intents']:
                self.write('stop', self.spec['old_uid'])
            old = self.released()  # Pending/failed stop is never release proof.
            if action == 'stop':
                return old
        # Reconcile both after an interrupted submission and before a new one.
        found = self.command('lookup')
        if not isinstance(found, dict) or found.get('complete') is not True or not isinstance(found.get('matches'), list):
            raise ValueError('complete_lookup_required')
        matches = found['matches']
        if len(matches) > 1:
            raise ValueError('ambiguous_submission_identity')
        if matches:
            uid = self.identity(matches[0], request=True)['uid']
            if getattr(self,'guard',None):self.guard.completed(uid)
        elif getattr(self,'guard',None) and self.guard.state['phase'] not in {'new','resolved-no-effect'}:
            raise ValueError('uncertain_submission_do_not_resubmit')
        elif 'submit' in self.state['intents']:
            raise ValueError('uncertain_submission_do_not_resubmit')
        else:
            result = self.write('submit')
            if not isinstance(result, dict) or not result.get('uid'):
                raise ValueError('submission_uid_required')
            uid = result['uid']
        value=self.identity(self.command('read-new', uid), uid, request=True)
        if getattr(self,'guard',None):self.guard.completed(uid)
        return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('spec', 'state', 'receipt', 'node'):
        parser.add_argument('--' + flag, required=True)
    parser.add_argument('--timeout', type=float, required=True)
    args = parser.parse_args()
    try:
        operation = Operation(json.loads(Path(args.spec).read_bytes()), args.state, args.timeout)
        observation = operation.run()
        result = {'task_node': args.node, 'status': 'completed', 'external_operation': {
            'schema': 'sdm-external-operation-result/v1', 'spec_digest': sha(operation.spec),
            'route': operation.state['route'], 'observation': observation}}
        if isinstance(observation,dict) and all(observation.get(k) for k in ('provider','namespace','uid')):
            result['external_operation']['resource_identity']={k:observation[k] for k in ('provider','namespace','uid')}
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        result = {'task_node': args.node, 'status': 'failed', 'reason': str(error),
                  'uncertain_effect': True}
        if 'operation' in locals() and (operation.state.get('pending_approval') or getattr(operation,'pending_approval',None)):
            result['pending_approval']=getattr(operation,'pending_approval',None) or operation.state['pending_approval']
            result['next_action']=('reconcile-submission; no alternative submit while approval can execute'
                if result['pending_approval'].get('command')=='submit' else 'verify original operation status; never retry the write blindly')
    save(Path(args.receipt), result)
    return 0 if result['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
