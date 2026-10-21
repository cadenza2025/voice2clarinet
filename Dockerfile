FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AUDIVERIS_BIN=/opt/audiveris/bin/Audiveris \
    DATA_DIR=/app/data \
    TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata \
    _JAVA_OPTIONS="-Djava.awt.headless=true -Xms256m -Xmx4g"

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv ca-certificates curl fontconfig fonts-dejavu-core \
    tesseract-ocr-eng libfreetype6 libfontconfig1 \
    && curl -fsSL --retry 3 \
    https://github.com/Audiveris/audiveris/releases/download/5.11.0/Audiveris-5.11.0-ubuntu24.04-x86_64.deb \
    -o /tmp/audiveris.deb \
    && echo "f20113aaa33b3149ec8d6a09b2a7963360e65fafd92d69389987a85bbc3ec7a3  /tmp/audiveris.deb" | sha256sum -c - \
    && apt-get install -y --no-install-recommends /tmp/audiveris.deb \
    && test -x /opt/audiveris/bin/Audiveris \
    && rm /tmp/audiveris.deb && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /app/.venv \
    && /app/.venv/bin/pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN useradd --create-home --uid 10001 musician \
    && mkdir -p /app/data \
    && chown -R musician:musician /app/data /home/musician
USER musician
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
    CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"]
CMD ["/app/.venv/bin/python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
