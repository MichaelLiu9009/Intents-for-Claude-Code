"""Store -- the truth layer of the intent plane (INTENT_SPEC v4b §1/§2/§7).

The table is truth; files are all render-consumption artifacts. Three
guardrails:
  1. DB single writer = the daemon process (a single in-process lock
     serializes writes; agents and pages never touch the db file
     directly, always via the engine's query surface).
  2. Boot-time reconciliation: rows <-> disk cross-verify; missing
     references are reported loudly, never silently.
  3. Schema only adds, never alters; user_version increments,
     additive migration.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA_VERSION = 20

# Flow names (M12: chains renamed to flow, typed node graph). qual·new
# = intent-creation QA (human-approval gate); qual·rework = rework QA for
# firing-failed (n0 diagnosis -> n1 sim) -- edge-entry only (on_fail of
# deliver:X), chains don't open chains.
FLOW_QUAL_NEW = "qual·new"
FLOW_QUAL_REWORK = "qual·rework"
FLOW_WS_QUAL = "qual·register"   # §2u: the sole human gate for folder-based
                             # submission (registration = compilation)
FLOW_RETIRE = "qual·retire"    # live-fire precedent 2026-08-23: retirement
                             # is a human ruling -- agent proposes, human
                             # approval takes effect (previously no verb
                             # existed, only engine-halting manual surgery)

_DDL = """
CREATE TABLE IF NOT EXISTS intents(
  name TEXT PRIMARY KEY,
  title TEXT,
  scenario TEXT,
  absorbed_into TEXT,
  steps TEXT,
  fires INTEGER NOT NULL DEFAULT 1,
  owner TEXT NOT NULL,
  status TEXT NOT NULL,
  rev INTEGER NOT NULL DEFAULT 1,
  created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS intent_steps(
  intent TEXT, seq INTEGER,
  ref TEXT NOT NULL,
  PRIMARY KEY (intent, seq)
);
CREATE TABLE IF NOT EXISTS intent_tools(
  intent TEXT, tool TEXT, ver TEXT
);
-- caveats: fossil (retired 2026-08-25, no writer/reader; additive law)
CREATE TABLE IF NOT EXISTS caveats(
  id INTEGER PRIMARY KEY,
  intent TEXT, text TEXT, origin TEXT, t TEXT
);
CREATE TABLE IF NOT EXISTS chain_specs(
  name TEXT PRIMARY KEY,
  head TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0,
  consequence TEXT
);
CREATE TABLE IF NOT EXISTS chain_spec_steps(
  spec TEXT, seq INTEGER,
  assignee TEXT NOT NULL,
  kind TEXT NOT NULL,
  ref TEXT,
  gate TEXT,
  PRIMARY KEY (spec, seq)
);
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY,
  chain_id INTEGER, seq INTEGER,
  spec TEXT,
  intent TEXT,
  payload TEXT,
  issuer TEXT NOT NULL,
  executor TEXT,
  status TEXT NOT NULL,
  gate TEXT,
  priority INTEGER NOT NULL DEFAULT 0,
  rev INTEGER NOT NULL DEFAULT 1,
  created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS records(
  id INTEGER PRIMARY KEY,
  task_id INTEGER, intent TEXT,
  is_test INTEGER NOT NULL,
  outcome TEXT, duration_s REAL, t TEXT
);
"""

# v2 (2026-08-10 cancel ruling): chain-level flag table -- the unit of
# cancel is the chain, not the ring; an accepted ring does not roll
# back, advance halts the chain by checking the flag.
_DDL_V2 = """
CREATE TABLE IF NOT EXISTS chain_flags(
  chain_id INTEGER PRIMARY KEY,
  cancelled INTEGER NOT NULL DEFAULT 0,
  actor TEXT, t TEXT
);
"""

# v3 (2026-08-10 retry ruling): origin = the previous task this ring
# points to (retry rides along the chain; mode executors have no
# persistent context, the last attempt relies entirely on this pointer).
_DDL_V3 = """
ALTER TABLE tasks ADD COLUMN origin INTEGER;
"""

# v4 (2026-08-11 key-binding law v3): soft-deck slot -> intent. Slots
# are a scarce resource, given their own table; no keycode stored
# (same table once the physical board B7 is wired in).
_DDL_V4 = """
CREATE TABLE IF NOT EXISTS bindings(
  slot INTEGER PRIMARY KEY,
  intent TEXT NOT NULL,
  t TEXT
);
"""

# v5 (2026-08-11 provision plane): class (the first-class retrieval
# index, mandatory when creating an intent), scope (creator mode,
# engine-stamped; editor-exclusive), migrated_to (a compile-time
# migration trace pointer -- redirect is not relocation), last_touched
# + use_score (raw material for the container law / scoring law --
# kept separate from updated_at: updated is a content change, touched
# is usage). last_touched backfills from updated_at so old rows aren't
# left bare.
_DDL_V5 = """
ALTER TABLE intents ADD COLUMN class TEXT NOT NULL DEFAULT '未分类';
ALTER TABLE intents ADD COLUMN scope TEXT NOT NULL DEFAULT 'sidecar';
ALTER TABLE intents ADD COLUMN migrated_to TEXT;
ALTER TABLE intents ADD COLUMN last_touched TEXT;
ALTER TABLE intents ADD COLUMN use_score REAL NOT NULL DEFAULT 0;
UPDATE intents SET last_touched=updated_at;
"""

# v6 (2026-08-11 M12 flow graph): chain -> graph. Node's five
# attributes -- accounting (real|test, step-level of the accounting
# law), template (the delivery-rendering template name, replacing the
# old if-else polymorphism keyed on spec name), effect (the
# constitutional verb, "ok|fail:<verb>", stamped before routing after
# settle), on_ok / on_fail (binary edges: "next"|"end"|"<flow>:<seq>",
# can loop back or cross chains -- routing is a table lookup, never an
# evaluation). Defaults keep old rows compatible: next past the tail
# means end; old specs get reseeded/recompiled on boot regardless.
_DDL_V6 = """
ALTER TABLE chain_spec_steps ADD COLUMN accounting TEXT NOT NULL DEFAULT 'real';
ALTER TABLE chain_spec_steps ADD COLUMN template TEXT;
ALTER TABLE chain_spec_steps ADD COLUMN effect TEXT;
ALTER TABLE chain_spec_steps ADD COLUMN on_ok TEXT NOT NULL DEFAULT 'next';
ALTER TABLE chain_spec_steps ADD COLUMN on_fail TEXT NOT NULL DEFAULT 'end';
"""

# v7 (2026-08-12 M15 join key, blueprint docs/M15-JOINKEY.md): the CLI
# transcript natively already carries token counts / full tool args /
# refusal types / causal tree -- none of that is rebuilt here. What's
# built is only the half the CLI can never give: task / intent /
# issuer / accounting.
#   delivered_at -- window start (the **delivery** instant, not
#                   enqueue; true execution duration excludes queueing)
#   host_session -- the host CLI's sessionId, the other half of the
#                   transcript-slicing coordinate
#   events       -- the engine's own facts; three indexes for three
#                   query shapes (time window / kind + time window /
#                   task); querying by time window + kind is the sole
#                   use case ruled by the user
#   boundary     -- fossil (retired 2026-08-24 with the permission-
#                   surface consolidation: the allow side belongs to
#                   the harness + PERM_ALLOW ledger, so the prose
#                   declaration column lost its consumer). Column kept
#                   under the additive law; no writer remains.
_DDL_V7 = """
ALTER TABLE tasks ADD COLUMN delivered_at TEXT;
ALTER TABLE tasks ADD COLUMN host_session TEXT;
ALTER TABLE intents ADD COLUMN boundary TEXT;
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY,
  t TEXT NOT NULL,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  task_id INTEGER, intent TEXT,
  issuer TEXT, session TEXT,
  fields TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_t    ON events(t);
CREATE INDEX IF NOT EXISTS ix_events_kind ON events(kind, name, t);
CREATE INDEX IF NOT EXISTS ix_events_task ON events(task_id);
"""

# v8 (2026-08-12 M16 materialization, blueprint docs/M16-MATERIALIZE.md):
# home of the compiled artifact -- **engine territory, never visible to
# the agent** (dual-column isolation: the declaration in
# intents.boundary belongs to the agent, the rules in effect here
# belong to the engine). No MCP verb reads this table, no render
# surface carries it; a db-level deny on state.db is the second line
# of defense. One row per intent, overwritten on recompile; provision
# reads rows by mode, validates, and takes the union.
_DDL_V8 = """
CREATE TABLE IF NOT EXISTS boundary_compiled(
  intent TEXT PRIMARY KEY,
  mode TEXT NOT NULL,
  rules TEXT NOT NULL,
  evidence TEXT,
  t TEXT NOT NULL
);
"""

# v9 (2026-08-12 M16 §5e creation-session isolation): birth stamping --
# an intent does not fall into the alert category within the session
# it was created in (approval during creation is part of the creation
# flow, not usage evidence). Determination = born_session != current
# session, compared at trigger time, zero rewrite steps (rewriting at
# close shares the same ailment as the close event: a kill permanently
# jams it). Old rows with NULL = born prehistoric, naturally ready.
# Engine-stamped, not on the MCP whitelist.
_DDL_V9 = """
ALTER TABLE intents ADD COLUMN born_session TEXT;
"""

# v10 (2026-08-13 M20 §2c procedure plane + §1 consolidate ring):
#   procedures -- the row-side of the centralized repo (the artifact's
#     ecological niche): hash = the approved snapshot (code on disk
#     not matching it = manual edit, rejected at firing time),
#     staged_hash = the submitted snapshot pending approval; only
#     human approval (effect provision_procedure) makes it live.
#   intent_steps.params -- a fossil column (no longer written once
#     §2u dropped param)
#   chain_spec_steps.params -- rides along as a compile artifact (node's
#     five attributes +1)
#   intents.mute_alert -- per-intent mute for the token alert (don't
#     remind)
_DDL_V10 = """
CREATE TABLE IF NOT EXISTS procedures(
  name TEXT PRIMARY KEY,
  desc TEXT NOT NULL DEFAULT '',
  hash TEXT,
  staged_hash TEXT,
  rev INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'draft',
  born_session TEXT,
  created_at TEXT, updated_at TEXT
);
ALTER TABLE intent_steps ADD COLUMN params TEXT;
ALTER TABLE chain_spec_steps ADD COLUMN params TEXT;
ALTER TABLE intents ADD COLUMN mute_alert INTEGER NOT NULL DEFAULT 0;
"""

# v11 (2026-08-13 M20 §2/§2d protocol plane): protocols -- the table is
# truth, the skill is a rendered artifact (same hash cross-verification
# law as procedure); subtype forks execution semantics (interactive =
# bracketed / sidecar self-executes; executor = the graduated form of
# scenario aggregation, member delivery redirects to a headless
# execution seat); members = a JSON roster.
_DDL_V11 = """
CREATE TABLE IF NOT EXISTS protocols(
  name TEXT PRIMARY KEY,
  subtype TEXT NOT NULL DEFAULT 'interactive',
  boundary TEXT NOT NULL DEFAULT '',
  class TEXT NOT NULL DEFAULT '未分类',
  scenario TEXT NOT NULL DEFAULT '',
  skill_hash TEXT,
  staged_hash TEXT,
  members TEXT NOT NULL DEFAULT '[]',
  rev INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'draft',
  born_session TEXT,
  created_at TEXT, updated_at TEXT
);
"""

# v12 (2026-08-13 user ruling): an executor protocol is **pure
# execution** (the intent is just a parameter) -- the execution seat's
# model is set independently, defaulting to sonnet (judgment is already
# done at compile time by the human-approved skill; runtime doesn't
# need an expensive model).
_DDL_V12 = """
ALTER TABLE protocols ADD COLUMN model TEXT NOT NULL DEFAULT 'sonnet';
"""

# v13 (§2g surgery ring 2026-08-13): if the staged skill is unapproved
# when surgery reconciles accounts -> the replay ticket hangs on the
# protocol row (released the moment approval lands, survives engine
# restart); non-empty = one state of the protocol being suspended.
_DDL_V13 = """
ALTER TABLE protocols ADD COLUMN parked_replay TEXT;
"""

# v14 (§2k params table 2026-08-14; **§2u has fully retired
# 2026-08-15**): both columns kept but unused -- the old rule of
# **never renaming DB columns** (fossil columns, zero migration risk).
# payload is now just the raw natural-language text typed at trigger
# time, no parsed artifact.
_DDL_V14 = """
ALTER TABLE intents ADD COLUMN params TEXT NOT NULL DEFAULT '[]';
ALTER TABLE tasks ADD COLUMN params TEXT;
"""

# v15 (§2m splits the v14 ruling): steps = a pure execution-call
# sequence (<=400 chars), instructions = execution notes / preference
# constraints (<=200 chars) -- filled in separately with separate
# length caps; the execution-seat package renders them as two
# distinct sections.
_DDL_V15 = """
ALTER TABLE intents ADD COLUMN instructions TEXT NOT NULL DEFAULT '';
"""

# v16 (user ruling 2026-08-16 night: procedure belongs to the physical
# layer, chain hangs off the key slot): bindings grows a chain column
# -- **the slot, not the intent, is the host of the procedure chain**.
# Key press -> run chain (physical layer gathers context) -> start
# task. A JSON array stores built-in procedure names; empty = start
# the task directly. The intent-side chain declaration retires in the
# same ruling (the intent_steps table goes idle from here on, read-
# only, never written again).
_DDL_V16 = """
ALTER TABLE bindings ADD COLUMN chain TEXT NOT NULL DEFAULT '[]';
"""

# v17 (user ruling 2026-08-16 late night: register by declaring
# separately -- protocol is a compilation unit): intents grows a proto
# column = structured membership. Members are declared with the roster
# (members/<name>/), the whole roster compiles atomically through one
# gate -- **take it all or nothing, no singletons** -- matching exactly
# the stateful scenario interaction. Once rostered, lookup switches
# from "scan protocols.members" to "read the column": a declared fact,
# not a runtime patch. executor intents (proto IS NULL) are unchanged.
# The data backfill is done in Store.__init__ (idempotent, no JSON
# parsing in SQL).
_DDL_V17 = """
ALTER TABLE intents ADD COLUMN proto TEXT;
"""

# v18 (user ruling 2026-08-23: procedure rewired -- hangs off the
# intent, not the key): procedures = an optional list of preludes
# declared by the intent (a JSON array referencing the engine's
# built-in library by name; matched against the wordlist at
# registration, the whole submission rejected if any name is outside
# it). At trigger time the engine runs the preludes first, material
# rides along with the ticket; if a prelude blows up, it's reported to
# the human and the ticket is not submitted. bindings.chain (v16's
# host) fossilizes here -- column name kept, both reads and writes
# stop.
_DDL_V18 = """
ALTER TABLE intents ADD COLUMN procedures TEXT NOT NULL DEFAULT '[]';
"""

# v19 (user ruling 2026-08-24: ·open/·wrap made real -- open/close are two
# system-native steps, the engine auto-delivers them when opening/
# closing a roster; the roster declares their content): prep = the
# opening/setup step, wrapup = the closing step (E prose, each
# <=PROTO_HOOK_MAX chars; empty = system default).
_DDL_V19 = """
ALTER TABLE protocols ADD COLUMN prep TEXT NOT NULL DEFAULT '';
ALTER TABLE protocols ADD COLUMN wrapup TEXT NOT NULL DEFAULT '';
"""

# v20 (release Latinization, user ruling 2026-08-26): the CJK runtime
# identifiers became Latin before the public flip (qual·初生/回炉/注册/
# 退役 -> qual·new/rework/register/retire, 手术 -> surgery, class
# default 未分类 -> unfiled). Live rows are renamed; the journal is
# read-only history and keeps old names (M12 precedent); stale
# chain_specs rows under old names are deleted -- boot reseeds the
# new ones. Frozen earlier migrations still say 未分类; the column
# DEFAULT is never relied on (every insert passes cls explicitly).
_DDL_V20 = """
UPDATE tasks SET spec='qual·new' WHERE spec='qual·初生';
UPDATE tasks SET spec='qual·rework' WHERE spec='qual·回炉';
UPDATE tasks SET spec='qual·register' WHERE spec='qual·注册';
UPDATE tasks SET spec='qual·retire' WHERE spec='qual·退役';
UPDATE tasks SET spec='surgery' WHERE spec='手术';
DELETE FROM chain_spec_steps WHERE spec IN
  ('qual·初生','qual·回炉','qual·注册','qual·退役','手术');
DELETE FROM chain_specs WHERE name IN
  ('qual·初生','qual·回炉','qual·注册','qual·退役','手术');
UPDATE intents SET class='unfiled' WHERE class='未分类';
"""

_MIGRATIONS = {2: _DDL_V2, 3: _DDL_V3, 4: _DDL_V4, 5: _DDL_V5,
               6: _DDL_V6, 7: _DDL_V7, 8: _DDL_V8, 9: _DDL_V9,
               10: _DDL_V10, 11: _DDL_V11, 12: _DDL_V12, 13: _DDL_V13,
               14: _DDL_V14, 15: _DDL_V15, 16: _DDL_V16, 17: _DDL_V17,
               18: _DDL_V18, 19: _DDL_V19, 20: _DDL_V20}


INTENT_STATUSES = ("draft", "provisioned", "retired")
TASK_STATUSES = ("queued", "running", "gated", "done", "failed",
                 "cancelled")


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _utc_iso(local: str | None, tail: str) -> str | None:
    """Local naive string -> UTC ISO for the transcript (M15 timezone
    gate).

    **Verified 2026-08-12**: timestamps in the db and journal are local
    naive strings (`2026-08-12 14:23:01`), while the host transcript's
    `timestamp` is UTC ISO with milliseconds
    (`2026-07-29T20:35:13.720Z`) -- a direct string comparison slices
    out an **empty set**, and silently at that (the hardest kind to
    debug). So the coordinate must be converted here before it goes
    out the door.

    `tail` keeps second-precision endpoints **inclusive** at millisecond
    precision: the start gets `.000Z` appended, the end gets `.999Z`.
    During the DST-transition hour, mktime guesses with isdst=-1,
    which is ambiguous -- accepted: twice a year, off by at most an
    hour, and the slice should carry slack anyway.
    """
    if not local:
        return None
    try:
        ep = time.mktime(time.strptime(local, "%Y-%m-%d %H:%M:%S"))
    except (ValueError, OverflowError):
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ep)) + tail


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # cached_statements=0 (M13 precedent): a single connection is
        # read across threads (pump / WS / HTTP / test threads).
        # SQLite itself is serialized (threadsafety=3) and fine with
        # that, but pysqlite's statement cache lets two threads share
        # one compiled statement for identical SQL -- symptom =
        # sporadic IndexError during fetch. Disabling the cache
        # eliminates it root and branch; at our query rate, the
        # recompile overhead is negligible.
        self._db = sqlite3.connect(str(path), check_same_thread=False,
                                   cached_statements=0)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()          # in-process single writer
        try:
            with self._lock, self._db:
                self._db.execute("PRAGMA journal_mode=WAL")
                v = self._db.execute("PRAGMA user_version").fetchone()[0]
                if v == 0:
                    self._db.executescript(_DDL)
                    self._db.execute("PRAGMA user_version=1")
                    v = 1
                if 0 < v < SCHEMA_VERSION:
                    # additive migration (guardrail 3: only add, never
                    # alter; patched version by version)
                    #
                    # Stamp **inside** the loop (audit 2026-08-25):
                    # executescript auto-commits its DDL, so a step
                    # that lands is durable immediately. Stamping
                    # once after the whole chain meant an interrupt
                    # anywhere in it (Ctrl+C, power loss) left the
                    # applied ALTERs under a stale version, and the
                    # next boot replayed them into "duplicate column
                    # name" with no repair path. A fresh db walks the
                    # whole V2..V19 chain on its first boot, so this
                    # is the common path, not an upgrade corner.
                    for ver in range(v + 1, SCHEMA_VERSION + 1):
                        if ver == 20:
                            # v20 renames are cosmetic UPDATEs over
                            # columns some pre-discipline dbs lack
                            # (audit 2026-08-26: the 2026-08-11
                            # fixture has no tasks.spec) — each
                            # statement is independent, best-effort;
                            # a rename must never brick a boot.
                            for stmt in _DDL_V20.split(";"):
                                if stmt.strip():
                                    try:
                                        self._db.execute(stmt)
                                    except sqlite3.OperationalError:
                                        pass
                            self._db.commit()
                        else:
                            self._db.executescript(_MIGRATIONS[ver])
                        self._db.execute(f"PRAGMA user_version={ver}")
                elif v > SCHEMA_VERSION:
                    raise RuntimeError(
                        f"db user_version {v} > engine {SCHEMA_VERSION} — "
                        "newer db, older engine: refusing to write "
                        "downgraded")
        except BaseException:
            self._db.close()               # close even on refusal -- never leave a locked-out db
            raise
        self._backfill_proto()

    def _backfill_proto(self) -> None:
        """v17 backfill (idempotent, runs every time the db opens):
        stamps intents.proto from the names in protocols.members. Old
        dbs (where members once registered independently) self-heal;
        new dbs already carry the stamp from proto_compile_unit, so
        this is a no-op there."""
        try:
            with self._lock, self._db:
                for r in self._db.execute(
                        "SELECT name, members FROM protocols"):
                    try:
                        mem = list(json.loads(r["members"] or "[]"))
                    except (ValueError, TypeError):
                        continue
                    for m in mem:
                        self._db.execute(
                            "UPDATE intents SET proto=? WHERE name=? "
                            "AND (proto IS NULL OR proto='')",
                            (r["name"], m))
        except sqlite3.Error:
            pass          # backfill failure never blocks boot; reconcile flags the sick rows

    def close(self) -> None:
        try:
            self._db.close()
        except Exception:
            pass

    def case_clash(self, name: str) -> str | None:
        """A different-cased twin of this name on either shelf (audit
        2026-08-26): NTFS folds case, so two case-variant assets
        collide onto ONE workspace directory and the later submit
        silently overwrites the earlier asset's intent.json. ASCII
        lower() is enough — case is an ASCII phenomenon here; CJK
        names have no case to collide on."""
        for tbl in ("intents", "protocols"):
            r = self._db.execute(
                f"SELECT name FROM {tbl} WHERE lower(name)=lower(?) "
                f"AND name != ?", (name, name)).fetchone()
            if r:
                return r["name"]
        return None

    # ---- intents ---------------------------------------------------------

    def intent_create(self, name: str, *, title: str = "", scenario: str = "",
                      steps: str = "", instructions: str = "",
                      fires: int = 1,
                      owner: str = "sidecar",
                      cls: str = "unfiled", scope: str | None = None,
                      born: str | None = None,
                      step_refs: list[str] | None = None,
                      tools: list[tuple[str, str]] | None = None,
                      params: list[str] | None = None) -> None:
        """cls kept as a dead-default param (class retired
        2026-08-25, column is a fossil); scope defaults to owner
        (the creator mode, engine-stamped -- an agent's self-report
        doesn't count). last_touched is set immediately: creation
        counts as a touch too (recency material for the container
        law). v15: steps = a pure call sequence, instructions =
        execution notes / preference constraints, each with its own
        length cap."""
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO intents(name,title,scenario,steps,"
                "instructions,fires,owner,"
                "status,rev,class,scope,born_session,params,last_touched,"
                "created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,'draft',1,?,?,?,?,?,?,?)",
                (name, title, scenario, steps, instructions,
                 int(fires), owner,
                 cls or "unfiled", scope or owner, born,
                 json.dumps(params or [], ensure_ascii=False),
                 _now(), _now(), _now()))
            self._put_children(name, step_refs, tools)

    def intent_revise(self, name: str, **fields) -> int:
        """Updates the row, rev++ (the vehicle for task-revision
        retry). Returns the new rev. scope/migrated_to are writable
        here (the store has the capability), but the MCP revision
        channel never allows it through -- whitelist layering: the
        store can do it, the verb keeps it locked down
        (editor-exclusive). Any content revision counts as a touch
        (recent = last_touched, any event)."""
        step_refs = fields.pop("step_refs", None)
        tools = fields.pop("tools", None)
        cols = {k: v for k, v in fields.items()
                if k in ("title", "scenario", "absorbed_into", "steps",
                         "instructions",
                         "fires", "status", "owner", "class", "scope",
                         "migrated_to", "boundary", "mute_alert",
                         "params", "proto", "procedures")}
        with self._lock, self._db:
            if cols:
                sets = ", ".join(f"{k}=?" for k in cols)
                self._db.execute(
                    f"UPDATE intents SET {sets}, rev=rev+1, updated_at=?, "
                    "last_touched=? WHERE name=?",
                    (*cols.values(), _now(), _now(), name))
            else:
                self._db.execute(
                    "UPDATE intents SET rev=rev+1, updated_at=?, "
                    "last_touched=? WHERE name=?", (_now(), _now(), name))
            self._put_children(name, step_refs, tools)
            row = self._db.execute(
                "SELECT rev FROM intents WHERE name=?", (name,)).fetchone()
            return row["rev"] if row else 0

    def _put_children(self, name, step_refs, tools) -> None:
        # caller already holds the lock; None = leave unchanged, [] =
        # clear. step_refs items = a name or (name, None) (§2u: chain
        # only stores names, params has retired)
        if step_refs is not None:
            self._db.execute("DELETE FROM intent_steps WHERE intent=?",
                             (name,))
            rows = [(r, None) if isinstance(r, str) else (r[0], r[1])
                    for r in step_refs]
            self._db.executemany(
                "INSERT INTO intent_steps(intent,seq,ref,params) "
                "VALUES(?,?,?,?)",
                [(name, i, ref, params)
                 for i, (ref, params) in enumerate(rows)])
        if tools is not None:
            self._db.execute("DELETE FROM intent_tools WHERE intent=?",
                             (name,))
            self._db.executemany(
                "INSERT INTO intent_tools(intent,tool,ver) VALUES(?,?,?)",
                [(name, t, v) for t, v in tools])

    def intent(self, name: str) -> dict | None:
        row = self._db.execute("SELECT * FROM intents WHERE name=?",
                               (name,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        chain = [(r["ref"], r["params"]) for r in self._db.execute(
            "SELECT ref,params FROM intent_steps WHERE intent=? "
            "ORDER BY seq", (name,))]
        d["step_refs"] = [ref for ref, _ in chain]
        d["chain"] = [{"proc": ref,
                       **({"with": json.loads(p)} if p else {})}
                      for ref, p in chain]
        d["tools"] = [(r["tool"], r["ver"]) for r in self._db.execute(
            "SELECT tool,ver FROM intent_tools WHERE intent=?", (name,))]
        return d

    def intents(self, owner: str | None = None,
                status: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM intents", []
        conds = []
        if owner is not None:
            conds.append("owner=?"); args.append(owner)
        if status is not None:
            conds.append("status=?"); args.append(status)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        return [dict(r) for r in self._db.execute(q + " ORDER BY name", args)]

    def migrate_owner(self, names: list[str], owner: str) -> None:
        """A mode migration = one transaction (INTENT_SPEC §1
        dimensionality-reduction checklist)."""
        with self._lock, self._db:
            self._db.executemany(
                "UPDATE intents SET owner=?, updated_at=? WHERE name=?",
                [(owner, _now(), n) for n in names])

    def count(self, owner: str, status: str = "provisioned") -> int:
        return self._db.execute(
            "SELECT COUNT(*) FROM intents WHERE owner=? AND status=?",
            (owner, status)).fetchone()[0]

    # count_class / class_pool / classes retired (class retirement
    # 2026-08-25): the depth gate died with v14, the few-shot pool
    # fed only the char-overlap assigner; the class column is a
    # fossil.

    def proto_pools(self) -> dict[str, dict]:
        """§2j protocol few-shot pool: protocol -> member scenario
        samples (the aggregation layer's samples belong to the
        protocol pool -- the data surface for matching protocol by
        scenario)."""
        out: dict[str, dict] = {}
        for p in self.protos(status="provisioned"):
            samples = []
            for m in p.get("members") or []:
                it = self.intent(m)
                if it is not None and (it.get("scenario") or ""):
                    samples.append(it["scenario"])
            out[p["name"]] = {"subtype": p["subtype"],
                              "members": p.get("members") or [],
                              "samples": samples}
        return out

    # ---- provision plane (v5: container law / scoring law, INTENT_SPEC §3c) ----

    def touch(self, name: str, score: float = 0.0) -> None:
        """Scoring law: only trigger/get stamps a score (the engine
        assigns score per the law); meta exposure gets zero. touch
        never touches rev/updated_at -- updated is a content change,
        touched is usage, two separate ledgers."""
        with self._lock, self._db:
            self._db.execute(
                "UPDATE intents SET last_touched=?, use_score=use_score+? "
                "WHERE name=?", (_now(), float(score), name))

    def intent_catalog(self, owner: str, *, top: int = 50
                       ) -> tuple[list[dict], int]:
        """Catalog: flat **top-N by usage** (class retired 2026-08-25
        — the per-class sampling went with it); ties favor recency,
        output name-sorted for stability; rows carry only
        name+scenario (saves tokens). The long tail goes through
        intent_search. Returns (rows, total) -- total covers the
        whole db so the agent can tell how much was truncated."""
        rows_all = self._db.execute(
            "SELECT name, scenario, use_score, "
            "COALESCE(last_touched, updated_at, created_at, '') AS ts "
            "FROM intents WHERE owner=? AND status='provisioned'",
            (owner,)).fetchall()
        pool = sorted(rows_all, key=lambda r: (r["ts"], r["name"]),
                      reverse=True)              # ties favor recency (stable order)
        best = sorted(pool, key=lambda r: -r["use_score"])[:max(1, top)]
        out = [{"name": r["name"], "scenario": r["scenario"] or ""}
               for r in sorted(best, key=lambda r: r["name"])]
        return out, len(rows_all)

    def intent_search(self, owner: str, *,
                      exclude: set[str] | None = None,
                      limit: int = 20) -> tuple[list[dict], int]:
        """Cold-storage listing, mechanical mode (explicit-rule
        filtering, semantic ranking left to the agent); the trimmed
        amount is disclosed via total_matched. Returns
        (rows[:limit], total_matched). The old query= LIKE branch was
        deleted (audit 2026-08-25): queried search goes through the
        engine's vector recall and never reached it -- and its %/_
        wildcard leak dies with it."""
        conds = ["owner=?", "status='provisioned'"]
        args: list = [owner]
        if exclude:
            names = sorted(exclude)
            conds.append(f"name NOT IN ({','.join('?' * len(names))})")
            args.extend(names)
        where = " AND ".join(conds)
        total = self._db.execute(
            f"SELECT COUNT(*) FROM intents WHERE {where}", args).fetchone()[0]
        rows = [dict(r) for r in self._db.execute(
            f"SELECT * FROM intents WHERE {where} "
            "ORDER BY COALESCE(last_touched, updated_at, '') DESC, name "
            "LIMIT ?", (*args, int(limit)))]
        return rows, total

    def never_used(self) -> list[str]:
        """Intents that have never been executed (non-test) --
        mechanical evidence for pruning."""
        return [r["name"] for r in self._db.execute(
            "SELECT i.name FROM intents i LEFT JOIN records r "
            "ON r.intent=i.name AND r.is_test=0 "
            "WHERE r.id IS NULL AND i.status='provisioned' ORDER BY i.name")]

    def tool_impact(self, ref: str) -> list[str]:
        """Which intents' delivery chains use this procedure@ver."""
        return [r["intent"] for r in self._db.execute(
            "SELECT DISTINCT intent FROM intent_steps WHERE ref=? "
            "ORDER BY intent", (ref,))]

    # caveats accessors retired (user ruling 2026-08-25): the table
    # is a fossil (schema additive-only, no writer, no reader) —
    # lessons flow back via sidecar revision (retry/rework brackets).

    # ---- chain specs (§6 v5: declare first, instantiate later) -----------

    def spec_put(self, name: str, *, head: str, priority: int = 0,
                 consequence: str = "",
                 steps: list[dict] | None = None) -> None:
        """Node: {assignee, kind, ref?, gate?, accounting?, template?,
        effect?, on_ok?, on_fail?} (M12's five attributes, defaulting
        to real/next/end). The declaration is written by the engine /
        the creation chain and never changed at runtime (priority
        law)."""
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO chain_specs(name,head,priority,"
                "consequence) VALUES(?,?,?,?)",
                (name, head, int(priority), consequence))
            self._db.execute("DELETE FROM chain_spec_steps WHERE spec=?",
                             (name,))
            self._db.executemany(
                "INSERT INTO chain_spec_steps(spec,seq,assignee,kind,ref,"
                "gate,accounting,template,effect,on_ok,on_fail,params) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                [(name, i, s["assignee"], s["kind"], s.get("ref"),
                  s.get("gate"), s.get("accounting", "real"),
                  s.get("template"), s.get("effect"),
                  s.get("on_ok", "next"), s.get("on_fail", "end"),
                  s.get("params"))
                 for i, s in enumerate(steps or [])])

    def spec_delete(self, name: str) -> None:
        """Sweeps out engine chain types entirely (M12 rename
        migration: intent-creation / debug old names retired; the
        historical journal is read-only and never migrated, old and
        new names coexist in history)."""
        with self._lock, self._db:
            self._db.execute("DELETE FROM chain_specs WHERE name=?",
                             (name,))
            self._db.execute("DELETE FROM chain_spec_steps WHERE spec=?",
                             (name,))

    def node(self, spec: str, seq: int) -> dict | None:
        """Unit addressing into the graph: (flow, seq) -> a node row
        (all five attributes present)."""
        r = self._db.execute(
            "SELECT * FROM chain_spec_steps WHERE spec=? AND seq=?",
            (spec, int(seq))).fetchone()
        return dict(r) if r else None

    def node_visits(self, chain_id: int, spec: str, seq: int) -> int:
        """How many times this token (chain_id) has visited a given
        node -- the mechanical face of the loop guardrail (back-edges
        are legal, capped by hop count, not by forbidding loops)."""
        return self._db.execute(
            "SELECT COUNT(*) FROM tasks WHERE chain_id=? AND spec=? "
            "AND seq=?", (chain_id, spec, int(seq))).fetchone()[0]

    def spec(self, name: str) -> dict | None:
        r = self._db.execute("SELECT * FROM chain_specs WHERE name=?",
                             (name,)).fetchone()
        if r is None:
            return None
        d = dict(r)
        d["steps"] = [dict(s) for s in self._db.execute(
            "SELECT * FROM chain_spec_steps WHERE spec=? ORDER BY seq",
            (name,))]
        return d

    def startable(self, instance: str) -> list[dict]:
        """The query surface of the access law: what can I start, and
        what follows (the load-task-chain mechanism)."""
        return [dict(r) for r in self._db.execute(
            "SELECT * FROM chain_specs WHERE head=? ORDER BY name",
            (instance,))]

    # ---- tasks / chains (§6 runtime instances) -----------------------------

    def chain_start(self, spec_name: str, *, issuer: str,
                    intent: str | None = None,
                    payload: str | None = None,
                    origin: int | None = None) -> dict:
        """Starts a chain: the access law's enforcement point -- only
        head can start it. The first ring is minted from spec step 0;
        chain_id is the first ring's id; priority is inherited from
        spec; payload = the user input carried at launch (parameters),
        riding along the whole chain."""
        sp = self.spec(spec_name)
        if sp is None:
            raise ValueError(f"chain_start: no such spec {spec_name!r}")
        if issuer != sp["head"] and issuer != "user":
            # A human owns every surface, bypassing the head check
            # (ruling 2026-08-10: chains triggered by IME have
            # issuer=user, otherwise the human couldn't cancel a chain
            # they misfired themselves)
            raise PermissionError(
                f"chain_start: {issuer!r} is not {spec_name!r}'s head "
                f"({sp['head']!r}) — only the head may initiate")
        if not sp["steps"]:
            raise ValueError(f"chain_start: spec {spec_name!r} has "
                             f"zero steps")
        s0 = sp["steps"][0]
        # §2u drops param (user ruling 2026-08-15): the params table
        # retires entirely -- payload is now the raw natural-language
        # text, no more parsed artifact. The params column stays but
        # is unused (the old never-rename-DB-columns rule, a fossil
        # column).
        parsed = None
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO tasks(chain_id,seq,spec,intent,payload,issuer,"
                "executor,status,gate,priority,rev,origin,params,"
                "created_at,updated_at) "
                "VALUES(0,0,?,?,?,?,?,?,?,?,1,?,?,?,?)",
                (spec_name, intent, payload, issuer, s0["assignee"],
                 "gated" if s0["kind"] == "gate" else "queued",
                 s0.get("gate"), sp["priority"], origin, parsed,
                 _now(), _now()))
            tid = cur.lastrowid
            self._db.execute("UPDATE tasks SET chain_id=? WHERE id=?",
                             (tid, tid))
            return self.task(tid)

    def advance(self, prev_id: int) -> dict | None:
        """Linear advancement = the implementation of the "next" edge
        (demoted in M12: engine routing looks up edges via settle,
        this is kept for purely linear consumers). Only allowed to
        mint once the previous ring is done; returns None once the
        chain finishes; a cancelled chain = halted, no more routing
        (an accepted ring never rolls back, it halts at accounting --
        ruling 2026-08-10)."""
        prev = self.task(prev_id)
        if prev is None or prev["status"] != "done":
            raise ValueError("advance: previous ring missing or not "
                             "done")
        if self.chain_cancelled(prev["chain_id"]):
            return None
        nxt_seq = prev["seq"] + 1
        if not prev["spec"] or self.node(prev["spec"], nxt_seq) is None:
            return None                     # chain finished
        return self.route_next(prev_id, prev["spec"], nxt_seq)

    def route_next(self, prev_id: int, spec: str, seq: int, *,
                   origin: int | None = None) -> dict:
        """Where an edge lands: the token (chain_id and its baggage --
        intent/payload/issuer/priority) continues on to (flow, seq) --
        the next node in the same chain, a back-edge within the same
        chain, or a cross-chain redirect, all the same path. origin
        defaults to inherited (a linear chain shares one anchor, the
        retry law); a jump edge has its origin passed explicitly by
        the engine (origin = the ring it jumped from)."""
        prev = self.task(prev_id)
        if prev is None:
            raise ValueError(f"route_next: no such task {prev_id!r}")
        s = self.node(spec, seq)
        if s is None:
            raise ValueError(f"route_next: no such node {spec}:{seq}")
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO tasks(chain_id,seq,spec,intent,payload,issuer,"
                "executor,status,gate,priority,rev,origin,params,"
                "created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,1,?,?,?,?)",
                (prev["chain_id"], int(seq), spec, prev["intent"],
                 prev["payload"], prev["issuer"], s["assignee"],
                 "gated" if s["kind"] == "gate" else "queued",
                 s.get("gate"), prev["priority"],
                 origin if origin is not None else prev.get("origin"),
                 prev.get("params"), _now(), _now()))
            return self.task(cur.lastrowid)

    def chain_cancel(self, chain_id: int, actor: str) -> int:
        """The unit of cancel is the chain, not the ring (ruling
        2026-08-10): unaccepted rings (queued/gated) are voided;
        accepted ones (running) don't roll back, the chain halts
        after accounting via advance's flag check. Returns the count
        of voided rings."""
        with self._lock, self._db:
            cur = self._db.execute(
                "UPDATE tasks SET status='cancelled', updated_at=? "
                "WHERE chain_id=? AND status IN ('queued','gated')",
                (_now(), chain_id))
            self._db.execute(
                "INSERT OR REPLACE INTO chain_flags(chain_id,cancelled,"
                "actor,t) VALUES(?,1,?,?)", (chain_id, actor, _now()))
            return cur.rowcount

    def chain_cancelled(self, chain_id: int) -> bool:
        r = self._db.execute(
            "SELECT cancelled FROM chain_flags WHERE chain_id=?",
            (chain_id,)).fetchone()
        return bool(r and r["cancelled"])

    def chains_recent(self, limit: int = 30) -> list[dict]:
        """The chain ledger -- the display surface (one row per chain,
        globally visible across instances; the issuer field lets the
        UI attribute ownership -- visibility is global, ownership only
        governs cancel permission): status = the cancelled flag takes
        precedence over the last ring's status; carries the current
        ring's id (where approval/observation lands)."""
        rows = [dict(r) for r in self._db.execute(
            "SELECT t.* FROM tasks t JOIN (SELECT chain_id, MAX(id) m "
            "FROM tasks GROUP BY chain_id) x ON t.id=x.m "
            "ORDER BY t.chain_id DESC LIMIT ?", (limit,))]
        out = []
        for r in rows:
            st = ("cancelled" if self.chain_cancelled(r["chain_id"])
                  else r["status"])
            out.append({"chain": r["chain_id"], "spec": r["spec"],
                        "intent": r["intent"], "issuer": r["issuer"],
                        "priority": r["priority"], "seq": r["seq"],
                        "status": st, "task": r["id"],
                        "gate": r["gate"] if r["status"] == "gated"
                        else None, "payload": r["payload"]})
        return out

    def queue_view(self) -> list[dict]:
        """The subtask queue -- the executor-side face (the UI task
        queue's data source): non-final rings, ordered by the
        priority law (higher tier first, FIFO within a tier)."""
        return [dict(r) for r in self._db.execute(
            "SELECT * FROM tasks WHERE status IN "
            "('queued','running','gated') ORDER BY priority DESC, id ASC")]

    # bind/unbind/bindings/binding_of/binding_chain have been removed
    # (user ruling 2026-08-23: the soft-deck/key-binding panel retired
    # alongside the native Elgato UI) -- the bindings table schema is
    # kept but no longer read or written (additive-migration indexing
    # left untouched).

    def overdue(self, executor: str, timeout_s: float) -> list[dict]:
        """The mechanical face of the timeout law: running and
        updated_at earlier than now-timeout (fixed-length format, so
        string order is time order)."""
        cut = time.strftime("%Y-%m-%d %H:%M:%S",
                            time.localtime(time.time() - timeout_s))
        return [dict(r) for r in self._db.execute(
            "SELECT * FROM tasks WHERE executor=? AND status='running' "
            "AND updated_at < ?", (executor, cut))]

    def inflight(self, intent: str) -> list[dict]:
        """Tasks for the same intent not yet accounted for
        (deduplication ruling 2026-08-10: in-flight rejects a new
        trigger -- the serialization law governs the agent side, this
        one governs the trigger side)."""
        return [dict(r) for r in self._db.execute(
            "SELECT * FROM tasks WHERE intent=? AND status IN "
            "('queued','running','gated') ORDER BY id", (intent,))]

    def latest_for(self, intent: str, spec: str) -> dict | None:
        """The most recent ring for a given intent on a given chain
        type (retry = the addressing surface for rev++ on the same
        ring)."""
        r = self._db.execute(
            "SELECT * FROM tasks WHERE intent=? AND spec=? "
            "ORDER BY id DESC LIMIT 1", (intent, spec)).fetchone()
        return dict(r) if r else None

    def queue_for(self, executor: str) -> list[dict]:
        """A given assignee's pending work, ordered by the priority
        law: higher tier cuts in, FIFO within a tier."""
        return [dict(r) for r in self._db.execute(
            "SELECT * FROM tasks WHERE executor=? AND status='queued' "
            "ORDER BY priority DESC, id ASC", (executor,))]

    def seat_running(self, executor: str) -> dict | None:
        """§2h one seat, one active ticket: the seat's currently
        active ticket (running = delivered, not yet accounted for).
        Shares the same source as the accounting surface -- telemetry
        events attribute to this ticket by seat."""
        r = self._db.execute(
            "SELECT * FROM tasks WHERE executor=? AND status='running' "
            "ORDER BY id LIMIT 1", (executor,)).fetchone()
        return dict(r) if r else None

    def queue_ceiling(self, executor: str) -> int | None:
        """§2h the reference surface for the intake law: the highest
        tier currently in queue (queued+running). gated doesn't occupy
        the queue -- a ticket awaiting approval hangs on the gate, not
        counted in the comparison."""
        r = self._db.execute(
            "SELECT MAX(priority) m FROM tasks WHERE executor=? AND "
            "status IN ('queued','running')", (executor,)).fetchone()
        return r["m"] if r and r["m"] is not None else None

    def compile_delivery(self, intent_name: str) -> str:
        """intent delivery = a flow specialization (§6 v7): **a single
        node** -- one intent compiles into one kind=deliver execution
        node, handed to x·solo. Called at provision time, zero special
        cases in the runner. flow name = deliver:<intent>, head =
        owner (only its own surface triggers it).

        **The procedure node has retired** (user ruling 2026-08-16
        night): procedure is the **physical layer** of the control
        protocol, hangs off a key slot (bindings.chain), run to
        completion by the trigger side before the task is handed off
        -- it's no longer in the intent's delivery chain. Three things
        went with it: (1) `effect: fail:suspend_intent` lost its
        anchor -- a physical-layer blowup shouldn't suspend the intent
        (a broken keyboard doesn't mean the document is corrupt);
        (2) rework's redirect to `qual·rework` retires for the same
        reason -- sidecar never wrote that code and can't fix it,
        rework would spin idle; (3) the fires dual-form pairing is
        voided along with it (fires=0 was defined as "chain bound to a
        procedure, steps left empty" -- once chain is gone there's no
        content left). The execution-time recovery path is still
        written into the E-node branches (an if-branch is the
        failback)."""
        it = self.intent(intent_name)
        if it is None:
            raise ValueError(f"compile_delivery: no such intent "
                             f"{intent_name!r}")
        # §2m v9/v14 redirect law: an intent always goes through the
        # general-purpose execution seat x·solo (sonnet pinned, spun
        # up on demand, can run in parallel) -- sidecar is reserved
        # only for creation/testing/debug/protocol multi-turn work;
        # protocols are entirely bracketed, no dedicated execution
        # seat. Single source here, boot recompiles the whole set and
        # self-heals, IME/deck triggers stay byte-for-byte unchanged.
        steps: list[dict] = [
            {"assignee": "x·solo", "kind": "deliver",
             "ref": intent_name,
             "template": "xsolo",
             "accounting": "real",
             "on_ok": "end", "on_fail": "end"}]
        name = f"deliver:{intent_name}"
        self.spec_put(name, head=it["owner"], priority=0,
                      consequence=(f"run intent "
                                   f"'{it['title'] or intent_name}'"),
                      steps=steps)
        return name

    def task_update(self, tid: int, *, status: str | None = None,
                    gate: str | None = None, executor: str | None = None,
                    delivered_at: str | None = None,
                    host_session: str | None = None,
                    bump_rev: bool = False) -> None:
        sets, args = ["updated_at=?"], [_now()]
        if status is not None:
            sets.append("status=?"); args.append(status)
        if gate is not None:
            sets.append("gate=?"); args.append(gate)
        if executor is not None:
            sets.append("executor=?"); args.append(executor)
        if delivered_at is not None:
            sets.append("delivered_at=?"); args.append(delivered_at)
        if host_session is not None:
            sets.append("host_session=?"); args.append(host_session)
        if bump_rev:
            sets.append("rev=rev+1")
        args.append(tid)
        with self._lock, self._db:
            self._db.execute(
                f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", args)

    def task(self, tid: int) -> dict | None:
        r = self._db.execute("SELECT * FROM tasks WHERE id=?",
                             (tid,)).fetchone()
        return dict(r) if r else None

    def chain(self, chain_id: int) -> list[dict]:
        """The token's travel ledger, in order of occurrence (M12:
        after back-edges/cross-chain jumps, seq repeats and
        interleaves, only id order is the journey order)."""
        return [dict(r) for r in self._db.execute(
            "SELECT * FROM tasks WHERE chain_id=? ORDER BY id", (chain_id,))]

    def issued_by(self, issuer: str) -> list[dict]:
        """The issuer ledger: each instance can see the tasks it
        issued and their status."""
        return [dict(r) for r in self._db.execute(
            "SELECT * FROM tasks WHERE issuer=? ORDER BY id", (issuer,))]

    def gated(self) -> list[dict]:
        """All rings parked at a human gate -- they can sit forever
        unapproved; only approval moves them forward."""
        return [dict(r) for r in self._db.execute(
            "SELECT * FROM tasks WHERE status='gated' ORDER BY id")]

    def tasks_recent(self, limit: int = 30) -> list[dict]:
        """The chain view for the observation surface (the UI's
        focus = intent execution status)."""
        return [dict(r) for r in self._db.execute(
            "SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (int(limit),))]

    # ---- records ---------------------------------------------------------

    def record(self, task_id: int, intent: str | None, *, is_test: bool,
               outcome: str, duration_s: float | None = None) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO records(task_id,intent,is_test,outcome,"
                "duration_s,t) VALUES(?,?,?,?,?,?)",
                (task_id, intent, int(is_test), outcome, duration_s, _now()))

    def record_for(self, task_id: int) -> dict | None:
        """A given task's most recent history entry (used by the
        retry-verification ring to fetch the last outcome)."""
        r = self._db.execute(
            "SELECT * FROM records WHERE task_id=? ORDER BY id DESC "
            "LIMIT 1", (task_id,)).fetchone()
        return dict(r) if r else None

    def track(self, intent: str, *, include_test: bool = False) -> list[dict]:
        q = "SELECT * FROM records WHERE intent=?"
        if not include_test:
            q += " AND is_test=0"
        return [dict(r) for r in self._db.execute(q + " ORDER BY id",
                                                  (intent,))]

    # ---- events (M15 post-hoc inspection surface) -------------------------

    def event_put(self, kind: str, name: str, *, t: str | None = None,
                  task_id: int | None = None, intent: str | None = None,
                  issuer: str | None = None, session: str | None = None,
                  fields: dict | None = None) -> None:
        """Rows in the engine's own facts.

        `t` can be passed in by the caller -- when journal double-
        writes, the same instant string is passed so the two sides
        line up. **A dropped row never bites back on real work** (same
        discipline as the journal): exceptions are swallowed, only a
        trace is left. This is the post-hoc inspection surface, not
        the decision surface; if it breaks, it must not take down a
        ring that's actively running.
        """
        try:
            blob = json.dumps(fields, ensure_ascii=False) if fields else None
            with self._lock, self._db:
                self._db.execute(
                    "INSERT INTO events(t,kind,name,task_id,intent,issuer,"
                    "session,fields) VALUES(?,?,?,?,?,?,?,?)",
                    (t or _now(), kind, name, task_id, intent, issuer,
                     session, blob))
        except Exception as e:
            print(f"[events] dropped line {kind}/{name}: {e!r}")

    def events_between(self, t0: str, t1: str, *,
                       kinds: list[str] | None = None,
                       names: list[str] | None = None,
                       task_id: int | None = None,
                       limit: int = 2000) -> list[dict]:
        """Query by time window + kind (user ruling 2026-08-12, M15's
        sole use case).

        - The time window is a **closed interval**, inclusive of both
          ends.
        - `kinds`/`names` being None **or empty** both mean no filter.
          Empty = no filter is intentional: in a UI dropdown, "nothing
          checked" means "unrestricted," not "want nothing."
        - An unknown kind naturally yields an empty set, never an
          error.
        - Results are ordered by `id` -- that's the **order of
          occurrence**; §7's materialization relies on it to tell
          "rejected then approved" apart from "approved directly," so
          aggregation is never allowed here.
        """
        q = ["SELECT * FROM events WHERE t>=? AND t<=?"]
        args: list = [t0, t1]
        if kinds:
            q.append(f"AND kind IN ({','.join('?' * len(kinds))})")
            args += list(kinds)
        if names:
            q.append(f"AND name IN ({','.join('?' * len(names))})")
            args += list(names)
        if task_id is not None:
            q.append("AND task_id=?")
            args.append(task_id)
        q.append("ORDER BY id LIMIT ?")
        args.append(int(limit))
        return [dict(r) for r in self._db.execute(" ".join(q), args)]

    def event_kinds(self) -> list[dict]:
        """The kind catalog (for the UI dropdown / for the §7 compiler
        to see what material is available)."""
        return [dict(r) for r in self._db.execute(
            "SELECT kind, name, COUNT(*) AS n FROM events "
            "GROUP BY kind, name ORDER BY n DESC, kind, name")]

    def task_window(self, task_id: int) -> dict | None:
        """The transcript-slicing coordinate for a single ticket --
        **this is M15's product**.

        Use it to slice the host transcript: matching `sessionId` AND
        `t0_utc <= timestamp <= t1_utc`. The engine itself never reads
        the transcript (a soft dependency, it only stores the
        coordinate, never a copy); reading is the job of the post-hoc
        analysis side.

        `t0` takes `delivered_at` -- the delivery instant, not the
        enqueue instant; time spent queueing doesn't count as
        execution. Old rows (submitted before v7) have no
        delivered_at, falling back to `created_at` with `queued`
        flagged, so the consumer knows this ticket's start point has
        queueing mixed in.
        """
        r = self._db.execute(
            "SELECT id,intent,issuer,executor,status,delivered_at,"
            "host_session,created_at,updated_at FROM tasks WHERE id=?",
            (task_id,)).fetchone()
        if r is None:
            return None
        t0 = r["delivered_at"] or r["created_at"]
        t1 = r["updated_at"]
        rec = self.record_for(task_id) or {}
        return {"task_id": r["id"], "host_session": r["host_session"],
                "t0": t0, "t1": t1,
                "t0_utc": _utc_iso(t0, ".000Z"),
                "t1_utc": _utc_iso(t1, ".999Z"),
                "queued": r["delivered_at"] is None,
                "intent": r["intent"], "issuer": r["issuer"],
                "executor": r["executor"], "status": r["status"],
                "duration_s": rec.get("duration_s")}

    # (boundary_compiled table: a fossil of M16 materialization -- the
    # permission surface consolidated and retired alongside the
    # pruner seat on 2026-08-24; the DDL stays per the additive law,
    # the access surface was removed.)

    # ---- boot-time reconciliation (guardrail 2) ----------------------------

    # ---- procedures (M20 §2c: row-side of the repo, human approval only) ----

    def proc_seed(self, builtin: dict) -> None:
        """Physical-layer wordlist registration (user ruling
        2026-08-16 night): procedure = an engine built-in, upserted to
        provisioned from the wordlist on boot (rev is governed by the
        engine version, always 1 here); existing rows not in the
        wordlist are marked retired -- the agent submission channel
        (proc_stage/proc_approve) has retired entirely along with the
        physical-layer ruling.

        Write-only by design (audit 2026-08-25): nothing reads this
        table at runtime -- registration validates against
        defaults.PHYS_PROCEDURES directly. The rows are a disclosure
        ledger (what wordlist this workspace has seen, hand-
        inspectable in the DB), not a lookup surface."""
        with self._lock, self._db:
            for name, desc in builtin.items():
                row = self._db.execute(
                    "SELECT name FROM procedures WHERE name=?",
                    (name,)).fetchone()
                if row is None:
                    self._db.execute(
                        "INSERT INTO procedures(name,desc,rev,status,"
                        "created_at,updated_at) "
                        "VALUES(?,?,1,'provisioned',?,?)",
                        (name, desc, _now(), _now()))
                else:
                    self._db.execute(
                        "UPDATE procedures SET desc=?, "
                        "status='provisioned', updated_at=? WHERE name=?",
                        (desc, _now(), name))
            qmarks = ",".join("?" * len(builtin)) or "''"
            self._db.execute(
                f"UPDATE procedures SET status='retired', updated_at=? "
                f"WHERE name NOT IN ({qmarks})",
                [_now(), *builtin.keys()])

    def proc_get(self, name: str) -> dict | None:
        row = self._db.execute("SELECT * FROM procedures WHERE name=?",
                               (name,)).fetchone()
        return dict(row) if row else None

    def procs(self, status: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM procedures", []
        if status:
            q, args = q + " WHERE status=?", [status]
        return [dict(r) for r in
                self._db.execute(q + " ORDER BY name", args)]

    # ---- protocols (M20 §2/§2d: the table is truth, skill is rendered) ----

    # boundary/cls params kept with dead defaults: their declaration
    # surfaces were retired (boundary 2026-08-24, class 2026-08-25),
    # both columns are fossils (additive law)
    def proto_stage(self, name: str, *, subtype: str, boundary: str = "",
                    cls: str = "unfiled", scenario: str, staged_hash: str,
                    model: str = "sonnet",
                    prep: str = "", wrapup: str = "",
                    born: str | None = None) -> None:
        """submit lands a row: a new item is draft; resubmitting
        swaps staged_hash (a live item keeps serving its old version
        while it awaits re-approval, only switching once approved --
        same law as procedure). subtype/scenario/model/prep/
        wrapup update with each submission (what's approved is the
        whole package)."""
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT name FROM protocols WHERE name=?",
                (name,)).fetchone()
            if row is None:
                self._db.execute(
                    "INSERT INTO protocols(name,subtype,boundary,class,"
                    "scenario,staged_hash,model,prep,wrapup,rev,status,"
                    "born_session,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,0,'draft',?,?,?)",
                    (name, subtype, boundary, cls, scenario, staged_hash,
                     model or "sonnet", prep, wrapup, born,
                     _now(), _now()))
            else:
                self._db.execute(
                    "UPDATE protocols SET subtype=?, boundary=?, class=?,"
                    " scenario=?, staged_hash=?, model=?, prep=?, "
                    "wrapup=?, updated_at=? WHERE name=?",
                    (subtype, boundary, cls, scenario, staged_hash,
                     model or "sonnet", prep, wrapup, _now(), name))

    def proto_set_status(self, name: str, status: str) -> None:
        """Editor-side status flip (consolidate suspension
        2026-08-25): draft = off the shelf, Start refused by the
        provisioned-only guard; revival is the whole-book
        re-registration approve (proto_compile_unit)."""
        with self._lock, self._db:
            self._db.execute(
                "UPDATE protocols SET status=?, updated_at=? "
                "WHERE name=?", (status, _now(), name))

    def proto_approve(self, name: str) -> dict | None:
        """Human approval makes it live: staged -> live (hash swaps,
        rev++, provisioned). No pending snapshot = idempotent no-op
        (safe for gate replay)."""
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT staged_hash FROM protocols WHERE name=?",
                (name,)).fetchone()
            if row is None or not row["staged_hash"]:
                return None
            self._db.execute(
                "UPDATE protocols SET skill_hash=staged_hash, "
                "staged_hash=NULL, rev=rev+1, status='provisioned', "
                "updated_at=? WHERE name=?", (_now(), name))
        return self.proto_get(name)

    def proto_get(self, name: str) -> dict | None:
        row = self._db.execute("SELECT * FROM protocols WHERE name=?",
                               (name,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        try:
            d["members"] = json.loads(d.get("members") or "[]")
        except ValueError:
            d["members"] = []
        return d

    def protos(self, status: str | None = None,
               subtype: str | None = None) -> list[dict]:
        q, args = "SELECT name FROM protocols", []
        conds = []
        if status:
            conds, args = conds + ["status=?"], args + [status]
        if subtype:
            conds, args = conds + ["subtype=?"], args + [subtype]
        if conds:
            q += " WHERE " + " AND ".join(conds)
        return [self.proto_get(r["name"]) for r in
                self._db.execute(q + " ORDER BY name", args)]

    def proto_of_member(self, intent: str,
                        subtype: str | None = None) -> dict | None:
        """Which live protocol an intent belongs to. v17 (compilation-
        unit ruling): read the column, don't scan the table --
        membership is a declared fact (intents.proto), not a runtime
        inference."""
        row = self._db.execute(
            "SELECT proto FROM intents WHERE name=?", (intent,)).fetchone()
        if row is None or not row["proto"]:
            return None
        p = self.proto_get(row["proto"])
        if (p is None or p["status"] != "provisioned"
                or (subtype is not None and p["subtype"] != subtype)):
            return None
        return p

    def proto_compile_unit(self, name: str, member_decls: list[dict], *,
                           owner: str, cls: str = "unfiled",
                           born: str | None = None) -> dict | None:
        """v17 atomic whole-roster compilation (user ruling 2026-08-16
        late night: "take it all, no singletons"). Done in **one
        transaction**: skill staged->live (rev++, provisioned) + per-
        member upsert (content + status=provisioned + proto stamp +
        pointer stamp) + the members roster lands. No pending
        snapshot = idempotent no-op (safe for gate replay). Member
        rows are a ledger projection (read by roster/IME/search); the
        canonical source is the roster directory
        members/<name>/intent.json."""
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT staged_hash FROM protocols WHERE name=?",
                (name,)).fetchone()
            if row is None or not row["staged_hash"]:
                return None
            self._db.execute(
                "UPDATE protocols SET skill_hash=staged_hash, "
                "staged_hash=NULL, rev=rev+1, status='provisioned', "
                "members=?, updated_at=? WHERE name=?",
                (json.dumps([d["name"] for d in member_decls],
                            ensure_ascii=False), _now(), name))
            for d in member_decls:
                m = d["name"]
                # member-step prelude (2026-08-24): procedures enters
                # with the member declaration (the caller already
                # passed the wordlist gate; the engine runs the
                # prelude first when the member's key is pressed)
                prcs = json.dumps(
                    [str(x).strip() for x in (d.get("procedures") or [])
                     if str(x).strip()], ensure_ascii=False)
                vals = (d.get("title") or "", d.get("scenario") or "",
                        d.get("steps") or "", d.get("acceptance") or "",
                        "")   # boundary column: fossil, no writer
                if self._db.execute("SELECT name FROM intents WHERE "
                                    "name=?", (m,)).fetchone() is None:
                    self._db.execute(
                        "INSERT INTO intents(name,title,scenario,steps,"
                        "instructions,boundary,procedures,fires,owner,"
                        "status,rev,"
                        "class,scope,proto,migrated_to,params,born_session,"
                        "last_touched,created_at,updated_at) "
                        "VALUES(?,?,?,?,?,?,?,1,?,'provisioned',1,?,?,?,?,"
                        "'[]',?,?,?,?)",
                        (m, *vals, prcs, owner, cls or "unfiled", owner,
                         name,
                         f"protocol:{name}", born, _now(), _now(), _now()))
                else:
                    self._db.execute(
                        "UPDATE intents SET title=?, scenario=?, steps=?, "
                        "instructions=?, boundary=?, procedures=?, "
                        "status='provisioned',"
                        " proto=?, migrated_to=?, rev=rev+1, updated_at=?,"
                        " last_touched=? WHERE name=?",
                        (*vals, prcs, name, f"protocol:{name}", _now(),
                         _now(), m))
        return self.proto_get(name)

    def reconcile(self, utility_root: Path) -> list[str]:
        """Rows <-> disk cross-verification. Returns a list of
        problems (the engine logs loudly to the journal + prints on
        boot, never silently); an empty list means no problems. After
        v16, procedure = an engine built-in (physical layer), no
        longer cross-verified against disk; the bindings.chain
        wordlist check retired along with the bind panel (user ruling
        2026-08-23). protocol's skill cross-verification has its own
        gate elsewhere."""
        problems: list[str] = []
        # v17 compilation-unit cross-verification: a roster is atomic
        # -- a live roster's members must have complete rows with
        # matching stamps; a stamp without a roster (or a roster not
        # live) is the same ailment. A problem = called out loudly,
        # never a silent downgrade.
        for p in self.protos(status="provisioned"):
            for m in p["members"]:
                row = self._db.execute(
                    "SELECT status, proto FROM intents WHERE name=?",
                    (m,)).fetchone()
                if row is None:
                    problems.append(f"booklet '{p['name']}' member "
                                    f"'{m}' has no ledger row — the "
                                    f"booklet should be resubmitted "
                                    f"and recompiled")
                elif row["status"] != "provisioned" \
                        or (row["proto"] or "") != p["name"]:
                    problems.append(
                        f"booklet '{p['name']}' member '{m}' stamp "
                        f"mismatch (status={row['status']}, "
                        f"proto={row['proto'] or '∅'})")
        live_p = {p["name"] for p in self.protos(status="provisioned")}
        for r in self._db.execute(
                "SELECT name, proto FROM intents "
                "WHERE proto IS NOT NULL AND proto != '' "
                "AND status != 'retired'"):
            # Retired members keep their proto stamp (soft-retirement
            # law: history stays) — a booklet retired whole would
            # otherwise flag every one of its members as sick on
            # every boot, forever (audit 2026-08-26).
            if r["proto"] not in live_p:
                problems.append(f"member '{r['name']}''s booklet "
                                f"'{r['proto']}' is not on the shelf — "
                                f"orphan member (booklet retired "
                                f"without clearing members?)")
        return problems
