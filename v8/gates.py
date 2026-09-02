"""Deterministic content and publication gates for V8."""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable

from PIL import Image


@dataclass(frozen=True)
class GateResult:
    passed: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normal(value: Any) -> str:
    return re.sub(r"[^a-z0-9äöüß]+", " ", _text(value).casefold()).strip()


def _https(value: Any) -> bool:
    return _text(value).startswith("https://")


def _duplicate_score(hook: str, recent_hooks: Iterable[str]) -> float:
    normalized = _normal(hook)
    if not normalized:
        return 1.0
    return max((SequenceMatcher(None, normalized, _normal(other)).ratio() for other in recent_hooks), default=0.0)


def evaluate_content(
    record: dict[str, Any],
    *,
    recent_hooks: Iterable[str] = (),
    recent_conflict_patterns: Iterable[str] = (),
) -> GateResult:
    """Block weak, risky, duplicated or unsupported content before production."""
    errors: list[str] = []
    warnings: list[str] = []

    risk_class = _text(record.get("risk_class")).lower()
    if risk_class not in {"green", "yellow", "red"}:
        errors.append("risk_class muss green, yellow oder red sein")
    elif risk_class == "red":
        errors.append("rote Inhalte dürfen nicht produziert werden")
    elif risk_class == "yellow" and _text(record.get("editorial_review")) != "approved":
        errors.append("gelber Inhalt benötigt redaktionelle Freigabe")

    if _text(record.get("fact_check_status")) != "verified":
        errors.append("Fact Check ist nicht verified")
    if _text(record.get("approval_status")) not in {"approved", "ready"}:
        errors.append("redaktionelle Freigabe fehlt")

    hook = _text(record.get("hook"))
    if len(hook) < 8 or len(hook) > 110:
        errors.append("Hook muss 8 bis 110 Zeichen lang sein")
    score = _duplicate_score(hook, recent_hooks)
    if score >= 0.86:
        errors.append(f"Hook-Dublette erkannt ({score:.0%})")

    # Fund 2026-08-26 (Projekt-Audit-Pipelinevergleich mit ZwischenAmtUndAnno):
    # Der Hook-Dublettencheck erkennt nur aehnliche FORMULIERUNGEN, nicht ein
    # wiederverwendetes zugrundeliegendes Konfliktmuster mit neuen Woertern -
    # exakt die Luecke, die der eigene Kanal-Audit vom 25.08. als "zu viel
    # Lexikon, zu wenig Entscheidung, fehlt Konflikt" benannt hat. Analog zur
    # "verbrauchte Mechanismen"-Sperrliste in ZwischenAmtUndAnno's Channel
    # Bible (docs/VERBRAUCHTE_KONFLIKTMUSTER.md hier im Projekt).
    conflict_pattern = _text(record.get("conflict_pattern"))
    if conflict_pattern:
        pattern_score = _duplicate_score(conflict_pattern, recent_conflict_patterns)
        if pattern_score >= 0.80:
            errors.append(f"Konfliktmuster-Dublette erkannt ({pattern_score:.0%})")

    if len(_text(record.get("voiceover"))) < 120:
        errors.append("Voiceover ist zu kurz für ein substanzielles Reel")
    if len(_text(record.get("caption"))) < 80:
        errors.append("Caption ist zu kurz")
    if len(_text(record.get("calculation"))) < 12:
        errors.append("nachvollziehbarer Rechenweg fehlt")
    if len(_text(record.get("risk_note"))) < 20:
        errors.append("Risiko- oder Bedingungshinweis fehlt")
    if len(_text(record.get("advice_disclaimer"))) < 20:
        errors.append("Beratungshinweis fehlt")

    sources = record.get("sources") or []
    if not isinstance(sources, list) or not sources:
        errors.append("mindestens eine Quelle fehlt")
    elif not any(
        source.get("is_primary") is True
        and _https(source.get("url"))
        and _text(source.get("retrieved_at"))
        and _text(source.get("data_as_of"))
        for source in sources
        if isinstance(source, dict)
    ):
        errors.append("vollständige HTTPS-Primärquelle fehlt")

    joined = " ".join((_text(record.get("hook")), _text(record.get("voiceover")), _text(record.get("caption")))).casefold()
    for phrase in ("garantiert reich", "risikolos", "sichere rendite", "ohne risiko"):
        if phrase in joined:
            errors.append(f"unzulässiges Versprechen: {phrase}")
    if "steuertrick" in joined:
        warnings.append("Begriff Steuertrick redaktionell präzisieren")

    return GateResult(not errors, tuple(sorted(set(errors))), tuple(sorted(set(warnings))))


def _difference_hash(path: Path, *, hash_size: int = 8) -> int:
    """Pure-Pillow perceptual hash (dHash) - no extra dependency needed.

    Resizes to (hash_size+1) x hash_size, greyscales, and encodes whether each
    pixel is brighter than its right neighbour as one bit. Robust against the
    resizing/recompression differences between two separately generated
    images, unlike a byte-for-byte or SHA-256 comparison.
    """
    image = Image.open(path).convert("L").resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
    pixels = image.load()
    bits = 0
    for row in range(hash_size):
        for col in range(hash_size):
            bits = (bits << 1) | int(pixels[col, row] > pixels[col + 1, row])
    return bits


