from __future__ import annotations

import difflib
import importlib.util
import logging
import platform
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .lyrics import comparison_key, text_sha256
from .models import (
    AlignedLine,
    AlignedWord,
    AlignmentQuality,
    AlignmentResult,
    LyricsDocument,
    TimedWord,
)


LOGGER = logging.getLogger(__name__)


class TimingBackend(Protocol):
    name: str

    def transcribe(
        self, audio: Path, lyrics: LyricsDocument, language: str, duration: float
    ) -> tuple[list[TimedWord], str]: ...


@dataclass
class FasterWhisperBackend:
    model_name: str = "small"
    device: str = "auto"
    compute_type: str = "int8"
    use_lyrics_prompt: bool = True
    vad_filter: bool = True
    vad_threshold: float = 0.30
    vad_min_silence_duration_ms: int = 1000
    vad_speech_pad_ms: int = 600
    name: str = "faster-whisper"

    def transcribe(
        self, audio: Path, lyrics: LyricsDocument, language: str, duration: float
    ) -> tuple[list[TimedWord], str]:
        if importlib.util.find_spec("faster_whisper") is None:
            raise RuntimeError("faster-whisper is not installed; run `pip install -e '.[ml]'`")
        from faster_whisper import WhisperModel

        device = self.device
        if device == "auto":
            device = "cpu" if platform.system() == "Darwin" else "auto"
        model = WhisperModel(self.model_name, device=device, compute_type=self.compute_type)
        requested_language = None if language == "auto" else language
        initial_prompt = (
            " ".join(word.display for word in lyrics.words) if self.use_lyrics_prompt else None
        )
        vad_parameters = (
            {
                "threshold": self.vad_threshold,
                "min_silence_duration_ms": self.vad_min_silence_duration_ms,
                "speech_pad_ms": self.vad_speech_pad_ms,
            }
            if self.vad_filter
            else None
        )
        segments, info = model.transcribe(
            str(audio),
            language=requested_language,
            word_timestamps=True,
            vad_filter=self.vad_filter,
            vad_parameters=vad_parameters,
            condition_on_previous_text=False,
            beam_size=5,
            initial_prompt=initial_prompt,
        )
        words: list[TimedWord] = []
        for segment in segments:
            for word in segment.words or []:
                if word.start is None or word.end is None:
                    continue
                words.append(
                    TimedWord(
                        text=word.word.strip(),
                        start=float(word.start),
                        end=float(word.end),
                        confidence=float(word.probability) if word.probability is not None else None,
                    )
                )
        return words, info.language or language


@dataclass
class WhisperXBackend:
    model_name: str = "small"
    device: str = "cpu"
    compute_type: str = "int8"
    use_lyrics_prompt: bool = True
    vad_filter: bool = True
    vad_threshold: float = 0.30
    vad_min_silence_duration_ms: int = 1000
    vad_speech_pad_ms: int = 600
    align_models: dict[str, str] | None = None
    name: str = "whisperx"

    def transcribe(
        self, audio: Path, lyrics: LyricsDocument, language: str, duration: float
    ) -> tuple[list[TimedWord], str]:
        if importlib.util.find_spec("whisperx") is None:
            raise RuntimeError("WhisperX is not installed; run `pip install -e '.[whisperx]'`")
        if importlib.util.find_spec("faster_whisper") is None:
            raise RuntimeError("faster-whisper is not installed; run `pip install -e '.[ml]'`")
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r"\s*torchcodec is not installed correctly.*",
                category=UserWarning,
            )
            import whisperx
        from faster_whisper import WhisperModel

        if self.device == "auto":
            import torch

            device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            # WhisperX does not currently support its alignment model on MPS.
            device = "cpu" if self.device == "mps" else self.device
        requested_language = None if language == "auto" else language
        initial_prompt = (
            " ".join(word.display for word in lyrics.words) if self.use_lyrics_prompt else None
        )
        vad_parameters = (
            {
                "threshold": self.vad_threshold,
                "min_silence_duration_ms": self.vad_min_silence_duration_ms,
                "speech_pad_ms": self.vad_speech_pad_ms,
            }
            if self.vad_filter
            else None
        )
        model = WhisperModel(
            self.model_name,
            device=device,
            compute_type=self.compute_type,
        )
        raw_segments, info = model.transcribe(
            str(audio),
            language=requested_language,
            word_timestamps=True,
            vad_filter=self.vad_filter,
            vad_parameters=vad_parameters,
            condition_on_previous_text=False,
            beam_size=5,
            initial_prompt=initial_prompt,
        )
        segments = list(raw_segments)
        base_words = [
            TimedWord(
                text=word.word.strip(),
                start=float(word.start),
                end=float(word.end),
                confidence=float(word.probability) if word.probability is not None else None,
            )
            for segment in segments
            for word in (segment.words or [])
            if word.start is not None and word.end is not None and word.word.strip()
        ]
        transcript = [
            {"start": float(segment.start), "end": float(segment.end), "text": segment.text}
            for segment in segments
            if segment.text.strip()
        ]
        detected = info.language or language
        if not transcript:
            return base_words, detected
        audio_data = whisperx.load_audio(str(audio))
        align_model_name = (self.align_models or {}).get(detected)
        align_model, metadata = whisperx.load_align_model(
            language_code=detected,
            device=device,
            model_name=align_model_name,
        )
        aligned = whisperx.align(
            transcript,
            align_model,
            metadata,
            audio_data,
            device,
            interpolate_method="ignore",
            return_char_alignments=False,
        )
        refined_words: list[TimedWord] = []
        for word in aligned.get("word_segments", []):
            if word.get("start") is None or word.get("end") is None:
                continue
            refined_words.append(
                TimedWord(
                    text=str(word.get("word", "")).strip(),
                    start=float(word["start"]),
                    end=float(word["end"]),
                    confidence=float(word.get("score", 0.0)),
                )
            )
        return _merge_refined_words(base_words, refined_words), detected


