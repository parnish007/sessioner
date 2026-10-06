"""Root product tests never access the user's account or launch Claude."""

from pathlib import Path
import socket
import subprocess

import pytest


@pytest.fixture(autouse=True)
def isolated_product_environment(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    profile.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: profile))
    for variable in ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "XDG_DATA_HOME"):
        monkeypatch.setenv(variable, str(profile))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(profile / ".claude"))
    monkeypatch.delenv("SESSIONER_COMMAND", raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("Product tests must inject their account engine; real I/O is forbidden")

    real_connect = socket.socket.connect
    real_create_connection = socket.create_connection

    def loopback_only(real):
        # The browser interface listens on 127.0.0.1; reaching anything else is still a test bug.
        def guarded(target, *args, **kwargs):
            address = target[0] if isinstance(target, tuple) else target
            if address != "127.0.0.1":
                return forbidden()
            return real(target, *args, **kwargs)
        return guarded

    def guarded_connect(self, address, *args, **kwargs):
        return loopback_only(lambda target, *a, **k: real_connect(self, target, *a, **k))(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", loopback_only(real_create_connection))
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    import sessioner.accounts.switcher
    monkeypatch.setattr(sessioner.accounts.switcher, "ClaudeAccountSwitcher", forbidden)
