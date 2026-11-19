"""Verify exact exported source bytes and portable publication boundaries."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def verify(root):
    root=Path(root).resolve();manifest=json.loads((root/'SOURCE-MANIFEST.json').read_bytes())
    required={'README.md','VERSION','RELEASE-NOTES.md','requirements-dev.txt',
        'scripts/run-offline-controls.py','scripts/tests/test_dashboard_reconnect.cjs',
        'docs/UNIFIED-WORKFLOW-RESULTS.zh-CN.md','docs/UNIFIED-WORKFLOW-VALIDATION.json',
        'docs/RELEASE-VERIFICATION.json'}
    if not required<=set(manifest['files']):raise ValueError('release_dependency_closure_incomplete')
    for name,wanted in manifest['files'].items():
        rel=Path(name)
        if rel.is_absolute() or '..' in rel.parts or ':' in name:raise ValueError('unsafe_source_manifest_path')
        if set(rel.parts)&{'.research','.runtime','.maintenance','.bmad-ml','__pycache__'}:
            raise ValueError('private_state_in_source_manifest')
        path=root/rel
        if path.is_symlink() or path.resolve()!=path:raise ValueError('linked_release_file')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=wanted:raise ValueError('release_source_digest_mismatch:'+name)
        if re.search(rb'lpak_[A-Za-z0-9_-]{12,}|-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----\s*\n[A-Za-z0-9/+]{16,}',raw):
            raise ValueError('credential_pattern_in_release:'+name)
    for name in ('docs/UNIFIED-WORKFLOW-RESULTS.zh-CN.md','docs/UNIFIED-WORKFLOW-VALIDATION.json','docs/RELEASE-VERIFICATION.json'):
        text=(root/name).read_text(encoding='utf-8')
        if re.search(r'[A-Za-z]:[\\/]|\.research/|\.maintenance/|#token=',text):
            raise ValueError('machine_or_live_evidence_reference_in_portable_report:'+name)
    return {'status':'verified','files':len(manifest['files']),'private_runtime_included':False,
            'raw_research_records_included':False,'model_or_platform_calls':0}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',default='.')
    print(json.dumps(verify(parser.parse_args().root)))
