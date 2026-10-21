# Reed & Voice

A private, full-stack website that reads a **voice-and-piano PDF**, lets you choose
the vocal part, and creates a **written B-flat clarinet score**. Includes real
[Audiveris](https://audiveris.github.io/audiveris/) optical music recognition
(OMR), local Verovio engraving, a paginated preview, downloadable vector PDF and
MusicXML, and a correction/re-import workflow. No cloud score-processing service,
API key, frontend build step, or CDN is used.

**GitHub hosts the code. GitHub Pages cannot run the Python/Java recognition
server.** Run the complete app locally or on a private server.

## Start with Docker (recommended)

Install Docker Desktop with Linux containers, or Docker Engine + Compose on a
Linux server. Reserve at least 6 GB RAM; 8 GB host RAM or more is recommended.
The image pins the official Audiveris 5.11.0 Linux AMD64 release and verifies its
SHA-256. Its Java runtime and native OCR engine are included in that release.
Apple Silicon uses AMD64 emulation and may be slower.

In this project's folder:

```powershell
docker compose up --build
```

Open **http://localhost:8000**. The first build downloads dependencies and can
take several minutes. To run persistently:

```powershell
docker compose up --build -d
docker compose logs -f web
```

Stop with `docker compose down`. Uploaded scores are stored in the `scores`
Docker volume, not in the image or Git. To erase all retained uploads, stop the
app and use `docker compose down --volumes` (this is destructive).

## Run directly on Windows

Install Python 3.12 and the official
[Audiveris 5.11.0 **windowsConsole** MSI](https://github.com/Audiveris/audiveris/releases/tag/5.11.0).
The console build reliably returns a process exit status and log output; a
separate Java install is not needed for the packaged release.

From PowerShell in the project folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:AUDIVERIS_BIN = 'C:\Program Files\Audiveris\Audiveris.exe'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Adjust `AUDIVERIS_BIN` to the actual executable. Keep this environment variable in
the same terminal as the server command. No PowerShell activation is required.
In Audiveris desktop, open **Tools → OCR languages** and install English (`eng`)
before the first recognition. Alternatively, place the official
[`eng.traineddata`](https://github.com/tesseract-ocr/tessdata_fast/tree/4.1.0)
in a folder and set `$env:TESSDATA_PREFIX = 'C:\path\to\tessdata'` in the server
terminal. Docker includes English automatically. Without language data,
instrument labels/lyrics may not be recognized even if note recognition works.
If you use [uv](https://docs.astral.sh/uv/), the equivalent setup is
`uv venv --python 3.12` then
`uv pip install --python .venv\Scripts\python.exe -r requirements.txt`.

The website can start without Audiveris, but shows a setup warning and rejects
PDF conversion with an actionable HTTP 503. It never pretends a fixture or
MusicXML import was PDF recognition. Check **http://localhost:8000/api/health**:
`omr_available` means the executable was found, not that Java/OCR has completed
a successful recognition. The real smoke check below verifies the latter.

For macOS/Linux without Docker, install the matching official Audiveris release,
use Python 3.12's virtual environment, and set `AUDIVERIS_BIN` to its launcher.
The app uses the documented
[`-batch -transcribe -export -save -output` CLI](https://audiveris.github.io/audiveris/_pages/guides/advanced/cli/).

## Use the website

1. Upload a printed voice-and-piano PDF (maximum **20 MB / 12 pages**). Recognition
   runs in a background queue and may take several minutes. One PDF is processed
   at a time, with a ten-minute deadline per job.
2. Choose a movement if Audiveris exported more than one. **Explicitly select
   the vocal part**. “Possible vocal” is only a hint based on labels/lyrics.
   Preview it and compare with the original PDF; confirm it is not the piano.
   Multi-staff piano conversion is rejected, not guessed at.
3. Make the clarinet part. Review every measure, then download the PDF or MusicXML.
   Both downloads and the preview come from the same locally engraved notation.
4. To fix mistakes, open the downloaded MusicXML in MuseScore or another notation
   editor. Keep the **B-flat clarinet instrument** and edit in **written pitch**
   (disable Concert Pitch). Correct notes, rests, rhythm, ties, slurs, key
   changes, lyrics, repeats, or layout. Export uncompressed `.musicxml`, then use
   **Fix a recognition mistake → Import corrected MusicXML**. The imported
   written score is validated and rendered **without a second transposition**.
   Advanced users can also edit its XML directly in the page.

The original `Orchard Song` sample is an eight-bar melody generated for this
project, not a transcription of a copyrighted song. Its PDF and source MusicXML
are in `app\static\demo`; the “Try our original sample” button sends that **PDF
through the real OMR pipeline**, not its source XML. Regenerate it with
`.\.venv\Scripts\python.exe scripts\make_sample.py`.

## Musical behavior and limitations

- A concert C4 becomes written D4. The resulting instrument metadata says that
  the clarinet sounds a major second lower (`diatonic=-1`, `chromatic=-2`).
  Key signatures move two fifths upward: C major → D major; A minor → B minor.
  Enharmonic spelling and octave crossings are computed diatonically, not just
  by MIDI note numbers.
- Existing source-instrument transposition is included in the conversion.
  Already-written B-flat notes are not shifted again. Unsupported ambiguous
  or staff-specific transpositions are rejected explicitly.
- Extraction copies the selected part's musical events instead of rebuilding
  its rhythm: durations, dots, tuplets, rests, ties, articulations, slurs,
  dynamics, lyrics, tempo, repeats and key changes are retained as MusicXML.
  Chord roots/basses are transposed too. Original page-position hints are
  discarded for a clean single-part layout. Cautionary accidental placement is
  regenerated; the original graphical layout is not preserved.
- Output is treble clef. **No automatic octave fitting, simplification, or
  polyphony reduction** is done. Notes outside approximately written E3–C7
  trigger a warning rather than being silently changed. A vocal part containing
  chords or multiple voices needs human arrangement for a monophonic clarinet.
- MusicXML import supports unnamespaced `score-partwise`. Audiveris's compressed
  `.mxl` exports are unpacked in memory with size checks. Correction imports use
  uncompressed `.musicxml` only. Nontraditional key signatures and percussion
  are rejected rather than mistransposed.
- **OMR is imperfect**, particularly handwriting, skewed scans, faint print,
  dense accompaniment, multiple vocal lines, cross-staff notation, ornaments,
  transposing/octave vocal clefs, and unusual rhythms. The preview is a review
  aid, not a correctness guarantee. Compare ornament accidentals, clefs,
  repeats and every bar with the PDF. Audiveris may export partial music even
  when its process exits successfully. Review missing bars and parts yourself.
- For scores where voice and piano were merged into one recognized part, open
  the original PDF in Audiveris desktop, fix the part/staff assignments, and
  export MusicXML to a notation editor. The website deliberately does not
  guess which notes to delete.
- Only arrange scores you own or have permission to use.

## Privacy, limits, and deployment

The browser contacts only this server. There is no telemetry or external upload.
The job URL contains an unguessable UUID; there is no job listing endpoint.
Treat that URL as private. No user-account or multi-tenant security system is
included. This release is intended for a personal computer or a **trusted,
access-controlled** server, not an anonymous public upload service.

Defaults: eight retained jobs, 20 MB PDF upload, 12 input pages, 8 MB expanded
MusicXML, 32 parts / 20,000 notes, and 40 rendered output pages. A single worker
serializes OMR; engraving is serialized because Verovio uses native resources.
Jobs are in-memory sessions with files on disk. A server restart loses session
links; download important work first. Files expire after two hours and are
removed opportunistically on subsequent API activity/startup; this is **not**
an exact deletion timer. Active recognition is never deleted mid-process.
Use **Delete session** to remove a finished job immediately.

`DATA_DIR` can relocate uploads. Keep it outside static web roots and Git.
Recognition logs can contain score names/content; they are only available
through the same private job URL. Failed jobs expose actionable messages rather
than fake results. Logs and `.omr` projects also remain in the private data
folder for troubleshooting until expiry.

For a private VPS: deploy the Compose project behind an HTTPS reverse proxy
with authentication (VPN or an identity-aware proxy). Keep the default loopback
port binding; never directly expose port 8000. Set the proxy request body limit
to at least 20 MB and allow long responses for engraving. Set `ALLOWED_HOSTS`
to a comma-separated list including your server hostname, `localhost`, and
`127.0.0.1` before starting Compose (for example,
`$env:ALLOWED_HOSTS = 'scores.example.com,localhost,127.0.0.1'`).
Unexpected Host headers and cross-site writes are rejected. Use **one Uvicorn
worker and one app replica**: job state is intentionally process-local. Keep
Docker's CPU/RAM/PID limits. A public multi-user service would additionally need
per-user storage/authorization, durable task queues, rate limits, isolated
per-upload sandboxing and quotas. PDF/XML parsers and the Java OMR engine handle
untrusted input: size limits alone are not a security sandbox.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check app scripts tests
```

Tests cover pitch spelling, octave crossings, keys/key changes, existing
instrument transposition/no double transposition, voice-only extraction, musical
event preservation, input limits, explicit failures, API correction, and actual
Verovio SVG/PDF generation. API tests **mock only Audiveris** so CI can run
without Java/OMR; they are not evidence of recognition accuracy.

With a real Audiveris installation configured:

```powershell
.\.venv\Scripts\python.exe scripts\verify_omr.py
```

This separate smoke check runs actual PDF recognition on the original sample,
reads its actual export, extracts a single-staff part and renders a clarinet PDF.
It does not assert perfect recognition or substitute source MusicXML. Inspect
the output summary for recognized part counts. Docker build/start requires
Docker; a successful local smoke check does not validate Docker itself.

During development on Windows, the actual Audiveris 5.11.0 console release read
the sample in about 17 seconds: eight vocal measures with 29 pitched notes and
a separate two-staff piano part. OMR missed some piano content/lyrics; this is
why the UI requires selection and proofreading. This is a smoke check, not an
accuracy benchmark. The Docker build was not exercised on the Windows host
because Docker was unavailable.

## Upload to GitHub

No repository or remote is created automatically. Create an **empty** GitHub
repository, then run the following from this project folder, replacing the URL
with your own:

```powershell
git init
git add .
git commit -m "Build Reed and Voice score converter"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/clarinet-score.git
git push -u origin main
```

Review staged files before committing. `.gitignore` excludes `.venv`, upload
data, caches and `.env`. Never commit uploaded user scores, private logs or
credentials. The included GitHub Actions workflow runs unit/API/rendering tests;
it does not deploy or start Audiveris. Do not turn on GitHub Pages expecting
recognition to run there.

## Components and licenses

Application code: MIT (see `LICENSE`). The original generated sample: CC0.
Audiveris is separately distributed under AGPL-3.0; Verovio is LGPL-3.0.
Dependencies retain their respective licenses. Review upstream license and
source-availability obligations before redistributing a Docker image or running
a modified public service. This project invokes the unmodified Audiveris CLI;
it does not relicense Audiveris or Verovio.
