# Intents for Claude Code · component catalog

> Baseline: M26 (1.0.0; engine-internal name IntentOS). ~13.5k lines
> of Python src (plus ~1.4k first-party js/html). **Usage**: every component is listed as responsibility /
> behavior / implementation / anchors — the anchors are grep targets
> (symbol names / spec names / journal kinds / frame types /
> constants). This catalog states abstractions, not line numbers:
> line numbers rot, symbols don't. Where this document and the code
> disagree, the code wins. Source comments cite `CASELAW <n>` and
> `docs/M*.md` — those are the development repo's internal engineering
> ledgers (rulings and design history) and don't ship with the
> release; the citations are kept as provenance anchors.

**One-paragraph architecture**: a single resident engine owns the truth
layer (SQLite) and three host kinds (CLI PTY / headless / none). It
compiles explicit human triggers into task chains; agents report back
exclusively over an MCP bridge with per-seat tool faces; humans decide
exclusively through the card stream, panels, and Stream Deck keys.
Species: intent (straight-line order) · procedure (built-in mechanical
prelude) · protocol (multi-round bracket booklet). Resident seats:
sidecar (admin/compile) · x·solo (parallel executor) · one x·\<booklet\>
instance per protocol. Every seat is spawned in the harness's own
permission mode (`SEAT_PERMISSION_MODE`, default `auto`); the engine
owns only the deny floor plus the human-editable `PERM_ALLOW` ledger.

New here? The human manual is `docs/GUIDE.md` — this catalog is the
technical reference (written for agents and contributors).

---

## 1. defaults.py — single source of names and texts

- **Responsibility**: the only home of constants, teaching texts
  (CLAUDE.md / skill / package templates), and refusal wording. One
  origin, many readers; no policy logic.
- **Knob file**: `<workspace>/config.json` overrides the ALL-CAPS
  scalar constants at boot (`kernel/config.py` — unknown keys refuse
  to boot loudly; explicit CLI flags win; applied overrides printed
  and journaled as `lifecycle/config`). It also holds the
  `PERM_ALLOW` always-allow ledger (str-list knob; `config.grant` is
  the engine's only write path into the file).
- **Anchors**: `OS_MODULE` `MODULE_POLICY`
  (`never_allow` substring ceiling) · `SEAT_PERMISSION_MODE`
  `PERM_ALLOW` · `SIDECAR_MODEL/SIDECAR_EFFORT`
  (compile seat pinned opus/high) · `XSOLO_SEAT/XSOLO_MODEL/
  XSOLO_EFFORT/XSOLO_THINKING` · `PROTO_MIN_SEATS/PROTO_MAX_SEATS`
  `PROTO_HOOK_MAX` `PROTO_RESERVED_MEMBERS` `PROTO_WRAP_GRACE_S`
  `PROTO_EXIT_GRACE_S` · `E_VERBS/E_GRAMMAR/E_COND_MAX` ·
  `PRIORITY_EXEC/ALERT/ERROR/INTERNAL` · `ST_COLORS` (status palette)
  · templates: `HOME_CLAUDE_MD` `XSOLO_CLAUDE_MD` `XSOLO_PACKAGE_MD`
  `XSOLO_SURGERY_MD` `PROTOCOL_PACKAGE_MD` (·open prep slot)
  `PROTO_HOST_CLAUDE_MD` `RETRY_FULFILL_MD`
  `DEBUG_MD` `WS_GUIDE_MD` `WS_REGISTER_MD` ·
  skills: `SKILL_TASK_DELIVERY_MD` `SKILL_INTENT_CREATION_MD`.

## 2. kernel/store.py — truth layer (SQLite, single writer)

- **Responsibility**: fourteen tables of truth (intents, intent_steps,
  intent_tools, chain_specs, chain_spec_steps, tasks, records,
  chain_flags, events, boundary_compiled, procedures, protocols, plus
  the fossil caveats and bindings tables); additive schema migrations;
  delivery-chain compilation.
- **Implementation**: `SCHEMA_VERSION` (19) with `_DDL_Vn` stepwise
  additive migrations (a fresh DB builds current then stamps); all
  writes behind one lock; `compile_delivery` is the delivery single
  source (procedure rings + a deliver tail when `fires=1`).
- **Anchors**: `SCHEMA_VERSION` `_DDL_V19` (protocols prep/wrapup
  columns) · `compile_delivery` · `intent_create/intent_revise` ·
  `touch` (scoring) · `intent_catalog` `intent_search` ·
  `chain_start/queue_for/queue_ceiling` · `proto_stage(prep=,wrapup=)`
  `proto_approve` `proto_get/protos` · flow names: `FLOW_QUAL_NEW`
  `FLOW_WS_QUAL` `FLOW_QUAL_REWORK` `FLOW_RETIRE`.

## 3. kernel/vector.py — mechanical embedder

- **Responsibility**: zero-dependency, zero-model text vectors for
  first-cut retrieval; a slot left for a future embed API.
- **Implementation**: 1+2-gram bag, L2 norm, cosine; CJK needs no
  tokenizer. Vectors are derived, never persisted — engine caches by
  `(name, rev)`, edits self-invalidate.
- **Anchors**: `embed` `sim` `_grams`; engine-side `_vec_of`.

## 4. kernel/provision.py — home minting

- **Responsibility**: the only minter of seat homes. Engine-owned
  files are rewritten on every mint; the human's things (memory,
  module scenarios) are never overwritten.
