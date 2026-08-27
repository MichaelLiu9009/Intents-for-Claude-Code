"""Engine's names and text — one source, many readers.

CASELAW 25: unknown config keys are hard-rejected by name; only the
engine's own constants live here, no policy.
"""
from __future__ import annotations

HTTP_PORT = 9700
WS_PORT = 9701

INSTANCES_DIRNAME = "instances"
# Resident-unit declaration zone (§2m v14 renamed modes→modules; boot
# has idempotent auto-migration for the old name): the user-side
# module customization surface (as of 2026-08-24 has no tenant —
# pruner retired along with the permission-plane consolidation; the
# directory law stays reserved for future resident units)
MODULES_DIRNAME = "modules"
# An instance's private memory (environment details belong to it —
# user ruling 2026-08-12): pinned inside home, never spills outside
# the git repo root. The engine only locks the location, never reads
# or writes it.
MEMORY_DIRNAME = "memory"
RECORDS_DIRNAME = "records"
RUNTIME_DIRNAME = "runtime"
JOURNAL_NAME = "events.jsonl"

# §2m v14: the "mode" concept is dead — resident-unit naming goes
# module:sidecar = OS module (the interaction surface and maintenance
# seat).
OS_MODULE = "sidecar"

# Module policy declaration (M16 §2, user ruling 2026-08-12): the
# substantiated third input. In a world where triggering is approval,
# there's no mid-flight human review — "how wide to generalize" rests
# entirely on this declaration; it is a **safety load-bearing piece**:
# reviewed against the module contract, not a runtime tunable; the
# agent has no surface to push against — widening it only happens by
# a human hand-editing here.
#   security.never_allow — the ceiling, **substring** semantics
#     (better over-block than miss): no matter what's declared or what
#     a human approved, an allow/ask containing these substrings is
#     rejected whole. The engine-territory deny floor is independent
#     of this, always cast separately (belt and suspenders).
#   generalization.level — scope: conservative = item-by-item as is,
#     never widened (open it once real evidence — e.g. a dir ledger —
#     builds the case, M16 §7).
MODULE_POLICY = {
    "sidecar": {
        "security": {"never_allow": [
            "state.db",                 # truth layer (not even readable)
            "/.claude/",                # harness territory (settings/skills)
            ".mcp.json", "CLAUDE.md",   # engine's own property
            "/utility/",                # procedure store (M20 §2c): the
                                        # only entry is workspace_submit,
                                        # human-approved
        ]},
        "generalization": {"level": "conservative"},
    },
}

# Permission-plane consolidation (user's final ruling 2026-08-24): the
# allow side is handed to the harness auto mode — when the engine
# casts a household / opens a seat it pre-writes `--permission-mode`
# via the spawn flag, value set here (config-editable; empty string =
# no flag, falls back to the harness's own default). The deny floor
# is unchanged (MODULE_POLICY + provision's engine territory).
SEAT_PERMISSION_MODE = "auto"
# Always-allow ledger (global): when a human clicks Always on a perm
# card, the engine appends the rule into <workspace>/config.json's
# PERM_ALLOW; the user may hand-edit it. Syntax shares harness
# settings' allow grammar (Tool / Tool(specifier) / mcp__server__tool).
#
# One click, two landings (CASELAW 64, 2026-08-25): this ledger is the
# **cross-seat** copy, materialized into every seat's project settings
# and x·solo's --allowedTools. The **per-seat** copy is the CLI's own —
# the allow answer carries updatedPermissions, and the CLI banks the
# rule in that seat's settings.local.json, which the engine never
# touches. They are read as a union; neither is the other's mirror.
# This is the factory default: an empty ledger.
PERM_ALLOW: list = []

# M20 §1 consolidate loop: a single task's output tokens crossing the
# threshold → an alert card (suggests consolidating / sinking down);
# per-intent mute = don't remind me again. Soft dependency: if the
# transcript can't be sliced, the receipt carries no token count, the
# alert never fires — the engine's main business has zero dependency
# on it.
TASK_TOKEN_ALERT = 50_000

# Physical-layer runtime parameters (procedure = the control
# protocol's physical layer, built into the engine — the submission
# gate's three pieces [word count/line count/def run] retire together
# with the agent submission port; these two remain as the subprocess
# shell's guardrails, the physical layer still uses them)
PROC_TIMEOUT_S = 30.0    # hard timeout per step, tree-killed
PROC_SAY_MAX = 4000      # ctx.say per-message text cap (word-count gate)

# Physical-layer word list (engine's property; extending the table =
# human ruling, edit the engine source — same law as the E-verb
# table). Attachment form (user ruling 2026-08-23, supersedes 08-16's
# "attach to a key"): a procedure is an **optional prelude declared by
# an intent** — intent.json's `procedures` field references it by
# name, matched against the word list at registration time (outside
# the word list = hard reject whole, with the reject reason carrying
# the list); on trigger the engine runs the prelude first, its
# material lands in the task directory and renders into the delivery
# order's Materials section; if the prelude blows up it's reported to
# a human and the order is not filed (the intent itself is innocent,
# not suspended). Key bindings stay dumb triggers, zero change.
# entry = the implementation file under kernel/procs/ (wall-less code
# the engine's subprocess runs directly, no agent submission port).
# The old "ime" entry retired along with the trigger-flow concept —
# its definition (which trigger fired is only known after the run
# finishes) is trigger-time routing, it can't become some intent's
# prelude; the IME proper is now implemented by the flow window's
# seat entry.
PHYS_PROCEDURES = {
    "screenshot": {
        "desc": "mouse-anchored screenshot: shoot the monitor under "
                "the mouse, attach it as task material (rendered "
                "into the order's Materials section)",
        "entry": "shot.py",
    },
}

# M20 §2/§2d protocol plane
PROTO_SKILL_MAX = 20_000     # skill body cap (what a human approves is the full text)
PROTO_SUBTYPES = ("interactive",)   # §2m v14: protocol now has only
                                    # one multi-round type — straight-
                                    # line execution belongs to
                                    # x·solo, the "graduation" concept
                                    # is retired
XPROTO_PREFIX = "x·"         # prefix for executor seat names (task.executor)
XSOLO_NAME = "solo"          # §2m v9 reserved name for the general executor (guards protocol naming)
XSOLO_SEAT = XPROTO_PREFIX + XSOLO_NAME   # standalone intent's executor seat x·solo
XSOLO_MODEL = "sonnet"       # v9: an intent binds only to the executor,
                             # the model is pinned — the low-overhead
                             # surface is a mechanical promise, not an
                             # adjective
XSOLO_THINKING = 4096        # v9: fixed thinking budget (settings env
                             # MAX_THINKING_TOKENS; a pure call
                             # sequence needs no deep thought)
XSOLO_EFFORT = "low"         # live-fire precedent 2026-08-15: the
                             # newer CLI's thinking dial runs through
                             # settings.effortLevel, MAX_THINKING_
                             # TOKENS is no longer that knob. **Unless
                             # pinned it inherits the user's global
                             # dial** (measured: xhigh) — the executor
                             # seat inherits deep thinking, and "the
                             # low-overhead surface is a mechanical
                             # promise" falls apart on the spot.
# §2i M22: the executor seat's human interaction is only two things —
# perm (permission gate) + form (multiple choice), both route to the
# card-flow block-and-wait; no conversational port is opened.
XPERM_TOOL = "mcp__intentOS__perm_gate"   # --permission-prompt-tool points here
# Seat tool-surface trim (user ruling 2026-08-16, goal: drive per-run
# token cost to the floor). Live-fire ledger: one order took three
# round trips, only one actually did work — the other two were
# "reading its own instruction manual" and "ToolSearch hunting the
# ledger tool's handle", and each round trip re-eats the 47k resident
# base (83% of the bill is cache_read, not output). The executor seat
# is an interpreter: none of the create/retrieve row is ever used,
# the three-piece set is enough.
MCP_SEAT_EXEC = "exec"
# Surface split (S2/C1, user ruling 2026-08-23 "clean it up as far as
# quality allows"): exec = x·solo surface (three-piece set — step_done
# is exclusive to the protocol seat; hanging it on the solo surface
# costs 88 tok per order in dead tool fees); proto = the bracket seat
# surface (+step_done, and ask_user's description swaps in bracket
# wording — a host seat has no E, "only ask where E names it" is
# wrong for it).
MCP_SEAT_PROTO = "proto"
XSOLO_MCP_TOOLS = ("task_done", "ask_user", "perm_gate")
# Second-cut completion (2026-08-17): (1) built-in tool-surface
# allowlist (CLI --tools) — the interpreter seat keeps only the five
# file tools + shell, the WebSearch/Agent/TodoWrite row never enters
# the prompt at all; a tool surface small enough never triggers
# deferral, so the ToolSearch round trip disappears with it.
# (2) the MCP server pins alwaysLoad: the three-piece set's schema
# stays resident, no more fetching the handle via ToolSearch.
XSOLO_CLI_TOOLS = "Bash,Read,Write,Edit,Glob,Grep"
XGATE_WAIT_S = 300.0         # perm/form card's wait-for-human cap (same tier as M18 blocking arbitration)
# §2i seat-count law: min3/max10; interactive's ·open/·wrap function words count toward the quota
PROTO_MIN_SEATS = 3
PROTO_MAX_SEATS = 10

