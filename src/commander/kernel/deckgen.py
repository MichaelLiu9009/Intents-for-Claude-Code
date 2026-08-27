"""Stream Deck keyset compiler — protocols and intents become importable
`.streamDeckProfile` files (M26: keys are HTTP requests, the Elgato app
is the UI; the engine's own bind/padbridge layer is legacy).

Format is ProfilesV3, copied verbatim from profiles the installed app
writes on this machine (empirical schema, 2026-08-22):

    <NAME>.streamDeckProfile            (zip)
    └── <PROFILE-UUID>.sdProfile/
        ├── manifest.json               Device / Name / Pages / Version 3.0
        └── Profiles/<PAGE-UUID>/
            ├── manifest.json           Controllers[Keypad].Actions{"c,r"}
            └── Images/k<i>.png         key faces (solid color; the app
                                        overlays the Title text)

Every key is a `com.elgato.streamdeck.system.website` action with
`openInBrowser: false` — a background GET against the engine's /trigger
endpoint. No plugin, no translation layer: the URL itself carries the
routing (`protocol=X&op=start` / `protocol=X&member=Y` / `intent=N`).

Determinism: profile/page/action UUIDs are uuid5 of the keyset name, so
recompiling an unchanged protocol reproduces identical bytes (the same
artifact-hash discipline as every other compiled artifact here).

Grid note: the connected device's factory pages populate exactly the
coordinate space "0,0".."2,3" (12 keys). The control strip lives on the
"0,*" edge, member slots fill the rest — a clean edge strip under either
axis-order reading of the coordinate string. 4 controls + 8 member slots
also exactly matches the protocol seat cap (PROTO_MAX_SEATS 10 incl. the
two marker verbs → ≤8 members).
"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import struct
import uuid
import zipfile
import zlib
from pathlib import Path
from urllib.parse import quote

from .. import defaults

# key coordinate space (mirrors the device's own factory pages)
CONTROL_COORDS = ("0,0", "0,1", "0,2", "0,3")
SLOT_COORDS = ("1,0", "1,1", "1,2", "1,3", "2,0", "2,1", "2,2", "2,3")
MAX_KEYS = len(CONTROL_COORDS) + len(SLOT_COORDS)

# key face colors (solid; the app renders Title text on top)
COLOR_START = (30, 112, 60)
COLOR_APPROVE = (32, 84, 148)
COLOR_INTERRUPT = (166, 106, 22)
COLOR_SHUTDOWN = (142, 40, 40)
COLOR_MEMBER = (54, 60, 74)
COLOR_INTENT = (217, 119, 87)    # Claude orange (user ruling 2026-08-24)
COLOR_STATUS = (40, 84, 92)

# DECK-UI refresh (user ruling 2026-08-23): key-face background =
# hardware black; system keys are pure graphics, zero text, the
# glyph vocabulary is pinned — green check approve / red cross
# cancel / yellow square interrupt / ▶ open the book / closed book
# wrap-up / power glyph Engine (start+stop combined). Color is
# reserved for status only.
FACE_BG = (29, 33, 39)           # #1d2127, same base as the refresh draft
GLYPH_NEUTRAL = (232, 234, 237)  # #e8eaed

# status-color icons ($B1 icon slot = the bar's color surface; the
# plugin swaps images by status word) — the semantic palette is
# single-sourced in defaults.ST_COLORS (all three surfaces share it,
# user ruling 2026-08-23)
STATUS_ICON_COLORS = {
    "st_ok": defaults.ST_COLORS["ok"],
    "st_run": defaults.ST_COLORS["run"],
    "st_warn": defaults.ST_COLORS["await"],
    "st_bad": defaults.ST_COLORS["fail"],
    "st_queue": defaults.ST_COLORS["queue"],
    "st_idle": defaults.ST_COLORS["idle"],
}

WEBSITE_UUID = "com.elgato.streamdeck.system.website"

_NS = uuid.uuid5(uuid.NAMESPACE_URL, "intentos.deck")


def _uid(*parts: str) -> str:
    return str(uuid.uuid5(_NS, "/".join(parts)))


def _png(rgb: tuple[int, int, int], size: int = 72) -> bytes:
    """Minimal solid-color PNG (stdlib only — no imaging dependency)."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(rgb) * size
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(row * size, 9))
            + chunk(b"IEND", b""))


