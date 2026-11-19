"""Read-only validation and recovery for historical project entry installations.

New installs use one native ResearchPilot Skill in the Codex client. This legacy
maintenance tool cannot generate a launcher or install another conversation entry.
"""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import sys

module=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(module/'sdm-lifecycle/scripts'))
from review_storage import atomic_json,read_json,locked

BEGIN='<!-- BMAD-ML-ENTRY:BEGIN -->';END='<!-- BMAD-ML-ENTRY:END -->'
HEADER='module,skill,display-name,menu-code,description,action,args,phase,preceded-by,followed-by,required,output-location,outputs'


def sha(raw):return hashlib.sha256(raw).hexdigest()
def raw(path):return path.read_bytes() if path.exists() else b''


def write(path,body):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name('.'+path.name+'.entry.tmp');tmp.write_bytes(body);tmp.replace(path)




def managed(text):
    if text.count(BEGIN)!=text.count(END) or text.count(BEGIN)>1:raise ValueError('ambiguous_entry_agents_block')
    return text[text.index(BEGIN):text.index(END)+len(END)] if BEGIN in text else None




def validate(root):
    record=read_json(root/'_bmad/sdm-entry/installation.json')
    if record['project_root']!=str(root):raise ValueError('installed_entry_root_changed')
    for name,expected in record['files'].items():
        if sha((root/name).read_bytes())!=expected:raise ValueError('installed_entry_file_changed:'+name)
    if sha(managed((root/'AGENTS.md').read_text(encoding='utf-8')).encode())!=record['agents_block_sha256']:
        raise ValueError('installed_entry_agents_block_changed')
    help_rows=list(csv.DictReader(io.StringIO((root/'_bmad/_config/bmad-help.csv').read_text(encoding='utf-8'))))
    if [r for r in help_rows if r['skill']=='bmad-ml']!=[record['help_row']]:raise ValueError('installed_entry_help_row_changed')
    entry=root/'.agents/skills/bmad-ml'
    sys.path.insert(0,str(entry/'scripts'));from bmad_ml import registration
    registration(root)
    return {'status':'valid','mode':'research-conversation-entry','project_root':str(root),
        'digest':sha(json.dumps(record,sort_keys=True).encode()),'release':record['release'],
        'discovered_skill':'bmad-ml','full_governed_host_installed':False,'platform_writes':0}


def install(args):
    return {'status':'retired','reason':'legacy_entry_install_removed',
        'next':'Load the ResearchPilot Skill in Codex, select ResearchPilot through /, or use $researchpilot.',
        'model_calls':0,'platform_writes':0,'project_writes':0}


def recover(root):
    control=root/'_bmad/sdm-entry';journal=control/'install-journal.json'
    with locked(control,'.entry-install.lock','entry_install_already_owned'):
        receipt=read_json(journal)
        if receipt['project_root']!=str(root):raise ValueError('journal_project_binding_changed')
        if receipt['status']!='prepared':return {'status':'no-recovery-needed','platform_writes':0}
        # Check ALL targets before rolling any back. Preserve unrelated edits.
        for name,old in receipt['originals_hex'].items():
            path=(root/name).resolve()
            if not path.is_relative_to(root):raise ValueError('journal_target_outside_project')
            value=raw(path).hex() if path.exists() else None
            if value!=old and (not path.exists() or sha(raw(path))!=receipt['desired_sha256'][name]):
                raise ValueError('changed_target_needs_reconciliation:'+name)
        for name,old in receipt['originals_hex'].items():
            path=root/name
            if old is None:
                if path.exists():path.unlink()
            else:write(path,bytes.fromhex(old))
        receipt['status']='rolled-back';atomic_json(journal,receipt)
        return {'status':'recovered','action':'restored-original-entry-targets','platform_writes':0}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=('install','validate','recover'))
    parser.add_argument('--project-root',required=True);parser.add_argument('--codex-cli')
    parser.add_argument('--expected-digest',default='absent');parser.add_argument('--capabilities')
    args=parser.parse_args()
    try:
        root=Path(args.project_root).resolve()
        result=install(args) if args.command=='install' else recover(root) if args.command=='recover' else validate(root)
        print(json.dumps(result,ensure_ascii=False))
        if result.get('status')=='retired':raise SystemExit(2)
    except Exception as error:
        print(json.dumps({'status':'blocked','reason':str(error),'next':'Preserve the installation journal and reconcile owned targets before retry.'}));raise SystemExit(1)
