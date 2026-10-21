"""Real-engine smoke check. Never substitutes fixtures for recognition output."""

import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.notation import part_summaries, render_score, to_clarinet  # noqa: E402
from app.omr import engine_path, recognize  # noqa: E402


def main():
    if not engine_path():
        raise SystemExit("Audiveris unavailable. Configure AUDIVERIS_BIN. This check does not use mocks.")
    source = Path(__file__).resolve().parents[1] / "app" / "static" / "demo" / "orchard-song.pdf"
    if not source.exists():
        raise SystemExit("Run python scripts\\make_sample.py first.")
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="reed-voice-real-omr-") as directory:
        folder = Path(directory)
        try:
            files = recognize(source, folder)
            summaries = []
            for file in files:
                data = file.read_bytes()
                parts = part_summaries(data)
                summaries.append(parts)
                candidates = [p for p in parts if p["staves"] == 1 and p["notes"]]
                if not candidates:
                    raise SystemExit(
                        "Real recognition exported no single-staff pitched part. Inspect the source in Audiveris."
                    )
                # This selection is only for the generated fixture smoke check.
                output, _ = to_clarinet(data, candidates[0]["id"])
                (folder / "clarinet.musicxml").write_bytes(output)
                pages = render_score(output, folder, "clarinet")
                assert pages > 0 and (folder / "clarinet.pdf").stat().st_size > 2000
            print(
                json.dumps(
                    {
                        "engine": "real Audiveris",
                        "seconds": round(time.monotonic() - start, 1),
                        "movements": summaries,
                        "pdf_rendered": True,
                    },
                    indent=2,
                )
            )
        except Exception:
            log = folder / "recognition.log"
            if log.exists():
                print(log.read_text(encoding="utf-8", errors="replace")[-12000:], file=sys.stderr)
            raise


if __name__ == "__main__":
    main()
