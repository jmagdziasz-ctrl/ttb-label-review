"""Field-by-field comparison between the submitted application data and
what was actually extracted from the label image.

Design principle straight from the discovery notes (Dave's "STONE'S THROW"
vs "Stone's Throw" complaint): a difference that is purely casing,
punctuation, or whitespace is not the same thing as a substantive
mismatch. We normalize before deciding something is wrong, but we still
surface the raw difference to the agent as a note rather than silently
hiding it, so a human stays in the loop on borderline calls.

The government warning is the one field where we do NOT apply fuzzy
tolerance: the statute requires the exact statutory wording, so any
deviation there is flagged as a mismatch, not smoothed over.
"""
from __future__ import annotations

import difflib
import re

from .models import ApplicationData, FieldResult, FieldStatus, OverallStatus, ReviewResult
from .extraction.base import ExtractedLabel
from .warning_text import CANONICAL_WARNING, WARNING_HEADER

EXACT_THRESHOLD = 0.97
MINOR_DIFF_THRESHOLD = 0.80


def _normalize_loose(text: str) -> str:
    text = text.upper()
    text = re.sub(r"[^\w%./]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def compare_text_field(field: str, submitted: str | None, extracted: str | None) -> FieldResult:
    if not extracted:
        return FieldResult(
            field=field, submitted_value=submitted, extracted_value=None,
            status=FieldStatus.MISSING, note="We couldn't find this on the label.",
        )
    if not submitted:
        return FieldResult(
            field=field, submitted_value=submitted, extracted_value=extracted,
            status=FieldStatus.NEEDS_REVIEW, note="The application didn't include this, so there's nothing to compare it to.",
        )

    if submitted.strip() == extracted.strip():
        return FieldResult(field=field, submitted_value=submitted, extracted_value=extracted, status=FieldStatus.MATCH)

    loose_a, loose_b = _normalize_loose(submitted), _normalize_loose(extracted)
    if loose_a == loose_b:
        return FieldResult(
            field=field, submitted_value=submitted, extracted_value=extracted,
            status=FieldStatus.MATCH_MINOR_DIFF,
            note="This matches — the only difference is things like capital letters, punctuation, or spacing, not the actual wording.",
        )

    # A blurry/low-quality photo can make our reader run words together or
    # split them oddly (e.g. "OLD TOM DISTILLERY" read as "OLDTOMDISTILLERY")
    # without actually misreading any letters. That's still purely a spacing
    # difference in spirit, just one _normalize_loose (which only collapses
    # repeated whitespace, not missing whitespace) doesn't catch - so check
    # again with spaces removed entirely before falling back to fuzzy scoring.
    if loose_a.replace(" ", "") == loose_b.replace(" ", ""):
        return FieldResult(
            field=field, submitted_value=submitted, extracted_value=extracted,
            status=FieldStatus.MATCH_MINOR_DIFF,
            note="This matches — our reader just ran some words together or split them oddly (common on a blurry photo), not a real wording difference.",
        )

    ratio = _similarity(loose_a, loose_b)
    if ratio >= EXACT_THRESHOLD:
        return FieldResult(
            field=field, submitted_value=submitted, extracted_value=extracted,
            status=FieldStatus.MATCH_MINOR_DIFF,
            note=f"Nearly identical (a {ratio:.0%} match) — likely just a small reading error, not a real difference.",
        )
    if ratio >= MINOR_DIFF_THRESHOLD:
        return FieldResult(
            field=field, submitted_value=submitted, extracted_value=extracted,
            status=FieldStatus.NEEDS_REVIEW,
            note=f"Similar, but not a clear match (about {ratio:.0%} alike) — a person should take a look.",
        )
    return FieldResult(
        field=field, submitted_value=submitted, extracted_value=extracted,
        status=FieldStatus.MISMATCH, note=f"This looks like a real difference — only about {ratio:.0%} similar to what the application says.",
    )


_PCT_RE = re.compile(r"(\d{1,2}(?:\.\d+)?)\s*%")
_PROOF_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*proof", re.IGNORECASE)


def compare_abv(submitted: str | None, extracted: str | None) -> FieldResult:
    base = compare_text_field("alcohol_content", submitted, extracted)
    if base.status not in (FieldStatus.MISMATCH, FieldStatus.NEEDS_REVIEW):
        return base
    if not submitted or not extracted:
        return base

    sub_pct = _PCT_RE.search(submitted)
    ext_pct = _PCT_RE.search(extracted)
    if sub_pct and ext_pct:
        diff = abs(float(sub_pct.group(1)) - float(ext_pct.group(1)))
        if diff <= 0.05:
            return FieldResult(
                field="alcohol_content", submitted_value=submitted, extracted_value=extracted,
                status=FieldStatus.MATCH_MINOR_DIFF,
                note="The alcohol percentage matches — only the wording around it (like \"Alc.\" vs \"Vol.\") looks different.",
            )
        return FieldResult(
            field="alcohol_content", submitted_value=submitted, extracted_value=extracted,
            status=FieldStatus.MISMATCH,
            note=f"The application says {sub_pct.group(1)}% but the label shows {ext_pct.group(1)}%.",
        )
    return base


def _internal_abv_consistency_note(extracted: str | None) -> str | None:
    """Sanity check the label against itself: 45% ABV should read 90 proof."""
    if not extracted:
        return None
    pct = _PCT_RE.search(extracted)
    proof = _PROOF_RE.search(extracted)
    if not (pct and proof):
        return None
    expected_proof = float(pct.group(1)) * 2
    actual_proof = float(proof.group(1))
    if abs(expected_proof - actual_proof) > 0.5:
        return (
            f"The label doesn't add up on its own: {pct.group(1)}% alcohol should be "
            f"about {expected_proof:g} proof, but the label says {proof.group(1)} proof."
        )
    return None


_NET_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(mL|ml|L|l|fl\.?\s*oz\.?)", re.IGNORECASE)
_TO_ML = {"ml": 1.0, "l": 1000.0, "floz": 29.5735, "fl oz": 29.5735}


def compare_net_contents(submitted: str | None, extracted: str | None) -> FieldResult:
    base = compare_text_field("net_contents", submitted, extracted)
    if base.status not in (FieldStatus.MISMATCH, FieldStatus.NEEDS_REVIEW):
        return base
    if not submitted or not extracted:
        return base

    sub_m = _NET_RE.search(submitted)
    ext_m = _NET_RE.search(extracted)
    if sub_m and ext_m:
        sub_ml = float(sub_m.group(1)) * _TO_ML.get(sub_m.group(2).lower().replace(".", "").replace(" ", ""), None) if sub_m.group(2).lower().replace(".", "").replace(" ", "") in _TO_ML else None
        ext_ml = float(ext_m.group(1)) * _TO_ML.get(ext_m.group(2).lower().replace(".", "").replace(" ", ""), None) if ext_m.group(2).lower().replace(".", "").replace(" ", "") in _TO_ML else None
        if sub_ml is not None and ext_ml is not None:
            if abs(sub_ml - ext_ml) < 0.5:
                return FieldResult(
                    field="net_contents", submitted_value=submitted, extracted_value=extracted,
                    status=FieldStatus.MATCH_MINOR_DIFF,
                    note="Same amount — it's just written with different units (like mL vs. L).",
                )
            return FieldResult(
                field="net_contents", submitted_value=submitted, extracted_value=extracted,
                status=FieldStatus.MISMATCH,
                note=f"The application says {submitted} but the label shows {extracted}.",
            )
    return base


def compare_warning(submitted: str | None, extracted: ExtractedLabel) -> FieldResult:
    target_text = submitted.strip() if submitted else CANONICAL_WARNING
    label_text = extracted.government_warning

    if not label_text:
        return FieldResult(
            field="government_warning", submitted_value=target_text, extracted_value=None,
            status=FieldStatus.MISSING, note="We couldn't find a government warning on the label.",
        )

    normalized_target = re.sub(r"\s+", " ", target_text).strip()
    normalized_label = re.sub(r"\s+", " ", label_text).strip()
    # A space OCR mistakenly inserts before the colon ("WARNING :") is a
    # character-segmentation artifact, not a real formatting choice by
    # whoever printed the label — collapsing it here keeps the header check
    # sensitive to what Jenny actually cares about (case, wording) without
    # hard-failing a label over OCR noise. Caught via a degraded synthetic
    # test image (dark + sensor noise) that would otherwise have gone from
    # "unreadable" to "incorrectly and confidently rejected."
    normalized_label = re.sub(r"\s+:", ":", normalized_label, count=1)

    notes = []
    status = FieldStatus.MATCH

    # Header capitalization is a reliable character-level check regardless of
    # extraction method (it isn't affected by OCR occasionally dropping a
    # whole line), so a deviation here is always treated as a hard mismatch —
    # this is exactly Jenny's "Government Warning" title-case rejection example.
    if not normalized_label.startswith(WARNING_HEADER):
        if normalized_label.upper().startswith(WARNING_HEADER.upper()):
            status = FieldStatus.MISMATCH
            notes.append(f'Header must read exactly "{WARNING_HEADER}" in all capital letters.')
        else:
            status = FieldStatus.MISMATCH
            notes.append(f'Required header "{WARNING_HEADER}" not found at the start of the statement.')

    if normalized_label != normalized_target:
        ratio = _similarity(normalized_label, normalized_target)
        text_note = (
            f"The wording doesn't exactly match the required government warning text "
            f"(about {ratio:.0%} similar). By law, this text has to be word-for-word exact."
        )
        if status != FieldStatus.MISMATCH:
            if extracted.method == "ocr":
                # OCR's text detector can silently miss an entire line (observed
                # during testing on an otherwise-compliant sample label), which
                # would otherwise show up here as a false "mismatch" on a label
                # that actually complies. Flag for a human instead of hard-failing
                # — a false FAIL erodes trust in the tool faster than a false
                # NEEDS_REVIEW does (see the scanning-vendor pilot in the
                # discovery notes: agents abandon tools that cry wolf).
                status = FieldStatus.NEEDS_REVIEW
                text_note += " Our automatic reader can sometimes miss a whole line of text, so please double-check by eye before rejecting this label."
            else:
                status = FieldStatus.MISMATCH
        notes.append(text_note)

    if extracted.warning_header_bold is False:
        status = FieldStatus.MISMATCH
        notes.append('"GOVERNMENT WARNING:" does not appear to be bold.')
    elif extracted.warning_header_bold is None:
        if status == FieldStatus.MATCH:
            status = FieldStatus.NEEDS_REVIEW
        notes.append("We couldn't automatically tell whether this is bold — please check by eye.")

    if not notes:
        notes.append("Matches the required legal wording exactly.")

    return FieldResult(
        field="government_warning", submitted_value=target_text, extracted_value=label_text,
        status=status, note=" ".join(notes),
    )


def build_review_result(
    application: ApplicationData,
    extracted: ExtractedLabel,
    processing_time_ms: int,
    filename: str | None = None,
) -> ReviewResult:
    fields = [
        compare_text_field("brand_name", application.brand_name, extracted.brand_name),
        compare_text_field("class_type", application.class_type, extracted.class_type),
        compare_net_contents(application.net_contents, extracted.net_contents),
        compare_warning(application.government_warning, extracted),
    ]
    # Alcohol content is only unconditionally mandatory for spirits and wine;
    # for malt beverages it's required only if the beer contains alcohol from
    # added flavors/ingredients or a state requires it (27 CFR 7.65) — a
    # compliant beer label can legitimately have no ABV statement at all. We
    # don't model that narrower trigger (would need data our application form
    # doesn't collect), so when the applicant didn't provide one, this field
    # is skipped rather than flagged — silence isn't a violation here.
    if application.alcohol_content:
        fields.append(compare_abv(application.alcohol_content, extracted.alcohol_content))
    if application.country_of_origin:
        fields.append(compare_text_field("country_of_origin", application.country_of_origin, extracted.country_of_origin))
    if application.bottler_name_address:
        fields.append(compare_text_field("bottler_name_address", application.bottler_name_address, extracted.bottler_name_address))

    # A "missing" field under OCR is ambiguous: it might genuinely be absent
    # from the label, or the OCR text detector might have simply failed to
    # find that region (observed directly during testing — see ocr_extractor.py).
    # We can't tell those apart automatically, so we don't auto-fail on it;
    # a human reviewer can tell at a glance which case they're looking at.
    if extracted.method == "ocr":
        for f in fields:
            if f.status == FieldStatus.MISSING:
                f.note = (f.note + " " if f.note else "") + (
                    "This might really be missing from the label, or our automatic reader "
                    "might have just missed it — please take a look yourself."
                )

    warnings = list(extracted.notes)
    consistency_note = _internal_abv_consistency_note(extracted.alcohol_content)
    if consistency_note:
        warnings.append(consistency_note)
    if extracted.confidence is not None and extracted.confidence < 0.6:
        warnings.append(
            "The photo quality made this hard to read (blurry, at an angle, glare, or low "
            "resolution), so the results below may be less reliable — worth a second look."
        )

    statuses = {f.status for f in fields}
    missing_is_hard_fail = extracted.method != "ocr"  # vision reads holistically; a lot less likely to silently drop content
    if FieldStatus.MISMATCH in statuses or (missing_is_hard_fail and FieldStatus.MISSING in statuses):
        overall = OverallStatus.FAIL
    elif (
        FieldStatus.NEEDS_REVIEW in statuses
        or FieldStatus.MISSING in statuses
        or (extracted.confidence is not None and extracted.confidence < 0.6)
    ):
        overall = OverallStatus.NEEDS_REVIEW
    else:
        overall = OverallStatus.PASS

    return ReviewResult(
        filename=filename,
        overall_status=overall,
        fields=fields,
        extraction_method=extracted.method,
        extraction_confidence=extracted.confidence,
        processing_time_ms=processing_time_ms,
        warnings=warnings,
    )
