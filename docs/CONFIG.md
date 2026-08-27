# CONFIG — every engine knob (0.1.0)

> **How to change things (three layers, later wins)**:
> ① `src/commander/defaults.py` — the constant source; every constant
> carries an inline comment, and **the comment is the authority**
> (this document is only the map).
> ② **`<workspace>/config.json`** — the per-workspace knob file:
> a JSON object whose keys are the ALL-CAPS **scalar** constants from
> defaults.py (int / float / str / bool; strings only when single-line
> and ≤80 chars — templates and tables are behavior design, not knobs,
> and stay source-only, see §9). **Unknown keys and type mismatches
> refuse to boot, loudly** — silent config drift is the worst kind of
> bug. Applied overrides are printed at boot and journaled.
> ③ CLI flags (`--http/--ws/--model`) beat both.
>
> ```json
> { "SIDECAR_MODEL": "sonnet", "TASK_TOKEN_ALERT": 80000 }
> ```
>
> Deck face color/glyph constants live separately in
> `src/commander/kernel/deckgen.py` (`COLOR_*` / `GLYPHS`; the semantic
> status palette `ST_COLORS` is in defaults).

## 1. CLI flags

| Command | Flag | Default | Governs |
|---|---|---|---|
| `run` | `--workspace` | `.` | workspace root (first run mints instances/ toolkit/ utility/ runtime/ state.db) |
| `run` | `--http` / `--ws` | 9700 / 9701 | HTTP panel port / WS channel port |
| `run` | `--no-host` | off | infrastructure only, no host CLI spawn (tests) |
| `run` | `--model` | `sonnet` | **general host seat** model (x·\<booklet\>); the sidecar ignores this flag (see §3) |
| `stop` | `--ws` | 9701 | graceful stop via the channel |
| `seed` | `--workspace` | `.` | seed the built-in templates: "timecheck" intent + "translator" booklet (skips the creation chain; cold-start / demo / format exemplars) |

## 2. Environment variables

| Name | Set by | Governs |
|---|---|---|
| `INTENTOS_TOKEN` | **the engine** (baked into each seat's `.mcp.json` at power-on) | MCP caller identification (per-instance accounting). **Not a security boundary** (ruling in `kernel/netguard.py`'s docstring); users never set it by hand |

## 3. Seats and models (defaults.py)

| Constant | Value | Governs |
|---|---|---|
| `OS_MODULE` | `sidecar` | name of the resident system seat |
| `SIDECAR_MODEL` / `SIDECAR_EFFORT` | `opus` / `high` | the **creation/compile seat**, pinned — asset quality lives here; not governed by `--model`; **this is the most expensive knob** (minting one intent is dozens of round trips) |
| `HOST_MODEL` / `HOST_EFFORT` / `HOST_THINKING` | `sonnet` / `medium` / 10_000 | general host seats (x·\<booklet\>); `--model` can override the model |
| `XSOLO_MODEL` / `XSOLO_EFFORT` / `XSOLO_THINKING` | `sonnet` / `low` / 4096 | the x·solo executor seat (standalone intents), pinned cheap |
| `XSOLO_CLI_TOOLS` | Bash,Read,Write,Edit,Glob,Grep | the executor's CLI tool face |
| `XSOLO_MCP_TOOLS` | task_done, ask_user_through_os, perm_gate | the executor's three MCP verbs (exec face) |
| `XPERM_TOOL` | `mcp__intentOS__perm_gate` | `--permission-prompt-tool` target (permission asks go to the card stream) |
| `SEAT_PERMISSION_MODE` | `auto` | the CLI permission mode every seat is spawned with (`--permission-mode` flag, written by the engine); empty string = no flag (harness default). The allow side of daily approvals lives here |
| `PERM_ALLOW` | `[]` | the **always-allow ledger**: rules granted via "Always allow" on permission cards land here (in `config.json`), and the engine materializes them into every seat's allow list. Human-editable — remove a line to revoke |
| `MODULE_POLICY` | table | per-seat allow/deny policy; `never_allow` is a substring ceiling (state.db, .claude, .mcp.json, CLAUDE.md, the utility store — never granted) |
| `XPROTO_PREFIX` / `XSOLO_SEAT` | `x·` / `x·solo` | executor seat naming convention (protects the protocol namespace) |

## 4. Intent / protocol grammar gates

| Constant | Value | Governs |
|---|---|---|
| `INTENT_STEPS_MAX` | 1200 | E pseudo-code function body length gate (compression is quality; ×2 with the English word list) |
| `INTENT_INSTR_MAX` | 800 | length gate for the I and R sections of I-E-R (each capped separately) |
| `INTENT_SCENARIO_MAX` | 20 | scenario word: one word, no whitespace/punctuation |
| `E_VERBS` / `E_COND_MAX` / `E_GRAMMAR` | table / 80 / grammar string | E pseudo-code verb allowlist (with per-verb content budgets) / condition length cap / line grammar |
| `PROTO_MIN_SEATS` / `PROTO_MAX_SEATS` | 3 / 10 | booklet member-count law (min/max) |
| `PROTO_SKILL_MAX` | 20_000 | booklet skill body cap (the human approves the full text) |
| `PROTO_HOOK_MAX` | 800 | prep / wrapup (·open/·wrap content) caps, each |
| `PROTO_RESERVED_MEMBERS` | ·open ·wrap 开启 结束 收场 prep wrapup | reserved names — opening/closing are system steps; user members may not occupy them |
| `PROTO_TOTAL_MAX` | 50 | library-wide booklet cap |
| `MAX_HOME_INTENTS` | 200 | registered-intent cap per home |

