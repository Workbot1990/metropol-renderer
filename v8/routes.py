"""Dormant authenticated Flask routes for the Metropol Erfolg V8 pipeline."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import shutil
import tempfile
import urllib.request
from functools import wraps
from pathlib import Path

from flask import Blueprint, jsonify, request

from .captions import write_vtt
from .clients import ElevenLabsClient, OpenAIImagesClient, OpenAIResponsesClient, V8ClientError
from .gates import evaluate_content
from .post_renderer import render_pages
from .reel_renderer import render_reel


bp = Blueprint("metropol_v8", __name__, url_prefix="/v8")
ROOT = Path(__file__).resolve().parents[1]
WORK_ROOT = Path(os.environ.get("METROPOL_V8_WORK_ROOT", ROOT / "work"))
VERSION = "8.0.0-staging"


def _authorized() -> bool:
    expected = os.environ.get("METROPOL_V8_WEBHOOK_SECRET", "")
    supplied = request.headers.get("X-Metropol-Token", "")
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


def require_token(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if not _authorized():
            return jsonify({"status": "blocked", "error": "unauthorized"}), 401
        return function(*args, **kwargs)
    return wrapper


def _content_id(value: str) -> str:
    if not re.fullmatch(r"ME-[0-9]{4}-[0-9]{3}", str(value or "")):
        raise ValueError("Ungültige Content-ID")
    return value


def _download(url: str, destination: Path, *, maximum_bytes: int = 30_000_000) -> Path:
    if not str(url).startswith("https://"):
        raise ValueError("Nur HTTPS-Assets sind erlaubt")
    request_object = urllib.request.Request(url, headers={"User-Agent": "Metropol-Erfolg-V8/1.0"})
    with urllib.request.urlopen(request_object, timeout=90) as response, destination.open("wb") as handle:
        total = 0
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > maximum_bytes:
                raise ValueError("Asset überschreitet das Größenlimit")
            handle.write(block)
    return destination


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _upload(path: Path, *, folder: str, resource_type: str = "image") -> dict:
    import cloudinary.uploader
    return cloudinary.uploader.upload(str(path), resource_type=resource_type, folder=folder, use_filename=True, unique_filename=True, overwrite=False)


@bp.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "version": VERSION,
        "configured": {
            "openai": bool(os.environ.get("METROPOL_OPENAI_API_KEY")),
            "elevenlabs": bool(os.environ.get("METROPOL_ELEVENLABS_API_KEY")),
            "voice": bool(os.environ.get("METROPOL_ELEVENLABS_VOICE_ID")),
            "webhook_auth": bool(os.environ.get("METROPOL_V8_WEBHOOK_SECRET")),
        },
    })


@bp.post("/content/generate")
@require_token
def generate_content():
    data = request.get_json(force=True)
    result = OpenAIResponsesClient().generate_json(
        instructions=str(data.get("instructions") or ""),
        input_text=str(data.get("input_text") or ""),
        schema_name=str(data.get("schema_name") or "metropol_content"),
        schema=data.get("schema") or {},
        max_output_tokens=min(8000, max(500, int(data.get("max_output_tokens") or 5000))),
    )
    return jsonify({"status": "draft", "content": result})


@bp.post("/image/generate")
@require_token
def generate_image():
    data = request.get_json(force=True)
    content_id = _content_id(data.get("content_id"))
    prompt = (
        "Photorealistische hochwertige Editorial-Fotografie in Deutschland, glaubwürdige Architektur "
        "und reale Materialien, zurückhaltendes natürliches Licht, erwachsene Finanzpublikation, "
        "keine Schrift, kein Logo, keine Infografik, kein Render-Look. " + str(data.get("prompt") or "")
    )
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"{content_id}-image-", dir=WORK_ROOT) as temporary:
        target = Path(temporary) / f"{content_id}.png"
        OpenAIImagesClient().generate(prompt=prompt, destination=target, project_root=WORK_ROOT, quality=str(data.get("quality") or "low"))
        uploaded = _upload(target, folder="metropol/v8/source")
        return jsonify({"status": "generated", "url": uploaded["secure_url"], "public_id": uploaded["public_id"], "sha256": _sha(target)})


@bp.post("/audio/generate")
@require_token
def generate_audio():
    data = request.get_json(force=True)
    content_id = _content_id(data.get("content_id"))
    text = str(data.get("text") or "")
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"{content_id}-audio-", dir=WORK_ROOT) as temporary:
        folder = Path(temporary)
        audio, timing = ElevenLabsClient().synthesize_with_timestamps(text, audio_destination=folder / f"{content_id}.mp3", timing_destination=folder / f"{content_id}.json", project_root=WORK_ROOT)
        captions = write_vtt(timing, folder / f"{content_id}.vtt")
        audio_upload = _upload(audio, folder="metropol/v8/audio", resource_type="video")
        captions_upload = _upload(captions, folder="metropol/v8/captions", resource_type="raw")
        cue_count = captions.read_text(encoding="utf-8").count(" --> ")
        return jsonify({"status": "generated", "audio_url": audio_upload["secure_url"], "captions_url": captions_upload["secure_url"], "audio_sha256": _sha(audio), "caption_cues": cue_count})


@bp.post("/reel/render")
@require_token
def render_reel_route():
    data = request.get_json(force=True)
    content = data.get("content") or {}
    content_id = _content_id(content.get("content_id"))
    gate = evaluate_content(content, recent_hooks=data.get("recent_hooks") or [])
    if not gate.passed:
        return jsonify({"status": "blocked", "gate": gate.to_dict()}), 422
    urls = data.get("background_urls") or []
    if not 1 <= len(urls) <= 5:
        raise ValueError("Ein bis fünf Hintergründe erforderlich")
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"{content_id}-reel-", dir=WORK_ROOT) as temporary:
        folder = Path(temporary)
        backgrounds = [_download(url, folder / f"background-{index}.png") for index, url in enumerate(urls, 1)]
        audio = _download(data.get("audio_url"), folder / "voice.mp3")
        captions = _download(data.get("captions_url"), folder / "voice.vtt")
        output = render_reel(content, backgrounds=backgrounds, audio=audio, captions=captions, output=folder / f"{content_id}.mp4", root=ROOT, work_root=folder / "render-work")
        uploaded = _upload(output, folder="metropol/v8/reels", resource_type="video")
        return jsonify({"status": "rendered", "url": uploaded["secure_url"], "public_id": uploaded["public_id"], "sha256": _sha(output), "qa": {"width": 720, "height": 1280, "duration_seconds": content["scenes"][-1]["end_seconds"], "has_voiceover": True}})


@bp.post("/post/render")
@require_token
def render_post_route():
    data = request.get_json(force=True)
    content = data.get("content") or {}
    content_id = _content_id(content.get("content_id"))
    gate = evaluate_content(content, recent_hooks=data.get("recent_hooks") or [])
    if not gate.passed:
        return jsonify({"status": "blocked", "gate": gate.to_dict()}), 422
    pages = data.get("pages") or []
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"{content_id}-post-", dir=WORK_ROOT) as temporary:
        folder = Path(temporary)
        background = _download(data.get("background_url"), folder / "background.png")
        outputs = render_pages(pages, background=background, output_dir=folder / "pages", root=ROOT, content_id=content_id)
        assets = []
        for output in outputs:
            uploaded = _upload(output, folder="metropol/v8/posts")
            assets.append({"url": uploaded["secure_url"], "public_id": uploaded["public_id"], "sha256": _sha(output)})
        return jsonify({"status": "rendered", "assets": assets, "qa": {"width": 1080, "height": 1350, "page_count": len(assets)}})


@bp.errorhandler(V8ClientError)
def client_error(error):
    return jsonify({"status": "failed", "error": str(error)}), 502


@bp.errorhandler(Exception)
def unexpected_error(error):
    return jsonify({"status": "failed", "error": str(error)}), 500
