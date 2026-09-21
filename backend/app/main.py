from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .extraction.factory import OcrUnavailableError, default_method, get_extractor
from .manifest import CSV_TEMPLATE, ManifestError, parse_manifest
from .matching import build_review_result
from .models import ApplicationData, BeverageType

app = FastAPI(title="TTB Label Compliance Review (Prototype)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"

# OCR inference is CPU-bound, and each call already fans out across multiple
# internal ONNX threads sized to the machine's core count. Running several
# calls concurrently doesn't parallelize across cores so much as it makes
# every call fight over the same cores — measured ~5-9s/label processed one
# at a time vs. ~20-30s/label at 4-8-way "concurrency" on a 4-core box during
# testing, i.e. concurrency made batch throughput *worse*. So we process OCR
# batches sequentially here; a production deployment would instead parallelize
# across separate worker processes/machines sized to available cores. Vision
# calls are network-bound instead, so real concurrency helps there.
_OCR_BATCH_WORKERS = 1
_VISION_BATCH_WORKERS = 8
_ocr_executor = ThreadPoolExecutor(max_workers=_OCR_BATCH_WORKERS)
_vision_executor = ThreadPoolExecutor(max_workers=_VISION_BATCH_WORKERS)


@app.get("/api/health")
def health():
    return {"status": "ok", "default_extraction_method": default_method()}


@app.get("/api/manifest-template", response_class=PlainTextResponse)
def manifest_template():
    return CSV_TEMPLATE


def _run_review(image_bytes: bytes, application: ApplicationData, method: str | None, filename: str | None):
    start = time.perf_counter()
    extractor = get_extractor(method)
    extracted = extractor.extract(image_bytes)
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    return build_review_result(application, extracted, elapsed_ms, filename=filename)


@app.post("/api/review")
async def review_single(
    image: UploadFile = File(...),
    beverage_type: BeverageType = Form(BeverageType.spirits),
    brand_name: str = Form(...),
    class_type: str = Form(...),
    alcohol_content: str = Form(...),
    net_contents: str = Form(...),
    government_warning: str | None = Form(None),
    country_of_origin: str | None = Form(None),
    method: str | None = Query(None, description="Force 'ocr' or 'vision'"),
):
    application = ApplicationData(
        beverage_type=beverage_type,
        brand_name=brand_name,
        class_type=class_type,
        alcohol_content=alcohol_content,
        net_contents=net_contents,
        government_warning=government_warning or None,
        country_of_origin=country_of_origin or None,
    )
    image_bytes = await image.read()
    try:
        result = _run_review(image_bytes, application, method, image.filename)
    except OcrUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not process label image: {exc}") from exc
    return result


@app.post("/api/review/batch")
async def review_batch(
    images: list[UploadFile] = File(...),
    manifest: UploadFile = File(...),
    method: str | None = Query(None, description="Force 'ocr' or 'vision'"),
):
    manifest_bytes = await manifest.read()
    try:
        rows = parse_manifest(manifest_bytes)
    except ManifestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    effective_method = method or default_method()
    executor = _vision_executor if effective_method == "vision" else _ocr_executor

    application_by_filename = dict(rows)
    image_bytes_by_filename: dict[str, bytes] = {}
    for img in images:
        image_bytes_by_filename[img.filename] = await img.read()

    def process_one(filename: str, application: ApplicationData):
        image_bytes = image_bytes_by_filename.get(filename)
        if image_bytes is None:
            return {
                "filename": filename,
                "error": "No matching image uploaded for this manifest row.",
            }
        try:
            return _run_review(image_bytes, application, method, filename).model_dump()
        except OcrUnavailableError as exc:
            return {"filename": filename, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - surface per-row, don't fail the whole batch
            return {"filename": filename, "error": f"Could not process label image: {exc}"}

    futures = [executor.submit(process_one, fn, app_data) for fn, app_data in rows]
    results = [f.result() for f in futures]

    uploaded_not_in_manifest = set(image_bytes_by_filename) - set(application_by_filename)

    summary = {"total": len(results), "pass": 0, "needs_review": 0, "fail": 0, "error": 0}
    for r in results:
        if "error" in r:
            summary["error"] += 1
        else:
            summary[r["overall_status"]] += 1

    return {
        "summary": summary,
        "results": results,
        "unmatched_uploaded_images": sorted(uploaded_not_in_manifest),
    }


if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
