"""Bergamot inference in its own process.

The native ``bergamot`` binding has no wheel for the Python the application runs
on (PyGObject ties that to the system interpreter), so it lives in a small
virtual environment of its own and this script runs there. It imports nothing
from kizurium_translator: only the standard library, PyYAML and ``bergamot``.

Protocol: one JSON object per line on stdin, one answer per line on stdout::

    {"id": 1, "op": "load", "dir": "/abs/model/dir"}   -> {"id": 1, "ok": true}
    {"id": 2, "op": "translate", "text": "Hello"}       -> {"id": 2, "ok": true, "text": "..."}
    {"id": 3, "op": "ping"}                             -> {"id": 3, "ok": true}

The model directory has already been verified (manifest, sizes, sha256) by the
application; the paths inside it are still never allowed to leave it. EOF on stdin
ends the process. Nothing is printed except answers.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# Run as a script, sys.path[0] is this package directory, whose bergamot.py is not
# the binding. Drop it before anything is imported.
if sys.path and Path(sys.path[0]).resolve() == Path(__file__).resolve().parent:
    sys.path.pop(0)

_state = {"service": None, "model": None, "bergamot": None}


def _inside(root: Path, name: str) -> str:
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("path outside the model directory")
    return str(path)


def load(directory: str) -> None:
    import bergamot
    import yaml

    root = Path(directory)
    manifest = json.loads((root / "kizurium-model.json").read_text(encoding="utf-8"))
    config_path = Path(_inside(root, manifest["config"]))
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    for key in ("models", "vocabs"):
        cfg[key] = [_inside(config_path.parent, name) for name in cfg[key]]
    if cfg.get("shortlist"):
        cfg["shortlist"][0] = _inside(config_path.parent, cfg["shortlist"][0])
    cfg.update({"ssplit-prefix-file": "", "ssplit-mode": "paragraph", "max-length-break": 128,
                "mini-batch-words": 512, "workspace": 128, "alignment": "soft"})
    service = bergamot.Service(bergamot.ServiceConfig(numWorkers=1, logLevel="off"))
    with tempfile.TemporaryDirectory(prefix="kizurium-bergamot-") as temp:
        private = Path(temp) / "config.yml"
        private.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        model = service.modelFromConfigPath(str(private))
    _state.update(service=service, model=model, bergamot=bergamot)


def translate(text: str) -> str:
    if _state["model"] is None:
        raise RuntimeError("no model loaded")
    bergamot = _state["bergamot"]
    result = _state["service"].translate(
        _state["model"], bergamot.VectorString([text]), bergamot.ResponseOptions())
    return result[0].target.text.strip()


def handle(request: dict) -> dict:
    op = request.get("op")
    if op == "ping":
        return {"ok": True}
    if op == "load":
        load(str(request["dir"]))
        return {"ok": True}
    if op == "translate":
        return {"ok": True, "text": translate(str(request["text"]))}
    return {"ok": False, "error": "unknown op"}


def main() -> int:
    # Answers go through a private copy of stdout; anything native code prints
    # lands on stderr instead of corrupting the protocol.
    out = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request_id = None
        try:
            request = json.loads(line)
            request_id = request.get("id")
            answer = handle(request)
        except Exception as exc:  # noqa: BLE001 - reported to the caller, never fatal
            answer = {"ok": False, "error": type(exc).__name__}
        answer["id"] = request_id
        out.write(json.dumps(answer, ensure_ascii=False) + "\n")
        out.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
