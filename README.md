# TTB Label Compliance Review — Prototype

A standalone tool that checks a submitted alcohol beverage label image against
the data an applicant entered on their COLA application, and tells an agent
exactly which fields match, which don't, and which need a human look —
instead of an agent eyeballing every field by hand.

Built in response to the discovery interviews with TTB's Label Compliance
Division. The design choices below trace directly back to specific quotes
from those interviews (cited inline).

## Why it's built this way

- **Free/offline by default, no cloud dependency.** Marcus (IT) noted the
  network blocks outbound traffic to a lot of domains and that the previous
  scanning-vendor pilot broke because of it. The default extraction path
  (`rapidocr-onnxruntime`) is a pure Python OCR engine — no external API call,
  no system binary to get through a change-control process, `pip install` and
  it works. See [Extraction backends](#extraction-backends) for the optional
  cloud upgrade.
- **Speed matters more than perfection.** Sarah was explicit: the prior pilot
  died because it took 30-40s/label and agents could eyeball five labels in
  that time. Single-label review here runs in roughly 4-9 seconds on a modest
  4-core machine (see [Known limitations](#known-limitations--trade-offs) —
  this is close to, not comfortably under, the ~5s bar she set, and it's the
  single biggest thing I'd optimize next).
- **Judgment over literal matching.** Dave's complaint was that a tool which
  flags `STONE'S THROW` vs `Stone's Throw` as a mismatch is useless. Every
  text field is compared after normalizing case/punctuation/whitespace, and
  the difference is still surfaced to the agent as a note — just not treated
  as a violation. See [`matching.py`](backend/app/matching.py).
- **The warning statement is the one place we do NOT go easy.** Jenny's
  example (a real rejection: "Government Warning" in title case instead of
  "GOVERNMENT WARNING:" in all caps) is implemented as an explicit, strict
  check, separate from the fuzzy-matching used everywhere else.
- **Batch upload**, because Sarah described importers dropping 200-300
  applications at once and agents processing them one at a time today.
- **A completely hands-off mode too** ([`watch_folder.py`](backend/watch_folder.py)):
  drop application folders into an inbox and the tool sorts them into
  `approved/` or `needs_review/` on its own — no one has to open the app, click
  upload, or wait for a page to respond at all. See
  [Hands-off automation](#hands-off-automation-watch_folderpy).
- **Simple, obvious UI.** Two tabs, big buttons, color-coded pass/fail/review
  badges, no settings to hunt for — aimed at Sarah's "my 73-year-old mother"
  bar, not Jenny's.
- **Standalone, not integrated with COLA.** Per Marcus: COLA integration is a
  separate, much bigger effort with its own authorization requirements. This
  is a proof of concept that could inform that decision later.

## Quickstart

Requires Python 3.10+.

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Open **http://localhost:8000** — the backend serves the frontend directly, so
there's nothing else to start.

Generate the sample test labels (optional — a handful of pre-generated ones
are already in `sample_labels/`, but you can regenerate or add more):

```bash
cd sample_labels
python generate_samples.py
```

Run the matching-logic unit tests (no OCR/model dependencies needed):

```bash
cd backend
python -m pytest tests/ -v
```

## Using it

**Single Label** tab: upload a label photo, fill in what the application
states, click Review. You get a pass/needs-review/fail verdict plus a
field-by-field table showing the submitted value, the extracted value, and
why they were judged to match or not.

**Batch Upload** tab: download the manifest CSV template, fill in one row per
application (the `filename` column must match an uploaded image's filename),
select all the images, click Review Batch. You get a summary count plus a
sortable-by-glance table; click any row to expand its field-by-field detail.

## Hands-off automation (`watch_folder.py`)

The web UI is for someone actively reviewing a label right now. For the
"take the user out of it entirely" case, there's a separate script that
needs no browser, no clicking, and no one waiting on a response:

```bash
cd backend
python watch_folder.py                 # scan once and exit — for cron / Task Scheduler
python watch_folder.py --watch         # keep running, poll every 15s (--interval to change)
```

A label never arrives on its own in the real workflow — it's always part of
a specific application, submitted together with the brand name, ABV, etc.
So the unit of work is an **application package**: one subfolder containing
the label image plus an `application.json` with the submitted fields. Drop
that folder into `watch_data/inbox/`:

```
watch_data/inbox/12345/
  label.jpg
  application.json
```

```json
{
  "brand_name": "OLD TOM DISTILLERY",
  "class_type": "Kentucky Straight Bourbon Whiskey",
  "alcohol_content": "45% Alc./Vol. (90 Proof)",
  "net_contents": "750 mL",
  "beverage_type": "spirits"
}
```

Once that folder stops changing (a simple two-snapshot check makes sure
nothing's still mid-copy), it gets reviewed automatically and the *whole
folder* — image, application data, and a new plain-English `report.txt` — is
moved into one of exactly two places:

- **`watch_data/approved/`** — a clean pass, nothing to look at.
- **`watch_data/needs_review/`** — anything else: a real mismatch, a field
  the extractor couldn't verify, a low-confidence/unreadable image, or even
  a malformed package (missing `application.json`, more than one image,
  etc.). Malformed packages are *never* silently dropped — they land here
  with a report explaining exactly what was wrong, so nothing goes missing.

Every run also appends one line to `watch_data/review_log.csv` — a running
audit trail (timestamp, outcome, confidence, per-field summary) of
everything the tool has ever looked at, independent of whatever an agent
does with the sorted folders afterward.

**Why one-shot-by-default rather than a persistent background service:**
Marcus's notes describe locked-down government infrastructure where
standing up a new persistent process is its own change-control conversation.
A script that scans once and exits is just a scheduled task
(Windows Task Scheduler / cron) — the kind of thing IT already knows how to
approve and monitor. `--watch` is there if continuous polling is cleared
instead; it's the same script either way, not a separate deployment.

**Why it needs `application.json` and won't just review bare images:** without
knowing what was actually applied for, the tool can't do the thing it's
for — checking the label *against the application*. Give it only an image
and the best it could do is check the label against itself (is the warning
text exact, is the stated proof consistent with the stated ABV), which
silently drops the majority of what agents described their actual daily
work being. A malformed or missing `application.json` is treated as a
"needs review" case with a clear explanation, not processed as if the label
were self-evidently fine, and not silently skipped either.

## Extraction backends

Two interchangeable backends implement the same interface
([`extraction/base.py`](backend/app/extraction/base.py)):

| | OCR (default) | Vision (optional) |
|---|---|---|
| Engine | `rapidocr-onnxruntime` (local, pure Python, ONNX) | Claude (multimodal) via the Anthropic API |
| Cost | Free | Per-image API cost |
| Network | None required | Requires outbound HTTPS to the Anthropic API |
| Setup | `pip install`, nothing else | Set `ANTHROPIC_API_KEY` env var |
| Speed | ~4-9s/label (this machine) | Not benchmarked here (no key available in dev); typically a few seconds, network-dependent |
| Field extraction | Regex + a layout heuristic (see below) | The model directly returns structured fields |
| Bold/formatting detection | **Cannot detect bold at all** — flagged as "needs review" every time | Can directly assess whether the warning header is bold |
| Handles blurry/angled photos | Weaker — text detector can miss whole lines | Much stronger (this was Jenny's specific ask) |

The app auto-selects Vision if `ANTHROPIC_API_KEY` is set, otherwise OCR. You
can also force one via `?method=ocr` or `?method=vision` on either API
endpoint, e.g. for side-by-side comparison.

**Why OCR is the default rather than an afterthought:** given Marcus's
firewall/change-control comments, a tool that only works with a cloud API key
would likely never survive contact with TTB's actual network. The free path
had to be the one that works out of the box.

## How matching works

See [`backend/app/matching.py`](backend/app/matching.py) for the full logic
and inline rationale. Summary:

- **Brand name / class-type / net contents:** normalized (case, punctuation,
  whitespace) before comparing. Exact-after-normalization → match. Close but
  not exact → flagged as a minor difference, not a violation. Below a
  similarity threshold → mismatch.
- **Alcohol content:** same as above, plus the numeric ABV is parsed out and
  compared directly so formatting differences ("45% Alc./Vol." vs "45% ALC/VOL")
  never cause a false mismatch. There's also a sanity check that the label's
  own stated proof is internally consistent with its own stated ABV (proof
  should be exactly 2× the percentage) — this catches labels that are simply
  wrong about themselves, independent of what the application says.
- **Net contents:** same fuzzy-then-numeric approach, with unit conversion
  (mL/L/fl oz) before comparing quantities.
- **Government warning:** compared against the exact statutory text (27 CFR
  16.21) if the application doesn't supply custom text to check against.
  Unlike every other field, this is **not** fuzzy-matched — the statute
  requires the wording verbatim. The header's capitalization
  ("GOVERNMENT WARNING:") is checked as a hard, non-negotiable requirement
  regardless of extraction method. Bold-type detection is only possible with
  the Vision backend; under OCR it's always flagged as "needs review" rather
  than silently assumed correct.

### A finding from testing that changed the design

While generating test labels I found that the OCR text detector can silently
**drop an entire line** of text (not misread it — never detect it existed at
all) on an otherwise perfectly rendered, high-quality synthetic label. That's
a real failure mode, not a hypothetical one — see
`sample_labels/old_tom_bourbon_correct.png`, which is a label rendered with
the exact statutory warning verbatim, on which the local OCR path drops one
line of it.

If that had been left as an automatic "mismatch," the tool would produce
false compliance failures on genuinely compliant labels — which is exactly
the "agents stopped trusting it and went back to eyeballing" failure mode
from the scanning-vendor pilot Sarah described. So: **under the OCR backend,
a warning-text content deviation is downgraded to "needs review" rather than
an automatic fail** (header capitalization stays a hard fail either way,
since that's a reliable character-level check unaffected by dropped lines).
Under the Vision backend, which reads the whole label holistically instead of
via segmented text detection, a content deviation stays a hard fail. This
distinction is covered by unit tests in `backend/tests/test_matching.py`.

The same reasoning applies to any field OCR reports as **missing entirely**:
under OCR, "missing" contributes to a "needs review" outcome, not an
automatic fail, since we can't distinguish "genuinely not on the label" from
"OCR's detector missed that region" (this is also what happens on the
deliberately blurry test sample).

### Brand name / class-type extraction heuristic

OCR gives us a bag of text lines with no idea which one is "the brand name."
The heuristic used is: **the topmost non-warning, non-quantity line is the
brand name; the next line down is the class/type designation.** This is not
theoretical — an earlier version of this heuristic picked the tallest line of
text instead, on the (reasonable-sounding) theory that brand names are
usually the biggest text on a label. That heuristic actively produced wrong
answers during testing: an all-caps brand name has no ascenders or
descenders, so its OCR bounding box measures *shorter* than a smaller,
mixed-case class/type line below it, causing the two to be swapped. Position
in reading order turned out to be the more reliable signal. This is still a
heuristic, though, and will misfire on labels with an unusual layout (e.g.
brand name below class/type, or a multi-line brand name) — the Vision backend
doesn't have this problem since it's told explicitly what each field means.

## Known limitations & trade-offs

- **The ~5 second target is not comfortably met by the default OCR path** —
  measured 4-9s/label on a 4-core dev machine, including some runs over 5s.
  This is close to, but doesn't confidently clear, Sarah's stated bar. Options
  for a real deployment: run on more capable hardware (this was tested in a
  constrained/shared sandbox), or default to the Vision backend where network
  latency to a fast model is typically the dominant cost and can be lower.
- **Batch throughput is currently sequential for the OCR path, on purpose.**
  Each OCR call already saturates the CPU with its own internal thread pool;
  running several concurrently was measured to make every job 3-4x *slower*,
  not faster, from thread oversubscription (a good reminder that CPU-bound
  work doesn't parallelize like I/O-bound work does). A production batch
  path would run separate worker processes sized to actual core count, or
  default heavy batches to the Vision backend, where concurrency is real
  (network-bound calls, not CPU-bound ones) and is enabled here.
- **Bold-type detection is impossible with OCR alone.** Every OCR-path result
  flags this for manual confirmation rather than guessing.
- **The brand/class-type extraction heuristic assumes a fairly standard label
  layout** (brand name above class/type, both above the fine print). Unusual
  layouts will misattribute these two fields under OCR.
- **No COLA integration.** This is intentionally a standalone tool per the
  scoping conversation with Marcus — it doesn't read from or write to the
  live COLA system, and doesn't handle authentication, audit logging, or
  document retention, all of which a production version would need.
- **No PII/security hardening beyond basics.** Per Marcus, this prototype
  isn't storing anything sensitive, so this wasn't a design focus. A real
  deployment handling actual applications would need the usual federal
  compliance treatment (access control, audit trail, retention policy).
- **Class/type legal correctness isn't validated** — the tool checks that the
  application and label *agree* with each other, not that either one is
  actually a legally valid class/type designation under TTB's regulations.
  That's a different (much larger) problem than label-vs-application matching.
- **Sample labels are synthetic**, generated with PIL rather than photographed
  bottles, since no real label photos were available. They're representative
  of clean product photography but don't fully exercise real-world glare,
  angle, or lighting issues the way an actual phone photo would (Jenny's
  request) — the blurry/rotated sample is an approximation of that, not the
  real thing.

## API reference

- `GET /api/health` — status check, reports which extraction method is active by default.
- `GET /api/manifest-template` — downloads the batch CSV template.
- `POST /api/review` — single review. Multipart form: `image` (file) +
  `brand_name`, `class_type`, `alcohol_content`, `net_contents`,
  `beverage_type`, `government_warning` (optional), `country_of_origin`
  (optional). Optional `?method=ocr|vision` query param.
- `POST /api/review/batch` — batch review. Multipart form: `manifest` (CSV
  file) + `images` (multiple files). Same optional `?method=` param.

## Deployment

The app is a single FastAPI process serving both the API and the static
frontend — no separate frontend build/deploy step.

**Render / Railway / Fly.io (Python buildpack):**
- Root/start directory: `backend`
- Build: `pip install -r requirements.txt`
- Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Optional env var: `ANTHROPIC_API_KEY` to switch to the Vision backend.

**Docker** (a `Dockerfile` is included at the repo root):

```bash
docker build -t ttb-label-review .
docker run -p 8000:8000 ttb-label-review
```

Note the OCR path downloads its ONNX model files on first use and caches
them; the first request after a fresh deploy will be slower than subsequent
ones.

## Project structure

```
backend/
  app/
    main.py            FastAPI app & routes
    models.py           Pydantic request/response models
    matching.py          Field comparison / matching engine
    manifest.py          Batch CSV manifest parsing
    warning_text.py       Canonical statutory warning text
    extraction/
      base.py             Common extractor interface
      ocr_extractor.py      Free/offline OCR backend (default)
      vision_extractor.py    Claude Vision backend (optional)
      factory.py            Picks a backend based on env/request
  tests/
    test_matching.py     Unit tests for the matching engine
    test_watch_folder.py  Unit tests for the folder-watcher's routing logic
  watch_folder.py        Hands-off folder watcher (see above)
  requirements.txt
frontend/
  index.html / style.css / app.js   Plain HTML/JS UI, no build step
sample_labels/
  generate_samples.py   Generates synthetic test label images
  manifest.csv           Example batch manifest for the generated samples
  example_application_package/  A ready-to-try watch_folder.py input (see below)
```

To try `watch_folder.py` immediately without assembling your own test data:

```bash
mkdir -p backend/watch_data/inbox
cp -r sample_labels/example_application_package backend/watch_data/inbox/example_001
cd backend
python watch_folder.py
```
