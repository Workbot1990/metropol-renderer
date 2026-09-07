from __future__ import annotations

import copy
import unittest
from datetime import date

from jsonschema import validate

from v8.daily import content_schema, daily_content_id, validate_daily_content


def valid_content() -> dict:
    content_id = daily_content_id(date(2026, 9, 9))
    scenes = []
    boundaries = (0.0, 3.5, 7.0, 15.0, 23.0, 32.0, 38.0)
    purposes = ("hook", "strategy", "calculation", "condition", "risk", "cta")
    for index, purpose in enumerate(purposes):
        scenes.append({
            "start_seconds": boundaries[index],
            "end_seconds": boundaries[index + 1],
            "purpose": purpose,
            "visual": f"Glaubwürdiges deutsches Motiv {index}",
            "on_screen_text": f"Konkrete Aussage Nummer {index + 1}",
        })
    return {
        "content_id": content_id,
        "slot": "A",
        "series": "Wo geht noch was?",
        "topic": "Bestandswohnung im Münchner Umland",
        "risk_class": "green",
        "editorial_review": "approved",
        "fact_check_status": "verified",
        "approval_status": "approved",
        "hook": "217.000 Euro günstiger – bis der Ordner aufgeht.",
        "voiceover": "Eine konkrete, natürlich formulierte Einordnung mit nachvollziehbarer Rechnung. " * 5,
        "caption": "Eine konkrete Einordnung mit Quelle, Annahme, Risiko und einem klaren nächsten Prüfschritt. " * 3,
        "calculation": "70 mal 4.760 Euro sind 333.200 Euro; die Differenz wird separat ausgewiesen.",
        "risk_note": "Medianwerte ersetzen weder Objektprüfung noch Rücklagen- und Protokollprüfung.",
        "advice_disclaimer": "Allgemeine Information, keine individuelle Finanz- oder Kaufberatung.",
        "sources": [{
            "title": "Grundstücksmarktbericht",
            "url": "https://www.kreis-freising.de/marktbericht.pdf",
            "retrieved_at": "2026-09-07",
            "data_as_of": "Bezugsjahr 2025",
            "is_primary": True,
        }],
        "scenes": scenes,
        "stock_video_queries": [
            {"query": "Munich commuter train station evening", "role": "location"},
            {"query": "German apartment exterior realistic", "role": "property"},
            {"query": "adult couple reviewing apartment", "role": "people"},
            {"query": "adults checking property documents", "role": "documents"},
        ],
        "hypothesis": "Der konkrete Konflikt erzeugt mehr Speicherungen als ein abstrakter Tipp.",
        "viewer_promise": "Der Zuschauer kennt anschließend den wichtigsten Prüfschritt vor dem Kauf.",
        "source_notes": "Alle Zahlen stammen aus der verlinkten kommunalen Primärquelle.",
        "conflict_pattern": "sichtbarer Preisvorteil gegen verborgenes Kostenrisiko",
    }


class MotionContractTests(unittest.TestCase):
    def test_valid_motion_package_matches_schema_and_contract(self):
        content = valid_content()
        validate(content, content_schema())
        self.assertEqual((), validate_daily_content(content, expected_content_id=content["content_id"]))

    def test_slow_opening_is_blocked(self):
        content = valid_content()
        content["scenes"][0]["end_seconds"] = 4.5
        content["scenes"][1]["start_seconds"] = 4.5
        errors = validate_daily_content(content, expected_content_id=content["content_id"])
        self.assertTrue(any("ersten beiden Szenen" in error for error in errors))

    def test_repeated_motion_query_is_blocked(self):
        content = valid_content()
        content["stock_video_queries"][1]["query"] = content["stock_video_queries"][0]["query"]
        errors = validate_daily_content(content, expected_content_id=content["content_id"])
        self.assertTrue(any("Vier unterschiedliche" in error for error in errors))

    def test_missing_human_action_role_is_blocked(self):
        content = copy.deepcopy(valid_content())
        content["stock_video_queries"][3]["role"] = "property"
        errors = validate_daily_content(content, expected_content_id=content["content_id"])
        self.assertTrue(any("Video-Rollen" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
