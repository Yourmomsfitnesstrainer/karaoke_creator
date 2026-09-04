import pytest

from karaoke_generator.alignment import (
    FasterWhisperBackend,
    _merge_refined_words,
    align_lyrics,
    monotonic_word_mapping,
)
from karaoke_generator.lyrics import parse_lyrics_text
from karaoke_generator.models import TimedWord


def test_repeated_lines_map_to_distinct_monotonic_occurrences() -> None:
    lyrics = ["я", "тебя", "люблю", "я", "тебя", "люблю"]
    timed = [TimedWord(word, index, index + 0.5, 0.9) for index, word in enumerate(lyrics)]
    assert monotonic_word_mapping(lyrics, timed) == list(range(6))


def test_missing_word_is_interpolated_and_display_is_unchanged() -> None:
    document = parse_lyrics_text("Я тебя навсегда люблю")
    timed = [
        TimedWord("я", 1.0, 1.2, 0.99),
        TimedWord("тебя", 1.2, 1.6, 0.98),
        TimedWord("люблю", 2.2, 3.0, 0.97),
    ]
    result = align_lyrics(document, timed, 4.0, "ru", "test")
    missing = result.lines[0].words[2]
    assert missing.text == "навсегда"
    assert missing.aligned is False
    assert missing.alignment_source == "interpolated"
    assert 1.6 <= missing.start < missing.end <= 2.2
    all_words = result.lines[0].words
    assert all_words == sorted(all_words, key=lambda word: word.start)


def test_asr_words_supply_timings_but_never_replace_user_lyrics() -> None:
    document = parse_lyrics_text("Я тебя никогда не забуду")
    timed = [
        TimedWord("я", 1.0, 1.2, 0.99),
        TimedWord("тибя", 1.2, 1.7, 0.85),
        TimedWord("ни", 1.7, 1.9, 0.93),
        TimedWord("когда", 1.9, 2.4, 0.96),
        TimedWord("не", 2.4, 2.6, 0.98),
        TimedWord("забуду", 2.6, 3.3, 0.97),
    ]

    result = align_lyrics(document, timed, 4.0, "ru", "faster-whisper")
    words = result.lines[0].words

    assert [word.text for word in words] == ["Я", "тебя", "никогда", "не", "забуду"]
    assert words[1].asr_text == "тибя"
    assert words[2].asr_text == "ни когда"
    assert words[2].start == 1.7
    assert words[2].end == 2.4
    assert all(word.aligned for word in words)
    assert result.quality.total_words == 5
    assert result.quality.recognized_words == 6
    assert result.quality.directly_aligned == 5
    assert result.quality.interpolated == 0
    assert result.quality.aligned_ratio == 1.0


def test_one_asr_token_can_be_split_across_two_user_words() -> None:
    document = parse_lyrics_text("ни когда")
    result = align_lyrics(
        document,
        [TimedWord("никогда", 2.0, 3.0, 0.91)],
        4.0,
        "ru",
        "whisperx",
    )

    first, second = result.lines[0].words
    assert [first.text, second.text] == ["ни", "когда"]
    assert first.asr_text == second.asr_text == "никогда"
    assert first.start == 2.0
    assert first.end == second.start
    assert second.end == 3.0
    assert first.end > first.start
    assert second.end > second.start


def test_alignment_requires_recognized_evidence_from_audio() -> None:
    document = parse_lyrics_text("слова песни")

    with pytest.raises(RuntimeError, match="none of the user words matched"):
        align_lyrics(document, [], 3.0, "ru", "faster-whisper")


def test_faster_whisper_receives_lyrics_prompt_and_vad_settings(monkeypatch, tmp_path) -> None:
    import faster_whisper

    captured: dict = {}

    class FakeModel:
        def __init__(self, *args, **kwargs):
            captured["model"] = (args, kwargs)

        def transcribe(self, *args, **kwargs):
            captured["transcribe"] = (args, kwargs)
            info = type("Info", (), {"language": "ru"})()
            return [], info

    monkeypatch.setattr(faster_whisper, "WhisperModel", FakeModel)
    document = parse_lyrics_text("Точные слова песни")
    backend = FasterWhisperBackend(
        vad_filter=True,
        vad_threshold=0.3,
        vad_min_silence_duration_ms=900,
        vad_speech_pad_ms=650,
    )

    backend.transcribe(tmp_path / "vocals.wav", document, "ru", 4.0)

    options = captured["transcribe"][1]
    assert options["initial_prompt"] == "Точные слова песни"
    assert options["vad_filter"] is True
    assert options["vad_parameters"] == {
        "threshold": 0.3,
        "min_silence_duration_ms": 900,
        "speech_pad_ms": 650,
    }


def test_whisperx_refinement_keeps_base_words_when_forced_alignment_misses_one() -> None:
    base = [
        TimedWord("один", 1.0, 1.4, 0.9),
        TimedWord("тихий", 1.4, 1.8, 0.7),
        TimedWord("два", 1.8, 2.2, 0.8),
    ]
    refined = [
        TimedWord("один", 1.1, 1.45, 0.95),
        TimedWord("два", 1.9, 2.15, 0.95),
    ]

    merged = _merge_refined_words(base, refined)

    assert [word.text for word in merged] == ["один", "тихий", "два"]
    assert (merged[0].start, merged[0].end) == (1.1, 1.45)
    assert (merged[1].start, merged[1].end) == (1.4, 1.8)
    assert (merged[2].start, merged[2].end) == (1.9, 2.15)


def test_all_ranges_remain_inside_audio_duration() -> None:
    document = parse_lyrics_text("раз два три")
    result = align_lyrics(
        document,
        [TimedWord("раз", 9.99, 10.0, 0.9)],
        10.0,
        "ru",
        "faster-whisper",
    )

    words = result.lines[0].words
    assert all(0.0 <= word.start < word.end <= result.duration for word in words)
    assert all(left.end <= right.start for left, right in zip(words, words[1:]))
