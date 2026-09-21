"""Unit tests for the matching engine. These don't touch OCR/vision at all,
so they run anywhere with just the stdlib + pydantic.

Run with: python -m pytest backend/tests
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.extraction.base import ExtractedLabel  # noqa: E402
from app.matching import build_review_result, compare_abv, compare_text_field, compare_warning  # noqa: E402
from app.models import ApplicationData, FieldStatus, OverallStatus  # noqa: E402
from app.warning_text import CANONICAL_WARNING  # noqa: E402


def test_exact_match():
    r = compare_text_field("brand_name", "OLD TOM DISTILLERY", "OLD TOM DISTILLERY")
    assert r.status == FieldStatus.MATCH


def test_case_difference_is_minor_not_mismatch():
    # Dave's example from the discovery notes.
    r = compare_text_field("brand_name", "STONE'S THROW", "Stone's Throw")
    assert r.status == FieldStatus.MATCH_MINOR_DIFF


def test_genuine_brand_mismatch():
    r = compare_text_field("brand_name", "OLD TOM DISTILLERY", "NEW TOM DISTILLERY COMPANY XYZ")
    assert r.status == FieldStatus.MISMATCH


def test_missing_field():
    r = compare_text_field("brand_name", "OLD TOM DISTILLERY", None)
    assert r.status == FieldStatus.MISSING


def test_abv_numeric_match_despite_formatting():
    r = compare_abv("45% Alc./Vol.", "45%  ALC/VOL (90 PROOF)")
    assert r.status in (FieldStatus.MATCH, FieldStatus.MATCH_MINOR_DIFF)


def test_abv_real_mismatch():
    r = compare_abv("45% Alc./Vol.", "40% Alc./Vol.")
    assert r.status == FieldStatus.MISMATCH


def test_warning_exact_canonical_match():
    label = ExtractedLabel(
        government_warning=CANONICAL_WARNING,
        warning_header_bold=True,
        warning_header_allcaps=True,
    )
    r = compare_warning(None, label)
    assert r.status == FieldStatus.MATCH


def test_warning_reworded_is_mismatch():
    label = ExtractedLabel(
        government_warning="GOVERNMENT WARNING: Drinking alcohol may be bad for your health.",
        warning_header_bold=True,
    )
    r = compare_warning(None, label)
    assert r.status == FieldStatus.MISMATCH


def test_warning_title_case_header_is_mismatch():
    label = ExtractedLabel(
        government_warning=CANONICAL_WARNING.replace("GOVERNMENT WARNING:", "Government Warning:"),
        warning_header_bold=True,
    )
    r = compare_warning(None, label)
    assert r.status == FieldStatus.MISMATCH


def test_warning_stray_space_before_colon_is_not_a_header_violation():
    # Caught via a degraded (dark + noisy) synthetic test image: OCR
    # occasionally inserts a space before the colon ("WARNING :") purely as
    # a character-segmentation artifact. That's not the same thing as
    # Jenny's real title-case complaint and shouldn't hard-fail a label
    # over OCR noise.
    label = ExtractedLabel(
        government_warning=CANONICAL_WARNING.replace("GOVERNMENT WARNING:", "GOVERNMENT WARNING :"),
        warning_header_bold=True,
        method="ocr",
    )
    r = compare_warning(None, label)
    assert r.status == FieldStatus.MATCH


def test_warning_title_case_with_stray_space_is_still_mismatch():
    # The colon-spacing fix must not accidentally launder a real case
    # violation just because OCR also added a stray space.
    label = ExtractedLabel(
        government_warning=CANONICAL_WARNING.replace("GOVERNMENT WARNING:", "Government Warning :"),
        warning_header_bold=True,
        method="ocr",
    )
    r = compare_warning(None, label)
    assert r.status == FieldStatus.MISMATCH


def test_warning_body_mismatch_via_ocr_is_needs_review_not_fail():
    # OCR's text detector can drop a whole line; treat a body-text deviation
    # under OCR as a signal for human review rather than an automatic fail,
    # since we can't yet tell "genuinely wrong" apart from "OCR missed a line".
    label = ExtractedLabel(
        government_warning="GOVERNMENT WARNING: (1) According to the Surgeon General, women should not "
        "drink alcoholic beverages during pregnancy because of the risk of birth defects. "
        "(2) Consumption of alcoholic health problems.",
        warning_header_bold=True,
        method="ocr",
    )
    r = compare_warning(None, label)
    assert r.status == FieldStatus.NEEDS_REVIEW


def test_warning_body_mismatch_via_vision_is_still_mismatch():
    # Vision reads the whole label holistically rather than via segmented
    # detection, so it's much less prone to dropping a line; keep it strict.
    label = ExtractedLabel(
        government_warning="GOVERNMENT WARNING: Drinking alcohol may be bad for your health.",
        warning_header_bold=True,
        method="vision",
    )
    r = compare_warning(None, label)
    assert r.status == FieldStatus.MISMATCH


def test_warning_unknown_bold_needs_review_if_text_matches():
    label = ExtractedLabel(government_warning=CANONICAL_WARNING, warning_header_bold=None)
    r = compare_warning(None, label)
    assert r.status == FieldStatus.NEEDS_REVIEW


def test_overall_pass():
    application = ApplicationData(
        brand_name="OLD TOM DISTILLERY",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol. (90 Proof)",
        net_contents="750 mL",
    )
    extracted = ExtractedLabel(
        brand_name="OLD TOM DISTILLERY",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol. (90 Proof)",
        net_contents="750 mL",
        government_warning=CANONICAL_WARNING,
        warning_header_bold=True,
        confidence=0.95,
        method="ocr",
    )
    result = build_review_result(application, extracted, processing_time_ms=100)
    assert result.overall_status == OverallStatus.PASS


def test_bottler_name_address_compared_when_provided():
    # Mandatory on every real label per TTB regs (27 CFR 5.66-5.68 for
    # spirits, 4.35 for wine, 7.66-7.68 for malt beverages) — confirmed via
    # ttb.gov's own mandatory-label-information checklists.
    application = ApplicationData(
        brand_name="OLD TOM DISTILLERY",
        class_type="Kentucky Straight Bourbon Whiskey",
        net_contents="750 mL",
        bottler_name_address="Produced and Bottled by Old Tom Distillery, Bardstown, KY",
    )
    extracted = ExtractedLabel(
        brand_name="OLD TOM DISTILLERY",
        class_type="Kentucky Straight Bourbon Whiskey",
        net_contents="750 mL",
        bottler_name_address="Produced and Bottled by Someone Else Entirely, Ohio",
        government_warning=CANONICAL_WARNING,
        warning_header_bold=True,
        method="ocr",
    )
    result = build_review_result(application, extracted, processing_time_ms=100)
    bottler_field = next(f for f in result.fields if f.field == "bottler_name_address")
    assert bottler_field.status == FieldStatus.MISMATCH
    assert result.overall_status == OverallStatus.FAIL


def test_bottler_name_address_not_checked_when_not_provided():
    # Left optional for backward compatibility with data collected before
    # this field existed (see models.py) — omitting it should not itself
    # produce a field result or count against the label.
    application = ApplicationData(
        brand_name="OLD TOM DISTILLERY",
        class_type="Kentucky Straight Bourbon Whiskey",
        net_contents="750 mL",
    )
    extracted = ExtractedLabel(
        brand_name="OLD TOM DISTILLERY",
        class_type="Kentucky Straight Bourbon Whiskey",
        net_contents="750 mL",
        government_warning=CANONICAL_WARNING,
        warning_header_bold=True,
        method="ocr",
    )
    result = build_review_result(application, extracted, processing_time_ms=100)
    assert not any(f.field == "bottler_name_address" for f in result.fields)
    assert result.overall_status == OverallStatus.PASS


def test_alcohol_content_optional_for_beer_without_one():
    # 27 CFR 7.65: mandatory for malt beverages only if the product contains
    # alcohol derived from added flavors/ingredients, or a state requires
    # it — otherwise a compliant beer label can have no ABV statement at
    # all. Omitting it from the application shouldn't be treated as a
    # missing/failed field the way it would for spirits or wine.
    application = ApplicationData(
        beverage_type="beer",
        brand_name="RIVER BEND BREWING",
        class_type="India Pale Ale",
        net_contents="12 fl. oz.",
    )
    extracted = ExtractedLabel(
        brand_name="RIVER BEND BREWING",
        class_type="India Pale Ale",
        net_contents="12 fl. oz.",
        government_warning=CANONICAL_WARNING,
        warning_header_bold=True,
        method="ocr",
    )
    result = build_review_result(application, extracted, processing_time_ms=100)
    assert not any(f.field == "alcohol_content" for f in result.fields)
    assert result.overall_status == OverallStatus.PASS


def test_ocr_missing_field_is_needs_review_not_fail():
    # OCR's text detector can miss a region entirely (e.g. a blurry photo);
    # that should prompt a human look, not an automatic compliance fail.
    application = ApplicationData(
        brand_name="OLD TOM DISTILLERY",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol.",
        net_contents="750 mL",
    )
    extracted = ExtractedLabel(
        brand_name="OLD TOM DISTILLERY",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol.",
        net_contents="750 mL",
        government_warning=None,  # not detected
        confidence=0.85,
        method="ocr",
    )
    result = build_review_result(application, extracted, processing_time_ms=100)
    assert result.overall_status == OverallStatus.NEEDS_REVIEW


def test_vision_missing_field_is_still_fail():
    application = ApplicationData(
        brand_name="OLD TOM DISTILLERY",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol.",
        net_contents="750 mL",
    )
    extracted = ExtractedLabel(
        brand_name="OLD TOM DISTILLERY",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol.",
        net_contents="750 mL",
        government_warning=None,
        confidence=0.95,
        method="vision",
    )
    result = build_review_result(application, extracted, processing_time_ms=100)
    assert result.overall_status == OverallStatus.FAIL


def test_overall_fail_on_bad_warning():
    application = ApplicationData(
        brand_name="OLD TOM DISTILLERY",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol.",
        net_contents="750 mL",
    )
    extracted = ExtractedLabel(
        brand_name="OLD TOM DISTILLERY",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol.",
        net_contents="750 mL",
        government_warning="Government Warning: drink responsibly.",
        warning_header_bold=True,
        confidence=0.95,
        method="ocr",
    )
    result = build_review_result(application, extracted, processing_time_ms=100)
    assert result.overall_status == OverallStatus.FAIL
