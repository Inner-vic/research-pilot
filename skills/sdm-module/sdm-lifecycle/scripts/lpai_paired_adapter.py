"""Native LPAI detached-task adapter. Explicit field mappings and sealed project hooks are required."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from comparison_reporting import load_ref
from paired_execution import ACTIVE, TERMINAL, bound_module, number
from project_driver import resolve


def at(value, selector):
    if not isinstance(selector, list) or not selector or any(type(x) not in (str, int) for x in selector):
        raise ValueError('explicit_native_field_selector_required')
    for part in selector:
        try: value = value[part]
        except (KeyError, IndexError, TypeError): raise ValueError('native_field_unavailable') from None
    return value


class LpaiAdapter:
    is_live = True

    def __init__(self, settings, transport=None, hooks=None, clock=time.time):
        self.settings = settings; self.transport = transport; self.hooks = hooks; self.clock = clock
        self.prepared = {}
        if settings.get('schema') != 'sdm-lpai-paired-adapter/v1': raise ValueError('lpai_adapter_schema_required')
        for key in ('namespace', 'owner', 'project', 'cli'):
            if not isinstance(settings.get(key), str) or not settings[key].strip(): raise ValueError('native_scope_required:' + key)
        if not Path(settings['cli']).is_absolute(): raise ValueError('absolute_cli_required')
        if type(settings.get('max_lookup_pages')) is not int or not 1 <= settings['max_lookup_pages'] <= 20:
            raise ValueError('bounded_lookup_pages_required')
        if type(settings.get('page_size')) is not int or not 1 <= settings['page_size'] <= 100:
            raise ValueError('bounded_page_size_required')
        if not isinstance(settings.get('arms'), dict) or len(settings['arms']) != 2: raise ValueError('two_native_arm_configs_required')
        if not isinstance(settings.get('phase_map'), dict) or not settings['phase_map']:
            raise ValueError('explicit_native_phase_map_required')
        if any(v not in ACTIVE | TERMINAL for v in settings['phase_map'].values()): raise ValueError('invalid_native_phase_mapping')
        if settings.get('allocation_mode', 'fields') not in ('fields', 'lpai-role-quote'):
            raise ValueError('explicit_allocation_mode_required')
        task_fields = ('uid', 'name', 'namespace', 'owner', 'component_uid', 'request_marker', 'status')
        if settings.get('allocation_mode', 'fields') == 'fields': task_fields += ('gpus', 'hourly_cost')
        for section, fields in {'component': ('uid', 'namespace'), 'start': ('uid',), 'list': ('items', 'total'),
                                'task': task_fields}.items():
            for field in fields: self.selector(section, field)
        if hooks is None: resolve({'project_driver': settings.get('project_hooks')}, Path.cwd())

    def selector(self, section, field):
        value = self.settings.get('fields', {}).get(section, {}).get(field)
        if not isinstance(value, list) or not value or any(type(x) not in (str, int) for x in value):
            raise ValueError('explicit_native_field_selector_required:' + section + '.' + field)
        return value

    def query(self, request, command, arguments):
        timeout = request.get('call_timeout_seconds')
        if not number(timeout, .001) or timeout > 60: raise ValueError('bounded_cli_timeout_required')
        argv = [self.settings['cli'], *command, '--namespace', self.settings['namespace'], *arguments,
                '--timeout', str(max(1, int(timeout)))]
        if self.transport is not None: result = self.transport(argv, timeout)
        else:
            executable = Path(self.settings['cli'])
            if executable.is_symlink() or not executable.is_file(): raise ValueError('regular_native_cli_required')
            if 'sha256:' + hashlib.sha256(executable.read_bytes()).hexdigest() != self.settings.get('cli_digest'):
                raise ValueError('native_cli_binding_changed')
            try:
                completed = subprocess.run(argv, capture_output=True, timeout=timeout, shell=False, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except subprocess.TimeoutExpired: raise RuntimeError('native_cli_timeout_outcome_unknown') from None
            if completed.returncode: raise RuntimeError('native_cli_nonzero_outcome_unknown')
            if len(completed.stdout) > 8 * 1024 * 1024: raise RuntimeError('native_response_too_large')
            try: result = json.loads(completed.stdout, parse_constant=lambda x: (_ for _ in ()).throw(ValueError('nonfinite_native_json')))
            except ValueError: raise RuntimeError('native_response_not_json') from None
        if not isinstance(result, dict): raise ValueError('native_response_object_required')
        # No stdout/stderr/credential values are written into state or exception text.
        return result

    def hook(self, operation, *args):
        if self.hooks is not None:
            value = getattr(self.hooks, operation)(*args)
        else:
            if os.name != 'posix': raise ValueError('posix_project_hook_execution_host_required')
            resolve({'project_driver': self.settings['project_hooks']}, Path.cwd())
            timeout = args[0]['call_timeout_seconds']
            payload = json.dumps({'binding': self.settings['project_hooks'], 'settings': self.settings.get('hook_settings', {}),
                                  'args': args}, allow_nan=False).encode()
            env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
            child = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()), '--hook', operation],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     shell=False, start_new_session=os.name == 'posix', env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                stdout, _ = child.communicate(payload, timeout=timeout)
                if child.returncode or len(stdout) > 8 * 1024 * 1024: raise RuntimeError('project_hook_failed')
                value = json.loads(stdout)
            except subprocess.TimeoutExpired:
                raise RuntimeError('project_hook_timeout') from None
            finally:
                if os.name == 'posix':
                    try: os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                elif child.poll() is None: child.kill()
                child.communicate(timeout=min(timeout, 5))
        if not isinstance(value, dict): raise ValueError('project_hook_object_required')
        return value

    def arm(self, request):
        if request.get('project') != self.settings['project']: raise ValueError('native_project_scope_mismatch')
        arm = self.settings['arms'].get(request.get('arm_id'))
        if not isinstance(arm, dict): raise ValueError('native_arm_configuration_required')
        for key in ('component_uid', 'component_name'):
            if not isinstance(arm.get(key), str) or not arm[key]: raise ValueError('native_component_identity_required')
        return arm

    def marker(self, request): return 'bmad-paired-request:' + request['request_key']

    def identity(self, task, request, uid=None):
        from lpai_native_evidence import job_matches
        arm = self.arm(request)
        expected = {'namespace': self.settings['namespace'], 'owner': self.settings['owner'],
                    'component_uid': arm['component_uid'], 'request_marker': self.marker(request)}
        if uid is not None: expected['uid'] = uid
        for key, wanted in expected.items():
            if at(task, self.selector('task', key)) != wanted: raise ValueError('native_task_identity_mismatch:' + key)
        found = at(task, self.selector('task', 'uid'))
        if not isinstance(found, str) or not found: raise ValueError('native_task_uid_required')
        if not job_matches(at(task, self.selector('task', 'name')), request['job_name'], found,
                           self.settings.get('job_name_policy', 'exact')):
            raise ValueError('native_task_identity_mismatch:name')
        return found

    def normalize(self, task, request, uid=None):
        uid = self.identity(task, request, uid)
        native_phase = at(task, self.selector('task', 'status'))
        if not isinstance(native_phase, str): raise ValueError('native_phase_string_required')
        phase = self.settings['phase_map'].get(native_phase)
        if phase is None: raise ValueError('unmapped_native_task_phase')
        allocation = {}
        if self.settings.get('allocation_mode') == 'lpai-role-quote':
            from lpai_native_evidence import priced_allocation
            allocation = priced_allocation(task, self.arm(request).get('quote_ref'), self.clock(), request['declared_arm']['allocation']['currency'])
            gpus, price = allocation['gpus'], allocation['hourly_cost']
        else:
            gpus = at(task, self.selector('task', 'gpus')); price = at(task, self.selector('task', 'hourly_cost'))
        if type(gpus) is not int or gpus < 0 or not number(price): raise ValueError('native_total_task_allocation_required')
        if self.settings.get('release_mode') == 'task-pods-deleted':
            task = {**task, '_bmad_native_pod_readback': self.query(request, ['tasks', 'pods'], ['--uid', uid])}
        proof = self.hook('observe', request, task)
        if proof.get('uid') != uid or proof.get('request_key') != request['request_key']:
            raise ValueError('project_observation_identity_mismatch')
        if not number(proof.get('observed_at')) or not 0 <= self.clock() - proof['observed_at'] <= request['observation_max_age_seconds']:
            raise ValueError('fresh_project_observation_required')
        if type(proof.get('released')) is not bool: raise ValueError('explicit_allocation_release_readback_required')
        if proof['released'] and (phase not in TERMINAL or not proof.get('release_evidence_ref')):
            raise ValueError('release_evidence_required')
        result = dict(uid=uid, job_name=request['job_name'], request_key=request['request_key'],
                      phase=phase, gpus=gpus, hourly_cost=price, released=proof['released'])
        if allocation: result['allocation_provenance'] = allocation
        result['native_job_name'] = at(task, self.selector('task', 'name'))
        for key in ('health', 'release_evidence_ref'):
            if key in proof: result[key] = proof[key]
        return result

    def preflight(self, request):
        arm = self.arm(request)
        component = self.query(request, ['projects', 'get'], ['--name', arm['component_name']])
        if (at(component, self.selector('component', 'uid')) != arm['component_uid']
            or at(component, self.selector('component', 'namespace')) != self.settings['namespace']):
            raise ValueError('native_component_scope_mismatch')
        admission = self.hook('preflight', request, component)
        guard = admission.get('lease_guard', {})
        if (guard.get('deadline') != request['deadline'] or guard.get('scope') != 'allocation-release'
            or guard.get('independent') is not True or not guard.get('evidence_ref')):
            raise ValueError('verified_independent_allocation_release_guard_required')
        body, body_ref = load_ref(admission.get('body_ref'))
        if admission.get('body_binding') != {'request_key': request['request_key'], 'deadline': request['deadline'],
                                            'arm_digest': request['declared_arm_digest']}:
            raise ValueError('native_payload_plan_and_lease_binding_required')
        # Project preflight verifies all role GPU counts, source/config/mounts and actual body semantics.
        self.prepared[request['request_key']] = {'body_ref': body_ref, 'request_digest': request['request_digest']}
        return admission

    def lookup(self, request):
        self.arm(request); matches = []; seen = set(); total = None
        for page in range(1, self.settings['max_lookup_pages'] + 1):
            result = self.query(request, ['tasks', 'list'], ['--name-like', request['job_name'], '--owner', self.settings['owner'],
                                '--page-size', str(self.settings['page_size']), '--page-num', str(page)])
            items = at(result, self.selector('list', 'items')); count = at(result, self.selector('list', 'total'))
            if not isinstance(items, list) or type(count) is not int or count < 0: raise ValueError('complete_native_pagination_required')
            if total is not None and count != total: raise ValueError('native_pagination_changed')
            total = count
            for task in items:
                if self.settings.get('list_item_envelope') == 'data': task = {'status': 'ok', 'data': task}
                uid = at(task, self.selector('task', 'uid'))
                if not isinstance(uid, str) or not uid or uid in seen: raise ValueError('native_pagination_identity_ambiguous')
                seen.add(uid)
                from lpai_native_evidence import job_matches
                if job_matches(at(task, self.selector('task', 'name')), request['job_name'], uid,
                               self.settings.get('job_name_policy', 'exact')):
                    matches.append(self.normalize(task, request))
            if len(seen) == total: return {'complete': True, 'matches': matches}
            if len(seen) > total or not items: raise ValueError('native_pagination_incomplete')
        return {'complete': False, 'matches': matches}

    def submit(self, request):
        arm = self.arm(request); prepared = self.prepared.get(request['request_key'])
        if not prepared or prepared['request_digest'] != request['request_digest']: raise ValueError('native_preflight_for_exact_request_required')
        load_ref(prepared['body_ref'])
        info = arm.get('task_info', {})
        for key in ('problem_statement', 'solution_approach', 'expected_result', 'expected_duration_desc'):
            if not isinstance(info.get(key), str) or not info[key].strip(): raise ValueError('native_task_info_required:' + key)
        if type(info.get('expected_duration')) is not int or info['expected_duration'] <= 0: raise ValueError('native_expected_duration_minutes_required')
        argv = ['--uid', arm['component_uid'], '--name', request['job_name'], '--body-file', prepared['body_ref']['path'],
                '--describe', self.marker(request)]
        for key in ('problem_statement', 'solution_approach', 'expected_result', 'expected_duration', 'expected_duration_desc'):
            argv += ['--' + key.replace('_', '-'), str(info[key])]
        result = self.query(request, ['projects', 'start'], argv)
        if result.get('status') == 'pending_approval':
            return {'outcome': 'approval-pending', 'approval_id': result.get('approval_id'), 'task_created_verified': False}
        uid = at(result, self.selector('start', 'uid'))
        if not isinstance(uid, str) or not uid: raise ValueError('native_start_uid_required')
        return {'outcome': 'submitted', 'uid': uid, 'observation': self.observe(uid, request)}

    def observe(self, uid, request):
        self.arm(request)
        task = self.query(request, ['tasks', 'get'], ['--uid', uid])
        return self.normalize(task, request, uid)

    def stop(self, uid, request, reason):
        self.arm(request)
        task = self.query(request, ['tasks', 'get'], ['--uid', uid])
        self.identity(task, request, uid)  # Never mutate a UID whose current identity cannot be verified.
        result = self.query(request, ['tasks', 'stop'], ['--uid', uid, '--reason', 'BMAD paired: ' + reason])
        if result.get('status')=='pending_approval':
            return {'acknowledged':False,'release_verified':False,'approval_pending':True,'approval_id':result.get('approval_id')}
        return {'acknowledged': True, 'release_verified': False}


def create_adapter(settings): return LpaiAdapter(settings)


if __name__ == '__main__':
    # Internal bounded subprocess hook protocol; this entry point never dispatches platform jobs.
    if len(sys.argv) != 3 or sys.argv[1] != '--hook' or sys.argv[2] not in ('preflight', 'observe'):
        raise SystemExit(2)
    try:
        payload = json.load(sys.stdin)
        resolve({'project_driver': payload['binding']}, Path.cwd())
        sys.path.insert(0, str(Path(payload['binding']['path']).parent))
        module = bound_module(payload['binding'])
        hooks = module.create_hooks(payload['settings'])
        value = getattr(hooks, sys.argv[2])(*payload['args'])
        print(json.dumps(value, allow_nan=False))
    except Exception:
        print(json.dumps({'status': 'hook-failed'})); raise SystemExit(2)
