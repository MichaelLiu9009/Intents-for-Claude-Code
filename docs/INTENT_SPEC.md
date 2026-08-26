# INTENT_SPEC — the intent & protocol contract (M26 / 0.1.0)

> This is the contract **as implemented**. Where this document and the
> code disagree, the code and its tests win. The single source of truth
> for declaration fields is `src/commander/kernel/wspace.py`
> (`SCHEMA` / `PROTO_SCHEMA`) — the engine validates with the same
> table agents learn from, and every intent workspace gets it rendered
> as a `schema.md` textbook. The Chinese design-history predecessor of
> this document (v1–v6) stays in the maintainer's working repo and is
> not part of the release.

## 0. Model

- **Two species**: an **intent** is a stateless one-way order; a
  **protocol** ("booklet") is a stateful multi-round bracket. The test
  for which one you need: does the next step have to remember the last
  one?
- **Three kinds of seats**: `sidecar` (resident admin/compile seat),
  `x·solo` (headless executor for standalone intents, one process per
  order, parallel), `x·<booklet>` (one resident instance seat per
  protocol, with a persistent home and memory). Every seat runs in
  the harness's own permission mode (spawned with
  `--permission-mode`, default `auto`); the engine owns the deny
  floor and the human-approved `PERM_ALLOW` ledger in `config.json`.
- **Truth lives in SQLite** (`state.db`, engine single-writer, schema
  `user_version` 19, additive migrations only). Every file an agent or
  a person sees — packages, CLAUDE.md, key sets, panels — is a render
  product and can be regenerated.
- Triggering is always explicit `(name, input)` — the engine never
  sniffs prose for intent.

## 1. Intent declaration (`intent.json`)

One intent = one directory: `<name>/` holding `intent.json`,
`CLAUDE.md` (conventions guide), `schema.md` (the field textbook,
engine-owned, rewritten on every provision), `tools/`, `inputs/`, and
`records/` (**engine-written, agent-read-only**).

Fields (authority: `wspace.SCHEMA`):

| Field | Required | Shape | Cap | Meaning |
|---|---|---|---|---|
| `name` | yes | word | 20 | scenario word; doubles as directory and trigger name |
| `title` | no | text | 60 | one-line human title |
| `scenario` | yes | word | 20 | **one-word** situational tag; the vector layer clusters on it (same-word pile-up = a signal this family wants to become a booklet); long descriptions hurt retrieval |
| `steps` | yes | text | 1200 | the **E section**: a pseudo-code function body (see grammar below) |
| `acceptance` | no | text | 800 | the **R section**: three-state verdict criteria — `ok:` / `ok_issue:` / `failed:`; mechanical checks preferred; omitted = default verdict rules |
| `procedures` | no | names | 5 | optional prelude names from the engine's built-in library (e.g. `screenshot`); matched against the wordlist at registration, run **before** delivery, materials rendered into the order; a crashed prelude reports to the human and does not deliver |
| `tools` | no | names | 20 | tool names; the engine resolves `tools/<name>.*` by convention and freezes a hash at registration |

**I-E-R**: I = the trigger context (scenario word + user input), E =
the steps function body, R = the acceptance verdict. The intent *is*
this function body — without `steps` it does not exist.

**E grammar** (registration-time mechanical check; sick lines are named
one by one): each line is

```
N. <verb> <content> [-> if <condition>, (<branch>, <branch>)]
```

The verb set is a compact **closed verb table owned by the engine**,
each verb carrying a content-length budget (`defaults.E_VERBS`):
`read` `inspect` `write` `open` `stop` (160) · `call` `ask` `report`
(200) · `judge` (400). `judge` is the only open semantic
instruction — it spends LLM judgment and is priced accordingly: the
fewer, the cheaper. Conditions are ≤80 chars and must be mechanically
decidable (exit code / count / contains a literal). Extending the
verb table means changing the engine.

## 2. Protocol declaration (`protocol.json`)

