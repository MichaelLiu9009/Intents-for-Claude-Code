"use strict";
/* IntentOS deck plugin — a DUMB TRIGGER with a LIVE FACE.
 *
 * M26 (2026-08-22): keys are HTTP requests. The engine compiles this
 * plugin's manifest (one sidebar action per protocol op / member /
 * intent) plus routes.json (action UUID -> trigger URL). keyDown fires
 * one GET; every guard lives in the engine behind /trigger. If this
 * file ever grows a guard, that is the bug.
 *
 * DECK-UI face refresh (user ruling 2026-08-23): faces are DISPLAY ONLY —
 *   · system keys are pure glyphs, no text (text = user's custom keys);
 *   · the engine power key is one TAP toggle (user ruling 08-23 night):
 *     probe status — running -> shutdown, stopped -> start/resurrect;
 *   · keys glow with semantic state (ok flash green / fail flash red /
 *     queued amber / running blue / awaiting-human amber-orange) driven
 *     by the engine's WS frame bus (routes.json __bus__.ws). The bus is
 *     best-effort: unreachable bus = static faces, nothing breaks.
 *
 * routes.json is re-read on every keyDown; only a changed ACTION ROSTER
 * needs a Stream Deck app reload.
 *
 * Runtime reality (2026-08-02, still true): the app's Node is v20 — no
 * global WebSocket. MiniWS below is the proven client-only fallback.
 *
 * Launched by the host with:
 *   node plugin.js -port N -pluginUUID U -registerEvent E -info JSON
 */

const fs = require("fs");
const path = require("path");
const http = require("http");

/* ---- file log: the app does not surface plugin stderr ---------------- */
const LOG = path.join(__dirname, "..", "logs", "plugin.log");
try { fs.mkdirSync(path.dirname(LOG), { recursive: true }); } catch (e) {}
function log(msg) {
  try {
    fs.appendFileSync(LOG, new Date().toISOString() + " " + msg + "\n");
  } catch (e) { /* a log line is never worth a crash */ }
}
process.on("uncaughtException", (e) => {
  log("CRASH " + (e && e.stack || e));
  process.exit(1);
});

/* ---- MiniWS: client-only RFC6455, text frames ------------------------ */
class MiniWS {
  constructor(url) {
    const net = require("net");
    const crypto = require("crypto");
    const m = /^ws:\/\/([^:/]+):(\d+)(\/.*)?$/.exec(url);
    if (!m) throw new Error("MiniWS: bad url " + url);
    this.readyState = 0;                 // CONNECTING
    this.onopen = this.onmessage = this.onclose = this.onerror = null;
    this._crypto = crypto;
    this._buf = Buffer.alloc(0);
    this._hs = false;
    const [, host, port, p] = m;
    const key = crypto.randomBytes(16).toString("base64");
    this._sock = net.connect(+port, host, () => {
      this._sock.write(
        `GET ${p || "/"} HTTP/1.1\r\n` +
        `Host: ${host}:${port}\r\n` +
        "Upgrade: websocket\r\nConnection: Upgrade\r\n" +
        `Sec-WebSocket-Key: ${key}\r\n` +
        "Sec-WebSocket-Version: 13\r\n\r\n");
    });
    this._sock.on("data", (d) => this._data(d));
    this._sock.on("error", (e) => { if (this.onerror) this.onerror(e); });
    this._sock.on("close", () => {
      const was = this.readyState;
      this.readyState = 3;               // CLOSED
      if (was !== 3 && this.onclose) this.onclose({});
    });
  }