# Catalog total-count sanity cap (provision plane 2026-08-11: hot/cold
# are hot-surface views, the archive-surgery retired — the ledger only
# grows, unused entries sink on their own).
MAX_HOME_INTENTS = 200

# Container law (§2m v4/v10, container wave 2026-08-14; the bind
# section retired 2026-08-23, now pure session form): hot surface =
# **the container** = this session's usage set (trigger/get/shelved,
# dedup-only-grows, cleared on changeover). Heuristic set selection
# (recent/frequent segments) and human-arranged bound segments both
# retired; recommendation and recall now go through vector multi-path
# recall (whole library, container-blind). The container sets a cap,
# the total count stands — **only evicts, never invades**: past the
# cap it evicts the least-recently-used member (in-memory LRU, usage
# order; score is a separate usage ledger) — out of
# container ≠ out of the library, the cold library is still
# retrieved and still triggerable,
# re-use returns it to the container; the cap governs hotness, not how
# much the library holds. Changeover point = session restart (the
# container resets to bound, no engine restart needed).
CONTAINER_CAP = 200

# Scoring law (2026-08-11): only "named" behavior scores — trigger is
# full marks, intent_get is discounted; meta exposure (index/search)
# and the engine's internal reads score zero — the score reflects
# usage history, not exposure history (it ranks the catalog's flat
# usage top).
SCORE_TRIGGER = 1.0
SCORE_GET = 0.3

# Cold-library retrieval contract (query?, limit?) →
# {items, total_matched, mode} — no query = always mechanical (rule
# filter, agent does the final sort); with a query, goes vector (M24
# lit up, contract unchanged, only adds a protocols column): whole-
# library provisioned recall (v10: the gate governs hotness, not
# recall), top25 candidates pass a similarity threshold → two columns,
# 5 intents (no pointer) + <=1 protocol (pointer aggregation), rows
# carry name/title/scenario — the fetch itself IS context, the goal
# is to feed multi-round (v9: one search, two uses); below threshold,
# rather return empty-handed than force a match.
INTENT_SEARCH_LIMIT = 20
CATALOG_TOP = 50             # catalog tool cap: flat top-N by usage
                             # (class retired 2026-08-25 — per-class
                             # sampling went with it); rows carry only
                             # name+scenario; the long tail goes
                             # through intent_search
INTENT_GET_MAX = 20          # intent_get batch cap (names array size)
SEARCH_RECALL_TOP = 25       # candidate pool size per recall
SEARCH_TOP_INTENTS = 5       # two-column intent side (pointer-free only)
SEARCH_TOP_PROTOS = 1        # two-column protocol side (pointer-probability aggregate)
SEARCH_MIN_SIM = 0.10        # recall floor similarity (mechanical v1
                             # starting value; name/title hits get a
                             # natural bonus past the threshold)

# Word-count gate (white-paper self-check follow-up ruling 2026-08-12):
# the contract says "the system remembers for you" — a write surface
# must have a cap or dumping garbage is free. Over the cap = **reject
# the whole order**, reject reason carries the count and where to trim.
# Scenario is **a one-word situational tag** (follow-up ruling two:
# the OS desktop is messy but accountable, tidying belongs to the
# executor; descriptive text hurts retrieval, the vector layer
# aggregates on the scenario word, the same word
# clustering = the signal to enroll a protocol — steps are the
# intent's substance), steps are the execution surface (doable with
# no context), acceptance is a one-line lesson. Folded in at M19.
INTENT_SCENARIO_MAX = 20         # scenario word: one word, <=20 chars, no whitespace/punctuation (\w)
INTENT_NAME_MAX = 30             # asset name: a word OR a short phrase (user ruling 2026-08-26: English names are phrases — cap the length, not the word count); internal spaces/hyphens ok, dots/path separators never (the name doubles as the workspace directory name)
INTENT_STEPS_MAX = 1200  # tight-budget craft law (user ruling
                         # 2026-08-16, originally 600 for Chinese
                         # text; ×2 with the English word list
                         # 2026-08-25 — same meaning needs ~2× the
                         # characters, the pressure is unchanged):
                         # this isn't a quota, it's **craft** — a
                         # tight budget forces the sidecar to sink
                         # detail into tools, the executor only acts
                         # on the tool's response. Can't fit = the
                         # signal to sink it further, not to widen
                         # the cap; small = composable, copyable,
                         # tunable — doesn't fit = should be enrolled
                         # as a protocol (overflow law)
INTENT_INSTR_MAX = 800   # I-E-R (2026-08-16; ×2 with the English
                         # word list): the acceptance three-state
                         # ruling criteria (DB still carries the
                         # legacy instructions column, the
                         # declaration-surface key is now acceptance)
                         # (what the executor must comply with;
                         # filled and capped separately from steps)
# CAVEAT_MAX retired (user ruling 2026-08-25): the caveats column is a
# fossil — lessons flow back through the sidecar revision channel
# (retry/rework brackets), the task loop IS the precedent mechanism.

# ---- E-section grammar (schema-based language, user ruling 2026-08-16 late night) ----
# intent declaration functionalized: I = formal params (context the
# physical layer collected + the trigger's raw text) · tools = methods
# · E = do-if-else pseudocode function body · R = the fixed
# three-state return.
# Verb table = a closed word list (engine's property, the agent only
# has usage rights, extending it = human ruling — same law as the
# procedures library); each verb carries its own content-length budget — the
# ground rule "word-count cap — word-list cap" applied: the word list
# gates the semantic space, the length caps gate the quantity. judge
# is the only verb that opens semantics (it spends LLM money, has
# the widest budget, priced explicitly).
# Full-English word list (user ruling 2026-08-25, no legacy
# fallback): re-run `intentos seed` after upgrading — it refreshes
# the system templates' rows; user-authored intents re-register.
# Budgets ×2 from the Chinese-era values (same-meaning English text
# runs ~2× the characters); the pricing structure is unchanged:
# judge = 2× the mid tier = 2.5× the mechanical tier.
E_VERBS = {"read": 160, "inspect": 160, "write": 160, "call": 200,
           "open": 160, "stop": 160, "ask": 200, "report": 200,
           "judge": 400}
E_COND_MAX = 80      # condition-clause free-text cap, ×2 for English ("mechanically judgeable" is discipline, left to human eyes)
E_GRAMMAR = ("each line 'N. <verb> <content> [-> if <condition>, "
             "(<branch>, <branch>)]'; branch ∈ next | L<n> | ok | "
             "ok_issue(one line) | failed(one line) | ask(one line); "
             "branches omitted = if it works, (next, failed(copy the "
             "line)); jumps go forward only (no loops)")

# Class retired (user ruling 2026-08-25): the mechanical assigner
# scored CJK char overlap — characters are morphemes in Chinese,
# noise in English — so the whole filing axis is removed. The
# workspace layout is flat (root/<name>), the DB class column is a
# fossil (additive law), aggregation rides the scenario word alone.
# INTENT_CLASSES / CLASS_POOL_CAP retired with it. The library has
# no cap: caps govern hotness (CONTAINER_CAP), not how much the
# library holds; MAX_HOME_INTENTS is the global sanity check. The
# protocol total-count gate stays.
PROTO_TOTAL_MAX = 50

# Caller pipe (2026-08-11): identity is a mechanical truth issued by
# the engine, agent self-reports don't count — at power-on the engine
# casts a token into .mcp.json's env, the bridge carries it back on
# every call frame. During single-instance operation a missing token
# is charitably treated as home; from the B6 second instance onward
# it becomes mandatory.
MCP_TOKEN_ENV = "INTENTOS_TOKEN"

# Priority tiers (§2h queue law 2026-08-13, named 2026-08-10, three
# tiers): user's three levels + an engine-reserved tier. Intake law —
# a seat's queue only accepts orders >= the queue's current top tier:
# equal joins the line (same-tier FIFO), higher cuts in (jumps to
# queue head), lower is refused outright; an already-queued
# lower-tier order is grandfathered, not evicted. Declared on the
# spec, never changed at runtime (priority law).
PRIORITY_EXEC = 0              # execution: intent delivery, protocol bracket
PRIORITY_ALERT = 1             # alert: create/consolidate/sim (self-built, self-tested)
PRIORITY_ERROR = 2             # error: surgery (retry / debug / breach)
PRIORITY_INTERNAL = 3          # engine maintenance reserved tier (non-cancellable)
# Legacy-name compatibility (aliases in use from the 2026-08-10 naming)
PRIORITY_INTENT = PRIORITY_EXEC
PRIORITY_SELF = PRIORITY_ALERT

