"""Premium feed-post and carousel renderer for Metropol Erfolg V8."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFont


NAVY = (5, 8, 30)
IVORY = (242, 237, 225)
GOLD = (196, 163, 99)
MUTED = (183, 186, 194)
WIDTH, HEIGHT = 1080, 1350


def _font(root: Path, size: int, *, bold: bool = False, serif: bool = False) -> ImageFont.FreeTypeFont:
    windows = Path("C:/Windows/Fonts")
    candidates = (
        [windows / "georgiab.ttf", root / "fonts" / "PlayfairDisplay-Bold.ttf"]
        if bold and serif
        else [windows / "georgia.ttf", root / "fonts" / "PlayfairDisplay-Regular.ttf"]
        if serif
        else [windows / "segoeuib.ttf", Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")]
        if bold
        else [windows / "segoeui.ttf", Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
    )
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _cover(path: Path, *, focus: float) -> Image.Image:
    source = Image.open(path).convert("RGB")
    ratio = WIDTH / HEIGHT
    crop_width = min(source.width, int(source.height * ratio))
    crop_height = min(source.height, int(crop_width / ratio))
    max_left = max(0, source.width - crop_width)
    left = int(max_left * max(0.0, min(1.0, focus)))
    top = max(0, (source.height - crop_height) // 2)
    image = source.crop((left, top, left + crop_width, top + crop_height)).resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    image = ImageEnhance.Color(image).enhance(0.58)
    image = ImageEnhance.Contrast(image).enhance(1.08).convert("RGBA")
    veil = Image.new("RGBA", image.size, NAVY + (0,))
    draw = ImageDraw.Draw(veil)
    draw.rectangle((0, 0, WIDTH, HEIGHT), fill=NAVY + (128,))
    draw.rectangle((0, 0, WIDTH, 250), fill=NAVY + (86,))
    draw.rectangle((0, 980, WIDTH, HEIGHT), fill=NAVY + (115,))
    image.alpha_composite(veil)
    return image


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if current and draw.textbbox((0, 0), trial, font=font)[2] > max_width:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def render_pages(
    pages: list[dict[str, Any]],
    *,
    background: Path,
    output_dir: Path,
    root: Path,
    content_id: str,
) -> list[Path]:
    if len(pages) not in {1, 4, 5, 6, 7}:
        raise ValueError("Feed-Inhalt muss eine oder vier bis sieben Seiten haben")
    output_dir.mkdir(parents=True, exist_ok=True)
    result: list[Path] = []
    for index, page in enumerate(pages, 1):
        image = _cover(background, focus=0.15 + 0.7 * ((index - 1) / max(1, len(pages) - 1)))
        draw = ImageDraw.Draw(image)
        kicker_font = _font(root, 28, bold=True)
        headline_font = _font(root, 72 if index == 1 else 62, bold=True, serif=True)
        body_font = _font(root, 34)
        footer_font = _font(root, 24, bold=True)
        margin = 88

        kicker = str(page.get("kicker") or "METROPOL ERFOLG").upper()[:42]
        headline = str(page.get("headline") or "").strip()
        body = str(page.get("body") or "").strip()
        if len(headline) < 8:
            raise ValueError(f"Seite {index}: Headline fehlt")

        draw.text((margin, 160), kicker, font=kicker_font, fill=GOLD)
        draw.line((margin, 215, margin + 130, 215), fill=GOLD, width=4)
        headline_lines = _wrap(draw, headline, headline_font, WIDTH - 2 * margin)[:4]
        draw.multiline_text((margin, 320), "\n".join(headline_lines), font=headline_font, fill=IVORY, spacing=18)
        if body:
            body_lines = _wrap(draw, body, body_font, WIDTH - 2 * margin)[:5]
            body_y = min(850, 340 + len(headline_lines) * 105)
            draw.multiline_text((margin, body_y), "\n".join(body_lines), font=body_font, fill=IVORY, spacing=15)

        draw.text((margin, 1240), "METROPOL ERFOLG", font=footer_font, fill=MUTED)
        draw.text((WIDTH - margin, 1240), f"{index:02d}/{len(pages):02d}", font=footer_font, fill=GOLD, anchor="ra")
        output = output_dir / f"{content_id}_p{index:02d}.png"
        image.convert("RGB").save(output, quality=94)
        result.append(output)
    return result
