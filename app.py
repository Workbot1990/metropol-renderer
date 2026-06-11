import os
import gc
import json
import random
import subprocess
import tempfile
import uuid
from flask import Flask, request, jsonify
import cloudinary
import cloudinary.uploader
from PIL import Image, ImageDraw, ImageFont

app = Flask(__name__)

cloudinary.config(
    cloud_name=os.environ.get('CLOUDINARY_CLOUD_NAME'),
    api_key=os.environ.get('CLOUDINARY_API_KEY'),
    api_secret=os.environ.get('CLOUDINARY_API_SECRET')
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VIDEOS_DIR = os.path.join(BASE_DIR, 'videos')
FONTS_DIR = os.path.join(BASE_DIR, 'fonts')
FONT_BOLD = os.path.join(FONTS_DIR, 'PlayfairDisplay-Bold.ttf')
FONT_REG = os.path.join(FONTS_DIR, 'PlayfairDisplay-Regular.ttf')
FALLBACK_BOLD = '/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf'
FALLBACK_REG = '/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf'

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

def get_video_dimensions(video_path):
    result = subprocess.run([
        'ffprobe', '-v', 'quiet', '-print_format', 'json',
        '-show_streams', video_path
    ], capture_output=True, text=True)
    info = json.loads(result.stdout)
    for s in info['streams']:
        if s['codec_type'] == 'video':
            return s['width'], s['height']
    return 1080, 1920

def wrap_text(draw, text, font, max_width):
    words = text.split()
    lines = []
    current = []
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

def create_text_overlay(hook_text, untertext_text, width, height):
    overlay = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    max_w = int(width * 0.82)
    scale = width / 1080

    # Hook text - bold, centered, upper middle
    hook_font = get_font(bold=True, size=int(68 * scale))
    hook_lines = wrap_text(draw, hook_text, hook_font, max_w)
    line_h = int(85 * scale)
    total_h = len(hook_lines) * line_h
    hook_start_y = int(height * 0.40) - total_h // 2
    for i, line in enumerate(hook_lines):
        draw.text((width // 2, hook_start_y + i * line_h), line,
                  fill=(255, 255, 255, 255), font=hook_font, anchor='mm')

    # Untertext - regular, lower area
    untertext_font = get_font(bold=False, size=int(40 * scale))
    u_lines = wrap_text(draw, untertext_text, untertext_font, max_w)
    u_start = int(height * 0.76)
    for i, line in enumerate(u_lines):
        draw.text((width // 2, u_start + i * int(52 * scale)), line,
                  fill=(200, 200, 200, 255), font=untertext_font, anchor='mm')

    return overlay

def create_video(hook_text, untertext_text):
    videos = [f for f in os.listdir(VIDEOS_DIR) if f.endswith('.mp4')]
    if not videos:
        raise Exception(f'No base videos found in {VIDEOS_DIR}')
    base_video = random.choice(videos)
    base_path = os.path.join(VIDEOS_DIR, base_video)

    width, height = get_video_dimensions(base_path)

    overlay = create_text_overlay(hook_text, untertext_text, width, height)

    tmp = tempfile.mkdtemp()
    overlay_path = os.path.join(tmp, f'{uuid.uuid4()}.png')
    output_path = os.path.join(tmp, f'{uuid.uuid4()}.mp4')

    overlay.save(overlay_path, 'PNG')
    del overlay
    gc.collect()

    # Composite video + text overlay
    subprocess.run([
        'ffmpeg', '-y',
        '-i', base_path,
        '-i', overlay_path,
        '-filter_complex', '[0:v][1:v]overlay=0:0',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23',
        '-c:a', 'copy',
        output_path
    ], check=True, capture_output=True)

    result = cloudinary.uploader.upload(
        output_path, resource_type='video',
        folder='metropol', public_id=str(uuid.uuid4())
    )

    os.remove(overlay_path)
    os.remove(output_path)
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
