"""Engine — M1 shape: cast home, pull up the host, open the
channel, serve the page.

One process, four threads: HTTP (observe page), WS (channel), PTY
reader, main thread (state and teardown). The task plane, staged
apply, and pipeline all land in later batches (see ROADMAP); M1's
home only supports chat (user ruling 2026-08-10).
"""
from __future__ import annotations

import http.server
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from pathlib import Path


from . import defaults
from .host.pty import PtyHost
from .host.headless import HeadlessHost
from .kernel import deckgen, netguard, procrun, prune_report, \
    vector, wspace
from .kernel import config as wsconfig
from .kernel.channel import Channel
from .kernel.journal import Journal
from .kernel.provision import instance_home, provision_home, \
    provision_proto_home, provision_solo_home, solo_allow_rules
from .kernel.store import FLOW_QUAL_NEW, \
    FLOW_QUAL_REWORK, FLOW_RETIRE, FLOW_WS_QUAL, Store

PANEL_DIR = Path(__file__).parent / "panel"

# ANSI stripping (for card-tail excerpts): CSI / OSC / other
# single-char ESC. Cards show a text approximation to people —
# the exact picture is on the terminal; expand for a closer look.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]"
                   r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
                   r"|\x1b[@-_]")


class ProtoInstance:
    """M26 §3: protocol's resident execution seat — one protocol,
    one household (home doesn't scatter across sessions), one
    session CLI of its own, one card-stream window of its own. All
    six host-contract faces are honored, plus the deliver/reap
    pair the task plane needs.

    Delivery discipline: envelopes queue up, only released once the
    host is ready+trusted (nothing gets dropped while booting);
    step envelopes get a separate lane — member steps aren't
    released until the bracket package has been delivered (with
    lazy spawn the deck keypress can precede the pump's delivery;
    this gate guarantees the order). kind = "pty": _deliver keeps a
    thin-envelope shape for this seat (the TUI composer chokes on
    long pasted text). reap is a no-op — resident seat, settling
    the account doesn't kill the process."""

    kind = "pty"

    def __init__(self, pname: str, home: Path, model: str,
                 on_output=None, spawn: bool = True,
                 step_ready=None):
        self.pname = pname
        self.home = home
        self.seat = defaults.XPROTO_PREFIX + pname
        self._pending: list[str] = []
        self._steps: list[str] = []
        # step ledger (user ruling 2026-08-23, the Step bar's data
        # face): name and state of the most recent member step —
        # "running" on delivery, "done" once the host reports back
        # via step_done (lightweight claim, no child task opened).
        self.step_name: str | None = None
        self.step_state: str | None = None
        # Quiet clock (P1-i fallback, display only): every PTY
        # output stamps the time; if the step ledger is "running"
        # but silent past STEP_QUIET_S, the pump flips the ledger
        # back to idle (so the Step bar doesn't stay stuck blue
        # when the host forgets to call step_done). step_done is
        # still the source of truth.
        self.last_output = time.monotonic()
        self._lock = threading.Lock()
        self._step_ready = step_ready or (lambda: True)
        # ·收 made real (user ruling 2026-08-24): the ack flag for
        # the wrap-up ceremony — step_done(member="·收") sets it,
        # and the teardown thread waits on it or the grace clock
        self.wrap_evt = threading.Event()

        def _out(data, _cb=on_output):
            self.last_output = time.monotonic()
            if _cb:
                _cb(data)
        self.host = PtyHost(home, on_output=_out, model=model)
        self._spawned = False
        if spawn:
            self.host.start()
            self._spawned = True

    # ---- six-face contract ----
    def alive(self) -> bool:
        return self._spawned and self.host.alive()

    def ready(self) -> bool:
        return self.host.ready()

    def trusted(self) -> bool:
        return self.host.trusted()

    def replay(self) -> str:
        return self.host.replay()

    def write_raw(self, data: str) -> None:
        self.host.write_raw(data)

    def resize(self, cols: int, rows: int) -> None:
        try:
            self.host.resize(cols, rows)
        except AttributeError:
            pass                    # fake host / no resize face: not load-bearing

    def inject_chat(self, text: str) -> None:
        self.enqueue(text)

    # ---- task plane ----
    def deliver(self, tid: int, line: str):
        self.enqueue(line)
        return None                 # PTY seat: engine doesn't issue session ids

    def reap(self, tid: int) -> None:
        pass                        # resident seat: settling doesn't kill the process

    def enqueue(self, line: str) -> None:
        with self._lock:
            self._pending.append(line)

    def enqueue_step(self, line: str, member: str | None = None) -> None:
        with self._lock:
            self._steps.append(line)
            if member:
                self.step_name = member
                self.step_state = "running"
                self.last_output = time.monotonic()   # reset the quiet clock

    def flush(self) -> None:
        """Released on the pump's beat: plain envelopes
        (package/receipt) go first; the step lane follows only
        once the bracket has truly been delivered (step_ready).

        **Check order is correctness** (P1-b, third fix, live-fire
        precedent 2026-08-23, three in a row): trusted() must run
        before ready() — flip (the screen-change gate's
        bookkeeping) happens inside trusted(); if ready() ran
        first, it wouldn't see the _flip_t from the same tick that
        just flipped true, and would release on that same tick —
        injection lands in the screen-change blank window, and the
        quiet window is defeated."""
        if not (self.alive() and self.trusted() and self.ready()):
            return
        with self._lock:
            batch, self._pending = self._pending, []
        for line in batch:
            self.host.inject_chat(line)
        if not self._step_ready():
            return
        with self._lock:
            steps, self._steps = self._steps, []
        for line in steps:
            self.host.inject_chat(line)

    def stop(self, graceful: bool = False) -> None:
        """graceful (user ruling 2026-08-23, the Shutdown key
        takes this shape): ESC first to interrupt the running
        turn, then type /exit to let the CLI exit cleanly, with a
        timeout and tree-kill as fallback — a hard kill is always
        the floor, never the first move."""
        if not self._spawned:
            return
        if graceful and self.alive():
            try:
                self.host.write_raw("\x1b")
                time.sleep(0.3)
                self.host.inject_chat("/exit")
                t0 = time.monotonic()
                while (time.monotonic() - t0
                       < defaults.PROTO_EXIT_GRACE_S):
                    if not self.host.alive():
                        break
                    time.sleep(0.2)
            except Exception:
                pass
        try:
            self.host.stop()
        except Exception:
            pass
        self._spawned = False