  _data(d) {
    this._buf = Buffer.concat([this._buf, d]);
    if (!this._hs) {
      const i = this._buf.indexOf("\r\n\r\n");
      if (i < 0) return;
      const head = this._buf.subarray(0, i).toString();
      this._buf = this._buf.subarray(i + 4);
      if (!/^HTTP\/1\.1 101 /.test(head)) {
        if (this.onerror) this.onerror(new Error("handshake refused"));
        this._sock.destroy();
        return;
      }
      this._hs = true;
      this.readyState = 1;               // OPEN
      if (this.onopen) this.onopen({});
    }
    while (true) {
      if (this._buf.length < 2) return;
      const b0 = this._buf[0], b1 = this._buf[1];
      const op = b0 & 0x0f;
      let len = b1 & 0x7f, off = 2;
      if (len === 126) {
        if (this._buf.length < 4) return;
        len = this._buf.readUInt16BE(2); off = 4;
      } else if (len === 127) {
        if (this._buf.length < 10) return;
        len = Number(this._buf.readBigUInt64BE(2)); off = 10;
      }
      const masked = b1 & 0x80;
      if (masked) off += 4;
      if (this._buf.length < off + len) return;
      let payload = this._buf.subarray(off, off + len);
      if (masked) {
        const mk = this._buf.subarray(off - 4, off);
        const un = Buffer.alloc(len);
        for (let i = 0; i < len; i++) un[i] = payload[i] ^ mk[i & 3];
        payload = un;
      }
      this._buf = this._buf.subarray(off + len);
      if (op === 1) {
        if (this.onmessage) this.onmessage({ data: payload.toString("utf8") });
      } else if (op === 8) {
        this.close();
      } else if (op === 9) {
        this._frame(10, payload);        // pong echoes the ping payload
      }                                  // op 2/0: not spoken here; dropped
    }
  }

  _frame(op, payload) {
    const mask = this._crypto.randomBytes(4);
    const len = payload.length;
    let head;
    if (len < 126) {
      head = Buffer.from([0x80 | op, 0x80 | len]);
    } else if (len < 65536) {
      head = Buffer.alloc(4);
      head[0] = 0x80 | op; head[1] = 0x80 | 126; head.writeUInt16BE(len, 2);
    } else {
      head = Buffer.alloc(10);
      head[0] = 0x80 | op; head[1] = 0x80 | 127;
      head.writeBigUInt64BE(BigInt(len), 2);
    }
    const body = Buffer.alloc(len);
    for (let i = 0; i < len; i++) body[i] = payload[i] ^ mask[i & 3];
    try { this._sock.write(Buffer.concat([head, mask, body])); } catch (e) {}
  }

  send(s) {
    if (this.readyState === 1) this._frame(1, Buffer.from(String(s), "utf8"));
  }

  close() {
    if (this.readyState === 3) return;
    this._frame(8, Buffer.alloc(0));
    this.readyState = 3;
    try { this._sock.destroy(); } catch (e) {}
    if (this.onclose) this.onclose({});
  }
}

const WS = (typeof WebSocket !== "undefined"
            && !process.env.INTENTOS_FORCE_MINIWS) ? WebSocket : MiniWS;

/* ---- args ------------------------------------------------------------ */
const args = {};
for (let i = 2; i < process.argv.length; i += 2)
  args[process.argv[i].replace(/^-/, "")] = process.argv[i + 1];
log(`boot node=${process.version} ws=${WS === MiniWS ? "MiniWS" : "native"} `
    + `port=${args.port}`);

/* ---- routes: action UUID -> trigger URL (engine-compiled) ------------ */
const ROUTES = path.join(__dirname, "..", "routes.json");
function readRoutes() {
  try { return JSON.parse(fs.readFileSync(ROUTES, "utf8")); }
  catch (e) { log("routes read failed: " + e); return {}; }
}
function routeOf(action) { return readRoutes()[action] || null; }

/* ---- engine resurrection (the ONE non-GET capability) ---------------- */
function launchEngine(l, done) {
  try {
    const cp = require("child_process");
    const child = cp.spawn(l.argv[0], l.argv.slice(1), {
      detached: true, stdio: "ignore",
      cwd: l.cwd || undefined,
      env: Object.assign({}, process.env, l.env || {}),
    });
    child.unref();
    log("engine launched pid=" + child.pid);
    done(true);
  } catch (e) {
    log("engine launch FAILED " + e);
    done(false);
  }
}

