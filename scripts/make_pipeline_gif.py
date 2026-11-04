"""Generates docs/media/pipeline.gif: scan -> PaddleOCR -> LLM -> structured data -> profile.

Every number and line on the animation comes from a real extraction stored in the database
(OCR text, model response, verification report, cost), so nothing is invented for the picture.

    # rebuild the sample from the database and render the GIF
    uv run python scripts/make_pipeline_gif.py --from-db --mp 1 --year 2025 --kind ANNUAL

    # render from the committed sample (no database needed)
    uv run python scripts/make_pipeline_gif.py
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
MEDIA = ROOT / "docs" / "media"
SAMPLE = MEDIA / "pipeline_sample.json"
SCAN = MEDIA / "pipeline_scan.png"

W, H = 960, 540
BG = (15, 23, 42)
PANEL = (30, 41, 59)
PANEL_EDGE = (51, 65, 85)
TEXT = (226, 232, 240)
MUTED = (148, 163, 184)
ACCENT = (56, 189, 248)
GREEN = (52, 211, 153)
AMBER = (251, 191, 36)
PINK = (244, 114, 182)
RED = (248, 113, 113)

STAGES = ["1  Scan (PDF)", "2  PaddleOCR (local)", "3  LLM", "4  Verify + compute"]
FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/Library/Fonts", "/System/Library/Fonts/Supplemental"]


def font(size: int, mono: bool = False, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "DejaVuSansMono" if mono else "DejaVuSans"
    if bold:
        name += "-Bold"
    for folder in FONT_DIRS:
        path = Path(folder) / f"{name}.ttf"
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size)


F_TITLE, F_BODY, F_SMALL = font(20, bold=True), font(16), font(13)
F_MONO, F_MONO_S = font(14, mono=True), font(13, mono=True)
F_BIG = font(30, bold=True)


# --------------------------------------------------------------------------- sample data

def build_sample_from_db(mp_id: int, year: int, kind: str) -> dict:
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    import pypdfium2 as pdfium

    from apps.assets.models import AssetDeclaration
    from apps.assets.services.summary import summarize

    d = AssetDeclaration.objects.select_related("mp", "raw").get(
        mp__sejm_id=mp_id, year=year, kind=kind, is_demo=False
    )
    run = d.latest_successful_run
    if not (d.structured and d.ocr_text and run and run.cost_usd is not None):
        raise SystemExit("This declaration needs a real ocr_llm run with a recorded cost; run extract_declarations.")

    lines = [ln.strip() for ln in d.ocr_text.splitlines() if ln.strip()]
    start = next((i for i, ln in enumerate(lines) if ln.lower().startswith("zasoby")), 0)
    noise = ("===", "dziennik ustaw")
    excerpt = [ln for ln in lines[start:] if "adres" not in ln.lower() and not ln.lower().startswith(noise)][:12]

    def amount(money):
        return float(money["amount"]) if money else None

    s = d.structured
    trimmed = {
        "savings_pln": [{"label": x["label"], "amount": amount(x["money"]), "quote": x["quote"]}
                        for x in s.get("savings_pln", [])[:1]],
        "insurance": [{"label": x["label"], "amount": amount(x["money"])} for x in s.get("insurance", [])[:2]],
        "real_estate": [{"kind": x["kind"], "area_m2": float(x["area_m2"]) if x.get("area_m2") else None,
                         "amount": amount(x.get("value"))}
                        for x in s.get("real_estate", [])[:2]],
        "liabilities": [{"kind": x["kind"], "amount": amount(x["amount"])} for x in s.get("liabilities", [])[:1]],
    }
    trimmed = {k: v for k, v in trimmed.items() if v}
    wealth = summarize(d)
    v = d.verification or {}

    pdf = pdfium.PdfDocument(d.raw.file.read())
    page = pdf[0].render(scale=0.7).to_pil().convert("RGB")
    page = page.filter(ImageFilter.GaussianBlur(3)).resize((int(page.width * 360 / page.height), 360))
    MEDIA.mkdir(parents=True, exist_ok=True)
    page.save(SCAN, optimize=True)

    return {
        "ocr": {"engine": d.ocr_engine, "confidence": d.ocr_confidence, "excerpt": excerpt},
        "llm": {
            "model": run.model,
            "prompt_tokens": run.prompt_tokens,
            "completion_tokens": run.completion_tokens,
            "cost_usd": float(run.cost_usd),
            "calls": run.llm_calls,
        },
        "structured": trimmed,
        "verification": {
            "checks": v.get("checks", 0),
            "issues": len(v.get("issues") or []),
            "confidence": v.get("confidence", d.confidence),
        },
        "summary": {
            "net_worth": float(wealth.net_worth),
            "savings": float(wealth.savings),
            "real_estate": float(wealth.real_estate),
            "liabilities": float(wealth.liabilities),
        },
        "declaration": {"year": year, "kind": kind},
    }


# --------------------------------------------------------------------------- drawing helpers

def short_pln(value: float) -> str:
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.2f}".replace(".", ",") + " mln zł"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.1f}".replace(".", ",").replace(",0", "") + " tys. zł"
    return f"{value:.0f} zł"


def ease(t: float) -> float:
    return 1 - (1 - t) ** 3


def new_frame(active: int | None) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    chip_w, gap, x = 192, 34, 40
    for i, label in enumerate(STAGES):
        on = i == active
        d.rounded_rectangle((x, 22, x + chip_w, 62), 10, fill=ACCENT if on else PANEL,
                            outline=ACCENT if on else PANEL_EDGE)
        d.text((x + chip_w / 2, 42), label, font=F_BODY, fill=BG if on else MUTED, anchor="mm")
        if i < len(STAGES) - 1:
            d.text((x + chip_w + gap / 2, 42), "→", font=F_TITLE, fill=MUTED, anchor="mm")
        x += chip_w + gap
    return img, d


def panel(d, box, title=None):
    d.rounded_rectangle(box, 14, fill=PANEL, outline=PANEL_EDGE)
    if title:
        d.text((box[0] + 18, box[1] + 14), title, font=F_SMALL, fill=MUTED)


def caption(d, text):
    d.text((W / 2, 514), text, font=F_SMALL, fill=MUTED, anchor="mm")


NUMBER = re.compile(r"\d[\d  .,]*\d|\d")


def rich_line(d, xy, text, fnt, base=TEXT, highlight=AMBER):
    x, y = xy
    pos = 0
    for m in NUMBER.finditer(text):
        for chunk, color in ((text[pos:m.start()], base), (m.group(), highlight)):
            if chunk:
                d.text((x, y), chunk, font=fnt, fill=color)
                x += d.textlength(chunk, font=fnt)
        pos = m.end()
    tail = text[pos:]
    if tail:
        d.text((x, y), tail, font=fnt, fill=base)


JSON_TOKEN = re.compile(r'\s+|"(?:[^"\\]|\\.)*"|-?\d[\d.]*|[{}\[\],:]|\w+')


def json_line(d, xy, line, fnt):
    x, y = xy
    for m in JSON_TOKEN.finditer(line):
        tok = m.group()
        if tok.isspace():
            color = TEXT
        elif tok.startswith('"'):
            color = ACCENT if line[m.end():].lstrip().startswith(":") else GREEN
        elif tok[0].isdigit() or tok[0] == "-":
            color = AMBER
        elif tok in "{}[],:":
            color = MUTED
        else:
            color = PINK
        d.text((x, y), tok, font=fnt, fill=color)
        x += d.textlength(tok, font=fnt)


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def json_lines(structured: dict) -> tuple[list[str], list[str]]:
    def item(obj):
        parts = []
        for k, v in obj.items():
            value = json.dumps(clip(v, 46) if isinstance(v, str) else v, ensure_ascii=False)
            parts.append(f'"{k}": {value}')
        return "{" + ", ".join(parts) + "}"

    out = ["{"]
    keys = list(structured)
    for ki, key in enumerate(keys):
        items = [item({k: v for k, v in obj.items() if k != "quote"}) for obj in structured[key]]
        comma = "," if ki < len(keys) - 1 else ""
        if len(items) == 1:
            out.append(f'  "{key}": [{items[0]}]{comma}')
        else:
            out.append(f'  "{key}": [')
            out += [f"    {it}{',' if i < len(items) - 1 else ''}" for i, it in enumerate(items)]
            out.append(f"  ]{comma}")
    out.append("}")
    quotes = [o["quote"] for key in keys for o in structured[key] if o.get("quote")]
    return out, quotes


# --------------------------------------------------------------------------- scenes

def scene_scan(sample, scan):
    frames = []
    conf = sample["ocr"]["confidence"]
    thumb = scan.copy()
    target = (70, 110)
    for i in range(8):
        img, d = new_frame(0 if i < 3 else 1)
        t = ease(min(1, (i + 1) / 3))
        x = int(-thumb.width + (target[0] + thumb.width) * t)
        img.paste(thumb, (x, target[1]))
        d.rectangle((x, target[1], x + thumb.width, target[1] + thumb.height), outline=PANEL_EDGE)
        if i >= 3:
            progress = (i - 2) / 5
            y = target[1] + int(thumb.height * progress)
            band = Image.new("RGBA", (thumb.width, 6), ACCENT + (150,))
            img.paste(band, (x, y), band)
        panel(d, (470, 110, 920, 470), "INPUT")
        d.text((492, 148), "Official declaration scan", font=F_TITLE, fill=TEXT)
        d.text((492, 180), "orka.sejm.gov.pl · PDF, image only", font=F_BODY, fill=MUTED)
        d.text((492, 206), "no text layer to copy from", font=F_BODY, fill=MUTED)
        if i >= 3:
            d.text((492, 280), "PaddleOCR reads the page", font=F_TITLE, fill=ACCENT)
            d.text((492, 312), "local CPU, no data leaves the machine", font=F_BODY, fill=MUTED)
            d.rounded_rectangle((492, 360, 892, 378), 8, outline=PANEL_EDGE)
            d.rounded_rectangle((492, 360, 492 + int(400 * (i - 2) / 5), 378), 8, fill=ACCENT)
            if i == 7 and conf:
                d.text((492, 394), f"mean confidence {conf * 100:.1f}%", font=F_BODY, fill=GREEN)
        caption(d, "The scan is blurred here on purpose; the pipeline works on the real image.")
        frames.append((img, 140))
    return frames


def scene_ocr(sample, scan):
    frames = []
    lines = sample["ocr"]["excerpt"]
    thumb = scan.resize((int(scan.width * 270 / scan.height), 270))
    steps = 7
    for i in range(steps):
        img, d = new_frame(1)
        img.paste(thumb, (40, 150))
        d.rectangle((40, 150, 40 + thumb.width, 420), outline=PANEL_EDGE)
        panel(d, (280, 90, 920, 480), f"OCR TEXT · {sample['ocr']['engine']} (excerpt)")
        shown = min(len(lines), round(len(lines) * (i + 1) / steps))
        for n, line in enumerate(lines[:shown]):
            rich_line(d, (300, 124 + n * 28), clip(line, 70), F_MONO)
        caption(d, "Raw OCR text: typos, dotted lines and layout noise included.")
        frames.append((img, 150 if i < steps - 1 else 900))
    return frames


def scene_llm(sample):
    frames = []
    llm = sample["llm"]
    ocr_box, llm_box, out_box = (50, 170, 270, 350), (360, 150, 600, 370), (690, 170, 910, 350)
    for i in range(10):
        img, d = new_frame(2)
        panel(d, ocr_box)
        d.text((ocr_box[0] + 110, ocr_box[1] + 60), "rules +", font=F_BODY, fill=MUTED, anchor="mm")
        d.text((ocr_box[0] + 110, ocr_box[1] + 90), "JSON schema +", font=F_BODY, fill=MUTED, anchor="mm")
        d.text((ocr_box[0] + 110, ocr_box[1] + 120), "OCR text", font=F_TITLE, fill=TEXT, anchor="mm")
        d.rounded_rectangle(llm_box, 14, fill=PANEL, outline=ACCENT, width=2)
        d.text((480, 190), "LLM", font=F_BIG, fill=ACCENT, anchor="mm")
        model = llm["model"]
        for n, part in enumerate(re.findall(r".{1,24}", model.replace("/", "/ "))[:3]):
            d.text((480, 236 + n * 22), part.strip(), font=F_SMALL, fill=TEXT, anchor="mm")
        d.text((480, 330), "copy, don't sum", font=F_SMALL, fill=MUTED, anchor="mm")
        panel(d, out_box)
        d.text((out_box[0] + 110, out_box[1] + 90), "JSON", font=F_TITLE, fill=TEXT, anchor="mm")
        d.text((out_box[0] + 110, out_box[1] + 118), "items + quotes", font=F_BODY, fill=MUTED, anchor="mm")
        if i < 4:
            px = 280 + (350 - 280) * ease(i / 3)
            d.ellipse((px - 7, 253, px + 7, 267), fill=AMBER)
            d.text((px, 292), f"{llm['prompt_tokens']:,} tokens in".replace(",", " "), font=F_SMALL, fill=AMBER,
                   anchor="mm")
        elif i < 7:
            d.text((480, 395), "thinking" + "." * (i - 3), font=F_BODY, fill=ACCENT, anchor="mm")
        else:
            px = 610 + (680 - 610) * ease((i - 7) / 2)
            d.ellipse((px - 7, 253, px + 7, 267), fill=GREEN)
            d.text((px, 292), f"{llm['completion_tokens']:,} tokens out".replace(",", " "), font=F_SMALL,
                   fill=GREEN, anchor="mm")
        caption(d, "The model only transcribes entries into a fixed JSON schema.")
        frames.append((img, 160))
    return frames


def scene_json(sample):
    frames = []
    lines, quotes = json_lines(sample["structured"])
    steps = 8
    for i in range(steps):
        img, d = new_frame(2)
        panel(d, (40, 90, 920, 480), "MODEL RESPONSE · structured JSON")
        shown = min(len(lines), round(len(lines) * (i + 1) / steps))
        for n, line in enumerate(lines[:shown]):
            json_line(d, (62, 124 + n * 22), line, F_MONO_S)
        if quotes and i == steps - 1:
            d.text((62, 330), "Every entry also carries a verbatim quote from the OCR text:", font=F_SMALL, fill=MUTED)
            json_line(d, (62, 360), '"quote": ' + json.dumps(clip(quotes[0], 70), ensure_ascii=False), F_MONO_S)
        caption(d, "Amounts keep their original currency; nothing is summed by the model.")
        frames.append((img, 170 if i < steps - 1 else 1100))
    return frames


def scene_verify(sample):
    frames = []
    v = sample["verification"]
    rows = ["amounts appear in the OCR text", "quotes match the scan text", "currency, declarant and year match"]
    for i in range(5):
        img, d = new_frame(3)
        panel(d, (180, 100, 780, 460), "AUTOMATIC VERIFICATION · no LLM involved")
        for n, row in enumerate(rows):
            if i >= n + 1:
                d.text((220, 170 + n * 50), "✓", font=F_TITLE, fill=GREEN)
                d.text((260, 170 + n * 50), row, font=F_BODY, fill=TEXT)
        if i >= 4:
            summary = f"{v['checks']} checks · " + (
                f"{v['issues']} flagged for review" if v["issues"] else "all passed")
            d.text((220, 350), summary, font=F_TITLE, fill=AMBER if v["issues"] else GREEN)
            if v.get("confidence"):
                d.text((220, 384), f"overall confidence {v['confidence'] * 100:.0f}%", font=F_BODY, fill=MUTED)
        caption(d, "Anything that can't be traced back to the scan is flagged for review.")
        frames.append((img, 450))
    return frames


def scene_result(sample):
    frames = []
    s, llm = sample["summary"], sample["llm"]
    tiles = [("Net worth", s["net_worth"], TEXT), ("Savings", s["savings"], ACCENT),
             ("Real estate", s["real_estate"], AMBER), ("Liabilities", s["liabilities"], RED)]
    for i in range(1, 6):
        img, d = new_frame(3)
        panel(d, (40, 90, 920, 480), "MP PROFILE · totals are computed by code")
        for n, (label, value, color) in enumerate(tiles[: min(i, 4)]):
            x = 62 + n * 212
            d.rounded_rectangle((x, 150, x + 196, 270), 12, fill=BG, outline=PANEL_EDGE)
            d.text((x + 16, 168), label, font=F_SMALL, fill=MUTED)
            d.text((x + 16, 210), short_pln(value), font=font(22, bold=True), fill=color)
        if i >= 5:
            d.text((62, 316), "Cost of computing this data", font=F_BODY, fill=MUTED)
            d.text((62, 346), f"{llm['cost_usd']:.4f} USD".replace(".", ","), font=F_BIG, fill=GREEN)
            d.text((62, 392), f"model: {llm['model']}", font=F_MONO_S, fill=TEXT)
            d.text((62, 418), f"OCR runs locally, free · {llm['calls']} model call(s)", font=F_SMALL, fill=MUTED)
        caption(d, "Same pipeline for every MP and every year.")
        frames.append((img, 450 if i < 5 else 3200))
    return frames


# --------------------------------------------------------------------------- output

def save_gif(frames, out: Path):
    images = [f for f, _ in frames]
    durations = [t for _, t in frames]
    mosaic = Image.new("RGB", (W, H * 3))
    for k, idx in enumerate((len(images) // 4, len(images) // 2, len(images) - 1)):
        mosaic.paste(images[idx], (0, H * k))
    palette = mosaic.quantize(colors=96, method=Image.Quantize.MEDIANCUT)
    paletted = [im.quantize(palette=palette, dither=Image.Dither.NONE) for im in images]
    out.parent.mkdir(parents=True, exist_ok=True)
    paletted[0].save(out, save_all=True, append_images=paletted[1:], duration=durations, loop=0,
                     optimize=True, disposal=1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-db", action="store_true", help="rebuild the sample from a stored extraction")
    ap.add_argument("--mp", type=int, help="Sejm MP id (with --from-db)")
    ap.add_argument("--year", type=int)
    ap.add_argument("--kind", default="ANNUAL")
    ap.add_argument("--out", type=Path, default=MEDIA / "pipeline.gif")
    ap.add_argument("--frames-dir", type=Path, help="also dump every frame as PNG for review")
    args = ap.parse_args()

    if args.from_db:
        if not (args.mp and args.year):
            ap.error("--from-db needs --mp and --year")
        sample = build_sample_from_db(args.mp, args.year, args.kind)
        SAMPLE.write_text(json.dumps(sample, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    scan = Image.open(SCAN).convert("RGB")

    frames = [*scene_scan(sample, scan), *scene_ocr(sample, scan), *scene_llm(sample),
              *scene_json(sample), *scene_verify(sample), *scene_result(sample)]
    save_gif(frames, args.out)
    if args.frames_dir:
        args.frames_dir.mkdir(parents=True, exist_ok=True)
        for n, (im, _) in enumerate(frames):
            im.save(args.frames_dir / f"frame_{n:02d}.png")
    print(f"{args.out} · {len(frames)} frames · {args.out.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
