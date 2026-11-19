"""Read-only Windows asset capability; no journal or workspace write support.

Open one component at a time relative to held NT directory handles. Do not
follow reparse points. Deny write/delete sharing on the asset root/children and
verify the actual file identity, link count, size and timestamps after reading.
Only local fixed drives are supported. POSIX retains its existing reader.

API contracts: Microsoft Learn NtCreateFile (winternl.h),
GetFileInformationByHandle and ReadFile (fileapi.h).
"""
import os
import re
import time
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _api():
    import ctypes as c
    from ctypes import wintypes as w
    class Unicode(c.Structure):
        _fields_ = [('Length', w.USHORT), ('MaximumLength', w.USHORT), ('Buffer', w.LPWSTR)]
    class Attributes(c.Structure):
        _fields_ = [('Length', w.ULONG), ('RootDirectory', w.HANDLE),
                    ('ObjectName', c.POINTER(Unicode)), ('Attributes', w.ULONG),
                    ('SecurityDescriptor', c.c_void_p), ('SecurityQualityOfService', c.c_void_p)]
    class Status(c.Structure):
        _fields_ = [('StatusOrPointer', c.c_void_p), ('Information', c.c_size_t)]
    class Info(c.Structure):
        _fields_ = [('attributes', w.DWORD), ('created', w.FILETIME),
                    ('accessed', w.FILETIME), ('written', w.FILETIME),
                    ('volume', w.DWORD), ('size_high', w.DWORD), ('size_low', w.DWORD),
                    ('links', w.DWORD), ('index_high', w.DWORD), ('index_low', w.DWORD)]
    nt = c.WinDLL('ntdll', use_last_error=True)
    kernel = c.WinDLL('kernel32', use_last_error=True)
    nt.NtCreateFile.argtypes = [c.POINTER(w.HANDLE), w.ULONG, c.POINTER(Attributes),
        c.POINTER(Status), c.c_void_p, w.ULONG, w.ULONG, w.ULONG, w.ULONG, c.c_void_p, w.ULONG]
    nt.NtCreateFile.restype = c.c_long
    nt.RtlNtStatusToDosError.argtypes = [c.c_long]; nt.RtlNtStatusToDosError.restype = w.ULONG
    kernel.CloseHandle.argtypes = [w.HANDLE]; kernel.CloseHandle.restype = w.BOOL
    kernel.GetFileInformationByHandle.argtypes = [w.HANDLE, c.POINTER(Info)]
    kernel.GetFileInformationByHandle.restype = w.BOOL
    kernel.GetFileType.argtypes = [w.HANDLE]; kernel.GetFileType.restype = w.DWORD
    kernel.ReadFile.argtypes = [w.HANDLE, c.c_void_p, w.DWORD, c.POINTER(w.DWORD), c.c_void_p]
    kernel.ReadFile.restype = w.BOOL
    kernel.GetDriveTypeW.argtypes = [w.LPCWSTR]; kernel.GetDriveTypeW.restype = w.UINT
    return c, w, Unicode, Attributes, Status, Info, nt, kernel


