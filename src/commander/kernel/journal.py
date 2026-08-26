"""Session journal —— one session directory per seat, one line per
event.

CASELAW 2: single writer —— only the engine process writes the
journal; the in-line timestamp is stamped at the moment of writing
(absolute instant, CASELAW 21). append mode + flush per line: one
line is one complete json.dumps; readers parse line by line and
skip broken lines (the read-side self-healing of CASELAW 6/7 belongs
to the reader, the writer only guarantees line completeness).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .. import defaults


class Journal:
    """M15: one more sink —— the same line lands both in the jsonl
    and is handed to the store (events table), so it can be queried
    by time range / category. **Still a single writer**: only the
    engine process writes; jsonl and events are two sheets of paper
    under the same pen, not two writers. The sink can be attached
    later (the engine wires it in run()); if absent it degrades to
    plain jsonl.
    """

    def __init__(self, records_root: Path, mode: str, *, sink=None):
        self.session = time.strftime("%Y-%m-%d_%H%M%S")
        self.dir = records_root / mode / self.session
        self.dir.mkdir(parents=True, exist_ok=True)
        self._path = self.dir / defaults.JOURNAL_NAME
        self._f = open(self._path, "a", encoding="utf-8")
        self.sink = sink

    def row(self, kind: str, name: str, **fields) -> None:
        rec = {"t": time.strftime("%Y-%m-%d %H:%M:%S"),
               "kind": kind, "name": name}
        rec.update({k: v for k, v in fields.items() if v is not None})
        try:
            self._f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._f.flush()
        except Exception as e:      # log fail must not backfire; still traced
            print(f"[journal] dropped line {kind}/{name}: {e!r}")
        # The two sheets of paper are independent: if jsonl drops,
        # the sink still runs, and vice versa —— so one breaking
        # doesn't drag the other down. Sink exceptions are swallowed
        # under the same discipline.
        if self.sink is not None:
            try:
                self.sink(dict(rec), self.session)
            except Exception as e:
                print(f"[journal] sink dropped line {kind}/{name}: "
                      f"{e!r}")

    def close(self) -> None:
        try:
            self._f.close()
        except Exception:
            pass
