<div align="center">

# Intents for Claude Code

**An input method for agency.**

Talk once — it compiles how you use this machine into push-button
assets. From then on, it's a button.

[**Watch the demo**](docs/DEMO.md) · [**Setup**](docs/SETUP.md) · [**User guide**](docs/GUIDE.md)

</div>

https://github.com/user-attachments/assets/8c613238-772f-41e1-84ef-ea72fb4f7dcc



<p align="center"><em>One tap on a real key. The engine wakes, opens its hub, and reports in.</em></p>

## Press a key. Get your workflow.

Each of these was described once, in conversation. Now it's a button.

https://github.com/user-attachments/assets/40f5e18e-5c60-4a01-b463-6cc76424341c



<p align="center"><em>An <b>intent</b> — a stateless order. One press: a live market
dashboard, fetched and assembled by a headless executor seat, receipt included.</em></p>

https://github.com/user-attachments/assets/a161dad7-0ffc-419d-92d0-1bdd7aa95391



<p align="center"><em>A <b>booklet</b> — a stateful session with its own resident seat.
Open the bracket, run member steps from keys; it remembers, across sessions.</em></p>

## Why it's different

- **The unit of interaction is a trigger, not a conversation.** On a
  plain harness every job starts by typing a prompt and re-assembling
  context. Here you say it once; from then on it fires from a key.
  The prompt disappears from daily use.
- **Registration is compilation.** What you teach doesn't dissolve
  with the session — it lands as **files you own**: a declared,
  schema-checked asset with acceptance criteria fixed at compile
  time. Revise, retire, revive through the same conversation, every
  change behind one human approval card.
- **Seats have economics.** Minting is expensive once: the sidecar
  compiles on a strong model. Running is cheap forever: headless
  seats on a fast model, one process per order, in parallel. Every
  run hands back a receipt — duration, calls, tokens.
- **It rides your harness's constitution.** Every seat is a real
  [Claude Code](https://claude.com/claude-code) CLI session on your
  own account, in the CLI's own permission mode. The engine owns only
  the deny floor — and it never presses an authorization key for you.

<div align="center">

> *"An operating system never asks a process to police its own memory
> access; it draws an address space around it, from outside. Every
> mature permission system ends up governing shapes, not clicks."*
>
> — **[Control from the outside](docs/MEMO.md)**, the design memo

</div>

## The default keyset

Control ships as physical keys with a fixed face grammar: **graphics
are system keys, text faces are your assets, color is state.** Every
workspace compiles two kinds of key groups:

**IntentOS — the system group** (one per workspace)

| Face | Key | What it does |
|---|---|---|
| power glyph · status dot | **Engine** | tap starts the engine — even a dead one, the launch command is baked into the key; hold shuts it down |
| green check | **Solo · Approve** | answers the newest executor card — always *allow once*; the physical key can never mint a permanent grant |
| red square | **Solo · Cancel** | force-stops the newest running order |
| orange text faces | *your intents* | one key per registered intent — press to fire it |
| dials: **Status** · **Task** | | read-only touch-strip bars: engine up/down, and the newest in-flight order with its state color |

**Per booklet — one group each** (compiled the moment you approve it)

| Face | Key | What it does |
|---|---|---|
| power glyph | **Start / Shutdown** | one toggle: a closed booklet opens, an open one wraps up; pressing again mid-wrap forces the close |
| green check | **Approve** | that booklet's newest waiting card |
| yellow square | **Interrupt** | cuts the seat's current turn; the order itself survives |
| dark text faces | *member steps* | one key per member — press to run that step |
| dials: **Status** · **Step** | | open/closed + seat liveness · the step now running |

<p align="center"><img src="docs/img/deck-keys.png" width="560" alt="The two default key groups in the Stream Deck sidebar: IntentOS system keys and a booklet's group"></p>
<p align="center"><em>Both groups as they appear in the Stream Deck app — drag onto any page.
Binding flow and the full face grammar: <a href="docs/GUIDE.md">the user guide</a>.</em></p>

## Install

**v1.0.0** — Windows · Python ≥ 3.12 ·
[Claude Code CLI](https://claude.com/claude-code) logged in. Then:

```
git clone https://github.com/MichaelLiu9009/Intents-for-Claude-Code.git
cd Intents-for-Claude-Code
pip install -e .
intentos seed --workspace <dir>
intentos run --workspace <dir> --http 9700 --ws 9701
```

First-run authorization, cost knobs, the cold-start templates, and
uninstall are walked through in **[docs/SETUP.md](docs/SETUP.md)**.

## Learn more

| Document | Purpose |
|---|---|
| [docs/DEMO.md](docs/DEMO.md) | **The full walkthrough on video**: a booklet minted from one sentence, used from the deck, revised the same way |
| [docs/MEMO.md](docs/MEMO.md) | **The design memo**: control from the outside — agents governed by boundary shapes, not approval clicks; why this project exists |
| [docs/SETUP.md](docs/SETUP.md) | **Install & first run**: requirements, first-run authorization, cost knobs, cold-start templates, uninstall |
| [docs/GUIDE.md](docs/GUIDE.md) | **The user guide — start here**: creating intents & booklets, triggering, Stream Deck binding, the folder map, troubleshooting |
| [docs/SECURITY.md](docs/SECURITY.md) | **The security model**: loopback-only network face, minted agent identity, the deny floor |
| [docs/CONFIG.md](docs/CONFIG.md) | **Every knob**: CLI flags / models & effort / limits & clocks |
| [docs/INTENT_SPEC.md](docs/INTENT_SPEC.md) | The intent & protocol contract (I-E-R, the E pseudo-code grammar, lifecycle) |
| [docs/FEATURES.md](docs/FEATURES.md) | The component catalog for agents and contributors |

## License

MIT (see `LICENSE`). This project is not affiliated with Anthropic;
"Claude Code" is Anthropic's product name, and this is a third-party
tool that runs on top of it.
