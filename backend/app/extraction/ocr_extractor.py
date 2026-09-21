"""Free, fully offline extraction using RapidOCR (an ONNX-based OCR engine
distributed as a pure Python wheel — no system binary like Tesseract needs
to be separately installed).

That matters specifically for this deployment target: Marcus's notes
describe locked-down government infrastructure where installing arbitrary
executables is a whole process and outbound network calls to third-party
ML endpoints get blocked by the firewall. A `pip install`-only, fully
offline OCR path sidesteps both problems, which is why it's the default
extractor rather than an afterthought.

RapidOCR gives us a list of (quadrilateral box, text, confidence) per
detected text line — already grouped into lines, unlike raw Tesseract
word output. We still don't get "this is the brand name" for free, so we
use a layout heuristic: the topmost non-warning, non-quantity line of
text is assumed to be the brand name, and the next one down is the
class/type designation, since that ordering is near-universal on TTB
labels. (We deliberately do NOT use text height/font-size as the signal,
even though that seems intuitive: an all-caps brand name has no
ascenders or descenders, so its measured bounding-box height can come out
*shorter* than a smaller, mixed-case class/type line below it — that
false signal was caught during testing and swapped brand/class on
several samples.) Alcohol content, net contents, and the government
warning are recovered via regex over the concatenated text instead, since
those follow fairly fixed vocabulary regardless of layout.
"""
from __future__ import annotations

import io
import re

import numpy as np
from PIL import Image

from .base import ExtractedLabel, LabelExtractor

try:
    from rapidocr_onnxruntime import RapidOCR
except ImportError:  # pragma: no cover
    RapidOCR = None


class OcrUnavailableError(RuntimeError):
    pass


ABV_RE = re.compile(
    r"(\d{1,2}(?:\.\d+)?)\s*%\s*(?:alc(?:ohol)?\.?\s*/?\s*vol\.?)?", re.IGNORECASE
)
PROOF_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*proof", re.IGNORECASE)
NET_CONTENTS_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(mL|ml|ML|L|l|liters?|fl\.?\s*oz\.?)", re.IGNORECASE
)
WARNING_START_RE = re.compile(r"GOVERNMENT\s+WARNING\s*:?", re.IGNORECASE)

_engine = None  # module-level singleton; loading the ONNX models is slow (~seconds)


def _patch_onnx_single_threaded() -> None:
    """RapidOCR's recognizer runs its (small, cheap) model once per detected
    text line. ONNX Runtime's default execution plan spins up a multi-thread
    pool per session sized to the machine's core count — for a model this
    small, the thread synchronization overhead measured *higher* than the
    compute it was supposed to save: ~5-9s/label with default threading vs.
    ~2-5s/label single-threaded, same machine, same images (see README for
    the full before/after numbers). RapidOCR doesn't expose a thread-count
    kwarg, so this patches the SessionOptions class it uses before it builds
    its inference sessions.
    """
    from rapidocr_onnxruntime import utils as rapidocr_utils

    Original = rapidocr_utils.SessionOptions
    if getattr(Original, "_ttb_single_threaded", False):
        return  # already patched (e.g. a second extractor instance)

    class SingleThreadedSessionOptions(Original):
        _ttb_single_threaded = True

        def __init__(self):
            super().__init__()
            self.intra_op_num_threads = 1
            self.inter_op_num_threads = 1

    rapidocr_utils.SessionOptions = SingleThreadedSessionOptions


def _get_engine():
    global _engine
    if RapidOCR is None:
        raise OcrUnavailableError(
            "rapidocr-onnxruntime is not installed. Run: pip install rapidocr-onnxruntime "
            "(or set ANTHROPIC_API_KEY to use the Claude Vision extractor instead)."
        )
    if _engine is None:
        _patch_onnx_single_threaded()
        # Angle classification (detecting 180°-upside-down text) is skipped:
        # it's a whole extra model pass per text line, and label photos are
        # taken deliberately right-side up by an agent, not randomly rotated —
        # measured negligible (<0.1s) benefit against a real time cost.
        _engine = RapidOCR(use_angle_cls=False)
    return _engine


def _line_top(box) -> float:
    return min(pt[1] for pt in box)


def _find_warning(full_text: str) -> str | None:
    match = WARNING_START_RE.search(full_text)
    if not match:
        return None
    return full_text[match.start():match.start() + 500].strip()


class OcrExtractor(LabelExtractor):
    method_name = "ocr"

    def __init__(self):
        _get_engine()  # fail fast at startup if the engine can't load

    def extract(self, image_bytes: bytes) -> ExtractedLabel:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        engine = _get_engine()
        ocr_result, _ = engine(np.array(image))
        ocr_result = ocr_result or []

        lines = [{"text": text, "top": _line_top(box), "confidence": float(conf)}
                 for box, text, conf in ocr_result]
        full_text = "\n".join(l["text"] for l in lines)
        confs = [l["confidence"] for l in lines]
        overall_conf = sum(confs) / len(confs) if confs else 0.0

        result = ExtractedLabel(raw_text=full_text, confidence=overall_conf, method=self.method_name)

        candidate_lines = [
            l for l in lines
            if not WARNING_START_RE.search(l["text"])
            and len(l["text"]) >= 3
            and not NET_CONTENTS_RE.fullmatch(l["text"].strip())
        ]
        candidate_lines.sort(key=lambda l: l["top"])
        if candidate_lines:
            result.brand_name = candidate_lines[0]["text"]
            result.notes.append("Brand name inferred from the topmost text on the label (layout heuristic).")
        if len(candidate_lines) > 1:
            result.class_type = candidate_lines[1]["text"]
            result.notes.append("Class/type inferred from the next line down (layout heuristic).")

        abv_match = ABV_RE.search(full_text)
        proof_match = PROOF_RE.search(full_text)
        if abv_match and proof_match:
            result.alcohol_content = f"{abv_match.group(0).strip()} ({proof_match.group(0).strip()})"
        elif abv_match:
            result.alcohol_content = abv_match.group(0).strip()
        elif proof_match:
            result.alcohol_content = proof_match.group(0).strip()

        net_match = NET_CONTENTS_RE.search(full_text)
        if net_match:
            result.net_contents = net_match.group(0).strip()

        warning = _find_warning(full_text)
        if warning:
            result.government_warning = warning
            result.warning_header_allcaps = "GOVERNMENT WARNING" in full_text  # case-sensitive check
            result.warning_header_bold = None  # OCR text extraction can't tell us this
            result.notes.append(
                "Bold formatting of the warning header cannot be verified by OCR; confirm visually."
            )

        if overall_conf < 0.6:
            result.notes.append(
                f"Low OCR confidence ({overall_conf:.0%}). Image may be blurry, angled, or low-resolution."
            )

        return result
