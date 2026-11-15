"""Pre-enrolled one-source transport, no model or platform task dispatch."""
from pathlib import Path
import subprocess
import sys
from review_storage import read_json
from model_containment import assert_current_member

if __name__ == '__main__':
    folder = Path(sys.argv[1]); saved = read_json(folder/'request.json')
    joined = read_json(folder/'guard-joined.json')
    if joined['binding'] != saved['binding']: raise ValueError('source_helper_binding_changed')
    assert_current_member(joined['job_name'])
    raise SystemExit(subprocess.call(saved['argv'], stdin=subprocess.DEVNULL))
