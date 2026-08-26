# Setup

Install, first run, cost knobs, cold-start templates, uninstall.
(Day-to-day usage lives in [GUIDE.md](GUIDE.md); every knob in
[CONFIG.md](CONFIG.md).)

## Requirements

- **Windows** (PTY seats use pywinpty; the `screenshot` prelude
  uses PowerShell)
- **Python ≥ 3.12**
- **[Claude Code CLI](https://claude.com/claude-code) on PATH, logged
  in** — run `claude` once in a terminal to finish the official auth.
  Every seat the engine opens is a real CLI session on your own
  subscription / API account.
- Stream Deck app and Edge are optional (no deck → use the panels/IME;
  no Edge → falls back to your default browser)

## Install and first run

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
(e.g. `{ "SIDECAR_MODEL": "sonnet" }`): see [CONFIG.md](CONFIG.md).

## Cold-start templates

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
