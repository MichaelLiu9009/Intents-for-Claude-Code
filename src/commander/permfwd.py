"""PermissionRequest forwarding bridge (M18) — the ask half of blocking
arbitration.

The CLI spawns this process when it needs a permission decision: stdin
carries {tool_name, tool_input, cwd, permission_suggestions, …}; this
long-polls a POST to the engine's /api/perm and **waits for a human**.
The engine answers allow/deny → output the decision, and the CLI's
native dialog never paints. An allow may carry `grant` — the
harness's own PermissionUpdate objects — which is relayed verbatim
as `updatedPermissions`, and the CLI then persists the rule into this
seat's own settings (that is what the native card's don't-ask-again
row does; live-fire 2026-08-25). Everything else (defer / timeout / engine
absent / bad payload / crash) = **silently exit 0**, and the CLI's
native flow carries on unchanged.

Carried over verbatim from the old repo's precedent
(docs/M18-APPROVAL.md §0):
- **fail-safe, never fail-open**: an allow can only come from a human
  pressing "allow". Worst case, this file is invisible. Exit code is
  always 0 — 2 would mean deny, and a crash must never deny on a
  human's behalf.
- **bytes → utf-8, never through the text stream** (old-repo
  2026-08-10 CJK precedent: Windows' sys.stdin decodes by locale,
  shredding Chinese into orphaned surrogates).
- stdlib only, run straight off its file path, never imports
  commander (same rule as hookfwd).
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

# The engine-side park dies first at 290s; this allows 8s more slack
# for the network, still ahead of the 300s on the settings hook entry
# going stale — who dies first matters: the CLI cutting off the hook
# = defer, harmless
POLL_TIMEOUT_S = 298.0


def _engine_port(argv: list[str]) -> int | None:
    roots: list[Path] = []
    if len(argv) > 1 and argv[1].strip():
        roots.append(Path(argv[1]))
    cwd = Path.cwd()
    roots += [cwd, *cwd.parents]
    for d in roots:
        p = d / "runtime" / "engine.json"
        if p.is_file():
            try:
                port = json.loads(p.read_text(encoding="utf-8")).get("http")
                return int(port) if port else None
            except (OSError, ValueError, TypeError):
                continue
    return None


def main(argv: list[str] | None = None) -> int:
    decision = None
    grant = None
    try:
        raw = sys.stdin.buffer.read()
        port = _engine_port(argv if argv is not None else sys.argv)
        if raw and port:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/perm", data=raw,
                headers={"Content-Type": "application/json; charset=utf-8"})
            with urllib.request.urlopen(req, timeout=POLL_TIMEOUT_S) as r:
                ans = json.loads(r.read().decode("utf-8", "replace"))
            d = (ans or {}).get("decision")
            if d in ("allow", "deny"):
                decision = d
                # "Always allow" -> the harness's own
                # PermissionUpdate objects, handed straight back so
                # the CLI banks them itself (live-fire 2026-08-25).
                g = (ans or {}).get("grant")
                if d == "allow" and isinstance(g, list) and g:
                    grant = g
    except Exception:
        decision = grant = None     # every failure mode is silent (defer)
    if decision is not None:
        try:
            dec = {"behavior": decision}
            if grant:
                dec["updatedPermissions"] = grant
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": dec}}), flush=True)
        except Exception:
            pass                    # even stdout broken = defer, still 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
