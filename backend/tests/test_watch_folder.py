"""Tests for the folder-watcher's routing logic (approved/ vs needs_review/,
error handling for malformed packages). These stub out the extractor so
they run fast and don't need a real OCR engine or real label images — the
field-matching logic itself is already covered by test_matching.py.

Run with: python -m pytest backend/tests
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watch_folder  # noqa: E402
from app.extraction.base import ExtractedLabel  # noqa: E402
from app.warning_text import CANONICAL_WARNING  # noqa: E402

GOOD_APPLICATION = {
    "brand_name": "OLD TOM DISTILLERY",
    "class_type": "Kentucky Straight Bourbon Whiskey",
    "alcohol_content": "45% Alc./Vol. (90 Proof)",
    "net_contents": "750 mL",
}


class FakeExtractor:
    def __init__(self, label: ExtractedLabel):
        self._label = label

    def extract(self, image_bytes: bytes) -> ExtractedLabel:
        return self._label


def _make_package(inbox: Path, name: str, application: dict | None, image_name: str = "label.png") -> Path:
    folder = inbox / name
    folder.mkdir(parents=True)
    (folder / image_name).write_bytes(b"not a real image, extractor is stubbed")
    if application is not None:
        (folder / "application.json").write_text(json.dumps(application), encoding="utf-8")
    return folder


def _patch_stability_and_extractor(monkeypatch, extracted_label: ExtractedLabel | None):
    monkeypatch.setattr(watch_folder, "STABILITY_CHECK_SECONDS", 0.0)
    if extracted_label is not None:
        monkeypatch.setattr(watch_folder, "get_extractor", lambda method=None: FakeExtractor(extracted_label))


def test_clean_match_routes_to_approved(tmp_path, monkeypatch):
    perfect = ExtractedLabel(
        brand_name=GOOD_APPLICATION["brand_name"],
        class_type=GOOD_APPLICATION["class_type"],
        alcohol_content=GOOD_APPLICATION["alcohol_content"],
        net_contents=GOOD_APPLICATION["net_contents"],
        government_warning=CANONICAL_WARNING,
        warning_header_bold=True,
        confidence=0.95,
        method="vision",
    )
    _patch_stability_and_extractor(monkeypatch, perfect)
    _make_package(tmp_path / "inbox", "app_ok", GOOD_APPLICATION)

    processed = watch_folder.process_inbox(tmp_path)

    assert processed == 1
    assert (tmp_path / "approved" / "app_ok" / "report.txt").exists()
    assert not (tmp_path / "needs_review" / "app_ok").exists()
    assert "PASS" in (tmp_path / "approved" / "app_ok" / "report.txt").read_text()


def test_mismatch_routes_to_needs_review(tmp_path, monkeypatch):
    wrong = ExtractedLabel(
        brand_name="SOMETHING ELSE ENTIRELY",
        class_type=GOOD_APPLICATION["class_type"],
        alcohol_content=GOOD_APPLICATION["alcohol_content"],
        net_contents=GOOD_APPLICATION["net_contents"],
        government_warning=CANONICAL_WARNING,
        warning_header_bold=True,
        confidence=0.95,
        method="vision",
    )
    _patch_stability_and_extractor(monkeypatch, wrong)
    _make_package(tmp_path / "inbox", "app_bad", GOOD_APPLICATION)

    processed = watch_folder.process_inbox(tmp_path)

    assert processed == 1
    assert (tmp_path / "needs_review" / "app_bad").exists()
    assert not (tmp_path / "approved" / "app_bad").exists()


def test_missing_application_json_routes_to_needs_review_with_explanation(tmp_path, monkeypatch):
    _patch_stability_and_extractor(monkeypatch, None)
    _make_package(tmp_path / "inbox", "app_no_data", application=None)

    processed = watch_folder.process_inbox(tmp_path)

    assert processed == 1
    report = (tmp_path / "needs_review" / "app_no_data" / "report.txt").read_text()
    assert "COULD NOT BE REVIEWED" in report
    assert "application.json" in report


def test_malformed_json_routes_to_needs_review(tmp_path, monkeypatch):
    _patch_stability_and_extractor(monkeypatch, None)
    folder = _make_package(tmp_path / "inbox", "app_bad_json", application=None)
    (folder / "application.json").write_text("{not valid json", encoding="utf-8")

    processed = watch_folder.process_inbox(tmp_path)

    assert processed == 1
    assert (tmp_path / "needs_review" / "app_bad_json").exists()


def test_no_image_routes_to_needs_review(tmp_path, monkeypatch):
    _patch_stability_and_extractor(monkeypatch, None)
    folder = tmp_path / "inbox" / "app_no_image"
    folder.mkdir(parents=True)
    (folder / "application.json").write_text(json.dumps(GOOD_APPLICATION), encoding="utf-8")

    processed = watch_folder.process_inbox(tmp_path)

    assert processed == 1
    report = (tmp_path / "needs_review" / "app_no_image" / "report.txt").read_text()
    assert "No label image found" in report


def test_multiple_images_routes_to_needs_review(tmp_path, monkeypatch):
    _patch_stability_and_extractor(monkeypatch, None)
    folder = _make_package(tmp_path / "inbox", "app_two_images", GOOD_APPLICATION, image_name="a.png")
    (folder / "b.jpg").write_bytes(b"also not a real image")

    processed = watch_folder.process_inbox(tmp_path)

    assert processed == 1
    report = (tmp_path / "needs_review" / "app_two_images" / "report.txt").read_text()
    assert "expected exactly one" in report


def test_unstable_package_is_left_alone(tmp_path, monkeypatch):
    # A file that's still being written (size changing between the two
    # snapshots) must not be processed yet.
    monkeypatch.setattr(watch_folder, "STABILITY_CHECK_SECONDS", 0.05)
    folder = _make_package(tmp_path / "inbox", "app_midcopy", GOOD_APPLICATION)

    real_snapshot = watch_folder._snapshot
    calls = {"n": 0}

    def flaky_snapshot(f):
        calls["n"] += 1
        if calls["n"] == 2:
            (folder / "label.png").write_bytes(b"still copying...")
        return real_snapshot(f)

    monkeypatch.setattr(watch_folder, "_snapshot", flaky_snapshot)

    processed = watch_folder.process_inbox(tmp_path)

    assert processed == 0
    assert (tmp_path / "inbox" / "app_midcopy").exists()
