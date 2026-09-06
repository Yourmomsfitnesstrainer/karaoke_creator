from pathlib import Path
import runpy

import pytest

from karaoke_generator.alignment import align_lyrics
from karaoke_generator.lyrics import parse_lyrics_text
from karaoke_generator.models import TimedWord

RUNNER = runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/check_timing_stability.py'))


def result(text='one two', shift=0):
    document = parse_lyrics_text(text)
    words = [TimedWord(w.display, index+.2+shift, index+.6+shift) for index,w in enumerate(document.words)]
    return align_lyrics(document, words, 10, 'en', 'test')


def test_stability_checks_known_shift_without_claiming_accuracy():
    report = RUNNER['compare_transformed'](result(), result(shift=.75), [.75])
    assert report['max_boundary_change_ms'] == 0
    assert report['over_40ms'] == 0
    wrong = result(shift=.75)
    wrong.lines[0].words[1].start += .2
    report = RUNNER['compare_transformed'](result(), wrong, [.75])
    assert report['max_boundary_change_ms'] == 200
    assert report['over_40ms'] == 1
    assert 'not acoustic error' in report['meaning']


def test_stability_rejects_dropped_or_changed_words():
    with pytest.raises(ValueError, match='dropped words'):
        RUNNER['compare_transformed'](result(),result('one'),[0])
    with pytest.raises(ValueError, match='changed display text'):
        RUNNER['compare_transformed'](result(),result('one three'),[0])