def _png_pixels(rows: list[list[tuple[int, int, int]]], size: int) -> bytes:
    """Arbitrary pixel-surface PNG (stdlib only; the general form of
    _png)."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes(v for p in r for v in p) for r in rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def _dist_seg(x, y, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((x - x1) * dx
                                               + (y - y1) * dy) / l2))
    return math.hypot(x - (x1 + t * dx), y - (y1 + t * dy))


def _sd_shape(x, y, sh, half):
    """Signed distance (negative = inside the shape); units = 24
    viewbox coordinates."""
    k = sh[0]
    if k == "seg":
        return _dist_seg(x, y, *sh[1:]) - half
    if k == "rectfill":
        x1, y1, x2, y2 = sh[1:]
        if x1 <= x <= x2 and y1 <= y <= y2:
            return -min(x - x1, x2 - x, y - y1, y2 - y)
        return math.hypot(max(x1 - x, 0, x - x2), max(y1 - y, 0, y - y2))
    if k == "rectring":
        x1, y1, x2, y2 = sh[1:]
        if x1 <= x <= x2 and y1 <= y <= y2:
            return min(x - x1, x2 - x, y - y1, y2 - y) - half
        return math.hypot(max(x1 - x, 0, x - x2),
                          max(y1 - y, 0, y - y2)) - half
    if k == "trifill":
        ax, ay, bx, by, cx, cy = sh[1:]
        d1 = (bx - ax) * (y - ay) - (by - ay) * (x - ax)
        d2 = (cx - bx) * (y - by) - (cy - by) * (x - bx)
        d3 = (ax - cx) * (y - cy) - (ay - cy) * (x - cx)
        edge = min(_dist_seg(x, y, ax, ay, bx, by),
                   _dist_seg(x, y, bx, by, cx, cy),
                   _dist_seg(x, y, cx, cy, ax, ay))
        inside = (d1 >= 0 and d2 >= 0 and d3 >= 0) or \
                 (d1 <= 0 and d2 <= 0 and d3 <= 0)
        return -edge if inside else edge
    if k == "ring":
        cx, cy, r, gap_c, gap_h = sh[1:]
        ang = math.degrees(math.atan2(y - cy, x - cx))
        if abs((ang - gap_c + 180.0) % 360.0 - 180.0) < gap_h:
            return 1e9                       # gap angular region: don't draw
        return abs(math.hypot(x - cx, y - cy) - r) - half
    return 1e9


# glyph vocabulary (24 viewbox; color None = neutral white, else
# takes the semantic color) — green check approve / red cross
# cancel / yellow square interrupt / ▶ open book / closed book /
# power Engine
GLYPHS: dict[str, tuple[str | None, list[tuple]]] = {
    "check": ("ok", [("seg", 4.5, 12.5, 9.2, 17.2),
                     ("seg", 9.2, 17.2, 19.5, 6.8)]),
    "cross": ("fail", [("seg", 6.2, 6.2, 17.8, 17.8),
                       ("seg", 17.8, 6.2, 6.2, 17.8)]),
    "square": ("queue", [("rectfill", 6.8, 6.8, 17.2, 17.2)]),
    "stop": ("fail", [("rectfill", 6.8, 6.8, 17.2, 17.2)]),
    "play": (None, [("trifill", 8.2, 5.2, 19.0, 12.0, 8.2, 18.8)]),
    "book": (None, [("rectring", 6.5, 4.8, 17.5, 19.2),
                    ("seg", 9.4, 4.8, 9.4, 19.2)]),
    "power": (None, [("ring", 12, 13, 7.4, -90, 38),
                     ("seg", 12, 3.4, 12, 10.4)]),
}


def glyph_png(name: str, size: int = 72,
              bg: tuple[int, int, int] = FACE_BG) -> bytes:
    """One system key's static key face: hardware-black background +
    glyph, zero text (DECK-UI refresh). Dynamic states (color flash /
    breathing) are overlaid by the plugin at runtime via setImage —
    this is the base face."""
    cname, shapes = GLYPHS[name]
    color = defaults.ST_COLORS[cname] if cname else GLYPH_NEUTRAL
    scale = size / 24.0
    half = 0.95                          # stroke half-width (24 coordinate system)
    rows = []
    for j in range(size):
        row = []
        for i in range(size):
            x, y = (i + 0.5) / scale, (j + 0.5) / scale
            d = min(_sd_shape(x, y, sh, half) for sh in shapes)
            a = max(0.0, min(1.0, 0.5 - d * scale))
            row.append(tuple(int(b + (c - b) * a)
                             for b, c in zip(bg, color)))
        rows.append(row)
    return _png_pixels(rows, size)


def discover_device(profiles_dir: Path | None = None) -> dict:
    """Copy the Device block from a profile the installed app already
    owns — targets the user's own hardware with zero configuration.
    Absent app/device: empty block (the app asks on import)."""
    if profiles_dir is None:
        import os
        appdata = os.environ.get("APPDATA")
        if not appdata:
            return {"Model": "", "UUID": ""}
        profiles_dir = Path(appdata) / "Elgato" / "StreamDeck" / "ProfilesV3"
    best, best_t = None, -1.0
    try:
        for mf in profiles_dir.glob("*.sdProfile/manifest.json"):
            try:
                t = mf.stat().st_mtime
                if t <= best_t:
                    continue
                d = json.loads(mf.read_text(encoding="utf-8"))
                dev = d.get("Device")
                if isinstance(dev, dict) and dev.get("Model"):
                    best, best_t = dev, t
            except (OSError, ValueError):
                continue
    except OSError:
        pass
    return best or {"Model": "", "UUID": ""}


def _action(keyset: str, coord: str, title: str, url: str,
            image_ref: str) -> dict:
    """One Website key — schema cloned from an app-authored profile."""
    return {
        "ActionID": _uid("action", keyset, coord),
        "LinkedTitle": True,
        "Name": "Website",
        "Plugin": {"Name": "Website", "UUID": WEBSITE_UUID,
                   "Version": "1.0"},
        "Resources": None,
        "Settings": {"browser": "", "openInBrowser": False, "path": url},
        "State": 0,
        "States": [{"FontFamily": "", "FontSize": 11, "FontStyle": "",
                    "FontUnderline": False, "Image": image_ref,
                    "OutlineThickness": 2, "ShowTitle": True,
                    "Title": title, "TitleAlignment": "middle",
                    "TitleColor": "#ffffff"}],
        "UUID": WEBSITE_UUID,
    }


def compile_keyset(out_path: Path, keyset_name: str, profile_title: str,
                   keys: list[dict], device: dict | None = None) -> Path:
    """Write one single-page .streamDeckProfile.

    keys: [{"coord": "c,r", "title": str, "url": str,
            "color": (r,g,b)}, ...]
    """
    if device is None:
        device = discover_device()
    puid = _uid("profile", keyset_name).upper()
    pgid = _uid("page", keyset_name)
    actions: dict[str, dict] = {}
    images: dict[str, bytes] = {}
    for i, k in enumerate(keys):
        img = f"Images/k{i}.png"
        images[img] = _png(tuple(k["color"]))
        actions[k["coord"]] = _action(keyset_name, k["coord"],
                                      k["title"], k["url"], img)
    page = {"Controllers": [{"Actions": actions or None, "Type": "Keypad"},
                            {"Actions": None, "Type": "Encoder"}],
            "Icon": "", "Name": ""}
    top = {"Device": device, "Name": profile_title,
           "Pages": {"Current": pgid, "Default": pgid, "Pages": [pgid]},
           "Version": "3.0"}
    root = f"{puid}.sdProfile"
    pdir = f"{root}/Profiles/{pgid.upper()}"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # atomic-ish: write then replace, so a half-written zip never sits
    # at the published path
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    def _entry(name: str) -> zipfile.ZipInfo:
        # Pinned timestamp (audit 2026-08-25 §4-doc-drift): writestr's
        # default ZipInfo stamps *now*, which quietly broke the
        # byte-reproducibility this module's header claims. The epoch
        # is arbitrary and constant; only sameness matters.
        zi = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
        zi.compress_type = zipfile.ZIP_DEFLATED
        return zi

    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(_entry(f"{root}/manifest.json"),
                   json.dumps(top, ensure_ascii=False,
                              separators=(",", ":")))
        z.writestr(_entry(f"{pdir}/manifest.json"),
                   json.dumps(page, ensure_ascii=False,
                              separators=(",", ":")))
        for name, data in images.items():
            z.writestr(_entry(f"{pdir}/{name}"), data)
    try:
        tmp.replace(out_path)
    except OSError:
        # Windows: replace fails while a reader holds the target open.
        # One unlink+retry; a still-held target raises to the caller
        # (which journals — compile failures are never silent).
        try:
            out_path.unlink()
        except OSError:
            pass
        tmp.replace(out_path)
    return out_path


def _trigger_url(port: int, **params: str) -> str:
    qs = "&".join(f"{k}={quote(v, safe='')}" for k, v in params.items())
    return f"http://127.0.0.1:{port}/trigger?{qs}"


def protocol_keyset(out_dir: Path, pname: str, members: list[str],
                    port: int, device: dict | None = None) -> Path:
    """Per-protocol keyset: fixed control strip (Start / Approve /
    Interrupt / Shutdown) + one slot per member intent. Compiled next to
    the protocol artifact (utility/protocols/<name>/), never into the
    tool layer."""
    controls = (("Start", "start", COLOR_START),
                ("Approve", "approve", COLOR_APPROVE),
                ("Interrupt", "interrupt", COLOR_INTERRUPT),
                ("Shutdown", "shutdown", COLOR_SHUTDOWN))
    keys = [{"coord": c, "title": title,
             "url": _trigger_url(port, protocol=pname, op=op),
             "color": color}
            for c, (title, op, color) in zip(CONTROL_COORDS, controls)]
    for c, m in zip(SLOT_COORDS, members[:len(SLOT_COORDS)]):
        keys.append({"coord": c, "title": m,
                     "url": _trigger_url(port, protocol=pname, member=m),
                     "color": COLOR_MEMBER})
    return compile_keyset(out_dir / f"{pname}.streamDeckProfile",
                          f"protocol:{pname}", f"IntentOS · {pname}",
                          keys, device)


def intents_keyset(out_dir: Path, names: list[str], port: int,
                   device: dict | None = None) -> tuple[Path, list[str]]:
    """System keyset: one one-way trigger key per independent intent
    (executor approvals never ride the keyboard — they live in the
    sidecar card flow). Capacity = the full grid; overflow is returned
    for the caller to log, never silently dropped."""
    coords = CONTROL_COORDS + SLOT_COORDS
    fit, dropped = names[:len(coords)], names[len(coords):]
    keys = [{"coord": c, "title": n,
             "url": _trigger_url(port, intent=n),
             "color": COLOR_INTENT}
            for c, n in zip(coords, fit)]
    path = compile_keyset(out_dir / "intents.streamDeckProfile",
                          "intents", "IntentOS · Intents", keys, device)
    return path, dropped


# ---------------------------------------------------------------------------
# M26b: plugin-form custom keyset (user ruling 2026-08-22 night,
# revision (1)) — one sidebar action per key, the user **drags it
# into their own profile** from the sidebar, no need to import a
# whole page. The plugin body = static plugin.js (keyDown -> GET
# /trigger, showOk/showAlert acknowledgment) + an engine-compiled
# manifest.json (action roster) + routes.json (UUID -> URL, read
# fresh on every keyDown — a changed port doesn't need an app
# restart; **only a changed action roster needs a Stream Deck app
# restart**).
#
# Revision (2) (user's third revision to (1), same night): SD's
# sidebar grouping (Category) is plugin-level — one plugin gets
# only one group — so **one book is one standalone plugin**
# (Category = book name, holding only that book's four fixed keys
# + members), plus the system-provided intents plugin (one-way
# trigger keys for standalone intents). The merged com.intentos.deck
# is retired; compilation also sweeps orphan directories under this
# prefix (leftovers from retired books / renames) — other plugins'
# directories are never touched.

PLUGIN_UUID_BASE = "com.intentos.deck"
# Legacy (pre-2026-08-25) flat names: the UUID was md5(book name)
# alone, with no workspace identity — so two workspaces on one
# machine compiled into the SAME plugin directories, each engine's
# sweep deleted the other's books, and a same-named book silently
# repointed its keys at whichever engine compiled last. Kept only so
# the sweep can still collect these orphans.
LEGACY_INTENTS_UUID = PLUGIN_UUID_BASE + ".intents"
INTENTS_PLUGIN_UUID = LEGACY_INTENTS_UUID          # back-compat alias

# DECK-UI refresh (user ruling 2026-08-23; 08-24 merged start/stop
# key): system control keys are pure graphics, zero text — power
# glyph open/close (tap toggles: closed book opens, an open book
# wraps up via ·wrap; pressing again mid-ceremony = force) / green
# check approve / yellow square interrupt. Key-face grammar:
# graphics = engine system key, text = user-customized key
# (member/intent).
_CONTROLS = (("Approve", "approve", COLOR_APPROVE, "check"),
             ("Interrupt", "interrupt", COLOR_INTERRUPT, "square"))


def _slug(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:10]


def ws_tag(workspace) -> str:
    """Workspace identity for the plugin namespace (audit
    2026-08-25). Case-folded because Windows hands the same
    directory back under different casings and a tag that jitters
    would orphan a whole keyset."""
    return _slug(str(Path(workspace).resolve()).lower())


def plugin_ns(tag: str) -> str:
    """This workspace's plugin namespace — every UUID it compiles
    hangs off here, and its sweep is confined to the same prefix, so
    a second workspace on the same machine can never collect this
    one's books."""
    return f"{PLUGIN_UUID_BASE}.w{tag}"


