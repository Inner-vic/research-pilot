"""Export an allowlisted, credential-free product snapshot into a fresh directory."""
import argparse,hashlib,json,re,shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FILES=['README.md','VERSION','requirements-runtime.txt','.gitignore','.gitattributes','.gitlab-ci.yml',
 '.agents/skills/bmad-ml/SKILL.md','.agents/skills/bmad-ml/agents/openai.yaml','docs/USER-GUIDE.zh-CN.md','docs/ARCHITECTURE.md','docs/CHANGELOG.md','docs/COMPARISON-WITH-BMAD-ML.zh-CN.md',
 'docs/delivery/INSTALLATION-MODES.md','scripts/build-native-skill.py',
 'scripts/tests/test_lineage_view.cjs','scripts/install-native-skill.py','scripts/setup.py','scripts/ensure-dashboard.py','scripts/prepare-handover.py']

FILES += ['examples/distal-outline/'+name for name in ('README.md','RUN-GUIDE.zh-CN.md','research-brief.json','case.json','provenance.json','verify_case.py','MANIFEST.json')]

def export(destination):
 dest=Path(destination).resolve()
 if dest.exists():raise ValueError('fresh_handover_destination_required')
 files=[ROOT/n for n in FILES]+[p for p in (ROOT/'skills').rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.pyo') and p.name!='.memlog.md' and not p.is_relative_to(ROOT/'skills/sdm-module/tests')]
 content={}
 for source in files:
  if source.is_symlink() or source.resolve()!=source or getattr(source.stat(),'st_file_attributes',0)&0x400:raise ValueError('linked_handover_source_rejected')
  raw=source.read_bytes();text=raw.decode('utf-8')
  if re.search(r'lpak_[A-Za-z0-9_-]{12,}|-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----\s*\n[A-Za-z0-9/+]{16,}',text):raise ValueError('credential_pattern_in_source:'+source.relative_to(ROOT).as_posix())
  content[source.relative_to(ROOT).as_posix()]=raw
 content['AGENTS.md']=b"# ResearchPilot\n\nRead README.md and docs/ARCHITECTURE.md for the product. Source is skills/bmad-ml and skills/sdm-module; SDM is the internal core. Use the project Skill entry for guided onboarding. Setup never authorizes training. Use the installed OFFICIAL LPAI Skill and actual scope. Preserve active frozen runs, Context, task Guards and unknown effects; new fixes use new versions. ML runs remotely. Do not commit credentials, local state, data or model weights. Record product changes in docs/CHANGELOG.md. Web UI projects the original records and delegates controls; do not build a second experiment engine.\n"
 for name,raw in content.items():
  target=dest/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
 manifest={n:hashlib.sha256(raw).hexdigest() for n,raw in sorted(content.items())}
 (dest/'SOURCE-MANIFEST.json').write_text(json.dumps({'version':(ROOT/'VERSION').read_text().strip(),'files':manifest},indent=2),encoding='utf-8')
 return {'destination':str(dest),'files':len(content),'bytes':sum(map(len,content.values())),'personal_state_included':False}

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--destination',required=True);print(json.dumps(export(p.parse_args().destination)))