# Host CLI's model (user ruling 2026-08-10: testing is always sonnet,
# fable burns money; 2026-08-15 exception: **the create flow uses
# opus** — building an intent needs deliberation, override with
# --model opus at launch)
HOST_MODEL = "sonnet"
# Sidecar seat listed separately (user ruling 2026-08-23: the sidecar
# is responsible for the compile mode, pinned to opus + high — not
# xhigh. --model only governs the general host seat (the x· roster),
# it doesn't override these two; x·solo still runs the pinned
# XSOLO_* values)
SIDECAR_MODEL = "opus"
SIDECAR_EFFORT = "high"
# Primary seat's thinking budget (user ruling 2026-08-15 "tune
# thinking to medium first"). This is the primary seat's thinking
# tier (user ruling 2026-08-15: the create flow needs deliberation,
# give it the medium tier).
# **Two knobs, effortLevel is authoritative** (live-fire 2026-08-15:
# CLI v2.1.233's banner reports effort, not a token budget; the env
# one stays as a fallback for older versions):
#   settings.effortLevel   low · **medium** · high · xhigh
#   settings.env.MAX_THINKING_TOKENS  low 4k · medium 10k · high 32k
# Without an explicit pin -> inherits the user's global dial
# (measured: xhigh), and the seat's cost promise fails.
# After editing, the engine must restart to re-cast the household and
# take effect.
HOST_THINKING = 10_000
HOST_EFFORT = "medium"
SIDECAR_THINKING = 32_000    # value for the high tier (fallback for older versions, matches SIDECAR_EFFORT's tier)
# Injection ack window (CASELAW 48, live-fire 2026-08-15): once a
# message is fed to the host, if the transcript directory gets no new
# bytes within this many seconds = suspected non-delivery (most
# likely eaten by a dialog mid-flight) → an info card carrying a
# screen-tail capture. The engine doesn't recognize dialogs, it only
# verifies "did the message land".
INJECT_ACK_S = 20.0
# Busy-deferred ruling (CASELAW 60 addendum, 2026-08-17): an
# injection during the host's turn queues but hasn't landed in the
# transcript — if the transcript is still growing (assistant lines),
# the host is busy, and ruling it lost right then is a false
# positive. While busy, keep watching; only judge lost on silence or
# at the hard cap.
INJECT_ACK_MAX_S = 180.0     # hard cap: even while busy, can't wait forever
INJECT_BUSY_QUIET_S = 6.0    # this many seconds with no new transcript bytes = host is now idle

# Soft-deck geometry has been removed (user ruling 2026-08-23: the
# key-binding section retired along with native Elgato UI — a key is
# just an HTTP request, the slot table / virtual deck carry no load).

# PTY injection (CASELAW 12/13/15)
PASTE_BEAT = 0.15              # one beat between body text and \r, seconds
READY_BYTES = 512              # output byte count = evidence the TUI
                               # has mounted. Measured 2026-08-12: CLI
                               # v2.1.228's banner narrowed, the whole
                               # composer mounts at just 1015 bytes — 9
                               # bytes short of 1024, stuck at
                               # "booting" — the threshold only needs
                               # to tell "not a single character out"
                               # from "the screen is there", 512 is
                               # enough, don't push it higher
SPAWN_SETTLE = 8.0             # conservative floor for the readiness probe, seconds
# Semantic status palette (DECK-UI refresh draft, user ruling
# 2026-08-23): the deck key face, status bar, and observe page share
# one source — success green / fail-refused red / queued yellow /
# running blue / waiting-on-human orange / idle gray. The deck
# side's PNG/SVG all pull values from here, no custom colors elsewhere.
ST_COLORS = {
    "ok":    (52, 211, 153),   # #34d399  done ok / ok_issue (blinks)
    "fail":  (248, 113, 113),  # #f87171  fail / timeout / perm refused (blinks)
    "queue": (251, 191, 36),   # #fbbf24  queued (breathes)
    "run":   (96, 165, 250),   # #60a5fa  running / step running (breathes)
    "await": (251, 146, 60),   # #fb923c  card waiting on a human (solid)
    "idle":  (75, 85, 99),     # #4b5563  idle / closed (still)
}
# P1-b screen-flip gate (live-fire 2026-08-23, two rounds; rule is in
# the pty.ready comment):
TRUST_FLIP_SETTLE_S = 3.0      # minimum wait from the flip
TRUST_FLIP_QUIET_S = 1.5       # and this long with no new bytes recently (redraw burst has stopped)
TRUST_FLIP_CAP_S = 15.0        # hard cap: continuous redraw / a slow machine still won't lock forever

# Cockpit (M13, docs/M13-COCKPIT.md): card flow + hook bypass + stall
# detection. The hook surface v1 = a pure bypass (zero hot-path
# cost): Notification/Stop → the hookfwd mailbox → /api/hook; never
# returns a decision, the reply channel is the card's key/line → PTY
# (zero padding upstream). PreToolUse blocking arbitration is an
# escalation lever on record (probe two proved both block and
# decide), off by default.
IDLE_STALL_S = 20.0            # running loop and PTY idle past this many seconds -> stall card
STALL_TAIL_CHARS = 800         # screen-tail capture length carried by perm/stall cards
HOOK_TIMEOUT_S = 5             # timeout of the hook entry in settings.json (seconds);
                               # the mailbox's own POST timeout is 4s
                               # (inside hookfwd, pure stdlib, doesn't
                               # read this back) and dies first

# Approval window (M18, docs/M18-APPROVAL.md): PermissionRequest
# blocking arbitration. The die-off order matters: engine park 290
# dies first → permfwd 298 → the hook entry in settings 300 — if the
# CLI kills the hook = defer (falls back to the native popup), harmless.
PERM_ASK_WAIT_S = 290.0
PERM_HOOK_TIMEOUT_S = 300
# Afterglow grace window (measured 2026-08-12, two failures
# overnight): Notification arrives ~6s after PermissionRequest — a
# human who answers fast in 2s would miss a 3s grace window. Baseline
# takes whichever of "ask born" / "answer done" is closer, 10s covers
# scheduling + jitter.
PERM_NOTIF_GRACE_S = 10.0

# ---------------------------------------------------------------------------
# CLAUDE.md — the sidecar's entire identity (user ruling 2026-08-10:
# not bound to an intent, chat support only, keep it brief; scenario/
# boundary customization never migrates). Engine's own property,
# rewritten every power-on (CASELAW 28).
#
# Memory boundary (PRODUCT2 twelfth draft): dynamic/episodic memory
# belongs to harness memory (the agent's own capability); long-lived
# assets go through the intent catalog (a later batch) — no more dark
# index here.

