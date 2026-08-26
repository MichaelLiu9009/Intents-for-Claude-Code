"""hook forwarding bridge (M13) — one-way mailbox from the CLI hook
surface to the engine.

Claude Code spawns this process on hook events, handing it one event's
JSON on stdin; this does exactly one thing: locate
runtime/engine.json (the engine's port ground truth), POST the event
verbatim to /api/hook, and exit 0. **Never returns a decision** — the
v1 hook surface is a side channel (zero hot-path cost); the reply
channel is the cockpit card's key/line → PTY. Blocking arbitration
(PreToolUse) is a documented escalation lever, off by default
(docs/M13-COCKPIT.md).

Two hard rules:
- **stdlib only, run straight off its file path** (provision casts
  the command as `"<python>" "<this file>" "<workspace>"`) — a hook
  command line has no PYTHONPATH to rely on, so this file never
  imports commander.
- **the mailbox never bites back at the CLI**: engine absent /
  timeout / bad payload all exit 0 silently; the POST times out at
  4s, ahead of the hook timeout in settings (5s) going stale.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

POST_TIMEOUT_S = 4.0


def _engine_info(argv: list[str]) -> dict | None:
    """argv[1] = workspace (cast in by provision); absent that, walk
    up from cwd — the hook's cwd is the instance home
    (workspace/instances/<mode>), two levels up lands there."""
    roots: list[Path] = []
    if len(argv) > 1 and argv[1].strip():
        roots.append(Path(argv[1]))
    cwd = Path.cwd()
    roots += [cwd, *cwd.parents]
    for d in roots:
        p = d / "runtime" / "engine.json"
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
    return None


def main(argv: list[str] | None = None) -> int:
    try:
        raw = sys.stdin.buffer.read()
        info = _engine_info(argv if argv is not None else sys.argv)
        port = (info or {}).get("http")
        if raw and port:
            req = urllib.request.Request(
                f"http://127.0.0.1:{int(port)}/api/hook", data=raw,
                headers={"Content-Type": "application/json; charset=utf-8"})
            urllib.request.urlopen(req, timeout=POST_TIMEOUT_S).read()
    except Exception:
        pass                        # mailbox failures never bite the CLI
    return 0


if __name__ == "__main__":
    sys.exit(main())
