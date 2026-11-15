"""Read project context inside the current Codex session; execution stays with SDM.

Native installation is one portable Skill folder. Legacy registered entries are
readable for compatibility; they are not a prerequisite for ordinary activation.
"""
import argparse
import hashlib
import json
from pathlib import Path


def read(path):return json.loads(Path(path).read_bytes())
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def capability_profile(project,skill):
    # Operational defaults are local project/user state, never bundled science
    # or authorization. Retain the old profile during a managed entry upgrade.
    for path in (project/'.bmad-ml/CAPABILITIES.json',
                 project/'.agents/skills/bmad-ml/CAPABILITIES.json',
                 skill/'CAPABILITIES.json'):
        if path.is_file():
            if read(path).get('schema')!='bmad-ml-capabilities/v1':
                raise ValueError('typed_capability_profile_required')
            return str(path)
    return None


def native_runtime(skill):
    inventory=skill/'BUNDLE.json'
    if inventory.is_file():
        bundle=read(inventory)
        if bundle.get('schema')!='bmad-ml-native-skill/v1':raise ValueError('unsupported_skill_bundle')
        for name,expected in bundle['files'].items():
            relative=Path(name)
            if relative.is_absolute() or '..' in relative.parts or ':' in name:
                raise ValueError('invalid_bundle_relative_path')
            path=skill/relative
            if path.resolve().is_relative_to(skill.resolve()) is False or sha(path)!=expected:
                raise ValueError('skill_bundle_file_changed:'+name)
        module=skill/'references/sdm-module'
        for required in ('SKILL.md','agents/openai.yaml','scripts/bmad_ml.py',
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
    candidates=[]
    for pattern in ('README*','pyproject.toml','requirements*.txt','train*.py','configs/*.yaml','configs/*.yml'):
        for path in project.glob(pattern):
            if path.is_file() and path.stat().st_size<=256*1024:candidates.append(str(path))
    indexes=[]
    for pattern in ('CURRENT.json','.bmad-ml/CURRENT.json','.bmad-ml/*/CURRENT.json',
            '_bmad-output/*/CURRENT.json','research/*/CURRENT.json','experiments/*/CURRENT.json'):
        indexes.extend(str(p) for p in project.glob(pattern) if p.is_file())
    indexes=sorted(set(indexes),key=lambda name:Path(name).stat().st_mtime,reverse=True)
    return {'schema':'bmad-ml-project-context/v1','project_root':str(project),'project_name':project.name,
        'entry':str(skill/'SKILL.md'),'installation_mode':mode,'conversation':'current-codex-session',
        'project_registration_required':False if module else True,
        'sdm_entry':str(lifecycle),
        'runtime_scripts':str(runtime),'material_candidates':list(dict.fromkeys(candidates))[:40],
        'study_indexes':indexes[:24],
        'capability_profile':capability_profile(project,skill),
            'environment':str(project/'.bmad-ml/ENVIRONMENT.json') if (project/'.bmad-ml/ENVIRONMENT.json').is_file() else None,
        'startup_authorizes_training':False,'task_specific_study_created':False,'platform_writes':0}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);subs=parser.add_subparsers(dest='action',required=True)
    command=subs.add_parser('inspect');command.add_argument('--project-root',required=True)
    args=parser.parse_args()
    print(json.dumps(inspect(args.project_root),ensure_ascii=False))
