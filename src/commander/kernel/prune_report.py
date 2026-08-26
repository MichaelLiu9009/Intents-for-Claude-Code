"""harness transcript-slicing tool (M15/M16 legacy, a holdover piece
after the permission-surface consolidation of 2026-08-24): slices
the host transcript by task_window —— still used for the completion
receipt (window_usage) and injection-receipt / bracket transcripts
(transcript_dir).

The transcript is **evidence, not a dependency** (soft dependency,
post-hoc analysis side): if it can't be sliced (compacted / deleted
/ host machine changed), that's written down as absent —— the
engine's real work has zero dependency on it.
(The materializing report compiler build retired along with the
pruner seat.)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_SLUG = re.compile(r"[^A-Za-z0-9]")


def transcript_dir(home: Path) -> Path:
    """The host transcript directory for a given seat's home (the
    observation point for the injection receipt, CASELAW 48)."""
    return Path.home() / ".claude" / "projects" / _SLUG.sub("-", str(home))


def _transcript_path(home: Path, session: str | None) -> Path | None:
    if not session:
        return None
    slug = _SLUG.sub("-", str(home))
    return (Path.home() / ".claude" / "projects" / slug
            / f"{session}.jsonl")


def window_usage(win: dict, home: Path) -> dict | None:
    """Material for the completion receipt (user ruling 2026-08-13):
    assistant usage totals within the window + tool-call count. If
    the transcript is unavailable, returns None (soft dependency ——
    the receipt is missing half its content, but the engine's real
    work has zero dependency on it)."""
    tr = _transcript_path(home, win.get("host_session"))
    if tr is None or not tr.is_file():
        return None
    try:
        text = tr.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    out = {"calls": 0, "out": 0, "cache_read": 0, "msgs": 0}
    for ln in text.splitlines():
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        ts = r.get("timestamp") or ""
        if not (win["t0_utc"] <= ts <= win["t1_utc"]):
            continue
        if r.get("type") != "assistant":
            continue
        m = r.get("message") or {}
        u = m.get("usage")
        if u:
            out["msgs"] += 1
            out["out"] += u.get("output_tokens", 0) or 0
            out["cache_read"] += u.get("cache_read_input_tokens", 0) or 0
        for c in m.get("content") or []:
            if isinstance(c, dict) and c.get("type") == "tool_use":
                out["calls"] += 1
    return out
