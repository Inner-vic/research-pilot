"""Generated-control process tree for explicit startup-fault fixtures only."""
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parent))
from review_storage import atomic_json


def main(mode,folder):
    folder=Path(folder)
    if mode=='branch':
        child=subprocess.Popen([sys.executable,'-I','-S','-X','utf8','-B',str(Path(__file__).resolve()),'leaf',str(folder)],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        atomic_json(folder/'probe-tree.json',{'branch_pid':os.getpid(),'leaf_pid':child.pid,'models_started':0})
    while True:time.sleep(1)


if __name__=='__main__':main(*sys.argv[1:])