def _merge_refined_words(
    base_words: list[TimedWord], refined_words: list[TimedWord]
) -> list[TimedWord]:
    """Use forced-alignment ranges where available without dropping ASR words."""
    if not refined_words:
        return base_words
    matches = monotonic_word_matches(
        [word.text for word in base_words],
        refined_words,
        min_similarity=0.80,
    )
    ranges = _matched_ranges([word.text for word in base_words], matches, refined_words)
    LOGGER.info(
        "WhisperX refined timestamps for %d/%d recognized words",
        sum(value is not None for value in ranges),
        len(base_words),
    )
    return [
        TimedWord(
            text=word.text,
            start=refined_range[0] if refined_range is not None else word.start,
            end=refined_range[1] if refined_range is not None else word.end,
            confidence=word.confidence,
        )
        for word, refined_range in zip(base_words, ranges)
    ]


@dataclass
class UniformBackend:
    name: str = "uniform"

    def transcribe(
        self, audio: Path, lyrics: LyricsDocument, language: str, duration: float
    ) -> tuple[list[TimedWord], str]:
        words = lyrics.words
        if not words:
            return [], language
        lead = min(0.5, duration * 0.05)
        usable = max(0.1, duration - 2 * lead)
        step = usable / len(words)
        return (
            [
                TimedWord(word.display, lead + index * step, lead + (index + 1) * step, 1.0)
                for index, word in enumerate(words)
            ],
            language if language != "auto" else "unknown",
        )


def create_backend(
    name: str,
    model: str,
    device: str,
    compute_type: str,
    *,
    use_lyrics_prompt: bool = True,
    vad_filter: bool = True,
    vad_threshold: float = 0.30,
    vad_min_silence_duration_ms: int = 1000,
    vad_speech_pad_ms: int = 600,
    align_models: dict[str, str] | None = None,
) -> TimingBackend:
    options = {
        "use_lyrics_prompt": use_lyrics_prompt,
        "vad_filter": vad_filter,
        "vad_threshold": vad_threshold,
        "vad_min_silence_duration_ms": vad_min_silence_duration_ms,
        "vad_speech_pad_ms": vad_speech_pad_ms,
    }
    if name == "faster-whisper":
        return FasterWhisperBackend(model, device, compute_type, **options)
    if name == "whisperx":
        return WhisperXBackend(model, device, compute_type, **options, align_models=align_models)
    if name == "uniform":
        return UniformBackend()
    raise ValueError(f"Unknown alignment backend: {name}")


def _similarity(left: str, right: str) -> float:
    left_key = comparison_key(left)
    right_key = comparison_key(right)
    if not left_key or not right_key:
        return 0.0
    if left_key == right_key:
        return 1.0
    return difflib.SequenceMatcher(a=left_key, b=right_key).ratio()


@dataclass(frozen=True)
class SequenceMatch:
    """One monotonic match between lyric and ASR token spans."""

    lyric_start: int
    lyric_end: int
    asr_start: int
    asr_end: int
    similarity: float


