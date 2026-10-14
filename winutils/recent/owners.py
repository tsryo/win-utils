r"""Look up who owns a file, as DOMAIN\name.

pathlib's Path.owner() is not supported on Windows, so the owner is read from
the file's security descriptor through advapi32. Account names are cached by
SID, because resolving a domain account can take a round trip to a domain
controller.
"""

from __future__ import annotations

import os
import sys

UNKNOWN = "(unknown)"  # the owner could not be read

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    from ..common import to_extended

    _SE_FILE_OBJECT = 1
    _OWNER_SECURITY_INFORMATION = 0x1
    _ERROR_INSUFFICIENT_BUFFER = 122

    _PVOID_P = ctypes.POINTER(ctypes.c_void_p)

    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _GetNamedSecurityInfoW = _advapi32.GetNamedSecurityInfoW
    _GetNamedSecurityInfoW.argtypes = (
        wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
        _PVOID_P, _PVOID_P, _PVOID_P, _PVOID_P, _PVOID_P,
    )
    _GetNamedSecurityInfoW.restype = wintypes.DWORD  # an error code, 0 on success

    _ConvertSidToStringSidW = _advapi32.ConvertSidToStringSidW
    _ConvertSidToStringSidW.argtypes = (ctypes.c_void_p, _PVOID_P)
    _ConvertSidToStringSidW.restype = wintypes.BOOL

    _LookupAccountSidW = _advapi32.LookupAccountSidW
    _LookupAccountSidW.argtypes = (
        wintypes.LPCWSTR, ctypes.c_void_p,
        wintypes.LPWSTR, wintypes.LPDWORD,
        wintypes.LPWSTR, wintypes.LPDWORD,
        ctypes.POINTER(ctypes.c_int),
    )
    _LookupAccountSidW.restype = wintypes.BOOL

    _LocalFree = _kernel32.LocalFree
    _LocalFree.argtypes = (ctypes.c_void_p,)
    _LocalFree.restype = ctypes.c_void_p

    _names: dict[str, str] = {}  # SID string -> account name

    def _sid_string(sid: ctypes.c_void_p) -> str:
        buffer = ctypes.c_void_p()
        if not _ConvertSidToStringSidW(sid, ctypes.byref(buffer)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.wstring_at(buffer.value)
        finally:
            _LocalFree(buffer)

    def _account_name(sid: ctypes.c_void_p) -> str | None:
        name_size = wintypes.DWORD(256)
        domain_size = wintypes.DWORD(256)
        use = ctypes.c_int()
        for _ in range(2):  # retry once if the buffers were too small
            name = ctypes.create_unicode_buffer(name_size.value)
            domain = ctypes.create_unicode_buffer(domain_size.value)
            if _LookupAccountSidW(
                None, sid, name, ctypes.byref(name_size), domain, ctypes.byref(domain_size), ctypes.byref(use)
            ):
                return f"{domain.value}\\{name.value}" if domain.value else name.value
            if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER:
                break
        return None

    def file_owner(path: str) -> str:
        r"""Return the owner of path as DOMAIN\name.

        Returns the SID string if the account no longer exists, and "(unknown)"
        if the owner can't be read.
        """
        owner = ctypes.c_void_p()  # points into descriptor, freed with it
        descriptor = ctypes.c_void_p()
        error = _GetNamedSecurityInfoW(
            to_extended(path),
            _SE_FILE_OBJECT,
            _OWNER_SECURITY_INFORMATION,
            ctypes.byref(owner),
            None,
            None,
            None,
            ctypes.byref(descriptor),
        )
        if error:
            return UNKNOWN
        try:
            if not owner.value:
                return UNKNOWN
            sid = _sid_string(owner)
            if sid not in _names:
                _names[sid] = _account_name(owner) or sid
            return _names[sid]
        except OSError:
            return UNKNOWN
        finally:
            _LocalFree(descriptor)

else:
    import pwd

    def file_owner(path: str) -> str:
        """Return the name of the user that owns path, or "(unknown)" if it can't be read."""
        try:
            uid = os.stat(path, follow_symlinks=False).st_uid
        except OSError:
            return UNKNOWN
        try:
            return pwd.getpwuid(uid).pw_name
        except KeyError:
            return str(uid)
