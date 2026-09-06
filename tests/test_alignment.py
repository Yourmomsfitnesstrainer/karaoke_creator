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


def test_compound_matching_cannot_pull_stanza_ending_into_next_repeat():
    document = parse_lyrics_text('Снова нервы е!\n\nСнова нервы е!')
    timed = [TimedWord('Снова', 1, 1.4), TimedWord('нервы', 2, 2.8),
             TimedWord('Снова', 34, 34.4), TimedWord('нервы', 35, 35.8)]
    result = align_lyrics(document, timed, 40, 'ru', 'test')
    first, second = result.lines
    assert first.words[-1].end == 2.8
    assert first.words[-1].asr_text == 'нервы'
    assert second.words[0].start == 34
    assert second.words[0].asr_text == 'Снова'


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


@pytest.mark.parametrize("device", ["cpu", "mps"])
def test_faster_whisper_receives_lyrics_prompt_and_vad_settings(monkeypatch, tmp_path, device) -> None:
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
        device=device,
        vad_filter=True,
        vad_threshold=0.3,
        vad_min_silence_duration_ms=900,
        vad_speech_pad_ms=650,
    )

    backend.transcribe(tmp_path / "vocals.wav", document, "ru", 4.0)

    options = captured["transcribe"][1]
    assert captured["model"][1]["device"] == "cpu"
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


@pytest.mark.parametrize('candidate,reason', [
    (TimedWord('слово', 1.2, 1.5, .01), 'low_refinement_score'),
    (TimedWord('слово', float('nan'), 1.5, .9), 'invalid_candidate_interval'),
    (TimedWord('слово', 1.2, float('inf'), .9), 'invalid_candidate_interval'),
    (TimedWord('слово', 1.5, 1.2, .9), 'invalid_candidate_interval'),
    (TimedWord('слово', -1, .5, .9), 'invalid_candidate_interval'),
    (TimedWord('слово', 1.1, 1.3), 'missing_refinement_score'),
    (TimedWord('слово', 4, 4.5, .9), 'outside_window'),
])
def test_rejected_refinement_preserves_asr_and_reason(candidate, reason):
    base = TimedWord('слово', 1, 1.3, .99)
    merged = _merge_refined_words([base], [candidate], duration=5, window=(.7, 1.6))[0]
    assert (merged.start, merged.end, merged.confidence) == (1, 1.3, .99)
    assert merged.timing['reason'] == reason
    assert merged.timing['source'] == 'asr'
    assert merged.timing['candidate']['score'] == candidate.confidence


@pytest.mark.parametrize('offset', [0, .75, 20.013875])
def test_refinement_accepts_quantized_window_edges_without_asr_jump(offset):
    from karaoke_generator.alignment import _merge_refined_words

    lower, upper = 1.100125+offset, 1.800125+offset
    base = [TimedWord('слово', 1.2+offset, 1.5+offset, .8)]
    candidate = TimedWord('слово', round(lower, 3), round(upper, 3), .9)
    word = _merge_refined_words(base, [candidate], duration=upper, window=(lower, upper))[0]
    assert word.timing['source'] == 'refined'
    assert word.start == max(lower, candidate.start)
    assert word.end == min(upper, candidate.end)
    assert word.timing['candidate']['end'] == candidate.end
    assert lower <= word.start < word.end <= upper
    if candidate.start < lower or candidate.end > upper:
        assert word.timing['rounding_clamp']['reason'] == 'millisecond_endpoint_quantization'


def test_refinement_rejects_real_window_overrun_beyond_quantization():
    from karaoke_generator.alignment import _merge_refined_words

    base = [TimedWord('слово', 1.2, 1.5, .8)]
    word = _merge_refined_words(base, [TimedWord('слово', 1.1, 1.802, .9)],
                               duration=2, window=(1, 1.800125))[0]
    assert word.timing['source'] == 'asr'
    assert word.timing['reason'] == 'outside_window'
    assert (word.start, word.end) == (1.2, 1.5)


def test_refinement_separates_scores_and_allows_large_supported_correction():
    result = _merge_refined_words([TimedWord('слово', 1, 1.3, .99)],
                                 [TimedWord('слово', 1.4, 1.8, .7)], duration=3, window=(.5, 2))
    assert result[0].start == 1.4
    assert result[0].confidence == .99
    assert result[0].timing['candidate']['score'] == .7


def test_repeat_cannot_cross_neighbor_anchor():
    base = [TimedWord('да', 1, 1.4, .9), TimedWord('да', 3, 3.4, .9)]
    merged = _merge_refined_words(base, [TimedWord('да', 3.1, 3.3, .99), TimedWord('да', 5, 5.2, .99)])
    assert merged[0].start == 1
    assert merged[0].timing['reason'] == 'crosses_next_anchor'


