"""Windowless child processes on Windows; preserve POSIX launch semantics."""
import os
import subprocess
import sys
from pathlib import Path


def hidden_process_options(additional_flags=0):
    if os.name != 'nt':
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    return {'creationflags': subprocess.CREATE_NO_WINDOW | additional_flags, 'startupinfo': startup}


def ensure_background_stdio():
    """pythonw has no default streams: preserve errors before parsing inputs."""
    if sys.stdout is not None and sys.stderr is not None:
        return None
    root = Path.cwd() / '_bmad-output' / 'background-logs'
    for flag in ('--output', '--project'):
        if flag in sys.argv and sys.argv.index(flag) + 1 < len(sys.argv):
            value = Path(sys.argv[sys.argv.index(flag) + 1]).absolute()
            root = (value if flag == '--output' else value.parent) / 'background-logs'
            break
    root.mkdir(parents=True, exist_ok=True)
    for stream in ('stdout', 'stderr'):
        if getattr(sys, stream) is None:
            setattr(sys, stream, (root / f'{os.getpid()}.{stream}.log').open('a', encoding='utf-8', buffering=1))
    return root
