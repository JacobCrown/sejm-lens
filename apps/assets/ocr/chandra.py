"""Chandra OCR 2 (datalab-to/chandra-ocr-2). Install with `uv sync --extra ocr`.

The weights are about 10 GB and run on the GPU when OCR_DEVICE=gpu.
The model license is OpenRAIL-M: fine for personal and research use.
"""

import os
import threading
from pathlib import Path
import sys

from django.conf import settings

from .base import OCRError, OCRLine

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from chandra_text import chunk_boxes  # noqa: E402


def lines_from_chandra_chunks(chunks) -> list[OCRLine]:
    """Chandra chunks already use pixel boxes on the image we passed in."""
    return [
        OCRLine(text=text, score=1.0, x0=x0, y0=y0, x1=x1, y1=y1)
        for text, x0, y0, x1, y1 in chunk_boxes(chunks)
    ]


def _torch_device(ocr_device: str) -> str:
    device = (ocr_device or "cpu").lower()
    if device == "cpu":
        return "cpu"
    if device == "gpu":
        return "cuda"
    if device.startswith("gpu:"):
        return "cuda:" + device.split(":", 1)[1]
    return device


class ChandraEngine:
    name = "chandra"
    _instance: "ChandraEngine | None" = None
    _lock = threading.Lock()

    def __init__(self, device: str):
        os.environ["TORCH_DEVICE"] = _torch_device(device)
        try:
            from chandra.model import InferenceManager
            from chandra.model.schema import BatchInputItem
        except ImportError as exc:
            raise OCRError("Chandra is not installed. Run: uv sync --extra ocr") from exc
        self._BatchInputItem = BatchInputItem
        try:
            self._manager = InferenceManager(method="hf")
        except Exception as exc:
            raise OCRError(f"Failed to start Chandra OCR: {exc}") from exc
        weight = next(self._manager.model.parameters())
        print(f"Chandra weights: {weight.device} {weight.dtype}", file=sys.stderr)
        if _torch_device(device).startswith("cuda") and weight.device.type != "cuda":
            raise OCRError(f"Chandra loaded on {weight.device}, not on the GPU")

    @classmethod
    def shared(cls) -> "ChandraEngine":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(settings.OCR_DEVICE)
            return cls._instance

    def recognize(self, image) -> list[OCRLine]:
        batch = [self._BatchInputItem(image=image.convert("RGB"), prompt_type="ocr_layout")]
        try:
            result = self._manager.generate(batch)[0]
        except Exception as exc:
            raise OCRError(f"Chandra OCR error: {exc}") from exc
        if result.error:
            raise OCRError("Chandra OCR returned an error for this page")
        return lines_from_chandra_chunks(result.chunks)
