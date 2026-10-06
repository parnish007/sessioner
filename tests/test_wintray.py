"""Real native lifecycle with a temporary profile and no Claude accounts."""

import importlib
import ctypes
import sys
import threading

import pytest


def native():
    try:
        return importlib.import_module("sessioner.wintray")
    except ModuleNotFoundError:
        pytest.fail("The native Sessioner tray is not implemented")


def test_native_import_is_safe_without_windows_libraries(monkeypatch):
    native()
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delattr(ctypes, "WinDLL", raising=False)
    module = importlib.reload(importlib.import_module("sessioner.wintray"))
    desktop = importlib.reload(importlib.import_module("sessioner.desktop"))
    assert not desktop.supported()
    assert not module.wake_for("unused-profile")


@pytest.mark.skipif(sys.platform != "win32", reason="Requires the Windows notification area")
def test_native_tray_creates_reopens_and_removes_an_isolated_icon(tmp_path):
    module = native()
    first = module.DesktopInstance(tmp_path)
    assert not first.already_running
    actions = []
    tray = module.NativeTray(tmp_path, actions.append)
    second = module.DesktopInstance(tmp_path)
    try:
        assert second.already_running
        tray.update("Sessioner · Test account", [module.MenuItem("Open dashboard", "dashboard"), module.MenuItem("Quit", "quit")])
        assert second.activate(open_browser=True)
        timer = threading.Timer(0.3, tray.stop)
        timer.start()
        tray.run()
        timer.join(1)
        assert "dashboard" in actions
        assert not module.wake_for(tmp_path)
    finally:
        tray.close()
        second.close()
        first.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Requires the Windows notification area")
def test_disabling_notifications_clears_queued_native_banners(tmp_path, monkeypatch):
    module = native()
    tray = module.NativeTray(tmp_path, lambda _: None)
    displayed = []
    real_notify = tray.api.shell.Shell_NotifyIconW

    def capture(command, pointer):
        data = ctypes.cast(pointer, ctypes.POINTER(tray.api.NotifyData)).contents
        if command == 1 and data.uFlags & 0x10 and data.szInfo:
            displayed.append(data.szInfo)
            return True  # Do not show test banners on the user's desktop.
        return real_notify(command, pointer)

    monkeypatch.setattr(tray.api.shell, "Shell_NotifyIconW", capture)
    try:
        tray.notify("Switch complete", "Test banner queued before disabling")
        tray.set_notifications_enabled(False)
        tray.notify("Switch complete", "Test banner after disabling")
        timer = threading.Timer(0.15, tray.stop)
        timer.start()
        tray.run()
        timer.join(1)
        assert displayed == []
    finally:
        tray.close()
