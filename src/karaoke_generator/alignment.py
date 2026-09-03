from __future__ import annotations

import difflib
import importlib.util
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .lyrics import comparison_key, text_sha256
from .models import (
    AlignedLine,
    AlignedWord,
    AlignmentResult,
    LyricsDocument,
    TimedWord,
)


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
        segments, info = model.transcribe(
            str(audio),
            language=requested_language,
            word_timestamps=True,
            vad_filter=True,
            condition_on_previous_text=False,
            beam_size=5,
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
    name: str = "whisperx"

    def transcribe(
        self, audio: Path, lyrics: LyricsDocument, language: str, duration: float
    ) -> tuple[list[TimedWord], str]:
        if importlib.util.find_spec("whisperx") is None:
            raise RuntimeError("WhisperX is not installed; run `pip install -e '.[whisperx]'`")
        import whisperx

        device = "cpu" if self.device in {"auto", "mps"} else self.device
        audio_data = whisperx.load_audio(str(audio))
        model = whisperx.load_model(self.model_name, device, compute_type=self.compute_type)
        result = model.transcribe(
            audio_data,
            batch_size=4,
            language=None if language == "auto" else language,
        )
        detected = result.get("language") or language
        align_model, metadata = whisperx.load_align_model(language_code=detected, device=device)
        aligned = whisperx.align(
            result["segments"], align_model, metadata, audio_data, device, return_char_alignments=False
        )
        words: list[TimedWord] = []
        for word in aligned.get("word_segments", []):
            if word.get("start") is None or word.get("end") is None:
                continue
            words.append(
                TimedWord(
                    text=str(word.get("word", "")).strip(),
                    start=float(word["start"]),
                    end=float(word["end"]),
                    confidence=float(word.get("score", 0.0)),
                )
            )
        return words, detected


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


def create_backend(name: str, model: str, device: str, compute_type: str) -> TimingBackend:
    if name == "faster-whisper":
        return FasterWhisperBackend(model, device, compute_type)
    if name == "whisperx":
        return WhisperXBackend(model, device, compute_type)
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


def monotonic_word_mapping(
    lyrics_words: list[str], timed_words: list[TimedWord], min_similarity: float = 0.62
) -> list[int | None]:
    """Globally align two token sequences while preserving occurrence order."""
    n, m = len(lyrics_words), len(timed_words)
    gap_lyrics, gap_asr = -1.15, -0.75
    scores = [[0.0] * (m + 1) for _ in range(n + 1)]
    moves = [[""] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        scores[i][0] = scores[i - 1][0] + gap_lyrics
        moves[i][0] = "up"
    for j in range(1, m + 1):
        scores[0][j] = scores[0][j - 1] + gap_asr
        moves[0][j] = "left"

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            similarity = _similarity(lyrics_words[i - 1], timed_words[j - 1].text)
            match_score = 4.0 if similarity == 1.0 else (2.5 * similarity if similarity >= min_similarity else -2.0)
            choices = (
                (scores[i - 1][j - 1] + match_score, "diag"),
                (scores[i - 1][j] + gap_lyrics, "up"),
                (scores[i][j - 1] + gap_asr, "left"),
            )
            scores[i][j], moves[i][j] = max(choices, key=lambda item: item[0])

    mapping: list[int | None] = [None] * n
    i, j = n, m
    while i or j:
        move = moves[i][j]
        if move == "diag":
            if _similarity(lyrics_words[i - 1], timed_words[j - 1].text) >= min_similarity:
                mapping[i - 1] = j - 1
            i -= 1
            j -= 1
        elif move == "up":
            i -= 1
        else:
            j -= 1
    return mapping


def _interpolated_ranges(
    mapping: list[int | None], timed_words: list[TimedWord], duration: float
) -> list[tuple[float, float]]:
    if not any(index is not None for index in mapping):
        raise RuntimeError(
            "Lyrics alignment failed: none of the user words matched the audio transcript. "
            "Check the language, lyrics and vocal track."
        )
    ranges: list[tuple[float, float] | None] = [None] * len(mapping)
    for index, timed_index in enumerate(mapping):
        if timed_index is not None:
            timed = timed_words[timed_index]
            ranges[index] = (max(0.0, timed.start), min(duration, max(timed.start + 0.01, timed.end)))

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
            right = left + count * 0.01
        step = (right - left) / count
        for offset in range(count):
            start = left + offset * step
            ranges[last + 1 + offset] = (start, start + step)

    concrete = [value for value in ranges if value is not None]
    previous_end = 0.0
    output: list[tuple[float, float]] = []
    for start, end in concrete:
        start = max(previous_end, start)
        end = max(start + 0.01, end)
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
    mapping = monotonic_word_mapping(
        [word.normalized for word in lyric_words], timed_words, min_similarity=min_similarity
    )
    ranges = _interpolated_ranges(mapping, timed_words, duration)
    aligned_words: list[AlignedWord] = []
    for lyric_word, timed_index, (start, end) in zip(lyric_words, mapping, ranges):
        timed = timed_words[timed_index] if timed_index is not None else None
        aligned_words.append(
            AlignedWord(
                text=lyric_word.display,
                normalized=lyric_word.normalized,
                start=round(start, 3),
                end=round(end, 3),
                confidence=round(float(timed.confidence or 0.0), 4) if timed else 0.0,
                aligned=timed is not None,
                alignment_source=backend if timed else "interpolated",
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
    return AlignmentResult(
        language=language,
        duration=round(duration, 3),
        source_text_sha256=text_sha256(document),
        lines=result_lines,
        backend=backend,
    )

