"""Unit tests for the API layer (main.py): endpoint routing, request
validation, error handling, and the batch filename-matching logic.

The actual extraction backend is stubbed out with a fake LabelExtractor
rather than running real OCR or calling the real Anthropic API, so these
run fast and deterministically - matching the project's existing "no
OCR/model/network dependencies needed" testing philosophy. What real OCR
actually extracts is covered separately by manual/live testing (see
README); what these test is that main.py wires requests, extraction, and
matching together correctly and handles every error path cleanly.

Run with: python -m pytest backend/tests
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.extraction.base import ExtractedLabel, LabelExtractor  # noqa: E402
from app.extraction.factory import OcrUnavailableError  # noqa: E402
from app.warning_text import CANONICAL_WARNING  # noqa: E402

FAKE_IMAGE_BYTES = b"not a real image - the extractor is stubbed, so nothing ever decodes this"


class FakeExtractor(LabelExtractor):
    method_name = "fake"

    def __init__(self, extracted: ExtractedLabel | None = None, raise_exc: Exception | None = None):
        self._extracted = extracted
        self._raise_exc = raise_exc

    def extract(self, image_bytes: bytes) -> ExtractedLabel:
        if self._raise_exc:
            raise self._raise_exc
        return self._extracted or ExtractedLabel(
            brand_name="OLD TOM DISTILLERY",
            class_type="Kentucky Straight Bourbon Whiskey",
            net_contents="750 mL",
            government_warning=CANONICAL_WARNING,
            warning_header_bold=True,
            warning_header_allcaps=True,
            method="fake",
            confidence=0.9,
        )


@pytest.fixture(autouse=True)
def stub_extractor(monkeypatch):
    """Every test gets a working fake extractor by default (matching
    submitted values, so single reviews come back a clean pass) - tests
    that need different behavior (an error, a mismatch, or to inspect what
    main.py actually passed to get_extractor) override this directly."""
    monkeypatch.setattr(main, "get_extractor", lambda method=None, api_key=None: FakeExtractor())


@pytest.fixture
def client(stub_extractor):
    with TestClient(main.app) as c:
        yield c


VALID_FORM = {
    "brand_name": "OLD TOM DISTILLERY",
    "class_type": "Kentucky Straight Bourbon Whiskey",
    "net_contents": "750 mL",
}


def _upload(filename="label.png"):
    return {"image": (filename, FAKE_IMAGE_BYTES, "image/png")}


# ---- /api/health, /api/manifest-template ----

def test_health_reports_ok_and_a_default_method(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["default_extraction_method"] in ("ocr", "vision")


def test_manifest_template_is_downloadable_csv_text(client):
    res = client.get("/api/manifest-template")
    assert res.status_code == 200
    assert "filename" in res.text
    assert "brand_name" in res.text


# ---- /api/review ----

def test_single_review_happy_path_returns_pass(client):
    res = client.post("/api/review", files=_upload(), data=VALID_FORM)
    assert res.status_code == 200
    body = res.json()
    assert body["overall_status"] == "pass"
    assert body["extraction_method"] == "fake"
    assert {f["field"] for f in body["fields"]} >= {"brand_name", "class_type", "net_contents"}


def test_single_review_missing_required_form_field_is_422(client):
    incomplete = {k: v for k, v in VALID_FORM.items() if k != "brand_name"}
    res = client.post("/api/review", files=_upload(), data=incomplete)
    assert res.status_code == 422


def test_single_review_extraction_error_returns_422_with_detail(client, monkeypatch):
    monkeypatch.setattr(
        main, "get_extractor",
        lambda method=None, api_key=None: FakeExtractor(raise_exc=RuntimeError("bad image data")),
    )
    res = client.post("/api/review", files=_upload(), data=VALID_FORM)
    assert res.status_code == 422
    assert "bad image data" in res.json()["detail"]


def test_single_review_ocr_unavailable_returns_503(client, monkeypatch):
    monkeypatch.setattr(
        main, "get_extractor",
        lambda method=None, api_key=None: FakeExtractor(raise_exc=OcrUnavailableError("not installed")),
    )
    res = client.post("/api/review", files=_upload(), data=VALID_FORM)
    assert res.status_code == 503


def test_single_review_forwards_method_and_api_key_header_to_extractor(client, monkeypatch):
    calls = []

    def spy(method=None, api_key=None):
        calls.append((method, api_key))
        return FakeExtractor()

    monkeypatch.setattr(main, "get_extractor", spy)
    res = client.post(
        "/api/review?method=vision",
        files=_upload(),
        data=VALID_FORM,
        headers={"X-Anthropic-Api-Key": "sk-ant-caller-key"},
    )
    assert res.status_code == 200
    assert calls == [("vision", "sk-ant-caller-key")]


# ---- /api/review/batch ----

VALID_MANIFEST = (
    "filename,brand_name,class_type,net_contents\n"
    "a.png,OLD TOM DISTILLERY,Kentucky Straight Bourbon Whiskey,750 mL\n"
    "b.png,RIVERBEND CELLARS,Cabernet Sauvignon,750 mL\n"
)


def test_batch_review_processes_every_manifest_row(client):
    res = client.post(
        "/api/review/batch",
        files=[
            ("manifest", ("manifest.csv", VALID_MANIFEST, "text/csv")),
            ("images", ("a.png", FAKE_IMAGE_BYTES, "image/png")),
            ("images", ("b.png", FAKE_IMAGE_BYTES, "image/png")),
        ],
    )
    assert res.status_code == 200
    body = res.json()
    assert body["summary"]["total"] == 2
    assert {r["filename"] for r in body["results"]} == {"a.png", "b.png"}
    assert body["unmatched_uploaded_images"] == []


def test_batch_review_flags_manifest_row_with_no_uploaded_image(client):
    res = client.post(
        "/api/review/batch",
        files=[
            ("manifest", ("manifest.csv", VALID_MANIFEST, "text/csv")),
            ("images", ("a.png", FAKE_IMAGE_BYTES, "image/png")),
            # "b.png" from the manifest is never uploaded.
        ],
    )
    assert res.status_code == 200
    body = res.json()
    b_result = next(r for r in body["results"] if r["filename"] == "b.png")
    assert "error" in b_result
    assert body["summary"]["error"] == 1


def test_batch_review_flags_uploaded_image_not_in_manifest(client):
    res = client.post(
        "/api/review/batch",
        files=[
            ("manifest", ("manifest.csv", VALID_MANIFEST, "text/csv")),
            ("images", ("a.png", FAKE_IMAGE_BYTES, "image/png")),
            ("images", ("b.png", FAKE_IMAGE_BYTES, "image/png")),
            ("images", ("extra_not_in_manifest.png", FAKE_IMAGE_BYTES, "image/png")),
        ],
    )
    assert res.status_code == 200
    assert res.json()["unmatched_uploaded_images"] == ["extra_not_in_manifest.png"]


def test_batch_review_bad_manifest_is_422_with_detail(client):
    bad_manifest = "filename,brand_name\na.png,BRAND\n"  # missing class_type/net_contents columns
    res = client.post(
        "/api/review/batch",
        files=[
            ("manifest", ("manifest.csv", bad_manifest, "text/csv")),
            ("images", ("a.png", FAKE_IMAGE_BYTES, "image/png")),
        ],
    )
    assert res.status_code == 422
    assert "missing required column" in res.json()["detail"]
