"""Pydantic models for requests/responses."""
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class BeverageType(str, Enum):
    beer = "beer"
    wine = "wine"
    spirits = "spirits"


class FieldStatus(str, Enum):
    MATCH = "match"                    # exact or normalized-equivalent match
    MATCH_MINOR_DIFF = "match_minor"   # matches but formatting/casing differs (Dave's case)
    MISMATCH = "mismatch"              # genuine discrepancy
    NEEDS_REVIEW = "needs_review"      # couldn't be verified automatically (e.g. bold, low OCR confidence)
    MISSING = "missing"                # field not found on label at all


class ApplicationData(BaseModel):
    """What the applicant submitted on the COLA application form.

    Field requiredness intentionally mirrors TTB's own rules (see
    matching.py and the README's TTB-requirements-coverage section), not
    just what's convenient: alcohol content is optional here because it's
    only conditionally mandatory for malt beverages (27 CFR 7.65 — required
    only if the beer contains alcohol derived from added flavors/
    ingredients, or a state requires it; otherwise it's legitimately absent
    from a compliant beer label). Bottler/importer name and address is
    mandatory on every real label (27 CFR 5.66-5.68 for spirits, 4.35 for
    wine, 7.66-7.68 for malt beverages) but is left optional here so
    existing sample data collected before this field existed doesn't
    become invalid.
    """
    beverage_type: BeverageType = BeverageType.spirits
    brand_name: str
    class_type: str
    alcohol_content: Optional[str] = Field(
        None, description="e.g. '45% Alc./Vol.' or '90 Proof'. Optional: not always "
        "mandatory for malt beverages (27 CFR 7.65) — leave blank if the application doesn't state one."
    )
    net_contents: str = Field(..., description="e.g. '750 mL'")
    government_warning: Optional[str] = Field(
        None, description="Leave blank to compare against the standard statutory text."
    )
    country_of_origin: Optional[str] = None
    bottler_name_address: Optional[str] = Field(
        None, description="e.g. 'Bottled by Old Tom Distillery, Bardstown, KY'. Mandatory on "
        "every real label per TTB regulations; left optional here only for backward "
        "compatibility with data collected before this field existed."
    )


class FieldResult(BaseModel):
    field: str
    submitted_value: Optional[str]
    extracted_value: Optional[str]
    status: FieldStatus
    note: str = ""


class OverallStatus(str, Enum):
    PASS = "pass"
    NEEDS_REVIEW = "needs_review"
    FAIL = "fail"


class ReviewResult(BaseModel):
    filename: Optional[str] = None
    overall_status: OverallStatus
    fields: list[FieldResult]
    extraction_method: str
    extraction_confidence: Optional[float] = None
    processing_time_ms: int
    warnings: list[str] = []
