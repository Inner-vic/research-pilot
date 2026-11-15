"""Portable process locks and durable JSON for ancillary review state."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import uuid


def native_path(path):
    """Use extended Windows IO paths without changing persisted identities."""
    path = Path(path)
    if os.name != 'nt':
        return path
    value = os.path.abspath(path)
    if not value.startswith('\\\\?\\'):
        value = '\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value
    return Path(value)


def read_json(path):
    def invalid(value):
        raise ValueError('nonfinite_review_json:' + value)
    return json.loads(native_path(path).read_bytes(), parse_constant=invalid)


def atomic_json(path, value):
    path = Path(path)
    native_path(path.parent).mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
    temporary = path.parent / ('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary = native_path(temporary)
    try:
        with temporary.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, native_path(path))
        if os.name != 'nt':
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        if temporary.exists():
            temporary.unlink()


@contextmanager
def locked(root, name='.review.lock', busy_reason='review_workflow_busy'):
    """OS-owned nonblocking lock; a terminated process releases it automatically."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if Path(name).name != name:
        raise ValueError('lock_filename_required')
    stream = (root / name).open('a+b')
    acquired = False
    try:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise ValueError(busy_reason) from error
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise ValueError(busy_reason) from error
        acquired = True
        yield
    finally:
        if acquired:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()
