"""Read-only official capability/identity diagnostics. No platform write commands."""
import argparse
import ast
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

SDK_DOC = 'https://example.feishu.cn/docx/DEMO00000000000000000000002'
AGENT_DOC = 'https://example.feishu.cn/docx/DEMO00000000000000000000001'


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def run(argv):
    """Capture diagnostics without persisting/echoing tool stdout or stderr."""
    try:
        result = subprocess.run(argv, capture_output=True, encoding='utf-8',
                                errors='replace', timeout=45, shell=False,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            value = json.loads(result.stdout)
        except ValueError:
            value = None
        return result.returncode, value, result.stdout
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, {'probe_error_type': type(error).__name__}, ''


def unwrap(value):
    if isinstance(value, dict) and isinstance(value.get('stdout'), str):
        try:
            return json.loads(value['stdout'])
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def subcommands(help_text):
    match = re.search(r'Available Commands:\s*\n(.*?)(?:\nFlags:|\nGlobal Flags:|\Z)',
                      help_text, flags=re.S)
    return re.findall(r'^  ([\w-]+)\s+', match[1], flags=re.M) if match else []


def sdk_methods(package):
    result, sources = {}, []
    if not package.is_dir() or not (package / '__init__.py').is_file():
        raise ValueError('installed_sdk_package_directory_required')
    for path in sorted(package.rglob('*.py')):
        # Static inspection only: no importing SDK modules or reading auth files.
        raw = path.read_bytes()
        tree = ast.parse(raw, filename=str(path))
        sources.append({'path': str(path.resolve()), 'sha256': hashlib.sha256(raw).hexdigest()})
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                result[str(path.relative_to(package)) + ':' + node.name] = [
                    child.name for child in node.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and not child.name.startswith('_')]
    return result, sources


def capability_report(cli, package):
    commands, evidence = {}, []
    for group in ('', 'projects', 'devspaces', 'tasks'):
        args = [cli] + ([group] if group else []) + ['--help']
        code, _, help_text = run(args)
        commands[group or 'root'] = subcommands(help_text) if code == 0 else []
        evidence.append({'argv': args, 'returncode': code,
                         'help_sha256': hashlib.sha256(help_text.encode()).hexdigest()})
    methods, sources = sdk_methods(Path(package))
    def sdk_has(class_name, method):
        return any(key.endswith(':' + class_name) and method in names for key, names in methods.items())
    rows = []
    for operation, group, verb, binding in (
        ('component.create', 'projects', 'create', ('Project', 'create')),
        ('component.edit', 'projects', 'update', ('Project', 'update')),
        ('component.read', 'projects', 'get', ('Project', 'get')),
        ('component.start', 'projects', 'start', ('Task', 'submit_project_task')),
        ('task.read', 'tasks', 'get', ('Task', 'get')),
        ('task.stop', 'tasks', 'stop', ('Task', 'update')),
        ('devspace.create', 'devspaces', 'create', None),
        ('devspace.edit', 'devspaces', 'update', None),
        ('devspace.read', 'devspaces', 'get', None),
        ('devspace.start', 'devspaces', 'start', None),
        ('devspace.stop', 'devspaces', 'stop', None),
        ('approval.read', 'approval', 'get', None),
        ('approval.cancel', 'approval', 'cancel', None),
        ('approval.resend', 'approval', 'resend', None),
    ):
        cli_available = verb in commands.get(group, [])
        sdk_available = bool(binding and sdk_has(*binding))
        rows.append({'operation': operation, 'cli_implemented': cli_available,
                     'cli_command': [group, verb] if cli_available else None,
                     'sdk_implemented': sdk_available,
                     'sdk_binding': '.'.join(binding) if sdk_available else None,
                     'qualification': 'local-implementation-only' if cli_available or sdk_available
                                      else 'not-found-in-inspected-clients',
                     'live_write_verified': False})
    return {'schema': 'lpai-management-capabilities/v1', 'at': now(), 'operations': rows,
            'cli_commands': commands, 'cli_evidence': evidence,
            'sdk_methods': methods, 'sdk_sources': sources,
            'official_docs': [SDK_DOC, AGENT_DOC], 'platform_writes': 0,
            'boundary': 'Local absence is not proof that the platform has no API. No approval bypass or automatic route fallback.'}


def binding_diagnosis(approval, profile, devspace=None):
    approval = unwrap(approval)
    data = profile.get('data', {}) if isinstance(profile, dict) else {}
    users = data.get('users', []) if isinstance(data, dict) else []
    current = users[0] if len(users) == 1 and profile.get('ok') is True and profile.get('identity') == 'user' else {}
    emails = sorted({str(current.get(key, '')).strip().lower()
                     for key in ('email', 'enterprise_email')} - {''})
    recipients = sorted({str(value).strip().lower() for value in approval.get('recipients', [])})
    outcome = 'unresolved'
    if emails and recipients:
        outcome = 'address-match-delivery-unverified' if set(emails) & set(recipients) else 'address-mismatch-alias-mapping-unverified'
    annotations = ((devspace or {}).get('metadata') or {}).get('annotations') or {}
    return {'schema': 'lpai-management-binding-diagnostic/v1', 'at': now(),
            'approval': {key: approval[key] for key in ('approval_id', 'status', 'mode') if key in approval},
            'gateway_recipient_emails': recipients,
            'feishu_profile': {key: current[key] for key in
                              ('open_id', 'localized_name', 'enterprise_email', 'email', 'is_activated', 'is_cross_tenant') if key in current},
            'platform_owner': annotations.get('lpai.org/owner'),
            'platform_original_owner': annotations.get('lpai.org/res-stat-task-origin-owner'),
            'binding_observation': outcome, 'root_cause_proven': False,
            'pending_is_submitted': False, 'delivery_verified': False,
            'approval_resolution': 'no-official-read-cancel-resend-operation-found-in-inspected-cli',
            'platform_writes': 0, 'notifications_sent': 0,
            'next_check': 'Verify provider mapping of normalized owner to the active Feishu account and delivery record; local lark-cli login does not bind the LPAI Agent bot.'}


def lark_prefix(path):
    return ['powershell.exe', '-NoProfile', '-File', path] if str(path).lower().endswith('.ps1') else [path]


def doctor(cli, lark, namespace, devspace, pending):
    # Fixed read verbs; never accept a shell string or arbitrary CLI operation.
    prefix = lark_prefix(lark)
    auth_code, auth, _ = run(prefix + ['auth', 'status', '--json', '--verify'])
    profile_code, profile, _ = run(prefix + ['contact', '+search-user', '--user-ids', 'me', '--as', 'user'])
    space_code, space, _ = run([cli, 'devspaces', 'get', '--namespace', namespace, '--name', devspace])
    native = space.get('data', {}) if isinstance(space, dict) and space_code == 0 else {}
    if not isinstance(native, dict) or native.get('metadata', {}).get('namespace') != namespace or native.get('metadata', {}).get('name') != devspace:
        native = {}
    original = json.loads(Path(pending).read_bytes())
    result = binding_diagnosis(original, profile or {}, native)
    result['original_response'] = {'path': str(Path(pending).resolve()),
                                   'sha256': hashlib.sha256(Path(pending).read_bytes()).hexdigest(),
                                   'at': original.get('at'),
                                   'current_approval_status_rechecked': False}
    auth = auth if isinstance(auth, dict) else {}
    identity = auth.get('identities', {}).get('user', {})
    result['lark_auth'] = {key: identity[key] for key in ('status', 'available', 'verified', 'userName', 'openId') if key in identity}
    result['diagnostic_application_id'] = auth.get('appId')
    auth_user = identity.get('openId')
    profile_user = result['feishu_profile'].get('open_id')
    result['identity_consistent'] = (auth_user == profile_user) if auth_user and profile_user else None
    if result['identity_consistent'] is False:
        result['binding_observation'] = 'unresolved-inconsistent-diagnostic-identities'
    result['application_scope_note'] = 'This is the local diagnostic app, not evidence of the LPAI notification bot binding.'
    result['read_results'] = {'auth_returncode': auth_code, 'profile_returncode': profile_code,
                              'devspace_returncode': space_code, 'devspace_identity_verified': bool(native)}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    subs = parser.add_subparsers(dest='action', required=True)
    cap = subs.add_parser('capabilities')
    cap.add_argument('--cli', required=True)
    cap.add_argument('--sdk-package', required=True)
    check = subs.add_parser('doctor')
    for field in ('cli', 'lark', 'namespace', 'devspace', 'pending-response'):
        check.add_argument('--' + field, required=True)
    args = parser.parse_args()
    if args.action == 'capabilities':
        result = capability_report(args.cli, args.sdk_package)
    else:
        result = doctor(args.cli, args.lark, args.namespace, args.devspace, args.pending_response)
    from review_storage import atomic_json
    atomic_json(args.output, result)
    print(json.dumps({'output': str(Path(args.output).resolve()), 'platform_writes': 0,
                      'binding_observation': result.get('binding_observation')}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
