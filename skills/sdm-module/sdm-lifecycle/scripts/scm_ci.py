"""GitLab official CLI and Jenkins Remote API adapter for existing SDM transactions.

No merges, approvals, deployment, credential discovery or automatic write retries.
A CI terminal result is engineering evidence, never ML quality acceptance or S13.
"""
import argparse
import base64
import hashlib
import http.cookiejar
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from review_storage import atomic_json, locked

GITLAB_DOC = 'https://docs.gitlab.com/api/'
JENKINS_DOC = 'https://www.jenkins.io/doc/book/using/remote-access-api/'
TERMINAL = {'success', 'failed', 'canceled', 'skipped', 'SUCCESS', 'FAILURE', 'ABORTED', 'UNSTABLE', 'NOT_BUILT', 'cancelled'}
SHA = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')


def digest(value):
    return 'sha256:' + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def part(value):
    if not isinstance(value, str) or not value or value in {'.', '..'} or any(c in value for c in '\\%?#\r\n\x00'):
        raise ValueError('invalid_remote_path')
    return urllib.parse.quote(value, safe='')


def relative(value):
    if not isinstance(value, str) or value.startswith('/'):
        raise ValueError('relative_artifact_path_required')
    return '/'.join(part(p) for p in value.split('/'))


def number(value):
    if not re.fullmatch(r'[1-9][0-9]*', str(value)):
        raise ValueError('positive_remote_identity_required')
    return int(value)


