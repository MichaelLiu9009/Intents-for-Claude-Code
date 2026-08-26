"""MCP stdio bridge -- the agent's settlement face (B3 minimal face,
zero SDK).

The bridge is the engine's thin client: holds no state, touches no
task files, interprets no verbs -- every call rereads
runtime/engine.json (the port file is ground truth), and one tool
call becomes one HTTP POST /api/mcp. The engine's refusal comes back
verbatim: a refusal is an answer (CASELAW 19); the engine being
absent is a situation the agent can reason about, not a stack trace.

Wire protocol = MCP stdio (newline-delimited JSON-RPC 2.0), hand-
written as a minimal face without pulling in the SDK -- zero
compilation for the audience, a dependency surface is debt (PRODUCT2
audience ruling).
CASELAW 5: stdin must be buffered + utf-8/replace.

M26 ④ two-face law (user ruling 2026-08-22): **the admin seat's and
the executor seat's MCP surfaces are fully separate** -- an unused
prompt is just entropy nudging the executor off its intended
behavior.
  admin (sidecar)  create/register/search/settle -- zero execution verbs
  exec (x·solo / x·<protocol>)  settle/ask/request-permission, these three only
Usage: python -m commander.mcp <workspace> [--face admin|exec|proto]
(the old positional arg "exec" still works; default admin.)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

from . import defaults

# Descriptions carry only each tool's own semantics; process narrative
# lives where the process happens (refusal texts, CLAUDE.md, packages).
TOOLS = [
    {"name": "task_done",
     "description": "Settle a task with the engine: three states "
                    "(I-E-R): ok = done; ok_issue = done with friction "
                    "(issue REQUIRED, one line naming it — it feeds the "
                    "consolidation loop); failed = cannot be done "
                    "(summary names what is missing). Rule by the "
                    "order's R criteria, never your own. Every [task N] "
                    "envelope must end with this call — unsettled tasks "
                    "are waited on forever. For report-type intents the "
                    "answer itself goes in summary: settling is "
                    "delivery. Reply where the work arrived: OS-plane "
                    "work through OS tools, conversation work in the "
                    "conversation.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "task": {"type": "integer"},
                         "outcome": {"type": "string",
                                     "enum": ["ok", "ok_issue",
                                              "failed"]},
                         "summary": {"type": "string"},
                         "issue": {"type": "string",
                                   "description": "required for "
                                                  "ok_issue: one line "
                                                  "naming the friction"}},
                     "required": ["task", "outcome"]}},
    {"name": "intent_submit",
     "description": "Open the ticket and found the workspace (§2u stage "
                    "one; call only after the user explicitly asks — "
                    "initiative belongs to the human). No human gate, "
                    "nothing goes live: the engine creates a workspace "
                    "by name and returns its path — **the field "
                    "textbook lands there as schema.md** (declaration "
                    "semantics, E grammar, limits). Write the pieces "
                    "locally (intent.json is the declaration; the "
                    "optional `procedures` field is the engine's "
                    "physical layer), then register with "
                    "workspace_submit — registration is compilation. "
                    "Stateful multi-round flows pass kind=protocol to "
                    "found a booklet; stateless one-way commands stay "
                    "intents.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "name": {"type": "string",
                                  "description": "one word, ≤20 chars; "
                                                 "both directory name "
                                                 "and trigger name"},
                         "kind": {"type": "string",
                                  "enum": ["intent", "protocol"],
                                  "description": "default intent; "
                                                 "protocol = multi-"
                                                 "round booklet — "
                                                 "members declare in "
                                                 "members/<name>/, the "
                                                 "whole booklet "
                                                 "compiles atomically "
                                                 "(details in the "
                                                 "founding reply and "
                                                 "schema.md)"},
                         "title": {"type": "string",
                                   "description": "one-line title "
                                                  "(for humans)"},
                         "scenario": {"type": "string",
                                      "description": "when to use it — "
                                                     "write WHEN, not "
                                                     "HOW; vector "
                                                     "recall runs on "
                                                     "this line"},
                         "steps": {"type": "string",
                                   "description": "E section: command "
                                                  "sequence with "
                                                  "guarded branches "
                                                  "(grammar in the "
                                                  "workspace's "
                                                  "schema.md)"},
                         "acceptance": {"type": "string",
                                        "description": "R section: "
                                                       "three-state "
                                                       "criteria (ok:/"
                                                       "ok_issue:/"
                                                       "failed:); omit "
                                                       "for defaults"},
                         "subtype": {"type": "string",
                                     "description": "kind=protocol "
                                                    "only: interactive "
                                                    "(default)"},
                         "procedures": {
                             "type": "array",
                             "items": {"type": "string"},
                             "description": "optional prelude names "
                                            "from the engine's built-"
                                            "in library (e.g. "
                                            "screenshot); "
                                            "run before delivery, "
                                            "output lands in the "
                                            "order's Materials "
                                            "section. Unknown names "
                                            "refuse with the library "
                                            "listed. Not for booklet "
                                            "members"}},
                     "required": ["name", "scenario"]}},
    {"name": "workspace_submit",
     "description": "Submit by folder = register = compile (§2u stage "
                    "two, the ONLY human gate). The engine validates "
                    "structure against the schema sheet, resolves "
                    "declared names to files, stamps hashes, then one "
                    "card asks the human to approve GOING LIVE (the "
                    "card carries no full text — to inspect, open the "
                    "directory). Only approved items can trigger. ANY "
                    "later change on disk requires re-submitting "
                    "before it takes effect — the library keeps "
                    "serving the approved version. Validation failures "
                    "come back itemized — fix and resubmit. Protocols "
                    "submit as a whole booklet (skill + every member "
                    "validated and gated together; one bad piece "
                    "refuses the booklet); members are never submitted "
                    "alone.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "name": {"type": "string",
                                  "description": "intent / protocol "
                                                 "name (directory "
                                                 "resolves by name)"}},
                     "required": ["name"]}},
    {"name": "intent_retire",
     "description": "Propose retiring one standalone intent (call only "
                    "when the user asks — termination is a human "
                    "ruling). Opens a retirement gate card; user "
                    "approval takes it out of the IME and the deck "
                    "keyset. Soft: history and ledger stay, "
                    "workspace_submit on the folder revives it. "
                    "Booklet members are not retirable here — edit the "
                    "booklet's members/ and resubmit the whole booklet.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "name": {"type": "string",
                                  "description": "the standalone "
                                                 "intent to retire"},
                         "why": {"type": "string",
                                 "description": "one line for the "
                                                "approval card (e.g. "
                                                "duplicates a booklet "
                                                "member)"}},
                     "required": ["name"]}},
    {"name": "perm_gate",
     "description": "Executor permission gate (wired by the engine via "
                    "--permission-prompt-tool). Tool calls outside the "
                    "approved floor route here, pop a card, and wait "
                    "for the user's allow/deny — you never call it "
                    "yourself.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "tool_name": {"type": "string"},
                         "input": {"type": "object"}},
                     "required": ["tool_name"]}},
    {"name": "ask_user",
     "description": "Multiple-choice question to the user (seat-"
                    "neutral, AskUserQuestion-shaped): question + "
                    "options (≤12), routed to the card flow, blocks for "
                    "the answer, returns {choice} — {typed: true} when "
                    "the user typed a free-form answer instead of "
                    "picking (the card has a typed-answer line, so an "
                    "off-list reply is always reachable; never invent a "
                    "'manual input' pseudo-option). Executor orders: ask "
                    "ONLY at forks where E explicitly says ask_user — "
                    "not written means not asked; if a default works, "
                    "don't ask. On timeout take the default or fail "
                    "naming the missing decision.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "question": {"type": "string"},
                         "options": {"type": "array",
                                     "items": {"type": "string"}}},
                     "required": ["question", "options"]}},
    {"name": "step_done",
     "description": "Protocol-seat step claim: call once after "
                    "finishing each member-step envelope (member = the "
                    "member intent's name). Ledger-only — it flips the "
                    "seat's step state for the Status/Step bars, opens "
                    "and closes nothing (the bracket stays one task; "
                    "task_done is still refused there).",
     "inputSchema": {"type": "object",
                     "properties": {
                         "member": {"type": "string"}},
                     "required": []}},
    {"name": "intent_memory_index",
     "description": "Call at start of work: your intent surface — the "
                    "hot container's meta rows (name/scenario/segment) "
                    "plus container level and cold-library count. "
                    "Execution detail is NOT here; fetch by name with "
                    "intent_get when needed. Snapshot semantics — call "
                    "again for freshness.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "intent_search",
     "description": "Retrieval. With query = vector recall (whole "
                    "library, scenario similarity + name hits), two "
                    "columns: items ≤5 independent intents + protocols "
                    "≤1 family (surfaced by member hits). Rows carry "
                    "name/title/scenario — the recall itself is "
                    "multi-round context: copy steps to reproduce "
                    "mechanics, or trigger by name for the product. "
                    "Below the similarity floor an empty result is "
                    "legitimate — rephrase and retry. Without query = "
                    "mechanical cold-library listing (hot filtered).",
     "inputSchema": {"type": "object",
                     "properties": {
                         "query": {"type": "string"},
                         "limit": {"type": "integer"}}}},
    {"name": "intent_catalog",
     "description": "Catalog: usage-top flat list (name+scenario only, "
                    "token-lean, top-50 by usage). "
                    "total counts the whole library — the "
                    "difference is the long tail; find it with "
                    "intent_search(query). Detail by name via "
                    "intent_get. No parameters.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "match_protocol",
     "description": "Aggregation sensor: returns protocol-family sample "
                    "pools (member scenarios; pass scenario to get "
                    "per-family scores on the shared embedding ruler). "
                    "Check before creating a new intent: resembles a "
                    "family → suggest joining the booklet (re-register "
                    "that booklet); resembles none → create normally. "
                    "Booklets are the only grouping axis (the old "
                    "class axis is retired).",
     "inputSchema": {"type": "object",
                     "properties": {
                         "scenario": {"type": "string",
                                      "description": "the new intent's "
                                                     "scenario word "
                                                     "(to score "
                                                     "against)"}}}},
    {"name": "intent_get",
     "description": "Fetch by name, layered and batchable (part takes "
                    "one layer — steps or acceptance — default full "
                    "record; names array fetches up to 20, part "
                    "applies to the batch). Two uses: ① pull material "
                    "— choose your layer: following the flow takes "
                    "part=steps, ruling three-state takes "
                    "part=acceptance; ② before revising, fetch full to "
                    "see the current body — never revise from memory. "
                    "Reading = using, scored as usual.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "name": {"type": "string",
                                  "description": "single; exclusive "
                                                 "with names"},
                         "names": {"type": "array",
                                   "items": {"type": "string"},
                                   "description": "batch (≤20), "
                                                  "returns items[]"},
                         "part": {"type": "string",
                                  "enum": ["steps", "acceptance"],
                                  "description": "one layer; default "
                                                 "full record"}}}},
]

# M26 ④ two-face law: the interface is a behavioral prior -- each
# seat sees only its own face's verbs. task_done is a settlement
# verb, not an execution verb, and both faces have orders to settle,
# so it's on both faces.
# Face split (S2/C1, 2026-08-23): exec = the solo three-piece set
# (step_done is a verb exclusive to the bracket seat; on the solo
# face it's dead-weight tool cost); proto = the bracket seat's four
# plus a swapped ask_user copy for bracket law (the host seat has no
# E).
FACE_EXEC = set(defaults.XSOLO_MCP_TOOLS)
FACE_PROTO = FACE_EXEC | {"step_done"}
# Bracket-seat wording for ask_user (C1: "only ask when E names it" is wrong for the host seat)
PROTO_DESC = {"ask_user": (
    "Multiple-choice question to the user (question + options ≤12, "
    "routed to the card flow, blocks for the answer, returns "
    "{choice} — {typed: true} when the user typed a free-form answer "
    "instead of picking; never invent a 'manual input' pseudo-option). "
    "Bracket hosting: ask only at real forks the user must decide, and "
    "open a card when the OPTIONS deserve to be seen as a list — "
    "judgments and reports go straight to chat. Prefer defaults. On "
    "timeout take the default or say what was missed.")}
# settle/ask/request-permission + in-bracket step settlement (the
# engine refuses by seat: x·solo calling step_done gets a refusal
# reason; only the protocol seat is recognized)
FACE_ADMIN = {"task_done", "intent_submit", "workspace_submit",
              "intent_retire",
              "intent_memory_index", "intent_search", "intent_catalog",
              "match_protocol", "intent_get"}


def _call_engine(workspace: Path, payload: dict,
                 timeout: float = 15.0) -> str:
    # caller pipe: the token is an identity minted by the engine
    # (written into .mcp.json's env at provision time), carried back
    # on every frame -- the agent's self-report doesn't count;
    # identity is a mechanical ground truth
    token = os.environ.get(defaults.MCP_TOKEN_ENV)
    if token:
        payload["token"] = token
    try:
        info = json.loads((workspace / "runtime" / "engine.json")
                          .read_text(encoding="utf-8"))
        port = int(info["http"])
    except (OSError, ValueError, KeyError, TypeError):
        return ("Engine offline (runtime/engine.json missing or "
                "unreadable) — nowhere to settle right now; retry "
                "later, or leave the result with the user in words.")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/mcp",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ans = json.loads(r.read().decode("utf-8", "replace"))
    except OSError as e:
        return f"Engine unreachable ({e}) — retry later."
    if isinstance(ans, dict) and ans.get("error"):
        return "Engine refused: " + str(ans["error"])
    return json.dumps(ans, ensure_ascii=False)


def _reply(out, mid, result=None, error=None) -> None:
    msg: dict = {"jsonrpc": "2.0", "id": mid}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    out.write(json.dumps(msg, ensure_ascii=False).encode("utf-8") + b"\n")
    out.flush()


def _dispatch(workspace: Path, name: str, args: dict) -> str:
    if name == "task_done":
        tid = args.get("task")
        try:
            tid = int(tid)
        except (ValueError, TypeError):
            pass
        # Live-fire precedent 2026-08-16: issue field omitted -> the
        # entire ok_issue path becomes unusable (the executor tried
        # four different phrasings, all refused). The bridge's field
        # pass-through must be checked line by line against
        # inputSchema.
        return _call_engine(workspace, {
            "verb": "task_done", "task": tid,
            "outcome": args.get("outcome"),
            "summary": str(args.get("summary") or ""),
            "issue": str(args.get("issue") or "")})
    if name == "intent_submit":
        # §2u stage one: the workspace is founded the moment the
        # ticket opens (no human gate) -- the engine creates it by name
        return _call_engine(workspace, {
            "verb": "intent_submit",
            "name": str(args.get("name") or ""),
            "kind": str(args.get("kind") or ""),
            "title": str(args.get("title") or ""),
            "scenario": str(args.get("scenario") or ""),
            "steps": str(args.get("steps") or ""),
            "acceptance": str(args.get("acceptance") or ""),
            "subtype": str(args.get("subtype") or ""),
            "procedures": (args.get("procedures")
                           if isinstance(args.get("procedures"), list)
                           else [])})
    if name == "workspace_submit":
        # §2u stage two: submit by folder = register = compile (the only human gate)
        return _call_engine(workspace, {
            "verb": "workspace_submit",
            "name": str(args.get("name") or "")})
    if name == "intent_retire":
        # Retirement proposal (live-fire precedent 2026-08-23): takes
        # effect on human approval, soft retirement
        return _call_engine(workspace, {
            "verb": "intent_retire",
            "name": str(args.get("name") or ""),
            "why": str(args.get("why") or "")})
    if name == "perm_gate":
        # §2i: blocks waiting on the human -- the bridge's timeout
        # must outlast the engine's wait window (300s)
        return _call_engine(workspace, {
            "verb": "perm_gate",
            "tool_name": str(args.get("tool_name") or ""),
            "input": args.get("input") if isinstance(args.get("input"),
                                                     dict) else {}},
            timeout=defaults.XGATE_WAIT_S + 30)
    if name == "ask_user":
        return _call_engine(workspace, {
            "verb": "ask_user",
            "question": str(args.get("question") or ""),
            "options": args.get("options")},
            timeout=defaults.XGATE_WAIT_S + 30)
    if name == "step_done":
        return _call_engine(workspace, {
            "verb": "step_done",
            "member": str(args.get("member") or "")})
    if name == "intent_memory_index":
        return _call_engine(workspace, {"verb": "intent_memory_index"})
    if name == "intent_search":
        return _call_engine(workspace, {
            "verb": "intent_search",
            "query": str(args.get("query") or ""),
            "limit": args.get("limit")})
    if name == "intent_catalog":
        return _call_engine(workspace, {"verb": "intent_catalog"})
    if name == "match_protocol":
        return _call_engine(workspace, {
            "verb": "match_protocol",
            "scenario": str(args.get("scenario") or "")})
    if name == "intent_get":
        return _call_engine(workspace, {
            "verb": "intent_get",
            "name": str(args.get("name") or ""),
            "names": args.get("names"),
            "part": str(args.get("part") or "")})
    return f"unknown tool {name!r}"


def main(argv: list[str]) -> int:
    workspace = Path(argv[0]) if argv else Path.cwd()
    # M26 ④ face selection: --face admin|exec|proto (old positional
    # arg "exec" still compatible).
    # Default admin -- an undeclared seat runs the fullest face; a
    # wrong face only ever grants more, never fewer, tools, so work
    # never stalls.
    face = "admin"
    rest = list(argv[1:])
    while rest:
        a = rest.pop(0)
        if a == "--face" and rest:
            face = rest.pop(0)
        elif a == defaults.MCP_SEAT_EXEC:
            face = defaults.MCP_SEAT_EXEC
    keep = (FACE_EXEC if face == defaults.MCP_SEAT_EXEC else
            FACE_PROTO if face == defaults.MCP_SEAT_PROTO else FACE_ADMIN)
    tools = [dict(t) for t in TOOLS if t["name"] in keep]
    if face == defaults.MCP_SEAT_PROTO:
        for t in tools:
            if t["name"] in PROTO_DESC:
                t["description"] = PROTO_DESC[t["name"]]
    out = sys.stdout.buffer
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if not isinstance(msg, dict):
            continue
        mid = msg.get("id")
        method = msg.get("method")
        if method == "initialize":
            proto = (msg.get("params") or {}).get("protocolVersion") \
                or "2024-11-05"
            _reply(out, mid, {"protocolVersion": proto,
                              "capabilities": {"tools": {}},
                              "serverInfo": {"name": "intentOS",
                                             "version": "0"}})
        elif method == "tools/list":
            _reply(out, mid, {"tools": tools})
        elif method == "tools/call":
            p = msg.get("params") or {}
            name = str(p.get("name"))
            if name not in keep:
                # enforcement point of the two-face law: a wrong-face
                # verb never even reaches dispatch
                _reply(out, mid, {"content": [{
                    "type": "text",
                    "text": f"tool {name!r} is not on this seat's "
                            f"face ({face})"}]})
                continue
            args = p.get("arguments")
            try:
                text = _dispatch(workspace, name,
                                 args if isinstance(args, dict) else {})
            except Exception as e:
                # This loop is the seat's only uplink (audit
                # 2026-08-25 §4-other): one malformed frame must
                # cost one error reply, never the whole bridge —
                # a dead bridge strands every later call in the
                # session with no card and no message.
                text = f"bridge error in {name}: {e}"
            _reply(out, mid, {"content": [{"type": "text", "text": text}]})
        elif method == "ping":
            _reply(out, mid, {})
        elif mid is not None:
            # a notification (no id) is silently ignored by name; an
            # unknown method that carries an id still needs a reply
            _reply(out, mid, error={"code": -32601,
                                    "message": f"unknown method {method!r}"})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
