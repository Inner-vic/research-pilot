"""Official SDK/CLI transports and source-backed notebook API. No credential export."""
import contextlib
import io
import json
import subprocess
import sys
import uuid
from pathlib import Path

from submission_guard import PendingApproval



class ProviderResponseFailure(ValueError):
    def __init__(self,status):
        super().__init__('provider_error_response')
        self.status_code=status if type(status) is int and 100<=status<=599 else None

def payload(value):
    if isinstance(value, dict):
        if value.get('status') in ('pending_approval', 'approval-pending'):
            raise PendingApproval(value)
        if value.get('error') or value.get('code') not in (None, 0, 200):
            raise ProviderResponseFailure(value.get('code'))
        if value.get('status') == 'ok' and 'data' in value:
            return payload(value['data'])
    if value is None:
        raise ValueError('provider_response_missing')
    return value


class OfficialReadFailure(ValueError):
    """Safe structured read failure; no raw provider text or write retry."""
    def __init__(self,status,kind):
        self.status_code=status if type(status) is int and 100<=status<=599 else None
        self.read_only=True
        self.retryable=self.status_code in (408,429,500,502,503,504) or self.status_code is None and kind in {
            'ConnectionError','ConnectTimeout','ReadTimeout','Timeout','TimeoutError','TimeoutExpired','ConnectionResetError','RemoteDisconnected','ProtocolError'}
        reason=('official_sdk_read_authentication_required' if self.status_code in (401,403)
            else 'official_sdk_read_transient' if self.retryable else 'official_sdk_read_request_failed')
        super().__init__(reason)


