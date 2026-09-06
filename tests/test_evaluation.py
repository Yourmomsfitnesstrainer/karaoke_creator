import math

import pytest

from karaoke_generator.evaluation import acceptance, corpus_requirements, evaluate, json_metrics, nearest_rank


def reference():
    return {'songs': [{'id': 'song', 'split': 'acceptance', 'fragments': [
        {'id': 'verse', 'start': 0, 'end': 25, 'words': [
            {'index': i, 'text': str(i), 'start': i, 'end': i+.5, 'uncertainty_ms': 20,
             'end_unambiguous': i != 9, 'cases': ['pause'] if i == 0 else []} for i in range(10)]}]}]}


def test_nearest_rank_and_missing_predictions_in_denominator():
    assert nearest_rank([1, 2, 3, 4], .5) == 2
    words = [{'text': str(i), 'start': i+.1, 'end': i+.6, 'timing': {'source': 'interpolated'}} for i in range(8)]
    result = evaluate(reference(), {'song': {'duration': 30, 'lines': [{'words': words}]}})
    overall = result['overall']
    assert overall['words'] == 10
    assert overall['coverage'] == .8
    assert overall['start_p90_ms'] == math.inf
    assert overall['start_over_250_ratio'] == .2
    assert overall['start_median_ms'] == pytest.approx(100)
    assert overall['signed_start_median_ms'] == pytest.approx(100)
    assert overall['signed_bias_words'] == 8
    assert overall['sources']['interpolated'] == 8
    assert overall['unambiguous_ends'] == 9
    assert json_metrics(result)['overall']['start_p90_ms'] is None
    assert json_metrics(result)['words'][-1]['status'] == 'missing_prediction'
    assert not corpus_requirements(reference())['sufficient']
    assert acceptance(result, result, corpus_requirements(reference()))['status'] == 'insufficient_corpus'


def test_invalid_prediction_and_reference_uncertainty():
    ref = reference()
    prediction = {'song': {'duration': 30, 'lines': [{'words': [{'text': '0', 'start': float('nan'), 'end': .5}]}]}}
    result = evaluate(ref, prediction)
    assert result['words'][0]['status'] == 'invalid_interval'
    assert result['overall']['start_median_ms'] == math.inf
    ref['songs'][0]['fragments'][0]['words'][0].pop('uncertainty_ms')
    with pytest.raises(ValueError, match='uncertainty'):
        evaluate(ref, prediction)


def test_no_pooled_improvement_can_hide_song_regression():
    old = {'overall': {'start_median_ms': 70, 'start_p90_ms': 210,
                      'start_over_250_ratio': .01, 'signed_start_median_ms': 10,
                      'end_p90_ms': 120}, 'by_song': {'a': {'start_p90_ms': 100}}}
    new = {'overall': {**old['overall'], 'start_p90_ms': 140},
           'by_song': {'a': {'start_p90_ms': 121}}}
    result = acceptance(new, old, {'sufficient': True})
    assert result['checks']['relative_improvement'] is True
    assert result['checks']['per_song_regression'] is False
    assert result['status'] == 'failed'


def test_benchmark_reuses_frozen_asr_and_cached_variants(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    import runpy
    import wave
    from karaoke_generator.lyrics import parse_lyrics_text, text_sha256
    from karaoke_generator.timing_cache import file_sha256
    run = runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/benchmark_timing.py'))['run']
    audio = tmp_path/'a.wav'
    with wave.open(str(audio), 'wb') as wav:
        wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\0\0'*32000)
    (tmp_path/'a.txt').write_text('one')
    raw = {'language': 'en', 'spec': {'audio_sha256': file_sha256(audio), 'text_sha256': text_sha256(parse_lyrics_text('one'))},
           'words': [{'text': 'one', 'start': .1, 'end': .5, 'confidence': .9}]}
    (tmp_path/'asr.json').write_text(json.dumps(raw))
    manifest = {'songs': [{'id': 'a', 'split': 'tuning', 'analysis_audio': 'a.wav', 'lyrics': 'a.txt',
        'asr_json': 'asr.json', 'fragments': [{'id': 'verse', 'start': 0, 'end': 2,
        'words': [{'index': 0, 'text': 'one', 'start': .1, 'end': .5, 'uncertainty_ms': 20}]}]}]}
    corpus = tmp_path/'corpus.json'
    corpus.write_text(json.dumps(manifest))
    calls = []
    class Backend:
        def __init__(self, **kwargs):
            self.options = kwargs
        def refine(self, audio, words, language, duration):
            calls.append((self.options, words[0].start))
            return words
    monkeypatch.setitem(run.__globals__, 'WhisperXBackend', Backend)
    monkeypatch.setitem(run.__globals__, 'model_identity', lambda name: {'name': name, 'revision': 'fixed'})
    report_path = run(corpus, tmp_path/'out', ['a', 'b'], [0, .3], 'tuning')
    run(corpus, tmp_path/'out', ['a', 'b'], [0, .3], 'tuning')
    assert len(calls) == 4
    assert all(start == .1 for _, start in calls)
    report = json.loads(report_path.read_text())
    assert len(report['variants']) == 4
    assert all(v['metrics']['overall']['start_p90_ms'] == 0 for v in report['variants'].values())
    assert all(v['acceptance']['status'] == 'not_run' for v in report['variants'].values())
    audio.write_bytes(b'changed waveform')
    with pytest.raises(ValueError, match='ASR audio'):
        run(corpus, tmp_path/'out', ['a'], [0], 'tuning')