def monotonic_word_matches(
    lyrics_words: list[str],
    timed_words: list[TimedWord],
    min_similarity: float = 0.62,
    max_span: int = 2,
) -> list[SequenceMatch | None]:
    """Globally align lyrics to ASR, including common one-to-two token splits."""
    n, m = len(lyrics_words), len(timed_words)
    gap_lyrics, gap_asr = -1.15, -0.75
    negative_infinity = float("-inf")
    scores = [[negative_infinity] * (m + 1) for _ in range(n + 1)]
    moves: list[list[tuple[str, int, int, float] | None]] = [
        [None] * (m + 1) for _ in range(n + 1)
    ]
    scores[0][0] = 0.0

    def update(
        lyric_index: int,
        asr_index: int,
        score: float,
        move: tuple[str, int, int, float],
    ) -> None:
        if score > scores[lyric_index][asr_index]:
            scores[lyric_index][asr_index] = score
            moves[lyric_index][asr_index] = move

    for i in range(n + 1):
        for j in range(m + 1):
            current = scores[i][j]
            if current == negative_infinity:
                continue

            if i < n:
                update(i + 1, j, current + gap_lyrics, ("gap_lyrics", 1, 0, 0.0))
            if j < m:
                update(i, j + 1, current + gap_asr, ("gap_asr", 0, 1, 0.0))

            for lyric_count in range(1, min(max_span, n - i) + 1):
                lyric_span = " ".join(lyrics_words[i : i + lyric_count])
                for asr_count in range(1, min(max_span, m - j) + 1):
                    asr_span = " ".join(
                        word.text for word in timed_words[j : j + asr_count]
                    )
                    similarity = _similarity(lyric_span, asr_span)
                    if similarity < min_similarity:
                        continue
                    token_scale = max(lyric_count, asr_count)
                    base = 4.0 if similarity == 1.0 else 2.5 * similarity
                    span_penalty = 0.15 * (lyric_count + asr_count - 2)
                    update(
                        i + lyric_count,
                        j + asr_count,
                        current + base * token_scale - span_penalty,
                        ("match", lyric_count, asr_count, similarity),
                    )

    matches: list[SequenceMatch | None] = [None] * n
    i, j = n, m
    while i or j:
        move = moves[i][j]
        if move is None:
            raise RuntimeError("Lyrics alignment could not build a monotonic path")
        kind, lyric_count, asr_count, similarity = move
        if kind == "match":
            match = SequenceMatch(
                lyric_start=i - lyric_count,
                lyric_end=i,
                asr_start=j - asr_count,
                asr_end=j,
                similarity=similarity,
            )
            for lyric_index in range(match.lyric_start, match.lyric_end):
                matches[lyric_index] = match
        i -= lyric_count
        j -= asr_count
    return matches


def monotonic_word_mapping(
    lyrics_words: list[str], timed_words: list[TimedWord], min_similarity: float = 0.62
) -> list[int | None]:
    """Return the first ASR index for each globally matched lyric token."""
    return [
        match.asr_start if match is not None else None
        for match in monotonic_word_matches(
            lyrics_words, timed_words, min_similarity=min_similarity
        )
    ]


def _matched_ranges(
    lyrics_words: list[str],
    matches: list[SequenceMatch | None],
    timed_words: list[TimedWord],
) -> list[tuple[float, float] | None]:
    ranges: list[tuple[float, float] | None] = [None] * len(matches)
    handled: set[int] = set()
    for match in matches:
        if match is None or match.lyric_start in handled:
            continue
        handled.add(match.lyric_start)
        start = timed_words[match.asr_start].start
        end = timed_words[match.asr_end - 1].end
        lyric_count = match.lyric_end - match.lyric_start
        if lyric_count == 1:
            ranges[match.lyric_start] = (start, end)
            continue

        weights = [
            max(1, len(comparison_key(word)))
            for word in lyrics_words[match.lyric_start : match.lyric_end]
        ]
        total_weight = sum(weights)
        group_end = max(end, start + 0.01 * lyric_count)
        cursor = start
        for offset, weight in enumerate(weights):
            lyric_index = match.lyric_start + offset
            word_end = (
                group_end
                if offset == lyric_count - 1
                else cursor + (group_end - start) * weight / total_weight
            )
            ranges[lyric_index] = (cursor, word_end)
            cursor = word_end
    return ranges