class OfficialBackend:
    def __init__(self, config, worker_mode=False):
        self.config = config
        self.worker_mode = worker_mode
        self._project = self._task = None

    def sdk_call(self, operation, arguments):
        from review_storage import atomic_json
        directory = Path(self.config['state_root']).resolve() / 'sdk-transport'
        source = directory / (uuid.uuid4().hex + '.json')
        minimal = {key: self.config[key] for key in ('namespace', 'owner', 'state_root', 'official_cli', 'devspace_transport') if key in self.config}
        atomic_json(source, {'operation': operation, 'config': minimal, 'arguments': arguments})
        worker = Path(__file__).with_name('lpai_sdk_worker.py')
        receipt=source.with_suffix('.result.json')
        import hashlib
        if operation=='mutate':
            self.last_mutation_receipt={'path':str(receipt),'input':str(source),
                'input_sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
        try:
            result = subprocess.run([self.config.get('sdk_python', sys.executable), '-B', str(worker), '--input', str(source)],
                                    shell=False, capture_output=True, encoding='utf-8', errors='replace', timeout=60,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired:
            if operation=='mutate':raise ValueError('official_sdk_write_outcome_unknown') from None
            raise OfficialReadFailure(None,'TimeoutExpired') from None
        saved={}
        if receipt.exists():
            saved=json.loads(receipt.read_bytes())
            if saved.get('input_sha256')!=hashlib.sha256(source.read_bytes()).hexdigest():
                raise ValueError('official_sdk_outcome_binding_changed')
            if operation=='mutate':
                self.last_mutation_receipt['sha256']=hashlib.sha256(receipt.read_bytes()).hexdigest()
        try: value = json.loads(result.stdout)
        except ValueError:
            identity=saved.get('mutation_identity')
            uid=identity.get('uid') if isinstance(identity,dict) else identity if isinstance(identity,str) else None
            if operation=='mutate' and saved.get('status')=='ok' and uid:
                return identity
            if operation!='mutate':
                raise OfficialReadFailure(saved.get('provider_status_code'),saved.get('error') or 'ProtocolError') from None
            raise ValueError('official_sdk_write_outcome_unknown') from None
        if result.returncode:
            if operation in {'read','create-candidates','task-candidates','owned-task-page','validate-task-data','compile-component'}:
                failure=saved if saved.get('status')=='error' else value if isinstance(value,dict) else {}
                raise OfficialReadFailure(failure.get('provider_status_code'),failure.get('error'))
            raise ValueError('official_sdk_worker_request_failed')
        return payload(value)

    def mutation_receipt(self,request,plan_path):
        """Recover only an exact durable SDK response; never repeat a write."""
        import hashlib
        directory=Path(self.config['state_root']).resolve()/'sdk-transport'
        matches=[]
        for source in directory.glob('*.json'):
            if source.name.endswith('.result.json'):continue
            spec=json.loads(source.read_bytes())
            arguments=spec.get('arguments',{})
            if (spec.get('operation')=='mutate' and arguments.get('request')==request
                    and Path(arguments.get('plan_path','')).resolve()==Path(plan_path).resolve()):
                matches.append(source)
        if len(matches)>1:raise ValueError('multiple_sdk_write_envelopes_reconcile_only')
        if not matches:return None
        source=matches[0];receipt=source.with_suffix('.result.json')
        if not receipt.exists():return None
        saved=json.loads(receipt.read_bytes())
        if saved.get('input_sha256')!=hashlib.sha256(source.read_bytes()).hexdigest():
            raise ValueError('official_sdk_outcome_binding_changed')
        identity=saved.get('mutation_identity')
        uid=identity.get('uid') if isinstance(identity,dict) else identity if isinstance(identity,str) else None
        return {'uid':uid if saved.get('status')=='ok' else None,'status':saved['status'],
            'reference':{'path':str(receipt),'sha256':hashlib.sha256(receipt.read_bytes()).hexdigest(),
                'input':str(source),'input_sha256':saved['input_sha256']}}

    def sdk(self):
        if self._project is None:
            from lpai.conf.lipai_conf import LpaiConf
            from lpai.pipelinev2.project import Project
            from lpai.pipelinev2.task import Task
            conf = LpaiConf()
            if conf.get_env() != 'prod' or conf.get_namespace() != self.config['namespace']:
                raise ValueError('official_sdk_scope_configuration_mismatch')
            self._project, self._task = Project(conf), Task(conf)
        return self._project, self._task

    def cli(self, args):
        result = subprocess.run([self.config['official_cli'], *args], shell=False,
                                capture_output=True, encoding='utf-8', errors='replace', timeout=60,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        # Never persist SDK/CLI error text: it can include auth diagnostics.
        try:
            value = json.loads(result.stdout)
        except ValueError:
            raise ValueError('official_cli_unstructured_response') from None
        if result.returncode:
            raise ValueError('official_cli_request_failed')
        return payload(value)

    def read(self, kind, uid=None, name=None):
        if self.config.get('component_transport') == 'official-cli' and kind in ('component', 'task'):
            if kind == 'component':
                component_name = self.config.get('component_names', {}).get(uid) or name
                if not component_name:
                    raise ValueError('official_cli_component_name_binding_required')
                return self.cli(['projects', 'get', '--namespace', self.config['namespace'], '--name', component_name])
            return self.cli(['projects' if kind == 'component' else 'tasks', 'get',
                             '--namespace', self.config['namespace'], '--uid', uid])
        api_space = kind == 'devspace' and self.config.get('devspace_transport') == 'official-api'
        if (kind != 'devspace' or api_space) and not self.worker_mode and self._project is None:
            return self.sdk_call('read', {'kind': kind, 'uid': uid, 'name': name})
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            if kind == 'devspace':
                if api_space:
                    client, path = self.notebook_api(name)
                    return payload(client.get(path, timeout=30))
                return self.cli(['devspaces', 'get', '--namespace', self.config['namespace'], '--name', name])
            project, task = self.sdk()
            return payload((project if kind == 'component' else task).get(uid))

    def notebook_api(self, name):
        """Official frontend module 58007: GET/PATCH namespaces/{ns}/notebooks/{name}.

        Authentication uses the installed SDK's normal LpaiJwt credential path.
        No browser token/cookie or CLI agent approval token is read or changed.
        """
        import re
        if not all(re.fullmatch(r'[a-z0-9][a-z0-9-]*', v or '') for v in (name, self.config['namespace'])):
            raise ValueError('notebook_api_identity_invalid')
        self.sdk()  # Existing SDK validates production namespace.
        from lpai.client.rest_client import RestClient
        from lpai.conf.lipai_conf import LpaiConf
        client = RestClient('https://lpai.lixiang.com', {'Content-Type': 'application/json'}, LpaiConf())
        return client, '/lpai/api/kubeflow/namespaces/' + self.config['namespace'] + '/notebooks/' + name

    def create_candidates(self, name):
        if self.config.get('component_transport') == 'official-cli':
            value = self.cli(['projects', 'list', '--namespace', self.config['namespace'],
                              '--name', name, '--owner', self.config['owner'],
                              '--page-size', '100', '--page-num', '1'])
            if (not isinstance(value, dict) or type(value.get('total')) is not int
                    or not isinstance(value.get('items'), (list, type(None)))):
                raise ValueError('complete_component_lookup_required')
            items = value['items'] or []
            if value['total'] != len(items):
                raise ValueError('component_lookup_pagination_incomplete')
            if any(row.get('namespace') != self.config['namespace'] or row.get('owner') != self.config['owner']
                   or row.get('name') != name for row in items):
                raise ValueError('component_lookup_scope_mismatch')
            return items
        if not self.worker_mode and self._project is None:
            return self.sdk_call('create-candidates', {'name': name})
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            project, _ = self.sdk()
            value = payload(project.list(name=name, owner=self.config['owner'], page_size=100, page_num=1))
        if isinstance(value, dict) and type(value.get('total')) is int and value['total'] == 0 and value.get('items') is None:
            value = {**value, 'items': []}
        if not isinstance(value, dict) or type(value.get('total')) is not int or not isinstance(value.get('items'), list):
            raise ValueError('complete_component_lookup_required')
        if value['total'] != len(value['items']):
            raise ValueError('component_lookup_pagination_incomplete')
        return value['items']

    def owned_task_page(self, page_num, page_size=100):
        """Complete native task data from the selected official read transport."""
        if type(page_num) is not int or not 1<=page_num<=100 or page_size!=100:
            raise ValueError('bounded_owned_task_page_required')
        transport=self.config.get('component_transport','official-sdk')
        if transport=='official-cli':
            return self.cli(['tasks','list','--namespace',self.config['namespace'],
                '--owner',self.config['owner'],'--page-size',str(page_size),'--page-num',str(page_num)])
        if transport!='official-sdk':raise ValueError('explicit_official_task_inventory_transport_required')
        if not self.worker_mode and self._project is None:
            return self.sdk_call('owned-task-page',{'page_num':page_num,'page_size':page_size})
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            _,task=self.sdk()
            return payload(task.list(owner=self.config['owner'],page_size=page_size,page_num=page_num,
                select_field='uid,name,namespace,owner,project_uid,status,data,update_timestamp'))

    def task_candidates(self, name, component_uid):
        """Read-only recovery of an approved start whose task UID was not returned."""
        transport = self.config.get('component_transport', 'official-sdk')
        if transport == 'official-sdk':
            if not self.worker_mode and self._project is None:
                return self.sdk_call('task-candidates', {'name': name, 'component_uid': component_uid})
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                _, task = self.sdk()
                value = payload(task.list(owner=self.config['owner'], page_size=100, page_num=1))
        elif transport == 'official-cli':
            value = self.cli(['tasks', 'list', '--namespace', self.config['namespace'],
                              '--owner', self.config['owner'], '--name-like', name,
                              '--page-size', '100', '--page-num', '1'])
        else:
            raise ValueError('explicit_official_task_discovery_transport_required')
        if not isinstance(value, dict) or type(value.get('total')) is not int or not isinstance(value.get('items'), (list, type(None))):
            raise ValueError('complete_task_lookup_required')
        items = value['items'] or []
        if len(items) != value['total']:
            raise ValueError('task_lookup_pagination_incomplete')
        from lpai_manager import task_name_matches
        selected = []
        for row in items:
            if row.get('namespace') != self.config['namespace'] or row.get('owner') != self.config['owner']:
                raise ValueError('task_lookup_scope_mismatch')
            if task_name_matches(name, row.get('name'), row.get('uid')) and row.get('project_uid') == component_uid:
                selected.append(row)
        return selected

    def compile_component(self, configuration):
        if not self.worker_mode and self._project is None:
            return self.sdk_call('compile-component', {'configuration': configuration})
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.sdk()  # Validate the active production namespace first.
            from lpai.pipelinev2.builder.payload_builder import PayloadBuilder
            builder = PayloadBuilder()
            result = (builder.with_cluster(configuration['cluster']).with_queue_code(configuration)
                      .with_name(configuration).with_describe(configuration).with_code(configuration)
                      .with_envs(configuration).with_retry(configuration).with_framework(configuration)
                      .with_resource(configuration).with_wait(configuration).with_elastic_train_info(configuration)
                      .with_input_model(configuration).with_input_data_volume(configuration).with_input_dataset(configuration)
                      .with_register_model(configuration).with_output_data_volume(configuration).with_gems_project(configuration).build())
        expected = {str(k): str(v) for k,v in configuration.get('envs', {}).items()}
        actual = {v['key']: v['value'] for v in result['data'].get('lpai_envs', [])}
        if expected != actual:
            raise ValueError('installed_sdk_changes_environment_keys')
        return {**result, 'namespace': self.config['namespace']}

    def validate_task_data(self,data):
        if not self.worker_mode:
            return self.sdk_call('validate-task-data',{'data':data})
        from lpai.pipelinev2.task import Task
        Task.validate_data(None,data)
        import inspect,hashlib
        return {'status':'valid','installed_sdk_source_sha256':hashlib.sha256(Path(inspect.getfile(Task)).read_bytes()).hexdigest(),
            'network_calls':0,'platform_writes':0}

    def mutate(self, request, plan_path):
        action = request['action']
        ns = self.config['namespace']
        if self.config.get('component_transport') == 'official-cli':
            if request.get('transport') == 'official-sdk':
                raise ValueError('explicit_cli_mode_forbids_sdk_fallback')
            if action == 'task.submit':
                from review_storage import atomic_json
                body = Path(plan_path).with_suffix('.native-task-body.json')
                atomic_json(body, {'name': request['name'], 'namespace': ns,
                                  'project_uid': request['uid'], 'cluster': request['cluster'],
                                  **request['component_settings'], 'data': request['data']})
                args = ['projects', 'start', '--namespace', ns, '--uid', request['uid'],
                        '--name', request['name'], '--cluster', request['cluster'], '--body-file', str(body)]
                for key in ('problem_statement', 'solution_approach', 'expected_result',
                            'expected_duration', 'expected_duration_desc'):
                    args.extend(['--' + key.replace('_', '-'), str(request['task_info'][key])])
                return self.cli(args)
            if action == 'task.stop':
                return self.cli(['tasks', 'stop', '--namespace', ns, '--uid', request['uid']])
        sdk_create = action == 'component.create' and request.get('transport') == 'official-sdk'
        api_space = action.startswith('devspace.') and self.config.get('devspace_transport') == 'official-api'
        if (action.startswith('task.') or sdk_create or api_space) and not self.worker_mode and self._project is None:
            return self.sdk_call('mutate', {'request': request, 'plan_path': str(plan_path)})
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            if action == 'task.submit':
                _, task = self.sdk()
                # Public full-data submit avoids the installed Project builder's
                # env-key capitalize() transform. No installed SDK file is patched.
                return payload(task.submit(cluster=request['cluster'], name=request['name'],
                                           project_uid=request['uid'], data=request['data'],
                                           **request.get('component_settings', {}), **request['task_info']))
            if action == 'task.stop':
                _, task = self.sdk()
                return payload(task.update(request['uid'], status='stop'))
            if action.startswith('devspace.'):
                if api_space:
                    client, path = self.notebook_api(request['name'])
                    return payload(client.patch(path, {'status': action.split('.')[1]}))
                return self.cli(['devspaces', action.split('.')[1], '--namespace', ns, '--name', request['name']])
            if action == 'component.create':
                if sdk_create:
                    project, _ = self.sdk()
                    configuration = request['sdk_config']
                    if self.compile_component(configuration) != request['body']:
                        raise ValueError('sdk_configuration_compilation_changed')
                    path = Path(plan_path).with_suffix('.sdk-config.json')
                    from review_storage import atomic_json
                    atomic_json(path, configuration)
                    return payload(project.create(configuration['cluster'], str(path)))
                # Native CLI body keeps exact env keys; do not route via the
                # installed SDK Project.create builder with known case mutation.
                body = Path(plan_path).with_suffix('.native-body.json')
                from review_storage import atomic_json
                atomic_json(body, request['body'])
                return self.cli(['projects', 'create', '--namespace', ns, '--name', request['name'],
                                 '--framework-type', request['body']['framework_type'],
                                 '--cluster', request['body']['cluster'], '--queue-code', request['body']['queue_code'],
                                 '--permission', request['body'].get('permission', 'user'), '--body-file', str(body)])
        raise ValueError('unsupported_management_operation')
