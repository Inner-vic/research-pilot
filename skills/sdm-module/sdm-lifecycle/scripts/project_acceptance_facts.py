"""Project byte/evaluation facts consumed by the original shared SDM gate."""
import hashlib,json
from component_research import reference
from component_payload import executed_request_files
from research_project import snapshot
from review_storage import read_json

def payload(value,acceptance,initial):
    project,hashes,base=snapshot(value['workload']['project_contract'])
    actual=read_json(reference(acceptance['manager_plan']))
    files,_=executed_request_files(actual['request'],acceptance['payload_manifest']['code_sha256'])
    identities=acceptance['payload_manifest']['identities']
    metadata=json.loads(files['_researchpilot_project.json'])
    if (metadata.get('project')!=project or metadata.get('comparison_identity')!=value['comparison_identity']
            or metadata.get('parameters_file')!=project['parameters_file']):raise ValueError('actual_project_contract_or_entrypoints_changed')
    expected_names=set(project['source_files'])|{'_researchpilot_runner.py','_researchpilot_preflight.py','_researchpilot_project.json'}
    if set(files)!=expected_names:raise ValueError('exact_project_payload_file_set_required')
    if metadata.get('source_sha256')!={n:h for n,h in identities.items() if n!='_researchpilot_project.json'}:
        raise ValueError('actual_project_source_manifest_changed')
    if any(identities.get(n)!=h for n,h in value['protected_runtime_sha256'].items()):
        raise ValueError('protected_project_runtime_changed')
    for name in project['source_files']:
        if name not in project['editable_files'] or initial and name!=project['parameters_file']:
            if identities[name]!=hashes[name]:raise ValueError('protected_or_initial_project_source_changed:'+name)