def _interpolated_ranges(
    ranges: list[tuple[float, float] | None], duration: float
) -> list[tuple[float, float]]:
    if not any(value is not None for value in ranges):
        raise RuntimeError(
            "Lyrics alignment failed: none of the user words matched the audio transcript. "
            "Check the language, lyrics and vocal track."
        )
    minimum_duration = 0.01
    if duration < len(ranges) * minimum_duration:
        raise RuntimeError("Audio is too short to assign positive timing to every lyric word")

    known = [index for index, value in enumerate(ranges) if value is not None]
    first = known[0]
    if first:
        right = ranges[first][0]
        left = max(0.0, right - first * 0.35)
        step = max(0.01, (right - left) / first)
        for index in range(first):
            ranges[index] = (left + index * step, left + (index + 1) * step)

    for left_index, right_index in zip(known, known[1:]):
        missing = right_index - left_index - 1
        if not missing:
            continue
        left = ranges[left_index][1]
        right = max(left + 0.01 * missing, ranges[right_index][0])
        step = max(0.01, (right - left) / missing)
        for offset in range(missing):
            start = left + offset * step
            ranges[left_index + 1 + offset] = (start, min(right, start + step))

    last = known[-1]
    if last < len(ranges) - 1:
        count = len(ranges) - last - 1
        left = ranges[last][1]
        right = min(duration, left + count * 0.35)
        if right <= left:
            right = left + count * minimum_duration
        step = (right - left) / count
        for offset in range(count):
            start = left + offset * step
            ranges[last + 1 + offset] = (start, start + step)

    concrete = [value for value in ranges if value is not None]
    previous_end = 0.0
    output: list[tuple[float, float]] = []
    for index, (start, end) in enumerate(concrete):
        remaining = len(concrete) - index - 1
        latest_end = duration - remaining * minimum_duration
        latest_start = latest_end - minimum_duration
        start = min(max(previous_end, 0.0, start), latest_start)
        end = min(max(start + minimum_duration, end), latest_end)
        output.append((start, end))
        previous_end = end
    return output


def align_lyrics(
    document: LyricsDocument,
    timed_words: list[TimedWord],
    duration: float,
    language: str,
    backend: str,
    min_similarity: float = 0.62,
) -> AlignmentResult:
    lyric_words = list(document.words)
    matches = monotonic_word_matches(
        [word.normalized for word in lyric_words], timed_words, min_similarity=min_similarity
    )
    direct_ranges = _matched_ranges(
        [word.normalized for word in lyric_words], matches, timed_words
    )
    ranges = _interpolated_ranges(direct_ranges, duration)
    aligned_words: list[AlignedWord] = []
    for lyric_word, match, (start, end) in zip(lyric_words, matches, ranges):
        matched_asr_words = (
            timed_words[match.asr_start : match.asr_end] if match is not None else []
        )
        confidences = [
            float(word.confidence)
            for word in matched_asr_words
            if word.confidence is not None
        ]
        asr_text = " ".join(word.text for word in matched_asr_words) or None
        aligned_words.append(
            AlignedWord(
                text=lyric_word.display,
                normalized=lyric_word.normalized,
                start=round(start, 3),
                end=round(end, 3),
                confidence=(
                    round(sum(confidences) / len(confidences), 4) if confidences else 0.0
                ),
                aligned=match is not None,
                alignment_source=backend if match is not None else "interpolated",
                asr_text=asr_text,
                match_similarity=round(match.similarity, 4) if match is not None else None,
            )
        )

    result_lines: list[AlignedLine] = []
    cursor = 0
    for source_line in document.lines:
        count = len(source_line.words)
        words = aligned_words[cursor : cursor + count]
        cursor += count
        result_lines.append(
            AlignedLine(
                text=source_line.text,
                start=words[0].start,
                end=words[-1].end,
                words=words,
                block_index=source_line.block_index,
            )
        )
    directly_aligned = sum(word.aligned for word in aligned_words)
    total_words = len(aligned_words)
    return AlignmentResult(
        language=language,
        duration=round(duration, 3),
        source_text_sha256=text_sha256(document),
        lines=result_lines,
        backend=backend,
        quality=AlignmentQuality(
            total_words=total_words,
            recognized_words=len(timed_words),
            directly_aligned=directly_aligned,
            interpolated=total_words - directly_aligned,
            aligned_ratio=round(directly_aligned / total_words, 4) if total_words else 0.0,
        ),
    )
