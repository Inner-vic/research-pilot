"""Package one self-contained ResearchPilot Skill; no launcher or machine state."""
import argparse
import importlib.util
import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import sys
import subprocess
import zipfile


_storage_spec=importlib.util.spec_from_file_location('researchpilot_install_storage',
 Path(__file__).resolve().parents[1]/'skills/sdm-module/sdm-lifecycle/scripts/review_storage.py')
_storage=importlib.util.module_from_spec(_storage_spec);_storage_spec.loader.exec_module(_storage)
native_path=_storage.native_path

ENTRY_FILES=('SKILL.md','agents/openai.yaml','scripts/researchpilot.py')


def checked_source(path):
    path=Path(path).absolute();info=path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400 or path.resolve(strict=True)!=path:
        raise ValueError('linked_release_input_rejected')
    if not stat.S_ISREG(info.st_mode):raise ValueError('regular_release_input_required')
    return path


def build(source,destination,version):
    source=Path(source).resolve();destination=Path(destination).resolve()
    if destination.name!='researchpilot' or destination.exists():
        raise ValueError('fresh_researchpilot_destination_required')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',version):raise ValueError('portable_version_required')
    module=source/'skills/sdm-module';life=module/'sdm-lifecycle'
    sources=[(source/'skills/researchpilot'/n,Path(n)) for n in ENTRY_FILES]
    sources += [(p,Path('references/sdm-module')/p.relative_to(module)) for p in
        [life/'SKILL.md',*sorted((life/'scripts').glob('*.py')),
         *sorted(p for p in (life/'references').rglob('*') if p.is_file()),
         module/'sdm-agent-research-planner/SKILL.md',
         module/'sdm-agent-evidence-review/SKILL.md']]
    # Resolve with this exact source interpreter boundary, not another already
    # imported lifecycle. No models/platforms and no generated policy copy.
    code=('import sys,json,hashlib;sys.path.insert(0,sys.argv[1]);'
          'from lifecycle_rules import asset_inputs;bundle,files=asset_inputs();'
          'print(json.dumps({str(p):hashlib.sha256(raw).hexdigest() for p,raw in files.items()}))')
    resolved=subprocess.run([sys.executable,'-I','-S','-B','-c',code,str(life/'scripts')],
        capture_output=True,text=True,check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    normative=json.loads(resolved.stdout)
    sources += [(Path(path),Path('references/sdm-module')/Path(path).relative_to(module)) for path in normative]
    contents={}
    for path,relative in sources:
        checked_source(path)
        raw=path.read_bytes();text=raw.decode('utf-8')
        if str(path) in normative and hashlib.sha256(raw).hexdigest()!=normative[str(path)]:
            raise ValueError('normative_asset_changed_during_build:'+relative.as_posix())
        if path.suffix=='.py':ast.parse(raw,filename=str(path))
        if re.search(r'lpak_[A-Za-z0-9_-]{12,}|-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----\s*\n[A-Za-z0-9/+]{16,}',text):
            raise ValueError('credential_pattern_in_release:'+relative.as_posix())
        if re.search(r'(?i)developer|/mnt/d/00_workspace|D:[/\\]00_workspace',text):
            raise ValueError('developer_binding_in_release:'+relative.as_posix())
        contents[relative.as_posix()]=raw
    archive=destination.parent/f'researchpilot-{version}.zip'
    if archive.exists():raise ValueError('fresh_archive_required')
    io_destination=native_path(destination)
    io_destination.mkdir(parents=True)
    for name,raw in contents.items():
        target=io_destination/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    manifest={'schema':'researchpilot-native-skill/v1','name':'researchpilot','version':version,
        'entry':'SKILL.md','conversation':'current-codex-session',
        'files':{name:hashlib.sha256(raw).hexdigest() for name,raw in contents.items()},
        'launchers':False,'project_registration_required':False,'platform_credentials_included':False}
    (io_destination/'BUNDLE.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    with zipfile.ZipFile(native_path(archive),'w',zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted(io_destination.rglob('*')):
            if path.is_file():zipped.write(path,Path('researchpilot')/path.relative_to(io_destination))
    checksum=hashlib.sha256(native_path(archive).read_bytes()).hexdigest()
    native_path(archive.with_suffix('.zip.sha256')).write_text(checksum+'  '+archive.name+'\n')
    return {'skill':str(destination),'archive':str(archive),'sha256':checksum,
        'files':len(contents),'installed_entry_count':1,'model_calls':0,'platform_writes':0}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True);parser.add_argument('--destination',required=True)
    parser.add_argument('--version',required=True)
    print(json.dumps(build(**vars(parser.parse_args())),ensure_ascii=False))
