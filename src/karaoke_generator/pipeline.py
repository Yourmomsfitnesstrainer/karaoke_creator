from __future__ import annotations

import hashlib
import json
import logging
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .alignment import align_lyrics, create_backend
from .audio import prepare_audio, probe_duration
from .lyrics import parse_lyrics_file, text_sha256
from .models import AlignmentResult
from .renderer import render_video
from .separation import DemucsSeparator, original_audio_fallback
from .subtitles import generate_ass


LOGGER = logging.getLogger("karaoke_generator")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


@contextmanager
def _stage(number: int, total: int, label: str) -> Iterator[None]:
    start = time.monotonic()
    LOGGER.info("[%d/%d] %s...", number, total, label)
    try:
        yield
    except Exception:
        LOGGER.exception("[%d/%d] %s failed after %.1fs", number, total, label, time.monotonic() - start)
        raise
    LOGGER.info("[%d/%d] %s done in %.1fs", number, total, label, time.monotonic() - start)


def generate(
    audio: Path,
    lyrics: Path,
    output_dir: Path,
    config: dict,
    *,
    background: str | None = None,
) -> dict[str, Path]:
    audio = audio.expanduser().resolve()
    lyrics = lyrics.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    if not audio.is_file():
        raise FileNotFoundError(f"Audio file not found: {audio}")
    if not lyrics.is_file():
        raise FileNotFoundError(f"Lyrics file not found: {lyrics}")
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = output_dir / "work"
    work_dir.mkdir(exist_ok=True)

    document = parse_lyrics_file(lyrics)
    processed_lyrics = output_dir / "processed_lyrics.txt"
    processed_lyrics.write_text(document.processed_text, encoding="utf-8")
    metadata_path = work_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
    source_key = _file_sha256(audio)

    source = work_dir / "source.wav"
    with _stage(1, 5, "Preparing audio"):
        if metadata.get("source_key") != source_key or not source.exists():
            prepare_audio(audio, source)
        else:
            LOGGER.info("Audio cache hit")
        duration = probe_duration(source)

    separation_config = config["separation"]
    separation_available = DemucsSeparator.available()
    separation_key = f"{source_key}:{separation_config}:available={separation_available}"
    vocals = output_dir / "vocals.wav"
    instrumental = output_dir / "instrumental.wav"
    with _stage(2, 5, "Separating vocals"):
        enabled = separation_config.get("enabled", "auto")
        separator = DemucsSeparator(str(separation_config.get("model", "htdemucs")))
        can_reuse = metadata.get("separation_key") == separation_key and vocals.exists()
        if can_reuse:
            separation_backend = metadata.get("separation_backend", "cached")
            if not instrumental.exists():
                instrumental = None
            LOGGER.info("Separation cache hit")
        elif enabled is False or (enabled == "auto" and not separator.available()):
            if enabled == "auto":
                LOGGER.warning("Demucs is unavailable; aligning original audio and falling back to original output")
            result = original_audio_fallback(source, output_dir)
            vocals, instrumental, separation_backend = result.vocals, result.instrumental, result.backend
        else:
            try:
                result = separator.separate(source, output_dir)
            except Exception as exc:
                if enabled is True:
                    raise
                LOGGER.warning("Separation failed (%s); using original audio", exc)
                result = original_audio_fallback(source, output_dir)
            vocals, instrumental, separation_backend = result.vocals, result.instrumental, result.backend

    alignment_config = config["alignment"]
    alignment_path = output_dir / "alignment.json"
    alignment_key = hashlib.sha256(
        f"{source_key}:{text_sha256(document)}:{alignment_config}:{separation_backend}".encode()
    ).hexdigest()
    with _stage(3, 5, "Aligning exact lyrics"):
        if metadata.get("alignment_key") == alignment_key and alignment_path.exists():
            alignment = AlignmentResult.from_dict(json.loads(alignment_path.read_text(encoding="utf-8")))
            LOGGER.info("Alignment cache hit")
        else:
            backend = create_backend(
                str(alignment_config.get("backend", "faster-whisper")),
                str(alignment_config.get("model", "small")),
                str(alignment_config.get("device", "auto")),
                str(alignment_config.get("compute_type", "int8")),
            )
            timed_words, detected_language = backend.transcribe(
                vocals,
                document,
                str(alignment_config.get("language", "auto")),
                duration,
            )
            alignment = align_lyrics(
                document,
                timed_words,
                duration,
                detected_language,
                backend.name,
                float(alignment_config.get("min_similarity", 0.62)),
            )
            _write_json(alignment_path, alignment.to_dict())

    ass_path = output_dir / "karaoke.ass"
    with _stage(4, 5, "Generating karaoke subtitles"):
        karaoke_settings = dict(config["karaoke"])
        karaoke_settings.update(
            width=config["video"]["width"], height=config["video"]["height"]
        )
        generate_ass(alignment, ass_path, karaoke_settings)

    requested_audio_mode = str(config["output"].get("audio_mode", "instrumental"))
    render_audio = instrumental if requested_audio_mode == "instrumental" and instrumental else source
    actual_audio_mode = "instrumental" if render_audio == instrumental else "original"
    if requested_audio_mode == "instrumental" and actual_audio_mode == "original":
        LOGGER.warning("Instrumental was requested but is unavailable; rendering original audio")
    video_path = output_dir / "karaoke.mp4"
    with _stage(5, 5, "Rendering MP4"):
        render_video(ass_path, render_audio, video_path, config["video"], config["output"], background)

    metadata = {
        "source_key": source_key,
        "separation_key": separation_key,
        "separation_backend": separation_backend,
        "alignment_key": alignment_key,
        "alignment_backend": alignment.backend,
        "requested_audio_mode": requested_audio_mode,
        "actual_audio_mode": actual_audio_mode,
    }
    _write_json(metadata_path, metadata)
    return {
        "video": video_path,
        "subtitles": ass_path,
        "alignment": alignment_path,
        "processed_lyrics": processed_lyrics,
        "vocals": vocals,
        **({"instrumental": instrumental} if instrumental else {}),
    }


def rerender(
    alignment_path: Path,
    audio_path: Path,
    output_path: Path,
    config: dict,
    *,
    background: str | None = None,
) -> dict[str, Path]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    alignment = AlignmentResult.from_dict(json.loads(alignment_path.read_text(encoding="utf-8")))
    ass_path = output_path.with_suffix(".ass")
    settings = dict(config["karaoke"])
    settings.update(width=config["video"]["width"], height=config["video"]["height"])
    generate_ass(alignment, ass_path, settings)
    render_video(ass_path, audio_path, output_path, config["video"], config["output"], background)
    return {"video": output_path, "subtitles": ass_path}
