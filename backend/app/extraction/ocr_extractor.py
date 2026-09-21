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

Because this image is part of an application a submitter sends in — not a
photo a TTB agent frames and takes themselves — it can arrive upside-down.
RapidOCR's angle classifier corrects each line's *text* for that, but not
the page layout our brand/class heuristic depends on, so `extract()` also
checks whether the government warning (always the label's last content
block) was found sitting above most other text; if so, it rotates the
whole image 180° and re-extracts. See `sample_labels/upside_down_label.png`
for a worked example.

Real-world submitted photos can also be dark, glare-y, or generally poor
quality (per Jenny's discovery-note complaint about agents having to
reject and ask for a re-shoot). Testing against synthetic dark/noisy/
glare-y labels found brand/class/ABV/net-contents survive that kind of
degradation almost every time on their own — RapidOCR's models are more
tolerant than expected — but the government warning (the longest, most
spatially spread-out text block) is the one field that can go completely
undetected. There's no single contrast fix that helps every case
(autocontrast recovered a noisy-dark sample but did nothing for a glare
sample; histogram equalization was the reverse, and made a different
noisy sample much worse by amplifying the noise) so rather than applying
one unconditionally and risking making a fine image worse, `extract()`
only reaches for these as a fallback, and only when the warning wasn't
found on the first pass — see `_recover_missing_warning()`.
"""
from __future__ import annotations

import io
import re
import statistics

import numpy as np
from PIL import Image, ImageOps

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
# Bottler/importer name and address is mandatory on every real label (27 CFR
# 5.66-5.68 for spirits, 4.35 for wine, 7.66-7.68 for malt beverages), and by
# regulation must immediately follow one of these phrases with no intervening
# text — so the phrase itself is a reliable anchor to search for.
BOTTLER_RE = re.compile(
    r"(?:Produced\s+and\s+Bottled\s+by|Bottled\s+by|Bottled\s+for|Imported\s+by|"
    r"Distributed\s+by)\s+(.+)",
    re.IGNORECASE,
)

# Tried in this order as a fallback when the warning isn't found on the
# first pass. Neither is a strict improvement over the other — each fixed
# a different failure mode in testing and made a different case worse — so
# these are only ever tried one at a time, stopping at the first success,
# never applied unconditionally. See module docstring.
_ENHANCEMENTS = {
    "autocontrast": lambda image: ImageOps.autocontrast(image, cutoff=1),
    "equalize": lambda image: ImageOps.equalize(image),
}

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
        # Angle classification (detecting 180°-upside-down text) stays ON:
        # this image is part of an application submitted to TTB, not a
        # photo an agent took themselves — a submitter could send it in
        # any orientation. Measured cost is negligible anyway (<0.1s), so
        # there's no real tradeoff to make here.
        _engine = RapidOCR()
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
        result, warning_top, other_tops_median = self._extract_from_image(image)

        # RapidOCR's angle classifier corrects each detected line's *text*
        # (so a word is still read right-way-round even upside-down), but it
        # does not correct the overall page layout our brand/class heuristic
        # relies on: a fully inverted submission still gets read top-to-bottom
        # in image coordinates, which is now bottom-to-top relative to the
        # real label. The government warning is a reliable anchor for
        # catching this — it's always the label's last content block, so on
        # a right-way-up label it should sit *below* most other text. If it's
        # sitting above the median of everything else instead, the page is
        # very likely upside-down; this image is an application attachment
        # from a submitter, not a photo an agent controls, so this is a real
        # case, not a hypothetical one.
        if warning_top is not None and other_tops_median is not None and warning_top <= other_tops_median:
            rotated = image.rotate(180)
            rotated_result, rotated_warning_top, rotated_other_median = self._extract_from_image(rotated)
            fixed = (
                rotated_warning_top is None
                or rotated_other_median is None
                or rotated_warning_top > rotated_other_median
            )
            if fixed:
                rotated_result.notes.insert(
                    0,
                    "Image appeared to be upside-down (the government warning was found above "
                    "most other label text); automatically rotated 180° and re-analyzed.",
                )
                image, result = rotated, rotated_result
            else:
                result.notes.append(
                    "This label's layout looks unusual (the government warning was found above "
                    "most other label text) — please confirm the image orientation manually."
                )

        if result.government_warning is None:
            result = self._recover_missing_warning(image, result)

        return result

    def _recover_missing_warning(self, image: Image.Image, original: ExtractedLabel) -> ExtractedLabel:
        """Testing against dark/noisy/glare-y synthetic labels found brand,
        class, ABV, and net contents almost always survive on the first
        pass, but the warning — the longest, most spread-out text block —
        can go completely undetected. Since no single contrast fix helped
        every case (see module docstring), each candidate is tried in turn
        against the *original* image, and the first one that actually finds
        the warning wins; this costs an extra OCR pass per candidate, but
        only for images that already failed to find the warning, not the
        common case.

        Only the warning-related fields are taken from the enhanced pass —
        everything else (brand, class, confidence, ...) stays from the
        original extraction. An enhancement that recovers the warning can
        still quietly hurt some other field (equalize did exactly this to
        brand-name accuracy on one glare-test image while fixing the
        warning), so there's no reason to risk fields that already read
        correctly just because one field didn't.
        """
        for name, transform in _ENHANCEMENTS.items():
            enhanced_result, _, _ = self._extract_from_image(transform(image))
            if enhanced_result.government_warning is not None:
                original.government_warning = enhanced_result.government_warning
                original.warning_header_allcaps = enhanced_result.warning_header_allcaps
                original.warning_header_bold = enhanced_result.warning_header_bold
                original.notes.append(
                    "Bold formatting of the warning header cannot be verified by OCR; confirm visually."
                )
                original.notes.insert(
                    0,
                    f"Government warning wasn't found on the original image; recovered after "
                    f"enhancing image contrast ({name}). Original image quality may be marginal — "
                    "consider requesting a clearer photo.",
                )
                return original
        return original

    def _extract_from_image(self, image: Image.Image) -> tuple[ExtractedLabel, float | None, float | None]:
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

        bottler_match = BOTTLER_RE.search(full_text)
        if bottler_match:
            # Capture just the matched line, not everything after it — the
            # regex's `.+` is greedy and full_text is newline-joined, but a
            # bare `.` doesn't cross newlines, so this naturally stops at
            # end of line.
            result.bottler_name_address = bottler_match.group(0).strip()

        warning_top: float | None = None
        warning_line = next((l for l in lines if WARNING_START_RE.search(l["text"])), None)
        warning = _find_warning(full_text)
        if warning:
            result.government_warning = warning
            result.warning_header_allcaps = "GOVERNMENT WARNING" in full_text  # case-sensitive check
            result.warning_header_bold = None  # OCR text extraction can't tell us this
            result.notes.append(
                "Bold formatting of the warning header cannot be verified by OCR; confirm visually."
            )
            if warning_line is not None:
                warning_top = warning_line["top"]

        if overall_conf < 0.6:
            result.notes.append(
                f"Low OCR confidence ({overall_conf:.0%}). Image may be blurry, angled, or low-resolution."
            )

        other_tops = [l["top"] for l in lines if l is not warning_line]
        other_tops_median = statistics.median(other_tops) if other_tops else None
        return result, warning_top, other_tops_median
