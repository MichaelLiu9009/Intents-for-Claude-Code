"""PTY host — the engine runs its own claude CLI inside its own ConPTY.

Precedent baked in (CASELAW 10/11/12/13/14/15/16): shim unwrapping,
env scrubbing, two-beat injection, single ESC, the wizard gate (a
virgin home's CLI is a wizard, not an agent — injection would tell
~/.claude.json this home is already trusted; the wizard itself is
answered directly by a human typing on the console face), readiness
= output byte count, tree-kill teardown.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from .. import defaults


def _find_claude() -> str:
    """CASELAW 10: spawn the real exe, not the shim shell. npm's
    claude on Windows is a .ps1/.cmd pair; the .cmd CALLs the real
    body."""
    found = shutil.which("claude")
    if not found:
        raise FileNotFoundError("claude CLI not on PATH")
    p = Path(found)
    if p.suffix.lower() == ".ps1":
        sib = p.with_suffix(".cmd")
        if sib.is_file():
            p = sib
    if p.suffix.lower() in (".cmd", ".bat"):
        try:
            body = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return str(p)
        for raw in re.findall(r'"([^"\n]+?\.exe)"', body, flags=re.IGNORECASE):
            cand = Path(raw.replace("%~dp0", str(p.parent) + "\\")
                           .replace("%dp0%", str(p.parent) + "\\"))
            try:
                cand = cand.resolve()
            except OSError:
                continue
            if cand.is_file():
                return str(cand)
    return str(p)


def _clean_env() -> dict:
    """CASELAW 11: strip the host's own Claude markers, or the child
    CLI thinks it's a nested session; strip the NO_COLOR family, or
    the TUI renders black-and-white."""
    env = dict(os.environ)
    for key in list(env):
        upper = key.upper()
        if upper.startswith("CLAUDE_CODE_") or upper in (
                "CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT",
                "CLAUDE_AGENT_SDK_VERSION",
                "NO_COLOR", "FORCE_COLOR", "CLICOLOR", "CLICOLOR_FORCE"):
            env.pop(key)
    return env


class PtyHost:
    """One host per CLI. on_output(str) fires back on the reader thread."""

    def __init__(self, cwd: Path, on_output=None, dims=(40, 120),
                 model: str = defaults.HOST_MODEL):
        self.cwd = cwd
        self.on_output = on_output or (lambda data: None)
        self._dims = dims
        self.model = model
        self._p = None
        self._buf: deque[str] = deque()     # replay buffer for late subscribers
        self._buf_len = 0
        self._out_bytes = 0
        self._born = 0.0
        self._trusted = False               # trust once, doesn't flip back within a session
        self._saw_untrusted = False         # saw the wizard (P1-b screen-flip gate signal)
        self._last_out = 0.0                # timestamp of the most recent output
        self._flip_t: float | None = None   # moment trust flipped true (screen-flip gate)

    # ---- lifecycle ---------------------------------------------------

    def start(self) -> None:
        from winpty import PtyProcess
        argv = [_find_claude(), "--model", self.model]
        # Permission-surface consolidation (user ruling 2026-08-24):
        # mode is pre-written by the engine onto the spawn flag
        # (project-scope settings entries pinning auto are ignored by
        # the harness, only the flag is authoritative); read
        # dynamically at spawn time — config.json overrides take
        # effect. Empty string = no flag (falls back to harness
        # default).
        if defaults.SEAT_PERMISSION_MODE:
            argv += ["--permission-mode", defaults.SEAT_PERMISSION_MODE]
        self._p = PtyProcess.spawn(
            argv, cwd=str(self.cwd), env=_clean_env(),
            dimensions=self._dims)
        self._born = time.monotonic()
        threading.Thread(target=self._read_loop, daemon=True,
                         name="pty-read").start()

    def _read_loop(self) -> None:
        while True:
            try:
                data = self._p.read(4096)
            except Exception:
                return                      # EOF = host died; M1 does not auto-respawn
            if not data:
                return
            self._out_bytes += len(data)
            self._last_out = time.monotonic()   # the other half of the P1-b quiet window
            self._buf.append(data)
            self._buf_len += len(data)
            while self._buf_len > 256_000 and len(self._buf) > 1:
                self._buf_len -= len(self._buf.popleft())
            self.on_output(data)

    def alive(self) -> bool:
        try:
            return self._p is not None and self._p.isalive()
        except Exception:
            return False

    def ready(self) -> bool:
        """CASELAW 15: readiness probe = output byte count + a
        conservative floor.

        P1-b second cut (live-fire 2026-08-23, two back-to-back
        hits): confirms the screen-flip gate. The first cut's "flip
        clears the byte count" deadlocked on the real machine — the
        banner bytes land first, the trust record is written after,
        so the flip wipes bytes already received, the composer goes
        silent from then on, and ready() stays permanently false.
        Changed to a **quiet window**: gate opens once flip is
        ≥SETTLE old *and* the last QUIET seconds saw no new bytes
        (the screen-flip burst has finished); a CAP-second hard
        ceiling backstops it (a slow machine or continuous repaint
        won't deadlock it). The gate is one-shot: it opens once, then
        lifts, and later injections proceed as normal."""
        if not (self._out_bytes >= defaults.READY_BYTES
                and time.monotonic() - self._born >= defaults.SPAWN_SETTLE):
            return False
        if self._flip_t is not None:
            now = time.monotonic()
            calm = (now - self._flip_t >= defaults.TRUST_FLIP_SETTLE_S
                    and now - self._last_out >= defaults.TRUST_FLIP_QUIET_S)
            if not calm and now - self._flip_t < defaults.TRUST_FLIP_CAP_S:
                return False
            self._flip_t = None             # gate lifts the instant it opens
        return True

    def trusted(self) -> bool:
        """CASELAW 14: the wizard screen is also a raw-mode TUI —
        indistinguishable from the stream alone. Only this
        file-level precondition can tell the wizard apart from the
        real composer. Unreadable file defaults to trusted (don't
        block what you can't judge); a home that's never been opened
        must be its first launch, hence a wizard. **Trust on an
        ancestor directory counts too** (observed 2026-08-10: the CLI
        inherits trust down the directory tree, but the record lands
        on the ancestor — an exact-match check would be a permanently
        closed false wizard)."""
        if self._trusted:
            return True
        try:
            data = json.loads((Path.home() / ".claude.json")
                              .read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return True
        projects = data.get("projects")
        if not isinstance(projects, dict):
            return True
        want = str(self.cwd).replace("\\", "/").rstrip("/").lower()
        for key, val in projects.items():
            k = str(key).replace("\\", "/").rstrip("/").lower()
            if ((want == k or want.startswith(k + "/"))
                    and (val or {}).get("hasTrustDialogAccepted")):
                self._trusted = True
                if self._saw_untrusted:
                    # P1-b: a seat that has seen the wizard hangs the
                    # screen-flip gate the instant trust flips true —
                    # the signal and the gate's release both live in
                    # ready() (quiet window + hard cap). A seat that
                    # was already trusted from the start never hangs
                    # this gate (protects idle seats).
                    self._flip_t = time.monotonic()
                return True
        self._saw_untrusted = True
        return False

    def replay(self) -> str:
        return "".join(self._buf)

    # ---- injection -----------------------------------------------------

    def write_raw(self, data: str) -> None:
        try:
            self._p.write(data)
        except Exception:
            pass

    def resize(self, cols: int, rows: int) -> None:
        """Real responsive terminal (user ruling 2026-08-23): once the
        front-end window geometry changes, pass cols×rows straight
        into ConPTY and let the TUI reflow itself — the display layer
        no longer forces a fit."""
        try:
            self._p.setwinsize(rows, cols)
            self._dims = (rows, cols)
        except Exception:
            pass                    # resize failing carries no weight (degrade rule)

    def inject_chat(self, text: str) -> None:
        """CASELAW 12: body text and \\r are written in two separate
        beats — a trailing \\r in the same chunk reads as a pasted
        newline, not a submit."""
        self.write_raw(text)
        time.sleep(defaults.PASTE_BEAT)
        self.write_raw("\r")

    def stop(self) -> None:
        """CASELAW 16: tree-kill, leave no orphans."""
        pid = None
        try:
            pid = self._p.pid
        except Exception:
            pass
        try:
            self._p.close()
        except Exception:
            pass
        if pid:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True)
