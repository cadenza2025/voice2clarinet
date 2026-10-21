from pathlib import Path

import pytest

from app import omr
from app.notation import ScoreError
from scripts.make_sample import sample


def test_engine_missing_has_setup_error(tmp_path, monkeypatch):
    monkeypatch.setattr(omr, "engine_path", lambda: None)
    with pytest.raises(ScoreError, match="AUDIVERIS_BIN"):
        omr.recognize(tmp_path / "source.pdf", tmp_path)


def test_supported_cli_and_actual_export_detection(tmp_path, monkeypatch):
    monkeypatch.setattr(omr, "engine_path", lambda: "Audiveris")
    calls = []

    class Process:
        def __init__(self, command, **kwargs):
            calls.append(command)
            output = Path(command[command.index("-output") + 1])
            (output / "book.musicxml").write_bytes(sample())

        def wait(self, timeout=None):
            assert timeout == 600
            return 0

    monkeypatch.setattr(omr.subprocess, "Popen", Process)
    files = omr.recognize(tmp_path / "source.pdf", tmp_path)
    assert files[0].read_bytes() == sample()
    assert calls[0][1:5] == ["-batch", "-transcribe", "-export", "-save"]
    assert "--" in calls[0]


def test_exit_zero_with_no_export_is_not_fake_success(tmp_path, monkeypatch):
    monkeypatch.setattr(omr, "engine_path", lambda: "Audiveris")

    class Process:
        def __init__(self, *args, **kwargs):
            pass

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(omr.subprocess, "Popen", Process)
    with pytest.raises(ScoreError, match="no exportable music"):
        omr.recognize(tmp_path / "source.pdf", tmp_path)