/* ---- fire one trigger, mirror the verdict on the key ----------------- */
function fire(url, done, quiet) {
  const req = http.get(url, { timeout: quiet ? 3000 : 8000 }, (res) => {
    let body = "";
    res.on("data", (d) => { body += d; });
    res.on("end", () => {
      let ok = res.statusCode === 200;
      try { ok = ok && !JSON.parse(body).error; } catch (e) {}
      if (!quiet) log(`fire ${url} -> ${res.statusCode} `
                      + body.slice(0, 120));
      done(ok, body);
    });
  });
  req.on("timeout", () => { req.destroy(new Error("timeout")); });
  req.on("error", (e) => {
    if (!quiet) log(`fire ${url} FAILED ${e}`);
    done(false, "");
  });
}

/* =====================================================================
 * FACES — semantic state palette (single source: defaults.ST_COLORS;
 * these hexes mirror it verbatim — from the face-refresh draft palette,
 * do not fork).
 * =================================================================== */
const ST = { ok: "#34d399", fail: "#f87171", queue: "#fbbf24",
             run: "#60a5fa", await: "#fb923c", idle: "#4b5563" };
const GLYPH_COLOR = { check: ST.ok, cross: ST.fail, square: ST.queue,
                      stop: ST.fail };
const NEUTRAL = "#e8eaed";
const GLYPH_PATH = {
  check: '<path d="M27 76 L55 104 L117 42" stroke="COL" stroke-width="12"'
       + ' fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
  cross: '<path d="M38 38 L106 106 M106 38 L38 106" stroke="COL"'
       + ' stroke-width="12" fill="none" stroke-linecap="round"/>',
  square: '<rect x="42" y="42" width="60" height="60" rx="7"'
        + ' fill="COL"/>',
  stop: '<rect x="42" y="42" width="60" height="60" rx="7"'
      + ' fill="COL"/>',
  play: '<path d="M50 30 L115 72 L50 114 Z" fill="COL"/>',
  book: '<rect x="40" y="28" width="64" height="88" rx="7" stroke="COL"'
      + ' stroke-width="10" fill="none"/>'
      + '<path d="M58 28 V116" stroke="COL" stroke-width="9"/>',
  power: '<path d="M72 20 V62" stroke="COL" stroke-width="12"'
       + ' fill="none" stroke-linecap="round"/>'
       + '<path d="M40 40 a45 45 0 1 0 64 0" stroke="COL"'
       + ' stroke-width="12" fill="none" stroke-linecap="round"/>',
};