def _component(value):
    if (not isinstance(value, str) or not value or value in ('.', '..')
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', value) or value[-1:] in ('.', ' ')
            or re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', value, re.I)):
        raise OSError('unsafe Windows asset path component')
    return value


def _information(handle, directory):
    c, w, Unicode, Attributes, Status, Info, nt, kernel = _api()
    info = Info()
    if not kernel.GetFileInformationByHandle(handle, c.byref(info)):
        raise c.WinError(c.get_last_error())
    if (kernel.GetFileType(handle) != 1 or info.attributes & 0x400
            or bool(info.attributes & 0x10) != directory
            or (not directory and info.links != 1)):
        raise OSError('asset handle is a reparse point, wrong type or multiply linked file')
    return info


def _identity(info):
    return (info.attributes, info.volume, info.index_high, info.index_low, info.links,
            info.size_high, info.size_low, info.created.dwHighDateTime,
            info.created.dwLowDateTime, info.written.dwHighDateTime, info.written.dwLowDateTime)


def _open(name, parent, directory, sharing=1):
    c, w, Unicode, Attributes, Status, Info, nt, kernel = _api()
    buffer = c.create_unicode_buffer(name)
    length = len(name.encode('utf-16-le'))
    if length > 65532:
        raise OSError('Windows asset path too long')
    string = Unicode(length, length + 2, c.cast(buffer, w.LPWSTR))
    attrs = Attributes(c.sizeof(Attributes), parent, c.pointer(string), 0x40, None, None)
    handle = w.HANDLE(); status = Status()
    # FILE_OPEN only; never create. A type-neutral no-follow open lets us reject
    # a directory reparse point by handle, rather than following it to test type.
    access = 0x00100000 | 0x80 | (0x20 if directory else 1)
    options = 0x00200000 | 0x20 | (0 if directory else 0x40)
    for attempt in range(4):
        result = nt.NtCreateFile(c.byref(handle), access, c.byref(attrs), c.byref(status),
                                 None, 0, sharing, 1, options, None, 0)
        if result >= 0:
            break
        error = nt.RtlNtStatusToDosError(result)
        if error not in (32, 33) or attempt == 3:
            raise c.WinError(error)
        # Short metadata-only retry, with identical no-follow/share flags.
        # Never fall back to a pathname read or weaken a busy handle's access.
        time.sleep(0.01 * (2 ** attempt))
    try:
        info = _information(handle, directory)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    return handle, info


def read_regular(root, parts, maximum):
    """Return immutable bytes from a regular single-link file, or fail closed."""
    if os.name != 'nt':
        raise OSError('Windows asset capability requested on unsupported host')
    c, w, Unicode, Attributes, Status, Info, nt, kernel = _api()
    root_name = os.fspath(root)
    if root_name.startswith('\\\\?\\'):
        root_name = root_name[4:]
    path = Path(os.path.abspath(root_name))
    if not re.fullmatch(r'[A-Za-z]:', path.drive) or not path.is_absolute():
        raise OSError('only local drive paths supported for Windows assets')
    if kernel.GetDriveTypeW(path.anchor) != 3:
        raise OSError('only fixed local drives supported for Windows assets')
    relative = tuple(_component(part) for part in parts)
    if not relative:
        raise OSError('asset file component required')
    root_parts = tuple(_component(part) for part in path.parts[1:])
    if not root_parts:
        raise OSError('an asset root below the drive root is required')
    handles = []
    try:
        current, initial = _open('\\??\\' + path.anchor, None, True, sharing=7)
        handles.append(current)
        # Ancestors are metadata anchors only. Do not deny unrelated writes
        # or renames in a drive/project parent. Child opens use held directory
        # objects, not an absolute pathname traversal. The governed root and
        # everything below it retain strict no-write/no-delete sharing.
        for index, part in enumerate(root_parts):
            sharing = 1 if index == len(root_parts)-1 else 7
            current, info = _open(part, current, True, sharing=sharing)
            handles.append(current)
            if info.volume != initial.volume:
                raise OSError('asset directory crossed volume')
        for part in relative[:-1]:
            current, info = _open(part, current, True)
            handles.append(current)
            if info.volume != initial.volume:
                raise OSError('asset directory crossed volume')
        handle, before = _open(relative[-1], current, False)
        handles.append(handle)
        size = (before.size_high << 32) | before.size_low
        if before.volume != initial.volume or size > maximum:
            raise OSError('asset file crossed volume or exceeds size limit')
        data = bytearray()
        while len(data) < size:
            amount = min(1024 * 1024, size - len(data))
            buffer = c.create_string_buffer(amount); received = w.DWORD()
            if not kernel.ReadFile(handle, buffer, amount, c.byref(received), None):
                raise c.WinError(c.get_last_error())
            if not received.value:
                break
            data.extend(buffer.raw[:received.value])
        after = _information(handle, False)
        if len(data) != size or _identity(before) != _identity(after):
            raise OSError('asset changed while reading')
        return bytes(data)
    finally:
        for handle in reversed(handles):
            kernel.CloseHandle(handle)
