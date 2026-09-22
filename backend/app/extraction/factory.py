"""Picks an extraction backend: Claude Vision if an API key is configured
(either server-side via ANTHROPIC_API_KEY, or supplied by the caller per
request - see main.py's X-Anthropic-Api-Key header), otherwise the
free/offline OCR extractor. A request can also force a specific method via
?method=ocr|vision for testing/demo purposes.
"""
from __future__ import annotations

import os

from .base import LabelExtractor
from .ocr_extractor import OcrExtractor, OcrUnavailableError
from .vision_extractor import VisionExtractor

_cache: dict[str, LabelExtractor] = {}


def default_method(api_key: str | None = None) -> str:
    return "vision" if (api_key or os.environ.get("ANTHROPIC_API_KEY")) else "ocr"


def get_extractor(method: str | None = None, api_key: str | None = None) -> LabelExtractor:
    method = method or default_method(api_key)
    if method == "vision" and api_key:
        # A caller-supplied key is per-request (potentially a different key
        # from a different person on every call) - it must never be cached
        # or reused for a different request, unlike the server's own
        # default-key client below.
        return VisionExtractor(api_key=api_key)
    if method not in _cache:
        if method == "vision":
            _cache[method] = VisionExtractor()
        elif method == "ocr":
            _cache[method] = OcrExtractor()
        else:
            raise ValueError(f"Unknown extraction method: {method}")
    return _cache[method]


__all__ = ["get_extractor", "default_method", "OcrUnavailableError"]