def test_character_boundaries_split_compound_and_keep_pause():
    chars = [{'char': c, 'start': s, 'end': e} for c, s, e in
             [('н', 1, 1.1), ('и', 1.1, 1.2), ('к', 1.6, 1.7), ('о', 1.7, 1.8),
              ('г', 1.8, 1.9), ('д', 1.9, 2), ('а', 2, 2.2)]]
    result = align_lyrics(parse_lyrics_text('ни когда'),
                         [TimedWord('никогда', 1, 2.2, .9, characters=chars)], 3, 'ru', 'test')
    a, b = result.lines[0].words
    assert (a.start, a.end, b.start, b.end) == (1, 1.2, 1.6, 2.2)
    assert a.timing['mapping'] == 'character_boundaries'
    approximate = align_lyrics(parse_lyrics_text('ни когда'), [TimedWord('никогда', 1, 2.2)], 3, 'ru', 'test')
    assert approximate.lines[0].words[0].timing['source'] == 'approximate_split'


def test_overlap_chain_records_every_original_boundary():
    result = align_lyrics(parse_lyrics_text('один два три'),
                         [TimedWord('один', 1, 1.7), TimedWord('два', 1.4, 1.9), TimedWord('три', 1.8, 2.2)],
                         3, 'ru', 'test')
    a, b, c = result.lines[0].words
    assert (a.start, b.start, c.start) == (1, 1.7, 1.9)
    assert b.timing['corrections'][0]['delta_start'] == .3
    assert c.timing['corrections'][0]['delta_start'] == .1
    assert b.timing['inputs'][0]['original']['start'] == 1.4


def test_schema_1_2_3_read_and_manual_edits_win():
    import json
    from karaoke_generator.models import AlignmentResult
    result = align_lyrics(parse_lyrics_text('один'), [TimedWord('один', 1, 1.7)], 3, 'ru', 'test')
    for version in [1, 2, 3]:
        payload = result.to_dict()
        payload['schema_version'] = version
        payload['lines'][0]['words'][0].update(start=.7, end=1.3)
        if version < 3:
            payload['lines'][0]['words'][0].pop('timing')
        loaded = AlignmentResult.from_dict(json.loads(json.dumps(payload)))
        word = loaded.lines[0].words[0]
        assert (word.start, word.end) == (.7, 1.3)
        assert loaded.lines[0].start == .7
        assert word.timing is None if version < 3 else word.timing['source'] == 'manual'
        assert AlignmentResult.from_dict(loaded.to_dict()).to_dict() == loaded.to_dict()


def test_whisperx_uses_bounded_absolute_windows_and_separate_repetitions(monkeypatch, tmp_path):
    import sys
    import types
    from importlib.machinery import ModuleSpec
    from karaoke_generator.alignment import WhisperXBackend
    calls = []
    fake = types.ModuleType('whisperx')
    fake.__spec__ = ModuleSpec('whisperx', loader=None)
    fake.load_audio = lambda path: [0] * (16000*25)
    fake.load_align_model = lambda **kw: ('model', {})
    def align(transcript, *args, **kwargs):
        calls.append((transcript, kwargs))
        window = transcript[0]
        start = window['start']+.1
        chars = [{'char': 'д', 'start': start, 'end': start+.1},
                 {'char': 'а', 'start': start+.1, 'end': start+.3}]
        return {'segments': [{'words': [{'word': 'да', 'start': start, 'end': start+.3, 'score': .9}], 'chars': chars}]}
    fake.align = align
    monkeypatch.setitem(sys.modules, 'whisperx', fake)
    backend = WhisperXBackend(device='cpu', align_models={'ru': 'test-model'}, context_seconds=.3)
    base = [TimedWord('да', 1, 1.5, .99, segment_id=0),
            TimedWord('да', 20, 20.5, .99, segment_id=1)]
    output = backend.refine(tmp_path/'vocals.wav', base, 'ru', 25)
    assert len(calls) == 2
    assert calls[0][0] == [{'start': .7, 'end': 1.8, 'text': 'да'}]
    assert calls[1][0] == [{'start': 19.7, 'end': 20.8, 'text': 'да'}]
    assert calls[0][1]['return_char_alignments'] is True
    assert output[0].start == pytest.approx(.8)
    assert output[1].start == pytest.approx(19.8)
    assert len(output[0].characters) == 2
    assert output[0].timing['window']['origin'] == 0


def test_model_input_normalization_preserves_audio_and_absolute_window():
    import numpy as np
    from transformers import Wav2Vec2FeatureExtractor
    from karaoke_generator.alignment import _prepare_alignment_window
    audio = np.random.default_rng(42).normal(.1, .3, 32000).astype(np.float32)
    saved = audio.copy()
    extractor = Wav2Vec2FeatureExtractor(do_normalize=True)
    processed = _prepare_alignment_window(audio, .25, 1.25, extractor)
    quieter = _prepare_alignment_window(audio*.5, .25, 1.25, extractor)
    np.testing.assert_array_equal(audio,saved)
    np.testing.assert_array_equal(processed[:4000],audio[:4000])
    np.testing.assert_array_equal(processed[20000:],audio[20000:])
    assert abs(float(processed[4000:20000].mean())) < 1e-6
    assert abs(float(processed[4000:20000].var())-1) < 1e-5
    np.testing.assert_allclose(processed[4000:20000],quieter[4000:20000],rtol=1e-5,atol=1e-5)
    assert _prepare_alignment_window(audio,.25,1.25,None) is audio
    unchanged = _prepare_alignment_window(audio,.25,1.25,Wav2Vec2FeatureExtractor(do_normalize=False))
    np.testing.assert_array_equal(unchanged,audio)
