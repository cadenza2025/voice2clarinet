"""Loss-minimizing MusicXML part extraction and concert-to-written conversion."""

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from io import BytesIO
from importlib.resources import files
from pathlib import Path
from threading import Lock
from zipfile import BadZipFile, ZipFile

from lxml import etree as ET

MAX_XML = 8 * 1024 * 1024
RENDER_LOCK = Lock()
STEPS = "CDEFGAB"
NATURAL = (0, 2, 4, 5, 7, 9, 11)


class ScoreError(ValueError):
    pass


def parse_score(data: bytes):
    if len(data) > MAX_XML:
        raise ScoreError("MusicXML exceeds the 8 MB limit.")
    try:
        root = ET.fromstring(data, ET.XMLParser(resolve_entities=False, no_network=True, load_dtd=False))
    except ET.XMLSyntaxError as exc:
        raise ScoreError("The MusicXML is not well-formed XML.") from exc
    if any(isinstance(node, ET._Entity) for node in root.iter()):
        raise ScoreError("XML entities are not supported.")
    if root.tag != "score-partwise":
        raise ScoreError("Use uncompressed, unnamespaced score-partwise MusicXML.")
    parts = root.findall("part")
    ids = [p.get("id") for p in parts]
    declared = [p.get("id") for p in root.findall("part-list/score-part")]
    if not parts or None in ids or len(set(ids)) != len(ids) or set(ids) != set(declared):
        raise ScoreError("MusicXML needs unique parts and a matching part-list.")
    if len(parts) > 32 or len(root.findall(".//note")) > 20000:
        raise ScoreError("This score is too large (maximum 32 parts / 20,000 notes).")
    if any(not p.findall("measure") for p in parts):
        raise ScoreError("Each part must contain at least one measure.")
    return root


def read_musicxml(path: Path) -> bytes:
    if path.suffix.lower() != ".mxl":
        data = path.read_bytes()
        parse_score(data)
        return data
    try:
        with ZipFile(path) as archive:
            if len(archive.infolist()) > 256:
                raise ScoreError("Too many files in compressed MusicXML.")
            container_info = archive.getinfo("META-INF/container.xml")
            if container_info.file_size > 65536:
                raise ScoreError("Invalid MusicXML container.")
            container = ET.fromstring(
                archive.read(container_info),
                ET.XMLParser(resolve_entities=False, no_network=True, load_dtd=False),
            )
            names = container.xpath("//*[local-name()='rootfile']/@full-path")
            if not names:
                raise ScoreError("Compressed MusicXML has no root score.")
            info = archive.getinfo(names[0])
            if info.file_size > MAX_XML:
                raise ScoreError("Expanded MusicXML exceeds the 8 MB limit.")
            data = archive.read(info)
    except (BadZipFile, KeyError, ET.XMLSyntaxError) as exc:
        raise ScoreError("The recognition engine produced invalid compressed MusicXML.") from exc
    parse_score(data)
    return data


def part_summaries(data: bytes) -> list[dict]:
    root = parse_score(data)
    result = []
    for part in root.findall("part"):
        info = next(p for p in root.findall("part-list/score-part") if p.get("id") == part.get("id"))
        notes = part.findall(".//note")
        staves = max([int(x.text) for x in part.findall(".//staves")] or [1])
        name = info.findtext("part-name") or part.get("id")
        result.append(
            {
                "id": part.get("id"),
                "name": name,
                "instrument": info.findtext("score-instrument/instrument-name") or "Unspecified",
                "measures": len(part.findall("measure")),
                "notes": sum(n.find("pitch") is not None for n in notes),
                "lyrics": len(part.findall(".//lyric")),
                "staves": staves,
                "likely_voice": staves == 1
                and (
                    bool(part.findall(".//lyric"))
                    or any(
                        s in name.lower() for s in ("voice", "vocal", "soprano", "alto", "tenor", "singer")
                    )
                ),
            }
        )
    return result


