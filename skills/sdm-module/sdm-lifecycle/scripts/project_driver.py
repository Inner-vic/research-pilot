"""Resolve a byte-pinned project adapter without owning its business logic."""
import hashlib
import json
import re
from pathlib import Path


def relative_file(name):
    if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*',name) or any(p in {'.','..','__pycache__'} for p in name.split('/')):
        raise ValueError('project_bundle_relative_file_required')
    return name


def file_digest(path):
    checksum=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            checksum.update(chunk)
    return 'sha256:'+checksum.hexdigest()


def bundle_digest(files):
    return 'sha256:'+hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def files_in(root):
    root=Path(root).absolute()
    if root.resolve()!=root:raise ValueError('project_bundle_links_not_allowed')
    if root.is_symlink() or getattr(root,'is_junction',lambda:False)():raise ValueError('project_bundle_links_not_allowed')
    result={};total=0
    for path in root.rglob('*'):
        if path.is_symlink() or getattr(path,'is_junction',lambda:False)():raise ValueError('project_bundle_links_not_allowed')
        if path.is_dir():continue
        if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):raise ValueError('regular_project_bundle_file_required')
        name=relative_file(path.relative_to(root).as_posix())
        size=path.stat().st_size;total+=size
        if len(result)>=64 or size>10*1024*1024 or total>32*1024*1024:raise ValueError('bounded_project_bundle_required')
        if name.casefold() in {n.casefold() for n in result}:raise ValueError('case_unique_project_bundle_required')
        result[name]=file_digest(path)
    return result


def bind_bundle(entry):
    entry=Path(entry).absolute()
    files=files_in(entry.parent)
    if entry.name not in files:raise ValueError('project_driver_missing')
    return {'path':str(entry),'digest':files[entry.name],'files':files,'bundle_digest':bundle_digest(files)}


def resolve(config, host):
    binding = config.get('project_driver')
    if binding is None:
        # Existing immutable RoadModel runs keep their original installed driver.
        if config.get('provider') != 'roadmodel-devspace-v1':
            raise ValueError('project_driver_binding_required')
        path = Path(host) / '.agents/skills/lpai-s12-platform/scripts/roadmodel_task_driver.py'
    else:
        if not isinstance(binding, dict) or set(binding) not in ({'path','digest'},{'path','digest','files','bundle_digest'}):
            raise ValueError('project_driver_path_and_digest_required')
        path = Path(binding['path'])
        if not path.is_absolute() or path.is_symlink() or getattr(path,'is_junction',lambda:False)():
            raise ValueError('absolute_project_driver_required')
        if 'files' in binding and path.parent.resolve()!=path.parent:
            raise ValueError('project_bundle_links_not_allowed')
        path = path.resolve()
        if not path.is_file():
            raise ValueError('project_driver_missing')
        actual = file_digest(path)
        if actual != binding['digest']:
            raise ValueError('project_driver_changed_create_new_run')
        if 'files' in binding:
            files=binding['files']
            if not isinstance(files,dict) or not files or len(files)>64:raise ValueError('bounded_project_bundle_required')
            for name,value in files.items():
                relative_file(name)
                if not isinstance(value,str) or not re.fullmatch(r'sha256:[0-9a-f]{64}',value):raise ValueError('project_bundle_digest_required')
            if files.get(path.name)!=binding['digest'] or bundle_digest(files)!=binding['bundle_digest']:
                raise ValueError('project_bundle_binding_mismatch')
            if files_in(path.parent)!=files:raise ValueError('project_bundle_changed_create_new_run')
    if not path.is_file():
        raise ValueError('provider_driver_missing')
    return path


def inspect_interface(config,host):
    """Static migration check: never import/execute a possibly mutating adapter."""
    import ast
    path=resolve(config,host)
    tree=ast.parse(path.read_text(encoding='utf-8'))
    flags=set();dispatch={};context_reads={}
    for node in ast.walk(tree):
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='add_argument':
            flags.update(arg.value for arg in node.args if isinstance(arg,ast.Constant) and isinstance(arg.value,str) and arg.value.startswith('--'))
        if (isinstance(node,ast.Compare) and isinstance(node.left,ast.Name) and node.left.id in {'key','node'}
            and len(node.ops)==1 and isinstance(node.ops[0],ast.Eq) and isinstance(node.comparators[0],ast.Constant)
            and isinstance(node.comparators[0].value,str)):
            dispatch.setdefault(node.comparators[0].value,[]).append(node.lineno)
        if (isinstance(node,ast.Subscript) and isinstance(node.slice,ast.Constant) and isinstance(node.slice.value,str)
            and (isinstance(node.value,ast.Name) and node.value.id in {'cfg','config'}
                 or isinstance(node.value,ast.Attribute) and isinstance(node.value.value,ast.Name)
                    and node.value.value.id=='self' and node.value.attr in {'c','cfg','config'})):
            context_reads.setdefault(node.slice.value,[]).append(node.lineno)
    required={'--project','--node','--receipt','--handshake','--handshake-context'}
    missing=sorted(required-flags)
    return {'schema':'sdm-project-adapter-inspection/v1','driver':str(path),'driver_digest':file_digest(path),
        'explicit_cli_flags':sorted(flags),'missing_explicit_flags':missing,
        'status':'migration-required' if missing else 'requires-behavioral-verification',
        'qualification':'static-explicit-parser-inspection-not-business-or-runtime-proof',
        'dynamic_parser_support':'not-assessed','adapter_imported':False,'adapter_executed':False,
        'literal_dispatch_branches':dispatch,'explicit_context_subscripts':context_reads,
        'static_inventory_limitations':['Literal branches and context reads are navigation aids, not an exhaustive interface or behavioral contract.',
            'Neither parser flags nor source inspection prove that offline handshake avoids platform operations.'],
        'execution_authority_created':False}
