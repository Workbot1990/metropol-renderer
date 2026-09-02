"""Restrained photographic 9:16 renderer used by the V8 staging API."""
from __future__ import annotations

import math
import re
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFont


NAVY, IVORY, GOLD, MUTED = (5, 8, 30), (242, 237, 225), (196, 163, 99), (177, 181, 191)


def _ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as exc:
        raise RuntimeError("ffmpeg ist nicht installiert") from exc


@lru_cache(maxsize=32)
def _font(root: Path, size: int, bold: bool = False, serif: bool = False):
    windows = Path("C:/Windows/Fonts")
    names = (["georgiab.ttf", "PlayfairDisplay-Bold.ttf"] if bold else ["georgia.ttf", "PlayfairDisplay-Regular.ttf"]) if serif else (["segoeuib.ttf", "DejaVuSans-Bold.ttf"] if bold else ["segoeui.ttf", "DejaVuSans.ttf"])
    paths = [windows / name for name in names] + [root / "fonts" / name for name in names] + [Path("/usr/share/fonts/truetype/dejavu") / name for name in names]
    for path in paths:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _wrap(draw, text, font, max_width):
    lines, current = [], ""
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


def _captions(path: Path):
    result = []
    for block in re.split(r"\r?\n\r?\n", path.read_text(encoding="utf-8-sig")):
        match = re.search(r"(\d\d):(\d\d):(\d\d)[,.](\d+)\s+-->\s+(\d\d):(\d\d):(\d\d)[,.](\d+)", block)
        if not match:
            continue
        values = list(map(int, match.groups()))
        start = values[0] * 3600 + values[1] * 60 + values[2] + values[3] / 1000
        end = values[4] * 3600 + values[5] * 60 + values[6] + values[7] / 1000
        lines = [line.strip() for line in block.splitlines() if line.strip() and "-->" not in line and not line.strip().isdigit() and line.strip() != "WEBVTT"]
        if lines:
            result.append((start, end, " ".join(lines)))
    return result


def _load_background(path: Path) -> Image.Image:
    source = Image.open(path).convert("RGB")
    source = ImageEnhance.Color(source).enhance(0.56)
    source = ImageEnhance.Contrast(source).enhance(1.14)
    return source


