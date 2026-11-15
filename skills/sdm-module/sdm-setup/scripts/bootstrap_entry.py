"""Retired launcher: BMAD ML is selected inside the Codex client's Skill picker."""
import argparse
import json
from pathlib import Path


def bootstrap(args):
    print(json.dumps({'status':'retired','reason':'cmd_bootstrap_removed',
        'next':'In the Codex client type /, select BMAD ML and send the guide prompt or your research task.',
        'model_calls':0,'platform_writes':0,'project_writes':0}));return 2


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root',type=Path,default=Path(__file__).resolve().parents[4])
    parser.add_argument('--codex-cli');parser.add_argument('--capabilities')
    parser.add_argument('--setup-only',action='store_true',help='Retired compatibility flag; no installation or model launch occurs.')
    args=parser.parse_args()
    try:raise SystemExit(bootstrap(args))
    except (ValueError,OSError) as error:
        print(json.dumps({'status':'blocked','reason':str(error),'next':'Preserve the original registration and installation journal; do not overwrite or start a second study.'}));raise SystemExit(1)
