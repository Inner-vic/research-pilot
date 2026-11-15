"""Job-enrolled SDK task helper; invoked only by model_process_guard."""
from pathlib import Path
import sys
from review_storage import read_json, atomic_json
from model_containment import assert_current_member
from research_event_service import Inbox, sdk_execute


if __name__ == '__main__':
    root = Path(sys.argv[1]); saved = read_json(root/'request.json')
    joined = read_json(root/'guard-joined.json')
    if joined['binding'] != saved['binding']: raise ValueError('execution_helper_binding_changed')
    assert_current_member(joined['job_name'])
    inbox = Inbox(saved['config'])
    result = sdk_execute(inbox, saved['row'], root)
    atomic_json(root/'result.json', {'job_id':saved['row']['id'], **result})