def intents_plugin_uuid(tag: str) -> str:
    return f"{plugin_ns(tag)}.intents"


def proto_plugin_uuid(pname: str, tag: str) -> str:
    return f"{plugin_ns(tag)}.p{_slug(pname)}"


def proto_entries(pname: str, members: list[str], port: int) -> list[dict]:
    """One book's sidebar roster: four fixed keys + member slots. The
    key field is identity (raw material for the action UUID slug,
    stable across recompiles); Name uses the bare title — the group
    header is already the book name, prefixing it again would just be
    noise."""
    out: list[dict] = []
    # merged start/stop key (user ruling 2026-08-24): power glyph
    # toggle — url=start / url2=shutdown / status_url probes the
    # open truth value (toggle="open": the plugin judges open/closed
    # by body.open; only the engine key judges by reachability)
    out.append({"key": f"proto/{pname}/op/power",
                "name": "Start / Shutdown", "title": "",
                "url": _trigger_url(port, protocol=pname, op="start"),
                "url2": _trigger_url(port, protocol=pname,
                                     op="shutdown"),
                "status_url": _trigger_url(port, protocol=pname,
                                           op="status"),
                "color": COLOR_START, "glyph": "power",
                "toggle": "open"})
    for title, op, color, glyph in _CONTROLS:
        # pure-glyph keys always use an empty-string title (aligned
        # with the intents system keys) — ShowTitle is already off,
        # but some SD surfaces still leak Title through, so this is
        # a belt-and-suspenders measure
        out.append({"key": f"proto/{pname}/op/{op}",
                    "name": title, "title": "",
                    "url": _trigger_url(port, protocol=pname, op=op),
                    "color": color, "glyph": glyph})
    for m in members:
        out.append({"key": f"proto/{pname}/member/{m}",
                    "name": m, "title": m,
                    "url": _trigger_url(port, protocol=pname, member=m),
                    "color": COLOR_MEMBER})
    # instance status bar (user ruling 2026-08-23): an Encoder
    # action, dragged onto a dial slot, the $B1 touch strip shows
    # this book's status (the plugin polls op=status and renders via
    # setFeedback; the dial press isn't bound — legacy-hardware
    # precedent 2026-08-05: some device models hard-wire
    # rotary-select).
    out.append({"key": f"proto/{pname}/status",
                "name": "Status", "title": pname,
                "url": _trigger_url(port, protocol=pname, op="status"),
                "color": COLOR_STATUS, "encoder": True, "poll": True,
                "bar": "proto-status"})
    # Step bar (user ruling 2026-08-23, second question): the text
    # bar = the current member step's name, color = claim state
    # (delivery is running, the host's step_done switches it to
    # done in the accounting)
    out.append({"key": f"proto/{pname}/step",
                "name": "Step", "title": pname,
                "url": _trigger_url(port, protocol=pname, op="status"),
                "color": COLOR_STATUS, "encoder": True, "poll": True,
                "bar": "proto-step"})
    return out


