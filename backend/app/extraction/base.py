"""Common interface every label-extraction backend implements."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ExtractedLabel:
    """Structured fields pulled off a label image, however that was done."""

    brand_name: Optional[str] = None
    class_type: Optional[str] = None
    alcohol_content: Optional[str] = None
    net_contents: Optional[str] = None
    government_warning: Optional[str] = None
    country_of_origin: Optional[str] = None

    raw_text: str = ""
    confidence: Optional[float] = None  # 0-1, method-specific meaning
    warning_header_bold: Optional[bool] = None  # None = "couldn't tell"
    warning_header_allcaps: Optional[bool] = None
    method: str = "unknown"
    notes: list[str] = field(default_factory=list)


class LabelExtractor:
    """Abstract extractor. Subclasses implement extract()."""

    method_name = "unknown"

    def extract(self, image_bytes: bytes) -> ExtractedLabel:
        raise NotImplementedError
