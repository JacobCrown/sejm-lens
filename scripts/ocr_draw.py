"""Drawing helpers shared by the Paddle and Chandra preview scripts. No Django."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
)
BOX = (220, 30, 30)
GAP = 18


def _font(size: int = 14) -> ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if Path(path).is_file():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int) -> list[str]:
    words = text.split()
    if not words or width < 8:
        return []
    lines = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if draw.textlength(trial, font=font) <= width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def draw_boxes(image: Image.Image, lines) -> Image.Image:
    """Left: the page with rectangles only. Right: the recognised text in those same rectangles."""
    left = image.convert("RGB")
    width, height = left.size
    draw_left = ImageDraw.Draw(left)
    right = Image.new("RGB", (width, height), "white")
    draw_right = ImageDraw.Draw(right)
    font = _font(13)
    for line in lines:
        x0, y0, x1, y1 = int(line.x0), int(line.y0), int(line.x1), int(line.y1)
        x0, x1 = max(0, min(x0, x1)), min(width - 1, max(x0, x1))
        y0, y1 = max(0, min(y0, y1)), min(height - 1, max(y0, y1))
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        draw_left.rectangle((x0, y0, x1, y1), outline=BOX, width=2)
        text = " ".join(line.text.split())
        if not text:
            continue
        wrapped = _wrap(draw_right, text, font, max(8, x1 - x0 - 4))
        ascent = font.getbbox("Ag")[3] - font.getbbox("Ag")[1]
        line_h = ascent + 2
        y = y0 + 2
        for row in wrapped:
            if y + line_h > y1 and y > y0 + 2:
                break
            draw_right.text((x0 + 2, y), row, fill=(0, 0, 0), font=font)
            y += line_h
    canvas = Image.new("RGB", (width * 2 + GAP, height), (232, 232, 232))
    canvas.paste(left, (0, 0))
    canvas.paste(right, (width + GAP, 0))
    return canvas


def save_pdf(path: Path, pages: list[Image.Image]) -> None:
    rgb = [page.convert("RGB") for page in pages]
    rgb[0].save(path, save_all=True, append_images=rgb[1:])