def intent_entries(intents: list[str], port: int) -> list[dict]:
    """System intents roster: one one-way trigger key per standalone
    intent (approve never rides the keyboard, it goes through the
    card flow)."""
    return [{"key": f"intent/{n}", "name": n, "title": n,
             "url": _trigger_url(port, intent=n),
             "color": COLOR_INTENT}
            for n in intents]


def engine_entries(port: int) -> list[dict]:
    """Engine system keys (DECK-UI refresh, user ruling 2026-08-23):
    start and stop are **merged into one key** (power glyph + status
    dot): a short press = start (when the engine isn't running, the
    plugin revives it using the launch order in routes), a long
    press = shutdown (misfire protection). The plugin polls
    status_url to light/dim the status dot. Solo quick-action
    surface: green check approves the newest card / red cross force-
    interrupts the newest order. All system keys are pure graphics,
    zero text."""
    return [
        {"key": "engine/power", "name": "Engine",
         "title": "",
         "url": _trigger_url(port, engine="start"),
         "url2": _trigger_url(port, engine="shutdown"),
         "status_url": _trigger_url(port, engine="status"),
         "color": COLOR_START, "launch": True,
         "glyph": "power"},
        {"key": "engine/status", "name": "Engine · Status",
         "title": "engine",
         "url": _trigger_url(port, engine="status"),
         "color": COLOR_STATUS, "encoder": True, "poll": True,
         "bar": "engine-status"},
        # Task bar (user ruling 2026-08-23, second question): the
        # newest in-flight standalone intent order — text bar =
        # intent name (+N when parallel), color = task state
        {"key": "engine/task", "name": "Engine · Task",
         "title": "task",
         "url": _trigger_url(port, engine="task"),
         "color": COLOR_STATUS, "encoder": True, "poll": True,
         "bar": "engine-task"},
        # solo execution seat's physical quick-action surface:
        # approve the newest card / force-interrupt the newest order
        {"key": "engine/approve", "name": "Solo · Approve",
         "title": "",
         "url": _trigger_url(port, engine="approve"),
         "color": COLOR_APPROVE, "glyph": "check"},
        # red square = force-stop (user ruling 2026-08-24: same
        # shape as interrupt, different color — yellow square
        # interrupts the current turn, red square force-stops the
        # whole order)
        {"key": "engine/cancel", "name": "Solo · Cancel",
         "title": "",
         "url": _trigger_url(port, engine="cancel"),
         "color": COLOR_SHUTDOWN, "glyph": "stop"},
    ]


