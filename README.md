
# Intents for Claude Code

**An input method for agency.**
Talk once — it compiles how you use this machine into push-button
assets. From then on, it's a button.

**v0.1.0 (M26 baseline)** — a personal customization layer on top of
the [Claude Code](https://claude.com/claude-code) harness. You
describe a habit once, in conversation; the engine compiles it into a
declared, schema-checked asset with acceptance criteria; a real agent
seat runs it every time you press its key — on a Stream Deck, or in
the browser hub. You never type prompts to make things run. (Project
name: Intents for Claude Code; the engine keeps **IntentOS** as its
internal name — you will see it on panels and key sets.)

## Demo — a sentence becomes a button

One real session, start to finish: minting a screen-recording booklet
from a one-sentence wish, then running it from the deck.

### 1 · Create your protocol

Tell the sidecar, in plain language, what you want as buttons. That is
the entire input — no config files, no prompt engineering.

<!-- STEP 1 VIDEO: https://github.com/user-attachments/assets/2ea31d76-5f0b-43f7-aded-518d4e0e7151 --> 


### 2 · It grounds your customization against your local environment

The agent probes the machine itself — screens, devices, installed
tools, your folder layout — and asks only the real forks, each with
its tradeoffs spelled out.

<!-- STEP 2 VIDEO: [drag readme-step2.mp4 here](https://github.com/user-attachments/assets/1f8f7177-0377-4fb9-a12e-d297311e2990) --> 

### 3 · It implements a tested solution

Declarations, member steps, and tool scripts get written — and
live-fired against this machine before anything is submitted.

<!-- STEP 3 VIDEO: drag readme-step3.mp4 here --> https://github.com/user-attachments/assets/6c883e45-530c-4e8c-90ef-769b7d8c65c2

### 4 · Register — the keyset compiles into the Elgato app

Registration raises one approval card, and that click is yours by
design. On approve the whole booklet goes live and its key set
auto-compiles into the Stream Deck app, ready to drag onto your deck.

<!-- STEP 4 VIDEO: drag readme-step4.mp4 here --> https://github.com/user-attachments/assets/89cff055-dd37-4d2c-baa2-6ca746b69135

### 5 · Start using it

https://github.com/user-attachments/assets/7a970b55-3b5a-45a7-b1c5-10ac8f6a728e

### 6 · Lifecycle control from the Stream Deck

Open the bracket, run steps, wrap up and settle the books — the whole
lifecycle rides on physical keys.

https://github.com/user-attachments/assets/478ed846-4200-461d-af6a-6f2bb6efc04d

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
