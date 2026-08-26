"""WS channel — the wire between the page and the engine.

Lean form (M1): a synchronous websockets server, one thread per
client. Frame grammar (shares its vocabulary with the old repo's BN —
the render layer only folds, never invents — CASELAW 22):

    up    {type:"hello", instance?}       -> replies immediately with
                                            surface/intents/cards;
                                            with instance = a flow
                                            window reporting in, also
                                            replies with that seat's
                                            IME wordlist frame
          {type:"chat", text}             -> injected into home's PTY (two-beat)
          {type:"cli_sub"}                -> replay buffer + subsequent stream frames
          {type:"cli_in", data}           -> keystrokes pass straight through
                                            to the PTY (wizard answers ride this too)
          {type:"cli_size", cols, rows, instance?} -> ConPTY resize
                                            (the terminal is genuinely
                                            responsive, the TUI
                                            re-lays-out itself)
          {type:"intent", name, input?}   -> trigger an intent (the IME
                                            wordlist's submit)
          {type:"chains"}                 -> a chains-frame snapshot
                                            reply (drawer refresh)
          {type:"approve", task}          -> a human gate's approve verb
          {type:"cancel", task}           -> cancel an unfinished task
          {type:"retry", task, reason?}   -> reopen a settled deliver
                                            task (reason = the note)
          {type:"validate", name}         -> run the sim check chain
                                            on a draft (no shipped
                                            surface sends this today —
                                            wire-only, for hand-rolled
                                            clients; audit 2026-08-25)
          {type:"card_answer", id, action, data?}  -> a card's answer (M13)
          {type:"stop"}                   -> graceful shutdown
    down  {type:"surface", focus, peers:{mode:{phase, activity}}}
          {type:"cli", data}              (sent only to subscribers — privacy surface, never broadcast)
          {type:"chat", name, text, t}    (archival mirror of the conversation)
          {type:"card", id, instance, kind, title, body, options?, task?, t}
          {type:"card_close", id} / {type:"cards", rows}(hello replay)
          {type:"feed", kind, text, t}    (task feed event line)
          {type:"flow_close", instance}   (shutdown's window-closing order; cc'd to hub to pull the tab)
          {type:"flow_open", instance}    (hub add/switch-tab order, sent only to ·hub)
          {type:"intents", instance, rows}(seat's IME wordlist, sent only to that window)

CASELAW 6: outgoing frames go through json.dumps(ensure_ascii=False)
before being handed to websockets (which sends UTF-8); an incoming
frame with malformed JSON is skipped, never fatal.

**Loopback guardrail (security ruling 2026-08-12)**: the verbs on this
wire can inject into the host, approve human gates, kill the engine —
and WS handshakes aren't bound by same-origin policy, so **any web
page** in a local browser can connect to 127.0.0.1. A gatekeeper runs
during the handshake (process_request); its criteria live in
kernel/netguard — a rejection means not a single frame is accepted.
"""
from __future__ import annotations

import json
import threading

from . import netguard


