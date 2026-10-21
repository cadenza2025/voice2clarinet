"""Audiveris' documented batch CLI, with isolated output and a hard deadline."""

import os
from pathlib import Path
import shutil
import signal
import subprocess

from .notation import ScoreError, read_musicxml


def engine_path() -> str | None:
    configured = os.getenv("AUDIVERIS_BIN")
    if configured:
        path = Path(configured).expanduser()
        return str(path.resolve()) if path.is_file() else shutil.which(configured)
    return shutil.which("Audiveris") or shutil.which("audiveris")


def recognize(pdf: Path, folder: Path, timeout: int = 600) -> list[Path]:
    executable = engine_path()
    if not executable:
        raise ScoreError(
            "Audiveris is not installed or AUDIVERIS_BIN is invalid. Start with Docker Compose, or set AUDIVERIS_BIN to the installed Audiveris executable. See README."
        )
    output = folder / "omr"
    output.mkdir()
    command = [
        executable,
        "-batch",
        "-transcribe",
        "-export",
        "-save",
        "-output",
        str(output),
        "--",
        str(pdf),
    ]
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    with (folder / "recognition.log").open("wb") as log:
        try:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, **kwargs)
        except OSError as exc:
            raise ScoreError(
                "Audiveris could not start. Check its installation and Java runtime; see the server log."
            ) from exc
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False, capture_output=True
                )
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise ScoreError(
                "Recognition exceeded 10 minutes. Try a shorter PDF or correct the score in Audiveris."
            ) from exc
    if code:
        raise ScoreError(
            f"Audiveris exited with code {code}. Download the recognition log. Try a clean 300 DPI scan or correct the PDF in Audiveris."
        )
    files = sorted(
        p
        for p in output.rglob("*")
        if p.suffix.lower() in (".mxl", ".musicxml", ".xml") and "META-INF" not in p.parts
    )
    if not files:
        raise ScoreError(
            "Audiveris found no exportable music. Download its log; try a clearer scan or open the .omr project in Audiveris."
        )
    # Do not merge movements or choose the first output silently.
    results = []
    for index, path in enumerate(files):
        data = read_musicxml(path)
        destination = folder / f"movement-{index}.musicxml"
        destination.write_bytes(data)
        results.append(destination)
    return results