class Engine:
    def __init__(self, workspace: Path,
                 http_port: int | None = None,
                 ws_port: int | None = None,
                 spawn_host: bool = True,
                 model: str | None = None):
        # workspace config file lands first (user ruling
        # 2026-08-24): defaults < config.json < explicit args — a
        # None arg picks up defaults after apply, an explicit value
        # (CLI flag / test) always wins
        self._cfg = wsconfig.load(Path(workspace))
        wsconfig.apply(self._cfg)
        self.model = model if model is not None else defaults.HOST_MODEL
        self.workspace = workspace
        self.http_port = (http_port if http_port is not None
                          else defaults.HTTP_PORT)
        self.ws_port = (ws_port if ws_port is not None
                        else defaults.WS_PORT)
        self.spawn_host = spawn_host
        self.module = defaults.OS_MODULE
        self._stop = threading.Event()
        # Teardown-window flag (teardown on standby, 2026-08-24):
        # set at the shutdown entry point — the status probe
        # reports draining (yellow dot on the key face), re-
        # entering shutdown is idempotent
        self._draining = False
        self.journal: Journal | None = None
        self.host: PtyHost | None = None
        # origin_port = the observe page's home: the WS gatekeeper uses it to recognize its own (loopback guard)
        # **Resolved** ports, not the raw args (audit 2026-08-25):
        # both default to None on the documented flag-less
        # `intentos run --workspace <dir>` (and whenever config.json
        # carries the knob), which handed Channel a None port —
        # serve(..., None) raises TypeError before anything binds,
        # and origin_port None armed the WS origin gate with 0.
        self.channel = Channel(self.ws_port, origin_port=self.http_port)
        self._httpd = None
        self.store = Store(workspace / "state.db")
        self.utility = workspace / "utility"
        self._wizard_warned = 0.0
        # M18 approval dedicated window: single-slot park (old-repo
        # precedent — one seat's CLI blocks inside the hook
        # waiting for an answer; a second ask arriving means the
        # old one is dead, so defer releases it); grants =
        # session-level always-approve (batch 2: resets on
        # restart, the persisted half is realized by M16)
        self._perm_slot: dict | None = None
        self._perm_lock = threading.Lock()
        self._bus_lock = threading.Lock()   # §2f task event-ledger append lock
        self._gate_lock = threading.Lock()  # §2i execution-seat gate-card wait table
        self._xhost_lock = threading.Lock()  # seat table: get/spawn/put is one critical section (audit 2026-08-25)
        self._gates: dict[int, dict] = {}   # card_id → {evt, ans}
        # P1-a gate clock (2026-08-23): a seat with a gate-card
        # hanging = waiting on a person — the time-limit rule
        # doesn't collect the body (a person's delay doesn't burn
        # machine clock time); the task's clock resets once the
        # gate drops (see _gate_wait)
        self._gate_busy: dict[str, int] = {}   # instance → open-gate count
        # Permission-face consolidation (user ruling 2026-08-24):
        # the always-allow ledger persists in config.json's
        # PERM_ALLOW (the user can hand-edit it); this is the
        # runtime view — seeded at boot, double-written (memory +
        # config.json) when Always is clicked on a card.
        self._perm_grants: set[str] = set(defaults.PERM_ALLOW)
        # Layer-order exemption clause (verified live 2026-08-12,
        # double-card bug, fixed twice the same night): two
        # moments — ask birth and ask-answered — the Notification
        # fallback card is exempted from opening when "the
        # dedicated window is present, or less than
        # PERM_NOTIF_GRACE_S has passed since max(birth,
        # answered)" (the echo arrives ~6s after PermissionRequest,
        # so the baseline must include the birth moment)
        self._perm_done_t = 0.0
        self._perm_seen_t = 0.0
        # M15 join key: the host CLI's sessionId, learned from any
        # hook payload (the engine can't manufacture it — it
        # belongs to the harness). Tasks dispatched before it's
        # learned land NULL, task_window consumers handle that
        # themselves.
        self._host_session: str | None = None
        # CASELAW 48 injection-receipt table: {wall, t, brief} — sending a message out != it landing
        self._inject_watch: list[dict] = []
        # caller channel: the token is an identity the engine mints
        # (an agent's self-report doesn't count); missing a token
        # is tolerated as home during the single-instance era, B6's
        # second instance makes it mandatory
        self.token = uuid.uuid4().hex[:20]
        self._tokens: dict[str, str] = {self.token: self.module}
        # M26 §3: execution-seat table — "solo" = the general
        # headless seat; other keys = protocol names, values =
        # ProtoInstance (resident session seat, lazily started
        # under the household scheme)
        self._xhosts: dict[str, object] = {}
        self._flow_opened: dict[str, float] = {}   # window debounce (seat → t)
        self._wrapping: set[str] = set()   # booklets mid wrap-up ceremony (press again = force)
        # v18 prelude in-flight set (procedures hung on an intent,
        # user ruling 2026-08-23): the pump thread doesn't wait on
        # the prelude — the prelude starts a background thread and
        # sets a prelude.ok marker on completion; the pump delivers
        # as usual on the next beat. While in flight, the pump
        # skips that order's beat.
        self._preluding: set[int] = set()
        # Container (the hot face, §2m v4/v10): bound (deck entries
        # the person placed) + this session's usage, cap =
        # CONTAINER_CAP, over the cap evict the least-recently-used
        # unbound member — **eviction only, never intrusion**
        # (leaving the container != leaving the library). The dict
        # is the LRU: key order = usage order within the session
        # (the container is a session-level face; ordering lives in
        # memory, not borrowed from the DB's second-resolution
        # clock).
        self._hot: dict[str, None] = {}
        self._veccache: dict[str, tuple[int, dict]] = {}  # M24 name→(rev,vec)
        # Cockpit card ledger (M13): a card = the live face of
        # "waiting on a person right now", not history (history is
        # in the journal) — in-memory state is enough, cards clear
        # naturally on an engine handover.
        # Two clocks feed PTY-stillness detection (completeness
        # rule, layer 3, sovereignty fallback).
        self._cards: dict[int, dict] = {}
        self._card_seq = 0
        self._card_lock = threading.Lock()
        self._last_output = time.monotonic()
        self._last_input = time.monotonic()
        self._stall_fired = False

    # ---- state face (CASELAW 22: vocabulary belongs to the engine) -------

    def _phase(self) -> str:
        if self.host is None:
            return "off"
        return "live" if self.host.alive() else "off"

    def _activity(self) -> str:
        if self.host is None or not self.host.alive():
            return ""
        if not self.host.trusted():
            return "wizard"                 # CASELAW 14: the wizard isn't an agent
        return "idle" if self.host.ready() else "booting"

    def _surface(self) -> dict:
        return {"type": "surface", "focus": self.module,
                "peers": {self.module: {"phase": self._phase(),
                                      "activity": self._activity(),
                                      "title": "OS home"}}}

    # ---- channel wiring ----------------------------------------------------

    def _on_chat(self, text: str, instance: str | None = None) -> None:
        """Plain chat — the engine doesn't sniff message bodies
        (IME ruling 2026-08-10: triggering is an explicit UI
        action, text that wasn't selected is always just talk).
        M26: a flow window's message carrying an instance goes
        straight to that household's host, bypassing sidecar."""
        if self.journal is not None:
            self.journal.row("chat", "user", text=text,
                             instance=instance)
        self.channel.broadcast({"type": "chat", "name": "user",
                                "text": text,
                                "instance": instance or self.module,
                                "t": time.strftime("%H:%M:%S")})
        if instance and instance != self.module:
            pname = (instance[len(defaults.XPROTO_PREFIX):]
                     if instance.startswith(defaults.XPROTO_PREFIX)
                     else instance)
            inst = self._xhosts.get(pname)
            if isinstance(inst, ProtoInstance) and inst.alive():
                inst.enqueue(text)
            else:
                self._say_engine("Instance host is not running — press "
                                 "Start first.", instance=instance)
            return
        if self.host is not None and self.host.alive():
            if not self.host.trusted():
                self._wizard_hint()         # don't inject into the wizard menu (CASELAW 14)
                return
            self._inject(text)

    def _say_engine(self, text: str, instance: str | None = None) -> None:
        """The engine speaking on the chat face (refusal reasons,
        gate lifts, provisioning notices — CASELAW 19: a refusal is
        an answer, and an answer has to reach the person). M26:
        frames carry instance — protocol events go only to that
        instance's card-stream window, leaving sidecar's face with
        only its own."""
        self.channel.broadcast({"type": "chat", "name": "engine",
                                "text": text,
                                "instance": instance or self.module,
                                "t": time.strftime("%H:%M:%S")})

    def _wizard_hint(self) -> None:
        """Injection is withheld during the wizard period — say it out loud once, don't stay silent (CASELAW 7/19)."""
        if time.monotonic() - self._wizard_warned < 60:
            return
        self._wizard_warned = time.monotonic()
        if self.journal is not None:
            self.journal.row("host", "wizard-held")
        self._say_engine("Host is still in the trust wizard — answer "
                         "it on the console face (login + trust); chat "
                         "and task delivery resume automatically.")

    def _bracket_of(self, pname: str, queued: bool = True) -> dict | None:
        """This protocol's in-flight bracket (M26 §3: one protocol,
        one bracket, **parallel** across protocols — the old
        global "one seat, one bracket" rule retired along with
        instance-based seating). queued=True also counts an order
        that hasn't been delivered yet but would open the bracket
        (double-clicking Start doesn't open twice); queued=False
        only recognizes already-delivered (running) ones — used by
        the step-release gate."""
        seat = defaults.XPROTO_PREFIX + pname
        t = self.store.seat_running(seat)
        if (t is not None
                and str(t.get("spec") or "") == f"protocol:{pname}"):
            return t
        if queued:
            for x in self.store.queue_for(seat):
                if str(x.get("spec") or "") == f"protocol:{pname}":
                    return x
        return None

    def _protocol_route(self, name: str, user_input: str,
                        by: str) -> bool:
        """Trigger routing on the protocol face (M26 reshuffle):
        both marker words and member words dispatch, by
        **household**, to that protocol's own instance; non-
        protocol words fall back to the regular chain. Returns
        True when this trigger has been consumed by the protocol
        face."""
        if name.endswith("·启") or name.endswith("·收"):
            pname, opening = name[:-2], name.endswith("·启")
            if opening:
                self._proto_start(pname, user_input, by=by)
            else:
                self._proto_close(pname, by=by)
            return True
        pmem = self.store.proto_of_member(name, subtype="interactive")
        if pmem is not None:
            self._proto_member(pmem["name"], name, user_input, by=by)
            return True
        return False

    # ---- protocol seat verbs (M26: deck's four fixed keys + the engine side of member slots) ----

    def _proto_guard(self, pname: str) -> dict | None:
        p = self.store.proto_get(pname)
        if (p is None or p["status"] != "provisioned"
                or p["subtype"] != "interactive"):
            self._say_engine(f"No provisioned interactive protocol "
                             f"'{pname}'.")
            return None
        return p

    def _proto_start(self, pname: str, user_input: str = "",
                     by: str = "deck") -> dict:
        """Start key / ·启: open the bracket + lazily start the
        household instance + open the card-stream window.
        Idempotent (household rule): an already-open bracket just
        points back to the same instance (just reopen the
        window)."""
        if self._proto_guard(pname) is None:
            return {"error": f"no provisioned protocol '{pname}'"}
        seat = defaults.XPROTO_PREFIX + pname
        br = self._bracket_of(pname)
        if br is not None:
            self._xhost(pname)          # seat may have dropped on an engine handover: bring it back
            self._open_flow_window(pname)
            self._say_engine(f"Protocol '{pname}' is already open "
                             f"(task {br['id']}).", instance=seat)
            return {"ok": True, "task": br["id"], "note": "already open"}
        if not self._admit_spec(f"protocol:{pname}", f"{pname}·启"):
            return {"error": "queue refused"}
        t = self.store.chain_start(f"protocol:{pname}", issuer="user",
                                   intent=pname,
                                   payload=user_input.strip() or None)
        self.journal.row("protocol", "start", intent=pname,
                        task=t["id"], by=by)
        self._wrapping.discard(pname)   # new bracket: clear the previous term's wrap-up flag
        inst0 = self._xhosts.get(pname)
        if isinstance(inst0, ProtoInstance):
            inst0.wrap_evt.clear()
        self._xhost(pname)              # lazy start (household home persists, process spawns lazily)
        self._open_flow_window(pname)
        self._task_bcast()
        return {"ok": True, "task": t["id"]}

    def _proto_close(self, pname: str, by: str = "deck") -> dict:
        """·收 / Shutdown, first half: a person closing the bracket
        is the acceptance ticket — the engine settles the account
        directly."""
        br = self._bracket_of(pname)
        if br is None:
            self._say_engine(f"Protocol '{pname}' has no open bracket.")
            return {"error": "no open bracket"}
        seat = defaults.XPROTO_PREFIX + pname
        self.store.task_update(br["id"], status="done")
        self.journal.row("protocol", "end", intent=pname,
                        task=br["id"], by=by)
        self._settle(br, "ok", outcome_text="protocol closed (human)")
        inst = self._xhosts.get(pname)
        if isinstance(inst, ProtoInstance):
            with inst._lock:
                inst._steps.clear()     # bracket is dead, any dangling step is buried with it
                inst.step_name = None
                inst.step_state = None
            if inst.alive():
                inst.enqueue(
                    f"[task {br['id']}] protocol {pname} end | the user "
                    f"closed the bracket; the engine has settled the "
                    f"task — no task_done needed, just wrap up whatever "
                    f"is in flight")
        self._say_engine(f"Protocol '{pname}' closed (task {br['id']}).",
                         instance=seat)
        # Booklet-consolidation prompt (reshaped 2026-08-25: same
        # consolidate semantics as a retry — approve suspends the
        # booklet and opens a consolidate order on the sidecar).
        # An empty booklet (zero member steps) doesn't prompt.
        # Filter in SQL, not in Python (audit 2026-08-25): the
        # all-time window comes back capped at `limit` (2000) ordered
        # by id, so once the never-pruned events table held 2000
        # protocol/step rows the current bracket's rows fell outside
        # the slice and this prompt went silent **forever**. The
        # store already takes task_id.
        steps = self.store.events_between(
            "2000-01-01 00:00:00", "2999-01-01 00:00:00",
            kinds=["protocol"], names=["step"], task_id=br["id"])
        if steps:
            self._consolidate_offer(
                "protocol", pname, br["id"],
                extra=f"Booklet '{pname}' closed — bracket task "
                      f"{br['id']} settled with {len(steps)} member "
                      f"step(s) on the ledger.")
        self._task_bcast()
        return {"ok": True, "task": br["id"]}

    def _proto_member(self, pname: str, member: str, user_input: str = "",
                      by: str = "deck") -> dict:
        """Member-slot key: if the bracket is already open → deliver
        a step envelope into that instance; if not open → refuse
        (user ruling 2026-08-23: opening a booklet doesn't trigger
        an intent, and a member key doesn't open a booklet either —
        this rule is what keeps "one open-close = one task" as the
        ledger's shape)."""
        p = self._proto_guard(pname)
        if p is None:
            return {"error": f"no provisioned protocol '{pname}'"}
        if member not in (p.get("members") or []):
            self._say_engine(f"'{member}' is not a member of protocol "
                             f"'{pname}'.")
            self.journal.row("protocol", "refused", intent=pname,
                            reason="non-member", member=member)
            return {"error": "non-member"}
        br = self._bracket_of(pname)
        if br is None:
            # lazy-spawn retired (user ruling 2026-08-23, overturns
            # 08-22's household lazy spawn): opening a booklet
            # **triggers no intent at all**, and a member key
            # doesn't open the booklet in reverse either — pressing
            # a member key on a closed booklet just prompts to
            # press Start first, zero side effects.
            self._say_engine(f"Protocol '{pname}' is closed — press "
                             f"Start first; member keys never open the "
                             f"bracket.")
            self.journal.row("protocol", "refused", intent=pname,
                            reason="bracket-closed", member=member)
            return {"error": "bracket closed"}
        seat = defaults.XPROTO_PREFIX + pname
        self._touch(member, defaults.SCORE_TRIGGER)
        self.journal.row("protocol", "step", intent=pname,
                        task=br["id"], member=member, by=by,
                        input=user_input.strip() or None)
        inst = self._xhost(pname)
        if inst is None or not inst.alive():
            self._say_engine(f"Protocol '{pname}': instance host is not "
                             f"up — step '{member}' dropped.",
                             instance=seat)
            return {"error": "instance host down"}
        line = (f"[task {br['id']}] protocol {pname} step | intent "
                f"{member} | input: {user_input.strip() or '(none)'}")
        procs = self._proc_names(self.store.intent(member) or {})
        if not procs:
            inst.enqueue_step(line, member=member)
            return {"ok": True, "task": br["id"], "member": member}

        # Member-step prelude (user ruling 2026-08-24, overturns
        # v18's "members aren't supported"): the engine runs the
        # prelude first, materials land in the **bracket's task
        # directory**, and the step envelope's tail carries a
        # materials pointer (the booklet seat's home
        # additionalDirectories covers runtime, so it's readable);
        # on failure, tell the person and don't deliver the step —
        # the bracket stays open, the member isn't suspended (same
        # rule as a physical-layer fault). Runs on a background
        # thread: the trigger entry point doesn't carry the 30s
        # hard timeout.
        def _run(tid=br["id"], line=line, procs=tuple(procs),
                 input_=user_input.strip()):
            td = self._task_dir(tid)
            mats: list[dict] = []
            for n in procs:
                spec = defaults.PHYS_PROCEDURES.get(n)
                entry = (Path(__file__).parent / "kernel" / "procs"
                         / spec["entry"]) if spec else None
                if entry is None or not entry.is_file():
                    ok, err, got = False, (f"procedure '{n}' not in "
                                           f"the engine library"), []
                else:
                    ok, err, got = procrun.run_step(
                        entry, td, input_=input_,
                        timeout=defaults.PROC_TIMEOUT_S,
                        say_max=defaults.PROC_SAY_MAX)
                if not ok:
                    self.journal.row("procedure", "step-prelude-failed",
                                    task=tid, intent=member, proc=n,
                                    why=str(err)[:300])
                    self._say_engine(
                        f"Step '{member}' not delivered: prelude "
                        f"procedure '{n}' failed — {err}. "
                        f"Physical-layer fault; the bracket stays "
                        f"open — fix the environment and press the "
                        f"key again.", instance=seat)
                    return
                mats += got
                self.journal.row("procedure", "prelude", task=tid,
                                intent=member, proc=n)
            ptrs = [m["path"] for m in mats if m.get("kind") == "file"]
            if ptrs:
                line += " | materials: " + ", ".join(ptrs)
            elif mats:
                line += (" | materials: text entries appended to "
                         "materials.jsonl in the task dir")
            inst.enqueue_step(line, member=member)
        threading.Thread(target=_run, daemon=True,
                         name=f"step-prelude-{member}").start()
        return {"ok": True, "task": br["id"], "member": member,
                "prelude": list(procs)}

    def _seat_approve(self, seat: str, label: str) -> dict:
        """The Approve key's generic half: answers the newest card
        with options in that seat's card stream (the first option
        is the affirmative one — allow / the first choice). An
        executor's approve doesn't route through key wiring, it
        routes through the card; this key is the card's physical
        shortcut face."""
        with self._card_lock:
            cands = [c for c in self._cards.values()
                     if c.get("instance") == seat and c.get("options")]
            card = max(cands, key=lambda c: c["id"]) if cands else None
        if card is None:
            self._say_engine(f"{label}: nothing waiting for approval.",
                             instance=seat)
            return {"error": "nothing pending"}
        opt = card["options"][0]
        self.journal.row("xgate", "approve-key", seat=seat,
                        card=card["id"], picked=opt.get("label"))
        self._on_card_answer(card["id"], str(opt.get("action") or ""),
                             opt.get("data"))
        # Making multiple-cards-pending explicit (live-fire
        # precedent 2026-08-23: "I approved it, why is the old one
        # still there" — the key always answers the newest card,
        # and however many remain must be said out loud, not
        # silently starved)
        with self._card_lock:
            left = sum(1 for c in self._cards.values()
                       if c.get("instance") == seat and c.get("options"))
        if left:
            self._say_engine(f"{label}: approved "
                             f"'{card.get('title') or card['id']}' — "
                             f"{left} more card(s) still waiting on "
                             f"this seat (press again, or answer on "
                             f"the panel).")
        return {"ok": True, "card": card["id"],
                "picked": opt.get("label"), "left": left}

    def _proto_approve(self, pname: str) -> dict:
        return self._seat_approve(defaults.XPROTO_PREFIX + pname,
                                  f"Protocol '{pname}'")

    def _solo_cancel(self) -> dict:
        """Solo Cancel key (user ruling 2026-08-23): force-
        interrupts the newest in-flight standalone intent order —
        reap kills the process, the task is judged cancelled
        directly, the chain flag drops. **Doesn't go through the
        settle edge**: a forced interrupt isn't a failure, it
        shouldn't fire off surgery's auto-replay."""
        seat = defaults.XPROTO_PREFIX + defaults.XSOLO_NAME
        t = next((x for x in self.store.tasks_recent(30)
                  if x.get("executor") == seat
                  and x["status"] in ("running", "gated", "queued")),
                 None)
        if t is None:
            self._say_engine("Solo cancel: nothing in flight.")
            return {"error": "nothing running"}
        inst = self._xhosts.get(defaults.XSOLO_NAME)
        if inst is not None:
            try:
                inst.reap(t["id"])
            except Exception:
                pass
        self.store.task_update(t["id"], status="cancelled")
        self.store.chain_cancel(t["chain_id"], actor="deck")
        with self._card_lock:
            stale = [c["id"] for c in self._cards.values()
                     if c.get("task") == t["id"]]
        for cid in stale:
            self._card_close(cid, "cancelled")
        self.journal.row("chain", "force-cancel", task=t["id"],
                        intent=t.get("intent"), by="deck")
        self._say_engine(f"Cancelled task {t['id']} "
                         f"('{t.get('intent') or '-'}') — process "
                         f"reaped, no auto-replay.")
        self._task_bcast()
        return {"ok": True, "task": t["id"], "intent": t.get("intent")}

    def _proto_interrupt(self, pname: str) -> dict:
        """Interrupt key: interrupts the instance host's current
        turn (ESC passes straight through — identical to the user
        pressing Esc by hand on that terminal)."""
        seat = defaults.XPROTO_PREFIX + pname
        inst = self._xhosts.get(pname)
        if not (isinstance(inst, ProtoInstance) and inst.alive()):
            self._say_engine(f"Protocol '{pname}': no live instance to "
                             f"interrupt.")
            return {"error": "no live instance"}
        inst.write_raw("\x1b")
        self.journal.row("protocol", "interrupt", intent=pname)
        self._say_engine(f"Protocol '{pname}': interrupt sent.",
                         instance=seat)
        return {"ok": True}

    def _proto_shutdown(self, pname: str, force: bool = False) -> dict:
        """Shutdown key: **the wrap-up ceremony** (user ruling
        2026-08-24: ·收 made real) — if the bracket is open and the
        seat is alive, first deliver the system ·收 step (the
        booklet's declared wrapup, defaulting to wrapping up
        whatever's in flight), wait for step_done(·收) or the grace
        clock, **then** settle the account + graceful seat exit
        (ESC + /exit, tree-kill as fallback) + the window self-
        closes. Pressing Shutdown again = force (skip the wait);
        engine shutdown's cascade also forces it (a full-machine
        shutdown doesn't pay the 45s-per-booklet cost). The
        household stays put — the next Start revives it in place."""
        seat = defaults.XPROTO_PREFIX + pname
        inst0 = self._xhosts.get(pname)
        if pname in self._wrapping:
            # pressing again while the ceremony is in flight = force: wake the teardown thread to finish immediately
            if isinstance(inst0, ProtoInstance):
                inst0.wrap_evt.set()
            self.journal.row("protocol", "wrapup-forced", intent=pname)
            return {"ok": True, "note": "forcing close"}
        br = self._bracket_of(pname)
        if (br is not None and not force
                and isinstance(inst0, ProtoInstance) and inst0.alive()):
            self._wrapping.add(pname)
            proto = self.store.proto_get(pname) or {}
            wtxt = (str(proto.get("wrapup") or "").strip()
                    or defaults.PROTO_WRAP_DEFAULT)
            inst0.wrap_evt.clear()
            inst0.enqueue_step(
                f"[task {br['id']}] step ·收 | {wtxt} — when done, call "
                f"step_done(member=\"·收\"); the seat closes right "
                f"after (grace "
                f"{int(defaults.PROTO_WRAP_GRACE_S)}s).", member="·收")
            self.journal.row("protocol", "wrapup", intent=pname,
                            task=br["id"])
            self._say_engine(f"Protocol '{pname}': wrap-up step (·收) "
                             f"delivered — closing after it settles; "
                             f"press Shutdown again to force.",
                             instance=seat)

            def _ceremony(inst=inst0):
                inst.wrap_evt.wait(defaults.PROTO_WRAP_GRACE_S)
                try:
                    if self._bracket_of(pname) is not None:
                        self._proto_close(pname, by="deck")
                except Exception:
                    pass
                self._wrapping.discard(pname)
                self._xhosts.pop(pname, None)
                try:
                    inst.stop(graceful=True)
                    self.journal.row("protocol", "shutdown",
                                    intent=pname)
                except Exception:
                    pass
                try:
                    self.channel.close_flow(seat)
                except Exception:
                    pass            # a dead teardown thread isn't held liable (the engine may have already changed terms)

            threading.Thread(target=_ceremony, daemon=True,
                             name=f"xwrap-{pname}").start()
            return {"ok": True, "task": br["id"], "note": "wrap-up first"}
        out = {"ok": True}
        if br is not None:
            out = self._proto_close(pname, by="deck")
        inst = self._xhosts.pop(pname, None)

        def _down():
            try:
                if isinstance(inst, ProtoInstance):
                    inst.stop(graceful=True)
                    self.journal.row("protocol", "shutdown",
                                    intent=pname)
                self.channel.close_flow(seat)
            except Exception:
                pass                # a dead teardown thread isn't held liable (the engine may have already changed terms)

        threading.Thread(target=_down, daemon=True,
                         name=f"xdown-{pname}").start()
        return out

    def _engine_shutdown(self) -> dict:
        """Engine Shutdown key (user ruling 2026-08-23): closing
        sidecar cascades to close all instances (each goes
        graceful, windows close via flow_close, the hub self-closes
        when its last tab is removed), then sidecar itself also
        exits cleanly via ESC + /exit, and finally the engine wraps
        up (run()'s teardown tree-kills as fallback). The HTTP
        receipt returns immediately; teardown runs on a background
        thread."""
        if self._draining:
            # Re-entry while teardown is in flight (key pressed
            # repeatedly / toggle probes and resends because it
            # still reports as responsive): idempotent, doesn't
            # open a second teardown thread
            return {"ok": True, "note": "already draining"}
        self._draining = True
        if self.journal is not None:
            self.journal.row("engine", "shutdown-key")

        def _down():
            try:
                insts = [i for i in list(self._xhosts.values())
                         if isinstance(i, ProtoInstance)]
                for p in [n for n, i in list(self._xhosts.items())
                          if isinstance(i, ProtoInstance)]:
                    # Full-machine shutdown: skip the wrap-up
                    # ceremony (don't pay the 45s-per-booklet cost),
                    # the seat still goes graceful (ESC + /exit)
                    self._proto_shutdown(p, force=True)

                # Each seat's teardown thread goes graceful on its
                # own; poll a truth value instead of sleeping a
                # full beat (teardown sped up 2026-08-24): move on
                # as soon as all are dead, the cap is still the
                # grace clock + 1
                def _any_alive():
                    for i in insts:
                        try:
                            if i.alive():
                                return True
                        except Exception:
                            pass
                    return False

                t0 = time.monotonic()
                while (time.monotonic() - t0
                       < defaults.PROTO_EXIT_GRACE_S + 1.0
                       and _any_alive()):
                    time.sleep(0.2)
                if self.host is not None:
                    try:
                        if self.host.alive():
                            self.host.write_raw("\x1b")
                            time.sleep(0.3)
                            self.host.inject_chat("/exit")
                            t0 = time.monotonic()
                            while (time.monotonic() - t0
                                   < defaults.PROTO_EXIT_GRACE_S
                                   and self.host.alive()):
                                time.sleep(0.2)
                    except AttributeError:
                        pass            # fake host (test) face is incomplete
            except Exception:
                pass                    # a dead teardown thread isn't held liable
            self._stop.set()

        threading.Thread(target=_down, daemon=True,
                         name="engine-down").start()
        return {"ok": True, "note": "shutting down"}

    def _open_browser(self, url: str, label: str) -> None:
        """Open a browser window (degrade rule: opening a window is
        never load-bearing — failures go to the journal, never re-
        raised). Live-fire precedent 2026-08-22: `cmd /c start`
        expands the URL's %XX sequences as environment variables
        and eats them (%C2%B7 → empty) — connect straight to
        msedge.exe, falling back to the default browser tab
        (degrading without losing the window)."""
        try:
            exe = next((str(p) for p in (
                Path(os.environ.get("ProgramFiles(x86)", "C:/"))
                / "Microsoft/Edge/Application/msedge.exe",
                Path(os.environ.get("ProgramFiles", "C:/"))
                / "Microsoft/Edge/Application/msedge.exe",
                Path(os.environ.get("LocalAppData", "C:/"))
                / "Microsoft/Edge/Application/msedge.exe",
            ) if p.is_file()), None)
            if exe:
                subprocess.Popen([exe, f"--app={url}"])
            else:
                import webbrowser
                webbrowser.open(url)
            self.journal.row("protocol", "window", intent=label, url=url,
                            via=("edge-app" if exe else "browser"))
        except Exception as e:
            self.journal.row("protocol", "window-error", intent=label,
                            err=repr(e)[:120])

    def _open_hub_at_boot(self) -> None:
        """Auto-open the hub window at boot (user ruling 2026-08-23,
        late night). Probes flow_alive after a few seconds' delay:
        the hub window left over from the previous engine term
        reconnects on its own within the 2s reconnect loop — if it
        reconnects, don't open a second one."""
        if not self.spawn_host:
            return
        def _go():
            if self.channel.flow_alive(defaults.HUB_SEAT):
                # Old window already reconnected, don't open a
                # second one — log it (live-fire precedent
                # 2026-08-23: skipping silently reads as "auto-open
                # didn't take effect")
                self.journal.row("protocol", "hub-boot",
                                note="old window reconnected — reusing")
                return
            self._flow_opened[defaults.HUB_SEAT] = time.monotonic()
            self._open_browser(
                f"http://127.0.0.1:{self.http_port}/hub", "hub-boot")
        threading.Timer(4.0, _go).start()

    def _open_flow_window(self, pname: str) -> None:
        """This instance's card-stream face. Window shape (user
        ruling 2026-08-23): all instances collect into **one single
        hub window**, one seat per tab — if the hub is alive, send
        it a flow_open frame to add/switch tabs, no more one-seat-
        one-window."""
        if not self.spawn_host:
            return                      # hostless engine (test/embed) doesn't open windows
        seat = defaults.XPROTO_PREFIX + pname
        if self.channel.flow_alive(defaults.HUB_SEAT):
            self.channel.flow_open(seat)    # hub is present: add a tab / switch to it
            return
        if self.channel.flow_alive(seat):
            return                      # old single-seat-window shape still alive: don't open again
        now = time.monotonic()
        if now - self._flow_opened.get(defaults.HUB_SEAT, 0.0) < 8.0:
            return                      # debounce: the hub window is still starting up
        self._flow_opened[defaults.HUB_SEAT] = now
        self._open_browser(f"http://127.0.0.1:{self.http_port}/hub?i="
                           + urllib.parse.quote(seat), pname)

    def _on_trigger(self, q: dict) -> dict:
        """The engine side of /trigger (M26 §1 binding flow): a key
        = an HTTP request, the URL carries its own routing — one-
        way intent triggers / protocol's four ops / member slots.
        The Elgato native app is the UI now; the padbridge/bind
        slot table retires to a fallback from here on."""
        intent = str(q.get("intent") or "").strip()
        pname = str(q.get("protocol") or "").strip()
        op = str(q.get("op") or "").strip()
        member = str(q.get("member") or "").strip()
        engine_op = str(q.get("engine") or "").strip()
        user_input = str(q.get("input") or "").strip()
        if op != "status" and engine_op not in ("status", "task"):
            # status/task = the dial's polling probe (fires every
            # few seconds), not logged — the journal is an event
            # ledger, not a heartbeat recorder
            self.journal.row("deck", "trigger", intent=intent or None,
                            proto=pname or None, op=op or None,
                            member=member or None,
                            engine=engine_op or None)
        if engine_op:
            # Engine on/off key (user ruling 2026-08-23): if start
            # can reach the engine, the engine is already up (cold
            # start is covered by the plugin-side launch);
            # shutdown = close sidecar, cascading to close all
            # instances, the engine then exits on its own.
            if engine_op == "start":
                self._say_engine("Engine already running — sidecar "
                                 "is up.")
                return {"ok": True, "note": "already running"}
            if engine_op == "shutdown":
                return self._engine_shutdown()
            if engine_op == "status":
                # Status-bar half (user ruling 2026-08-23): read-
                # only probe, status = a single word (the bar text
                # strip only takes one word)
                rows = self.store.protos(status="provisioned",
                                         subtype="interactive")
                return {"ok": True,
                        "status": ("draining" if self._draining
                                   else "up"),
                        "draining": self._draining,
                        "open": sum(1 for p in rows
                                    if self._bracket_of(p["name"])
                                    is not None),
                        "live": sum(1 for i in self._xhosts.values()
                                    if isinstance(i, ProtoInstance)
                                    and i.alive())}
            if engine_op == "task":
                # Task bar probe: the newest in-flight standalone
                # intent order (under the parallel rule there may
                # be more than one — show the newest, more counts
                # the rest)
                seat = defaults.XPROTO_PREFIX + defaults.XSOLO_NAME
                rows = [t for t in self.store.tasks_recent(30)
                        if t.get("executor") == seat]
                act = [t for t in rows if t["status"]
                       in ("running", "gated", "queued")]
                t = act[0] if act else (rows[0] if rows else None)
                return {"ok": True,
                        "name": (t.get("intent") if t else None),
                        "status": (t["status"] if t else None),
                        "more": max(0, len(act) - 1)}
            if engine_op == "approve":
                return self._seat_approve(
                    defaults.XPROTO_PREFIX + defaults.XSOLO_NAME,
                    "Solo")
            if engine_op == "cancel":
                return self._solo_cancel()
            return {"error": "engine op must be start|shutdown|status|"
                             "task|approve|cancel"}
        if intent:
            self._on_intent(intent, user_input, by="deck")
            return {"ok": True, "intent": intent}
        if not pname:
            return {"error": "need ?intent= or ?protocol="}
        if op == "status":
            # instance status bar (user ruling 2026-08-23): the
            # dial's polling read-only probe — open/closed,
            # account, seat liveness, pending-approval count, zero
            # side effects. Silent guard (no _say_engine): a dial
            # left over after a booklet retires fires every few
            # seconds — calling out to a person would just spam
            # the screen
            p = self.store.proto_get(pname)
            if (p is None or p["status"] != "provisioned"
                    or p["subtype"] != "interactive"):
                return {"error": f"no provisioned protocol '{pname}'"}
            br = self._bracket_of(pname)
            inst = self._xhosts.get(pname)
            seat = defaults.XPROTO_PREFIX + pname
            with self._card_lock:
                pending = sum(1 for cd in self._cards.values()
                              if cd.get("instance") == seat
                              and cd.get("options"))
            live = isinstance(inst, ProtoInstance) and inst.alive()
            step = (inst.step_name
                    if isinstance(inst, ProtoInstance) else None)
            step_state = (inst.step_state
                          if isinstance(inst, ProtoInstance) else None)
            # status = a single word (the bar text strip only takes
            # one word); priority: wrapping up > pending approval >
            # seat dead > step running > booklet open idle > booklet
            # closed
            draining = (pname in self._wrapping) or self._draining
            word = ("draining" if draining else
                    "await" if pending else
                    "down" if (br is not None and not live) else
                    "running" if (br is not None
                                  and step_state == "running") else
                    "idle" if br is not None else "closed")
            return {"ok": True, "status": word, "draining": draining,
                    "open": br is not None,
                    "task": br["id"] if br else None,
                    "live": live, "pending": pending,
                    "step": step, "step_state": step_state}
        if op == "start":
            return self._proto_start(pname, user_input)
        if op == "approve":
            return self._proto_approve(pname)
        if op == "interrupt":
            return self._proto_interrupt(pname)
        if op == "shutdown":
            return self._proto_shutdown(pname)
        if member:
            return self._proto_member(pname, member, user_input)
        return {"error": "need ?op=start|approve|interrupt|shutdown "
                         "or ?member="}

    def _admit(self, seat: str, prio: int, label: str) -> bool:
        """§2h order-admission rule: a seat's queue only accepts
        orders ≥ the queue's current highest tier — equal joins the
        line, higher cuts in (natural ordering), lower is refused
        outright (the refusal carries a signpost). gated doesn't
        occupy the queue; a lower-tier order already queued is
        grandfathered in and never evicted, only new orders get
        refused."""
        ceil = self.store.queue_ceiling(seat)
        if ceil is not None and prio < ceil:
            self._say_engine(
                f"'{label}' refused: seat {seat} queue holds higher-"
                f"priority work (tier {ceil} > this order's {prio}) — "
                f"trigger again after it settles.")
            self.journal.row("chain", "refused", intent=label,
                            reason="queue-priority", seat=seat)
            return False
        return True

    def _admit_spec(self, spec_name: str, label: str) -> bool:
        """Admits orders by chain type: find that chain's delivery
        seat (the assignee of its first deliver node), check
        against the spec's declared tier. A pure-gate chain (no
        deliver node) doesn't occupy the seat queue — it's released
        directly."""
        sp = self.store.spec(spec_name)
        if sp is None:
            return True
        seat = next((s["assignee"] for s in sp["steps"]
                     if s.get("kind") == "deliver"), None)
        if seat is None:
            return True
        return self._admit(seat, int(sp["priority"]), label)

    def _on_intent(self, name: str, user_input: str = "",
                   by: str = "ime") -> None:
        """Explicit trigger (IME pin-select / deck grid tap): the UI
        decides whether to fire; the engine only accepts an
        explicit (name, input). The protocol face routes first (M20
        §2)."""
        if self._protocol_route(name, user_input, by):
            return
        it = self.store.intent(name)
        if (it is None or it["owner"] != self.module
                or it["status"] != "provisioned"):
            if (it is not None and it["owner"] == self.module
                    and it["status"] == "draft"):
                self._say_engine(f"'{name}' is in draft (rework / "
                                 f"pending approval) — not triggerable "
                                 f"yet.")
            return
        # The enforcement point for "entering a booklet locks it"
        # (user ruling 2026-08-17) has moved up with M26: member
        # words are always caught by _protocol_route (bracket not
        # open = lazy spawn opens the booklet); a name that reaches
        # here must be a standalone intent.
        if any(str(x.get("spec") or "") == "手术"
               and x.get("intent") == name
               for x in self.store.queue_view()):
            # §2g×v9 suspension lock (single-item granularity): surgery is on the table, this intent's trigger is refused
            self._say_engine(f"'{name}' is under surgery — settlement "
                             f"auto-replays; try again later.")
            self.journal.row("chain", "refused", intent=name,
                            reason="surgery-lock")
            return
        busy = self.store.inflight(name)
        if busy:
            self._say_engine(f"'{name}' is still running (task "
                             f"{busy[0]['id']}, {busy[0]['status']}) — "
                             f"new trigger refused, wait for "
                             f"settlement.")
            self.journal.row("chain", "refused", intent=name,
                            reason="in-flight", task=busy[0]["id"])
            return
        if not self._admit_spec(f"deliver:{name}", name):   # §2h order-admission rule
            return
        # issuer=user (ruling 2026-08-10): the IME is an explicit
        # human action, the chain is logged on the person's
        # ledger — the person owns the surface, bypassing the head
        # check
        t = self.store.chain_start(f"deliver:{name}", issuer="user",
                                   intent=name,
                                   payload=user_input.strip() or None)
        self._touch(name, defaults.SCORE_TRIGGER)
        self.journal.row("chain", "start", spec=t["spec"], task=t["id"],
                        by=by, input=user_input.strip() or None)
        self._task_bcast()

    def _touch(self, name: str, score: float = 0.0) -> None:
        """touch = using something puts it in the container (the
        container rule's unified verb: trigger/get/update/
        provisioning have no special case; something already in the
        container gets bumped to the tail = LRU). Scoring is a
        separate ledger: score>0 is only for "calling by name"
        behavior (trigger gets full score, get gets a fraction).
        Meta exposure and the engine's internal read paths never go
        through here."""
        self.store.touch(name, score)
        it = self.store.intent(name)
        if (it is not None and it["owner"] == self.module
                and it["status"] == "provisioned"):
            self._hot.pop(name, None)
            self._hot[name] = None
            self._container_trim()

    def _container_trim(self) -> None:
        """Container rule (§2m v10; pure LRU since the bind section
        retired on 2026-08-23): total kept ≤ CONTAINER_CAP — over
        the cap, evict the least-recently-used member (**eviction
        only, never intrusion**: leaving the container != leaving
        the library — the cold library is still searchable and
        triggerable, using it again brings it back into the
        container). Evictions go to the journal, not the chat face
        (a degraded recommendation face is expected behavior, not
        an incident)."""
        over = len(self._hot) - defaults.CONTAINER_CAP
        if over <= 0:
            return
        for n in list(self._hot)[:over]:
            del self._hot[n]
            self.journal.row("intent", "container-evict", intent=n,
                            n=len(self._hot))

    def _workset_reset(self) -> None:
        """Container handover (container rule): the only
        reorganization point is a session's start — since the bind
        section retired (user ruling 2026-08-23, the soft
        deck/slot table retires along with the Elgato native UI)
        the container starts at zero each session and its usage set
        accumulates from scratch; recommendations and recall fall
        back to vector's multi-path retrieval (the whole library,
        container not consulted)."""
        self._hot = {}
        self.journal.row("intent", "container-reset", n=0)

    def _intents_frame(self) -> dict:
        """IME dictionary frame (only the word list remains after the bind section retired)."""
        return {"type": "intents", "rows": self._intent_menu()}

    def _intent_menu(self) -> list[dict]:
        """IME's dictionary face — the sidecar half (user ruling
        2026-08-23: **lists only non-protocol intents**). Member
        words belong to each protocol's own flow-window IME
        (_flow_intents_frame); opening/closing a booklet goes
        through the deck's Start/Shutdown keys, and the ·启/·收
        virtual words leave the dictionary (bound grid cells still
        recognize them — the trigger grammar isn't retired, it's
        just no longer exposed)."""
        return [{"name": it["name"], "title": it.get("title") or ""}
                for it in self.store.intents(owner=self.module,
                                             status="provisioned")
                if not it.get("proto")]

    def _flow_intents_frame(self, instance: str) -> dict | None:
        """IME's dictionary face — the seat half (user ruling
        2026-08-23): an executor window's word list has **only its
        own protocol's member words**. Pulled by the channel when
        hello reports the household, one copy per window."""
        if not (isinstance(instance, str)
                and instance.startswith(defaults.XPROTO_PREFIX)):
            return None
        pname = instance[len(defaults.XPROTO_PREFIX):]
        p = self.store.proto_get(pname)
        if p is None:
            return None
        rows = []
        for m in (p.get("members") or []):
            it = self.store.intent(str(m)) or {}
            rows.append({"name": str(m),
                         "title": it.get("title") or ""})
        return {"type": "intents", "instance": instance, "rows": rows}

    def _replay_for(self, instance: str | None = None) -> str:
        """Terminal replay is fetched per seat (M26: a flow window's terminal drawer belongs to that instance)."""
        if instance and instance != self.module:
            pname = (instance[len(defaults.XPROTO_PREFIX):]
                     if instance.startswith(defaults.XPROTO_PREFIX)
                     else instance)
            inst = self._xhosts.get(pname)
            return (inst.replay()
                    if isinstance(inst, ProtoInstance) else "")
        return self.host.replay() if self.host is not None else ""

    def _on_cli_in(self, data: str, instance: str | None = None) -> None:
        if instance and instance != self.module:
            # M26: keystrokes from a flow window's terminal drawer
            # pass straight through to that instance (wizard
            # answers, permission dialogs all go through this
            # path — identical to typing by hand on that terminal)
            pname = (instance[len(defaults.XPROTO_PREFIX):]
                     if instance.startswith(defaults.XPROTO_PREFIX)
                     else instance)
            inst = self._xhosts.get(pname)
            if isinstance(inst, ProtoInstance) and inst.alive():
                inst.write_raw(data)
            return
        if self.host is not None:
            # a person on the terminal in person = waiting is over (card-withdrawal rule) + rearm the stillness clock (M13)
            self._last_input = time.monotonic()
            self._stall_fired = False
            self.host.write_raw(data)
            self._close_wait_cards("cli-engaged")

    def _on_cli_size(self, cols: int, rows: int,
                     instance: str | None = None) -> None:
        """True terminal responsiveness (user ruling 2026-08-23):
        the frontend measures cols×rows and reports it up, the
        engine resizes the ConPTY, and the TUI reflows on its own.
        Clamped to a sane range — a number reported by the UI is
        never trusted directly."""
        cols = max(40, min(400, int(cols)))
        rows = max(8, min(200, int(rows)))
        if instance and instance != self.module:
            pname = (instance[len(defaults.XPROTO_PREFIX):]
                     if instance.startswith(defaults.XPROTO_PREFIX)
                     else instance)
            inst = self._xhosts.get(pname)
            if isinstance(inst, ProtoInstance) and inst.alive():
                inst.resize(cols, rows)
            return
        if self.host is not None:
            try:
                self.host.resize(cols, rows)
            except AttributeError:
                pass                # fake host (test) has no resize face

    def _on_stop(self) -> None:
        self._stop.set()

    def _on_approve(self, tid: int) -> None:
        """The gate's approve verb: it only proceeds once a person
        presses it (a gate-type validator is this hand — there's
        no fail, not approving just stops, cancel is a separate
        path). Provisioning is no longer a chain-completion special
        case: the qual·初生 gate node hangs effect ok:provision,
        settle stamps everything through one unified route."""
        t = self.store.task(tid)
        if t is None or t["status"] != "gated":
            return
        self.store.task_update(tid, status="done")
        self.journal.row("chain", "gate-approved", task=tid, gate=t["gate"])
        self._settle(t, "ok")
        self._task_bcast()
    def _on_retry(self, tid: int, reason: str = "") -> None:
        """retry key (ruling 2026-08-10/11): human expresses
        dissatisfaction + typing becomes reason (same rule as intent,
        lands in the typing window) — opens a retry chain; validation
        duty is pushed onto sidecar. done is terminal — retry is a
        new task carrying last round's context. retry is always the
        human's verb (retrying the whole pipeline); machine failures
        go through the debug chain — never mixed."""
        t = self.store.task(tid)
        if t is None or not str(t.get("spec") or "").startswith("deliver:"):
            self._say_engine(f"retry: task {tid} is not an intent "
                             f"execution ring — cannot retry.")
            return
        if t["status"] not in ("done", "failed"):
            self._say_engine(f"retry: task {tid} has not reached a "
                             f"final state ({t['status']}) — cannot "
                             f"retry.")
            return
        name = t["intent"]
        # R5 two-round ruling (2026-08-23): retry always opens a
        # **retry bracket** and casts to sidecar (fulfilled directly,
        # no longer entering executor) — the old rule "x· single
        # retry = open 手术 (surgery)" retires with it; 手术 now has
        # only one entry point, the failed-proposal card.
        busy = self.store.inflight(name)
        if busy:
            self._say_engine(f"'{name}' still has a ring in flight "
                             f"(task {busy[0]['id']}) — retry after it "
                             f"lands.")
            return
        if not self._admit_spec("retry", f"retry:{name}"):  # §2h intake rule
            return
        rt = self.store.chain_start("retry", issuer="user", intent=name,
                                    payload=reason.strip() or None,
                                    origin=tid)
        self._touch(name, defaults.SCORE_TRIGGER)
        self.journal.row("chain", "retry", task=rt["id"], of=tid,
                        intent=name, reason=reason.strip() or None)
        self._task_bcast()

    def _consolidate_offer(self, kind: str, name: str, tid: int,
                           extra: str = "") -> None:
        """Consolidate ring, reshaped (user ruling 2026-08-25): a
        retry settling / a booklet closing is the moment to ask
        "fold the lesson into the asset?". The card is kind
        **offer** — a decision card that is NOT swept by terminal
        engagement (_close_wait_cards only sweeps perm/stall/ask;
        live-fire 2026-08-25: the retry acceptance card lost its
        buttons the moment the user typed). Approving suspends the
        asset and opens a consolidate order on the sidecar; the
        registration gate is what revives it."""
        if not name:
            return
        self._card_open(
            "offer", f"Consolidate '{name}'?",
            (extra + "\n" if extra else "")
            + f"Consolidate — the {kind} is suspended (triggers "
              f"refused), the sidecar folds the lesson into its "
              f"declaration, and the revision re-registers through "
              f"your gate; your approval there revives it.\n"
              f"Skip — leave the {kind} as it is.",
            options=[{"action": "consolidate",
                      "data": json.dumps({"kind": kind, "name": name,
                                          "task": tid},
                                         ensure_ascii=False),
                      "label": "Consolidate"},
                     {"action": "dismiss", "label": "Skip"}],
            task=tid)

    def _consolidate_go(self, d: dict) -> None:
        """Consolidate approved: suspend the asset (off keys/IME,
        triggers refused via the existing provisioned-only guards)
        and open the consolidate order on the sidecar seat. Revival
        is the registration approve — the existing draft →
        provisioned handlers, zero new machinery."""
        kind = str(d.get("kind")
                   or ("protocol" if d.get("proto") else "intent"))
        name = str(d.get("name") or d.get("proto") or "")
        origin = d.get("task")
        if not name:
            return
        if not self._admit_spec("consolidate", f"consolidate: {name}"):
            return
        if kind == "protocol":
            if (self.store.proto_get(name) or {}).get("status") \
                    != "provisioned":
                # Say why the button did nothing (audit 2026-08-25
                # §4-interpretability): a silent return reads as a
                # dead click — the asset moved on while the card sat
                self._say_engine(f"Consolidate '{name}' skipped: "
                                 f"no longer provisioned (already "
                                 f"suspended or retired).")
                return
            self.store.proto_set_status(name, "draft")
            self._compile_deck_plugin()
        else:
            it = self.store.intent(name)
            if it is None or it.get("status") != "provisioned":
                self._say_engine(f"Consolidate '{name}' skipped: "
                                 f"no longer provisioned (already "
                                 f"suspended or retired).")
                return
            self.store.intent_revise(name, status="draft")
            self._hot.pop(name, None)
            self._solo_refresh()
            self._compile_intents_keyset()
        ct = self.store.chain_start(
            "consolidate", issuer="user", intent=name,
            origin=int(origin) if origin else None)
        self.journal.row("chain", "consolidate-open", task=ct["id"],
                        intent=name, ckind=kind)
        self._say_engine(f"Consolidate opened (task {ct['id']}): "
                         f"{kind} '{name}' suspended — the sidecar "
                         f"folds the lesson in; your approval of the "
                         f"re-registration revives it.")
        self._task_bcast()

    def _consolidate_unsuspend(self, name: str) -> None:
        """The inverse of _consolidate_go's suspend: a consolidate
        ring that dies before it re-registers hands the asset back
        (nothing was compiled — the library still serves the old
        version). Only a **draft** asset is revived: a consolidate
        that already finished leaves its asset in draft on purpose,
        waiting on its own registration gate, and that is none of
        cancel's business."""
        if not name:
            return
        if (self.store.intent(name) or {}).get("status") == "draft":
            self.store.intent_revise(name, status="provisioned")
            self._solo_refresh()
            self._compile_intents_keyset()
        elif (self.store.proto_get(name) or {}).get("status") \
                == "draft":
            self.store.proto_set_status(name, "provisioned")
            self._compile_deck_plugin()

    def _on_cancel(self, cid: int) -> None:
        """Unified cancel (user ruling 2026-08-25, supersedes the
        2026-08-10 soft law): cancel = **interrupt the running ring
        NOW + void the chain** — one meaning on every surface, and
        never a failure (no settle edge, no surgery). Per seat:
        x·solo reaps the process (same as the deck Solo·Cancel
        key); a sidecar ring gets a drop notice injected; a
        cancelled consolidate un-suspends its asset (nothing
        compiled yet — the library still serves the old version).
        Queued/gated rings are voided by chain_cancel (they occupy
        no seat). Protocol brackets refuse — their close IS the
        Shutdown key (Interrupt for the in-flight step). Internal
        chains refuse (unchanged). Root incident: the old soft law
        left a cancelled retry running forever (timeout-exempt,
        conversational), pinning the seat's error-tier ceiling and
        refusing every new order — live-fire deadlock 2026-08-25."""
        rings = self.store.chain(cid)
        if not rings or self.store.chain_cancelled(cid):
            return
        if rings[0]["priority"] >= defaults.PRIORITY_INTERNAL:
            self._say_engine(f"chain {cid} is internal (maintenance "
                             f"tier) — cannot cancel.")
            return
        if any(str(r.get("spec") or "").startswith("protocol:")
               for r in rings):
            self._say_engine(f"chain {cid} is a protocol bracket — "
                             f"close it with the Shutdown key "
                             f"(Interrupt for the in-flight step); "
                             f"cancel does not apply to brackets.")
            return
        n = self.store.chain_cancel(cid, actor="user")
        solo_seat = defaults.XPROTO_PREFIX + defaults.XSOLO_NAME
        dropped: list = []
        for r in rings:
            spec = str(r.get("spec") or "")
            if (spec == "consolidate"
                    and r["status"] in ("queued", "gated")):
                # audit 2026-08-25: chain_cancel voids exactly the
                # queued/gated rings, and the running-only branch
                # below never sees them — a consolidate cancelled
                # before delivery has to hand its asset back here, or
                # it stays suspended in draft forever with no notice
                # and no path back but a manual re-submit.
                self._consolidate_unsuspend(str(r.get("intent") or ""))
            if r["status"] != "running":
                continue
            if r.get("executor") == solo_seat:
                inst = self._xhosts.get(defaults.XSOLO_NAME)
                if inst is not None:
                    try:
                        inst.reap(r["id"])
                    except Exception:
                        pass
            elif r.get("executor") == self.module:
                if (self.host is not None and self.host.alive()
                        and self.host.trusted()):
                    self._inject(f"[task {r['id']}] cancelled by "
                                 f"the user — drop this {spec} "
                                 f"order; no task_done needed.")
                if spec == "consolidate":
                    self._consolidate_unsuspend(
                        str(r.get("intent") or ""))
            self.store.task_update(r["id"], status="cancelled")
            dropped.append(r)
            with self._card_lock:
                stale = [c["id"] for c in self._cards.values()
                         if c.get("task") == r["id"]]
            for scid in stale:
                self._card_close(scid, "cancelled")
            self.journal.row("chain", "force-cancel", task=r["id"],
                            intent=r.get("intent"), by="cancel")
        self._say_engine(
            f"chain {cid} cancelled ({n} queued/gated ring(s) voided"
            + (f"; running task {dropped[0]['id']} interrupted — "
               f"no auto-replay" if dropped else "") + ").")
        # cancel receipt (context sync): an agent issuer's future
        # obligation must be zeroed out; a user issuer's receipt is
        # just the line above
        issuer = rings[0]["issuer"]
        delivered = False
        if (issuer not in ("user", "engine") and self.host is not None
                and self.host.alive() and self.host.trusted()):
            self._inject(defaults.CANCEL_LINE.format(
                cid=cid, spec=rings[0]["spec"]))
            delivered = True
        self.journal.row("chain", "cancelled", chain=cid, voided=n,
                        issuer=issuer, notice=delivered)
        self._task_bcast()

    def _chains_frame(self) -> dict:
        """Three faces, one frame: ring stream (rows), chain ledger
        (ledger — globally visible, issuer-scoped), subtask queue
        (queue — executor side). CASELAW 6: one poisoned row must
        not choke the whole frame — degrade and return, log the
        cause loudly to the journal."""
        try:
            rows = self.store.tasks_recent(30)
            specs: dict = {}
            for r in rows:
                # Promote for display: a procedure ring carries
                # ref/kind (a ring is the chain's formal ring)
                name = r.get("spec")
                if name not in specs:
                    specs[name] = self.store.spec(name) if name else None
                sp = specs[name]
                if sp and r["seq"] < len(sp["steps"]):
                    st = sp["steps"][r["seq"]]
                    r["kind"] = st["kind"]
                    if st.get("ref"):
                        r["ref"] = st["ref"]
                if r.get("status") == "gated":
                    # What's being approved, on the card itself
                    # (audit 2026-08-25 §4-correctness): the gate
                    # card used to read "waiting at a human gate"
                    # and nothing else — the submit summary was
                    # already written to the task dir's template.md,
                    # it just never traveled to the UI. Head only;
                    # the full text stays in the file.
                    tpl = (self.workspace / defaults.RUNTIME_DIRNAME
                           / "tasks" / str(r["id"]) / "template.md")
                    try:
                        r["detail"] = tpl.read_text(
                            encoding="utf-8", errors="replace")[:600]
                    except OSError:
                        pass
            return {"type": "chains", "rows": rows,
                    "ledger": self.store.chains_recent(30),
                    "queue": self.store.queue_view()}
        except Exception as e:
            if self.journal is not None:
                self.journal.row("chain", "frame-error", err=repr(e)[:200])
            return {"type": "chains", "rows": [], "ledger": [],
                    "queue": [], "error": repr(e)[:120]}

    def _task_bcast(self) -> None:
        self.channel.broadcast(self._chains_frame())

    # ---- Cockpit card plane (M13, docs/M13-COCKPIT.md: wait
    #      completeness + voice completeness — gate cards are
    #      derived by the UI from the chains frame, zero new engine
    #      surface; this only handles perm/stall/info and the
    #      future ask/notify) -------------------------------------

    def _cards_frame(self) -> dict:
        """One frame of on-rack cards (hello replay): late
        subscribers can still see what's waiting."""
        with self._card_lock:
            rows = [{k: v for k, v in c.items() if not k.startswith("_")}
                    for c in self._cards.values()]
        return {"type": "cards", "rows": rows}

    def _card_open(self, kind: str, title: str, body: str,
                   options: list | None = None,
                   task: int | None = None,
                   instance: str | None = None) -> dict:
        with self._card_lock:
            self._card_seq += 1
            card = {"type": "card", "id": self._card_seq,
                    "instance": instance or self.module,
                    "kind": kind, "title": title,
                    "body": body, "t": time.strftime("%H:%M:%S"),
                    "_born": time.monotonic()}
            if options:
                card["options"] = options
            if task is not None:
                card["task"] = task
            self._cards[card["id"]] = card
        self.journal.row("card", "open", id=card["id"], ckind=kind,
                        title=title, task=task)
        self.channel.broadcast({k: v for k, v in card.items()
                                if not k.startswith("_")})
        return card

    def _card_close(self, cid: int, why: str = "") -> None:
        with self._card_lock:
            card = self._cards.pop(cid, None)
        if card is None:
            return
        self.journal.row("card", "close", id=cid, ckind=card["kind"],
                        why=why or None)
        self.channel.broadcast({"type": "card_close", "id": cid})

    def _close_wait_cards(self, why: str,
                          kinds=("perm", "stall", "ask")) -> None:
        """Card-dismissal rule (live-fire revision 2026-08-12): stall
        is born of long quiet — any output is a real recovery →
        output dismisses it; **perm cards ignore output** — while a
        permission dialog hangs, the TUI still redraws sporadically
        (precedent: a card opened 1s ago got wrongly dismissed), and
        the dialog doesn't disappear on its own, so perm only
        dismisses on the human's answer (card_answer / terminal
        keystroke cli_in). **Gate-card exemption** (precedent
        2026-08-14: an x·solo permission-request card got swept away
        by cli-engaged, and the gate kept blocking into a wait
        nobody could answer) — an executor seat's perm/ask gate card
        is not a mirror of the host's dialog, terminal keystrokes are
        unrelated to it; any card with an event parked in _gates is
        only collected by card_answer / timeout."""
        with self._gate_lock:
            gated = set(self._gates)
        with self._card_lock:
            victims = [c["id"] for c in self._cards.values()
                       if c["kind"] in kinds and c["id"] not in gated]
        for cid in victims:
            self._card_close(cid, why)

    def _pty_tail(self) -> str:
        """Screen tail capture (body material for perm/stall cards):
        strip ANSI from replay, collapse blank lines, take the
        tail."""
        if self.host is None:
            return ""
        txt = _ANSI.sub("", self.host.replay()[-6000:])
        txt = txt.replace("\r\n", "\n").replace("\r", "\n")
        keep: list[str] = []
        for ln in (s.rstrip() for s in txt.split("\n")):
            if ln or (keep and keep[-1]):
                keep.append(ln)
        return "\n".join(keep)[-defaults.STALL_TAIL_CHARS:]

    def _feed(self, kind: str, text: str,
              instance: str | None = None) -> None:
        """An event line for the task feed (left-pane timeline) —
        not a card, no obligation to answer."""
        self.channel.broadcast({"type": "feed", "kind": kind, "text": text,
                                "instance": instance or self.module,
                                "t": time.strftime("%H:%M:%S")})

    def _inject(self, text: str) -> None:
        """Inject into the host + register a receipt (CASELAW 48:
        "sent" ≠ "landed"). Two rounds of live-fire precedent
        2026-08-15: a mid-flow CLI wizard swallowed the whole line,
        the engine only reported idle, and it silently deadlocked
        for 9 minutes. The engine doesn't try to recognize dialogs —
        it only verifies "did the words land": if the transcript
        directory has no new bytes within INJECT_ACK_S, open an info
        card with the screen tail."""
        if self.host is None or not self.host.alive():
            return
        self.host.inject_chat(text)
        self._inject_watch.append(
            {"wall": time.time(), "t": time.monotonic(),
             "t0": time.monotonic(),
             "brief": text.strip().replace("\n", " ")[:100]})

    def _inject_ack(self) -> None:
        """Receipt check inside the pump: batch-verify on due, open
        at most one card per batch (no screen flooding).

        Criterion revision (live-fire precedent 2026-08-15 late
        night; the mtime version was falsified: while a wizard eats
        a line the CLI itself is still writing the transcript, so
        there's always noise within 20s → false-positive acks).
        Strong criterion = **this exact line truly appears as a user
        row in the transcript** (a submission lands on disk;
        messages queued while the host is busy eventually get
        delivered and will show up too). If it can't be judged
        (transcript directory absent), don't alarm — fail-safe, not
        fail-noisy.

        busy deferral (CASELAW 60 addendum, 2026-08-17): an
        injection made mid-host-turn queues up without landing in
        the transcript yet — a false positive against the real
        baseline precedent. If due but not landed, and the
        transcript is still growing (mtime within
        INJECT_BUSY_QUIET_S of now = host still busy), extend the
        watch one more window and reverify; INJECT_ACK_MAX_S is the
        hard ceiling backstop (busy still can't wait forever — a
        line truly eaten by a wizard keeps the transcript moving
        too)."""
        if not self._inject_watch:
            return
        now = time.monotonic()
        due = [w for w in self._inject_watch
               if now - w["t"] >= defaults.INJECT_ACK_S]
        if not due:
            return
        self._inject_watch = [w for w in self._inject_watch
                              if now - w["t"] < defaults.INJECT_ACK_S]
        landed: list[str] = []
        t_mtime = 0.0                        # latest transcript mtime (busy criterion)
        try:
            d = prune_report.transcript_dir(
                instance_home(self.workspace, self.module))
            files = sorted(d.glob("*.jsonl"),
                           key=lambda f: f.stat().st_mtime)[-2:]
            if files:
                t_mtime = files[-1].stat().st_mtime
            for f in files:
                # Read only the tail window (injection just
                # happened — don't read the whole large transcript)
                tail = f.read_bytes()[-200_000:].decode(
                    "utf-8", "replace")
                for ln in tail.splitlines():
                    if '"type":"user"' not in ln.replace(" ", ""):
                        continue
                    try:
                        r = json.loads(ln)
                    except ValueError:
                        continue
                    if r.get("type") != "user":
                        continue
                    c = (r.get("message") or {}).get("content")
                    if isinstance(c, list):
                        c = " ".join(str(x.get("text") or "")
                                     for x in c if isinstance(x, dict))
                    if isinstance(c, str) and c:
                        # normalize the same way as brief (brief
                        # already folds newlines into spaces)
                        landed.append(c.replace("\n", " "))
        except OSError:
            landed = None                    # observation point unavailable — don't alarm
        lost = []
        host_busy = (time.time() - t_mtime) < defaults.INJECT_BUSY_QUIET_S
        # Long-think deferral (live-fire precedent 2026-08-23, a new
        # false-positive shape after opus/high): during extended
        # thinking the composer holds input and the transcript
        # writes zero bytes — the mtime criterion goes blind. Seeing
        # a thinking marker on the screen tail means the host is
        # alive and thinking, the line is queued — defer
        # indefinitely (a wizard/update screen never grows these
        # markers, so a truly-eaten line is never wrongly pardoned).
        tail = self._pty_tail()[-500:]
        thinking = any(m in tail for m in
                       ("thinking", "Musing", "esc to interrupt"))
        for w in due:
            key = w["brief"][:60]
            ok = landed is None or any(key in c for c in landed)
            if not ok and thinking:
                w["t"] = now
                self._inject_watch.append(w)
                self.journal.row("inject", "defer-thinking",
                                brief=w["brief"])
                continue
            if not ok and host_busy and (
                    now - w.get("t0", w["t"])
                    < defaults.INJECT_ACK_MAX_S):
                # Host still busy: the line is likely queued and
                # hasn't landed in the transcript yet — extend the
                # watch one more window and reverify, don't judge
                # lost (false-positive precedent: fired on the real
                # baseline five times)
                w["t"] = now
                self._inject_watch.append(w)
                self.journal.row("inject", "defer", brief=w["brief"])
                continue
            self.journal.row("inject", "ack" if ok else "lost",
                            brief=w["brief"])
            if not ok:
                lost.append(w["brief"])
        # No card for a lost line (user ruling 2026-08-25): the
        # terminal drawer shows the miss at a glance, and the
        # journal's inject/lost row keeps the evidence — a card on
        # top was noise (it also fired on queued-behind-a-form
        # lines that were not actually lost).

    def _gate_wait(self, kind: str, title: str, body: str,
                   options: list, timeout: float,
                   instance: str | None = None) -> dict | None:
        """§2i blocking arbitration (isomorphic to M18, native to
        the card-stream plane): open a card → an HTTP thread waits
        for the human's answer; timeout returns None (the card is
        collected along with it). The pump and other seats are not
        blocked.

        P1-a gate clock (live-fire precedent 2026-08-23): while the
        gate is open, mark that seat busy (the time-limit rule
        doesn't collect corpses); when the gate closes, re-stamp the
        seat's running order — time spent waiting on a human doesn't
        count against the machine clock. A's first trigger, proven
        in practice: a settlement card timed out at 5 minutes and
        was reaped in the same second its retry card batch arrived
        — live work got judged dead."""
        evt = threading.Event()
        if instance:
            with self._gate_lock:
                self._gate_busy[instance] = \
                    self._gate_busy.get(instance, 0) + 1
        try:
            card = self._card_open(kind, title, body, options=options,
                                   instance=instance)
            with self._gate_lock:
                self._gates[card["id"]] = {"evt": evt, "ans": None}
            got = evt.wait(timeout)
            with self._gate_lock:
                slot = self._gates.pop(card["id"], None)
            if not got:
                self._card_close(card["id"], "timeout")
                return None
            return (slot or {}).get("ans")
        finally:
            if instance:
                with self._gate_lock:
                    n = self._gate_busy.get(instance, 1) - 1
                    if n <= 0:
                        self._gate_busy.pop(instance, None)
                    else:
                        self._gate_busy[instance] = n
                for t in self.store.queue_view():
                    if (t["status"] == "running"
                            and t.get("executor") == instance):
                        # Gate-close re-stamp: task_update with no
                        # args = only refresh updated_at (the anchor
                        # of the time-limit rule) — time spent
                        # waiting on a human doesn't count against
                        # the machine clock
                        self.store.task_update(t["id"])

    def _on_card_answer(self, cid: int, action: str, data=None) -> None:
        """The card's answer verbs: key = the literal keystroke
        passes straight through to the PTY (perm's digits/Esc); line
        = the whole line gets injected (the two-beat discipline —
        stall's typed line, an unknown prompt's answer). Identical,
        character for character, to the user typing it in the
        terminal by hand (the zero-padding constitution); dismiss /
        anything else = collect the card. Answering always collects
        the card — no zombie cards left behind."""
        with self._card_lock:
            card = self._cards.get(cid)
        if card is None:
            return
        self.journal.row("card", "answer", id=cid, ckind=card["kind"],
                        action=action,
                        data=(str(data)[:80] if data is not None else None))
        with self._gate_lock:
            slot = self._gates.get(cid)
        if slot is not None:
            # §2i gate card: an answer wakes the waiting HTTP
            # thread, skip the rest of the branches
            slot["ans"] = {"action": action, "data": data}
            self._card_close(cid, "answered:" + str(action)[:40])
            slot["evt"].set()
            return
        if action == "perm" and isinstance(data, str) and data:
            # M18 approval: the decision returns via the hook (API),
            # never a synthetic keystroke
            self._card_close(cid, "answered:" + data)
            self._perm_answer(data)
            return
        if action == "surgery" and isinstance(data, str) and data:
            # §2g failed path: proposal-card approve = the human
            # gate, opens surgery (手术)
            self._card_close(cid, "answered")
            ft = self.store.task(int(data)) if data.isdigit() else None
            if ft is not None:
                self._surgery_open(ft, "")
            return
        if action == "consolidate" and isinstance(data, str) and data:
            # Consolidate reshaped (user ruling 2026-08-25): approve
            # = suspend the asset + open a consolidate order on the
            # sidecar seat; revival passes the registration gate.
            self._card_close(cid, "answered")
            try:
                d = json.loads(data)
            except ValueError:
                d = {"name": data}
            self._consolidate_go(d)
            return
        if action == "mute-alert" and isinstance(data, str) and data:
            # Per-intent mute for the token alert (M20 §1: don't
            # remind me again) — persisted to the store, silent
            # across sessions from now on
            self._card_close(cid, "answered")
            if self.store.intent(data) is not None:
                self.store.intent_revise(data, mute_alert=1)
                self.journal.row("alert", "alert-muted", intent=data)
                self._say_engine(f"Token alert for '{data}' muted "
                                 f"(no more reminders for this "
                                 f"intent).")
            return
        if (action in ("key", "line") and isinstance(data, str) and data
                and self.host is not None and self.host.alive()):
            self._last_input = time.monotonic()
            self._stall_fired = False
            if action == "key":
                self.host.write_raw(data)
            else:
                self._inject(data.strip())
        self._card_close(cid, f"answered:{action}")

    # ---- Loop-guardrail trace (safety batch 2026-08-12) ------------

    def _open_perm_tail(self, msg: str) -> None:
        """Layer-3 backstop card (perm tail capture + keystroke
        pass-through): the observation surface for while the native
        dialog is waiting on a human. Two paths in: Notification
        (when the hook is absent / the exemption misses) and defer
        (the dedicated-window timeout — that Notification already
        fired during the park window and was caught by the exemption
        stamp, so a second one won't come; defer opens the backstop
        card itself)."""
        with self._card_lock:
            dup = any(c["kind"] == "perm" for c in self._cards.values())
            stale = [c["id"] for c in self._cards.values()
                     if c["kind"] == "stall"]
        if dup:
            return                  # never stack cards: one card per wait
        for cid in stale:
            # perm supersedes a same-wait stall (a more accurate
            # diagnosis takes over the slot)
            self._card_close(cid, "superseded-by-perm")
        self._card_open(
            "perm", "Host is waiting for permission approval",
            (msg or "(hook carried no message)")
            + "\n\nScreen tail:\n"
            + (self._pty_tail() or "(none)"),
            options=[{"action": "key", "data": "1", "label": "1"},
                     {"action": "key", "data": "2", "label": "2"},
                     {"action": "key", "data": "3", "label": "3"},
                     {"action": "key", "data": "\x1b",
                      "label": "Esc"}])

    def _open_question_card(self, ti, tid) -> None:
        """Mirror card for AskUserQuestion: the question text and
        options go into the card stream, buttons = digits pass
        straight through (the selector lives in the terminal, digits
        are the options). If it can't be parsed, don't open one — it
        exists in the terminal regardless, a missed render is
        harmless."""
        qs = ti.get("questions") if isinstance(ti, dict) else None
        if not isinstance(qs, list) or not qs:
            return
        parts, nopt = [], 0
        for q in qs[:4]:
            if not isinstance(q, dict):
                continue
            opts = [o for o in (q.get("options") or [])
                    if isinstance(o, dict)]
            lines = [str(q.get("question") or "").strip()]
            for i, o in enumerate(opts):
                lab = str(o.get("label") or "")
                desc = str(o.get("description") or "")
                lines.append(f"  {i + 1}. {lab}"
                             + (f" — {desc[:240]}" if desc else ""))
            if q.get("multiSelect"):
                lines.append("  (multi-select — answer in the "
                             "terminal)")
            nopt = max(nopt, len(opts))
            parts.append("\n".join(lines))
        if not parts:
            return
        head = ""
        if isinstance(qs[0], dict):
            head = str(qs[0].get("header") or "").strip()
        body = ("\n\n".join(parts)
                + "\n\nButtons key straight into the terminal; answer "
                  "multi-part in order — when unsure, read the "
                  "terminal")
        options = [{"action": "key", "data": str(i + 1),
                    "label": str(i + 1)} for i in range(min(nopt, 4))]
        options.append({"action": "key", "data": "\x1b", "label": "Esc"})
        self._card_open("ask",
                        "Host question" + (": " + head if head else ""),
                        body, options=options, task=tid)

    @staticmethod
    def _perm_detail(ti) -> str:
        """One line spelling out what's being requested — a pure
        display string: the engine doesn't parse it or decide from
        it, a human reads it and presses the key (precedent from the
        old repo)."""
        if not isinstance(ti, dict):
            return ""
        for k in ("command", "file_path", "url", "pattern", "path"):
            v = ti.get(k)
            if isinstance(v, str) and v:
                return " ".join(v.split())[:200]
        return ""

    @staticmethod
    def _perm_rule_text(r):
        """One PermissionUpdate rule -> its display/ledger text.
        Shared by _perm_suggest (what the card shows, what the
        ceiling judges) and _perm_updates (which objects survive)."""
        if isinstance(r, str):
            return r
        if isinstance(r, dict):
            t = r.get("toolName")
            if isinstance(t, str) and t:
                c = r.get("ruleContent")
                return (t + "(" + c + ")"
                        if isinstance(c, str) and c else t)
            return json.dumps(r, sort_keys=True,
                              ensure_ascii=False)[:200]
        return None

    @staticmethod
    def _perm_suggest(tool: str, data: dict) -> list:
        """The harness's own verbatim always-allow rules (precedent
        from a real-world bug in the old repo, 2026-08-07:
        permission_suggestions is a list of PermissionUpdate
        objects, must be unpacked; deny-flavored suggestions never
        get recorded). mcp fallback: granularity is its name.
        No hardcoded per-tool law here (user ruling 2026-08-25):
        what must never be banked is the user's own call —
        never_allow in MODULE_POLICY is that surface, enforced at
        _perm_capped."""
        rule_text = Engine._perm_rule_text
        raw = data.get("permission_suggestions")
        cands = (raw.get("allow_rules") if isinstance(raw, dict)
                 else raw) or []
        if not isinstance(cands, list):
            cands = []
        out = []
        for c in cands:
            if isinstance(c, dict) and isinstance(c.get("rules"), list):
                if c.get("behavior") not in (None, "allow"):
                    continue
                out += [t for t in map(rule_text, c["rules"]) if t]
            else:
                t = rule_text(c)
                if t:
                    out.append(t)
        if not out and tool.startswith("mcp__"):
            return [tool]
        return out[:8]

    @staticmethod
    def _perm_updates(data: dict, keep: list[str]) -> list:
        """The harness's own PermissionUpdate objects, **verbatim** —
        the shape the CLI banks for itself.

        Live-fire 2026-08-25 (probes D/E/F): an allow carrying
        `updatedPermissions` runs the CLI's own persistence, lands in
        that seat's `.claude/settings.local.json`, and the next
        headless run honors it with no card at all — even though the
        seat's home has no trust record. So "Always allow" no longer
        needs an engine-side ledger to survive: the CLI keeps its own
        permissions, exactly like the native card's don't-ask-again
        row, which mints this same object.

        Nothing is minted here — only objects the harness itself
        offered, and only those whose rule text cleared
        _perm_capped."""
        raw = data.get("permission_suggestions")
        cands = (raw.get("allow_rules") if isinstance(raw, dict)
                 else raw) or []
        if not isinstance(cands, list):
            return []
        kept, out = set(keep), []
        for c in cands:
            if not isinstance(c, dict):
                continue
            if c.get("behavior") not in (None, "allow"):
                continue
            rules = c.get("rules")
            if not isinstance(rules, list):
                continue
            live = [r for r in rules
                    if Engine._perm_rule_text(r) in kept]
            if live:
                out.append({**c, "rules": live})
        return out[:8]

    def _perm_ask(self, data: dict) -> dict:
        """Blocking arbitration (M18): park → approval card → wait
        on the human ≤290s. Returns {"decision":
        "allow"|"deny"|"ask"} (ask = defer, the CLI's native dialog
        proceeds as usual — fail-safe, never fail-open)."""
        tool = str(data.get("tool_name") or "")
        if not tool:
            return {"decision": "ask"}
        self._perm_seen_t = time.time()     # birth stamp for the residual-echo exemption
        detail = self._perm_detail(data.get("tool_input"))
        suggest = self._perm_suggest(tool, data)
        running = next((t for t in self.store.queue_view()
                        if t["status"] == "running"
                        and t["executor"] == self.module), None)
        tid = running["id"] if running else None
        if tool == "AskUserQuestion":
            # A question is not a permission (user ruling
            # 2026-08-12 night: the admission dialog would ask for
            # nothing, the question text still lands in the CLI):
            # the engine allows the ask action outright (by=policy,
            # no ask row — a question doesn't accrue pruning
            # friction), the question is rendered from tool_input
            # into a question card in the card stream, and the
            # answer goes through digit pass-through (in the CLI a
            # question only has one path, keying in — same lane as
            # the perm tail card).
            self.journal.row("perm", "allow", by="policy", tool=tool,
                            task=tid)
            self._perm_done_t = time.time()
            self._open_question_card(data.get("tool_input"), tid)
            return {"decision": "allow"}
        # session-level always-approve: answer first, stop after —
        # no card pops (batch two)
        if suggest and any(r in self._perm_grants for r in suggest):
            self.journal.row("perm", "allow", by="grant", tool=tool,
                            detail=detail or None, task=tid)
            self._perm_done_t = time.time()
            return {"decision": "allow"}
        slot = {"tool": tool, "detail": detail, "suggest": suggest,
                "event": threading.Event(), "decision": None,
                "always": False, "card": None, "task": tid}
        with self._perm_lock:
            old_slot = self._perm_slot
            self._perm_slot = slot
        if old_slot is not None:
            # supersede: the old question is dead (its CLI timed
            # out / was killed), defer releases it back to the
            # native dialog
            old_slot["decision"] = None
            old_slot["superseded"] = True   # the supersede path never opens a backstop card (a new question is present)
            old_slot["event"].set()
            if old_slot.get("card"):
                self._card_close(old_slot["card"], "superseded")
            self.journal.row("perm", "superseded",
                            tool=old_slot.get("tool"))
        body = (tool + "\n" + detail
                + ("\n\n\"Always allow\" banks these rules — the CLI "
                   "keeps its own copy in this seat's settings, the "
                   "engine keeps the cross-seat copy in config.json "
                   "(PERM_ALLOW); edit either to revoke:\n  "
                   + "\n  ".join(suggest) if suggest else ""))
        card = self._card_open(
            "approval", "Permission request: " + tool, body,
            options=[{"action": "perm", "data": "allow",
                      "label": "Allow once"},
                     {"action": "perm", "data": "always",
                      "label": "Always allow"},
                     {"action": "perm", "data": "deny",
                      "label": "Deny"}],
            task=tid)
        slot["card"] = card["id"]
        self.journal.row("perm", "ask", tool=tool, detail=detail or None,
                        suggest=json.dumps(suggest, ensure_ascii=False)
                        if suggest else None, task=tid)
        slot["event"].wait(defaults.PERM_ASK_WAIT_S)
        with self._perm_lock:
            if self._perm_slot is slot:
                self._perm_slot = None
        decision = slot["decision"]
        if decision is None:                       # timeout/supersede = defer
            self._card_close(slot["card"], "timeout")
            self.journal.row("perm", "defer", tool=tool, task=tid)
            if not slot.get("superseded"):
                # defer = the native dialog is really about to
                # render, the layer-3 backstop card takes over here
                self._open_perm_tail(tool + ("\n" + detail
                                             if detail else ""))
            return {"decision": "ask"}
        grant = []
        if decision == "allow" and slot["always"] and suggest:
            # Two landings for one click (2026-08-25): the CLI banks
            # the harness's own objects in this seat's settings
            # (`grant` -> permfwd -> updatedPermissions), and the
            # ledger keeps the engine-side, cross-seat copy. Both
            # pass the ceiling first — _grant_rules re-checks, which
            # is idempotent once the capped ones are already gone.
            kept = self._perm_capped(suggest)
            if kept:
                grant = self._perm_updates(data, kept)
                self._grant_rules(kept)
                self.journal.row("perm", "grant", tool=tool, task=tid,
                                rules=json.dumps(kept, ensure_ascii=False),
                                cli=len(grant) or None)
        self.journal.row("perm", decision, by="human", tool=tool,
                        detail=detail or None, task=tid)
        return ({"decision": decision, "grant": grant} if grant
                else {"decision": decision})

    def _perm_capped(self, rules: list[str]) -> list[str]:
        """The **never_allow ceiling** — the single choke point every
        permanent rule passes through, whichever face minted it
        (audit 2026-08-25, user ruling the same day; widened the same
        evening to cover the CLI-persisted path, so handing the rule
        to the CLI cannot route around it).

        Returns the rules that survive; refusals are journalled and
        raise a notify card. Substring semantics, deliberately
        over-blocking — and deliberately **above the human**, because
        the rule this mints is permanent: a card can be raised on an
        agent's behalf, so "a human clicked it" is not by itself
        proof of intent. Widening the ceiling is a hand edit of
        MODULE_POLICY, which no agent can reach."""
        never = ((defaults.MODULE_POLICY.get(self.module) or {})
                 .get("security") or {}).get("never_allow") or []
        capped = [r for r in rules
                  if any(str(n) in r for n in never)]
        if not capped:
            return list(rules)
        for r in capped:
            self.journal.row("perm", "ceiling-refused", rule=r)
        self._card_open(
            "notify", "Permission ceiling",
            "Not saved: " + ", ".join(capped)
            + "\nThese touch never_allow (" + ", ".join(never)
            + ") — the ceiling holds regardless of who asked or "
              "who approved. To widen it, hand-edit "
              "MODULE_POLICY in defaults.py.")
        return [r for r in rules if r not in capped]

    def _grant_rules(self, rules: list[str]) -> None:
        """Record an always-allow (permission-surface convergence
        2026-08-24): dual-write to the in-memory view + config.json's
        PERM_ALLOW — takes effect for every seat. Instant for the
        PTY face and perm_gate (the _perm_grants pre-check);
        headless's --allowedTools refreshes on the same beat as the
        home render, effective on the next order. A persistence
        failure doesn't block this allow (a ledger gap gets logged,
        visible to a human).

        The **never_allow ceiling is enforced here** (audit
        2026-08-25, user ruling the same day): this is the single
        choke point every grant passes through, and until now the
        ceiling was advertised in README/CONFIG and enforced
        nowhere. Substring semantics, deliberately over-blocking —
        and deliberately above the human, because the rule this
        gate mints is permanent and applies to every seat: the card
        that asks for it can be authored by an agent (perm_gate sits
        on the executor's own tool face), so "a human clicked it" is
        not by itself proof of intent. Widening the ceiling is a
        hand edit of MODULE_POLICY, which no agent can reach."""
        rules = self._perm_capped(rules)
        if not rules:
            return
        self._perm_grants.update(rules)
        self._perm_grants.update(rules)
        try:
            wsconfig.grant(self.workspace, rules)
        except OSError as e:
            self.journal.row("perm", "grant-persist-failed",
                            why=str(e)[:200])
        if defaults.XSOLO_NAME in self._xhosts:
            self._xhosts[defaults.XSOLO_NAME].allow_tools = \
                solo_allow_rules(self.workspace)
            self._solo_refresh()

    def _perm_answer(self, choice: str) -> bool:
        """A card's answer → releases the hook. Answering is the
        API call itself, never a simulated keystroke (user order
        2026-08-02). False = nothing to answer (an empty admission,
        same as flushing an empty queue)."""
        with self._perm_lock:
            slot = self._perm_slot
            if slot is None:
                return False
            self._perm_slot = None
        slot["always"] = choice == "always"
        slot["decision"] = ("allow" if choice in ("allow", "always")
                            else "deny")
        self._perm_done_t = time.time()
        slot["event"].set()
        return True

    def _blocked(self, face: str, detail: dict) -> None:
        """Log the gatekeeper's rejection letter — never silent
        (CASELAW 7). Normal use should never produce this row:
        nonzero = something on the web is knocking on this local
        engine's door, a ledger anomaly."""
        if self.journal is not None:
            # Attacker-shaped values (audit 2026-08-25 §4-security):
            # every field here is a request header or path the
            # remote side chose — cap each so a hostile client
            # can't pump the ledger through the rejection lane.
            self.journal.row("guard", "blocked", face=face,
                             **{k: str(v)[:200]
                                for k, v in detail.items() if v})

    # ---- hook bypass (M13: CLI hook → hookfwd mailbox → here.
    #      Visibility-completeness rule layers 1/2: dedicated cards
    #      surface known waits, unknowns are never swallowed) -------

    def _on_hook(self, evt: dict) -> None:
        """Full journal record + dispatch. Never returns a decision
        (v1 bypass; blocking arbitration = the escalation lever, off
        by default) — the answer channel is the card's key/line.
        Never gamble on the subtype field's shape: explicit fields
        take priority, message keywords are the fallback, anything
        unrecognized always surfaces as an info card (never
        swallowed)."""
        name = str(evt.get("hook_event_name") or "?")
        if name == "PreToolUse":
            # §2f telemetry bus (user ruling 2026-08-13):
            # high-frequency bypass — attributed by seat to the
            # current active order, doesn't enter the full hook log
            # (journal-flood exempt), never opens an unknown card;
            # sid learning doesn't go this way either (an x· seat's
            # session isn't the host)
            self._bus_event(evt)
            return
        sub = str(evt.get("notification_type") or evt.get("subtype") or "")
        msg = str(evt.get("message") or "")
        # M15: every hook payload carries a session_id — the other
        # half of the join-key coordinate is learned right here (a
        # host restart changes the id, so follow the latest)
        sid = str(evt.get("session_id") or "") or None
        if sid and sid != self._host_session:
            self._host_session = sid
        self.journal.row("hook", name, subtype=sub or None,
                        payload=json.dumps(evt, ensure_ascii=False)[:1200])
        if name == "Stop":
            # turn ended = a new reply exists; the collapsed state
            # lights its badge off this row
            self._feed("reply", "host turn ended — new reply (details "
                                "in the terminal)")
            return
        low = (sub + " " + msg).lower()
        if name == "Notification":
            if "permission" in low:
                # M18 layer-order precedent (measured 2026-08-12, a
                # double-card bug): while the dedicated-window ask
                # is still up (human hasn't pressed), Notification
                # still fires around ~6s regardless — layer 0 is
                # present, so layer 3's backstop is exempt from
                # opening; the same exemption holds for 3s right
                # after answering (answering releases immediately,
                # a residual echo isn't a new wait). defer/timeout
                # are not on this list: at that point the native
                # dialog is really waiting on a human, the backstop
                # card must open.
                with self._perm_lock:
                    parked = self._perm_slot is not None
                fresh = max(self._perm_done_t, self._perm_seen_t)
                if (parked or time.time() - fresh
                        < defaults.PERM_NOTIF_GRACE_S):
                    return
                self._open_perm_tail(msg)
                return
            if ("idle" in low or "waiting" in low
                    or "agent_needs_input" in low):
                self._feed("idle", msg or "host is waiting for input")
                return
        # Unknown hook / unknown subtype: never swallowed — surfaces
        # as an info card (completeness-rule layer 2)
        self._card_open("info",
                        f"hook:{name}" + (f" · {sub}" if sub else ""),
                        msg or json.dumps(evt, ensure_ascii=False)[:400],
                        options=[{"action": "dismiss",
                                  "label": "Got it"}])

    def _bus_event(self, evt: dict) -> None:
        """§2f telemetry bus: sidecar and every executor seat share
        one stream — seat name = cwd's tail name (home name is seat
        name, a hook's cwd is the instance home), attributed to the
        current active order (§2h one seat, one active order → the
        active order is unique, attribution is never ambiguous).
        Collects only the tool name + a coarse-grained target
        (path / command's first word), never the payload. Three
        consumers: the task card display (M23) / the completion
        receipt / pruning reconciliation; the §2g surgery (手术)
        ring uses it as a residue map. Load-bearing rule: the bus
        never throws back, bad material is silently dropped."""
        try:
            seat = Path(str(evt.get("cwd") or "")).name
            if not seat:
                return
            t = self.store.seat_running(seat)
            if t is None:
                return                  # no active order on the seat = gap noise, no case opened
            tool = str(evt.get("tool_name") or "?")[:60]
            ti = evt.get("tool_input")
            target = ""
            if isinstance(ti, dict):
                for k in ("file_path", "path", "notebook_path", "url",
                          "pattern", "skill", "command"):
                    v = ti.get(k)
                    if isinstance(v, str) and v.strip():
                        v = v.strip()
                        target = (v.split()[0] if k == "command"
                                  else v)[:200]
                        break
            d = self._task_dir(t["id"])
            d.mkdir(parents=True, exist_ok=True)
            row = json.dumps(
                {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "tool": tool,
                 "target": target or None}, ensure_ascii=False)
            with self._bus_lock:
                with open(d / "events.jsonl", "a",
                          encoding="utf-8") as f:
                    f.write(row + "\n")
        except Exception:
            pass                        # a bypass is never load-bearing (the degradation clause)

    def _bus_census(self, tid: int) -> tuple[int, str]:
        """Census of a task's event ledger: total call count + top 3
        (a shared read surface for the receipt / surgery map)."""
        ev = self._task_dir(tid) / "events.jsonl"
        cnt: dict[str, int] = {}
        if ev.is_file():
            for line in ev.read_text(encoding="utf-8",
                                     errors="replace").splitlines():
                try:
                    k = json.loads(line).get("tool") or "?"
                except ValueError:
                    continue
                cnt[k] = cnt.get(k, 0) + 1
        n = sum(cnt.values())
        top = " ".join(f"{k}×{v}" for k, v in sorted(
            cnt.items(), key=lambda kv: -kv[1])[:3])
        return n, top

    def _residue_md(self, tid: int) -> str:
        """§2g residue map: a failed order's bus event list → the
        surgery (手术) package's troubleshooting checklist (tool +
        target, deduped, order preserved, capped at 40 lines)."""
        ev = self._task_dir(tid) / "events.jsonl"
        seen, lines = set(), []
        if ev.is_file():
            for line in ev.read_text(encoding="utf-8",
                                     errors="replace").splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                key = (r.get("tool"), r.get("target"))
                if key in seen:
                    continue
                seen.add(key)
                lines.append(f"  - {r.get('tool')}"
                             + (f" → {r['target']}" if r.get("target")
                                else ""))
        if not lines:
            return ("  (no bus record — investigate carefully, don't "
                    "trust the map alone)")
        cut = lines[:40]
        if len(lines) > 40:
            cut.append(f"  - … {len(lines)} sites total, the rest in "
                       f"{ev}")
        return "\n".join(cut)

    # ---- §2g surgery (手术) ring (executor failure loop; since v14
    #      only the x·solo seat exists) --------------------------------

    def _surgery_open(self, ft: dict, note: str) -> None:
        """Open the table (surgery, 手术) — two entry points converge
        here: retry with a note / failed-proposal-card approve (each
        one's human touch is already done at its entry point). ft =
        the failed x· order. The suspension granularity is a single
        intent (a 手术 lock on the trigger port); what gets fixed is
        the intent itself — the folder is edited and re-registered
        through workspace_submit (intent_update is retired; the
        directory is the source), settlement auto-replays."""
        if self.store.inflight(ft["intent"]):
            self._say_engine(f"'{ft['intent']}' still has a ring in "
                             f"flight — open surgery after it "
                             f"lands.")
            return
        if not self._admit_spec("手术", f"surgery: {ft['intent']}"):
            return
        st = self.store.chain_start("手术", issuer="user",
                                    intent=ft["intent"],
                                    payload=note.strip() or None,
                                    origin=ft["id"])
        self.journal.row("surgery", "open", task=st["id"],
                        of=ft["id"], note=note.strip() or None)
        self._say_engine(f"Surgery opened (task {st['id']}): "
                         f"'{ft['intent']}' suspended (triggers "
                         f"refused); maintenance clears residue + "
                         f"repairs the intent; settlement "
                         f"auto-replays.")
        self._task_bcast()

    def _surgery_settle(self, t: dict, outcome: str) -> None:
        """Settle a surgery (手术) order: ok → replay directly
        (task_done is the sole ignition signal; the intent revision
        takes effect immediately, no approval-queue step); fail /
        timeout → unlock back to the human face, no replay."""
        name = t.get("intent")
        if outcome != "ok":
            self.journal.row("surgery", "aborted", task=t["id"],
                            outcome=outcome)
            self._card_open(
                "notify", f"Surgery incomplete: '{name}'",
                f"Surgery task {t['id']} ended {outcome} — unlocked; "
                f"residue may remain. Retry the failed order for "
                f"another surgery, or let it go.",
                task=t["id"])
            return
        orig = self.store.task(int(t.get("origin") or 0)) or {}
        self._surgery_replay(defaults.XSOLO_NAME,
                             {"intent": name,
                              "input": orig.get("payload") or "",
                              "origin": t.get("origin")})

    def _surgery_replay(self, pname: str, payload: dict) -> None:
        """Auto-replay: resubmit the original intent + original
        input to the executor seat. One surgery, one replay — if
        this order fails again it goes back to the human face via
        the failed-proposal card; no machine self-loop is set up."""
        name = str(payload.get("intent") or "")
        it = self.store.intent(name)
        if (it is None or it.get("status") != "provisioned"
                or self.store.inflight(name)):
            self._say_engine(f"Replay stranded: '{name}' is off the "
                             f"shelf or in flight — re-trigger "
                             f"manually.")
            return
        t = self.store.chain_start(
            f"deliver:{name}", issuer="user", intent=name,
            payload=str(payload.get("input") or "") or None,
            origin=payload.get("origin"))
        self.journal.row("surgery", "replay", task=t["id"],
                        protocol=pname, intent=name,
                        of=payload.get("origin"))
        self._say_engine(f"Auto-replay: task {t['id']} ('{name}', "
                         f"protocol '{pname}') — another failure comes "
                         f"back to you.")
        self._task_bcast()

    # ---- PTY quiescence detection (completeness-rule layer 3,
    #      sovereign backstop: sees "waiting" even without a hook) --

    def _on_pty_output(self, data: str) -> None:
        """The engine's tap on host output (reader-thread callback):
        stamp the clock → relay the stream → the card-dismissal
        rule. The card ledger is locked; journal/broadcast are
        already shared across threads."""
        self._last_output = time.monotonic()
        self._stall_fired = False
        self.channel.push_cli(data)
        self._close_wait_cards("output-resumed", kinds=("stall",))

    def _stall_watch(self) -> None:
        """Quiescence detection inside the pump: host alive ∧
        trusted ∧ this mode has a running ring ∧ both output and
        keystrokes have been quiet past the threshold ∧ no wait card
        already on the rack → a stall card (with the screen tail,
        the UI pairs it with a typed-input line). Only one card per
        quiet episode — output or a keystroke re-arms it."""
        if (self.host is None or not self.host.alive()
                or not self.host.trusted() or self._stall_fired):
            return
        quiet = time.monotonic() - max(self._last_output, self._last_input)
        if quiet < defaults.IDLE_STALL_S:
            return
        running = [t for t in self.store.queue_view()
                   if t["status"] == "running"
                   and t["executor"] == self.module]
        if not running:
            return
        with self._card_lock:
            if any(c["kind"] in ("perm", "stall")
                   for c in self._cards.values()):
                return                      # a wait card is already on the rack — don't stack
        t = running[0]
        self._stall_fired = True
        self._card_open(
            "stall",
            f"Host quiet {int(quiet)}s — task {t['id']}"
            f" ({t.get('intent') or t.get('spec')}) still running",
            "Screen tail:\n" + (self._pty_tail() or "(no output)")
            + "\n\nProbably waiting for input — a typed answer keys "
              "straight into the terminal; or expand the terminal and "
              "look.",
            options=[{"action": "dismiss", "label": "Dismiss"}],
            task=t["id"])

    # ---- Unified settle surface (M12 flow graph: validator →
    #      record → effect → edge-lookup routing → hop guardrail;
    #      docs/M12-FLOW.md) --------------------------------------------

    def _node_of(self, t: dict) -> dict:
        """The node a task sits at (five attributes). v5 migration
        compatibility: for old chain types that were swept away
        (intent-creation/debug), a ring already in flight is
        synthesized the old way — accounting stays the same, the
        edge is always end (only wraps up, never routes further),
        an old gate approval still has to go up on the rack."""
        n = (self.store.node(t["spec"], t["seq"])
             if t.get("spec") else None)
        if n is not None:
            return n
        spec = t.get("spec") or ""
        aux = spec in ("validate", "debug")   # R5: a retry single ring is a real account
        return {"kind": "gate" if spec == "intent-creation" else "deliver",
                "accounting": "test" if aux else "real",
                "ref": None, "template": None,
                "effect": ("ok:provision" if spec == "intent-creation"
                           else None),
                "on_ok": "end", "on_fail": "end"}

    def _sample_local_perms(self, home: Path) -> None:
        """M15 §4: on power-up, read the instance home's
        settings.local.json and log the permissions block
        (allow/deny/ask counts + the verbatim rules) into the
        journal (a sink dual-writes into events, kind=perm /
        name=local-accretion). Read-only, no stripping — stripping
        waits until §7's materialization exit is built."""
        local = home / ".claude" / "settings.local.json"
        present, counts, rules = False, {}, {}
        try:
            data = json.loads(local.read_text(encoding="utf-8"))
            perms = data.get("permissions")
            if isinstance(perms, dict):
                present = True
                rules = perms
                counts = {k: len(v) for k, v in perms.items()
                          if isinstance(v, list)}
        except (OSError, ValueError):
            pass                      # missing file / bad JSON are both logged as "no block"
        self.journal.row("perm", "local-accretion", present=present,
                         counts=counts or None,
                         rules=(json.dumps(rules, ensure_ascii=False)[:4000]
                                if rules else None))

    def _task_receipt(self, t: dict) -> dict | None:
        """Completion receipt (user ruling 2026-08-13): the moment
        an order settles, backfill display of steps / duration /
        tokens — the join key slices the transcript to gather usage
        (soft dependency: if slicing fails, just report duration, no
        harm done). Logs to the journal + a feed row into the card
        stream. Returns usage (material for the consolidate ring;
        None = transcript unavailable)."""
        win = self.store.task_window(t["id"])
        if win is None:
            return None
        execu = str(t.get("executor") or "")
        home = (self.workspace / defaults.INSTANCES_DIRNAME / execu
                if execu.startswith(defaults.XPROTO_PREFIX)
                else instance_home(self.workspace, self.module))
        u = prune_report.window_usage(win, home)
        name = t.get("intent") or t.get("spec") or "?"
        dur = win.get("duration_s") or 0.0
        self.journal.row("task", "receipt", task=t["id"],
                        intent=t.get("intent"),
                        dur=dur, calls=(u or {}).get("calls"),
                        out=(u or {}).get("out"),
                        cache_read=(u or {}).get("cache_read"))
        n, top = self._bus_census(t["id"])   # §2f bus census feeds into the receipt
        tools = f" · tools {n} calls ({top})" if n else ""
        if u is not None:
            self._feed("receipt",
                       f"task {t['id']} '{name}' settled: {u['calls']} "
                       f"steps · {dur:.0f}s · out {u['out']:,} "
                       f"(cache read {u['cache_read']:,}){tools}")
        else:
            self._feed("receipt",
                       f"task {t['id']} '{name}' settled: {dur:.0f}s "
                       f"(transcript unavailable — steps and tokens "
                       f"missing){tools}")
        return u

    def _token_alert(self, t: dict, u: dict | None) -> None:
        """The consolidate ring (M20 §1): a single task's output
        crossing the threshold → an alert card. Execution produces
        evidence → threshold → one human click → the asset gets
        better (isomorphic to the friction ring: the friction ring
        governs permissions, this ring governs token cost). soft
        dependency: no usage, no alarm; per-intent mute (don't
        remind me again)."""
        if u is None or (u.get("out") or 0) < defaults.TASK_TOKEN_ALERT:
            return
        name = t.get("intent")
        it = self.store.intent(name) if name else None
        if it is None or it.get("status") != "provisioned":
            return
        if it.get("mute_alert"):
            return
        self.journal.row("alert", "token-alert", intent=name,
                        task=t["id"], out=u["out"])
        # consolidate retargeted (2026-08-24): the correct path to
        # a cheaper intent is the retry ring (sidecar reproduces →
        # fixes the piece → passes the registration gate) — the
        # alert only reports the account and points the way, no
        # longer hangs a one-click consolidate
        self._card_open(
            "ask", f"Token alert: '{name}'",
            f"task {t['id']} output {u['out']:,} tokens (threshold "
            f"{defaults.TASK_TOKEN_ALERT:,}). If this order should be "
            f"cheaper, trigger a retry with a note — the sidecar "
            f"autopsies and redoes it, and the consolidate offer "
            f"that follows folds the lesson into the intent through "
            f"your registration gate.",
            options=[{"action": "mute-alert", "data": name,
                      "label": "Mute this intent"},
                     {"action": "dismiss", "label": "Not now"}])

    def _exec_dur(self, tid: int) -> float | None:
        """Real execution duration (M15): delivered_at → now,
        **excludes queueing**. The records table's duration_s is
        promoted from a dead-in-service field. For anything never
        stamped delivered (a gate ring / a pre-v7 old order), return
        None — don't fabricate a number."""
        row = self.store.task(tid) or {}
        d = row.get("delivered_at")
        if not d:
            return None
        try:
            ep = time.mktime(time.strptime(d, "%Y-%m-%d %H:%M:%S"))
        except (ValueError, OverflowError):
            return None
        return round(max(0.001, time.time() - ep), 3)

    def _settle(self, t: dict, outcome: str,
                outcome_text: str | None = None) -> dict | None:
        """Settling is routing: the caller has already judged
        ok/fail by type (procedure=exit code / agent=task_done+time
        limit / gate=human approval) and landed the task status;
        this does everything else — record (driven by the
        accounting attribute) → the cancel flag (settling stops it)
        → effect (the constitutional verb, stamp before routing) →
        edge lookup (binary, a table lookup not an evaluation) →
        the hop guardrail. Returns the next ring routed to (None =
        chain finished / chain stopped / guardrail)."""
        node = self._node_of(t)
        execu = str(t.get("executor") or "")
        if execu.startswith(defaults.XPROTO_PREFIX):
            xh = self._xhosts.get(execu[len(defaults.XPROTO_PREFIX):])
            if xh is not None:
                xh.reap(t["id"])
        # §2m v9: execution travels with the seat (sidecar or an x·
        # executor seat), while the friction ledger / receipt /
        # first-flight card are per-intent product artifacts — they
        # follow the order, not the seat
        ran_here = (t.get("executor") == self.module
                    or execu.startswith(defaults.XPROTO_PREFIX))
        usage = None
        if node.get("kind") == "deliver" and ran_here:
            usage = self._task_receipt(t)   # completion receipt (test orders get an account too)
        if (node.get("kind") == "deliver"
                and execu.startswith(defaults.XPROTO_PREFIX)
                and not str(t.get("spec") or "").startswith("protocol:")):
            # §2g×v9: an executor seat finishing → the human gets
            # notified. ok but unsatisfied = retry with a note
            # (opens surgery/手术); fail = an automatic debug
            # proposal, still needs approve to open the table (each
            # of the two entry points gets one human touch). M26:
            # bracket settlement is not on this list — closing a
            # bracket is already a human action, notify is just its
            # echo.
            dur = self._exec_dur(t["id"])
            if outcome == "ok":
                self._card_open(
                    "notify", f"Executor done: '{t.get('intent')}'",
                    "ok" + (f" · {dur:.0f}s" if dur else "") + " — "
                    + (outcome_text or "(no summary)")[:400]
                    + "\nNot satisfied? Retry this task with a note — "
                      "a retry bracket opens: maintenance fulfills "
                      "directly and you approve the result.",
                    task=t["id"])
            else:
                # kind **offer**, not ask (audit 2026-08-25, same
                # ruling as the consolidate card): this card carries
                # the only entry into surgery, and _close_wait_cards
                # sweeps perm/stall/ask on cli-engaged — as an ask
                # card it lost its Open-surgery button the moment the
                # user typed anything into the sidecar terminal, with
                # no way back to the repair loop.
                self._card_open(
                    "offer", f"Executor failed: '{t.get('intent')}'",
                    f"{outcome}"
                    + (f" · {dur:.0f}s" if dur else "") + " — "
                    + (outcome_text or "(no summary)")[:400]
                    + "\nOpen surgery = suspend this intent; "
                      "maintenance clears by the residue map + repairs "
                      "the intent; settlement auto-replays (§2g).",
                    options=[{"action": "surgery", "data": str(t["id"]),
                              "label": "Open surgery"},
                             {"action": "dismiss", "label": "Not now"}],
                    task=t["id"])
            self._say_engine(f"Executor done: '{t.get('intent')}' "
                             f"({outcome}) — "
                             f"{(outcome_text or '')[:160]}")
        if (node.get("kind") == "deliver"
                and str(t.get("spec") or "") == "手术"
                and not self.store.chain_cancelled(t["chain_id"])):
            self._surgery_settle(t, outcome)    # §2g settling is the ignition signal
        edge = str((node.get("on_ok") if outcome == "ok"
                    else node.get("on_fail")) or "end")
        # (route=False — the 2026-08-16 inside-bracket stop — had no
        # remaining caller and was deleted, audit 2026-08-25.)
        # Recording: an agent ring records the moment it settles, a
        # procedure ring must record on fail; a gate never records;
        # a procedure's mid-chain ok doesn't record (no padding the
        # numbers), it records only once the chain finishes — and
        # must pass the cancel flag (a cancelled chain's plain
        # finish never posts a "done" record)
        rec = outcome_text is not None and node.get("kind") != "gate"
        proc_ok = node.get("kind") == "procedure" and outcome == "ok"
        if rec and not proc_ok:
            self.store.record(t["id"], t.get("intent"),
                              is_test=node.get("accounting") == "test",
                              outcome=outcome_text,
                              duration_s=self._exec_dur(t["id"]))
        if self.store.chain_cancelled(t["chain_id"]):
            return None                     # settlement received, chain stops (effect stops with it)
        if edge == "end":
            target = None
        elif edge == "next":
            target = (t["spec"], t["seq"] + 1)
        else:
            s, _, q = edge.rpartition(":")
            target = (s, int(q))
        is_end = target is None or self.store.node(*target) is None
        if rec and proc_ok and is_end:
            self.store.record(t["id"], t.get("intent"),
                              is_test=node.get("accounting") == "test",
                              outcome=outcome_text,
                              duration_s=self._exec_dur(t["id"]))
        self._apply_effect(t, node, outcome)
        if (outcome == "ok" and t.get("intent")
                and node.get("kind") == "deliver"
                and (t.get("executor") == self.module
                     or str(t.get("executor") or "")
                     .startswith(defaults.XPROTO_PREFIX))   # §2m v9: follows the order
                and str(t.get("spec") or "").startswith("deliver:")
                and node.get("accounting") != "test"):
            # Token alert (M20 §1). consolidate retargeted (user
            # ruling 2026-08-24, live-test night): once an intent is
            # registered/compiled, consolidation has no landing spot
            # — the correct path to improving an intent is the
            # retry ring (sidecar reproduces and fixes the piece);
            # the first-flight suggestion card retires with the old
            # target, the consolidate prompt moves to **booklet
            # merge** time (_proto_close).
            self._token_alert(t, usage)
        if target is None:
            return None
        if self.store.node(*target) is None:
            if edge != "next":              # next running past the tail = chain finished, not a bug
                self.journal.row("chain", "route-missing", task=t["id"],
                                to=edge)
            return None
        spec, seq = target
        if (self.store.node_visits(t["chain_id"], spec, seq)
                >= defaults.MAX_NODE_VISITS):
            # Loop guardrail: a jump-back edge is legal, the hop
            # count is capped — the chain stops and waits for a
            # human
            self.journal.row("chain", "loop-limit", chain=t["chain_id"],
                            node=f"{spec}:{seq}")
            self._say_engine(f"chain {t['chain_id']} hit the loop cap "
                             f"(node {spec}:{seq} visited "
                             f"{defaults.MAX_NODE_VISITS}×) — chain "
                             f"stopped, awaiting a human.")
            return None
        jump = edge not in ("next", "end")
        nxt = self.store.route_next(t["id"], spec, seq,
                                    origin=t["id"] if jump else None)
        if jump:
            # A jump edge (jump-back/cross-chain) logs loudly;
            # linear next stays silent (the deliver row records
            # it). origin = the ring it jumped out of — the next
            # node's "previous task"
            self.journal.row("chain", "route", task=nxt["id"],
                            frm=t["id"], edge=outcome,
                            to=f"{spec}:{seq}")
        return nxt
    def _apply_effect(self, t: dict, node: dict, outcome: str) -> None:
        """effect fixed table (constitutional verbs, settle stamps
        before routing): suspend_intent / provision / reprovision.
        Format "ok|fail:<verb>"; mismatched polarity doesn't fire;
        verbs carry their own idempotency guard (silently no-op on
        status mismatch)."""
        pol, _, verb = str(node.get("effect") or "").partition(":")
        if not verb or pol != outcome:
            return
        name = t.get("intent")
        it = self.store.intent(name) if name else None
        if verb == "suspend_intent":
            # Rework law (2026-08-11): bad units suspend back to
            # draft (IME/deck naturally isolated); repair goes
            # through the same QA path as creation (the edge has
            # rerouted to qual·回炉)
            if it is None or it["status"] != "provisioned":
                return
            self.store.intent_revise(name, status="draft")
            self.journal.row("intent", "suspended", intent=name,
                            by="firing-failed")
            self.channel.broadcast(self._intents_frame())
            self._compile_intents_keyset()
        elif verb == "retire_intent":
            # Retirement law (live-fire precedent 2026-08-23): soft
            # retirement — leaves the roster but not the history;
            # journal/tasks all retained; revival = re-submit via
            # workspace_submit
            if it is None:
                # Booklet branch (live-fire precedent 2026-08-26: a
                # renamed booklet left its old self stranded — there
                # was no retirement path for protocols at all). Same
                # soft law: proto row + declared members flip to
                # retired together (one compile unit, one fate); the
                # open-bracket case was refused at proposal time.
                p = self.store.proto_get(name)
                if p is None or p["status"] != "provisioned":
                    return
                self.store.proto_set_status(name, "retired")
                for m in (p.get("members") or []):
                    mi = self.store.intent(m)
                    if (mi is not None and mi.get("proto") == name
                            and mi["status"] == "provisioned"):
                        self.store.intent_revise(m, status="retired")
                        self._hot.pop(m, None)
                self.journal.row("protocol", "retired", intent=name,
                                by="gate-approved",
                                members=len(p.get("members") or []))
                self._solo_refresh()
                self._compile_intents_keyset()
                self._compile_deck_plugin()    # sidebar group drops with the roster
                self._say_engine(f"Booklet '{name}' retired with its "
                                 f"members — gone from the IME and the "
                                 f"deck (one Stream Deck app restart "
                                 f"clears its sidebar group). History "
                                 f"and ledger stay; to revive, "
                                 f"workspace_submit the folder again.")
                self.channel.broadcast(self._intents_frame())
                return
            if it["status"] != "provisioned":
                return
            self.store.intent_revise(name, status="retired")
            self._hot.pop(name, None)
            self.journal.row("intent", "retired", intent=name,
                            by="gate-approved")
            self._solo_refresh()               # roster changed, re-render home (seat unchanged)
            self._compile_intents_keyset()     # roster change re-compiles the keyset
            self._say_engine(f"Intent '{name}' retired — gone from the "
                             f"IME and the deck keyset (one Stream Deck "
                             f"app restart clears its sidebar entry). "
                             f"History and ledger stay; to revive, "
                             f"workspace_submit the folder again.")
            self.channel.broadcast(self._intents_frame())
        elif verb == "provision":
            # Human approves template = goes live (ruling 2026-08-11:
            # sim downgraded to optional)
            if it is None:
                return
            self.store.compile_delivery(name)
            self.store.intent_revise(name, status="provisioned")
            self._touch(name)               # new intent enters the container directly (container law)
            self._solo_refresh()               # roster changed, re-render home (seat unchanged)
            self._compile_intents_keyset()     # M26: roster change re-compiles the keyset
            self.journal.row("intent", "provisioned", intent=name,
                            by="template-approved")
            self._say_engine(f"Intent '{name}' is live — in the IME, "
                             f"and its key joined the IntentOS · "
                             f"Intents deck set (restart the Stream "
                             f"Deck app once to see it in the sidebar; "
                             f"already-placed keys pick up route "
                             f"changes on their own). To test it, pin "
                             f"it and press Validate (optional).")
            self.channel.broadcast(self._intents_frame())
        elif verb == "provision_workspace":
            # §2u register-is-compile: one card approves the whole
            # unit in one pass — declaration into the ledger +
            # delivery chain recompiled. Idempotent (safe for gate
            # replay).
            if it is None:
                # protocol side (v17 compile unit): skill + member
                # declarations, the whole booklet goes live
                # atomically — the human-approved frozen staging
                # copy is the sole material, one transaction lands
                # it, no half-booklet state.
                sdir = (self.workspace / defaults.RUNTIME_DIRNAME
                        / "staging" / f"protocol-{name}")
                stg = sdir / "skill.md"
                prow = self.store.proto_get(name)
                if prow is None or not stg.is_file():
                    self.journal.row("protocol", "orphan-gate",
                                    intent=name, task=t["id"])
                    return
                mdecls: list = []
                mj = sdir / "members.json"
                if mj.is_file():
                    try:
                        mdecls = json.loads(
                            mj.read_text(encoding="utf-8"))
                    except ValueError:
                        self.journal.row("protocol", "orphan-gate",
                                        intent=name, task=t["id"],
                                        why="members.json corrupt")
                        return
                row = self.store.proto_compile_unit(
                    name, mdecls, owner=self.module,
                    born=(self.journal.session if self.journal
                          else None))
                if row is None:
                    return
                dst = self._proto_skill_path(name)
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(stg.read_text(encoding="utf-8"),
                               encoding="utf-8")
                self.journal.row("protocol", "provisioned", intent=name,
                                rev=row["rev"], members=len(mdecls),
                                by="unit-compiled")
                self._render_proto_skill_home(name)
                self._seed_proto_spec(name)
                kp = self._compile_proto_keyset(name)   # M26 register-is-compile
                self.channel.broadcast(self._intents_frame())
                self._say_engine(
                    f"Protocol '{name}' compiled and live as one "
                    f"booklet ({len(mdecls)} members ride it; solo "
                    f"firing locked — enter via Start)."
                    + (f" Its own Stream Deck set '{name}' is "
                       f"compiled: Start / Approve / Interrupt / "
                       f"Shutdown, member keys, and a Status dial — "
                       f"restart the Stream Deck app once, then drag "
                       f"keys from the '{name}' sidebar group."
                       if kp else ""))
                # CASELAW 50: approval result only reaches the WS
                # pane, the host conversation never learns it — in
                # the rework/re-approval ring the agent is waiting
                # on exactly this word before it dares settle; skip
                # the inject and it burns through the timeout
                # (live-fire precedent 2026-08-15: missed by 8 min)
                self._inject(f"Protocol '{name}' registration "
                             f"approved, booklet live — settle any "
                             f"in-flight diagnosis/rework orders.")
                return
            decl_dir = wspace.find(
                instance_home(self.workspace, self.module), name)
            decl = {}
            if decl_dir is not None:
                decl, _ = wspace.read_decl(decl_dir)
                decl = decl or {}
            self.store.compile_delivery(name)
            self.store.intent_revise(name, status="provisioned")
            self._touch(name)              # new unit enters the container directly (container law)
            self._solo_refresh()           # roster changed, re-render home (seat unchanged)
            self._compile_intents_keyset()  # M26: roster change re-compiles the keyset
            self.journal.row("intent", "provisioned", intent=name,
                            by="workspace-registered")
            self._say_engine(f"'{name}' registered and live — in the "
                             f"IME, and its key joined the IntentOS · "
                             f"Intents deck set (one Stream Deck app "
                             f"restart shows it in the sidebar). "
                             f"Further disk changes need a fresh "
                             f"workspace_submit.")
            self._inject(f"'{name}' registration approved and live "
                         f"(rev via intent_get) — settle any in-flight "
                         f"diagnosis/rework orders.")
            self.channel.broadcast(self._intents_frame())
        elif verb == "provision_protocol":
            # M20 §2: human approves the full skill text → staged
            # goes live + the skill rendering lands on disk
            # (utility territory); interactive also renders the
            # home skill in passing; if the executor already has
            # members, the execution seat is recast (a revision
            # swaps the script)
            stg = (self.workspace / defaults.RUNTIME_DIRNAME / "staging"
                   / f"protocol-{name}" / "skill.md")
            if not stg.is_file():
                self.journal.row("protocol", "orphan-gate", intent=name,
                                task=t["id"])
                return
            row = self.store.proto_approve(name)
            if row is None:
                return
            dst = self._proto_skill_path(name)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(stg.read_text(encoding="utf-8"),
                           encoding="utf-8")
            self.journal.row("protocol", "provisioned", intent=name,
                            rev=row["rev"], subtype=row["subtype"],
                            hash=(row["skill_hash"] or "")[:12])
            self._render_proto_skill_home(name)   # render the draft at the maintenance seat
            self._seed_proto_spec(name)           # v14: protocols are fully bracket-form
            kp2 = self._compile_proto_keyset(name)  # M26 register-is-compile
            self.channel.broadcast(self._intents_frame())
            self._say_engine(
                f"Protocol '{name}' live (rev {row['rev']})."
                + (f" Stream Deck set '{name}' recompiled (controls "
                   f"+ members + Status dial); a roster change needs "
                   f"one Stream Deck app restart to show in the "
                   f"sidebar." if kp2 else ""))
        elif verb == "reprovision":
            # Rework reinstatement: sim passing auto-returns to
            # provisioned — reinstatement needs no approval: the
            # existing authority already granted it, and the
            # revision channel was approval-free to begin with
            if it is None or it["status"] != "draft":
                return
            self.store.compile_delivery(name)
            self.store.intent_revise(name, status="provisioned")
            self._touch(name)
            self._solo_refresh()               # roster changed, re-render home (seat unchanged)
            self._compile_intents_keyset()     # M26: roster change re-compiles the keyset
            self.journal.row("intent", "reprovisioned", intent=name,
                            by="sim-passed")
            self._say_engine(f"'{name}' passed sim — auto-reinstated "
                             f"(rework → repair → validate → "
                             f"reinstate).")
            self.channel.broadcast(self._intents_frame())

    # ---- chain runner (pump; INTENT_SPEC §6) --------------------------

    def _task_dir(self, tid: int) -> Path:
        d = self.workspace / defaults.RUNTIME_DIRNAME / "tasks" / str(tid)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _pump(self) -> None:
        self._reap_overdue()
        self._stall_watch()
        self._inject_ack()
        # §2h one-ring-per-seat (queue law): if a seat has a live
        # ring running, don't deliver the next one; an empty seat
        # delivers only the queue head — queue_for is already
        # sorted by the priority law, so cutting in line works
        # naturally. Queuing doesn't burn the clock: TASK_TIMEOUT
        # counts from delivery (running), not from enqueue.
        for seat in (self.module,):
            if self.store.seat_running(seat) is not None:
                continue                    # §2h one-ring-per-seat (resident pane)
            q = self.store.queue_for(seat)
            if not q:
                continue
            t = q[0]
            step = self._node_of(t)
            if step.get("kind") != "deliver":
                continue                    # queue head isn't a deliver ring, skip this tick
            if (seat == self.module and self.host is not None
                    and self.host.alive() and not self.host.trusted()):
                self._wizard_hint()         # withholding delivery isn't a failure; auto-resumes once the wizard is answered
                continue
            self._deliver(t, step)
        # §2m v14: x·solo spins up on demand — stateless, one-shot-
        # per-ring, no reason to serialize, deliver as soon as
        # queued (same-intent collision guard lives at the trigger
        # point's in-flight de-dup / surgery lock)
        for t in self.store.queue_for(defaults.XSOLO_SEAT):
            step = self._node_of(t)
            if step.get("kind") == "deliver":
                self._deliver(t, step)
        # M26 §3: protocol instance seats — one seat per protocol,
        # parallel across seats; one bracket per seat (seat_running
        # means the bracket is in flight, no second delivery).
        # After delivering, flush each seat's envelope queue
        # (booting doesn't drop text, steps wait for the bracket).
        for p in self.store.protos(status="provisioned",
                                   subtype="interactive"):
            seat = defaults.XPROTO_PREFIX + p["name"]
            if self.store.seat_running(seat) is not None:
                continue
            q = self.store.queue_for(seat)
            if q:
                step = self._node_of(q[0])
                if step.get("kind") == "deliver":
                    self._deliver(q[0], step)
        for inst in list(self._xhosts.values()):
            if isinstance(inst, ProtoInstance):
                inst.flush()
                if (inst.step_state == "running"
                        and time.monotonic() - inst.last_output
                        > defaults.STEP_QUIET_S):
                    # P1-i quiet-timeout fallback (display-only,
                    # carries no weight, doesn't touch the task
                    # ledger): if the host forgets to call
                    # step_done, the Step bar shouldn't stay stuck
                    # blue forever
                    inst.step_state = "idle"
                    self.journal.row("protocol", "step-quiet",
                                    intent=inst.pname,
                                    member=inst.step_name)

    def _reap_overdue(self) -> None:
        """Timeout law v1: running (delivered, not yet settled)
        rings that time out are ruled failed — the verdict rides
        with the receipt (cancel receipts carry the same duty, both
        sides close the context loop): the executor gets an
        envelope, the issuer follows the route (user chain = status
        update + one line on the conversation pane; agent chain —
        executor is the issuer, envelope already delivered)."""
        seats = [self.module, defaults.XSOLO_SEAT]
        rows = [t for s in seats
                for t in self.store.overdue(s, defaults.TASK_TIMEOUT_S)
                # M20 §2 timeout-law exception: brackets don't
                # consume TASK_TIMEOUT (gated sets-no-clock
                # precedent) — how long a multi-round interaction
                # takes is the human's business. Retry and
                # consolidate orders follow the same law: both are
                # conversational sidecar orders that may grill the
                # user for as long as it takes.
                if not str(t.get("spec") or "").startswith("protocol:")
                and str(t.get("spec") or "") not in ("retry",
                                                     "consolidate")
                # P1-a gate clock: a gate card hanging on the seat
                # means it's waiting on a human — don't reap it
                # (when the gate closes, _gate_wait has already
                # re-timestamped the ring)
                and not self._gate_busy.get(str(t.get("executor") or ""))]
        for t in rows:
            self.store.task_update(t["id"], status="failed")
            # timeout folds into fail (M12 has no third state): the
            # ledger split follows the node's attribute, downstream
            # follows the on_fail edge (e.g. qual·回炉 n1 timeout =
            # jump back to rework)
            nxt = self._settle(t, "fail", outcome_text="timeout")
            self.journal.row("chain", "timeout", task=t["id"],
                            intent=t.get("intent"))
            if (t.get("executor") == self.module
                    and self.host is not None and self.host.alive()
                    and self.host.trusted()):
                # §2m v9: verdict-rides-with-receipt only holds for
                # the resident pane — x· seats have no listening
                # pane (process is dead), the verdict goes via the
                # conversation pane + ledger + card
                self._inject(defaults.TIMEOUT_LINE.format(
                    tid=t["id"], mins=defaults.TASK_TIMEOUT_S // 60))
            extra = (f" (chain continues: task {nxt['id']})" if nxt
                     else " — the intent can be triggered again.")
            self._say_engine(f"task {t['id']} (intent "
                             f"{t.get('intent') or '-'}) timed out — "
                             f"ruled failed{extra}")
            self._task_bcast()

    def _deliver(self, t: dict, step: dict) -> None:
        """kind=deliver: render the package into the task
        directory, the envelope carries only a pointer (delivery
        law). Host absent = breakpoint, never silent. ref defaults
        to the chain's intent. Polymorphism lives in the node's
        template attribute (M12: spec-name if-else moved into the
        edge table) — package / sim / xsolo / protocol / debug /
        retry-fulfill / consolidate / surgery variants."""
        name = str(step.get("ref") or t.get("intent"))
        tpl = step.get("template") or "package"
        execu = str(t.get("executor") or "")
        xp = execu.startswith(defaults.XPROTO_PREFIX)
        if xp:
            host = self._xhost(execu[len(defaults.XPROTO_PREFIX):])
        else:
            host = self.host
        # A bracket ring's body of record is the protocol row, not
        # the intent row (M20 §2); a consolidate order may target
        # either species (2026-08-25)
        proto = (self.store.proto_get(name)
                 if tpl in ("protocol", "consolidate") else None)
        it = self.store.intent(name)
        if ((it is None and proto is None) or host is None
                or not host.alive()):
            # graceful fail (user ruling 2026-08-11): a breakpoint
            # isn't silent — the cause is written into the journal,
            # the conversation pane speaks up (WS doesn't depend on
            # the host, this line reaches the human even if the
            # host is down). Doesn't go through settle: a ring that
            # never got delivered has no outcome, settle only
            # closes the books on rings that actually ran.
            why = (f"intent '{name}' has no ledger row"
                   if it is None and proto is None else
                   ("claude CLI not on PATH (headless host cannot "
                    "start)" if xp else "host absent"))
            self.store.task_update(t["id"], status="failed")
            self.journal.row("chain", "breakpoint", task=t["id"],
                            reason=f"deliver: {why}")
            self._say_engine(f"task {t['id']} (intent "
                             f"{t.get('intent') or t['spec']}) could "
                             f"not be delivered: {why} — ruled failed"
                             + ("; trigger again once the host is back."
                                if it is not None
                                else "; see journal (breakpoint)."))
            self._task_bcast()
            return
        if (it is not None
                and tpl in ("xsolo", "package", "sim")
                and not self._prelude_gate(t, it)):
            return                      # prelude running / just ruled dead — skip delivery this tick
        prev = self.store.task(t.get("origin") or -1) or {}
        prec = self.store.record_for(t.get("origin") or -1) or {}
        if tpl == "protocol":
            # M20 §2 bracket opening: skill body + member roster +
            # bracket discipline. Aggregate warm-up (user's idea
            # 2026-08-16, landed 08-17): the engine reads the
            # ledger by member declaration and renders it into the
            # package — the skill only declares the aggregate, the
            # host seat opens the booklet warm, saving the round
            # trips of per-member intent_get queries.
            roster = []
            home = instance_home(self.workspace, self.module)
            pdir = wspace.find(home, name)
            for m in (proto["members"] or []):
                mi = self.store.intent(m)
                if mi is None:
                    continue
                # Tool directory: v17 layout = members/<name>/tools
                # inside the booklet; legacy layout (member once
                # had its own repo) falls back by name — if
                # neither exists, say so honestly
                mdir = (wspace.member_dir(pdir, m) if pdir is not None
                        else None)
                if mdir is None or not mdir.is_dir():
                    mdir = wspace.find(home, m)
                tooldir = (str(mdir / wspace.TOOLS_DIR)
                           if mdir is not None
                           and (mdir / wspace.TOOLS_DIR).is_dir()
                           else "(no registered tools)")
                roster.append(defaults.PROTO_ROSTER_ITEM.format(
                    name=m,
                    title=(f"({mi['title']})" if mi.get("title")
                           else ""),
                    scenario=mi.get("scenario") or "(unset)",
                    tooldir=tooldir,
                    steps=mi.get("steps") or "(empty)",
                    acceptance=(mi.get("instructions")
                                or defaults.XSOLO_ACCEPT_DEFAULT)))
            body = defaults.PROTOCOL_PACKAGE_MD.format(
                name=name, tid=t["id"],
                input=str(t.get("payload") or "").strip() or "(none)",
                members=("、".join(proto["members"])
                         or "(none — free-form multi-round)"),
                # ·启 made concrete (user ruling 2026-08-24):
                # opening is a system step, the booklet declares
                # its content (prep); empty = default greeting-
                # then-standby
                prep=(str(proto.get("prep") or "").strip()
                      or defaults.PROTO_PREP_NONE),
                roster=("\n".join(roster)
                        or defaults.PROTO_ROSTER_NONE),
                skill=self._proto_skill(name))
        elif tpl == "xsolo":
            # §2m v9 standalone intent: no single skill source,
            # steps (≤600 chars, pure mechanics) ride the ring
            # whole — the package is the entire instruction set
            body = defaults.XSOLO_PACKAGE_MD.format(
                name=name, title=(it or {}).get("title") or "",
                user_input=(t.get("payload")
                            or "(none — follow defaults)"),
                materials=self._materials_md(t),
                methods=self._methods_md(name),
                steps=((it or {}).get("steps")
                       or "(steps missing — settle failed naming it)"),
                grammar=defaults.E_GRAMMAR,
                acceptance=(it or {}).get("instructions")
                or defaults.XSOLO_ACCEPT_DEFAULT)
        elif tpl == "debug" or t.get("spec") == "debug":
            # Rework diagnosis node (qual·回炉.n0): reason = the
            # history line from the jump origin — the raw error
            # text from a firing failure / sim's complaint. (For
            # legacy debug-chain in-flight rings, reason lives in
            # payload, rendered the same as a fallback.)
            body = defaults.DEBUG_MD.format(
                tid=t["id"], name=it["name"], origin=t.get("origin"),
                mins=int(defaults.TASK_TIMEOUT_S // 60),
                reason=(prec.get("outcome") or t.get("payload")
                        or "(none — check the journal)"))
        elif tpl == "retry-fulfill":
            # Retry reshaped (2026-08-25): autopsy + redo in the
            # sidecar seat, settles for real (no acceptance
            # bracket); the consolidate offer follows settlement
            body = defaults.RETRY_FULFILL_MD.format(
                tid=t["id"], name=it["name"], origin=t.get("origin"),
                reason=t.get("payload") or "(none — judge from the "
                                           "previous record)",
                prev_status=prev.get("status", "?"),
                prev_outcome=prec.get("outcome") or "(no record)",
                prev_pkg=self._task_dir(int(t.get("origin") or 0))
                / "package.md")
        elif tpl == "consolidate":
            # Consolidate order (2026-08-25): the asset is already
            # suspended; the package tells the sidecar how to fold
            # the lesson in and bring it back through the gate
            ckind = "protocol" if proto is not None else "intent"
            org = int(t.get("origin") or 0)
            body = defaults.CONSOLIDATE_MD.format(
                tid=t["id"], name=name, kind=ckind,
                evidence=(f"origin task {org} — see "
                          f"runtime/tasks/{org}/ and the record"
                          if org else "(none on file — ask the user)"))
        elif tpl == "surgery":
            # §2g surgery table: failure evidence + user note +
            # residue map (bus transcript)
            body = defaults.XSOLO_SURGERY_MD.format(
                tid=t["id"],
                name=it["name"], origin=t.get("origin"),
                origin_dir=self._task_dir(int(t.get("origin") or 0)),
                fail=prec.get("outcome") or prev.get("status")
                or "(no receipt)",
                note=t.get("payload") or "(none — judge from the "
                                         "failure evidence)",
                residue=self._residue_md(int(t.get("origin") or 0)))
        else:
            # caveats section retired (user ruling 2026-08-25): the
            # table is a fossil with no writer — lessons re-enter as
            # conditional clauses in steps via sidecar revision
            # retry-package/RETRY_SECTION has retired along with
            # the R5 bracket form (steer re-delivers dead rings) —
            # this branch now has only the package / sim variants
            banner = defaults.SIM_BANNER if tpl == "sim" else ""
            body = banner + defaults.PACKAGE_MD.format(
                name=it["name"], title=it.get("title") or "",
                scenario=(it.get("scenario") or "(expressed by mode)")
                if not it.get("absorbed_into") else "(expressed by mode)",
                user_input=t.get("payload") or "(none — follow defaults)",
                steps=(it.get("steps")
                       or "(empty — legacy item, act on the R criteria)"),
                # sim validates E; without criteria riding the
                # ring it has no acceptance surface (live-fire
                # precedent 2026-08-16: the sim seat was forced to
                # invent its own standard)
                acceptance=(it.get("instructions")
                            or defaults.XSOLO_ACCEPT_DEFAULT),
                materials=self._materials_md(t))
        pkg = self._task_dir(t["id"]) / "package.md"
        pkg.write_text(body, encoding="utf-8")
        # Settlement law (INTENT_SPEC §6): after delivery a ring
        # sits at running, only lands done/failed once the executor
        # settles it via MCP task_done. Flip status before
        # injecting so the pump doesn't redeliver.
        # M15: delivery stamps the window's start — delivered_at is
        # the **delivery** instant, not the enqueue instant; time
        # spent queuing doesn't count as execution. host_session
        # may not be known yet at this instant (NULL); once
        # learned the next ring picks it up naturally, older rings
        # aren't backfilled (the coordinate is a snapshot, not a
        # fill-in-later target).
        self.store.task_update(t["id"], status="running",
                               delivered_at=time.strftime(
                                   "%Y-%m-%d %H:%M:%S"),
                               host_session=self._host_session)
        if xp:
            if getattr(host, "kind", "") == "pty":
                # M26: protocol instances are PTY resident seats —
                # the envelope carries only a pointer (the TUI
                # composer chokes on long-text paste; CASELAW 12's
                # two-tick discipline)
                line = defaults.PROTO_TASK_LINE.format(
                    tid=t["id"], name=(it or proto)["name"], path=pkg)
                host.deliver(t["id"], line)
                self.journal.row("chain", "deliver", task=t["id"],
                                intent=(it or proto)["name"])
                self._task_bcast()
                return
            # Second-pass fix (2026-08-17): envelope carries no
            # path — when the full text already rides the ring,
            # writing "package: <path>" at the top just makes the
            # executor seat Read it on sight anyway, paying for a
            # wasted round trip (live-fire precedent 2026-08-16).
            line = defaults.TASK_LINE_INLINE.format(
                tid=t["id"], name=(it or proto)["name"])
            # headless: spins a fresh process per ring, **the
            # session id it hands out is this ring's own
            # coordinate** (precedent 2026-08-15: the id stamped
            # above is the host's own sid, wrong for the executor —
            # transcript cut comes up empty, the receipt is left
            # with only a duration).
            # The delivery law forks by host form (user ruling
            # 2026-08-16, saves a round trip): "envelope carries
            # only a pointer" was set for PTY seats — the TUI input
            # box can't paste long text. headless is
            # `claude -p <prompt>`, where the prompt itself is a
            # full text field; handing it a pointer just forces it
            # to Read its own instructions first (live-fire
            # precedent: that was the first of three round trips,
            # paying for a wasted 47k baseline). Deliver the full
            # text directly.
            try:
                line = (line + "\n\n"
                        + Path(pkg).read_text(encoding="utf-8"))
            except OSError:
                # If it can't be read, fall back to pointer form
                # (the path is actually needed here now) — don't
                # crash
                line = defaults.TASK_LINE.format(
                    tid=t["id"], name=(it or proto)["name"], path=pkg)
            sid = host.deliver(t["id"], line)
            if isinstance(sid, str) and sid:
                self.store.task_update(t["id"], host_session=sid)
        else:
            line = defaults.TASK_LINE.format(
                tid=t["id"], name=(it or proto)["name"], path=pkg)
            self._inject(line)
        self.journal.row("chain", "deliver", task=t["id"],
                        intent=(it or proto)["name"])
        self._task_bcast()

    def _proto_skill_path(self, name: str) -> Path:
        return wspace.utility_skill_path(self.workspace, name)

    def _proto_skill(self, name: str) -> str:
        try:
            return self._proto_skill_path(name).read_text(
                encoding="utf-8")
        except OSError:
            return ("(skill rendering absent — check the journal / "
                    "re-approve the protocol)")

    def _seat_token(self, seat: str) -> str:
        """Engine-minted identity for an execution seat (one token
        per seat, redraws don't change it)."""
        for k, v in self._tokens.items():
            if v == seat:
                return k
        tok = uuid.uuid4().hex
        self._tokens[tok] = seat
        return tok

    def _solo_token(self) -> str:
        return self._seat_token(defaults.XSOLO_SEAT)

    def _solo_refresh(self) -> None:
        """PERM_ALLOW ledger changes → only re-render the x·solo
        home (idempotent, path unchanged); the seat object is kept
        — swapping it out would drop the test double / in-flight
        process along with it, re-rendering the file is enough to
        take effect (headless spins a new process per ring, reads
        it fresh each time)."""
        provision_solo_home(self.workspace, token=self._solo_token())

    def _xhost(self, pname: str):
        # One critical section around get/spawn/put (audit
        # 2026-08-25): this was a bare check-then-create while every
        # other shared table in this class already had a lock. The
        # HTTP trigger thread and the pump beat both call it for the
        # same booklet, and each raced copy provisioned a home and
        # spawned a **real CLI** — one duplicate resident seat, one
        # orphan process, and the bracket package delivered into
        # whichever object lost the write. Provisioning under the
        # lock is acceptable: the only other holder is the pump.
        with self._xhost_lock:
            return self._xhost_locked(pname)

    def _xhost_locked(self, pname: str):
        h = self._xhosts.get(pname)
        if h is not None:
            return h
        if pname == defaults.XSOLO_NAME:
            # §2m v9 general-purpose execution seat: the execution
            # seat for standalone intents (sonnet pinned + fixed
            # thinking budget). Ledger changes re-render the home
            # via _solo_refresh, the seat object is minted only
            # once.
            home = provision_solo_home(self.workspace,
                                       token=self._solo_token())
            h = HeadlessHost(home, defaults.XSOLO_MODEL,
                             self.workspace / defaults.RUNTIME_DIRNAME
                             / "tasks",
                             perm_tool=defaults.XPERM_TOOL,
                             tools=defaults.XSOLO_CLI_TOOLS,
                             # P1-a: the allow floor rides the flag
                             # (headless home has no trust record,
                             # settings' allow isn't honored by the
                             # harness) — settlement/toolkit reads
                             # don't get stuck on a gate; the
                             # PERM_ALLOW ledger is merged in inside
                             # solo_allow_rules
                             allow_tools=solo_allow_rules(
                                 self.workspace))
            if not self.spawn_host:
                h._cli = None   # fuse: an embedded/test engine never actually spawns the CLI
            self._xhosts[pname] = h
            return h
        # M26 §3: resident seat for an interactive protocol — one
        # household per protocol (home is permanent, permissions
        # settle into its own settings.local.json; the process is
        # lazily spawned, Shutdown stops it, Start respawns it in
        # place). Model follows the engine (sonnet law).
        p = self.store.proto_get(pname)
        if (p is None or p["status"] != "provisioned"
                or p["subtype"] != "interactive"):
            return None
        seat = defaults.XPROTO_PREFIX + pname
        home = provision_proto_home(self.workspace, pname,
                                    token=self._seat_token(seat))
        inst = ProtoInstance(
            pname, home, self.model,
            on_output=(lambda data, s=seat:
                       self.channel.push_cli(data, instance=s)),
            spawn=self.spawn_host,
            step_ready=(lambda n=pname:
                        self._bracket_of(n, queued=False) is not None))
        self._xhosts[pname] = inst
        self.journal.row("protocol", "instance", intent=pname,
                        home=str(home), spawned=self.spawn_host)
        return inst

    def _methods_md(self, name: str) -> str:
        """M section (user ruling 2026-08-16): **registered items
        are filled in by the engine, the agent never writes a
        path**. E only quotes a name (e.g. "呼 练琴表"), the real
        address gets resolved here — the execution seat doesn't
        have to go find it, and procedure doesn't have to hardcode
        an absolute path either (the root of defect ②). External
        target directories aren't in this list: that's the
        intent's own knowledge, written into steps/conventions —
        the engine doesn't overreach to fill it in for the intent."""
        rows: list[str] = []
        wsdir = wspace.find(
            instance_home(self.workspace, self.module), name)
        if wsdir is not None:
            decl, _ = wspace.read_decl(wsdir)
            for nm in ((decl or {}).get("tools") or []):
                hits = sorted((wsdir / wspace.TOOLS_DIR)
                              .glob(f"{nm}.*"))
                if hits:
                    rows.append(f"- `{nm}` → {hits[0]}")
        tk = self.workspace / "toolkit"
        if tk.is_dir():
            rows.append(f"- shared toolkit (read-only) → {tk}")
        return "\n".join(rows) or defaults.XSOLO_METHODS_NONE

    def _proc_names(self, it: dict) -> list[str]:
        """The prelude roster an intent declares (v18: procedures
        column, JSON array)."""
        try:
            v = json.loads(str(it.get("procedures") or "[]"))
        except ValueError:
            return []
        if not isinstance(v, list):
            return []
        return [str(x).strip() for x in v if str(x).strip()]

    def _prelude_gate(self, t: dict, it: dict) -> bool:
        """v18 prelude gate (user ruling 2026-08-23: procedure = an
        optional item an intent declares): for a ring whose intent
        declares procedures, the engine runs the prelude before
        delivery — on a background thread (the pump doesn't carry
        a hard 30s timeout), once done it writes prelude.ok and
        delivers normally on the next tick; on failure it reports
        to the human and withholds delivery (the intent is
        innocent, stays live, no surgery opened).
        Returns True = clear to deliver this tick."""
        procs = self._proc_names(it)
        if not procs:
            return True
        tid = t["id"]
        if (self._task_dir(tid) / "prelude.ok").is_file():
            return True
        if tid in self._preluding:
            return False
        self._preluding.add(tid)
        threading.Thread(
            target=self._prelude_run,
            args=(tid, str(t.get("payload") or ""), procs,
                  str(t.get("intent") or it.get("name") or "")),
            daemon=True).start()
        return False

    def _prelude_run(self, tid: int, input_: str, procs: list[str],
                     intent: str) -> None:
        """Prelude execution half (background thread): steps
        through procrun's three-part transaction, materials land
        in the task directory's materials.jsonl (_materials_md
        renders it into the package)."""
        td = self._task_dir(tid)
        try:
            for n in procs:
                spec = defaults.PHYS_PROCEDURES.get(n)
                entry = (Path(__file__).parent / "kernel" / "procs"
                         / spec["entry"]) if spec else None
                if entry is None or not entry.is_file():
                    ok, err = False, f"procedure '{n}' not in the " \
                                     f"engine library"
                else:
                    ok, err, _mats = procrun.run_step(
                        entry, td, input_=input_,
                        timeout=defaults.PROC_TIMEOUT_S,
                        say_max=defaults.PROC_SAY_MAX)
                if not ok:
                    # Physical-layer faults are reported to the
                    # human, no surgery opened (carried forward
                    # from the 2026-08-16 ruling): the ring is
                    # ruled dead directly, doesn't go through the
                    # settle edge (a ring that never ran has no
                    # outcome), the intent stays live
                    self.store.task_update(tid, status="failed")
                    self.journal.row("chain", "breakpoint", task=tid,
                                    reason=f"prelude {n}: {err}")
                    self._say_engine(
                        f"task {tid} (intent {intent}) not delivered: "
                        f"prelude procedure '{n}' failed — {err}. "
                        f"Physical-layer fault, reported to you; the "
                        f"intent is NOT suspended — fix the "
                        f"environment and trigger again.")
                    self._task_bcast()
                    return
                self.journal.row("procedure", "prelude", task=tid,
                                intent=intent, proc=n)
            (td / "prelude.ok").write_text("ok\n", encoding="utf-8")
        finally:
            self._preluding.discard(tid)

    def _materials_md(self, t: dict) -> str:
        """Materials section: materials the prelude absorbed
        render into the package — files get their post-absorption
        path, text is embedded inline. Since v18 materials land in
        this ring's own task directory (the prelude runs first at
        trigger time); materials from other rings on the chain are
        still collected (a retry ring inherits the origin ring's
        old materials)."""
        rows: list[str] = []
        for r in self.store.chain(t["chain_id"]):
            mf = (self.workspace / defaults.RUNTIME_DIRNAME / "tasks"
                  / str(r["id"]) / "materials.jsonl")
            if not mf.is_file():
                continue
            for ln in mf.read_text(encoding="utf-8").splitlines():
                try:
                    m = json.loads(ln)
                except ValueError:
                    continue
                label = m.get("label") or "material"
                if m.get("kind") == "file":
                    rows.append(f"- [{label}] file: {m.get('path')}")
                else:
                    txt = str(m.get("text") or "")
                    if "\n" in txt:
                        rows.append(f"- [{label}]\n\n```\n{txt}\n```")
                    else:
                        rows.append(f"- [{label}] {txt}")
        if not rows:
            return ""
        return defaults.MATERIALS_SECTION.format(rows="\n".join(rows))

    def _mcp_call(self, f: dict) -> dict:
        """The agent's settlement verbs. Refusals come with a
        reason (CASELAW 19); the engine records mechanical facts,
        the agent reports only the semantic truth value (CASELAW
        26) — verdict never lives here. caller = resolved from
        token (identity minted by the engine; one endpoint, view
        tailored per caller). **Token is identity recognition, not
        a security boundary** (threat-model ruling 2026-08-12, see
        kernel/netguard): tolerating a missing token as "home" is
        deliberate — the external surface is sealed off by the
        loopback guard, local-machine processes are outside the
        threat model."""
        verb = f.get("verb")
        token = str(f.get("token") or "")
        if token and token not in self._tokens:
            return {"error": "caller token unrecognized — the engine "
                             "has changed terms; restart this host "
                             "(the bridge remounts via .mcp.json) and "
                             "retry"}
        caller = self._tokens.get(token, self.module)
        # report_to_user was tried and killed (M14, live-fire
        # ruling 2026-08-12): it directly conflicts with the role
        # call to "tell the user the result right in the
        # conversation" — if the agent trusts the tool description
        # it copies output into a pane a human might not even be
        # watching and that vanishes on disconnect. There is
        # exactly one conversation, the terminal; the engine's own
        # voice (_say_engine) is unaffected.
        if verb == "perm_gate":
            # §2i execution-seat permission gate (the engine end of
            # --permission-prompt-tool): requests outside auto-
            # mode's allowance land on a card and wait for a human;
            # Always = the rule goes into the PERM_ALLOW ledger
            # (config.json, effective across all seats, user can
            # hand-edit); timeout defaults to deny (fail-safe).
            tool = str(f.get("tool_name") or "?")[:80]
            tin = f.get("input") if isinstance(f.get("input"), dict) \
                else {}
            if tool in self._perm_grants:
                # Ledger hit (this session just approved Always, or
                # it was hand-written into config)
                self.journal.row("xgate", "perm", seat=caller,
                                tool=tool, result="grant")
                return {"behavior": "allow", "updatedInput": tin}
            digest = json.dumps(tin, ensure_ascii=False)[:280]
            # Always mints a bare tool-name rule. No hardcoded
            # per-tool exception (user ruling 2026-08-25): the
            # customization surface for "never bank this" is
            # never_allow in MODULE_POLICY, enforced at
            # _perm_capped on every path.
            rule = tool
            opts = [{"action": "allow", "label": "Allow once"}]
            if rule:
                opts.append({"action": "always",
                             "label": f"Always allow ({rule})"})
            opts.append({"action": "deny", "label": "Deny"})
            ans = self._gate_wait(
                "perm", f"Permission request: {caller}",
                f"{tool} — {digest}\n\"Always allow\" banks the rule "
                f"in this seat's own settings (the CLI keeps it) and "
                f"in config.json (PERM_ALLOW, every seat) — later "
                f"orders included; edit either to revoke.",
                opts, defaults.XGATE_WAIT_S, instance=caller)
            act = (ans or {}).get("action")
            self.journal.row("xgate", "perm", seat=caller, tool=tool,
                            result=act or "timeout")
            if ans is not None and act in ("allow", "always"):
                out = {"behavior": "allow", "updatedInput": tin}
                if act == "always" and rule:
                    kept = self._perm_capped([rule])
                    if kept:
                        self._grant_rules(kept)
                        # The CLI banks its own copy (live-fire
                        # 2026-08-25, probes D/E/F): the prompt-tool
                        # result honors updatedPermissions on the
                        # same path the native card's don't-ask-again
                        # row uses, so the rule survives in this
                        # seat's settings and the next order raises
                        # no card. Minted from tool_name rather than
                        # relayed, because this wire carries no
                        # permission_suggestions (probe D) — safe
                        # because tool_name is the CLI's, and the
                        # prompt tool is off the model's own tool
                        # face (probe J: the executor cannot reach
                        # perm_gate to forge one).
                        out["updatedPermissions"] = [{
                            "type": "addRules",
                            "rules": [{"toolName": kept[0]}],
                            "behavior": "allow",
                            "destination": "localSettings"}]
                        out["decisionClassification"] = "user_permanent"
                return out
            return {"behavior": "deny",
                    "message": ("denied by the user" if ans is not None
                                else f"no answer within "
                                f"{defaults.XGATE_WAIT_S:.0f}s — if "
                                f"this fails the order, name this "
                                f"permission in the summary")}
        if verb == "ask_user":
            # §2i form (the execution seat's only other
            # interaction mode): a multiple-choice question comes
            # back as a card.
            q = str(f.get("question") or "").strip()
            raw_opts = f.get("options")
            if not q or not isinstance(raw_opts, list) or not raw_opts:
                return {"error": "ask_user: question + options required "
                                 "(multiple choice, ≤12 options)"}
            # Cap of 12 (live-fire precedent 2026-08-23 late night:
            # the practice-log booklet's 9 pieces hit the old cap
            # of 6 and got forced into voice narration; the panel
            # buttons flex-wrap into two rows and fit, and the ask
            # card always has a typing line — an off-list answer
            # can always be typed)
            opts = [str(o).strip()[:80] for o in raw_opts
                    if str(o).strip()][:12]
            if not opts:
                return {"error": "ask_user: all options empty"}
            ans = self._gate_wait(
                "ask", f"Executor question: {caller}", q[:400],
                [{"action": f"opt:{i}", "label": o}
                 for i, o in enumerate(opts)],
                defaults.XGATE_WAIT_S, instance=caller)
            self.journal.row("xgate", "form", seat=caller,
                            result=(ans or {}).get("action") or "timeout")
            # Free-text catch (live-fire precedent 2026-08-23: the
            # panel's typing line sends action=line, previously
            # rejected as an invalid choice — the agent had to
            # invent a fake "manual entry" button)
            if (ans is not None and str(ans.get("action")) == "line"
                    and str(ans.get("data") or "").strip()):
                return {"choice": str(ans["data"]).strip()[:400],
                        "typed": True,
                        "note": "free-form answer typed by the user "
                                "(not one of the offered options)"}
            if ans is None or not str(ans.get("action", "")
                                      ).startswith("opt:"):
                return {"error": "no answer in time / no valid choice "
                                 "— take the default path, or settle "
                                 "failed naming this missing decision"}
            i = int(str(ans["action"]).split(":", 1)[1])
            return {"choice": opts[i]}
        if verb == "step_done":
            # A lightweight claim inside the bracket (user ruling
            # 2026-08-23, the settlement half of the Step bar): the
            # host calls out once per completed member step — only
            # flips the ledger state, doesn't open or close any
            # task (the whole booklet is still one ledger entry,
            # task_done still refuses).
            if not caller.startswith(defaults.XPROTO_PREFIX) \
                    or caller == defaults.XPROTO_PREFIX \
                    + defaults.XSOLO_NAME:
                return {"error": "step_done is a protocol-seat verb — "
                                 "this seat settles via task_done"}
            pn = caller[len(defaults.XPROTO_PREFIX):]
            inst = self._xhosts.get(pn)
            if not isinstance(inst, ProtoInstance):
                return {"error": f"step_done: no instance for '{pn}'"}
            mem = str(f.get("member") or "").strip() or inst.step_name
            if mem == "·收":
                # Closing-ceremony acknowledgment (user ruling
                # 2026-08-24): releases the wrap-up thread
                inst.wrap_evt.set()
            inst.step_state = "done"
            if mem:
                inst.step_name = mem
            self.journal.row("protocol", "step-done", intent=pn,
                            member=mem or None)
            return {"ok": True, "member": mem}
        if verb == "task_done":
            tid = f.get("task")
            t = self.store.task(tid) if isinstance(tid, int) else None
            if t is None:
                return {"error": f"task_done: no task {tid!r}"}
            if str(t.get("spec") or "").startswith("protocol:"):
                # CASELAW 46 (live-fire precedent 2026-08-15): when
                # two skill texts conflict, the mechanical law
                # wins — the package explicitly said "task_done not
                # needed" and it still got called; a bracket closed
                # via settlement means cleanup never runs → an
                # orphaned ffmpeg for 6 minutes. Brackets are
                # closed by the human (the Shutdown key).
                return {"error": f"task_done: task {tid} is a protocol "
                                 f"bracket — the user closes it with "
                                 f"the Shutdown key; it never settles "
                                 f"via task_done. Wrap up whatever is "
                                 f"in flight, then wait for the user."}
            if t["executor"] != caller:
                return {"error": f"task_done: task {tid} belongs to "
                                 f"{t['executor']}, not you — never "
                                 f"settle another seat's ring"}
            # Retry reshaped (user ruling 2026-08-25): a retry task
            # settles like any ring — no claim/acceptance bracket;
            # the lesson rides the consolidate offer raised after
            # settlement (below).
            if t["status"] != "running":
                return {"error": f"task_done: task {tid} is "
                                 f"{t['status']} — only running "
                                 f"(delivered) rings settle"}
            outcome = f.get("outcome")
            if outcome not in ("ok", "ok_issue", "failed"):
                return {"error": "task_done: outcome must be ok | "
                                 "ok_issue | failed"}
            summary = str(f.get("summary") or "").strip()
            issue = str(f.get("issue") or "").strip()
            if outcome == "ok_issue" and not issue:
                return {"error": "task_done: ok_issue requires issue "
                                 "(one line naming the friction — it "
                                 "feeds the consolidation loop)"}
            # agent-type validator = the settlement itself; the
            # verdict is entirely settle's business (the ledger
            # split follows the node's attribute, reinstate/rework/
            # go-live all live in effect and the edge table — M12:
            # auto-sim is just the next edge from qual·回炉 n0→n1,
            # rework is just the jump-back edge n1→n0, no separate
            # chain needed)
            if outcome in ("ok", "ok_issue"):
                # Three states ride a two-value edge (M12: no
                # third-state edge) — ok_issue routes as ok, the
                # issue lands on the ledger: records/receipt/
                # completion card all carry it, accumulated
                # they're the mechanical feedstock for the
                # consolidate ring (threshold wiring pending)
                self.store.task_update(tid, status="done")
                text = summary or "ok"
                if issue:
                    text = (summary + " " if summary else "")                            + "· issue: " + issue
            else:
                self.store.task_update(tid, status="failed")
                text = "failed: " + (summary or "no explanation")
            self.journal.row("chain", "claim", task=tid, outcome=outcome,
                            summary=summary or None,
                            issue=issue or None)
            nxt = self._settle(t, "ok" if outcome in ("ok", "ok_issue")
                               else "fail", outcome_text=text)
            if (nxt is not None and outcome == "failed"
                    and nxt.get("spec") == FLOW_QUAL_REWORK
                    and nxt.get("seq") == 0):
                self._say_engine(f"'{t.get('intent')}' failed sim — "
                                 f"still suspended; rework diagnosis "
                                 f"reopened (task {nxt['id']}).")
            if (str(t.get("spec") or "") == "retry"
                    and outcome in ("ok", "ok_issue")):
                # Retry reshaped (2026-08-25): settlement is real;
                # the lesson rides a consolidate offer
                self._consolidate_offer(
                    "intent", str(t.get("intent") or ""), tid,
                    extra=f"Retry of '{t.get('intent')}' settled "
                          f"({outcome}) — task {tid}.")
            self._task_bcast()
            # A three-state receipt must also be three-state (live-
            # fire precedent 2026-08-16: ok_issue fell into the
            # else branch, the receipt said "failed" — everything
            # internal was correct (task done, issue into the
            # journal, riding the ok edge), **only the sentence
            # told to the agent was wrong**. The sim seat believed
            # "didn't pass," went to fix a failure that never
            # happened, resubmitted, opened another human gate — a
            # wasted round trip)
            resp = {"ok": True, "task": tid,
                    "status": ("done" if outcome in ("ok", "ok_issue")
                               else "failed")}
            if outcome == "ok_issue":
                resp["note"] = ("ok_issue received: the ring routes as "
                                "ok (chain proceeds); the issue is on "
                                "record — consolidation feed, not a "
                                "failure.")
            if self.store.chain_cancelled(t["chain_id"]):
                # The settlement response carries chain news
                # (context sync): the executor side closes its
                # loop too
                resp["note"] = ("chain cancelled: settlement received, "
                                "chain stops — no further rings")
            return resp
        if verb == "intent_submit":
            return self._intent_submit(f, caller)
        if verb == "workspace_submit":
            return self._workspace_submit(f, caller)
        if verb == "intent_retire":
            return self._intent_retire(f, caller)
        if verb == "intent_update":
            # Removed (user ruling 2026-08-15, §2u): declarative
            # content now lives in the workspace's intent.json —
            # editing it is editing the intent, resubmit to take
            # effect. The directory is source, the ledger is the
            # compiled form; there is no second channel for
            # editing the source of truth.
            return {"error": "intent_update is removed — edit the "
                             "workspace's intent.json (steps/"
                             "acceptance/scenario/procedures/tools "
                             "all live there), then workspace_submit "
                             "to re-register; that's when it takes "
                             "effect"}
        if verb == "caveat_add":
            # Removed (user ruling 2026-08-13; column fossilized
            # 2026-08-25): the task loop IS the precedent
            # mechanism — the sidecar is the reviser now.
            return {"error": "caveat_add is removed — lessons flow "
                             "back through the task loop: fold the "
                             "lesson into the workspace intent.json's "
                             "steps as a conditional clause, then "
                             "workspace_submit; a protocol's lesson "
                             "goes into skill.md and resubmits"}
        if verb == "procedure_submit":
            # Physical-layer ruling (user ruling, night of
            # 2026-08-16): procedure no longer has an agent-side
            # submission channel — it's built into the engine,
            # bound to a keybinding, doesn't even have a gate.
            return {"error": "procedure_submit is retired — a "
                             "procedure is the control protocol's "
                             "physical layer (engine built-in), not "
                             "something an agent writes or submits. "
                             "Upfront context goes into E's first "
                             "hop, or the declared procedures field."}
        if verb in ("protocol_submit", "protocol_register"):
            # Merged into workspace_submit (user ruling 2026-08-15,
            # §2u): the unit is written into the directory,
            # submitted as a whole package, one card approval takes
            # it live. The member roster goes into protocol.json's
            # members field, registration stamps the pointer.
            return {"error": f"{verb} is folded into workspace_submit "
                             f"— write skill.md into the workspace, "
                             f"the declaration into protocol.json, "
                             f"then submit the whole directory by "
                             f"name"}
        if verb == "intent_memory_index":
            return self._intent_index(caller)
        if verb == "intent_search":
            return self._intent_search(f, caller)
        if verb == "intent_catalog":
            return self._intent_catalog(caller, f)
        if verb == "match_protocol":
            return self._match_protocol(f, caller)
        if verb == "intent_detail":
            return self._intent_detail(f, caller)
        if verb == "intent_get":
            return self._intent_get(f, caller)
        return {"error": f"unknown verb {verb!r}"}

    # ---- provision pane (§3c: hot/cold container view, meta vs. detail layering) --------

    def _intent_meta(self, it: dict) -> dict:
        """The meta layer of INTENT_CARD — defined once, shared by
        the index/search/get three panes; steps/tools
        (execution detail) never go in here."""
        m = {"name": it["name"], "title": it.get("title") or "",
             "scenario": it.get("scenario") or "", "rev": it["rev"]}
        if it.get("migrated_to"):
            m["migrated"] = f"migrated → {it['migrated_to']}"
        return m

    def _intent_index(self, caller: str) -> dict:
        """Full flat hot listing (container view) + cold-store
        count. meta exposure scores zero (scoring law); the view is
        tailored to the caller (in the single-instance era, caller
        is home)."""
        rows = self.store.intents(owner=caller, status="provisioned")
        hot = [r for r in rows if r["name"] in self._hot]
        flat = [self._intent_meta(r) for r in hot]
        out = {"ok": True,
               "hot": flat, "cold_count": len(rows) - len(hot),
               "container": f"{len(self._hot)}/{defaults.CONTAINER_CAP}",
               "note": "hot = this session's working set. The hot face "
                       "is the container (kept within cap, cold ones "
                       "sink out naturally, nothing is deleted); to "
                       "find things use intent_search (query, full-"
                       "library recall); details via intent_get"}
        drafts = [r["name"] for r in self.store.intents(owner=caller,
                                                        status="draft")]
        if drafts:
            out["drafts_pending"] = drafts
        return out

    def _intent_catalog(self, caller: str, f: dict | None = None) -> dict:
        """Catalog: flat top-N by usage (class retired 2026-08-25 —
        the sampling axis went with it), rows carry only
        name+scenario (saves tokens). Long tail goes through
        intent_search; total carries the whole library so the
        truncation amount is computable. Zero scoring (walking past
        the shelf doesn't stamp it)."""
        f = f or {}
        if str(f.get("category") or "").strip():
            return {"error": "intent_catalog: the catalog takes no "
                             "filter — it is a flat usage top; to "
                             "find things use intent_search(query)."}
        rows, total = self.store.intent_catalog(
            caller, top=defaults.CATALOG_TOP)
        return {"ok": True, "items": rows, "total": total,
                "note": "catalog = usage top (name + scenario); total "
                        "counts the whole library, the difference is "
                        "the long tail — reach it with intent_search"
                        "(query). Details by name via intent_get "
                        "(part for layered fetch)."}

    # _assign_class / _class_seeds retired (user ruling 2026-08-25):
    # the char-overlap scorer only worked because CJK characters are
    # morphemes — meaningless for English; the class axis is removed,
    # the layout is flat, the DB class column stays a fossil.

    def _match_protocol(self, f: dict, caller: str) -> dict:
        """§2l aggregation sensor: returns the protocol family
        sample pool (member scenarios). Which family does a new
        intent resemble → suggest joining that family or proposing
        graduation; if it resembles none, build normally."""
        scen = str(f.get("scenario") or "")
        pools = self.store.proto_pools()
        if scen:                    # M24 merged track: family pool scored with the same embedding ruler
            qv = vector.embed(scen)
            for p in pools.values():
                p["score"] = round(sum(
                    vector.sim(qv, vector.embed(s))
                    for s in p.get("samples", [])), 4)
        return {"ok": True, "scenario": scen,
                "protocols": pools,
                "note": "resembles a family → propose joining it (the "
                        "executor needs that family re-registered) or "
                        "propose graduation; resembles none → build "
                        "normally."}

    def _vec_of(self, it: dict) -> dict:
        """Intent scenario vector, cached by (name, rev): an edit
        means re-embedding (v2 scenario re-aggregation) — self-
        invalidates via rev, zero hooks, zero disk writes, the
        source of truth always lives in SQLite."""
        rev = int(it.get("rev") or 0)
        hit = self._veccache.get(it["name"])
        if hit is None or hit[0] != rev:
            hit = (rev, vector.embed(it.get("scenario") or ""))
            self._veccache[it["name"]] = hit
        return hit[1]

    @staticmethod
    def _proto_target(migrated_to) -> str:
        """Migration-out pointer → the protocol name it points to
        (retrieval bridge, §2j pointer's second function). Accepts
        both "protocol:X" and "X/member" spellings."""
        s = str(migrated_to or "").strip()
        if s.startswith("protocol:"):
            s = s[len("protocol:"):]
        return s.split("/", 1)[0].strip()

    def _vector_recall(self, caller: str,
                       query: str) -> list[tuple[float, dict]]:
        """Multi-path recall unified (M24): scenario cosine as the
        main path + a name/title hit bonus as the second path.
        Scope = the whole provisioned library (v10: the gate
        manages hotness, not recall); below the SEARCH_MIN_SIM
        threshold, better empty than padded. Sorted descending by
        score."""
        qv = vector.embed(query)
        ql = query.lower()
        out: list[tuple[float, dict]] = []
        for it in self.store.intents(owner=caller, status="provisioned"):
            s = vector.sim(qv, self._vec_of(it))
            if ql in it["name"].lower() \
                    or (ql in (it.get("title") or "").lower()):
                s += 1.0
            if s >= defaults.SEARCH_MIN_SIM:
                out.append((s, it))
        out.sort(key=lambda x: (-x[0], x[1]["name"]))
        return out
    def _intent_search(self, f: dict, caller: str) -> dict:
        """Cold-storage retrieval. No query = mechanical (explicit-
        law filter, filters out hot, agent does the final sort) as
        before; with query = vector (M24 lit up, contract unchanged,
        only adds a protocols column): top25 candidates -> two
        columns -- unpointed ones take 5 into items, pointed ones
        aggregate by pointer and surface <=1 protocol (§2j: pointer
        = retrieval bridge, more members = more likely to surface).
        Rows carry name/title/scenario: a recall doubles as context,
        feeding multi-turn use (v9: one search, two uses); below
        threshold is a legitimate empty-handed result."""
        query = str(f.get("query") or "").strip() or None
        try:
            limit = int(f.get("limit") or defaults.INTENT_SEARCH_LIMIT)
        except (TypeError, ValueError):
            limit = defaults.INTENT_SEARCH_LIMIT
        if query is None:
            rows, total = self.store.intent_search(
                caller, exclude=self._hot, limit=max(1, limit))
            return {"ok": True,
                    "items": [self._intent_meta(r) for r in rows],
                    "total_matched": total, "mode": "mechanical"}
        cand = self._vector_recall(caller, query)
        total = len(cand)
        protos: dict[str, dict] = {}
        items: list[dict] = []
        for s, it in cand[:defaults.SEARCH_RECALL_TOP]:
            tgt = self._proto_target(it.get("migrated_to"))
            if tgt:
                p = protos.setdefault(tgt, {"name": tgt, "score": 0.0,
                                            "hits": []})
                p["score"] += s
                p["hits"].append(it["name"])
            elif len(items) < defaults.SEARCH_TOP_INTENTS:
                m = self._intent_meta(it)
                m["score"] = round(s, 4)
                items.append(m)
        top_p = sorted(protos.values(),
                       key=lambda p: (-p["score"], p["name"])
                       )[:defaults.SEARCH_TOP_PROTOS]
        for p in top_p:
            p["score"] = round(p["score"], 4)
        return {"ok": True, "items": items, "protocols": top_p,
                "total_matched": total, "mode": "vector"}

    def _intent_detail(self, f: dict, caller: str) -> dict:
        """Removed (user ruling 2026-08-14: once get has tiers,
        detail is redundant) -- "read = use" merged into one ledger:
        reading the full file is also usage; the zero-score
        read-only view retires."""
        return {"error": "intent_detail is removed — use intent_get: "
                         "full file by default, part=chain|steps|"
                         "acceptance fetches one layer; the names "
                         "array batches. Reading is using, scored as "
                         "usual."}

    def _intent_get(self, f: dict, caller: str) -> dict:
        """Get by name, **tiered and batchable** (user correction +
        follow-up ruling 2026-08-14: a mixed body isn't split into
        two -- one intent stays one layer internally, material is
        picked by tier: copying mechanically gets chain, executing
        gets steps, constraints get instructions, default is the
        full file; names array batches, part applies to the whole
        batch). Reading by name = scored (get splits score, per
        name) + runtime promotion -- library-card model: naming it
        to borrow is what stamps it; detail is removed, read=use
        merged into one ledger. Works across hot/cold alike."""
        part = str(f.get("part") or "").strip()
        names = f.get("names")
        if isinstance(names, list) and names:
            ns = [str(x).strip() for x in names if str(x).strip()]
            if len(ns) > defaults.INTENT_GET_MAX:
                return {"error": f"intent_get: at most "
                                 f"{defaults.INTENT_GET_MAX} per call "
                                 f"— narrow it down with "
                                 f"intent_search first"}
            return {"ok": True,
                    "items": [self._intent_get_one(n, caller, part)
                              for n in ns]}
        return self._intent_get_one(str(f.get("name") or "").strip(),
                                    caller, part)

    def _intent_get_one(self, name: str, caller: str, part: str) -> dict:
        it = self.store.intent(name)
        if it is None or it["owner"] != caller:
            return {"error": f"intent_get: no such intent '{name}'"}
        self._touch(name, defaults.SCORE_GET)
        self.journal.row("intent", "get", intent=name, caller=caller,
                        part=part or None)
        out = self._intent_meta(it)
        out.update({"ok": True, "status": it["status"]})
        layers = {"steps": it.get("steps") or "",
                  # I-E-R (2026-08-16): the declaration-face key is
                  # acceptance, the value still lives in the fossil
                  # instructions column
                  "acceptance": it.get("instructions") or ""}
        if part in layers:
            out[part] = layers[part]
            out["note"] = ("layered fetch — other layers: "
                           + ", ".join(k for k in layers if k != part)
                           + " (part parameter); full file by default")
        else:
            out.update(layers)
            # v18 (R3): the preamble declaration surfaces with the
            # full file -- no need to flip through intent.json
            # before revising procedures
            prcs = self._proc_names(it)
            if prcs:
                out["procedures"] = prcs
        return out

    @staticmethod
    def _over_limit(fields: dict) -> str | None:
        """Character-flood gate (blank-slate self-check follow-up
        ruling 2026-08-12): steps has a character cap; scenario is
        a **word gate** (a one-word situational tag -- a description
        hurts retrieval; the vector layer aggregates by scenario
        word, same word clustering = a graduation signal).
        Over the limit rejects the whole submission, the rejection
        reason carries the count and where to put the overflow --
        push it back into memory."""
        v = fields.get("scenario")
        if v and not re.fullmatch(r"\w{1,%d}" % defaults.INTENT_SCENARIO_MAX,
                                  v):
            return (f"scenario must be a **single-word** situational "
                    f"tag (≤{defaults.INTENT_SCENARIO_MAX} chars, no "
                    f"spaces or punctuation), not a description — the "
                    f"intent body lives in steps, details in memory")
        v = fields.get("steps")
        if v and len(v) > defaults.INTENT_STEPS_MAX:
            return (f"steps over the cap ({len(v)}/"
                    f"{defaults.INTENT_STEPS_MAX} chars) — steps hold "
                    f"only the command sequence (the E section); "
                    f"acceptance criteria go into acceptance "
                    f"(≤{defaults.INTENT_INSTR_MAX} chars), "
                    f"machine-local details into your memory")
        v = fields.get("instructions")
        if v and len(v) > defaults.INTENT_INSTR_MAX:
            return (f"acceptance over the cap ({len(v)}/"
                    f"{defaults.INTENT_INSTR_MAX} chars) — only the "
                    f"notes and preference constraints the executor "
                    f"must honor; compress — not fitting is the "
                    f"signal this belongs in a protocol booklet")
        return None

    def _seed_proto_spec(self, name: str) -> None:
        """The chain type for the interactive bracket (one hop
        delivers, a human closes the bracket to settle). M26 §3:
        assignee = that protocol's own instance seat (x·<name>) --
        the bracket owns its seat, no longer crowds sidecar; cross-
        protocol parallelism comes for free."""
        self.store.spec_put(
            f"protocol:{name}", head=self.module,
            priority=0,
            consequence=f"protocol '{name}''s multi-round bracket "
                        f"(start opens the ring, a human closes and "
                        f"settles; member keys inside only deliver, "
                        f"never open rings; the seat is the household "
                        f"instance)",
            steps=[{"assignee": defaults.XPROTO_PREFIX + name,
                    "kind": "deliver",
                    "ref": name, "template": "protocol",
                    "accounting": "real",
                    "on_ok": "end", "on_fail": "end"}])

    def _compile_proto_keyset(self, name: str) -> Path | None:
        """M26 §1: register-is-compile's deck half -- a protocol's
        .streamDeckProfile lands with the other rendered artifacts
        in the **catalog directory** (utility/protocols/<name>/,
        not in the toolkit layer). Fixed four keys Start/Approve/
        Interrupt/Shutdown + member slots; a key = a background GET
        to /trigger. Failure goes into the journal, never re-raised
        (the deck face never carries weight)."""
        p = self.store.proto_get(name)
        if p is None:
            return None
        try:
            path = deckgen.protocol_keyset(
                self.utility / "protocols" / name, name,
                [str(m) for m in (p.get("members") or [])],
                self.http_port)
            if self.journal is not None:    # power-on recompile precedes journal's own birth
                self.journal.row("deck", "keyset", intent=name,
                                path=str(path))
            self._compile_deck_plugin()     # M26b: roster changed, resync sidebar
            return path
        except Exception as e:
            if self.journal is not None:
                self.journal.row("deck", "keyset-error", intent=name,
                                err=repr(e)[:200])
            return None

    def _compile_intents_keyset(self) -> Path | None:
        """M26 §2: the system's built-in intents keyset -- every
        standalone intent gets one one-way trigger key (approve
        never goes on a keyboard, it goes through the card flow).
        Ranked by usage top-N; overflow past 12 slots is journaled
        (not silently truncated)."""
        try:
            rows, _ = self.store.intent_catalog(
                self.module, top=defaults.CATALOG_TOP)
            names = [r["name"] for r in rows]
            for it in self.store.intents(owner=self.module,
                                         status="provisioned"):
                if it["name"] not in names:
                    names.append(it["name"])
            names = [n for n in names
                     if not (self.store.intent(n) or {}).get("proto")
                     and (self.store.intent(n) or {}).get("status")
                     == "provisioned"]
            path, dropped = deckgen.intents_keyset(
                self.utility, names, self.http_port)
            if self.journal is not None:
                self.journal.row("deck", "intents-keyset", n=len(names),
                                path=str(path),
                                dropped=("、".join(dropped) or None))
            self._compile_deck_plugin()     # M26b: roster changed, resync sidebar
            return path
        except Exception as e:
            if self.journal is not None:
                self.journal.row("deck", "keyset-error",
                                intent="(intents)", err=repr(e)[:200])
            return None

    def _sd_plugins_root(self) -> Path | None:
        a = os.environ.get("APPDATA")
        if not a:
            return None
        d = Path(a) / "Elgato" / "StreamDeck" / "Plugins"
        return d if d.is_dir() else None

    def _compile_deck_plugin(self) -> list[Path] | None:
        """M26b (user correction #1, night of 2026-08-22): the
        correct form for a custom keyset is a **sidebar plugin
        action** -- the user drags keys from the sidebar into their
        own profile, no need to import a whole page. Correction #2
        (third revision of #1, same night): one catalog = one
        independent plugin (Category = catalog name) + the system
        intents plugin; the merged version retires, and compilation
        sweeps orphaned directories under our own prefix. The engine
        writes straight into the SD Plugins directory (register-is-
        compile); when the action roster changes, restart the
        Stream Deck app once; when the URL/port changes, only
        routes.json needs rewriting (keyDown reads it live, no
        restart needed). Failure goes into the journal, never
        re-raised; with no host engine (tests/embedding), it never
        touches the real machine install."""
        if not self.spawn_host:
            return None
        root = self._sd_plugins_root()
        if root is None:
            if self.journal is not None:
                self.journal.row("deck", "plugin-skip",
                                why="Stream Deck app not installed")
            return None
        try:
            protos = []
            for p in self.store.protos(status="provisioned",
                                       subtype="interactive"):
                row = self.store.proto_get(p["name"]) or {}
                protos.append((p["name"],
                               [str(m) for m in (row.get("members")
                                                 or [])]))
            intents = [it["name"] for it in
                       self.store.intents(owner=self.module,
                                          status="provisioned")
                       if not it.get("proto")]
            # Engine Start key's revival mandate (user ruling
            # 2026-08-23): the engine bakes its own respawn command
            # into routes.json -- if the key is pressed while the
            # engine is down, the plugin uses this argv to bring the
            # engine back up (the Stream Deck app itself is the only
            # resident that survives a cold start).
            launch = {"argv": [sys.executable, "-m", "commander",
                               "run", "--workspace",
                               str(self.workspace),
                               "--http", str(self.http_port),
                               "--ws", str(self.ws_port)],
                      "cwd": str(self.workspace),
                      "env": {"PYTHONPATH":
                              str(Path(__file__).resolve().parents[1]),
                              "PYTHONIOENCODING": "utf-8"}}
            paths, swept = deckgen.compile_plugins(
                root, protos, intents, self.http_port,
                Path(__file__).parent / "deckplugin" / "plugin.js",
                launch=launch, ws_port=self.ws_port,
                # workspace-scoped plugin namespace (audit
                # 2026-08-25): plugin UUIDs used to be md5(book name)
                # alone, so two workspaces on one machine compiled
                # into the same directories and each engine's sweep
                # deleted the other's books
                tag=deckgen.ws_tag(self.workspace))
            if self.journal is not None:
                self.journal.row(
                    "deck", "plugins", n=len(paths),
                    path="、".join(p.name for p in paths),
                    swept=("、".join(swept) or None))
            return paths
        except Exception as e:
            if self.journal is not None:
                self.journal.row("deck", "plugin-error",
                                err=repr(e)[:200])
            return None

    def _render_proto_skill_home(self, name: str) -> None:
        """Renders the protocol skill into the sidecar home --
        interactive is the execution face (the bracket package also
        carries a full-text copy), executor is a **readable copy
        for the maintenance seat** (user ruling 2026-08-13: sidecar
        keeps the skill in his local, edit it there and resubmit
        when analyzing a retry)."""
        p = self.store.proto_get(name) or {}
        role = ("interactive bracket; opened by '" + name + "·启', "
                "read while hosting in protocol state"
                if p.get("subtype") == "interactive" else
                "executor aggregation; execution lives on the x· "
                "seat, you are the maintenance seat — when analyzing "
                "a retry, revise this script and resubmit")
        home = instance_home(self.workspace, self.module)
        d = home / ".claude" / "skills" / f"protocol-{name}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(
            f"---\nname: protocol-{name}\ndescription: "
            f"the script of protocol '{name}' ({role}).\n---\n\n"
            + self._proto_skill(name), encoding="utf-8")

    def _intent_retire(self, f: dict, caller: str) -> dict:
        """Retirement proposal (live-fire precedent 2026-08-23: there
        was no verb for this before, retirement required stopping
        the engine for manual surgery). Agent proposes -> qual·退役
        (retire) human gate -> effect retire_intent, a soft
        retirement. Termination is a ruling, not a record -- the
        person who approves is always the user; the full history
        ledger is always kept, resubmitting via workspace_submit
        revives it."""
        name = str(f.get("name") or "").strip()
        why = str(f.get("why") or "").strip()
        if not name:
            return {"error": "intent_retire: name is empty"}
        it = self.store.intent(name)
        if it is None:
            p = self.store.proto_get(name)
            if p is None:
                return {"error": f"intent_retire: '{name}' does not "
                                 f"exist"}
            # Booklet retirement (2026-08-26, closes the gap the
            # rename live-fire exposed): same proposal law — agent
            # proposes, the human gate decides; approval retires the
            # whole compile unit (booklet + declared members).
            if p["status"] != "provisioned":
                return {"error": f"intent_retire: booklet '{name}' is "
                                 f"not on the shelf "
                                 f"(status={p['status']}) — nothing "
                                 f"to retire"}
            if self._bracket_of(name) is not None:
                return {"error": f"intent_retire: booklet '{name}' has "
                                 f"an open bracket — Shutdown it "
                                 f"first, then retire"}
            prev = self.store.latest_for(name, FLOW_RETIRE)
            if prev is not None and prev["status"] == "gated":
                return {"ok": True, "task": prev["id"],
                        "note": "the retirement gate is already up "
                                "waiting for the user — no duplicate "
                                "card"}
            t = self.store.chain_start(FLOW_RETIRE, issuer=caller,
                                       intent=name, payload=why or None)
            roster = "、".join(p.get("members") or []) or "(none)"
            tpl = self._task_dir(t["id"]) / "template.md"
            tpl.write_text(
                f"# Retirement: booklet {name}\n\n"
                f"- scenario: {p.get('scenario') or '(none)'}\n"
                f"- members (retire together): {roster}\n"
                f"- proposed reason: {why or '(unset)'}\n\n"
                f"Approval = the booklet and its member keys leave "
                f"the IME and the deck; the full history ledger is "
                f"kept, resubmitting via workspace_submit revives "
                f"it.\n", encoding="utf-8")
            self.journal.row("protocol", "retire-proposed",
                            intent=name, task=t["id"], caller=caller,
                            why=why or None)
            self._say_engine(f"Booklet '{name}' pending retirement "
                             f"(task {t['id']}): approval takes it and "
                             f"its member keys out of the IME and the "
                             f"deck — history stays, resubmitting the "
                             f"workspace revives it."
                             + (f" Reason: {why}" if why else ""))
            self._task_bcast()
            return {"ok": True, "task": t["id"], "status": t["status"],
                    "note": "retirement gate is open — waiting for "
                            "the user; approval retires the booklet "
                            "with its members and recompiles the "
                            "keysets"}
        if it.get("proto"):
            return {"error": f"intent_retire: '{name}' is a member of "
                             f"booklet '{it['proto']}' — members are "
                             f"declared with the booklet: delete "
                             f"{it['proto']}/members/{name}/ then "
                             f"resubmit the whole booklet with "
                             f"workspace_submit(name={it['proto']})"}
        if it["status"] != "provisioned":
            return {"error": f"intent_retire: '{name}' is not on the "
                             f"shelf (status={it['status']}) — nothing "
                             f"to retire"}
        prev = self.store.latest_for(name, FLOW_RETIRE)
        if prev is not None and prev["status"] == "gated":
            return {"ok": True, "task": prev["id"],
                    "note": "the retirement gate is already up "
                            "waiting for the user — no duplicate card"}
        busy = [b for b in self.store.inflight(name)
                if b.get("spec") != FLOW_RETIRE]   # own gate doesn't count as in-flight
        if busy:
            return {"error": f"intent_retire: '{name}' has a ring in "
                             f"flight (task {busy[0]['id']}) — retire "
                             f"after it lands"}
        t = self.store.chain_start(FLOW_RETIRE, issuer=caller,
                                   intent=name, payload=why or None)
        tpl = self._task_dir(t["id"]) / "template.md"
        tpl.write_text(
            f"# Retirement: {name}\n\n"
            f"- title: {it.get('title') or '(none)'}\n"
            f"- scenario: {it.get('scenario') or '(none)'}\n"
            f"- proposed reason: {why or '(unset)'}\n\n"
            f"Approval = removed from the IME and the deck roster; "
            f"the full history ledger is kept, resubmitting via "
            f"workspace_submit revives it.\n", encoding="utf-8")
        self.journal.row("intent", "retire-proposed", intent=name,
                        task=t["id"], caller=caller, why=why or None)
        self._say_engine(f"'{name}' pending retirement (task {t['id']}): "
                         f"approval takes it OUT of the IME and the "
                         f"deck keyset — history stays, resubmitting "
                         f"the workspace revives it."
                         + (f" Reason: {why}" if why else ""))
        self._task_bcast()
        return {"ok": True, "task": t["id"], "status": t["status"],
                "note": "retirement gate is open — waiting for the "
                        "user; approval removes it from the roster "
                        "and recompiles the keyset"}

    def _intent_submit(self, f: dict, caller: str) -> dict:
        """§2u two-stage protocol, first stage (user ruling
        2026-08-15): **opening the ticket provisions the workspace,
        no human gate at this point** -- nothing can run yet, there
        is nothing to approve yet. The engine builds a workspace
        under the intent's name (schema skeleton intent.json +
        CLAUDE.md guidance + empty tools/inputs/records), the agent
        goes back to fill in the local files, then calls
        workspace_submit to register. **Register = compile**: the
        directory is source, the library is the executable form;
        the human gate is in the second stage (that's when it
        actually "goes live"). The right to initiate still belongs
        to the human (§3); a draft is not shown and cannot be bound;
        resubmitting under the same name = revise. Any class field
        passed in is ignored (the axis retired 2026-08-25)."""
        name = str(f.get("name") or "").strip()
        steps_text = str(f.get("steps") or "").strip()
        if not name:
            return {"error": "intent_submit: name is empty"}
        if str(f.get("kind") or "").strip() == "protocol":
            return self._protocol_open(f, caller, name)
        # Name gate (audit 2026-08-25): the name doubles as the
        # workspace **directory** name — it reaches wspace.provision
        # as a path component, and the engine writes it as itself, so
        # neither the CLI permission system nor the deny floor is in
        # that path. Ungated, a '..' segment or a drive letter walks
        # the write out of the workspace (pathlib drops the base
        # entirely when the joined segment carries a drive). Same
        # rule the protocol branch above already applies; it sits
        # here, after the dispatch, so protocols keep their own
        # message, and above every store write so a refusal leaves
        # no orphan row. \w is Unicode-aware — CJK names still pass.
        if not re.fullmatch(r"\w{1,%d}" % defaults.INTENT_SCENARIO_MAX,
                            name):
            return {"error": f"intent_submit: name must be one word "
                             f"(≤{defaults.INTENT_SCENARIO_MAX} chars, "
                             f"no spaces, punctuation or path "
                             f"separators) — it doubles as the "
                             f"workspace directory name"}
        title = str(f.get("title") or "").strip()
        scenario = str(f.get("scenario") or "").strip()
        # I-E-R (2026-08-16): the declaration-face key is
        # acceptance; internally and in the DB it still uses
        # instructions (fossil column)
        instr = str(f.get("acceptance") or "").strip()
        over = self._over_limit({"scenario": scenario,
                                 "steps": steps_text,
                                 "instructions": instr})
        if over:
            return {"error": f"intent_submit: {over}"}
        # The dual-form parity rule is repealed (user ruling
        # 2026-08-16 night): **single form** -- one intent is one
        # segment of E, always cast into a deliver hop and posted
        # to x·solo.
        if f.get("chain"):
            return {"error": "intent_submit: chain is retired — "
                             "upfront context collection goes through "
                             "the optional procedures field (the "
                             "engine's built-in library, referenced "
                             "by name; the engine runs the prelude "
                             "before delivering); any other prelude "
                             "goes into E's first hop."}
        # v18 preamble declaration (user ruling 2026-08-23): matched
        # by name against the engine's word list; anything outside
        # the list rejects the whole submission, the rejection
        # reason carries the available word list (the library
        # belongs to the engine, agents have no submission channel)
        raw_prcs = f.get("procedures")
        prcs = ([str(x).strip() for x in raw_prcs if str(x).strip()]
                if isinstance(raw_prcs, list) else [])
        bad = [p for p in prcs if p not in defaults.PHYS_PROCEDURES]
        if bad:
            return {"error": f"intent_submit: procedures outside the "
                             f"word list: {', '.join(bad)} — the "
                             f"library belongs to the engine; "
                             f"available list: "
                             f"{', '.join(defaults.PHYS_PROCEDURES) or '(empty)'}"
                             f"; extensions go through the user"}
        it = self.store.intent(name)
        if it is not None and (it.get("proto") or ""):
            # v17 compilation unit: a member has no independent seat
            # -- the name is held by the catalog, changing it goes
            # through the catalog (resubmit the whole catalog);
            # start a new standalone file under a different name
            return {"error": f"intent_submit: '{name}' is a member of "
                             f"booklet '{it['proto']}' — members are "
                             f"declared with the booklet: edit "
                             f"{it['proto']}/members/{name}/ then "
                             f"resubmit the whole booklet with "
                             f"workspace_submit(name={it['proto']}); "
                             f"for a stateless single-shot one, found "
                             f"it under a different name"}
        if it is not None and it["status"] != "draft":
            # the rejection reason IS the signpost (live-fire
            # precedent 2026-08-11: opus wanted to revise but was
            # pointed down a dead-end by "pick a different name",
            # costing an extra round trip)
            hint = (f"migrated: {it['migrated_to']} — triggering "
                    f"still works under the old name; "
                    if it.get("migrated_to") else "")
            return {"error": f"intent_submit: '{name}' is already on "
                             f"the shelf (status={it['status']}). "
                             f"{hint}To change it, edit the "
                             f"workspace's intent.json and "
                             f"workspace_submit to re-register "
                             f"(rev++); for a genuinely new intent, "
                             f"pick another name"}
        if (it is not None
                and self.store.spec(f"deliver:{name}") is not None):
            # a rework item doesn't go through the creation chain
            # (its gate already closed the books) -- the rejection
            # reason IS the signpost
            return {"error": f"intent_submit: '{name}' is a rework "
                             f"item (firing failed, suspended for "
                             f"repair) — edit intent.json and "
                             f"re-register (multiple rounds fine); "
                             f"when repaired, settle ok on the rework "
                             f"diagnosis task and the engine runs the "
                             f"sim check and re-shelves automatically"}
        if it is None and (self.store.count(caller)
                           >= defaults.MAX_HOME_INTENTS):
            return {"error": f"intent_submit: catalog is full "
                             f"({defaults.MAX_HOME_INTENTS}, a sanity "
                             f"cap) — merge what should merge, retire "
                             f"what should retire"}
        # class retired (user ruling 2026-08-25): no filing axis —
        # any passed-in class field is ignored, the DB column is a
        # fossil, the workspace layout is flat.
        if it is None:
            self.store.intent_create(name, title=title, scenario=scenario,
                                     steps=steps_text, instructions=instr,
                                     owner=caller, scope=caller,
                                     born=(self.journal.session
                                           if self.journal else None))
            if prcs:                         # v18 preamble declaration (already word-list checked)
                self.store.intent_revise(
                    name, procedures=json.dumps(prcs, ensure_ascii=False))
            self.journal.row("intent", "draft", intent=name,
                            caller=caller)
        else:
            # resubmitting the same-name draft = revising the draft
            # (the gate is still open, template re-renders)
            self.store.intent_revise(name, title=title, scenario=scenario,
                                     steps=steps_text, instructions=instr,
                                     **({"procedures": json.dumps(
                                         prcs, ensure_ascii=False)}
                                        if prcs else {}))
            self.journal.row("intent", "revised", intent=name,
                            caller=caller)
        # §2u provisioning: the declaration lands in intent.json
        # (the schema table is the single source of truth), the
        # directory idempotently fills in missing pieces, an
        # existing CLAUDE.md convention is never overwritten (those
        # are the user's own words)
        # Carry the declared tools forward (audit 2026-08-25):
        # provision() rewrites intent.json unconditionally and this,
        # its only production caller, hard-coded tools=[] — so
        # re-submitting a draft silently erased whatever tool names
        # the sidecar had written into the file. The tools field is
        # the folder's to own; intent_submit has no argument for it.
        _home = instance_home(self.workspace, self.module)
        _prev, _ = wspace.read_decl(wspace.wdir(_home, name))
        decl = {"name": name, "title": title, "scenario": scenario,
                "steps": steps_text, "acceptance": instr,
                "procedures": prcs,
                "tools": list((_prev or {}).get("tools") or [])}
        d = wspace.provision(_home, name, decl)
        self.journal.row("intent", "workspace", intent=name,
                        caller=caller, dir=str(d))
        self.channel.broadcast(self._intents_frame())
        return {
            "ok": True, "status": "draft", "workspace": str(d),
            "next": (f"directory created (not live yet — a draft "
                     f"can't be triggered). **The field textbook is "
                     f"the workspace's schema.md** (incl. the E "
                     f"grammar and the optional procedures prelude "
                     f"field). Now write the pieces in locally: "
                     f"tools/<name>.*, materials into inputs/, "
                     f"conventions into CLAUDE.md; editing "
                     f"intent.json is editing this intent. When done "
                     f"call workspace_submit(name={name}) to register "
                     f"— registration = compilation, and that is the "
                     f"step with the human gate.")}

    def _protocol_open(self, f: dict, caller: str, name: str) -> dict:
        """§2u: a protocol goes through the same two-stage protocol
        -- open the ticket, provision the workspace (protocol.json +
        skill.md skeleton), fill it in, then workspace_submit to
        register. The old protocol_submit / protocol_register are
        both folded in: the member roster goes into the members
        field, registering stamps the pointer."""
        if not re.fullmatch(r"\w{1,%d}" % defaults.INTENT_SCENARIO_MAX,
                            name or " "):
            return {"error": f"intent_submit(protocol): name must be "
                             f"one word "
                             f"(≤{defaults.INTENT_SCENARIO_MAX} chars)"}
        if name == defaults.XSOLO_NAME:
            return {"error": f"'{defaults.XSOLO_NAME}' is the "
                             f"executor seat's reserved name"}
        subtype = str(f.get("subtype") or "interactive").strip()
        if subtype not in defaults.PROTO_SUBTYPES:
            return {"error": "protocols come in exactly one "
                             "multi-round bracket type (subtype="
                             "interactive, omissible) — straight-line "
                             "execution is intent business, run on "
                             "x·solo"}
        scenario = str(f.get("scenario") or "").strip()
        if (self.store.proto_get(name) is None
                and len(self.store.protos())
                >= defaults.PROTO_TOTAL_MAX):
            return {"error": f"protocol total count is full "
                             f"({defaults.PROTO_TOTAL_MAX})"}
        decl = {"name": name, "scenario": scenario,
                "subtype": subtype, "members": []}
        home = instance_home(self.workspace, self.module)
        d = wspace.wdir(home, name)
        d.mkdir(parents=True, exist_ok=True)
        for sub in wspace.SUBDIRS:
            (d / sub).mkdir(exist_ok=True)
        (d / wspace.MEMBERS_DIR).mkdir(exist_ok=True)
        wspace.write_schema_md(d, "protocol")   # N1: both tables land with the catalog
        # Never clobber an existing booklet (audit 2026-08-25): this
        # skeleton carries members=[] and no prep/wrapup, and
        # re-opening a live booklet under the same name used to
        # overwrite the file with it — silently destroying the whole
        # roster on disk. Same guard skill.md has had all along, one
        # branch below. A re-open still refreshes the two declared
        # header fields, so "re-submit to revise scenario/subtype"
        # keeps working; the roster is the sidecar's to edit.
        pj = d / "protocol.json"
        if pj.is_file():
            try:
                prev = json.loads(pj.read_text(encoding="utf-8"))
                if isinstance(prev, dict):
                    prev.update({"name": name, "scenario": scenario,
                                 "subtype": subtype})
                    decl = prev
            except (OSError, ValueError):
                pass                    # unreadable: fall back to the skeleton
        pj.write_text(
            json.dumps(decl, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        sk = d / wspace.SKILL_NAME
        if not sk.is_file():
            sk.write_text(f"# {name}\n\n(skill-book body — scenario "
                          f"aggregation, interaction rules, division "
                          f"of labor inside the bracket)\n",
                          encoding="utf-8")
        self.store.proto_stage(name, subtype=subtype,
                               scenario=scenario, staged_hash="",
                               born=(self.journal.session
                                     if self.journal else None))
        self.journal.row("protocol", "draft", intent=name, caller=caller)
        return {"ok": True, "status": "draft", "workspace": str(d),
                "next": (f"directory created; **both field sheets are "
                         f"in the workspace's schema.md** (booklet "
                         f"declaration + member table). **The booklet "
                         f"is the compile unit (v17)**: member "
                         f"declarations travel with it, never through "
                         f"intent_submit — each member puts one "
                         f"intent.json (steps required) + tools/ "
                         f"under {wspace.MEMBERS_DIR}/<member>/; "
                         f"protocol.json's members holds the roster "
                         f"(3–10 counting the two system slots ·启/"
                         f"·收 — **opening/closing are not members**: "
                         f"they are system steps, opening setup goes "
                         f"in the prep field, the closing step in "
                         f"wrapup, auto-delivered at bracket open/"
                         f"close); skill.md holds only the "
                         f"aggregation. When done, "
                         f"workspace_submit(name={name}) submits the "
                         f"whole booklet at once — one gate, atomic "
                         f"compile, one bad member refuses the whole "
                         f"booklet, all or nothing.")}

    def _workspace_submit(self, f: dict, caller: str) -> dict:
        """§2u two-stage protocol, second stage: **submit by folder,
        register-is-compile**.

        The engine only reads the schema table (structure, not
        content) -> checks whether the files exist at the paths the
        declaration names -> records the hash (proof of going live)
        -> one registration card waits for human approval.
        **The card doesn't dump the full text**: the harness already
        approved once at write time, what's being approved here is
        "going live" -- to inspect the content, open the directory
        yourself. But this card cannot be skipped -- under auto mode
        the write-to-disk step may auto-allow, so this is the only
        human eye across the whole pipeline.
        Changing anything on disk afterward = resubmit required; the
        ledger keeps serving the approved version until then. (The
        stamped hash is the audit anchor for that comparison, not a
        runtime gate — nothing re-checks it at trigger time.)"""
        name = str(f.get("name") or "").strip()
        if not name:
            return {"error": "workspace_submit: name is empty"}
        it = self.store.intent(name)
        proto = self.store.proto_get(name) if hasattr(
            self.store, "proto_get") else None
        kind = "protocol" if (it is None and proto is not None) \
            else "intent"
        if it is None and proto is None:
            return {"error": f"workspace_submit: no '{name}' — open "
                             f"the ticket with intent_submit first, "
                             f"then write the pieces into the "
                             f"directory"}
        if it is not None and (it.get("proto") or "") and proto is None:
            # v17 compilation unit: a member never resubmits alone
            # -- a lone submission breaks atomicity
            return {"error": f"workspace_submit: '{name}' is a member "
                             f"of booklet '{it['proto']}' — edit "
                             f"{it['proto']}/members/{name}/ then "
                             f"resubmit the whole booklet with "
                             f"workspace_submit(name={it['proto']})"}
        if it is not None and it["owner"] != caller:
            return {"error": f"workspace_submit: '{name}' is not "
                             f"yours to manage"}
        home = instance_home(self.workspace, self.module)
        d = wspace.wdir(home, name)
        if not (d / wspace.DECL_NAME).is_file() \
                and not (d / wspace.PROTO_DECL_NAME).is_file():
            return {"error": f"workspace_submit: cannot find "
                             f"'{name}''s workspace (expected at "
                             f"{d}) — the directory was moved or "
                             f"{wspace.DECL_NAME} is missing"}
        decl, rerr = wspace.read_decl(d)
        if decl is None:
            return {"error": f"workspace_submit: {rerr}"}
        probs = wspace.validate(decl, kind)
        found, fprobs = wspace.resolve(d, decl, kind)
        probs += fprobs
        if kind == "intent" and not str(decl.get("steps") or "").strip():
            # steps is mandatory (user ruling 2026-08-16 night,
            # single-form closes the loop)
            probs.append("steps is required — the intent IS this "
                         "segment of E (a pseudo-code function body); "
                         "without it the intent does not exist")
        wprcs = [str(x).strip() for x in (decl.get("procedures") or [])
                 if str(x).strip()] if kind == "intent" else []
        wbad = [p for p in wprcs if p not in defaults.PHYS_PROCEDURES]
        if wbad:
            # v18 registration-time match (user ruling 2026-08-23):
            # matched by name against the engine's word list,
            # anything outside it rejects the whole submission, the
            # rejection reason carries the available word list
            probs.append(f"procedures outside the word list: "
                         f"{', '.join(wbad)} — the library belongs to "
                         f"the engine; available list: "
                         f"{', '.join(defaults.PHYS_PROCEDURES) or '(empty)'}"
                         f"; extensions go through the user")
        if probs:
            # the rejection reason IS the lesson (call each one out,
            # never a blanket silent rejection -- the check_rules
            # precedent)
            return {"error": "workspace_submit: registration "
                             "validation failed —\n"
                             + "\n".join("· " + p for p in probs)}
        # snapshot into staging + stamp the hash: what the human
        # gate freezes is this copy, changes on disk afterward are
        # void (to change it = resubmit and re-approve)
        staged = []
        for nm, (p, h) in found["tools"].items():
            staged.append(f"tools/{p.name}  ({h[:12]})")
        if wprcs:            # v18: the approval card shows which preambles are attached
            staged.append("procedures (engine built-in preludes): "
                          + ", ".join(wprcs))
        if kind == "protocol":
            # v17 compilation unit (user ruling 2026-08-16 late
            # night): member declarations live under the catalog
            # (members/<name>/), the whole catalog is validated and
            # gated in one pass -- one bad member rejects the whole
            # catalog, take it all or nothing, no singles.
            mem = [str(x).strip() for x in (decl.get("members") or [])
                   if str(x).strip()]
            mdecls, mstaged, mprobs = wspace.resolve_members(d, decl)
            for m in mem:
                if m in defaults.PROTO_RESERVED_MEMBERS:
                    # ·启/·收 made concrete (user ruling 2026-08-24):
                    # open/close are two built-in system steps, a
                    # user member can't occupy that slot -- content
                    # goes through the declaration fields instead
                    mprobs.append(f"member '{m}' is a reserved system "
                                  f"slot — opening/closing are not "
                                  f"members: opening setup goes in "
                                  f"protocol.json's prep field, the "
                                  f"closing step in wrapup; the "
                                  f"engine delivers them at bracket "
                                  f"open/close")
                    continue
                mit = self.store.intent(m)
                if mit is not None and (mit.get("proto") or "") != name:
                    mprobs.append(f"member '{m}' name collision: "
                                  f"already "
                                  + (f"a member of booklet "
                                     f"'{mit['proto']}'"
                                     if mit.get("proto")
                                     else "a standalone intent")
                                  + " — names are globally unique; "
                                    "rename or retire the old one "
                                    "first")
            if mprobs:
                return {"error": "workspace_submit: whole booklet "
                                 "refused (member problems named one "
                                 "by one; one bad member refuses the "
                                 "booklet) —\n"
                                 + "\n".join("· " + p for p in mprobs)}
            seats = len(mem) + 2      # §2i seat-count rule: ·启/·收 the two placeholder words count too
            if not (defaults.PROTO_MIN_SEATS <= seats
                    <= defaults.PROTO_MAX_SEATS):
                return {"error": f"workspace_submit: seat count "
                                 f"{seats} out of bounds — the law is "
                                 f"{defaults.PROTO_MIN_SEATS}–"
                                 f"{defaults.PROTO_MAX_SEATS} "
                                 f"(counting the ·启/·收 system "
                                 f"slots); too few, don't aggregate; "
                                 f"too many, split into two"}
            sp, sh = found["skill"]
            skill = sp.read_text(encoding="utf-8")
            if len(skill) > defaults.PROTO_SKILL_MAX:
                return {"error": f"workspace_submit: skill.md over "
                                 f"the cap ({len(skill)}/"
                                 f"{defaults.PROTO_SKILL_MAX} chars)"}
            # ·启/·收 content (user ruling 2026-08-24): prep = the
            # opening housekeeping, wrapup = the closing step --
            # these are declaration fields, not members; over the
            # limit rejects the whole catalog
            prep = str(decl.get("prep") or "").strip()
            wrapup = str(decl.get("wrapup") or "").strip()
            for fld, val in (("prep", prep), ("wrapup", wrapup)):
                if len(val) > defaults.PROTO_HOOK_MAX:
                    return {"error": f"workspace_submit: {fld} over "
                                     f"the cap ({len(val)}/"
                                     f"{defaults.PROTO_HOOK_MAX} "
                                     f"chars)"}
            stg = (self.workspace / defaults.RUNTIME_DIRNAME / "staging"
                   / f"protocol-{name}")
            stg.mkdir(parents=True, exist_ok=True)
            (stg / "skill.md").write_text(skill, encoding="utf-8")
            # what the human gate freezes is this copy: member
            # declarations go into staging together with the skill
            (stg / "members.json").write_text(
                json.dumps(mdecls, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
            self.store.proto_stage(
                name, subtype=str(decl.get("subtype") or "interactive"),
                scenario=str(decl.get("scenario") or ""),
                staged_hash=procrun.text_hash(skill),
                prep=prep, wrapup=wrapup,
                born=(self.journal.session if self.journal else None))
            staged.append(f"skill.md  ({sh[:12]})")
            staged += mstaged
            staged.append("members: " + ", ".join(mem))
            if prep:
                staged.append("prep (·启 system-step content): "
                              "declared")
            if wrapup:
                staged.append("wrapup (·收 system-step content): "
                              "declared")
        # write the declared content back into the library (the
        # directory is source -- the copy on disk is authoritative)
        if it is not None and kind == "intent":
            self.store.intent_revise(
                name, title=str(decl.get("title") or ""),
                scenario=str(decl.get("scenario") or ""),
                steps=str(decl.get("steps") or ""),
                # I-E-R (2026-08-16): the declaration-face key is
                # acceptance (the acceptance criteria); the DB still
                # uses the fossil instructions column (column name
                # unchanged)
                instructions=str(decl.get("acceptance") or ""),
                procedures=json.dumps(wprcs, ensure_ascii=False))
        t = self.store.latest_for(name, FLOW_WS_QUAL)
        if t is None or t["status"] != "gated":
            t = self.store.chain_start(FLOW_WS_QUAL, issuer=caller,
                                       intent=name)
        extra = wspace.undeclared(d, decl)
        tpl = self._task_dir(t["id"]) / "template.md"
        tpl.write_text(defaults.WS_REGISTER_MD.format(
            name=name, kind=kind, wdir=str(d),
            scenario=decl.get("scenario") or "(unset)",
            form="agent execution (x·solo)",
            n=len(staged),
            files=("\n".join("- " + s for s in staged)
                   or "- (declaration only, no executable pieces)"),
            extra=("\n".join("- " + s for s in extra)
                   or "- (none)")), encoding="utf-8")
        self.journal.row("intent", "ws-submitted", intent=name,
                        task=t["id"], caller=caller, files=len(staged))
        self._say_engine(f"'{name}' pending registration (task "
                         f"{t['id']}): {len(staged)} pieces will go "
                         f"live. The approval is for GOING LIVE, not "
                         f"content — to inspect, open {d}. Approval "
                         f"compiles into the library.")
        self._task_bcast()
        return {"ok": True, "task": t["id"], "status": t["status"],
                "files": staged, "undeclared": extra,
                "note": "hash stamped as the audit anchor — the "
                        "library keeps serving this approved "
                        "version, so a later edit on disk does "
                        "nothing until you workspace_submit again "
                        "and re-approve"}

    def _on_validate(self, name: str) -> None:
        """The validate key (ruling 2026-08-11): sim is an optional
        action, human-triggered."""
        it = self.store.intent(name)
        if (it is None or it["owner"] != self.module
                or it["status"] != "provisioned"):
            self._say_engine(f"validate: '{name}' is not on the shelf "
                             f"— nothing to validate.")
            return
        if self.store.inflight(name):
            self._say_engine(f"'{name}' still has a ring in flight — "
                             f"validate after it lands.")
            return
        if not self._admit_spec("validate", f"validate: {name}"):  # §2h intake rule
            return
        t = self.store.chain_start("validate", issuer="user", intent=name)
        self._touch(name, defaults.SCORE_TRIGGER)
        self.journal.row("chain", "validate", task=t["id"], intent=name)
        self._task_bcast()

    # ---- HTTP (observe page + discovery) -------------------------------

    def _serve_http(self) -> None:
        engine = self

        # Base, not SimpleHTTP (audit 2026-08-25 §4-security): the
        # Simple base ships do_HEAD/do_GET that serve the process
        # CWD as a file tree, and inherited verbs never pass
        # _guarded — do_HEAD answered with real file metadata. This
        # handler hand-writes every response; the only verbs that
        # exist are the ones defined below, anything else gets the
        # base's 501.
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _guarded(self) -> bool:
                """The loopback guardrail (kernel/netguard): the
                gate on the browser face. Any cross-origin page
                request is rejected -- /api/mcp is a no-preflight
                CSRF write face, /api/hook can forge a permission-
                card message to trick a click; /trigger is a pure
                GET action face, an <img> simple request carries no
                Origin, so it relies on Sec-Fetch-Site as a third
                gate (only mounted on the action face; panel paths
                aren't gated -- clicking a localhost link from a
                cross-site page to open the panel is a legitimate
                route). Legitimate clients (the MCP bridge's urllib
                / hookfwd / deck plugin / guard) never carry these
                headers, so there's zero collateral damage."""
                origin = self.headers.get("Origin")
                host = self.headers.get("Host")
                sf = self.headers.get("Sec-Fetch-Site")
                action = (self.path.startswith("/trigger")
                          or self.path.startswith("/api/"))
                if (netguard.origin_ok(origin, engine.http_port)
                        and netguard.host_ok(host)
                        and (not action or netguard.sec_fetch_ok(sf))):
                    return True
                engine._blocked("http", {"origin": origin, "host": host,
                                         "sec_fetch": sf,
                                         "path": self.path})
                self.send_error(403, "forbidden origin")
                return False

            def do_GET(self):
                if not self._guarded():
                    return
                if self.path.startswith("/trigger"):
                    # M26 binding flow: a key = a background GET
                    # (Stream Deck Website action, openInBrowser=
                    # false). Always returns 200 JSON -- the deck
                    # face never carries weight, the rejection
                    # reason is written into the body and the
                    # journal.
                    qs = urllib.parse.parse_qs(
                        urllib.parse.urlparse(self.path).query)
                    try:
                        ans = engine._on_trigger(
                            {k: v[0] for k, v in qs.items()})
                    except Exception as e:
                        if engine.journal is not None:
                            engine.journal.row("deck", "trigger-error",
                                               err=repr(e)[:200])
                        ans = {"error": f"engine internal: {e!r}"}
                    body = json.dumps(ans, ensure_ascii=False).encode(
                        "utf-8", "replace")
                    self.send_response(200)
                    self.send_header("Content-Type",
                                     "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif (self.path in ("/", "/observe")
                        or self.path.startswith("/flow")
                        or self.path.startswith("/hub")):
                    # unified all-seats panel (user ruling 2026-08-23
                    # night: observe and flow merge into one --
                    # flow.html with no ?i = the engine face): /hub
                    # is the shell, every other route serves
                    # flow.html
                    page = ("hub.html"
                            if self.path.startswith("/hub")
                            else "flow.html")
                    body = (PANEL_DIR / page).read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type",
                                     "text/html; charset=utf-8")
                    # Framing gate (audit 2026-08-25): panel routes
                    # are deliberately exempt from the Sec-Fetch
                    # check (a cross-site link that opens the panel
                    # is a legitimate route) — but that same
                    # exemption let any visited page embed the
                    # **live** panel cross-origin and steal a click
                    # onto a human gate's Approve. 'self' keeps the
                    # hub's own same-origin frames working and kills
                    # every foreign embed; top-level navigation is
                    # not framing, so the cross-site link survives.
                    self.send_header("Content-Security-Policy",
                                     "frame-ancestors 'self'")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/api/discover":
                    # CASELAW 6: encode carries an errors policy
                    body = json.dumps({"ws": engine.ws_port},
                                      ensure_ascii=False).encode(
                                          "utf-8", "replace")
                    self.send_response(200)
                    self.send_header("Content-Type",
                                     "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path in ("/xterm.js", "/xterm.css"):
                    p = PANEL_DIR / self.path.lstrip("/")
                    body = p.read_bytes()
                    ctype = ("text/javascript" if p.suffix == ".js"
                             else "text/css")
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_error(404)

            def do_POST(self):
                if not self._guarded():
                    return
                if self.path == "/api/perm":
                    # M18 blocking arbitration: this request IS the
                    # hand waiting for the human -- ThreadingHTTPServer
                    # is one thread per request, hanging here is
                    # harmless
                    try:
                        n = int(self.headers.get("Content-Length") or 0)
                        obj = json.loads(self.rfile.read(n)
                                         .decode("utf-8", "replace"))
                    except (ValueError, TypeError):
                        obj = None
                    try:
                        ans = (engine._perm_ask(obj)
                               if isinstance(obj, dict)
                               else {"decision": "ask"})
                    except Exception:
                        ans = {"decision": "ask"}   # sick = defer, not fail-open
                    body = json.dumps(ans).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path == "/api/hook":
                    # the hook mailbox intake (M13): dispatch is the
                    # engine's job; **always 200** -- the mailbox is
                    # fire-and-forget, bad material is filed, never
                    # swallowed or blown up
                    try:
                        n = int(self.headers.get("Content-Length") or 0)
                        obj = json.loads(self.rfile.read(n)
                                         .decode("utf-8", "replace"))
                    except (ValueError, TypeError):
                        obj = None
                    try:
                        if isinstance(obj, dict):
                            engine._on_hook(obj)
                        elif engine.journal is not None:
                            engine.journal.row("hook", "bad-json")
                    except Exception as e:
                        if engine.journal is not None:
                            engine.journal.row("hook", "hook-error",
                                               err=repr(e)[:200])
                    body = b'{"ok": true}'
                    self.send_response(200)
                    self.send_header("Content-Type",
                                     "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path != "/api/mcp":
                    self.send_error(404)
                    return
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    obj = json.loads(self.rfile.read(n)
                                     .decode("utf-8", "replace"))
                except (ValueError, TypeError):
                    obj = None
                try:
                    # CASELAW 6/19: one verb's death must never kill
                    # the request thread -- a rejection is still an
                    # answer, even an internal blow-up must become a
                    # readable answer
                    ans = (engine._mcp_call(obj) if isinstance(obj, dict)
                           else {"error": "bad json"})
                except Exception as e:
                    if engine.journal is not None:
                        engine.journal.row("mcp", "verb-error",
                                           err=repr(e)[:200])
                    ans = {"error": f"engine internal: {e!r}"}
                body = json.dumps(ans, ensure_ascii=False).encode(
                    "utf-8", "replace")
                self.send_response(200)
                self.send_header("Content-Type",
                                 "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._httpd = http.server.ThreadingHTTPServer(
            ("127.0.0.1", self.http_port), Handler)
        threading.Thread(target=self._httpd.serve_forever,
                         daemon=True, name="http").start()

    # ---- lifecycle -------------------------------------------------------

    def run(self) -> int:
        # Double-start guard (audit 2026-08-25 §4-correctness): a
        # second `intentos run` on the same workspace used to march
        # straight into power-on — reseeding the store and rewriting
        # the live engine's seat homes under it — and only died
        # minutes of damage later, at the port bind. Probe the
        # port-truth file first: something answering on that port =
        # an engine is alive here, refuse before touching anything.
        try:
            info = json.loads(
                (self.workspace / defaults.RUNTIME_DIRNAME
                 / "engine.json").read_text(encoding="utf-8"))
            with socket.create_connection(
                    ("127.0.0.1", int(info["http"])), timeout=1.0):
                pass
        except (OSError, ValueError, KeyError, TypeError):
            pass                    # stale/absent file or dead port = clear to start
        else:
            print(f"[intentos] refusing to start: an engine already "
                  f"answers on http port {info['http']} for this "
                  f"workspace (pid {info.get('pid', '?')}). Stop it "
                  f"first (intentos stop --ws {info.get('ws', '?')}).")
            return 1
        # physical-layer word-list registration (v18, user ruling
        # 2026-08-23): procedure = an engine built-in item, upserted
        # at power-on, rows outside the table retire -- procedures
        # declared by an intent may only reference these names.
        # Word-list values take a new form {desc, entry}, the ledger
        # only stores desc (entry is the engine's own business).
        self.store.proc_seed({n: v["desc"]
                              for n, v in defaults.PHYS_PROCEDURES.items()})
        # OS-level flow (M12 edge table, docs/M12-FLOW.md is the
        # authoritative blueprint): everything the engine owns is
        # reseeded at power-on (no template, not editable). The
        # entry point has three separated powers: a chain never
        # opens a chain, a human opens a chain, an edge reroutes
        # internally.
        # qual·初生 (genesis): the submit entry point, a human
        # approval gate -- approval means it goes on the shelf
        # (effect).
        self.store.spec_put(
            FLOW_QUAL_NEW, head=self.module,
            priority=defaults.PRIORITY_SELF,
            consequence="create a new intent: human approves the "
                        "template (the final human gate) — approval "
                        "shelves it; sim goes through validate "
                        "(optional)",
            steps=[
                {"assignee": "user", "kind": "gate",
                 "gate": "approve template",
                 "template": "template", "accounting": "test",
                 "effect": "ok:provision",
                 "on_ok": "end", "on_fail": "end"},   # a gate has no fail path (cancel is a separate route)
            ])
        # qual·注册 (registration) (M20 §2u, user ruling 2026-08-15):
        # **the only human gate for a folder submission**. What's
        # being approved is "going live", not the content (the
        # harness already approved once at write time; under auto
        # mode that pass may auto-allow -- which is why this card
        # can't be skipped). effect compiles once: the intent goes
        # live + the delivery chain recompiles.
        self.store.spec_put(
            FLOW_WS_QUAL, head=self.module,
            priority=defaults.PRIORITY_SELF,
            consequence="register a workspace (the directory is "
                        "source, the library the executable form): "
                        "human approval takes it live — approval "
                        "compiles into the library; a later edit on "
                        "disk takes effect only after another "
                        "registration",
            steps=[
                {"assignee": "user", "kind": "gate",
                 "gate": "approve registration",
                 "template": "template", "accounting": "test",
                 "effect": "ok:provision_workspace",
                 "on_ok": "end", "on_fail": "end"},
            ])
        # qual·退役 (retire) (live-fire precedent 2026-08-23):
        # termination is a ruling, not a record -- the agent
        # proposes (intent_retire), the human approves for it to
        # take effect. effect is a soft retirement: leaves the
        # roster but not the history, resubmitting revives it.
        self.store.spec_put(
            FLOW_RETIRE, head=self.module,
            priority=defaults.PRIORITY_SELF,
            consequence="retire a standalone intent or a whole "
                        "booklet (with its member keys): human "
                        "approval takes effect — leaves the IME and "
                        "deck rosters; the full history ledger is "
                        "kept, resubmitting via workspace_submit "
                        "revives it",
            steps=[
                {"assignee": "user", "kind": "gate",
                 "gate": "approve retirement",
                 "template": "template", "accounting": "test",
                 "effect": "ok:retire_intent",
                 "on_ok": "end", "on_fail": "end"},
            ])
        # qual·protocol (M20 §2): a protocol's human approval gate
        # -- what's approved is the full skill text (words aren't
        # permissions, but approval binds the subtype's execution
        # semantics).
        self.store.spec_put(
            "qual·protocol", head=self.module,
            priority=defaults.PRIORITY_SELF,
            consequence="submit a protocol (skill+subtype): human "
                        "approves the full text — approval shelves "
                        "it; effective once register binds the "
                        "members",
            steps=[
                {"assignee": "user", "kind": "gate",
                 "gate": "approve protocol", "template": "template",
                 "accounting": "test",
                 "effect": "ok:provision_protocol",
                 "on_ok": "end", "on_fail": "end"},
            ])
        # qual·回炉 (rework): an edge-entry flow (the rework rule)
        # -- n0 diagnosis (repair = folder edits accumulated over
        # multiple turns + workspace_submit; intent_update is
        # retired, closing the books ok = submit the repair)
        # -> n1 sim quality check (passing the check effects a
        # reprovision; failing it loops back to n0 for another
        # pass, the repair<->check loop is capped by the hop
        # guardrail). The original sole entry edge (deliver:X
        # procedure node's on_fail) has already been cut by the
        # physical-layer ruling (2026-08-16 night) -- no path leads
        # to it for now, left in place as a ready-made object for
        # wiring up E-layer failures later; sim's n1->n0 loopback
        # still uses it.
        self.store.spec_put(
            FLOW_QUAL_REWORK, head="engine",
            priority=defaults.PRIORITY_ERROR,
            consequence="rework QA for a firing failure (edge entry, "
                        "chains never open chains): diagnosis → sim, "
                        "passing re-shelves automatically, failing "
                        "loops back for another repair; test "
                        "accounting; the re-run initiative goes back "
                        "to the human",
            steps=[
                {"assignee": self.module, "kind": "deliver",
                 "template": "debug", "accounting": "test",
                 "on_ok": "next", "on_fail": "end"},  # diagnosis failure = stop, hold at draft for manual work
                {"assignee": self.module, "kind": "deliver",
                 "template": "sim", "accounting": "test",
                 "effect": "ok:reprovision",
                 "on_ok": "end", "on_fail": f"{FLOW_QUAL_REWORK}:0"},
            ])
        # validate (ruling 2026-08-11): sim is an optional action,
        # human-triggered by pressing "validate" (issuer=user
        # bypasses the head check) -- a single node, no effect, no
        # rerouting; a rework item's sim already belongs to
        # qual·回炉.n1, no longer borrows this one.
        self.store.spec_put(
            "validate", head="engine", priority=defaults.PRIORITY_SELF,
            consequence="sim self-test (optional, human-triggered by "
                        "the validate key; record goes to the test "
                        "ledger)",
            steps=[{"assignee": self.module, "kind": "deliver",
                    "template": "sim", "accounting": "test",
                    "on_ok": "end", "on_fail": "end"}])
        # retry (ruling 2026-08-10): n0 sidecar mandatory validation
        # + steer (test accounting; if validation rules "shouldn't
        # retry" it stops there), n1 resubmits with the previous
        # context (real accounting). In v1 both nodes post to
        # sidecar; once the SDK execution layer ships, n1's assignee
        # switches to owner (per-intent compilation).
        self.store.spec_put(
            "retry", head="user", priority=defaults.PRIORITY_ERROR,
            consequence="retry order (reshaped 2026-08-25): sidecar "
                        "autopsies the previous run and redoes the "
                        "result directly (no executor); settlement "
                        "is real — the lesson rides the consolidate "
                        "offer that follows",
            steps=[
                {"assignee": self.module, "kind": "deliver",
                 "template": "retry-fulfill", "accounting": "real",
                 "on_ok": "end", "on_fail": "end"},
            ])
        # consolidate (user ruling 2026-08-25): approve on the offer
        # card suspends the asset and opens this order on the
        # sidecar; the registration gate is the revival edge
        self.store.spec_put(
            "consolidate", head="user",
            priority=defaults.PRIORITY_ERROR,
            consequence="consolidate order: the asset is suspended; "
                        "sidecar folds the lesson into the "
                        "declaration and re-registers — the "
                        "registration approval revives it",
            steps=[{"assignee": self.module, "kind": "deliver",
                    "template": "consolidate", "accounting": "real",
                    "on_ok": "end", "on_fail": "end"}])
        # surgery (§2g 2026-08-13): the executor's failure loop --
        # two entry points, each human-triggered once (retry with a
        # note / a failed proposal card's approve), filed under
        # error (§2h intake rule: with surgery in the queue, a new
        # exec submission is rejected outright). task_done is the
        # sole ignition signal (the old proto_park approval-parking
        # never went live and was deleted, audit 2026-08-25).
        self.store.spec_put(
            "手术", head="user", priority=defaults.PRIORITY_ERROR,
            consequence="the executor's failure loop (surgery): the "
                        "maintenance seat clears residue + repairs "
                        "the intent; settling auto-replays the "
                        "original order (one surgery one replay, a "
                        "second failure goes back to the human)",
            steps=[{"assignee": self.module, "kind": "deliver",
                    "template": "surgery", "accounting": "test",
                    "on_ok": "end", "on_fail": "end"}])
        # old-name sweep (M12 migration: debug/requalify's
        # standalone chain type folds into qual·回炉, intent-creation
        # is formally renamed qual·初生; v16: qual·procedure retires
        # along with the physical-layer ruling) -- only clears the
        # engine's own spec rows; historical journal/tasks stay
        # read-only and unmigrated, an in-flight old loop is
        # synthesized as a fallback by _node_of.
        # (prune: permission-face consolidation 2026-08-24 -- the
        # materialized chain type retires along with the pruner
        # seat)
        for legacy in ("intent-creation", "debug", "qual·procedure",
                       "prune"):
            self.store.spec_delete(legacy)
        # deliver spec refresh: the v5 library's edge columns are
        # all migration defaults, the compiled artifact should
        # reflect today's template -- recompile by intent row at
        # power-on (idempotent; the executor rerouting rule lives in
        # the store as a single source, recompiling self-heals it).
        for it in self.store.intents(status="provisioned"):
            if it.get("proto"):
                continue    # v17: a member lives under its catalog, a single post locks -- no delivery chain is cast
            self.store.compile_delivery(it["name"])
        # M20: the toolkit's neutral territory (a seat-shared
        # toolkit) + the interactive bracket chain type and the home
        # skill re-render (engine-owned artifacts, rewritten on
        # every cast)
        (self.workspace / "toolkit").mkdir(parents=True, exist_ok=True)
        for p in self.store.protos(status="provisioned",
                                   subtype="interactive"):
            self._seed_proto_spec(p["name"])
        # Cancel-deadlock sweep (live-fire 2026-08-25; merged and
        # widened by the audit the same day — the narrower of the two
        # loops that stood here was a strict subset of the other).
        # A ring left `running` across a restart can never settle by
        # itself, for either of two reasons: its chain was cancelled
        # under the old soft law, or it is a **conversational** ring
        # (retry / consolidate) and the seat that was holding the
        # conversation is gone — every seat's CLI is spawned fresh at
        # boot, so nobody can call task_done on the old session's
        # task id, and _reap_overdue exempts these two specs from the
        # timeout law on purpose. Left alone such a ring pins
        # seat_running(sidecar) and the error-tier queue ceiling
        # forever, which wedges the entire failure-recovery loop
        # (surgery, rework, further retries). A swept consolidate
        # hands its asset back exactly as cancel does.
        # The journal is built ~100 lines below (this sweep has to
        # run before store.reconcile), so the rows are emitted there
        # off this list — a `self.journal is not None` guard here
        # would simply never fire.
        self._boot_swept: list = []
        for r in self.store.tasks_recent(200):
            if r["status"] != "running":
                continue
            spec = str(r.get("spec") or "")
            handover = spec in ("retry", "consolidate")
            if not (handover
                    or self.store.chain_cancelled(r["chain_id"])):
                continue
            self.store.task_update(r["id"], status="cancelled")
            if spec == "consolidate":
                self._consolidate_unsuspend(str(r.get("intent") or ""))
            self._boot_swept.append(dict(r))
            print(f"[sweep] task {r['id']} ({spec}) finalized "
                  f"cancelled ("
                  + ("cannot survive an engine restart" if handover
                     else "its chain was already cancelled") + ")")
        # class retirement (2026-08-25): one-shot flatten of legacy
        # <class>/<name>/ workspaces to root/<name>/
        for mv in wspace.flatten_legacy(
                instance_home(self.workspace, self.module)):
            if self.journal is not None:
                self.journal.row("intent", "ws-flattened", note=mv)
            print(f"[migrate] workspace {mv}")
        # schema.md is engine-owned and must track the live schema
        # table — re-render every workspace's field textbook at boot
        # (live-fire 2026-08-25: textbooks rendered before the
        # caveats retirement still taught the retired field; a
        # schema change must not depend on "happened to
        # re-register")
        sc_home = instance_home(self.workspace, self.module)
        if sc_home.is_dir():
            for d in sorted(sc_home.iterdir()):
                try:
                    if (d / wspace.PROTO_DECL_NAME).is_file():
                        wspace.write_schema_md(d, "protocol")
                    elif (d / wspace.DECL_NAME).is_file():
                        wspace.write_schema_md(d)
                except OSError:
                    pass
        # power-on reconciliation (guardrail 2): sick intents get
        # called out loudly
        sick = self.store.reconcile(self.utility)
        # permission-face consolidation (user's final ruling
        # 2026-08-24, the completed form of §2t): the allow side is
        # entirely handed to harness auto mode (SEAT_PERMISSION_MODE
        # pre-written with the spawn flag) + the PERM_ALLOW ledger
        # (settles when the human clicks Always on a perm card,
        # persisted in config.json, user can hand-edit); the engine
        # only casts the deny floor and the pipe floor
        # (mcp__intentOS + toolkit reads). The pruner seat / evidence
        # loop / compiled union have all retired along with this
        # ruling.
        home = provision_home(self.workspace, token=self.token)
        for p in self.store.protos(status="provisioned"):
            # casting the home rmtree's the skills area -- the
            # protocol script re-renders with each cast (executor's
            # copy renders too: the maintenance seat's readable
            # copy). M26: the keyset recompiles in the same pass
            # (idempotent, only changes bytes when the port/members
            # change)
            self._render_proto_skill_home(p["name"])
            self._compile_proto_keyset(p["name"])
        # §2m v14 rename modes->modules: if the old directory exists
        # and the new name is absent, idempotently self-migrate
        # (os.rename does an atomic move, a human's edits are kept
        # intact along with the whole directory)
        mdir = self.workspace / defaults.MODULES_DIRNAME
        legacy = self.workspace / "modes"
        if legacy.is_dir() and not mdir.exists():
            legacy.rename(mdir)
        # M15 dual-write: journal rows sync into the events table
        # via a sink (the single writer is unchanged -- the same pen,
        # two sheets of paper). Conventional in-row fields get
        # promoted to columns: task->task_id, intent/issuer keep
        # their names; everything else goes into the fields JSON
        # as-is, queryable when needed.
        def _sink(rec: dict, session: str) -> None:
            extra = {k: v for k, v in rec.items()
                     if k not in ("t", "kind", "name", "task",
                                  "intent", "issuer")}
            self.store.event_put(
                rec["kind"], rec["name"], t=rec.get("t"),
                task_id=rec.get("task"), intent=rec.get("intent"),
                issuer=rec.get("issuer"), session=session,
                fields=extra or None)
        self.journal = Journal(
            self.workspace / defaults.RECORDS_DIRNAME, self.module,
            sink=_sink)
        self.journal.row("lifecycle", "start", module=self.module)
        # boot cancel-sweep receipts (audit 2026-08-25): the sweep
        # itself has to run before store.reconcile, i.e. before this
        # Journal exists — so its rows are written here instead of
        # being silently dropped by a guard that could never fire.
        for r in getattr(self, "_boot_swept", []):
            self.journal.row("chain", "force-cancel", task=r["id"],
                            intent=r.get("intent"),
                            spec=r.get("spec"), by="boot-sweep")
        self._boot_swept = []
        # M15 §4 power-on sampling (a detection half, no stripping):
        # the permissions block of settings.local.json goes into the
        # journal whole. The engine is the sole writer of that file
        # besides the human => the diff between two adjacent samples
        # = that session's permission growth in between. Missing
        # file / missing block is also recorded -- both sides of a
        # diff must be present, absence itself is evidence (§0).
        self._sample_local_perms(home)
        for p in sick:
            self.journal.row("reconcile", "sick", problem=p)
            print(f"[intentos] reconcile problem: {p}")
        # container changeover (the container rule): reorganize at
        # session start -- currently a session lives and dies with
        # the engine; once the host can restart independently this
        # hook follows the session's own lifecycle
        self._workset_reset()

        self._compile_intents_keyset()      # M26: the system intents keyset
        self._compile_deck_plugin()         # M26b: the sidebar custom keyset

        self.channel.on_chat = self._on_chat
        self.channel.on_cli_in = self._on_cli_in
        self.channel.on_cli_size = self._on_cli_size
        self.channel.on_stop = self._on_stop
        self.channel.on_approve = self._on_approve
        self.channel.on_cancel = self._on_cancel
        self.channel.on_retry = self._on_retry
        self.channel.on_validate = self._on_validate
        self.channel.on_intent = self._on_intent
        self.channel.on_card_answer = self._on_card_answer
        self.channel.on_blocked = self._blocked
        self.channel.intents_frame = self._intents_frame
        self.channel.flow_intents_frame = self._flow_intents_frame
        self.channel.chains_frame = self._chains_frame
        self.channel.cards_frame = self._cards_frame
        self.channel.surface = self._surface
        self.channel.replay = self._replay_for
        self.channel.start()
        self._serve_http()

        # port-truth file: the MCP bridge rereads it on every call to
        # find the engine (atomic write, CASELAW 1)
        rt = self.workspace / defaults.RUNTIME_DIRNAME
        rt.mkdir(parents=True, exist_ok=True)
        tmp = rt / "engine.json.tmp"
        tmp.write_text(json.dumps({"http": self.http_port,
                                   "ws": self.ws_port,
                                   "pid": os.getpid()}), encoding="utf-8")
        os.replace(tmp, rt / "engine.json")

        if self.spawn_host:
            # on_output goes through the engine's wrapper point
            # (M13): logs the idle clock + relays the stream + the
            # card-withdrawal rule
            self.host = PtyHost(home, on_output=self._on_pty_output,
                                model=defaults.SIDECAR_MODEL)
            self.host.start()
            self.journal.row("lifecycle", "host", pid=getattr(
                self.host._p, "pid", None))
        print(f"[intentos] hub: http://127.0.0.1:{self.http_port}/hub"
              f"  ws: {self.ws_port}  home: {home}")
        if self._cfg:
            # a config override taking effect must be loud --
            # silent overrides are a debugging black hole
            print("[intentos] config.json: "
                  + ", ".join(f"{k}={v}" for k, v in self._cfg.items()))
            self.journal.row("lifecycle", "config",
                             overrides=json.dumps(self._cfg,
                                                  ensure_ascii=False)[:800])
        self._open_hub_at_boot()        # user ruling 2026-08-23 night: open the window at power-on

        last = ""
        try:
            while not self._stop.is_set():
                word = self._phase() + "/" + self._activity()
                if word != last:
                    last = word
                    self.channel.broadcast(self._surface())
                try:
                    self._pump()
                except Exception as e:
                    self.journal.row("chain", "pump-error",
                                     err=repr(e)[:200])
                time.sleep(0.5)
        except KeyboardInterrupt:
            # Ctrl+C on a foreground console (audit 2026-08-25): the
            # README's own start line runs the engine in one, and
            # without this the interrupt unwound straight past the
            # teardown below — every spawned seat orphaned (a
            # ConPTY child has its own console and a headless one
            # is CREATE_NO_WINDOW, so neither sees the Ctrl+C), and
            # the process usually did not even exit: websockets'
            # connection threads are deliberately non-daemon, so it
            # hung holding both ports. Falling through to the normal
            # teardown is the whole fix.
            print("[intentos] interrupt — tearing down")

        # shutdown: signal (frame) already broadcast, tree-kill comes
        # last (CASELAW 16)
        self.journal.row("lifecycle", "end", module=self.module)
        if self.host is not None:
            self.host.stop()
        for inst in list(self._xhosts.values()):
            # M26: instance/executor hosts get collected together
            # too (no orphaned CLIs left behind)
            try:
                inst.stop()
            except Exception:
                pass
        self.channel.stop()
        if self._httpd is not None:
            self._httpd.shutdown()
        try:
            (self.workspace / defaults.RUNTIME_DIRNAME
             / "engine.json").unlink()
        except OSError:
            pass
        self.journal.close()
        self.store.close()
        print("[intentos] down")
        return 0
