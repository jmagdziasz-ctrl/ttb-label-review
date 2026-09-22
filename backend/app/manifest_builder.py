"""Turns raw application text into ApplicationData, without anyone having
to build a spreadsheet by hand.

The batch review flow (see manifest.py) needs one ApplicationData per
label, but the manifest CSV it expects is exactly the kind of clerical
busywork the discovery notes complain about — Sarah's agents already have
this data (it's sitting in COLA, or in whatever notes/emails they copied
it from), they shouldn't have to retype it into a spreadsheet with the
right column headers just to use this tool.

Two ways in, matching how people said they'd actually have the text:
  - One big pasted block with a blank line between each application
    (`split_blob`) — each gets a synthetic id: "1", "2", "3", ...
  - One file per application (`files_to_chunks`) — each keeps its own
    filename (minus extension) as its id, so a matching photo can just be
    named to the same stem with no renumbering step.

Either way, the caller ends up with a list of (id, text) chunks, and
`build_applications()` turns each chunk into an ApplicationData the same
way the OCR/Vision split works for label images: a free, offline,
regex-based parser by default, or a Claude-based one if ANTHROPIC_API_KEY
is set, for text that isn't consistently labeled. Neither backend invents
data — a field that can't be found is left blank (or the whole application
is flagged as unparseable if a required field is missing), the same
"don't guess, flag it" principle used everywhere else in this tool.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from .models import ApplicationData

try:
    import anthropic
except ImportError:  # pragma: no cover
    anthropic = None

MODEL = os.environ.get("TTB_VISION_MODEL", "claude-sonnet-5")

REQUIRED_FIELDS = ("brand_name", "class_type", "net_contents")


class ParsingUnavailableError(RuntimeError):
    pass


@dataclass
class ParsedApplication:
    id: str
    application: ApplicationData | None
    source_text: str
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


def split_blob(raw_text: str) -> list[tuple[str, str]]:
    """One big pasted block -> [("1", text), ("2", text), ...], split on
    blank lines. Applications are numbered in the order they appear."""
    blocks = re.split(r"\n\s*\n+", raw_text.strip())
    blocks = [b.strip() for b in blocks if b.strip()]
    return [(str(i + 1), b) for i, b in enumerate(blocks)]


def files_to_chunks(files: list[tuple[str, bytes]]) -> list[tuple[str, str]]:
    """One file per application -> [(stem, text), ...], sorted by filename
    so the order is predictable (matches what most file pickers show).
    Each file keeps its own name as its id, rather than being renumbered,
    so a same-stem photo pairs with it automatically."""
    chunks = []
    for filename, content in sorted(files, key=lambda item: item[0]):
        stem = Path(filename).stem
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("utf-8", errors="replace")
        chunks.append((stem, text))
    return chunks


def default_parse_method() -> str:
    return "ai" if os.environ.get("ANTHROPIC_API_KEY") else "rules"


def build_applications(chunks: list[tuple[str, str]], method: str | None = None) -> list[ParsedApplication]:
    effective = method or default_parse_method()
    if effective == "ai":
        return _parse_with_ai(chunks)
    if effective != "rules":
        raise ValueError(f"Unknown parse method: {effective}")
    return [_parse_one_rule_based(chunk_id, text) for chunk_id, text in chunks]


# --- Free, offline: line-by-line label matching -----------------------------

_BEVERAGE_TYPE_ALIASES = {
    "spirits": "spirits", "distilled spirits": "spirits", "liquor": "spirits", "whiskey": "spirits",
    "wine": "wine",
    "beer": "beer", "malt beverage": "beer", "malt beverages": "beer", "ale": "beer", "lager": "beer",
}

_FIELD_LINE_PATTERNS = {
    "beverage_type": re.compile(r"^\s*beverage\s*type\s*:?\s*(.+)$", re.IGNORECASE),
    "brand_name": re.compile(r"^\s*brand(?:\s*name)?\s*:?\s*(.+)$", re.IGNORECASE),
    "class_type": re.compile(
        r"^\s*(?:class\s*/?\s*type(?:\s*designation)?|class\s+and\s+type|type\s*/?\s*class|designation|type)\s*:?\s*(.+)$",
        re.IGNORECASE,
    ),
    "alcohol_content": re.compile(
        r"^\s*(?:alcohol\s*content|alcohol\s*by\s*volume|abv|alc\.?\s*/?\s*vol\.?)\s*:?\s*(.+)$", re.IGNORECASE
    ),
    "net_contents": re.compile(r"^\s*(?:net\s*contents?|contents?|volume|size)\s*:?\s*(.+)$", re.IGNORECASE),
    "government_warning": re.compile(r"^\s*(?:government\s*)?warning\s*:?\s*(.+)$", re.IGNORECASE),
    "country_of_origin": re.compile(r"^\s*(?:country\s*of\s*origin|origin)\s*:?\s*(.+)$", re.IGNORECASE),
    "bottler_name_address": re.compile(
        r"^\s*(?:bottler|bottled\s*by|produced\s*(?:and\s*bottled)?\s*by|producer|"
        r"name\s*and\s*address|importer|imported\s*by)\s*:?\s*(.+)$",
        re.IGNORECASE,
    ),
}
# Checked in this order so a more specific label (e.g. "Class/Type") is
# tried before a more general one that could also match part of it.
_FIELD_ORDER = [
    "beverage_type", "brand_name", "class_type", "alcohol_content",
    "net_contents", "government_warning", "country_of_origin", "bottler_name_address",
]


def _normalize_beverage_type(raw: str | None) -> str:
    if not raw:
        return "spirits"
    return _BEVERAGE_TYPE_ALIASES.get(raw.strip().lower(), "spirits")


def _parse_one_rule_based(chunk_id: str, text: str) -> ParsedApplication:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for field_name in _FIELD_ORDER:
            if field_name in fields:
                continue
            match = _FIELD_LINE_PATTERNS[field_name].match(line)
            if match:
                fields[field_name] = match.group(1).strip()
                break

    missing = [f for f in REQUIRED_FIELDS if not fields.get(f)]
    if missing:
        return ParsedApplication(
            id=chunk_id, application=None, source_text=text,
            error=f"Couldn't find {', '.join(missing)} in this application's text — "
            f"make sure each is on its own line, e.g. \"Brand Name: ...\".",
        )

    try:
        application = ApplicationData(
            beverage_type=_normalize_beverage_type(fields.get("beverage_type")),
            brand_name=fields["brand_name"],
            class_type=fields["class_type"],
            alcohol_content=fields.get("alcohol_content"),
            net_contents=fields["net_contents"],
            government_warning=fields.get("government_warning"),
            country_of_origin=fields.get("country_of_origin"),
            bottler_name_address=fields.get("bottler_name_address"),
        )
    except ValidationError as exc:
        return ParsedApplication(id=chunk_id, application=None, source_text=text, error=str(exc))

    warnings = []
    if not fields.get("bottler_name_address"):
        warnings.append("No bottler/importer name and address found in this text — TTB requires it on every real label.")
    return ParsedApplication(id=chunk_id, application=application, source_text=text, warnings=warnings)


# --- Optional upgrade: Claude, for text that isn't consistently labeled -----

_AI_BATCH_PROMPT = """You will be given several TTB alcohol beverage label applications, each marked with an ID like "=== ID: 3 ===". The text for each may be neatly labeled (e.g. "Brand Name: ...") or written more like a note or email - read it however it's written, and don't invent data that isn't there.

