"""Enrolled supervisor child: run exactly the original frozen event service."""
import sys
from pathlib import Path
from review_storage import read_json
from task_pipeline import digest

if __name__=='__main__':
    request=read_json(Path(sys.argv[1])/'request.json')
    cfg=read_json(request['config_path'])
    if digest(cfg)!=request['binding']['config_sha256']:raise ValueError('supervised_configuration_changed')
    sys.path.insert(0,cfg['runtime_root'])
    from research_event_service import main
    sys.argv=[str(Path(cfg['runtime_root'])/'research_event_service.py'),'--config',request['config_path']]
    main()
