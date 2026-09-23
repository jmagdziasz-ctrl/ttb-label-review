# TTB Label Compliance Review — Prototype

A standalone tool that checks a submitted alcohol beverage label image against
the data an applicant entered on their COLA application, and tells an agent
exactly which fields match, which don't, and which need a human look —
instead of an agent eyeballing every field by hand.

Built in response to the discovery interviews with TTB's Label Compliance
Division. The design choices below trace directly back to specific quotes
from those interviews (cited inline).

## Approach, tools, and assumptions

**Approach:** two-tier label extraction — a free, fully offline OCR reader
by default, with an optional Claude Vision upgrade for harder photos — feeds
into a matching engine that's deliberately lenient about cosmetic
differences (casing, punctuation, spacing) but strict about the one field
regulation doesn't allow any deviation on (the government warning text).
Every field the tool can't confidently judge is flagged for a human rather
than guessed at, in either direction — see
[Why it's built this way](#why-its-built-this-way) for the full reasoning
tied back to specific discovery-interview feedback.

**Tools used:**
- **Backend:** Python 3.10+, FastAPI, Pydantic, Uvicorn
- **Label reading:** [`rapidocr-onnxruntime`](https://github.com/RapidAI/RapidOCR) (free, offline, ONNX-based OCR — the default) and, optionally, Claude (Vision) via the `anthropic` Python SDK
- **Frontend:** plain HTML/CSS/JavaScript — no framework, no build step
- **Testing:** `pytest`
- **Sample data:** synthetic label images generated with Pillow (`sample_labels/generate_samples.py`), since no real submitted label photos were available

**Key assumptions made** (see [Known limitations & trade-offs](#known-limitations--trade-offs) and [TTB requirements coverage](#ttb-requirements-coverage) for the complete list and citations):
- Each review is against one flat label image with no notion of multiple
  label panels, so layout-only rules (e.g. brand/ABV/class-type needing to
  share the same field of vision) can't be checked.
- A field OCR reports as "missing" might genuinely be absent from the label,
  or the reader might have just missed it — the tool doesn't try to guess
  which, and always defers to a human rather than picking a side.
- Alcohol content is only mandatory for beer in the narrower cases 27 CFR
  7.65 specifies, not universally, since a compliant beer label can
  legitimately omit it.
- The tool checks that the label and application *agree*, not that the
  class/type designation is itself a legally valid one under TTB's own
  regulations — that's a separate, larger problem.
- This is a standalone prototype with no COLA integration, authentication,
  audit logging, or retention policy, per explicit scoping in the discovery
  conversation — a production version handling real submissions would need
  all of those.
- Batch manifest matching assumes each uploaded image's filename is unique
  and matches the manifest CSV's `filename` column exactly.

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
  that time. Single-label review runs in roughly 1.8-3.5 seconds for a
  normal-quality image on a modest 4-core dev machine, comfortably under her
  ~5s bar. Getting there took a specific fix, not just "OCR is inherently
  fast enough" — see [Performance tuning](#performance-tuning-hitting-the-5-second-target).
  A badly degraded photo (see [Handling imperfect photos](#handling-imperfect-photos-angle-lighting-glare))
  can cost more than that, on purpose — the alternative is an automatic
  reject, which costs the applicant far more real time than a slow response.
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
- **Simple, obvious UI.** Two tabs, big buttons, color-coded pass/fail/review
  badges, no settings to hunt for — aimed at Sarah's "my 73-year-old mother"
  bar, not Jenny's.
- **Standalone, not integrated with COLA.** Per Marcus: COLA integration is a
  separate, much bigger effort with its own authorization requirements. This
  is a proof of concept that could inform that decision later.

## Quickstart

Requires Python 3.10+. **3.12 is recommended** and pinned via `.python-version`
for anyone deploying this — very recent Python releases (3.14, for example)
don't yet have full wheel coverage for every dependency here on every
platform; this bit a real deploy attempt (see
[Deployment](#deployment)) before being pinned.

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

**Batch Upload** tab: download the manifest CSV template, fill in one row
per application (the `filename` column must match an uploaded image's
filename), select all the label images, click Review Batch. You get a
summary count plus a sortable-by-glance table; click any row to expand its
field-by-field detail.

### Extraction backends

Two interchangeable backends implement the same interface
([`extraction/base.py`](backend/app/extraction/base.py)):

| | OCR (default) | Vision (optional) |
|---|---|---|
| Engine | `rapidocr-onnxruntime` (local, pure Python, ONNX) | Claude (multimodal) via the Anthropic API |
| Cost | Free | Per-image API cost |
| Network | None required | Requires outbound HTTPS to the Anthropic API |
| Setup | `pip install`, nothing else | Set `ANTHROPIC_API_KEY` env var |
| Speed | ~1.8-3.5s/label normally (this machine, after tuning — see below); up to ~15s if the image is degraded enough to need the contrast-recovery fallback | Not benchmarked here (no key available in dev); typically a few seconds, network-dependent |
| Field extraction | Regex + a layout heuristic (see below) | The model directly returns structured fields |
| Bold/formatting detection | **Cannot detect bold at all** — flagged as "needs review" every time | Can directly assess whether the warning header is bold |
| Handles blurry/angled photos | Weaker — text detector can miss whole lines | Much stronger (this was Jenny's specific ask) |

The app auto-selects Vision if an API key is available, otherwise OCR. You
can also force one via `?method=ocr` or `?method=vision` on either API
endpoint, e.g. for side-by-side comparison.

**Why OCR is the default rather than an afterthought:** given Marcus's
firewall/change-control comments, a tool that only works with a cloud API key
would likely never survive contact with TTB's actual network. The free path
had to be the one that works out of the box, with no account, key, or setup
required.

### Switching reading methods in the app

Click **⚙ Reading settings** near the top of either tab to see which
reader is currently active and switch it:

- **Switch to the Free Reader** — a one-click override that forces every
  review to use the free OCR reader for the rest of the browser session,
  regardless of any key (yours or the server's). Useful for comparing both
  extraction methods side by side, and doubles as a self-serve fallback if
  Vision ever has an issue (an exhausted key, a rate limit, a transient API
  outage) — an evaluator isn't stuck looking at an error, they can just
  switch. This sends `?method=ocr` on that request (see
  [API reference](#api-reference)); nothing else changes.
- **Advanced: use your own Anthropic API key** (collapsed by default) —
  paste your own key to use Vision under your own account instead of
  whatever this app is currently defaulting to. Don't have one?
  1. Go to [console.anthropic.com](https://console.anthropic.com) and sign up or log in.
  2. Go to **Settings → API Keys**, click **Create Key**, and copy it (it's only shown once).
  3. Add a payment method under **Settings → Billing** — usage is billed per
     request, typically a few cents per label reviewed.
  4. Paste the key in and click "Use for This Session."

  Your key is kept only in that browser tab's session storage — cleared
  automatically when the tab closes, so it doesn't linger on a shared or
  public computer. It's sent with each review request as an
  `X-Anthropic-Api-Key` header straight to this app's own backend, which
  uses it to call Claude on your behalf for that request only (it's never
  cached, logged, or reused for anyone else's request — see
  `get_extractor()` in [`extraction/factory.py`](backend/app/extraction/factory.py)).

**What's active by default** depends on how this instance is run: locally
with no configuration, that's the free OCR reader (see
[Quickstart](#quickstart)); the publicly deployed demo linked above has
`ANTHROPIC_API_KEY` configured server-side, so Vision is the default there
with no setup needed from a visitor — the status line always shows which
one is actually in effect, since it checks the live `/api/health` response
rather than assuming. A server-side key is a platform-managed secret on the
host, never in source control (see [Deployment](#deployment)), and always
loses to a visitor's own key or the free-reader override if either is set.

## Performance tuning: hitting the 5-second target

An earlier version of this tool measured 4-9s/label — close to, but not
confidently under, Sarah's ~5s bar. Profiling (`text_detector`/`text_cls`/
`text_recognizer` each report their own elapsed time) showed detection and
classification were both fast; **recognition** — the step that reads the
text out of each detected line — was 80-90% of the total, at roughly
1-4.5s depending on how many lines the label had.

The actual cause wasn't compute, it was **thread oversubscription**: ONNX
Runtime's default execution plan spins up a multi-threaded worker pool
(sized to the machine's core count) for every inference call. For a model
this small — a handful of 48×320px text-line crops — the overhead of
synchronizing that thread pool measured *higher* than the compute it was
supposed to parallelize. Forcing single-threaded ONNX execution
(`intra_op_num_threads=1`, `inter_op_num_threads=1`) cut total time roughly
in half on its own. RapidOCR doesn't expose a thread-count option, so this
is applied by patching the `SessionOptions` class it builds its sessions
from — see `_patch_onnx_single_threaded()` in
[`ocr_extractor.py`](backend/app/extraction/ocr_extractor.py).

RapidOCR's angle classifier — a whole extra model pass per detected line to
check whether it's upside-down — was disabled in an earlier draft on the
assumption that a label photo is essentially never rotated 180°. That
assumption doesn't hold: this image arrives as part of an applicant's
submission, not a photo a TTB agent takes themselves, so a submitter could
send it in any orientation (the same image-quality unpredictability Jenny
raised in the discovery notes). Since the classifier's measured cost is
negligible anyway (<0.1s), it stays on — there was never a real tradeoff
to make here, just a wrong assumption caught before it shipped.

One thing that looked promising but wasn't, worth recording so it isn't
re-tried later: **increasing the recognizer's batch size** (to fit more
detected lines into a single inference call, hoping to cut the *number* of
calls) made things slower, not faster — batching pads every crop in a
batch to the width of the longest one, so grouping a short line (like the
warning header) with a long wrapped paragraph line wastes compute on
padding. The default batch size of 6 was already a reasonable balance and
was left alone.

Net effect, measured end-to-end through the actual browser (not a
synthetic benchmark) across the six normal-quality sample labels:
**1.8-3.5 seconds**, down from 4.9-9.6 seconds — comfortably under Sarah's
bar in every case tested. This was measured on a 4-core dev machine inside
a shared/sandboxed environment; real deployment hardware would likely do at
least as well. (The four deliberately degraded samples added later —
angle, lighting, glare, upside-down — trade some of that speed back for
correctness; see [Handling imperfect photos](#handling-imperfect-photos-angle-lighting-glare).)

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
  wrong about themselves, independent of what the application says. This
  field is optional on the application: it's only checked if provided, since
  it's not unconditionally mandatory for malt beverages (27 CFR 7.65) the
  way it is for spirits and wine — see [TTB requirements coverage](#ttb-requirements-coverage) below.
- **Net contents:** same fuzzy-then-numeric approach, with unit conversion
  (mL/L/fl oz) before comparing quantities.
- **Bottler/importer name and address:** same fuzzy text comparison as
  brand/class-type. Optional on the application for backward compatibility,
  but this is mandatory on every real label (27 CFR 5.66-5.68 for spirits,
  4.35 for wine, 7.66-7.68 for malt beverages) — see below.
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

**Position in reading order breaks completely if the whole image is
upside-down**, though, and that's a real scenario here, not a hypothetical
one: this image is part of an application a *submitter* sends in, not a
photo a TTB agent frames and takes themselves, so it can arrive rotated.
RapidOCR's built-in angle classifier corrects each line's *text* for this
(so a word still reads correctly even upside-down), but not the page
*layout* the heuristic above depends on — an inverted image still gets
read top-to-bottom in image coordinates, which is now bottom-to-top
relative to the real label (caught during testing: an upside-down sample
extracted "health problems." — the actual last line of the warning
statement — as the brand name). The fix: the government warning is always
the label's last content block, so on a right-way-up label it should sit
below most other text; if OCR finds it sitting *above* most other text
instead, the image is very likely inverted, so it gets rotated 180° and
re-extracted automatically (double the OCR cost, but only for this rare
case — see `sample_labels/upside_down_label.png` and
[`ocr_extractor.py`](backend/app/extraction/ocr_extractor.py)).

## TTB requirements coverage

The spec's own list of common label elements was checked directly against
TTB's published mandatory-information checklists rather than assumed
correct — TTB publishes a separate checklist per beverage type, since the
rules genuinely differ:

- [Distilled Spirits Labeling: Checklist of Mandatory Label Information](https://www.ttb.gov/regulated-commodities/beverage-alcohol/distilled-spirits/ds-labeling-home/ds-checklist) ([PDF](https://www.ttb.gov/system/files/images/labeling-ds/ds-labeling-checklist.pdf), 27 CFR part 5)
- [Wine Labeling: Checklist of Mandatory Label Information](https://www.ttb.gov/regulated-commodities/beverage-alcohol/wine/labeling-wine/wine-labeling-checklist-of-mandatory-label-information) ([PDF](https://www.ttb.gov/system/files/images/wine-label/wine-labeling-checklist.pdf), 27 CFR part 4)
- [Malt Beverage Labeling: Checklist of Mandatory Label Information](https://www.ttb.gov/beer/labeling/malt-beverage-labeling-checklist) ([PDF](https://www.ttb.gov/system/files/images/beer/labeling/malt-beverage-labeling-checklist-information.pdf), 27 CFR part 7)

**What this found and what changed as a result:**

- **Bottler/importer name and address was completely missing.** It's
  mandatory on every real label across all three beverage types (27 CFR
  5.66-5.68 for spirits, 4.35 for wine, 7.66-7.68 for malt beverages) — and
  it was even in the original spec's own list of common elements, so this
  wasn't a new requirement to discover, just one that hadn't been wired up
  yet. Now extracted, compared, and optional on the application (for
  backward compatibility with data collected before the field existed) —
  see `bottler_name_address` in [`models.py`](backend/app/models.py). While
  fixing this, a real bug turned up in the sample generator: the "Produced
  and Bottled by..." line was hardcoded to "Old Tom Distillery" regardless
  of the label's actual brand, so every non-Old-Tom sample was printing a
  bottler line that didn't match its own brand name. Fixed to use the
  label's real brand by default.
- **Alcohol content isn't unconditionally mandatory for malt beverages.**
  The spec's own brief already flagged this as a nuance ("with some
  exceptions for certain wine/beer"), and TTB's checklist confirms exactly
  what the exception is: for beer, an ABV statement is required only if the
  product contains alcohol derived from added flavors or non-beverage
  ingredients (excluding hop extract), or if a state requires it (27 CFR
  7.65) — otherwise it's legitimately absent. `alcohol_content` is now
  optional on the application (previously it was required for every
  beverage type, which would have wrongly flagged a compliant beer label
  with no ABV statement as missing one).
- **The health warning statement's exact requirements were already
  right**, cross-checked against TTB's own checklist wording: exact text,
  "GOVERNMENT WARNING" in capital letters and bold, and the "S" in Surgeon
  and "G" in General capitalized. Matches [`warning_text.py`](backend/app/warning_text.py)
  and the strict (non-fuzzy) comparison in `compare_warning()`.

**What TTB requires that this tool deliberately does not check**, because
doing so would mean collecting production details this application form
was never scoped to capture — brand name, class/type, ABV, net contents,
warning, country of origin, and bottler/importer are the core fields this
tool is built around, and these are additional, narrower disclosures:

| Requirement | When it applies | Citation |
|---|---|---|
| Sulfite declaration | Product has ≥10 ppm total SO₂ | 27 CFR 5.63(c)(7) / 4.32(e) / 7.63(b)(3) |
| FD&C Yellow #5 disclosure | That colorant is used | 27 CFR 5.63(c)(5) / 4.32(c) / 7.63(b)(1) |
| Cochineal extract/carmine disclosure | Either is used | 27 CFR 5.63(c)(6) / 4.32(d) / 7.63(b)(2) |
| Aspartame declaration | Beer containing aspartame | 27 CFR 7.63(b)(4) |
| Statement of age | Whisky aged <4 years, certain brandies, etc. | 27 CFR 5.74 |
| Treatment with wood | Whisky/brandy treated with wood other than oak containers | 27 CFR 5.73 |
| State of distillation | Certain whisky not distilled in its labeled state | 27 CFR 5.66(f) |
| Commodity statements | Neutral spirits/gin from continuous distillation, or blends | 27 CFR 5.71 |
| Appellation of origin | Wine with a varietal, vintage, or semi-generic designation | 27 CFR 4.25, 4.34 |
| Percentage of foreign wine | Blends of American and foreign wine, if labeled as such | 27 CFR 4.32(a)(4) |
| "Same field of vision" placement | Brand, ABV, and class/type on spirits labels | 27 CFR 5.63 |

That last one is worth calling out specifically: it's a *layout* rule
(these three fields must be visible together without turning the bottle),
not a content rule, and this tool only ever sees one flat image with no
notion of "which side of the bottle is this," so it isn't something OCR or
Vision extraction could check even if scope allowed for it.

## Handling imperfect photos (angle, lighting, glare)

Jenny's discovery-note complaint was specific: agents currently reject any
label they can't read cleanly and ask for a re-shoot, rather than the tool
handling *some* of that itself. This was tested directly rather than
assumed — `sample_labels/steep_angle_label.png` (20° tilt),
`dark_noisy_label.png` (dark + sensor noise, simulating a cheap phone photo
in bad light), and `glare_label.png` (a bright reflection over part of the
label) all exercise this.

**Finding: brand name, class/type, ABV, and net contents survive this kind
of degradation almost every time on their own** — RapidOCR's models turned
out more tolerant of angle, darkness, and noise than expected. **The
government warning is the one field that can go completely undetected**,
since it's the longest, most spatially spread-out text block, so it's the
most likely to have some portion cut off or missed under any distortion.

For that specific failure, `extract()` has a fallback: if the warning
wasn't found on the first pass, it retries with a contrast-enhanced version
of the image before giving up. Two techniques were tested (autocontrast,
histogram equalization) and neither one is a strict improvement over the
other — autocontrast recovered the dark-and-noisy sample but did nothing
for the glare sample; equalization was the reverse, and made a *different*
noisy sample much worse by amplifying its noise. So rather than applying
either unconditionally (real regression risk — a "fix" that sometimes makes
things worse isn't safe to always run), each is tried in turn only when the
warning is missing, keeping only the recovered warning text and discarding
everything else from that attempt — brand/class/etc. stay from the original
pass, since an enhancement that rescues the warning can still quietly hurt
some other field it didn't need to touch (equalization measurably did this
to brand-name accuracy on one test image before that isolation was added).

This trades speed for the rare case, on purpose: a normal-quality image is
still a single OCR pass at 2-5s, but an image degraded enough to lose the
warning entirely now costs up to 3 passes — measured 8-15s end-to-end on
`dark_noisy_label.png` and `glare_label.png`. That's well past the 5s
target, but the alternative is what Jenny described: an automatic reject
and a request for a new photo, which costs far more real time than a slow
response. The outcome in every tested case was `needs_review` (or the
correct `fail` for a genuine violation), never a false pass and never a
crash — see the table this produces in [Known limitations](#known-limitations--trade-offs).

One correctness fix fell directly out of this testing: OCR occasionally
inserts a stray space before the warning header's colon ("WARNING :")
purely as a character-segmentation artifact. That was being treated as "the
required header isn't there" — a hard, unconditional fail — which would
have wrongly rejected a label that's actually fully compliant, just poorly
photographed. Fixed by normalizing that specific whitespace before the
header check, without touching case (so Jenny's real "Government Warning"
title-case violation still fails correctly — see
`test_warning_title_case_with_stray_space_is_still_mismatch` in
[`test_matching.py`](backend/tests/test_matching.py)).

## Known limitations & trade-offs

- **Batch throughput is currently sequential for the OCR path, on purpose.**
  This was tuned specifically for single-label latency (each OCR call now
  runs single-threaded — see [Performance tuning](#performance-tuning-hitting-the-5-second-target)).
  Retesting batch concurrency after that change showed only a modest ~10%
  gain from running several single-threaded calls at once on this 4-core
  machine — real, but not the dramatic win true multi-core parallelism would
  give, likely because the batch shares one engine instance and Python-level
  pre/post-processing (image decode, cropping) still serializes on the GIL.
  A production batch path would get more out of separate worker processes
  (one engine instance each, no GIL contention) sized to actual core count,
  or would default heavy batches to the Vision backend, where concurrency is
  real (network-bound calls, not CPU-bound ones) and is already enabled here.
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
  bottles, since no real label photos were available — including the ones
  built specifically to exercise angle, lighting, and glare (see
  [Handling imperfect photos](#handling-imperfect-photos-angle-lighting-glare)).
  They're a reasonable approximation, but a real phone photo of a real
  printed label has failure modes these don't fully capture: glare off
  curved glass rather than a flat overlay, actual JPEG compression from a
  real camera, uneven real-world lighting rather than a synthetic gradient
  or noise filter. The tested robustness is real, but the true worst case
  on an actual submitted photo could still be somewhat harder than these
  results suggest.

## API reference

- `GET /api/health` — status check, reports which extraction method is active by default.
- `GET /api/manifest-template` — downloads the batch CSV template.
- `POST /api/review` — single review. Multipart form: `image` (file) +
  `brand_name`, `class_type`, `net_contents`, `beverage_type` (required);
  `alcohol_content`, `government_warning`, `country_of_origin`,
  `bottler_name_address` (all optional — see
  [TTB requirements coverage](#ttb-requirements-coverage) for why alcohol
  content in particular isn't always required). Optional `?method=ocr|vision`
  query param.
- `POST /api/review/batch` — batch review. Multipart form: `manifest` (CSV
  file) + `images` (multiple files). Same optional `?method=` param.

Both review endpoints also accept an optional `X-Anthropic-Api-Key` header
— a caller-supplied key that switches that single request to Vision without
needing `ANTHROPIC_API_KEY` set on the server (see
[Switching reading methods in the app](#switching-reading-methods-in-the-app)).

## Deployment

The app is a single FastAPI process serving both the API and the static
frontend — no separate frontend build/deploy step.

**Render / Railway / Fly.io (Python buildpack):**
- Root/start directory: `backend`
- Build: `pip install -r requirements.txt`
- Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Optional env var: `ANTHROPIC_API_KEY` — set this on the deployment itself
  to make Vision the default for every visitor, with no key of their own
  required (this is how the publicly deployed demo is configured, so it
  demonstrates the AI reading path out of the box). **Set this as a
  platform-managed environment variable/secret in the hosting dashboard —
  never commit a real key into source control**, since this repo is public.
  The free OCR path stays fully intact in the code either way — a visitor
  can still force it via `?method=ocr`, and the app keeps working on OCR
  alone if this key is ever removed or its credits run out.
- Optional env var: `ALLOWED_ORIGINS` — comma-separated list of origins
  allowed to call the API cross-origin (defaults to `localhost:8000` /
  `127.0.0.1:8000`, which only matters for local development anyway, since
  the frontend is served from this same origin in production — see
  [Deployment/CORS](backend/app/main.py)). Set this to your deployed
  origin, e.g. `https://ttb-review.example.gov`, only if something *else*
  needs to call this API from a different origin.

**Docker** (a `Dockerfile` is included at the repo root):

```bash
docker build -t ttb-label-review .
docker run -p 8000:8000 ttb-label-review
```

The OCR path's ONNX model files ship bundled inside the
`rapidocr-onnxruntime` pip package itself — nothing is downloaded at
runtime, so there's genuinely zero network dependency for the free reader,
not just a one-time download (worth calling out explicitly, since this is a
stronger version of the offline guarantee than it might first appear — see
[Why it's built this way](#why-its-built-this-way)). Loading them into an
ONNX Runtime session takes ~3.4s the first time it happens (measured
locally), which is done eagerly at server startup rather than on the first
request — see `lifespan()` in [`main.py`](backend/app/main.py) — so that
cost lands during deploy, invisible to the first real visitor, not during
whatever they're timing.

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
  requirements.txt
frontend/
  index.html / style.css / app.js   Plain HTML/JS UI, no build step
sample_labels/
  generate_samples.py   Generates synthetic test label images
  manifest.csv           Example batch manifest for the generated samples
```