def _hamming_distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def evaluate_image_diversity(image_paths: Iterable[Path], *, min_hamming_distance: int = 10) -> GateResult:
    """Block a reel whose source images are visually near-duplicates.

    Fund 2026-08-26 (Projekt-Audit-Pipelinevergleich mit ZwischenAmtUndAnno):
    Bisher gab es hier keinerlei Pruefung auf visuelle Aehnlichkeit der
    generierten Bilder selbst - nur eine Textpruefung, dass die drei
    Bildprompts unterschiedlich formuliert sind (`daily.py`,
    `validate_daily_content`). Unterschiedliche Prompts koennen trotzdem
    sehr aehnliche Bilder liefern. Dieses Gate vergleicht die tatsaechlichen
    Pixel (Differenz-Hash, hamming-Abstand) paarweise.

    Deckt NICHT ab, dass das Rendering aktuell nur 3 Bilder auf 5 Szenen
    verteilt (`reel_renderer.py`, `backgrounds[index % len(backgrounds)]`) -
    das ist eine bewusste Kosten-/Architekturentscheidung (5 statt 3 Bilder
    pro Reel wuerde die OpenAI-Bildkosten um ~67% erhoehen, siehe
    `docs/PROJECT_STATUS.md`) und keine, die dieses Gate stillschweigend
    aendert. Siehe README.md-Abschnitt "Offene Architekturfrage".
    """
    paths = list(image_paths)
    errors: list[str] = []
    try:
        hashes = {str(path): _difference_hash(path) for path in paths}
    except (OSError, ValueError) as exc:
        return GateResult(False, (f"Bild konnte nicht gelesen werden: {exc}",))
    for left, right in combinations(hashes.items(), 2):
        distance = _hamming_distance(left[1], right[1])
        if distance < min_hamming_distance:
            errors.append(
                f"Bilder zu aehnlich (Hamming-Abstand {distance} < {min_hamming_distance}): "
                f"{Path(left[0]).name} vs. {Path(right[0]).name}"
            )
    return GateResult(not errors, tuple(sorted(set(errors))))


def evaluate_publication(record: dict[str, Any], *, now: datetime | None = None) -> GateResult:
    """Final fail-closed gate shared by both Make publishers."""
    errors: list[str] = []
    warnings: list[str] = []
    content_type = _text(record.get("content_type")).lower()
    if content_type not in {"reel", "post", "carousel"}:
        errors.append("content_type muss reel, post oder carousel sein")
    if _text(record.get("status")) != "Scheduled":
        errors.append("Status muss Scheduled sein")
    if _text(record.get("approval")) != "ready":
        errors.append("Approval muss ready sein")
    if _text(record.get("instagram_media_id")):
        errors.append("Instagram Media ID existiert bereits")

    planned_at = _text(record.get("planned_at"))
    try:
        planned = datetime.fromisoformat(planned_at.replace("Z", "+00:00"))
    except ValueError:
        errors.append("planned_at ist kein ISO-Zeitpunkt")
        planned = None
    if planned and now:
        comparison_now = now if now.tzinfo else now.replace(tzinfo=planned.tzinfo)
        if planned > comparison_now:
            errors.append("Veröffentlichungszeitpunkt ist noch nicht erreicht")

    for field in ("visual_qa", "compliance_qa"):
        if _text(record.get(field)) != "passed":
            errors.append(f"{field} ist nicht passed")
    if content_type == "reel" and _text(record.get("audio_qa")) != "passed":
        errors.append("audio_qa ist nicht passed")

    if not _https(record.get("asset_url")):
        errors.append("HTTPS-Asset-URL fehlt")
    sha = _text(record.get("asset_sha256")).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        errors.append("Asset-SHA-256 fehlt oder ist ungültig")

    if content_type == "reel":
        if record.get("width") not in {720, 1080} or record.get("height") not in {1280, 1920}:
            errors.append("Reel muss im Format 9:16 vorliegen")
        duration = record.get("duration_seconds")
        if not isinstance(duration, (int, float)) or not 15 <= duration <= 60:
            errors.append("Reel-Dauer muss zwischen 15 und 60 Sekunden liegen")
        if int(record.get("caption_cues") or 0) < 1:
            errors.append("eingebrannte Untertitel fehlen")
    else:
        if (record.get("width"), record.get("height")) != (1080, 1350):
            errors.append("Feed-Post muss 1080 × 1350 Pixel haben")
        pages = int(record.get("page_count") or 0)
        if content_type == "post" and pages != 1:
            errors.append("Einbild-Post muss genau eine Seite haben")
        if content_type == "carousel" and not 4 <= pages <= 7:
            errors.append("Carousel muss vier bis sieben Seiten haben")

    return GateResult(not errors, tuple(sorted(set(errors))), tuple(sorted(set(warnings))))


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
