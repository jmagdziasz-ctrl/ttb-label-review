"""CSV manifest parsing for batch review.

Batch mode pairs each label image with its submitted application data via
a manifest CSV, matched on filename. This mirrors how a real importer
would already have a spreadsheet of pending applications when they dump
200-300 labels on the review queue at once (Sarah's "Janet from Seattle"
scenario in the discovery notes).
"""
from __future__ import annotations

import csv
import io

from pydantic import ValidationError

from .models import ApplicationData

REQUIRED_COLUMNS = {"filename", "brand_name", "class_type", "alcohol_content", "net_contents"}


class ManifestError(Exception):
    pass


def parse_manifest(csv_bytes: bytes) -> list[tuple[str, ApplicationData]]:
    text = csv_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ManifestError("Manifest CSV appears to be empty.")

    missing = REQUIRED_COLUMNS - {c.strip() for c in reader.fieldnames}
    if missing:
        raise ManifestError(f"Manifest is missing required column(s): {', '.join(sorted(missing))}")

    rows: list[tuple[str, ApplicationData]] = []
    for i, row in enumerate(reader, start=2):  # header is row 1
        filename = (row.get("filename") or "").strip()
        if not filename:
            raise ManifestError(f"Row {i}: missing filename.")
        try:
            data = ApplicationData(
                beverage_type=(row.get("beverage_type") or "spirits").strip().lower(),
                brand_name=(row.get("brand_name") or "").strip(),
                class_type=(row.get("class_type") or "").strip(),
                alcohol_content=(row.get("alcohol_content") or "").strip(),
                net_contents=(row.get("net_contents") or "").strip(),
                government_warning=(row.get("government_warning") or "").strip() or None,
                country_of_origin=(row.get("country_of_origin") or "").strip() or None,
            )
        except ValidationError as exc:
            raise ManifestError(f"Row {i} ({filename}): {exc}") from exc
        rows.append((filename, data))
    return rows


CSV_TEMPLATE = (
    "filename,beverage_type,brand_name,class_type,alcohol_content,net_contents,"
    "government_warning,country_of_origin\n"
    "old_tom_bourbon.png,spirits,OLD TOM DISTILLERY,Kentucky Straight Bourbon Whiskey,"
    "45% Alc./Vol. (90 Proof),750 mL,,\n"
)
