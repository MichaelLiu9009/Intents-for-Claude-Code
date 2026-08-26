"""Instance provisioning — idempotent (CASELAW 29): engine-owned
files are rewritten every time, agent-owned files are never touched
(CASELAW 28).

M1's home (sidecar) has only two things: CLAUDE.md (identity +
discipline) and scratch/. Dynamic memory belongs to harness memory;
long-term assets go through the intent catalog (a later batch,
PRODUCT2 draft 12). Scenario/boundary/intent customizations from the
old repo are not migrated (user ruling 2026-08-10).
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from .. import defaults
from . import config as wsconfig


def instance_home(workspace: Path, module: str) -> Path:
    return workspace / defaults.INSTANCES_DIRNAME / module


def posix_rule(s: str) -> str:
    """POSIX-normalized absolute path -> permission rule form (plain
    string, easy to diff cross-platform). The CLI normalizes paths to
    POSIX before matching, collapsing the Windows drive letter into
    one segment:

        Windows  D:/intentos/ws     -> //d/intentos/ws
        mac/linux /Users/y/ws       -> //Users/y/ws

    Drive-letter collapsing only happens when a drive letter is
    present — the same policy renders identically on both platforms,
    with the same rule structure (verified 2026-08-12: the Windows
    form has been confirmed live-fire)."""
    drive, sep, rest = s.partition(":")
    if sep and len(drive) == 1 and drive.isalpha():     # Windows drive letter
        return f"//{drive.lower()}/{rest.lstrip('/')}"
    return "//" + s.lstrip("/")                         # already an absolute path


def _rule_path(p: Path) -> str:
    return posix_rule(p.resolve().as_posix())


def _engine_territory(workspace: Path, home: Path) -> list[str]:
    """The boundary the engine draws for itself (accountability
    mechanism 2026-08-12) — the boundary doesn't rely on agent
    self-discipline, it's welded shut by config (same philosophy as
    the loopback guardrail).

    **Live-fire precedent (probe five)**: (1) the `Read`/`Edit` deny
    blocks built-in tools, **and also blocks recognized file-reading
    commands like PowerShell's `Get-Content`**; (2) **the
    `Write(path)` rule doesn't participate in file-permission
    matching — only `Edit(path)` counts** (confirmed by the CLI
    itself: Edit covers every file-writing tool); (3) it can't block
    an arbitrary subprocess (a self-written script opening a file) —
    that gap is covered by "even if changed, it doesn't count":
    engine-owned files are rewritten on every boot, and the ledger is
    engine-single-writer.

    Calibration: **the truth layer isn't even given read access**
    (always go through the query surface — opening the DB directly
    would bypass accounting and trimming); **the ledger is
    write-denied only** (the troubleshooting ladder explicitly wants
    the agent to read the journal); `settings.local.json` is
    write-denied = the agent can never grant itself permissions
    (pre-authorization is not a mechanical rubber stamp) — and since
    the 2026-08-24 consolidation moved the always-allow ledger into
    `<workspace>/config.json`, **that file is on the floor too**
    (audit 2026-08-25: it was missing, which left the sentence above
    literally false — PERM_ALLOW is materialized verbatim into every
    seat's allow list and into x·solo's --allowedTools, so a seat
    that could edit it could grant itself anything, everywhere). The
    engine writes it as itself, via config.grant(), and a human
    editing it in an ordinary editor is unaffected: deny binds the
    CLI seats only."""
    # state.db* — WAL mode also has -wal / -shm siblings, cover them too
    db = _rule_path(workspace / "state.db") + "*"
    records = _rule_path(workspace / defaults.RECORDS_DIRNAME)
    utility = _rule_path(workspace / "utility")
    cfg = _rule_path(workspace / wsconfig.NAME)
    h = _rule_path(home)
    return [
        f"Read({db})", f"Edit({db})",           # truth layer: always via the query surface
        f"Edit({cfg})",                         # the always-allow ledger (PERM_ALLOW):
                                                # engine-written, human-editable, never
                                                # writable by a seat
        f"Edit({records}/**)",                  # ledger: readable, not writable
        f"Edit({utility}/**)",                  # engine territory (protocol render
                                                # artifacts land here; v16: procedure
                                                # is now built-in, no longer written to disk)
        f"Edit({h}/CLAUDE.md)",                 # everything below is engine-owned
        f"Edit({h}/.mcp.json)",
        f"Edit({h}/.claude/**)",                # skills / settings /
                                                # settings.local (self-authorization gate)
    ]


def provision_home(workspace: Path, token: str | None = None,
                   extra_allow: list[str] | None = None,
                   extra_deny: list[str] | None = None) -> Path:
    """CLAUDE.md is the static instruction surface (byte-stable,
    cache-prefix friendly): intent provisioning was changed to "fetch
    on boot" — the agent calls intent_memory_index at start of work,
    all dynamic content flows through tool results, the single source
    of truth is the store, so there's never a stale copy (decided
    2026-08-11). token = the caller identity the engine mints, written
    into the bridge's env and carried back on every call — identity is
    a mechanical ground truth issued by the engine; agent self-reports
    don't count."""
    home = instance_home(workspace, defaults.OS_MODULE)
    # scratch short-lifespan rule (user ruling 2026-08-13): a scratch
    # pad — the engine clears it on every provisioning pass to
    # encourage putting things in their proper place (finished work
    # goes into toolkit / submit takes a snapshot / task artifacts go
    # into the task directory). What's preserved is memory / toolkit /
    # task records.
    shutil.rmtree(home / "scratch", ignore_errors=True)
    (home / "scratch").mkdir(parents=True, exist_ok=True)
    (home / "CLAUDE.md").write_text(defaults.HOME_CLAUDE_MD,
                                    encoding="utf-8")
    # third tier of the three knowledge layers (INTENT_SPEC §3c):
    # skills are deep-loaded on demand — engine-owned files are
    # rewritten on every provisioning pass; the protocol body never
    # grows thicker, bulk goes into skills instead. The skill surface
    # is trimmed per role — the knowledge package is itself part of
    # the role definition.
    # whole-region rewrite: the skills directory is engine territory;
    # old volumes (leftovers from renames) get swept too
    shutil.rmtree(home / ".claude" / "skills", ignore_errors=True)
    for name, text in (("task-delivery", defaults.SKILL_TASK_DELIVERY_MD),
                       ("intent-creation",
                        defaults.SKILL_INTENT_CREATION_MD)):
        d = home / ".claude" / "skills" / name
        d.mkdir(parents=True, exist_ok=True)
        # substitute the <workspace> placeholder with the real path
        # (the toolkit boundary needs to be resolvable to be usable)
        (d / "SKILL.md").write_text(
            text.replace("<workspace>", str(workspace.resolve())),
            encoding="utf-8")
    # permission floor (2026-08-11, playground live-fire precedent: a
    # fresh session starts in manual mode, and package lives outside
    # home — every task's first step, Read(package.md), hit the human
    # gate, and if the gate wasn't cleared within 15 minutes the
    # time-limit rule reaped it). Within the contract there are only
    # two work surfaces: runtime (the task-delivery rule requires
    # reading the package/task directory) and utility (home of
    # protocol render artifacts) — both get minted into
    # additionalDirectories. settings.json is engine-owned (rewritten
    # on every provisioning pass); the user's own authorizations live
    # in settings.local.json, which the engine never touches.
    ws = workspace.resolve()
    src_root = Path(__file__).resolve().parents[2]
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    # hook surface (M13, docs/M13-COCKPIT.md): all Notification
    # subtypes (matcher left blank) + Stop -> the hookfwd mailbox
    # (fire-and-forget, never returns a decision) — this is how the
    # cockpit gets full voice coverage; replies travel via the card's
    # key/line -> PTY. hookfwd is pure stdlib and runs straight from
    # its file path: the hook command line has no PYTHONPATH to rely
    # on. settings.json is entirely engine-owned (rewritten on every
    # provisioning pass); the user's own authorizations live in
    # settings.local.json, which the engine never touches.
    fwd_cmd = (f'"{sys.executable}" '
               f'"{src_root / "commander" / "hookfwd.py"}" "{ws}"')
    hook = [{"hooks": [{"type": "command", "command": fwd_cmd,
                        "timeout": defaults.HOOK_TIMEOUT_S}]}]
    # M18 approval-only window: PermissionRequest -> permfwd (blocking
    # arbitration, waits up to 300s for a human — this hook is
    # drawing the dialog on the CLI's behalf, it can't be cut off at
    # 5s)
    perm_cmd = (f'"{sys.executable}" '
                f'"{src_root / "commander" / "permfwd.py"}" "{ws}"')
    perm_hook = [{"hooks": [{"type": "command", "command": perm_cmd,
                             "timeout": defaults.PERM_HOOK_TIMEOUT_S}]}]
    # private-memory pinning (user ruling 2026-08-12): memory files by
    # default (per the docs) index at the **git repo root** — all
    # subdirectories/worktrees under the same repo share one store.
    # When a workstation is embedded inside the repo, each instance's
    # private property would get mixed into that same store, and
    # would also collide with dev sessions. autoMemoryDirectory pins
    # it to the instance's own home instead: one instance, one store
    # — B6's multi-seat isolation falls out naturally. **The engine
    # only pins the location, never reads or writes the store's
    # contents** — that is the agent's own private property (a mirror
    # image of the engine's own deny-protected territory), which is
    # why it doesn't go on the deny list either. Live-fire precedent
    # (probe seven): pinning a subdirectory inside the repo succeeded,
    # zero leakage into the repo-root shared store.
    (home / defaults.MEMORY_DIRNAME).mkdir(parents=True, exist_ok=True)
    # union rendering endpoint (M16 §4): extra_allow = the compiled
    # union of every provisioned intent for this module (validated
    # via boundary.union_render — the symbol this comment used to
    # name, union_allow, never existed; boundary itself is a cold
    # standby since the 2026-08-24 consolidation). Writing
    # it into allow is the hop where it "takes material effect" — but
    # the deny floor sits in the same file, and deny beats allow: the
    # union can never override engine territory.
    perms = {"additionalDirectories": [
        str(ws / defaults.RUNTIME_DIRNAME),
        str(ws / "utility"),
        # toolkit neutral zone (M20 §2d): sidecar is a read-write seat
        # (drafting tools, surgical cleanup all happen here) —
        # gap-precedent 2026-08-13: every x· seat had it, yet the
        # maintenance seat itself was locked out
        str(ws / "toolkit")],
        # the MCP front door is the engine's own door, so its approval
        # belongs on the engine floor — accounting/query calls must
        # never stall on permissions (live-fire 2026-08-12: these
        # rules used to sit only in the human-approved local file, and
        # stripping that broke accounting; the floor now covers it).
        # PERM_ALLOW = the always-allow ledger (config.json, effective
        # for every seat).
        "allow": ["mcp__intentOS"] + list(defaults.PERM_ALLOW),
        # engine territory (accountability mechanism): deny is
        # evaluated before allow and wins across every scope
        "deny": _engine_territory(workspace, home)}
    if extra_allow:
        perms["allow"] += list(extra_allow)
    if extra_deny:
        # compiled deny (promotion-scheme tier three) is appended
        # after the territory floor, de-duplicated
        perms["deny"] += [d for d in extra_deny
                          if d not in perms["deny"]]
    (home / ".claude" / "settings.json").write_text(json.dumps(
        {"permissions": perms,
         "autoMemoryDirectory": str((home / defaults.MEMORY_DIRNAME)
                                    .resolve()),
         # sidecar-seat thinking tier (user ruling 2026-08-23: the
         # compiling seat is pinned to high, not xhigh — the creation
         # flow requires deliberation: how to write a scenario, which
         # steps are mechanical, where artifacts go). **effortLevel is
         # the current knob** (live-fire 2026-08-15: the CLI banner
         # reports effort; leaving it unpinned inherits the user's
         # global xhigh); the env one is kept as a fallback for older
         # versions.
         "effortLevel": defaults.SIDECAR_EFFORT,
         "env": {"MAX_THINKING_TOKENS": str(defaults.SIDECAR_THINKING)},
         # §2f telemetry bus: PreToolUse -> the same mailbox
         # (fire-and-forget, never returns a decision) — the engine
         # files task-event records against the seat's current active
         # order
         "hooks": {"Notification": hook, "Stop": hook,
                   "PreToolUse": hook,
                   "PermissionRequest": perm_hook}},
        ensure_ascii=False, indent=2), encoding="utf-8")
    # accounting-surface wiring: the host CLI reads .mcp.json from cwd
    # and launches the bridge itself (engine-owned, rewritten every
    # time; the first run needs a human to approve this server once in
    # the CLI — a one-time step)
    env = {"PYTHONPATH": str(src_root)}
    if token:
        env[defaults.MCP_TOKEN_ENV] = token
    (home / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"intentOS": {
            "command": sys.executable,
            # M26 (4): the management-seat surface — create/retrieve/
            # account, zero execution verbs
            "args": ["-m", "commander.mcp", str(workspace.resolve()),
                     "--face", "admin"],
            "env": env}}},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return home


