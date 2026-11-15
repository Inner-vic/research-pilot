"""Bounded SDK child entrypoint. Parent captures stdout; no credential diagnostics."""
import argparse
import contextlib
import io
import json
import hashlib
from pathlib import Path

from lpai_manager_backend import OfficialBackend
from review_storage import read_json
from review_storage import atomic_json
from submission_guard import PendingApproval


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    args = parser.parse_args()
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            spec = read_json(args.input)
            backend = OfficialBackend(spec['config'], worker_mode=True)
            arguments = spec['arguments']
            if spec['operation'] == 'read':
                value = backend.read(**arguments)
            elif spec['operation']=='validate-task-data':
                value=backend.validate_task_data(**arguments)
            elif spec['operation'] == 'create-candidates':
                value = backend.create_candidates(**arguments)
            elif spec['operation'] == 'task-candidates':
                value = backend.task_candidates(**arguments)
            elif spec['operation'] == 'owned-task-page':
                value=backend.owned_task_page(**arguments)
            elif spec['operation'] == 'compile-component':
                value = backend.compile_component(**arguments)
            elif spec['operation'] == 'mutate' and (arguments['request']['action'] in ('task.submit', 'task.stop')
                    or (arguments['request']['action'] in ('devspace.start', 'devspace.stop') and spec['config'].get('devspace_transport') == 'official-api')
                    or (arguments['request']['action'] == 'component.create' and arguments['request'].get('transport') == 'official-sdk')):
                value = backend.mutate(**arguments)
            else:
                raise ValueError('sdk_worker_operation_not_allowed')
        # ASCII JSON preserves all Unicode through escapes and works even when
        # Windows gives this isolated child a legacy console encoding.
        safe={'uid':value.get('uid')} if isinstance(value,dict) else value if isinstance(value,str) else None
        atomic_json(Path(args.input).with_suffix('.result.json'),{'status':'ok','operation':spec['operation'],
            'input_sha256':hashlib.sha256(Path(args.input).read_bytes()).hexdigest(),
            'mutation_identity':safe if spec['operation']=='mutate' else None})
        print(json.dumps({'status': 'ok', 'data': value}, ensure_ascii=True))
        return 0
    except PendingApproval as error:
        print(json.dumps(error.approval)); return 0
    except Exception as error:
        import re
        match=re.search(r"Invalid status code '(\d+)'",str(error))
        status=getattr(error,'status_code',None) or getattr(getattr(error,'response',None),'status_code',None) or (int(match[1]) if match else None)
        if type(status) is not int or not 100<=status<=599:status=None
        failure={'status':'error','error':type(error).__name__,'provider_status_code':status,
            'input_sha256':hashlib.sha256(Path(args.input).read_bytes()).hexdigest(),'submission_outcome_known':False}
        atomic_json(Path(args.input).with_suffix('.result.json'),failure)
        print(json.dumps(failure)); return 1


if __name__ == '__main__': raise SystemExit(main())
