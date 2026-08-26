"""boundary — format gate and union for the compiled access list
(M16 §3/§4).

**COLD STANDBY** (permission-surface consolidation 2026-08-24, audit
2026-08-25): no production caller imports this module — the live
never_allow ceiling is engine._perm_capped (substring law), and the
declaration surface that fed check_rules is retired. Kept as tested
spare parts (test_m16 3a-3f2 exercise it directly): the format triage
here is the landing pad if per-rule vetting ever comes back.

Pure functions, touch neither DB nor disk. Two consumers: the
materialization flow validates once before landing to the DB;
provision **re-checks** once before rendering (the DB file is an
on-disk object, guards against hand edits — a single-point check
doesn't count as a gate).

Validation philosophy (revised, user ruling 2026-08-13): **two-tier
triage** —
- **Syntax sickness gets dropped per-rule** (named, not silent): a
  syntactically broken rule is a **dead rule**, not a dangerous one
  (never matches, the prompt still fires — fail-safe direction);
  rejecting the whole batch only takes legitimate entries down with
  it, at the cost of pure friction and zero safety gain (first
  live-fire precedent: three legitimate PowerShell always entries
  were killed alongside one bare URL).
- **Shape sickness / ceiling hits still reject the whole batch**:
  wanting an always on ceiling content isn't a typo — it means the
  judge overstepped or got confused — the whole batch is
  untrustworthy, no slack on the safety-load-bearing part.
"""
from __future__ import annotations

import json
import re

# Tool surface that participates in file/command permission
# matching. Note Write is not in it — Probe Five precedent:
# Write(path) rules don't participate in file permission matching,
# only Edit(path) counts (per the CLI itself); including it would
# only manufacture rules that never take effect.
KNOWN_TOOLS = frozenset({
    "Read", "Edit", "Glob", "Grep", "Bash", "PowerShell",
    "WebFetch", "WebSearch", "NotebookEdit",
})
# spec checking is split by tool family (first live-fire precedent
# 2026-08-13, whole-batch-reject case: a one-size-fits-all path
# check mis-killed both command rules and URL rules):
#   path family — spec is a file path, POSIX (//drive/...) + no ..;
#   command family — spec is a command prefix, not a path (ceiling
#                    substring check still applies);
#   net family — spec only recognizes the domain: form (a bare URL
#               is a dead rule that never matches; the harness only
#               recognizes domain, and suggest already has one
#               ready-made).
PATH_TOOLS = frozenset({"Read", "Edit", "Glob", "Grep", "NotebookEdit"})
NET_TOOLS = frozenset({"WebFetch", "WebSearch"})
_DOMAIN_SPEC = re.compile(r"^domain:[A-Za-z0-9.*-]+$")

_RULE = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)(?:\((.*)\))?$", re.S)


def _rule_syntax(key: str, v: str) -> str | None:
    """Syntax sickness of a single rule (None = clean). Syntax
    sickness = dead rule, drop per-rule."""
    m = _RULE.match(v.strip())
    if m is None:
        # Prose-dimension entries (win32: / "read-only, no delete,
        # no modify" and the like) don't fit the rule syntax —
        # that's a caveat, not a permission
        return (f"'{v[:60]}' in {key} is not of the Tool or "
                f"Tool(specifier) form")
    tool, spec = m.group(1), m.group(2)
    if tool not in KNOWN_TOOLS:
        return (f"'{tool}' in {key} is not on the known tool surface "
                f"(Write takes no part in file-permission matching — "
                f"Probe Five)")
    if tool in PATH_TOOLS and spec:
        if ".." in spec:
            return (f"'{v[:60]}' in {key} carries .. (no upward path "
                    f"jumps)")
        if "/" in spec and not spec.startswith("//"):
            return (f"'{v[:60]}' in {key} path not POSIX-normalized "
                    f"(needs the //drive/… form, see posix_rule)")
    if tool in NET_TOOLS and spec and not _DOMAIN_SPEC.match(spec):
        return (f"'{v[:60]}' in {key} — network rules only recognize "
                f"the domain: form (a bare URL is a dead rule that "
                f"never matches; the evidence's verbatim no-ask rule "
                f"has one ready-made)")
    return None