A booklet directory additionally holds `skill.md` (the booklet's manual,
≤20,000 chars — the human approves the full text) and `members/` — one
`members/<name>/intent.json` per member (same field table as a
standalone intent). Members have **no independent household**: the
booklet is one compile unit behind one gate; one bad member rejects the
whole booklet.

Fields (authority: `wspace.PROTO_SCHEMA`):

| Field | Required | Shape | Cap | Meaning |
|---|---|---|---|---|
| `name` | yes | word | 20 | booklet name |
| `scenario` | no | word | 20 | situational tag (booklets cluster by family) |
| `subtype` | yes | enum | — | only `interactive` (multi-round bracket); straight-line execution belongs to intents on x·solo |
| `members` | — | names | 10 | member roster; the seat-count law wants **3–10 including the two system slots ·启/·收** (so 1–8 human members); reserved names (`·启 ·收 开启 结束 收场 prep wrapup`) are rejected at registration |
| `prep` | no | text | 800 | content of the **·启 opening system step** — delivered automatically when the bracket opens, run before the greeting (e.g. read the booklet's state, report where we left off); empty = default (greet and stand by) |
| `wrapup` | no | text | 800 | content of the **·收 closing system step** — delivered when the user presses Shutdown; the seat runs it and calls `step_done(member="·收")`, and only then does the engine settle and close (45 s grace as fallback; pressing Shutdown again forces) |

## 3. Lifecycle

**Creation is human-initiated.** The agent may point out "this could be
an intent" in conversation; it never registers on its own.

Two-phase registration:

1. `intent_submit` — opens the ticket and mints the workspace folder.
   Nothing is approvable yet, so there is no gate here. The agent then
   writes the declaration and assets locally (guided by the folder's
   `CLAUDE.md` and `schema.md`).
2. `workspace_submit` — **registration = compilation**: the engine
   validates against the schema table, resolves declared names to
   conventional paths, freezes content hashes, and raises **one
   registration card**. The human's Approve makes it `provisioned`:
   the IME dictionary and the Stream Deck key sets recompile
   immediately.

**Revision**: edit the folder, re-register. Content hashes are frozen
at registration, and re-registering is the only way an edit becomes
official — the seats' teaching texts hold agents to this. (A
mechanical trigger-time hash re-check is on the roadmap; today the
freeze is an audit anchor, not a runtime gate.)

**Retirement**: `intent_retire` (admin verb) opens a human gate
(flow `qual·退役`); on approval the intent goes `retired`, leaves the
hot index, and the key sets recompile.

**Failure → surgery**: a failed solo order opens the surgery loop —
a human-gated proposal card, a repair pass, exactly one replay, and
back to the human if it fails again. Cancellation by the human is not
failure and never triggers surgery.

**Retry → redo + consolidate**: retry is the user's move (a verdict
of "not good enough", available from the panel's task drawer with an
optional reason). It opens a **retry order on the sidecar seat**: the
sidecar autopsies the previous run, redoes the result directly
(grilling the user when needed), and settles — no acceptance round;
still unsatisfied means pressing retry again. After settlement the
engine raises a **consolidate offer**; approving it **suspends** the
intent (or booklet — a booklet close raises the same offer) and opens
a consolidate order telling the sidecar to fold the lesson into the
declaration. The registration approval is what revives the asset.
Retry and consolidate orders are exempt from the task clock.

## 4. Execution

**Standalone intents** (x·solo): trigger → chain; declared procedures
run first as preludes; the engine renders a package into
`runtime/tasks/<id>/`; a fresh headless CLI seat executes it and
settles with `task_done` (`ok` / `ok_issue` / `failed` per the R
section). Parallel by design; a same-intent order already in flight
dedupes the new trigger; `TASK_TIMEOUT_S` (15 min) judges an unsettled
order failed (brackets exempt).

