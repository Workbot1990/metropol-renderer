"""Lightweight per-reel cost ledger for V8.

Fund 2026-08-26 (Projekt-Audit-Pipelinevergleich mit ZwischenAmtUndAnno):
V8 hatte bislang ueberhaupt kein Tracking realer Kosten pro Reel, nur ein
pauschales, hartes 10-USD-Monatslimit auf OpenAI-Projektebene (siehe
README.md "Kostenprofil"). Dieses Modul fuehrt Buch ueber jeden erzeugten
Reel, analog zu ZwischenAmtUndAnnos `automation/ledger.py`.

WICHTIG: `_UNIT_COST_EUR_ESTIMATE` sind grobe, NICHT verifizierte
Naeherungswerte - keine echten Rechnungsdaten. Vor dem ersten produktiven
Cutover gegen die tatsaechliche OpenAI-/ElevenLabs-Abrechnung pruefen und
diese Konstanten korrigieren. Jeder Ledger-Eintrag traegt deshalb
`cost_is_estimate: true`, damit das nie stillschweigend als Ist-Kosten
missverstanden wird.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

# Grobe Naeherung, nicht verifiziert (Stand 2026-08-26). gpt-image-2 "low"
# Qualitaet, 1024x1536; ElevenLabs multilingual v2 pro 1000 Zeichen.
_UNIT_COST_EUR_ESTIMATE = {
    "image_low_1024x1536": 0.02,
    "elevenlabs_per_1000_chars": 0.30,
}


def estimate_cost_eur(*, image_calls: int, voice_characters: int) -> float:
    return round(
        image_calls * _UNIT_COST_EUR_ESTIMATE["image_low_1024x1536"]
        + (voice_characters / 1000) * _UNIT_COST_EUR_ESTIMATE["elevenlabs_per_1000_chars"],
        4,
    )


def record_reel_cost(
    content_id: str,
    *,
    image_calls: int,
    voice_characters: int,
    ledger_path: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append one ledger line for a produced reel and return its entry."""
    entry = {
        "content_id": content_id,
        "logged_at": (now or datetime.now(timezone.utc)).isoformat(),
        "image_calls": image_calls,
        "voice_characters": voice_characters,
        "estimated_cost_eur": estimate_cost_eur(image_calls=image_calls, voice_characters=voice_characters),
        "cost_is_estimate": True,
        "unit_costs_used": dict(_UNIT_COST_EUR_ESTIMATE),
    }
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def month_to_date_cost_eur(ledger_path: Path, *, today: date | None = None) -> float:
    """Sum estimated costs for the current calendar month, for the cap check."""
    if not ledger_path.exists():
        return 0.0
    reference = today or date.today()
    total = 0.0
    with ledger_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                logged = datetime.fromisoformat(entry["logged_at"])
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
            if logged.year == reference.year and logged.month == reference.month:
                total += float(entry.get("estimated_cost_eur") or 0.0)
    return round(total, 4)
