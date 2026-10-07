"""Native Windows notification area, with no import-time Windows dependency."""

from __future__ import annotations

from collections import deque
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import hashlib
from pathlib import Path
import sys
import threading
import time


WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_CONTEXTMENU = 0x007B
WM_TIMER = 0x0113
WM_APP = 0x8000
TRAY_CALLBACK = WM_APP + 1
UPDATE_MESSAGE = WM_APP + 2
NOTICE_MESSAGE = WM_APP + 3
PREFERENCES_MESSAGE = WM_APP + 4
ACTIVATE_MESSAGE = "Sessioner.Desktop.Activate.v1"
ASFW_ANY = 0xFFFFFFFF


@dataclass(frozen=True)
class MenuItem:
    label: str
    action: str | None = None
    enabled: bool = True
    checked: bool = False


def profile_key(state_dir: Path) -> str:
    return hashlib.sha256(str(Path(state_dir).resolve()).casefold().encode("utf-8")).hexdigest()[:24]


class _API:
    def __init__(self):
        if sys.platform != "win32":
            raise OSError("The Sessioner desktop companion requires Windows.")
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.shell = ctypes.WinDLL("shell32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.wndproc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t)

        class WindowClass(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", self.wndproc_type),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

        class Guid(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", wintypes.BYTE * 8)]

        class NotifyData(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                        ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                        ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                        ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
                        ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
                        ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                        ("dwInfoFlags", wintypes.DWORD), ("guidItem", Guid),
                        ("hBalloonIcon", wintypes.HICON)]

        class Message(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                        ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t),
                        ("time", wintypes.DWORD), ("pt", wintypes.POINT),
                        ("lPrivate", wintypes.DWORD)]

        self.WindowClass, self.NotifyData, self.Message = WindowClass, NotifyData, Message
        signatures = [
            (self.kernel.GetModuleHandleW, [wintypes.LPCWSTR], wintypes.HMODULE),
            (self.kernel.CreateMutexW, [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            (self.kernel.CloseHandle, [wintypes.HANDLE], wintypes.BOOL),
            (self.user.RegisterWindowMessageW, [wintypes.LPCWSTR], wintypes.UINT),
            (self.user.FindWindowW, [wintypes.LPCWSTR, wintypes.LPCWSTR], wintypes.HWND),
            (self.user.PostMessageW, [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t], wintypes.BOOL),
            (self.user.AllowSetForegroundWindow, [wintypes.DWORD], wintypes.BOOL),
            (self.user.RegisterClassW, [ctypes.POINTER(WindowClass)], wintypes.WORD),
            (self.user.UnregisterClassW, [wintypes.LPCWSTR, wintypes.HINSTANCE], wintypes.BOOL),
            (self.user.CreateWindowExW, [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p], wintypes.HWND),
            (self.user.DestroyWindow, [wintypes.HWND], wintypes.BOOL),
            (self.user.DefWindowProcW, [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t], ctypes.c_ssize_t),
            (self.user.GetMessageW, [ctypes.POINTER(Message), wintypes.HWND, wintypes.UINT, wintypes.UINT], wintypes.BOOL),
            (self.user.TranslateMessage, [ctypes.POINTER(Message)], wintypes.BOOL),
            (self.user.DispatchMessageW, [ctypes.POINTER(Message)], ctypes.c_ssize_t),
            (self.user.PostQuitMessage, [ctypes.c_int], None),
            (self.user.CreateIcon, [wintypes.HINSTANCE, ctypes.c_int, ctypes.c_int, wintypes.BYTE, wintypes.BYTE, ctypes.c_void_p, ctypes.c_void_p], wintypes.HICON),
            (self.user.DestroyIcon, [wintypes.HICON], wintypes.BOOL),
            (self.user.CreatePopupMenu, [], wintypes.HMENU),
            (self.user.AppendMenuW, [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR], wintypes.BOOL),
            (self.user.TrackPopupMenu, [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, ctypes.c_void_p], wintypes.UINT),
            (self.user.DestroyMenu, [wintypes.HMENU], wintypes.BOOL),
            (self.user.GetCursorPos, [ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
            (self.user.SetForegroundWindow, [wintypes.HWND], wintypes.BOOL),
            (self.user.SetTimer, [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p], ctypes.c_size_t),
            (self.user.KillTimer, [wintypes.HWND, ctypes.c_size_t], wintypes.BOOL),
            (self.user.MessageBoxW, [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT], ctypes.c_int),
            (self.shell.Shell_NotifyIconW, [wintypes.DWORD, ctypes.POINTER(NotifyData)], wintypes.BOOL),
        ]
        for function, arguments, result in signatures:
            function.argtypes, function.restype = arguments, result
        self.activate_message = self.user.RegisterWindowMessageW(ACTIVATE_MESSAGE)
        self.taskbar_created = self.user.RegisterWindowMessageW("TaskbarCreated")


def wake_for(state_dir: Path, *, open_browser: bool = False) -> bool:
    """Signal the existing profile window; its launch URL stays in that process."""
    if sys.platform != "win32":
        return False
    api = _API()
    window = api.user.FindWindowW(f"SessionerTray-{profile_key(state_dir)}", None)
    if window and open_browser:
        # The person launched this process, so it may bring windows forward; the running tray
        # app may not. Hand that right over, or the dashboard opens behind other windows.
        api.user.AllowSetForegroundWindow(ASFW_ANY)
    return bool(window and api.user.PostMessageW(window, api.activate_message, int(open_browser), 0))


class DesktopInstance:
    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.api = _API()
        ctypes.set_last_error(0)
        self.handle = self.api.kernel.CreateMutexW(None, False, f"Local\\SessionerDesktop-{profile_key(state_dir)}")
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.already_running = ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS

    def activate(self, *, open_browser: bool = True) -> bool:
        # The mutex can be acquired a moment before the hidden window exists.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if wake_for(self.state_dir, open_browser=open_browser):
                return True
            time.sleep(0.05)
        return False

    def close(self) -> None:
        if self.handle:
            self.api.kernel.CloseHandle(self.handle)
            self.handle = None


class NativeTray:
    """Own a hidden message window; worker threads only post messages to it."""

    def __init__(self, state_dir: Path, on_action):
        self.api = _API()
        self.on_action = on_action
        self.class_name = f"SessionerTray-{profile_key(state_dir)}"
        self.instance = self.api.kernel.GetModuleHandleW(None)
        self._lock = threading.Lock()
        self._menu: list[MenuItem] = []
        self._tip = "Sessioner"
        self._notices: deque[tuple[str, str]] = deque(maxlen=20)
        self._notice_waiting = False
        self._notifications_enabled = True
        self._closed = False
        self._callback = self.api.wndproc_type(self._window_proc)
        self._class = self.api.WindowClass()
        self._class.lpfnWndProc = self._callback
        self._class.hInstance = self.instance
        self._class.lpszClassName = self.class_name
        if not self.api.user.RegisterClassW(ctypes.byref(self._class)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.window = self.api.user.CreateWindowExW(0, self.class_name, "Sessioner", 0, 0, 0, 0, 0, None, None, self.instance, None)
        if not self.window:
            self.api.user.UnregisterClassW(self.class_name, self.instance)
            raise ctypes.WinError(ctypes.get_last_error())
        self.icon = self._create_icon()
        try:
            self._add_icon()
        except Exception:
            self.close()
            raise

    def _create_icon(self):
        # A blue tile with a white S, generated locally rather than a dependency.
        pixels = bytearray()
        for y in range(32):
            for x in range(32):
                white = ((8 <= x <= 23 and (7 <= y <= 10 or 14 <= y <= 17 or 22 <= y <= 25))
                         or (8 <= x <= 11 and 7 <= y <= 17)
                         or (20 <= x <= 23 and 14 <= y <= 25))
                pixels.extend((255, 255, 255, 255) if white else (215, 123, 55, 255))
        mask = ctypes.create_string_buffer(bytes(128))
        bitmap = ctypes.create_string_buffer(bytes(pixels))
        icon = self.api.user.CreateIcon(self.instance, 32, 32, 1, 32, mask, bitmap)
        if not icon:
            raise ctypes.WinError(ctypes.get_last_error())
        return icon

    def _data(self):
        data = self.api.NotifyData()
        data.cbSize = ctypes.sizeof(data)
        data.hWnd, data.uID = self.window, 1
        return data

    def _add_icon(self):
        data = self._data()
        data.uFlags = 1 | 2 | 4 | 0x80  # message, icon, tooltip, show tooltip in v4
        data.uCallbackMessage, data.hIcon, data.szTip = TRAY_CALLBACK, self.icon, self._tip[:127]
        if not self.api.shell.Shell_NotifyIconW(0, ctypes.byref(data)):
            raise OSError("Windows could not add the Sessioner tray icon.")
        data.uVersion = 4
        self.api.shell.Shell_NotifyIconW(4, ctypes.byref(data))

    def update(self, title: str, menu: list[MenuItem]) -> None:
        with self._lock:
            self._tip, self._menu = title[:127], list(menu)
        self.api.user.PostMessageW(self.window, UPDATE_MESSAGE, 0, 0)

    def notify(self, title: str, body: str) -> None:
        with self._lock:
            if not self._notifications_enabled:
                return
            self._notices.append((title[:63], body[:255]))
        self.api.user.PostMessageW(self.window, NOTICE_MESSAGE, 0, 0)

    def set_notifications_enabled(self, enabled: bool) -> None:
        with self._lock:
            if self._notifications_enabled == enabled:
                return
            self._notifications_enabled = enabled
            if not enabled:
                self._notices.clear()
        self.api.user.PostMessageW(self.window, PREFERENCES_MESSAGE, 0, 0)

    def _show_notice(self):
        with self._lock:
            if not self._notifications_enabled or self._notice_waiting or not self._notices:
                return
            title, body = self._notices.popleft()
            self._notice_waiting = True
        data = self._data()
        data.uFlags = 0x10 | 0x40  # information, discard if it cannot display now
        data.szInfoTitle, data.szInfo = title, body
        data.dwInfoFlags = 1 | 0x80  # information, respect Windows quiet time
        self.api.shell.Shell_NotifyIconW(1, ctypes.byref(data))
        self.api.user.SetTimer(self.window, 1, 12000, None)

    def _show_menu(self):
        with self._lock:
            entries = list(self._menu)
        menu = self.api.user.CreatePopupMenu()
        if not menu:
            return
        try:
            for index, item in enumerate(entries, 1):
                flags = 0x800 if not item.label else (0 if item.action and item.enabled else 1) | (8 if item.checked else 0)
                self.api.user.AppendMenuW(menu, flags, index, item.label.replace("&", "&&"))
            point = wintypes.POINT()
            self.api.user.GetCursorPos(ctypes.byref(point))
            self.api.user.SetForegroundWindow(self.window)
            chosen = self.api.user.TrackPopupMenu(menu, 0x100 | 2, point.x, point.y, 0, self.window, None)
            self.api.user.PostMessageW(self.window, 0, 0, 0)
            if 1 <= chosen <= len(entries):
                action = entries[chosen - 1].action
                if action:
                    self.on_action(action)
        finally:
            self.api.user.DestroyMenu(menu)

    def _window_proc(self, window, message, wparam, lparam):
        try:
            if message == self.api.activate_message:
                self.on_action("dashboard" if wparam else "wake")
                return 0
            if message == self.api.taskbar_created:
                self._add_icon()
                return 0
            if message == UPDATE_MESSAGE:
                data = self._data()
                data.uFlags = 4 | 0x80
                with self._lock:
                    data.szTip = self._tip
                self.api.shell.Shell_NotifyIconW(1, ctypes.byref(data))
                return 0
            if message == NOTICE_MESSAGE:
                self._show_notice()
                return 0
            if message == PREFERENCES_MESSAGE:
                if not self._notifications_enabled:
                    self.api.user.KillTimer(window, 1)
                    self._notice_waiting = False
                    data = self._data()
                    data.uFlags = 0x10
                    data.szInfo = ""
                    self.api.shell.Shell_NotifyIconW(1, ctypes.byref(data))
                return 0
            if message == WM_TIMER and wparam == 1:
                self.api.user.KillTimer(window, 1)
                self._notice_waiting = False
                self._show_notice()
                return 0
            if message == TRAY_CALLBACK:
                event = lparam & 0xFFFF
                if event in {WM_CONTEXTMENU, 0x205}:  # keyboard/right-button menu
                    self._show_menu()
                elif event in {0x400, 0x401, 0x203}:  # select, keyboard select, double click
                    self.on_action("dashboard")
                return 0
            if message == WM_CLOSE:
                self.api.user.DestroyWindow(window)
                return 0
            if message == WM_DESTROY:
                self.api.shell.Shell_NotifyIconW(2, ctypes.byref(self._data()))
                self.api.user.PostQuitMessage(0)
                return 0
        except Exception:
            # A Python exception must not escape a Win32 callback.
            return 0
        return self.api.user.DefWindowProcW(window, message, wparam, lparam)

    def run(self) -> None:
        message = self.api.Message()
        try:
            while True:
                result = self.api.user.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result == 0:
                    break
                if result == -1:
                    raise ctypes.WinError(ctypes.get_last_error())
                self.api.user.TranslateMessage(ctypes.byref(message))
                self.api.user.DispatchMessageW(ctypes.byref(message))
        finally:
            self.close()

    def stop(self) -> None:
        if not self._closed:
            self.api.user.PostMessageW(self.window, WM_CLOSE, 0, 0)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.api.shell.Shell_NotifyIconW(2, ctypes.byref(self._data()))
        self.api.user.DestroyWindow(self.window)
        self.api.user.DestroyIcon(self.icon)
        self.api.user.UnregisterClassW(self.class_name, self.instance)


def show_error(message: str) -> None:
    if sys.platform == "win32":
        _API().user.MessageBoxW(None, message, "Sessioner", 0x10)
