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
workspace compiles two kinds of key groups.

**IntentOS — the system group** (one per workspace)

| | |
|---|---|
| <img src="docs/img/keys/engine.png" width="56" alt="power glyph with status dot"> | **Engine** — tap starts the engine, even a dead one: the launch command is baked into the key. Hold shuts it down. The dot is live engine state. |
| <img src="docs/img/keys/approve.png" width="56" alt="green check"> | **Solo · Approve** — answers the newest executor card. Always *allow once*: the physical key can never mint a permanent grant. |
| <img src="docs/img/keys/cancel.png" width="56" alt="red square"> | **Solo · Cancel** — force-stops the newest running order. |
| <img src="docs/img/keys/intent.png" width="56" alt="orange text key"> | **Your intents** — one orange text key per registered intent. Press to fire it. |
| <img src="docs/img/keys/dial-engine.png" width="110" alt="engine status dial"> <img src="docs/img/keys/dial-task.png" width="110" alt="task dial"> | **Status · Task** — read-only touch-strip dials: engine up/down, and the newest in-flight order with its state color. |

**Per booklet — one group each** (compiled the moment you approve it)

| | |
|---|---|
| <img src="docs/img/keys/power.png" width="56" alt="power glyph"> | **Start / Shutdown** — one toggle: a closed booklet opens, an open one wraps up. Pressing again mid-wrap forces the close. |
| <img src="docs/img/keys/approve.png" width="56" alt="green check"> | **Approve** — that booklet's newest waiting card. |
| <img src="docs/img/keys/interrupt.png" width="56" alt="yellow square"> | **Interrupt** — cuts the seat's current turn; the order itself survives. |
| <img src="docs/img/keys/member.png" width="56" alt="dark text key"> | **Member steps** — one dark text key per member. Press to run that step. |
| <img src="docs/img/keys/dial-status.png" width="110" alt="status dial"> <img src="docs/img/keys/dial-step.png" width="110" alt="step dial"> | **Status · Step** — open/closed plus seat liveness · the step now running. |

<p align="center"><em>Binding is drag-and-drop; the full flow and the face grammar are in <a href="docs/GUIDE.md">the user guide</a>.</em></p>

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