function faceSVG(glyph, word, dot) {
  const col = glyph ? (GLYPH_COLOR[glyph] || NEUTRAL) : NEUTRAL;
  const g = glyph
    ? GLYPH_PATH[glyph].replace(/COL/g, col) : "";
  const glow = word
    ? `<rect x="0" y="0" width="144" height="144" rx="20"`
      + ` fill="${ST[word]}" opacity="0.16"/>`
      + `<rect x="5" y="5" width="134" height="134" rx="17"`
      + ` fill="none" stroke="${ST[word]}" stroke-width="7"/>`
    : "";
  const d = dot
    ? `<circle cx="121" cy="23" r="10" fill="${dot}"/>` : "";
  return "data:image/svg+xml;charset=utf8," + encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="144" height="144">`
    + `<rect width="144" height="144" rx="20" fill="#1d2127"/>`
    + glow + g + d + `</svg>`);
}

function setImage(ctx, image) {
  sd.send(JSON.stringify({ event: "setImage", context: ctx,
                           payload: image ? { image: image, target: 0 }
                                          : {} }));
}

/* ---- key registry + state paint -------------------------------------
 * keys: every Keypad context on screen. State words come from the bus;
 * a flash (ok/fail) overrides steady for FLASH_MS then repaints steady.
 * Everything here is display: a lost frame just means a stale face. */
const keys = new Map();              // context -> action uuid
const flashTimer = new Map();        // context -> timeout handle
const steadyWord = new Map();        // context -> word|null (bus-computed)
const FLASH_MS = 2200;

function protoOf(route) {
  const m = /[?&]protocol=([^&]+)/.exec(route.url || "");
  return m ? decodeURIComponent(m[1]) : null;
}
function intentOf(route) {
  const m = /[?&]intent=([^&]+)(&|$)/.exec(route.url || "");
  return m ? decodeURIComponent(m[1]) : null;
}
function opOf(route) {
  const m = /[?&]op=([^&]+)/.exec(route.url || "");
  return m ? m[1] : null;
}

function paintSteady(ctx) {
  if (flashTimer.has(ctx)) return;           // a flash owns this key; don't preempt it
  const r = routeOf(keys.get(ctx));
  if (!r) return;
  const word = steadyWord.get(ctx) || null;
  if (r.glyph === "power") return;           // power key face is painted by the status poll
  if (!word && !r.glyph) { setImage(ctx, null); return; }
  setImage(ctx, faceSVG(r.glyph || null, word, null));
}

function flash(ctx, word) {
  const r = routeOf(keys.get(ctx));
  if (!r) return;
  const old = flashTimer.get(ctx);
  if (old) clearTimeout(old);
  setImage(ctx, faceSVG(r.glyph || null, word, null));
  flashTimer.set(ctx, setTimeout(() => {
    flashTimer.delete(ctx);
    paintSteady(ctx);
  }, FLASH_MS));
}

/* ---- engine power key: status poll paints the dot -------------------- */
function pollPower() {
  for (const [ctx, action] of keys) {
    const r = routeOf(action);
    if (!r || !r.status_url || r.glyph !== "power") continue;
    if (steadyWord.get(ctx)) continue;   // a live word-face is showing; yield instead of fighting it
    fire(r.status_url, (ok, body) => {
      let up = false, draining = false;
      if (ok) {
        try {
          const j = JSON.parse(body);
          // toggle="open" (a booklet's power key): the light reflects
          // the bracket's open truth value; the engine key reflects
          // engine reachability; draining = amber dot during shutdown
          // (the antidote to the "won't turn off" impression — the
          // engine still answers, but is already on its way out)
          up = r.toggle === "open" ? j.open === true : !j.error;
          draining = j.draining === true;
        } catch (e) {}
      }
      setImage(ctx, faceSVG("power", null,
        draining ? ST.queue : (up ? ST.ok : ST.idle)));
    }, true);
  }
}
setInterval(pollPower, 4000);

/* =====================================================================
 * BUS — engine WS frames drive the faces. Best-effort: no bus, no glow.
 * Frames used: chains (task rows -> steady words + done/failed flash),
 * card / card_close (a card awaiting a human -> amber-orange).
 * =================================================================== */
let bus = null;
const taskPrev = new Map();          // task id -> last seen status
let lastRows = [];                   // latest chains rows
const openCards = new Map();         // card id -> instance

function wordOfStatus(s) {
  return { queued: "queue", running: "run", gated: "await" }[s] || null;
}

function recomputeSteady() {
  /* intent keys (system set): word from that intent's newest live row.
   * proto control keys: Start breathes while the bracket is open;
   * Approve goes amber while a card for this seat awaits a human. */
  const byIntent = new Map();
  for (const row of lastRows) {
    const w = wordOfStatus(row.status);
    if (w && row.intent && !byIntent.has(row.intent))
      byIntent.set(row.intent, w);     // rows arrive newest-first
  }
  const awaiting = new Set();          // instances with an open card
  for (const inst of openCards.values()) if (inst) awaiting.add(inst);
  for (const [ctx, action] of keys) {
    const r = routeOf(action);
    if (!r) continue;
    if (r.glyph === "power") continue;   // power key face belongs to the status poll's light
    let word = null;
    const it = intentOf(r);
    const pn = protoOf(r);
    if (it) {
      word = byIntent.get(it) || null;
      if (awaiting.has("x·solo")) {
        // the solo seat has a card awaiting a human: light up the
        // intent key for that seat's current live task
        const live = lastRows.find((x) =>
          x.intent === it && (x.status === "running"
                              || x.status === "gated")
          && String(x.executor || "").endsWith("solo"));
        if (live) word = "await";
      }
    } else if (pn) {
      const op = opOf(r);
      const open = lastRows.some((x) =>
        x.intent === pn && String(x.spec || "").startsWith("protocol")
        && (x.status === "running" || x.status === "gated"));
      if (op === "start" && open) word = "run";
      if (op === "approve" && awaiting.has("x·" + pn)) word = "await";
    }
    const prev = steadyWord.get(ctx) || null;
    if (word !== prev) {
      if (word) steadyWord.set(ctx, word); else steadyWord.delete(ctx);
      paintSteady(ctx);
    }
  }
}

function onBusFrame(f) {
  if (f.type === "chains" && Array.isArray(f.rows)) {
    lastRows = f.rows;
    for (const row of f.rows) {
      const prev = taskPrev.get(row.id);
      if (prev && prev !== row.status
          && (row.status === "done" || row.status === "failed")) {
        // terminal-state edge: flash the matching intent key
        // (green/red), then revert to steady after 2.2s
        const word = row.status === "done" ? "ok" : "fail";
        for (const [ctx, action] of keys) {
          const r = routeOf(action);
          if (r && intentOf(r) === row.intent) flash(ctx, word);
        }
      }
      taskPrev.set(row.id, row.status);
    }
    recomputeSteady();
    return;
  }
  if (f.type === "card" && f.id !== undefined) {
    openCards.set(f.id, f.instance || null);
    recomputeSteady();
    return;
  }
  if (f.type === "card_close" && f.id !== undefined) {
    openCards.delete(f.id);
    recomputeSteady();
    return;
  }
}

function busConnect() {
  const cfg = readRoutes().__bus__;
  if (!cfg || !cfg.ws) return;               // old routes: no bus, static faces
  try {
    bus = new WS(`ws://127.0.0.1:${cfg.ws}`);
  } catch (e) {
    log("bus connect failed: " + e);
    setTimeout(busConnect, 5000);
    return;
  }
  bus.onopen = () => { log("bus connected :" + cfg.ws); };
  bus.onmessage = (m) => {
    let f;
    try { f = JSON.parse(m.data); } catch (e) { return; }
    try { onBusFrame(f); } catch (e) { log("bus frame error " + e); }
  };
  bus.onclose = () => {
    log("bus closed; retry in 5s");
    setTimeout(busConnect, 5000);            // self-heals across engine restarts
  };
  bus.onerror = () => {};
}
setTimeout(busConnect, 1500);

