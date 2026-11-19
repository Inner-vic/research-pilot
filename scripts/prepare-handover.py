"""Export an allowlisted, credential-free product snapshot into a fresh directory."""
import argparse,hashlib,json,re,shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FILES=['README.md','VERSION','requirements-runtime.txt','.gitignore','.gitattributes','.github/workflows/source-checks.yml',
 '.agents/skills/researchpilot/SKILL.md','.agents/skills/researchpilot/agents/openai.yaml','docs/USER-GUIDE.zh-CN.md','docs/ARCHITECTURE.md','docs/CHANGELOG.md','docs/COMPARISON-WITH-BMAD-ML.zh-CN.md','docs/WORKFLOW-GUIDE.zh-CN.md','docs/WORKFLOW-AUDIT.json','docs/SDM-REUSE-PLAN.zh-CN.md','docs/SUBMISSION-GATE-VALIDATION.json','docs/CHAT-WORKBENCH-BINDING.zh-CN.md','docs/SDM21-EVOLUTION-AUDIT.zh-CN.md','docs/SDM21-EVOLUTION-AUDIT.json','docs/SDM-REFACTOR-PROGRESS.zh-CN.md','docs/SDM-GOVERNANCE-BATCH1-VALIDATION.json','scripts/audit-sdm21.py',
 'docs/delivery/INSTALLATION-MODES.md','scripts/build-native-skill.py',
 'scripts/tests/test_lineage_view.cjs','scripts/install-native-skill.py','scripts/setup.py','scripts/ensure-dashboard.py','scripts/prepare-handover.py']

FILES += ['docs/UNIFIED-WORKFLOW-VALIDATION.json','scripts/tests/test_acceptance_view.cjs',
 'docs/UNIFIED-WORKFLOW-RESULTS.zh-CN.md','docs/RELEASE-VERIFICATION.json','RELEASE-NOTES.md','requirements-dev.txt',
 'scripts/run-offline-controls.py','scripts/verify-handover.py','scripts/tests/test_dashboard_reconnect.cjs']

FILES += ['docs/README.md','docs/EXECUTION.md','docs/TERMINOLOGY.zh-CN.md','docs/RESEARCH-CONTRACT.md','docs/sdm/PLANNER-EXECUTOR.zh-CN.md','docs/SDM21-UNIFICATION-VALIDATION.json','docs/EVOLVED-WORKFLOW-VALIDATION.json','docs/CORE-LIFECYCLE-DOCS-VALIDATION.json']

FILES += ['examples/distal-outline/'+name for name in ('README.md','RUN-GUIDE.zh-CN.md','research-brief.json','case.json','provenance.json','verify_case.py','MANIFEST.json')]

FILES += ['docs/HANDOVER-READINESS.zh-CN.md','scripts/tests/test_installation.py','docs/SKILL-NAME-VALIDATION.json']
FILES += ['docs/PLANNER-CONTEXT-VALIDATION.json','docs/WORKING-SESSIONS-VALIDATION.json',
          'scripts/tests/test_session_binding.cjs']

FILES += ['docs/AUTONOMOUS-ITERATION-RESULTS.zh-CN.md','docs/AUTONOMOUS-ITERATION-VALIDATION.json']

FILES += ['scripts/tests/test_workbench.py']

FILES += ['docs/GENERIC-DELIVERY.zh-CN.md','docs/GENERIC-DELIVERY-VALIDATION.json']
FILES += ['examples/project-command-demo/'+name for name in ('README.md','researchpilot.project.json',
    'model_impl.py','experiment.json','project_data.py','check_inputs.py','train_model.py','evaluate_model.py',
    'prepare_example.py','engineering_provider.py','data.json','split-input.json')]

