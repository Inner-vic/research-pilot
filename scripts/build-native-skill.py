"""Package one self-contained bmad-ml Skill; no launcher or machine state."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import sys
import zipfile


ENTRY_FILES=('SKILL.md','agents/openai.yaml','scripts/bmad_ml.py')


def checked_source(path):
    path=Path(path).absolute();info=path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400 or path.resolve(strict=True)!=path:
        raise ValueError('linked_release_input_rejected')
    if not stat.S_ISREG(info.st_mode):raise ValueError('regular_release_input_required')
    return path


def build(source,destination,version):
    source=Path(source).resolve();destination=Path(destination).resolve()
    if destination.name!='bmad-ml' or destination.exists():
        raise ValueError('fresh_bmad_ml_destination_required')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',version):raise ValueError('portable_version_required')
    module=source/'skills/sdm-module';life=module/'sdm-lifecycle'
    sources=[(source/'skills/bmad-ml'/n,Path(n)) for n in ENTRY_FILES]
    sources += [(p,Path('references/sdm-module')/p.relative_to(module)) for p in
        [life/'SKILL.md',*sorted((life/'scripts').glob('*.py')),
         *sorted(p for p in (life/'references').rglob('*') if p.is_file()),
         module/'sdm-agent-research-planner/SKILL.md',
         module/'sdm-agent-evidence-review/SKILL.md']]
    contents={}
    for path,relative in sources:
        checked_source(path)
        raw=path.read_bytes();text=raw.decode('utf-8')
        if path.suffix=='.py':ast.parse(raw,filename=str(path))
        if re.search(r'lpak_[A-Za-z0-9_-]{12,}|-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----\s*\n[A-Za-z0-9/+]{16,}',text):
            raise ValueError('credential_pattern_in_release:'+relative.as_posix())
        if re.search(r'(?i)developer|/mnt/d/00_workspace|D:[/\\]00_workspace',text):
            raise ValueError('developer_binding_in_release:'+relative.as_posix())
        contents[relative.as_posix()]=raw
    archive=destination.parent/f'bmad-ml-{version}.zip'
    if archive.exists():raise ValueError('fresh_archive_required')
    destination.mkdir(parents=True)
    for name,raw in contents.items():
        target=destination/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    manifest={'schema':'bmad-ml-native-skill/v1','name':'bmad-ml','version':version,
        'entry':'SKILL.md','conversation':'current-codex-session',
        'files':{name:hashlib.sha256(raw).hexdigest() for name,raw in contents.items()},
        'launchers':False,'project_registration_required':False,'platform_credentials_included':False}
    (destination/'BUNDLE.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted(destination.rglob('*')):
            if path.is_file():zipped.write(path,Path('bmad-ml')/path.relative_to(destination))
    checksum=hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(checksum+'  '+archive.name+'\n')
    return {'skill':str(destination),'archive':str(archive),'sha256':checksum,
        'files':len(contents),'installed_entry_count':1,'model_calls':0,'platform_writes':0}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True);parser.add_argument('--destination',required=True)
    parser.add_argument('--version',required=True)
    print(json.dumps(build(**vars(parser.parse_args())),ensure_ascii=False))
