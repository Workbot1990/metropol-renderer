"""Dormant authenticated Flask routes for the Metropol Erfolg V8 pipeline."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import shutil
import tempfile
import urllib.request
from datetime import date
from functools import wraps
from pathlib import Path

from flask import Blueprint, jsonify, request

from .captions import write_vtt
from .clients import ElevenLabsClient, OpenAIImagesClient, OpenAIResponsesClient, V8ClientError
from .daily import (
    ALLOWED_RESEARCH_DOMAINS,
    content_schema,
    daily_content_id,
    research_input,
    research_instructions,
    slot_from_content_id,
    validate_daily_content,
)
from .gates import evaluate_content, evaluate_image_diversity
from .ledger import record_reel_cost
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
    if not re.fullmatch(r"ME-[0-9]{4}-[0-9]{3}(?:-[MNA])?", str(value or "")):
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


def _upload(path: Path, *, folder: str, resource_type: str = "image", public_id: str | None = None) -> dict:
    import cloudinary.uploader
    options = {
        "resource_type": resource_type,
        "folder": folder,
        "use_filename": public_id is None,
        "unique_filename": public_id is None,
        # Deterministic V8 IDs make a retry replace the incomplete prior
        # attempt instead of creating another asset or failing on a conflict.
        "overwrite": public_id is not None,
    }
    if public_id:
        options["public_id"] = public_id
    return cloudinary.uploader.upload(str(path), **options)


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
        web_search=bool(data.get("web_search")),
        allowed_domains=tuple(domain for domain in data.get("allowed_domains") or () if domain in ALLOWED_RESEARCH_DOMAINS),
    )
    return jsonify({"status": "draft", "content": result})


@bp.post("/daily/prepare")
@require_token
def prepare_daily_package():
    data = request.get_json(force=True)
    # Make may omit the ID entirely. The server then derives one deterministic
    # evening experiment for the current calendar day, avoiding fragile
    # day-of-year formatting in the automation UI.
    content_id = _content_id(data.get("content_id") or daily_content_id(date.today()))
    slot = slot_from_content_id(content_id)
    recent_hooks = [str(value) for value in data.get("recent_hooks") or []][:20]
    recent_conflict_patterns = [str(value) for value in data.get("recent_conflict_patterns") or []][:20]
    today = date.today()
    content = OpenAIResponsesClient().generate_json(
        instructions=research_instructions(today, slot, recent_conflict_patterns),
        input_text=research_input(content_id, recent_hooks, slot),
        schema_name="metropol_daily_package",
        schema=content_schema(),
        max_output_tokens=7000,
        web_search=True,
        allowed_domains=ALLOWED_RESEARCH_DOMAINS,
    )
    contract_errors = validate_daily_content(content, expected_content_id=content_id)
    gate = evaluate_content(content, recent_hooks=recent_hooks, recent_conflict_patterns=recent_conflict_patterns)
    if contract_errors or not gate.passed:
        return jsonify({
            "status": "blocked",
            "contract_errors": contract_errors,
            "gate": gate.to_dict(),
        }), 422

    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"{content_id}-daily-", dir=WORK_ROOT) as temporary:
        folder = Path(temporary)
        images: list[Path] = []
        source_assets: list[dict] = []
        image_client = OpenAIImagesClient()
        for index, prompt in enumerate(content["image_prompts"], 1):
            image = image_client.generate(
                prompt=prompt,
                destination=folder / f"background-{index}.png",
                project_root=WORK_ROOT,
                quality=str(data.get("image_quality") or "low"),
            )
            images.append(image)

        # Fund 2026-08-26 (Projekt-Audit-Pipelinevergleich mit
        # ZwischenAmtUndAnno): bisher gab es keine Pruefung, ob die drei
        # generierten Bilder sich visuell tatsaechlich unterscheiden - nur
        # dass ihre Text-Prompts unterschiedlich formuliert sind
        # (validate_daily_content). Analog zu ZwischenAmtUndAnnos
        # IMAGE-REPEAT-GATE, hier vor dem Upload/Render geprueft.
        image_gate = evaluate_image_diversity(images)
        if not image_gate.passed:
            return jsonify({"status": "blocked", "gate": image_gate.to_dict()}), 422

        for index, image in enumerate(images, 1):
            uploaded = _upload(image, folder="metropol/v8/source", public_id=f"{content_id}-source-{index}")
            source_assets.append({"url": uploaded["secure_url"], "public_id": uploaded["public_id"], "sha256": _sha(image)})

        audio, timing = ElevenLabsClient().synthesize_with_timestamps(
            content["voiceover"],
            audio_destination=folder / f"{content_id}.mp3",
            timing_destination=folder / f"{content_id}.json",
            project_root=WORK_ROOT,
        )
        captions = write_vtt(timing, folder / f"{content_id}.vtt")
        audio_upload = _upload(audio, folder="metropol/v8/audio", resource_type="video", public_id=f"{content_id}-voice")
        captions_upload = _upload(captions, folder="metropol/v8/captions", resource_type="raw", public_id=f"{content_id}-captions")

        reel = render_reel(
            content,
            backgrounds=images,
            audio=audio,
            captions=captions,
            output=folder / f"{content_id}.mp4",
            root=ROOT,
            work_root=folder / "render-work",
        )
        reel_upload = _upload(reel, folder="metropol/v8/reels", resource_type="video", public_id=f"{content_id}-reel")

        cost = record_reel_cost(
            content_id,
            image_calls=len(content["image_prompts"]),
            voice_characters=len(content["voiceover"]),
            ledger_path=Path(os.environ.get("METROPOL_V8_LEDGER_PATH", ROOT / "docs" / "V8_KOSTENLEDGER.jsonl")),
        )

        duration = content["scenes"][-1]["end_seconds"]
        return jsonify({
            "status": "qa_passed",
            "content": content,
            "source_assets": source_assets,
            "cost_estimate": cost,
            "audio": {"url": audio_upload["secure_url"], "public_id": audio_upload["public_id"], "sha256": _sha(audio)},
            "captions": {"url": captions_upload["secure_url"], "public_id": captions_upload["public_id"], "cues": captions.read_text(encoding="utf-8").count(" --> ")},
            "reel": {"url": reel_upload["secure_url"], "public_id": reel_upload["public_id"], "sha256": _sha(reel), "width": 720, "height": 1280, "duration_seconds": duration},
            # The rescue experiment prepares one measurable Reel only. The
            # separate post endpoint remains available for a later, proven format.
            "post": None,
            "qa": {"content": "passed", "sources": "passed", "audio": "passed", "visual": "passed", "compliance": "passed"},
        })


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
