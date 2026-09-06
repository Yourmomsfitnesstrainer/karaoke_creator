from __future__ import annotations

import hashlib
import json
import logging
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from .alignment import MAPPING_ALGORITHM_VERSION, align_lyrics
from .audio import prepare_audio, probe_duration, timeline_report
from .lyrics import cleanup_report, parse_lyrics_file, text_sha256
from .models import AlignmentResult
from .renderer import render_video
from .separation import DemucsSeparator, original_audio_fallback
from .subtitles import generate_ass
from .timing_cache import cached_timing, fingerprint, package_versions


LOGGER = logging.getLogger("karaoke_generator")
ProgressCallback = Callable[[int, str], None]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)


@contextmanager
def _stage(
    number: int,
    total: int,
    label: str,
    progress_callback: ProgressCallback | None = None,
    durations: dict | None = None,
) -> Iterator[None]:
    start = time.monotonic()
    if progress_callback:
        progress_callback(round((number - 1) * 100 / total), label)
    LOGGER.info("[%d/%d] %s...", number, total, label)
    try:
        yield
    except Exception:
        LOGGER.exception("[%d/%d] %s failed after %.1fs", number, total, label, time.monotonic() - start)
        raise
    finally:
        if durations is not None:
            durations[label] = round(time.monotonic() - start, 6)
    LOGGER.info("[%d/%d] %s done in %.1fs", number, total, label, time.monotonic() - start)
    if progress_callback:
        progress_callback(round(number * 100 / total), label)


