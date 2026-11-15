"""Windows launch contracts without running platform commands."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys
import io

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import process_visibility as visibility


def test_posix_options_do_not_change_launch_contract():
    with patch.object(visibility, 'os', SimpleNamespace(name='posix')):
        assert visibility.hidden_process_options() == {}


def test_windows_hides_at_creation_not_after_shell_start():
    class Startup:
        dwFlags = 0
        wShowWindow = 99
    fake = SimpleNamespace(STARTUPINFO=Startup, STARTF_USESHOWWINDOW=1, CREATE_NO_WINDOW=0x08000000)
    with patch.object(visibility, 'os', SimpleNamespace(name='nt')), patch.object(visibility, 'subprocess', fake):
        opts = visibility.hidden_process_options(additional_flags=512)
    assert opts['creationflags'] == 0x08000000 | 512
    assert opts['startupinfo'].dwFlags & 1
    assert opts['startupinfo'].wShowWindow == 0


def test_pythonw_missing_streams_get_local_evidence(tmp_path):
    with patch.object(sys, 'argv', ['entry', '--output', str(tmp_path)]), \
            patch.object(sys, 'stdout', None), patch.object(sys, 'stderr', None):
        root = visibility.ensure_background_stdio()
        try:
            print('background output')
            print('startup failure', file=sys.stderr)
        finally:
            sys.stdout.close()
            sys.stderr.close()
    assert 'background output' in next(root.glob('*.stdout.log')).read_text()
    assert 'startup failure' in next(root.glob('*.stderr.log')).read_text()


def test_redirected_streams_are_preserved():
    out, err = io.StringIO(), io.StringIO()
    with patch.object(sys, 'stdout', out), patch.object(sys, 'stderr', err):
        assert visibility.ensure_background_stdio() is None
        assert sys.stdout is out and sys.stderr is err


def test_all_direct_module_launches_have_explicit_visibility_policy():
    root = Path(visibility.__file__).parent
    for path in root.glob('*.py'):
        for call in ast.walk(ast.parse(path.read_bytes())):
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name) and call.func.value.id == 'subprocess'
                    and call.func.attr in {'run', 'Popen', 'check_call', 'check_output'}):
                assert (any(k.arg == 'creationflags' for k in call.keywords)
                        or any(k.arg is None and isinstance(k.value, ast.Call)
                               and getattr(k.value.func, 'id', '') == 'hidden_process_options'
                               for k in call.keywords)), f'{path.name}:{call.lineno}'
