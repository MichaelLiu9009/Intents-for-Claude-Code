"""procedure engine-side runner (M20 §2c) —— the execution half of
the stage transaction.

Ports the old repo's chains.py three-phase transaction semantics,
not its mechanics: RUN (subprocess, the only writable place is
stage) → FLUSH (effects-ledger replay: file materials get absorbed
into the task directory, text materials land in materials.jsonl)
→ delivery (the engine pump runs as usual). Any step failing =
zero absorption, the whole stage directory is incinerated. Hard
timeout tree-kills (charter's own wording).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_SHIM = Path(__file__).with_name("procshim.py")


def text_hash(code: str) -> str:
    """Canonical text hash: newlines normalized (Windows write_text
    lands as CRLF on disk, a byte hash would jitter across
    platforms —— snapshot cross-checks go by content, not by
    newline style)."""
    return hashlib.sha256(
        code.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def file_hash(p: Path) -> str:
    return text_hash(p.read_text(encoding="utf-8", errors="replace"))


def _kill_tree(proc: subprocess.Popen) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True)
    else:
        proc.kill()


def run_step(entry: Path, task_dir: Path, *, input_: str,
             timeout: float, say_max: int
             ) -> tuple[bool, str, list[dict]]:
    """Run one step: returns (ok, err, materials). materials items:
    {"kind":"file","label",...,"path": absorbed path inside the task
    dir} or {"kind":"text","label",...,"text"}. Absorption means
    appending into task_dir/materials.jsonl (deliver renders the
    package by gathering along the chain)."""
    task_dir.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="stage-", dir=task_dir))
    try:
        eff = stage / "_effects.jsonl"
        spec = stage / "_spec.json"
        spec.write_text(json.dumps(
            {"run": str(entry), "stage": str(stage), "input": input_,
             "say_max": say_max}, ensure_ascii=False),
            encoding="utf-8")
        try:
            proc = subprocess.Popen(
                [sys.executable, str(_SHIM), str(spec)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                cwd=str(stage))
        except OSError as e:
            return False, f"spawn failed: {e}", []
        try:
            out, errtxt = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            try:
                proc.communicate(timeout=5)
            except Exception:
                pass
            return False, f"timeout {timeout:g}s (tree killed)", []
        if proc.returncode != 0:
            # cause line = last stderr line (the exception message);
            # a full traceback would crowd the useful signal out of
            # err's truncation window (raw error text goes into the
            # chat surface and the DEBUG reason; the first 160
            # chars must read as a sentence, not a stack)
            lines = [ln for ln in (errtxt or out or "").splitlines()
                     if ln.strip()]
            tail = lines[-1].strip()[-300:] if lines else ""
            return False, tail or f"exit {proc.returncode}", []
        # FLUSH: effects-ledger replay —— only materials that made
        # the ledger go out the door
        mats: list[dict] = []
        if eff.is_file():
            matdir = task_dir / "materials"
            for ln in eff.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(ln)
                except ValueError:
                    continue
                if rec.get("kind") == "file":
                    src = Path(rec.get("path") or "")
                    if not src.is_file():
                        continue
                    matdir.mkdir(parents=True, exist_ok=True)
                    dst = matdir / src.name
                    i = 1
                    while dst.exists():
                        dst = matdir / f"{i}-{src.name}"
                        i += 1
                    try:
                        if str(src).startswith(str(stage)):
                            src.replace(dst)     # in stage: rename = absorb
                        else:
                            shutil.copy2(src, dst)  # external: copy only
                    except OSError as e:
                        return False, (f"material absorption failed "
                                       f"{src.name}: {e}"), []
                    mats.append({"kind": "file", "label": rec.get("label")
                                 or "", "path": str(dst)})
                elif rec.get("kind") == "text":
                    mats.append({"kind": "text", "label": rec.get("label")
                                 or "", "text": rec.get("text") or ""})
        if mats:
            with open(task_dir / "materials.jsonl", "a",
                      encoding="utf-8") as f:
                for m in mats:
                    f.write(json.dumps(m, ensure_ascii=False) + "\n")
        return True, "", mats
    finally:
        shutil.rmtree(stage, ignore_errors=True)   # chain dead = burn it
