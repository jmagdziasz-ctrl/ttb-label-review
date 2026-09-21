"""Picks an extraction backend: Claude Vision if an API key is configured,
otherwise the free/offline OCR extractor. A request can also force a
specific method via ?method=ocr|vision for testing/demo purposes.
"""
from __future__ import annotations

import os

from .base import LabelExtractor
from .ocr_extractor import OcrExtractor, OcrUnavailableError
from .vision_extractor import VisionExtractor

_cache: dict[str, LabelExtractor] = {}


def default_method() -> str:
    return "vision" if os.environ.get("ANTHROPIC_API_KEY") else "ocr"


def get_extractor(method: str | None = None) -> LabelExtractor:
    method = method or default_method()
    if method not in _cache:
        if method == "vision":
            _cache[method] = VisionExtractor()
        elif method == "ocr":
            _cache[method] = OcrExtractor()
        else:
            raise ValueError(f"Unknown extraction method: {method}")
    return _cache[method]


__all__ = ["get_extractor", "default_method", "OcrUnavailableError"]
