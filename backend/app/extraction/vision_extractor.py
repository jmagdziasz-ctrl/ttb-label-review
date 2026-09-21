"""Higher-accuracy extraction using Claude's vision capability.

Unlike OCR, a multimodal model can reason about layout and styling
directly, so it can tell us, e.g., whether "GOVERNMENT WARNING:" is
actually bold and all-caps rather than us having to guess. It also
handles skewed/glare-y photos far better than Tesseract (see Jenny's
feedback in the discovery notes).

Requires ANTHROPIC_API_KEY to be set. Costs money and adds network
latency per call, so it's opt-in rather than the default for this
prototype (see README for the tradeoff discussion).
"""
from __future__ import annotations

import base64
import json
import os

from .base import ExtractedLabel, LabelExtractor

try:
    import anthropic
except ImportError:  # pragma: no cover
    anthropic = None

MODEL = os.environ.get("TTB_VISION_MODEL", "claude-sonnet-5")

EXTRACTION_PROMPT = """You are looking at a photo of an alcohol beverage label (beer, wine, or spirits) submitted as part of a TTB COLA (Certificate of Label Approval) application.

Extract exactly the following fields as they appear on the label, as literally as possible (do not correct spelling, casing, or punctuation - transcribe exactly what's printed):

- brand_name
- class_type (the class/type designation, e.g. "Kentucky Straight Bourbon Whiskey")
- alcohol_content (transcribe exactly, e.g. "45% Alc./Vol. (90 Proof)")
- net_contents (e.g. "750 mL")
- government_warning (the FULL text of the government warning statement, transcribed exactly, verbatim, including punctuation)
- country_of_origin (if present, else null)
- bottler_name_address (the full name-and-address statement, e.g. "Bottled by Old Tom Distillery, Bardstown, KY" — this is a required disclosure on real labels, transcribe it exactly if present, else null)
- warning_header_bold (true/false: is the text "GOVERNMENT WARNING:" rendered in bold type?)
- warning_header_allcaps (true/false: is "GOVERNMENT WARNING:" rendered in all capital letters?)
- confidence (0.0-1.0: your confidence that you read this label correctly - lower it for blur, glare, extreme angle, tiny text, or partial occlusion)
- notes (array of short strings flagging anything ambiguous, unreadable, or unusual)

Respond with ONLY a single JSON object with exactly these keys, no other text, no markdown fences."""


class VisionExtractor(LabelExtractor):
    method_name = "vision"

    def __init__(self):
        if anthropic is None:
            raise RuntimeError("anthropic package not installed. Run: pip install anthropic")
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set.")
        self._client = anthropic.Anthropic(api_key=api_key)

    def extract(self, image_bytes: bytes) -> ExtractedLabel:
        media_type = _guess_media_type(image_bytes)
        b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

        response = self._client.messages.create(
            model=MODEL,
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": media_type, "data": b64},
                        },
                        {"type": "text", "text": EXTRACTION_PROMPT},
                    ],
                }
            ],
        )
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        text = _strip_code_fence(text)

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Vision model returned non-JSON output: {text[:200]}") from exc

        return ExtractedLabel(
            brand_name=parsed.get("brand_name"),
            class_type=parsed.get("class_type"),
            alcohol_content=parsed.get("alcohol_content"),
            net_contents=parsed.get("net_contents"),
            government_warning=parsed.get("government_warning"),
            country_of_origin=parsed.get("country_of_origin"),
            bottler_name_address=parsed.get("bottler_name_address"),
            raw_text=text,
            confidence=parsed.get("confidence"),
            warning_header_bold=parsed.get("warning_header_bold"),
            warning_header_allcaps=parsed.get("warning_header_allcaps"),
            method=self.method_name,
            notes=list(parsed.get("notes") or []),
        )


def _guess_media_type(image_bytes: bytes) -> str:
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if image_bytes[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _strip_code_fence(text: str) -> str:
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    return text.strip()