def generate(
    audio: Path,
    lyrics: Path,
    output_dir: Path,
    config: dict,
    *,
    background: str | None = None,
    progress_callback: ProgressCallback | None = None,
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
    cleanup_path = output_dir / "lyrics_cleanup.json"
    _write_json(cleanup_path, cleanup_report(document))
    if document.removed_lines:
        LOGGER.info("Lyrics cleanup removed %d metadata lines", len(document.removed_lines))
    metadata_path = work_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
    original_hash = _file_sha256(audio)
    source_key = fingerprint({"original_sha256": original_hash, "preparation_algorithm": "2"})

    durations: dict = {}
    source = work_dir / "source.wav"
    with _stage(1, 5, "Preparing audio", progress_callback, durations):
        if metadata.get("source_key") != source_key or not source.exists():
            prepare_audio(audio, source)
        else:
            LOGGER.info("Audio cache hit")
        duration = probe_duration(source)

    separation_config = config["separation"]
    separation_available = DemucsSeparator.available()
    separation_key = fingerprint({"source_sha256": _file_sha256(source), "config": separation_config,
                                  "demucs": package_versions()["packages"]["demucs"],
                                  "available": separation_available})
    vocals = output_dir / "vocals.wav"
    instrumental = output_dir / "instrumental.wav"
    with _stage(2, 5, "Separating vocals", progress_callback, durations):
        enabled = separation_config.get("enabled", "auto")
        separator = DemucsSeparator(str(separation_config.get("model", "htdemucs")))
        can_reuse = metadata.get("separation_key") == separation_key and vocals.exists()
        if can_reuse:
            separation_backend = metadata.get("separation_backend", "cached")
            if separation_backend == "original-audio" or not instrumental.exists():
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

        audio_timeline = timeline_report(audio, source, vocals, instrumental)

    alignment_config = config["alignment"]
    alignment_path = output_dir / "alignment.json"
    with _stage(3, 5, "Aligning exact lyrics", progress_callback, durations):
        timed_words, detected_language, timing_details = cached_timing(
            vocals, document, str(alignment_config.get("language", "auto")), duration,
            alignment_config, work_dir)
        alignment_key = fingerprint({"timing_key": timing_details["timing_key"],
            "mapping_algorithm": MAPPING_ALGORITHM_VERSION, "text_sha256": text_sha256(document),
            "min_similarity": alignment_config.get("min_similarity", .62)})
        if metadata.get("alignment_key") == alignment_key and alignment_path.exists():
            alignment = AlignmentResult.from_dict(json.loads(alignment_path.read_text(encoding="utf-8")))
            LOGGER.info("Text mapping cache hit; canonical edits retained")
        else:
            alignment = align_lyrics(document, timed_words, duration, detected_language,
                str(alignment_config.get("backend", "whisperx")),
                float(alignment_config.get("min_similarity", .62)))
        alignment.diagnostics.update(timing_details)
        alignment.diagnostics["audio_timeline"] = audio_timeline
        alignment.diagnostics["timing_sources"] = _timing_counts(alignment)
        _write_json(alignment_path, alignment.to_dict())
        LOGGER.info("ASR cache: %s; refinement cache: %s", timing_details["asr_cache_hit"],
                    timing_details["refinement_cache_hit"])
        LOGGER.info("Timing sources: %s", alignment.diagnostics["timing_sources"])
        LOGGER.info("ASR model: %s; refinement model: %s",
                    timing_details["asr"]["model"], timing_details.get("refinement", {}).get("model", "none"))
        quality = alignment.quality
        LOGGER.info(
            "ASR recognized %s words; matched %d/%d lyrics words (%.1f%%); interpolated %d",
            quality.recognized_words,
            quality.directly_aligned,
            quality.total_words,
            quality.aligned_ratio * 100,
            quality.interpolated,
        )
        if quality.interpolated:
            LOGGER.warning(
                "%d lyrics words use interpolated timings; inspect %s",
                quality.interpolated,
                alignment_path,
            )

    ass_path = output_dir / "karaoke.ass"
    with _stage(4, 5, "Generating karaoke subtitles", progress_callback, durations):
        karaoke_settings = dict(config["karaoke"])
        karaoke_settings.update(
            width=config["video"]["width"], height=config["video"]["height"]
        )
        LOGGER.info("Effective highlight offset: %s ms", karaoke_settings.get("timing_offset_ms", 0))
        generate_ass(alignment, ass_path, karaoke_settings)

    requested_audio_mode = str(config["output"].get("audio_mode", "instrumental"))
    render_audio = instrumental if requested_audio_mode == "instrumental" and instrumental else source
    actual_audio_mode = "instrumental" if render_audio == instrumental else "original"
    if requested_audio_mode == "instrumental" and actual_audio_mode == "original":
        LOGGER.warning("Instrumental was requested but is unavailable; rendering original audio")
    video_path = output_dir / "karaoke.mp4"
    with _stage(5, 5, "Rendering MP4", progress_callback, durations):
        alignment.diagnostics["render_runtime"] = render_video(ass_path, render_audio, video_path, config["video"], config["output"], background)

        audio_timeline["output"] = timeline_report(render_audio, render_audio, render_audio, None,
                                                video_path=video_path)
    alignment.diagnostics.update(effective_settings=config, stage_seconds=durations,
        original_sha256=original_hash, lyrics_file_sha256=_file_sha256(lyrics),
        effective_timing_offset_ms=config["karaoke"].get("timing_offset_ms", 0))
    _write_json(alignment_path, alignment.to_dict())
    metadata = {
        "diagnostics": alignment.diagnostics,
        "source_key": source_key,
        "separation_key": separation_key,
        "separation_backend": separation_backend,
        "alignment_key": alignment_key,
        "alignment_backend": alignment.backend,
        "alignment_quality": alignment.to_dict()["quality"],
        "requested_audio_mode": requested_audio_mode,
        "actual_audio_mode": actual_audio_mode,
    }
    _write_json(metadata_path, metadata)
    return {
        "video": video_path,
        "subtitles": ass_path,
        "alignment": alignment_path,
        "processed_lyrics": processed_lyrics,
        "lyrics_cleanup": cleanup_path,
        "vocals": vocals,
        **({"instrumental": instrumental} if instrumental else {}),
    }


def _timing_counts(alignment: AlignmentResult) -> dict:
    counts: dict[str, int] = {}
    for line in alignment.lines:
        for word in line.words:
            source = (word.timing or {}).get("source", "unknown")
            counts[source] = counts.get(source, 0) + 1
            if (word.timing or {}).get("corrections"):
                counts["corrected"] = counts.get("corrected", 0) + 1
    return counts


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
    LOGGER.info("Effective highlight offset: %s ms", settings.get("timing_offset_ms", 0))
    generate_ass(alignment, ass_path, settings)
    render_runtime = render_video(ass_path, audio_path, output_path, config["video"], config["output"], background)
    diagnostics_path = output_path.with_suffix(".render.json")
    _write_json(diagnostics_path, {"effective_timing_offset_ms": settings.get("timing_offset_ms", 0),
        "settings": config, "alignment_sha256": _file_sha256(alignment_path),
        "audio_timeline": timeline_report(audio_path, audio_path, audio_path, None, video_path=output_path),
        "versions": package_versions(), "render_runtime": render_runtime})
    return {"video": output_path, "subtitles": ass_path, "diagnostics": diagnostics_path}
