"""procedure subprocess-side shim (M20 §2c) —— self-contained, pure
stdlib, zero dependencies.

The engine spawns this via `python procshim.py <spec.json>`; spec
carries the run.py path, the stage directory, the trigger input, and
bound parameters. Here it builds ctx, loads run.py, and calls
`run(ctx)`. Contract (declared by the engine, the only shape it
recognizes):

  ctx.stage            scratch directory (Path; chain dies = burned,
                        anything not on the ledger never goes out)
  ctx.input            trigger input (the argument following the IME
                        word, can be an empty string)
  ctx.attach(path, label="")   file material onto the ledger →
                                engine stitches it into the package
  ctx.say(text, label="")      text material onto the ledger →
                                engine stitches it into the package

Failure = raise (non-zero exit, the engine voids the whole chain
tagged with the step name). Materials leave only through the two
doors attach/say —— that's the transaction: the effects ledger lives
at stage/_effects.jsonl, the engine absorbs it only after success,
zero residue on failure.
"""
import importlib.util
import json
import sys
import traceback
from pathlib import Path


class Ctx:
    def __init__(self, stage: Path, input_: str,
                 effects: Path, say_max: int):
        self.stage = stage
        self.input = input_
        self._eff = effects
        self._say_max = say_max

    def _emit(self, rec: dict) -> None:
        with open(self._eff, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def attach(self, path, label: str = "") -> None:
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"attach: not a file {p}")
        self._emit({"kind": "file", "path": str(p.resolve()),
                    "label": str(label)[:80]})

    def say(self, text, label: str = "") -> None:
        self._emit({"kind": "text", "text": str(text)[:self._say_max],
                    "label": str(label)[:80]})


def main() -> int:
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    stage = Path(spec["stage"])
    entry = Path(spec["run"])
    mspec = importlib.util.spec_from_file_location("proc_run", entry)
    mod = importlib.util.module_from_spec(mspec)
    try:
        mspec.loader.exec_module(mod)
    except Exception:
        traceback.print_exc()
        return 2
    fn = getattr(mod, "run", None)
    if not callable(fn):
        print("contract breach: run.py lacks a top-level def run(ctx)",
              file=sys.stderr)
        return 3
    ctx = Ctx(stage, str(spec.get("input") or ""),
              stage / "_effects.jsonl",
              int(spec.get("say_max") or 4000))
    try:
        r = fn(ctx)
    except Exception:
        traceback.print_exc()
        return 1
    if r is not None:
        # live-fire precedent 2026-08-15: agent-written procedures
        # naturally use return to hand off output (that's how the
        # first paved-road version was written, and that reporting
        # line silently evaporated) —— a non-None return value is
        # now an implicit say, output no longer depends on the
        # author remembering to call ctx.say
        ctx.say(r, label="return")
    return 0


if __name__ == "__main__":
    sys.exit(main())
