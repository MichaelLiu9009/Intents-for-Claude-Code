# Intents for Claude Code

**An input method for agency.**
Talk once — it compiles how you use this machine into push-button
assets. From then on, it's a button.

<!-- 
https://github.com/user-attachments/assets/7a970b55-3b5a-45a7-b1c5-10ac8f6a728e


https://github.com/user-attachments/assets/478ed846-4200-461d-af6a-6f2bb6efc04d

 VIDEO: in the GitHub web editor, drag
     readme-embed-usage.mp4 here (it uploads as a playable inline
     embed; readme-embed-deck.mp4 is the alternate). -->

**v0.1.0 (M26 baseline)** — a personal customization layer on top of
the [Claude Code](https://claude.com/claude-code) harness. You
describe a habit once, in conversation; the engine compiles it into a
declared, schema-checked asset with acceptance criteria; a real agent
seat runs it every time you press its key — on a Stream Deck, or in
the browser hub. You never type prompts to make things run. (Project
name: Intents for Claude Code; the engine keeps **IntentOS** as its
internal name — you will see it on panels and key sets.)

## See it work

<!-- FULL WALKTHROUGH: YouTube link goes here (~8 min). Chapters =
     the six section clips:
     1 create your protocol
     2 the agent grounds your customization against your local environment
     3 implement the tested solution
     4 register the package — the keyset auto-compiles into the Elgato app
     5 start using your protocol
     6 lifecycle control from the Stream Deck -->

![The hub's engine tab: an intent pinned on the input line, Enter fires](docs/img/hub.png)

## Getting started

Requirements:

- **Windows** (PTY seats use pywinpty; the `screenshot` prelude
  uses PowerShell)
- **Python ≥ 3.12**
- **[Claude Code CLI](https://claude.com/claude-code) on PATH, logged
  in** — run `claude` once in a terminal to finish the official auth.
  Every seat the engine opens is a real CLI session on your own
  subscription / API account.
- Stream Deck app and Edge are optional (no deck → use the panels/IME;
  no Edge → falls back to your default browser)

```
git clone https://github.com/MichaelLiu9009/Intents-for-Claude-Code.git
cd Intents-for-Claude-Code
pip install -e .
intentos seed --workspace <dir>
intentos run --workspace <dir> --http 9700 --ws 9701
```

(`seed` is optional but goes **before** `run` — it writes the registry
directly, and a running engine compiles its key sets at boot, so
templates seeded afterwards stay invisible until you restart the
engine once. Details under "Cold-start templates" below.)

(`intentos` is the installed console command; `python -m commander`
is the equivalent module form — the import package keeps its
engineering-lineage name.)

The workspace directory is minted on first run (instances/ toolkit/
utility/ runtime/ state.db), and the engine opens the hub panel
automatically (manual entry: `http://127.0.0.1:9700/hub`). Stop with
`intentos stop --ws 9701`.

**First-run authorization (read this):** seats are real Claude Code CLI
sessions. The first time a seat opens in a new workspace, the CLI runs
its own trust confirmation (the trust wizard) and login check — answer
it once in that seat's **Terminal drawer** in the hub; it will not ask
again. This is the harness's own security design: **the engine will
not, and must not, press any authorization key for you.** For the same
reason, the final Approve card when registering a new intent or protocol
always waits for you personally.

**Cost knobs:** the sidecar (creation/compile seat) defaults to `opus`
at high effort — minting one intent is a real expense of a few dozen
round trips. Executor seats (x·solo) default to `sonnet` at low effort,
so day-to-day triggering is cheap. Every model and effort level is
adjustable — drop overrides into `<workspace>/config.json`
(e.g. `{ "SIDECAR_MODEL": "sonnet" }`): see `docs/CONFIG.md`.

Cold-start templates and your first intent:

```
intentos seed --workspace <dir>
```

Seed lands the templates **provisioned** (engine property — they skip
the human approval chain) by writing the workspace registry directly.
The engine only re-compiles its roster on its own registration events,
so if it was already running when you seeded, restart it once
(`intentos stop --ws 9701` → `intentos run …`) and the two keys
appear.

This seeds the two built-in templates — **timecheck** (a standalone
intent: fetch the time, report it in chat) and **translator** (an
interactive booklet: a `language` step picks the target language on a
card; each press of `translate` has the engine screenshot the monitor
under your mouse and the booklet seat translate everything on it,
answering on a card). They are also **format exemplars**: open their
folders under the sidecar home and you see exactly what a registration
must look like — the whole creation thesis in two files. An intent's E
section is a *pseudo-code function body* in a schema-based language:
a **closed verb dictionary** (each verb with a character budget) plus
the **declaration schema** bound at registration — the format is what
makes an intent cheap to reproduce, not prose.

Your own first one: tell the sidecar (the Terminal in the hub's first
tab) about one thing you want push-button — it opens the ticket, writes
the files, registers → the panel raises a registration card → you
Approve → the key face compiles and goes live.

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

## Uninstall

```
intentos stop --ws 9701
pip uninstall intents-for-claude-code
```

Then delete two things: `%APPDATA%\Elgato\StreamDeck\Plugins\
com.intentos.deck.*` (quit the Stream Deck app from its tray icon
first — the plugin folders are locked while it runs) and the workspace
directory (that one is your asset — keep it if you want). The deck
plugin folder above is the only thing the engine ever writes outside
the workspace — it exists because the Stream Deck app only loads
plugins from its own directory.

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
