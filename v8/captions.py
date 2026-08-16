"""Turn ElevenLabs character timing into compact WebVTT captions."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


def _timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{milliseconds:03d}"


def cues_from_alignment(alignment: dict, *, max_words: int = 6, max_seconds: float = 3.2) -> list[Cue]:
    characters = alignment["characters"]
    starts = alignment["starts"]
    ends = alignment["ends"]
    if not characters or not (len(characters) == len(starts) == len(ends)):
        raise ValueError("Ungültige Zeichen-Zeitmarken")

    words: list[tuple[str, float, float]] = []
    current: list[str] = []
    word_start = float(starts[0])
    word_end = word_start
    for char, start, end in zip(characters, starts, ends):
        if str(char).isspace():
            if current:
                words.append(("".join(current), word_start, word_end))
                current = []
            continue
        if not current:
            word_start = float(start)
        current.append(str(char))
        word_end = float(end)
    if current:
        words.append(("".join(current), word_start, word_end))

    cues: list[Cue] = []
    bucket: list[tuple[str, float, float]] = []
    for word in words:
        bucket.append(word)
        duration = bucket[-1][2] - bucket[0][1]
        sentence_end = word[0].endswith((".", "!", "?", ":"))
        if len(bucket) >= max_words or duration >= max_seconds or sentence_end:
            cues.append(Cue(bucket[0][1], bucket[-1][2], " ".join(item[0] for item in bucket)))
            bucket = []
    if bucket:
        cues.append(Cue(bucket[0][1], bucket[-1][2], " ".join(item[0] for item in bucket)))
    return cues


def write_vtt(timing_json: Path, destination: Path) -> Path:
    alignment = json.loads(timing_json.read_text(encoding="utf-8"))
    cues = cues_from_alignment(alignment)
    lines = ["WEBVTT", ""]
    for index, cue in enumerate(cues, 1):
        lines.extend((str(index), f"{_timestamp(cue.start)} --> {_timestamp(cue.end)}", cue.text, ""))
    destination.write_text("\n".join(lines), encoding="utf-8")
    return destination
