from __future__ import annotations

import difflib
import importlib.util
import logging
import math
import platform
import warnings
from dataclasses import dataclass, replace
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
        elif device == "mps":
            # CTranslate2 and WhisperX alignment use CPU on Apple Silicon.
            device = "cpu"
        model = WhisperModel(self.model_name, device=device, compute_type=self.compute_type)
        self.runtime = {"device": getattr(getattr(model, "model", None), "device", device),
                        "compute_type": getattr(getattr(model, "model", None), "compute_type", self.compute_type)}
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
        for segment_id, segment in enumerate(segments):
            for word in segment.words or []:
                if word.start is None or word.end is None:
                    continue
                words.append(
                    TimedWord(
                        text=word.word.strip(),
                        start=float(word.start),
                        end=float(word.end),
                        confidence=float(word.probability) if word.probability is not None else None,
                        segment_id=segment_id,
                    )
                )
        return words, info.language or language


ASR_ALGORITHM_VERSION = "2"
REFINEMENT_ALGORITHM_VERSION = "5"
MAPPING_ALGORITHM_VERSION = "4"


def _valid_interval(start: float, end: float, duration: float) -> bool:
    return math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= duration


def _number(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


def _asr_evidence(word: TimedWord) -> dict:
    return {"source": "asr", "original": {"start": _number(word.start), "end": _number(word.end)},
            "asr_confidence": _number(word.confidence)}


def _prepare_alignment_window(audio, lower, upper, feature_extractor):
    """Apply the model's input transform only to its window on the absolute axis."""
    if feature_extractor is None:
        return audio
    first, last = int(lower * 16000), int(upper * 16000)
    window = audio[first:last]
    if not len(window):
        return audio
    prepared = audio.copy()
    prepared[first:last] = feature_extractor(
        window, sampling_rate=16000, return_attention_mask=False
    )["input_values"][0]
    return prepared


@dataclass
class WhisperXBackend(FasterWhisperBackend):
    align_models: dict[str, str] | None = None
    context_seconds: float = 0.3
    max_window_seconds: float = 20.0
    score_thresholds: dict[str, float] | None = None
    name: str = "whisperx"

    def recognize(self, audio: Path, lyrics: LyricsDocument, language: str,
                  duration: float) -> tuple[list[TimedWord], str]:
        return super().transcribe(audio, lyrics, language, duration)

    def transcribe(self, audio: Path, lyrics: LyricsDocument, language: str,
                   duration: float) -> tuple[list[TimedWord], str]:
        base, detected = self.recognize(audio, lyrics, language, duration)
        return self.refine(audio, base, detected, duration), detected

    def refine(self, audio: Path, base: list[TimedWord], language: str,
               duration: float) -> list[TimedWord]:
        if importlib.util.find_spec("whisperx") is None:
            raise RuntimeError("WhisperX is not installed; run `pip install -e '.[whisperx]'`")
        if not math.isfinite(self.context_seconds) or self.context_seconds < 0:
            raise ValueError("context_seconds must be finite and nonnegative")
        if not math.isfinite(self.max_window_seconds) or self.max_window_seconds <= 2*self.context_seconds:
            raise ValueError("max_window_seconds must exceed twice the context")
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=r"\s*torchcodec is not installed correctly.*")
            import whisperx
        device = self.device
        if device == "auto":
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "mps":
            device = "cpu"
        if not base:
            return []
        requested_model = (self.align_models or {}).get(language)
        model, metadata = whisperx.load_align_model(language_code=language, device=device,
                                                    model_name=requested_model)
        if requested_model is None:
            from whisperx.alignment import DEFAULT_ALIGN_MODELS_HF, DEFAULT_ALIGN_MODELS_TORCH
            requested_model = DEFAULT_ALIGN_MODELS_TORCH.get(language) or DEFAULT_ALIGN_MODELS_HF.get(language)
        feature_extractor = None
        input_processing = {"source": metadata.get("type", "unknown")}
        if metadata.get("type") == "huggingface":
            from transformers import AutoFeatureExtractor

            feature_extractor = AutoFeatureExtractor.from_pretrained(requested_model)
            input_processing.update(
                feature_extractor=type(feature_extractor).__name__,
                do_normalize=feature_extractor.do_normalize,
                sampling_rate=feature_extractor.sampling_rate,
            )
        thresholds = self.score_thresholds or {}
        # This is only a provisional sanity floor, not a calibrated accuracy claim.
        threshold = float(thresholds.get(requested_model, 0.05))
        if not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise ValueError("Refinement score threshold must be in [0, 1]")
        audio_data = whisperx.load_audio(str(audio))
        duration = min(duration, len(audio_data) / 16000)
        groups: list[list[int]] = []
        for index, word in enumerate(base):
            if not _valid_interval(word.start, word.end, duration):
                continue
            if (not groups or base[groups[-1][-1]].segment_id != word.segment_id
                    or word.end-base[groups[-1][0]].start > self.max_window_seconds-2*self.context_seconds):
                groups.append([])
            groups[-1].append(index)
        output = [replace(word, timing={**_asr_evidence(word), "reason": "invalid_asr_interval"}) for word in base]
        for indices in groups:
            first, last = indices[0], indices[-1]
            words = [base[index] for index in indices]
            lower = max(0., words[0].start-self.context_seconds)
            upper = min(duration, words[-1].end+self.context_seconds)
            if first and _valid_interval(base[first-1].start, base[first-1].end, duration):
                lower = max(lower, (base[first-1].start+base[first-1].end)/2)
            if last+1 < len(base) and _valid_interval(base[last+1].start, base[last+1].end, duration):
                upper = min(upper, (base[last+1].start+base[last+1].end)/2)
            # WhisperX receives absolute windows on the complete decoded waveform.
            # No second origin addition is needed for returned times.
            window = {"start": lower, "end": upper, "origin": 0.,
                      "first_asr_index": first, "last_asr_index": last}
            if upper <= lower or upper-lower > self.max_window_seconds:
                merged = [replace(word, timing={**_asr_evidence(word), "reason": "invalid_window"}) for word in words]
            else:
                aligned = whisperx.align(
                    [{"start": lower, "end": upper, "text": " ".join(w.text for w in words)}],
                    model, metadata, _prepare_alignment_window(audio_data, lower, upper, feature_extractor),
                    device, interpolate_method="ignore",
                    return_char_alignments=True,
                )
                candidates = _whisperx_candidates(aligned)
                merged = _merge_refined_words(words, candidates, duration=duration,
                                              window=(lower, upper), min_score=threshold)
            for index, word in zip(indices, merged):
                evidence = dict(word.timing or _asr_evidence(word))
                evidence.update(model=requested_model, device=device, window=window, input_processing=input_processing,
                                score_threshold=threshold,
                                score_policy="configured" if requested_model in thresholds else "uncalibrated_sanity_floor")
                output[index] = replace(word, timing=evidence)
        return output


