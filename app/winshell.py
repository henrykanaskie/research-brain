"""research brain and the Windows shell: one taskbar button of its own, and voice typing.

- Taskbar identity: research brain's window runs in pythonw.exe, so without an AppUserModelID of its own
  Windows groups it with every other Python program, and "Pin to taskbar" pins a bare pythonw. set_app_id()
  gives the process research brain's ID (app/winwindow.py, before the window is made), and the Start menu
  shortcut carries the same ID (app/launcher.py makes it; ensure_shortcut() adds the ID to one made by an
  older version), so the running window, the Start menu entry and a pinned button are one taskbar button
  with the brain icon, the way the Mac app is one Dock icon. set_window_relaunch() also tells the taskbar
  how to start research brain again from that window, in case the shortcut is missing.
- voice_typing(): Windows voice typing (Win+H), into the field that has the focus: the chat's mic button in
  research brain's own window (static/app.js), as macOS dictation is on the Mac.

Everything here is Windows only and never raises: off Windows, or when the shell says no, the answer is
False (or None) and research brain carries on without it. Raw ctypes COM, so no extra package is needed.
"""
from __future__ import annotations

import functools
import os
from pathlib import Path

APP_ID = "ResearchBrain.App"

# {9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}: the AppUserModel property set (ID 5, RelaunchCommand 2,
# RelaunchIconResource 3, RelaunchDisplayNameResource 4)
_PKEY_FMTID = "{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}"
_PID_ID, _PID_RELAUNCH_COMMAND, _PID_RELAUNCH_ICON, _PID_RELAUNCH_NAME = 5, 2, 3, 4
_IID_IPROPERTYSTORE = "{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}"
_VT_LPWSTR = 31


def _windows() -> bool:
    return os.name == "nt"


def set_app_id(app_id: str = APP_ID) -> bool:
    """This process's taskbar identity (before any window is shown)."""
    if not _windows():
        return False
    try:
        import ctypes
        f = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        f.argtypes = [ctypes.c_wchar_p]
        f.restype = ctypes.c_long
        return f(app_id) == 0
    except Exception:
        return False


# ---------------------------------------------------------------- property stores (raw COM)
@functools.lru_cache(maxsize=1)
def _com():
    """The ctypes pieces: GUID, PROPERTYKEY, PROPVARIANT, and a vtable caller. Made once, so every call
    shares the same structure types (ctypes refuses a PROPERTYKEY made by another call)."""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]

    class PROPVARIANT(ctypes.Structure):  # vt, three reserved words, then a union two pointers wide
        _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort),
                    ("r3", ctypes.c_ushort), ("p", ctypes.c_void_p), ("p2", ctypes.c_void_p)]

    def guid(s: str) -> GUID:
        g = GUID()
        ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(s), ctypes.byref(g))
        return g

    def call(obj, index: int, *args, argtypes=()):
        vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        fn = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(vtbl[index])
        return fn(obj, *args)

    return ctypes, GUID, PROPERTYKEY, PROPVARIANT, guid, call


class _Store:
    """An IPropertyStore (a file's, through the shell, or a window's), released and COM uninitialized on exit."""

    def __init__(self, opener):
        self.opener = opener
        self.obj = None
        self.inited = False

    def __enter__(self):
        ctypes, _G, _K, _V, guid, _call = _com()
        hr = ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
        self.inited = hr in (0, 1)  # S_OK, S_FALSE (a thread already in the STA); not RPC_E_CHANGED_MODE
        obj = ctypes.c_void_p()
        try:
            self.opener(ctypes, guid(_IID_IPROPERTYSTORE), obj)
        except Exception:
            if self.inited:
                ctypes.windll.ole32.CoUninitialize()
            raise
        self.obj = obj
        return self

    def __exit__(self, *exc):
        ctypes, *_rest, call = _com()
        try:
            if self.obj:
                vtbl = ctypes.cast(self.obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
                ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])(self.obj)  # Release
        finally:
            if self.inited:
                ctypes.windll.ole32.CoUninitialize()
        return False

    def _key(self, pid: int):
        ctypes, _G, PROPERTYKEY, _V, guid, _call = _com()
        k = PROPERTYKEY()
        k.fmtid = guid(_PKEY_FMTID)
        k.pid = pid
        return k

    def get(self, pid: int) -> str | None:
        ctypes, _G, PROPERTYKEY, PROPVARIANT, _guid, call = _com()
        v = PROPVARIANT()
        call(self.obj, 5, ctypes.byref(self._key(pid)), ctypes.byref(v),  # GetValue
             argtypes=(ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT)))
        try:
            return ctypes.wstring_at(v.p) if v.vt == _VT_LPWSTR and v.p else None
        finally:
            ctypes.windll.ole32.PropVariantClear(ctypes.byref(v))

    def set(self, pid: int, value: str) -> None:
        ctypes, _G, PROPERTYKEY, PROPVARIANT, _guid, call = _com()
        buf = ctypes.create_unicode_buffer(value)  # the store copies it
        v = PROPVARIANT()
        v.vt = _VT_LPWSTR
        v.p = ctypes.cast(buf, ctypes.c_void_p)
        call(self.obj, 6, ctypes.byref(self._key(pid)), ctypes.byref(v),  # SetValue
             argtypes=(ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT)))

    def commit(self) -> None:
        call = _com()[-1]
        call(self.obj, 7)  # Commit


