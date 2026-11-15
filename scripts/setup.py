"""One-time/upgrade setup for a cloned ResearchPilot source. No training or login changes."""
import argparse,hashlib,importlib.util,json,os,subprocess,sys,sysconfig,time,uuid,venv
from pathlib import Path

sys.dont_write_bytecode=True

ROOT=Path(__file__).resolve().parents[1]

def load(name,file):
 spec=importlib.util.spec_from_file_location(name,file);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def write(path,value):
 path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_suffix('.'+uuid.uuid4().hex+'.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)

def setup(project_root=None,skill_destination=None,sdk_root=None,skip_model_sdk=False,no_dashboard=False,no_install=False):
 print('准备 ResearchPilot：构建并核验 Skill…',file=sys.stderr,flush=True)
 if sys.version_info<(3,11):raise ValueError('Python 3.11+ required (core uses stdlib tomllib)')
 project=Path(project_root or ROOT).resolve();runtime=project/'.runtime';runtime.mkdir(parents=True,exist_ok=True)
 version=(ROOT/'VERSION').read_text(encoding='utf-8').strip();builder=load('bmad_build',ROOT/'scripts/build-native-skill.py');installer=load('bmad_install',ROOT/'scripts/install-native-skill.py')
 destination=Path(skill_destination).expanduser().absolute() if skill_destination else (Path(os.environ['CODEX_HOME'])/'skills/bmad-ml' if os.environ.get('CODEX_HOME') else Path.home()/'.agents/skills/bmad-ml')
 build=runtime/'packages'/uuid.uuid4().hex/'bmad-ml';builder.build(ROOT,build,version);wanted=installer.verify(build)
 if no_install:skill=build;installation='isolated-bundle'
 elif destination.exists() and installer.verify(destination)==wanted:skill=destination;installation='already-installed'
 else:
  previous=installer.verify(destination) if destination.exists() else 'absent'
  backup=runtime/'skill-backups'/uuid.uuid4().hex if destination.exists() else None
  installer.install(build,destination,previous,backup);skill=destination;installation='installed'
 python=Path(sys.executable);sdk=None;model_status='not-installed'
 if sdk_root:
  sdk=Path(sdk_root).resolve()
  if not (sdk/'openai_codex/client.py').is_file():raise ValueError('existing_official_codex_sdk_required')
  from importlib.metadata import distributions
  versions=[d.version for d in distributions(path=[str(sdk)]) if d.metadata.get('Name','').replace('_','-').lower()=='openai-codex']
  if versions!=['0.163.0']:raise ValueError('official_codex_sdk_0.163.0_required')
  model_status='existing-sdk'
 elif not skip_model_sdk:
  print('准备官方 Codex SDK；已有登录保持不变…',file=sys.stderr,flush=True)
  env=runtime/'bmad-ml-python'
  if not (env/'pyvenv.cfg').is_file():venv.EnvBuilder(with_pip=True).create(env)
  python=env/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
  check=subprocess.run([str(python),'-c',"import importlib.metadata as m;print(m.version('openai-codex'))"],capture_output=True,text=True)
  if check.returncode or check.stdout.strip()!='0.163.0':
   install=subprocess.run([str(python),'-m','pip','install','--disable-pip-version-check','--retries','1','--timeout','20','-r',str(ROOT/'requirements-runtime.txt')],capture_output=True,text=True,timeout=240)
   if install.returncode:
    import re
    safe=re.sub(r'https?://[^\s/]*@','https://[redacted]@',install.stderr[-6000:])
    (runtime/'sdk-install-error.log').write_text(safe,encoding='utf-8')
    raise ValueError('codex_sdk_install_failed: inspect .runtime/sdk-install-error.log; configure approved package index/network and retry setup')
  detected=subprocess.check_output([str(python),'-c',"import sysconfig;print(sysconfig.get_paths()['purelib'])"],text=True).strip();sdk=Path(detected);model_status='sdk-ready-auth-check-on-use'
 environment={'schema':'bmad-ml-environment/v1','python':str(python),'codex_sdk_root':str(sdk) if sdk else None,'codex_sdk_version':'0.163.0' if sdk else None,
  'model_status':model_status,'skill':str(skill),'skill_version':version,'bundle_sha256':wanted,'source_root':str(ROOT),
  'lpai':'configure official Skill/SDK/auth and actual namespace/resource scope separately','research_started':False,'model_calls':0,'platform_writes':0}
 write(project/'.bmad-ml/ENVIRONMENT.json',environment)
 dashboard=None
 if not no_dashboard:dashboard=load('bmad_dashboard',ROOT/'scripts/ensure-dashboard.py').ensure(project,skill,python)
 result={'installation':installation,'environment':environment,'dashboard':dashboard,'next':'在 Codex 当前项目输入 /，选择 ResearchPilot 并发送；根据引导配置 LPAI，确认方案后才开始研究。'}
 write(project/'.bmad-ml/installation.json',result);return result

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project-root');p.add_argument('--skill-destination');p.add_argument('--sdk-root');p.add_argument('--skip-model-sdk',action='store_true');p.add_argument('--no-dashboard',action='store_true');p.add_argument('--no-install',action='store_true')
 print(json.dumps(setup(**vars(p.parse_args())),ensure_ascii=False))
