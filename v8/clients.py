"""Small API clients for the isolated Metropol Erfolg V8 pipeline.

Secrets are read only from project-specific environment variables.  The module
does not persist credentials and never includes them in exceptions.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class V8ClientError(RuntimeError):
    """An external request failed without exposing credentials."""


def _read_json(request: urllib.request.Request, *, timeout: int = 90) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise V8ClientError(f"API-Anfrage fehlgeschlagen ({exc.code})") from None
    except urllib.error.URLError as exc:
        raise V8ClientError(f"API nicht erreichbar: {exc.reason}") from None


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise V8ClientError(f"Erforderliche Umgebungsvariable fehlt: {name}")
    return value


def _json_request(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    provider: str,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            error = json.loads(body)
            detail = error.get("detail") or error.get("error") or {}
            if isinstance(detail, dict):
                message = detail.get("message") or detail.get("status") or detail.get("code")
            else:
                message = str(detail)
        except json.JSONDecodeError:
            message = None
        raise V8ClientError(
            f"{provider}-Anfrage fehlgeschlagen ({exc.code}): {message or 'ohne Details'}"
        ) from None
    except urllib.error.URLError as exc:
        raise V8ClientError(f"API nicht erreichbar: {exc.reason}") from None


def _project_target(destination: Path, project_root: Path) -> Path:
    root = project_root.resolve()
    target = destination.resolve()
    if root != target and root not in target.parents:
        raise V8ClientError("Dateiziel liegt außerhalb des Projektordners")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


class OpenAIResponsesClient:
    """Generate schema-constrained content through the Responses API."""

    def __init__(self, *, model: str = "gpt-5.6-luna") -> None:
        self.api_key = _required_env("METROPOL_OPENAI_API_KEY")
        self.model = model

    def generate_json(
        self,
        *,
        instructions: str,
        input_text: str,
        schema_name: str,
        schema: dict[str, Any],
        max_output_tokens: int = 5000,
        web_search: bool = False,
        allowed_domains: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "instructions": instructions,
            "input": input_text,
            "max_output_tokens": max_output_tokens,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        if web_search:
            tool: dict[str, Any] = {"type": "web_search", "search_context_size": "low"}
            if allowed_domains:
                tool["filters"] = {"allowed_domains": list(allowed_domains)}
            payload["tools"] = [tool]
            payload["tool_choice"] = "auto"
            payload["reasoning"] = {"effort": "low"}
        response = _json_request(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {self.api_key}"},
            payload=payload,
            provider="OpenAI",
        )
        for item in response.get("output", []):
            for part in item.get("content", []):
                if part.get("type") == "output_text" and part.get("text"):
                    try:
                        return json.loads(part["text"])
                    except json.JSONDecodeError as exc:
                        raise V8ClientError("OpenAI-Antwort war kein gültiges JSON") from exc
        raise V8ClientError("OpenAI-Antwort enthielt keinen auswertbaren Inhalt")


class PexelsVideoClient:
    """Select and download distinct portrait stock clips from the official API."""

    API_URL = "https://api.pexels.com/v1/videos/search"

    def __init__(self) -> None:
        self.api_key = _required_env("METROPOL_PEXELS_API_KEY")

    def search_and_download(
        self,
        *,
        query: str,
        role: str,
        destination: Path,
        project_root: Path,
        selection_seed: str,
        excluded_ids: set[int] | None = None,
        maximum_bytes: int = 45_000_000,
    ) -> dict[str, Any]:
        if len(query.strip()) < 8:
            raise V8ClientError("Pexels-Suchbegriff ist zu kurz")
        target = _project_target(destination, project_root)
        parameters = urllib.parse.urlencode({
            "query": query,
            "orientation": "portrait",
            "size": "medium",
            "locale": "de-DE",
            "per_page": 24,
        })
        request = urllib.request.Request(
            f"{self.API_URL}?{parameters}",
            headers={"Authorization": self.api_key, "User-Agent": "Metropol-Erfolg-V8/1.0"},
        )
        payload = _read_json(request)
        excluded = excluded_ids or set()
        candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for video in payload.get("videos") or []:
            try:
                video_id = int(video["id"])
            except (KeyError, TypeError, ValueError):
                continue
            if video_id in excluded or float(video.get("duration") or 0) < 5:
                continue
            files = [
                item for item in video.get("video_files") or []
                if item.get("file_type") == "video/mp4"
                and int(item.get("width") or 0) >= 720
                and int(item.get("height") or 0) >= 1280
                and int(item.get("height") or 0) > int(item.get("width") or 0)
                and str(item.get("link") or "").startswith("https://")
            ]
            if not files:
                continue
            best_file = min(files, key=lambda item: (int(item.get("height") or 0), int(item.get("width") or 0)))
            candidates.append((video, best_file))
        if not candidates:
            raise V8ClientError(f"Pexels lieferte kein geeignetes Hochkantvideo für Rolle {role}")

        digest = hashlib.sha256(f"{selection_seed}|{role}|{query}".encode("utf-8")).digest()
        video, video_file = candidates[int.from_bytes(digest[:4], "big") % len(candidates)]
        direct_url = str(video_file["link"])
        download = urllib.request.Request(direct_url, headers={"User-Agent": "Metropol-Erfolg-V8/1.0"})
        try:
            with urllib.request.urlopen(download, timeout=120) as response, target.open("wb") as handle:
                total = 0
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    total += len(block)
                    if total > maximum_bytes:
                        raise V8ClientError("Pexels-Video überschreitet das Größenlimit")
                    handle.write(block)
        except urllib.error.HTTPError as exc:
            target.unlink(missing_ok=True)
            raise V8ClientError(f"Pexels-Video konnte nicht geladen werden ({exc.code})") from None
        except urllib.error.URLError as exc:
            target.unlink(missing_ok=True)
            raise V8ClientError(f"Pexels-Video ist nicht erreichbar: {exc.reason}") from None
        if target.stat().st_size < 100_000:
            target.unlink(missing_ok=True)
            raise V8ClientError("Pexels-Video ist nicht plausibel")

        user = video.get("user") or {}
        return {
            "path": target,
            "pexels_id": int(video["id"]),
            "role": role,
            "page_url": str(video.get("url") or ""),
            "creator": str(user.get("name") or "Pexels Creator"),
            "creator_url": str(user.get("url") or ""),
            "width": int(video_file.get("width") or 0),
            "height": int(video_file.get("height") or 0),
            "duration_seconds": float(video.get("duration") or 0),
        }


class OpenAIImagesClient:
    """Generate one restrained photographic source asset per request."""

    def __init__(self, *, model: str = "gpt-image-2") -> None:
        self.api_key = _required_env("METROPOL_OPENAI_API_KEY")
        self.model = model

    def generate(
        self,
        *,
        prompt: str,
        destination: Path,
        project_root: Path,
        size: str = "1024x1536",
        quality: str = "low",
    ) -> Path:
        if len(prompt.strip()) < 20:
            raise V8ClientError("Bildprompt ist zu kurz")
        if quality not in {"low", "medium"}:
            raise V8ClientError("Für V8 sind nur low oder medium zulässig")
        target = _project_target(destination, project_root)
        response = _json_request(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {self.api_key}"},
            payload={
                "model": self.model,
                "prompt": prompt,
                "size": size,
                "quality": quality,
                "output_format": "png",
                "n": 1,
            },
            provider="OpenAI Images",
        )
        try:
            encoded = response["data"][0]["b64_json"]
            image = base64.b64decode(encoded, validate=True)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise V8ClientError("OpenAI lieferte kein gültiges Bild") from exc
        if len(image) < 10_000:
            raise V8ClientError("OpenAI-Bilddatei ist nicht plausibel")
        target.write_bytes(image)
        return target


class ElevenLabsClient:
    """Synthesize the approved Metropol voice without local secret storage."""

    def __init__(self, *, voice_id: str | None = None) -> None:
        self.api_key = _required_env("METROPOL_ELEVENLABS_API_KEY")
        self.voice_id = voice_id or _required_env("METROPOL_ELEVENLABS_VOICE_ID")

    def synthesize(self, text: str, destination: Path, *, project_root: Path) -> Path:
        if not text.strip():
            raise V8ClientError("Leerer Voiceover-Text")
        target = _project_target(destination, project_root)

        query = urllib.parse.urlencode({"output_format": "mp3_44100_128"})
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}?{query}"
        payload = {
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {
                "stability": 0.58,
                "similarity_boost": 0.78,
                "style": 0.18,
                "use_speaker_boost": True,
            },
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "xi-api-key": self.api_key},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                audio = response.read()
        except urllib.error.HTTPError as exc:
            raise V8ClientError(f"ElevenLabs-Anfrage fehlgeschlagen ({exc.code})") from None
        except urllib.error.URLError as exc:
            raise V8ClientError(f"ElevenLabs ist nicht erreichbar: {exc.reason}") from None
        if len(audio) < 1024:
            raise V8ClientError("ElevenLabs lieferte keine plausible Audiodatei")
        target.write_bytes(audio)
        return target

    def synthesize_with_timestamps(
        self,
        text: str,
        *,
        audio_destination: Path,
        timing_destination: Path,
        project_root: Path,
    ) -> tuple[Path, Path]:
        """Create MP3 plus character timing JSON for deterministic captions."""
        if not text.strip():
            raise V8ClientError("Leerer Voiceover-Text")
        audio_target = _project_target(audio_destination, project_root)
        timing_target = _project_target(timing_destination, project_root)
        query = urllib.parse.urlencode({"output_format": "mp3_44100_128"})
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}/with-timestamps?{query}"
        response = _json_request(
            url,
            headers={"xi-api-key": self.api_key},
            payload={
                "text": text,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {
                    "stability": 0.58,
                    "similarity_boost": 0.78,
                    "style": 0.18,
                    "use_speaker_boost": True,
                },
            },
            provider="ElevenLabs",
        )
        try:
            audio = base64.b64decode(response["audio_base64"], validate=True)
            alignment = response.get("normalized_alignment") or response["alignment"]
            characters = alignment["characters"]
            starts = alignment["character_start_times_seconds"]
            ends = alignment["character_end_times_seconds"]
        except (KeyError, TypeError, ValueError) as exc:
            raise V8ClientError("ElevenLabs lieferte keine gültigen Zeitmarken") from exc
        if len(audio) < 1024 or not (len(characters) == len(starts) == len(ends)):
            raise V8ClientError("ElevenLabs-Audio oder Zeitmarken sind unvollständig")
        audio_target.write_bytes(audio)
        timing_target.write_text(
            json.dumps({"characters": characters, "starts": starts, "ends": ends}, ensure_ascii=False),
            encoding="utf-8",
        )
        return audio_target, timing_target