def _file_store(path: Path, write: bool) -> _Store:
    def opener(ctypes, iid, out):
        f = ctypes.windll.shell32.SHGetPropertyStoreFromParsingName
        f.restype = ctypes.HRESULT
        f(ctypes.c_wchar_p(str(path)), None, 2 if write else 0, ctypes.byref(iid), ctypes.byref(out))  # GPS_READWRITE / GPS_DEFAULT
    return _Store(opener)


def _window_store(hwnd: int) -> _Store:
    def opener(ctypes, iid, out):
        f = ctypes.windll.shell32.SHGetPropertyStoreForWindow
        f.restype = ctypes.HRESULT
        f(ctypes.c_void_p(int(hwnd)), ctypes.byref(iid), ctypes.byref(out))
    return _Store(opener)


def shortcut_app_id(lnk_path) -> str | None:
    """The AppUserModelID a .lnk carries, or None (none, not a shortcut, or not Windows)."""
    if not _windows() or not Path(lnk_path).is_file():
        return None
    try:
        with _file_store(Path(lnk_path), write=False) as s:
            return s.get(_PID_ID) or None
    except Exception:
        return None


def set_shortcut_app_id(lnk_path, app_id: str = APP_ID) -> bool:
    """Give a .lnk research brain's AppUserModelID. True once it reads back."""
    if not _windows() or not Path(lnk_path).is_file():
        return False
    try:
        with _file_store(Path(lnk_path), write=True) as s:
            s.set(_PID_ID, app_id)
            s.commit()
    except Exception:
        return False
    return shortcut_app_id(lnk_path) == app_id


def ensure_shortcut() -> bool:
    """The Start menu entry carries research brain's ID (one made before it did gets it now). Cheap, never
    raises; True if the entry has the ID afterwards."""
    if not _windows():
        return False
    try:
        from .launcher import windows_shortcut_path
        lnk = windows_shortcut_path()
        if not lnk.is_file():
            return False
        if shortcut_app_id(lnk) == APP_ID:
            return True
        return set_shortcut_app_id(lnk, APP_ID)
    except Exception:
        return False


def set_window_relaunch(hwnd: int, command: str, name: str, icon: str | None = None, app_id: str = APP_ID) -> bool:
    """How the taskbar starts research brain again from this window (pinning it, or its jump list): the
    command, its name and icon, then the window's ID (the ID last, as the shell asks)."""
    if not _windows() or not hwnd:
        return False
    try:
        with _window_store(hwnd) as s:
            s.set(_PID_RELAUNCH_COMMAND, command)
            s.set(_PID_RELAUNCH_NAME, name)
            if icon:
                s.set(_PID_RELAUNCH_ICON, icon)
            s.set(_PID_ID, app_id)
            s.commit()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- voice typing
def voice_typing() -> bool:
    """Win+H: Windows voice typing starts (or stops) in the focused field. True if the keys were sent."""
    if not _windows():
        return False
    try:
        import ctypes
        from ctypes import wintypes
        ULONG_PTR = ctypes.c_size_t

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

        class _U(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]

        def key(vk: int, up: bool) -> INPUT:
            i = INPUT()
            i.type = 1  # INPUT_KEYBOARD
            i.u.ki = KEYBDINPUT(vk, 0, 0x2 if up else 0, 0, 0)  # KEYEVENTF_KEYUP
            return i

        VK_LWIN, VK_H = 0x5B, 0x48
        seq = (INPUT * 4)(key(VK_LWIN, False), key(VK_H, False), key(VK_H, True), key(VK_LWIN, True))
        send = ctypes.windll.user32.SendInput
        send.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
        send.restype = wintypes.UINT
        return send(4, seq, ctypes.sizeof(INPUT)) == 4
    except Exception:
        return False