/* ---- status dials: poll -> $B1 touch strip --------------------------- */
const dials = new Map();             // context -> action uuid
const lastOk = new Map();            // action -> last poll verdict

/* one WORD per bar; COLOR carries the state via the $B1 icon slot
 * (engine-compiled solid PNGs from defaults.ST_COLORS). */
const WORD_ICON = {
  offline: "st_bad", down: "st_bad", failed: "st_bad",
  cancelled: "st_bad",
  await: "st_warn", gated: "st_warn",
  running: "st_run",
  queued: "st_queue", draining: "st_queue",
  closed: "st_idle", none: "st_idle",
  idle: "st_ok", done: "st_ok", up: "st_ok",
};

function stripFeedback(route, ok, body) {
  let s = null;
  if (ok) { try { s = JSON.parse(body); } catch (e) {} }
  if (!s || s.error) {
    return { value: s && s.error ? "gone" : "offline", word: "offline" };
  }
  const bar = route.bar || "status";
  if (bar === "engine-task")
    return { value: (s.name || "—") + (s.more ? " +" + s.more : ""),
             word: s.status || "none" };
  if (bar === "proto-step")
    return { value: s.step || "—",
             word: s.step ? (s.status === "await" ? "await"
                             : (s.step_state || "none"))
                          : "none" };
  return { value: s.status || "?", word: s.status || "none" };
}