class ApiError(ValueError):
    def __init__(self, category, code=None):
        self.category, self.code = category, code
        super().__init__(category + (':' + str(code) if code else ''))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Transport:
    def __init__(self, config):
        self.c = config
        self.cookies = http.cookiejar.CookieJar()
        self.http = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPCookieProcessor(self.cookies))

    def request(self, method, endpoint, body=None, headers=None, binary=False):
        c = self.c
        limit = c.get('max_response_bytes', 8 * 1024 * 1024)
        if c['provider'] == 'gitlab':
            argv = ['glab', 'api', endpoint, '--hostname', urllib.parse.urlsplit(c['base_url']).netloc, '--method', method]
            if binary:argv += ['--include']
            for k, v in (headers or {}).items():
                argv += ['--header', k + ': ' + str(v)]
            if body is not None:
                argv += ['--input', '-']
            with tempfile.TemporaryFile() as stream:
                result = subprocess.run(argv, input=json.dumps(body).encode() if body is not None else None,
                    stdout=stream, stderr=subprocess.PIPE, timeout=c.get('request_timeout_seconds', 30),
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                if result.returncode:
                    error = result.stderr.decode(errors='replace')
                    code = next((v for v in (401, 403, 404, 409, 429, 500, 502, 503, 504) if str(v) in error), None)
                    raise ApiError('authentication' if code == 401 else 'permission' if code == 403 else 'http' if code else 'transport', code)
                if stream.tell() > limit:
                    raise ApiError('response_too_large')
                stream.seek(0); raw = stream.read()
            if binary:
                split = b'\r\n\r\n' if b'\r\n\r\n' in raw else b'\n\n'
                head, sep, raw_body = raw.partition(split)
                if not sep or not head.startswith(b'HTTP/'):raise ApiError('cli_response_headers_required')
                response_headers = dict(line.decode('latin1').split(':', 1) for line in head.splitlines()[1:] if b':' in line)
                return raw_body, {k: v.strip() for k, v in response_headers.items()}
            return json.loads(raw)
        if endpoint.startswith('/') or '..' in endpoint.split('/') or urllib.parse.urlsplit(endpoint).scheme:
            raise ValueError('relative_api_endpoint_required')
        user = os.environ.get(c.get('user_env', 'JENKINS_USER'))
        token = os.environ.get(c.get('token_env', 'JENKINS_API_TOKEN'))
        if not user or not token:
            raise ApiError('jenkins_credentials_required')
        auth = base64.b64encode((user + ':' + token).encode()).decode()
        h = {'Authorization': 'Basic ' + auth, **(headers or {})}
        if body is not None:
            h['Content-Type'] = 'application/x-www-form-urlencoded'
        req = urllib.request.Request(c['base_url'].rstrip('/') + '/' + endpoint,
            data=urllib.parse.urlencode(body).encode() if body is not None else None, headers=h, method=method)
        try:
            response = self.http.open(req, timeout=c.get('request_timeout_seconds', 30))
        except urllib.error.HTTPError as exc:
            if method == 'POST' and exc.code == 302:
                response = exc  # Observe Location; never follow a credential-bearing redirect.
            else:
                raise ApiError('authentication' if exc.code == 401 else 'permission' if exc.code == 403 else 'http', exc.code) from None
        except (urllib.error.URLError, TimeoutError):
            raise ApiError('transport') from None
        with response:
            raw = response.read(limit + 1); h = dict(response.headers)
            if len(raw) > limit:
                raise ApiError('response_too_large')
            if method == 'POST':
                return {'http_status': response.code, 'location': h.get('Location')}
            return (raw, h) if binary else json.loads(raw)


class Adapter:
    def __init__(self, config, transport=None):
        self.c = config
        if config.get('schema') != 'sdm-scm-ci/v1' or config.get('provider') not in {'gitlab', 'jenkins'}:
            raise ValueError('scm_ci_configuration_required')
        u = urllib.parse.urlsplit(config['base_url'])
        if u.scheme != 'https' or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError('credential_free_https_base_required')
        if config['provider'] == 'gitlab' and u.path not in {'', '/'}:
            raise ValueError('gitlab_host_root_required')
        if not 1 <= config.get('max_pages', 10) <= 100 or not 1 <= config.get('request_timeout_seconds', 30) <= 300:
            raise ValueError('bounded_api_scan_and_timeout_required')
        if not config.get('namespace') or not config.get('owner'):
            raise ValueError('namespace_and_expected_actor_required')
        writes = config.get('allowed_writes', [])
        if not isinstance(writes, list) or any(v not in {'branch','tag','merge-request','pipeline','build'} for v in writes):
            raise ValueError('explicit_supported_write_kinds_required')
        if config.get('request_key') is not None:
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', config['request_key']) or not re.fullmatch(r'sha256:[0-9a-f]{64}', config.get('configuration_digest', '')):
                raise ValueError('bound_ci_request_and_configuration_required')
        self.kind = config.get('resource', {}).get('kind', 'snapshot' if config['provider'] == 'gitlab' else 'build')
        if config['provider'] == 'gitlab':
            self.root = 'projects/' + part(str(config['project']))
            if self.kind not in {'snapshot', 'pipeline', 'job', 'merge-request', 'branch', 'tag'}:
                raise ValueError('unsupported_gitlab_resource')
        else:
            self.root = '/'.join('job/' + part(p) for p in config['job'].split('/'))
            if self.kind != 'build':
                raise ValueError('jenkins_build_resource_required')
        self.api = transport or Transport(config)

    def get(self, endpoint, **kwargs):
        return self.api.request('GET', endpoint, **kwargs)

    def revision(self):
        rev = self.c['resource'].get('revision')
        if not isinstance(rev, str) or not SHA.fullmatch(rev):
            raise ValueError('exact_source_revision_required')
        return rev

    def identity(self):
        if self.c['provider'] == 'gitlab':
            actor = self.get('user')
            if actor.get('username') != self.c['owner']:
                raise ValueError('gitlab_actor_mismatch')
            project = self.get(self.root)
            if project.get('path_with_namespace') != self.c['namespace'] or str(project.get('id')) != str(self.c['project_id']):
                raise ValueError('gitlab_project_mismatch')
            return {'actor': actor['username'], 'project_id': project['id']}
        actor = self.get('whoAmI/api/json')
        if actor.get('anonymous') or actor.get('name') != self.c['owner']:
            raise ValueError('jenkins_actor_mismatch')
        job = self.get(self.root + '/api/json?' + urllib.parse.urlencode({'tree': 'name,url,buildable,property[parameterDefinitions[name,type]]'}))
        if job.get('url', '').rstrip('/') != (self.c['base_url'].rstrip('/') + '/' + self.root):
            raise ValueError('jenkins_job_mismatch')
        return {'actor': actor['name'], 'job': self.c['job'], 'buildable': job.get('buildable'),
                'parameters': [p['name'] for prop in job.get('property', []) for p in prop.get('parameterDefinitions', [])]}

    def probe(self):
        facts = self.identity()
        operations = ['read']
        if self.kind in self.c.get('allowed_writes', []) and self.kind not in {'snapshot', 'job'}:
            self.revision()
            if self.c['provider'] == 'jenkins':
                if not facts['buildable'] or not set(self.params()) <= set(facts['parameters']):
                    raise ValueError('jenkins_declared_parameters_required')
            operations += ['lookup', 'submit', 'read-new']
        version = 'glab/' + subprocess.run(['glab', '--version'], capture_output=True, text=True, timeout=10).stdout.splitlines()[0] if self.c['provider'] == 'gitlab' else 'jenkins-remote-api/v1'
        return {'available': True, 'operations': operations, 'source_ref': GITLAB_DOC if self.c['provider'] == 'gitlab' else JENKINS_DOC,
                'version': version, 'identity': facts, 's13_emitted': False}

    def stamp(self, uid, **data):
        return {'provider': self.c['provider'], 'namespace': self.c['namespace'], 'owner': self.c['owner'],
                'component_uid': self.component(), 'uid': str(uid), 'observed_at': time.time(), **data}

    def component(self):
        return str(self.c['project']) if self.c['provider'] == 'gitlab' else self.c['job']

    def params(self):
        out = dict(self.c.get('parameters', {}))
        reserved = {'SDM_REQUEST_KEY': self.c['request_key'], 'SDM_CONFIGURATION_DIGEST': self.c['configuration_digest'], 'SDM_REVISION': self.revision()}
        if any(k in out for k in reserved) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in out.items()):
            raise ValueError('reserved_or_nonstring_ci_parameter')
        return {**out, **reserved}

    def correlation(self, parameters):
        values = {p['name'] if 'name' in p else p['key']: p.get('value') for p in parameters}
        if values.get('SDM_REQUEST_KEY') != self.c.get('request_key'):
            return False
        if any(values.get(k) != v for k, v in self.params().items()):
            raise ValueError('original_request_configuration_mismatch')
        return True

    def pages(self, endpoint):
        """A bounded incomplete scan cannot authorize another submission."""
        rows = []
        for page in range(1, self.c.get('max_pages', 10) + 1):
            result = self.get(endpoint + ('&' if '?' in endpoint else '?') + urllib.parse.urlencode({'per_page': 100, 'page': page}))
            if not isinstance(result, list):
                raise ValueError('paged_api_list_required')
            rows += result
            if len(result) < 100:
                return rows
        raise ValueError('lookup_incomplete_no_resubmit')

    def read(self, uid=None):
        r = self.c['resource']
        uid = uid or str(r.get('id') or r.get('ref') or r.get('revision') or '')
        if self.c['provider'] == 'jenkins':
            return self.build(uid)
        if self.kind == 'snapshot':
            v = self.get(self.root + '/repository/commits/' + self.revision())
            if v.get('id') != self.revision():
                raise ValueError('source_revision_mismatch')
            if r.get('ref'):
                branch = self.get(self.root + '/repository/branches/' + part(r['ref']))
                if branch.get('commit', {}).get('id') != v['id']:raise ValueError('source_branch_moved')
            return self.stamp(v['id'], status='snapshot', revision=v['id'], source_revision_verified=True, ref=r.get('ref'))
        if self.kind in {'branch', 'tag'}:
            group = 'branches' if self.kind == 'branch' else 'tags'
            v = self.get(self.root + '/repository/' + group + '/' + part(uid))
            actual = v.get('commit', {}).get('id')
            if actual != self.revision():
                raise ValueError('source_revision_mismatch')
            return self.stamp(uid, status='exists', revision=actual, source_revision_verified=True)
        id_ = number(uid)
        if self.kind == 'merge-request':
            v = self.get(self.root + '/merge_requests/' + str(id_))
            if v.get('sha') != self.revision():
                raise ValueError('merge_request_revision_changed')
            approvals = self.get(self.root + '/merge_requests/' + str(id_) + '/approvals')
            return self.stamp(uid, status=v['state'], revision=v['sha'], source_revision_verified=True,
                approved=approvals.get('approved'), approvals_left=approvals.get('approvals_left'), pipeline_id=(v.get('head_pipeline') or {}).get('id'),
                merge_status=v.get('detailed_merge_status', v.get('merge_status')), merged_commit=v.get('merge_commit_sha'),
                request_key=self.c.get('request_key') if self.marker() in (v.get('description') or '') else None)
        v = self.get(self.root + ('/pipelines/' if self.kind == 'pipeline' else '/jobs/') + str(id_))
        actual = v.get('sha') if self.kind == 'pipeline' else v.get('commit', {}).get('id')
        if actual != self.revision():
            raise ValueError('ci_source_revision_mismatch')
        if self.kind == 'job' and r.get('pipeline_id') is not None and v.get('pipeline', {}).get('id') != r['pipeline_id']:
            raise ValueError('job_pipeline_identity_mismatch')
        key = None
        if self.kind == 'pipeline' and self.c.get('request_key'):
            if not self.correlation(self.get(self.root + '/pipelines/' + str(id_) + '/variables')):
                raise ValueError('pipeline_request_identity_mismatch')
            key = self.c['request_key']
        return self.stamp(uid, status=v['status'], revision=actual, source_revision_verified=True, request_key=key,
            terminal=v['status'] in TERMINAL, successful=v['status'] == 'success', web_url=v.get('web_url'))

    def marker(self):
        return '<!-- SDM ' + self.c.get('request_key', '') + ' ' + self.c.get('configuration_digest', '') + ' -->'

    def builds(self):
        fields = 'number,queueId,building,result,actions[parameters[name,value],lastBuiltRevision[SHA1]]'
        out = []
        for page in range(self.c.get('max_pages', 10)):
            tree = 'allBuilds[' + fields + ']{' + str(page * 100) + ',' + str((page + 1) * 100) + '}'
            rows = self.get(self.root + '/api/json?' + urllib.parse.urlencode({'tree': tree})).get('allBuilds')
            if not isinstance(rows, list):
                raise ValueError('complete_jenkins_build_list_required')
            out += rows
            if len(rows) < 100:
                return out
        raise ValueError('lookup_incomplete_no_resubmit')

    def build(self, uid):
        match = re.fullmatch(r'(queue|build):([1-9][0-9]*)', str(uid))
        if not match:
            raise ValueError('exact_queue_or_build_identity_required')
        kind, value = match.groups()
        if kind == 'queue':
            try:
                v = self.get('queue/item/' + value + '/api/json?' + urllib.parse.urlencode({'tree': 'id,cancelled,why,task[url],executable[number],actions[parameters[name,value]]'}))
            except ApiError as error:
                if error.code != 404:
                    raise
                found = [b for b in self.builds() if b.get('queueId') == int(value)]
                if len(found) != 1:
                    raise ValueError('queue_expired_build_unresolved_no_resubmit')
                v = {'executable': {'number': found[0]['number']}, 'actions': found[0].get('actions', [])}
            else:
                if v.get('task', {}).get('url', '').rstrip('/') != self.c['base_url'].rstrip('/') + '/' + self.root:
                    raise ValueError('queue_job_mismatch')
            if not v.get('executable'):
                if not self.correlation([p for a in v.get('actions', []) for p in a.get('parameters', [])]):
                    raise ValueError('queue_request_mismatch')
                return self.stamp(uid, status='cancelled' if v.get('cancelled') else 'queued',
                    terminal=bool(v.get('cancelled')), successful=False, source_revision_verified=False,
                    request_key=self.c['request_key'], why=v.get('why'))
            build = number(v['executable']['number'])
        else:
            build = number(value)
        v = self.get(self.root + '/' + str(build) + '/api/json?' + urllib.parse.urlencode({'tree': 'number,queueId,building,result,actions[parameters[name,value],lastBuiltRevision[SHA1],remoteUrls]'}))
        if kind == 'queue' and v.get('queueId') != int(value):
            raise ValueError('queue_build_identity_mismatch')
        if not self.correlation([p for a in v.get('actions', []) for p in a.get('parameters', [])]):
            raise ValueError('build_request_identity_mismatch')
        revisions = [a['lastBuiltRevision'].get('SHA1') for a in v.get('actions', []) if a.get('lastBuiltRevision')]
        if self.c.get('source_repository'):
            revisions = [a['lastBuiltRevision'].get('SHA1') for a in v.get('actions', []) if a.get('lastBuiltRevision') and self.c['source_repository'] in a.get('remoteUrls', [])]
        verified = bool(revisions) and set(revisions) == {self.revision()}
        return self.stamp(uid, status='running' if v.get('building') else v.get('result', 'unknown'),
            terminal=not v.get('building') and v.get('result') in TERMINAL, successful=v.get('result') == 'SUCCESS',
            source_revision_verified=verified, revision=self.revision() if verified else None,
            build_number=build, queue_id=v.get('queueId'), request_key=self.c['request_key'])

    def lookup(self):
        self.revision(); found = []
        if self.c['provider'] == 'jenkins':
            items = self.get('queue/api/json?' + urllib.parse.urlencode({'tree': 'items[id,task[url],actions[parameters[name,value]]]'})).get('items')
            if not isinstance(items, list):
                raise ValueError('complete_queue_list_required')
            for v in items:
                if v.get('task', {}).get('url', '').rstrip('/') == self.c['base_url'].rstrip('/') + '/' + self.root and self.correlation([p for a in v.get('actions', []) for p in a.get('parameters', [])]):
                    found.append(self.read('queue:' + str(number(v['id']))))
            for v in self.builds():
                if self.correlation([p for a in v.get('actions', []) for p in a.get('parameters', [])]):
                    key = 'queue:' + str(v['queueId']) if v.get('queueId', 0) > 0 else 'build:' + str(v['number'])
                    if key not in {f['uid'] for f in found}:
                        found.append(self.read(key))
        elif self.kind in {'branch', 'tag'}:
            try:found = [self.read(self.c['resource']['ref'])]
            except ApiError as error:
                if error.code != 404:raise
        elif self.kind == 'merge-request':
            q = urllib.parse.urlencode({'state': 'all', 'source_branch': self.c['resource']['ref'], 'target_branch': self.c['resource']['target']})
            for v in self.pages(self.root + '/merge_requests?' + q):
                if self.marker() in (v.get('description') or ''):
                    found.append(self.read(str(v['iid'])))
        elif self.kind == 'pipeline':
            for v in self.pages(self.root + '/pipelines?' + urllib.parse.urlencode({'ref': self.c['resource']['ref']})):
                if self.correlation(self.get(self.root + '/pipelines/' + str(number(v['id'])) + '/variables')):
                    found.append(self.read(str(v['id'])))
        else:
            raise ValueError('resource_not_submittable')
        for v in found:v['request_key'] = self.c['request_key']
        return {'complete': True, 'matches': found, 'observed_at': time.time()}

    def submit(self, spec_path, state_path):
        """Only existing Operation.write may invoke this; no standalone unsafe POST."""
        from external_operations import Operation
        spec = json.loads(Path(spec_path).read_bytes())
        op = Operation(spec, state_path, 60); op.authorization()
        if 'submit' not in op.state.get('intents', []) or 'submit' in op.state.get('responses', {}):
            raise ValueError('durable_original_submit_intent_required')
        for k, expected in [('provider', self.c['provider']), ('namespace', self.c['namespace']), ('owner', self.c['owner']),
                            ('request_key', self.c['request_key']), ('component_uid', self.component()), ('configuration_digest', self.c['configuration_digest'])]:
            if spec.get(k) != expected:raise ValueError('transaction_configuration_mismatch:' + k)
        if self.kind not in self.c.get('allowed_writes', []):raise ValueError('write_not_in_project_scope')
        self.identity()
        r = self.c['resource']
        if self.c['provider'] == 'jenkins':
            headers = {}
            declared = self.identity()
            if not declared['buildable'] or not set(self.params()) <= set(declared['parameters']):raise ValueError('jenkins_declared_parameters_required')
            if self.c.get('use_crumb'):
                crumb = self.get('crumbIssuer/api/json'); field = crumb['crumbRequestField']
                if not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]*', field) or field.lower() in {'authorization', 'cookie', 'host'}:
                    raise ValueError('invalid_crumb_header')
                headers[field] = crumb['crumb']
            v = self.post_once(spec, state_path, self.root + '/buildWithParameters', self.params(), headers=headers)
            location = urllib.parse.urljoin(self.c['base_url'].rstrip('/') + '/', v.get('location') or '')
            prefix = self.c['base_url'].rstrip('/') + '/queue/item/'
            if not location.startswith(prefix) or not re.fullmatch(r'[1-9][0-9]*/?', location[len(prefix):]):
                raise ValueError('submission_queue_location_unknown_no_retry')
            return {'uid': 'queue:' + location[len(prefix):].strip('/')}
        revision = self.revision()
        source = self.get(self.root + '/repository/branches/' + part(r['ref'])) if self.kind in {'pipeline', 'merge-request'} else None
        if source and source.get('commit', {}).get('id') != revision:raise ValueError('source_branch_moved')
        if self.kind == 'pipeline':
            data = {'ref': r['ref'], 'variables': [{'key': k, 'value': v} for k, v in self.params().items()]}
            endpoint = '/pipeline'
        elif self.kind == 'merge-request':
            target = self.get(self.root + '/repository/branches/' + part(r['target']))
            if not SHA.fullmatch(r.get('target_revision', '')) or target.get('commit', {}).get('id') != r['target_revision']:
                raise ValueError('target_branch_moved')
            data = {'source_branch': r['ref'], 'target_branch': r['target'], 'title': r['title'], 'description': r.get('description', '') + '\n' + self.marker(), 'remove_source_branch': False}
            endpoint = '/merge_requests'
        elif self.kind in {'branch', 'tag'}:
            data = {'branch' if self.kind == 'branch' else 'tag_name': r['ref'], 'ref': revision}
            endpoint = '/repository/' + ('branches' if self.kind == 'branch' else 'tags')
        else:raise ValueError('resource_not_submittable')
        v = self.post_once(spec, state_path, self.root + endpoint, data)
        return {'uid': str(v['iid'] if self.kind == 'merge-request' else v['name'] if self.kind in {'branch', 'tag'} else v['id'])}

    def post_once(self, spec, state_path, endpoint, body, headers=None):
        from submission_guard import Guard
        guard = Guard(spec, state_path); guard.load()
        if guard.state.get('phase') != 'intent' or guard.state.get('state_path') != str(Path(state_path).resolve()):
            raise ValueError('original_guard_intent_required')
        marker = guard.root / (guard.key + '.adapter-write.json')
        with locked(guard.root, guard.key + '.adapter-write.lock', 'adapter_write_busy'):
            if marker.exists():raise ValueError('adapter_write_already_attempted_reconcile_original')
            atomic_json(marker, {'phase': 'intent', 'request_key': self.c['request_key'], 'config_digest': digest(self.c), 'at': time.time()})
            response = self.api.request('POST', endpoint, body, headers=headers)
            atomic_json(marker, {'phase': 'response-received', 'request_key': self.c['request_key'], 'config_digest': digest(self.c), 'at': time.time()})
            return response

    def log(self, uid, offset=0):
        if offset < 0:raise ValueError('negative_log_offset')
        observed = self.read(uid)
        if self.c['provider'] == 'gitlab':
            if self.kind != 'job':raise ValueError('exact_job_required_for_log')
            raw, headers = self.get(self.root + '/jobs/' + str(number(uid)) + '/trace', headers={'Range': 'bytes=' + str(offset) + '-' + str(offset + 65535)}, binary=True)
            # A server ignoring Range must not turn an offset into invented progress.
            if offset and not any(k.lower() == 'content-range' for k in headers):
                raise ValueError('log_range_not_supported')
            next_offset = offset + len(raw)
        else:
            raw, headers = self.get(self.root + '/' + str(observed['build_number']) + '/logText/progressiveText?start=' + str(offset), binary=True)
            h = {k.lower(): v for k, v in headers.items()}; next_offset = int(h.get('x-text-size', offset))
            if next_offset < offset:raise ValueError('log_cursor_regressed')
        text = raw.decode('utf-8', errors='replace')
        for name in (self.c.get('token_env', 'JENKINS_API_TOKEN'), 'GITLAB_TOKEN', 'GLAB_TOKEN'):
            secret = os.environ.get(name)
            if secret:text = text.replace(secret, '[REDACTED]')
        text = re.sub(r'glpat-[A-Za-z0-9_-]+|(?i:authorization:\s*(?:basic|bearer)\s+)\S+', '[REDACTED]', text)
        return {'uid': uid, 'offset': offset, 'next_offset': next_offset, 'text': text, 'untrusted_evidence': True}

    def artifacts(self, uid):
        observed = self.read(uid)
        if self.c['provider'] == 'gitlab':
            if self.kind != 'job':raise ValueError('exact_job_required_for_artifacts')
            value = self.get(self.root + '/jobs/' + str(number(uid)))
            items = [{'filename': x.get('filename'), 'file_type': x.get('file_type'), 'size': x.get('size')} for x in value.get('artifacts', [])]
        else:
            value = self.get(self.root + '/' + str(observed['build_number']) + '/api/json?' + urllib.parse.urlencode({'tree': 'artifacts[fileName,relativePath]'}))
            items = [{'filename': x.get('fileName'), 'path': x.get('relativePath')} for x in value.get('artifacts', [])]
        return {'uid': uid, 'artifacts': items, 'observed_at': time.time(), 'checksums_verified': False}

    def jobs(self, uid):
        if self.c['provider'] != 'gitlab' or self.kind != 'pipeline':raise ValueError('exact_gitlab_pipeline_required')
        self.read(uid)
        rows = self.pages(self.root + '/pipelines/' + str(number(uid)) + '/jobs')
        for row in rows:
            if row.get('commit', {}).get('id') != self.revision():raise ValueError('job_source_revision_mismatch')
        return {'pipeline_uid': uid, 'complete': True, 'jobs': [{k: v.get(k) for k in ('id', 'name', 'status', 'stage', 'web_url')} for v in rows]}

    def artifact(self, uid, remote_path, output, expected_sha256):
        if not re.fullmatch(r'[0-9a-f]{64}', expected_sha256):raise ValueError('expected_artifact_sha256_required')
        v = self.read(uid)
        if not v.get('terminal') or not v.get('successful') or not v.get('source_revision_verified'):
            raise ValueError('successful_pinned_build_required')
        endpoint = self.root + ('/jobs/' + str(number(uid)) + '/artifacts/' if self.c['provider'] == 'gitlab' else '/' + str(v['build_number']) + '/artifact/') + relative(remote_path)
        raw, _ = self.get(endpoint, binary=True)
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected_sha256:raise ValueError('artifact_sha256_mismatch')
        out = Path(output)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open('xb') as stream:stream.write(raw)
        return {'uid': uid, 'path': str(out.resolve()), 'remote_path': remote_path, 'sha256': actual, 'bytes': len(raw), 'archive_extracted': False}


