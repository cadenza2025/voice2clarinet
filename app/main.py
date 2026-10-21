from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from io import BytesIO
import logging
import os
from pathlib import Path
import shutil
import tempfile
from threading import RLock
import time
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .notation import (
    MAX_XML,
    ScoreError,
    parse_score,
    part_summaries,
    preview_part,
    render_score,
    to_clarinet,
)
from .omr import engine_path, recognize

BASE = Path(__file__).resolve().parent
DATA = Path(os.getenv("DATA_DIR", str(BASE.parent / "data"))).resolve()
MAX_PDF = 20 * 1024 * 1024
TTL = 2 * 60 * 60
MAX_JOBS = 8
LOG = logging.getLogger("clarinet")


@dataclass
class Job:
    id: str
    name: str
    folder: Path
    created: float = field(default_factory=time.time)
    status: str = "queued"
    error: str | None = None
    movements: list = field(default_factory=list)
    output: dict | None = None
    busy: bool = False
    lock: RLock = field(default_factory=RLock)


JOBS: dict[str, Job] = {}
LOCK = RLock()
POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="omr")


def cleanup():
    with LOCK:
        now = time.time()
        for key, job in list(JOBS.items()):
            with job.lock:
                if now - job.created > TTL and job.status not in ("queued", "recognizing") and not job.busy:
                    shutil.rmtree(job.folder)
                    del JOBS[key]
        if DATA.exists():
            for folder in DATA.iterdir():
                if folder.is_dir() and folder.name not in JOBS and now - folder.stat().st_mtime > TTL:
                    try:
                        UUID(folder.name)
                    except ValueError:
                        continue
                    shutil.rmtree(folder)


@asynccontextmanager
async def lifespan(app):
    DATA.mkdir(parents=True, exist_ok=True)
    cleanup()
    yield
    POOL.shutdown(wait=True)


app = FastAPI(title="Reed & Voice", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(","),
)


@app.middleware("http")
async def security_headers(request, call_next):
    if request.method in ("POST", "PUT", "DELETE"):
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{request.headers.get('host')}"
        if request.headers.get("sec-fetch-site") == "cross-site" or (origin and origin != expected):
            return JSONResponse(
                status_code=403, content={"detail": "Cross-origin score changes are not allowed."}
            )
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api") else "no-cache"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; object-src 'self'; frame-src 'self' blob:; base-uri 'none'; form-action 'self'"
    )
    return response


@app.exception_handler(ScoreError)
async def score_error(request, exc):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


async def body(request: Request, limit: int) -> bytes:
    if request.headers.get("content-length"):
        try:
            size = int(request.headers["content-length"])
        except ValueError as exc:
            raise HTTPException(400, "Invalid Content-Length.") from exc
        if size > limit:
            raise HTTPException(413, f"File too large. Limit: {limit // (1024 * 1024)} MB.")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise HTTPException(413, f"File too large. Limit: {limit // (1024 * 1024)} MB.")
    return bytes(data)


def get_job(job_id: str) -> Job:
    cleanup()
    with LOCK:
        job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "This session expired or the server restarted. Upload the PDF again.")
    return job


def movement(job: Job, index: int) -> bytes:
    if job.status != "ready" or not 0 <= index < len(job.movements):
        raise HTTPException(409, "Recognition is not ready, or the movement does not exist.")
    return (job.folder / f"movement-{index}.musicxml").read_bytes()


def job_json(job):
    with job.lock:
        return {
            "id": job.id,
            "name": job.name,
            "status": job.status,
            "error": job.error,
            "movements": job.movements,
            "output": job.output,
            "busy": job.busy,
            "expires_at": job.created + TTL,
            "has_log": (job.folder / "recognition.log").is_file(),
        }


def run_recognition(job):
    with job.lock:
        job.status = "recognizing"
    try:
        files = recognize(job.folder / "source.pdf", job.folder)
        movements = []
        for index, file in enumerate(files):
            data = file.read_bytes()
            root = parse_score(data)
            movements.append(
                {
                    "index": index,
                    "title": root.findtext("work/work-title")
                    or root.findtext("movement-title")
                    or f"Movement {index + 1}",
                    "parts": part_summaries(data),
                }
            )
        with job.lock:
            job.movements = movements
            job.status = "ready"
    except ScoreError as exc:
        LOG.warning("Recognition failed for %s: %s", job.id, exc)
        with job.lock:
            job.status, job.error = "failed", str(exc)
    except Exception:
        LOG.exception("Unexpected recognition failure for %s", job.id)
        with job.lock:
            job.status, job.error = (
                "failed",
                "Unexpected recognition failure. Check the server and recognition logs.",
            )


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "omr_available": bool(engine_path()),
        "engine": "Audiveris",
        "max_mb": 20,
        "max_pages": 12,
    }