def compile_plugin(plugins_root: Path, plugin_uuid: str, name: str,
                   category: str, description: str, entries: list[dict],
                   src_js: Path, launch: dict | None = None,
                   ws_port: int | None = None) -> Path:
    """Compile a roster into <plugin_uuid>.sdPlugin/, writing
    directly to the Stream Deck Plugins directory (engine-owned,
    compiled on every registration; tolerant per-file when a file is
    locked). launch = the engine revival order (argv/cwd/env), baked
    into the route of whichever entry carries the launch flag.
    ws_port = the engine's WS bus port, written into the routes'
    __bus__ entry — the plugin subscribes to frames for color-flash
    effects (DECK-UI refresh; if the subscription fails it degrades
    to a static surface — the lights never carry load-bearing
    meaning)."""
    d = plugins_root / f"{plugin_uuid}.sdPlugin"
    for sub in ("bin", "imgs", "logs"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    actions, routes = [], {}
    imgs: dict[str, bytes] = {
        "imgs/plugin.png": _png(COLOR_INTENT, 32),
        "imgs/plugin@2x.png": _png(COLOR_INTENT, 64),
    }
    for icon, rgb in STATUS_ICON_COLORS.items():
        imgs[f"imgs/{icon}.png"] = _png(rgb, 72)
    for e in entries:
        sid = _slug(e["key"])
        uid = f"{plugin_uuid}.k{sid}"
        img = f"imgs/k{sid}"
        glyph = e.get("glyph")
        if glyph:
            # system key: pure graphics, zero text (key-face grammar
            # — graphics = system, text = customized)
            imgs[img + ".png"] = glyph_png(glyph, 72)
            imgs[img + "@2x.png"] = glyph_png(glyph, 144)
        else:
            imgs[img + ".png"] = _png(tuple(e["color"]), 72)
            imgs[img + "@2x.png"] = _png(tuple(e["color"]), 144)
        act = {
            "UUID": uid, "Name": e["name"], "Icon": img,
            "States": [{"Image": img, "Title": e["title"],
                        "TitleAlignment": "middle",
                        "ShowTitle": not glyph}],
            "SupportedInMultiActions": True,
            "Controllers": ["Keypad"]}
        if e.get("encoder"):
            # status bar: Encoder action ($B1 = title + value touch
            # strip, precedent copied from com.claudecommander.deck's
            # Commander Dial)
            act["Controllers"] = ["Encoder"]
            act["Encoder"] = {"layout": "$B1"}
            act["SupportedInMultiActions"] = False
        actions.append(act)
        routes[uid] = {"url": e["url"], "name": e["name"]}
        if glyph:
            routes[uid]["glyph"] = glyph
        for extra in ("url2", "status_url", "toggle"):
            if e.get(extra):
                routes[uid][extra] = e[extra]
        if e.get("hold"):
            routes[uid]["hold"] = True
        if e.get("poll"):
            routes[uid]["poll"] = True
            routes[uid]["title"] = e["title"]
            routes[uid]["bar"] = e.get("bar") or "status"
        if e.get("launch") and launch:
            routes[uid]["launch"] = launch
    if ws_port is not None:
        # bus coordinates (not an action entry; the plugin reads it
        # to connect to the engine's WS and subscribe to frames)
        routes["__bus__"] = {"ws": ws_port}
    manifest = {
        "$schema": "https://schemas.elgato.com/streamdeck/plugins/"
                   "manifest.json",
        "Name": name,
        "Version": "0.3.0.0",
        "Author": "intentos",
        "Description": description,
        "UUID": plugin_uuid,
        "Icon": "imgs/plugin",
        "Category": category,
        "CategoryIcon": "imgs/plugin",
        "SDKVersion": 2,
        "Software": {"MinimumVersion": "6.5"},
        "OS": [{"Platform": "windows", "MinimumVersion": "10"},
               {"Platform": "mac", "MinimumVersion": "12"}],
        "Nodejs": {"Version": "20", "Debug": ""},
        "CodePath": "bin/plugin.js",
        "Actions": actions,
    }
    files: dict[str, bytes] = dict(imgs)
    files["manifest.json"] = json.dumps(
        manifest, ensure_ascii=False, indent=2).encode("utf-8")
    files["routes.json"] = json.dumps(
        routes, ensure_ascii=False, indent=2).encode("utf-8")
    for rel, data in files.items():
        try:
            (d / rel).write_bytes(data)
        except OSError:
            pass                    # locked by the app: filled in on the next compile, never fatal
    try:
        shutil.copyfile(src_js, d / "bin" / "plugin.js")
    except OSError:
        pass
    return d


def compile_plugins(plugins_root: Path,
                    protos: list[tuple[str, list[str]]],
                    intents: list[str], port: int,
                    src_js: Path,
                    launch: dict | None = None,
                    ws_port: int | None = None,
                    tag: str | None = None
                    ) -> tuple[list[Path], list[str]]:
    """Full compile: one plugin per book + the system intents plugin
    (the engine power key is always resident, so this always
    compiles), and sweeps orphan directories that are no longer a
    book. Returns (written plugin directories, swept directory
    names).

    `tag` = this workspace's identity (deckgen.ws_tag), which every
    compiled UUID hangs off. The sweep is confined to that namespace
    plus the pre-2026-08-25 flat names, so a second workspace's
    engine can no longer delete this one's books or repoint a
    same-named book at its own port (audit 2026-08-25). Callers
    without a tag get the legacy flat namespace — tests only; the
    engine always passes one."""
    written: list[Path] = []
    ns = plugin_ns(tag) if tag else PLUGIN_UUID_BASE
    for pname, members in protos:
        written.append(compile_plugin(
            plugins_root,
            (proto_plugin_uuid(pname, tag) if tag
             else f"{PLUGIN_UUID_BASE}.p{_slug(pname)}"),
            pname, pname,
            f"IntentOS protocol set '{pname}': Start / Approve / "
            f"Interrupt / Shutdown plus member keys — each key is one "
            f"background GET against the local engine's /trigger. "
            f"Compiled by the engine; a changed roster needs a Stream "
            f"Deck restart.",
            proto_entries(pname, members, port), src_js,
            ws_port=ws_port))
    written.append(compile_plugin(
        plugins_root,
        (intents_plugin_uuid(tag) if tag else LEGACY_INTENTS_UUID),
        "IntentOS · Intents", "IntentOS · Intents",
        "IntentOS system set: an Engine power key (tap = start, hold = "
        "shutdown) plus one one-way trigger key per standalone intent — "
        "each key is one background GET against the local engine's "
        "/trigger (the power key can also revive a stopped engine). "
        "Compiled by the engine; a changed roster needs a Stream Deck "
        "restart.",
        engine_entries(port) + intent_entries(intents, port), src_js,
        launch=launch, ws_port=ws_port))
    keep = {p.name for p in written}
    swept: list[str] = []
    try:
        for child in plugins_root.iterdir():
            n = child.name
            if not n.endswith(".sdPlugin") or n in keep:
                continue
            stem = n[:-len(".sdPlugin")]
            # Sweep ONLY what this workspace owns (audit 2026-08-25):
            #   · its own namespace (com.intentos.deck.w<tag>.*), and
            #   · the pre-namespace flat names, which no engine
            #     writes any more and which are unowned orphans.
            # Another workspace's namespace is never touched — that
            # mutual deletion is the defect being fixed here, so the
            # fix must not keep a path to it.
            ours = (stem == ns or stem.startswith(ns + "."))
            legacy = (stem == PLUGIN_UUID_BASE
                      or stem == LEGACY_INTENTS_UUID
                      or (stem.startswith(PLUGIN_UUID_BASE + ".p")
                          and "." not in stem[len(PLUGIN_UUID_BASE) + 1:]))
            if not (ours or legacy):
                continue
            shutil.rmtree(child, ignore_errors=True)
            if not child.exists():
                swept.append(n)
    except OSError:
        pass
    return written, swept
