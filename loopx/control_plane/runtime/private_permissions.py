"""Platform permissions for caller-owned private local IO; no authority decisions."""
from __future__ import annotations

import os
from pathlib import Path
import stat


def protect_private_path(path: Path, *, directory: bool = False) -> None:
    """Restrict an existing private leaf to its current account and OS admins.

    The caller owns the namespace/locking. Reparse points are never repaired.
    Windows uses a protected DACL, not chmod's emulated POSIX write flag.
    """
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400
            or (not stat.S_ISDIR(info.st_mode) if directory else not stat.S_ISREG(info.st_mode))):
        raise OSError("private IO path is not a regular owned leaf")
    if os.name != "nt":
        os.chmod(path, 0o700 if directory else 0o600)
        return
    _protect_windows_path(path, directory=directory)


def _protect_windows_path(path: Path, *, directory: bool) -> None:
    # Native APIs are loaded only on Windows. No subprocess, external package,
    # ambient inherited ACE or caller-supplied principal is trusted here.
    import ctypes as ct
    from ctypes import wintypes as w

    adv = ct.WinDLL("advapi32", use_last_error=True)
    kernel = ct.WinDLL("kernel32", use_last_error=True)
    pointer = ct.c_void_p
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.LocalFree.argtypes = [pointer]
    kernel.LocalFree.restype = pointer
    adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ct.POINTER(w.HANDLE)]
    adv.GetTokenInformation.argtypes = [w.HANDLE, ct.c_int, pointer, w.DWORD, ct.POINTER(w.DWORD)]
    adv.ConvertSidToStringSidW.argtypes = [pointer, ct.POINTER(w.LPWSTR)]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [w.LPCWSTR, w.DWORD, ct.POINTER(pointer), ct.POINTER(w.DWORD)]
    adv.GetSecurityDescriptorDacl.argtypes = [pointer, ct.POINTER(w.BOOL), ct.POINTER(pointer), ct.POINTER(w.BOOL)]
    adv.SetNamedSecurityInfoW.argtypes = [w.LPWSTR, ct.c_int, w.DWORD, pointer, pointer, pointer, pointer]
    adv.SetNamedSecurityInfoW.restype = w.DWORD
    token = w.HANDLE()
    sd = pointer()
    sid_text = w.LPWSTR()
    try:
        if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 0x8, ct.byref(token)):
            raise ct.WinError(ct.get_last_error())
        size = w.DWORD()
        adv.GetTokenInformation(token, 1, None, 0, ct.byref(size))  # TokenUser
        if not size.value:
            raise ct.WinError(ct.get_last_error())
        user = ct.create_string_buffer(size.value)
        if not adv.GetTokenInformation(token, 1, user, size, ct.byref(size)):
            raise ct.WinError(ct.get_last_error())
        sid = ct.cast(user, ct.POINTER(pointer))[0]
        if not adv.ConvertSidToStringSidW(sid, ct.byref(sid_text)):
            raise ct.WinError(ct.get_last_error())
        flags = "OICI" if directory else ""
        descriptor = f"D:P(A;{flags};FA;;;{sid_text.value})(A;{flags};FA;;;SY)(A;{flags};FA;;;BA)"
        if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(descriptor, 1, ct.byref(sd), None):
            raise ct.WinError(ct.get_last_error())
        present, defaulted = w.BOOL(), w.BOOL()
        dacl = pointer()
        if not adv.GetSecurityDescriptorDacl(sd, ct.byref(present), ct.byref(dacl), ct.byref(defaulted)) or not present.value or not dacl.value:
            raise OSError("private IO DACL unavailable")
        # SE_FILE_OBJECT; DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION.
        error = adv.SetNamedSecurityInfoW(str(path), 1, 0x80000004, None, None, dacl, None)
        if error:
            raise ct.WinError(error)
    finally:
        if sid_text:
            kernel.LocalFree(ct.cast(sid_text, pointer))
        if sd:
            kernel.LocalFree(sd)
        if token:
            kernel.CloseHandle(token)


def require_private_path(path: Path, *, directory: bool = False, descriptor: int | None = None) -> None:
    """Read OS access facts without repairing an untrusted caller's file."""
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400
            or (not stat.S_ISDIR(info.st_mode) if directory else not stat.S_ISREG(info.st_mode))):
        raise OSError("private IO path is not a regular owned leaf")
    if descriptor is not None:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise OSError("private IO path changed after open")
        info = opened
    if os.name != "nt":
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise OSError("private IO permissions are invalid")
        return
    _require_windows_private_path(path, descriptor=descriptor)