def _whisperx_candidates(aligned: dict) -> list[TimedWord]:
    candidates = []
    for segment in aligned.get("segments", []):
        chars = segment.get("chars") or []
        cursor = 0
        for raw in segment.get("words", []):
            text = str(raw.get("word", "")).strip()
            while cursor < len(chars) and not str(chars[cursor].get("char", "")).strip():
                cursor += 1
            word_chars = chars[cursor:cursor+len(text)]
            cursor += len(text)
            candidates.append(TimedWord(text, float(raw.get("start") if raw.get("start") is not None else 'nan'),
                float(raw.get("end") if raw.get("end") is not None else 'nan'),
                float(raw["score"]) if raw.get("score") is not None else None,
                characters=word_chars))
    return candidates


def _merge_refined_words(base_words: list[TimedWord], refined_words: list[TimedWord], *,
                         duration: float = math.inf, window: tuple[float, float] | None = None,
                         min_score: float = 0.05) -> list[TimedWord]:
    """Merge within one ASR window; retain rejected evidence and ASR confidence."""
    matches = monotonic_word_matches([word.text for word in base_words], refined_words, min_similarity=.80)
    ranges = _matched_ranges([word.text for word in base_words], matches, refined_words)
    output = []
    for index, (word, match, candidate) in enumerate(zip(base_words, matches, ranges)):
        evidence = _asr_evidence(word)
        reason = "no_refinement_match"
        chars = []
        if candidate is not None and match is not None:
            start, end = candidate
            matched = refined_words[match.asr_start:match.asr_end]
            scores = [_number(item.confidence) for item in matched]
            score = min(scores) if scores and all(item is not None for item in scores) else None
            evidence["candidate"] = {"start": _number(start), "end": _number(end), "score": score}
            # WhisperX rounds output to milliseconds. A rounded endpoint can be
            # up to half a millisecond outside the exact decoded audio/window.
            # Clamp only that quantization error; retain the raw candidate above.
            lower = max(0., window[0]) if window else 0.
            upper = min(duration, window[1]) if window else duration
            raw_start, raw_end = start, end
            clamped = False
            if lower - .000500001 <= start < lower:
                start = lower
                clamped = True
            if upper < end <= upper + .000500001:
                end = upper
                clamped = True
            if clamped:
                evidence["rounding_clamp"] = {
                    "start": _number(start), "end": _number(end),
                    "delta_start_ms": (start-raw_start)*1000,
                    "delta_end_ms": (end-raw_end)*1000,
                    "reason": "millisecond_endpoint_quantization",
                }
            reason = "accepted"
            if not _valid_interval(start, end, duration):
                reason = "invalid_candidate_interval"
            elif window and (start < window[0] or end > window[1]):
                reason = "outside_window"
            elif score is None:
                reason = "missing_refinement_score"
            elif not 0 <= score <= 1 or score < min_score:
                reason = "low_refinement_score"
            elif index and start < (base_words[index-1].start+base_words[index-1].end)/2:
                reason = "crosses_previous_anchor"
            elif index+1 < len(base_words) and end > (base_words[index+1].start+base_words[index+1].end)/2:
                reason = "crosses_next_anchor"
            elif output and output[-1].timing.get("source") == "refined" and start < output[-1].end:
                reason = "overlaps_previous_candidate"
            if reason == "accepted":
                source = _range_source([item.text for item in base_words], match, refined_words)
                evidence["source"] = "approximate_split" if source == "approximate_split" else "refined"
                chars = [char for item in matched for char in item.characters
                         if char.get("start") is not None and char.get("end") is not None
                         and start <= char["start"] < char["end"] <= end]
                word = replace(word, start=start, end=end)
        evidence["reason"] = reason
        output.append(replace(word, timing=evidence, characters=chars))
    return output


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
    context_seconds: float = 0.3,
    max_window_seconds: float = 20.0,
    score_thresholds: dict[str, float] | None = None,
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
        return WhisperXBackend(model, device, compute_type, **options, align_models=align_models,
                               context_seconds=context_seconds, max_window_seconds=max_window_seconds,
                               score_thresholds=score_thresholds)
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
    lyric_block_ids: list[int] | None = None,
) -> list[SequenceMatch | None]:
    """Globally align lyrics to ASR, including common one-to-two token splits."""
    n, m = len(lyrics_words), len(timed_words)
    if lyric_block_ids is not None and len(lyric_block_ids) != n:
        raise ValueError("Each lyric word must have one block identifier")
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
                # A compound token cannot consume the end of one stanza and
                # the start of another, even when their combined text is close.
                if (lyric_block_ids is not None
                        and lyric_block_ids[i] != lyric_block_ids[i+lyric_count-1]):
                    continue
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