def serialize(root) -> bytes:
    return ET.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def extract_part(root, part_id: str):
    root = deepcopy(root)
    part = next((p for p in root.findall("part") if p.get("id") == part_id), None)
    if part is None:
        raise ScoreError("Select one of the recognized parts.")
    for other in root.findall("part"):
        if other is not part:
            root.remove(other)
    part_list = root.find("part-list")
    for info in list(part_list):
        if info.tag != "score-part" or info.get("id") != part_id:
            part_list.remove(info)
    # Score-level credits can include piano/voice labels and absolute positions.
    for credit in root.findall("credit"):
        root.remove(credit)
    for layout in root.findall(".//print"):
        layout.getparent().remove(layout)
    for node in part.iter():
        for key in ("default-x", "default-y", "relative-x", "relative-y"):
            node.attrib.pop(key, None)
    return root, part


def preview_part(data: bytes, part_id: str) -> bytes:
    root, _ = extract_part(parse_score(data), part_id)
    return serialize(root)


def integer(node, name, default=None):
    text = node.findtext(name)
    try:
        return int(text) if text is not None else default
    except ValueError as exc:
        raise ScoreError(f"Invalid {name} in MusicXML.") from exc


def shift_pitch(parent, step_tag, alter_tag, diatonic, chromatic, octave_tag=None):
    step = parent.findtext(step_tag)
    if step not in STEPS:
        raise ScoreError("A recognized note has an invalid pitch step. Correct the MusicXML.")
    octave = integer(parent, octave_tag, 4) if octave_tag else 4
    if octave is None or not -1 <= octave <= 10:
        raise ScoreError("A recognized note has an invalid octave.")
    try:
        alteration = Decimal(parent.findtext(alter_tag, "0"))
        if not alteration.is_finite() or abs(alteration) > 4:
            raise InvalidOperation
    except InvalidOperation as exc:
        raise ScoreError("A recognized accidental is invalid.") from exc
    old_index = STEPS.index(step)
    new_octave, new_index = divmod(octave * 7 + old_index + diatonic, 7)
    target = octave * 12 + NATURAL[old_index] + alteration + chromatic
    new_alter = target - (new_octave * 12 + NATURAL[new_index])
    parent.find(step_tag).text = STEPS[new_index]
    alter = parent.find(alter_tag)
    if new_alter:
        if alter is None:
            alter = ET.Element(alter_tag)
            parent.insert(list(parent).index(parent.find(step_tag)) + 1, alter)
        alter.text = str(new_alter)
    elif alter is not None:
        parent.remove(alter)
    if octave_tag:
        parent.find(octave_tag).text = str(new_octave)