function pollAll() {
  if (!dials.size) return;
  const byAction = new Map();
  for (const [ctx, action] of dials) {
    if (!byAction.has(action)) byAction.set(action, []);
    byAction.get(action).push(ctx);
  }
  for (const [action, ctxs] of byAction) {
    const r = routeOf(action);
    if (!r || !r.poll || !r.url) continue;
    fire(r.url, (ok, body) => {
      if (ok !== lastOk.get(action)) {
        lastOk.set(action, ok);
        log("status " + (r.name || action) + " -> "
            + (ok ? "reachable" : "unreachable"));
      }
      const fb = stripFeedback(r, ok, body);
      const icon = "imgs/"
                   + (WORD_ICON[fb.word] || "st_idle") + ".png";
      for (const c of ctxs)
        sd.send(JSON.stringify({ event: "setFeedback", context: c,
                                 payload: { title: r.title || "",
                                            value: fb.value,
                                            icon: icon } }));
    }, true);
  }
}
setInterval(pollAll, 4000);

/* ---- Stream Deck socket ---------------------------------------------- */
const sd = new WS(`ws://127.0.0.1:${args.port}`);
sd.onopen = () => {
  sd.send(JSON.stringify({ event: args.registerEvent,
                           uuid: args.pluginUUID }));
  log("registered");
};
sd.onmessage = (m) => {
  let f;
  try { f = JSON.parse(m.data); } catch (e) { return; }
  if (f.event === "willAppear") {
    if (f.payload && f.payload.controller === "Encoder") {
      dials.set(f.context, f.action);
      pollAll();                     // paint the strip now, not in 4s
    } else {
      keys.set(f.context, f.action);
      const r = routeOf(f.action);
      if (r && r.glyph === "power") pollPower();
      else paintSteady(f.context);
    }
    return;
  }
  if (f.event === "willDisappear") {
    dials.delete(f.context);
    keys.delete(f.context);
    steadyWord.delete(f.context);
    const t = flashTimer.get(f.context);
    if (t) { clearTimeout(t); flashTimer.delete(f.context); }
    return;
  }
  if (f.event !== "keyDown") return;  // trigger-style: fires on press
                                      // (long-press was cut; user
                                      // ruling 2026-08-23 night)
  const r = routeOf(f.action);
  const ctx = f.context;
  if (!r || !r.url) {
    log("no route for " + f.action + " (stale manifest? engine will "
        + "recompile routes.json at next boot)");
    sd.send(JSON.stringify({ event: "showAlert", context: ctx }));
    return;
  }
  const verdict = (ok) => {
    sd.send(JSON.stringify({ event: ok ? "showOk" : "showAlert",
                             context: ctx }));
  };
  if (r.url2 && r.status_url) {
    // power key = trigger-style switch: probe the status endpoint —
    // if it's up, turn it off (url2); if it's down, turn it on
    // (url, falling back to the resurrection order on failure).
    // Probe has a 3s quiet timeout. toggle="open" (a booklet key):
    // open/closed reads body.open's truth value, not reachability;
    // pressing again mid-shutdown = the engine auto-escalates to force.
    fire(r.status_url, (ok, body) => {
      let on = ok;
      if (r.toggle === "open") {
        on = false;
        try { on = ok && JSON.parse(body).open === true; } catch (e) {}
      }
      if (on) { fire(r.url2, verdict); return; }
      fire(r.url, (ok2) => {
        if (!ok2 && r.launch) { launchEngine(r.launch, verdict); return; }
        verdict(ok2);
      });
    }, true);
    return;
  }
  fire(r.url, (ok) => {
    if (!ok && r.launch) { launchEngine(r.launch, verdict); return; }
    verdict(ok);
  });
};
sd.onclose = () => { log("host socket closed; exiting"); process.exit(0); };
sd.onerror = (e) => { log("host socket error: " + e); };
