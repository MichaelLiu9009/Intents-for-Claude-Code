"""Headless host — the shape of the x·solo execution seat (born M16
§5d; after the 2026-08-24 permission-surface consolidation, its only
resident is the general-purpose execution slot).

Differs from PtyHost only in host shape: no resident process — a
`claude -p` is spawned when a task arrives, and it disperses once
settled. The six-face contract is still honored (alive/ready/trusted
are always true — availability is judged once at power-on by
available(); missing CLI says so loudly, never silently).

Delivery does not go through inject_chat (that's for the resident
composer): deliver(tid, line) spawns one process per unit, with
stdout/stderr landing in the task directory (a debugging ladder: the
log sits right next to the ledger). Settlement, deadlines, and
settling all reuse the task-plane's current machinery — when the
deadline rule reaps a unit, its process is cleared out along with it
by stop().
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from .. import defaults


class HeadlessHost:
    def __init__(self, home: Path, model: str, task_root: Path,
                 perm_tool: str | None = None,
                 tools: str | None = None,
                 allow_tools: list[str] | None = None):
        self.home = home
        self.model = model
        self.task_root = task_root
        # §2i M22: the front door for permission delegation — headless
        # no longer dies outright on hitting an approval surface; it
        # goes through an MCP tool back to the engine, raises a card,
        # and waits for a human (allow/deny). None = the old shape.
        self.perm_tool = perm_tool
        # Second cut completed (2026-08-17): built-in tool-surface
        # allowlist (CLI --tools). The interpreter seat has no use for
        # the WebSearch/Agent/TodoWrite row — a smaller surface means
        # a smaller chassis, and it also avoids triggering deferral
        # (the ToolSearch round-trip disappears along with it). None =
        # don't trim.
        self.tools = tools
        # P1-a live-fire 2026-08-23: headless's home has never been
        # through the trust wizard (`-p` never paints a screen), and
        # the harness does **not** honor the project settings allow
        # floor for an untrusted directory — the mcp__intentOS
        # allowlist cast into settings is dead weight there, and even
        # the task_done settlement falls through to a perm_gate card.
        # The floor has to travel on the caller's own flag instead:
        # --allowedTools comes from the invoker and doesn't look at
        # directory trust. Rule grammar matches settings (tool-name /
        # Read(...) form).
        #
        # Re-confirmed 2026-08-25 (probes K/L, CASELAW 64 (5)): the
        # boundary is **scope**, not trust as such. The same MCP rule
        # in a seat's own settings.local.json IS honored on a home
        # with no trust record (zero gate calls); in project
        # settings.json it is not (the gate fires). The engine writes
        # project scope, so this flag stays — and it is why the CLI's
        # own always-allow landing (localSettings) works without it.
        self.allow_tools = list(allow_tools or [])
        self._procs: dict[int, subprocess.Popen] = {}
        self._cli = shutil.which("claude")

    def available(self) -> bool:
        return self._cli is not None

    # ---- six-face contract (what the engine checks a host for) --------
    def alive(self) -> bool:
        return self._cli is not None

    def ready(self) -> bool:
        return True                     # no TUI to mount, so always ready

    def trusted(self) -> bool:
        return True                     # headless has no wizard screen

    def replay(self) -> str:
        return ""

    def write_raw(self, data: str) -> None:
        pass                            # no terminal, no keystroke surface

    def inject_chat(self, text: str) -> None:
        # Shouldn't reach here: service tasks always go through
        # deliver(tid, line). Left as a non-crashing fallback
        # (contract completeness), but with no tid there's nowhere for
        # the log to land.
        self.deliver(0, text)      # return value has nowhere to go — fallback path is unbilled

    def spawn_args(self, sid: str) -> list[str]:
        """Single source for the command line (split out so a guard
        can see it — a cut like trimming the tool surface should be
        verifiable without ever spawning a process).

        **The unit never goes into argv** (live-fire 2026-08-17, fifth
        salvo, task 3): `-p <long multiline text>` passed through
        Windows CreateProcess evaporates everything after the first
        newline — the execution slot only received the title line.
        The full text now goes entirely through **stdin** (`claude -p`
        with no arg reads stdin): a pipe doesn't touch the command
        line, so it's immune to both length and newlines. This also
        rewrites the fourth salvo's diagnosis: that execution slot's
        Read was probably not "saw the path and read it out of
        habit" — the full text plain never arrived, and it was
        rescuing itself via the path."""
        args = [self._cli, "-p", "--model", self.model,
                "--session-id", sid]
        # Permission-surface consolidation (2026-08-24): auto mode
        # rides in on a pre-written flag (headless's home has no trust
        # record, settings can't be relied on — same family as P1-a);
        # read dynamically at spawn time, so config.json overrides
        # take effect; empty string = no flag.
        if defaults.SEAT_PERMISSION_MODE:
            args += ["--permission-mode", defaults.SEAT_PERMISSION_MODE]
        if self.tools:
            args += ["--tools", self.tools]
        if self.perm_tool:
            args += ["--permission-prompt-tool", self.perm_tool]
        if self.allow_tools:
            # variadic flag goes last (it swallows every bare arg after it)
            args += ["--allowedTools", *self.allow_tools]
        return args

    # ---- delivery (spawns a process per task) --------------------------
    def deliver(self, tid: int, line: str) -> str | None:
        """Spawn one unit. Returns **this unit's session id** (the
        other half of the transcript-cut coordinate); None = the CLI
        is missing (the caller takes the breakpoint path — never
        silent).

        The session id is **pinned here** (live-fire precedent
        2026-08-15, AUDIT red ①): the engine's `_host_session` was
        learned from the host's hook, and that's the **sidecar's**
        session — stamping it onto an execution-slot unit means any
        transcript cut necessarily misses (looking in the x· seat's
        directory for the sidecar's file), `out`/`calls` stay
        permanently NULL, and the low-cost surface can't prove
        itself. `--session-id` is a native CLI surface: we issue the
        number, we no longer guess it.

        stdout/stderr land in the task directory's headless.log — the
        debugging ladder sits right next to the ledger. Windows
        precedent: child process stdio must be explicit utf-8, or
        Chinese text shatters the instant it enters the box."""
        if self._cli is None:
            return None
        d = self.task_root / str(tid)
        d.mkdir(parents=True, exist_ok=True)
        log = open(d / "headless.log", "a", encoding="utf-8")
        kw = {}
        if sys.platform == "win32":
            kw["creationflags"] = 0x08000000        # CREATE_NO_WINDOW
        env = None                                   # inherited; token lives in .mcp.json
        sid = str(uuid.uuid4())
        args = self.spawn_args(sid)
        p = subprocess.Popen(
            args, cwd=str(self.home), stdout=log, stderr=log,
            stdin=subprocess.PIPE, env=env, **kw)
        try:
            p.stdin.write(line.encode("utf-8"))
            p.stdin.close()                  # EOF = unit handed off, CLI is running
        except OSError:
            pass                             # instant process death waits for reaping; delivery doesn't crash
        self._procs[tid] = p
        return sid

    def reap(self, tid: int) -> None:
        """Clear a unit at settlement (settle/timeout): if the process
        is still alive, kill it — the deadline rule's verdict has to
        land on the process itself, no orphans left behind.

        **Tree** kill (audit 2026-08-25): p.kill() is a bare
        TerminateProcess on Windows and reaches only the node/claude
        process, leaving every tool subprocess that seat had spawned
        (bash, git, python, powershell) running — which is exactly
        the orphan this docstring promises not to leave. Same idiom
        as kernel/procrun._kill_tree."""
        p = self._procs.pop(tid, None)
        if p is not None and p.poll() is None:
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/PID", str(p.pid),
                                    "/T", "/F"], capture_output=True)
                else:
                    p.kill()
            except Exception:
                pass

    def stop(self) -> None:
        for tid in list(self._procs):
            self.reap(tid)
