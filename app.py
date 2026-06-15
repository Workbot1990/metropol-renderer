import os
import gc
import math
import random
import shutil
import subprocess
import tempfile
import uuid
from flask import Flask, request, jsonify
import cloudinary
import cloudinary.uploader
from PIL import Image, ImageDraw, ImageFont, ImageFilter

app = Flask(__name__)

cloudinary.config(
    cloud_name=os.environ.get('CLOUDINARY_CLOUD_NAME'),
    api_key=os.environ.get('CLOUDINARY_API_KEY'),
    api_secret=os.environ.get('CLOUDINARY_API_SECRET')
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONTS_DIR = os.path.join(BASE_DIR, 'fonts')
AUDIO_DIR = os.path.join(BASE_DIR, 'audio')
FONT_BOLD = os.path.join(FONTS_DIR, 'PlayfairDisplay-Bold.ttf')
FONT_REG = os.path.join(FONTS_DIR, 'PlayfairDisplay-Regular.ttf')
FALLBACK_BOLD = '/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf'
FALLBACK_REG = '/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf'

# ---------- Video ----------
VIDEO_W = 1080
VIDEO_H = 1920
FPS = 30
DURATION = 8.0

# ---------- Hintergrund ----------
BG_C0 = '0x050816'
BG_C1 = '0x0d1530'
BG_SPEED = 0.012

# ---------- Header (fixe Marke, linksbuendig) ----------
HEADER_TEXT = 'METROPOL ERFOLG'
HEADER_Y = 0.115
HEADER_X = 0.12          # linker Rand (Anteil der Breite)
HEADER_SIZE = 34
HEADER_TRACK = 8         # Sperrung (Buchstabenabstand) bei 1080px

# ---------- Hook: verstreuter Blur-Reveal ----------
HOOK_SIZE = 82            # Start-Schriftgroesse (wird bei langen Hooks autom. verkleinert)
HOOK_MIN_SIZE = 46        # Untergrenze beim Auto-Verkleinern
HOOK_LINE_RATIO = 1.42    # Zeilenhoehe = Schriftgroesse * Ratio
HOOK_BAND_TOP = 0.22      # Hook bleibt in diesem vertikalen Band (Anteil der Hoehe)
HOOK_BAND_BOTTOM = 0.66
HOOK_REVEAL_START = 0.3   # ab wann Buchstaben starten
HOOK_REVEAL_SPAN = 3.2    # ueber welchen Zeitraum sie verstreut erscheinen
HOOK_CHAR_FADE = 0.55     # Schaerf-/Einblendedauer je Buchstabe
HOOK_MAX_BLUR = 14        # Anfangsunschaerfe je Buchstabe (px bei 1080)

# ---------- Feste Signaturzeile unten ----------
# Leeren String setzen ('') = keine Signaturzeile.
SIGNATURE_TEXT = 'Assets verändern langfristig ganze Lebensrealitäten.'
SIGNATURE_Y = 0.76
SIGNATURE_SIZE = 46

# Untere Zeile: Notion-Untertext (True, statisch pro Post) oder feste SIGNATURE_TEXT (False)
USE_NOTION_UNTERTEXT = True

# ---------- Audio ----------
AUDIO_FADE = 0.5
AUDIO_EXT = ('.mp3', '.m4a', '.aac', '.wav', '.ogg')


def get_font(bold=False, size=60):
    path = FONT_BOLD if bold else FONT_REG
    fallback = FALLBACK_BOLD if bold else FALLBACK_REG
    try:
        return ImageFont.truetype(path, size)
    except:
        try:
            return ImageFont.truetype(fallback, size)
        except:
            return ImageFont.load_default()


def wrap_text(draw, text, font, max_width):
    words = text.split()
    lines, current = [], []
    for word in words:
        test = ' '.join(current + [word])
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current.append(word)
        else:
            if current:
                lines.append(' '.join(current))
            current = [word]
    if current:
        lines.append(' '.join(current))
    return lines


def build_static_overlay(untertext_text, width, height):
    """Header (oben, linksbuendig) + feste Signaturzeile / Notion-Untertext (unten)."""
    scale = width / 1080
    overlay = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Header linksbuendig mit Sperrung
    hf = get_font(bold=True, size=int(HEADER_SIZE * scale))
    track = int(HEADER_TRACK * scale)
    x = int(width * HEADER_X)
    y = int(height * HEADER_Y)
    for ch in HEADER_TEXT:
        draw.text((x, y), ch, font=hf, fill=(255, 255, 255, 255), anchor='lm')
        x += draw.textlength(ch, font=hf) + track

    # Untere Zeile
    bottom = untertext_text if USE_NOTION_UNTERTEXT else SIGNATURE_TEXT
    if bottom and bottom.strip():
        bf = get_font(bold=False, size=int(SIGNATURE_SIZE * scale))
        max_w = int(width * 0.82)
        for i, line in enumerate(wrap_text(draw, bottom, bf, max_w)):
            draw.text((width // 2, int(height * SIGNATURE_Y) + i * int(58 * scale)),
                      line, font=bf, fill=(255, 255, 255, 255), anchor='mm')

    return overlay


def layout_hook_chars(hook_text, width, height):
    scale = width / 1080
    max_w = int(width * 0.82)
    band_top = int(height * HOOK_BAND_TOP)
    band_h = int(height * (HOOK_BAND_BOTTOM - HOOK_BAND_TOP))
    tmp = Image.new('RGBA', (width, height))
    d = ImageDraw.Draw(tmp)

    # Schrift so weit verkleinern, bis der Hook ins Band passt (respektiert \n)
    size = int(HOOK_SIZE * scale)
    min_size = int(HOOK_MIN_SIZE * scale)
    while True:
        font = get_font(bold=True, size=size)
        line_h = int(size * HOOK_LINE_RATIO)
        lines = []
        for seg in hook_text.split('\n'):
            seg = seg.strip()
            if seg:
                lines.extend(wrap_text(d, seg, font, max_w))
        if len(lines) * line_h <= band_h or size <= min_size:
            break
        size -= 4

    total_h = len(lines) * line_h
    first_center = band_top + (band_h - total_h) // 2 + line_h // 2
    chars = []
    for li, line in enumerate(lines):
        left_x = width // 2 - d.textlength(line, font=font) / 2
        y = first_center + li * line_h
        for j, ch in enumerate(line):
            if ch != ' ':
                chars.append((ch, left_x + d.textlength(line[:j], font=font), y))
    return chars, font


def _blur_letter(img, ch, x, y, font, p, max_blur):
    pad = int(max_blur * 2) + 4
    tw = int(font.getlength(ch)) + pad * 2
    th = int(font.size * 1.6) + pad * 2
    tile = Image.new('RGBA', (tw, th), (0, 0, 0, 0))
    td = ImageDraw.Draw(tile)
    td.text((pad, th // 2), ch, font=font, fill=(255, 255, 255, 255), anchor='lm')
    blur = (1 - p) * max_blur
    if blur > 0.3:
        tile = tile.filter(ImageFilter.GaussianBlur(blur))
    alpha = tile.split()[3].point(lambda v: int(v * p))
    tile.putalpha(alpha)
    img.alpha_composite(tile, (int(x - pad), int(y - th // 2)))


def render_hook_frames(hook_text, width, height, outdir):
    scale = width / 1080
    max_blur = HOOK_MAX_BLUR * scale
    chars, font = layout_hook_chars(hook_text, width, height)
    n = max(1, len(chars))

    span = HOOK_REVEAL_SPAN
    if HOOK_REVEAL_START + span + HOOK_CHAR_FADE > DURATION - 1.0:
        span = max(0.2, DURATION - 1.0 - HOOK_REVEAL_START - HOOK_CHAR_FADE)

    rng = random.Random()
    starts = [HOOK_REVEAL_START + rng.random() * span for _ in range(n)]
    reveal_end = (max(starts) if starts else 0) + HOOK_CHAR_FADE
    nframes = max(1, math.ceil(reveal_end * FPS))

    for f in range(nframes):
        t = f / FPS
        img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        for (ch, x, y), st in zip(chars, starts):
            p = (t - st) / HOOK_CHAR_FADE
            p = max(0.0, min(1.0, p))
            if p <= 0:
                continue
            if p >= 1:
                d.text((x, y), ch, font=font, fill=(255, 255, 255, 255), anchor='lm')
            else:
                _blur_letter(img, ch, x, y, font, p, max_blur)
        img.save(os.path.join(outdir, f'h{f:05d}.png'))
        del img, d
    gc.collect()
    return nframes


def pick_audio():
    if not os.path.isdir(AUDIO_DIR):
        return None
    tracks = [f for f in os.listdir(AUDIO_DIR) if f.lower().endswith(AUDIO_EXT)]
    return os.path.join(AUDIO_DIR, random.choice(tracks)) if tracks else None


def get_audio_duration(path):
    try:
        r = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
             '-of', 'csv=p=0', path],
            capture_output=True, text=True)
        return float(r.stdout.strip())
    except Exception:
        return None


def create_video(hook_text, untertext_text):
    tmp = tempfile.mkdtemp()
    frames_dir = os.path.join(tmp, 'hook')
    os.makedirs(frames_dir, exist_ok=True)
    static_path = os.path.join(tmp, 'static.png')
    output_path = os.path.join(tmp, f'{uuid.uuid4()}.mp4')

    render_hook_frames(hook_text, VIDEO_W, VIDEO_H, frames_dir)
    hook_pattern = os.path.join(frames_dir, 'h%05d.png')

    static = build_static_overlay(untertext_text, VIDEO_W, VIDEO_H)
    static.save(static_path, 'PNG')
    del static
    gc.collect()

    seed = random.randint(0, 99999)
    gradient = (
        f'gradients=s={VIDEO_W}x{VIDEO_H}:c0={BG_C0}:c1={BG_C1}:c2={BG_C0}'
        f':nb_colors=3:duration={DURATION}:speed={BG_SPEED}:type=radial:seed={seed}'
    )

    cmd = [
        'ffmpeg', '-y',
        '-f', 'lavfi', '-i', gradient,
        '-framerate', str(FPS), '-i', hook_pattern,
        '-loop', '1', '-i', static_path,
    ]

    audio_path = pick_audio()
    if audio_path:
        # Zufaelligen Startpunkt waehlen, damit derselbe Track abwechslungsreich
        # klingt und schwache Intros umgangen werden. Kurze Tracks werden geloopt.
        adur = get_audio_duration(audio_path)
        if adur and adur > DURATION + 0.3:
            start = random.uniform(0, adur - DURATION - 0.1)
            cmd += ['-ss', f'{start:.2f}', '-i', audio_path]
        else:
            cmd += ['-stream_loop', '-1', '-i', audio_path]
        fade_out = max(0.0, DURATION - AUDIO_FADE)
        fc = (
            '[0:v]noise=alls=7:allf=t,vignette=PI/4.5[bg];'
            '[bg][1:v]overlay=0:0:eof_action=repeat[v1];'
            '[v1][2:v]overlay=0:0[v];'
            f'[3:a]atrim=0:{DURATION},asetpts=N/SR/TB,'
            f'afade=t=in:st=0:d={AUDIO_FADE},afade=t=out:st={fade_out}:d={AUDIO_FADE}[a]'
        )
        cmd += ['-filter_complex', fc, '-map', '[v]', '-map', '[a]']
    else:
        fc = (
            '[0:v]noise=alls=7:allf=t,vignette=PI/4.5[bg];'
            '[bg][1:v]overlay=0:0:eof_action=repeat[v1];'
            '[v1][2:v]overlay=0:0[v]'
        )
        cmd += ['-filter_complex', fc, '-map', '[v]']

    cmd += [
        '-t', str(DURATION), '-r', str(FPS),
        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23', '-pix_fmt', 'yuv420p',
    ]
    if audio_path:
        cmd += ['-c:a', 'aac', '-b:a', '128k']
    cmd += [output_path]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError('ffmpeg failed: ' + (proc.stderr or '')[-600:])

    result = cloudinary.uploader.upload(
        output_path, resource_type='video',
        folder='metropol', public_id=str(uuid.uuid4())
    )

    shutil.rmtree(tmp, ignore_errors=True)
    gc.collect()
    return result['secure_url']


@app.route('/render', methods=['POST'])
def render():
    try:
        data = request.json
        hook = data.get('hook', '')
        untertext = data.get('untertext', '')
        if not hook:
            return jsonify({'error': 'Hook required'}), 400
        url = create_video(hook, untertext)
        return jsonify({'url': url, 'status': 'success'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
