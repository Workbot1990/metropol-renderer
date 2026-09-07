"""Premium motion-first 9:16 renderer for the Metropol Erfolg V8 pipeline."""
from __future__ import annotations

import hashlib
import math
import re
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


NAVY, IVORY, GOLD, RED, MUTED = (5, 8, 30), (242, 237, 225), (196, 163, 99), (199, 87, 81), (177, 181, 191)
WIDTH, HEIGHT, FPS = 720, 1280, 25


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
    names = (["georgiab.ttf", "PlayfairDisplay-Bold.ttf"] if bold else ["georgia.ttf", "PlayfairDisplay-Regular.ttf"]) if serif else (["segoeuib.ttf", "DejaVuSans-Bold.ttf"] if bold else ["segoeui.ttf", "DejaVuSans.ttf"])
    roots = [Path("C:/Windows/Fonts"), root / "fonts", Path("/usr/share/fonts/truetype/dejavu")]
    for folder in roots:
        for name in names:
            path = folder / name
            if path.exists():
                return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
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


def _run(command: list[str]) -> None:
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode:
        raise RuntimeError(f"ffmpeg fehlgeschlagen: {(completed.stderr or '')[-800:]}")


def _duration(path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    completed = subprocess.run([
        ffprobe, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ], capture_output=True, text=True)
    try:
        return float(completed.stdout.strip()) if not completed.returncode else 0.0
    except ValueError:
        return 0.0


def _brand_overlay(content: dict[str, Any], scene: dict[str, Any], root: Path, destination: Path) -> Path:
    image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image, "RGBA")
    for y in range(HEIGHT):
        alpha = int(55 + 115 * y / HEIGHT)
        draw.line((0, y, WIDTH, y), fill=(*NAVY, alpha))
    draw.rectangle((0, 0, WIDTH, 230), fill=(*NAVY, 80))
    draw.rectangle((0, 930, WIDTH, HEIGHT), fill=(*NAVY, 125))
    draw.text((54, 54), "METROPOL ERFOLG", font=_font(root, 18, True), fill=GOLD)
    draw.line((54, 88, 205, 88), fill=GOLD, width=3)
    labels = {"hook": "KERNFRAGE", "strategy": "EINORDNUNG", "calculation": "RECHNUNG", "condition": "BEDINGUNG", "risk": "WICHTIG", "cta": "NÄCHSTER SCHRITT"}
    draw.text((54, 172), labels.get(str(scene.get("purpose")), "EINORDNUNG"), font=_font(root, 20, True), fill=GOLD)
    headline = _font(root, 46 if scene.get("purpose") == "hook" else 39, True, True)
    lines = _wrap(draw, str(scene.get("on_screen_text") or ""), headline, WIDTH - 108)[:4]
    draw.multiline_text((54, 242), "\n".join(lines), font=headline, fill=IVORY, spacing=12, stroke_width=1, stroke_fill=NAVY)
    draw.text((54, 1207), str(content.get("advice_disclaimer") or "").upper()[:82], font=_font(root, 13), fill=MUTED)
    image.save(destination)
    return destination


