import os
import gc
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

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"

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

def create_video(hook_text, untertext_text):
    width, height = 720, 1280
    img = Image.new('RGB', (width, height), (10, 15, 30))
    draw = ImageDraw.Draw(img)

    try:
        font_headline = ImageFont.truetype(FONT_REG, 24)
        font_hook = ImageFont.truetype(FONT_BOLD, 55)
        font_untertext = ImageFont.truetype(FONT_REG, 32)
    except:
        font_headline = ImageFont.load_default()
        font_hook = ImageFont.load_default()
        font_untertext = ImageFont.load_default()

    # Headline
    draw.text((width // 2, 90), "METROPOL ERFOLG",
              fill=(255, 255, 255), font=font_headline, anchor="mm")

    # Hook
    max_w = int(width * 0.82)
    hook_lines = wrap_text(draw, hook_text, font_hook, max_w)
    line_h = 70
    total_h = len(hook_lines) * line_h
    start_y = (height // 2) - (total_h // 2) - 50
    for i, line in enumerate(hook_lines):
        draw.text((width // 2, start_y + i * line_h), line,
                  fill=(255, 255, 255), font=font_hook, anchor="mm")

    # Untertext
    u_lines = wrap_text(draw, untertext_text, font_untertext, max_w)
    u_start = height - 260
    for i, line in enumerate(u_lines):
        draw.text((width // 2, u_start + i * 45), line,
                  fill=(180, 180, 180), font=font_untertext, anchor="mm")

    # Save image
    tmp = tempfile.mkdtemp()
    img_path = os.path.join(tmp, f"{uuid.uuid4()}.png")
    vid_path = os.path.join(tmp, f"{uuid.uuid4()}.mp4")
    img.save(img_path, optimize=True)

    # Free memory before FFmpeg
    del img
    del draw
    gc.collect()

    # Convert to video
    subprocess.run([
        'ffmpeg', '-y', '-loop', '1', '-i', img_path,
        '-c:v', 'libx264', '-t', '8', '-preset', 'ultrafast',
        '-pix_fmt', 'yuv420p', '-r', '25', '-crf', '28', vid_path
    ], check=True, capture_output=True)

    # Upload to Cloudinary
    result = cloudinary.uploader.upload(
        vid_path, resource_type='video',
        folder='metropol', public_id=str(uuid.uuid4())
    )

    # Cleanup
    os.remove(img_path)
    os.remove(vid_path)
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
