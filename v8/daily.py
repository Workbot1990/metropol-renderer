"""Deterministic contract for one researched daily V8 content package."""
from __future__ import annotations

import re
from datetime import date
from urllib.parse import urlparse
from typing import Any, Iterable


ALLOWED_RESEARCH_DOMAINS = (
    "bayernlabo.de",
    "bundesfinanzministerium.de",
    "bundesbank.de",
    "bafin.de",
    "destatis.de",
    "finanzamt.bayern.de",
    "gesetze-im-internet.de",
    "kreis-freising.de",
    "kfw.de",
    "notar.de",
    "stadt.muenchen.de",
)

EXPERIMENT_SLOT = "A"
SLOT_LABELS = {
    EXPERIMENT_SLOT: "Abendexperiment",
}
DAILY_CONTENT_ID_PATTERN = r"^ME-[0-9]{4}-[0-9]{3}-A$"


def daily_content_id(day: date, slot: str = EXPERIMENT_SLOT) -> str:
    slot_code = str(slot).upper()
    if slot_code not in SLOT_LABELS:
        raise ValueError("Unbekannter Tages-Slot")
    return f"ME-{day.year}-{day.timetuple().tm_yday:03d}-{slot_code}"


def daily_content_ids(day: date) -> tuple[str, ...]:
    """Return the single permitted experiment ID for the day."""
    return (daily_content_id(day),)


def slot_from_content_id(content_id: str) -> str:
    if not re.fullmatch(DAILY_CONTENT_ID_PATTERN, str(content_id or "")):
        raise ValueError("Daily-Content-ID benötigt einen gültigen Tages-Slot")
    return content_id.rsplit("-", 1)[-1]


def content_schema() -> dict[str, Any]:
    text = {"type": "string", "minLength": 1}
    source = {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "url", "retrieved_at", "data_as_of", "is_primary"],
        "properties": {
            "title": text,
            "url": {"type": "string", "pattern": "^https://"},
            "retrieved_at": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},
            "data_as_of": text,
            "is_primary": {"type": "boolean"},
        },
    }
    scene = {
        "type": "object",
        "additionalProperties": False,
        "required": ["start_seconds", "end_seconds", "purpose", "visual", "on_screen_text"],
        "properties": {
            "start_seconds": {"type": "number", "minimum": 0, "maximum": 55},
            "end_seconds": {"type": "number", "minimum": 3, "maximum": 55},
            "purpose": {"type": "string", "enum": ["hook", "strategy", "calculation", "condition", "risk", "cta"]},
            "visual": text,
            "on_screen_text": {"type": "string", "minLength": 5, "maxLength": 90},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "content_id", "slot", "series", "topic", "risk_class", "editorial_review",
            "fact_check_status", "approval_status", "hook", "voiceover", "caption",
            "calculation", "risk_note", "advice_disclaimer", "sources", "scenes",
            "stock_video_queries", "hypothesis", "viewer_promise", "source_notes",
            "conflict_pattern",
        ],
        "properties": {
            "content_id": {"type": "string", "pattern": DAILY_CONTENT_ID_PATTERN},
            "slot": {"type": "string", "enum": list(SLOT_LABELS)},
            "series": {"type": "string", "enum": [
                "Kann ich mir das leisten?", "Wo geht noch was?", "Legal gestalten",
            ]},
            "topic": text,
            "risk_class": {"type": "string", "enum": ["green"]},
            "editorial_review": {"type": "string", "enum": ["approved"]},
            "fact_check_status": {"type": "string", "enum": ["verified"]},
            "approval_status": {"type": "string", "enum": ["approved"]},
            "hook": {"type": "string", "minLength": 8, "maxLength": 110},
            "voiceover": {"type": "string", "minLength": 300, "maxLength": 700},
            "caption": {"type": "string", "minLength": 180, "maxLength": 1200},
            "calculation": {"type": "string", "minLength": 20, "maxLength": 500},
            "risk_note": {"type": "string", "minLength": 30, "maxLength": 500},
            "advice_disclaimer": {"type": "string", "minLength": 25, "maxLength": 180},
            "sources": {"type": "array", "minItems": 1, "maxItems": 3, "items": source},
            "scenes": {"type": "array", "minItems": 6, "maxItems": 6, "items": scene},
            "stock_video_queries": {
                "type": "array", "minItems": 4, "maxItems": 4,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["query", "role"],
                    "properties": {
                        "query": {"type": "string", "minLength": 8, "maxLength": 90},
                        "role": {"type": "string", "enum": ["location", "property", "people", "documents"]},
                    },
                },
            },
            "hypothesis": {"type": "string", "minLength": 25, "maxLength": 250},
            "viewer_promise": {"type": "string", "minLength": 25, "maxLength": 250},
            "source_notes": {"type": "string", "minLength": 20, "maxLength": 500},
            # Ergaenzt 2026-08-26 (Projekt-Audit-Pipelinevergleich mit
            # ZwischenAmtUndAnno): strukturelle Kurzform des zentralen
            # Konflikts/der Entscheidung, unabhaengig vom Thema - damit
            # `evaluate_content()` Wiederholung auf Musterebene erkennen
            # kann, nicht nur auf Wortlautebene wie beim Hook. Siehe
            # docs/VERBRAUCHTE_KONFLIKTMUSTER.md fuer die gepflegte Liste
            # bereits genutzter Muster und wie sie gegengeprueft wird.
            "conflict_pattern": {"type": "string", "minLength": 10, "maxLength": 120},
        },
    }


