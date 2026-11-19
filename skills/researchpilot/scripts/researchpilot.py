"""Read project context inside the current Codex session; execution stays with SDM.

Native installation is one portable Skill folder. Legacy registered entries are
readable for compatibility; they are not a prerequisite for ordinary activation.
"""
import argparse
import hashlib
import json
import os
import uuid
import sys
from pathlib import Path

sys.dont_write_bytecode=True


def io_path(path):
    path=Path(path)
    if os.name!='nt':return path
    value=os.path.abspath(path)
    if not value.startswith('\\\\?\\'):
        value='\\\\?\\UNC\\'+value[2:] if value.startswith('\\\\') else '\\\\?\\'+value
    return Path(value)


def read(path):return json.loads(io_path(path).read_bytes())
def sha(path):return hashlib.sha256(io_path(path).read_bytes()).hexdigest()


def capability_profile(project,skill):
    # Operational defaults are local project/user state, never bundled science
    # or authorization. Retain the old profile during a managed entry upgrade.
    for path in (project/'.bmad-ml/CAPABILITIES.json',
                 project/'.agents/skills/researchpilot/CAPABILITIES.json',
                 project/'.agents/skills/bmad-ml/CAPABILITIES.json'):
        if path.is_file():
            if read(path).get('schema') not in ('bmad-ml-capabilities/v1','researchpilot-capabilities/v1'):
                raise ValueError('typed_capability_profile_required')
            return str(path)
    return None


def native_runtime(skill):
    inventory=skill/'BUNDLE.json'
    if inventory.is_file():
        bundle=read(inventory)
        if bundle.get('schema')!='researchpilot-native-skill/v1' or bundle.get('name')!='researchpilot':raise ValueError('unsupported_skill_bundle')
        for name,expected in bundle['files'].items():
            relative=Path(name)
            if relative.is_absolute() or '..' in relative.parts or ':' in name:
                raise ValueError('invalid_bundle_relative_path')
            path=skill/relative
            if path.resolve().is_relative_to(skill.resolve()) is False or sha(path)!=expected:
                raise ValueError('skill_bundle_file_changed:'+name)
        module=skill/'references/sdm-module'
        for required in ('SKILL.md','agents/openai.yaml','scripts/researchpilot.py',
                'references/sdm-module/sdm-lifecycle/SKILL.md',
                'references/sdm-module/sdm-lifecycle/scripts/research_initialize.py'):
            if required not in bundle['files']:raise ValueError('incomplete_native_skill')
        return module,'native-skill'
    # Maintainer checkout: the same implementation, without project registration.
    module=skill.parent/'sdm-module'
    if (module/'sdm-lifecycle/scripts/research_initialize.py').is_file():
        return module,'source-checkout'
    return None,None


def registration(project):
    project=Path(project).resolve();skill=project/'.agents/skills/bmad-ml'
    installed=read(project/'_bmad/sdm-entry/installation.json')
    if installed['project_root']!=str(project):raise ValueError('installed_project_binding_changed')
    for name,expected in installed['files'].items():
        if sha(project/name)!=expected:raise ValueError('registered_entry_file_changed:'+name)
    cfg=read(skill/'ENTRY.json')
    if cfg['project_root']!=str(project):raise ValueError('entry_project_binding_changed')
    for relative,expected in cfg['entry_files'].items():
        if sha(skill/relative)!=expected:raise ValueError('registered_entry_changed:'+relative)
    release=Path(cfg['release'])
    for relative,expected in cfg['release_files'].items():
        if sha(release/relative)!=expected:raise ValueError('pinned_release_changed:'+relative)
    manifest=read(release/'manifest.json')
    for name,expected in manifest['runtime_manifest'].items():
        if sha(Path(manifest['runtime_root'])/name)!=expected:raise ValueError('pinned_runtime_changed:'+name)
    for item in manifest['guidance_manifest']:
        if sha(item['path'])!=item['sha256']:raise ValueError('pinned_guidance_changed')
    if sha(release/'sdm-lifecycle/SKILL.md')!=cfg['lifecycle_sha256']:
        raise ValueError('pinned_lifecycle_entry_changed')
    if sha(cfg['codex_cli'])!=cfg['codex_cli_sha256']:raise ValueError('registered_codex_cli_changed')
    return cfg,manifest


