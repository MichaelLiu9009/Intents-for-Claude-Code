"""workspace plane (M20 §2u, user ruling 2026-08-15): the physical
form of one intent = one directory.

**Location unified, effect layered.** The directory is source; the
library is the executable form -- **registration = compilation**.
The engine never judges permission by location, only by state
(approved or not + hash match or not) -- so tools / inputs / records
living in the same directory never gets confused.

**The procedure library is not on this plane; it's declared here**
(user ruling 2026-08-23, superseding 08-16's "bind to keys").
procedure is the control protocol's **physical layer** -- it
captures the physical scene (mouse position, screen) as material;
the engine holds a built-in library, and **intent declarations
reference it by name** (the `procedures` field, matched against the
wordlist at registration time). On trigger, the engine runs the
prelude first, then delivers the order. The dividing line is the
**wall**: procedure is wall-less code the engine's subprocess runs
directly -- the permission gate can't reach it; tools are invoked by
the executor inside the wall. Hence "you may write tools, may
declare procedures, may not write a procedure." Corollary: a
procedure blowing up is not the agent's business (physical-layer
failure reports to the human, the order isn't delivered, the intent
is innocent and stays live).

**The engine only reads the schema sheet.** What isn't in the
schema, the engine can't even look up -- "never peek inside the
directory" goes from a discipline to a structural guarantee.
Declarations give only **names**; location is derived by convention
(convention over configuration; same law as "procedure takes no
params": a name is an enum, a path is free text = an unbounded
input space).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .. import defaults

# ---- Convention layout (engine-mandated; paths never appear in declarations) ----
DECL_NAME = "intent.json"        # declarative content (= old submit form)
PROTO_DECL_NAME = "protocol.json"   # the protocol side's counterpart
GUIDE_NAME = "CLAUDE.md"         # convention & guide: readable by whoever enters this dir
TOOLS_DIR = "tools"              # this intent's tools; declared name Y -> Y.*
INPUTS_DIR = "inputs"            # material: entities not pointers; unapproved but conventional
RECORDS_DIR = "records"          # output & ledger: **engine writes, agent reads only**
SKILL_NAME = "skill.md"          # protocol only: skill-book body text
MEMBERS_DIR = "members"          # protocol only: member declarations travel with the booklet
                                 # (v17 compile unit: members/<name>/intent.json
                                 #  + tools/ -- members have no separate home)

SUBDIRS = (TOOLS_DIR, INPUTS_DIR, RECORDS_DIR)


# ---- schema sheet: the single source of truth (agents read it to learn, the engine reads it to validate) ----
# Teaching text is embedded as field descriptions -- keeping two
# copies (one for teaching, one for validation) is bound to drift,
# and "what's taught disagrees with what's validated" is the hardest
# bug class to catch.
SCHEMA: dict = {
    "name": {
        "required": True, "kind": "phrase",
        "max": defaults.INTENT_NAME_MAX,
        "desc": "a word or a short phrase (internal spaces/hyphens "
                "ok; no dots or path separators). Doubles as the "
                "directory name and the trigger name."},
    "title": {
        "required": False, "kind": "text", "max": 60,
        "desc": "one-line human-facing title."},
    "scenario": {
        "required": True, "kind": "word",
        "max": defaults.INTENT_SCENARIO_MAX,
        "desc": "scenario word: a **single-word** situational tag "
                "(≤20 chars, no whitespace/punctuation). The vector "
                "layer clusters on it — the same word piling up = the "
                "signal this family wants to become a protocol; long "
                "descriptive phrases hurt retrieval, the intent body "
                "belongs in steps."},
    "steps": {
        "required": True, "kind": "text",
        "max": defaults.INTENT_STEPS_MAX,
        "desc": "the E section = a **pseudo-code function body** (the "
                "intent IS a function): the trigger text plus "
                "physical-layer context are the input, tools/toolkit "
                "are the methods, do-if-else forms the execution "
                "strategy, return is limited to three states "
                "(criteria in acceptance). Grammar (machine-checked "
                "at registration, sick lines named one by one): "
                + defaults.E_GRAMMAR + ". Verb table (content "
                "character budgets): "
                + " ".join(f"{v}({n})" for v, n in defaults.E_VERBS.items())
                + " — judge is the only semantically open "
                "instruction (it spends LLM money, priced explicitly; "
                "the fewer, the cheaper); the word list belongs to "
                "the engine, extensions go through the user. "
                f"Conditions ≤{defaults.E_COND_MAX} chars and must be "
                "mechanically decidable (exit code / a count / "
                "contains a literal). **Required** — the intent IS "
                "this function body; without it the intent does not "
                "exist."},
    "acceptance": {
        "required": False, "kind": "text",
        "max": defaults.INTENT_INSTR_MAX,
        "desc": "acceptance criteria (the R section of I-E-R): the "
                "**three-state verdict conditions are fixed at "
                "compile time** — the executor rules status by them, "
                "never inventing its own standard. Format: "
                "'ok: <condition>', 'ok_issue: <condition>' "
                "(omissible), 'failed: everything else'; mechanically "
                "checkable preferred (placed=page count, exit 0, file "
                "exists). A failed inside an E line is an in-process "
                "death sentence; the ok / ok_issue borderline is "
                "governed only here. The whole section is omissible — "
                "omitted runs the default verdict (all-ok E path = "
                "ok; any line landing failed = failed; done but with "
                "friction = ok_issue)."},
    # boundary retired 2026-08-24: the allow side belongs to the
    # harness (--permission-mode) + the PERM_ALLOW ledger; a prose
    # declaration with no consumer is schema noise. DB column stays
    # as a fossil (additive law).
    "procedures": {
        "required": False, "kind": "names", "max": 5,
        "desc": "optional prelude roster: an array of names from the "
                "engine's built-in procedure library (e.g. "
                "screenshot). On "
                "trigger the engine **runs the prelude first, then "
                "delivers**; the produced materials render into the "
                "order's Materials section — E starts from 'the "
                "materials are already there'. Matched against the "
                "word list at registration; a name outside it refuses "
                "the whole submission (the refusal carries the "
                "available list); the library belongs to the engine, "
                "agents have no submission channel. A crashed prelude "
                "reports to the human and the order is not delivered; "
                "the intent is not suspended. Member declarations "
                "(inside a booklet) are supported the same way: the "
                "member's key runs the prelude first, and the step "
                "envelope's tail carries a materials pointer."},
    "tools": {
        "required": False, "kind": "names", "max": 20,
        "desc": "array of this intent's tool names. The engine "
                "resolves tools/<name>.* by convention; approved ones "
                "the executor may call directly. How "
                "they're used, desktop or browser — not the engine's "
                "concern."},
    # "caveats" retired (user ruling 2026-08-25): the declared field
    # never reached the DB (review P2-c) — lessons flow back through
    # the sidecar revision channel (retry/rework), where the sidecar
    # folds them into steps/acceptance and re-registers; the DB
    # caveats table stays as a fossil (no writer).
}

PROTO_SCHEMA: dict = {
    "name": SCHEMA["name"],
    "scenario": {
        "required": False, "kind": "word",
        "max": defaults.INTENT_SCENARIO_MAX,
        "desc": "situational tag: one word (protocols aggregate by "
                "family; the tag is the family's entrance)."},
    "subtype": {
        "required": True, "kind": "enum",
        "options": defaults.PROTO_SUBTYPES,
        "desc": "execution semantics: protocols come in exactly one "
                "multi-round bracket type (interactive) — straight-"
                "line execution is intent business, run on x·solo "
                "(§2m v14)."},
    "members": {
        # not marked required -- the refusal reason for an empty
        # member list belongs to the **headcount law** (it states it
        # more precisely: two function words 2 < min3, an empty
        # booklet doesn't stand up); schema only governs shape
        "required": False, "kind": "names", "max": 10,
        "desc": "member roster (3–10 counting the two system slots "
                "·启/·收, i.e. 1–8 real members). **v17 compile unit "
                "(user ruling 2026-08-16 late night)**: member "
                "declarations travel with the booklet — each name "
                "maps to members/<name>/intent.json (same schema "
                "table as a standalone intent) + tools/; members have "
                "**no independent home**, never go through "
                "intent_submit, and the whole booklet compiles "
                "atomically behind one gate — one bad member refuses "
                "the booklet, all or nothing, exactly matching "
                "stateful situational interaction. **Opening/closing "
                "are not members** (reserved names are rejected at "
                "registration): opening content goes in prep; "
                "closing (·收) is the engine's fixed final-cleanup "
                "contract, not declarable — closing domain work "
                "belongs in a member step the user presses."},
    "prep": {
        "required": False, "kind": "text",
        "max": defaults.PROTO_HOOK_MAX,
        "desc": "content of the ·启 system step (opening setup, E "
                "prose): auto-delivered by the engine at bracket "
                "open; the seat runs it before greeting — e.g. read "
                "the booklet's state and report where we left off, "
                "pre-warm today's material. Empty = default (one "
                "greeting line, then stand by)."},
    # wrapup left the schema (user ruling 2026-08-26): ·收 is
    # engine-owned — the fixed final-cleanup contract
    # (defaults.PROTO_WRAP_FINAL); closing domain work belongs in a
    # member step the user presses. The validate() tail gives a
    # declared wrapup its own teaching refusal.
}


def schema_of(kind: str) -> dict:
    return PROTO_SCHEMA if kind == "protocol" else SCHEMA


def render_schema(kind: str = "intent") -> str:
    """Render the schema sheet into teaching material (the agent-
    facing side -- same sheet)."""
    out = []
    for k, spec in schema_of(kind).items():
        head = ("- `" + k + "`"
                + (" (required)" if spec.get("required") else ""))
        if spec.get("options"):
            head += " values: " + " | ".join(spec["options"])
        lim = spec.get("max")
        if lim and spec["kind"] in ("word", "text"):
            head += f" (≤{lim} chars)"
        out.append(head + " — " + spec["desc"])
    return "\n".join(out)


SCHEMA_MD_NAME = "schema.md"


def write_schema_md(d: Path, kind: str = "intent") -> None:
    """Field-textbook publishing port (N1, 2026-08-23): render_schema
    had zero callers before this -- the textbook was never published,
    both prompt sites pointed at nothing. schema.md is written the
    moment a workspace is provisioned -- teaching lands at the point
    of authoring, read on demand, never enters the resident context;
    it's engine property, rewritten on every provision (the textbook
    tracks the engine version, not user content). A protocol
    workspace carries two sheets (the booklet declaration + the
    intent sheet used by members)."""
    if kind == "protocol":
        body = ("# protocol.json field sheet (booklet declaration — "
                "engine-owned, rewritten on provisioning)"
                "\n\n" + render_schema("protocol")
                + "\n\n# Member intent.json field sheet (one per "
                "members/<name>/ — the same table as a standalone "
                "intent)\n\n"
                + render_schema("intent"))
    else:
        body = ("# intent.json field sheet (declaration textbook — "
                "engine-owned, rewritten on provisioning)"
                "\n\n" + render_schema("intent"))
    (d / SCHEMA_MD_NAME).write_text(body + "\n", encoding="utf-8")


# ---- Path derivation: name -> directory (paths never appear in declarations) ----
# class retired (user ruling 2026-08-25): the filing axis rode on a
# char-overlap scorer that only worked for CJK morphemes — the layout
# is flat now (root/<name>); the DB class column stays a fossil.
def root(home: Path) -> Path:
    """workspace root = the seat's home (sidecar is workspace 0, same
    shape)."""
    return Path(home)


def wdir(home: Path, name: str) -> Path:
    return root(home) / name


def flatten_legacy(home: Path) -> list[str]:
    """One-shot boot migration (class retirement 2026-08-25): move
    legacy root/<class>/<name>/ workspaces up to root/<name>/. A
    legacy shell = a first-level dir with no declaration of its own
    whose children carry one. Skips a child whose flat target already
    exists (reported, never overwritten — the engine never deletes a
    user directory). Returns the moved names."""
    r, moved = root(home), []
    if not r.is_dir():
        return moved
    # System dirs share the root but are never class shells (audit
    # 2026-08-25 §4-correctness): scratch/ is a drafting bench the
    # agent builds freely in, memory/ is the harness's — a nested
    # dir that happens to carry intent.json must not get hoisted
    # into the intent namespace.
    system = {"scratch", defaults.MEMORY_DIRNAME, "utility"}
    for shell in sorted(r.iterdir()):
        if (not shell.is_dir() or shell.name.startswith(".")
                or shell.name in system
                or (shell / DECL_NAME).is_file()
                or (shell / PROTO_DECL_NAME).is_file()):
            continue
        kids = [k for k in sorted(shell.iterdir()) if k.is_dir()
                and ((k / DECL_NAME).is_file()
                     or (k / PROTO_DECL_NAME).is_file())]
        if not kids:
            continue
        for k in kids:
            tgt = r / k.name
            if tgt.exists():
                moved.append(f"{shell.name}/{k.name} SKIPPED "
                             f"(flat '{k.name}' already exists)")
                continue
            k.rename(tgt)
            moved.append(f"{shell.name}/{k.name} -> {k.name}")
        try:
            shell.rmdir()                  # only if now empty
        except OSError:
            pass
    return moved


def utility_skill_path(workspace: Path, name: str) -> Path:
    """Landing spot for the rendered protocol skill (utility is
    engine territory) -- single source shared by the engine's
    delivery path and the seed template (writing the path in two
    places is bound to drift)."""
    return Path(workspace) / "utility" / "protocols" / name / "skill.md"


def find(home: Path, name: str) -> Path | None:
    """Workspace directory by name — flat layout: present only when
    the directory carries a declaration file."""
    d = wdir(home, name)
    if (d / DECL_NAME).is_file() or (d / PROTO_DECL_NAME).is_file():
        return d
    return None


# ---- Provisioning (phase 1: founded the moment the ticket opens, no human gate -- nothing can run yet) ----
def provision(home: Path, name: str, decl: dict) -> Path:
    """Idempotent: if the directory exists, leave existing content
    alone and only fill in what's missing. **The engine never deletes
    a user directory** (hard rule, 2026-08-15)."""
    d = wdir(home, name)
    d.mkdir(parents=True, exist_ok=True)
    for sub in SUBDIRS:
        (d / sub).mkdir(exist_ok=True)
    write_decl(d, decl)
    write_schema_md(d)                 # N1: field textbook ships with the workspace
    g = d / GUIDE_NAME
    if not g.is_file():                # don't overwrite an existing convention (the user's own words)
        g.write_text(defaults.WS_GUIDE_MD.format(
            name=name, scenario=decl.get("scenario") or "(unset)"),
            encoding="utf-8")
    return d


def write_decl(d: Path, decl: dict) -> None:
    (d / DECL_NAME).write_text(
        json.dumps(decl, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")


def decl_path(d: Path) -> Path:
    """intent and protocol share the same two-stage flow; only the
    declaration filename differs."""
    p = d / PROTO_DECL_NAME
    return p if p.is_file() else d / DECL_NAME


def read_decl(d: Path) -> tuple[dict | None, str]:
    p = decl_path(d)
    try:
        return json.loads(p.read_text(encoding="utf-8")), ""
    except OSError as e:
        return None, f"cannot read {p.name} ({e})"
    except json.JSONDecodeError as e:
        return None, f"{p.name} is not valid JSON: {e}"


# ---- E-section grammar parser (schema-based language, user ruling 2026-08-16) ----
_DEST_RE = re.compile(
    r"^(next|ok|L\d+|ok_issue\(.+\)|failed\(.+\)|ask\(.+\))$")
_LINE_RE = re.compile(r"^(\d+)\s*[.、]\s*(\S+)\s+(.*)$")


def _split_top(sub: str) -> tuple[str, str] | None:
    """Split on the top-level comma into two halves (commas inside
    parentheses don't count -- destination messages are allowed to
    contain commas)."""
    depth = 0
    for i, ch in enumerate(sub):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            return sub[:i].strip(), sub[i + 1:].strip()
    return None


def parse_steps(text: str) -> tuple[int, list[str]]:
    """Machine check for the E section: verb in wordlist (wordlist
    gate) - content <= verb budget (character-count gate) -
    destination in the closed wordlist - jumps only go forward
    (structurally acyclic). Full-width/half-width punctuation is
    normalized; "no nesting" and "implicit else" are guaranteed by
    the grammar's structure, no longer a discipline to enforce by
    hand. Returns (line count, list of bad lines) -- bad lines are
    named one by one, never a blanket rejection of the whole
    block."""
    probs: list[str] = []
    norm = (text.replace("→", "->").replace(",", ",")
            .replace("(", "(").replace(")", ")"))
    lines = [ln.strip() for ln in norm.splitlines() if ln.strip()]
    prev = 0
    for ln in lines:
        m = _LINE_RE.match(ln)
        if not m:
            probs.append(f"E line breaks the grammar (need "
                         f"'N. <verb> …'): {ln[:40]}")
            continue
        num, verb, rest = int(m.group(1)), m.group(2), m.group(3).strip()
        if num <= prev:
            probs.append(f"L{num} numbering must strictly increase")
        prev = max(prev, num)
        budget = defaults.E_VERBS.get(verb)
        if budget is None:
            probs.append(f"L{num} verb '{verb}' not in the word list "
                         f"({'/'.join(defaults.E_VERBS)}) — extending "
                         f"the list goes through the user; judgment "
                         f"is written 'judge'")
            continue
        content, _, branch = rest.partition("->")
        content, branch = content.strip(), branch.strip()
        if not content:
            probs.append(f"L{num} content is empty")
        elif len(content) > budget:
            probs.append(f"L{num} '{verb}' content over budget "
                         f"({len(content)}/{budget} chars) — sink "
                         f"detail into tools, keep one hop here")
        if not branch:
            continue                      # implicit branch: success -> next, failure -> failed
        if not branch.startswith("if "):
            probs.append(f"L{num} branch must be 'if <condition>, "
                         f"(<branch>, <branch>)'")
            continue
        two = _split_top(branch[3:].strip())
        if two is None:
            probs.append(f"L{num} missing the comma after the "
                         f"condition: if <condition>, (…, …)")
            continue
        cond, dests = two
        if not cond:
            probs.append(f"L{num} condition is empty")
        elif len(cond) > defaults.E_COND_MAX:
            probs.append(f"L{num} condition over the cap "
                         f"({len(cond)}/{defaults.E_COND_MAX} chars) "
                         f"— conditions must be mechanically "
                         f"decidable; long explanations go into the "
                         f"content slot or acceptance")
        if not (dests.startswith("(") and dests.endswith(")")):
            probs.append(f"L{num} branches need a parenthesis pair: "
                         f"(<branch>, <branch>)")
            continue
        pair = _split_top(dests[1:-1])
        if pair is None:
            probs.append(f"L{num} needs two branches (then, else) — "
                         f"binary branching; multi-way takes multiple "
                         f"lines")
            continue
        for d in pair:
            if not _DEST_RE.match(d):
                probs.append(f"L{num} branch '{d[:24]}' not "
                             f"recognized — only next/L<n>/ok/"
                             f"ok_issue(…)/failed(…)/ask(…)")
            elif d.startswith("L") and int(d[1:]) <= num:
                probs.append(f"L{num} jump {d} may not go backward — "
                             f"E has no loops; retry logic sinks "
                             f"into tools")
    return len(lines), probs


# ---- Validation: reads only the schema sheet (structure, not content) ----
def validate(decl: dict, kind: str = "intent") -> list[str]:
    """Names problems one by one (the check_rules precedent: a bad
    entry speaks for itself, never a blanket rejection of the whole
    submission)."""
    probs: list[str] = []
    sch = schema_of(kind)
    if not isinstance(decl, dict):
        return [DECL_NAME + " top level must be an object"]
    for k, spec in sch.items():
        v = decl.get(k)
        if v in (None, "", [], {}):
            if spec.get("required"):
                probs.append(f"`{k}` is required — " + spec["desc"][:60])
            continue
        kd = spec["kind"]
        if kd in ("word", "text"):
            if not isinstance(v, str):
                probs.append(f"`{k}` must be a string")
            elif len(v) > spec["max"]:
                probs.append(f"`{k}` over the cap "
                             f"({len(v)}/{spec['max']} chars)")
            elif kd == "word" and not _is_word(v):
                probs.append(f"`{k}` must be one word (no spaces or "
                             f"punctuation)")
            elif kd == "phrase" and not _is_phrase(v):
                probs.append(f"`{k}` must be a word or a short "
                             f"phrase (internal spaces/hyphens ok; "
                             f"no dots or path separators, no "
                             f"leading/trailing space)")
            elif k == "steps":
                probs += parse_steps(v)[1]
            elif k == "acceptance":
                # Light I-E-R check (2026-08-16): the criteria are
                # prose for the agent to read, not code the engine
                # executes -- only verify the skeleton is present, no
                # deep parsing
                norm = v.replace(":", ":")
                if "ok:" not in norm or "failed:" not in norm:
                    probs.append("`acceptance` must contain 'ok:' and "
                                 "'failed:' criteria lines (ok_issue "
                                 "omissible); or omit the whole "
                                 "section for the default verdict")
        elif kd == "enum":
            if v not in spec["options"]:
                probs.append(f"`{k}` only accepts "
                             + " | ".join(spec["options"]))
        elif kd == "names":
            if not isinstance(v, list) or any(not isinstance(x, str)
                                              for x in v):
                probs.append(f"`{k}` must be an array of strings "
                             f"(**names only, never paths**)")
            elif len(v) > spec["max"]:
                probs.append(f"`{k}` at most {spec['max']} entries "
                             f"(got {len(v)})")
            elif any(("/" in x or "\\" in x or ".." in x) for x in v):
                probs.append(f"`{k}` contains a path — names only; "
                             f"location is derived by convention")
        elif kd == "textlist":
            if not isinstance(v, list):
                probs.append(f"`{k}` must be an array")
            elif any(len(str(x)) > spec["max"] for x in v):
                probs.append(f"`{k}` has an entry over {spec['max']} "
                             f"chars")
    unknown = [k for k in decl if k not in sch]
    if "wrapup" in unknown:
        # Teaching refusal, not a generic unknown (user ruling
        # 2026-08-26): declared wrapups used to be legal and one
        # blocked shutdown — the field is retired, ·收 is the
        # engine's fixed final-cleanup contract.
        unknown.remove("wrapup")
        probs.append("`wrapup` is engine-owned now: ·收 delivers the "
                     "fixed final-cleanup contract and the session "
                     "shuts down on the grace clock right after — "
                     "delete the field; closing domain work belongs "
                     "in a member step the user presses, opening "
                     "setup in prep")
    if unknown:
        probs.append("unknown fields (not on the schema sheet, the "
                     "engine won't recognize them): "
                     + ", ".join(sorted(unknown)[:6]))
    return probs


def _is_word(s: str) -> bool:
    return bool(s) and all(ch.isalnum() or ch == "_" for ch in s)


def _is_phrase(s: str) -> bool:
    """Name rule (user ruling 2026-08-26): a word or a short phrase —
    \\w runs joined by single spaces or hyphens. Dots and path
    separators stay impossible (the name doubles as a directory
    name; the path-escape audit of 2026-08-25 still holds)."""
    return bool(s) and _is_word(s[0]) and _is_word(s[-1]) and all(
        ch.isalnum() or ch in "_ -" for ch in s)


# ---- Resolve files by declaration: existence + hash (proof of effect) ----
def file_hash(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def resolve(d: Path, decl: dict,
            kind: str = "intent") -> tuple[dict, list[str]]:
    """Declared names -> conventional paths. Returns (found, problems).
    found = {"tools": {name: (Path, hash)}, "skill": (Path, hash) | None}

    procedures are not resolved here -- the name matches against the
    engine's built-in wordlist (the library isn't on the workspace
    plane); matching happens on the registration side
    (engine._workspace_submit / _intent_submit). Here we only check
    the shape (schema names)."""
    found: dict = {"tools": {}, "skill": None}
    probs: list[str] = []
    tdir = d / TOOLS_DIR
    for nm in (decl.get("tools") or []):
        hits = sorted(x for x in tdir.glob(str(nm) + ".*")
                      if x.is_file()) if tdir.is_dir() else []
        if len(hits) == 1:
            found["tools"][nm] = (hits[0], file_hash(hits[0]))
        elif not hits:
            probs.append(f"tool '{nm}' declared, but {TOOLS_DIR}/"
                         f"{nm}.* is not there")
        else:
            probs.append(f"tool '{nm}' name collision: {TOOLS_DIR}/ "
                         f"holds " + ", ".join(h.name for h in hits))
    if kind == "protocol":
        p = d / SKILL_NAME
        if p.is_file():
            found["skill"] = (p, file_hash(p))
        else:
            probs.append(f"protocol is missing {SKILL_NAME} (the "
                         f"skill-book body)")
    return found, probs


# ---- v17 compile unit (user ruling 2026-08-16 late night: members travel with the booklet, one gate for the whole booklet) ----
def member_dir(proto_dir: Path, name: str) -> Path:
    return proto_dir / MEMBERS_DIR / name


def resolve_members(d: Path, decl: dict) -> tuple[list[dict], list[str],
                                                  list[str]]:
    """Whole-booklet resolution: for each name in the members list,
    read intent.json from members/<name>/ and validate against **the
    same schema sheet as a standalone intent** (full E grammar, steps
    required, the works); tools resolve by convention at
    tools/<name>.* and get hash-stamped. Returns (member declaration
    table, staged entries, problem list) -- problems are named one by
    one with a member prefix, **any one member's problem fails the
    whole booklet** (atomicity lives with the caller; this function's
    only job is to report every problem)."""
    decls: list[dict] = []
    staged: list[str] = []
    probs: list[str] = []
    for m in (decl.get("members") or []):
        m = str(m).strip()
        if not m:
            continue
        md = member_dir(d, m)
        mp = md / DECL_NAME
        if not mp.is_file():
            probs.append(f"member '{m}': {MEMBERS_DIR}/{m}/{DECL_NAME} "
                         f"is not there — member declarations travel "
                         f"with the booklet, one directory per member")
            continue
        try:
            mdecl = json.loads(mp.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            probs.append(f"member '{m}': {DECL_NAME} unreadable ({e})")
            continue
        mp_probs = validate(mdecl, "intent")
        if str(mdecl.get("name") or "") != m:
            mp_probs.append(f"name field '{mdecl.get('name')}' ≠ "
                            f"directory name '{m}' — the name IS the "
                            f"household, they must match")
        if not str(mdecl.get("steps") or "").strip():
            mp_probs.append("steps is required — the member IS this "
                            "segment of E")
        # Member-step prelude (user ruling 2026-08-24, overturns v18's
        # "members unsupported"): pressing a member key -> engine runs
        # the prelude first, material lands in the bracket's task dir,
        # step envelope carries a materials pointer at the tail --
        # in-booklet on-site material now has a front door. Wordlist
        # match follows the same law as standalone intent (checked
        # here locally, refusal names the wordlist).
        wl = [str(x).strip() for x in (mdecl.get("procedures") or [])
              if str(x).strip()]
        bad = [x for x in wl if x not in defaults.PHYS_PROCEDURES]
        if bad:
            mp_probs.append(
                "procedures outside the word list: " + ", ".join(bad)
                + " — the engine's built-in list: "
                + (", ".join(defaults.PHYS_PROCEDURES) or "(empty)"))
        mfound, mfprobs = resolve(md, mdecl, "intent")
        mp_probs += mfprobs
        probs += [f"member '{m}': {p}" for p in mp_probs]
        if mp_probs:
            continue
        staged.append(f"{MEMBERS_DIR}/{m}/{DECL_NAME}  "
                      f"({file_hash(mp)[:12]})")
        for nm, (p, h) in mfound["tools"].items():
            staged.append(f"{MEMBERS_DIR}/{m}/{TOOLS_DIR}/{p.name}  "
                          f"({h[:12]})")
        decls.append(mdecl)
    return decls, staged, probs


def undeclared(d: Path, decl: dict) -> list[str]:
    """Reverse reminder (not validation): what's in the directory but
    not in the declaration -- the most common failure is writing a
    file and forgetting to declare it, then staring baffled at why it
    has no effect. Lists filenames only, never reads content."""
    out: list[str] = []
    tnamed = {str(x) for x in (decl.get("tools") or [])}
    tdir = d / TOOLS_DIR
    if tdir.is_dir():
        for p in sorted(tdir.iterdir()):
            if p.is_file() and p.stem not in tnamed:
                out.append(TOOLS_DIR + "/" + p.name)
    return out[:12]