## 5. Retrieval / IME / catalog

| Constant | Value | Governs |
|---|---|---|
| `SEARCH_RECALL_TOP` / `SEARCH_MIN_SIM` | 25 / 0.10 | recall pool and minimum similarity (mechanical v1) |
| `SEARCH_TOP_INTENTS` / `SEARCH_TOP_PROTOS` | 5 / 1 | per-column result counts |
| `INTENT_SEARCH_LIMIT` | 20 | intent_search cap |
| `CATALOG_TOP` | 50 | catalog flat usage-top quota |
| `INTENT_GET_MAX` | 20 | intent_get batch cap |
| `SCORE_TRIGGER` / `SCORE_GET` | 1.0 / 0.3 | usage scoring (trigger / read), orders the hot index |
| `CONTAINER_CAP` | 200 | host-session container LRU cap (cleared on session turnover) |

## 6. Lifecycle clocks (seconds unless noted)

| Constant | Value | Governs |
|---|---|---|
| `SPAWN_SETTLE` / `READY_BYTES` | 8.0 / 512 bytes | seat readiness probe: conservative floor + TUI-mounted evidence threshold |
| `TRUST_FLIP_SETTLE_S` / `_QUIET_S` / `_CAP_S` | 3.0 / 1.5 / 15.0 | trust-wizard scene-change detection (only re-arm after the wizard was actually seen) |
| `PASTE_BEAT` | 0.15 | one beat between injected text and its carriage return |
| `INJECT_ACK_S` / `INJECT_ACK_MAX_S` / `INJECT_BUSY_QUIET_S` | 20 / 180 / 6 | injection watchdog: ack window / busy hard cap / quiet-means-idle threshold (thinking markers at the screen tail defer indefinitely) |
| `IDLE_STALL_S` / `STALL_TAIL_CHARS` | 20 / 800 chars | running yet PTY-silent → stall card; screen-tail length the card carries |
| `STEP_QUIET_S` | 90 | member-step output-silence fallback (display only, never load-bearing) |
| `XGATE_WAIT_S` / `PERM_ASK_WAIT_S` / `PERM_HOOK_TIMEOUT_S` / `PERM_NOTIF_GRACE_S` | 300 / 290 / 300 / 10 | permission/form card waiting clocks (a parked gate is never reaped; re-render on gate close) |
| `TASK_TIMEOUT_S` | 15 min | a task with no settlement times out (bracket tasks are exempt — brackets eat no clock) |
| `MAX_NODE_VISITS` | 4 | per-node revisit cap within a chain (surgery/rework loop guard) |
| `PROTO_EXIT_GRACE_S` | 6.0 | booklet close: ESC + /exit grace, then tree-kill fallback |
| `PROTO_WRAP_GRACE_S` | 45.0 | ·wrap closing ceremony: wait for step_done before settling (press again = force) |
| `PROC_TIMEOUT_S` / `PROC_SAY_MAX` | 30 / 4000 chars | procedure preludes: per-step hard timeout (tree-kill) / per-item material size gate |
| `HOOK_TIMEOUT_S` | 5 | timeout for hooks in settings.json |

## 7. Audit and alerts

| Constant | Value | Governs |
|---|---|---|
| `TASK_TOKEN_ALERT` | 50_000 | per-task token alert line |

## 8. Priorities (queue tiers, lower = first)

`PRIORITY_EXEC` (0, intent delivery / protocol brackets) →
`PRIORITY_ALERT` (1, creation / sim) → `PRIORITY_ERROR`
(2, surgery) → `PRIORITY_INTERNAL` (3, engine maintenance, not
cancellable).

## 9. Scripts and templates (not numeric knobs)

The lower half of defaults.py is the per-seat scripts: CLAUDE.md /
package / surgery / retry / skill texts (`HOME_CLAUDE_MD`,
`PROTOCOL_PACKAGE_MD`, `XSOLO_*_MD`, `SKILL_*_MD` …). These are
**behavior design, not configuration** — every verb in them is an
affordance that shifts the executor's behavioral prior. Before editing,
read the inline comments and the neighboring tests (tests/ guards the
load-bearing sentences).

## 10. Ports and network

One HTTP port (panels + the `/trigger` action face) and one WS port
(the channel) — `HTTP_PORT` 9700 / `WS_PORT` 9701, overridable with
`--http/--ws`. Loopback only, with three browser-face gates (Origin /
Host / Sec-Fetch-Site on action faces) — the full threat model lives in
`src/commander/kernel/netguard.py`'s module docstring.
**Do not forward these two ports to untrusted networks.**