def portable_document(name,raw):
 # Documentation exports retain conclusions and evidence digests, not local capabilities/paths.
 text=raw.decode('utf-8')
 if name.endswith('.json'):
  obj=json.loads(text)
  def redact(value):
   if isinstance(value,dict):return {k:redact(v) for k,v in value.items()}
   if isinstance(value,list):return [redact(v) for v in value]
   if isinstance(value,str) and (re.search(r'[A-Za-z]:[\\/]',value) or '.research/' in value or '.maintenance/' in value or '#token=' in value):return '[private local reference retained by maintainer]'
   return value
  obj=redact(obj)
  if name=='docs/UNIFIED-WORKFLOW-VALIDATION.json' and 'publication' not in obj:
   obj['publication']={'form':'portable summary','original_record_sha256':hashlib.sha256(raw).hexdigest(),'raw_records':'Retained privately; digests identify unchanged originals.'}
  return (json.dumps(obj,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
 if name=='docs/UNIFIED-WORKFLOW-RESULTS.zh-CN.md':
  text=re.sub(r'\[([^\]]+)\]\(\.\./\.research/[^)]+\)',r'\1（原件私有留存，摘要见机器验收记录）',text)
  text=text.replace('`04c886fe-1b2b-4589-84b6-2ba427219abe`','已核验远端任务').replace('`637e0f18-a6de-44c1-95e6-8cc8131d8eae`','已核验远端任务')
  text=text.replace('本批未提交或推送Git。','源工作目录的Git索引保持；交付通过独立发布副本完成。')
  text=text.replace('详细摘要、哈希、原件路径及旧项目核对','详细摘要、哈希及旧项目核对').replace('和本机维护目录的`FINAL-ACCEPTANCE.json`','；原始验收记录由维护者私有留存，不包含在发布包中')
 return text.encode('utf-8')

def export(destination):
 dest=Path(destination).resolve()
 if dest.exists():raise ValueError('fresh_handover_destination_required')
 files=[ROOT/n for n in FILES]+[p for p in (ROOT/'skills').rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.pyo') and p.name!='.memlog.md' and not p.is_relative_to(ROOT/'skills/sdm-module/tests')]
 content={}
 for source in files:
  if source.is_symlink() or source.resolve()!=source or getattr(source.stat(),'st_file_attributes',0)&0x400:raise ValueError('linked_handover_source_rejected')
  raw=source.read_bytes();text=raw.decode('utf-8')
  if re.search(r'lpak_[A-Za-z0-9_-]{12,}|-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----\s*\n[A-Za-z0-9/+]{16,}',text):raise ValueError('credential_pattern_in_source:'+source.relative_to(ROOT).as_posix())
  name=source.relative_to(ROOT).as_posix()
  content[name]=portable_document(name,raw) if name.startswith('docs/') else raw
 content['AGENTS.md']=b'# ResearchPilot\n\nRead README.md, docs/EXECUTION.md and docs/GENERIC-DELIVERY.zh-CN.md for current delivery and limits. The current 13-activity lifecycle evolves from SDM21; its contracts, validators, evidence, authority and scientific decisions govern execution, review, recovery and UI together. Source is skills/researchpilot and skills/sdm-module. Read skills/sdm-module/sdm-lifecycle/references/sdm21-to-13-evolution.zh-CN.md for provenance, gates and qualification gaps. Prefer original rules before adding adapters; do not maintain parallel passing criteria. Activity associations never imply acceptance. Setup and offline checks never authorize training. Use the configured pinned platform capability and explicit real scope; the LPAI adapter requires its official capability. ML stays remote. Preserve frozen Context, Guards, tasks, unknown-request identities and original records. New fixes use new freezes. Do not resume old studies or timers without authorization. Do not commit credentials, machine configuration, SDK, data or model weights. Run scripts/verify-handover.py and the offline controls described in .gitlab-ci.yml. Record product changes in docs/CHANGELOG.md.\n'
 for name,raw in content.items():
  target=dest/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
 manifest={n:hashlib.sha256(raw).hexdigest() for n,raw in sorted(content.items())}
 (dest/'SOURCE-MANIFEST.json').write_text(json.dumps({'version':(ROOT/'VERSION').read_text().strip(),'files':manifest},indent=2),encoding='utf-8')
 return {'destination':str(dest),'files':len(content),'bytes':sum(map(len,content.values())),'personal_state_included':False}

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--destination',required=True);print(json.dumps(export(p.parse_args().destination)))