HOME_CLAUDE_MD = """\
# sidecar — IntentOS's interaction face and maintenance seat

**IntentOS is an index over the knowledge base that is the user's
local OS**: it accretes "how this person uses this machine" into
reusable, triggerable assets. The user is the engine's **owner and
operator**; they want exactly two things, and the system splits into
exactly two execution seats to match:

- **Straight-line quick triggers** (fast / low overhead) — the
  **executor seat** (headless, one process per order, zero session
  residue). The user fires an intent from the deck / IME, the engine
  delivers straight to the executor — **it never passes through you**.
- **Sustained interaction** (multi-round / complex collaboration) —
  a **protocol's resident seat**: one household seat per booklet
  (x·<booklet>). The user presses the deck's Start key to open a
  bracket; the hosting happens in that seat's own window, booklets
  run in parallel — **it never passes through you**.

You are the **maintenance seat**: creation, revision, testing,
surgery, rework diagnosis, everything in conversation — that is your
whole job. Every asset the two execution seats run is compiled and
repaired by you.

The harness gives you one session; IntentOS gives you **a position
that never moves house** — the person you get to know here and the
craft you accumulate don't dissolve when the session ends. (Session
history is not saved; what persists is task records, your memory,
and the shared toolkit. scratch is a drafting bench, wiped on every
engine restart — put finished work in its proper place, don't hoard
it on the bench.)

## Your work: tasks

Work arrives from the engine: `[task N] … | package: <path>` — read
the package, do it, close the books with `task_done` (task=N,
outcome=ok|failed, summary in one line). **If you don't close the
books, the engine waits on you forever.** The work you receive is
**maintenance-seat work**: surgery (an executor run failed — clean
the residue, fix the asset), retry brackets (the user isn't
satisfied — you fulfill directly until they nod), rework diagnosis
(firing failed / QA rejected).
Plain intent orders never come to you — those live on the executor;
protocol brackets don't either — those live in each booklet's own
resident seat.
**Which face to answer on depends on where it came from (law)**:
work the engine delivered (task envelope / card) is always answered
on the engine face (task_done / cards) — **delivery means the user
may not be at this window**, saying it only in conversation is
saying nothing; what the user types in conversation is answered in
conversation as usual. Mechanism details (surgery / retry / cancel /
timeouts / firing failed) are in the skill **task-delivery**.
**A cancelled question form yields zero answers (user ruling
2026-08-25)**: when the user cancels or declines a question form,
every part of it is void — adopt nothing from it (not the
highlighted option, not a half-entered value, not an answer that
looked selected before the cancel), and re-collect whatever you
still need in plain conversation. Live-fire precedent: a cancelled
form's device pick was silently carried into the build and the user
had to correct it.

## Your assets: intent and protocol (two species, peer rank)

Every capability you accrete for the user is a **reusable knowledge
asset** that **belongs to the user** and outlives any one session.
The two species split on **state** (user ruling 2026-08-17): intent
= a **one-way order** to the system (pure function, stateless, one
firing one order); protocol = a **stateful workflow** (opens and
closes as a bracket, and the steps in between must remember each
other). The one-line test: **does the next step need to remember
what the last one chose? Yes → protocol, no → intent.** An intent
can be distilled into a protocol as a member.

**Registration compiles onto the Stream Deck (you never touch it —
you only relay)**: a standalone intent going live joins the system
"IntentOS · Intents" key group (trigger keys + the Engine
Start/Shutdown/Status/Task and Solo Approve/Cancel system keys); an
approved protocol gets **its own sidebar key group** (Start/Approve/
Interrupt/Shutdown + member keys + Status/Step dials, group name =
booklet name). When the roster changes, the Stream Deck app must be
**restarted once** before the sidebar shows it (keys already placed
on the deck read routes live and follow automatically). The engine's
approval receipt carries this reminder — just pass it on to the
user; compiling, sweeping, and the resurrection command all belong
to the engine.

An intent is **one segment of E** (steps required, a pseudo-code
function body) — mechanical detail sinks into tools, and the
executor acts on what the tools answer. Upfront context collection
(screenshots and the like) is the engine's **physical layer**: the
optional `procedures` field in an intent's declaration references
the engine's built-in library by name; on trigger the engine runs
the prelude first and the materials ride with the order — the
declaration is yours, the library is not yours to write. How to
write all this: the skill **intent-creation**.

**steps are written for the executor, not for you**: the executor
is a stateless stranger seat (a fresh process per order that sees
only the package and what's on disk), so steps must be pure
mechanics — "call this, then call that" — that anyone could follow.
Not fitting inside the character caps = the signal this belongs in
a protocol booklet.

**These assets are not your memory, they're the engine's ledger —
no jitter, no loss, nothing to memorize.** How to use the four
retrieval tools is described at the call site; here, only when:
start work by warming `intent_memory_index`; find material with
`intent_search`; fall back to top-N with `intent_catalog`; details
by name with `intent_get`. When something in conversation smells
like some intent's scenario — remind the user they can trigger it.

**Initiative belongs to the user.** For things done repeatedly,
**remind** them it could be saved as an intent; for workflows that
form a set, **remind** them a protocol could be opened — spotting
the moment is your job, the call is theirs. Only after they nod do
you `intent_submit` (**opening the ticket founds the workspace** —
the engine creates it by name and returns the path, the schema.md
field textbook lands with it). Write the pieces into the directory, edit the
declaration in `intent.json`, then `workspace_submit(name)` —
**registration = compilation**, and the human gate sits only there.
**The sink-down law**: mechanical lines that keep causing friction
in E get written into a tool and pushed down. The full creation
guide is the skill **intent-creation**.

**Before writing any script, glance at the shared toolkit** (the
workspace's `toolkit/`, read-write for you) — the value of existing
scripts is **the blood in their comments** (hard constraints proven
live); rewriting from zero = re-stepping into every pit. New
scripts that generalize go into toolkit; ones serving a single
intent go into that intent's own `tools/`. This rule is permanent,
exploration phase or compile phase alike (live accounting and fine
print in the skill **intent-creation**).

## Library shape

The library has no cap: caps govern the hot surface (containers),
not how much the library holds — relative position in the ledger is
maintained by usage, and **remaining in the ledger is itself
maturity**. **The same scenario word piling up forms a family** —
remind the user that family could become a protocol (initiative is
theirs).

## Your territory

**Yours**

- `scratch/` — drafting bench, build freely; **wiped on engine
  restart, never preserved**. Working files for a task in flight go
  here; finished goods go to their homes — reusable scripts to
  toolkit, submitted pieces get snapshotted by the engine, task
  products go into that task's directory.
- `toolkit/` (shared workspace ground) — you are the compiler of
  every intent, and this is the tool store you share with the
  executor (read-only on their side): **only generalizable utility
  scripts** (useful across intents) live here. Placed once,
  permission rules bind to the path, content changes ripple
  nothing. One-off scripts owned by a single intent don't belong.
- `memory/` — **your private property**. This user's habits and
  temper, this machine's environment and quirks, which road worked
  and which hit a wall — all recorded here. **Recording is your
  duty**: the more accurately you remember, the more you are an
  assistant who has known them for years rather than a stranger
  starting from zero. It survives sessions; the next you picks it
  up. (The line against intents: an intent holds what **any agent
  with zero session context** could follow; memory holds what's
  only true here — keep each in its place so retrieval can find
  them.)

**Not yours**

- `state.db`, `records/` — the system's books. You don't read or
  write them; to see them, always go through the tools
  (`intent_memory_index` / `intent_get` / `task_done`) — that's the
  front door built for you.
- `CLAUDE.md` (the sheet you're reading), `.claude/`, `.mcp.json` —
  the identity and configuration the system minted for you, not
  yours to change. If you think they should change, tell the user.
- The permission plane (the §2t split): **your seat's approvals
  belong to the harness** (auto mode / prompts / the user's own
  settings) — the engine only mints its own deny floor and no
  longer accretes allow on your behalf; **the executor seats'
  permission floor** is provisioned by the engine from approval
  history — a different seat's books. **You never grant yourself
  permission**: if you need one, state the reason clearly and let
  the user approve.

## Rules of engagement

Operating preferences (which shell, which actions are off-limits,
how files are arranged) are **this user's preferences and live in
your memory** — read them before starting, keep recording. Only one
rule is spec-level: before touching the user's existing files, state
your intent and wait for their confirmation.
"""

# Delivery rendering (the kind=deliver ring; delivery law: the substance lives on disk)
PACKAGE_MD = """\
# intent: {name} ({title})

## Scenario
{scenario}

## User input (this run's parameters)
{user_input}

## Execution steps (E)
{steps}
{materials}
## Acceptance criteria (R — the three-state ruling, fixed at compile)
{acceptance}

## Wrap-up (engine discipline)

Settle with `task_done` (unsettled orders are waited on forever) —
delivered work reports through the engine: the summary is the report,
never assume the user is watching this window.
"""
TASK_LINE = "[task {tid}] intent {name} | package: {path}"
# M26: the protocol instance's (PTY seat) bracket-open envelope — pointer form, the wording is literal
PROTO_TASK_LINE = "[task {tid}] protocol {name} | package: {path}"
# Shutdown key's graceful grace period (user ruling 2026-08-23):
# seconds to wait for the CLI to self-exit after ESC + /exit,
# tree-kill as fallback on timeout
PROTO_EXIT_GRACE_S = 6.0
# step_done's silent fallback (display-only, carries no real weight):
# if the step ledger reads running but the PTY has been silent past
# this many seconds → the ledger falls back to idle (in case the host
# forgets to call step_done, the Step bar stops staying stuck blue).
# The truth is still step_done's account — this is only dashboard
# rust removal.
STEP_QUIET_S = 90.0
# Hub window's reserved seat name (user ruling 2026-08-23: all
# instance UIs collapse into one browser window, one seat per tab).
# The prefix avoids x· so it never collides with a real seat name.
HUB_SEAT = "·hub"
# Headless direct-delivery form (second-cut completion): the envelope
# carries no path — live-fire 2026-08-16: even though the full text
# already rides along in the order, the envelope header still wrote
# package: <path>, and the executor saw a path and casually Read it,
# paying for a free round trip. Pointer form is now reserved for the
# PTY seat only.
TASK_LINE_INLINE = ("[task {tid}] intent {name} | full order below — "
                    "no disk read needed, execute per E")

# Materials section: the physical-layer prelude's (an intent's
# declared procedures, run by the engine on trigger) output is sewn
# into the package — material already staged, steps begin from
# "the material is already here"
MATERIALS_SECTION = """\

## Materials (pre-staged by the engine's prelude procedures)
{rows}
"""

# The interactive bracket's opening package (rendered into the task
# directory on a start trigger) — from M26 delivered to the
# protocol's own instance seat (English throughout: the executor-
# facing surface is always English)
PROTOCOL_PACKAGE_MD = """\
# protocol: {name} (interactive bracket, task {tid})

**You are now in protocol state**: host this multi-round interaction by
the skill below. Your household brief (CLAUDE.md) carries the bracket
rules — **opening runs the system ·open step below and nothing else**
(then greet in one short line at most), member steps are hosted by the
roster's E sections and claimed with `step_done`, and only the user
closes the bracket (Shutdown key delivers a system ·wrap step first;
task_done here is refused).

## Opening step (system ·open — run BEFORE greeting)

{prep}

Opening input (what the user said when triggering Start — rendered here
so you never have to ask for it again): {input}

- Bracket members: {members} — the whole bracket is one task; member
  keys drop step envelopes into this conversation.
- **Member declarations are pre-warmed below (see "Member roster") — no
  intent_get needed**; the skill itself only declares how the members
  aggregate, never their details.

## Member roster (pre-warmed from the ledger — live values, fresh at
bracket open)

{roster}

---

{skill}
"""

# Roster unit (rendered by the engine per member; E/acceptance are
# live ledger values, fresh at bracket open). The tools-dir line is
# the M-section's in-bracket projection (live-fire precedent
# 2026-08-16: not rendering the path cost 2 extra hands of ls+find
# self-rescue on the first run after bracket open) — only the engine
# knows the true address of what it has stamped.
PROTO_ROSTER_ITEM = """### {name}{title}
scenario: {scenario}
tools dir: {tooldir}
E:
{steps}
acceptance: {acceptance}
"""
PROTO_ROSTER_NONE = "(no members — free-form multi-round)"

