"""Copy the native Skill folder; upgrade only a verified owned installation.

This is maintainer tooling, not a prerequisite for using the installed Skill.
It never starts Codex, registers project nodes or touches a frozen research run.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import shutil
import sys
import uuid

sys.dont_write_bytecode=True


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):return json.loads(path.read_bytes())


def verify(folder):
    spec=importlib.util.spec_from_file_location('native_bmad_ml',folder/'scripts/bmad_ml.py')
    entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)
    module,mode=entry.native_runtime(folder)
    if mode!='native-skill':raise ValueError('portable_native_bundle_required')
    allowed=set(read(folder/'BUNDLE.json')['files'])|{'BUNDLE.json'}
    actual={p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()}
    if actual!=allowed:raise ValueError('unexpected_native_bundle_files')
    return sha(folder/'BUNDLE.json')


def install(bundle,destination,expected_digest=None,backup=None):
    bundle=Path(bundle).resolve();destination=Path(destination).absolute()
    if destination.resolve()!=destination or destination.name!='bmad-ml':
        raise ValueError('unlinked_named_skill_destination_required')
    digest=verify(bundle);old_record=None;profile=None;old_launcher=None
    if destination.exists():
        if (destination/'BUNDLE.json').exists():previous=verify(destination)
        else:
            project=destination.parents[2]
            if destination!=project/'.agents/skills/bmad-ml':raise ValueError('unowned_skill_destination')
            old_record=project/'_bmad/sdm-entry/installation.json'
            record=read(old_record);previous=sha(old_record)
            if record['project_root']!=str(project):raise ValueError('legacy_project_binding_changed')
            for name,value in record['files'].items():
                if sha(project/name)!=value:raise ValueError('legacy_owned_file_changed:'+name)
            owned={Path(n).relative_to('.agents/skills/bmad-ml').as_posix() for n in record['files']
                if n.startswith('.agents/skills/bmad-ml/')}
            actual={p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()}
            if actual!=owned:raise ValueError('unowned_legacy_skill_files')
            old_launcher=project/'Start-BMAD-ML.cmd'
            if 'Start-BMAD-ML.cmd' not in record['files']:old_launcher=None
            if (destination/'CAPABILITIES.json').exists():
                profile=(destination/'CAPABILITIES.json',project/'.bmad-ml/CAPABILITIES.json')
                if profile[1].exists() and profile[1].read_bytes()!=profile[0].read_bytes():
                    raise ValueError('existing_capability_profile_differs')
        if previous!=expected_digest:raise ValueError('installed_digest_changed')
        if not backup:raise ValueError('fresh_backup_required_for_upgrade')
    elif expected_digest not in (None,'absent'):raise ValueError('expected_installation_missing')
    backup=Path(backup).absolute() if backup else None
    if backup and (backup.resolve()!=backup or backup.exists() or backup.is_relative_to(destination)
                   or destination.is_relative_to(backup)):
        raise ValueError('fresh_independent_backup_required')
    destination.parent.mkdir(parents=True,exist_ok=True)
    staging=destination.with_name('.bmad-ml-install-'+uuid.uuid4().hex)
    shutil.copytree(bundle,staging);verify(staging)
    if backup:backup.mkdir(parents=True)
    moves=[];profile_created=False
    try:
        if profile and not profile[1].exists():
            profile[1].parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(*profile);profile_created=True
        if destination.exists():
            destination.rename(backup/'previous-skill');moves.append((backup/'previous-skill',destination))
        staging.rename(destination)
        for path in (old_record,old_launcher):
            if path:
                path.rename(backup/path.name);moves.append((backup/path.name,path))
        installed=verify(destination)
        if installed!=digest:raise ValueError('installed_bundle_readback_changed')
    except BaseException:
        # Keep any failed new copy for diagnosis; restore only exact owned moves.
        if destination.exists() and not staging.exists():destination.rename(staging)
        for saved,original in reversed(moves):saved.rename(original)
        if profile_created:profile[1].unlink()
        raise
    result={'status':'installed','skill':str(destination),'bundle_sha256':digest,
        'conversation':'current-codex-session','backup':str(backup) if backup else None,
        'retired_legacy_registration':bool(old_record),'model_calls':0,'platform_writes':0}
    if backup:(backup/'receipt.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',required=True);parser.add_argument('--destination',required=True)
    parser.add_argument('--expected-digest');parser.add_argument('--backup')
    print(json.dumps(install(**vars(parser.parse_args())),ensure_ascii=False))