def research_instructions(today: date, slot: str, recent_conflict_patterns: Iterable[str] = ()) -> str:
    slot_code = slot_from_content_id(f"ME-{today.year}-{today.timetuple().tm_yday:03d}-{slot}")
    patterns = [str(value).strip() for value in recent_conflict_patterns if str(value).strip()][:20]
    patterns_note = (
        "\n\nBereits verwendete Konfliktmuster (nicht wiederholen, auch nicht mit anderem Thema/Wortlaut): "
        + ("; ".join(patterns) if patterns else "keine")
    )
    return f"""
Du bist Chefredakteur von Metropol Erfolg, einem anonymen Premium-Kanal für
Menschen mit rund 100.000 Euro Bruttoeinkommen in München und Umland, die ohne
Erbe und ohne großes liquides Startkapital Wohneigentum und Vermögen aufbauen
wollen. Recherchiere mit der Websuche ausschließlich in den zugelassenen
Primärquellen. Heute ist {today.isoformat()}.

Dieser Auftrag ist das einzige tägliche Content-Experiment
({SLOT_LABELS[slot_code]}). Erzeuge keinen Morgen- oder Mittag-Slot und keinen
zweiten Beitrag für denselben Tag. Wähle genau einen konkreten Fall mit einer
Euro-Rechnung und einem klaren Konflikt aus einer der drei Testserien:
"Kann ich mir das leisten?", "Wo geht noch was?" oder "Legal gestalten".

Erzeuge genau ein sachliches, substanzielles Evergreen-Thema der Risikoklasse
green. Verwende keine individuelle Finanz-, Steuer- oder Rechtsberatung, keine
Garantien und nicht den Begriff Steuertrick. Jede Zahl braucht eine konkrete
Primärquelle, Datenstand, Annahme und nachvollziehbaren Rechenweg. Trenne
Förderung, Steuerwirkung, Finanzierung und Liquidität sauber. Das Voice-over
soll 34 bis 40 Sekunden dauern, erwachsen und natürlich klingen. Vermeide
Listenrhythmus, Floskeln und hörbare KI-Satzmuster. Hook und Bildsprache müssen
sich klar von den mitgegebenen letzten Hooks unterscheiden. Das erste Motiv
muss neu sein und darf nicht dem Startmotiv des Vortags entsprechen. Erzeuge
vier unterschiedliche englische Pexels-Suchbegriffe mit genau den Rollen
location, property, people und documents. Die Rollen people und documents
müssen sichtbare erwachsene Menschen in echter Handlung zeigen; kein Posieren,
keine Kinder, kein Luxusklischee und kein KI-/Render-Look. Erzeuge in diesem
Experiment kein Carousel und keinen Zusatz-Post. Verwende genau einen
sinnvollen CTA mit dem Keyword RECHNER; die Veröffentlichung bleibt bis zur
menschlichen Freigabe gesperrt.

Die Szenen strategy/calculation/condition/risk steigern sich sichtbar: jede
folgende Szene macht die Aussage konkreter, teurer oder dringlicher als die
vorherige, statt vier gleichwertige Fakten aneinanderzureihen. Der CTA muss
aus der letzten Risk-Szene zwingend folgen, nicht nur thematisch dazu passen.

Fülle zusätzlich `conflict_pattern`: eine kurze, themenunabhängige
Beschreibung der zentralen Entscheidungs-/Konfliktstruktur dieser Folge
(z. B. "Warten kostet mehr als sofort handeln" oder "Zwei Optionen, eine
schließt die andere faktisch aus") - keine Paraphrase des Hooks oder Themas,
sondern das dahinterliegende Muster. Prüfe dieses Muster explizit gegen die
unten gelistete Sperrliste bereits verwendeter Muster und wähle ein neues,
falls es zu ähnlich wäre.{patterns_note}
""".strip()