# ·open/·wrap made real (user ruling 2026-08-24): open/close are **two
# built-in system steps** — the engine auto-delivers them at bracket
# open/close. prep (opening setup) is declarable in protocol.json;
# **wrapup is engine-owned** (user ruling 2026-08-26: a declared
# wrapup smuggled extra ceremony in and blocked shutdown — ·wrap is
# the fixed final-cleanup contract below, closing domain work
# belongs in a member step the user presses). A user-built member
# can never occupy these two slots (reserved names are rejected at
# registration).
PROTO_PREP_NONE = ("(none declared — greet in one short line and "
                   "wait for member keys)")
PROTO_WRAP_FINAL = (
    "this is the FINAL cleanup — the session is shutting down on the "
    "grace clock right after (a second Shutdown press forces it "
    "sooner). Do not start new work and do not improvise extra "
    "ceremony. Stop and clean up everything this session started and "
    "still owns — processes, windows, long-running jobs — closing "
    "only what this booklet opened, never shared apps. Flush the "
    "booklet's state to disk where it belongs, then one closing line "
    "to the user")
PROTO_WRAP_GRACE_S = 45.0     # wrap-up grace period: clock after the ·wrap step is delivered, waiting for step_done
PROTO_HOOK_MAX = 800          # word-count gate for prep (×2 for English; same order of magnitude as steps)
PROTO_RESERVED_MEMBERS = ("·open", "·wrap", "开启", "结束", "收场",
                          "prep", "wrapup")

# Failback consolidation ruling (user's second review 2026-08-16,
# repeals the same-day 53 out-of-bracket mechanism): the recovery
# path isn't an engine-level special order — it's **wrapped inside
# the execution strategy**, i.e. an if-branch in the E section; the
# engine's flow graph has no failback node. Third review (same night)
# folds further: procedure = the control protocol's physical layer,
# attaching to a key never enters the delivery chain — if it blows up
# it's reported to a human, not sent back for surgery and not
# suspended on the intent (a broken keyboard doesn't mark the
# document as damaged). The in-bracket exception stands: the host
# seat IS the recovery agent.
# M-section (user ruling 2026-08-16): **registry entries are filled
# by the engine, the agent never writes paths**. The law "a name, not
# a path" used to cover only the declaration surface (steps
# referencing by name); the delivery surface leaked — live fire: E
# wrote "呼 练琴表", the executor got it with no idea where that file
# lives and had to Glob on its own; procedure had the same disease,
# forced to pin absolute paths (defect (2)). Only the engine knows
# the true address of what it has stamped a hash on, so only it is
# qualified to fill it in. **External target directories are
# excluded** (a sheet-music library is that intent's own knowledge,
# written into steps/conventions, not the engine's to fill).
XSOLO_METHODS_NONE = ("(this intent has no registered tools — "
                      "reference none in E)")

# §2m v9/v14 the general executor's (x·solo) delivery package: an
# intent has no single skill source, steps (pure call sequence) +
# instructions (notes/preference constraints) ride along in full —
# the package IS the entire instruction
XSOLO_PACKAGE_MD = """\
# intent: {name} ({title}) — command order

**You are an interpreter, not a planner**: execute the E section line
by line, branching on its condition lines; a condition not listed takes
that line's default else. Off-script situations (E doesn't cover it /
environment mismatch) → ask only at forks where E explicitly says
ask_user, otherwise settle failed — no exploring, no improvising.

## I · input (procedure-staged context + this trigger's input)
{user_input}
{materials}
## M · methods (filled by the engine from the registry — names map to
paths, never go hunting)
{methods}

## E · execution (pseudo-code function body, follow line by line)
Grammar: {grammar}

{steps}

## R · report (settle by the stated criteria, never invent your own)
{acceptance}

When done, settle with `task_done` — outcome semantics live on the
tool; the summary is your only voice.
"""

# Acceptance default criteria (rendered when the declaration omits R criteria)
XSOLO_ACCEPT_DEFAULT = """\
(no criteria declared — defaults apply)
- ok: every E line took its ok path
- ok_issue: done, but with friction (trial-and-error / detours / \
partial output)
- failed: any line hit a failed branch, or the end state contradicts \
the intent"""

# §2m v9/v14 general executor's CLAUDE.md (x·solo home, rendered at
# casting time): all intents execute here (can run in parallel, one
# process per order) — the sidecar only keeps the create/test/debug/
# protocol multi-round surface
XSOLO_CLAUDE_MD = """\
# Executor seat: general (x·solo) — command interpreter

You are IntentOS's headless general executor — **an interpreter, not a
planner**. **Stateless**: every order runs in a fresh process, zero
memory, zero session residue — behavior is determined only by this
order's package and durable artifacts on disk.

Three steps per order, fixed:
1. **Execute E** — follow the order's E section line by line; its
   discipline rides the package.
2. **Set status against the R criteria** — declared or default; you do
   not invent standards.
3. **Settle** — `task_done` (semantics on the tool). **The summary is
   your only voice**: the user is NOT at this window — nothing said
   anywhere else reaches them.

Human interaction: an insufficient permission pops a card and waits
(engine-wired, invisible to you); `ask_user` only where E says so.
Beyond these, stay silent.

Iron rules:
- **Everything you need is in the order's [M · methods] section** (the
  engine filled names → paths from the registry) — use those paths
  directly, **never hunt, never guess paths**.
- The shared toolkit lives at {toolkit} (read-only): local scripts E
  references live there.
- Anything that must outlive this order **goes to disk, nothing else**
  — you cannot remember this order in the next one, and must not
  pretend to.
- Never repair or create intents — maintenance belongs to the sidecar;
  write what you found into issue/summary and the human relays it.
"""

# M26 §3: the protocol instance's CLAUDE.md (household seat, rendered
# at casting time). A resident conversational seat: one household per
# protocol, brackets open here, member steps deliver here, the card
# flow window faces this; memory belongs to it privately, persists
# across sessions.
PROTO_HOST_CLAUDE_MD = """\
# Protocol seat: {name} — resident interactive host

You are the resident host of protocol "{name}" on IntentOS. This home
is your **household**: it does not move, it survives sessions, and your
`memory/` here is yours — the longer you live here, the better you host
this one workflow.

## How work arrives

- `[task N] protocol {name} | package: <path>` — a bracket has opened.
  Read the package (skill + member roster + the user's opening input)
  and host the multi-round interaction by it. **Opening starts
  nothing**: never begin a member intent or the workflow on your own —
  greet in one short line at most and wait for a step envelope or the
  user's message.
- `[task N] protocol {name} step | intent <member> | input: …` — the
  user pressed a member key. Run that member's E section from the
  roster; keep the bracket's running context in mind (that is why this
  seat exists). **When the step is finished, call `step_done` (member =
  that member's name)** — a one-line ledger claim that keeps the deck's
  Status/Step bars honest. It opens and closes nothing; the bracket
  stays one task.
- Plain text lines are the user talking to you through this protocol's
  window. Answer in the conversation.

## The rules of the bracket

- **The user closes the bracket, not you.** The Shutdown key settles
  the ledger engine-side; `task_done` on the bracket task is refused.
  When the close notice arrives, wrap up what is in flight. Shutdown
  then ends this session gracefully and closes the window — your
  household and `memory/` stay; the next Start revives you here.
- One bracket, one ledger entry: everything between Start and Shutdown
  is a single task — member steps never open new ones.
- Questions to the user go through `ask_user` (card flow of this
  window, ≤12 options; the card also takes a typed free-form answer,
  so an off-list reply is always reachable). Their Approve key answers
  the newest card. Ask only at real forks; prefer defaults. Open a
  card when the OPTIONS deserve to be seen as a list; judgments and
  reports go straight to chat.
- Opening and closing are SYSTEM steps, not members: the opening
  envelope carries a `·open` step (the booklet's declared prep — run it
  before greeting), and the Shutdown key first delivers a `·wrap` step
  (the engine's fixed final-cleanup contract — the same for every
  booklet). Finish `·wrap` and call
  `step_done(member="·wrap")` — the seat closes right after (or after
  the grace clock; a second Shutdown press forces).
- Permission dialogs render in this window's terminal drawer; approvals
  the user grants persist in this household — earned trust stays here.
- `memory/` is your private ledger of this workflow's quirks: the
  user's preferences inside this protocol, paths that worked, walls you
  hit. Write it; the next session's you reads it.
  **Memory supplements the booklet, it never overrules it (user ruling
  2026-08-25)**: put nothing in `memory/` that directly contradicts
  your given protocol — its declarations, steps, or boundaries —
  unless the user specifically instructs you to. When you notice that
  case (the user keeps wanting something the booklet's text forbids or
  does differently), steer them to consolidate the change through the
  sidecar: the booklet gets revised and re-approved there, and your
  memory stays a ledger of facts, not a shadow copy of the rules.
- The shared toolkit at {toolkit} is read-only from this seat.
"""

