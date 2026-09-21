"""Hands-off, no-UI label review: drop application packages in a folder,
get sorted results out.

A label never shows up on its own in real life — it's always part of a
specific application, submitted together with the brand name, ABV, etc. So
the unit of work here is an "application package": one subfolder containing
exactly one label image and one `application.json` with the submitted
fields. Drop such a folder into the inbox, and it gets moved into either
`approved/` (a clean pass) or `needs_review/` (anything else — a mismatch,
an ambiguous OCR result, a low-confidence image, or a malformed package),
with a plain-English `report.txt` written alongside it explaining why.

Usage:
    python watch_folder.py                       # scan once and exit (cron / Task Scheduler)
    python watch_folder.py --watch                # keep running, poll every --interval seconds
    python watch_folder.py --base-dir ./mydata --interval 30 --watch

`--base-dir` (default: ./watch_data, created if missing) holds three
subfolders: inbox/, approved/, needs_review/ — plus a running review_log.csv
audit trail at its root.

application.json shape (any field ApplicationData accepts):
    {
      "brand_name": "OLD TOM DISTILLERY",
      "class_type": "Kentucky Straight Bourbon Whiskey",
      "alcohol_content": "45% Alc./Vol. (90 Proof)",
      "net_contents": "750 mL",
      "beverage_type": "spirits"
    }

Why one-shot-by-default rather than a persistent watcher: per the discovery
notes, TTB's IT infrastructure is locked-down government infrastructure
where installing/running a new persistent service is its own change-control
process. A script that scans once and exits is trivial to schedule (Windows
Task Scheduler / cron) without that overhead. --watch is there for anyone
who's cleared to run it continuously instead.

Why polling instead of a filesystem-events library (e.g. watchdog): one
fewer dependency to get approved, and polling every few seconds is more
than fast enough for a review queue that isn't latency-sensitive to begin
with — the whole point is nobody's sitting there waiting on it.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pydantic import ValidationError

from app.extraction.factory import OcrUnavailableError, get_extractor
from app.matching import build_review_result
from app.models import ApplicationData, OverallStatus, ReviewResult

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"}
APPLICATION_FILENAME = "application.json"
STABILITY_CHECK_SECONDS = 2.0
LOG_FILENAME = "review_log.csv"
LOG_FIELDS = [
    "timestamp", "application_id", "outcome", "extraction_method",
    "extraction_confidence", "processing_time_ms", "summary",
]


class PackageError(Exception):
    """A package can't be reviewed as-is (bad/missing data) — still routed
    to needs_review with an explanatory report, never silently dropped."""


def _find_images(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS)


def _snapshot(folder: Path) -> dict[str, tuple[int, float]]:
    return {p.name: (p.stat().st_size, p.stat().st_mtime) for p in folder.iterdir() if p.is_file()}


def _load_application(folder: Path) -> ApplicationData:
    app_path = folder / APPLICATION_FILENAME
    if not app_path.exists():
        raise PackageError(
            f"No {APPLICATION_FILENAME} found in this package. A label can't be verified "
            "without knowing what was applied for — add the application data file and resubmit."
        )
    try:
        raw = json.loads(app_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PackageError(f"{APPLICATION_FILENAME} is not valid JSON: {exc}") from exc
    try:
        return ApplicationData(**raw)
    except ValidationError as exc:
        raise PackageError(f"{APPLICATION_FILENAME} is missing or has invalid fields: {exc}") from exc


def _select_image(folder: Path) -> Path:
    images = _find_images(folder)
    if not images:
        raise PackageError("No label image found in this package (expected one .png/.jpg/etc file).")
    if len(images) > 1:
        raise PackageError(
            f"Found {len(images)} image files in this package ({', '.join(p.name for p in images)}) "
            "— expected exactly one label image per application."
        )
    return images[0]


def _review_package(folder: Path, method: str | None) -> ReviewResult:
    application = _load_application(folder)
    image_path = _select_image(folder)
    start = time.perf_counter()
    extractor = get_extractor(method)
    extracted = extractor.extract(image_path.read_bytes())
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    return build_review_result(application, extracted, elapsed_ms, filename=image_path.name)


def _write_report(folder: Path, result: ReviewResult | None, error: str | None) -> None:
    lines = [
        "TTB Label Compliance Review — Automated Report",
        f"Package: {folder.name}",
        f"Generated: {datetime.now(timezone.utc).isoformat()}Z",
        "",
    ]
    if error:
        lines += ["OUTCOME: COULD NOT BE REVIEWED", "", error]
    else:
        lines += [
            f"OUTCOME: {result.overall_status.value.upper().replace('_', ' ')}",
            f"Extraction method: {result.extraction_method}"
            + (f" (confidence {result.extraction_confidence:.0%})" if result.extraction_confidence is not None else ""),
            f"Processing time: {result.processing_time_ms} ms",
            "",
            "Field-by-field:",
        ]
        for f in result.fields:
            lines.append(f"  [{f.status.value.upper():^12}] {f.field}")
            lines.append(f"      Application says: {f.submitted_value or '(none)'}")
            lines.append(f"      Label shows:       {f.extracted_value or '(not found)'}")
            if f.note:
                lines.append(f"      Note: {f.note}")
            lines.append("")
        if result.warnings:
            lines.append("Heads up:")
            lines += [f"  - {w}" for w in result.warnings]
    (folder / "report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _append_log(base_dir: Path, application_id: str, outcome: str, result: ReviewResult | None) -> None:
    log_path = base_dir / LOG_FILENAME
    is_new = not log_path.exists()
    with log_path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
        if is_new:
            writer.writeheader()
        summary = ""
        if result:
            summary = "; ".join(f"{f.field}={f.status.value}" for f in result.fields)
        writer.writerow({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "application_id": application_id,
            "outcome": outcome,
            "extraction_method": result.extraction_method if result else "",
            "extraction_confidence": f"{result.extraction_confidence:.2f}" if result and result.extraction_confidence is not None else "",
            "processing_time_ms": result.processing_time_ms if result else "",
            "summary": summary,
        })


def process_inbox(base_dir: Path, method: str | None = None) -> int:
    """Scans the inbox once, moves every stable+complete package out.
    Returns the number of packages processed."""
    inbox = base_dir / "inbox"
    approved = base_dir / "approved"
    needs_review = base_dir / "needs_review"
    for d in (inbox, approved, needs_review):
        d.mkdir(parents=True, exist_ok=True)

    candidates = [p for p in inbox.iterdir() if p.is_dir()]
    if not candidates:
        return 0

    # Stability check: a package might still be mid-copy. Snapshot file
    # sizes/mtimes, wait briefly, snapshot again — only touch folders that
    # didn't change, so we never grab a half-written file.
    before = {p: _snapshot(p) for p in candidates}
    time.sleep(STABILITY_CHECK_SECONDS)
    stable = [p for p in candidates if p.exists() and _snapshot(p) == before[p]]

    processed = 0
    for folder in stable:
        application_id = folder.name
        result: ReviewResult | None = None
        error: str | None = None
        try:
            result = _review_package(folder, method)
        except PackageError as exc:
            error = str(exc)
        except OcrUnavailableError as exc:
            error = f"OCR engine unavailable: {exc}"
        except Exception as exc:  # noqa: BLE001 - never let one bad package kill the run
            error = f"Unexpected error while reviewing this package: {exc}"

        outcome = "approved" if (result and result.overall_status == OverallStatus.PASS) else "needs_review"
        destination_root = approved if outcome == "approved" else needs_review

        _write_report(folder, result, error)
        destination = destination_root / application_id
        if destination.exists():
            destination = destination_root / f"{application_id}_{int(time.time())}"
        shutil.move(str(folder), str(destination))

        _append_log(base_dir, application_id, outcome, result)
        print(f"[{outcome:>12}] {application_id}" + (f" - {error}" if error else ""), flush=True)
        processed += 1

    return processed


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-dir", default="watch_data", help="Root folder holding inbox/approved/needs_review (default: ./watch_data)")
    parser.add_argument("--watch", action="store_true", help="Keep running and poll for new packages instead of scanning once and exiting")
    parser.add_argument("--interval", type=float, default=15.0, help="Seconds between polls in --watch mode (default: 15)")
    parser.add_argument("--method", choices=["ocr", "vision"], default=None, help="Force an extraction backend instead of auto-selecting")
    args = parser.parse_args()

    base_dir = Path(args.base_dir).resolve()
    print(f"Watching {base_dir / 'inbox'}", flush=True)
    print(f"  approved      -> {base_dir / 'approved'}", flush=True)
    print(f"  needs review  -> {base_dir / 'needs_review'}", flush=True)
    print(f"  audit log     -> {base_dir / LOG_FILENAME}", flush=True)

    if not args.watch:
        n = process_inbox(base_dir, args.method)
        print(f"Done. Processed {n} package(s).", flush=True)
        return

    print(f"Polling every {args.interval}s. Press Ctrl+C to stop.")
    try:
        while True:
            n = process_inbox(base_dir, args.method)
            if n:
                print(f"Processed {n} package(s).", flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
