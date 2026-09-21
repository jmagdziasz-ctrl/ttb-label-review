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
    """What the applicant submitted on the COLA application form."""
    beverage_type: BeverageType = BeverageType.spirits
    brand_name: str
    class_type: str
    alcohol_content: str = Field(..., description="e.g. '45% Alc./Vol.' or '90 Proof'")
    net_contents: str = Field(..., description="e.g. '750 mL'")
    government_warning: Optional[str] = Field(
        None, description="Leave blank to compare against the standard statutory text."
    )
    country_of_origin: Optional[str] = None


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