def inspect(project):
    project=Path(project).resolve()
    if not project.is_dir():raise ValueError('existing_project_root_required')
    skill=Path(__file__).resolve().parents[1]
    module,mode=native_runtime(skill)
    if module:
        lifecycle=module/'sdm-lifecycle/SKILL.md'
        runtime=module/'sdm-lifecycle/scripts'
    else:
        cfg,manifest=registration(project)
        lifecycle=Path(cfg['release'])/'sdm-lifecycle/SKILL.md'
        runtime=Path(manifest['runtime_root']);mode='legacy-registered-entry'
    try:origin_chat=str(uuid.UUID(os.environ.get('CODEX_THREAD_ID','')))
    except (ValueError,AttributeError):origin_chat=None
    candidates=[]
    for pattern in ('README*','pyproject.toml','requirements*.txt','train*.py','configs/*.yaml','configs/*.yml'):
        for path in project.glob(pattern):
            if path.is_file() and path.stat().st_size<=256*1024:candidates.append(str(path))
    indexes=[]
    for pattern in ('CURRENT.json','.bmad-ml/CURRENT.json','.bmad-ml/*/CURRENT.json',
            '_bmad-output/*/CURRENT.json','research/*/CURRENT.json','experiments/*/CURRENT.json'):
        indexes.extend(str(p) for p in project.glob(pattern) if p.is_file())
    indexes=sorted(set(indexes),key=lambda name:Path(name).stat().st_mtime,reverse=True)
    sys.path.insert(0,str(runtime))
    from research_project import inspect as inspect_project
    project_integration=inspect_project(project)
    from research_deployment import inspect as inspect_deployment
    deployment=inspect_deployment(project,skill,runtime,indexes,mode)
    from research_task_scope import tasks
    entry_tasks,task_errors=tasks(project)
    return {'schema':'bmad-ml-project-context/v1','project_root':str(project),'project_name':project.name,
        'entry':str(skill/'SKILL.md'),'installation_mode':mode,'conversation':'current-codex-session','deployment':deployment,
        'entry_tasks':entry_tasks,'task_scope_errors':task_errors,
        'task_modes':{'single-evaluation':'评测与分析完成后结束','continuous-research':'原持续授权内分析、方案、审议与执行接续'},
        'project_registration_required':False if module else True,
        'sdm_entry':str(lifecycle),
        'new_study_initialization_schema':'sdm-research-initialization/v3',
        'new_study_workload_version':3 if project_integration['status']=='ready' else None,
        'workload_selection':'Use discovered project integration; retain the binary adapter only for its declared input format.',
        'acceptance_reader':str(runtime/'research_acceptance.py'),
        'legacy_studies':'Use each original frozen host; never reinterpret under the new profile.',
        'runtime_scripts':str(runtime),'material_candidates':list(dict.fromkeys(candidates))[:40],
        'project_integration':project_integration,'project_integration_guide':str(module/'sdm-lifecycle/references/project-integration.md'),
        'study_indexes':indexes[:24],'origin_codex_chat_id':origin_chat,
        'capability_profile':capability_profile(project,skill),
            'environment':str(project/'.bmad-ml/ENVIRONMENT.json') if (project/'.bmad-ml/ENVIRONMENT.json').is_file() else None,
        'workbench_action':{'command':'workbench','project_root':str(project),'ui_open_required':True,
            'when':'Before onboarding or research handoff; open in the current Codex chat.'},
        'startup_authorizes_training':False,'task_specific_study_created':False,'platform_writes':0}


def workbench(project,study=None):
    """Prepare/reuse the real view. The desktop host opens the returned target."""
    context=inspect(project);skill=Path(context['entry']).parent
    sys.path.insert(0,context['runtime_scripts'])
    # Validate a requested study in the same original read-only projection.
    if study is not None:
        from research_dashboard_data import DashboardData
        registered=DashboardData(context['project_root']).discover()
        if not any(cfg['project_id']==study for _,cfg,_ in registered):raise KeyError('study_not_registered')
    python=sys.executable
    if context['environment']:
        saved=read(context['environment']);candidate=saved.get('python')
        if candidate and Path(candidate).is_file():python=candidate
    from research_workbench import ensure
    result=ensure(context['project_root'],skill,python)
    from urllib.parse import urlsplit,urlunsplit,urlencode,parse_qsl
    target=urlsplit(result['url']);query=dict(parse_qsl(target.query))
    if study is not None:query['study']=study
    url=urlunsplit((target.scheme,target.netloc,target.path,urlencode(query),target.fragment))
    return {**result,'study_id':study,'ui_open_required':True,'ui_opened':False,
        'open_target':{'type':'browser','url':url},'placement':'right',
        'open_with':'current desktop host open_in_codex; reuse the current project tab',
        'research_started':False,'training_authorized':False,
        'next':'Open the target now and verify visible project/study. A returned URL alone is not an opened workbench.'}


def task(project,operation,task_id,mode=None,objective=None,source_ref=None,result=None,analysis=None,pending_reason=None,config=None):
    context=inspect(project);sys.path.insert(0,context['runtime_scripts'])
    import research_task_scope as scope
    value=scope.begin(project,task_id,mode,objective,source_ref) if operation=='declare' else scope.update(project,task_id,result,analysis,pending_reason,config) if operation=='record' else scope.status(project,task_id)
    if value.get('study_id'):
        from research_dashboard_data import DashboardData
        try:
            study=DashboardData(project).snapshot(value['id'])['study']
            return {**value,**study['task_handoff'],'id':study['id'],'task_id':task_id}
        except KeyError:pass
    return value


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__);subs=parser.add_subparsers(dest='action',required=True)
    command=subs.add_parser('inspect');command.add_argument('--project-root',required=True)
    command=subs.add_parser('workbench');command.add_argument('--project-root',required=True);command.add_argument('--study')
    command=subs.add_parser('task');command.add_argument('--project-root',required=True);command.add_argument('--task-id',required=True)
    command.add_argument('--operation',choices=['declare','record','status'],default='status')
    command.add_argument('--mode',choices=['single-evaluation','continuous-research'])
    for name in ('objective','source-ref','result','analysis','pending-reason','config'):command.add_argument('--'+name)
    args=vars(parser.parse_args());action=args.pop('action');project=args.pop('project_root')
    value=inspect(project) if action=='inspect' else workbench(project,**args) if action=='workbench' else task(project,**args)
    print(json.dumps(value,ensure_ascii=False))
