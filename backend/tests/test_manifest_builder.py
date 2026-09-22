"""Unit tests for the rule-based application-text parser. These don't
touch the AI path at all, so they run anywhere with just the stdlib +
pydantic, same philosophy as test_matching.py.

Run with: python -m pytest backend/tests
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.manifest_builder import build_applications, files_to_chunks, split_blob  # noqa: E402


def test_split_blob_numbers_in_order():
    raw = "Brand Name: A\n\nBrand Name: B\n\nBrand Name: C"
    chunks = split_blob(raw)
    assert [c[0] for c in chunks] == ["1", "2", "3"]
    assert chunks[1][1] == "Brand Name: B"


def test_split_blob_ignores_extra_blank_lines():
    raw = "Brand Name: A\n\n\n\nBrand Name: B"
    chunks = split_blob(raw)
    assert len(chunks) == 2


def test_split_blob_single_application_no_blank_lines():
    raw = "Brand Name: Solo Brand\nClass/Type: Vodka\nNet Contents: 750 mL"
    chunks = split_blob(raw)
    assert len(chunks) == 1
    assert chunks[0][0] == "1"


def test_files_to_chunks_uses_stem_sorted_by_filename():
    files = [("b_brand.txt", b"Brand Name: B"), ("a_brand.txt", b"Brand Name: A")]
    chunks = files_to_chunks(files)
    assert [c[0] for c in chunks] == ["a_brand", "b_brand"]


def test_rule_based_parses_well_labeled_application():
    text = (
        "Brand Name: OLD TOM DISTILLERY\n"
        "Class/Type: Kentucky Straight Bourbon Whiskey\n"
        "Alcohol Content: 45% Alc./Vol. (90 Proof)\n"
        "Net Contents: 750 mL\n"
        "Bottled By: Old Tom Distillery, Bardstown, KY\n"
    )
    [result] = build_applications([("1", text)], method="rules")
    assert result.error is None
    assert result.application.brand_name == "OLD TOM DISTILLERY"
    assert result.application.class_type == "Kentucky Straight Bourbon Whiskey"
    assert result.application.alcohol_content == "45% Alc./Vol. (90 Proof)"
    assert result.application.net_contents == "750 mL"
    assert result.application.bottler_name_address == "Old Tom Distillery, Bardstown, KY"
    assert result.warnings == []


def test_rule_based_tolerates_label_phrasing_variants():
    text = (
        "Brand: Riverbend\n"
        "Type: Blended Whiskey\n"
        "ABV: 40%\n"
        "Contents: 750 mL\n"
    )
    [result] = build_applications([("1", text)], method="rules")
    assert result.error is None
    assert result.application.brand_name == "Riverbend"
    assert result.application.class_type == "Blended Whiskey"
    assert result.application.alcohol_content == "40%"


def test_rule_based_missing_required_field_is_an_error_not_a_crash():
    text = "Class/Type: Vodka\nNet Contents: 750 mL\n"  # no brand name
    [result] = build_applications([("1", text)], method="rules")
    assert result.application is None
    assert result.error is not None
    assert "brand_name" in result.error


def test_rule_based_missing_bottler_is_a_warning_not_an_error():
    text = "Brand Name: X\nClass/Type: Y\nNet Contents: 750 mL\n"
    [result] = build_applications([("1", text)], method="rules")
    assert result.error is None
    assert any("bottler" in w.lower() for w in result.warnings)


def test_rule_based_beverage_type_alias_normalized():
    text = "Beverage Type: Malt Beverage\nBrand Name: X\nClass/Type: IPA\nNet Contents: 12 fl. oz.\n"
    [result] = build_applications([("1", text)], method="rules")
    assert result.application.beverage_type.value == "beer"


def test_rule_based_unrecognized_beverage_type_defaults_to_spirits():
    text = "Beverage Type: Mystery Juice\nBrand Name: X\nClass/Type: Y\nNet Contents: 750 mL\n"
    [result] = build_applications([("1", text)], method="rules")
    assert result.application.beverage_type.value == "spirits"


def test_build_applications_processes_multiple_chunks_independently():
    chunks = [
        ("1", "Brand Name: A\nClass/Type: Vodka\nNet Contents: 750 mL\n"),
        ("2", "Class/Type: Only\n"),  # missing brand + net contents
    ]
    results = build_applications(chunks, method="rules")
    assert results[0].error is None
    assert results[1].error is not None


def test_unknown_parse_method_raises():
    import pytest
    with pytest.raises(ValueError):
        build_applications([("1", "Brand Name: X\nClass/Type: Y\nNet Contents: 1L\n")], method="nonsense")
