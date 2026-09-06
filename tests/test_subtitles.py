from pathlib import Path

from karaoke_generator.alignment import align_lyrics
from karaoke_generator.lyrics import parse_lyrics_text
from karaoke_generator.models import TimedWord
from karaoke_generator.subtitles import generate_ass, seconds_to_ass


def test_ass_time_rounding() -> None:
    assert seconds_to_ass(62.349) == "0:01:02.35"


def test_ass_uses_exact_words_kf_and_next_line(tmp_path: Path) -> None:
    document = parse_lyrics_text("Привет, мир!\nNext line")
    timed = [
        TimedWord("привет", 0.5, 1.0, 1),
        TimedWord("мир", 1.0, 1.5, 1),
        TimedWord("next", 2.0, 2.5, 1),
        TimedWord("line", 2.5, 3.0, 1),
    ]
    alignment = align_lyrics(document, timed, 4.0, "mixed", "test")
    output = tmp_path / "karaoke.ass"
    generate_ass(alignment, output, {"width": 1280, "height": 720, "max_chars_per_line": 40})
    content = output.read_text()
    assert "{\\kf50}Привет," in content
    assert "мир!" in content
    assert "Dialogue: 0" in content
    assert "Next line" in content


def test_ass_applies_negative_highlight_offset(tmp_path: Path) -> None:
    document = parse_lyrics_text("Привет мир")
    alignment = align_lyrics(
        document,
        [TimedWord("Привет", 1.0, 1.4, 1), TimedWord("мир", 1.4, 1.8, 1)],
        3.0,
        "ru",
        "test",
    )
    output = tmp_path / "karaoke.ass"

    generate_ass(alignment, output, {"timing_offset_ms": -250})

    assert "Dialogue: 1,0:00:00.75" in output.read_text()


def test_absolute_boundaries_do_not_accumulate_rounding(tmp_path):
    import re
    from karaoke_generator.subtitles import _karaoke_text
    timed = [TimedWord(f'w{i}', .003 + i*.173, .091 + i*.173, 1) for i in range(250)]
    result = align_lyrics(parse_lyrics_text(' '.join(w.text for w in timed)), timed, 50, 'en', 'test')
    line = result.lines[0]
    for offset in [-.25, 0, .25]:
        origin = max(0, round((line.start+offset)*100))
        tags = re.findall(r'\\kt(-?\d+)\}\{\\kf(\d+)', _karaoke_text(line, origin, offset))
        assert len(tags) == len(timed)
        for (start, duration), word in zip(tags, line.words):
            assert abs((origin+int(start))/100 - (word.start+offset)) <= .00501
            assert abs((origin+int(start)+int(duration))/100 - (word.end+offset)) <= .00501


def test_wrap_uses_canonical_manual_edits_and_retains_negative_times(tmp_path):
    from karaoke_generator.models import AlignmentResult
    result = align_lyrics(parse_lyrics_text('one two three'),
                         [TimedWord('one', .01, .1), TimedWord('two', .2, .6), TimedWord('three', .9, 1.2)],
                         2, 'en', 'test')
    payload = result.to_dict()
    payload['lines'][0]['words'][2].update(start=1.1, end=1.5)
    edited = AlignmentResult.from_dict(payload)
    output = tmp_path / 'edited.ass'
    generate_ass(edited, output, {'timing_offset_ms': -250, 'max_chars_per_line': 7})
    content = output.read_text()
    assert r'{\kt-24}{\kf9}one' in content
    assert r'{\kt-5}{\kf40}two' in content
    assert 'Dialogue: 1,0:00:00.85' in content
    assert edited.lines[0].words[2].start == 1.1
