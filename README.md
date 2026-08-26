<div align="center">

# Intents for Claude Code

**An input method for agency.**

Talk once — it compiles how you use this machine into push-button
assets. From then on, it's a button.

[**Watch the demo**](docs/DEMO.md) · [Setup](docs/SETUP.md) · [User guide](docs/GUIDE.md)

<img src="docs/img/hub.png" alt="The hub: one tab per seat — card stream, input line, terminal drawer" width="860">

</div>

**v0.1.0 (M26 baseline)** — a personal customization layer on top of
the [Claude Code](https://claude.com/claude-code) harness. You
describe a habit once, in conversation; the engine compiles it into a
declared, schema-checked asset with acceptance criteria; a real agent
seat runs it every time you press its key — on a Stream Deck, or in
the browser hub. (Project name: Intents for Claude Code; the engine
keeps **IntentOS** as its internal name — you will see it on panels
and key sets.)

## What's different

#### The unit of interaction is a trigger, not a conversation

On a plain harness, every job starts by typing a prompt and
re-assembling context. Here you say it once; from then on it fires
from a key — a Stream Deck button or the hub's input line. The prompt
disappears from daily use.

<img src="docs/img/deck-keys.png" alt="A booklet's key group dragged onto a Stream Deck page" width="720">

#### Registration is compilation

What you teach it doesn't dissolve with the session — it lands as
**files you own**: an I-E-R declaration in a closed verb grammar,
machine-validated at registration, acceptance criteria fixed at
compile time. Revise, retire, revive — all through the same
conversation, every change behind one human approval card.

<img src="docs/img/booklet.png" alt="A booklet tab with its member steps as pills" width="720">

#### Seats have economics

Minting is expensive once: the sidecar compiles on a strong model.
Running is cheap forever: headless executor seats on a fast model,
one process per order, in parallel. A booklet's resident seat keeps
its own home and memory — the longer it lives with you, the better it
hosts that one workflow. Every run hands back a receipt: duration,
calls, tokens.

<img src="docs/img/step-result.png" alt="A step's result card with its receipt and follow-up buttons" width="720">

#### It rides your harness's constitution

Every seat is a real Claude Code CLI session on your own account, in
the CLI's own permission mode. The allow side belongs to the harness;
the engine owns only the deny floor — and it never presses an
authorization key for you. Loopback only, three gates on the network
face.

<img src="docs/img/terminal.png" alt="The terminal drawer: the seat is a real Claude Code session" width="720">

**[See the full demo →](docs/DEMO.md)** — a recording booklet minted
from one sentence, used from the deck, then revised the same way.

## Install

Windows · Python ≥ 3.12 · [Claude Code CLI](https://claude.com/claude-code)
logged in. Then:

```
git clone https://github.com/MichaelLiu9009/Intents-for-Claude-Code.git
cd Intents-for-Claude-Code
pip install -e .
intentos seed --workspace <dir>
intentos run --workspace <dir> --http 9700 --ws 9701
```

First-run authorization, cost knobs, the cold-start templates, and
uninstall are walked through in **[docs/SETUP.md](docs/SETUP.md)**.

## The model

Two species, three seats, one resident engine:

- **intent** — a stateless one-way order (an E pseudo-code function
  body inside the three-section I-E-R declaration); triggering it
  delivers to the **x·solo executor seat** (headless, one process per
  order, parallel). Optional `procedures` preludes (the engine's
  built-in physical layer, e.g. `screenshot`, mouse-anchored)
  collect materials before delivery. The step grammar is a compact
  closed verb set owned by the engine — see `docs/INTENT_SPEC.md`.
- **protocol** — a stateful multi-round bracket ("booklet"; the test:
  does the next step need to remember the last one). Each booklet gets a
  **resident instance seat** (x·\<booklet\>): the power key opens the
  bracket, member keys deliver steps, pressing power again runs the
  closing ceremony; the seat's home and memory persist across sessions,
  and booklets run in parallel.
- **sidecar** — the resident interaction/maintenance seat: creation,
  revision, surgery, rework, sim QA; the compiler of every asset.

Physical entry points: a **Stream Deck** (registration compiles key
sets: standalone intents into a system group, one sidebar group per
booklet, Status/Step/Task dials with live color bars) and a **browser
hub window** (opened automatically at boot; one tab per seat: card
stream / search-IME input line / terminal drawer). Everything works
without a deck — the panels and the IME are a complete trigger surface.

## Stream Deck

The engine compiles plugins into `%APPDATA%\Elgato\StreamDeck\Plugins\`
(one per booklet plus one for standalone intents); drag keys from the
sidebar. **After the action roster changes (new booklet / retirement /
rename), restart the Stream Deck app once** to see it; URL/route changes
need no restart. The Engine power key carries a baked-in resurrection
command — if the engine is down, tapping it launches the engine
directly (hold = shutdown). Key-face lights are advisory, never
load-bearing — the truth is always the panel's card stream. Key
faces, binding, and the dial bars are walked through in
[docs/GUIDE.md](docs/GUIDE.md#stream-deck).

## Security model (short version)

- **Loopback only, three gates**: HTTP/WS bind 127.0.0.1; the browser
  face is guarded by an Origin allowlist + Host anti-DNS-rebinding +
  a Sec-Fetch-Site gate on the action faces (/trigger, /api/*). The
  full threat model and its rulings live in
  `src/commander/kernel/netguard.py`'s module docstring. Do not forward
  these ports to untrusted networks.
- **Agent identity is minted by the engine**: the MCP token is baked
  into each seat's `.mcp.json` at power-on; self-reported identity does
  not count. The bridge trims the tool face per seat (admin / exec /
  proto) — wrong-face verbs never reach dispatch.
  `MODULE_POLICY.never_allow` is a substring ceiling (state.db,
  .claude, .mcp.json, CLAUDE.md, the utility store — never granted).
- **Procedures are wall-less code**: a built-in engine library
  (`kernel/procs/`) with no agent submission path — intents may only
  reference procedures **by name**; extending the library means changing
  the engine source. A crashed prelude reports to the human and does not
  deliver; the intent is not suspended.
- **The allow side belongs to the harness; the engine owns only the
  deny floor**: every seat is spawned in the CLI's own permission mode
  (`--permission-mode`, default `auto` — a `SEAT_PERMISSION_MODE`
  knob), so day-to-day approvals are the harness's business. What auto
  mode doesn't cover raises a card: **Allow once** is one-shot;
  **Always allow** banks the rule twice — the CLI keeps its own copy
  in that seat's `settings.local.json` (the same path its native
  "don't ask again" uses), and the engine keeps a cross-seat copy in
  `<workspace>/config.json` (`PERM_ALLOW`). Both are human-editable;
  anything matching `never_allow` in the module policy is refused at
  a single choke point no matter who clicked. The engine never
  presses an authorization key and never widens its own deny floor.

## Documentation map

| Document | Purpose |
|---|---|
| `docs/DEMO.md` | **The full walkthrough on video**: a booklet minted from one sentence, used from the deck, revised the same way |
| `docs/SETUP.md` | **Install & first run**: requirements, first-run authorization, cost knobs, cold-start templates, uninstall |
| `docs/GUIDE.md` | **The user guide — start here**: creating intents & booklets with the sidecar, triggering, Stream Deck binding, the folder map, tasks, troubleshooting |
| `docs/CONFIG.md` | **Every knob**: CLI flags / models & effort / limits & clocks, and how to change them |
| `docs/INTENT_SPEC.md` | The intent & protocol contract (I-E-R, the E pseudo-code grammar, lifecycle) |
| `docs/FEATURES.md` | The component catalog for agents and contributors (responsibility / behavior / implementation / grep anchors) |

Source comments cite `CASELAW <n>` and `docs/M*.md` — those are the
development repo's internal engineering ledgers (rulings and design
history) and don't ship with the release; the citations are kept as
provenance anchors.

## License

MIT (see `LICENSE`). This project is not affiliated with Anthropic;
"Claude Code" is Anthropic's product name, and this is a third-party
tool that runs on top of it.