def _animated_frame(content: dict[str, Any], scene: dict[str, Any], progress: float, root: Path) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), NAVY)
    draw = ImageDraw.Draw(image)
    for y in range(HEIGHT):
        glow = int(15 * math.sin(math.pi * y / HEIGHT))
        draw.line((0, y, WIDTH, y), fill=(5 + glow // 3, 8 + glow // 3, 30 + glow))
    draw.text((54, 54), "METROPOL ERFOLG", font=_font(root, 18, True), fill=GOLD)
    draw.line((54, 88, 205, 88), fill=GOLD, width=3)
    label = "RECHNUNG" if scene.get("purpose") == "calculation" else "NÄCHSTER SCHRITT"
    draw.text((54, 170), label, font=_font(root, 21, True), fill=GOLD)
    eased = progress * progress * (3 - 2 * progress)
    box_top = 300 + int(24 * (1 - eased))
    draw.rounded_rectangle((54, box_top, 666, 790), 24, outline=GOLD, width=2, fill=(8, 13, 39))
    headline = _font(root, 47 if scene.get("purpose") == "calculation" else 42, True, True)
    lines = _wrap(draw, str(scene.get("on_screen_text") or ""), headline, 520)[:4]
    draw.multiline_text((WIDTH // 2, box_top + 215), "\n".join(lines), font=headline, fill=IVORY, anchor="mm", align="center", spacing=16)
    if scene.get("purpose") == "calculation":
        calculation = str(content.get("calculation") or "")
        small_lines = _wrap(draw, calculation, _font(root, 19), 560)[:3]
        draw.multiline_text((WIDTH // 2, 875), "\n".join(small_lines), font=_font(root, 19), fill=MUTED, anchor="ma", align="center", spacing=8)
    else:
        draw.text((WIDTH // 2, 890), "SPEICHERN · PRÜFEN · DANN ENTSCHEIDEN", font=_font(root, 18, True), fill=MUTED, anchor="ma")
    draw.line((54, 1145, 666, 1145), fill=(55, 61, 84), width=2)
    draw.line((54, 1145, 54 + int(612 * eased), 1145), fill=GOLD, width=4)
    draw.text((54, 1207), str(content.get("advice_disclaimer") or "").upper()[:82], font=_font(root, 13), fill=MUTED)
    return image


def _stock_scene(source: Path, overlay: Path, *, start: float, duration: float, destination: Path) -> None:
    graph = (
        f"[0:v]trim=start={start}:duration={duration},setpts=PTS-STARTPTS,"
        "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,"
        "eq=saturation=0.76:contrast=1.08:brightness=-0.02,fps=25,format=yuv420p[bg];"
        f"[1:v]trim=duration={duration},setpts=PTS-STARTPTS,format=rgba[ov];"
        "[bg][ov]overlay=0:0:shortest=1,format=yuv420p[out]"
    )
    _run([
        _ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-stream_loop", "-1", "-i", str(source),
        "-loop", "1", "-framerate", str(FPS), "-i", str(overlay), "-filter_complex", graph,
        "-map", "[out]", "-an", "-t", f"{duration:.3f}", "-r", str(FPS), "-c:v", "libx264",
        "-preset", "fast", "-crf", "19", "-pix_fmt", "yuv420p", "-video_track_timescale", "90000", str(destination),
    ])


def _animated_scene(content: dict[str, Any], scene: dict[str, Any], *, duration: float, root: Path, frame_root: Path, destination: Path) -> None:
    authored_fps = 10
    frame_root.mkdir(parents=True, exist_ok=True)
    count = math.ceil(duration * authored_fps)
    for index in range(count):
        progress = min(1.0, index / max(1, count - 1))
        _animated_frame(content, scene, progress, root).save(frame_root / f"{index:05d}.jpg", quality=90, subsampling=1)
    _run([
        _ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-framerate", str(authored_fps),
        "-i", str(frame_root / "%05d.jpg"), "-t", f"{duration:.3f}", "-r", str(FPS),
        "-c:v", "libx264", "-preset", "fast", "-crf", "19", "-pix_fmt", "yuv420p",
        "-video_track_timescale", "90000", str(destination),
    ])


def _filter_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _ass_timestamp(value: str) -> str:
    hours, minutes, remainder = value.replace(",", ".").split(":")
    seconds = float(remainder)
    return f"{int(hours)}:{int(minutes):02d}:{seconds:05.2f}"


def _write_ass(captions: Path, destination: Path) -> Path:
    dialogues: list[str] = []
    for block in re.split(r"\r?\n\r?\n", captions.read_text(encoding="utf-8-sig")):
        match = re.search(r"(\d\d:\d\d:\d\d[.,]\d+)\s+-->\s+(\d\d:\d\d:\d\d[.,]\d+)", block)
        if not match:
            continue
        lines = [line.strip() for line in block.splitlines() if line.strip() and "-->" not in line and not line.strip().isdigit() and line.strip() != "WEBVTT"]
        if not lines:
            continue
        text = " ".join(lines).replace("{", "(").replace("}", ")")
        dialogues.append(f"Dialogue: 0,{_ass_timestamp(match.group(1))},{_ass_timestamp(match.group(2))},Default,,0,0,0,,{text}")
    if not dialogues:
        raise ValueError("Keine gültigen Untertitel-Cues vorhanden")
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,34,&H00F2EDE1,&H00F2EDE1,&H00180603,&HD0180603,-1,0,0,0,100,100,0,0,3,1,0,2,72,72,230,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    destination.write_text(header + "\n".join(dialogues) + "\n", encoding="utf-8")
    return destination


def render_reel(content: dict[str, Any], *, media: list[Path], audio: Path, captions: Path, output: Path, root: Path, work_root: Path) -> Path:
    scenes = content.get("scenes") or []
    if len(scenes) != 6:
        raise ValueError("Genau sechs Szenen erforderlich")
    if len(media) != 4 or len({path.resolve() for path in media}) != 4:
        raise ValueError("Vier unterschiedliche Bewegungsvideos erforderlich")
    base_duration = float(scenes[-1]["end_seconds"])
    audio_duration = _duration(audio)
    if audio_duration and not 30 <= audio_duration <= 42:
        raise ValueError("Voice-over-Dauer liegt außerhalb des Qualitätsfensters")
    extension = max(0.0, audio_duration + 0.25 - base_duration) if audio_duration else 0.0
    if base_duration + extension > 42:
        raise ValueError("Voice-over ist zu lang für das Reel")

    work_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="metropol-v8-motion-", dir=work_root))
    try:
        parts: list[Path] = []
        media_index = 0
        for index, scene in enumerate(scenes):
            duration = float(scene["end_seconds"]) - float(scene["start_seconds"])
            if index == len(scenes) - 1:
                duration += extension
            part = temporary / f"scene-{index + 1}.mp4"
            if scene.get("purpose") in {"calculation", "cta"}:
                _animated_scene(content, scene, duration=duration, root=root, frame_root=temporary / f"frames-{index + 1}", destination=part)
            else:
                source = media[media_index]
                clip_duration = _duration(source)
                seed = hashlib.sha256(f"{content.get('content_id')}|{index}".encode("utf-8")).digest()
                maximum_start = max(0.0, clip_duration - duration - 0.5)
                start = (int.from_bytes(seed[:4], "big") / (2**32 - 1)) * maximum_start if maximum_start else 0.0
                overlay = _brand_overlay(content, scene, root, temporary / f"overlay-{index + 1}.png")
                _stock_scene(source, overlay, start=start, duration=duration, destination=part)
                media_index += 1
            parts.append(part)

        concat = temporary / "concat.txt"
        concat.write_text("\n".join(f"file '{path.as_posix()}'" for path in parts), encoding="utf-8")
        silent = temporary / "silent.mp4"
        _run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", "-movflags", "+faststart", str(silent)])
        output.parent.mkdir(parents=True, exist_ok=True)
        ass = _write_ass(captions, temporary / "captions.ass")
        subtitle_filter = f"subtitles=filename='{_filter_path(ass)}'"
        _run([
            _ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(silent), "-i", str(audio),
            "-vf", subtitle_filter, "-filter:a", "highpass=f=70,lowpass=f=15000,acompressor=threshold=-18dB:ratio=1.8:attack=15:release=120,loudnorm=I=-16:LRA=7:TP=-1.5",
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "fast", "-crf", "19",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{base_duration + extension:.3f}", "-movflags", "+faststart", str(output),
        ])
        return output
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
