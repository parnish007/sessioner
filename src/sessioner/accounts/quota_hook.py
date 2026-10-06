"""Rotate saved accounts on a confirmed Claude usage-limit failure.

Claude owns its terminal and conversation. This module only installs an observer
hook and activates credentials through the existing account-switch transaction.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from uuid import uuid4

from sessioner.activity import FILE as ACTIVITY_FILE, ActivityStore
from sessioner.accounts import history, paths
from sessioner.accounts.selection import select_backup
from sessioner.accounts.fsutil import replace_with_retry
from sessioner.accounts.locking import FileLock


MAX_INPUT_BYTES = 1024 * 1024
USAGE_MAX_AGE_SECONDS = 300
FAILED_ACCOUNT_COOLDOWN_SECONDS = 300
_HOOK_ARGS = ["-m", "sessioner.accounts.quota_hook", "run"]
_QUOTA_MESSAGE = re.compile(
    r"you(?:'|’)ve hit your (?:usage )?limit|usage limit (?:reached|exceeded)|"
    r"(?:weekly|session usage) limit|quota (?:exceeded|exhausted)",
    re.IGNORECASE,
)


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _account_number(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _rate_failure(event: object) -> bool:
    return (
        isinstance(event, dict)
        and event.get("hook_event_name") == "StopFailure"
        and event.get("error") == "rate_limit"
        and not event.get("agent_id")
    )


def _windows(account: dict) -> list[object]:
    usage = account.get("usage")
    if not isinstance(usage, dict):
        return []
    windows = [usage[key] for key in ("fiveHour", "sevenDay") if key in usage]
    scoped = usage.get("scoped", [])
    if isinstance(scoped, list):
        windows.extend(scoped)
    return windows


def _quota_exhausted(event: dict, active: dict) -> bool:
    messages = " ".join(
        value[:4096]
        for key in ("error_details", "last_assistant_message")
        if isinstance(value := event.get(key), str)
    )
    if _QUOTA_MESSAGE.search(messages):
        return True
    age = active.get("usageAgeSeconds")
    if active.get("usageStatus") != "ok" or not _number(age) or not 0 <= age <= USAGE_MAX_AGE_SECONDS:
        return False
    return any(
        isinstance(window, dict)
        and _number(window.get("pct"))
        and window["pct"] >= 100
        for window in _windows(active)
    )


def _available(account: dict) -> bool:
    age = account.get("usageAgeSeconds")
    if (
        account.get("disabled")
        or account.get("usageStatus") != "ok"
        or not _number(age)
        or not 0 <= age <= USAGE_MAX_AGE_SECONDS
    ):
        return False
    windows = _windows(account)
    if not windows:
        return False
    scoped = account["usage"].get("scoped", [])
    if not isinstance(scoped, list):
        return False
    return all(
        isinstance(window, dict)
        and _number(window.get("pct"))
        and 0 <= window["pct"] < 100
        for window in windows + scoped
    )


def _roster_accounts(roster: object) -> tuple[int, list[dict]]:
    if not isinstance(roster, dict) or roster.get("schemaVersion") != 1 or roster.get("error"):
        raise ValueError("Invalid account roster")
    active = roster.get("activeAccountNumber")
    accounts = roster.get("accounts")
    if not _account_number(active) or not isinstance(accounts, list):
        raise ValueError("The active Claude account is not saved")
    if any(not isinstance(row, dict) or not _account_number(row.get("number")) for row in accounts):
        raise ValueError("Invalid account roster")
    numbers = [row["number"] for row in accounts]
    if len(numbers) != len(set(numbers)) or active not in numbers:
        raise ValueError("Invalid account roster")
    return active, accounts


def _account_identity(account: dict) -> tuple[str, str]:
    return (
        (account.get("email") or "").strip().casefold(),
        (account.get("organizationUuid") or "").strip().casefold(),
    )


def choose_account(
    event: dict,
    roster: dict,
    excluded: set[int] | None = None,
    *,
    now: datetime | None = None,
) -> int | None:
    """Select a fresh, usable saved account; ordinary throttles do nothing."""
    if not _rate_failure(event):
        return None
    active_number, accounts = _roster_accounts(roster)
    active = next(row for row in accounts if row["number"] == active_number)
    if not _quota_exhausted(event, active):
        return None
    result = select_backup(accounts, active_number=active_number, excluded=excluded, now=now)
    if result.target is not None:
        return result.target
    raise ValueError("No saved account has fresh available usage")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        replace_with_retry(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_settings(path: Path) -> tuple[dict, bytes | None]:
    original = path.read_bytes() if path.exists() else None
    try:
        document = json.loads(original.decode("utf-8-sig")) if original is not None else {}
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Claude settings are invalid; no changes were made") from exc
    if not isinstance(document, dict):
        raise ValueError("Claude settings must be a JSON object")
    hooks = document.get("hooks", {})
    if not isinstance(hooks, dict) or not isinstance(hooks.get("StopFailure", []), list):
        raise ValueError("Claude hook settings are invalid; no changes were made")
    for entry in hooks.get("StopFailure", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
            raise ValueError("Claude StopFailure settings are invalid; no changes were made")
    return document, original


def _our_handler(handler: object) -> bool:
    return isinstance(handler, dict) and handler.get("type") == "command" and handler.get("args") == _HOOK_ARGS


def hook_enabled(settings_path: Path) -> bool:
    document, _ = _read_settings(Path(settings_path))
    return any(
        entry.get("matcher") == "rate_limit" and any(_our_handler(handler) for handler in entry["hooks"])
        for entry in document.get("hooks", {}).get("StopFailure", [])
    )


def configure_hook(settings_path: Path, enabled: bool) -> dict:
    """Change only our rate-limit hook and retain an exact settings backup."""
    path = Path(settings_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(path.parent / f".{path.name}.sessioner.lock", timeout=2):
        document, original = _read_settings(path)
        entries = document.get("hooks", {}).get("StopFailure", [])
        if not enabled and not any(
            entry.get("matcher") == "rate_limit" and any(_our_handler(handler) for handler in entry["hooks"])
            for entry in entries
        ):
            return {"enabled": False, "changed": False, "settings": str(path)}
        remaining = []
        for entry in entries:
            if entry.get("matcher") != "rate_limit":
                remaining.append(entry)
                continue
            handlers = [handler for handler in entry["hooks"] if not _our_handler(handler)]
            if handlers:
                remaining.append({**entry, "hooks": handlers})
            elif not any(_our_handler(handler) for handler in entry["hooks"]):
                remaining.append(entry)
        if enabled:
            remaining.append({"matcher": "rate_limit", "hooks": [{
                "type": "command", "command": sys.executable,
                "args": list(_HOOK_ARGS), "timeout": 120,
            }]})
        if remaining:
            document.setdefault("hooks", {})["StopFailure"] = remaining
        elif "hooks" in document:
            document["hooks"].pop("StopFailure", None)
            if not document["hooks"]:
                document.pop("hooks")
        # Comparing parsed JSON makes repeated enable/disable calls no-ops.
        if original is not None and document == json.loads(original.decode("utf-8-sig")):
            return {"enabled": enabled, "changed": False, "settings": str(path)}
        if original is None and not document:
            return {"enabled": enabled, "changed": False, "settings": str(path)}
        backup = None
        if original is not None:
            backup = path.with_name(f"{path.name}.sessioner-{uuid4().hex}.bak")
            _atomic_write(backup, original)
        content = (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        _atomic_write(path, content)
        return {"enabled": enabled, "changed": True, "settings": str(path), "backup": str(backup) if backup else None}


def _read_cooldowns(path: Path, now: float) -> dict[str, float]:
    # Cooldowns are a convenience, never a gate: a damaged file must not stop every
    # future switch, so anything unreadable is dropped and the caller rewrites the file.
    try:
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(state, dict):
        return {}
    return {
        key: deadline for key, deadline in state.items()
        if isinstance(key, str) and key.isdigit() and _number(deadline) and deadline > now
    }


def _switch_and_verify(switcher, target: int) -> bool:
    """One verified handoff attempt. False means this candidate did not take; try another."""
    try:
        switched = switcher.switch_to(str(target), json_output=True)
        if (
            not isinstance(switched, dict)
            or switched.get("schemaVersion") != 1
            or switched.get("switched") is not True
            or not isinstance(switched.get("to"), dict)
            or switched["to"].get("number") != target
        ):
            return False
        status = switcher.status(json_output=True)
        return (
            isinstance(status, dict)
            and status.get("schemaVersion") == 1
            and isinstance(status.get("active"), dict)
            and status["active"].get("number") == target
            and status["active"].get("managed") is not False
        )
    except Exception:
        return False


def rotate_account(event: dict, switcher=None, now: float | None = None) -> dict:
    """Perform one serialized credential handoff, then verify the active slot."""
    if not _rate_failure(event):
        return {"status": "ignored"}
    try:
        if switcher is None:
            from sessioner.accounts.switcher import ClaudeAccountSwitcher
            switcher = ClaudeAccountSwitcher()
        timestamp = time.time() if now is None else now
        if not _number(timestamp):
            raise ValueError("Invalid account cooldown time")
        backup_dir = Path(getattr(switcher, "backup_dir", paths.get_backup_root()))
        journal = ActivityStore(backup_dir / ACTIVITY_FILE)
        state_path = backup_dir / "sessioner-hook-state.json"
        with FileLock(backup_dir / ".sessioner-hook.lock", timeout=0.5):
            roster = switcher.list_accounts(json_output=True)
            active_number, accounts = _roster_accounts(roster)
            active = next(row for row in accounts if row["number"] == active_number)
            if not _quota_exhausted(event, active):
                return {"status": "ignored"}
            journal.observe_accounts(accounts, source="hook")
            journal.record("quota_exhausted", source="hook", from_slot=active_number, reason="quota-exhausted")
            cooldowns = _read_cooldowns(state_path, timestamp)
            # Remember an observed failure before attempting the write so a
            # second failure cannot cycle back to stale apparently-usable data.
            cooldowns[str(active_number)] = timestamp + FAILED_ACCOUNT_COOLDOWN_SECONDS
            _atomic_write(state_path, (json.dumps(cooldowns) + "\n").encode("utf-8"))
            excluded = {int(number) for number in cooldowns}
            # Work down the ranked candidates: one account with a bad credential must not
            # strand the person when another has room. Each candidate is tried at most once.
            for _ in range(len(accounts)):
                selection = select_backup(accounts, active_number=active_number, excluded=excluded)
                try:
                    target = choose_account(event, roster, excluded=excluded)
                except ValueError:
                    if selection.all_exhausted or selection.reset_unknown:
                        journal.record("all_exhausted", source="hook", from_slot=active_number, reason=selection.reason)
                    raise
                if target is None:
                    return {"status": "ignored"}
                journal.record("candidate_selected", source="hook", from_slot=active_number, to_slot=target, reason=selection.reason)
                if _switch_and_verify(switcher, target):
                    history.note_active(backup_dir, next(row for row in accounts if row["number"] == target), now=timestamp)
                    journal.record("switch_confirmed", source="hook", from_slot=active_number, to_slot=target, reason="verified")
                    return {"status": "switched", "from": active_number, "to": target}
                journal.record("switch_failed", source="hook", from_slot=active_number, to_slot=target, reason="switch-unverified")
                excluded.add(target)
                cooldowns[str(target)] = timestamp + FAILED_ACCOUNT_COOLDOWN_SECONDS
                _atomic_write(state_path, (json.dumps(cooldowns) + "\n").encode("utf-8"))
            raise ValueError("No saved account could be switched to")
    except Exception:
        # Do not forward exception details: upstream exceptions can contain
        # account identifiers. Hook diagnostics expose only account slot numbers.
        return {"status": "blocked"}


def _run_hook() -> int:
    try:
        incoming = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(incoming) > MAX_INPUT_BYTES:
            raise ValueError("Hook input exceeds its size limit")
        event = json.loads(incoming)
        result = rotate_account(event)
        if result["status"] == "switched":
            print(f"Sessioner: account {result['from']} switched to {result['to']}.", file=sys.stderr)
        elif result["status"] == "blocked":
            print("Sessioner: account switch blocked; check saved accounts and available usage.", file=sys.stderr)
    except Exception:
        print("Sessioner: account hook input could not be processed.", file=sys.stderr)
    # StopFailure hooks observe a failed request; their output cannot retry it.
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Switch saved Claude accounts on usage limits.")
    parser.add_argument("action", choices=("enable", "disable", "status", "run"))
    action = parser.parse_args(argv).action
    if action == "run":
        return _run_hook()
    settings_path = paths.get_claude_config_home() / "settings.json"
    try:
        if action == "status":
            print(f"Account switching: {'enabled' if hook_enabled(settings_path) else 'disabled'}")
            print(f"Claude settings: {settings_path}")
            return 0
        if action == "enable":
            from sessioner.accounts.switcher import ClaudeAccountSwitcher
            roster = ClaudeAccountSwitcher().list_accounts(json_output=True)
            _, accounts = _roster_accounts(roster)
            if len([row for row in accounts if not row.get("disabled")]) < 2:
                raise ValueError("Save at least two enabled Claude accounts before enabling the hook")
        result = configure_hook(settings_path, action == "enable")
        print(f"Account switching: {'enabled' if result['enabled'] else 'disabled'}")
        print(f"Claude settings: {settings_path}")
        if result.get("backup"):
            print(f"Settings backup: {result['backup']}")
        return 0
    except Exception:
        print("Sessioner: configuration failed; check valid settings and two saved accounts.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
