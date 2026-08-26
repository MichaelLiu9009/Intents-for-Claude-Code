"""§2u two-phase test helper: open ticket to provision -> write files into
the folder -> register by name.

The old API (procedure_submit / intent_update / protocol_submit /
register) has been split apart; the suite now goes through here
uniformly -- when the flow changes, fix it in one place, not twelve.
"""
import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from commander.kernel import wspace                      # noqa: E402
from commander.kernel.provision import instance_home     # noqa: E402


def home(eng):
    return instance_home(eng.workspace, eng.module)


def wdir(eng, name: str) -> Path:
    d = wspace.find(home(eng), name)
    if d is None:
        # Fixture landed straight in the store (didn't go through
        # intent_submit) -- provision from the row already in the store.
        it = eng.store.intent(name)
        if it is None and eng.store.proto_get(name) is not None:
            raise AssertionError(f"protocol '{name}' has no workspace "
                                 f"— open_proto first")
        assert it is not None, f"no such intent: {name}"
        d = wspace.provision(
            home(eng), name,
            {"name": name, "title": it.get("title") or "",
             "scenario": it.get("scenario") or name,
             "steps": it.get("steps") or "",
             "acceptance": it.get("instructions") or "",
             "tools": []})
    return d


def decl(eng, name: str) -> dict:
    d, err = wspace.read_decl(wdir(eng, name))
    assert d is not None, err
    return d


def edit(eng, name: str, **fields):
    """Editing the declaration = editing this intent (the folder is the
    source of truth)."""
    d = wdir(eng, name)
    cur, _ = wspace.read_decl(d)
    cur = cur or {}
    cur.update(fields)
    wspace.decl_path(d).write_text(
        json.dumps(cur, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return cur


def put_proc(eng, name, proc, code, declare=True):
    """Retired (user's call, night of 2026-08-16): procedure = physical
    layer of the control protocol, built into the engine, bound to keys
    -- tests no longer have a "write procedure into workspace" path.
    Any use site still calling this is a fossil; fail loudly, don't
    skip silently."""
    raise AssertionError(
        "put_proc is retired: procedures belong to the physical layer "
        "(engine built-in) — migrate the case to steps/tools, or test "
        "the wordlist gate instead")


def put_tool(eng, name: str, tool: str, body: str, ext: str = ".ps1",
             declare: bool = True):
    d = wdir(eng, name)
    (d / wspace.TOOLS_DIR).mkdir(exist_ok=True)
    (d / wspace.TOOLS_DIR / (tool + ext)).write_text(body, encoding="utf-8")
    if declare:
        cur, _ = wspace.read_decl(d)
        cur = cur or {}
        tools = list(cur.get("tools") or [])
        if tool not in tools:
            tools.append(tool)
        edit(eng, name, tools=tools)


def put_skill(eng, name: str, text: str):
    (wdir(eng, name) / wspace.SKILL_NAME).write_text(text, encoding="utf-8")


def register(post, name: str) -> dict:
    """Phase two: submit by folder = register = compile. Returns the
    engine's reply as-is."""
    return post({"verb": "workspace_submit", "name": name})


def open_intent(post, name: str, **fields) -> dict:
    """Phase one: open ticket to provision (no approval needed)."""
    payload = {"verb": "intent_submit", "name": name}
    payload.update(fields)
    return post(payload)


def write(eng, name: str, decl: dict):
    """Overwrite the whole declaration (for testing schema
    out-of-table fields etc.)."""
    d = wdir(eng, name)
    wspace.decl_path(d).write_text(
        json.dumps(decl, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")


def open_proto(post, name: str, *, scenario: str = "",
               subtype: str = "interactive") -> dict:
    """protocol goes through the same two-phase flow: open ticket to
    provision (protocol.json + skill.md)."""
    return post({"verb": "intent_submit", "name": name,
                 "kind": "protocol", "scenario": scenario,
                 "subtype": subtype})


def member_decl(name: str, scenario: str = "测",
                steps: str | None = None, **over) -> dict:
    """v17 member declaration template (same schema table as a
    standalone intent)."""
    d = {"name": name, "title": "", "scenario": scenario,
         "steps": steps or f"1. report {name}好了,一句",
         "acceptance": "", "tools": []}
    d.update(over)
    return d


def put_member(eng, proto: str, decl: dict):
    """Write a member declaration into the protocol's members/<name>/
    folder (v17 compilation unit)."""
    d = wdir(eng, proto)
    md = d / wspace.MEMBERS_DIR / decl["name"]
    md.mkdir(parents=True, exist_ok=True)
    (md / wspace.DECL_NAME).write_text(
        json.dumps(decl, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return md


def proto_ready(post, eng, name: str, skill: str, members: list,
                **decl_over) -> dict:
    """Open ticket -> skill.md + members alongside the protocol
    (members/<name>/) -> register the whole protocol.
    members entries: dict = full declaration; str = auto-synthesized
    minimal declaration (v17: members have no independent registry
    entry, always declared inside the protocol). Returns the
    registration reply (with the gate task)."""
    open_proto(post, name, **{k: v for k, v in decl_over.items()
                              if k in ("scenario", "subtype")})
    d = wdir(eng, name)
    (d / wspace.SKILL_NAME).write_text(skill, encoding="utf-8")
    decls = [m if isinstance(m, dict) else member_decl(m)
             for m in members]
    for md in decls:
        put_member(eng, name, md)
    cur, _ = wspace.read_decl(d)
    cur = cur or {}
    cur["members"] = [m["name"] for m in decls]
    wspace.decl_path(d).write_text(
        json.dumps(cur, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return register(post, name)


def set_members(eng, name: str, members: list):
    d = wdir(eng, name)
    cur, _ = wspace.read_decl(d)
    cur = cur or {}
    cur["members"] = list(members)
    wspace.decl_path(d).write_text(
        json.dumps(cur, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