For each application, extract:
- beverage_type: "spirits", "wine", or "beer" (infer from context if not stated)
- brand_name
- class_type (the class/type designation)
- alcohol_content (or null if not stated)
- net_contents
- government_warning (or null - most applications won't restate the standard warning text, leave null in that case)
- country_of_origin (or null)
- bottler_name_address (or null)
- missing_required: an array listing which of brand_name/class_type/net_contents were genuinely not present in that application's text (empty array if all present)

Respond with ONLY a JSON array, one object per application, each including its original "id" plus the fields above. No other text, no markdown fences.

Applications:
{applications_block}"""


def _parse_with_ai(chunks: list[tuple[str, str]]) -> list[ParsedApplication]:
    if anthropic is None:
        raise ParsingUnavailableError("anthropic package not installed. Run: pip install anthropic")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ParsingUnavailableError("ANTHROPIC_API_KEY is not set.")
    if not chunks:
        return []

    client = anthropic.Anthropic(api_key=api_key)
    applications_block = "\n\n".join(f"=== ID: {chunk_id} ===\n{text}" for chunk_id, text in chunks)
    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": _AI_BATCH_PROMPT.format(applications_block=applications_block)}],
    )
    raw = "".join(block.text for block in response.content if block.type == "text").strip()
    raw = _strip_code_fence(raw)
    try:
        parsed_items = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"AI parser returned non-JSON output: {raw[:200]}") from exc

    by_id = {str(item.get("id")): item for item in parsed_items}
    results = []
    for chunk_id, text in chunks:
        item = by_id.get(chunk_id)
        if item is None:
            results.append(ParsedApplication(
                id=chunk_id, application=None, source_text=text,
                error="The AI parser didn't return a result for this application.",
            ))
            continue
        missing = item.get("missing_required") or []
        if missing:
            results.append(ParsedApplication(
                id=chunk_id, application=None, source_text=text,
                error=f"Couldn't find {', '.join(missing)} in this application's text.",
            ))
            continue
        try:
            application = ApplicationData(
                beverage_type=_normalize_beverage_type(item.get("beverage_type")),
                brand_name=item["brand_name"],
                class_type=item["class_type"],
                alcohol_content=item.get("alcohol_content"),
                net_contents=item["net_contents"],
                government_warning=item.get("government_warning"),
                country_of_origin=item.get("country_of_origin"),
                bottler_name_address=item.get("bottler_name_address"),
            )
        except (ValidationError, KeyError) as exc:
            results.append(ParsedApplication(id=chunk_id, application=None, source_text=text, error=str(exc)))
            continue
        warnings = []
        if not item.get("bottler_name_address"):
            warnings.append("No bottler/importer name and address found in this text — TTB requires it on every real label.")
        results.append(ParsedApplication(id=chunk_id, application=application, source_text=text, warnings=warnings))
    return results


def _strip_code_fence(text: str) -> str:
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    return text.strip()