def _require_windows_private_path(path: Path, *, descriptor: int | None) -> None:
    import ctypes as ct
    from ctypes import wintypes as w
    import msvcrt

    adv = ct.WinDLL("advapi32", use_last_error=True)
    kernel = ct.WinDLL("kernel32", use_last_error=True)
    pointer = ct.c_void_p
    out = ct.POINTER(pointer)
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.LocalFree.argtypes = [pointer]
    kernel.LocalFree.restype = pointer
    adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ct.POINTER(w.HANDLE)]
    adv.GetTokenInformation.argtypes = [w.HANDLE, ct.c_int, pointer, w.DWORD, ct.POINTER(w.DWORD)]
    adv.ConvertSidToStringSidW.argtypes = [pointer, ct.POINTER(w.LPWSTR)]
    adv.GetNamedSecurityInfoW.argtypes = [w.LPCWSTR, ct.c_int, w.DWORD, out, out, out, out, out]
    adv.GetNamedSecurityInfoW.restype = w.DWORD
    adv.GetSecurityInfo.argtypes = [w.HANDLE, ct.c_int, w.DWORD, out, out, out, out, out]
    adv.GetSecurityInfo.restype = w.DWORD
    adv.GetSecurityDescriptorControl.argtypes = [pointer, ct.POINTER(w.WORD), ct.POINTER(w.DWORD)]
    adv.GetAclInformation.argtypes = [pointer, pointer, w.DWORD, ct.c_int]
    adv.GetAce.argtypes = [pointer, w.DWORD, out]
    token, sd, owner, dacl = w.HANDLE(), pointer(), pointer(), pointer()

    def sid_text(sid: pointer) -> str:
        value = w.LPWSTR()
        if not sid or not adv.ConvertSidToStringSidW(sid, ct.byref(value)):
            raise OSError("private IO SID unavailable")
        try:
            return str(value.value)
        finally:
            kernel.LocalFree(ct.cast(value, pointer))

    try:
        if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 0x8, ct.byref(token)):
            raise ct.WinError(ct.get_last_error())
        size = w.DWORD()
        adv.GetTokenInformation(token, 1, None, 0, ct.byref(size))
        if not size.value:
            raise OSError("private IO account unavailable")
        user = ct.create_string_buffer(size.value)
        if not adv.GetTokenInformation(token, 1, user, size, ct.byref(size)):
            raise ct.WinError(ct.get_last_error())
        allowed = {sid_text(ct.cast(user, ct.POINTER(pointer))[0]), "S-1-5-18", "S-1-5-32-544"}
        args = (1, 0x5, ct.byref(owner), None, ct.byref(dacl), None, ct.byref(sd))
        error = (adv.GetNamedSecurityInfoW(str(path), *args) if descriptor is None else
                 adv.GetSecurityInfo(w.HANDLE(msvcrt.get_osfhandle(descriptor)), *args))
        if error:
            raise ct.WinError(error)
        control, revision = w.WORD(), w.DWORD()
        if (not sd or not dacl or sid_text(owner) not in allowed
                or not adv.GetSecurityDescriptorControl(sd, ct.byref(control), ct.byref(revision))
                or not control.value & 0x1000):  # SE_DACL_PROTECTED
            raise OSError("private IO owner or protected DACL is invalid")
        info = (w.DWORD * 3)()
        if not adv.GetAclInformation(dacl, info, ct.sizeof(info), 2) or info[0] != 3:
            raise OSError("private IO ACE set is invalid")
        seen = set()
        for index in range(info[0]):
            ace = pointer()
            if not adv.GetAce(dacl, index, ct.byref(ace)) or not ace.value:
                raise OSError("private IO ACE unavailable")
            header = (ct.c_ubyte * 4).from_address(ace.value)
            # Only explicit ACCESS_ALLOWED_ACE, full control, current account/OS admins.
            if header[0] != 0 or header[1] & 0x10 or ct.c_uint32.from_address(ace.value + 4).value != 0x1F01FF:
                raise OSError("private IO ACE grants unexpected access")
            principal = sid_text(pointer(ace.value + 8))
            if principal not in allowed:
                raise OSError("private IO principal is invalid")
            seen.add(principal)
        if seen != allowed:
            raise OSError("private IO account access is incomplete")
    finally:
        if sd:
            kernel.LocalFree(sd)
        if token:
            kernel.CloseHandle(token)


if __name__ == "__main__":
    # Fixed, package-owned OS IO helper for Node on Windows; no Goal decisions.
    import sys
    if len(sys.argv) != 3 or sys.argv[1] != "--verify-snapshot":
        raise SystemExit(2)
    try:
        leaf = Path(sys.argv[2])
        require_private_path(leaf.parent, directory=True)
        with leaf.open("rb") as handle:
            require_private_path(leaf, descriptor=handle.fileno())
    except OSError:
        raise SystemExit(1) from None