class Channel:
    def __init__(self, port: int, origin_port: int | None = None):
        self.port = port
        # allowed page origin = the engine's own HTTP port (home of
        # the observe page). None = only clients with no Origin
        # (non-browser) are let through
        self.origin_port = origin_port
        self._clients: set = set()          # everyone (surface/chat broadcast surface)
        # cli stream subscribers (targeted surface). M26: ws ->
        # instance (None = main host) — one WS, one terminal port;
        # a flow window subscribes to its own instance's stream.
        self._subs: dict = {}
        # flow-window registry (user ruling 2026-08-23: one seat, one
        # window): a connection whose hello carries instance = the
        # card-stream window for that seat; if a live window is
        # already registered no new window is opened, and the
        # downstream flow_close frame from shutdown closes the window.
        self._flows: dict = {}
        self._lock = threading.Lock()
        self._server = None
        # engine wiring points (bound when the engine starts)
        self.on_chat = lambda text, instance=None: None
        self.on_cli_in = lambda data, instance=None: None
        self.on_cli_size = lambda cols, rows, instance=None: None
        self.on_stop = lambda: None
        self.on_approve = lambda tid: None
        self.on_cancel = lambda cid: None
        self.on_retry = lambda tid, reason="": None
        self.on_validate = lambda name: None
        self.on_intent = lambda name, user_input="": None
        self.on_card_answer = lambda cid, action, data=None: None
        self.intents_frame = lambda: {"type": "intents", "rows": []}
        # seat IME (user ruling 2026-08-23): once a flow window's
        # hello reports in, the engine replies per-seat with its own
        # wordlist frame (None = no reply)
        self.flow_intents_frame = lambda instance: None
        self.chains_frame = lambda: {"type": "chains", "rows": []}
        self.cards_frame = lambda: {"type": "cards", "rows": []}
        self.on_blocked = lambda face, detail: None   # gatekeeper leaves a trace (never silent)
        self.surface = lambda: {"type": "surface", "focus": None, "peers": {}}
        self.replay = lambda instance=None: ""

    # ---- downstream --------------------------------------------------------

    def _send(self, ws, frame: dict) -> None:
        try:
            ws.send(json.dumps(frame, ensure_ascii=False))
        except Exception:
            pass                            # one client dying doesn't take the others down with it

    def broadcast(self, frame: dict) -> None:
        with self._lock:
            targets = list(self._clients)
        for ws in targets:
            self._send(ws, frame)

    def push_cli(self, data: str, instance: str | None = None) -> None:
        with self._lock:
            targets = [ws for ws, inst in self._subs.items()
                       if inst == instance]
        for ws in targets:
            self._send(ws, {"type": "cli", "data": data})

    def flow_alive(self, instance: str) -> bool:
        """Does this seat already have a live flow surface? (same
        criterion for both the hub window and a single-seat window)"""
        with self._lock:
            return instance in self._flows.values()

    def close_flow(self, instance: str) -> None:
        """Downstream flow_close: this seat's surface closes itself
        (the window-closing half of shutdown). Cc'd to hub (·hub) —
        in tab form, it's the hub shell that pulls the tab."""
        with self._lock:
            targets = [ws for ws, inst in self._flows.items()
                       if inst == instance or inst == "·hub"]
        for ws in targets:
            self._send(ws, {"type": "flow_close", "instance": instance})

    def flow_open(self, instance: str) -> None:
        """Downstream flow_open: tells hub to add/switch to this
        seat's tab (the window-opening half done by the engine)."""
        with self._lock:
            targets = [ws for ws, inst in self._flows.items()
                       if inst == "·hub"]
        for ws in targets:
            self._send(ws, {"type": "flow_open", "instance": instance})

    # ---- client loop ---------------------------------------------------------

    def _handler(self, ws) -> None:
        with self._lock:
            self._clients.add(ws)
        try:
            for raw in ws:
                try:
                    f = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if not isinstance(f, dict):
                    continue
                kind = f.get("type")
                try:
                    self._dispatch(ws, kind, f)
                except Exception:
                    # CASELAW 6: one verb dying doesn't kill the whole
                    # connection; the real cause is caught by the
                    # engine side into the journal
                    continue
        except Exception:
            pass
        finally:
            with self._lock:
                self._clients.discard(ws)
                self._subs.pop(ws, None)
                self._flows.pop(ws, None)

    def _dispatch(self, ws, kind, f) -> None:
        if kind == "hello":
            self._send(ws, self.surface())
            self._send(ws, self.intents_frame())
            self._send(ws, self.cards_frame())   # late arrivals can also see what's pending
            inst = f.get("instance")
            if isinstance(inst, str) and inst:
                with self._lock:
                    self._flows[ws] = inst       # flow window reporting in
                fi = self.flow_intents_frame(inst)
                if fi:
                    self._send(ws, fi)           # seat IME wordlist
        elif kind == "intent":
            name = f.get("name")
            if isinstance(name, str) and name:
                self.on_intent(name, str(f.get("input") or ""))
        elif kind == "chat":
            text = str(f.get("text") or "").strip()
            inst = f.get("instance")
            if text:
                self.on_chat(text,
                             inst if isinstance(inst, str) and inst
                             else None)
        elif kind == "cli_sub":
            inst = f.get("instance")
            inst = inst if isinstance(inst, str) and inst else None
            with self._lock:
                self._subs[ws] = inst
            buf = self.replay(inst)
            if buf:
                self._send(ws, {"type": "cli", "data": buf})
        elif kind == "cli_in":
            data = f.get("data")
            inst = f.get("instance")
            if isinstance(data, str) and data:
                self.on_cli_in(data,
                               inst if isinstance(inst, str) and inst
                               else None)
        elif kind == "cli_size":
            cols, rows = f.get("cols"), f.get("rows")
            inst = f.get("instance")
            if isinstance(cols, int) and isinstance(rows, int):
                self.on_cli_size(cols, rows,
                                 inst if isinstance(inst, str) and inst
                                 else None)
        elif kind == "chains":
            self._send(ws, self.chains_frame())
        elif kind == "approve":
            tid = f.get("task")
            if isinstance(tid, int):
                self.on_approve(tid)
        elif kind == "cancel":
            cid = f.get("chain")
            if isinstance(cid, int):
                self.on_cancel(cid)
        elif kind == "retry":
            tid = f.get("task")
            if isinstance(tid, int):
                self.on_retry(tid, str(f.get("reason") or ""))
        elif kind == "validate":
            name = f.get("name")
            if isinstance(name, str) and name:
                self.on_validate(name)
        elif kind == "card_answer":
            cid = f.get("id")
            if isinstance(cid, int):
                self.on_card_answer(cid, str(f.get("action") or ""),
                                    f.get("data"))
        elif kind == "stop":
            self.on_stop()
        # unknown frames are ignored by name (forward compatible; only
        # config keys are hard-rejected — CASELAW 25)

    # ---- lifecycle -------------------------------------------------------

    def _screen(self, conn, request):
        """Handshake gatekeeper: Origin allowlist + Host validation
        (kernel/netguard precedent). Returning None lets it through;
        returning a Response means the handshake fails and the
        connection is voided. Non-browser clients carry no Origin and
        pass through as usual — zero false positives."""
        h = request.headers
        origin, host = h.get("Origin"), h.get("Host")
        if (netguard.origin_ok(origin, self.origin_port or 0)
                and netguard.host_ok(host)):
            return None
        self.on_blocked("ws", {"origin": origin, "host": host})
        return conn.respond(403, "forbidden origin\n")

    def start(self) -> None:
        from websockets.sync.server import serve
        self._server = serve(self._handler, "127.0.0.1", self.port,
                             process_request=self._screen)
        threading.Thread(target=self._server.serve_forever,
                         daemon=True, name="channel").start()

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception:
                pass
