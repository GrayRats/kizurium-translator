"""Independent OCR selection and local dependency/model diagnostics."""
from __future__ import annotations

import importlib.util
import logging
import threading
from pathlib import Path

log = logging.getLogger(__name__)
_lock = threading.Lock()
_paddle = None
_paddle_config = None


def inventory(options=None):
    from .universal import installed_tesseract_langs

    options = options or {}
    langs = installed_tesseract_langs()
    result = {"tesseract": {"available": bool(langs), "detail": ", ".join(langs) or "Tesseract / language packs missing", "languages": list(langs)}}
    for engine, modules in {"rapid": ("rapidocr", "onnxruntime"), "paddle": ("paddleocr", "paddle")}.items():
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
        else:
            dirs = [Path(options.get(key) or "/nonexistent") for key in ("paddle_det", "paddle_rec")]
            ready = all((d / "inference.pdiparams").is_file() and ((d / "inference.json").is_file() or (d / "inference.pdmodel").is_file()) for d in dirs)
            detail = "Local PaddleOCR models configured" if ready else "Set local PaddleOCR detection and recognition model directories"
        result[engine] = {"available": ready, "detail": detail, "languages": []}
    return result


def paddle_lines(image):
    global _paddle, _paddle_config
    from ..core.active import current
    import numpy as np

    options = current().ocr_options
    signature = (options.get("paddle_det"), options.get("paddle_rec"))
    if not inventory(options)["paddle"]["available"]:
        raise RuntimeError("PaddleOCR dependencies or local models missing")
    with _lock:
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