def plan(config_path, output, state, authorization, registry):
    """Prepare an existing external-operation spec; never create authorization."""
    path = Path(config_path).resolve(); c = json.loads(path.read_bytes()); a = Adapter(c)
    module = Path(__file__).resolve(); scripts = module.parent
    def binding(p):return {'path': str(p), 'digest': 'sha256:' + hashlib.sha256(p.read_bytes()).hexdigest()}
    refs = [binding(path), binding(module), binding(scripts/'review_storage.py'), binding(scripts/'external_operations.py'), binding(scripts/'submission_guard.py')]
    commands = {}
    for command in ('probe', 'read', 'lookup', 'submit', 'read-new'):
        commands[command] = [__import__('sys').executable, '-B', str(module), '--config', str(path), '--command', command, '--uid', '{uid}']
        if command == 'submit':commands[command] += ['--operation-spec', str(Path(output).resolve()), '--operation-state', str(Path(state).resolve())]
    spec = {'schema': 'sdm-external-operation/v1', 'provider': c['provider'], 'namespace': c['namespace'], 'owner': c['owner'],
        'request_key': c['request_key'], 'component_uid': a.component(), 'configuration_digest': c['configuration_digest'],
        'action': 'submit' if authorization else 'read', 'routes': [{'id': c['provider'] + '-official',
        'kind': 'official-cli' if c['provider'] == 'gitlab' else 'custom', 'documentation_ref': GITLAB_DOC if c['provider'] == 'gitlab' else JENKINS_DOC,
        'adapter_refs': refs, 'commands': commands}]}
    if authorization:spec['authorization_ref'] = binding(Path(authorization).resolve())
    if registry:spec['submission_registry'] = str(Path(registry).resolve())
    if authorization and not registry:raise ValueError('shared_submission_registry_required')
    if Path(output).exists() and json.loads(Path(output).read_bytes()) != spec:raise ValueError('operation_spec_is_immutable')
    atomic_json(output, spec)
    return {'spec': str(Path(output).resolve()), 'state': str(Path(state).resolve()), 'authorization_created': False, 'platform_writes': 0}


