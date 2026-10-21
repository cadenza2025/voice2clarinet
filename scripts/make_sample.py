"""Generate an original eight-bar voice + grand-piano fixture (not a real song)."""

from pathlib import Path
import sys

from lxml import etree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.notation import render_score, serialize  # noqa: E402


def element(parent, tag, text=None, **attrs):
    child = ET.SubElement(parent, tag, **{key: str(value) for key, value in attrs.items()})
    if text is not None:
        child.text = str(text)
    return child


def note(measure, step, octave, duration=1, staff=None, lyric=None):
    n = element(measure, "note")
    pitch = element(n, "pitch")
    element(pitch, "step", step)
    element(pitch, "octave", octave)
    element(n, "duration", duration)
    element(n, "type", {1: "quarter", 2: "half", 4: "whole"}[duration])
    if staff:
        element(n, "staff", staff)
    if lyric:
        text = element(n, "lyric")
        element(text, "syllabic", "single")
        element(text, "text", lyric)
    return n


def sample():
    root = ET.Element("score-partwise", version="4.0")
    work = element(root, "work")
    element(work, "work-title", "Orchard Song")
    ident = element(root, "identification")
    element(ident, "creator", "Original demonstration melody", type="composer")
    element(ident, "rights", "CC0 - generated for Reed & Voice")
    part_list = element(root, "part-list")
    for part_id, name in (("P1", "Voice"), ("P2", "Piano")):
        info = element(part_list, "score-part", id=part_id)
        element(info, "part-name", name)
    voice = element(root, "part", id="P1")
    melody = [
        [("C", 4), ("D", 4), ("E", 4), ("G", 4)],
        [("A", 4), ("G", 4), ("E", 4), ("D", 4)],
        [("E", 4), ("F", 4), ("G", 4), ("E", 4)],
        [("D", 4), ("C", 4), ("D", 4), ("G", 4)],
        [("A", 4), ("B", 4), ("C", 5), ("A", 4)],
        [("G", 4), ("E", 4), ("F", 4), ("D", 4)],
        [("E", 4), ("G", 4), ("D", 4), ("B", 3)],
        [("C", 4)],
    ]
    for index, pitches in enumerate(melody):
        m = element(voice, "measure", number=index + 1)
        if index == 0:
            attributes(m)
            direction = element(m, "direction")
            words = element(direction, "direction-type")
            element(words, "words", "Gently")
            element(direction, "sound", tempo="92")
        for n, (step, octave) in enumerate(pitches):
            item = note(
                m,
                step,
                octave,
                4 if index == 7 else 1,
                lyric=["Sing", "to", "the", "sun"][n] if index == 0 else None,
            )
            if index == 0 and n == 0:
                notations = element(item, "notations")
                artic = element(notations, "articulations")
                element(artic, "tenuto")
        if index == 7:
            bar = element(m, "barline", location="right")
            element(bar, "bar-style", "light-heavy")
    piano = element(root, "part", id="P2")
    for index in range(8):
        m = element(piano, "measure", number=index + 1)
        if index == 0:
            attributes(m, piano=True)
        note(m, "E", 4, 4, staff=1)
        backup = element(m, "backup")
        element(backup, "duration", 4)
        note(m, "C", 3, 4, staff=2)
    return serialize(root)


def attributes(m, piano=False):
    attrs = element(m, "attributes")
    element(attrs, "divisions", 1)
    key = element(attrs, "key")
    element(key, "fifths", 0)
    time = element(attrs, "time")
    element(time, "beats", 4)
    element(time, "beat-type", 4)
    if piano:
        element(attrs, "staves", 2)
    clef = element(attrs, "clef", **({"number": "1"} if piano else {}))
    element(clef, "sign", "G")
    element(clef, "line", 2)
    if piano:
        clef = element(attrs, "clef", number="2")
        element(clef, "sign", "F")
        element(clef, "line", 4)


if __name__ == "__main__":
    destination = Path(__file__).resolve().parents[1] / "app" / "static" / "demo"
    destination.mkdir(parents=True, exist_ok=True)
    data = sample()
    (destination / "orchard-song.musicxml").write_bytes(data)
    render_score(data, destination, "orchard-song")
    for svg in destination.glob("orchard-song-*.svg"):
        svg.unlink()
    print(f"Generated original MusicXML and PDF in {destination}")
