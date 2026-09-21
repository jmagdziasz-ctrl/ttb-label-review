"""Generates synthetic label images for testing, since we don't have real
bottle photos. Each one exercises a different scenario from the discovery
notes. Run: python generate_samples.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from app.warning_text import CANONICAL_WARNING, WARNING_BODY, WARNING_HEADER  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent
W, H = 900, 1200


def _font(size: int, bold: bool = False):
    candidates = (
        ["arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf"]
        if bold
        else ["arial.ttf", "Arial.ttf", "DejaVuSans.ttf"]
    )
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(draw, text, font, max_width):
    words = text.split()
    lines, current = [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def make_label(
    filename: str,
    brand_name: str = "OLD TOM DISTILLERY",
    class_type: str = "Kentucky Straight Bourbon Whiskey",
    abv_line: str = "45% Alc./Vol. (90 Proof)",
    net_contents: str = "750 mL",
    warning_header: str = WARNING_HEADER,
    warning_body: str = WARNING_BODY,
    warning_bold: bool = True,
    warning_upper_override: str | None = None,
    blur: bool = False,
    rotate: float = 0,
    jpeg_noise: bool = False,
):
    img = Image.new("RGB", (W, H), color=(245, 240, 225))
    draw = ImageDraw.Draw(img)

    draw.rectangle([20, 20, W - 20, H - 20], outline=(80, 60, 30), width=6)

    y = 90
    brand_font = _font(56, bold=True)
    for line in _wrap(draw, brand_name, brand_font, W - 160):
        draw.text((W / 2, y), line, font=brand_font, fill=(30, 20, 10), anchor="ma")
        y += 66
    y += 20

    class_font = _font(34)
    for line in _wrap(draw, class_type, class_font, W - 160):
        draw.text((W / 2, y), line, font=class_font, fill=(50, 35, 20), anchor="ma")
        y += 42
    y += 40

    abv_font = _font(30)
    draw.text((W / 2, y), abv_line, font=abv_font, fill=(50, 35, 20), anchor="ma")
    y += 45
    draw.text((W / 2, y), net_contents, font=abv_font, fill=(50, 35, 20), anchor="ma")
    y += 80

    draw.text((W / 2, y), "Produced and Bottled by Old Tom Distillery, Bardstown, KY",
              font=_font(20), fill=(60, 45, 25), anchor="ma")
    y += 60

    header_text = warning_upper_override if warning_upper_override is not None else warning_header
    header_font = _font(24, bold=warning_bold)
    draw.text((W / 2, y), header_text, font=header_font, fill=(10, 10, 10), anchor="ma")
    y += 34

    body_font = _font(20)
    for line in _wrap(draw, warning_body, body_font, W - 140):
        draw.text((W / 2, y), line, font=body_font, fill=(10, 10, 10), anchor="ma")
        y += 26

    if rotate:
        img = img.rotate(rotate, expand=True, fillcolor=(200, 200, 200))
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(radius=4))

    out_path = OUT_DIR / filename
    img.save(out_path, quality=60 if jpeg_noise else 95)
    print(f"wrote {out_path}")


def main():
    # 1. Clean, fully-compliant label matching the spec's sample fields.
    make_label("old_tom_bourbon_correct.png")

    # 2. Dave's "STONE'S THROW" vs "Stone's Throw" case-only difference —
    #    should be flagged as a minor/formatting match, not a mismatch.
    make_label(
        "stones_throw_case_diff.png",
        brand_name="Stone's Throw",
        class_type="Straight Rye Whiskey",
        abv_line="40% Alc./Vol. (80 Proof)",
    )

    # 3. Genuine ABV mismatch: label says 40%, application will claim 45%.
    make_label(
        "wrong_abv_label.png",
        brand_name="RIVERBEND DISTILLERS",
        class_type="Blended Whiskey",
        abv_line="40% Alc./Vol. (80 Proof)",
    )

    # 4. Warning statement reworded and header in title case — must fail.
    make_label(
        "bad_warning_label.png",
        brand_name="HARBOR LIGHT SPIRITS",
        class_type="London Dry Gin",
        abv_line="47% Alc./Vol. (94 Proof)",
        warning_upper_override="Government Warning:",
        warning_body=(
            "Drinking alcohol during pregnancy can cause birth defects. "
            "Alcohol impairs your ability to drive or operate machinery."
        ),
        warning_bold=False,
    )

    # 5. Poor-quality photo (blur + slight rotation) — should trigger a
    #    low-confidence / needs-review outcome rather than a false mismatch.
    make_label(
        "blurry_label.png",
        brand_name="OLD TOM DISTILLERY",
        class_type="Kentucky Straight Bourbon Whiskey",
        abv_line="45% Alc./Vol. (90 Proof)",
        blur=True,
        rotate=3,
        jpeg_noise=True,
    )

    # 6. Internally inconsistent label: 45% ABV should be 90 proof, not 100.
    make_label(
        "inconsistent_proof_label.png",
        brand_name="CROOKED CREEK",
        class_type="Straight Bourbon Whiskey",
        abv_line="45% Alc./Vol. (100 Proof)",
    )

    # 7. Submitted upside-down. This is an application attachment, not a
    #    photo a TTB agent takes themselves, so a submitter really could
    #    send it in any orientation — caught during review of this
    #    prototype, see ocr_extractor.py's orientation-detection logic.
    make_label(
        "upside_down_label.png",
        brand_name="OLD TOM DISTILLERY",
        class_type="Kentucky Straight Bourbon Whiskey",
        abv_line="45% Alc./Vol. (90 Proof)",
        rotate=180,
    )


if __name__ == "__main__":
    main()