def monitor(adapter, uid, state_path, timeout, poll):
    if not 0 < timeout <= 2678400 or not 1 <= poll <= 3600:raise ValueError('bounded_monitor_timeout_and_poll_required')
    path = Path(state_path); until = time.monotonic() + timeout
    identity = {'config_digest': digest(adapter.c), 'uid': uid}
    with locked(path.parent, '.' + path.name + '.lock', 'ci_monitor_already_running'):
        state = json.loads(path.read_bytes()) if path.exists() else {**identity, 'observations': 0}
        if any(state.get(k) != v for k, v in identity.items()):raise ValueError('ci_monitor_identity_changed')
        while True:
            if state.get('retry_at', 0) > time.time():time.sleep(min(state['retry_at'] - time.time(), max(0, until-time.monotonic())))
            if time.monotonic() >= until:raise ValueError('monitor_deadline_remote_task_not_cancelled')
            try:
                state['observation'] = adapter.read(uid); state['observations'] += 1
                state.pop('error', None); state['retry_at'] = time.time() + poll
            except ApiError as e:
                state['error'] = str(e)
                state['retry_at'] = time.time() + max(poll, 60 if e.code == 429 else poll * 2)
                atomic_json(path, state)
                if e.category not in {'transport'} and e.code not in {429,500,502,503,504}:raise
                continue
            atomic_json(path, state)
            value = state['observation']
            if value.get('terminal'):return value
            if value.get('status') in {'manual', 'blocked'}:raise ValueError('ci_requires_platform_action')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', required=True); p.add_argument('--command', required=True, choices=['probe','read','lookup','submit','read-new','log','artifact','artifacts','jobs','monitor','plan'])
    p.add_argument('--uid'); p.add_argument('--output'); p.add_argument('--operation-spec'); p.add_argument('--operation-state'); p.add_argument('--authorization'); p.add_argument('--registry')
    p.add_argument('--state'); p.add_argument('--timeout', type=float, default=900); p.add_argument('--poll', type=float, default=30); p.add_argument('--offset', type=int, default=0)
    p.add_argument('--artifact-path'); p.add_argument('--artifact-output'); p.add_argument('--sha256'); p.add_argument('--task-node')
    args = p.parse_args(); a = None
    try:
        a = Adapter(json.loads(Path(args.config).read_bytes()))
        if args.command in {'read','read-new','lookup','log','artifact','artifacts','jobs','monitor'}:a.identity()
        if args.command == 'plan':data = plan(args.config, args.output, args.state, args.authorization, args.registry)
        elif args.command == 'probe':data = a.probe()
        elif args.command == 'lookup':data = a.lookup()
        elif args.command == 'submit':data = a.submit(args.operation_spec, args.operation_state)
        elif args.command == 'monitor':data = monitor(a, args.uid, args.state, args.timeout, args.poll)
        elif args.command == 'log':data = a.log(args.uid, args.offset)
        elif args.command == 'jobs':data = a.jobs(args.uid)
        elif args.command == 'artifacts':data = a.artifacts(args.uid)
        elif args.command == 'artifact':data = a.artifact(args.uid, args.artifact_path, args.artifact_output, args.sha256)
        elif args.command == 'read' and a.c.get('watch'):
            if a.kind not in {'pipeline','job','build'}:raise ValueError('monitorable_ci_resource_required')
            watch = a.c['watch']
            data = monitor(a, args.uid or str(a.c['resource'].get('id', '')), watch['state'], watch.get('timeout_seconds', 7200), watch.get('poll_seconds', 30))
            if not data.get('successful') or not data.get('source_revision_verified'):raise ValueError('verified_successful_ci_terminal_required')
        else:data = a.read(args.uid)
        result = {'status': 'ok', 'data': data}
        if args.task_node:
            if args.command != 'monitor' or not data.get('terminal') or not data.get('successful') or not data.get('source_revision_verified'):
                raise ValueError('verified_successful_ci_terminal_required')
            result = {'task_node': args.task_node, 'status': 'completed', 'ci_result': data, 'ml_quality_accepted': False, 's13_emitted': False}
        code = 0
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as e:
        result = {'status': 'failed', 'error': str(e) if isinstance(e, ValueError) else type(e).__name__,
                  'automatic_write_retry': False, 'remote_task_cancelled': False,
                  'uncertain_effect': args.command in {'submit','monitor'} or bool(a and a.c.get('watch')),
                  'next_action': 'reconcile-original-write' if args.command == 'submit' else 'inspect-original-object-and-configuration'}
        if args.task_node:result.update(task_node=args.task_node, reason=result['error'])
        code = 1
    if args.output and args.command != 'plan':atomic_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False)); return code


if __name__ == '__main__':
    raise SystemExit(main())
