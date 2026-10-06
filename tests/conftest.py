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

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    import sessioner.accounts.switcher
    monkeypatch.setattr(sessioner.accounts.switcher, "ClaudeAccountSwitcher", forbidden)
