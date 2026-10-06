"""Readable terminal output with commands matching the active launcher."""

import os
import re


def command_name() -> str:
    return os.environ.get("SESSIONER_COMMAND") or "sessioner"


_COMMAND = re.compile(r"(?<![\w.-])sessioner(?=\s+(?:setup|add|accounts|switch|rename|on|off|status|doctor|ui|watch|desktop)\b|\s+--(?:help|version)\b)")


def say(console, text: str, **kwargs):
    command = command_name()
    text = _COMMAND.sub(lambda match: command, text)
    if text.endswith("Next: sessioner"):
        text = text[:-len("sessioner")] + command
    kwargs.setdefault("soft_wrap", True)
    console.print(text, markup=False, highlight=False, **kwargs)