def vet_rules(rules, policy: dict | None = None
              ) -> tuple[list[str], dict, list[str]]:
    """Two-tier triage (user ruling 2026-08-13) → (fatal, clean,
    dropped).

    Non-empty fatal = reject the whole batch (shape sickness /
    ceiling hit — the judge overstepped, the whole batch is
    untrustworthy); otherwise clean = the clean set after dropping
    syntax-sick entries, dropped = the named list of dropped
    entries (a sickness roster the consumer must surface, never
    silently).
    """
    fatal: list[str] = []
    dropped: list[str] = []
    clean: dict = {}
    if not isinstance(rules, dict):
        return ([f"shape: rules must be a dict, got "
                 f"{type(rules).__name__}"], {}, [])
    extra = set(rules) - {"always", "allow", "deny", "ask", "notes"}
    if extra:
        fatal.append(f"shape: unknown keys {sorted(extra)} (only "
                     f"always/allow/deny/ask/notes)")
    if "notes" in rules and not (
            isinstance(rules["notes"], list)
            and all(isinstance(x, str) for x in rules["notes"])):
        fatal.append("shape: notes must be a list of str")
    never = [str(x) for x in
             ((policy or {}).get("security") or {}).get("never_allow", [])]
    for key in ("always", "allow", "deny", "ask"):
        vals = rules.get(key)
        if vals is None:
            continue
        if not isinstance(vals, list) or any(not isinstance(v, str)
                                             for v in vals):
            fatal.append(f"shape: {key} must be a list of str")
            continue
        kept: list[str] = []
        for v in vals:
            if key in ("always", "allow", "ask"):
                # allow is a ledger, not a grant, but it still goes
                # through the ceiling check — a ledger entry's
                # territory rule will eventually get promoted, so
                # it must be stopped at the door; ceiling = fatal,
                # not dropped
                hit = [n for n in never if n and n in v]
                if hit:
                    fatal.append(f"ceiling: '{v[:60]}' in {key} "
                                 f"touches never_allow {hit} — whole "
                                 f"batch refused")
                    continue
            sick = _rule_syntax(key, v)
            if sick:
                dropped.append("syntax: " + sick)
            else:
                kept.append(v.strip())
        clean[key] = kept
    if "notes" in rules and not fatal:
        clean["notes"] = rules["notes"]
    return fatal, clean, dropped


def check_rules(rules, policy: dict | None = None) -> list[str]:
    """Compat surface: the full sickness roster (fatal + dropped).
    Use vet_rules to decide "does it pass the gate" — this is only
    for inspecting "is there any sickness at all"."""
    fatal, _, dropped = vet_rules(rules, policy)
    return fatal + dropped


def union_render(rows: list[dict], policy: dict | None = None
                 ) -> tuple[list[str], list[str], list[str]]:
    """provision union (M16 §4 + §5f promotion scheme): re-checks
    per-intent batch by batch, a sick batch is **dropped whole**
    and named. Returns (allow render set, deny render set, sickness
    roster).

    **Only always and deny get rendered** — the allow tier is a
    candidate ledger; if it were rendered the prompt would never
    fire again, "repeat approval" would never happen, and the
    promotion path would be dead on arrival (a mechanical
    inevitability of §5f). ask is the harness's default behavior,
    not rendered.
    """
    always: set[str] = set()
    deny: set[str] = set()
    problems: list[str] = []
    for row in rows:
        try:
            rules = json.loads(row.get("rules") or "{}")
        except ValueError:
            problems.append(f"{row.get('intent')}: rules is not valid "
                            f"JSON (hand-edited on disk?) — batch "
                            f"dropped")
            continue
        fatal, clean, dropped = vet_rules(rules, policy)
        if fatal:
            problems.append(f"{row.get('intent')}: " + "; ".join(fatal)
                            + " — batch dropped")
            continue
        if dropped:
            # Syntax sickness dropped per-rule (two-tier triage):
            # named into the sickness roster, clean entries still
            # render
            problems.append(f"{row.get('intent')}: dropped "
                            f"{len(dropped)} rule(s) — "
                            + "; ".join(dropped))
        always.update(clean.get("always") or [])
        deny.update(clean.get("deny") or [])
    return sorted(always), sorted(deny), problems
