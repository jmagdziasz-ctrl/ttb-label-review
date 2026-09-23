"""Unit tests for CSV manifest parsing (manifest.py). No OCR/model/network
dependencies needed - pure CSV-to-ApplicationData logic.

Run with: python -m pytest backend/tests
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from app.manifest import CSV_TEMPLATE, ManifestError, parse_manifest  # noqa: E402
from app.models import ApplicationData  # noqa: E402

VALID_CSV = (
    "filename,beverage_type,brand_name,class_type,alcohol_content,net_contents,"
    "government_warning,country_of_origin,bottler_name_address\n"
    "1.png,spirits,OLD TOM DISTILLERY,Kentucky Straight Bourbon Whiskey,"
    "45% Alc./Vol.,750 mL,,,\"Bottled by Old Tom Distillery, Bardstown, KY\"\n"
    "2.png,wine,RIVERBEND CELLARS,Cabernet Sauvignon,,750 mL,,France,\n"
)


def test_parses_multiple_rows_in_order():
    rows = parse_manifest(VALID_CSV.encode("utf-8"))
    assert [fn for fn, _ in rows] == ["1.png", "2.png"]
    assert all(isinstance(app, ApplicationData) for _, app in rows)


def test_required_fields_populated_correctly():
    rows = parse_manifest(VALID_CSV.encode("utf-8"))
    _, app = rows[0]
    assert app.brand_name == "OLD TOM DISTILLERY"
    assert app.class_type == "Kentucky Straight Bourbon Whiskey"
    assert app.net_contents == "750 mL"
    assert app.beverage_type.value == "spirits"


def test_blank_optional_cells_become_none_not_empty_string():
    rows = parse_manifest(VALID_CSV.encode("utf-8"))
    _, app = rows[0]
    assert app.government_warning is None
    assert app.country_of_origin is None
    _, app2 = rows[1]
    assert app2.alcohol_content is None
    assert app2.bottler_name_address is None
    assert app2.country_of_origin == "France"


def test_beverage_type_defaults_to_spirits_when_blank():
    csv_bytes = (
        "filename,brand_name,class_type,net_contents\n"
        "x.png,BRAND,Type,750 mL\n"
    ).encode("utf-8")
    rows = parse_manifest(csv_bytes)
    assert rows[0][1].beverage_type.value == "spirits"


def test_empty_csv_raises():
    with pytest.raises(ManifestError, match="empty"):
        parse_manifest(b"")


def test_missing_required_column_raises():
    csv_bytes = "filename,brand_name\nx.png,BRAND\n".encode("utf-8")
    with pytest.raises(ManifestError, match="missing required column"):
        parse_manifest(csv_bytes)


def test_missing_filename_in_a_row_raises_with_row_number():
    csv_bytes = (
        "filename,brand_name,class_type,net_contents\n"
        ",BRAND,Type,750 mL\n"
    ).encode("utf-8")
    with pytest.raises(ManifestError, match="Row 2"):
        parse_manifest(csv_bytes)


def test_blank_brand_name_is_not_rejected_by_the_parser():
    # ApplicationData.brand_name is a plain `str` with no min_length, so an
    # empty value is technically valid Pydantic input and passes through
    # rather than raising here - it's the matching engine's job (already
    # covered in test_matching.py) to flag a blank/wrong value against the
    # label, not the manifest parser's. Documents this deliberately, so a
    # future reader isn't surprised parse_manifest lets it through.
    csv_bytes = (
        "filename,brand_name,class_type,net_contents\n"
        "x.png,,Kentucky Straight Bourbon Whiskey,750 mL\n"
    ).encode("utf-8")
    rows = parse_manifest(csv_bytes)
    assert rows[0][1].brand_name == ""


def test_invalid_beverage_type_raises_with_row_and_filename():
    # This is the one field in ApplicationData that *can* fail validation
    # through this path (an enum), so it's what actually exercises the
    # pydantic ValidationError -> ManifestError translation.
    csv_bytes = (
        "filename,beverage_type,brand_name,class_type,net_contents\n"
        "x.png,vodka-cooler,BRAND,Type,750 mL\n"
    ).encode("utf-8")
    with pytest.raises(ManifestError, match=r"Row 2 \(x\.png\)"):
        parse_manifest(csv_bytes)


def test_handles_utf8_bom():
    # Excel commonly saves CSVs with a BOM prefix - parse_manifest decodes
    # with utf-8-sig specifically to tolerate this.
    csv_bytes = b"\xef\xbb\xbf" + VALID_CSV.encode("utf-8")
    rows = parse_manifest(csv_bytes)
    assert len(rows) == 2
    assert rows[0][0] == "1.png"


def test_csv_template_itself_parses_successfully():
    # The template downloadable from the app should always be valid input
    # to the same parser, so a user filling it in starts from something
    # that actually works.
    rows = parse_manifest(CSV_TEMPLATE.encode("utf-8"))
    assert len(rows) == 1
    assert rows[0][0] == "old_tom_bourbon.png"