def to_clarinet(data: bytes, part_id: str) -> tuple[bytes, list[str]]:
    root, part = extract_part(parse_score(data), part_id)
    if part.findall(".//unpitched") or part.findall(".//tablature"):
        raise ScoreError("This is an unpitched/percussion part. Select the pitched vocal part.")
    if any((integer(x, "staves", 1) or 1) > 1 for x in part.findall(".//attributes")):
        raise ScoreError(
            "This part has multiple staves (probably piano). Select a separate vocal part, or separate it in Audiveris/MuseScore first."
        )
    info = root.find("part-list/score-part")
    for child in list(info):
        info.remove(child)
    ET.SubElement(info, "part-name").text = "Clarinet in B-flat"
    ET.SubElement(info, "part-abbreviation").text = "Cl."
    inst_id = f"{part_id}-clarinet"
    inst = ET.SubElement(info, "score-instrument", id=inst_id)
    ET.SubElement(inst, "instrument-name").text = "Clarinet in B-flat"
    midi = ET.SubElement(info, "midi-instrument", id=inst_id)
    ET.SubElement(midi, "midi-channel").text = "1"
    ET.SubElement(midi, "midi-program").text = "72"
    source_d, source_c = 0, 0
    warnings = [
        "Recognition is not proofread. Compare every measure, accidental, rhythm, tie and repeat with the original PDF.",
        "No automatic octave fitting is applied; written clarinet range is approximately E3 to C7.",
    ]
    outside = 0
    initialized = False
    for measure_index, measure in enumerate(part.findall("measure")):
        # Process attributes in document order: mid-score instrument/key changes matter.
        if measure_index == 0 and measure.find("attributes") is None:
            measure.insert(0, ET.Element("attributes"))
        for event in list(measure):
            if event.tag == "attributes":
                transposes = event.findall("transpose")
                if len(transposes) > 1 or any(t.get("number") for t in transposes):
                    raise ScoreError(
                        "Staff-specific instrument transpositions need correction in a notation editor."
                    )
                if transposes:
                    t = transposes[0]
                    if t.find("double") is not None:
                        raise ScoreError("Octave-doubled transposing instruments are not supported.")
                    source_c = integer(t, "chromatic", 0) + 12 * integer(t, "octave-change", 0)
                    source_d = integer(t, "diatonic")
                    if source_d is None and source_c != 0:
                        raise ScoreError(
                            "The source instrument needs an explicit diatonic transposition in MusicXML."
                        )
                    source_d = (source_d or 0) + 7 * integer(t, "octave-change", 0)
                    event.remove(t)
                d, c = source_d + 1, source_c + 2
                if not initialized and event.find("key") is None:
                    key = ET.Element("key")
                    ET.SubElement(key, "fifths").text = "0"
                    divisions = event.find("divisions")
                    event.insert(list(event).index(divisions) + 1 if divisions is not None else 0, key)
                for key in event.findall("key"):
                    fifths = key.find("fifths")
                    if fifths is None:
                        raise ScoreError(
                            "Nontraditional key signatures need correction in a notation editor."
                        )
                    fifths.text = str(integer(key, "fifths") + 7 * c - 12 * d)
                    cancel = key.find("cancel")
                    if cancel is not None:
                        cancel.text = str(integer(key, "cancel") + 7 * c - 12 * d)
                    for ko in key.findall("key-octave"):
                        key.remove(ko)
                for clef in event.findall("clef"):
                    for child in list(clef):
                        clef.remove(child)
                    ET.SubElement(clef, "sign").text = "G"
                    ET.SubElement(clef, "line").text = "2"
                if measure_index == 0 and event.find("clef") is None:
                    clef = ET.SubElement(event, "clef")
                    ET.SubElement(clef, "sign").text = "G"
                    ET.SubElement(clef, "line").text = "2"
                # Insert in MusicXML attribute order, before measure-style.
                target = ET.Element("transpose")
                ET.SubElement(target, "diatonic").text = "-1"
                ET.SubElement(target, "chromatic").text = "-2"
                style = event.find("measure-style")
                event.insert(list(event).index(style) if style is not None else len(event), target)
                initialized = True
            elif event.tag == "note":
                pitch = event.find("pitch")
                if pitch is not None:
                    shift_pitch(pitch, "step", "alter", source_d + 1, source_c + 2, "octave")
                    midi_pitch = (
                        (int(pitch.findtext("octave")) + 1) * 12
                        + NATURAL[STEPS.index(pitch.findtext("step"))]
                        + float(pitch.findtext("alter", "0"))
                    )
                    outside += not 52 <= midi_pitch <= 96
                # Accidentals and display positions must be regenerated from the new pitches.
                for accidental in event.findall("accidental"):
                    event.remove(accidental)
                for instrument in event.findall("instrument"):
                    instrument.set("id", inst_id)
                rest = event.find("rest")
                if rest is not None:
                    for display in list(rest):
                        rest.remove(display)
            elif event.tag == "harmony":
                for tag in ("root", "bass"):
                    node = event.find(tag)
                    if node is not None:
                        shift_pitch(node, f"{tag}-step", f"{tag}-alter", source_d + 1, source_c + 2)
            elif event.tag == "direction":
                for sound in event.findall(".//sound"):
                    for tag in ("midi-instrument", "midi-device"):
                        for child in sound.findall(tag):
                            sound.remove(child)
    if outside:
        warnings.append(
            f"{outside} notes lie outside E3-C7 written pitch. Review range in a notation editor; pitches were not silently moved."
        )
    if not part.findall(".//pitch"):
        raise ScoreError("No pitched notes were recognized in this part.")
    return serialize(root), warnings


