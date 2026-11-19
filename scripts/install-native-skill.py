"""Install ResearchPilot and retire only verified owned legacy entries.

No research, model calls, platform writes, authentication or frozen-state changes.
"""
import argparse,hashlib,importlib.util,json,shutil,sys,uuid
from pathlib import Path
sys.dont_write_bytecode=True
_storage_spec=importlib.util.spec_from_file_location('researchpilot_install_storage',
 Path(__file__).resolve().parents[1]/'skills/sdm-module/sdm-lifecycle/scripts/review_storage.py')
_storage=importlib.util.module_from_spec(_storage_spec);_storage_spec.loader.exec_module(_storage)
native_path=_storage.native_path
def sha(path):return hashlib.sha256(native_path(path).read_bytes()).hexdigest()
def read(path):return json.loads(native_path(path).read_bytes())

def verify(folder):
    folder=native_path(folder);bundle=read(folder/'BUNDLE.json')
    names={'researchpilot-native-skill/v1':('researchpilot','researchpilot.py'),
           'bmad-ml-native-skill/v1':('bmad-ml','bmad_ml.py')}
    expected=names.get(bundle.get('schema'))
    if not expected or bundle.get('name')!=expected[0]:raise ValueError('owned_native_bundle_identity_required')
    spec=importlib.util.spec_from_file_location('native_researchpilot_entry',folder/'scripts'/expected[1])
    entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)
    _,mode=entry.native_runtime(folder)
    if mode!='native-skill':raise ValueError('portable_native_bundle_required')
    allowed=set(bundle['files'])|{'BUNDLE.json'}
    actual={p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()}
    if actual!=allowed:raise ValueError('unexpected_native_bundle_files')
    return sha(folder/'BUNDLE.json')

def owned(folder):
    folder=Path(folder).absolute()
    if folder.resolve()!=folder:raise ValueError('unlinked_owned_skill_required')
    if (folder/'BUNDLE.json').exists():return {'digest':verify(folder)}
    project=folder.parents[2]
    if folder!=project/'.agents/skills/bmad-ml':raise ValueError('unowned_skill_destination')
    record_path=project/'_bmad/sdm-entry/installation.json';record=read(record_path)
    if record['project_root']!=str(project):raise ValueError('legacy_project_binding_changed')
    for name,value in record['files'].items():
        if sha(project/name)!=value:raise ValueError('legacy_owned_file_changed:'+name)
    files={Path(n).relative_to('.agents/skills/bmad-ml').as_posix() for n in record['files']
           if n.startswith('.agents/skills/bmad-ml/')}
    actual={p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()}
    if actual!=files:raise ValueError('unowned_legacy_skill_files')
    result={'digest':sha(record_path),'record':record_path}
    if 'Start-BMAD-ML.cmd' in record['files']:result['launcher']=project/'Start-BMAD-ML.cmd'
    if (folder/'CAPABILITIES.json').exists():
        source,target=folder/'CAPABILITIES.json',project/'.bmad-ml/CAPABILITIES.json'
        if target.exists() and target.read_bytes()!=source.read_bytes():raise ValueError('existing_capability_profile_differs')
        result['profile']=(source,target)
    return result

def install(bundle,destination,expected_digest=None,backup=None,legacy_digest=None):
    bundle=Path(bundle).resolve();destination=Path(destination).absolute()
    if destination.resolve()!=destination or destination.name!='researchpilot':
        raise ValueError('unlinked_researchpilot_destination_required')
    digest=verify(bundle)
    if read(bundle/'BUNDLE.json').get('name')!='researchpilot':raise ValueError('researchpilot_bundle_required')
    current=owned(destination) if destination.exists() else None
    if current and current['digest']!=expected_digest:raise ValueError('installed_digest_changed')
    if not current and expected_digest not in (None,'absent'):raise ValueError('expected_installation_missing')
    legacy=destination.with_name('bmad-ml');retired=owned(legacy) if legacy.exists() else None
    if retired and retired['digest']!=legacy_digest:raise ValueError('legacy_installed_digest_changed')
    if not retired and legacy_digest not in (None,'absent'):raise ValueError('expected_legacy_installation_missing')
    if (current or retired) and not backup:raise ValueError('fresh_backup_required_for_upgrade')
    backup=Path(backup).absolute() if backup else None
    if backup and (backup.resolve()!=backup or backup.exists() or
       any(backup.is_relative_to(p) or p.is_relative_to(backup) for p in (destination,legacy))):
        raise ValueError('fresh_independent_backup_required')
    display_destination,display_backup=destination,backup
    destination.parent.mkdir(parents=True,exist_ok=True)
    staging=destination.with_name('.researchpilot-install-'+uuid.uuid4().hex)
    shutil.copytree(native_path(bundle),native_path(staging));verify(staging)
    for path,record in ((destination,current),(legacy,retired)):
        if record and owned(path)['digest']!=record['digest']:raise ValueError('owned_installation_changed_before_migration')
    if backup:backup.mkdir(parents=True)
    moves=[];created_profiles=[];placed=False
    try:
        for record in (current,retired):
            if record and record.get('profile'):
                source,target=record['profile']
                if not target.exists():
                    target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target);created_profiles.append(target)
        for path,record,label in ((destination,current,'previous-skill'),(legacy,retired,'previous-legacy-skill')):
            if record:
                saved=backup/label;native_path(path).rename(native_path(saved));moves.append((saved,path))
                for key in ('record','launcher'):
                    old=record.get(key)
                    if old:
                        saved=backup/(label+'-'+old.name);native_path(old).rename(native_path(saved));moves.append((saved,old))
        native_path(staging).rename(native_path(destination));placed=True
        if verify(destination)!=digest:raise ValueError('installed_bundle_readback_changed')
    except BaseException:
        if placed and destination.exists():native_path(destination).rename(native_path(staging))
        for saved,original in reversed(moves):native_path(saved).rename(native_path(original))
        for profile in created_profiles:profile.unlink()
        raise
    result={'status':'installed','skill':str(display_destination),'skill_name':'ResearchPilot','invocation':'$researchpilot',
      'bundle_sha256':digest,'conversation':'current-codex-session','backup':str(display_backup) if display_backup else None,
      'retired_legacy_skill':bool(retired),'retired_legacy_registration':bool(retired and retired.get('record')),
      'model_calls':0,'platform_writes':0}
    if backup:(backup/'receipt.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',required=True);parser.add_argument('--destination',required=True)
    parser.add_argument('--expected-digest');parser.add_argument('--backup');parser.add_argument('--legacy-digest')
    print(json.dumps(install(**vars(parser.parse_args())),ensure_ascii=False))
