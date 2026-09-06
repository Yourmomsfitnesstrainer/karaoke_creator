from karaoke_generator.pipeline import _stage


def test_pipeline_stage_reports_start_and_finish_percentages() -> None:
    events: list[tuple[int, str]] = []

    with _stage(3, 5, "Aligning exact lyrics", lambda progress, label: events.append((progress, label))):
        pass

    assert events == [(40, "Aligning exact lyrics"), (60, "Aligning exact lyrics")]


def test_asr_refinement_cache_invalidation(tmp_path, monkeypatch):
    from copy import deepcopy
    from karaoke_generator import timing_cache as cache
    from karaoke_generator.config import DEFAULT_CONFIG
    from karaoke_generator.lyrics import parse_lyrics_text
    from karaoke_generator.models import TimedWord
    audio = tmp_path/'audio.wav'
    audio.write_bytes(b'waveform-one')
    document = parse_lyrics_text('один два')
    options = deepcopy(DEFAULT_CONFIG['alignment'])
    calls = {'asr': 0, 'refine': 0}

    class Backend:
        def transcribe(self, *args):
            calls['asr'] += 1
            return [TimedWord('один', 1, 1.3, .99), TimedWord('два', 1.6, 1.9, .99)], 'ru'
        def refine(self, audio, words, *args):
            calls['refine'] += 1
            return words

    monkeypatch.setattr(cache, 'create_backend', lambda *a, **kw: Backend())
    monkeypatch.setattr(cache, 'model_identity', lambda name, **kw: {'name': name, 'revision': 'fixed'})
    def run():
        return cache.cached_timing(audio, document, 'ru', 3, options, tmp_path)[2]
    assert run()['asr_cache_hit'] is False
    assert run()['refinement_cache_hit'] is True
    assert calls == {'asr': 1, 'refine': 1}
    options['align_models']['ru'] = 'another-model'
    assert run()['asr_cache_hit'] is True
    assert calls == {'asr': 1, 'refine': 2}
    options['context_seconds'] = .5
    run()
    assert calls == {'asr': 1, 'refine': 3}
    monkeypatch.setattr(cache, 'REFINEMENT_ALGORITHM_VERSION', 'future')
    run()
    assert calls == {'asr': 1, 'refine': 4}
    options['vad_filter'] = False
    run()
    assert calls == {'asr': 2, 'refine': 5}
    audio.write_bytes(b'waveform-two')
    run()
    assert calls == {'asr': 3, 'refine': 6}


def test_full_pipeline_reuses_ml_for_style_and_preserves_manual_edits(tmp_path, monkeypatch):
    import json
    import wave
    from copy import deepcopy
    from karaoke_generator import pipeline, timing_cache
    from karaoke_generator.config import DEFAULT_CONFIG
    from karaoke_generator.models import TimedWord
    audio, lyrics, out = tmp_path/'song.wav', tmp_path/'lyrics.txt', tmp_path/'out'
    with wave.open(str(audio), 'wb') as wav:
        wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\0\0'*32000)
    lyrics.write_text('one two')
    calls = []
    class Backend:
        def transcribe(self, *args):
            calls.append('ASR')
            return [TimedWord('one', .1, .5), TimedWord('two', .9, 1.5)], 'en'
    monkeypatch.setattr(timing_cache, 'create_backend', lambda *a, **kw: Backend())
    config = deepcopy(DEFAULT_CONFIG)
    config['alignment']['backend'] = 'uniform'
    config['separation']['enabled'] = False
    config['video'].update(width=640, height=360, background='solid')
    result = pipeline.generate(audio, lyrics, out, config)
    payload = json.loads(result['alignment'].read_text())
    payload['lines'][0]['words'][0]['start'] = .2
    result['alignment'].write_text(json.dumps(payload))
    config['karaoke'].update(timing_offset_ms=250, active_color='#FF0000')
    pipeline.generate(audio, lyrics, out, config)
    assert calls == ['ASR']
    updated = json.loads(result['alignment'].read_text())
    assert updated['lines'][0]['words'][0]['start'] == .2
    assert updated['lines'][0]['words'][0]['timing']['source'] == 'manual'
    assert updated['diagnostics']['effective_timing_offset_ms'] == 250
    assert len(updated['diagnostics']['stage_seconds']) == 5
    assert updated['diagnostics']['asr_cache_hit'] is True
    pipeline.rerender(result['alignment'], audio, tmp_path/'edited.mp4', config)
    assert calls == ['ASR']
    assert json.loads((tmp_path/'edited.render.json').read_text())['effective_timing_offset_ms'] == 250
