from io import BytesIO
import time

from fastapi.testclient import TestClient
from pypdf import PdfWriter
import pytest

from app import main
from scripts.make_sample import sample


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA", tmp_path)
    main.JOBS.clear()
    # Lifespan is not needed here; avoid shutting down the module's shared executor.
    with TestClient(main.app, raise_server_exceptions=True) as api:
        yield api


@pytest.fixture(autouse=True)
def executor(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    pool = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(main, "POOL", pool)
    yield
    pool.shutdown(wait=True)


def pdf(pages=1):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(595, 842)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def wait_ready(client, job_id):
    for _ in range(100):
        response = client.get(f"/api/jobs/{job_id}").json()
        if response["status"] not in ("recognizing", "queued"):
            return response
        time.sleep(0.01)
    pytest.fail("Recognition worker did not finish.")


def mock_engine(monkeypatch):
    monkeypatch.setattr(main, "engine_path", lambda: "fake-test-engine")

    def recognize(source, folder):
        destination = folder / "movement-0.musicxml"
        destination.write_bytes(sample())
        return [destination]

    monkeypatch.setattr(main, "recognize", recognize)


def test_missing_engine_is_explicit(client, monkeypatch):
    monkeypatch.setattr(main, "engine_path", lambda: None)
    assert client.get("/api/health").json()["omr_available"] is False
    response = client.post("/api/jobs", content=pdf())
    assert response.status_code == 503
    assert "Audiveris" in response.json()["detail"]


def test_upload_bounds_and_invalid_pdf(client, monkeypatch):
    mock_engine(monkeypatch)
    assert client.post("/api/jobs", content=b"not a pdf").status_code == 415
    assert client.post("/api/jobs", content=b"%PDF-broken").status_code == 422
    assert client.post("/api/jobs", content=pdf(13)).status_code == 422
    assert (
        client.post("/api/jobs", content=b"x", headers={"Content-Length": str(main.MAX_PDF + 1)}).status_code
        == 413
    )


def test_full_api_flow_mock_omr_real_rendering(client, monkeypatch):
    mock_engine(monkeypatch)
    response = client.post("/api/jobs", content=pdf(), headers={"X-File-Name": "original.pdf"})
    assert response.status_code == 202
    job_id = response.json()["id"]
    job = wait_ready(client, job_id)
    assert job["status"] == "ready"
    assert len(job["movements"][0]["parts"]) == 2
    base = f"/api/jobs/{job_id}"
    assert client.post(base + "/convert", json={"movement": 0, "part_id": "P2"}).status_code == 422
    selected = {"movement": 0, "part_id": "P1"}
    assert client.post(base + "/preview", json=selected).status_code == 200
    result = client.post(base + "/convert", json=selected)
    assert result.status_code == 200, result.text
    assert result.json()["pages"] >= 1
    xml = client.get(base + "/files/clarinet.musicxml").content
    assert client.get(base + "/files/clarinet.pdf").content.startswith(b"%PDF-")
    assert "<svg" in client.get(base + "/files/clarinet-1.svg").text
    # Importing a correction is deliberately not another transposition.
    assert client.put(base + "/correction", content=xml).status_code == 200
    assert client.get(base + "/files/clarinet.musicxml").content == xml
    assert client.put(base + "/correction", content=sample()).status_code == 422
    assert client.get(base + "/files/recognition.log").status_code == 404
    assert client.get(base + "/files/nope").status_code == 404
    assert client.delete(base).status_code == 204
    assert client.get(base).status_code == 404


def test_failure_reaches_client(client, monkeypatch):
    mock_engine(monkeypatch)

    def fail(*args):
        raise main.ScoreError("No exportable music.")

    monkeypatch.setattr(main, "recognize", fail)
    response = client.post("/api/jobs", content=pdf())
    job = wait_ready(client, response.json()["id"])
    assert job["status"] == "failed"
    assert job["error"] == "No exportable music."


def test_site_and_headers(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Reed &amp; Voice" in response.text
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert client.get("/app.js").status_code == 200
    assert client.get("/styles.css").status_code == 200


def test_cross_site_upload_and_dns_rebinding_rejected(client):
    assert (
        client.post("/api/jobs", content=pdf(), headers={"Origin": "https://example.org"}).status_code == 403
    )
    assert client.get("/api/health", headers={"Host": "attacker.example"}).status_code == 400
