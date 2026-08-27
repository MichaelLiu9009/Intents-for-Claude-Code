<div align="center">

# Intents for Claude Code

**An input method for agency.**

Talk once — it compiles how you use this machine into push-button
assets. From then on, it's a button.

[**Watch the demo**](docs/DEMO.md) · [**Setup**](docs/SETUP.md) · [**User guide**](docs/GUIDE.md)

</div>

> ⬇ replace this line with the uploaded URL of `readme-hero-engine-start.mp4`

<p align="center"><em>One tap on a real key. The engine wakes, opens its hub, and reports in.</em></p>

## Press a key. Get your workflow.

Each of these was described once, in conversation. Now it's a button.

> ⬇ replace this line with the uploaded URL of `readme-hero-dashboard.mp4`

<p align="center"><em>An <b>intent</b> — a stateless order. One press: a live market
dashboard, fetched and assembled by a headless executor seat, receipt included.</em></p>

> ⬇ replace this line with the uploaded URL of `readme-hero-guitar.mp4`

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

## Install

**v0.1.0 (M26 baseline)** — Windows · Python ≥ 3.12 ·
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
