"""Copy explicit adapter files into a fresh sealed project-owned driver bundle."""
import argparse
import json
from pathlib import Path

from project_driver import bind_bundle, file_digest, relative_file, resolve


def package(source,entry,files,output):
    source=Path(source).resolve();output=Path(output).absolute()
    names=[relative_file(n) for n in files]
    relative_file(entry)
    if '/' in entry or entry not in names:raise ValueError('top_level_entry_must_be_selected')
    if not names or len(names)>64 or len({n.casefold() for n in names})!=len(names):raise ValueError('unique_bounded_adapter_files_required')
    if output.exists():raise ValueError('fresh_adapter_package_required')
    payload={};origin={};total=0
    for name in names:
        path=source/name
        if not path.resolve().is_relative_to(source):raise ValueError('project_source_file_escape')
        if any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in [path,*path.parents[:len(Path(name).parts)]]):raise ValueError('project_source_links_not_allowed')
        if not path.is_file():raise ValueError('selected_adapter_file_missing')
        size=path.stat().st_size;total+=size
        if size>10*1024*1024 or total>32*1024*1024:raise ValueError('bounded_adapter_bytes_required')
        payload[name]=path.read_bytes();origin[name]=file_digest(path)
    output.mkdir(parents=True,exist_ok=False)
    bundle=output/'adapter';bundle.mkdir()
    for name,raw in payload.items():
        path=bundle/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    binding=bind_bundle(bundle/entry)
    for name,expected in origin.items():
        if binding['files'][name]!=expected:raise ValueError('adapter_source_changed_during_packaging')
    resolve({'project_driver':binding},output)
    result={'schema':'sdm-project-driver-package/v1','project_driver':binding,
        'source_directory':str(source),'source_files':origin,'source_modified':False,
        'project_execution_performed':False,'platform_install_updated':False,
        'compatibility':'Use a fresh configuration and run; do not alter prior bindings.'}
    (output/'adapter-binding.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',required=True);p.add_argument('--entry',required=True)
    p.add_argument('--file',action='append',required=True,dest='files');p.add_argument('--output',required=True)
    print(json.dumps(package(**vars(p.parse_args())),ensure_ascii=False))

if __name__=='__main__':main()