# §2m v9 standalone intent's surgery table (the general executor's
# failure recovery loop): what gets repaired is the intent itself
# (editing intent.json and re-registering takes effect immediately,
# no skill approval-queue step)
XSOLO_SURGERY_MD = """\
# Surgery: standalone intent '{name}' (task {tid})

The general executor (x·solo) failed this order. You are the
maintenance seat — perform repair surgery.

- Failed order: task {origin} (directory {origin_dir})
- Failure receipt: {fail}
- User note: {note}
- Residue map (tools and targets the executor actually touched,
  telemetry-bus record):
{residue}

Surgery scope:
1. **Clear residue** — walk the map for half-finished files and
   misplaced products; remove or relocate them.
2. **Repair the intent** (only if needed) — edit the workspace's
   `intent.json` (steps/acceptance live there); it takes effect
   immediately (the next order runs the new version).
3. `task_done` settlement = **the ONE ignition signal**: on
   settlement the system auto-replays the original order (same
   intent, same input).

Do NOT re-trigger it yourself — replay is the system's job. One
surgery carries exactly one auto-replay; a second failure goes back
to the user.
"""

# Cancel receipt (context-sync ruling): a hanging task is a future
# obligation in the issuer's context — if the chain is cancelled
# externally, a receipt must be sent back, no dead accounts left
CANCEL_LINE = ("[chain {cid}] cancelled | the {spec} you initiated has "
               "been cancelled; no further rings will arrive — close "
               "any related waits")

# Debug-diagnosis script (qual·rework n0). The original inbound edge =
# the procedure section's on_fail, already cut loose by the physical-
# layer ruling (2026-08-16 night: if the physical layer blows up,
# report to a human, no surgery-return) — the flow stays as a ready-
# made object for the E-layer failure to wire up to later; sim's
# n1→n0 loop-back still uses it.
DEBUG_MD = """\
# Debug diagnosis (task {tid})

**Engine has filed a debug task** — intent '{name}' failed its
mechanics chain at task {origin} (firing failed / sim not passed;
see reason). **The intent is suspended back to draft** (IME/deck
triggering paused; it re-shelves automatically once repaired and
re-checked).

**Error text (reason): {reason}**

Your job, in order:

0. **The clock is running**: this order is in running state — no
   settlement within {mins} minutes and the engine rules it failed
   (time-limit law). If the repair must wait on a human approval,
   **say so in the conversation first** — a late approval burns
   through. Mechanical fact, not something you are trusted to
   remember.
1. **Diagnose the root cause**: check the error the reason names
   (journal err tails, the previous task directory
   runtime/tasks/{origin}/) — locate whether it is E, tools, or
   data; **the source lives in this intent's workspace** — look
   there, fix there.
2. **Repair (multiple rounds allowed)**: fix the pieces in the
   workspace (steps/tools/scenario), then `workspace_submit` to
   re-register (run `intent_get` first to see the current body).
   Fold universal lessons into steps (with condition lines).
3. `task_done` settlement — **ok = submitting the repair**: the next
   node auto-runs a sim check; passing re-shelves the intent, a sim
   failure loops back for another diagnosis round. summary =
   diagnosis + repair note (the engine relays it — never assume the
   user is watching this window). Do NOT re-execute this intent
   yourself — whether to re-run after re-shelving is the user's call
   (initiative belongs to the human).
"""

# Time-limit law v1 (2026-08-10): running (delivered, unsettled) past
# timeout is ruled failed, the ruling rides the receipt. gated has no
# clock (a gate may never approve); per-intent overrides hang off the
# validator batch. timeout folds into fail (M12: no third state, goes
# through the on_fail edge).
TASK_TIMEOUT_S = 15 * 60

# Loop guardrail (M12 flow graph): a loop-back edge is legal (rework =
# sim.on_fail looping back to the diagnosis node), capped by a hop
# count — the same token revisiting the same node past the limit
# halts the chain, the conversation surface says "loop limit hit,
# waiting on a human". 4 = headroom for three rounds of rework.
MAX_NODE_VISITS = 4
TIMEOUT_LINE = ("[task {tid}] timeout | no settlement within {mins} "
                "minutes — the engine has ruled it failed; do NOT "
                "task_done it anymore. Tell the user whatever results "
                "you already have in the conversation; redo waits for a "
                "fresh delivery")

# Retry bracket (user ruling 2026-08-23, R5 two-round ruling —
# supersedes the 2026-08-10 "validate + steer re-file" two-loop
# form): **retry-fulfill = a built-in engine protocol, a bracket
# hosted by the sidecar**. The first booklet in the built-in protocol
# library, same law as the procedures library (engine's own, the
# script = the defaults text, no agent submission port) (grill-me
# [the create flow] will be the second booklet later). Mechanical
# form: a single ring delivered to the sidecar, the bracket law is
# exempt from the time limit; task_done on this order = claim, not
# close (the engine posts an acceptance card, resubmission overwrites
# it); the user approves to close the bracket, continuing to talk is
# the next round. The steer mechanism and RETRY_VALIDATE_MD /
# RETRY_SECTION retire together with the old form.
RETRY_FULFILL_MD = """\
# Retry: intent '{name}' (task {tid})

**The user has asked for a retry** — the previous run (task {origin},
final status {prev_status}, record: {prev_outcome}) did not satisfy
them. Previous package: {prev_pkg}

**The user's complaint: {reason}**

Two duties, in order:

1. **Autopsy** — read the previous run (its package above, and the
   record) and name the root cause of the miss in one line: what did
   the executor do, and why did that not match what the user asked?
   A retry you can't explain is a retry you can't prevent.
2. **Redo, in this seat** — deliver what the user actually wanted,
   yourself. Do NOT re-trigger the intent and do NOT route it back
   to the executor. Grill when unsure — the user just asked for
   this and is reachable in this window: ask in the conversation
   until "right" is pinned down.

Then settle with `task_done(outcome, summary)` — a real settlement,
no acceptance round; if the user is still unsatisfied they press
retry again. **The summary must carry the root cause** (one line).
Do NOT edit the intent inside this task: after settlement the
engine offers the user a **consolidate** card — fixing the asset is
a separate, human-approved order.
"""

# Consolidate order (user ruling 2026-08-25): the asset is suspended
# the moment the user approves the consolidate offer; this package
# tells the sidecar how to fold the lesson in and bring it back
# through the registration gate.
CONSOLIDATE_MD = """\
# Consolidate: {kind} '{name}' (task {tid})

The user pressed **Consolidate** — {kind} '{name}' is now
**suspended** (off the keys and the IME, triggers refused) until a
revised declaration passes the registration gate.

Evidence: {evidence}

Your order:

1. Review the evidence trail (the task directories under
   runtime/tasks/ and the record) plus the current declaration in
   the workspace folder; name what should change.
2. Ask the user when the direction isn't already pinned by the
   evidence — they just approved this, they're reachable.
3. Fold the durable fix into the workspace declaration (an intent's
   intent.json — steps/acceptance; a booklet's skill.md / member
   sheets), then `workspace_submit`.
4. Settle this task with `task_done(ok, summary)` once submitted.
   The user's approval of the registration card is what revives the
   {kind}; nothing goes live before that gate.
"""

# M20 §2u workspace plane (user ruling 2026-08-15): the directory is
# source code, the library is the executable form, **registration =
# compilation**. This guide sits at every workspace root, readable by
# anyone who enters the directory — including the isolated executor
# (memory is the agent's private property, the executor can't read
# it; conventions must live here to take effect for everyone —
# precedent 2026-08-15: sheet-music conventions sat in the sidecar's
# memory while scoreopen lived in the shared repo, unreachable at
# execution time).
WS_GUIDE_MD = """\
# {name} — this intent's workspace

Scenario: {scenario}

## What this directory is

**The complete material form of one intent**: declaration, collected
pieces, tools, materials, and products all live here. Copying this
directory = carrying one runnable whole (but **trust doesn't travel**
— approval records and hashes live in the ledger; the receiver must
approve it themselves).

- `intent.json` — the declarative content (scenario/steps/
  acceptance/tools). **Editing it is editing this intent**;
  resubmit for the change to take effect.
- `tools/` — this intent's tools; declared name Y maps to `Y.*`.
  Once approved the executor may call them directly;
  how they're used, desktop or browser, is not the engine's concern.
- `inputs/` — materials. **Substance, not pointers** (a pointer is
  an unpinned dependency — one external move and it fails). No human
  approval needed, but **how each is used must be written below**.
- `records/` — reserved ledger seat, engine territory (treat as
  read-only). Session journals live in the workspace-level
  `records/`; receipts and accounts live in the task ledger —
  self-certifying material must not be held by the party being
  certified.

## How to write the declaration

**The field textbook is `schema.md` in this directory** (engine-
owned, rewritten on provisioning) — every field's semantics, the E
grammar and verb table, and the character gates are all on that
sheet; the function view (I-E-R) and the sink-down law are in the
skill **intent-creation**. One-line version: input (trigger text +
`procedures` prelude materials) → steps (do-if-else pseudo-code,
recovery paths written as if-branches) → three-state return
(criteria pinned in acceptance) — the executor sees only I·E·R;
the scenario tag never enters the order.

## Conventions (**compile-time** rules — audience: sidecar and
humans, never the executor)

<!-- Since CASELAW 56 the executor sees only I·E·R — not one word of
     this file reaches it. So: constraints that must hold at
     execution time must be **compiled into steps/acceptance** or
     built into the tools' responses. Anything written here but not
     compiled in is dead text. -->


<!-- e.g.: one piece per directory; filename = piece title; PDF
     first, PNG second -->
(unsettled — write this out before mechanizing: an unclosed input
space makes any mechanical form a fake)
"""

