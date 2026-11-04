"""PaddleOCR (PP-OCRv5/v6) running locally. Install with `uv sync --extra ocr`."""

import logging
import os
import threading

import numpy as np
from django.conf import settings

from .base import OCRError, OCRLine

logger = logging.getLogger(__name__)

# Without this, PaddleX queries the model servers on every startup.
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


class PaddleOCREngine:
    name = "paddleocr"
    _instance: "PaddleOCREngine | None" = None
    _lock = threading.Lock()

    def __init__(self, lang: str, device: str, det_model: str = "", rec_model: str = ""):
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise OCRError("PaddleOCR is not installed. Run: uv sync --extra ocr") from exc

        kwargs = {}
        if det_model or rec_model:
            kwargs.update(text_detection_model_name=det_model or None, text_recognition_model_name=rec_model or None)
        else:
            kwargs["lang"] = lang
        try:
            self._ocr = PaddleOCR(
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                device=device,
                # oneDNN in Paddle 3.x can raise NotImplementedError on CPU
                enable_mkldnn=False,
                **kwargs,
            )
        except Exception as exc:
            raise OCRError(f"Failed to start PaddleOCR: {exc}") from exc

    @classmethod
    def shared(cls) -> "PaddleOCREngine":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(
                    settings.OCR_LANG, settings.OCR_DEVICE, settings.OCR_DET_MODEL, settings.OCR_REC_MODEL
                )
            return cls._instance

    def recognize(self, image) -> list[OCRLine]:
        try:
            result = self._ocr.predict(np.asarray(image.convert("RGB"))[:, :, ::-1])
        except Exception as exc:
            raise OCRError(f"PaddleOCR error: {exc}") from exc
        lines: list[OCRLine] = []
        for page in result:
            for text, score, poly in zip(page["rec_texts"], page["rec_scores"], page["rec_polys"]):
                pts = np.asarray(poly)
                lines.append(
                    OCRLine(
                        text=str(text).strip(),
                        score=float(score),
                        x0=float(pts[:, 0].min()),
                        y0=float(pts[:, 1].min()),
                        x1=float(pts[:, 0].max()),
                        y1=float(pts[:, 1].max()),
                    )
                )
        return lines
