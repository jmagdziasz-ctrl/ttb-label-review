"""Canonical text for the federal alcohol beverage Government Warning statement.

Source: 27 U.S.C. 215 / 27 CFR 16.21. The statute fixes this wording verbatim;
labels may not paraphrase it. The header "GOVERNMENT WARNING:" is required to
appear in capital letters and in bold type.
"""

WARNING_HEADER = "GOVERNMENT WARNING:"

WARNING_BODY = (
    "(1) According to the Surgeon General, women should not drink alcoholic "
    "beverages during pregnancy because of the risk of birth defects. "
    "(2) Consumption of alcoholic beverages impairs your ability to drive a "
    "car or operate machinery, and may cause health problems."
)

CANONICAL_WARNING = f"{WARNING_HEADER} {WARNING_BODY}"
