from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile

import pytest
from pypdf import PdfReader

from app.notation import (
    ScoreError,
    parse_score,
    part_summaries,
    read_musicxml,
    render_score,
    serialize,
    to_clarinet,
)
from scripts.make_sample import sample


def test_extracts_only_voice_and_transposes_c_to_d():
    source = sample()
    output, warnings = to_clarinet(source, "P1")
    root = parse_score(output)
    assert [p.get("id") for p in root.findall("part")] == ["P1"]
    assert root.findtext("part-list/score-part/part-name") == "Clarinet in B-flat"
    assert root.findtext(".//key/fifths") == "2"
    assert root.findtext(".//pitch/step") == "D"
    assert root.findtext(".//pitch/octave") == "4"
    assert root.findtext(".//transpose/chromatic") == "-2"
    assert root.findtext(".//transpose/diatonic") == "-1"
    assert root.findtext(".//midi-program") == "72"
    assert warnings
    original = parse_score(source).find("part")
    result = root.find("part")
    for path in (".//duration", ".//type", ".//lyric", ".//notations", ".//barline", ".//sound"):
        assert [ET_text(x) for x in original.findall(path)] == [ET_text(x) for x in result.findall(path)]


def ET_text(node):
    return serialize(node)


def test_no_double_transposition():
    once, _ = to_clarinet(sample(), "P1")
    twice, _ = to_clarinet(once, "P1")
    assert parse_score(once).findtext(".//pitch/step") == "D"
    assert [ET_text(p) for p in parse_score(once).findall(".//pitch")] == [
        ET_text(p) for p in parse_score(twice).findall(".//pitch")
    ]
    assert [x.text for x in parse_score(twice).findall(".//fifths")] == ["2"]


@pytest.mark.parametrize(
    "step,alter,octave,expected",
    [
        ("B", "0", "4", ("C", "1", "5")),
        ("B", "-1", "3", ("C", "0", "4")),
        ("E", "1", "4", ("F", "2", "4")),
        ("C", "-1", "4", ("D", "-1", "4")),
        ("F", "0.5", "5", ("G", "0.5", "5")),
    ],
)
def test_exact_diatonic_spelling_and_octave(step, alter, octave, expected):
    root = parse_score(sample())
    pitch = root.find(".//pitch")
    pitch.find("step").text = step
    from lxml import etree as ET

    ET.SubElement(pitch, "alter").text = alter
    pitch.find("octave").text = octave
    result = parse_score(to_clarinet(serialize(root), "P1")[0]).find(".//pitch")
    assert (result.findtext("step"), result.findtext("alter", "0"), result.findtext("octave")) == expected


def test_key_changes_minor_ties_tuplets_articulations_and_harmony():
    from lxml import etree as ET

    root = parse_score(sample())
    part = root.find("part")
    second = part.findall("measure")[1]
    attrs = ET.Element("attributes")
    key = ET.SubElement(attrs, "key")
    ET.SubElement(key, "fifths").text = "-3"
    ET.SubElement(key, "mode").text = "minor"
    second.insert(0, attrs)
    n = part.find(".//note")
    ET.SubElement(n, "tie", type="start")
    time_mod = ET.SubElement(n, "time-modification")
    ET.SubElement(time_mod, "actual-notes").text = "3"
    ET.SubElement(time_mod, "normal-notes").text = "2"
    harmony = ET.Element("harmony")
    chord = ET.SubElement(harmony, "root")
    ET.SubElement(chord, "root-step").text = "B"
    ET.SubElement(chord, "root-alter").text = "-1"
    ET.SubElement(harmony, "kind").text = "major"
    second.append(harmony)
    result = parse_score(to_clarinet(serialize(root), "P1")[0])
    assert [x.text for x in result.findall(".//fifths")] == ["2", "-1"]
    assert result.findtext(".//mode") == "minor"
    assert result.find(".//tie").get("type") == "start"
    assert result.findtext(".//actual-notes") == "3"
    assert result.find(".//tenuto") is not None
    assert result.findtext(".//harmony/root/root-step") == "C"
    assert result.find(".//harmony/root/root-alter") is None