def _character_ranges(lyrics_words, match, timed_words):
    targets = lyrics_words[match.lyric_start:match.lyric_end]
    chars = [char for word in timed_words[match.asr_start:match.asr_end]
             for char in word.characters if comparison_key(str(char.get("char", "")))]
    if "".join(comparison_key(str(char["char"])) for char in chars) != "".join(comparison_key(t) for t in targets):
        return None
    if not chars or any(not _valid_interval(float(c.get("start") if c.get("start") is not None else math.nan),
                                           float(c.get("end") if c.get("end") is not None else math.nan), math.inf) for c in chars):
        return None
    if any(a["end"] > b["start"] for a, b in zip(chars, chars[1:])):
        return None
    output, cursor = [], 0
    for target in targets:
        count = len(comparison_key(target))
        if not count or cursor+count > len(chars):
            return None
        output.append((chars[cursor]["start"], chars[cursor+count-1]["end"]))
        cursor += count
    return output


def _range_source(lyrics_words, match, timed_words):
    if match.lyric_end-match.lyric_start == 1:
        return "word_boundaries"
    targets = lyrics_words[match.lyric_start:match.lyric_end]
    words = timed_words[match.asr_start:match.asr_end]
    if len(targets) == len(words) and all(comparison_key(t) == comparison_key(w.text) for t, w in zip(targets, words)):
        return "word_boundaries"
    return "character_boundaries" if _character_ranges(lyrics_words, match, timed_words) else "approximate_split"


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

        source = _range_source(lyrics_words, match, timed_words)
        if source != "approximate_split":
            values = (_character_ranges(lyrics_words, match, timed_words)
                      if source == "character_boundaries" else
                      [(w.start, w.end) for w in timed_words[match.asr_start:match.asr_end]])
            ranges[match.lyric_start:match.lyric_end] = values
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
    ranges: list[tuple[float, float] | None], duration: float,
    corrections: list[list[dict]] | None = None,
) -> list[tuple[float, float]]:
    ranges = list(ranges)
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
        original = (start, end)
        previous_anchor = previous_end
        remaining = len(concrete) - index - 1
        latest_end = duration - remaining * minimum_duration
        latest_start = latest_end - minimum_duration
        start = min(max(previous_end, 0.0, start), latest_start)
        end = min(max(start + minimum_duration, end), latest_end)
        output.append((start, end))
        if corrections is not None:
            reasons = []
            if original[0] < previous_anchor:
                reasons.append("overlap_previous_word")
            if original[0] < 0 or original[1] > latest_end or original[0] > latest_start:
                reasons.append("audio_bounds_and_remaining_words")
            if original[1] - original[0] < minimum_duration:
                reasons.append("minimum_duration")
            corrections.append([{"original": {"start": original[0], "end": original[1]},
                                 "delta_start": round(start-original[0], 6),
                                 "delta_end": round(end-original[1], 6),
                                 "reasons": reasons or ["monotonic_normalization"],
                                 "previous_word_index": index-1 if index else None}]
                               if original != (start, end) else [])
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
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Audio duration must be finite and positive")
    invalid_asr = [{"text": w.text, "start": _number(w.start), "end": _number(w.end),
                    "status": "invalid_asr_interval"} for w in timed_words
                   if not _valid_interval(w.start, w.end, duration)]
    timed_words = [w for w in timed_words if _valid_interval(w.start, w.end, duration)]
    lyric_words = list(document.words)
    matches = monotonic_word_matches(
        [word.normalized for word in lyric_words], timed_words, min_similarity=min_similarity,
        lyric_block_ids=[line.block_index for line in document.lines for _ in line.words],
    )
    direct_ranges = _matched_ranges(
        [word.normalized for word in lyric_words], matches, timed_words
    )
    corrections: list[list[dict]] = []
    ranges = _interpolated_ranges(direct_ranges, duration, corrections)
    aligned_words: list[AlignedWord] = []
    for index, (lyric_word, match, (start, end)) in enumerate(zip(lyric_words, matches, ranges)):
        matched_asr_words = (
            timed_words[match.asr_start : match.asr_end] if match is not None else []
        )
        confidences = [
            float(word.confidence)
            for word in matched_asr_words
            if _number(word.confidence) is not None
        ]
        asr_text = " ".join(word.text for word in matched_asr_words) or None
        method = _range_source([w.normalized for w in lyric_words], match, timed_words) if match else "interpolated"
        inputs = [{"text": w.text, **(w.timing or _asr_evidence(w))} for w in matched_asr_words]
        sources = {item["source"] for item in inputs}
        source = ("interpolated" if match is None else "approximate_split"
                  if method == "approximate_split" or "approximate_split" in sources else
                  "refined" if sources == {"refined"} else "asr" if sources == {"asr"} else "mixed")
        if backend == "uniform" and match is not None:
            source = "uniform"
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
                timing={"source": source, "mapping": method, "inputs": inputs,
                        "reason": "no_text_match" if match is None else None,
                        "corrections": corrections[index],
                        "generated": {"start": round(start, 3), "end": round(end, 3)}},
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
        diagnostics={"mapping_algorithm": MAPPING_ALGORITHM_VERSION, "invalid_asr": invalid_asr},
        quality=AlignmentQuality(
            total_words=total_words,
            recognized_words=len(timed_words),
            directly_aligned=directly_aligned,
            interpolated=total_words - directly_aligned,
            aligned_ratio=round(directly_aligned / total_words, 4) if total_words else 0.0,
        ),
    )