# Registration-card template (§2u): **not the full text** — by write
# time the harness has already approved once, what's approved here is
# "taking effect", not "the content". Under auto mode, writing to
# disk may auto-allow, so this card is the whole pipeline's one human
# eyeball, can't be skipped or defaulted to approve; to see the
# content, open the directory yourself.
WS_REGISTER_MD = """\
# Registration: {name} ({kind})

workspace: {wdir}

What you approve is **taking effect**, not the content (the content
is in local files — open the directory above to read it). Approval
compiles it into the ledger: the executor runs from the ledger from
now on, so a later edit on disk changes nothing until you register
again.

## Declaration summary
Form: {form}
Scenario: {scenario}

## Pieces taking effect ({n})
{files}

## Not declared (will not take effect)
{extra}
"""

# sim self-test's package header (INTENT_SPEC §3b three walls, ruling: full-chain real test)
SIM_BANNER = """\
> **This is a sim self-test (validation task)**: verify the intent
> below can actually be followed. Three-wall discipline: ① touch only
> what is within this task's scope; ② never touch the user's real files
> — build fixtures for materials, keep products in scratch, clean up
> after; ③ no permission exemptions — a permission the test can't get,
> production shouldn't have. Settle with task_done: genuinely
> followable = ok; unclear/undoable = failed + where it got stuck.
> **What is under test is whether E and the criteria line up**: walk E
> line by line (substituting fixtures where materials are missing) and
> check the end state can be ruled three-state by the acceptance
> section.

"""

# ---------------------------------------------------------------------------
# skills (the third layer of the knowledge trilogy, INTENT_SPEC §3c):
# CLAUDE.md is the resident-instruction surface, byte-stable; heavy
# knowledge goes into a skill loaded on demand — keeps the protocol
# lean. Engine's own property, rewritten on every cast (CASELAW 28).

SKILL_TASK_DELIVERY_MD = """\
---
name: task-delivery
description: IntentOS task mechanics manual + debugging ladder — executor division of labor / surgery loop / sim / settlement law / cancel / time limits. Read when a [task N] arrives and you're unsure, a settlement is refused, or a delivery breakpoint needs tracing.
---

# Task delivery: mechanics and debugging (backup manual)

**A task represents one intent the user initiated** — the final
result reaches the user through the `task_done` settlement (the
ledger and the card stream both render it). For report-style intents
(queries/briefings), the answer goes into summary; settling IS
delivering. **Which face to answer on depends on where it came from
(law)**: work the engine delivered is answered on the engine face
(task_done / cards) — delivery means the user may not be at this
window; what the user types in conversation is answered in
conversation.

## Division of labor (§2m v9)

**Intent orders are not delivered to you** — when the user fires
from deck / IME, the order goes to the **x·solo executor**
(headless, stateless, one process per order, **parallel**: spawns on
demand, concurrent orders don't touch each other). **Protocol
brackets aren't delivered to you either** — each booklet has its own
resident seat (x·<booklet>); the Start key opens the bracket and
hosting happens there. You are the **maintenance seat**: executor
successes are none of your business; work only comes back to you
when an executor fails or the user isn't satisfied (the surgery
loop, below). Your own work (surgery / retry / consolidate / sim)
runs through your seat queue, one at a time.

## The four kinds of work you receive

1. **Surgery** (spec=surgery): after an executor failure the user
   approves the proposal card — the script carries the failure
   receipt, the user's note, and the residue map (telemetry-bus
   record). You: ① clear residue by the map (half-finished files,
   misplaced products); ② repair the asset only if needed — edit
   intent.json then `workspace_submit` (the next order runs the new
   version); ③ the `task_done` settlement is **the ONE ignition
   signal**: the system auto-replays the original order. One surgery
   one replay; a second failure goes back to the user; the intent is
   suspended for the duration. **Never re-trigger it yourself.**
2. **Retry** (retry): the user pressed retry on a settled order,
   with an optional note — you **fulfill directly on this seat**
   (ask in conversation when unsure; never re-deliver to the
   executor). Start by reading why the previous run fell short (the
   package carries the prior record) and name the root cause; then
   redo the work here. `task_done` is a **real settlement** like any
   other — your summary must carry that root cause. Do **not** edit
   the intent inside this order: on an ok settlement the engine
   offers the user a **consolidate** card, and folding the lesson
   into the declaration is that separate, human-approved order.
   Exempt from the time limit (it is a conversation).
3. **Consolidate** (consolidate): the user approved the offer above,
   or closed a booklet and approved its offer. The asset is
   **suspended** for the duration (triggers refused, nothing
   compiled — the library keeps serving the old version). Review the
   evidence, grill what should change, fold it into the declaration,
   `workspace_submit`, then `task_done`. Your re-registration raises
   the ordinary registration card; **the user's approval of that
   card is what revives the asset.**

## The life of one order (debugging background)

Trigger (issuer=user; a new trigger is refused while the same intent
is in flight) → if the intent declares `procedures`, the engine runs
the prelude before delivery and stitches the materials into the
package's Materials section; **a crashed prelude reports to the
human and the order is not delivered** (the intent is innocent — not
suspended, no surgery) → delivery to the executor (the package
carries the full steps text — **steps are the executor's only source
of instruction**, which is why they must be pure mechanics) →
settlement ok (completion card notifies the user) / failed (proposal
card waits for approve) / timeout (ruled failed).

## Common settlement refusals (engine refusal texts, meanings)

- "only running (delivered) rings settle": duplicate settlement, or
  the ring was already ruled failed by the time-limit law — tell the
  user the result directly, do not task_done again.
- "belongs to X, not you": the ring isn't yours (usually the
  executor's) — don't settle another seat's books.
- A reply carrying "chain cancelled: settlement received, chain
  stops": normal wind-down, no further rings will arrive.
- Retry: `task_done` settles for real (no acceptance round,
  reshaped 2026-08-25); if the user is still unsatisfied they press
  retry again and a fresh bracket opens with your record attached.

## Time limits and cancellation

- running with no settlement for 15 minutes → the engine rules it
  failed (your surgery/diagnosis orders follow the same law; queue
  time doesn't count; **retry brackets and protocol brackets are
  both exempt** — how long a multi-round interaction takes is the
  human's business).
- A chain you initiated gets cancelled → you receive one
  `[chain N] cancelled` line; close any related waits.

## Protocol state (brackets — not on your seat, but know the works)

- Brackets live in **each booklet's own resident seat** (x·<booklet>,
  one household per protocol, home and memory persisting across
  sessions), and booklets run **in parallel**. You never receive a
  bracket's start/step envelopes.
- The entrance is the deck: the **Start key** opens the bracket
  (opening runs nothing on its own), member keys drop step
  envelopes, and the **Shutdown key** is how the human closes it —
  the engine settles directly; the host seat doesn't (and can't)
  task_done. Pressing a member key on a closed booklet is refused
  with a pointer to press Start first.
- Your entire intersection with protocols is **compile-time**: open
  the ticket, write the skill, submit the whole booklet with
  workspace_submit, revise and resubmit — hosting belongs to the
  resident seat.

## Debugging ladder (in order)

1. MCP says the engine is offline → runtime/engine.json missing, the
   engine isn't up.
2. **The refusal is the answer**: the engine's rejection text is the
   next instruction — follow it first.
3. The delivered artifact: `runtime/tasks/<N>/package.md`.
4. The journal: `records/<seat>/<session>/events.jsonl` — one line
   per event; deliver / claim / timeout / cancelled / firing-failed
   are all there.
5. `state.db` is the engine's single-writer truth layer — **you
   never read or write it** (not even to debug): for the books, use
   records/ above and the tool face (intent_get / intent_catalog).
"""