def provision_proto_home(workspace: Path, pname: str,
                         token: str | None = None) -> Path:
    """M26 §3: a protocol's household registration (x·<name>) — the
    home of a resident-session seat.

    Three properties of this registration: (1) **it never moves** —
    home and memory/ persist across sessions, Shutdown only stops the
    process; (2) **permission is territory** — this seat accumulates
    native harness authorizations in its own settings.local.json
    (workspace boundary = permission boundary), the engine only mints
    the deny floor and never grants approvals on the user's behalf;
    (3) **a lean interface** — MCP exposes only the exec surface
    (task_done/ask_user/perm_gate); unused verbs are entropy that lets
    the executor drift from its intended behavior (user ruling
    2026-08-22).

    Engine-owned files (CLAUDE.md/settings.json/.mcp.json) are
    rewritten on every provisioning pass; scratch is cleared; memory/
    and settings.local.json are never touched."""
    seat = defaults.XPROTO_PREFIX + pname
    home = workspace / defaults.INSTANCES_DIRNAME / seat
    ws = workspace.resolve()
    toolkit = ws / "toolkit"
    shutil.rmtree(home / "scratch", ignore_errors=True)
    (home / "scratch").mkdir(parents=True, exist_ok=True)
    (home / defaults.MEMORY_DIRNAME).mkdir(parents=True, exist_ok=True)
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / "CLAUDE.md").write_text(
        defaults.PROTO_HOST_CLAUDE_MD
        .replace("{name}", pname).replace("{toolkit}", str(toolkit)),
        encoding="utf-8")
    src_root = Path(__file__).resolve().parents[2]
    fwd_cmd = (f'"{sys.executable}" '
               f'"{src_root / "commander" / "hookfwd.py"}" "{ws}"')
    bus_hook = [{"hooks": [{"type": "command", "command": fwd_cmd,
                            "timeout": defaults.HOOK_TIMEOUT_S}]}]
    (home / ".claude" / "settings.json").write_text(json.dumps(
        {"permissions": {
            "additionalDirectories": [
                str(ws / defaults.RUNTIME_DIRNAME),   # package lives in the task directory
                str(ws / "utility"),                  # protocol render artifacts
                str(toolkit)],
            "allow": ["mcp__intentOS"] + list(defaults.PERM_ALLOW),
            "deny": _engine_territory(workspace, home)},
         "enableAllProjectMcpServers": True,
         "autoMemoryDirectory": str((home / defaults.MEMORY_DIRNAME)
                                    .resolve()),
         # the hosting seat needs deliberation (multi-round
         # aggregation), pinned at the host-seat tier
         "effortLevel": defaults.HOST_EFFORT,
         "env": {"MAX_THINKING_TOKENS": str(defaults.HOST_THINKING)},
         "hooks": {"PreToolUse": bus_hook}},   # §2f telemetry bus (same rule)
        ensure_ascii=False, indent=2), encoding="utf-8")
    env = {"PYTHONPATH": str(src_root)}
    if token:
        env[defaults.MCP_TOKEN_ENV] = token
    (home / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"intentOS": {
            "command": sys.executable,
            # surface split (S2/C1): the bracket seat uses the proto
            # surface (+step_done, ask_user swaps in bracket-rule copy)
            "args": ["-m", "commander.mcp", str(ws), "--face",
                     defaults.MCP_SEAT_PROTO],
            "alwaysLoad": True,
            "env": env}}},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return home