**Protocols** (x·\<booklet\>): the power key opens the bracket — one
bracket = one task, and opening triggers **no** member intent. The
opening envelope carries the ·启 prep. Member keys deliver steps into
the instance (each settles with `step_done(member=...)`); a member
that declares `procedures` gets its preludes run by the engine first —
materials land in the bracket's task directory and the step envelope
ends with a `materials:` pointer (a failed prelude reports to the
human and drops that step; the bracket stays open). Shutdown
delivers the ·收 wrapup, waits for `step_done(·收)` or the 45 s grace,
then settles the task and closes the seat gracefully (ESC + /exit,
tree-kill fallback); pressing Shutdown during the ceremony forces it.
The instance home (workspace, memory, permission sediment) persists
across sessions.

**Asking the human**: `ask_user` raises a card with ≤12 options and
also accepts a typed free-form answer (`{typed: true}`); agents must
not invent a "manual input" pseudo-option. Cards flow chronologically
in the seat's stream and dim when answered.

## 5. Trigger surfaces

- **Stream Deck**: the engine compiles one plugin per booklet plus one
  for standalone intents. Keys are plain HTTP GETs to `/trigger`
  (`?intent=N`, `?protocol=X&op=start|approve|interrupt|shutdown|
  status`, `?protocol=X&member=Y`, `?engine=start|shutdown|status|
  task|approve|cancel`). Power keys are tap-toggles that probe status truth
  (`open` for booklets, reachability for the engine; `draining` shows
  as an amber dot during teardown). Lights are advisory, never
  load-bearing.
- **Panels**: `/hub` is one window, one tab per seat (plus the
  permanent engine tab); the input box is a **search IME** — typing
  filters this seat's entries (standalone intents on the engine plane,
  member steps on a booklet plane), pinning + Enter fires
  `(intent, input)`. Free text is not chat: seats answer only through
  cards, so conversation happens in the Terminal drawer.

## 6. Flows (task chains)

A flow spec declares ordered steps — `procedure` (engine-run),
`deliver` (render a package to a seat), `gate` (parks on a human,
forever if need be) — with `on_ok` / `on_fail` edges; the engine owns
all routing, and an agent's job ends at settling its own step. The
spec library is **engine-owned and seeded at boot** (the `spec_put`
block at the top of `engine.run`): `qual·初生` (new intent),
`qual·注册` (workspace registration), `qual·回炉` (rework),
`qual·退役` (retire), `qual·protocol` (booklet registration),
`validate`, `手术` (surgery), `retry` (deliver template
retry-fulfill), and `consolidate` (deliver template consolidate).
Agents have no flow-submission verb.

Priorities are fixed: 0 execution · 1 alert (self-build/QA) · 2 error
(surgery) · 3 internal (never cancellable). A seat's queue admits only
orders at or above its current ceiling; gated tasks occupy no seat.
Cancel is chain-scoped and unified (one meaning on every surface):
it **interrupts the running ring now** — an x·solo process is
reaped, a sidecar order gets a drop notice, a cancelled consolidate
un-suspends its asset — and voids the rest of the chain. A cancel
is never a failure and does not ignite surgery. Protocol brackets
are the exception: their only exits are the Shutdown key and
Interrupt, so cancel (and retry) do not apply to bracket tasks.

## 7. Guard rails

- **Store single-writer**: agents and pages never touch the DB file;
  every read goes through engine query faces.
- **Schema additive-only**; `user_version` marks the generation (19).
- **MCP faces are disjoint by seat** (`src/commander/mcp.py`):
  **admin** (sidecar; 9 verbs — task_done, intent_submit,
  workspace_submit, intent_retire, intent_memory_index, intent_search,
  intent_catalog, match_protocol, intent_get; **zero execution
  verbs**), **exec** (x·solo; 3 — task_done, ask_user, perm_gate),
  **proto** (booklet seats; exec + step_done). Wrong-face verbs are
  rejected at the bridge, before dispatch.
- **Verdicts stay with the human**: the engine records mechanical
  truth (timestamps, state transitions, records); agents report only
  semantic truth about their own work; no verdict verb exists on any
  agent face.
- **Network**: loopback only, three browser-face gates — see
  `kernel/netguard.py`.
