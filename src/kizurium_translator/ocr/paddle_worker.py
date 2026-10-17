"""Persistent Python worker for PaddleOCR when the app Python lacks a wheel.

The protocol is one JSON metadata line, exactly ``size`` RGB bytes, then a
newline. Responses are JSON lines. The Paddle runtime is initialized once per
model pair and reused for later frames.
"""
from __future__ import annotations

import contextlib
import json
import sys


def read_exact(stream, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.read(size - len(chunks))
        if not chunk:
            raise EOFError("incomplete image payload")
        chunks.extend(chunk)
    return bytes(chunks)


def main() -> int:
    import numpy as np

    runtime = None
    signature = None
    source, sink = sys.stdin.buffer, sys.stdout.buffer
    while header := source.readline():
        try:
            request = json.loads(header)
            width, height, size = (int(request[k]) for k in ("width", "height", "size"))
            if width <= 0 or height <= 0 or size != width * height * 3:
                raise ValueError("invalid image dimensions")
            raw = read_exact(source, size)
            if source.read(1) != b"\n":
                raise ValueError("invalid image frame boundary")
            pair = (str(request.get("det") or ""), str(request.get("rec") or ""))
            if runtime is None or pair != signature:
                with contextlib.redirect_stdout(sys.stderr):
                    from paddleocr import PaddleOCR

                    runtime = PaddleOCR(
                        text_detection_model_dir=pair[0],
                        text_recognition_model_dir=pair[1],
                        use_doc_orientation_classify=False,
                        use_doc_unwarping=False,
                        use_textline_orientation=False,
                        device="cpu",
                        cpu_threads=2,
                    )
                signature = pair
            image = np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 3))
            with contextlib.redirect_stdout(sys.stderr):
                results = runtime.predict(image[:, :, ::-1])
            lines = []
            for result in results:
                for text, score, poly in zip(
                    result["rec_texts"], result["rec_scores"], result["rec_polys"], strict=True
                ):
                    points = np.asarray(poly, dtype=float)
                    x1, y1 = points.min(axis=0)
                    x2, y2 = points.max(axis=0)
                    lines.append({
                        "text": str(text), "conf": float(score) * 100,
                        "box": [int(x1), int(y1), int(x2), int(y2)],
                        "line_height": max(1, int(y2) - int(y1)), "engine": "paddle",
                    })
            response = {"ok": True, "lines": lines}
        except Exception as exc:
            response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        sink.write((json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n").encode())
        sink.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