def solo_allow_rules(workspace: Path,
                     extra_allow: list[str] | None = None) -> list[str]:
    """x·solo's allow floor, single-sourced (P1-a 2026-08-23): settings
    and the spawn flag (--allowedTools) share this one list — when a
    headless home has no trust record, the harness won't honor
    settings' allow, so the floor must also travel via the caller's
    flag. Permission-surface consolidation (2026-08-24): the
    PERM_ALLOW ledger (config.json — human-approved "Always" entries /
    user hand-written) is merged in here — the allow that's effective
    for every seat now has this single source."""
    toolkit = workspace.resolve() / "toolkit"
    allow = ["mcp__intentOS", f"Read({_rule_path(toolkit)}/**)"]
    allow += [a for a in list(defaults.PERM_ALLOW)
              + list(extra_allow or []) if a not in allow]
    return allow


def provision_solo_home(workspace: Path,
                        extra_allow: list[str] | None = None,
                        token: str | None = None) -> Path:
    """§2m v9 home of the general-purpose execution seat (x·solo): the
    execution seat for every standalone intent. Structurally identical
    to the protocol seat (headless three-piece set + bus hook + deny
    floor), with three differences: CLAUDE.md has no skills (steps
    ride along in the package per order); boundary = the compiled
    union of every standalone intent (computed and passed in by the
    caller); the settings env pins the thinking budget (v9: the
    low-overhead surface is a mechanical commitment). Engine-owned,
    rewritten idempotently on every provisioning pass."""
    home = (workspace / defaults.INSTANCES_DIRNAME / defaults.XSOLO_SEAT)
    ws = workspace.resolve()
    toolkit = ws / "toolkit"
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / "CLAUDE.md").write_text(
        defaults.XSOLO_CLAUDE_MD.replace("{toolkit}", str(toolkit)),
        encoding="utf-8")
    allow = solo_allow_rules(workspace, extra_allow)
    src_root = Path(__file__).resolve().parents[2]
    fwd_cmd = (f'"{sys.executable}" '
               f'"{src_root / "commander" / "hookfwd.py"}" "{ws}"')
    bus_hook = [{"hooks": [{"type": "command", "command": fwd_cmd,
                            "timeout": defaults.HOOK_TIMEOUT_S}]}]
    (home / ".claude" / "settings.json").write_text(json.dumps(
        {"permissions": {
            # permission-surface consolidation (user final ruling
            # 2026-08-24): mode belongs to the harness's auto — the
            # engine pre-writes --permission-mode on the spawn flag
            # (pinning auto in settings gets ignored by project scope;
            # only the flag counts); defaultMode is no longer pinned
            # here — the allow floor + perm_gate cover whatever falls
            # outside auto.
            "additionalDirectories": [
                str(ws / defaults.RUNTIME_DIRNAME), str(toolkit)],
            "allow": allow,
            "deny": _engine_territory(workspace, home)},
         "enableAllProjectMcpServers": True,
         # the low-overhead surface is a mechanical commitment: the
         # tier must be pinned too, or it inherits the user's global
         # tier
         "effortLevel": defaults.XSOLO_EFFORT,
         "env": {"MAX_THINKING_TOKENS": str(defaults.XSOLO_THINKING)},
         "hooks": {"PreToolUse": bus_hook}},
        ensure_ascii=False, indent=2), encoding="utf-8")
    env = {"PYTHONPATH": str(src_root)}
    if token:
        env[defaults.MCP_TOKEN_ENV] = token
    (home / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"intentOS": {
            "command": sys.executable,
            # seat trimming (user ruling 2026-08-16): the execution
            # seat sees only the three-piece set. Second cut completed
            # (08-17): alwaysLoad opts out of deferral — the
            # three-piece schema stays resident in the prompt, so the
            # extra round-trip to ToolSearch for the doorknob
            # disappears (live-fire: trimming to three still hit
            # deferral — it's the harness's default policy for MCP,
            # independent of tool count). M26 (4): the --face form
            # (the bridge side still supports the old positional
            # argument).
            "args": ["-m", "commander.mcp", str(ws), "--face",
                     defaults.MCP_SEAT_EXEC],
            "alwaysLoad": True,
            "env": env}}},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return home