def research_input(content_id: str, recent_hooks: Iterable[str], slot: str) -> str:
    hooks = [str(hook).strip() for hook in recent_hooks if str(hook).strip()][:20]
    return (
        f"Content-ID: {content_id}\n"
        f"Slot: {slot} ({SLOT_LABELS[slot]})\n"
        "Wähle selbst das stärkste zulässige Thema aus den aktuellen Primärquellen.\n"
        f"Nicht wiederholen – letzte Hooks: {hooks or ['keine']}"
    )


def validate_daily_content(content: dict[str, Any], *, expected_content_id: str) -> tuple[str, ...]:
    errors: list[str] = []
    if content.get("content_id") != expected_content_id:
        errors.append("Content-ID stimmt nicht mit dem Auftrag überein")
    if not re.fullmatch(DAILY_CONTENT_ID_PATTERN, str(content.get("content_id") or "")):
        errors.append("Content-ID ist ungültig")
    else:
        expected_slot = slot_from_content_id(expected_content_id)
        if content.get("slot") != expected_slot:
            errors.append("Slot stimmt nicht mit der Content-ID überein")

    sources = content.get("sources") or []
    for source in sources:
        host = (urlparse(str(source.get("url") or "")).hostname or "").casefold()
        if not any(host == domain or host.endswith(f".{domain}") for domain in ALLOWED_RESEARCH_DOMAINS):
            errors.append(f"Quelle außerhalb der Primärquellen-Allowlist: {host or 'leer'}")

    scenes = content.get("scenes") or []
    if len(scenes) == 6:
        if abs(float(scenes[0].get("start_seconds") or 0)) > 0.05:
            errors.append("Erste Szene muss bei 0 Sekunden beginnen")
        for previous, current in zip(scenes, scenes[1:]):
            if abs(float(previous.get("end_seconds") or 0) - float(current.get("start_seconds") or 0)) > 0.2:
                errors.append("Szenen müssen lückenlos aufeinander folgen")
        duration = float(scenes[-1].get("end_seconds") or 0)
        if not 34 <= duration <= 40:
            errors.append("Reel muss 34 bis 40 Sekunden dauern")
        if scenes[0].get("purpose") != "hook" or scenes[-1].get("purpose") != "cta":
            errors.append("Szenenfolge benötigt Hook am Anfang und CTA am Ende")

        first_duration = float(scenes[0].get("end_seconds") or 0) - float(scenes[0].get("start_seconds") or 0)
        second_duration = float(scenes[1].get("end_seconds") or 0) - float(scenes[1].get("start_seconds") or 0)
        if not 2.5 <= first_duration <= 4.0 or not 2.5 <= second_duration <= 4.0:
            errors.append("Die ersten beiden Szenen müssen jeweils 2,5 bis 4 Sekunden dauern")
        if float(scenes[1].get("end_seconds") or 0) > 8.0:
            errors.append("In den ersten acht Sekunden sind zwei Motivwechsel erforderlich")

    queries = content.get("stock_video_queries") or []
    normalized_queries = [re.sub(r"\W+", " ", str(value.get("query") or "").casefold()).strip() for value in queries]
    roles = [str(value.get("role") or "") for value in queries]
    if len(set(normalized_queries)) != 4:
        errors.append("Vier unterschiedliche Video-Suchmotive sind erforderlich")
    if set(roles) != {"location", "property", "people", "documents"}:
        errors.append("Video-Rollen müssen location, property, people und documents genau einmal enthalten")
    return tuple(sorted(set(errors)))