def test_other_source_transposing_instrument():
    from lxml import etree as ET

    root = parse_score(sample())
    attrs = root.find(".//attributes")
    t = ET.SubElement(attrs, "transpose")
    ET.SubElement(t, "diatonic").text = "-5"
    ET.SubElement(t, "chromatic").text = "-9"  # E-flat alto: written C4 sounds E-flat3
    result = parse_score(to_clarinet(serialize(root), "P1")[0])
    assert result.findtext(".//pitch/step") == "F"
    assert result.findtext(".//pitch/octave") == "3"
    assert result.findtext(".//fifths") == "-1"


def test_piano_not_silently_treated_as_voice():
    with pytest.raises(ScoreError, match="multiple staves"):
        to_clarinet(sample(), "P2")
    with pytest.raises(ScoreError, match="Select"):
        to_clarinet(sample(), "missing")
    parts = part_summaries(sample())
    assert parts[0]["likely_voice"]
    assert not parts[1]["likely_voice"]
    assert parts[1]["staves"] == 2


def test_entities_and_oversized_xml_rejected():
    with pytest.raises(ScoreError):
        parse_score(
            b'<!DOCTYPE x [<!ENTITY test SYSTEM "file:///secret">]><score-partwise>&test;</score-partwise>'
        )
    with pytest.raises(ScoreError, match="8 MB"):
        parse_score(b" " * (8 * 1024 * 1024 + 1))


def test_mxl_is_read_without_extracting_archive_paths(tmp_path):
    path = tmp_path / "score.mxl"
    with ZipFile(path, "w") as archive:
        archive.writestr(
            "META-INF/container.xml",
            '<container><rootfiles><rootfile full-path="score.xml"/></rootfiles></container>',
        )
        archive.writestr("score.xml", sample())
        archive.writestr("../../escape.txt", "not extracted")
    assert read_musicxml(path) == sample()
    assert not (tmp_path.parent / "escape.txt").exists()


def test_real_engraver_produces_svg_and_vector_pdf(tmp_path):
    data, _ = to_clarinet(sample(), "P1")
    count = render_score(data, tmp_path, "clarinet")
    assert count >= 1
    assert "<svg" in (tmp_path / "clarinet-1.svg").read_text(encoding="utf-8")
    reader = PdfReader(BytesIO((tmp_path / "clarinet.pdf").read_bytes()))
    assert len(reader.pages) == count
    assert (tmp_path / "clarinet.pdf").stat().st_size > 2000
    import pypdfium2

    with pypdfium2.PdfDocument(str(tmp_path / "clarinet.pdf")) as document:
        page = document[0]
        bitmap = page.render(scale=1)
        image = bitmap.to_pil().convert("L")
        width, height = image.size
        pixels = image.tobytes()
        rows = [sum(v < 160 for v in pixels[y * width : (y + 1) * width]) for y in range(height)]
        # A valid PDF can still be blank or have off-page staves. Require actual,
        # long horizontal printed lines, not merely a PDF header or SVG tags.
        assert sum(count > width * 0.6 for count in rows) >= 5
        assert sum(rows) > 3000
        bitmap.close()
        page.close()


def test_source_tree_not_mutated():
    data = sample()
    root = parse_score(data)
    before = deepcopy(root)
    to_clarinet(serialize(root), "P1")
    assert serialize(root) == serialize(before)


def test_implicit_c_key_becomes_explicit_d_key():
    root = parse_score(sample())
    attrs = root.find(".//attributes")
    attrs.remove(attrs.find("key"))
    result = parse_score(to_clarinet(serialize(root), "P1")[0])
    assert result.findtext(".//fifths") == "2"
