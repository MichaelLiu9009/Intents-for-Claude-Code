# Security model

The stance in one line: **the engine compiles and delivers; a human
approves; the harness executes.** Human approval is the only gate that
mints or changes an asset, and the engine never presses an
authorization key for itself.

## Loopback only, three gates

HTTP/WS bind 127.0.0.1; the browser face is guarded by an Origin
allowlist + Host anti-DNS-rebinding + a Sec-Fetch-Site gate on the
action faces (`/trigger`, `/api/*`). The full threat model and its
rulings live in `src/commander/kernel/netguard.py`'s module
docstring. Do not forward these ports to untrusted networks.

## Agent identity is minted by the engine

The MCP token is baked into each seat's `.mcp.json` at power-on;
self-reported identity does not count. The bridge trims the tool face
per seat (admin / exec / proto) — wrong-face verbs never reach
dispatch. `MODULE_POLICY.never_allow` is a substring ceiling
(state.db, .claude, .mcp.json, CLAUDE.md, the utility store — never
granted).

## Procedures are wall-less code

A built-in engine library (`kernel/procs/`) with no agent submission
path — intents may only reference procedures **by name**; extending
the library means changing the engine source. A crashed prelude
reports to the human and does not deliver; the intent is not
suspended.

## The allow side belongs to the harness; the engine owns only the deny floor

Every seat is spawned in the CLI's own permission mode
(`--permission-mode`, default `auto` — a `SEAT_PERMISSION_MODE`
knob), so day-to-day approvals are the harness's business. What auto
mode doesn't cover raises a card: **Allow once** is one-shot;
**Always allow** banks the rule twice — the CLI keeps its own copy in
that seat's `settings.local.json` (the same path its native "don't
ask again" uses), and the engine keeps a cross-seat copy in
`<workspace>/config.json` (`PERM_ALLOW`). Both are human-editable;
anything matching `never_allow` in the module policy is refused at a
single choke point no matter who clicked. The engine never presses an
authorization key and never widens its own deny floor.
