"""Independent OCR selection and local dependency/model diagnostics."""
from __future__ import annotations

import atexit
import importlib.util
import json
import logging
import os
import select
import subprocess
import sys
import threading
from pathlib import Path

log = logging.getLogger(__name__)
_lock = threading.Lock()
_paddle = None
_paddle_config = None
_paddle_process = None
_paddle_log = None
_paddle_runtime_cache = None


def _paddle_python():
    """Return a Python that can import Paddle, preferring the app's sidecar."""
    global _paddle_runtime_cache
    from ..paths import default_paths

    sidecar = default_paths().data_dir / "paddle-venv" / "bin" / "python"
    configured = os.environ.get("KIZURIUM_PADDLE_PYTHON", "").strip()
    candidates = [Path(configured).expanduser()] if configured else []
    candidates.extend((sidecar, Path(sys.executable)))
    for python in candidates:
        try:
            # Keep the venv path: resolving its python symlink to /usr/bin/python
            # would discard the venv's site-packages when probing/importing.
            resolved = str(python.absolute())
            if not python.exists():
                continue
        except OSError:
            continue
        cached = _paddle_runtime_cache
        if cached and cached[0] == resolved:
            return python if cached[1] else None
        try:
            result = subprocess.run(
                [resolved, "-c", "import importlib.util as u; raise SystemExit(0 if u.find_spec('paddle') and u.find_spec('paddleocr') else 1)"],
                capture_output=True,
                check=False,
                timeout=5,
            )
            available = result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            available = False
        _paddle_runtime_cache = (resolved, available)
        if available:
            return python
    return None


def _stop_paddle_worker():
    global _paddle_process, _paddle_log
    process, _paddle_process = _paddle_process, None
    if process is not None:
        try:
            if process.stdin:
                process.stdin.close()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
            process.wait()
    if _paddle_log is not None:
        _paddle_log.close()
        _paddle_log = None


def _write_pipe(stream, data):
    view = memoryview(data)
    while view:
        written = stream.write(view)
        if not written:
            raise BrokenPipeError("PaddleOCR worker pipe closed")
        view = view[written:]


def shutdown_paddle_worker():
    """Stop the persistent Paddle runtime during application shutdown."""
    with _lock:
        _stop_paddle_worker()


atexit.register(shutdown_paddle_worker)


def _paddle_worker_lines(image, signature):
    global _paddle_process, _paddle_log
    from ..paths import default_paths

    python = _paddle_python()
    if python is None:
        raise RuntimeError("PaddleOCR runtime unavailable (install PaddleOCR with install.sh --paddle)")
    paths = default_paths()
    worker = Path(__file__).with_name("paddle_worker.py")
    if not worker.is_file():
        raise RuntimeError(f"PaddleOCR worker missing: {worker}")
    if _paddle_process is None or _paddle_process.poll() is not None:
        _stop_paddle_worker()
        paths.state_dir.mkdir(parents=True, exist_ok=True)
        _paddle_log = (paths.state_dir / "paddle-worker.log").open("ab", buffering=0)
        _paddle_process = subprocess.Popen(
            [str(python), str(worker)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=_paddle_log,
            bufsize=0,
        )
    process = _paddle_process
    if process.stdin is None or process.stdout is None:
        _stop_paddle_worker()
        raise RuntimeError("Could not open PaddleOCR worker pipes")
    import numpy as np

    rgb = np.ascontiguousarray(image.convert("RGB"), dtype=np.uint8)
    payload = memoryview(rgb).cast("B")
    request = {"width": rgb.shape[1], "height": rgb.shape[0],
               "size": len(payload), "det": signature[0], "rec": signature[1]}
    try:
        _write_pipe(process.stdin, (json.dumps(request, separators=(",", ":")) + "\n").encode())
        _write_pipe(process.stdin, payload)
        _write_pipe(process.stdin, b"\n")
        process.stdin.flush()
        ready, _, _ = select.select([process.stdout], [], [], 300)
        if not ready:
            raise TimeoutError("PaddleOCR worker timed out")
        response = json.loads(process.stdout.readline())
    except (BrokenPipeError, OSError, ValueError, TimeoutError) as exc:
        _stop_paddle_worker()
        raise RuntimeError(f"PaddleOCR worker failed: {type(exc).__name__}: {exc}") from exc
    if not response.get("ok"):
        raise RuntimeError(str(response.get("error") or "PaddleOCR worker failed"))
    return response.get("lines", [])


def inventory(options=None):
    from .universal import installed_tesseract_langs

    options = options or {}
    langs = installed_tesseract_langs()
    result = {"tesseract": {"available": bool(langs), "detail": ", ".join(langs) or "Tesseract / language packs missing", "languages": list(langs)}}
    for engine, modules in {"rapid": ("rapidocr", "onnxruntime")}.items():
        missing = [name for name in modules if importlib.util.find_spec(name) is None]
        if missing:
            result[engine] = {"available": False, "detail": "Missing: " + ", ".join(missing), "languages": []}
            continue
        if engine == "rapid":
            spec = importlib.util.find_spec("rapidocr")
            root = Path(spec.origin).parent
            models = list(root.rglob("*.onnx"))
            ready = len(models) >= 3
            detail = "Local ONNX models: " + ", ".join(p.name for p in models) if ready else "Local detection/classification/recognition ONNX models missing"
        result[engine] = {"available": ready, "detail": detail, "languages": []}
    dirs = [Path(options.get(key) or "/nonexistent") for key in ("paddle_det", "paddle_rec")]
    models_ready = all((d / "inference.pdiparams").is_file() and ((d / "inference.json").is_file() or (d / "inference.pdmodel").is_file()) for d in dirs)
    runtime = _paddle_python()
    result["paddle"] = {
        "available": bool(runtime and models_ready),
        "detail": ("Local PaddleOCR models configured" if runtime and models_ready else
                   "PaddleOCR Python runtime missing" if runtime is None else
                   "Set local PaddleOCR detection and recognition model directories"),
        "languages": [],
    }
    return result


def paddle_lines(image):
    global _paddle, _paddle_config
    from ..core.active import current
    options = current().ocr_options
    signature = (options.get("paddle_det"), options.get("paddle_rec"))
    if not inventory(options)["paddle"]["available"]:
        raise RuntimeError("PaddleOCR dependencies or local models missing")
    with _lock:
        python = _paddle_python()
        if python is not None and os.path.abspath(python) != os.path.abspath(sys.executable):
            return _paddle_worker_lines(image, signature)
        import numpy as np

        if _paddle is None or _paddle_config != signature:
            from paddleocr import PaddleOCR

            _paddle = PaddleOCR(text_detection_model_dir=signature[0], text_recognition_model_dir=signature[1],
                                use_doc_orientation_classify=False, use_doc_unwarping=False,
                                use_textline_orientation=False, device="cpu", cpu_threads=2)
            _paddle_config = signature
        lines = []
        for result in _paddle.predict(np.asarray(image.convert("RGB"))[:, :, ::-1]):
            for text, score, poly in zip(result["rec_texts"], result["rec_scores"], result["rec_polys"], strict=True):
                xs, ys = [float(p[0]) for p in poly], [float(p[1]) for p in poly]
                x1, y1, x2, y2 = int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))
                lines.append({"text": text, "conf": float(score) * 100, "box": (x1, y1, x2, y2),
                              "line_height": max(1, y2-y1), "engine": "paddle"})
        return lines