SKILL_INTENT_CREATION_MD = """\
---
name: intent-creation
description: Intent authoring guide (sidecar duty) — intent fields and lifecycle (I-E-R: steps = the E command sequence, acceptance = the R criteria) / the intent-vs-memory split / the sink-down law (mechanical lines pressed into tools) / the toolkit-first law / how protocols relate to their material. Read before creating an intent, opening a protocol, or revising an asset.
---

# Intent authoring guide (sidecar duty)

## Intent (the smallest unit of capability)

- **Initiative belongs to the human**: in conversation you only
  point out "this could be saved as an intent"; submit only when the
  user explicitly asks.
- **The intent-vs-memory knife**: before writing, ask one question —
  could any agent with zero session context, using only the tools
  already in the environment, complete this by following the text?
  Yes → intent (steps/acceptance/scenario/procedures/tools); no
  (the user's preferences, this machine's paths and quirks, your own
  experience) → your memory. An intent is an **execution artifact**
  handed to a stranger seat — private context mixed in warps the
  run; know-how scattered in memory can't ride along in retrieval.
  Unsure → default to memory.
- **I-E-R = the intent IS a function** (user ruling 2026-08-16,
  replacing the v15 split): the executor is **an interpreter, not a
  planner**. Design the intent as a function: **input** = the
  trigger text + context collected by the physical layer;
  **methods** = tools/toolkit; **function body** = `steps`,
  do-if-else pseudo-code (grammar machine-checked at registration:
  verb ∈ the nine-verb table, content budgeted per verb, binary
  branches, jumps forward only — sick lines are named one by one,
  fix accordingly); **return** = three states, criteria pinned in
  `acceptance` (ok:/ok_issue:/failed:, omissible for defaults) —
  acceptance standards are fixed at compile time, the executor never
  invents its own. `judge` is the only semantically open verb —
  the fewer, the cheaper. **The word list + budgets govern only the
  E section**: scenario is a retrieval axis for your refining,
  behind its own gate, invisible to the executor — the
  order carries only I·E·R. **Recovery paths are wrapped inside E**:
  catch foreseeable failures with if-branches (that IS failback).
  Upfront context collection is the engine's **physical layer**: the
  optional `procedures` field in intent.json references the built-in
  library by name — the declaration is yours, the library is not
  yours to write; if it blows up, a human is told (next section).
- **The two sink-down questions (layering law at implement time)**:
  what can be mechanical becomes a tool (the executor calls it in
  one hop, details live in the tool's response); what can't be
  mechanical but can be a command goes into steps (the interpreter
  runs it straight); only what can't even be a command stays a
  judgment line (`judge`) — the more judgment, the more expensive the
  intent. **ok_issue's issue backflow is next round's sink-down
  list**: the line that keeps causing friction is the line to press
  one level down.
- **steps' audience is the executor, not you** (§2m v9): the
  executor is a stateless headless seat — a fresh process per order
  that sees only the package (full steps text rides along) and
  what's on disk — so steps must be pure "call this, then call
  that" mechanics that work with zero context; narrative,
  preference, and reasoning don't belong. Fields and form follow the
  workspace's **schema.md** (lands at provisioning, engine-owned,
  not restated here). **The cheap-reproduction law**: steps hold
  conclusions, not process; wherever a run burns serious tokens,
  write a one-line direct-fetch pointer into intent.json and
  re-register while you're at it.
- **Write gates**: scenario is a single-word gate, steps have a
  character cap; over the cap the **whole submission refuses** (the
  refusal names the cap). Not harassment: scenario is the
  aggregation axis, steps hold only what following requires — over
  the cap, compress first; the machine-local details and preferences
  squeezed out go into your memory.
- Lifecycle: draft → human approves the template, it goes live →
  revised while in use (edit intent.json — **`intent_get` the
  current asset before editing**) → situational lessons fold into
  steps as conditional clauses; no separate precedent pile. **Live =
  on the deck**: a standalone intent's key auto-joins the "IntentOS
  · Intents" group; a registered protocol gets its own key group
  (four fixed keys + members + Status/Step dials); the Stream Deck
  app must restart once before the sidebar shows it — you only
  relay this, compiling is the engine's. validate (sim) is optional
  and human-triggered; three walls: touch only your remit / never
  the user's real files (build fixtures, products into scratch,
  clean up after) / no permission exemptions. **The three walls
  cover creation time too** (live fire 2026-08-15: a trial
  recording landed straight in the user's directory — deleted after
  the test, but a mid-run crash would have left garbage): every
  draft-phase trial product goes into scratch; the real target
  directory is written only on a real trigger.
- An intent is a living asset that steadies with use: a good
  scenario word decides whether it can ever be retrieved.
- **Before writing any script, glance at the shared toolkit** (the
  workspace's `toolkit/`, read-write for you). Three live rounds,
  the same hole three times: a window-layout script sat there with
  four battle-tested hard constraints sealed in its comment header
  (non-ASCII paths mojibake through the command line / Chrome's
  normal-window minimum width is 516px / image and PDF paths share
  the layout routine / parameters go via manifest, not argv), and
  every time it was rewritten from zero, every pit re-stepped.
  **The value of an existing script is the blood in its comments**,
  not its line count. Conversely: a new script that would serve
  another intent goes into toolkit; one serving only this intent
  goes into its own `tools/`.

## The physical layer (procedures — the library isn't yours, the declaration is)

Upfront context collection (screenshots of the monitor under the
mouse and the like) is the control protocol's **physical layer**:
an engine-built-in procedure library that **intent declarations
reference by name** — intent.json's optional `procedures` field
takes an array of names, matched against the word list at
registration (a name outside the list refuses the whole submission,
the refusal carries the available list). On trigger the engine
**runs the prelude before delivering**; the materials are stitched
into the order's Materials section, and E starts from "the
materials are already in the package" without restating the
extraction. **The library body is not yours to write** — the line
is drawn at the wall: procedures are wall-less code run directly by
an engine subprocess, beyond the permission gate; tools are called
by the executor inside the wall. So you may write tools and declare
procedures, but never write a procedure; if one blows up, the
engine tells the human and the order is not delivered — the intent
is innocent, not suspended, and not yours to fix. Member
declarations (inside a booklet) support procedures the same way —
the member's key runs its prelude before the step envelope.

## Protocol (the intent's peer species — a material relationship)

**The split is state** (user ruling 2026-08-17, replacing the
"single action vs multi-round" rule of thumb): intent = stateless
one-way order, protocol = stateful workflow — the test: **does the
next step need to remember what the last one chose**. A protocol is
**a piece of stateful context used as one object**: opening the
booklet = construction (Start key; member declarations pre-warmed
with the booklet = the method table loading), closing = destruction
(Shutdown key — the human closes; an object doesn't wind itself
down); every resource and action inside the bracket is booked under
that one task, and non-members can't get in — the encapsulation is
enforced mechanically by the engine, not by good behavior.
**Joining the booklet locks the member** (user ruling 2026-08-17):
member use is stateful and goes through the booklet — pressing a
member key on a closed booklet is refused with a pointer to Start;
to keep a stateless single-shot life, stay out of the booklet (or
found a separate standalone intent with the same tools).
No need to wait for intents to pile up — initiative is the human's:
the user nods, you research and fill the booklet in.

A protocol = one skill (you draft it, the human approves the full
text) + a member intent roster, in exactly **one multi-round
bracket type**, hosted by **its own resident seat** (x·<booklet>,
one household per booklet; not you — you only compile):

- **The bracket regime**: the user presses this booklet's Start key
  to open a bracket (opening runs nothing on its own); the resident
  seat reads the skill and hosts the rounds; inside the bracket,
  member keys drop step envelopes and never open new orders; the
  Shutdown key is the human's close, settled directly by the
  engine. **Booklets run in parallel across booklets**; one bracket
  at a time within a booklet. Good for: practice companions,
  scripts, continuous work sessions, research that forms a set.
  Seat count 3–10 counting the two system slots (ledger names
  ·open/·wrap; 1–8 real members; an empty roster doesn't stand).
- **Writing = declare only the aggregation** (user ruling
  2026-08-16): the skill says **how these members chain together**
  (order, conditions, phrasing) and **never restates any member's
  details** — each member's E and acceptance are pre-warmed into
  the package from the ledger at bracket open (the member-roster
  section); you get a warm open with zero intent_get round trips.
  Copying details into the skill = two truths; one ledger edit and
  the skill is stale. A member joining the booklet becomes material
  (search surfaces the booklet through the pointer aggregation, the
  catalog still lists it; **standalone triggering is locked** —
  joining means stateful, everything goes through the booklet).
- **The booklet is the compile unit** (user ruling 2026-08-16 late
  night: no separate member registration): member declarations
  **travel with the booklet**, never through intent_submit —
  `intent_submit(name, kind="protocol")` opens the ticket and
  founds the workspace → one directory per member under
  `members/<name>/`, each holding intent.json (the **same schema
  table** as a standalone intent, steps required) + tools/ → the
  `members` list in `protocol.json` → `skill.md` holds only the
  aggregation → `workspace_submit(name)` **submits the whole
  booklet through one gate, compiled atomically**. One bad member
  refuses the whole booklet (problems named one by one) — **all or
  nothing, no singles**, exactly matching stateful situational
  interaction. Changing any member = editing that in-booklet
  declaration and resubmitting the whole booklet; a member that
  wants a stateless single-shot life gets a new name as a
  standalone intent outside.
- **Local tools live in `<workspace>/toolkit/`** (shared seat
  ground, read-only for executors): generalizable tools go there
  from the drafting phase — permission rules bind to the path, so
  content changes ripple nothing; scratch holds drafts only.

## Where your duty ends

The system has exactly two species: intent (straight-line execution
artifact, runs on the executor) and protocol (multi-round booklet,
hosted by its own resident seat). Your role: compile intents well,
sink mechanical segments into tools, land generalizable tools in
toolkit, and when one scenario word piles into a family, remind the
user that family could become a protocol — the call is always
theirs.
"""


# The dark_knowledge contract retired along with the PRODUCT2 twelfth
# draft (relational knowledge now lives in an intent entry's scenario
# and evidence); if the original text is needed for reference, see
# the old repo's defaults.DARK_HEADER.