def _background(source: Image.Image, width: int, height: int, progress: float):
    zoom = 1.04 + 0.07 * progress
    ratio = width / height
    crop_h = int(source.height / zoom)
    crop_w = int(crop_h * ratio)
    if crop_w > source.width:
        crop_w = int(source.width / zoom)
        crop_h = int(crop_w / ratio)
    left = max(0, int((source.width - crop_w) * (0.25 + 0.45 * progress)))
    top = max(0, (source.height - crop_h) // 2)
    image = source.crop((left, top, left + crop_w, top + crop_h)).resize((width, height), Image.Resampling.BILINEAR)
    image = Image.blend(image, Image.new("RGB", image.size, NAVY), 0.36).convert("RGBA")
    return image


def _frame(content, backgrounds, captions, timestamp, width, height, root):
    scenes = content["scenes"]
    index = next((i for i, scene in enumerate(scenes) if scene["start_seconds"] <= timestamp < scene["end_seconds"]), len(scenes) - 1)
    scene = scenes[index]
    progress = min(1, max(0, (timestamp - scene["start_seconds"]) / max(0.01, scene["end_seconds"] - scene["start_seconds"])))
    image = _background(backgrounds[index % len(backgrounds)], width, height, progress)
    if index and timestamp - scene["start_seconds"] < 0.45:
        dissolve = max(0, min(1, (timestamp - scene["start_seconds"]) / 0.45))
        image = Image.blend(_background(backgrounds[(index - 1) % len(backgrounds)], width, height, 1), image, dissolve)
    draw = ImageDraw.Draw(image)
    scale = width / 1080
    margin = int(72 * scale)
    kicker = _font(root, max(15, int(24 * scale)), True)
    headline = _font(root, max(31, int(67 * scale)), True, True)
    small = _font(root, max(14, int(22 * scale)))
    labels = {"hook": "KERNFRAGE", "strategy": "EINORDNUNG", "calculation": "RECHNUNG", "condition": "BEDINGUNG", "risk": "WICHTIG", "cta": "NÄCHSTER SCHRITT"}
    draw.text((margin, int(height * 0.19)), labels.get(scene["purpose"], scene["purpose"].upper()), font=kicker, fill=GOLD)
    draw.multiline_text((margin, int(height * 0.28)), "\n".join(_wrap(draw, scene["on_screen_text"], headline, width - 2 * margin)[:4]), font=headline, fill=IVORY, spacing=int(10 * scale), stroke_width=1, stroke_fill=NAVY)
    line_y = int(height * 0.57)
    draw.line((margin, line_y, margin + int((width - 2 * margin) * min(1, progress * 1.8)), line_y), fill=GOLD, width=max(2, int(3 * scale)))
    draw.text((margin, int(height * 0.925)), content["advice_disclaimer"].upper()[:88], font=small, fill=MUTED)
    duration = scenes[-1]["end_seconds"]
    progress_y = int(height * 0.895)
    draw.line((margin, progress_y, width - margin, progress_y), fill=GOLD, width=max(1, int(2 * scale)))
    draw.line((margin, progress_y, margin + int((width - 2 * margin) * timestamp / duration), progress_y), fill=GOLD, width=max(2, int(4 * scale)))
    caption = next((text for start, end, text in captions if start <= timestamp < end), None)
    if caption:
        sub_font = _font(root, max(18, int(31 * scale)), True)
        lines = _wrap(draw, caption, sub_font, width - 2 * margin - int(30 * scale))[-2:]
        box_y, line_h = int(height * 0.765), max(28, int(45 * scale))
        box_h = line_h * len(lines) + int(34 * scale)
        draw.rounded_rectangle((margin, box_y - box_h // 2, width - margin, box_y + box_h // 2), radius=max(4, int(7 * scale)), fill=(3, 6, 24, 218))
        draw.multiline_text((width // 2, box_y), "\n".join(lines), font=sub_font, fill=IVORY, anchor="mm", align="center", spacing=max(4, int(8 * scale)))
    return image.convert("RGB")


def render_reel(content: dict[str, Any], *, backgrounds: list[Path], audio: Path, captions: Path, output: Path, root: Path, work_root: Path, fps: int = 25) -> Path:
    if not backgrounds:
        raise ValueError("Mindestens ein Hintergrund fehlt")
    scenes = content.get("scenes") or []
    if not 3 <= len(scenes) <= 8:
        raise ValueError("Drei bis acht Szenen erforderlich")
    duration = float(scenes[-1]["end_seconds"])
    if not 15 <= duration <= 60:
        raise ValueError("Reel-Dauer muss 15 bis 60 Sekunden betragen")
    width, height, authored_fps = 720, 1280, 10
    work_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="metropol-v8-", dir=work_root))
    try:
        frames = temporary / "frames"
        frames.mkdir()
        cues = _captions(captions)
        prepared_backgrounds = [_load_background(path) for path in backgrounds]
        for index in range(math.ceil(duration * authored_fps)):
            _frame(content, prepared_backgrounds, cues, index / authored_fps, width, height, root).save(
                frames / f"{index:05d}.jpg", quality=88, subsampling=1, optimize=False
            )
        silent = temporary / "silent.mp4"
        ffmpeg = _ffmpeg()
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-framerate", str(authored_fps), "-i", str(frames / "%05d.jpg"), "-r", str(fps), "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p", str(silent)], check=True)
        output.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(silent), "-i", str(audio), "-filter:a", "loudnorm=I=-16:LRA=7:TP=-1.5", "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(output)], check=True)
        return output
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
