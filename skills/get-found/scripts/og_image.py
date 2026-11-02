#!/usr/bin/env python3
"""Generate clean 1200x630 Open Graph preview images (fallback when the framework has no
built-in OG image generation). Requires Pillow: pip install pillow

  og_image.py --title "How to cut clinic no-shows by 40%" --brand "Acme Health" --out public/og/no-shows.png
  og_image.py --batch og-jobs.json        # [{"title": "...", "subtitle": "...", "out": "public/og/x.png"}, ...]

Options: --subtitle, --bg "#0b1220" --fg "#ffffff" --accent "#14b8a6", --logo path/to/logo.png, --font path.ttf,
         --format png|jpg. Keeps text inside safe margins so platforms that crop to a square still read well.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("Pillow is required: pip install pillow (or use your framework's OG image generator — see references/frameworks.md)")

W, H = 1200, 630
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


def load_font(path: str | None, size: int):
    for p in ([path] if path else []) + FONT_CANDIDATES:
        if p and Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=font) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def render(job: dict, defaults: dict):
    o = {**defaults, **{k: v for k, v in job.items() if v is not None}}
    img = Image.new("RGB", (W, H), hex2rgb(o["bg"]))
    d = ImageDraw.Draw(img)
    margin = 80
    d.rectangle([0, H - 12, W, H], fill=hex2rgb(o["accent"]))
    y = margin
    brand_font = load_font(o.get("font"), 30)
    if o.get("logo") and Path(o["logo"]).exists():
        logo = Image.open(o["logo"]).convert("RGBA")
        logo.thumbnail((220, 72))
        img.paste(logo, (margin, y), logo)
        if o.get("brand"):  # beside the logo, so it can never collide with the title or subtitle below
            d.text((margin + logo.width + 24, y + max(0, (logo.height - 30) // 2)), o["brand"],
                   font=brand_font, fill=hex2rgb(o["accent"]))
        y += logo.height + 28
    elif o.get("brand"):
        d.text((margin, y), o["brand"], font=brand_font, fill=hex2rgb(o["accent"]))
        y += 60
    for size in range(72, 39, -6):
        f = load_font(o.get("font"), size)
        lines = wrap(d, o["title"], f, W - 2 * margin)
        if len(lines) <= 3:
            break
    for line in lines[:4]:
        d.text((margin, y), line, font=f, fill=hex2rgb(o["fg"]))
        y += int(size * 1.18)
    if o.get("subtitle"):
        y += 16
        sf = load_font(o.get("font"), 32)
        for line in wrap(d, o["subtitle"], sf, W - 2 * margin)[:2]:
            y += 10
            d.text((margin, y), line, font=sf, fill=tuple(int(c * 0.75) for c in hex2rgb(o["fg"])))
            y += 40
    out = Path(o["out"])
    out.parent.mkdir(parents=True, exist_ok=True)
    if o.get("format") == "jpg" or out.suffix.lower() in (".jpg", ".jpeg"):
        img.save(out.with_suffix(".jpg"), "JPEG", quality=85, optimize=True, progressive=True)
        out = out.with_suffix(".jpg")
    else:
        img = img.convert("P", palette=Image.ADAPTIVE, colors=128)
        img.save(out, "PNG", optimize=True)
    print(f"{out}  ({out.stat().st_size // 1024} KB, {W}x{H})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--title"); ap.add_argument("--subtitle"); ap.add_argument("--brand")
    ap.add_argument("--bg", default="#0b1220"); ap.add_argument("--fg", default="#ffffff"); ap.add_argument("--accent", default="#14b8a6")
    ap.add_argument("--logo"); ap.add_argument("--font"); ap.add_argument("--format", choices=["png", "jpg"], default="png")
    ap.add_argument("--out"); ap.add_argument("--batch")
    a = ap.parse_args()
    defaults = {k: getattr(a, k) for k in ("subtitle", "brand", "bg", "fg", "accent", "logo", "font", "format")}
    if a.batch:
        defaults.pop("subtitle")  # a subtitle is per page; --subtitle must not land on every batch image
        for i, job in enumerate(json.loads(Path(a.batch).read_text(encoding="utf-8"))):
            missing = [k for k in ("title", "out") if not job.get(k)]
            if missing:
                print(f"skipping job {i}: missing {', '.join(missing)}", file=sys.stderr)
                continue
            render(job, defaults)
    elif a.title and a.out:
        render({"title": a.title, "out": a.out}, defaults)
    else:
        ap.error("give --title and --out, or --batch")


if __name__ == "__main__":
    main()
