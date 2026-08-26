"""§2u guard: **the parameter declaration surface has been removed**
(user ruling 2026-08-15).

The original §2k parameter table (intent-declared params + chain's
with: defaults) has retired entirely -- not for "simplification" but
as **symptom elimination**: a param is a symptom of an input space
that isn't closed. Once the workspace is pinned down, parameters
bake into the environment (a procedure no longer asks "which
folder" -- that intent's workspace is the answer).

**2026-08-15 user order "clear out params while we're at it": the
machinery has been pulled out by the roots** -- `parse_params` /
`ctx.opt` / `with:` / the IME parameter placeholder hint / open-loop
parsing all retired; a procedure's contract is now just
`ctx.stage / ctx.input / ctx.attach / ctx.say`. The DB's
`intents.params` `tasks.params` `intent_steps.params` three columns
**stay but are unused** (fossil columns -- the old rule of never
renaming DB columns).

Run: PYTHONIOENCODING=utf-8 python tests/test_params.py
"""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from commander import mcp                               # noqa: E402
from commander.kernel import wspace                     # noqa: E402

FAILS = []


def check(label, cond):
    print(("OK  " if cond else "FAIL") + " " + label)
    if not cond:
        FAILS.append(label)


tools = {t["name"]: t for t in mcp.TOOLS}
props = tools["intent_submit"]["inputSchema"]["properties"]
check("1 §2u the ticket-opening verb carries no params field "
      "(declaration surface torn out)",
      "params" not in props)
check("2 §2u the chain field exits entirely (v16 physical "
      "layer: chain hangs off the keybinding, no longer a "
      "declaration-surface slot)",
      "chain" not in props)
check("3 §2u the schema table has none of the three slots "
      "params / with / chain",
      "params" not in wspace.SCHEMA and "with" not in wspace.SCHEMA
      and "chain" not in wspace.SCHEMA)
probs = wspace.validate({"name": "x", "scenario": "x", "steps": "s",
                         "params": ["名字"]})
check("5 §2u stuffing params into the declaration -> "
      "registration calls out the unknown field (anything "
      "outside the table is rejected)",
      any("unknown fields" in p for p in probs))

# -- machinery pulled out by the roots: unfindable in the engine --
import inspect                                          # noqa: E402
from commander import engine as _eng                    # noqa: E402
from commander.kernel import store as _st, procshim as _ps,     procrun as _pr                                      # noqa: E402

src = inspect.getsource(_eng) + inspect.getsource(_st)
check("6 §2u parse_params / _parse_pdecl / _typed_input_md all "
      "torn out",
      "def parse_params" not in src and "_parse_pdecl" not in src
      and "_typed_input_md" not in src)
check("7 §2u the procedure contract is down to just "
      "stage/input/attach/say (no ctx.opt)",
      not hasattr(_ps.Ctx, "opt")
      and "opts" not in inspect.signature(_pr.run_step).parameters)

print()
print("PARAMS PASS" if not FAILS else f"PARAMS FAIL ({len(FAILS)})")
sys.exit(1 if FAILS else 0)