def render_score(data: bytes, folder: Path, stem: str) -> int:
    """Engrave written pitches locally; use the same SVGs for preview and vector PDF."""
    import verovio
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.graphics import renderPDF
    from svglib.svglib import svg2rlg

    score = parse_score(data)
    title = (score.findtext("work/work-title") or score.findtext("movement-title") or "Score")[:70]
    subtitle = "Clarinet in B-flat - written pitch" if stem == "clarinet" else "Recognized score"
    with RENDER_LOCK:
        toolkit = verovio.toolkit()
        # Verovio resource defaults are thread-local; FastAPI renders in worker threads.
        if not toolkit.setResourcePath(str(files("verovio") / "data")):
            raise ScoreError("Verovio font resources are missing. Reinstall the pinned verovio package.")
        toolkit.setOptions(
            {
                "inputFrom": "musicxml",
                "pageWidth": 2100,
                "pageHeight": 2970,
                "scale": 40,
                "adjustPageHeight": False,
                "breaks": "auto",
                "header": "none",
                "footer": "none",
                "pageMarginTop": 230,
                "pageMarginBottom": 120,
                "pageMarginLeft": 120,
                "pageMarginRight": 120,
            }
        )
        if not toolkit.loadData(data.decode("utf-8")):
            raise ScoreError(
                "The notation renderer could not read this MusicXML. Correct it in a notation editor."
            )
        pages = toolkit.getPageCount()
        if not 1 <= pages <= 40:
            raise ScoreError("The engraved score exceeds the 40-page output limit.")
        pdf = canvas.Canvas(str(folder / f"{stem}.pdf"), pagesize=(595.28, 841.89))
        pdf.setTitle("Clarinet in B-flat" if stem == "clarinet" else "Recognized part")
        for page in range(1, pages + 1):
            tree = ET.fromstring(toolkit.renderToSVG(page).encode("utf-8"))
            ns = "{http://www.w3.org/2000/svg}"
            nested = tree.find(f"{ns}svg")
            if nested is None or not nested.get("viewBox"):
                raise ScoreError("The notation engine returned an unsupported SVG layout.")
            # svglib ignores nested SVG viewBoxes: lift the coordinate system to
            # the root so preview and PDF have identical, correctly scaled staves.
            tree.set("viewBox", nested.attrib.pop("viewBox"))
            nested.tag = f"{ns}g"
            for stylesheet in tree.findall(f"{ns}style"):
                tree.remove(stylesheet)
            for node in tree.iter():
                classes = set(node.get("class", "").split())
                if "page-margin" in classes:
                    node.set("font-family", "Times,serif")
                if classes & {"ending", "fing", "reh", "tempo"}:
                    node.set("font-weight", "bold")
                if classes & {"dir", "dynam", "mNum"}:
                    node.set("font-style", "italic")
                if "label" in classes:
                    node.set("font-weight", "normal")
                if node.tag == f"{ns}path" and node.get("stroke") is None:
                    node.set("stroke", "#000000")
            # Plain text headers avoid unsupported nested-tspan alignment in PDF.
            for y, size, text in (("1000", "550", title), ("1600", "310", subtitle)):
                text_width = stringWidth(text, "Times-Roman", float(size))
                if text_width > 18000:
                    size = str(float(size) * 18000 / text_width)
                label = ET.SubElement(
                    tree,
                    f"{ns}text",
                    {
                        "x": "10500",
                        "y": y,
                        "font-size": size,
                        "font-family": "Times,serif",
                        "text-anchor": "middle",
                        "fill": "#000000",
                    },
                )
                label.text = text
            footer = ET.SubElement(
                tree,
                f"{ns}text",
                {
                    "x": "10500",
                    "y": "28900",
                    "font-size": "280",
                    "font-family": "Times,serif",
                    "text-anchor": "middle",
                    "fill": "#000000",
                },
            )
            footer.text = f"{page} / {pages}"
            svg = ET.tostring(tree, encoding="unicode")
            (folder / f"{stem}-{page}.svg").write_text(svg, encoding="utf-8")
            drawing = svg2rlg(BytesIO(svg.encode("utf-8")))
            if drawing is None or not drawing.width or not drawing.height:
                raise ScoreError("Could not create a printable score from the engraved page.")
            scale = min(595.28 / drawing.width, 841.89 / drawing.height)
            drawing.scale(scale, scale)
            renderPDF.draw(drawing, pdf, 0, 841.89 - drawing.height * scale)
            pdf.showPage()
        pdf.save()
        return pages
