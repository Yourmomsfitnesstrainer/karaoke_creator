"""Exercise installed libass, video frames and decoded AAC; no ML or song needed."""
import json
import subprocess
import wave
from pathlib import Path

import pytest

from karaoke_generator.alignment import align_lyrics
from karaoke_generator.audio import find_ffmpeg, prepare_audio
from karaoke_generator.lyrics import parse_lyrics_text
from karaoke_generator.models import TimedWord
from karaoke_generator.renderer import render_video
from karaoke_generator.subtitles import generate_ass

np = pytest.importorskip('numpy')


def measure_clip(folder: Path, offset: int, generator=generate_ass, renderer=render_video) -> dict:
    ffmpeg = find_ffmpeg(require_ass=True)
    folder.mkdir(parents=True, exist_ok=True)
    rate, duration, fps = 48000, 3, 30
    starts, ends = [.1, .9, 1.7], [.5, 1.4, 2.3]
    samples = np.zeros(rate * duration, dtype=np.float64)
    # Broadband markers with reproducible waveforms permit delay measurement.
    rng = np.random.default_rng(42)
    for start in starts:
        at = round(start * rate)
        samples[at:at+960] = rng.uniform(-.6, .6, 960)
    original = folder / 'markers.wav'
    with wave.open(str(original), 'wb') as wav:
        wav.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        wav.writeframes((samples * 32767).astype('<i2').tobytes())
    source = folder / 'source.wav'
    prepare_audio(original, source)
    result = align_lyrics(parse_lyrics_text('MMMM MMMM MMMM'),
                          [TimedWord('MMMM', s, e, 1) for s, e in zip(starts, ends)],
                          duration, 'en', 'test')
    (folder/'alignment.json').write_text(json.dumps(result.to_dict(), indent=2))
    ass, video = folder / 'clip.ass', folder / 'clip.mp4'
    generator(result, ass, {'width': 640, 'height': 360, 'font_size': 45,
                           'timing_offset_ms': offset, 'active_color': '#00FF00',
                           'inactive_color': '#FF0000'})
    runtime = renderer(ass, source, video, {'width': 640, 'height': 360, 'fps': fps,
                 'background': 'solid', 'background_color': '#000000'}, {})
    frames = subprocess.check_output([ffmpeg, '-v', 'error', '-i', str(video),
              '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
    frames = np.frombuffer(frames, np.uint8).reshape(-1, 360, 640, 3).astype(np.int16)
    green = (frames[:,:,:,1] > 100) & (frames[:,:,:,1] > frames[:,:,:,0]*1.8)
    red = (frames[:,:,:,0] > 100) & (frames[:,:,:,0] > frames[:,:,:,1]*1.8)
    columns = np.flatnonzero((green | red).any(axis=(0, 1)))
    bounds = np.linspace(columns[0], columns[-1]+1, 4).astype(int)
    visual = []
    for index in range(3):
        counts = green[:,:,bounds[index]:bounds[index+1]].sum(axis=(1,2))
        fractions = counts / max(counts.max(), 1)
        onset = np.flatnonzero(fractions > .01)
        finish = np.flatnonzero(fractions >= .99)
        visual.append({'start': float(onset[0]/fps) if len(onset) else None,
                       'end': float(finish[0]/fps) if len(finish) else None,
                       'at_zero': float(fractions[0]),
                       'pause_min': float(fractions[round(max(0, ends[index]+offset/1000+.05)*fps):
                             round((starts[index+1]+offset/1000-.05)*fps)].min()) if index < 2 else None})
    def decode(path):
        raw = subprocess.check_output([ffmpeg, '-v', 'error', '-i', str(path),
              '-ac', '1', '-ar', '16000', '-f', 'f32le', '-'])
        return np.frombuffer(raw, '<f4')
    reference, prepared, encoded = decode(original), decode(source), decode(video)
    lags = {}
    for name, signal in [('prepared', prepared), ('mp4', encoded)]:
        values = []
        for start in starts:
            at = round(start*16000)
            template = reference[at:at+320]
            search = signal[at-800:at+1120]
            values.append(int(np.argmax(np.correlate(search, template, 'valid')))-800)
        lags[name] = values
    report = {'offset_ms': offset, 'fps': fps, 'visual': visual, 'render_runtime': runtime,
              'audio_lag_samples_16k': lags}
    (folder/'measurements.json').write_text(json.dumps(report, indent=2))
    return report


@pytest.mark.parametrize('offset', [0, -250, 250])
def test_frames_and_decoded_audio_preserve_word_boundaries(tmp_path, offset):
    try:
        find_ffmpeg(require_ass=True)
    except RuntimeError as exc:
        pytest.skip(str(exc))
    report = measure_clip(tmp_path, offset)
    tolerance = 1/30 + .01
    for word, start, end in zip(report['visual'], [.1, .9, 1.7], [.5, 1.4, 2.3]):
        assert abs(word['start'] - max(0, start+offset/1000)) <= tolerance
        assert abs(word['end'] - max(0, end+offset/1000)) <= tolerance
        if word['pause_min'] is not None:
            assert word['pause_min'] > .97
    if offset == -250:
        assert .15 < report['visual'][0]['at_zero'] < .8
    assert all(abs(lag) <= 16 for lags in report['audio_lag_samples_16k'].values() for lag in lags)
