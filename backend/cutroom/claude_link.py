"""Link to Claude Code: find the CLI, check the sign-in, open the sign-in window when needed.

Runs at backend start (Cutroom.exe → backend) so the agents are ready before anyone presses
«Предложить стратегию». `claude auth status --json` is local and free; it never calls the model.
Sign-in is the user's own action in the browser: we only open `claude auth login` in a console.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time

from .agent import find_claude
from .config import load_settings

NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NEWCON = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)

_state: dict = {"checked": None, "found": False, "path": None, "version": None, "logged_in": False,
                "account": None, "plan": None, "error": None, "login_opened": None}
_lock = threading.Lock()


def check() -> dict:
    cli = find_claude()
    st: dict = {"checked": time.time(), "found": bool(cli), "path": cli, "version": None, "logged_in": False,
                "account": None, "plan": None, "error": None}
    if cli:
        try:
            v = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=20, creationflags=NOWIN)
            st["version"] = (v.stdout or "").strip().split(" ")[0] or None
            a = subprocess.run([cli, "auth", "status", "--json"], capture_output=True, text=True, timeout=20,
                               encoding="utf-8", errors="replace", creationflags=NOWIN)
            info = json.loads(a.stdout or "{}")
            st["logged_in"] = bool(info.get("loggedIn"))
            st["account"] = info.get("email")
            st["plan"] = info.get("subscriptionType")
        except Exception as e:  # noqa: BLE001
            st["error"] = str(e)[:200]
    else:
        st["error"] = "Claude Code не найден: установите приложение Claude для рабочего стола или укажите путь к claude.exe"
    with _lock:
        _state.update(st)
        return dict(_state)


def status() -> dict:
    with _lock:
        return dict(_state)


def open_login() -> dict:
    """A console window with `claude auth login`: the browser opens, the user signs in, the window closes."""
    cli = _state.get("path") or find_claude()
    if not cli:
        return check()
    subprocess.Popen(["cmd", "/c", "title Cutroom: вход в Claude & echo Откроется браузер: войдите в аккаунт Claude. &&",
                      cli, "auth", "login"], creationflags=NEWCON)
    with _lock:
        _state["login_opened"] = time.time()
    return status()


def startup() -> None:
    """Check at launch; if not signed in, open the sign-in window once (setting claude_auto_login)."""
    st = check()
    if st["found"] and not st["logged_in"] and load_settings().get("claude_auto_login", True):
        open_login()
        # keep re-checking for a few minutes so the header turns green by itself
        for _ in range(60):
            time.sleep(5)
            if check()["logged_in"]:
                break


def start_background() -> None:
    threading.Thread(target=startup, daemon=True, name="claude-link").start()
