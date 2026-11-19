"""Compatibility entry for the same bundled read-only workbench startup."""
import argparse,json,sys
from pathlib import Path
sys.dont_write_bytecode=True

def ensure(project,skill,python=None,port=8792):
 skill=Path(skill).resolve();scripts=skill/'references/sdm-module/sdm-lifecycle/scripts'
 if not scripts.is_dir():scripts=skill.parent/'sdm-module/sdm-lifecycle/scripts'
 sys.path.insert(0,str(scripts))
 from research_workbench import ensure as bundled_ensure
 return bundled_ensure(project,skill,python,port)

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project-root',required=True);p.add_argument('--skill',required=True);p.add_argument('--python');p.add_argument('--port',type=int,default=8792)
 args=vars(p.parse_args());args['project']=args.pop('project_root');print(json.dumps(ensure(**args),ensure_ascii=False))