def validate_pdf(data):
    if not data.startswith(b"%PDF-"):
        raise HTTPException(415, "Upload a PDF file, not an image or MusicXML.")
    try:
        reader = PdfReader(BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise HTTPException(422, "Password-protected PDFs are not supported. Export an unlocked copy.")
        if not 1 <= len(reader.pages) <= 12:
            raise HTTPException(422, "Upload between 1 and 12 pages at a time.")
        if any(
            not 72 <= float(p.mediabox.width) <= 1800 or not 72 <= float(p.mediabox.height) <= 2400
            for p in reader.pages
        ):
            raise HTTPException(422, "PDF pages are too large. Export at A4 or Letter size.")
    except (PdfReadError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, "This PDF is damaged or unreadable. Export it again.") from exc


@app.post("/api/jobs", status_code=202)
async def upload(request: Request):
    if not engine_path():
        raise HTTPException(
            503,
            "PDF recognition needs Audiveris. Run docker compose up --build, or configure AUDIVERIS_BIN (see README).",
        )
    data = await body(request, MAX_PDF)
    await run_in_threadpool(validate_pdf, data)
    cleanup()
    with LOCK:
        if len(JOBS) >= MAX_JOBS:
            raise HTTPException(
                429, "The server has 8 active sessions. Delete a finished session or wait for expiry."
            )
        key = str(uuid4())
        folder = DATA / key
        folder.mkdir(parents=True)
        (folder / "source.pdf").write_bytes(data)
        name = request.headers.get("x-file-name", "Uploaded score.pdf")[:160]
        job = Job(key, name, folder)
        JOBS[key] = job
        POOL.submit(run_recognition, job)
    return job_json(job)


@app.get("/api/jobs/{job_id}")
def status(job_id: str):
    return job_json(get_job(job_id))


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    movement: int = Field(ge=0, le=31)
    part_id: str = Field(min_length=1, max_length=128)


def operation(job, callback):
    with LOCK, job.lock:
        if job.id not in JOBS:
            raise HTTPException(404, "This session expired. Upload the PDF again.")
        if job.busy:
            raise HTTPException(409, "Another operation is running for this session.")
        job.busy = True
    try:
        return callback()
    finally:
        with job.lock:
            job.busy = False


def publish_score(job, data, stem):
    # A failed correction/engraving must leave the previous downloads intact.
    with tempfile.TemporaryDirectory(prefix="render-", dir=job.folder) as directory:
        staging = Path(directory)
        pages = render_score(data, staging, stem)
        if stem == "clarinet":
            (staging / "clarinet.musicxml").write_bytes(data)
        for path in staging.iterdir():
            os.replace(path, job.folder / path.name)
        for path in job.folder.glob(f"{stem}-*.svg"):
            number = int(path.stem.split("-")[-1])
            if number > pages:
                path.unlink()
        return pages


@app.post("/api/jobs/{job_id}/preview")
def preview(job_id: str, selection: Selection):
    job = get_job(job_id)

    def work():
        data = preview_part(movement(job, selection.movement), selection.part_id)
        pages = publish_score(job, data, "preview")
        return {"pages": pages, "part_id": selection.part_id}

    return operation(job, work)


@app.post("/api/jobs/{job_id}/convert")
def convert(job_id: str, selection: Selection):
    job = get_job(job_id)

    def work():
        data, warnings = to_clarinet(movement(job, selection.movement), selection.part_id)
        pages = publish_score(job, data, "clarinet")
        with job.lock:
            job.output = {
                "pages": pages,
                "warnings": warnings,
                "part_id": selection.part_id,
                "movement": selection.movement,
                "revision": str(uuid4()),
            }
        return job.output

    return operation(job, work)


@app.put("/api/jobs/{job_id}/correction")
async def correction(job_id: str, request: Request):
    job = get_job(job_id)
    data = await body(request, MAX_XML)

    def work():
        # This is a *written* clarinet score: validate/render, never transpose again.
        if job.output is None:
            raise ScoreError("Convert a selected vocal part before importing a correction.")
        root = parse_score(data)
        if len(root.findall("part")) != 1:
            raise ScoreError("Correction must contain exactly one clarinet part.")
        for attrs in root.findall(".//attributes"):
            for t in attrs.findall("transpose"):
                if (
                    t.findtext("chromatic") != "-2"
                    or t.findtext("diatonic") != "-1"
                    or t.findtext("octave-change", "0") != "0"
                ):
                    raise ScoreError(
                        "Keep the B-flat clarinet instrument transposition (-1 diatonic, -2 chromatic)."
                    )
        if not root.findall(".//transpose"):
            raise ScoreError(
                "Correction is missing the B-flat clarinet instrument transposition. Export from the clarinet part, not a concert-pitch score."
            )
        pages = publish_score(job, data, "clarinet")
        with job.lock:
            job.output = {
                **job.output,
                "pages": pages,
                "warnings": [
                    "Corrected written-pitch score. No additional transposition was applied. Proofread before playing."
                ],
                "revision": str(uuid4()),
            }
        return job.output

    return await run_in_threadpool(operation, job, work)


@app.get("/api/jobs/{job_id}/files/{filename}")
def download(job_id: str, filename: str):
    job = get_job(job_id)
    allowed = {
        "source.pdf": "application/pdf",
        "recognition.log": "text/plain",
        "clarinet.musicxml": "application/vnd.recordare.musicxml+xml",
        "clarinet.pdf": "application/pdf",
    }
    for prefix in ("preview", "clarinet"):
        for page in range(1, 41):
            allowed[f"{prefix}-{page}.svg"] = "image/svg+xml"
    for index in range(len(job.movements)):
        allowed[f"movement-{index}.musicxml"] = "application/vnd.recordare.musicxml+xml"
    if filename not in allowed:
        raise HTTPException(404, "File not found.")
    with job.lock:
        if job.busy and filename.startswith(("clarinet", "preview")):
            raise HTTPException(409, "The score is being updated. Try again when rendering finishes.")
        path = job.folder / filename
        if not path.is_file():
            raise HTTPException(404, "File not found.")
        return FileResponse(
            path,
            media_type=allowed[filename],
            filename=filename,
            content_disposition_type="inline" if filename.endswith((".pdf", ".svg")) else "attachment",
        )


@app.delete("/api/jobs/{job_id}", status_code=204)
def delete(job_id: str):
    job = get_job(job_id)
    with LOCK, job.lock:
        if job.status in ("queued", "recognizing") or job.busy:
            raise HTTPException(409, "Wait for processing to finish before deleting this session.")
        shutil.rmtree(job.folder)
        del JOBS[job_id]


app.mount("/", StaticFiles(directory=BASE / "static", html=True), name="website")
