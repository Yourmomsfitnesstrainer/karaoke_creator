from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from karaoke_generator import web


def _fake_generate(
    audio: Path,
    lyrics: Path,
    output_dir: Path,
    config: dict,
    *,
    background: str | None = None,
    progress_callback=None,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    for progress, label in ((0, "Preparing audio"), (40, "Separating vocals"), (80, "Rendering MP4"), (100, "Rendering MP4")):
        if progress_callback:
            progress_callback(progress, label)
    artifacts = {}
    for filename in ("karaoke.mp4", "karaoke.ass", "alignment.json"):
        path = output_dir / filename
        path.write_bytes(b"demo")
        artifacts[filename] = path
    return artifacts


def test_index_contains_accessible_progress_loader() -> None:
    response = TestClient(web.app).get("/")
    assert response.status_code == 200
    assert 'id="progress-panel"' in response.text
    assert 'role="progressbar"' in response.text
    assert "'/api/jobs'" in response.text
    assert "job.progress" in response.text


def test_language_name_and_whitespace_are_normalized() -> None:
    assert web._normalize_language(" English ") == "en"
    assert web._normalize_language(" RU ") == "ru"


def test_invalid_language_is_rejected_before_generation() -> None:
    with pytest.raises(HTTPException) as error:
        web._normalize_language("x")
    assert error.value.status_code == 400


def test_background_job_reaches_100_and_exposes_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(web, "JOBS_ROOT", tmp_path)
    monkeypatch.setattr(web, "generate", _fake_generate)
    web.JOBS.clear()
    client = TestClient(web.app)

    response = client.post(
        "/api/jobs",
        files={"audio": ("song.mp3", b"audio", "audio/mpeg"), "lyrics": ("lyrics.txt", b"hello", "text/plain")},
        data={"language": "en", "audio_mode": "original"},
    )
    assert response.status_code == 202
    status = client.get(response.json()["status_url"]).json()
    assert status["status"] == "complete"
    assert status["progress"] == 100
    assert status["stage"] == "Karaoke ready"
    assert client.get(status["artifacts"]["karaoke.mp4"]).status_code == 200


def test_background_job_reports_failure(tmp_path: Path, monkeypatch) -> None:
    def fail(*args, progress_callback=None, **kwargs):
        if progress_callback:
            progress_callback(20, "Separating vocals")
        raise RuntimeError("alignment exploded")

    monkeypatch.setattr(web, "JOBS_ROOT", tmp_path)
    monkeypatch.setattr(web, "generate", fail)
    web.JOBS.clear()
    client = TestClient(web.app)
    response = client.post(
        "/api/jobs",
        files={"audio": ("song.wav", b"audio", "audio/wav"), "lyrics": ("lyrics.txt", b"hello", "text/plain")},
    )
    status = client.get(response.json()["status_url"]).json()
    assert status["status"] == "failed"
    assert status["progress"] == 20
    assert status["error"] == "alignment exploded"


def test_background_job_reports_configuration_failure(tmp_path: Path, monkeypatch) -> None:
    def fail_to_load_config() -> dict:
        raise RuntimeError("config unavailable")

    monkeypatch.setattr(web, "load_config", fail_to_load_config)
    web.JOBS.clear()
    web.JOBS["job"] = {
        "id": "job",
        "status": "queued",
        "progress": 0,
        "stage": "Queued",
        "detail": "Waiting for a worker",
        "artifacts": {},
    }

    web._run_job("job", tmp_path / "song.wav", tmp_path / "lyrics.txt", "en", "original")

    status = web._job_snapshot("job")
    assert status["status"] == "failed"
    assert status["error"] == "config unavailable"