- **Behavior**: sidecar home (CLAUDE.md + skills + settings render +
  `.mcp.json` with minted token), x·solo home (allow = engine pipe
  floor + `PERM_ALLOW` ledger, mirrored into `--allowedTools`), and
  **per-protocol instance homes** (the household system: a booklet
  seat's workspace persists). Permission mode is not pinned in
  settings — it rides the spawn flag (`SEAT_PERMISSION_MODE`).
- **Anchors**: `instance_home` `provision_home` `provision_solo_home`
  `provision_proto_home` `solo_allow_rules`; `MEMORY_DIRNAME` (seat-
  private, never read or written by the engine).

## 5. kernel/journal.py — append-only ledger

- **Responsibility**: one line per event, persisted per seat/session;
  the panels' history is derived, the ledger is the record.
- **Anchors**: `Journal` `row`; high-frequency kinds: `lifecycle`
  `chain` `intent` `card` `perm` `xgate` `surgery` `protocol` `deck`
  `guard` `engine` `alert` `reconcile` `hook`.

## 6. kernel/channel.py — WS state face (default port 9701)

- **Responsibility**: the UI's only two-way channel: state frames go
  down, human verbs come up; also tracks per-seat panel windows.
- **Behavior**: `hello` full resync (idempotent; carries the seat when
  a booklet window reports in); CLI stream subscription (`cli_sub`);
  verb dispatch (intent / approve / cancel / retry / validate /
  card_answer / chat / cli_in / chains resync / stop); flow-window
  bookkeeping (`flow_open` / `flow_close` frames,
  one window per booklet, the hub adds/removes tabs on them);
  `cli_size` reports panel terminal geometry for a real ConPTY resize.
- **Anchors**: `Channel` `broadcast` `push_cli` `close_flow`; frames
  `surface` `intents` `chains` `card` `card_close` `cards` `chat`
  `feed` `cli` `flow_open` `flow_close`.

## 7. kernel/boundary.py — permission rule validators (cold standby)

- **Responsibility**: rule-grammar validators (shape / syntax /
  `never_allow` ceiling). Since the permission consolidation
  (2026-08-24) nothing feeds them at runtime — the allow side is the
  harness's auto mode plus the `PERM_ALLOW` ledger; the validators
  stay as tested library code.
- **Anchors**: `vet_rules` `check_rules` `union_render`;
  `MODULE_POLICY.never_allow` as the substring ceiling.

## 8. kernel/procrun.py + procshim.py + kernel/procs/ — procedures

- **Responsibility**: the contract runner for wall-less code: the
  human approved the full text, hashes are cross-checked, staging is
  transactional, timeouts tree-kill. The built-in library lives in
  `kernel/procs/` (e.g. `shot.py`, the mouse-anchored / desktop
  screenshot); intents reference it **by name only** — there is no
  agent submission path.
- **Anchors**: `run_step` `file_hash` `_kill_tree`; ctx contract
  `stage / input / attach / say`; `PROC_TIMEOUT_S` `PROC_SAY_MAX`;
  defaults `PHYS_PROCEDURES` (the wordlist registration matches).

## 9. kernel/netguard.py — loopback guard, three gates

- **Responsibility**: the browser-face door. Gate 1: Origin allowlist
  (absent = non-browser client, allowed — zero false positives for
  the MCP bridge and hook mailboxes). Gate 2: Host check against DNS
  rebinding. Gate 3 (2026-08-24): `Sec-Fetch-Site` on the **action
  faces only** (/trigger, /api/*) — browsers attach it to every
  request including `<img>` simple GETs that carry no Origin, so a
  hostile page cannot blind-fire approve/shutdown; panel paths stay
  navigable from links. Full threat model in the module docstring.
- **Anchors**: `origin_ok` `host_ok` `sec_fetch_ok`; engine `_guarded`
  `_blocked`; journal kind `guard`.

## 10. kernel/prune_report.py — transcript slicing

- **Responsibility**: slices the harness's own session transcripts by
  task window — work receipts (per-task token/call counts) and the
  transcript directory locator (injection receipts, bracket
  transcripts). Soft dependency: an unavailable transcript degrades
  the receipt, never the engine's real work.
- **Anchors**: `window_usage` `transcript_dir`; engine `_task_receipt`.

## 11. kernel/deckgen.py — Stream Deck compiler

- **Responsibility**: compiles the registered library into Stream Deck
  **plugins** (one per booklet + one for standalone intents), written
  directly into `%APPDATA%\Elgato\StreamDeck\Plugins\`
  (`com.intentos.deck.*`); sweeps its own orphaned plugin dirs
  (retired/renamed booklets); never touches anyone else's plugin.
- **Behavior**: booklet plugin = one merged Start/Shutdown power key
  (`toggle: "open"`) + Approve + Interrupt + one key per member +
  Status/Step dials; intents plugin = one-way trigger keys plus
  Engine Start/Stop power key (with a baked **resurrection command** —
  argv/cwd/env — so a dead engine can be relaunched by its own key)
  and Engine Status/Task dials. Keys are plain GETs on `/trigger`
  carried in `routes.json` (re-read per keypress — URL changes need no
  app restart; manifest/action-roster changes do). Faces are rendered
  PNGs from a small built-in rasterizer; glyph vocabulary: check
  (approve, green) / square (interrupt, amber) / stop-square (cancel,
  red) / power ring.
- **Anchors**: `compile_plugins` `compile_plugin` `GLYPHS`
  `COLOR_INTENT` (Claude orange 217,119,87) `COLOR_START`;
  `proto_entries` `engine_entries`; deterministic uuid5 action UUIDs;
  `routes.json`.

## 12. deckplugin/plugin.js — the dumb trigger (Node, per plugin)

- **Responsibility**: keyDown → GET the route's URL; show ok/alert;
  everything visual is advisory. No logic that could contradict the
  engine — lights never carry load.
- **Behavior**: tap-toggle power keys probe `status_url` first
  (booklet keys read `open === true`, the engine key reads
  reachability; `draining: true` paints the amber dot during
  teardown); dead-engine Start falls back to the baked resurrection
  command; a 4 s `pollPower` owns power-key dots; a WS bus
  subscription (frames `chains` / `card`) drives steady words and
  flash colors on member keys; status dials render one word + a color
  chip (`WORD_ICON`, including `draining`).
- **Anchors**: `keys/dials` registries · `fire` `routeOf` `pollPower`
  `launchEngine` `busConnect` `stripFeedback` `WORD_ICON`
  `GLYPH_PATH/GLYPH_COLOR` `faceSVG` · MiniWS (SD's Node 20 has no
  native WebSocket).

## 13. host/pty.py — ConPTY host (sidecar and booklet CLIs)

- **Responsibility**: carries a real `claude` CLI inside the engine:
  spawn, injection, readiness/trust probes, screen replay, real
  terminal resize.
- **Implementation**: pywinpty; two-beat injection (`PASTE_BEAT`
  between text and \r); `READY_BYTES` as mounted evidence; injection
  is withheld during the trust wizard (the seat only re-arms after the
  wizard was actually seen); `setwinsize` follows panel `cli_size`.
- **Anchors**: `PtyHost` `inject_chat` `write_raw` `trusted` `replay`
  `setwinsize` `_find_claude`.

## 14. host/headless.py — headless host (x·solo)

- **Responsibility**: one order, one `claude -p` process: spawns on
  delivery, exits on settle, no conversation surface.
- **Implementation**: `deliver(tid, line)` spawns with cwd = seat
  home, pinned `--model`, `--permission-mode` (`SEAT_PERMISSION_MODE`,
  read at spawn so config overrides apply), `--permission-prompt-tool`
  = perm_gate, `--allowedTools` = pipe floor + `PERM_ALLOW` ledger.
  **Fuse**: `spawn_host=False` engines never launch a real CLI
  (test-cost precedent).
- **Anchors**: `HeadlessHost` `deliver` `reap`; engine `_xhost`
  `spawn_host`.

## 15. engine.py — the engine (plane index)

> One file, many planes; anchors per plane.

- **Trigger face**: explicit `(name, input)` only. HTTP `/trigger`
  (deck keys; `intent=` / `protocol=` + `op=start|approve|interrupt|
  shutdown|status` / `member=` / `engine=start|shutdown|status|task|
  approve|cancel`) and panel IME verbs. Anchors: `_on_trigger`
  `_on_intent` `_proto_start` `_proto_member` `_proto_close`
  `_proto_shutdown` (·wrap ceremony, `_wrapping`, force on second
  press) `_proto_interrupt` `_proto_approve` `_seat_approve`
  (newest card first, leftover count announced) `_solo_cancel`
  `_engine_shutdown` (cascade + `_draining` + truth-polled teardown).
- **Instance seats**: `ProtoInstance` — envelope queue (steps wait for
  the package to land), step ledger (`step_name/step_state`,
  `step_done` settles), `wrap_evt` for the closing ceremony; one
  bracket per booklet, brackets in parallel across booklets.
- **Container / hot set**: in-memory LRU of recently used intents,
  cap `CONTAINER_CAP`, cleared on session turnover. Anchors: `_hot`
  `_touch` `_container_trim` `_workset_reset`.
- **Retrieval faces**: `_intent_index` (container snapshot)
  `_intent_search` (two-column: ≤5 intents + ≤1 protocol)
  `_intent_catalog` (flat usage top) `_intent_get`
  (batched, part-leveled).
- **Creation & registration**: two-phase (§2u): `_intent_submit`
  (ticket + workspace mint, no gate) → `_workspace_submit`
  (register = compile: schema validation, name checks, hash freeze,
  registration card; protocol branch parses members + prep — a
  declared `wrapup` is refused since 2026-08-26 (closing is the
  engine-owned final-cleanup contract) — and rejects reserved
  member names); effects `provision_workspace` /
  `retire_intent`. Field truth: `kernel/wspace.py` `SCHEMA` /
  `PROTO_SCHEMA` (`schema.md` textbook shipped into every workspace
  by `write_schema_md`).
- **Chain runner**: spec instantiation, priority queues, procedure
  rings engine-run, deliver rings rendered into
  `runtime/tasks/<id>/`; settle routes edges and stamps effects.
  Anchors: `_deliver` `_settle` `_admit` `_admit_spec`
  `TASK_TIMEOUT_S` `MAX_NODE_VISITS`; boot-seeded specs `qual·new`
  `qual·register (FLOW_WS_QUAL)` `qual·rework` `qual·retire (FLOW_RETIRE)`
  `qual·protocol` `validate` `surgery` (surgery) `retry`
  (deliver template retry-fulfill) and `consolidate`
  (deliver template consolidate).
- **Surgery loop** (failed solo orders): proposal card (human gate) →
  repair → exactly one replay → back to the human. Anchors:
  `_surgery_open` `_surgery_settle` `_surgery_replay`
  `XSOLO_SURGERY_MD`.
- **Retry + consolidate** (reshaped 2026-08-25): user-initiated
  retry opens a retry order on the sidecar seat — autopsy the
  previous run + redo directly, settle for real (no acceptance
  round). Settlement (and a booklet close) raises a **consolidate
  offer** (card kind `offer`, exempt from the cli-engaged sweep);
  approving suspends the asset and opens a consolidate order on
  sidecar — the registration approval revives it. Anchors:
  `_on_retry` `_consolidate_offer` `_consolidate_go`
  `RETRY_FULFILL_MD` `CONSOLIDATE_MD` `proto_set_status`.
- **Card stream**: the live "waiting on a human" face (history lives
  in the journal); gate cards are exempt from reaping; ask cards
  accept typed lines. Anchors: `_card_open` `_card_close`
  `_on_card_answer` `_gates` `_gate_wait`; kinds `gate/perm/stall/
  info/ask/notify/approval`; `IDLE_STALL_S`.
- **Permission plane** (consolidated 2026-08-24): seats spawn in the
  harness's own mode (`--permission-mode`, `SEAT_PERMISSION_MODE`);
  what auto mode doesn't cover raises a card — PTY asks via the
  PermissionRequest hook (`_perm_ask`, harness-suggested rules),
  headless asks via the `perm_gate` MCP verb (tool-name rules; never
  bare Bash/PowerShell). **Always allow** on either card calls
  `_grant_rules`: ledger to `config.json` `PERM_ALLOW` (`config.
  grant`), live grants (`_perm_grants`), solo home + `--allowedTools`
  re-render. Deny floor stays engine-minted and is never widened.
  Anchors: `_perm_ask` `_perm_answer` `_grant_rules` `_perm_grants`
  `perm_gate`.
- **Telemetry bus**: PreToolUse mailbox → per-seat attribution to the
  active order; receipts and token alerts follow the order. Anchors:
  `_bus_event` `_task_receipt` `TASK_TOKEN_ALERT`;
  `runtime/tasks/<id>/events.jsonl`.
- **HTTP face** (default port 9700): panels (`/`, `/observe`,
  `/flow*` → flow.html; `/hub*` → hub.html, served from disk per
  request — edits go live on refresh), `/trigger`, `/api/discover`,
  `/api/mcp` (bridge), `/api/hook` + `/api/perm` (mailboxes);
  netguard guards every request. Anchors: `_serve_http` `_guarded`
  `PANEL_DIR`.
- **Boot sequence**: recompile delivery chains, reseed flow specs,
  re-render homes/skills, reconcile (loud, not silent), compile deck
  plugins, open the hub window (4 s probe first — an already-open
  panel reconnects instead). Anchors: `run` tail; `_open_browser`
  `_open_hub_at_boot`.

## 16. mcp.py — the MCP bridge (all agent upstream)

- **Responsibility**: a stdio thin bridge: tool frames → HTTP
  `/api/mcp`; the token rides env (identity is engine-minted); tool
  descriptions are half the teaching surface.
- **Behavior**: **per-seat faces** via `--face`: admin (sidecar, 9
  verbs: task_done, intent_submit, workspace_submit, intent_retire,
  intent_memory_index, intent_search, intent_catalog, match_protocol,
  intent_get — zero execution verbs), exec (x·solo, 3: task_done,
  ask_user, perm_gate), proto (booklet seats, exec + step_done).
  Wrong-face verbs are rejected at the bridge. `ask_user` contract:
  ≤12 options, typed free-form accepted, no pseudo-options.
- **Anchors**: `TOOLS` `FACE_ADMIN` `FACE_EXEC` `FACE_PROTO`
  `PROTO_DESC` `_call_engine` `MCP_TOKEN_ENV`. Caselaw: a schema
  field missing from the args mapping is silently dropped — every
  schema change re-checks the mapping.

## 17. panel/ — flow.html + hub.html (served fresh per request)

- **flow.html** — the universal panel: with `?i=<seat>` it is that
  booklet's face, without it the engine plane (sidecar terminal +
  sidecar/x·solo cards + human gates + standalone-intent entries).
  Cards flow **in the stream** chronologically and dim when answered;
  gate cards render only for truly human-gated rows. The input box is
  a **search IME and nothing else**: typing filters this plane's
  entries, pinning + Enter fires `(intent, input)`; free text is
  pointed to the Terminal drawer (seats cannot chat in the stream).
  Task drawer: click a task line for retry (with reason) / cancel
  (unified 2026-08-25: cancel interrupts the running ring now;
  bracket tasks get neither button — Shutdown/Interrupt are their
  exits).
  Terminal: xterm with real resize reporting (`cli_size`).
- **hub.html** — the one window: a permanent `·engine` tab (iframe of
  `/observe`) plus one tab per booklet, driven by `flow_open` /
  `flow_close` frames; last tab out closes the window.
- **Anchors**: flow `EPLANE` `own` `cardEls/gateEls` `streamAdd`
  `closeCardEl` `paintIme` `setPin`; hub `ENGINE` tab bookkeeping.

## 18. permfwd.py / hookfwd.py — hook mailboxes (harness → engine)

- **Responsibility**: the two forwarders wired into seat
  settings.json: PermissionRequest blocking arbitration, and
  Notification/Stop/PreToolUse side-channel. Pure stdlib; if the
  engine is gone they defer harmlessly.
- **Anchors**: `permfwd.main` `hookfwd.main`; engine `/api/perm`
  `/api/hook`; `PERM_HOOK_TIMEOUT_S` `HOOK_TIMEOUT_S`.

## 19. cli.py / __main__.py — entry points

- `intentos {run,stop,seed}` (installed console script;
  `python -m commander` is the module-form equivalent — the import
  package keeps its lineage name); run flags `--workspace --http
  --ws --no-host --model`; stop goes over WS; seed plants the two
  built-in templates — "timecheck" intent + "translator" booklet
  (screenshot-prelude member) — validated against their own
  registration gates, workspace sources included (format exemplars;
  skips the creation chain).
- **Anchors**: `cli.main`.

## 20. Workspace layout (data plane)

```
workspace/
  config.json         # optional knob file (kernel/config.py: ALL-CAPS
                      #   scalar defaults overridable; unknown keys
                      #   refuse to boot; CLI flags win) + the
                      #   PERM_ALLOW always-allow ledger

  state.db            # truth layer (schema v19)
  instances/<seat>/   # minted homes; memory/ is seat-private;
                      #   intent workspaces live under the sidecar
                      #   home as <name>/
  modules/<module>/   # human-owned module customizations (reserved;
                      #   no resident since 2026-08-24)
  records/<seat>/     # journal history
  runtime/            # transient (tasks/<id>/ package · materials ·
                      #   bus ledger; engine.json port truth)
  toolkit/            # shared tools (sidecar writes, executors read)
  utility/            # engine-owned store (booklet skill + key-set
                      #   artifacts, the intents keyset)
```

## 21. Test regime (the guard)

- 34 standalone scripts `python tests/test_*.py` (not pytest);
  `PYTHONIOENCODING=utf-8` required; engines embed with
  `spawn_host=False` (the no-real-CLI fuse); executor seats are faked
  (fake hosts + engine-minted tokens); `tests/hand.py` is a WS
  machine-hand fixture, not a test.
- Rough map (change a plane, run its suite; big changes run all):
  store → `test_store` · retrieval/container → `test_m10`
  `test_vector` · x·solo → `test_xsolo` `test_m9` · surgery →
  `test_surgery` · queue → `test_queue` · bus → `test_bus` ·
  perm/ask → `test_m22` · protocol/booklets → `test_m21`
  `test_cluster` `test_wrapup` · cards/cockpit → `test_m13`
  `test_m18` · bridge/faces → `test_m3` · deck & /trigger →
  `test_m26` · netguard → `test_netguard` · regressions →
  `test_fix0823` `test_p1fix`.
